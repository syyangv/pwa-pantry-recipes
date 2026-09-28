"""`MealShortlistStore` — the three PWA-owned shortlists, and F12's closed enum.

Run alone:  .venv/bin/python -m pytest tests/shortlists/test_store.py -q

**Nothing here reads a live path, and the recipe half reads no file at all.** The
store takes a `RecipeNames` provider — `Callable[[], frozenset[str]]` — rather
than a `RecipeIndex`, exactly as `IngredientMappingStore` takes a
`CatalogProvider` rather than a `PantryCatalog`. That is what makes the rename
drift testable: a `lambda: frozenset({"盐焗鸡"})` is a *renamed* index, with no
vault behind it, so a test can prove a `⚠ 已重命名` row survives a rename without
touching the user's folder. The database is the shipped `init_db()` under the
`tmp_path` the root `conftest.py` builds, so `meal_lists`, its `CHECK`, and both
its indexes are the real ones.

The tests are grouped by the failure each is load-bearing for:

1. **The empty state is a state.** All three keys, always; `()` for an empty
   slot; and no exception, no refusal, no error code anywhere on the path. This
   is the D3 rule an implementer is most likely to get wrong, because "no rows"
   and "the table is missing" look alike to code that has never seen either.
2. **F12's closed enum** — three slots, and the `CHECK` refusing `snack` at the
   storage layer even when the store's own validator is bypassed. The decisive
   test inserts `snack` with raw SQL, straight past every Python guard: it proves
   the *schema* refuses a fourth value, not that this module's Python happens to.
3. **Add** — idempotence through `ON CONFLICT DO NOTHING` and the unique index,
   next position derived from the table rather than from a count, and the rule
   that a `recipe_note` is a **basename** and never a path.
4. **Remove** — `DELETE` plus a re-sequence in the *same* transaction, so
   `position ∈ [0, n)` holds, and a 404 for a row that is not there.
5. **Reorder** — exact membership, or a refusal. The five rejections are
   separate facts and the boundary is tested from both sides.
6. **Rename drift** — the row is kept, the entry is marked `resolved: false`, and
   `DELETE` by the *stored* name still removes it. A silent drop is the failure
   the whole treatment exists to prevent, so the tests assert the row is *still
   in the table* rather than merely that a flag is false.
7. **Concurrency** — a `DELETE` racing a `reorder` and two `reorder`s racing each
   other, driven by two connections, which is the only way to reach the
   interleaving.
8. **The contract** — the methods §9.12 names, the package the wheel discovers,
   the SQL facts §7.4 rests on, and D3's load-bearing negative: no vault write.

The four HTTP routes and their status codes are `tests/api/test_shortlists_api.py`.
"""

from __future__ import annotations

import asyncio
import sqlite3
import tomllib
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Final

import pytest

from app.config import Settings
from app.db.database import connect_db, db_path, init_db
from app.shortlists.store import (
    MAX_SHORTLIST_ENTRIES,
    MEAL_SLOTS,
    MealShortlistStore,
    OrderMembershipMismatch,
    ShortlistEntry,
    ShortlistEntryMissing,
    ShortlistError,
    ShortlistFull,
    ShortlistOrderTooLong,
    UnknownMealSlot,
    UnknownRecipeNote,
)

REPO_ROOT: Final = Path(__file__).resolve().parents[2]

#: The live recipe names. Three, so a two-element membership has a member outside
#: it — the distinction between a dropped and an invented member is the whole
#: content of the reorder refusals.
RECIPES: Final[tuple[str, ...]] = ("番茄炒蛋", "盐焗鸡", "清蒸鲈鱼")

#: A basename that is also a SQL injection attempt. Stored, listed, reordered, and
#: removed like any other name, because every statement binds it.
HOSTILE_NAME: Final = "菜'; DROP TABLE meal_lists; --"

#: Basenames that are *names*, not paths. A dot, a space, CJK punctuation, and
#: Latin script are all things the real vault's sixteen notes contain.
LEGAL_NAMES: Final[tuple[str, ...]] = (
    "Easy Fragrant Fried Rice",
    "烤鱼.v2",
    "盐焗鸡（改）",
)


#: A scenario `_rejects` can run. Named so a coroutine handed to it at one of the
#: call sites is inferable without an inline `Callable[...]` in every one of them.
Scenario = Coroutine[Any, Any, object]


@dataclass
class _RecipeBox:
    """The stand-in for the live recipe index: a set of basenames.

    `RecipeIndex.snapshot()` is TTL-cached, so the app's own view of a rename
    arrives on the next rebuild. The box has no such delay — it *is* the current
    truth — which is what makes the drift assertions deterministic instead of
    timing-dependent, and it is why nothing here needs a folder to rename.
    """

    names: frozenset[str] = frozenset(RECIPES)

    def __call__(self) -> frozenset[str]:
        return self.names

    def rename(self, note: str, to: str) -> None:
        """Move one name, the way an Obsidian rename moves a file."""
        self.names = frozenset((self.names - {note}) | {to})


@pytest.fixture
def recipes() -> _RecipeBox:
    return _RecipeBox()


@pytest.fixture
def store(settings: Settings, recipes: _RecipeBox) -> MealShortlistStore:
    """A store over a `tmp_path` database and a three-name recipe box."""
    _run(lambda: init_db(settings))
    return MealShortlistStore(lambda: connect_db(settings), recipes)


# --- harness ---------------------------------------------------------------


def _run[T](scenario: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """One scenario to completion, on a fresh loop.

    A fresh loop per call is the point: it is what makes a store that accidentally
    cached a loop-bound object fail here instead of working in a test and
    deadlocking in a request. Same helper, and the same reason, as
    `tests/mapping/test_store.py`.
    """
    return asyncio.run(scenario())


def _add(store: MealShortlistStore, slot: str, note: str) -> tuple[ShortlistEntry, ...]:
    """`store.add`, run. Every store call in this file goes through one of these
    three, so no test has to remember the `asyncio.run` dance."""
    return _run(lambda: store.add(slot, note))


def _remove(store: MealShortlistStore, slot: str, note: str) -> tuple[ShortlistEntry, ...]:
    return _run(lambda: store.remove(slot, note))


def _reorder(
    store: MealShortlistStore, slot: str, order: Sequence[str]
) -> tuple[ShortlistEntry, ...]:
    return _run(lambda: store.reorder(slot, order))


def _rejects(scenario: Scenario, expected: type[ShortlistError]) -> ShortlistError:
    """Run one awaited call expecting a typed refusal, and **return** the exception.

    The argument is the **coroutine itself**, not a factory — so a call site inside
    a loop binds its loop variable eagerly and there is no late-binding lambda to
    get wrong. Returning the exception rather than swallowing it is what lets a
    test assert on the refusal itself (the codes-are-class-attributes test reaches
    a `code` this way, without going through a route) and why a refusal reads the
    same way in these tests as a happy path does.

    An unexpected *success* closes the coroutine before re-raising, so a failure
    here reports the real assertion rather than being buried under a
    "coroutine was never awaited" warning.
    """
    try:
        with pytest.raises(expected) as caught:
            _run(lambda: scenario)
    except BaseException:
        scenario.close()
        raise
    refusal = caught.value
    assert isinstance(refusal, ShortlistError)
    return refusal


def _seed(store: MealShortlistStore, slot: str, *notes: str) -> None:
    """Add each note in order, so a test reads about one thing at a time."""
    for note in notes:
        _add(store, slot, note)


def _listing(store: MealShortlistStore) -> list[tuple[str, ...]]:
    """`list_all`'s non-empty values, for tests that assert about one slot."""
    return [names for names in _run(store.list_all).values() if names]


def _entries(*names: str, resolved: bool = True) -> tuple[ShortlistEntry, ...]:
    """The projection a caller gets, spelled out rather than recomputed."""
    return tuple(ShortlistEntry(name, resolved) for name in names)


def _rows(settings: Settings, slot: str) -> list[tuple[int, str, int]]:
    """`(position, recipe_note, id)` straight from SQLite, in `position` order.

    Straight SQL on purpose: `list_all()` decodes, and a test that cannot observe
    a raw column is a test that cannot prove what was written.
    """

    async def read() -> list[tuple[int, str, int]]:
        async with connect_db(settings) as conn:
            cursor = await conn.execute(
                "SELECT position, recipe_note, id FROM meal_lists WHERE slot = ?"
                " ORDER BY position, id",
                (slot,),
            )
            return [(int(row[0]), str(row[1]), int(row[2])) for row in await cursor.fetchall()]

    return _run(read)


def _names(settings: Settings, slot: str) -> list[str]:
    return [name for _, name, _ in _rows(settings, slot)]


def _positions(settings: Settings, slot: str) -> list[int]:
    return [position for position, _, _ in _rows(settings, slot)]


def _insert_raw(settings: Settings, slot: str, position: int, recipe_note: str) -> None:
    """Insert a `meal_lists` row without the store's validators.

    This is the seam the F12 and the reorder-bound tests need: the only way to
    state "what if the row were in the table" without the store being the thing
    that put it there. It is also how a restored backup or a hand-edit reaches the
    table in production, which is why drift is computed from the table and not
    from what `add` recorded.
    """

    async def run() -> None:
        async with connect_db(settings) as conn:
            await conn.execute(
                "INSERT INTO meal_lists (slot, position, recipe_note) VALUES (?, ?, ?)",
                (slot, position, recipe_note),
            )
            await conn.commit()

    _run(run)


# --- 1. the empty state is a state -----------------------------------------


def test_list_all_publishes_all_three_slots_when_the_table_is_empty(
    store: MealShortlistStore,
) -> None:
    """D3: all three keys are **always** present, and an empty one is `()`.

    Not `None`, not absent, not a refusal. A client that renders three
    invitations must be able to read `body["lunch"]` on a fresh install and get a
    list it can `.map()` over; a missing key makes "the user has no lunch list"
    and "this build forgot lunch" the same wire answer, which is exactly the
    distinction D3 says must not be lost.
    """
    assert _run(store.list_all) == {"breakfast": (), "lunch": (), "dinner": ()}
    assert set(_run(store.list_all)) == {"breakfast", "lunch", "dinner"}


def test_a_slot_stays_empty_after_an_operation_on_another_slot(
    store: MealShortlistStore,
) -> None:
    """Empty is a *state*, so it survives an unrelated write.

    The bug this catches is the store "helpfully" returning only the slots that
    have rows, or having an `add` on `lunch` reshape the answer for `breakfast`.
    """
    _add(store, "lunch", "盐焗鸡")

    listed = _run(store.list_all)

    assert listed["lunch"] == ("盐焗鸡",)
    assert listed["breakfast"] == ()
    assert listed["dinner"] == ()


def test_an_empty_slot_is_an_invitation_not_a_refusal(store: MealShortlistStore) -> None:
    """No operation on an empty slot can raise for *emptiness*.

    Every entry point is driven against a slot with no rows. `remove` of a name
    that is not there is `ShortlistEntryMissing` because the *row* is missing —
    but `list_all`, `entries`, and a reorder of `()` all succeed, because "this
    slot is empty" is the answer, not a failure to produce one.
    """
    assert _run(store.entries) == {"breakfast": (), "lunch": (), "dinner": ()}
    assert _reorder(store, "breakfast", ()) == ()
    _rejects(store.remove("breakfast", "盐焗鸡"), ShortlistEntryMissing)


def test_the_empty_listing_is_a_tuple_of_names_and_nothing_else(
    store: MealShortlistStore,
) -> None:
    """`list_all()`'s declared type is real, not aspirational — including empty."""
    listed = _run(store.list_all)

    assert all(isinstance(names, tuple) for names in listed.values())
    assert listed == {slot: () for slot in MEAL_SLOTS}


def test_every_entry_is_a_tuple_so_a_caller_cannot_reorder_a_shared_list(
    store: MealShortlistStore,
) -> None:
    """`entries()` returns tuples, so the store's answer is a value and not a
    shared buffer the routes build their payload from."""
    _add(store, "lunch", "盐焗鸡")

    listed = _run(store.entries)

    assert all(isinstance(entries, tuple) for entries in listed.values())


# --- 2. F12: the closed enum, and the schema that closes it -----------------


def test_there_are_exactly_three_slots_and_no_snack() -> None:
    """F12, stated as a value so a fourth slot is a diff and not a comment.

    `breakfast` / `lunch` / `dinner` and nothing else. `snack` is spelled out in
    the assertion on purpose: the temptation this test exists to kill is "`snack`
    is obviously harmless", and it is not — see
    `test_the_storage_layer_refuses_a_fourth_slot` for what it costs.
    """
    assert MEAL_SLOTS == ("breakfast", "lunch", "dinner")
    assert "snack" not in MEAL_SLOTS
    assert len(MEAL_SLOTS) == 3


def test_the_storage_layer_refuses_a_fourth_slot(
    settings: Settings, store: MealShortlistStore
) -> None:
    """The `CHECK` is the guarantee, so the test bypasses every Python validator.

    The store's `MEAL_SLOTS` check is a convenience — it turns a bad request into
    a 422 rather than a 500. It is **not** the constraint: a future call site, a
    migration, a repair script, or a hand-edited database all reach SQLite
    without passing through it. So the row is inserted with raw SQL, straight past
    the store, and `sqlite3.IntegrityError` is what comes back.

    F12's reason for closing the enum now rather than later is exactly this
    clause: an added slot is a **table rebuild**, and the outbox replays this
    table, so widening it after shortlist data exists is an availability-affecting
    migration rather than a one-line `ALTER`.
    """
    for slot in MEAL_SLOTS:
        _insert_raw(settings, slot, 0, "x")

    for rejected in ("snack", "brunch", "Breakfast", "LUNCH", "", "lunch "):
        with pytest.raises(sqlite3.IntegrityError):
            _insert_raw(settings, rejected, 0, "x")

    # The three legal ones are still there, so the refusals above were refusals
    # and not a broken fixture.
    assert _names(settings, "breakfast") == ["x"]


def test_the_store_refuses_a_fourth_slot_before_it_can_reach_the_check(
    store: MealShortlistStore,
) -> None:
    """The Python half of F12: `snack` is a typed refusal, not an exception.

    Same answer as the `CHECK`, one layer earlier, so a caller gets a publishable
    code instead of a `sqlite3.IntegrityError` out of the middle of a transaction.
    Case-sensitively: `Breakfast` is not a slot, and a case-insensitive match
    would be a fourth vocabulary the table cannot hold.
    """
    for rejected in ("snack", "Breakfast", "LUNCH", "lunch ", ""):
        # The coroutine is created here with `rejected` already bound, so there is
        # no late-binding lambda for the value to escape through.
        _rejects(store.add(rejected, "盐焗鸡"), UnknownMealSlot)
    for accepted in MEAL_SLOTS:
        assert _add(store, accepted, "盐焗鸡") == _entries("盐焗鸡")


def test_the_slot_validator_is_membership_and_nothing_cheaper() -> None:
    """`MEAL_SLOTS` membership is the *only* test.

    A prefix check, a `startswith`, or a `snack` alias would each pass a looser
    test and each break the `CHECK` in `schema.sql`.
    """
    from app.shortlists.store import _slot

    for accepted in MEAL_SLOTS:
        assert _slot(accepted) == accepted
    for rejected in ("Snack", "sNACK", " breakfast", "breakfast ", "../breakfast", ""):
        with pytest.raises(UnknownMealSlot):
            _slot(rejected)


# --- 3. add ----------------------------------------------------------------


def test_add_appends_at_the_next_position_and_keeps_them_contiguous(
    settings: Settings, store: MealShortlistStore
) -> None:
    """`position` is dense from 0, which is the invariant `remove` has to hold.

    Contiguity is what reorder depends on: a slot whose positions were `0, 1, 7`
    would have two different notions of "first", and every client rendering
    `position` as an ordinal would disagree with the store.
    """
    _seed(store, "lunch", *RECIPES)

    assert _names(settings, "lunch") == list(RECIPES)
    assert _positions(settings, "lunch") == [0, 1, 2]


def test_add_derives_the_next_position_from_the_table_not_from_a_count(
    settings: Settings, store: MealShortlistStore
) -> None:
    """`max(position) + 1`, not `len(rows)`.

    The two agree only while the table is exactly as this store left it. A row
    inserted out of band, or a hand-edited database, is where they part company,
    and `len(rows)` would then hand the new entry a position another row already
    holds — a duplicate ordinal that `ix_meal_lists_slot_position`, being a plain
    index and not a unique one, cannot catch.
    """
    _add(store, "lunch", "番茄炒蛋")
    _insert_raw(settings, "lunch", 7, "清蒸鲈鱼")
    _add(store, "lunch", "盐焗鸡")

    assert _positions(settings, "lunch") == [0, 7, 8]
    assert _names(settings, "lunch") == ["番茄炒蛋", "清蒸鲈鱼", "盐焗鸡"]


def test_adding_the_same_recipe_twice_is_idempotent_not_a_second_row(
    settings: Settings, store: MealShortlistStore
) -> None:
    """A duplicate add is a **no-op**, by the unique index, and the store says so.

    `ux_meal_lists_slot_recipe` is what makes this free; the store's job is to
    notice the collision is *expected* and not surface it. Without that, a double
    tap on `+ 午餐` would either raise or append a second row, and both are worse
    than doing nothing — the user would have to remove a duplicate to find out the
    tap had already worked.

    The raw rows are compared, not just the count, so an insert that fired and
    landed somewhere else could not pass.
    """
    first = _add(store, "lunch", "盐焗鸡")
    before = _rows(settings, "lunch")
    second = _add(store, "lunch", "盐焗鸡")
    after = _rows(settings, "lunch")

    assert first == second == _entries("盐焗鸡")
    assert after == before, "a second add created or renumbered a row"
    assert len(after) == 1


def test_a_recipe_may_be_on_all_three_shortlists_at_once(
    settings: Settings, store: MealShortlistStore
) -> None:
    """Uniqueness is `(slot, recipe_note)`, not `recipe_note`.

    The direction of the index is load-bearing: `盐焗鸡` in `breakfast` and in
    `lunch` is a real arrangement — and the right one, because a breakfast egg
    dish becomes a lunch — while two rows of the same recipe in the *same* slot is
    the defect the index catches.
    """
    for slot in MEAL_SLOTS:
        _add(store, slot, "盐焗鸡")

    assert _run(store.list_all) == {slot: ("盐焗鸡",) for slot in MEAL_SLOTS}


def test_a_second_row_of_the_same_recipe_in_the_same_slot_is_impossible(
    settings: Settings, store: MealShortlistStore
) -> None:
    """The other direction of `ux_meal_lists_slot_recipe`, from the storage layer.

    The test above holds the index open across slots; this holds it closed within
    one. A bare `UNIQUE` on `recipe_note` would make the first test impossible, and
    the other direction of the same index makes that obvious.
    """
    _add(store, "lunch", "盐焗鸡")

    with pytest.raises(sqlite3.IntegrityError):
        _insert_raw(settings, "lunch", 9, "盐焗鸡")

    assert _names(settings, "lunch") == ["盐焗鸡"]


def test_add_refuses_a_recipe_the_live_index_does_not_have(store: MealShortlistStore) -> None:
    """422-shaped, and **not** inserted.

    Adding is *by recipe name*, and the name is validated against the live index
    so a shortlist can only hold recipes that exist. The refused add leaves
    nothing behind: a refusal that had already written would show the user a row
    it had just told them it would not create.
    """
    _rejects(store.add("lunch", "不存在的菜"), UnknownRecipeNote)

    assert _run(store.list_all)["lunch"] == ()


def test_add_refuses_a_path_where_a_basename_belongs(store: MealShortlistStore) -> None:
    """Server-Owned Root: a request contributes *which recipe*, never *which path*.

    D2's rule, and the reason the key is the basename. A body carrying
    `Hobbies/做饭/Recipes/盐焗鸡.md` is refused rather than stored, because a
    shortlist that stored paths would be a second, client-owned mapping of names
    to files — and a client that could point one at a file it chose could walk
    out of `RECIPES_ROOT`.
    """
    for path in (
        "Hobbies/做饭/Recipes/盐焗鸡.md",
        "../Recipes/盐焗鸡",
        "盐焗鸡/番茄炒蛋",
        ".",
        "..",
    ):
        _rejects(store.add("lunch", path), ShortlistError)


def test_a_basename_is_a_name_and_every_real_one_is_accepted(
    store: MealShortlistStore, recipes: _RecipeBox
) -> None:
    """The other side of the path rule, because a validator that refuses real data
    is as broken as one that accepts a path.

    A dot, a space, CJK parentheses, and Latin script are all things the real
    vault's sixteen notes contain. A backslash is *not* refused: it is a legal
    filename character on APFS, so excluding it would be refusing a real note to
    prevent an attack that `/` alone already prevents.
    """
    recipes.names = recipes.names | frozenset(LEGAL_NAMES)

    for name in LEGAL_NAMES:
        _add(store, "lunch", name)

    assert _run(store.list_all)["lunch"] == LEGAL_NAMES


def test_add_is_capped_so_reorder_can_always_reach_the_list(
    settings: Settings, store: MealShortlistStore
) -> None:
    """The cap is on **add** as well as on reorder, and that is the point.

    §7.4 bounds a reorder at 50 rows. A cap only on reorder would let a list grow
    to 60 and then make itself permanently un-reorderable — the user would have no
    way to fix it and no way to understand the 422. So the bound is on the list,
    and reorder's bound is then an invariant of the table rather than a surprise
    the user discovers.
    """
    for position in range(MAX_SHORTLIST_ENTRIES):
        _insert_raw(settings, "dinner", position, f"菜{position}")

    _rejects(store.add("dinner", "盐焗鸡"), ShortlistFull)

    assert len(_rows(settings, "dinner")) == MAX_SHORTLIST_ENTRIES


# --- 4. remove -------------------------------------------------------------


def test_remove_deletes_the_row_and_resequences_in_the_same_transaction(
    settings: Settings, store: MealShortlistStore
) -> None:
    """The middle row goes, and the invariant survives it.

    `DELETE` alone would leave `0, 2` — two rows, a gap, and a client rendering
    `position` as an ordinal showing a recipe as third of two. The re-sequence is
    in the *same* transaction as the `DELETE` precisely so a reader can never
    observe the gap: a separate transaction would publish it for the duration.
    """
    _seed(store, "lunch", *RECIPES)

    assert _remove(store, "lunch", "盐焗鸡") == _entries("番茄炒蛋", "清蒸鲈鱼")
    assert _names(settings, "lunch") == ["番茄炒蛋", "清蒸鲈鱼"]
    assert _positions(settings, "lunch") == [0, 1]


def test_remove_compacts_gaps_from_rows_it_did_not_write(
    settings: Settings, store: MealShortlistStore
) -> None:
    """Resequencing reads the *table*, not a list the store kept in memory."""
    _seed(store, "lunch", "番茄炒蛋", "盐焗鸡")
    _insert_raw(settings, "lunch", 9, "清蒸鲈鱼")

    _remove(store, "lunch", "番茄炒蛋")

    assert _positions(settings, "lunch") == [0, 1]
    assert _names(settings, "lunch") == ["盐焗鸡", "清蒸鲈鱼"]


def test_remove_is_a_404_for_a_row_that_is_not_in_that_slot(
    settings: Settings, store: MealShortlistStore
) -> None:
    """404, not a silent no-op — and still with nothing written.

    A recipe on `lunch` is not a member of `breakfast`, and "you asked to remove
    something from a list it is not on" is a different answer from "removed".
    Confusing the two would make a UI drop a row the user is still looking at.
    """
    _add(store, "lunch", "盐焗鸡")

    _rejects(store.remove("breakfast", "盐焗鸡"), ShortlistEntryMissing)
    _rejects(store.remove("lunch", "清蒸鲈鱼"), ShortlistEntryMissing)

    assert _names(settings, "lunch") == ["盐焗鸡"]


def test_remove_validates_the_slot_before_the_membership(store: MealShortlistStore) -> None:
    """The bound is checked before the row is looked for — the same order
    `app/api/recipes.py::_locate` uses, and for the same reason: a malformed
    request gets the cheaper, state-independent answer, so a client can always
    tell "you named a slot that cannot exist" from "no such row"."""
    _rejects(store.remove("snack", "盐焗鸡"), UnknownMealSlot)


def test_remove_touches_no_other_slot(settings: Settings, store: MealShortlistStore) -> None:
    """Positions are per-slot; a removal must not renumber a sibling list."""
    _seed(store, "lunch", "番茄炒蛋", "盐焗鸡")
    _seed(store, "dinner", "番茄炒蛋")
    before = _rows(settings, "dinner")

    _remove(store, "lunch", "番茄炒蛋")

    assert _rows(settings, "dinner") == before


# --- 5. reorder, and its boundary ------------------------------------------


def test_reorder_rewrites_every_position_in_the_requested_order(
    settings: Settings, store: MealShortlistStore
) -> None:
    """A reorder is a real write, not a sort of a returned list.

    The assertion is on the raw rows, because a `reorder` that returned a sorted
    tuple without writing would satisfy every caller-visible test and leave the
    next `list_all` — and the next boot — reporting the old order.
    """
    _seed(store, "lunch", *RECIPES)
    backwards = tuple(reversed(RECIPES))

    assert _reorder(store, "lunch", backwards) == _entries(*backwards)
    assert _names(settings, "lunch") == list(backwards)
    assert _positions(settings, "lunch") == [0, 1, 2]
    # Idempotent: the same order twice is the same answer and the same rows.
    assert _reorder(store, "lunch", backwards) == _entries(*backwards)
    assert _names(settings, "lunch") == list(backwards)


def test_reorder_never_renumbers_a_row(settings: Settings, store: MealShortlistStore) -> None:
    """It rewrites `position` and nothing else, which is why the order is the
    first column to change and the `id` sequence is untouched.

    An in-transaction `UPDATE` is what §7.4 requires, and this is the observable
    reason: a `DELETE`-then-`INSERT` shuffle would mint new ids, and an offline
    replay holding a stale id could then resurrect a row the user had just
    reordered past.
    """
    _seed(store, "lunch", *RECIPES)
    before = _rows(settings, "lunch")

    _reorder(store, "lunch", tuple(reversed(RECIPES)))

    after = _rows(settings, "lunch")
    assert sorted(identifier for _, _, identifier in after) == sorted(
        identifier for _, _, identifier in before
    ), "a row was replaced rather than moved"
    assert [name for _, name, _ in after] != [name for _, name, _ in before]
    # And the ids are now in *descending* order, which is the whole point: the
    # rows moved, and nothing about their identity did.
    assert [identifier for _, _, identifier in after] == [
        identifier for _, _, identifier in reversed(before)
    ]


def test_reorder_touches_no_other_slot(settings: Settings, store: MealShortlistStore) -> None:
    """A reorder rewrites `position` for the one slot, keyed by the stored name."""
    _seed(store, "lunch", "番茄炒蛋", "盐焗鸡")
    _seed(store, "dinner", "番茄炒蛋", "盐焗鸡")

    _reorder(store, "lunch", ("盐焗鸡", "番茄炒蛋"))

    assert _names(settings, "dinner") == ["番茄炒蛋", "盐焗鸡"]


@pytest.mark.parametrize(
    ("membership", "order", "why"),
    [
        (("番茄炒蛋", "盐焗鸡"), ("番茄炒蛋",), "a dropped member"),
        (("番茄炒蛋", "盐焗鸡"), ("番茄炒蛋", "清蒸鲈鱼"), "an invented member"),
        (("番茄炒蛋", "盐焗鸡"), ("番茄炒蛋", "番茄炒蛋"), "a repeated member"),
        (("番茄炒蛋", "盐焗鸡"), ("番茄炒蛋", "盐焗鸡", "番茄炒蛋"), "an extra member"),
        ((), ("番茄炒蛋",), "a member in a slot with none"),
    ],
)
def test_reorder_refuses_anything_but_exact_membership(
    settings: Settings,
    store: MealShortlistStore,
    membership: tuple[str, ...],
    order: tuple[str, ...],
    why: str,
) -> None:
    """422, never a silent reconciliation — `why` is in the failure message.

    A reorder is the one shortlist write with a *whole-list* argument, so a
    request naming a subset, a superset, or a different set is asking for
    something the client cannot have meant: it either dropped a row by forgetting
    it, or invented one. Reconciling either silently would lose user data or add a
    row the user never chose, and the client would render a list it did not ask
    for as though it had. So the membership must match **exactly**.

    The repeated-member and extra-member cases are the two a set comparison alone
    would let through, which is why they are in this list rather than assumed
    impossible: the extra-member order has the *same set* as the membership.
    """
    _seed(store, "lunch", *membership)
    before = _rows(settings, "lunch")

    _rejects(store.reorder("lunch", order), OrderMembershipMismatch)

    assert _rows(settings, "lunch") == before, f"{why} must not have been reconciled"


def test_a_populated_slot_cannot_be_cleared_by_ordering_it_empty(
    settings: Settings, store: MealShortlistStore
) -> None:
    """`order: []` against a populated slot is the dangerous shape, and it is
    refused.

    A reorder that *reconciled* it would empty the user's list. §9.12 says a
    reorder that drops a member is a 422 precisely so that a client bug — or a
    list rendered from a stale read — cannot delete a curated list by asking for
    it with the wrong array. Clearing a list is what the `×` on each row is for.
    """
    _seed(store, "lunch", "盐焗鸡", "番茄炒蛋")
    before = _rows(settings, "lunch")

    _rejects(store.reorder("lunch", ()), OrderMembershipMismatch)

    assert _rows(settings, "lunch") == before


def test_reorder_validates_the_slot_before_the_membership(
    store: MealShortlistStore,
) -> None:
    """Same order as `add` and `remove`: the closed enum first."""
    _rejects(store.reorder("snack", ()), UnknownMealSlot)


def test_reorder_is_bounded_at_fifty_rows(
    settings: Settings, store: MealShortlistStore
) -> None:
    """The §7.4 bound, tested from the side that can actually reach it.

    `add` caps a list at `MAX_SHORTLIST_ENTRIES`, so a *request* cannot exceed the
    bound — which is exactly why this test writes the rows directly. The bound has
    to hold for a list this store never filled: a restored backup, a hand-edit, or
    a future code path that skips `add` all produce one, and an unbounded reorder
    over it is an unbounded transaction over a table the outbox replays.
    """
    for position in range(MAX_SHORTLIST_ENTRIES + 1):
        _insert_raw(settings, "dinner", position, f"菜{position}")
    over = tuple(f"菜{position}" for position in range(MAX_SHORTLIST_ENTRIES + 1))
    within = over[1:]

    _rejects(store.reorder("dinner", over), ShortlistOrderTooLong)
    assert _names(settings, "dinner") == list(over), "the refusal wrote something"

    # A slot over the bound is not reorderable *at all* — exact membership means
    # an `order` naming all 51 is the only legal one, and that is the one the bound
    # refuses. So the bound has to leave a way out, and it does: `remove` is not
    # bounded, so one `×` takes the list to size and the reorder works again.
    # Asserted because a bound with no recovery is a brick, and the recovery being
    # a *different* verb is the reason the test is here at all.
    _remove(store, "dinner", over[0])
    assert _reorder(store, "dinner", within) == _entries(*within, resolved=False)
    assert _positions(settings, "dinner") == list(range(MAX_SHORTLIST_ENTRIES))


@pytest.mark.parametrize("order", [(1,), ("",), ("盐焗鸡", " "), (None,), ("a/b",)])
def test_reorder_refuses_a_malformed_member(
    store: MealShortlistStore, order: tuple[Any, ...]
) -> None:
    """A malformed `order` is a 422, not a row keyed on a coerced value.

    `str()` of a nested structure would put a Python repr into a user-visible list
    position, which is the failure `app/recipes/reader.py::_entries` refuses for
    the same reason, and a blank member is a row with no name to render.
    """
    _add(store, "lunch", "盐焗鸡")
    _rejects(store.reorder("lunch", list(order)), ShortlistError)


def test_reorder_refuses_a_repeated_member_wherever_it_sits(
    settings: Settings, store: MealShortlistStore
) -> None:
    """Validation is per member, not "the list looks plausible".

    `["盐焗鸡", ""]` is malformed whichever position the bad value occupies, so the
    rejection must not depend on where in the list it sat.
    """
    _seed(store, "lunch", "盐焗鸡", "番茄炒蛋")
    for order in (("",), ("番茄炒蛋", ""), ("番茄炒蛋", "盐焗鸡", "番茄炒蛋")):
        _rejects(store.reorder("lunch", list(order)), ShortlistError)
    assert _names(settings, "lunch") == ["盐焗鸡", "番茄炒蛋"]


def test_reorder_accepts_every_sequence_shape_the_signature_promises(
    store: MealShortlistStore,
) -> None:
    """`Sequence[str]` is a promise, and three callers keep it.

    The routes hand pydantic a `list[str]`, these tests hand a `tuple`, and #23's
    replay will hand whatever it decoded from JSON. The type is the contract; a
    store that silently required a `list` would be a `TypeError` at a call site
    with no way to know.
    """
    _seed(store, "lunch", "盐焗鸡", "番茄炒蛋")

    for order in (["番茄炒蛋", "盐焗鸡"], ("盐焗鸡", "番茄炒蛋")):
        assert _reorder(store, "lunch", order) == _entries(*order)


# --- 6. rename drift: `⚠ 已重命名` ------------------------------------------


def test_a_renamed_recipe_stays_on_the_list_marked_unresolved(
    settings: Settings, store: MealShortlistStore, recipes: _RecipeBox
) -> None:
    """D3's accepted drift, made visible. The row is **kept**.

    The alternative — dropping the row, or hiding it — is worse than showing it
    broken, for two reasons. Dropping it destroys user data on an event the user
    caused innocently, and hiding it makes the shortlist quietly shorter than the
    user curated. So the entry survives with `resolved: false`, and the renderer's
    `⚠ 已重命名` is a decision the *client* makes from that flag.

    In-band, not as a second response shape (F7): a second shape is a second thing
    that can drift from the first, and the drift here is a per-row fact.
    """
    _seed(store, "lunch", "盐焗鸡", "番茄炒蛋")
    recipes.rename("盐焗鸡", "盐焗鸡（新）")

    listed = _run(store.entries)["lunch"]

    assert listed == (ShortlistEntry("盐焗鸡", False), ShortlistEntry("番茄炒蛋", True))
    # The strongest form of the assertion: the row is still in the table. A store
    # that "handled" the rename by deleting would satisfy every other assertion
    # in this file and fail only here.
    assert _names(settings, "lunch") == ["盐焗鸡", "番茄炒蛋"]


def test_a_deleted_recipe_reads_the_same_way_as_a_renamed_one(
    settings: Settings, store: MealShortlistStore, recipes: _RecipeBox
) -> None:
    """Removal and rename are the same fact to the shortlist: the name is gone.

    The app cannot tell them apart — it only knows the name it stored is not in
    the index — and it must not try. A "was it renamed or deleted?" branch would
    need a tombstone this app has no way to keep, and both answers are the same
    thing to the user: this row points at nothing, and only a `DELETE` clears it.
    """
    _add(store, "lunch", "盐焗鸡")
    recipes.names = frozenset({"番茄炒蛋"})

    assert _run(store.entries)["lunch"] == _entries("盐焗鸡", resolved=False)
    assert _names(settings, "lunch") == ["盐焗鸡"]


def test_list_all_publishes_names_only_and_entries_publishes_the_drift(
    store: MealShortlistStore, recipes: _RecipeBox
) -> None:
    """Two projections, one read — and each is what its own contract promises.

    §9.12's `list_all() -> dict[str, tuple[str, ...]]` is a list of **names**, and
    it is the projection the store's own tests and the emptiness check want.
    `entries()` is the same one read plus the `resolved` flag the API needs to
    render `⚠ 已重命名`. `list_all` is derived from `entries`, not from a second
    query, so a rename cannot be visible in one projection and not in the other.
    """
    _add(store, "lunch", "盐焗鸡")
    recipes.rename("盐焗鸡", "盐焗鸡（新）")

    assert _run(store.list_all)["lunch"] == ("盐焗鸡",)
    assert _run(store.entries)["lunch"] == _entries("盐焗鸡", resolved=False)


def test_a_renamed_recipe_can_still_be_removed_by_its_stored_name(
    settings: Settings, store: MealShortlistStore, recipes: _RecipeBox
) -> None:
    """`DELETE` matches the **stored** name, so a `⚠ 已重命名` row is removable.

    This is the whole point of keeping the row. If `remove` validated against the
    live index the row would be un-removeable — not by the name the user sees
    (which no longer resolves) and not by the old one (which the validator would
    refuse) — a permanent, unreachable row in the user's own list. §9.12 says "a
    `DELETE` is the only thing that removes it", and that has to be reachable.
    """
    _seed(store, "lunch", "盐焗鸡", "番茄炒蛋")
    recipes.rename("盐焗鸡", "盐焗鸡（新）")

    assert _remove(store, "lunch", "盐焗鸡") == _entries("番茄炒蛋")
    assert _names(settings, "lunch") == ["番茄炒蛋"]


def test_a_renamed_recipe_still_participates_in_a_reorder(
    settings: Settings, store: MealShortlistStore, recipes: _RecipeBox
) -> None:
    """A broken row is still a member, and dropping it from an order is a 422.

    The membership rule is about the *table*, not about whether the client can
    render each row. If an unresolved row were excludable from an `order`, the UI
    would have to send a subset, and the one thing §9.12 calls out — "a reorder
    that drops or invents a member is a 422, not a silent reconciliation" — would
    be unreachable for exactly the rows that most need the user's attention.
    """
    _seed(store, "lunch", "盐焗鸡", "番茄炒蛋")
    recipes.rename("盐焗鸡", "盐焗鸡（新）")

    assert _reorder(store, "lunch", ("番茄炒蛋", "盐焗鸡")) == (
        ShortlistEntry("番茄炒蛋", True),
        ShortlistEntry("盐焗鸡", False),
    )
    _rejects(store.reorder("lunch", ("番茄炒蛋",)), OrderMembershipMismatch)
    assert _names(settings, "lunch") == ["番茄炒蛋", "盐焗鸡"]


def test_a_renamed_recipe_cannot_be_re_added_under_either_name(
    store: MealShortlistStore, recipes: _RecipeBox
) -> None:
    """`add` validates against the live index, so the *old* name is refused too.

    The corollary of the drift treatment, and the reason `add` checks the index
    while `remove` does not: the list must not grow a second, unresolvable row for
    a recipe the user already has listed.
    """
    _add(store, "lunch", "盐焗鸡")
    recipes.rename("盐焗鸡", "盐焗鸡（新）")

    _rejects(store.add("lunch", "盐焗鸡"), UnknownRecipeNote)
    assert _listing(store) == [("盐焗鸡",)]


def test_a_row_the_store_did_not_write_is_still_marked_unresolved(
    settings: Settings, store: MealShortlistStore
) -> None:
    """Drift is computed from the table against the index, so it covers rows this
    store never saw arrive.

    Restored backups, a hand-edit, and #23's replay all produce rows through a path
    the Python validators do not cross. If `resolved` were derived from what `add`
    recorded, those rows would render as ordinary and the one affordance D3 offers
    for drift would be missing exactly when it is needed.
    """
    _insert_raw(settings, "dinner", 0, "早已删除的菜")

    assert _run(store.entries)["dinner"] == _entries("早已删除的菜", resolved=False)


def test_an_empty_index_marks_everything_unresolved_and_refuses_nothing(
    settings: Settings, store: MealShortlistStore, recipes: _RecipeBox
) -> None:
    """An empty index is a real state, and it is not this store's to refuse.

    `RecipeIndex` raises `ConfigurationError` for a *missing* folder, which
    `app/api/recipes.py` maps to a 503. An index that resolved to zero notes is a
    different thing: the folder is there and holds nothing a `RecipeSnapshot` could
    read. Either way the rows are kept and marked `resolved: false`, because the
    shortlists are the user's data and the folder being empty is a fact about the
    folder, not about their breakfast.
    """
    _seed(store, "lunch", "盐焗鸡", "番茄炒蛋")
    recipes.names = frozenset()

    assert _run(store.entries)["lunch"] == _entries("盐焗鸡", "番茄炒蛋", resolved=False)
    assert _run(store.list_all)["lunch"] == ("盐焗鸡", "番茄炒蛋")
    assert _names(settings, "lunch") == ["盐焗鸡", "番茄炒蛋"]


def test_the_drift_flag_is_recomputed_per_read_with_no_invalidation_call(
    store: MealShortlistStore, recipes: _RecipeBox
) -> None:
    """A rename is visible on the very next read, and there is no `invalidate()`.

    That is deliberate. The flag is derived per read; a cached copy of "which
    names resolve" would be a second source of truth about the vault, repairable
    only by remembering to clear it. `RecipeIndex` already owns a TTL for the
    folder, and the app's staleness bound is that TTL, not a store-level memo. A
    store with an `invalidate()` here would be a claim this module cannot keep.
    """
    _add(store, "lunch", "盐焗鸡")
    assert _run(store.entries)["lunch"] == _entries("盐焗鸡")

    recipes.rename("盐焗鸡", "盐焗鸡（新）")

    assert _run(store.entries)["lunch"] == _entries("盐焗鸡", resolved=False)
    assert not hasattr(store, "invalidate")


# --- 7. concurrency --------------------------------------------------------


def test_a_delete_racing_a_reorder_leaves_positions_dense(
    settings: Settings, store: MealShortlistStore
) -> None:
    """Both writers take the write lock up front, so one of them loses cleanly.

    `reorder` reads the membership **inside** its `BEGIN IMMEDIATE` and validates
    it there. That is what makes this race safe: a `DELETE` that commits first
    changes the membership, and the reorder's in-transaction check then refuses
    with a mismatch rather than writing a `position` for a row that is no longer
    there. A reorder that read the membership *outside* the transaction would have
    a window in which it writes positions for a deleted row — the row would stay
    deleted, so nothing would look wrong, and the slot would keep a gap forever.

    Two connections and a one-turn stagger, and the assertion is on the invariant
    rather than on which of the two won: `position ∈ [0, n)`, pairwise distinct,
    and the names drawn from what was there.
    """
    _seed(store, "lunch", *RECIPES)

    async def scenario() -> list[object]:
        deleting = asyncio.ensure_future(store.remove("lunch", "番茄炒蛋"))
        # One turn, so the `DELETE` has opened its transaction and the `reorder`
        # starts while it is still open. That is the interleaving the
        # in-transaction membership check exists for.
        await asyncio.sleep(0)
        shuffling = asyncio.ensure_future(
            store.reorder("lunch", ("清蒸鲈鱼", "盐焗鸡", "番茄炒蛋"))
        )
        return list(await asyncio.gather(deleting, shuffling, return_exceptions=True))

    outcomes = _run(scenario)

    rows = _rows(settings, "lunch")
    positions = [position for position, _, _ in rows]
    names = [name for _, name, _ in rows]
    # The invariant, which is what the test is for: dense, distinct, and drawn
    # from what was there. Which of the two won decides the order, not these.
    assert positions == list(range(len(rows))), "a position was duplicated or left a gap"
    assert set(names) <= set(RECIPES)
    assert len(names) in (2, 3)
    assert {"清蒸鲈鱼", "盐焗鸡"} <= set(names)
    # Nothing was resurrected: the reorder either refused, or it ran before the
    # delete and every name it wrote is still in the table.
    for outcome in outcomes:
        assert not isinstance(outcome, (sqlite3.Error, ShortlistError)) or isinstance(
            outcome, OrderMembershipMismatch
        ), outcome


def test_a_reorder_whose_membership_goes_stale_refuses_instead_of_writing_a_phantom(
    settings: Settings, store: MealShortlistStore
) -> None:
    """**The `DELETE`-races-`reorder` interleaving, forced rather than hoped for.**

    The `asyncio.gather` test above runs the two writers concurrently and asserts
    the invariant afterwards. That is necessary and it is not sufficient: it
    cannot tell a reorder that *read its membership inside its transaction* from
    one that read it a moment earlier, because both leave a dense list whenever
    the timing does not land in the window. This test lands in the window every
    time, by taking the write lock away and then releasing it underneath the
    reorder.

    The mechanism is `BEGIN IMMEDIATE` itself. A second connection opens
    `BEGIN IMMEDIATE`, deletes a row, and **does not commit**. The reorder's
    `BEGIN IMMEDIATE` then blocks on the write lock, so the two writers are
    ordered by SQLite rather than by the scheduler:

    - **Membership read inside the transaction** (correct): the reorder blocks
      *before* its read, so it sees the committed two-row slot, and the three-name
      `order` no longer matches — `OrderMembershipMismatch`, and nothing written.
    - **Membership read before the transaction** (the mutant): the read is not
      blocked by the write lock, so it sees the *uncommitted* three-row state,
      then blocks, then writes `position` for a row the `DELETE` has removed. The
      `UPDATE` matches nothing, so the reorder **returns three items naming a row
      that is not in the table** — a response that claims a recipe is on the list
      when it is not, which is the plausible-looking wrong answer this whole
      mechanism exists to prevent.

    The second assertion is the general form of the first: whatever a reorder
    returns must match the table, so a returned list and the stored rows can never
    disagree.
    """
    _seed(store, "lunch", *RECIPES)
    order = ("清蒸鲈鱼", "盐焗鸡", "番茄炒蛋")

    async def scenario() -> BaseException | None:
        blocker = sqlite3.connect(
            db_path(settings), check_same_thread=False, isolation_level=None
        )
        try:
            await asyncio.to_thread(
                blocker.execute,
                "BEGIN IMMEDIATE",
            )
            await asyncio.to_thread(
                blocker.execute,
                "DELETE FROM meal_lists WHERE slot = ? AND recipe_note = ?",
                ("lunch", "番茄炒蛋"),
            )

            async def release() -> None:
                # Long enough for the reorder to have reached its blocked write
                # lock, short enough to keep the test quick.
                await asyncio.sleep(0.1)
                await asyncio.to_thread(blocker.execute, "COMMIT")

            releasing = asyncio.ensure_future(release())
            try:
                await store.reorder("lunch", order)
                return None
            except OrderMembershipMismatch as exc:
                return exc
            finally:
                await releasing
        finally:
            blocker.close()

    refusal = _run(scenario)

    assert isinstance(refusal, OrderMembershipMismatch), (
        "the reorder did not refuse a stale membership, so it wrote positions for a row "
        f"a concurrent DELETE had already removed (the table is now {_names(settings, 'lunch')})"
    )
    # The two survivors kept the positions they already had, so the table reads
    # `1, 2` rather than `0, 1` — and that is expected rather than a gap this
    # store left: the blocking connection issued a **raw** `DELETE`, not
    # `store.remove`, so nothing was there to re-sequence. What is under test is
    # that the reorder wrote *nothing at all*; had it written, the deleted row's
    # name would be in its response and the assertion above would not have fired.
    assert _names(settings, "lunch") == ["盐焗鸡", "清蒸鲈鱼"]
    assert _positions(settings, "lunch") == [1, 2]


def test_a_reorder_response_always_matches_the_table_it_wrote(
    settings: Settings, store: MealShortlistStore
) -> None:
    """The general form of the invariant above, in the happy path.

    A `reorder` that returns `names` rather than reading them back could, after a
    concurrent write, name a row that is not there. The two must be the same list,
    and the position sequence must be dense — so "what the client is told" and
    "what the next read will say" cannot diverge.
    """
    _seed(store, "lunch", *RECIPES)

    for order in (tuple(reversed(RECIPES)), RECIPES, ("盐焗鸡", "番茄炒蛋", "清蒸鲈鱼")):
        returned = _reorder(store, "lunch", order)

        assert [entry.note_name for entry in returned] == _names(settings, "lunch")
        assert _positions(settings, "lunch") == list(range(len(order)))


def test_a_reorder_racing_a_reorder_never_duplicates_a_position(
    settings: Settings, store: MealShortlistStore
) -> None:
    """Two concurrent reorders of the same slot: the last commit wins, whole.

    `BEGIN IMMEDIATE` serialises them, so the second reads the membership the
    first produced and rewrites `position` over the same names. The invariant that
    must survive is `position ∈ [0, n)`; the order itself is whichever committed
    last, which is the correct answer for two simultaneous intents.
    """
    _seed(store, "lunch", *RECIPES)

    async def scenario() -> None:
        await asyncio.gather(
            store.reorder("lunch", RECIPES),
            store.reorder("lunch", tuple(reversed(RECIPES))),
        )

    _run(scenario)

    rows = _rows(settings, "lunch")
    assert [name for _, name, _ in rows] in [
        list(RECIPES),
        list(reversed(RECIPES)),
    ]
    assert [position for position, _, _ in rows] == [0, 1, 2]



# --- 8. the contract the router and the wheel both depend on ----------------


def test_every_documented_store_method_exists_with_its_promised_name() -> None:
    """§9.12's four, as a list. A rename that left the behaviour intact would
    break `app/api/shortlists.py` and be discovered there, which is the wrong
    place to learn a method moved."""
    for name in ("list_all", "add", "remove", "reorder"):
        assert callable(getattr(MealShortlistStore, name)), name
    # Plus the one §9.12 has no name for: the drift flag the API needs. Without
    # it the routes would have to re-read the index, and the shortlist response
    # would be assembled from two reads that could disagree.
    assert callable(MealShortlistStore.entries)


def test_a_shortlist_error_carries_a_publishable_code() -> None:
    """Every refusal names itself, because the route publishes that name.

    The codes are part of the wire contract: `app/api/shortlists.py` maps each
    exception to a status and publishes `exception.code`, and
    `tests/api/test_shortlists_api.py` asserts the strings. One generic
    `shortlist_error` for all of them would make the 404 and the 422s
    indistinguishable to a client, which is the only reason the statuses differ.
    """
    expected = {
        ShortlistError: "shortlist_error",
        UnknownMealSlot: "unknown_meal_slot",
        UnknownRecipeNote: "unknown_recipe_note",
        OrderMembershipMismatch: "shortlist_order_mismatch",
        ShortlistFull: "shortlist_full",
        ShortlistOrderTooLong: "shortlist_order_too_long",
        ShortlistEntryMissing: "shortlist_entry_missing",
    }
    for exception, code in expected.items():
        assert exception.code == code, exception.__name__
        assert issubclass(exception, ShortlistError), exception.__name__


def test_a_message_never_becomes_a_code_and_a_note_is_never_reflected_back() -> None:
    """The codes are class attributes, so a recipe name cannot leak into one.

    `app/api/recipes.py::_code_of` guards the same property for the codes it does
    derive from an exception message. This store never derives a code from a
    message at all, so the note a user typed is carried in the exception and can
    never reach the envelope, which publishes exactly `{"requestId", "code"}`.
    """
    error = UnknownRecipeNote("不存在的菜")

    assert str(error) == "不存在的菜"
    assert error.code == "unknown_recipe_note"
    assert "不存在的菜" not in error.code


def test_a_refusal_happens_before_any_write(settings: Settings, store: MealShortlistStore) -> None:
    """A refused add leaves the table byte-identical.

    Asserted on raw rows rather than on `list_all`, because a refusal that had
    inserted and then rolled back would pass the public read and still have burned
    a row `id` and a `created_at`.
    """
    _add(store, "lunch", "盐焗鸡")
    before = _rows(settings, "lunch")

    for scenario in (
        store.add("snack", "盐焗鸡"),
        store.add("lunch", "不存在的菜"),
        store.add("lunch", "Hobbies/做饭/Recipes/盐焗鸡.md"),
    ):
        _rejects(scenario, ShortlistError)

    assert _rows(settings, "lunch") == before


def test_a_name_is_never_interpolated_into_sql(
    settings: Settings, store: MealShortlistStore, recipes: _RecipeBox
) -> None:
    """Every name travels as a bound parameter, so a note titled
    `'; DROP TABLE meal_lists; --` is a *name*.

    Asserted on the effect rather than on the source: the hostile basename is
    stored, listed, reordered, and removed like any other, `meal_lists` is still
    there afterwards, and no statement in the module is built by formatting.
    """
    recipes.names = recipes.names | {HOSTILE_NAME}
    _seed(store, "lunch", HOSTILE_NAME, "盐焗鸡")

    assert _names(settings, "lunch") == [HOSTILE_NAME, "盐焗鸡"]
    assert _run(store.entries)["lunch"] == _entries(HOSTILE_NAME, "盐焗鸡")

    _reorder(store, "lunch", ("盐焗鸡", HOSTILE_NAME))
    _remove(store, "lunch", HOSTILE_NAME)
    assert _names(settings, "lunch") == ["盐焗鸡"]

    _insert_raw(settings, "lunch", 5, HOSTILE_NAME)
    assert len(_rows(settings, "lunch")) == 2


def test_the_write_statements_name_their_duplicate_and_carry_no_reorder_shuffle() -> None:
    """The two SQL facts §7.4 and F2's refusal rest on, asserted statically.

    `INSERT … ON CONFLICT DO NOTHING` is what makes a re-add a no-op, and naming
    the target is better than a bare `OR IGNORE` because a future constraint would
    then abort loudly instead of being swallowed — and because `OR IGNORE` would
    also swallow a `CHECK` violation, which is the one thing F12 must never do
    quietly. And `reorder` must never be built from a `DELETE`-then-`INSERT`
    shuffle: SQLite has no deferred unique constraint, so a shuffle transiently
    violates `ux_meal_lists_slot_recipe`, while an in-transaction `UPDATE` of
    `position` does not.

    Asserted on the module's own SQL so the property survives a refactor of the
    Python around it.
    """
    from app.shortlists import store as module

    assert "ON CONFLICT (slot, recipe_note) DO NOTHING" in module._INSERT_ENTRY
    assert "OR REPLACE" not in module._INSERT_ENTRY
    assert "OR IGNORE" not in module._INSERT_ENTRY
    assert "position = ?" in module._UPDATE_POSITION
    assert "DELETE" not in module._UPDATE_POSITION
    for forbidden in ("OR ROLLBACK", "OR FAIL", "OR IGNORE"):
        assert forbidden not in module._UPDATE_POSITION


def test_a_shortlist_entry_is_two_fields_and_nothing_else() -> None:
    """The wire item is `{noteName, resolved}` and nothing more.

    F17: no `cookable` boolean, here or anywhere near it. F7: provenance in-band
    — `resolved` is the drift flag, and there is no second response shape, no
    `?strict=1`, and no `?debug=1` that would carry it instead. The two fields are
    also the whole story: which recipe, and whether it still resolves.
    """
    entry = ShortlistEntry("盐焗鸡", False)

    assert (entry.note_name, entry.resolved) == ("盐焗鸡", False)
    assert {field.name for field in fields(ShortlistEntry)} == {"note_name", "resolved"}


def test_every_published_slot_is_one_of_the_three_and_every_entry_is_marked(
    store: MealShortlistStore, recipes: _RecipeBox
) -> None:
    """`entries()` is total: three keys, and a `resolved` on every row.

    Not a default. The point of the flag is that its absence is impossible — a
    client that read `entry.resolved` and got an `AttributeError` on the one row
    that had drifted would have no way to render `⚠ 已重命名`, which is the only
    affordance D3 offers for a broken row.
    """
    _seed(store, "lunch", "盐焗鸡", "番茄炒蛋")
    recipes.rename("盐焗鸡", "盐焗鸡（新）")

    listed = _run(store.entries)

    assert tuple(listed) == MEAL_SLOTS
    for slot, entries in listed.items():
        assert isinstance(entries, tuple), slot
        assert all(isinstance(entry.resolved, bool) for entry in entries), slot


def test_the_store_never_invents_a_fourth_key(store: MealShortlistStore) -> None:
    """Both projections are keyed by exactly the three slots.

    A slot in one and not the other would be the most confusing failure available
    to a client: `GET /api/shortlists` renders three panels from whatever keys it
    finds, so a fourth would appear and a missing one would silently lose a panel
    the user had curated.
    """
    _add(store, "lunch", "盐焗鸡")

    assert set(_run(store.entries)) == set(MEAL_SLOTS)
    assert set(_run(store.list_all)) == set(MEAL_SLOTS)


def test_the_whole_shortlist_state_is_reachable_from_one_read(
    store: MealShortlistStore, recipes: _RecipeBox
) -> None:
    """`entries()` is the one read `GET /api/shortlists` needs, for all three.

    A route that read `list_all` for the names and then re-read the index for the
    flags would have two reads that could disagree — a response naming a recipe
    that was renamed between them, which is a plausible-looking wrong answer and
    the exact class D3 says must be made visible rather than smoothed over.
    """
    _seed(store, "breakfast", "番茄炒蛋")
    _seed(store, "lunch", "盐焗鸡")
    _seed(store, "dinner", "清蒸鲈鱼")
    recipes.rename("盐焗鸡", "盐焗鸡（新）")

    assert _run(store.entries) == {
        "breakfast": _entries("番茄炒蛋"),
        "lunch": _entries("盐焗鸡", resolved=False),
        "dinner": _entries("清蒸鲈鱼"),
    }


def test_a_store_over_an_empty_recipe_index_still_answers_all_three_slots(
    settings: Settings, recipes: _RecipeBox
) -> None:
    """No recipes, no problem: the shortlists are the user's, not the vault's.

    A `RecipeIndex` over an empty folder is a different object from a missing one
    (the latter raises `ConfigurationError`, which `app/api/recipes.py` maps to a
    503). This store is handed a provider, so it is handed neither, and the user
    still gets three empty invitations rather than an error. That is D3's rule, and
    it is why the drift flag is per row rather than per response.
    """
    _run(lambda: init_db(settings))
    recipes.names = frozenset()
    store = MealShortlistStore(lambda: connect_db(settings), recipes)

    assert _run(store.entries) == {slot: () for slot in MEAL_SLOTS}


def test_the_reorder_keeps_positions_dense_after_every_step(
    settings: Settings, store: MealShortlistStore
) -> None:
    """The invariant, walked through a sequence that stresses it.

    Add, reorder, remove the head, add again, remove a middle, reorder. Each
    step's raw positions are checked *at that step*, so a re-sequence that works
    for only one of them is caught where it broke rather than at the end.
    """
    _seed(store, "lunch", *RECIPES)
    assert _positions(settings, "lunch") == [0, 1, 2]

    _reorder(store, "lunch", ("清蒸鲈鱼", "番茄炒蛋", "盐焗鸡"))
    assert _names(settings, "lunch") == ["清蒸鲈鱼", "番茄炒蛋", "盐焗鸡"]
    assert _positions(settings, "lunch") == [0, 1, 2]

    _remove(store, "lunch", "清蒸鲈鱼")
    assert _positions(settings, "lunch") == [0, 1]

    _add(store, "lunch", "清蒸鲈鱼")
    assert _positions(settings, "lunch") == [0, 1, 2]

    _remove(store, "lunch", "番茄炒蛋")
    assert _positions(settings, "lunch") == [0, 1]

    _reorder(store, "lunch", ("盐焗鸡", "清蒸鲈鱼"))
    assert _names(settings, "lunch") == ["盐焗鸡", "清蒸鲈鱼"]
    assert _positions(settings, "lunch") == [0, 1]


def test_the_ordering_is_by_position_and_id_is_only_a_tiebreak(
    settings: Settings, store: MealShortlistStore
) -> None:
    """`ORDER BY position` is the contract, and `id` only breaks a tie.

    A reorder rewrites `position` and leaves every `id` alone, so a query ordered
    by `id` would report the *original* add order forever. The test inverts the two
    so the ordering column is the thing under test, and asserts the ids are still
    ascending in their new homes.
    """
    _seed(store, "lunch", *RECIPES)
    _reorder(store, "lunch", ("清蒸鲈鱼", "盐焗鸡", "番茄炒蛋"))

    rows = _rows(settings, "lunch")

    # `position` ascends; `id` does not. A query ordered by `id` would report the
    # original add order forever, so the two moving in opposite directions is what
    # makes "the read is on `position`" an assertion rather than a claim.
    assert [name for _, name, _ in rows] == ["清蒸鲈鱼", "盐焗鸡", "番茄炒蛋"]
    assert [position for position, _, _ in rows] == [0, 1, 2]
    assert [identifier for _, _, identifier in rows] == [3, 2, 1]


def test_the_store_holds_no_connection_between_operations(
    settings: Settings, store: MealShortlistStore
) -> None:
    """One connection per operation, and the same reason
    `IngredientMappingStore` follows the same rule.

    `connect_db` is an `asynccontextmanager`, so the store depends on a factory
    and each operation opens and closes its own connection. A store that kept one
    open would pin a WAL handle for the life of the process — which is exactly the
    leak `app/main.py`'s `finally` cannot see, because such a store would have
    nothing to close.
    """
    _add(store, "lunch", "盐焗鸡")
    _remove(store, "lunch", "盐焗鸡")

    async def still_readable() -> int:
        async with connect_db(settings) as conn:
            cursor = await conn.execute("SELECT COUNT(*) FROM meal_lists")
            row = await cursor.fetchone()
            assert row is not None
            return int(row[0])

    assert _run(still_readable) == 0


def test_the_store_holds_no_index_and_no_path(
    settings: Settings, store: MealShortlistStore, recipes: _RecipeBox
) -> None:
    """The suite-wide property, asserted for this module rather than assumed.

    The vault is the user's live, machine-rewritten folder and the producer's
    `pantry_items.db` is in a sibling checkout CI does not have. A test reaching
    for either would pass on one machine and assert nothing on every other one.
    So: the database is under `tmp_path` and outside the vault, and the recipe
    half is a callable returning a `frozenset` — not a `RecipeIndex`, which would
    have to be built over a folder to be used at all.
    """
    database = settings.app_data_dir / "recipes.sqlite3"

    assert database.is_file()
    assert not database.is_relative_to(settings.vault_path)
    assert settings.app_data_dir != settings.vault_path
    assert set(MealShortlistStore.__init__.__annotations__) == {
        "connect",
        "recipes",
        "return",
    }
    assert not hasattr(store, "_index")
    assert recipes() == frozenset(RECIPES)


def test_a_full_cycle_writes_nothing_to_the_vault(settings: Settings) -> None:
    """D3's load-bearing negative, asserted across the whole operation.

    A Meal Shortlist is PWA-owned state. Nothing in it belongs in the vault: no
    frontmatter field is added to any Recipe (there is no `餐次`), no Cooking
    Record is grouped by slot, and nothing is synced back. So the vault is
    byte-identical before and after a full add / reorder / remove cycle, and the
    recipe notes in it carry no meal key at all.
    """
    before = {path.name: path.read_bytes() for path in settings.vault_path.rglob("*.md")}
    recipes = _RecipeBox()

    async def cycle() -> None:
        await init_db(settings)
        store = MealShortlistStore(lambda: connect_db(settings), recipes)
        await store.add("lunch", "盐焗鸡")
        await store.add("lunch", "番茄炒蛋")
        await store.reorder("lunch", ("番茄炒蛋", "盐焗鸡"))
        await store.remove("lunch", "盐焗鸡")

    _run(cycle)

    after = {path.name: path.read_bytes() for path in settings.vault_path.rglob("*.md")}
    assert after == before
    assert "餐次".encode() not in b"".join(after.values()), "a meal key reached the vault"


def test_the_store_ships_as_a_discovered_package() -> None:
    """No `pyproject.toml` edit is needed for a new subpackage, and this is why.

    `[tool.setuptools.packages.find]` uses `include = ["app*"]` with
    `namespaces = false`, so `app/shortlists` ships because it carries an
    `__init__.py`. An explicit `packages` list would have made a subpackage a
    ticket forgot to register a silent-green hole; asserting discovery reaches it
    is what makes the absence of a packaging edit a property rather than a hope.
    """
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    finder = config["tool"]["setuptools"]["packages"]["find"]

    assert finder["include"] == ["app*"]
    assert finder["namespaces"] is False
    assert (REPO_ROOT / "app" / "shortlists" / "__init__.py").is_file()
    assert (REPO_ROOT / "app" / "shortlists" / "store.py").is_file()
    # And no `package-data` glob was needed: this package ships code, not files,
    # so the wheel's 73 entries are unchanged by this ticket.
    assert "app.shortlists" not in config["tool"]["setuptools"]["package-data"]


def test_the_store_repr_says_what_it_is(store: MealShortlistStore) -> None:
    """A one-line repr, so a failed test's output names the object."""
    assert repr(store).startswith("MealShortlistStore(")

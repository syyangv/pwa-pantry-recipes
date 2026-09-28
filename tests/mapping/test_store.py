"""`IngredientMappingStore`: the materialized mapping, and F2's per-slot recovery.

Run alone:  .venv/bin/python -m pytest tests/mapping/test_store.py -q

**Nothing here reads a live path.** The catalog is
`tests/fixtures/pantry_items_snapshot.json` through the shared
`tests/recipes/corpus.py` helpers, the recipes are the frozen
`tests/fixtures/real_recipes/*.md`, and the database lives under the `tmp_path`
the root `conftest.py` builds. The producer's `pantry_items.db` is in a sibling
checkout CI does not have, so a test reaching for it would pass on one machine and
assert nothing on every other one — worse than not asserting at all.
`test_nothing_here_reads_a_live_path` makes that a property of this module rather
than of good intentions.

The tests are grouped by the failure each one is load-bearing for:

1. **`ensure_rows`** — idempotence in the strong sense (two runs, same rows, same
   `created_at`, same `updated_at`), §9.6 provenance on a row no pass has touched,
   `材料` only.
2. **The positive half of F2** — `空心菜 → 83` legal in three recipes at once,
   which is the *direction* of `ux_ingredient_mappings_recipe_item`. A bare
   `UNIQUE` on the nullable id column would make two of those three impossible
   and would **silently** drop a resolution, so this is the test that holds the
   uniqueness direction in place.
3. **F2's six duplicate-conflict properties**, one at a time, plus the static
   contract on the slot `UPDATE`. The decisive assertion is not that the
   conflicting slot was downgraded — it is that **the other thirty-two rows of
   the same committed table are byte-identical afterwards**, because that is the
   only thing which distinguishes a per-slot catch from a transaction-wide abort.
4. **F6** — a `manual` row survives a pass unchanged, the trigger refuses an
   `UPDATE`, and delete-and-insert is the only path that works.
5. **Catalog drift** — a renumbered id, `stale_rows()`, the reset, and
   `catalog_changed` being true after a change and false when nothing changed.
6. **Provenance** — an audit entry's name and id survive the round trip, a
   multi-guard refusal keeps *both* reasons, and a hand-corrupted audit trail is
   refused on read and repaired by the next pass.
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
from app.db.database import connect_db, init_db
from app.mapping.store import (
    _INSERT_SLOT,
    _UPDATE_SLOT,
    _UPDATE_SLOT_CONFLICT,
    DUPLICATE_SLOT_CONFLICT,
    MATCHED_REASON,
    CorruptMappingRow,
    IngredientMappingStore,
    MappingError,
    ResolveReport,
    SlotConflict,
    UnknownIngredientSlot,
    UnknownPantryItem,
    _transaction,
)
from app.pantry.catalog import CatalogSnapshot, build_snapshot
from app.recipes.reader import RecipeNote, parse_recipe
from tests.recipes.corpus import CATALOG_SNAPSHOT, committed_catalog, real_notes

REPO_ROOT: Final = Path(__file__).resolve().parents[2]

#: `空心菜` resolves to this Pantry Item, and three of the sixteen notes carry it.
#: F2's whole argument about the uniqueness *direction* is this number in three
#: different `recipe_note` values.
KONGXINCAI_ITEM_ID: Final = 83
KONGXINCAI_RECIPES: Final = ("fake/拌空心菜.md", "fake/煮菜菜.md", "fake/花蛤拌饭.md")
#: `空心菜`'s index within each of the three, so a rename of the list cannot make
#: this file assert the wrong slot.
KONGXINCAI_INDEX: Final = {
    "fake/拌空心菜.md": 0,
    "fake/煮菜菜.md": 0,
    "fake/花蛤拌饭.md": 3,
}

#: The `材料` slots of the 16 real notes. F2 enumerates them in the spec, and
#: `tests/recipes/corpus.py` already asserts the fixtures still parse, so this
#: number only has to be the committed one.
REAL_SLOT_COUNT: Final = 32
#: How many of those 32 the shipped ladder resolves, measured over the committed
#: 176-row candidate universe. Restated here so a matcher change that moves the
#: corpus is a visible failure in this file rather than a quiet drift in a count
#: nobody wrote down twice.
REAL_RESOLVED: Final = 9

# --- harness ---------------------------------------------------------------


def _run[T](scenario: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """Run one scenario to completion. `asyncio.run` per call, as in `tests/db`.

    A fresh loop per call is the point rather than an accident: it is what makes a
    store that accidentally cached a loop-bound object — a connection, an
    `asyncio.Lock` — fail here instead of working in a test and deadlocking in a
    request.
    """
    return asyncio.run(scenario())


@dataclass
class _CatalogBox:
    """A swappable catalog, so a test can renumber an id between two passes.

    A `PantryCatalog` cannot be used for this: it opens a file, and this suite
    reads no live path. The store takes a `CatalogProvider` precisely so a
    committed snapshot and a synthetic one are interchangeable, and so the
    renumbering test is expressible at all.
    """

    snapshot: CatalogSnapshot

    def __call__(self) -> CatalogSnapshot:
        return self.snapshot


@pytest.fixture
def catalog_box() -> _CatalogBox:
    return _CatalogBox(committed_catalog())


@pytest.fixture
def store(settings: Settings, catalog_box: _CatalogBox) -> IngredientMappingStore:
    """A store over a `tmp_path` database and the committed 178-row catalog."""
    _run(lambda: init_db(settings))
    return IngredientMappingStore(lambda: connect_db(settings), catalog_box)


@pytest.fixture
def seeded(store: IngredientMappingStore) -> IngredientMappingStore:
    """A store whose table already holds the whole committed corpus, resolved.

    Deliberately does *not* add a conflicting recipe: the control rows have to
    exist first, so a test can prove a conflicting pass left all thirty-two of
    them untouched.
    """
    _run(lambda: store.ensure_rows(real_notes()))
    _run(store.resolve_all)
    return store


def _rows_for(store: IngredientMappingStore, recipe_note: str) -> tuple[Any, ...]:
    return _run(lambda: store.rows_for(recipe_note))


def _raw_rows(settings: Settings) -> list[tuple[Any, ...]]:
    """Every row, read straight from SQLite.

    Straight SQL on purpose: `rows_for` decodes the audit trail, and a test that
    cannot observe a raw column is a test that cannot prove what was written.
    """

    async def read() -> list[tuple[Any, ...]]:
        async with connect_db(settings) as conn:
            cursor = await conn.execute(
                "SELECT id, recipe_note, ingredient_index, raw_value, parsed_name,"
                " parse_method, match_method, match_tier, pantry_item_id, confidence,"
                " candidates_json, created_at, updated_at"
                " FROM ingredient_mappings ORDER BY id"
            )
            return [tuple(row) for row in await cursor.fetchall()]

    return _run(read)


def _column_names(settings: Settings, table: str) -> tuple[str, ...]:
    async def read() -> tuple[str, ...]:
        async with connect_db(settings) as conn:
            cursor = await conn.execute(f"PRAGMA table_info({table})")
            return tuple(str(row[1]) for row in await cursor.fetchall())

    return _run(read)


def _execute(settings: Settings, statement: str, parameters: tuple[Any, ...] = ()) -> None:
    """One statement, committed, on its own connection."""

    async def run() -> None:
        async with connect_db(settings) as conn:
            await conn.execute(statement, parameters)
            await conn.commit()

    _run(run)


def _synthetic_note(note_name: str, materials: Sequence[str]) -> RecipeNote:
    """A recipe note with the given `材料` and nothing else, built from bytes.

    Through `parse_recipe` rather than by constructing a `RecipeNote`, so a fixture
    cannot hold a shape the vault reader would never produce — which is the
    difference between testing the store and testing a store-shaped object.
    """
    listed = "\n".join(f"  - {value}" for value in materials)
    source = f"---\n材料:\n{listed}\n---\n\n# 3 步骤\ndo the thing\n".encode()
    return parse_recipe(source, note_name=note_name, note_path=f"fake/{note_name}.md")


def _with_conflict(store: IngredientMappingStore) -> RecipeNote:
    """Add the F2 fixture to the table: two `材料` resolving to the same item.

    Both values are real corpus values, so the collision is a real outcome of the
    real ladder rather than an artifact of a synthetic catalog. Slots 2 and 3
    resolve to two *different* Pantry Items, which is what makes "the other slots
    of this recipe still resolved" checkable.
    """
    note = _synthetic_note("双空心菜", CONFLICT_MATERIALS)
    _run(lambda: store.ensure_rows([note]))
    return note


#: The F2 fixture's `材料`: slots 0 and 1 both resolve to 83, slots 2 and 3 to 139
#: and 14 respectively.
CONFLICT_MATERIALS: Final = ("空心菜", "空心菜", "红苋菜", "🥦")
#: What the shipped ladder resolves each of them to. Slot 1 collides with slot 0.
CONFLICT_EXPECTED: Final = (83, None, 139, 14)


# --- 1. ensure_rows --------------------------------------------------------


def test_ensure_rows_materializes_one_unresolved_row_per_material_slot(
    settings: Settings, store: IngredientMappingStore
) -> None:
    notes = real_notes()
    _run(lambda: store.ensure_rows(notes))
    rows = _raw_rows(settings)

    assert len(rows) == REAL_SLOT_COUNT
    for row in rows:
        # `match_method='unresolved'` is what makes a never-resolved slot a
        # *state* the repair list can find rather than an absence.
        assert row[6] == "unresolved"
        assert row[7] == 0
        assert row[8] is None
        assert row[9] == 0.0
        assert row[10] == "[]"
    expected = [
        (note.note_path, entry.index, entry.raw)
        for note in notes
        for entry in note.ingredients
    ]
    assert [(row[1], row[2], row[3]) for row in rows] == expected


def test_ensure_rows_carries_the_shipped_parser_provenance(
    settings: Settings, store: IngredientMappingStore
) -> None:
    # §9.6's `parse_method` / `parsed_name` land on the row *before* any pass, so a
    # slot that never resolves is still auditable. A store that wrote its own parse
    # result here would be a second answer, and it would drift the first time the
    # parser changed.
    note = _synthetic_note("三种形状", ("空心菜", "'[[Mackerel]]'", "🌶️/Shishito"))
    _run(lambda: store.ensure_rows([note]))

    assert [(row[4], row[5]) for row in _raw_rows(settings)] == [
        ("空心菜", "bare"),
        ("Mackerel", "wikilink"),
        ("Shishito", "emoji_alt"),
    ]


def test_ensure_rows_materializes_no_seasoning_slot(
    store: IngredientMappingStore,
) -> None:
    # `材料` and `调料` stay apart: the table's `ingredient_index` is a `材料` index
    # and a Seasoning is not an Ingredient. A row for a `调料` slot would give the
    # staples tier a slot to resolve into and inflate the `材料` headline.
    note = parse_recipe(
        "---\n材料:\n  - 空心菜\n调料:\n  - 盐\n  - 蚝油\n---\n\n# 3 步骤\n".encode(),
        note_name="拌空心菜",
        note_path="fake/拌空心菜.md",
    )
    assert (len(note.ingredients), len(note.seasonings)) == (1, 2)

    _run(lambda: store.ensure_rows([note]))

    rows = _rows_for(store, "fake/拌空心菜.md")
    assert len(rows) == 1
    assert rows[0].ingredient_index == 0
    assert rows[0].raw_value == "空心菜"


def test_ensure_rows_is_idempotent_across_two_runs(
    settings: Settings, store: IngredientMappingStore
) -> None:
    notes = real_notes()
    _run(lambda: store.ensure_rows(notes))
    first = _raw_rows(settings)

    _run(lambda: store.ensure_rows(notes))
    second = _raw_rows(settings)

    # Strong idempotence: same row ids, same `created_at`, same `updated_at`. A
    # second run that rewrote identical state would move the stamps, and a
    # `created_at` that moved would mean the row is being re-created rather than
    # left alone — which is what makes a per-request `ensure_rows` safe.
    assert first == second
    assert len(second) == REAL_SLOT_COUNT


def test_ensure_rows_on_no_recipes_writes_nothing(
    settings: Settings, store: IngredientMappingStore
) -> None:
    _run(lambda: store.ensure_rows([]))
    _run(lambda: store.ensure_rows(()))
    assert _raw_rows(settings) == []


def test_ensure_rows_tolerates_a_note_with_no_ingredients(
    store: IngredientMappingStore,
) -> None:
    # The vault's own `Recipes.md` carries no `材料` at all, so an empty list is an
    # ordinary shape and must not be an error.
    note = parse_recipe(
        b"---\nname: Recipes\n---\n\n# 1 \xe8\x8c\x83\xe7\x9b\x98\n",
        note_name="Recipes",
        note_path="fake/Recipes.md",
    )
    assert note.ingredients == ()
    _run(lambda: store.ensure_rows([note]))


# --- 2. the positive half of F2: the direction of the uniqueness ------------


def test_kongxincai_83_may_resolve_in_three_recipes_simultaneously(
    store: IngredientMappingStore,
) -> None:
    """F2 part 1: the inverted index is scoped to the recipe, and must stay so.

    This is the test that holds the uniqueness *direction* in place. A bare
    `UNIQUE(pantry_item_id)` — the `nutrition-intake` pattern F2 explicitly
    forbids — permits unlimited `NULL`s but one row per non-null id, so
    `拌空心菜`, `煮菜菜` and `花蛤拌饭` would become mutually exclusive and one of
    them would come back `unresolved` with no error, no log, and no way for the
    user to tell. The failure is silent, which is the whole reason it is a
    first-class requirement and not a footnote.
    """
    _run(lambda: store.ensure_rows(real_notes()))
    report = _run(store.resolve_all)

    assert report.duplicate_slot_conflicts == 0
    for path in KONGXINCAI_RECIPES:
        rows = _rows_for(store, path)
        index = KONGXINCAI_INDEX[path]
        assert rows[index].pantry_item_id == KONGXINCAI_ITEM_ID
        assert rows[index].match_method == "synonym"


def test_the_committed_corpus_does_not_collide(
    store: IngredientMappingStore,
) -> None:
    # F2's recorded evidence, asserted rather than trusted. 32 slots, and the two
    # closest calls — `花蛤拌饭` with four, `微波菜菜` with three — stay distinct. A
    # future reader must not mistake the constraint for an active guard.
    _run(lambda: store.ensure_rows(real_notes()))
    report = _run(store.resolve_all)

    assert report.reconsidered == REAL_SLOT_COUNT
    assert report.duplicate_slot_conflicts == 0
    assert report.stale_reset == 0
    assert report.resolved == REAL_RESOLVED
    assert report.resolved + report.still_unresolved == report.reconsidered
    assert report.conflicts == ()

    # The two closest calls, spelled out. "The constraint never fired" is only
    # evidence if the constraint could have.
    # `花蛤拌饭`: `Clam` and `🍄/香菇` and `🌶️/Shishito` all miss, `空心菜` hits 83.
    # `Clam` is *not* 143 — the segment guard refuses both 143 and 161, and the
    # matcher's own measured table says so. The ticket's parenthetical
    # ("Clam→143") is not what the shipped ladder does, and it is not a defect in
    # it: fewer resolutions is strictly safer for a uniqueness constraint.
    assert [row.pantry_item_id for row in _rows_for(store, "fake/花蛤拌饭.md")] == [
        None,
        None,
        None,
        83,
    ]
    # `微波菜菜`: `Kale` has three same-family candidates (10, 36, 56) and the
    # lowest id wins, which is the closest call in the corpus. `娃娃菜` has a
    # synonym entry but the catalog holds no "Baby Chinese Cabbage", so it misses.
    assert [row.pantry_item_id for row in _rows_for(store, "fake/微波菜菜.md")] == [
        56,
        None,
        None,
    ]


def test_a_second_pass_over_the_committed_corpus_is_a_fixed_point(
    settings: Settings, store: IngredientMappingStore
) -> None:
    _run(lambda: store.ensure_rows(real_notes()))
    first_report = _run(store.resolve_all)
    first_rows = _raw_rows(settings)

    second_report = _run(store.resolve_all)
    second_rows = _raw_rows(settings)

    assert first_report == second_report
    # Identical down to `updated_at`, which is only true because a pass skips the
    # write when a slot's resolution is unchanged. A pass that rewrote identical
    # state would stamp every row, and "idempotent" would mean nothing.
    assert first_rows == second_rows


# --- 3. F2's per-slot catch, all six properties ----------------------------


def test_the_conflicting_pass_commits_the_whole_table(
    settings: Settings, seeded: IngredientMappingStore
) -> None:
    control = _raw_rows(settings)
    assert len(control) == REAL_SLOT_COUNT

    _with_conflict(seeded)
    report = _run(seeded.resolve_all)

    assert report.reconsidered == REAL_SLOT_COUNT + len(CONFLICT_MATERIALS)
    assert len(_raw_rows(settings)) == REAL_SLOT_COUNT + len(CONFLICT_MATERIALS)


def test_f2_property_1_the_first_slot_commits_normally(
    seeded: IngredientMappingStore,
) -> None:
    _with_conflict(seeded)
    _run(seeded.resolve_all)

    first = _rows_for(seeded, "fake/双空心菜.md")[0]
    assert first.ingredient_index == 0
    assert first.pantry_item_id == CONFLICT_EXPECTED[0]
    assert first.match_method == "synonym"
    assert first.match_tier == 6
    assert first.confidence == pytest.approx(0.7)
    assert [c.reason for c in first.candidates] == [MATCHED_REASON]


def test_f2_property_2_the_second_slot_is_downgraded_to_unresolved(
    seeded: IngredientMappingStore,
) -> None:
    _with_conflict(seeded)
    _run(seeded.resolve_all)

    second = _rows_for(seeded, "fake/双空心菜.md")[1]
    assert second.ingredient_index == 1
    assert second.match_method == "unresolved"
    assert second.match_tier == 0
    assert second.pantry_item_id is None
    assert second.confidence == 0.0
    assert not second.resolved
    assert not second.is_manual


def test_f2_property_3_the_conflict_is_recorded_in_candidates_json(
    seeded: IngredientMappingStore,
) -> None:
    _with_conflict(seeded)
    _run(seeded.resolve_all)

    second = _rows_for(seeded, "fake/双空心菜.md")[1]
    conflict = second.conflict()
    assert conflict is not None, "the dropped resolution must be auditable, not invisible"
    assert conflict.reason == DUPLICATE_SLOT_CONFLICT
    assert conflict.pantry_item_id == CONFLICT_EXPECTED[0]
    assert conflict.canonical_name == "空心菜嫩苗 0.95-1.05 磅"
    # The name *and* the id of the product that was lost are both recoverable,
    # which is what lets the UI say why the chip is unresolved.
    assert conflict.canonical_name
    assert conflict.pantry_item_id == 83
    assert second.candidates == (conflict,)


def test_f2_property_4_the_report_counts_the_conflict_and_names_it(
    seeded: IngredientMappingStore,
) -> None:
    _with_conflict(seeded)
    report = _run(seeded.resolve_all)

    assert report.duplicate_slot_conflicts == 1
    assert len(report.conflicts) == 1
    conflict = report.conflicts[0]
    assert conflict.recipe_note == "fake/双空心菜.md"
    assert conflict.ingredient_index == 1
    assert conflict.raw_value == "空心菜"
    assert conflict.pantry_item_id == 83
    assert conflict.canonical_name == "空心菜嫩苗 0.95-1.05 磅"
    assert conflict.reason == DUPLICATE_SLOT_CONFLICT
    # The downgraded slot is counted as unresolved, not resolved: the table holds
    # it as unresolved, and a report claiming otherwise would be a
    # plausible-looking wrong answer about the user's own pantry.
    assert report.reconsidered == REAL_SLOT_COUNT + len(CONFLICT_MATERIALS)
    assert report.resolved == REAL_RESOLVED + len(CONFLICT_EXPECTED) - 1
    assert report.still_unresolved == (
        REAL_SLOT_COUNT + len(CONFLICT_MATERIALS) - report.resolved
    )
    assert report.resolved + report.still_unresolved == report.reconsidered


def test_f2_property_5_every_other_slot_of_the_recipe_still_resolved(
    seeded: IngredientMappingStore,
) -> None:
    _with_conflict(seeded)
    _run(seeded.resolve_all)

    rows = _rows_for(seeded, "fake/双空心菜.md")
    # Slots 0, 2 and 3 resolve to three *different* Pantry Items, in the same
    # recipe, in the same transaction as the one that collided.
    assert [row.pantry_item_id for row in rows] == list(CONFLICT_EXPECTED)
    assert [row.match_method for row in rows] == [
        "synonym",
        "unresolved",
        "synonym",
        "synonym",
    ]


def test_f2_property_5_the_whole_table_commits_not_just_the_conflicting_recipe(
    settings: Settings, seeded: IngredientMappingStore
) -> None:
    """The decisive one: a per-slot catch, distinguished from an abort.

    A transaction-wide abort would roll every row back to its pre-pass state. So
    the thirty-two committed rows are captured *before* the conflicting recipe
    exists and compared byte-for-byte *after* the pass that hit the violation. If
    the catch aborted the transaction, the downgraded row would not be in the table
    at all and no report would have been returned at all.
    """
    control = _raw_rows(settings)
    assert len(control) == REAL_SLOT_COUNT
    control_resolved = [row for row in control if row[8] is not None]
    assert len(control_resolved) == REAL_RESOLVED

    _with_conflict(seeded)
    report = _run(seeded.resolve_all)

    after = _raw_rows(settings)
    assert report.duplicate_slot_conflicts == 1
    assert len(after) == REAL_SLOT_COUNT + len(CONFLICT_MATERIALS)
    # Byte-identical: same ids, same `updated_at`, same everything. This is the
    # evidence that the transaction *committed* rather than rolled back.
    assert after[:REAL_SLOT_COUNT] == control
    assert [row for row in after[:REAL_SLOT_COUNT] if row[8] is not None] == control_resolved


def test_f2_property_6_a_second_pass_is_idempotent_on_the_downgraded_slot(
    settings: Settings, seeded: IngredientMappingStore
) -> None:
    _with_conflict(seeded)
    first_report = _run(seeded.resolve_all)
    first = _raw_rows(settings)

    second_report = _run(seeded.resolve_all)
    second = _raw_rows(settings)

    # Byte-identical table, `updated_at` included. The second pass re-derives 83
    # for slot 1, collides again, and finds the row already in exactly the state
    # it would write — so the recovery write is skipped and the stamp does not
    # move. A pass that rewrote identical state is not idempotent.
    assert first == second
    assert first_report == second_report
    assert [row[8] for row in second if row[1] == "fake/双空心菜.md"] == list(
        CONFLICT_EXPECTED
    )
    # The conflict is re-counted, and that is the point: the defect is in the
    # user's note and is still there, so it stays visible. What must never happen
    # is a conflict touching a row that had already committed cleanly.
    assert second_report.duplicate_slot_conflicts == 1
    assert second_report.conflicts[0].ingredient_index == 1


def test_the_downgraded_slot_reaches_the_repair_list(
    seeded: IngredientMappingStore,
) -> None:
    # F2 chose a *visible* degradation: the slot lands in `unresolved_rows()`, so
    # the "re-resolve the unresolved ones" action and `/api/recipes` both see it.
    # A conflict visible only in `candidates_json` would need the 调试 view to
    # surface, which is not the same as being findable.
    _with_conflict(seeded)
    _run(seeded.resolve_all)

    slots = {
        (row.recipe_note, row.ingredient_index) for row in _run(seeded.unresolved_rows)
    }
    assert ("fake/双空心菜.md", 1) in slots
    assert ("fake/双空心菜.md", 0) not in slots


def test_the_slot_update_carries_neither_or_rollback_nor_or_fail() -> None:
    """The static contract, and the reason it is a contract at all.

    SQLite's `ON CONFLICT ROLLBACK` and `ON CONFLICT FAIL` both undo the
    *enclosing* transaction; the default `ABORT` undoes only the offending
    statement. Either on the slot `UPDATE` would silently reinstate the exact
    failure F2's override exists to prevent — one duplicated `材料` reverting all
    sixteen recipes' mappings — and it would do so invisibly, because the pass
    would still return a report. `OR IGNORE` is the third spelling of the same bug:
    it discards the write instead, which is a silently dropped resolution with no
    audit record either.
    """
    for statement in (_UPDATE_SLOT, _UPDATE_SLOT_CONFLICT):
        collapsed = " ".join(statement.upper().split())
        for forbidden in ("OR ROLLBACK", "OR FAIL", "OR IGNORE", "OR REPLACE", "OR ABORT"):
            assert forbidden not in collapsed, f"{forbidden} on: {collapsed}"
        assert "ON CONFLICT" not in collapsed, "an upsert clause could swallow the violation"
    # The two statements really are two, because the mechanism is that the recovery
    # write is a *new* statement in the still-open transaction.
    assert _UPDATE_SLOT is not _UPDATE_SLOT_CONFLICT
    # The recovery write is the one that nulls the id, which is what clears the
    # unique-index violation.
    assert "pantry_item_id = NULL" in _UPDATE_SLOT_CONFLICT
    assert "match_tier = 0" in _UPDATE_SLOT_CONFLICT
    assert "confidence = 0.0" in _UPDATE_SLOT_CONFLICT
    assert "duplicate_slot_conflict" not in _UPDATE_SLOT
    # And the insert names its conflict target rather than blanket-ignoring, for
    # the same reason: a fresh row can only violate the slot index, and a future
    # constraint must still be able to abort it.
    assert "ON CONFLICT (recipe_note, ingredient_index) DO NOTHING" in _INSERT_SLOT
    assert "OR IGNORE" not in _INSERT_SLOT


def test_resolve_recipe_uses_the_same_per_slot_catch(
    store: IngredientMappingStore,
) -> None:
    # §9.10.1 #7: the override is not specific to the full pass. Without this, the
    # targeted form would be the one entry point where a duplicate is a 500.
    _with_conflict(store)
    report = _run(lambda: store.resolve_recipe("fake/双空心菜.md"))

    assert report.reconsidered == len(CONFLICT_MATERIALS)
    assert report.duplicate_slot_conflicts == 1
    assert report.resolved == len(CONFLICT_EXPECTED) - 1
    assert [row.pantry_item_id for row in _rows_for(store, "fake/双空心菜.md")] == list(
        CONFLICT_EXPECTED
    )


def test_resolve_ingredient_uses_the_same_per_slot_catch(
    store: IngredientMappingStore,
) -> None:
    _with_conflict(store)
    # Slot 0 first, so the collision slot 1 will meet is real rather than latent.
    first = _run(lambda: store.resolve_ingredient("fake/双空心菜.md", 0))
    assert (first.reconsidered, first.resolved, first.duplicate_slot_conflicts) == (1, 1, 0)

    report = _run(lambda: store.resolve_ingredient("fake/双空心菜.md", 1))

    assert report.reconsidered == 1
    assert report.duplicate_slot_conflicts == 1
    assert report.resolved == 0
    assert report.still_unresolved == 1
    assert report.conflicts[0].ingredient_index == 1
    assert _rows_for(store, "fake/双空心菜.md")[1].pantry_item_id is None


def test_resolve_ingredient_on_a_clean_slot_reports_no_conflict(
    store: IngredientMappingStore,
) -> None:
    _with_conflict(store)
    _run(store.resolve_all)
    report = _run(lambda: store.resolve_ingredient("fake/双空心菜.md", 0))
    assert report == ResolveReport(reconsidered=1, resolved=1)


# --- 4. F6: a hand fix is a property of the row ----------------------------


def test_resolve_all_skips_manual_rows_entirely(
    settings: Settings, store: IngredientMappingStore
) -> None:
    _run(lambda: store.ensure_rows(real_notes()))
    _run(store.resolve_all)
    # `香菇` resolves to nothing, so this is a hand fix the algorithm would
    # otherwise keep re-deriving: the store is saving the user a fight.
    manual = _run(lambda: store.set_manual("fake/微波菜菜.md", 1, 22))
    assert manual.is_manual
    assert manual.pantry_item_id == 22
    before = {(row[1], row[2]): row for row in _raw_rows(settings)}
    stamped = before[("fake/微波菜菜.md", 1)]

    report = _run(store.resolve_all)

    after = {(row[1], row[2]): row for row in _raw_rows(settings)}
    # Every field identical, `created_at` and `updated_at` included: the pass did
    # not merely produce the same values, it never wrote to the row.
    assert after[("fake/微波菜菜.md", 1)] == stamped
    # And the pass's own accounting says so: 32 rows, one of them skipped.
    assert report.reconsidered == REAL_SLOT_COUNT - 1
    assert report.resolved + report.still_unresolved == report.reconsidered
    assert _rows_for(store, "fake/微波菜菜.md")[1].is_manual


def test_a_stale_manual_row_is_reported_but_never_reset(
    store: IngredientMappingStore, catalog_box: _CatalogBox
) -> None:
    # F6 against §7.3: an algorithm may not overwrite a hand fix, so a manual row
    # whose id was renumbered away is surfaced to the user instead of quietly
    # reset. This is the one place the two rules pull against each other, and F6
    # wins — which is also why `stale_reset` counts non-manual rows only.
    _run(lambda: store.ensure_rows(real_notes()))
    _run(store.resolve_all)
    _run(lambda: store.set_manual("fake/微波菜菜.md", 1, 22))

    catalog_box.snapshot = build_snapshot([(9_999, "空心菜嫩苗 0.95-1.05 磅", "1.1", "[]", None)])

    # Every one of the nine resolved rows goes stale, plus the hand fix.
    stale = _run(store.stale_rows)
    assert len(stale) == REAL_RESOLVED + 1
    manual_stale = [row for row in stale if row.is_manual]
    assert [row.recipe_note for row in manual_stale] == ["fake/微波菜菜.md"]
    assert manual_stale[0].pantry_item_id == 22

    report = _run(store.resolve_all)

    assert report.stale_reset == REAL_RESOLVED
    assert report.reconsidered == REAL_SLOT_COUNT - 1
    survivor = _rows_for(store, "fake/微波菜菜.md")[1]
    assert (survivor.pantry_item_id, survivor.is_manual) == (22, True)
    # Still reported, because nothing else will ever repair it.
    assert [row.recipe_note for row in _run(store.stale_rows)] == ["fake/微波菜菜.md"]


def test_the_schema_trigger_refuses_an_update_of_a_manual_row(
    settings: Settings, store: IngredientMappingStore
) -> None:
    """F6's evidence, and the reason `set_manual`'s delete is mandatory.

    The guarantee is in the schema, not in a call site, so it holds for a
    backfill, a repair script, and a hand-edited `sqlite3` session exactly as it
    does for this module. Asserted against a raw statement rather than against
    `set_manual`, because the claim is that nothing *can* update the row.
    """
    _run(lambda: store.ensure_rows(real_notes()))
    _run(store.resolve_all)
    _run(lambda: store.set_manual("fake/微波菜菜.md", 1, 22))

    with pytest.raises(sqlite3.IntegrityError, match="manual"):
        _execute(
            settings,
            "UPDATE ingredient_mappings SET pantry_item_id = ?"
            " WHERE recipe_note = ? AND ingredient_index = ?",
            (99, "fake/微波菜菜.md", 1),
        )
    assert _rows_for(store, "fake/微波菜菜.md")[1].pantry_item_id == 22


def test_delete_and_insert_is_the_only_way_to_change_a_manual_row(
    settings: Settings, store: IngredientMappingStore
) -> None:
    # The mandatory half of `set_manual`, asserted as a two-statement sequence
    # rather than through the method, so a future refactor that "optimizes" the
    # delete away is caught here rather than in production.
    _run(lambda: store.ensure_rows(real_notes()))
    _run(store.resolve_all)
    _run(lambda: store.set_manual("fake/微波菜菜.md", 1, 22))

    def replace(item_id: int) -> Any:
        async def scenario() -> Any:
            async with connect_db(settings) as conn:
                await conn.execute(
                    "DELETE FROM ingredient_mappings"
                    " WHERE recipe_note = ? AND ingredient_index = ?",
                    ("fake/微波菜菜.md", 1),
                )
                await conn.execute(
                    "INSERT INTO ingredient_mappings"
                    " (recipe_note, ingredient_index, raw_value, parsed_name,"
                    "  parse_method, match_method, match_tier, pantry_item_id, confidence)"
                    " VALUES (?, 1, '香菇', '香菇', 'bare', 'manual', 0, ?, 1.0)",
                    ("fake/微波菜菜.md", item_id),
                )
                await conn.commit()
            return (await store.rows_for("fake/微波菜菜.md"))[1]

        return _run(scenario)

    assert replace(22).pantry_item_id == 22
    # 56 is `Kale`, which slot 0 of this same recipe already holds, so F2's
    # inverted index refuses a hand fix to it too — the constraint is not a
    # property of the algorithm's writes. 139 is free in this recipe.
    changed = replace(139)
    assert (changed.pantry_item_id, changed.is_manual) == (139, True)
    # Still skipped by a pass, because the replacement is itself a manual row and
    # the skip is a property of the row rather than of the row that used to be
    # there.
    assert _run(store.resolve_all).reconsidered == REAL_SLOT_COUNT - 1
    assert _rows_for(store, "fake/微波菜菜.md")[1].pantry_item_id == 139


def test_set_manual_validates_the_id_against_the_live_catalog(
    store: IngredientMappingStore,
) -> None:
    # A `manual` row is immutable, so a typo accepted here could only be repaired
    # by another delete-and-insert — and until then it renders a chip claiming a
    # product that does not exist.
    _run(lambda: store.ensure_rows(real_notes()))
    with pytest.raises(UnknownPantryItem) as caught:
        _run(lambda: store.set_manual("fake/拌空心菜.md", 0, 4_242_424))
    assert caught.value.code == "unknown_pantry_item"
    untouched = _rows_for(store, "fake/拌空心菜.md")
    assert [row.match_method for row in untouched] == ["unresolved"]
    assert untouched[0].pantry_item_id is None


def test_set_manual_refuses_a_slot_the_table_has_no_row_for(
    store: IngredientMappingStore,
) -> None:
    # `ensure_rows` is how a slot comes into being. Inventing a `raw_value` for a
    # slot this app never read would put a frontmatter string into its own audit
    # anchor, the one field D1 says is never in doubt.
    _run(lambda: store.ensure_rows(real_notes()))
    with pytest.raises(UnknownIngredientSlot) as caught:
        _run(lambda: store.set_manual("fake/从未存在.md", 0, 83))
    assert caught.value.code == "unknown_ingredient_slot"

    with pytest.raises(UnknownIngredientSlot):
        _run(lambda: store.set_manual("fake/拌空心菜.md", 9, 83))


def test_set_manual_preserves_the_frontmatter_provenance_verbatim(
    store: IngredientMappingStore,
) -> None:
    # A hand fix changes *which product this Ingredient is*, not what the
    # frontmatter says. Overwriting the audit anchor with the repair would destroy
    # the evidence the 调试 view exists to show — byte-for-byte, which for
    # `烤鲭鱼` means the `[[…]]` wikilink shape survives rather than being
    # normalized into a bare name.
    _run(lambda: store.ensure_rows(real_notes()))
    manual = _run(lambda: store.set_manual("fake/烤鲭鱼.md", 0, 22))

    assert manual.raw_value == "[[Mackerel]]"
    assert (manual.parsed_name, manual.parse_method) == ("Mackerel", "wikilink")
    # Tier 0 is the schema's "no tier" for a hand fix and 1.0 the full claim. It
    # is not a *unique* 1.0 — tiers 1 and 2 also score 1.0 — which is exactly why
    # `match_method` and not `confidence` is the discriminator between a hand fix
    # and a first-tier match.
    assert manual.match_tier == 0
    assert manual.confidence == 1.0
    assert manual.candidates == ()


def test_set_manual_is_refused_when_the_recipe_already_claims_that_item(
    store: IngredientMappingStore,
) -> None:
    # F2's inverted index is not a property of the algorithm's writes. `Kale` holds
    # id 56 in `微波菜菜`, so binding slot 1 to it collides — and because the
    # `DELETE` and the `INSERT` share one transaction, the refusal leaves the
    # previous row standing rather than emptying the slot. A hand fix is not a way
    # around "one recipe lists the same Pantry Item twice"; the user who wants
    # that outcome edits the note.
    _run(lambda: store.ensure_rows(real_notes()))
    _run(store.resolve_all)
    assert _rows_for(store, "fake/微波菜菜.md")[0].pantry_item_id == 56

    with pytest.raises(sqlite3.IntegrityError, match="ux_ingredient|UNIQUE"):
        _run(lambda: store.set_manual("fake/微波菜菜.md", 1, 56))

    after = _rows_for(store, "fake/微波菜菜.md")
    assert len(after) == 3, "the DELETE rolled back; the slot was not emptied"
    assert after[1].match_method == "unresolved"
    assert after[1].pantry_item_id is None


def test_set_manual_leaves_the_slot_count_unchanged(
    store: IngredientMappingStore,
) -> None:
    _run(lambda: store.ensure_rows(real_notes()))
    _run(store.resolve_all)
    first = _run(lambda: store.set_manual("fake/拌空心菜.md", 0, 83))
    second = _run(lambda: store.set_manual("fake/拌空心菜.md", 0, 83))
    # The slot count is unchanged — a re-map replaces its slot, never adds one.
    assert len(_rows_for(store, "fake/拌空心菜.md")) == 1
    # Everything except the stamps is identical, and the stamps are the point:
    # delete-and-insert means a re-map is a *new row*, auditable as one, rather
    # than a silent overwrite of the old one.
    assert first.recipe_note == second.recipe_note
    assert (first.ingredient_index, first.raw_value) == (
        second.ingredient_index,
        second.raw_value,
    )
    assert (first.pantry_item_id, first.match_method) == (second.pantry_item_id, "manual")
    assert second.created_at >= first.created_at


# --- 5. catalog drift: the renumbered id ----------------------------------


def test_a_renumbered_catalog_id_is_detected_and_re_resolved(
    store: IngredientMappingStore, catalog_box: _CatalogBox
) -> None:
    """§7.3's substitute for the cross-database foreign key the schema cannot have.

    The producer re-imports and `AUTOINCREMENT` renumbers `items.id`, so a mapping
    can end up pointing at a *different product* — not merely dangling, which would
    at least be visible. Re-resolution is what repairs it, and the repair is
    assertable here only because the store takes a snapshot provider.
    """
    _run(lambda: store.ensure_rows(real_notes()))
    _run(store.resolve_all)
    assert _rows_for(store, "fake/拌空心菜.md")[0].pantry_item_id == KONGXINCAI_ITEM_ID
    assert _run(store.stale_rows) == ()

    # The catalog is re-imported and 83 is gone; only that product survives, under
    # a new id. That is the silent wrong pointer this whole section exists for.
    catalog_box.snapshot = build_snapshot([(9_999, "空心菜嫩苗 0.95-1.05 磅", "1.1", "[]", None)])

    # Every one of the nine resolved rows is now stale, because the replacement
    # catalog has one row in it.
    stale = _run(store.stale_rows)
    assert len(stale) == REAL_RESOLVED
    assert {row.recipe_note for row in stale} >= set(KONGXINCAI_RECIPES)
    # Nothing has changed the rows yet: detection and repair are two steps, and the
    # slot is still *resolved* — which is why the count must be surfaced.
    assert _rows_for(store, "fake/拌空心菜.md")[0].pantry_item_id == KONGXINCAI_ITEM_ID

    report = _run(store.resolve_all)

    assert report.stale_reset == REAL_RESOLVED
    assert report.stale_reset <= report.reconsidered
    for path in KONGXINCAI_RECIPES:
        assert (
            _rows_for(store, path)[KONGXINCAI_INDEX[path]].pantry_item_id == 9_999
        )
    assert _run(store.stale_rows) == ()


def test_an_id_the_ladder_cannot_refind_ends_up_unresolved(
    store: IngredientMappingStore, catalog_box: _CatalogBox
) -> None:
    # The other half of the repair: the product is gone from the catalog
    # altogether, so the row resets to `unresolved` and lands in the repair list
    # rather than pointing at nothing.
    _run(lambda: store.ensure_rows(real_notes()))
    _run(store.resolve_all)
    catalog_box.snapshot = build_snapshot([(9_999, "某种全新的商品", "1.1", "[]", None)])

    assert len(_run(store.stale_rows)) == REAL_RESOLVED
    report = _run(store.resolve_all)

    assert report.stale_reset == REAL_RESOLVED
    assert report.resolved == 0
    assert report.still_unresolved == report.reconsidered
    row = _rows_for(store, "fake/拌空心菜.md")[0]
    assert row.pantry_item_id is None
    assert row.match_method == "unresolved"
    assert row.match_tier == 8
    assert ("fake/拌空心菜.md", 0) in {
        (found.recipe_note, found.ingredient_index) for found in _run(store.unresolved_rows)
    }


def test_catalog_changed_is_false_after_a_pass_and_true_after_one(
    store: IngredientMappingStore, catalog_box: _CatalogBox
) -> None:
    # §9.11.2's boot-time signal. `True` before anything has run, because a pass is
    # owed; `False` after one against this revision; `True` again the moment the
    # revision moves; `False` again once the pass has run against the new one.
    assert store.catalog_changed is True
    _run(lambda: store.ensure_rows(real_notes()))
    _run(store.resolve_all)
    assert store.catalog_changed is False

    catalog_box.snapshot = build_snapshot([(9_999, "空心菜嫩苗 0.95-1.05 磅", "1.1", "[]", None)])
    assert store.catalog_changed is True

    _run(store.resolve_all)
    assert store.catalog_changed is False


def test_an_unchanged_catalog_does_not_trigger_a_re_resolution(
    store: IngredientMappingStore,
) -> None:
    # The `PantryCatalog` TTL bounds staleness; the revision is a *change signal*.
    # A rebuild that changes nothing must cost a wasted rebuild, not a re-resolve
    # of every mapping. Provable here only through the revision being stable,
    # which is exactly what the comparison reads.
    _run(lambda: store.ensure_rows(real_notes()))
    _run(store.resolve_all)
    for _ in range(3):
        assert store.catalog_changed is False


def test_invalidate_forces_the_next_pass(store: IngredientMappingStore) -> None:
    # "Called after any write that changes stock or recipes." Deliberately
    # conservative: a write that cannot renumber an `items.id` still costs one
    # wasted pass rather than risking a skipped one.
    _run(lambda: store.ensure_rows(real_notes()))
    _run(store.resolve_all)
    assert store.catalog_changed is False

    store.invalidate()

    assert store.catalog_changed is True
    assert "resolved_revision=None" in repr(store)


def test_a_targeted_resolve_does_not_claim_the_whole_table_is_current(
    store: IngredientMappingStore,
) -> None:
    # Only `resolve_all` records the revision. A targeted resolve that marked the
    # catalog current would suppress the boot-time pass that repairs the other
    # fifteen recipes — a silent wrong answer one level above the one F2 guards.
    _run(lambda: store.ensure_rows(real_notes()))
    _run(store.resolve_all)
    assert store.catalog_changed is False

    _run(lambda: store.resolve_recipe("fake/拌空心菜.md"))
    assert store.catalog_changed is False

    _run(lambda: store.resolve_ingredient("fake/拌空心菜.md", 0))
    assert store.catalog_changed is False

    _run(lambda: store.set_manual("fake/微波菜菜.md", 1, 22))
    assert store.catalog_changed is False


# --- 6. provenance ---------------------------------------------------------


def test_candidates_json_round_trips_a_rejected_candidate(
    store: IngredientMappingStore,
) -> None:
    # D1's first mitigation: nothing is ever *un*-debuggable. `土豆` reaches
    # `好丽友 呀!土豆 薯条` (62) and is refused, so the row is unresolved *and* says
    # which row it nearly was and why that row was refused.
    _run(lambda: store.ensure_rows(real_notes()))
    _run(store.resolve_all)

    row = _rows_for(store, "fake/烤土豆.md")[0]
    assert row.pantry_item_id is None
    refused = [c for c in row.candidates if c.pantry_item_id == 62]
    assert len(refused) == 1
    assert refused[0].canonical_name == "好丽友 呀!土豆 薯条 里脊牛排味 70 克"
    assert "segment_boundary" in refused[0].rejected_by
    assert row.rejected(62) == refused[0].rejected_by


def test_candidates_json_keeps_every_guard_that_refused_a_row(
    store: IngredientMappingStore,
) -> None:
    # `reason` is the single stable string F2's conflict entry fixes; `rejectedBy`
    # is the complete list, and it is the list a reader must consult. The matcher's
    # audit deliberately records *both* objections to a row — recording only the
    # first would understate what is holding it back.
    _run(lambda: store.ensure_rows(real_notes()))
    _run(store.resolve_all)

    row = _rows_for(store, "fake/Easy Fragrant Fried Rice.md")[1]
    both = [c for c in row.candidates if c.pantry_item_id == 86]
    assert len(both) == 1
    assert set(both[0].rejected_by) == {"segment_boundary", "category_family"}
    assert both[0].reason == both[0].rejected_by[0]


def test_an_adopted_candidate_is_recorded_as_matched_not_refused(
    store: IngredientMappingStore,
) -> None:
    # The audit keeps adopted rows too, so a reader can see what the ladder chose
    # *and* what it walked past. `reason` is the only thing that distinguishes the
    # two, so it has to be right.
    _run(lambda: store.ensure_rows(real_notes()))
    _run(store.resolve_all)

    row = _rows_for(store, "fake/拌空心菜.md")[0]
    assert [c.reason for c in row.candidates] == [MATCHED_REASON]
    assert row.candidates[0].pantry_item_id == KONGXINCAI_ITEM_ID
    assert row.candidates[0].rejected_by == ()


def test_a_hand_corrupted_audit_trail_is_refused_on_read_and_repaired_by_a_pass(
    settings: Settings, store: IngredientMappingStore
) -> None:
    # The only writer of `candidates_json` is this module, so a corrupt one is a
    # hand-edited database. Reading it as "nothing was rejected" would be a
    # plausible-looking wrong answer about provenance, so the read fails closed
    # with a named error — and a pass, which never decodes, repairs it.
    _run(lambda: store.ensure_rows(real_notes()))
    _run(store.resolve_all)
    _execute(
        settings,
        "UPDATE ingredient_mappings SET candidates_json = ?"
        " WHERE recipe_note = ? AND ingredient_index = ?",
        ("not json at all", "fake/炒红苋菜.md", 0),
    )
    with pytest.raises(CorruptMappingRow) as caught:
        _rows_for(store, "fake/炒红苋菜.md")
    assert caught.value.code == "corrupt_ingredient_mapping_row"

    report = _run(store.resolve_all)

    assert report.reconsidered == REAL_SLOT_COUNT
    repaired = _rows_for(store, "fake/炒红苋菜.md")[0]
    assert [c.pantry_item_id for c in repaired.candidates] == [139]
    assert repaired.pantry_item_id == 139


def test_a_candidates_json_that_is_not_a_list_is_refused(
    settings: Settings, store: IngredientMappingStore
) -> None:
    _run(lambda: store.ensure_rows(real_notes()))
    _execute(
        settings,
        "UPDATE ingredient_mappings SET candidates_json = ?"
        " WHERE recipe_note = ? AND ingredient_index = ?",
        ('{"pantryItemId": 1}', "fake/炒红苋菜.md", 0),
    )
    with pytest.raises(CorruptMappingRow, match="not_a_list"):
        _rows_for(store, "fake/炒红苋菜.md")


def test_a_candidate_missing_its_name_is_refused_rather_than_skipped(
    settings: Settings, store: IngredientMappingStore
) -> None:
    # A lenient reader that skipped a missing key would answer "nothing was
    # rejected" about a row that was — the one thing D1 says must never happen.
    _run(lambda: store.ensure_rows(real_notes()))
    _execute(
        settings,
        "UPDATE ingredient_mappings SET candidates_json = ?"
        " WHERE recipe_note = ? AND ingredient_index = ?",
        ('[{"pantryItemId": 139}]', "fake/炒红苋菜.md", 0),
    )
    with pytest.raises(CorruptMappingRow, match="canonicalName"):
        _rows_for(store, "fake/炒红苋菜.md")


# --- 7. the remaining surface ---------------------------------------------


def test_resolving_an_unknown_note_or_slot_is_an_empty_report(
    store: IngredientMappingStore,
) -> None:
    # "Nothing to re-resolve" is the answer, and it is also the answer before
    # `ensure_rows` has run — not an error.
    _run(lambda: store.ensure_rows(real_notes()))
    assert _run(lambda: store.resolve_recipe("fake/从未存在.md")) == ResolveReport()
    assert _run(lambda: store.resolve_ingredient("fake/从未存在.md", 0)) == ResolveReport()
    assert _rows_for(store, "fake/从未存在.md") == ()


def test_resolve_ingredient_of_a_manual_slot_is_a_no_op(
    store: IngredientMappingStore,
) -> None:
    _run(lambda: store.ensure_rows(real_notes()))
    _run(lambda: store.set_manual("fake/拌空心菜.md", 0, 83))
    assert _run(lambda: store.resolve_ingredient("fake/拌空心菜.md", 0)) == ResolveReport()
    assert _rows_for(store, "fake/拌空心菜.md")[0].is_manual


def test_resolve_recipe_leaves_other_recipes_alone(store: IngredientMappingStore) -> None:
    _run(lambda: store.ensure_rows(real_notes()))
    _run(store.resolve_all)
    report = _run(lambda: store.resolve_recipe("fake/微波菜菜.md"))
    assert (report.reconsidered, report.resolved, report.still_unresolved) == (3, 1, 2)


def test_unresolved_rows_is_empty_when_every_slot_resolves(
    store: IngredientMappingStore,
) -> None:
    _run(lambda: store.ensure_rows([_synthetic_note("全中", ("空心菜", "红苋菜", "茼蒿"))]))
    report = _run(store.resolve_all)
    assert (report.reconsidered, report.resolved) == (3, 3)
    assert _run(store.unresolved_rows) == ()


def test_an_unexpected_error_rolls_the_whole_pass_back(
    settings: Settings, store: IngredientMappingStore
) -> None:
    """The *other* half of §9.10.1's table, and the one the override kept.

    The inverted unique index is absorbed per slot. Everything else — a disk
    error, `busy_timeout`, a bug — must still undo the pass, so the previous state
    is intact rather than half-resolved. Asserted on the transaction primitive
    directly, because provoking a real disk error in a unit test is not possible
    and a mocked one would only test the mock.
    """
    assert store is not None  # the fixture is what runs `init_db`
    parameters = ("fake/x.md", 0, "空心菜", "空心菜", "bare")

    async def scenario() -> int:
        async with connect_db(settings) as conn:
            with pytest.raises(RuntimeError, match="boom"):
                async with _transaction(conn):
                    await conn.execute(_INSERT_SLOT, parameters)
                    assert await _count(conn) == 1
                    raise RuntimeError("boom")
            # Same connection, so the rolled-back write is visible as absent.
            return await _count(conn)

    assert _run(scenario) == 0


async def _count(conn: Any) -> int:
    row = await (await conn.execute("SELECT COUNT(*) FROM ingredient_mappings")).fetchone()
    assert row is not None
    return int(row[0])


def test_a_nested_transaction_is_refused_rather_than_joined(
    settings: Settings,
) -> None:
    # "One transaction per pass" is a precondition a reader can see, not an
    # accident: a nested `BEGIN` would raise from SQLite anyway, and the store
    # would rather say why than surface SQLite's wording.
    async def scenario() -> None:
        async with connect_db(settings) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                with pytest.raises(MappingError) as caught:
                    async with _transaction(conn):
                        pass
                assert caught.value.code == "ingredient_mapping_error"
            finally:
                if conn.in_transaction:
                    await conn.rollback()

    _run(scenario)


def test_no_connection_is_left_open_after_a_pass(
    settings: Settings, store: IngredientMappingStore
) -> None:
    # WAL leaves a `-wal` and `-shm` beside the database only while a connection
    # is open, so their absence is the observable. The store opens one per
    # operation; a leaked handle would outlive the request that made it.
    _run(lambda: store.ensure_rows(real_notes()))
    _run(store.resolve_all)
    _run(store.stale_rows)
    _run(store.unresolved_rows)
    _run(lambda: store.rows_for("fake/拌空心菜.md"))

    database = settings.app_data_dir / "recipes.sqlite3"
    for suffix in ("-wal", "-shm"):
        assert not database.with_name(database.name + suffix).exists()
    assert len(_raw_rows(settings)) == REAL_SLOT_COUNT


def test_the_table_carries_no_stock_column(
    settings: Settings, store: IngredientMappingStore
) -> None:
    # §7.3: stock is volatile and deliberately not persisted, so a chip colour
    # derived from this table would go stale the moment the user toggled a task in
    # `Pantry.md`. The ladder is asked *which product*, never *do I have it*.
    _run(lambda: store.ensure_rows(real_notes()))
    _run(store.resolve_all)
    columns = set(_column_names(settings, "ingredient_mappings"))
    assert "in_stock" not in columns
    assert not any("stock" in column for column in columns)
    assert {"pantry_item_id", "match_method", "match_tier", "candidates_json"} <= columns


def test_nothing_here_reads_a_live_path(
    settings: Settings, store: IngredientMappingStore, catalog_box: _CatalogBox
) -> None:
    """The suite-wide property, asserted for this module rather than assumed.

    The producer's `pantry_items.db` lives in a sibling checkout CI does not have,
    and the vault is the user's live, machine-rewritten folder. A test that reached
    for either would pass on one machine and assert nothing on every other one —
    and would start failing the day the user bought something. So: the database is
    under `tmp_path`, and the catalog is the committed 178-row JSON snapshot built
    through the connection-free `build_snapshot`, not a `PantryCatalog` over a
    file.
    """
    database = settings.app_data_dir / "recipes.sqlite3"
    assert database.is_file()
    assert not database.is_relative_to(settings.vault_path)
    assert settings.app_data_dir != settings.vault_path

    # The provider hands out the committed snapshot, and that snapshot is the
    # committed one by identity, not merely by equal count.
    assert catalog_box() is catalog_box.snapshot
    assert catalog_box() is committed_catalog()
    assert len(catalog_box().rows) == 176
    assert catalog_box().total_row_count == 178
    assert catalog_box().excluded_row_count == 2
    assert CATALOG_SNAPSHOT.is_file()


# --- 8. the contract this file and the wheel both depend on ----------------


def test_the_store_ships_as_a_discovered_package() -> None:
    # `pyproject.toml` uses setuptools auto-discovery rather than a hand-kept list,
    # so a subpackage a ticket forgot to register is the only way one could go
    # missing — and it would not surface as a stale registration either. Assert
    # that discovery reaches it, which is the property that makes no
    # `pyproject.toml` edit necessary for this ticket.
    config = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    finder = config["tool"]["setuptools"]["packages"]["find"]
    assert finder["include"] == ["app*"]
    assert finder["namespaces"] is False
    assert (REPO_ROOT / "app" / "mapping" / "__init__.py").is_file()
    assert (REPO_ROOT / "app" / "mapping" / "store.py").is_file()


def test_every_documented_store_method_exists_with_its_promised_name() -> None:
    # §9.10's list, asserted as a list. A rename that left the behaviour intact
    # would still break #14's and #15's wiring, and the failure would surface
    # there rather than here, which is the wrong place to learn a method moved.
    for name in (
        "ensure_rows",
        "resolve_all",
        "resolve_recipe",
        "resolve_ingredient",
        "set_manual",
        "unresolved_rows",
        "stale_rows",
        "invalidate",
    ):
        assert callable(getattr(IngredientMappingStore, name)), name
    # Plus the two things §9.11.2 and the payload need, which §9.10's list has no
    # method for.
    assert isinstance(IngredientMappingStore.catalog_changed, property)
    assert callable(IngredientMappingStore.rows_for)


def test_the_resolve_report_names_all_five_counters() -> None:
    # §9.10.1 #4. Snake-cased here; `app.api.recipes` owns the camelCase wire names
    # and #14 is where those are asserted.
    report = ResolveReport()
    assert (
        report.reconsidered,
        report.resolved,
        report.still_unresolved,
        report.stale_reset,
        report.duplicate_slot_conflicts,
        report.conflicts,
    ) == (0, 0, 0, 0, 0, ())

    assert {f.name for f in fields(ResolveReport)} == {
        "reconsidered",
        "resolved",
        "still_unresolved",
        "stale_reset",
        "duplicate_slot_conflicts",
        "conflicts",
    }
    # A conflict carries enough to be shown, and its reason defaults to F2's.
    conflict = SlotConflict("fake/x.md", 1, "空心菜", 83, "空心菜嫩苗")
    assert conflict.reason == DUPLICATE_SLOT_CONFLICT

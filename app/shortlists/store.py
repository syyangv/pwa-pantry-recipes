"""The three PWA-owned Meal Shortlists (D3) — a planning convenience, and nothing else.

**This module writes to the PWA's SQLite and to nothing else, ever.** That is D3
in one sentence and the whole reason this file exists in the shape it does: there
is no `餐次` frontmatter key, no recipe classification, no meal grouping of Cooking
Records, and no sync-back. The vault's recipe notes stay exactly as meal-free as
they were before this ticket, and `tests/shortlists/test_store.py`
(`test_a_full_cycle_writes_nothing_to_the_vault`) asserts that as bytes rather than
as an intention.

**The drift is real, accepted, and visible — and both halves of that are load-bearing.**
These lists are PWA-owned state that can drift from the vault and is **never
synced back**; §13.4 records that as an accepted cost of D3's "no new frontmatter
field, vault stays meal-free". The consequence is that a Recipe renamed in Obsidian
leaves a `meal_lists` row whose `recipe_note` no longer resolves. Three things then
have to be true at once:

1. **The row is kept.** Dropping it destroys user data on an event the user caused
   innocently, and hiding it makes the list quietly shorter than the user curated.
   Nothing in this module deletes a row for any reason other than an explicit
   `remove` (§9.12: "a `DELETE` is the only thing that removes it").
2. **The drift is in-band, per row** — `ShortlistEntry.resolved`, published as the
   wire field `resolved` and nothing more. F7 forbids a second response shape, so
   there is no `?debug=1`, no `?strict=1`, and no companion endpoint that carries the
   flag instead. The client turns it into a dimmed `⚠ 已重命名` row; the server never
   owns that string, for the same reason it owns no CSS class name.
3. **A `DELETE` by the stored name still works.** This is the part that is easy to
   get wrong in the other direction. If `remove` validated `recipe_note` against the
   live index, a broken row would be *un-removeable* — not by the name the user sees
   (which no longer exists) and not by the old one (which the validator would
   refuse) — a permanent, unreachable row in the user's own list. So `add` checks
   the index and `remove` deliberately does not; §9.12 says "a `DELETE` is the only
   thing that removes it", and that has to be reachable.

**The rename cannot be repaired automatically, by design, and this is the
reason.** The store is handed a `RecipeNames` provider — `Callable[[], frozenset[str]]` —
not a `RecipeIndex`, so it holds no path, no cache, and no snapshot. It cannot know
that `盐焗鸡` became `盐焗鸡（新）`; the index knows only that `盐焗鸡` is gone. Any
"repair" would be a heuristic matching names by prefix or edit distance, and a wrong
guess would silently retarget a user's breakfast at a different recipe — which is
strictly worse than showing a row broken. The honest move is the visible one, and
the user retargets it with a remove and an add.

**An empty shortlist is a normal empty state and never an error.** Not a 404, not an
error code, not a "something went wrong" affordance. `list_all()` and `entries()`
both return **all three keys always**, with `()` for an empty slot, because a missing
key makes "the user has no lunch list" and "this build forgot lunch" the same wire
answer. Story 39 asks for an invitation; the only way to render one is for the empty
list to arrive as an empty list.

**F12: three slots, closed, and closing them is cheap *now*.** `MEAL_SLOTS` is the
whole enum and `meal_lists`' `CHECK` is the enforcement. Adding a slot to a SQLite
`CHECK` is a **table rebuild**, and the outbox replays this table — so widening the
enum after shortlist data exists is an availability-affecting migration, not a
one-line `ALTER`. That is why there is no `snack`, no free-text slot, and no fourth
value anywhere: the three answer the question the user actually has ("what do I
usually eat at lunch"), and a fourth would only re-open D3's closed question of what
a slot *means*. The store refuses an unknown slot with a typed error *before* the
`CHECK` would, so a bad request is a 422 rather than an `IntegrityError` out of the
middle of a transaction; the `CHECK` is still the guarantee, and
`test_the_storage_layer_refuses_a_fourth_slot` proves it by inserting `snack` with
raw SQL, straight past every Python guard.

**Every write is one `BEGIN IMMEDIATE` transaction, and the two indexes say why.**
`ux_meal_lists_slot_recipe` makes "add" idempotent — `ON CONFLICT (slot, recipe_note)
DO NOTHING` is a *no-op*, so a double tap on `+ 午餐` cannot create a second row —
and it is why `reorder` must be an in-transaction `UPDATE` of `position` rather than
a `DELETE`-then-`INSERT` shuffle: SQLite has no deferred unique constraint, so a
shuffle would transiently violate that index under concurrency, and an `UPDATE` does
not. `IMMEDIATE` rather than `DEFERRED` because both a `remove` and a `reorder` are
read-then-write: a deferred transaction takes its write lock at the first `UPDATE`,
by which point another writer can have changed the membership underneath and the
read half is already stale. The `reorder`'s membership check therefore happens
**inside** the transaction, which is what makes a `DELETE` racing a `reorder` safe —
the reorder either wins and rewrites positions, or reads the changed membership and
refuses with a mismatch. It never writes a position for a row that is no longer
there, which would leave a gap that nothing would ever close.

**`recipe_note` is the basename (`盐焗鸡`), never a path.** D2's rule, and the reason
the key is what D2's wikilink carries: the cook log and this store key the same one
field, so a note the index can no longer resolve is detectable in both. `_note_name`
refuses a value carrying `/`, and nothing else that a real filename cannot: a dot, a
space, CJK punctuation, and Latin script are all things the real vault's sixteen
notes contain, and a backslash is a legal APFS filename character, so excluding it
would refuse a real recipe to prevent an attack `/` alone already prevents.

**F5: nothing here enqueues anything, and #23 is the ticket that will.** The
offline outbox is #23's work and its `shortlist_intents` table already exists from
#3, unused. **#23 must wrap the three mutating *routes*, not the store:** for a
request carrying a validated `X-Client-Id`, compute the `request_fingerprint` of the
(canonicalised) body, `SELECT` the ledger inside its own transaction, return the
cached `response_json` on a hit, and otherwise call the store and record what came
back — all *around* `add` / `remove` / `reorder` rather than inside them. Two
consequences for that ticket, stated here so it is not re-derived: (a) the store
must stay the single writer of `meal_lists`, so the outbox may not re-implement a
re-sequence or its own position arithmetic; (b) a replayed **`reorder`** is a
lost-update hazard, because an `order` captured offline and replayed against a list
the user has since edited on another device is exactly the membership mismatch §9.12
refuses — so #23 has to fingerprint the order body and let that 422 stand rather than
reconciling it, and the client's answer is to re-read and re-offer. Nothing in this
module has a hook, a callback, or a queue for it, which is the deliberate shape:
an outbox wired into a store would be a store that knows about transport.

**One connection per operation, no descriptors held, and no vault access.** Same
discipline and the same reason as `app.mapping.store`: `connect_db` is an
`asynccontextmanager`, so the store takes a factory and closes every connection it
opens. A store that held one open would pin a WAL handle for the life of the
process — the leak `app/main.py`'s `finally` cannot see, because such a store would
have nothing to close.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Final, Literal

import aiosqlite

#: F12, in the one place it is written down. A `Literal` alias is exported as
#: well so a caller can annotate against the same three names, but the *test* is
#: membership in the tuple below: a `Literal` matches what a validator accepts and
#: says nothing about what the `CHECK` holds, and those two drifting apart is the
#: failure this module must not have.
MealSlot = Literal["breakfast", "lunch", "dinner"]

#: The closed enum, in the order the three panels are rendered. Three values, and
#: there is no fourth: see the module docstring for why closing it is cheap now and
#: expensive later.
MEAL_SLOTS: Final[tuple[str, ...]] = ("breakfast", "lunch", "dinner")

#: §7.4's bound on a reorder, and **also** the cap on a list. The cap on `add` is
#: the load-bearing half: a bound on reorder alone would let a list grow past it
#: and then make itself permanently un-reorderable, so the user would have no way
#: to fix it and no way to understand the 422. Fifty dishes is far more than three
#: meals can usefully hold, and it is a ceiling rather than a target.
MAX_SHORTLIST_ENTRIES: Final = 50

#: A bound on the `recipe_note` key. It is a ceiling, not a validation: a real
#: basename is a few dozen characters, and this key travels in a URL path segment
#: and in a request body, both of which need a bound that is a property of this app
#: rather than of whatever the vault happens to contain. 200 characters is
#: comfortably under what any proxy or browser will put in a path.
MAX_NOTE_NAME_CHARS: Final = 200

#: `app.db.connect_db` is an `asynccontextmanager`, so the store depends on a
#: factory rather than a connection — one connection per operation, which is also
#: what keeps a single-user PWA from pinning a WAL handle for the process lifetime.
#: Spelled out here rather than imported from `app.mapping.store` because a type
#: alias is not worth a dependency that points the wrong way through the package
#: graph.
ConnectFactory = Callable[[], AbstractAsyncContextManager[aiosqlite.Connection]]

#: The live recipe basenames, as a callable. A `RecipeIndex` would be the obvious
#: collaborator and is deliberately *not* this: a `RecipeIndex` is a path, a TTL
#: cache, and a `ConfigurationError` on a missing folder, and this store needs none
#: of the three. It needs the answer to one question — "is this name live?" — and a
#: `Callable` is that, with nothing to close and nothing to invalidate. `main.py`
#: adapts the index to it.
RecipeNames = Callable[[], frozenset[str]]

#: Idempotent add, and the conflict target is **named** rather than a bare
#: `OR IGNORE`. The only constraint a new row can violate is
#: `ux_meal_lists_slot_recipe` — `slot` is checked first and `position` carries no
#: unique index — so naming the target states the intent exactly, and a future
#: constraint on this table would still abort the insert loudly instead of being
#: swallowed. `OR IGNORE` would not be equivalent: it would also swallow a `CHECK`
#: violation, which is the one thing F12 must never do quietly.
_INSERT_ENTRY: Final = (
    "INSERT INTO meal_lists (slot, position, recipe_note) VALUES (?, ?, ?)"
    " ON CONFLICT (slot, recipe_note) DO NOTHING"
)

#: The one position write, used by both `remove`'s re-sequence and `reorder`.
#: **`OR ROLLBACK`, `OR FAIL`, and `OR IGNORE` must never appear here**: each would
#: undo or silently discard the whole transaction's work. Nor may a `DELETE`-then-
#: `INSERT` shuffle replace it — §7.4's `ux_meal_lists_slot_recipe` has no deferred
#: form, so a shuffle transiently violates it while this does not.
#: `tests/shortlists/test_store.py` asserts all four absences.
_UPDATE_POSITION: Final = "UPDATE meal_lists SET position = ? WHERE slot = ? AND recipe_note = ?"

#: Serves `ix_meal_lists_slot_position`; the `id` is only a tiebreak for a
#: hand-edited table that produced two rows at one position, where a stable
#: answer beats an arbitrary one.
_SELECT_SLOT: Final = (
    "SELECT position, recipe_note FROM meal_lists WHERE slot = ? ORDER BY position, id"
)

_DELETE_ENTRY: Final = "DELETE FROM meal_lists WHERE slot = ? AND recipe_note = ?"

_SELECT_MAX_POSITION: Final = "SELECT MAX(position) FROM meal_lists WHERE slot = ?"


class ShortlistError(RuntimeError):
    """Base class for this store's own refusals. Carries an API-publishable code.

    Every code is a **class attribute**, never derived from the exception's message.
    `app/api/recipes.py::_code_of` has to guard that property with an
    `isidentifier` check for the codes it does derive; this store never derives
    one at all, so the note a user typed is carried in the exception and can never
    reach the envelope, which publishes exactly `{"requestId", "code"}`.
    """

    code: str = "shortlist_error"


class UnknownMealSlot(ShortlistError):
    """`slot` is not one of the three, checked before anything is written.

    F12. Named rather than a bare string so a route can map it to a 422 and publish
    a code the client can branch on, and so a fourth value has nowhere to arrive
    from: the check is membership in `MEAL_SLOTS`, not a prefix, a `startswith`, or
    an alias — each of which would pass a looser test and break the `CHECK`.
    """

    code = "unknown_meal_slot"


class UnknownRecipeNote(ShortlistError):
    """`add` was handed a basename the live recipe index does not have.

    Adding is *by recipe name*, and the name is checked so a shortlist can only
    hold recipes that exist. `remove` deliberately does **not** raise this — see
    the module docstring, where the un-removeable broken row is spelled out.
    """

    code = "unknown_recipe_note"


class OrderMembershipMismatch(ShortlistError):
    """`reorder` named a set that is not exactly the slot's current membership.

    A reorder is the one write with a *whole-list* argument, so a request naming a
    subset, a superset, or a different set is asking for something the client
    cannot have meant: it either dropped a row by forgetting it, or invented one.
    Reconciling either silently would lose user data or add a row the user never
    chose. An empty `order` against a populated slot is the dangerous shape and is
    refused for exactly that reason — clearing a list is what the `×` on each row
    is for.
    """

    code = "shortlist_order_mismatch"


class ShortlistOrderTooLong(ShortlistError):
    """`reorder` was handed more rows than the bound allows.

    Reachable only from a list this store never filled — a restored backup, a
    hand-edit, or a future write path that skips `add` — which is exactly why the
    bound is checked here as well as on `add`. An unbounded reorder is an unbounded
    transaction, and the table is what the outbox replays.
    """

    code = "shortlist_order_too_long"


class ShortlistFull(ShortlistError):
    """`add` would take a slot past `MAX_SHORTLIST_ENTRIES`.

    The cap is on `add` as well as on `reorder` so a list can never become one
    that cannot be reordered.
    """

    code = "shortlist_full"


class ShortlistEntryMissing(ShortlistError):
    """`remove` named a row the slot does not have.

    A recipe on `lunch` is not a member of `breakfast`, and "you asked to remove
    something from a list it is not on" is a different answer from "removed" — a
    UI that confused the two would drop a row the user is still looking at. A
    `⚠ 已重命名` row is **not** this error: the row is present under its stored name.
    """

    code = "shortlist_entry_missing"


@dataclass(frozen=True)
class ShortlistEntry:
    """One row of one shortlist, decoded.

    `resolved` is D3's drift, made visible, and it is the only thing about the row
    that is not just the row. `True` when `note_name` is in the live recipe index;
    `False` when it is not, which is what the client renders as a dimmed
    `⚠ 已重命名`. It is a per-row fact and it travels per row, because F7 forbids
    the second response shape that would otherwise carry it.

    There is no `cookable` boolean here or anywhere near it (F17), and no position:
    the position is the order of the tuple, and publishing an ordinal the client
    then has to keep consistent with a list it also re-orders would be a second
    source of truth about the same thing.
    """

    note_name: str
    resolved: bool


@asynccontextmanager
async def _transaction(conn: aiosqlite.Connection) -> AsyncIterator[None]:
    """One `BEGIN IMMEDIATE` … `COMMIT`, or a full rollback and a re-raise.

    `IMMEDIATE` rather than the default `DEFERRED` because every write here is
    read-then-write over one slot's membership. A deferred transaction takes its
    write lock at the first `UPDATE`, by which point another writer can have
    changed the table underneath and the read half is already stale — which for
    `reorder` would mean validating a membership that no longer exists.

    The rollback is the other half: an unexpected exception — `busy_timeout`
    exceeded, disk error — undoes the whole operation, so the previous state is
    intact rather than half-resequenced. A refusal raised *before* the
    `BEGIN` therefore leaves nothing to roll back, and a refusal raised inside it
    (the reorder's membership check) rolls back a transaction that had written
    nothing yet.

    Nesting is refused rather than joined, for the reason
    `app.mapping.store._transaction` gives: this store owns its transactions, and a
    nested `BEGIN` would raise from SQLite anyway. Stating it as a precondition
    keeps "one transaction per operation" an invariant a reader can see.
    """
    if conn.in_transaction:
        raise ShortlistError("nested_shortlist_transaction")
    await conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if conn.in_transaction:
            await conn.rollback()
        raise
    if conn.in_transaction:
        await conn.commit()


class MealShortlistStore:
    """The three user-curated shortlists, and the drift that makes them honest.

    One instance per app lifespan, published on `app.state` and read by
    `app/api/shortlists.py`. It holds no connection (the factory opens and closes
    one per operation), no path, no cache, and no vault handle — the only state on
    it is the two callables. That is why it has no `close()`: like
    `IngredientMappingStore`, everything it owns is closed by not being held open.
    """

    def __init__(self, connect: ConnectFactory, recipes: RecipeNames) -> None:
        self._connect = connect
        self._recipes = recipes

    # --- 1. reads -----------------------------------------------------------

    async def entries(self) -> dict[str, tuple[ShortlistEntry, ...]]:
        """All three slots, each ordered by `position`, each entry marked.

        The one read `GET /api/shortlists` needs, and the one that carries the
        drift flag. All three keys are **always** present: an empty slot is `()`
        and renders as an invitation, never as an error and never as a missing
        key, because a missing key would make "the user has no lunch list" and
        "this build forgot lunch" the same wire answer.

        `resolved` is computed from the table against the provider on every call,
        so rows this store never wrote — a restored backup, a hand-edit, #23's
        replay — are marked exactly like its own. It is recomputed rather than
        cached, and there is no `invalidate()`, because a memo of "which names
        resolve" would be a second source of truth about the vault repairable only
        by remembering to clear it.
        """
        live = self._recipes()
        async with self._connect() as conn:
            rows = await _select_slots(conn)
        return {slot: _entries(rows.get(slot, ()), live) for slot in MEAL_SLOTS}

    async def list_all(self) -> dict[str, tuple[str, ...]]:
        """All three slots as bare **names**, each ordered by `position`.

        §9.12's declared shape, and derived from `entries()` rather than from a
        second query — so a rename cannot be visible in this projection and not in
        that one, and neither projection can disagree with the other about which
        slots exist. The API does not use this: it needs the `resolved` flag, and a
        route that read names here and flags there would be answering from two
        reads that could disagree.
        """
        listed = await self.entries()
        return {
            slot: tuple(entry.note_name for entry in entries)
            for slot, entries in listed.items()
        }

    # --- 2. add -------------------------------------------------------------

    async def add(self, slot: str, recipe_note: str) -> tuple[ShortlistEntry, ...]:
        """Append one Recipe to one slot, and return that slot's new contents.

        The order is fixed and each step is load-bearing:

        1. **`slot` against the closed enum** (F12), before the note is even looked
           at: a malformed request gets the cheaper, state-independent answer, the
           same order `app/api/recipes.py::_locate` uses.
        2. **`recipe_note` is a basename.** A value carrying `/` is refused rather
           than stored, so a shortlist can never hold a client-supplied path.
        3. **`recipe_note` against the live index.** The list may only hold recipes
           that exist, so `add` is the validating half of the drift asymmetry.
        4. **`INSERT … ON CONFLICT DO NOTHING`**, inside one transaction, at
           `max(position) + 1`. A duplicate is a **no-op** and returns the current
           list unchanged: a double tap on `+ 午餐` must not create a second row,
           and must not fail either — the user already has the recipe on the list,
           which is the thing the tap was asking for.

        `max(position) + 1` rather than `len(rows)`. The two agree only while the
        table is exactly as this store left it; after an out-of-band write `len`
        would hand the new entry a position another row already holds, and
        `ix_meal_lists_slot_position` is a plain index, not a unique one, so
        nothing would catch it.
        """
        checked_slot = _slot(slot)
        note = _note_name(recipe_note)
        # **One** snapshot of the live index for the whole operation, read once
        # and threaded through. Calling the provider again later would be cheap —
        # `RecipeIndex` is TTL-cached — but it could straddle a cache expiry, and
        # an `add` whose validation saw one index and whose response was marked
        # against another could report a recipe it had just rejected as live.
        live = self._recipes()
        if note not in live:
            raise UnknownRecipeNote(note)
        async with self._connect() as conn:
            current = await _select_slot(conn, checked_slot)
            if note in {name for _, name in current}:
                return _entries(current, live)
            if len(current) >= MAX_SHORTLIST_ENTRIES:
                raise ShortlistFull(checked_slot)
            next_position = await _next_position(conn, checked_slot)
            async with _transaction(conn):
                await conn.execute(_INSERT_ENTRY, (checked_slot, next_position, note))
            kept = (*current, (next_position, note))
        return _entries(kept, live)

    # --- 3. remove ----------------------------------------------------------

    async def remove(self, slot: str, recipe_note: str) -> tuple[ShortlistEntry, ...]:
        """Delete one row and re-sequence, in **one** transaction.

        The re-sequence is not tidiness. `DELETE` alone would leave `0, 2` — two
        rows, a gap, and a client rendering `position` as an ordinal showing a
        recipe as third of two. Doing it in the *same* transaction as the `DELETE`
        is what means a reader can never observe the gap; a separate transaction
        would publish it for the duration.

        **`note` is matched against the stored value, not against the live index.**
        That is deliberate and it is what keeps a `⚠ 已重命名` row removable. A
        validator here would make the row unreachable: not by the name the user
        sees (which no longer resolves) and not by the old one (which the validator
        would refuse). §9.12 says a `DELETE` is the only thing that removes it, and
        that has to be reachable.

        A row that is not in the slot is `ShortlistEntryMissing` — a 404 at the
        route — rather than a silent no-op, because "you asked to remove something
        from a list it is not on" and "removed" are different answers.
        """
        checked_slot = _slot(slot)
        note = _note_name(recipe_note)
        async with self._connect() as conn:
            current = await _select_slot(conn, checked_slot)
            if note not in {name for _, name in current}:
                raise ShortlistEntryMissing(f"{checked_slot}#{note}")
            kept = tuple((_, name) for _, name in current if name != note)
            async with _transaction(conn):
                await conn.execute(_DELETE_ENTRY, (checked_slot, note))
                await _resequence(conn, checked_slot, kept)
        return _entries(kept, self._recipes())

    # --- 4. reorder ---------------------------------------------------------

    async def reorder(
        self, slot: str, order: Sequence[str]
    ) -> tuple[ShortlistEntry, ...]:
        """Rewrite `position` for one slot from a whole-list `order`, in one
        `BEGIN IMMEDIATE` transaction.

        **`order` must be exactly the slot's current membership — same names, same
        count, no repeats.** A subset, a superset, a different set, or an empty list
        against a populated slot is `OrderMembershipMismatch` (a 422 at the route),
        never a silent reconciliation: reconciling would lose user data or add a
        row the user never chose, and the client would render a list it did not ask
        for as though it had. That includes a `⚠ 已重命名` row, which is still a
        member — if unresolved rows were excludable, the UI would have to send a
        subset, and the rule that protects the list would be unreachable for
        exactly the rows that most need attention.

        **The membership is read inside the transaction, and that is the point.**
        It is what makes a `DELETE` racing a `reorder` safe: a delete that commits
        first changes the membership, and this check then refuses rather than
        writing a `position` for a row that is no longer there. Reading it outside
        the transaction would open a window in which the reorder leaves a gap that
        nothing would ever close.

        **The write is an in-transaction `UPDATE` of `position`, never a
        `DELETE`-then-`INSERT` shuffle.** SQLite has no deferred unique constraint,
        so a shuffle transiently violates `ux_meal_lists_slot_recipe` under
        concurrency; an `UPDATE` of a non-unique column does not. Every row's `id`
        also survives, so an offline replay holding a stale id cannot resurrect a
        row the user has just reordered past.

        Bounded at `MAX_SHORTLIST_ENTRIES`, so a list this store never filled — a
        restored backup, a hand-edit — cannot make this an unbounded transaction
        over a table the outbox replays.
        """
        checked_slot = _slot(slot)
        names = _order(order)
        if len(names) > MAX_SHORTLIST_ENTRIES:
            raise ShortlistOrderTooLong(str(len(names)))
        async with self._connect() as conn:
            async with _transaction(conn):
                current = await _select_slot(conn, checked_slot)
                if not _same_membership(current, names):
                    raise OrderMembershipMismatch(checked_slot)
                await _resequence(
                    conn, checked_slot, tuple((index, name) for index, name in enumerate(names))
                )
        return _entries(
            tuple((position, name) for position, name in enumerate(names)), self._recipes()
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"


# --- validation ------------------------------------------------------------


def _slot(value: str) -> str:
    """The closed enum, as a membership test and nothing cheaper.

    F12. A prefix check, a `startswith`, a case-insensitive match, or a `snack`
    alias would each pass a looser test and each break the `CHECK` in
    `schema.sql` — so the `CHECK` is the guarantee and this is the courtesy that
    turns a bad request into a 422 instead of an `IntegrityError` out of the middle
    of a transaction.
    """
    if value not in MEAL_SLOTS:
        raise UnknownMealSlot(str(value))
    return value


def _note_name(value: str) -> str:
    """A recipe **basename**, or a refusal.

    D2's rule and the Server-Owned Root invariant: a request contributes *which
    recipe*, never *which path*, so a value carrying `/` is refused rather than
    stored. A shortlist holding paths would be a second, client-owned mapping of
    names to files, and a client able to point one at a file it chose could walk
    out of `RECIPES_ROOT`.

    `.` and `..` are refused as the traversal they are, and a control character is
    refused because it cannot render and would be invisible in a list. Everything
    else a real filename can contain is allowed: dots, spaces, CJK punctuation, and
    Latin script are all present in the real vault's sixteen notes, and a
    backslash is a legal APFS filename character — excluding it would refuse a real
    recipe to prevent an attack that `/` alone already prevents.

    **Surrounding whitespace is stripped, and the asymmetry with `_slot` is
    deliberate.** A name is user-typed twice — in a body and in a URL path segment —
    so trimming an accidental space is the same courtesy `app/auth.py`'s
    `normalize_identity` extends to an identity header, and it is applied *before*
    the length bound so a padded name cannot smuggle past it. A slot is not
    user-typed but *chosen from a closed set*, so there is nothing to forgive and
    `_slot` tests the value exactly: a trimmed `" lunch"` would be a fourth
    spelling of a three-value enum, and `MEAL_SLOTS` membership is the only test
    that agrees with the `CHECK`.
    """
    name = value.strip()
    if not name or name in {".", ".."}:
        raise ShortlistError("empty_or_relative_recipe_note")
    if len(name) > MAX_NOTE_NAME_CHARS:
        raise ShortlistError("recipe_note_too_long")
    if "/" in name or any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise ShortlistError("recipe_note_must_be_a_basename")
    return name


def _order(values: Sequence[str]) -> tuple[str, ...]:
    """The requested order, validated per member and as a whole.

    Per member, not "the list looks plausible": `["盐焗鸡", ""]` is malformed
    whichever position the bad value occupies, and `str()` of a nested structure
    would put a Python repr into a user-visible list position — the same failure
    `app/recipes/reader.py::_entries` refuses. Each member goes through
    `_note_name`, so an `order` cannot smuggle in a path either.
    """
    names: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ShortlistError("recipe_note_must_be_a_string")
        names.append(_note_name(value))
    return tuple(names)


def _same_membership(
    current: Sequence[tuple[int, str]], names: Sequence[str]
) -> bool:
    """Whether `names` is **exactly** the slot's membership, and nothing else.

    Both halves, and the length check is not redundant. `ux_meal_lists_slot_recipe`
    makes a duplicate row impossible, so the table's names are pairwise distinct
    and a set comparison alone would be enough — but it is enough only in one
    direction. `("番茄炒蛋", "盐焗鸡", "番茄炒蛋")` against a two-row slot has the
    *same set* as the membership, and a set-only check would accept it: the
    re-sequence would then write `position` for the same row twice, and the last
    write would win, leaving the slot with a duplicate ordinal that
    `ix_meal_lists_slot_position` — a plain index, not a unique one — cannot catch.
    Requiring the counts to agree as well pins the shape from both sides, and a
    client that lost track of the list gets the same 422 whether it repeated a
    member or dropped one.
    """
    if len(names) != len(current):
        return False
    return set(names) == {name for _, name in current}


# --- row projection --------------------------------------------------------


def _entries(
    rows: Sequence[tuple[int, str]], live: frozenset[str]
) -> tuple[ShortlistEntry, ...]:
    """`(position, recipe_note)` pairs as entries, marking the drift in-band.

    `live` is passed in rather than re-read so one call resolves the index once for
    all three slots: a route answering from two reads could publish a recipe that
    was renamed between them, which is a plausible-looking wrong answer and the
    exact class D3 says must be made visible rather than smoothed over.
    """
    return tuple(ShortlistEntry(name, name in live) for _, name in rows)


async def _select_slots(
    conn: aiosqlite.Connection,
) -> dict[str, tuple[tuple[int, str], ...]]:
    """Every row, grouped by slot, each group in `position` order.

    One query for all three slots rather than three, so `GET /api/shortlists` is a
    single read of the table and cannot see one slot before another's re-sequence
    and the other after it.
    """
    grouped: dict[str, list[tuple[int, str]]] = {slot: [] for slot in MEAL_SLOTS}
    for position, slot, name in await _select_slots_raw(conn):
        # A slot the enum does not hold cannot be written past the `CHECK`, so
        # this branch is unreachable; `_slot` would be the wrong call here anyway
        # because it raises, and an unrecognised row in a hand-edited table is
        # dropped rather than failing the whole read.
        if slot in grouped:
            grouped[slot].append((position, name))
    return {slot: tuple(grouped[slot]) for slot in MEAL_SLOTS}


async def _select_slots_raw(
    conn: aiosqlite.Connection,
) -> tuple[tuple[int, str, str], ...]:
    """`(position, slot, recipe_note)` for the whole table, in match order."""
    cursor = await conn.execute(
        "SELECT position, slot, recipe_note FROM meal_lists ORDER BY slot, position, id"
    )
    return tuple(
        (int(row[0]), str(row[1]), str(row[2])) for row in await cursor.fetchall()
    )


async def _select_slot(conn: aiosqlite.Connection, slot: str) -> tuple[tuple[int, str], ...]:
    """One slot's `(position, recipe_note)` rows, in `position` order.

    Inside a mutation's transaction, so it is the membership the operation is
    actually about and not one that another writer can change before the write.
    """
    cursor = await conn.execute(_SELECT_SLOT, (slot,))
    return tuple((int(row[0]), str(row[1])) for row in await cursor.fetchall())


async def _next_position(conn: aiosqlite.Connection, slot: str) -> int:
    """`max(position) + 1` for one slot, or `0` for an empty one.

    `MAX` over the table rather than `COUNT(*)`, and the reason is in `add`: the
    two agree only while the table is exactly as this store left it.
    """
    cursor = await conn.execute(_SELECT_MAX_POSITION, (slot,))
    row = await cursor.fetchone()
    return 0 if row is None or row[0] is None else int(row[0]) + 1


async def _resequence(
    conn: aiosqlite.Connection, slot: str, rows: Sequence[tuple[int, str]]
) -> None:
    """Rewrite `position` to `0..n-1` over `rows`, in place.

    An `UPDATE` per member, keyed by the **stored** name — never a
    `DELETE`-then-`INSERT`. The whole thing runs inside the caller's
    `BEGIN IMMEDIATE`, so no reader can observe an intermediate state even though
    `(slot, position)` carries no unique index and a rewrite would transiently
    hold two rows at one ordinal. That is the honest answer rather than an
    `UPDATE`-to-negative-offsets dance: atomicity, not the absence of a
    constraint, is what makes the intermediate state unobservable, and pretending
    otherwise would need a second index the schema does not have.

    `rows` is `(position, recipe_note)` and the incoming `position` is ignored on
    purpose — `remove` passes the *old* positions and `reorder` passes the *new*
    ones, and both mean "rewrite these names to `0..n-1` in the order given".
    """
    if not rows:
        return
    await conn.executemany(
        _UPDATE_POSITION, [(index, slot, name) for index, (_, name) in enumerate(rows)]
    )


__all__ = [
    "MAX_NOTE_NAME_CHARS",
    "MAX_SHORTLIST_ENTRIES",
    "MEAL_SLOTS",
    "ConnectFactory",
    "MealShortlistStore",
    "MealSlot",
    "OrderMembershipMismatch",
    "RecipeNames",
    "ShortlistEntry",
    "ShortlistEntryMissing",
    "ShortlistError",
    "ShortlistFull",
    "ShortlistOrderTooLong",
    "UnknownMealSlot",
    "UnknownRecipeNote",
]

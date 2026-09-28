"""`IngredientMappingStore` — the materialized, auditable Ingredient resolution.

**This module persists Pantry Item ids and is the only place in the app that
does.** `app.recipes.matcher` decides *which* product one `材料` value is and is
pure; this one remembers the answer, records why, and gives the user a way to
overrule it. Everything expensive and everything clever is reused wholesale from
the shipped modules rather than reimplemented: `parse_ingredient_value` (§9.6) is
the one parser, `normalize_ingredient` is the one normalizer, the two closed
lexicons are read by the matcher at import, and `resolve_ingredient` is the
eight-tier ladder. There is no second tier, no second normalizer, and no
reach-around into a lexicon file from here — a store that re-derived its own
answers would be a second, drifting copy of §9.6–§9.8.

**The honesty tradeoff is stated rather than hidden (D1).** A persisted wrong
mapping is permanent and invisible. Three mitigations ship together:

1. **Provenance is always stored**, on every row, whatever the outcome:
   `raw_value` verbatim (the audit anchor), `parsed_name`, `parse_method`,
   `match_method`, `match_tier`, `confidence`, and `candidates_json` — every
   catalog row the ladder looked at, with its name, id, family, and the guards
   that refused it. Nothing is ever *un*-debuggable, including a row that
   matched on the first try.
2. **A re-resolve path**, over unresolved *and* stale rows: `resolve_all()`,
   `resolve_recipe()`, `resolve_ingredient()`, plus `catalog_changed`, which is
   the §9.11.2 boot-time signal that repairs mappings after the producer
   re-imports and renumbers an `items.id`.
3. **The `调试` provenance toggle**, which is the render layer's concern. This
   module supplies the data it renders.

**`pantry_item_id` carries no `REFERENCES` clause and this module does not wish
one.** `pantry_items.db` is another repository's file, opened read-only;
SQLite cannot express a cross-database foreign key, and adding a same-named
`items` table here purely to hang a key off would be a second *writable* copy
of catalog truth, which `AGENTS.md` #4 forbids in spirit and D1 forbids in
fact. Referential integrity is therefore handled by **re-resolution**:
`stale_rows()` reports every row whose id is gone from the live catalog,
`/api/recipes` surfaces the count as `staleMappingCount`, and the next
`resolve_all()` resets those rows to `unresolved` and re-derives them. That is
strictly better than a foreign key for a cross-database reference, because it
also catches the id being renumbered onto a *different product* — which is the
case that actually happens, because `items.id` is `AUTOINCREMENT` and a
re-import renumbers it.

=================================  ===========================================
Failure                             Caught by
=================================  ===========================================
A slot's `(recipe_note,             the **per-slot** `sqlite3.IntegrityError`
pantry_item_id)` collides with      catch (F2), scoped to one `execute()` call
another slot of the same recipe     inside the still-open transaction
Catalog unreadable, DB locked      the **transaction** — the whole
past `busy_timeout`, disk error,    `BEGIN IMMEDIATE` rolls back, the call
or any unexpected exception         raises, and the previous state is intact
=================================  ===========================================

**How the per-slot catch and the single-transaction guarantee coexist, precisely**
— this is the mechanism F2's override rests on, and it is a property of SQLite's
conflict resolution rather than of anything clever here:

1. The whole pass runs in one `BEGIN IMMEDIATE` … `COMMIT`.
2. `try` / `except sqlite3.IntegrityError` wraps **one** `execute()` call — the
   slot `UPDATE` — not the transaction object and not the loop.
3. SQLite's default conflict resolution is `ON CONFLICT ABORT`, which rolls
   back **only the offending statement**. The transaction stays open and usable,
   so the recovery write that follows is a new statement in the same transaction,
   and it clears the violation. Every other slot's write therefore commits
   normally. Neither `OR ROLLBACK` nor `OR FAIL` may appear on the slot
   `UPDATE`: both undo the enclosing transaction, which is precisely the failure
   the override exists to prevent. Neither may `OR IGNORE` it either, because
   that *would* swallow the write — the silent dropped resolution, the worst bug
   class in this app.
4. Because `pantry_item_id` has no `REFERENCES` clause, `PRAGMA
   foreign_keys=ON` can never produce an `IntegrityError` here. The only
   constraint this catch can ever see is the inverted unique index, so it is
   narrow and total: it cannot mask an unrelated integrity failure.

**A conflict is re-detected on every pass, and that is correct.** The downgraded
slot holds `NULL`, so the next pass re-derives the same Pantry Item for it,
collides again, and downgrades it again to the *same* state. The row is a fixed
point and its `updated_at` does not move, because a pass skips the write when a
slot's resolution is unchanged. The report counts the conflict again, because
the defect is in the user's note and is still there — which is the visible
degradation F2 asks for rather than a defect that quietly disappears after one
refusal.

**`match_method='manual'` is a different kind of claim and never becomes one.**
F6's `BEFORE UPDATE` trigger raises `ABORT` on a hand fix, so the only mutation
path is `set_manual()`'s `DELETE` + fresh `INSERT`: an auditable pair rather
than a silent overwrite. `resolve_all()` skips those rows entirely, which makes
immutability a property of the *row* rather than of one call site — a
catalog-change auto-re-resolve, a future backfill, a repair script, and a
hand-edited query all behave identically, because none of them is what enforces
it. A stale *manual* row is therefore **not** silently reset (an algorithm may
not overwrite a hand fix): `stale_rows()` reports it and the user re-maps it.
`stale_reset` counts non-`manual` rows only, and this is why.

**Only `材料` slots are materialized, and that is what the table says.** Its
`ingredient_index` is documented in `schema.sql` as "0-based index within the
`材料` list". So every ladder call here passes `source="材料"` — the matcher's
**safe** default, under which a staples-listed `材料` is `unresolved` with
`misfiled_staple=True` rather than counted as a Seasoning on hand. A `调料` slot
has no row to record an answer on, and calling the ladder with `source="调料"`
would resolve a Seasoning through the staples tier into a slot that does not
exist; nothing in this module may do that.

**No stock is read and none is stored.** `stock=None` is passed to the ladder,
and the schema carries no stock column by design (§7.3): Pantry Stock is
volatile, so persisting it would make the chip colour wrong. The ladder answers
*which product*; `PantryStockIndex` answers *do I have it*, per response, and
neither consults the other.

**Pure reads take a `CatalogSnapshot` provider, not a `PantryCatalog`.** That is
what lets this file be tested from the committed 178-row snapshot and from
in-test synthetic catalogs with no sibling checkout and no live path — the
provider is a `Callable[[], CatalogSnapshot]`, and `PantryCatalog.snapshot` is
one such callable. The store keeps **no** snapshot cache of its own, on purpose:
`PantryCatalog` already owns a 300 s TTL for exactly that, and a second memo here
would silently pin an older catalog than the reader the app believes it is
looking at, which is precisely the stale-pointer failure the revision check
exists to catch. `invalidate()` therefore drops the one thing this object really
owns — the revision its last pass ran against.

**No clock in this module.** `updated_at` is written by SQLite's own
`strftime(…,'now')`, the identical expression the column `DEFAULT` uses, so
there is one clock for the whole table and the Python side cannot disagree with
it.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Final, Literal, cast

import aiosqlite

from ..pantry.catalog import CatalogRow, CatalogSnapshot
from ..recipes.ingredients import ParseMethod, parse_ingredient_value
from ..recipes.matcher import (
    MatchCandidate,
    MatchMethod,
    MatchResult,
    RejectionReason,
    Relation,
    resolve_ingredient,
)
from ..recipes.reader import IngredientEntry, RecipeNote

#: The frontmatter key every ladder call here is made for. See the module
#: docstring: the table's `ingredient_index` is a `材料` index, and `材料` is the
#: matcher's safe default, so this is a fact and not a preference.
_SLOT_SOURCE: Final = "材料"

#: `app.db.connect_db` is an `asynccontextmanager`, so the store depends on a
#: factory rather than a connection: one connection per operation, which is also
#: what keeps a single-user PWA from pinning a WAL handle for the process
#: lifetime. Spelled out here rather than imported from `app.cooklog.writer`
#: because a type alias is not worth a dependency that points the wrong way
#: through the package graph.
ConnectFactory = Callable[[], AbstractAsyncContextManager[aiosqlite.Connection]]

#: A `PantryCatalog` method, or a closure over a committed snapshot. Deliberately
#: the `CatalogSnapshot` and not the `PantryCatalog`: this module needs the
#: indexes and the revision and nothing else, and a test must be able to hand it
#: a synthetic catalog with no file behind it.
CatalogProvider = Callable[[], CatalogSnapshot]

#: One stamped `updated_at`, written in SQL and never in Python. The expression
#: is character-for-character the table's `DEFAULT`, so a row inserted by
#: `ensure_rows` and a row updated by a pass are stamped by the same clock.
_STAMP: Final = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"

_SLOT_COLUMNS: Final = (
    "recipe_note, ingredient_index, raw_value, parsed_name, parse_method,"
    " match_method, match_tier, pantry_item_id, confidence, candidates_json,"
    " created_at, updated_at"
)

#: Idempotent slot materialization, and the conflict target is **named** rather
#: than a bare `OR IGNORE`. The only constraint a fresh unresolved row can violate
#: is the slot index — `pantry_item_id` is `NULL`, and SQLite permits unlimited
#: `NULL`s under a unique index — so naming the target states the intent exactly,
#: and a future constraint on this table would still abort the insert loudly
#: instead of being swallowed. `OR IGNORE` is the clause F2 forbids on the slot
#: `UPDATE` for exactly this reason, and it is not used here either.
_INSERT_SLOT: Final = (
    "INSERT INTO ingredient_mappings"
    " (recipe_note, ingredient_index, raw_value, parsed_name, parse_method,"
    "  match_method, match_tier, pantry_item_id, confidence, candidates_json)"
    " VALUES (?, ?, ?, ?, ?, 'unresolved', 0, NULL, 0.0, '[]')"
    " ON CONFLICT (recipe_note, ingredient_index) DO NOTHING"
)

#: F2's slot write. **`OR ROLLBACK` and `OR FAIL` must never appear here**, and
#: neither must `OR IGNORE`: each of the three would undo or silently discard the
#: whole transaction's work, which is the exact failure the per-slot catch exists
#: to prevent. `tests/mapping/test_store.py` asserts all three absences.
_UPDATE_SLOT: Final = (
    "UPDATE ingredient_mappings SET"
    " match_method = ?, match_tier = ?, pantry_item_id = ?, confidence = ?,"
    f" candidates_json = ?, updated_at = {_STAMP}"
    " WHERE recipe_note = ? AND ingredient_index = ?"
)

#: F2's recovery write: a new statement in the same still-open transaction, which
#: clears the violation so this slot's downgraded state and every other slot's
#: write commit together. `match_tier = 0` and `confidence = 0.0` are the
#: schema's own "no tier, no claim" encoding, shared with `manual`.
_UPDATE_SLOT_CONFLICT: Final = (
    "UPDATE ingredient_mappings SET"
    " match_method = 'unresolved', match_tier = 0, pantry_item_id = NULL,"
    " confidence = 0.0, candidates_json = ?,"
    f" updated_at = {_STAMP}"
    " WHERE recipe_note = ? AND ingredient_index = ?"
)

_SELECT_SLOT: Final = (
    f"SELECT {_SLOT_COLUMNS} FROM ingredient_mappings"
    " WHERE recipe_note = ? AND ingredient_index = ?"
)
_SELECT_SLOTS: Final = (
    f"SELECT {_SLOT_COLUMNS} FROM ingredient_mappings WHERE recipe_note = ?"
    " ORDER BY ingredient_index"
)
_SELECT_ALL_SLOTS: Final = (
    f"SELECT {_SLOT_COLUMNS} FROM ingredient_mappings"
    " ORDER BY recipe_note, ingredient_index"
)
#: Serves `ix_ingredient_mappings_item` rather than scanning the table and
#: filtering in Python.
_SELECT_RESOLVED_SLOTS: Final = (
    f"SELECT {_SLOT_COLUMNS} FROM ingredient_mappings WHERE pantry_item_id IS NOT NULL"
    " ORDER BY recipe_note, ingredient_index"
)
#: Serves the partial index `ix_ingredient_mappings_unresolved` rather than
#: filtering the table in Python, and says `unresolved` in exactly one place.
_SELECT_UNRESOLVED: Final = (
    f"SELECT {_SLOT_COLUMNS} FROM ingredient_mappings WHERE match_method = 'unresolved'"
    " ORDER BY recipe_note, ingredient_index"
)

_DELETE_SLOT: Final = (
    "DELETE FROM ingredient_mappings WHERE recipe_note = ? AND ingredient_index = ?"
)
#: F6's only mutation path. The `DELETE` above is mandatory, not stylistic: the
#: `BEFORE UPDATE` trigger would `ABORT` an update of a manual row, so
#: delete-and-insert is the only way a hand fix may be changed, and it is an
#: auditable pair rather than a silent overwrite.
_INSERT_MANUAL: Final = (
    "INSERT INTO ingredient_mappings"
    " (recipe_note, ingredient_index, raw_value, parsed_name, parse_method,"
    "  match_method, match_tier, pantry_item_id, confidence, candidates_json)"
    " VALUES (?, ?, ?, ?, ?, 'manual', 0, ?, 1.0, '[]')"
)

#: `manual` is a `match_method` the ladder can never produce, so the persisted
#: vocabulary is the ladder's nine plus this one and nothing else.
MappingMethod = MatchMethod | Literal["manual"]

#: Why a candidate is recorded, from this store's point of view. `matched` is a
#: row the ladder looked at and adopted; the `RejectionReason`s are its guards;
#: `duplicate_slot_conflict` is F2's — not a guard and not a tier, which is
#: exactly why it needs its own name.
CandidateReason = Literal["matched", "duplicate_slot_conflict"] | RejectionReason

#: The reason F2 fixes as a single string on a conflict entry, exported so the
#: render layer and its tests name it rather than spell it.
DUPLICATE_SLOT_CONFLICT: Final = "duplicate_slot_conflict"
#: A ladder-adopted candidate, as opposed to a refused one. See `CandidateReason`.
MATCHED_REASON: Final = "matched"


class MappingError(RuntimeError):
    """Base class for this store's own refusals. Carries an API-publishable code."""

    code: str = "ingredient_mapping_error"


class UnknownPantryItem(MappingError):
    """`set_manual` was handed an id the live catalog does not have.

    Named, and refused, because the alternative is a `manual` row pointing at
    nothing: F6 makes such a row immutable, so a typo accepted once could only
    ever be repaired by another delete-and-insert, and until then it renders a
    chip claiming a product that does not exist.
    """

    code = "unknown_pantry_item"


class UnknownIngredientSlot(MappingError):
    """`set_manual` named a slot the table has no row for.

    `ensure_rows(recipes)` is how a slot comes into existence, and it is
    idempotent, so a caller that has not called it has nothing to overrule.
    Guessing a `raw_value` for a slot this app never read would put a frontmatter
    string into its own audit anchor.
    """

    code = "unknown_ingredient_slot"


class CorruptMappingRow(MappingError):
    """A row's `candidates_json` is not the JSON this store writes.

    Raised on a **read**, never on a pass. The only writer is this module, so this
    is a hand-edited database, and reading it as an empty audit trail would be a
    plausible-looking wrong answer about provenance — the one thing D1 says must
    never happen. A pass never decodes: it compares re-derived JSON *as text*
    against the stored text and rewrites when they differ, so a corrupted audit
    trail is repaired by the next pass rather than being the thing that blocks
    one.
    """

    code = "corrupt_ingredient_mapping_row"


@dataclass
class ResolveReport:
    """What one pass did, in the numbers a caller can render.

    Snake-cased here and camel-cased by `app.api.recipes`, which owns the wire
    contract; these are §9.10.1's five names.

    Mutable, and deliberately so: this is the pass's own accumulator, built by
    `_accumulate` as it goes and handed to the caller only once the transaction
    has committed. A frozen report would mean rebuilding it on every counter
    touch, and the frozen one is the one a caller could not trust to be a
    snapshot.

    * `reconsidered` — non-`manual` rows the ladder was re-run on. `manual` rows
      are outside the mechanism entirely and are not counted.
    * `resolved` / `still_unresolved` — how the reconsidered rows came out.
      `resolved + still_unresolved == reconsidered`, always.
    * `stale_reset` — of those, how many held a `pantry_item_id` that is gone
      from the live catalog, so the row was reset and re-derived. Non-`manual`
      only: a stale *hand fix* is reported by `stale_rows()` and re-mapped by the
      user, never reset by an algorithm (F6).
    * `duplicate_slot_conflicts` — slots F2's per-slot catch downgraded, and
      `conflicts` the same events with their recipe, slot, and the candidate that
      lost. A count above zero is a **data defect in a recipe note**, not an
      engine error, and it is visible without opening the `调试` view.
    """

    reconsidered: int = 0
    resolved: int = 0
    still_unresolved: int = 0
    stale_reset: int = 0
    duplicate_slot_conflicts: int = 0
    conflicts: tuple[SlotConflict, ...] = ()


@dataclass(frozen=True)
class SlotConflict:
    """One slot F2 had to downgrade, named precisely enough to be shown.

    `pantry_item_id` and `canonical_name` are the candidate that lost, so the UI
    can say "this Ingredient also matched X" rather than rendering an unresolved
    chip with no explanation — the difference between an honest miss and an
    unexplained one.
    """

    recipe_note: str
    ingredient_index: int
    raw_value: str
    pantry_item_id: int
    canonical_name: str
    reason: CandidateReason = DUPLICATE_SLOT_CONFLICT


@dataclass(frozen=True)
class CandidateRecord:
    """One catalog row the ladder looked at, decoded out of `candidates_json`.

    `reason` is a single stable string and `rejected_by` is the complete list. For
    a row refused by two guards the two differ — `reason` is the first, because
    F2's conflict entry fixes that field as one string — and **`rejected_by` is
    the field a reader must consult**, because the matcher's audit deliberately
    keeps every objection: `高笑美芝麻饼干` (25) is refused by the segment rule
    *and* the family rule, and an audit trail recording only the first would
    understate what is holding it back.
    """

    pantry_item_id: int
    canonical_name: str
    pantry_category: str
    family: str
    relation: Relation
    reason: CandidateReason
    rejected_by: tuple[RejectionReason, ...] = ()


@dataclass(frozen=True)
class IngredientMapping:
    """One row of `ingredient_mappings`, decoded. The `调试` view's whole input.

    `candidates` is every row the ladder looked at — the adopted one, the merely
    considered ones, and the refused ones — so a reader never has to ask the
    database what else it might have looked at. `resolved` is the chip colour and
    `is_manual` is the outline that keeps a hand fix and an algorithm fix from
    reading alike.
    """

    recipe_note: str
    ingredient_index: int
    raw_value: str
    parsed_name: str | None
    parse_method: ParseMethod
    match_method: MappingMethod
    match_tier: int
    pantry_item_id: int | None
    confidence: float
    candidates: tuple[CandidateRecord, ...]
    created_at: str
    updated_at: str

    @property
    def resolved(self) -> bool:
        """Whether a Pantry Item was adopted. `pantry_item_id` is the only test."""
        return self.pantry_item_id is not None

    @property
    def is_manual(self) -> bool:
        """A hand fix, which outranks every algorithm in this app (F6)."""
        return self.match_method == "manual"

    def conflict(self) -> CandidateRecord | None:
        """This row's F2 duplicate-slot conflict, or `None`.

        The one query a caller needs to decide whether to *show* a duplicate
        rather than hide it, which is what the user asked F2's design to do.
        """
        for record in self.candidates:
            if record.reason == DUPLICATE_SLOT_CONFLICT:
                return record
        return None

    def rejected(self, pantry_item_id: int) -> tuple[RejectionReason, ...]:
        """Every guard that refused one candidate row, in tier order."""
        for record in self.candidates:
            if record.pantry_item_id == pantry_item_id:
                return record.rejected_by
        return ()


@dataclass(frozen=True)
class _Slot:
    """A row as SQL returned it, with `candidates_json` still **raw text**.

    Text on purpose. A pass compares a re-derived audit trail against this string
    and writes when they differ, which is both cheaper than decoding and what
    makes a hand-corrupted audit trail self-heal. Only `_to_mapping` decodes, and
    it decodes strictly.
    """

    recipe_note: str
    ingredient_index: int
    raw_value: str
    parsed_name: str | None
    parse_method: ParseMethod
    match_method: MappingMethod
    match_tier: int
    pantry_item_id: int | None
    confidence: float
    candidates_json: str
    created_at: str
    updated_at: str

    @property
    def resolution(self) -> tuple[MappingMethod, int, int | None, float, str]:
        """The five fields a pass may change, in one comparable tuple.

        This tuple is what `updated_at` is gated on: an unchanged resolution is
        not written, so `updated_at` honestly means "when this row's resolution
        last changed" rather than "when a pass last walked over it" — which is
        what makes a second pass idempotent down to the timestamp.
        """
        return (
            self.match_method,
            self.match_tier,
            self.pantry_item_id,
            self.confidence,
            self.candidates_json,
        )


@asynccontextmanager
async def _transaction(conn: aiosqlite.Connection) -> AsyncIterator[None]:
    """One `BEGIN IMMEDIATE` … `COMMIT`, or a full rollback and a re-raise.

    `IMMEDIATE` rather than the default `DEFERRED` because a pass is
    read-then-write over every row: a deferred transaction takes its write lock at
    the first `UPDATE`, by which point another writer can have changed the table
    underneath the pass and the read half of it is already stale.

    The rollback is the second half of §9.10.1's table: an unexpected exception —
    catalog unreadable, `busy_timeout` exceeded, disk error — undoes the whole
    pass, so the previous state is intact rather than half-resolved. That is the
    *only* failure this transaction is responsible for. The inverted unique index
    is not one of them, because `_write_slot` absorbs it first.

    Nesting is refused rather than joined: this store owns its transactions, and a
    nested `BEGIN` would raise from SQLite anyway. Making that a stated
    precondition rather than an accident keeps "one transaction per pass" an
    invariant a reader can see.
    """
    if conn.in_transaction:
        raise MappingError("nested_mapping_transaction")
    await conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if conn.in_transaction:
            await conn.rollback()
        raise
    if conn.in_transaction:
        await conn.commit()


class IngredientMappingStore:
    """The materialized Ingredient-to-Pantry-Item resolution, and its repair path.

    One instance per app lifespan. It holds no connection (the factory opens and
    closes one per operation) and no catalog snapshot (the provider owns its own
    TTL), so the only state on it is `catalog_revision` — the revision the last
    **full** pass ran against, which is what `catalog_changed` compares.
    """

    def __init__(self, connect: ConnectFactory, catalog: CatalogProvider) -> None:
        self._connect = connect
        self._catalog = catalog
        self._resolved_revision: str | None = None

    # --- the catalog revision, and the automatic re-resolve it triggers ----

    @property
    def catalog_changed(self) -> bool:
        """Whether the live catalog differs from the last full pass's revision.

        §9.11.2's boot-time signal, and the reason re-resolution exists at all:
        the producer re-imports on its own schedule and renumbers `items.id`, and
        a renumbered id is a mapping now pointing at a *different product* —
        worse than a dangling one, because it still resolves and still renders a
        chip. The boot path reads this once and calls `resolve_all()` when it is
        true.

        `True` before any full pass has run, which is the honest answer: nothing
        has been resolved against this catalog yet, so a pass is owed.
        """
        if self._resolved_revision is None:
            return True
        return self._catalog().catalog_revision != self._resolved_revision

    def invalidate(self) -> None:
        """Forget the revision the last full pass ran against, forcing the next.

        Called after any write that can change stock or recipes. It does **not**
        drop a catalog snapshot, because this store holds none — `PantryCatalog`
        owns that TTL, and a second memo here would pin an older catalog than the
        reader the app believes it is looking at, which is the stale-pointer
        failure `catalog_changed` exists to catch.

        It is deliberately conservative: a write that cannot renumber an
        `items.id` still costs one wasted pass rather than risking a skipped one.
        That is the right trade for a single-user PWA, where a wasted pass is a
        few hundred index probes and a skipped one is a wrong chip that persists
        until the next boot.
        """
        self._resolved_revision = None

    # --- 1. materialization ------------------------------------------------

    async def ensure_rows(self, recipes: Sequence[RecipeNote]) -> None:
        """Give every `材料` slot of every note a row, if it lacks one.

        Idempotent, and idempotent in the strong sense: the second run writes
        nothing, so `created_at`, `updated_at`, and the row `id`s are all
        unchanged. That is what makes it safe to call on every `/api/recipes`
        request, which is the point.

        The inserted row carries §9.6's `parse_method` / `parsed_name` and
        `match_method='unresolved'`, so a slot that has never been resolved is
        still fully auditable — an empty row would not say *what* it failed to
        match. `parsed_name` and `parse_method` of an **existing** row are left
        alone here; a pass re-derives both from `raw_value`, which is the audit
        anchor, so refreshing them in two places would be a second answer.

        `调料` slots get no row: the table's `ingredient_index` is a `材料` index,
        and a Seasoning is not an Ingredient — `CONTEXT.md` keeps the two apart
        for exactly this reason.
        """
        rows = [
            _slot_insert_row(note, entry) for note in recipes for entry in note.ingredients
        ]
        if not rows:
            return
        async with self._connect() as conn, _transaction(conn):
            await conn.executemany(_INSERT_SLOT, rows)

    # --- 2. resolution -----------------------------------------------------

    async def resolve_all(self) -> ResolveReport:
        """Re-derive every non-`manual` row, in one transaction (F2, F6).

        The full pass, and the only entry point that records `catalog_revision`.
        `resolve_recipe` and `resolve_ingredient` deliberately do not: they touch
        one recipe, so claiming "this catalog is now current" after one of them
        would suppress the boot-time re-resolve that repairs the other fifteen.
        A targeted resolve that made the whole table look fresh is the silent
        wrong answer F2 exists to prevent, one level up.

        On success the live revision becomes the recorded one, so
        `catalog_changed` goes false. On any exception the transaction rolls back
        and the recorded revision is untouched, because nothing changed.
        """
        catalog = self._catalog()
        async with self._connect() as conn:
            slots = await _select_slots(conn, _SELECT_ALL_SLOTS)
            report = await self._resolve_slots(conn, slots, catalog)
        self._resolved_revision = catalog.catalog_revision
        return report

    async def resolve_recipe(self, recipe_note: str) -> ResolveReport:
        """Re-derive one recipe's non-`manual` rows, in the same one-transaction form.

        F2's override is not specific to the full pass (§9.10.1 #7), so this shares
        `_resolve_slots` with `resolve_all()` rather than re-implementing a
        narrower version of it that could be correct in one and missing from the
        other. An unknown `recipe_note` is an empty report, not an error: "nothing
        to re-resolve" is the answer, and it is also the answer before
        `ensure_rows` has run.
        """
        catalog = self._catalog()
        async with self._connect() as conn:
            slots = await _select_slots(conn, _SELECT_SLOTS, (recipe_note,))
            return await self._resolve_slots(conn, slots, catalog)

    async def resolve_ingredient(
        self, recipe_note: str, ingredient_index: int
    ) -> ResolveReport:
        """Re-derive one slot, in the same one-transaction per-slot-catch form.

        Returns a `ResolveReport` rather than the row, and that is the point: a
        targeted resolve is exactly where a duplicate-slot conflict is most likely
        to be hit — a user re-resolving the one Ingredient that just misbehaved —
        and a return value that could not carry the conflict would make this the
        one entry point where F2's degradation is invisible. Read the row back
        with `rows_for()`.

        A `manual` slot is skipped, so the report is empty and the row is
        untouched. So is an unknown slot.
        """
        catalog = self._catalog()
        async with self._connect() as conn:
            slots = await _select_slots(conn, _SELECT_SLOT, (recipe_note, ingredient_index))
            return await self._resolve_slots(conn, slots, catalog)

    async def _resolve_slots(
        self,
        conn: aiosqlite.Connection,
        slots: Sequence[_Slot],
        catalog: CatalogSnapshot,
    ) -> ResolveReport:
        """The one body of the pass, shared by all three public entry points.

        Single-slot, single-recipe, and full-table resolution differ only in the
        rows handed in, so the per-slot `IntegrityError` catch cannot be correct in
        one of them and missing from another — which is the entire content of F2's
        "the override is not specific to the full pass".
        """
        report = ResolveReport()
        if not slots:
            # No write lock for a pass over nothing: `resolve_ingredient` on an
            # unknown slot and `resolve_all` on an empty table both land here, and
            # taking `BEGIN IMMEDIATE` for either would block every other writer
            # on the file for the privilege of doing nothing.
            return report
        async with _transaction(conn):
            for slot in slots:
                if slot.match_method == "manual":
                    # F6: a hand fix is a property of the row, not of this call
                    # site. Skipped before anything is read of it, so an
                    # auto-re-resolve cannot become a way to overwrite one.
                    continue
                if slot.pantry_item_id is not None and slot.pantry_item_id not in catalog.by_id:
                    # §7.3's substitute for the cross-database foreign key the
                    # schema cannot have. Counted before the ladder runs, because
                    # "this id was dead" is a fact about the *old* row and the
                    # ladder's new answer says nothing about it.
                    report.stale_reset += 1
                await self._write_slot(conn, slot, catalog, report)
        return report

    async def _write_slot(
        self,
        conn: aiosqlite.Connection,
        slot: _Slot,
        catalog: CatalogSnapshot,
        report: ResolveReport,
    ) -> None:
        """Re-run the ladder for one slot and record the outcome, F2 intact.

        **The try/except below is the mechanism the whole override rests on.** It
        wraps one `execute()` call, not the loop and not the transaction. On
        SQLite's default `ON CONFLICT ABORT` the offending *statement* is what
        rolls back — the transaction stays open, so the recovery `UPDATE` that
        follows is a new statement in the same transaction and clears the
        violation before the commit. Every slot after this one therefore commits
        normally. A whole-transaction abort would silently revert all sixteen
        recipes' mappings over one duplicated `材料`, which is what the user
        rejected.
        """
        result = resolve_ingredient(
            parse_ingredient_value(slot.raw_value), None, catalog, source=_SLOT_SOURCE
        )
        proposed: tuple[MappingMethod, int, int | None, float, str] = (
            cast("MappingMethod", result.match_method),
            result.match_tier,
            result.pantry_item_id,
            result.confidence,
            _candidate_json(result.candidates),
        )
        downgraded = False
        if proposed != slot.resolution:
            try:
                await conn.execute(
                    _UPDATE_SLOT,
                    (*proposed, slot.recipe_note, slot.ingredient_index),
                )
            except sqlite3.IntegrityError:
                # F2, part 2. ONLY this slot is downgraded; the pass continues.
                # Narrow and total: `pantry_item_id` has no REFERENCES clause, so
                # `PRAGMA foreign_keys=ON` can never be the source of this error
                # and the catch cannot mask an unrelated integrity failure.
                await self._downgrade_conflicting_slot(conn, slot, result, catalog, report)
                downgraded = True
        # Counted from what the row now *is*, not from what the ladder proposed:
        # a slot F2 downgraded is in the table as `unresolved`, and a report that
        # counted it under `resolved` would tell a caller the recipe has two more
        # Pantry Items than the table holds — the plausible-looking wrong answer
        # this whole mechanism exists to replace.
        report.reconsidered += 1
        if result.resolved and not downgraded:
            report.resolved += 1
        else:
            report.still_unresolved += 1

    async def _downgrade_conflicting_slot(
        self,
        conn: aiosqlite.Connection,
        slot: _Slot,
        result: MatchResult,
        catalog: CatalogSnapshot,
        report: ResolveReport,
    ) -> None:
        """Record F2's conflict and clear the violation, in the open transaction.

        Two things happen here and both are load-bearing. The conflicting
        candidate is written to this slot's `candidates_json` with
        `reason: "duplicate_slot_conflict"`, so the dropped resolution is auditable
        rather than invisible — F2's whole point, and what the `调试` toggle
        renders. And the recovery `UPDATE` clears the unique-index violation, so
        this slot's downgraded state commits together with every other slot's
        write instead of poisoning the transaction.

        `pantry_item_id is not None` is guaranteed here: a statement that raised
        `IntegrityError` changed nothing, and a row adopting the id it already
        holds cannot collide with anything. The candidate's name comes from the
        live catalog, so the record names the product that was actually lost rather
        than whatever a stale audit row happened to remember.

        The recovery write is skipped only when the row is *already* in exactly
        this state — the second pass over a still-defective note. Writing anyway
        would stamp `updated_at` for a change that did not happen, and a pass that
        rewrites identical state is not idempotent.
        """
        pantry_item_id = result.pantry_item_id
        if pantry_item_id is None:  # pragma: no cover — see the docstring
            raise MappingError("conflicting_slot_resolved_to_nothing")
        row: CatalogRow | None = catalog.by_id.get(pantry_item_id)
        if row is None:  # pragma: no cover — the ladder read it from this snapshot
            raise MappingError("conflicting_slot_left_the_candidate_universe")
        record = CandidateRecord(
            pantry_item_id=pantry_item_id,
            canonical_name=row.canonical_name,
            pantry_category=row.category,
            family=row.family,
            relation=_relation_of(result, pantry_item_id),
            reason=DUPLICATE_SLOT_CONFLICT,
        )
        recovery = _candidate_json((record,))
        if recovery != slot.candidates_json:
            await conn.execute(
                _UPDATE_SLOT_CONFLICT, (recovery, slot.recipe_note, slot.ingredient_index)
            )
        report.duplicate_slot_conflicts += 1
        report.conflicts = (
            *report.conflicts,
            SlotConflict(
                recipe_note=slot.recipe_note,
                ingredient_index=slot.ingredient_index,
                raw_value=slot.raw_value,
                pantry_item_id=record.pantry_item_id,
                canonical_name=record.canonical_name,
                reason=record.reason,
            ),
        )

    # --- 3. the hand fix (F6) ---------------------------------------------

    async def set_manual(
        self, recipe_note: str, ingredient_index: int, pantry_item_id: int
    ) -> IngredientMapping:
        """Bind one slot to one Pantry Item by hand, and return the row.

        The order is fixed and each step is load-bearing:

        1. **Validate the id is live in the live catalog.** A `manual` row is
           immutable (F6), so a typo accepted here could only ever be repaired by
           another delete-and-insert, and until then it renders a chip claiming a
           product that does not exist.
        2. **Require an existing row.** `ensure_rows(recipes)` is how a slot comes
           into existence; inventing a `raw_value` for a slot this app never read
           would put a frontmatter string into its own audit anchor.
        3. **`DELETE` then `INSERT`, in one transaction.** The `DELETE` is
           mandatory, not stylistic: F6's `BEFORE UPDATE` trigger raises `ABORT` on
           a manual row, so an update is not available. Delete-and-insert is the
           only mutation path, and it is an auditable pair — the row's `created_at`
           moves, so a re-map is visible as a new row rather than a silent
           overwrite.

        F2's inverted index applies here too, and it is not a property of the
        algorithm's writes: binding a slot to an id **another slot of the same
        recipe already holds** raises `sqlite3.IntegrityError` out of the
        transaction, the `DELETE` rolls back with it, and the previous row is
        intact. A hand fix is not a way around "one recipe lists the same Pantry
        Item twice" — the user who wants that outcome edits the note.

        `raw_value`, `parsed_name`, and `parse_method` are carried over verbatim: a
        hand fix changes *which product this Ingredient is*, not what the
        frontmatter says, and overwriting the audit anchor with the repair would
        destroy the evidence. `match_tier` is `0` and `confidence` is `1.0` — the
        schema's "no tier, full claim" encoding. Note `1.0` is not unique to
        `manual`: tiers 1 and 2 also score `1.0`, which is exactly why
        `match_method` and not `confidence` is the discriminator between a hand fix
        and a first-tier match.
        """
        catalog = self._catalog()
        if pantry_item_id not in catalog.by_id:
            raise UnknownPantryItem(f"{pantry_item_id}")
        key = (recipe_note, ingredient_index)
        async with self._connect() as conn:
            existing = await _select_slots(conn, _SELECT_SLOT, key)
            if not existing:
                raise UnknownIngredientSlot(f"{recipe_note}#{ingredient_index}")
            async with _transaction(conn):
                await conn.execute(_DELETE_SLOT, key)
                await conn.execute(
                    _INSERT_MANUAL,
                    (
                        recipe_note,
                        ingredient_index,
                        existing[0].raw_value,
                        existing[0].parsed_name,
                        existing[0].parse_method,
                        pantry_item_id,
                    ),
                )
            written = await _select_slots(conn, _SELECT_SLOT, key)
        if not written:  # pragma: no cover — the INSERT above cannot be a no-op
            raise UnknownIngredientSlot(f"{recipe_note}#{ingredient_index}")
        return _to_mapping(written[0])

    # --- 4. reads ----------------------------------------------------------

    async def rows_for(self, recipe_note: str) -> tuple[IngredientMapping, ...]:
        """One recipe's rows in slot order — what `/api/recipes` renders.

        The one method beyond §9.10's list, and it is there because none of the
        seven can answer it: `unresolved_rows()` and `stale_rows()` are each a
        filtered subset, and a payload that showed a recipe's resolved Ingredients
        but not its unresolved ones would be the dishonest headline D4 exists to
        prevent.
        """
        async with self._connect() as conn:
            slots = await _select_slots(conn, _SELECT_SLOTS, (recipe_note,))
        return tuple(_to_mapping(slot) for slot in slots)

    async def unresolved_rows(self) -> tuple[IngredientMapping, ...]:
        """Every row the ladder has not resolved, plus every F2-downgraded slot.

        The input to the "re-resolve the unresolved ones" action, and the reason
        `unresolved` is a *state* rather than an absence: an F2 downgrade lands
        here too, so a recipe whose two `材料` both matched one Pantry Item shows
        up in the repair list instead of quietly rendering a single chip. A
        `manual` row is never here, so re-resolving can never overrule a hand fix.
        """
        async with self._connect() as conn:
            slots = await _select_slots(conn, _SELECT_UNRESOLVED)
        return tuple(_to_mapping(slot) for slot in slots)

    async def stale_rows(self) -> tuple[IngredientMapping, ...]:
        """Every row whose `pantry_item_id` is gone from the live catalog.

        The cross-database referential check the schema cannot express (§7.3),
        surfaced as `staleMappingCount` in `/api/recipes` so catalog drift is
        visible without opening the `调试` view. A producer re-import renumbers
        `items.id`, and a renumbered id does not merely dangle — it may now name a
        *different product*, which is the failure that stays silent.

        `manual` rows are included, deliberately and against the usual instinct:
        F6 forbids an algorithm from overwriting a hand fix, so a stale hand fix
        has to be surfaced to the user instead of quietly reset. They are the rows
        most worth showing, because they are the ones nothing else will repair.
        """
        live = self._catalog()
        async with self._connect() as conn:
            slots = await _select_slots(conn, _SELECT_RESOLVED_SLOTS)
        return tuple(
            _to_mapping(slot)
            for slot in slots
            if slot.pantry_item_id is not None and slot.pantry_item_id not in live.by_id
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}(resolved_revision={self._resolved_revision!r})"


# --- row projection --------------------------------------------------------


def _slot_insert_row(
    note: RecipeNote, entry: IngredientEntry
) -> tuple[str, int, str, str | None, str]:
    """The `ensure_rows` insert parameters for one `材料` slot.

    §9.6's parse runs here so the row is auditable *before* any pass touches it.
    The stored `parse_method` is the one the parser reports, not a normalized or
    upgraded value: a re-parse at resolve time reads the same bytes and reaches
    the same answer, so a second answer written here would be a second answer to
    drift.
    """
    parsed = parse_ingredient_value(entry.raw)
    return (note.note_path, entry.index, entry.raw, parsed.parsed_name, parsed.parse_method)


def _to_slot(row: aiosqlite.Row) -> _Slot:
    pantry_item_id = row["pantry_item_id"]
    return _Slot(
        recipe_note=str(row["recipe_note"]),
        ingredient_index=int(row["ingredient_index"]),
        raw_value=str(row["raw_value"]),
        parsed_name=None if row["parsed_name"] is None else str(row["parsed_name"]),
        parse_method=cast("ParseMethod", str(row["parse_method"])),
        match_method=cast("MappingMethod", str(row["match_method"])),
        match_tier=int(row["match_tier"]),
        pantry_item_id=None if pantry_item_id is None else int(pantry_item_id),
        confidence=float(row["confidence"]),
        candidates_json=str(row["candidates_json"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


async def _select_slots(
    conn: aiosqlite.Connection, statement: str, parameters: tuple[object, ...] = ()
) -> tuple[_Slot, ...]:
    """Run one slot query and project every row. In `id` order, never dict order."""
    cursor = await conn.execute(statement, parameters)
    return tuple(_to_slot(row) for row in await cursor.fetchall())


# --- provenance codec ------------------------------------------------------


def _relation_of(result: MatchResult, pantry_item_id: int) -> Relation:
    """The relation the ladder recorded for a row, or a typed no-relation.

    `_Audit` strips the objections from a row the ladder eventually adopted, so a
    conflict record's `relation` is the very one that justified the match. The
    `exact_id` fallback covers the one case where the audit carries nothing — tier
    1 adopting a row — and it cannot mislead, because `reason` is what a reader
    acts on and `relation` is context.
    """
    candidate = result.candidate(pantry_item_id)
    return candidate.relation if candidate is not None else "exact_id"


def _candidate_json(candidates: Sequence[MatchCandidate | CandidateRecord]) -> str:
    return json.dumps(
        [_candidate_payload(candidate) for candidate in candidates],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _candidate_payload(candidate: MatchCandidate | CandidateRecord) -> dict[str, object]:
    """One audit entry. `reason` is the single string, `rejectedBy` the whole list.

    F2 fixes the conflict entry as
    `{"pantryItemId": …, "canonicalName": …, "reason": "duplicate_slot_conflict"}`
    and this is a superset of that shape: the three keys it names are present and
    spelled as it spells them, and the extra `pantryCategory` / `family` /
    `relation` are provenance the conflict record would otherwise throw away.
    """
    rejected: tuple[RejectionReason, ...] = candidate.rejected_by
    if isinstance(candidate, CandidateRecord):
        reason = candidate.reason
    else:
        reason = rejected[0] if rejected else MATCHED_REASON
    return {
        "pantryItemId": candidate.pantry_item_id,
        "canonicalName": candidate.canonical_name,
        "pantryCategory": candidate.pantry_category,
        "family": candidate.family,
        "relation": candidate.relation,
        "reason": reason,
        "rejectedBy": list(rejected),
    }


def _candidate_record(payload: object) -> CandidateRecord:
    """Decode one audit entry. Strict: every key required, every type checked.

    Fail-closed rather than tolerant, because this is the provenance record and a
    lenient reader that skipped a missing key would answer "nothing was rejected"
    about a row that was — a plausible-looking wrong answer about the one thing D1
    says must always be debuggable.
    """
    if not isinstance(payload, dict):
        raise CorruptMappingRow("candidate_not_an_object")
    values: dict[str, object] = {}
    for key in ("pantryItemId", "canonicalName", "pantryCategory", "family"):
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise CorruptMappingRow(f"candidate_{key}_missing_or_mistyped")
        values[key] = value
    for key in ("relation", "reason"):
        if not isinstance(payload.get(key), str):
            raise CorruptMappingRow(f"candidate_{key}_missing_or_mistyped")
        values[key] = payload[key]
    rejected = payload.get("rejectedBy", [])
    if not isinstance(rejected, list) or any(
        isinstance(entry, bool) or not isinstance(entry, str) for entry in rejected
    ):
        raise CorruptMappingRow("candidate_rejectedBy_not_a_string_list")
    return CandidateRecord(
        pantry_item_id=cast("int", values["pantryItemId"]),
        canonical_name=cast("str", values["canonicalName"]),
        pantry_category=cast("str", values["pantryCategory"]),
        family=cast("str", values["family"]),
        relation=cast("Relation", values["relation"]),
        reason=cast("CandidateReason", values["reason"]),
        rejected_by=cast("tuple[RejectionReason, ...]", tuple(rejected)),
    )


def _to_mapping(slot: _Slot) -> IngredientMapping:
    """Decode a row's audit trail. The one place `candidates_json` is parsed."""
    try:
        loaded = json.loads(slot.candidates_json)
    except (TypeError, ValueError) as exc:
        raise CorruptMappingRow("candidates_json_not_json") from exc
    if not isinstance(loaded, list):
        raise CorruptMappingRow("candidates_json_not_a_list")
    return IngredientMapping(
        recipe_note=slot.recipe_note,
        ingredient_index=slot.ingredient_index,
        raw_value=slot.raw_value,
        parsed_name=slot.parsed_name,
        parse_method=slot.parse_method,
        match_method=slot.match_method,
        match_tier=slot.match_tier,
        pantry_item_id=slot.pantry_item_id,
        confidence=slot.confidence,
        candidates=tuple(_candidate_record(entry) for entry in loaded),
        created_at=slot.created_at,
        updated_at=slot.updated_at,
    )


__all__ = [
    "DUPLICATE_SLOT_CONFLICT",
    "MATCHED_REASON",
    "CandidateReason",
    "CandidateRecord",
    "CatalogProvider",
    "ConnectFactory",
    "CorruptMappingRow",
    "IngredientMapping",
    "IngredientMappingStore",
    "MappingError",
    "MappingMethod",
    "ResolveReport",
    "SlotConflict",
    "UnknownIngredientSlot",
    "UnknownPantryItem",
]

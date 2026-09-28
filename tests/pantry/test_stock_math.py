"""F1's per-unit `💵` money obligation, observed through *this* ticket's path.

Run alone:  .venv/bin/python -m pytest tests/pantry/test_stock_math.py -q

`tests/vault/test_pantry.py` already pins the three shape cases against
`untagged_inventory()` itself, and those pins are not repeated here. What is
asserted here is the thing that could still be wrong after all that: **the
number this ticket's own entry point returns**, `PantryStockIndex.
inventory_figure`, plus the frozen parity record
`tests/fixtures/pantry_stock_math_parity.json` that
`scripts/snapshot_pantry_catalog.py --job stock` refuses to refresh while this
app and `Helper/scripts/pantry_snapshot.py` disagree.

**The invariant, restated because it is the single easiest thing in this app to
get wrong.** `💵 $X.XX` on a **parent** line is the *effective per-unit price*,
not an item total. Every consumer excludes the unit parent and counts each open
`k/N` subtask at that price. Counting the parent *and* its units double-counts
($8.97 for a $2.99 two-pack); counting the parent *instead of* its units halves
a half-used item ($2.99 for two units).

**No test here reads the live vault or the live `Helper/` script.** The note is
`tests/fixtures/pantry/Pantry.md` copied into `tmp_path`; the "other side" of
the parity comparison is a number inside the committed fixture, and the live
three-way comparison is a deliberate, human-run act in
`scripts/snapshot_pantry_catalog.py`, exactly like regenerating the golden
catalog snapshot (§13.16 states plainly that only one of the three
implementations is in CI, and that this is a procedural mitigation, not a
guarantee).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Final

import pytest

from app.config import Settings
from app.pantry.catalog import CatalogSnapshot, build_snapshot
from app.pantry.stock import PantryStockIndex
from app.vault.atomic_write import AtomicNoteStore
from app.vault.pantry import PantryError, parse_pantry, untagged_inventory

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
SNAPSHOT: Final = REPO_ROOT / "tests" / "fixtures" / "pantry_items_snapshot.json"
PANTRY_NOTE: Final = REPO_ROOT / "tests" / "fixtures" / "pantry" / "Pantry.md"
PARITY: Final = REPO_ROOT / "tests" / "fixtures" / "pantry_stock_math_parity.json"

#: `pantry_snapshot.py --dry-run` on the frozen note, and the PWA's own answer.
#: They are equal because the fixture was only written when they were — the
#: regeneration script refuses to emit on a disagreement.
FROZEN_COUNT: Final = 40
FROZEN_TOTAL: Final = 166.90
FROZEN_EXCLUDED_PARENTS: Final = 4


class _SnapshotCatalog:
    """A `CatalogSource` over an already-built snapshot.

    The money path does not consult the catalog at all — that is the point of
    F1's split — so this exists only to satisfy the index's constructor, and it
    is empty on purpose: a `💵` figure that changed when the catalog did would be
    a bug, and `test_the_money_figure_does_not_consult_the_catalog` proves it.
    """

    def __init__(self, records: list[list[Any]]) -> None:
        self._snapshot: CatalogSnapshot = build_snapshot(records)

    def snapshot(self) -> CatalogSnapshot:
        return self._snapshot


@pytest.fixture
def store(settings: Settings) -> AtomicNoteStore:
    root = settings.app_data_dir / "vault-recovery"
    root.mkdir(exist_ok=True)
    return AtomicNoteStore(settings.vault_path, root)


def _index(
    settings: Settings,
    store: AtomicNoteStore,
    body: str,
    records: list[list[Any]] | None = None,
) -> Any:
    path = settings.vault_path / settings.pantry_note_relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return PantryStockIndex(
        store,
        settings.pantry_note_relative,
        settings.stock_cache_seconds,
        _SnapshotCatalog(records if records is not None else []),
    )


# --- The frozen figure, through this ticket's path --------------------------


def test_the_agreed_figure_comes_back_through_pantry_stock_index(
    settings: Settings, store: AtomicNoteStore
) -> None:
    """`40 项 · $166.90`, and the four per-unit-excluded parents with it.

    The excluded-parent count is asserted as well as the money, because it is
    the number that proves the exclusion *happened* rather than the money
    happening to balance. `PantryStockIndex.inventory_figure` delegates to
    #7's `untagged_inventory` — the join adds no fourth implementation of the
    math, and this is the assertion that the delegation still returns it.
    """
    index = _index(settings, store, PANTRY_NOTE.read_text("utf-8"))
    try:
        figure = index.inventory_figure
        assert (figure.count, figure.total) == (FROZEN_COUNT, FROZEN_TOTAL)
        assert figure.excluded_parents == FROZEN_EXCLUDED_PARENTS
        # Not a coincidence of the same parser: the frozen bytes and #7's own
        # entry point agree, character for character.
        direct = untagged_inventory(parse_pantry(PANTRY_NOTE.read_bytes()))
        assert (direct.count, direct.total, direct.excluded_parents) == (
            FROZEN_COUNT,
            FROZEN_TOTAL,
            FROZEN_EXCLUDED_PARENTS,
        )
    finally:
        index.close()


def test_the_frozen_parity_fixture_is_the_note_it_claims_to_be(
    settings: Settings, store: AtomicNoteStore
) -> None:
    """`pantry_stock_math_parity.json` records *both* sides, and this is the test
    that the PWA's side still reproduces.

    The fixture's `noteSha256` is what makes the other numbers comparable at all:
    a total is only a claim about particular bytes, and the note is edited from
    Obsidian constantly. So the sha is checked against the *live fixture note*,
    and the fixture's own copy of the PWA's figure is checked against this run's
    — a fixture that had drifted from the note would otherwise be silently
    asserting a number about a file nobody has.
    """
    record = json.loads(PARITY.read_text(encoding="utf-8"))
    assert record["noteSha256"] == hashlib.sha256(PANTRY_NOTE.read_bytes()).hexdigest()
    assert record["generatedFrom"] == "Logistics/库存/Pantry.md"
    # The other side's numbers, kept in the fixture on purpose: §13.16's honest
    # limitation is that only one of the three implementations is in this repo,
    # so the number it agreed with has to be recorded rather than assumed.
    assert (record["helperCount"], record["helperTotal"]) == (FROZEN_COUNT, FROZEN_TOTAL)
    assert record["helperPath"] == "Helper/scripts/pantry_snapshot.py"
    assert (record["count"], record["total"], record["excludedParents"]) == (
        FROZEN_COUNT,
        FROZEN_TOTAL,
        FROZEN_EXCLUDED_PARENTS,
    )

    index = _index(settings, store, PANTRY_NOTE.read_text("utf-8"))
    try:
        figure = index.inventory_figure
        assert (record["count"], record["total"], record["excludedParents"]) == (
            figure.count,
            figure.total,
            figure.excluded_parents,
        )
    finally:
        index.close()


def test_the_money_figure_does_not_consult_the_catalog(
    settings: Settings, store: AtomicNoteStore
) -> None:
    """The `💵` price comes from the LINE, never from the catalog.

    The same note, once against an empty catalog and once against the full
    178-row one, must return the same figure. This is the assertion behind
    §9.20's rule that no recipe response carries `last_price`: a figure that
    moved when the catalog did would be a purchase-history number wearing a
    stock label, and it is the exact Pantry Item / Pantry Stock conflation
    `CONTEXT.md` forbids.
    """
    records = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    empty = _index(settings, store, PANTRY_NOTE.read_text("utf-8"), records=[])
    full = _index(settings, store, PANTRY_NOTE.read_text("utf-8"), records=records)
    try:
        assert empty.inventory_figure == full.inventory_figure
        assert empty.inventory_figure.total == FROZEN_TOTAL
    finally:
        empty.close()
        full.close()


# --- The three shape cases, through this path --------------------------------
#
# Same three cases §9.11.3 names, re-asserted through `inventory_figure` rather
# than through #7's function. They are not a duplicate of that file's tests:
# those pin the *rule*, these pin the *wiring* — that the value a consumer of
# `PantryStockIndex` reads carries the rule, which is the only way a caller can
# be sure of it.


def test_shape_case_a_done_three_of_three_parent_contributes_nothing(
    settings: Settings, store: AtomicNoteStore
) -> None:
    note = (
        "# 1 冰箱\n"
        "- [ ] 三分之二 没了 💵 $3.00 ➕ 2026-09-22\n"
        "\t- [x] 1/3 ➕ 2026-09-22\n"
        "\t- [x] 2/3 ➕ 2026-09-22\n"
        "\t- [x] 3/3 ➕ 2026-09-22\n"
    )
    index = _index(settings, store, note)
    try:
        figure = index.inventory_figure
        assert (figure.count, figure.total) == (0, 0.0)
        # Not the naive "count the [ ] parent" reading, which charges $3.00 for
        # an empty box.
        assert figure.total != 3.0
    finally:
        index.close()


def test_shape_case_a_half_used_parent_contributes_exactly_one_unit_price(
    settings: Settings, store: AtomicNoteStore
) -> None:
    note = (
        "# 1 冰箱\n"
        "- [ ] 甘栗仁 60g*5 300 克 💵 $6.48 ➕ 2026-09-22 🛫 2026-09-24\n"
        "\t- [x] 1/2 ➕ 2026-09-22 ✅ 2026-09-26\n"
        "\t- [ ] 2/2 ➕ 2026-09-22\n"
    )
    index = _index(settings, store, note)
    try:
        figure = index.inventory_figure
        assert (figure.count, figure.total, figure.excluded_parents) == (1, 6.48, 1)
        # Not $12.96 (parent *and* unit) and not $0.00 (a unit has no price of
        # its own — the parent's is the per-unit price).
        assert figure.total not in {0.0, 12.96}
    finally:
        index.close()


def test_shape_case_an_in_progress_unit_counts_as_open(
    settings: Settings, store: AtomicNoteStore
) -> None:
    note = (
        "# 1 冰箱\n"
        "- [ ] 父项 💵 $4.00 ➕ 2026-09-22\n"
        "\t- [/] 1/2 🛫 2026-09-24\n"
        "\t- [ ] 2/2 ➕ 2026-09-22\n"
        "- [/] 单独进行中 💵 $7.00 ➕ 2026-09-22\n"
    )
    index = _index(settings, store, note)
    try:
        figure = index.inventory_figure
        # Two units at $4.00 each, plus the standalone in-progress item.
        assert (figure.count, figure.total, figure.excluded_parents) == (3, 15.0, 1)
    finally:
        index.close()


# --- Fails closed, in money too ---------------------------------------------


def test_a_broken_note_yields_no_figure_at_all(
    settings: Settings, store: AtomicNoteStore
) -> None:
    """A money figure is a stock answer, so it fails closed like every other one.

    This is the property with the largest blast radius in the whole ticket: if a
    broken `Pantry.md` produced `$0.00 · 0 项` instead of an error, the app would
    report an empty pantry for the entire household and every score would deflate
    to `have-been-buying` with nothing on screen to say why.
    """
    path = settings.vault_path / settings.pantry_note_relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe\x00\x01")
    index = PantryStockIndex(
        store,
        settings.pantry_note_relative,
        settings.stock_cache_seconds,
        _SnapshotCatalog([]),
    )
    try:
        with pytest.raises(PantryError, match="pantry_unparseable"):
            _ = index.inventory_figure
        assert index._cached is None
    finally:
        index.close()

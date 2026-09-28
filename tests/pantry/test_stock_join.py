"""F1's Stock Join: three tiers in order, the `unjoined` bucket, and failing closed.

Run alone:  .venv/bin/python -m pytest tests/pantry/test_stock_join.py -q

**No test here reads `/Users/syang/obsidian/syang`.** The note is the committed
frozen copy at `tests/fixtures/pantry/Pantry.md` and the catalog is the committed
frozen snapshot at `tests/fixtures/pantry_items_snapshot.json`, both copied into
`tmp_path` by the fixtures below. The live vault is read by exactly one thing in
this repository — `scripts/snapshot_pantry_catalog.py`, deliberately, by a human.

Nine obligations, kept apart because each fails in its own way:

1. **The three tiers, in that order.** A key reachable by two tiers must be
   answered by the *first* one, which is asserted with an override deliberately
   present for a key tier 1 resolves: the override must not fire, so its
   presence is provably subordinate rather than untested.
2. **Both measured misses resolve**, and only because the override tier is
   load-bearing: with an empty override map the same two lines are `unjoined`
   and absent from `in_stock_ids`.
3. **A duplicate catalog name surfaces both candidates.** Eight basename keys
   hold two rows each, and all eight are asserted, because a `dict` that picks a
   winner looks exactly like a correct answer.
4. **An unjoinable line is absent from `in_stock_ids`, present in `unjoined`,
   and counted** — all three, since each is a separate consumer.
5. **Fails closed at all three modes** (missing / unreadable / unparseable),
   through the index's own refresh, with the path in the message, and the cache
   left unpopulated. The property asserted is the negative one: *no* stock
   property ever answers with an empty set, in either direction.
6. **The override survives a renumbered `items.id`** — the whole reason it keys
   on a `canonical_name` resolved at read time. The test is built so that an
   id-keyed implementation would land on a *different product*, not merely on
   nothing.
7. **`line_overrides.yaml` fails closed** on every malformation, and its two
   shipped entries are both still resolvable against the frozen catalog — a typo
   in a reviewed repair file must be a red build.
8. **The join's universe agrees with the parser's notion of a unit**, and the
   frozen note's measured numbers (45 product lines, 3/40/2/0) are pinned.
9. **Every mutant is killed.** A green test that cannot fail is worse than no
   test, and the join is the app's weakest link: the tier order, the override
   tier's read-time resolution, the `unjoined` bucket, and the fail-closed raise
   are each mutated *in the module's own source* and each is required to break a
   named property. `MUTANTS` at the bottom is the list.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest

from app.config import ConfigurationError, Settings
from app.pantry.catalog import CatalogSnapshot, PantryCatalog, build_snapshot, fold_name
from app.pantry.stock import (
    LINE_OVERRIDES,
    StockJoin,
    StockJoinMiss,
    is_unit_row,
    load_line_overrides,
)
from app.recipes.normalize import normalize_ingredient
from app.vault.atomic_write import AtomicNoteStore, PathSafetyError
from app.vault.pantry import PantryError, parse_pantry

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
STOCK_SOURCE: Final = REPO_ROOT / "app" / "pantry" / "stock.py"
SNAPSHOT: Final = REPO_ROOT / "tests" / "fixtures" / "pantry_items_snapshot.json"
PANTRY_NOTE: Final = REPO_ROOT / "tests" / "fixtures" / "pantry" / "Pantry.md"
OVERRIDES_SOURCE: Final = REPO_ROOT / "app" / "pantry" / "line_overrides.yaml"

#: The two misses F1 measured, with the catalog row each override names. Written
#: out rather than derived so this file states the claim it is checking.
MEASURED_MISSES: Final = (
    ("禾苑 蟹粉鱼肉狮子头 冷冻 280 克", "蟹粉鱼肉狮子头 冷冻", 108),
    (
        "Sanpellegrino CIAO! Peach Sparkling Water, 24-Pack",
        "CIAO! Peach Sparkling Water 24-Pack",
        48,
    ),
)
#: `PantrySnapshot.total` on the frozen note: 50 listed rows, 5 of them bare
#: `k/N` units, 4 of them unit parents. `line_count` is therefore 50 - 5 = 45.
FROZEN_LISTED_ROWS: Final = 50
FROZEN_UNIT_ROWS: Final = 5
FROZEN_LINES: Final = 45
#: 3 exact, 40 basename, 2 override, 0 unresolved. See the module docstring for
#: why this is not the spec's "2 failures in 46 open lines" and why both numbers
#: are right.
FROZEN_EXACT: Final = 3
FROZEN_BASENAME: Final = 40
FROZEN_OVERRIDE: Final = 2
FROZEN_UNJOINED: Final = 0

#: All eight basename keys that hold more than one catalog row, with their ids.
#: The same table is in `scripts/snapshot_pantry_catalog.py` as a regeneration
#: gate; a disagreement between the two is a re-import nobody re-measured.
COLLISIONS: Final[dict[str, tuple[int, int]]] = {
    "小白菜心": (18, 106),
    "台湾旺旺浪味仙 熔岩辣起司口味": (27, 38),
    "韩国紫苏叶": (39, 105),
    "organic 1% milk": (90, 116),
    "优质白桃礼盒": (104, 128),
    "2026fifa世界杯限定联名薯片牛肉派味": (111, 124),
    "poland spring maine spring bottled water": (156, 165),
    "chocolate crepe": (168, 169),
}

#: A three-row catalog built for the tier-**order** test. `Yuzu Tart` and
#: `Yuzu Tart 4 盎司` both reduce to the product core `Yuzu Tart`, so the key
#: `yuzu tart` is reachable from `by_name` (row 1) *and* from `by_basename`
#: (rows 1 and 2) — and the two answers differ, which is what makes the order
#: observable. The ids are not 1/2 in the real catalog; this is a synthetic
#: catalog and says so.
ORDER_ROWS: Final = (
    (1, "Yuzu Tart", "1.1", "[]", None),
    (2, "Yuzu Tart 4 盎司", "1.1", "[]", None),
)
#: A one-line note whose product core is `Yuzu Tart`.
ORDER_NOTE: Final = "# 1 冰箱\n- [ ] Yuzu Tart 💵 $3.00 ➕ 2026-09-22\n"


# --- fixtures ---------------------------------------------------------------


@pytest.fixture(scope="module")
def frozen_records() -> list[list[Any]]:
    """The committed 178-row catalog, as `build_snapshot()` takes it."""
    loaded = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    return loaded


@pytest.fixture
def frozen_catalog(frozen_records: list[list[Any]]) -> CatalogSnapshot:
    return build_snapshot(frozen_records)


class _SnapshotCatalog:
    """A `CatalogSource` over an already-built snapshot — F14, for the join.

    The join needs a catalog and a note, neither of which should require opening
    `pantry_items.db` or touching the vault. `PantryCatalog` is exercised
    separately below, because its own TTL and lifecycle are #8's contract.
    """

    def __init__(self, snapshot: CatalogSnapshot) -> None:
        self._snapshot = snapshot

    def snapshot(self) -> CatalogSnapshot:
        return self._snapshot


@pytest.fixture
def catalog(frozen_catalog: CatalogSnapshot) -> _SnapshotCatalog:
    return _SnapshotCatalog(frozen_catalog)


@pytest.fixture
def store(settings: Settings) -> AtomicNoteStore:
    root = settings.app_data_dir / "vault-recovery"
    root.mkdir(exist_ok=True)
    return AtomicNoteStore(settings.vault_path, root)


def _write(settings: Settings, body: str) -> None:
    path = settings.vault_path / settings.pantry_note_relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _index(
    module: ModuleType,
    settings: Settings,
    store: AtomicNoteStore,
    catalog_source: Any,
    *,
    overrides: Mapping[str, str] | None = None,
    body: str = "# 1 冰箱\n",
) -> Any:
    """A stock index from `module` — the real one, or a mutated copy of it."""
    _write(settings, body)
    return module.PantryStockIndex(
        store,
        settings.pantry_note_relative,
        settings.stock_cache_seconds,
        catalog_source,
        overrides=overrides,
    )


@pytest.fixture
def stock(settings: Settings, store: AtomicNoteStore, catalog: _SnapshotCatalog) -> Any:
    """A stock index over a **frozen** note copied into the tmp vault."""
    import app.pantry.stock as stock_module

    index = _index(stock_module, settings, store, catalog, body=PANTRY_NOTE.read_text("utf-8"))
    try:
        yield index
    finally:
        index.close()


def _hit(join: StockJoin, text: str) -> Any:
    return next(hit for hit in join.hits if hit.text == text)


# --- 1. The three tiers, in order -------------------------------------------


def test_the_exact_tier_answers_a_key_the_basename_tier_also_owns(settings, store) -> None:
    """Tier 1 wins over tier 2 for a key both indexes hold — and over an override.

    The override entry is the point. `overrides` maps `yuzu tart` to a *different*
    product, so if the order were wrong in that direction the id would change; if
    tier 3 ran before tier 1 the answer would be row 2. The correct answer is
    row 1, which is the only one of the three that is neither.
    """
    import app.pantry.stock as stock_module

    catalog = _SnapshotCatalog(build_snapshot(ORDER_ROWS))
    note = "# 1 冰箱\n- [ ] Yuzu Tart 💵 $3.00 ➕ 2026-09-22\n"
    index = _index(
        stock_module,
        settings,
        store,
        catalog,
        overrides={"yuzu tart": "Yuzu Tart 4 盎司"},
        body=note,
    )
    try:
        join = index.join
        assert len(join.hits) == 1
        hit = join.hits[0]
        # Both rows, because `yuzu tart` is a basename collision too — that is
        # the "never hide a duplicate" rule, and it is not the order. The order
        # is the tier and the state: tier 1, `joined`, and the override silent.
        assert hit.item_ids == (1, 2)
        assert (hit.tier, hit.state) == (1, "joined")
        assert join.exact_tier_count == 1
        assert join.basename_tier_count == 0
        assert join.override_tier_count == 0
        assert index.in_stock_ids == frozenset({1, 2})
    finally:
        index.close()


def test_the_basename_tier_reaches_a_row_the_name_tier_cannot(settings, store) -> None:
    """Tier 2, the spec's own example: `空心菜嫩苗 0.95-1.05 磅` -> id 83.

    The pantry side carries a size range; the catalog row is
    `空心菜嫩苗 0.95-1.05 磅` too, so the *names* are not equal after folding and
    only the shared product core is. This is the tier that makes the join work
    for the 40 of 45 lines it carries on the frozen note.
    """
    import app.pantry.stock as stock_module

    index = _index(
        stock_module,
        settings,
        store,
        _SnapshotCatalog(build_snapshot(_import_records())),
        body="# 1 冰箱\n- [ ] 空心菜嫩苗 0.95-1.05 磅 💵 $2.99 ➕ 2026-09-22\n",
    )
    try:
        hit = index.join.hits[0]
        assert hit.tier == 2
        assert hit.state == "joined"
        assert hit.item_ids == (83,)
        assert hit.core == "空心菜嫩苗"
    finally:
        index.close()


def test_the_override_tier_resolves_the_two_measured_misses(stock) -> None:
    """Tier 3, on the two lines F1 measured as unjoinable without it."""
    for line, core, item_id in MEASURED_MISSES:
        hit = _hit(stock.join, line)
        assert hit.core == core
        assert hit.tier == 3, line
        assert hit.state == "override"
        assert hit.item_ids == (item_id,), line
    assert stock.unjoined_line_count == 0
    assert {item_id for _, _, item_id in MEASURED_MISSES} <= stock.in_stock_ids


def test_the_override_file_is_the_only_thing_that_resolves_them(settings, store, catalog) -> None:
    """The load-bearing claim, as a subtraction: no overrides, two misses again.

    Same note, same catalog, same code — the only difference is an empty override
    map. Both lines move from `in_stock_ids` to `unjoined`, which is what "the
    escape hatch is not a nicety" means concretely: without it the only remedy is
    editing the user's vault.
    """
    import app.pantry.stock as stock_module

    index = _index(
        stock_module,
        settings,
        store,
        catalog,
        overrides={},
        body=PANTRY_NOTE.read_text("utf-8"),
    )
    try:
        join = index.join
        assert join.unjoined_count == 2
        assert [(miss.text, miss.core) for miss in join.unjoined] == [
            (line, core) for line, core, _ in MEASURED_MISSES
        ]
        # Not "unjoined and also in the ids": a miss is absent from the joined
        # view entirely, or the chip classifier would colour it in stock.
        assert not {item_id for _, _, item_id in MEASURED_MISSES} & index.in_stock_ids
        assert join.line_count == FROZEN_LINES, "the miss does not change the denominator"
    finally:
        index.close()


# --- 2. A duplicate catalog name surfaces both candidates --------------------


@pytest.mark.parametrize(("key", "ids"), sorted(COLLISIONS.items()))
def test_every_basename_collision_surfaces_both_candidates(
    frozen_records, settings, store, key: str, ids: tuple[int, int]
) -> None:
    """All eight, individually. A `dict[str, CatalogRow]` would drop the loser.

    The pantry line is the collision key verbatim, which is a legal free-text
    line: the point is not that the user typed it, it is that **whichever**
    product of the two the line meant, both are in `in_stock_ids` and neither is
    silently picked. The hit carries the pair, ascending, so a caller that needs
    to disambiguate has the evidence rather than a guess.

    Four of the eight keys are *also* one of the pair's `canonical_name` values,
    so they resolve on tier 1 and three on tier 2 — and both routes must
    surface the pair, or the exact tier becomes a silent winner-picker for
    exactly the duplicates the rule exists to protect.
    """
    import app.pantry.stock as stock_module

    snapshot = build_snapshot(frozen_records)
    expected_tier = 1 if snapshot.by_name.get(key) else 2
    index = _index(
        stock_module,
        settings,
        store,
        _SnapshotCatalog(snapshot),
        body=f"# 1 冰箱\n- [ ] {key} 💵 $1.00 ➕ 2026-09-22\n",
    )
    try:
        hit = index.join.hits[0]
        assert hit.tier == expected_tier, key
        assert hit.item_ids == ids, key
        assert list(hit.item_ids) == sorted(hit.item_ids)
        assert set(ids) <= index.in_stock_ids
    finally:
        index.close()


def test_every_collision_is_a_basename_collision_and_two_are_live(
    stock, catalog: _SnapshotCatalog
) -> None:
    """`canonical_name` is UNIQUE in the producer's schema, so the exact tier can
    never return two rows — which makes `by_basename` the *only* place a
    duplicate can reach the join, and therefore the only place tier 2's
    "surface both" behaviour has to be right.

    On the frozen note two of the eight are held: `organic 1% milk` [90, 116]
    on the basename tier and `Chocolate Crepe (30 servings)` [168, 169] on the
    exact tier. The second is the case the union exists for — tier 1 alone would
    have returned 168 and hidden 169. That is the measured answer, and it is why
    the other six are asserted one at a time above against their own lines
    instead of being folded into a count here.
    """
    snapshot = catalog.snapshot()
    assert all(len(rows) == 1 for rows in snapshot.by_name.values()), (
        "canonical_name is UNIQUE upstream; a by_name collision would be a real defect"
    )
    assert {key for key, rows in snapshot.by_basename.items() if len(rows) > 1} == set(COLLISIONS)
    live = [hit for hit in stock.join.hits if len(hit.item_ids) > 1]
    assert [(hit.text, hit.item_ids, hit.tier) for hit in live] == [
        ("365 By Whole Foods Market, Organic 1% Milk, 32 Fl Oz", (90, 116), 2),
        ("Chocolate Crepe (30 servings)", (168, 169), 1),
    ]


# --- 3. The unjoined bucket -------------------------------------------------


def test_an_unjoinable_line_is_absent_present_and_counted(settings, store, catalog) -> None:
    """All three consequences, because they have three different consumers.

    The chip classifier reads `in_stock_ids`; the `调试` toggle reads `unjoined`;
    `/health` and `GET /api/recipes` read the count. Asserting only the first
    would let the other two regress silently.
    """
    import app.pantry.stock as stock_module

    index = _index(
        stock_module,
        settings,
        store,
        catalog,
        body="# 1 冰箱\n- [ ] 完全无法解释的商品 💵 $9.99 ➕ 2026-09-22\n"
        "- [ ] 空心菜嫩苗 0.95-1.05 磅 💵 $2.99 ➕ 2026-09-22\n",
    )
    try:
        join = index.join
        assert len(join.unjoined) == 1
        miss = join.unjoined[0]
        assert isinstance(miss, StockJoinMiss)
        assert miss.text == "完全无法解释的商品"
        assert miss.core == "完全无法解释的商品"
        assert miss.override is None, "no override fired; this is a plain miss"
        assert miss.line_index >= 0
        assert miss.section == "1"
        assert join.unjoined_count == 1
        assert index.unjoined_line_count == 1
        # Absent from the joined view, present in the name set: the line is on
        # the shelf, it just cannot be identified.
        assert index.in_stock_ids == frozenset({83})
        assert miss.core in index.in_stock_names
    finally:
        index.close()


def test_a_dangling_override_is_reported_with_the_name_it_named(settings, store, catalog) -> None:
    """An entry whose `canonical_name` is not in the live catalog is a *miss*,
    and the miss carries the name it tried — otherwise a typo in a reviewed
    repair file is indistinguishable from no entry at all, and the file quietly
    stops repairing.
    """
    import app.pantry.stock as stock_module

    index = _index(
        stock_module,
        settings,
        store,
        catalog,
        overrides={"完全无法解释的商品": "A Product That Does Not Exist"},
        body="# 1 冰箱\n- [ ] 完全无法解释的商品 💵 $9.99 ➕ 2026-09-22\n",
    )
    try:
        assert index.unjoined_line_count == 1
        miss = index.unjoined[0]
        assert miss.override == "A Product That Does Not Exist"
        assert index.in_stock_ids == frozenset()
    finally:
        index.close()


# --- 4. Fail closed ---------------------------------------------------------


def test_a_missing_note_raises_and_never_answers_empty(settings, store, catalog) -> None:
    import app.pantry.stock as stock_module

    assert not (settings.vault_path / settings.pantry_note_relative).exists()
    index = stock_module.PantryStockIndex(
        store, settings.pantry_note_relative, settings.stock_cache_seconds, catalog
    )
    try:
        _assert_fail_closed(index, "pantry_source_missing")
    finally:
        index.close()


def test_an_unreadable_note_raises_and_never_answers_empty(
    settings, store, catalog, monkeypatch
) -> None:
    import app.pantry.stock as stock_module

    _write(settings, "# 1 冰箱\n- [ ] 有货 💵 $2.99 ➕ 2026-09-22\n")
    index = stock_module.PantryStockIndex(
        store, settings.pantry_note_relative, settings.stock_cache_seconds, catalog
    )
    try:
        def refuse(*args: object, **kwargs: object) -> bytes:
            raise PathSafetyError("unsafe_note")

        monkeypatch.setattr(store, "read_existing_if_exists", refuse)
        _assert_fail_closed(index, "pantry_source_unreadable")
    finally:
        index.close()


def test_an_unparseable_note_raises_and_never_answers_empty(settings, store, catalog) -> None:
    import app.pantry.stock as stock_module

    (settings.vault_path / settings.pantry_note_relative).parent.mkdir(parents=True, exist_ok=True)
    (settings.vault_path / settings.pantry_note_relative).write_bytes(b"\xff\xfe\x00\x01")
    index = stock_module.PantryStockIndex(
        store, settings.pantry_note_relative, settings.stock_cache_seconds, catalog
    )
    try:
        _assert_fail_closed(index, "pantry_unparseable")
    finally:
        index.close()


def _assert_fail_closed(index: Any, code: str) -> None:
    """The raise, the path, the unpopulated cache, and the negative property.

    The last block is the one that matters and the one that is easy to skip: each
    of those properties is a *consumer* of the stock answer, and if any of them
    answered an empty set the app would render "you hold nothing" for every
    recipe in the house — a plausible-looking wrong answer, which is the specific
    thing F1 refuses to ship. Asserted on all of them, in both the direction that
    would inflate and the direction that would deflate.
    """
    with pytest.raises(PantryError) as raised:
        index.snapshot()
    assert str(raised.value) == f"{code}: {index.relative}"
    # Vault-relative, never absolute: the error envelope and `/health` leak no
    # filesystem layout (§9.19), and neither does this one.
    assert str(raised.value).endswith("Logistics/库存/Pantry.md")
    assert not str(raised.value).startswith("/")

    for accessor in (
        "in_stock_ids",
        "in_stock_names",
        "unjoined",
        "sections",
        "open_count",
        "unjoined_line_count",
        "inventory_figure",
        "stock_revision",
    ):
        with pytest.raises(PantryError):
            getattr(index, accessor)
    # A failure does not poison the cache into an empty snapshot, and does not
    # pin "nothing held" for a whole TTL either.
    assert index._cached is None
    assert index._join is None


# --- 5. The override survives a renumbered items.id ------------------------


def test_the_override_follows_the_product_when_the_producer_renumbers_it(
    settings, store, tmp_path
) -> None:
    """`items.id` is AUTOINCREMENT, so a re-import can renumber it. §9.8's rule
    ("never store a Pantry Item id in a lexicon file") is why this file is keyed
    by name.

    Built so an id-keyed implementation lands on a **different product**, not
    merely on nothing: after the renumber, the vacated id 108 is re-used by an
    unrelated row. An id-keyed override would then return 108 and quietly point
    the repair at the wrong food.
    """
    import sqlite3

    import app.pantry.stock as stock_module

    records = [
        [108, "禾苑 蟹粉鱼肉狮子头", "1.2v", "[]", None],
        [48, "Sanpellegrino CIAO! Peach Sparkling Water", "5", "[]", None],
    ]
    db_path = tmp_path / "renumbered" / "pantry_items.db"
    db_path.parent.mkdir(parents=True)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " canonical_name TEXT NOT NULL UNIQUE, category TEXT NOT NULL,"
            " variants TEXT DEFAULT '[]', area TEXT)"
        )
        connection.executemany(
            "INSERT INTO items (id, canonical_name, category, variants, area)"
            " VALUES (?, ?, ?, ?, ?)",
            [tuple(row) for row in records],
        )
        connection.commit()
    finally:
        connection.close()

    catalog = PantryCatalog(replace(settings, pantry_items_db=db_path))
    index = stock_module.PantryStockIndex(
        store, settings.pantry_note_relative, settings.stock_cache_seconds, catalog
    )
    try:
        _write(
            settings,
            "# 1 冰箱\n- [ ] 禾苑 蟹粉鱼肉狮子头 冷冻 280 克 💵 $4.59 ➕ 2026-09-22\n"
            "- [ ] Sanpellegrino CIAO! Peach Sparkling Water, 24-Pack ➕ 2026-09-22\n",
        )
        assert index.in_stock_ids == frozenset({108, 48})
        assert [hit.tier for hit in index.join.hits] == [3, 3]
        assert index.unjoined_line_count == 0

        # The producer re-imports: the products are renumbered, and the freed id
        # is taken by something else entirely.
        connection = sqlite3.connect(db_path)
        try:
            connection.execute("UPDATE items SET id = 9001 WHERE id = 108")
            connection.execute("UPDATE items SET id = 9002 WHERE id = 48")
            connection.execute(
                "INSERT INTO items (id, canonical_name, category, variants, area)"
                " VALUES (108, 'Some Other Product Entirely', '3', '[]', NULL)"
            )
            connection.commit()
        finally:
            connection.close()

        catalog.invalidate()
        index.invalidate()
        assert index.in_stock_ids == frozenset({9001, 9002})
        assert 108 not in index.in_stock_ids, "the vacated id is a different product now"
        # The override's *key* is untouched, which is the other half: the pantry
        # line did not change, only the catalog's numbering.
        assert LINE_OVERRIDES["蟹粉鱼肉狮子头 冷冻"] == "禾苑 蟹粉鱼肉狮子头"
        assert index.unjoined_line_count == 0
    finally:
        index.close()
        catalog.close()


# --- 6. line_overrides.yaml: fail-closed loader, and its two entries ---------


def _override_file(tmp_path: Path, body: str) -> Any:
    """A `Traversable` over a temporary override file.

    `importlib.resources`' own `Traversable` is an interface, not something to
    fabricate; a real file on disk satisfies it, and the loader only ever calls
    `is_file()` and `read_text()`. The return type is `Any` for exactly that
    reason, and the alternative — writing into the package — is not a thing a
    test may do.
    """
    path = tmp_path / "line_overrides.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_the_shipped_override_file_loads_and_names_live_catalog_rows(
    frozen_catalog: CatalogSnapshot,
) -> None:
    """Every shipped key is a product core, and every shipped value resolves.

    The value half is the gate a startup-time loader cannot do: there is no
    catalog at boot, by design, because a `canonical_name` must be resolved
    against the *live* one. So a typo in a reviewed repair file is caught here
    instead — as a red build, not as a rising `unjoined` count nobody is watching.
    """
    loaded = load_line_overrides(OVERRIDES_SOURCE)
    assert dict(loaded) == dict(LINE_OVERRIDES)
    assert loaded, "the file exists to repair the two measured misses"
    for key, canonical_name in loaded.items():
        assert fold_name(key) == key, key
        assert fold_name(normalize_ingredient(key)) == key, key
        assert canonical_name == canonical_name.strip()
        assert frozen_catalog.by_name.get(fold_name(canonical_name)), canonical_name


def test_the_override_path_resolves_through_importlib_resources() -> None:
    """The file is reached as package data, not by a path relative to the cwd.

    That is the whole reason the loader takes a `Traversable`: correct from a
    source checkout *and* from an installed wheel. Whether the wheel actually
    carries it is `tests/scaffold/test_app.py`'s `package-data` gate plus CI's
    wheel-content check; this is the half that only the app can assert.
    """
    from app.pantry.stock import LINE_OVERRIDES_SOURCE

    assert LINE_OVERRIDES_SOURCE.is_file()
    assert LINE_OVERRIDES_SOURCE.name == "line_overrides.yaml"
    assert str(LINE_OVERRIDES_SOURCE).endswith("app/pantry/line_overrides.yaml")


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ("version: 1\n", "invalid_pantry_line_overrides_section"),
        ("overrides: {}\n", "missing_pantry_line_overrides_version"),
        ("version: 2\noverrides: {}\n", "unsupported_pantry_line_overrides_version"),
        ("version: 1\noverrides: []\n", "invalid_pantry_line_overrides_section"),
        ("version: 1\noverrides: {}\nextra: 1\n", "unknown_pantry_line_overrides_keys"),
        ("- a\n- b\n", "invalid_pantry_line_overrides_document"),
        ("version: 1\noverrides: {'Yuzu Tart': 'X'}\n", "pantry_line_override_key_not_normalized"),
        ("version: 1\noverrides: {'yuzu tart': ''}\n", "pantry_line_override_value_not_a_name"),
        ("version: 1\noverrides: {'yuzu tart': ' X '}\n", "pantry_line_override_value_not_a_name"),
        ("version: 1\noverrides: {5: 'X'}\n", "pantry_line_override_key_not_a_name"),
        ('version: 1\noverrides: {"a\\u0007b": "X"}\n', "pantry_line_override_key_not_a_name"),
        (
            'version: 1\noverrides: {"yuzu tart": "A\\u0007B"}\n',
            "pantry_line_override_value_not_a_name",
        ),
        ("version: 1\noverrides: {\n", "unreadable_pantry_line_overrides"),
        ("version: 1\ntiers: {}\n", "unknown_pantry_line_overrides_keys"),
        (
            "version: 1\noverrides:\n  'yuzu tart': 'A'\n  'yuzu tart': 'B'\n",
            "unreadable_pantry_line_overrides",
        ),
        ("version: 1\noverrides: {}\n", None),
    ],
)
def test_every_override_malformation_fails_closed(
    tmp_path: Path, body: str, code: str | None
) -> None:
    if code is None:
        # The one legal empty file: a fresh checkout has nothing to repair, tiers
        # 1 and 2 do not read this file, and the `unjoined` counter is the
        # honest report of a miss. What is *not* legal is a key that could never
        # fire, and the rows above refuse those at startup.
        assert load_line_overrides(_override_file(tmp_path, body)) == {}
        return
    with pytest.raises(ConfigurationError, match=code):
        load_line_overrides(_override_file(tmp_path, body))


def test_a_missing_override_file_is_a_startup_error_not_an_empty_repair_table(
    tmp_path: Path,
) -> None:
    with pytest.raises(ConfigurationError, match="missing_pantry_line_overrides"):
        load_line_overrides(tmp_path / "absent.yaml")


# --- 7. The join's universe, and the frozen note's measured numbers -----------


def test_the_unit_filter_agrees_with_the_parser_on_every_frozen_row() -> None:
    """The join's universe is the parser's notion of a unit, not a second one.

    `app.vault.pantry._UNIT` is the ported parser's marker; this module has its
    own copy for the *filter*, and a copy is a thing that can drift. The
    assertion is over every task row of the real note rather than over a handful
    of examples, so a change to either side that moved them apart is a failure
    here.
    """
    from app.vault import pantry as parser

    source = PANTRY_NOTE.read_bytes()
    rows = re.findall(r"^\s*- \[[^\]]\]\s?(.*)$", source.decode(), flags=re.MULTILINE)
    assert len(rows) == 73, "the frozen note's task-row count is itself pinned by #7"
    parser_marked = {text for text in rows if parser._UNIT.match(text)}
    mine = {text for text in rows if is_unit_row(text)}
    assert mine == parser_marked
    assert all(is_unit_row("1/2") and is_unit_row("23/24") for _ in (0,))
    assert not is_unit_row("空心菜嫩苗 0.95-1.05 磅")


def test_the_frozen_notes_measured_join_is_pinned(stock) -> None:
    """45 product lines: 3 exact, 40 basename, 2 override, **0 unjoined**.

    The tier split is the join's health number, so it is pinned rather than
    recomputed-and-hoped-for. The `unjoined == 0` half is the load-bearing
    claim: with the shipped override file, every open product line on the frozen
    note reaches a live catalog row, and the two lines that could not be
    explained without it are explained.
    """
    join = stock.join
    assert join.line_count == FROZEN_LINES
    assert (join.exact_tier_count, join.basename_tier_count, join.override_tier_count) == (
        FROZEN_EXACT,
        FROZEN_BASENAME,
        FROZEN_OVERRIDE,
    )
    assert join.unjoined_count == FROZEN_UNJOINED
    assert stock.unjoined_line_count == 0
    # 50 listed rows, 5 of them bare units; 4 unit parents ARE product lines,
    # because a half-used multipack is held.
    assert sum(len(section.items) for section in stock.sections) == FROZEN_LISTED_ROWS
    assert sum(1 for i in (i for s in stock.sections for i in s.items) if is_unit_row(i.text)) == (
        FROZEN_UNIT_ROWS
    )
    assert stock.open_count == 46, "PantrySnapshot.total, the dataviewjs's own figure"
    # 44 names for 45 lines: 原味糯米笋 248 克 appears twice on the note.
    assert len(stock.in_stock_names) == 44
    # 46 ids for 44 names: two lines are two-row collisions, one of them reached
    # on the exact tier (`Chocolate Crepe (30 servings)`) and one on the basename
    # tier (`organic 1% milk`).
    assert len(stock.in_stock_ids) == 46


def test_the_money_figure_through_this_path_is_the_agreed_one(stock) -> None:
    """`40 项 · $166.90`, unchanged — the join adds no fourth implementation.

    `PantryStockIndex.inventory_figure` delegates to #7's `untagged_inventory`
    rather than summing anything, so the per-unit invariant has one owner in this
    app. The number is the frozen one `Helper/scripts/pantry_snapshot.py`
    reported for this exact note.
    """
    figure = stock.inventory_figure
    assert (figure.count, figure.total, figure.excluded_parents) == (40, 166.90, 4)
    assert stock.stock_revision == stock.note_revision
    assert stock.stock_revision.startswith("sha256:")


# --- 8. The frozen catalog helper (kept last: it is shared state) -----------


_RECORDS: list[list[Any]] = []


def _import_records() -> list[list[Any]]:
    global _RECORDS
    if not _RECORDS:
        loaded = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        assert isinstance(loaded, list)
        _RECORDS = loaded
    return _RECORDS


# --- 9. Mutation tests: every join mutant is killed --------------------------
#
# The mutants are **real edits to this module's own source**, compiled and loaded
# as a second module, not a hand-written wrong answer. A hand-written mutant
# proves the *test* distinguishes two implementations; a source mutation proves
# the *implementation* is what the test is holding still, which is the claim
# "a green test that cannot fail is worse than none" actually needs.
#
# Each entry is `(name, old, new, what it breaks)`. `old` must appear exactly
# once in the source, and the loader asserts the mutation applied — a mutation
# that silently matches nothing would leave the mutant identical to the original
# and every assertion below would pass for the wrong reason.

_TIER_ORDER_OLD = """    exact = catalog.by_name.get(key)
    basenamed = catalog.by_basename.get(key)
    if exact or basenamed:
"""

_TIER_ORDER_NEW = """    _premature = overrides.get(key)
    exact = catalog.by_name.get(key) if _premature is None else ()
    basenamed = catalog.by_basename.get(key) if _premature is None else ()
    if exact or basenamed:
"""

_OVERRIDE_OLD = """    resolved = catalog.by_name.get(folded) or catalog.by_basename.get(folded)"""

_OVERRIDE_NEW = """    resolved = catalog.by_name.get(key) or catalog.by_basename.get(key)"""

_MISS_OLD = """            if tier == 0:"""

_MISS_NEW = """            if tier == -1:"""

_FAIL_CLOSED_OLD = """        except PantryError as exc:
            raise PantryError(f"{exc.args[0]}: {self._relative}") from exc"""

_FAIL_CLOSED_NEW = """        except PantryError:
            self._join = StockJoin(frozenset(), frozenset(), (), ())
            return"""

MUTANTS: Final = (
    (
        "tier order: the override pre-empts a catalog row",
        _TIER_ORDER_OLD,
        _TIER_ORDER_NEW,
        "test_the_exact_tier_answers_a_key_the_basename_tier_also_owns",
    ),
    (
        "override value not resolved against the live catalog",
        _OVERRIDE_OLD,
        _OVERRIDE_NEW,
        "test_the_override_tier_resolves_the_two_measured_misses",
    ),
    (
        "unjoined bucket never filled",
        _MISS_OLD,
        _MISS_NEW,
        "test_an_unjoinable_line_is_absent_present_and_counted",
    ),
    (
        "fail-closed raise swallowed into an empty stock set",
        _FAIL_CLOSED_OLD,
        _FAIL_CLOSED_NEW,
        "test_a_missing_note_raises_and_never_answers_empty",
    ),
)


def _mutant_module(tmp_path: Path, name: str, old: str, new: str) -> ModuleType:
    """`app.pantry.stock` with one source edit, loaded as its own module.

    Loaded under `app.pantry.<name>` so the relative imports (`..config`,
    `..vault.pantry`, `.catalog`) resolve exactly as they do in the real module —
    the mutant is the same code with one line changed, not a reconstruction.
    """
    source = STOCK_SOURCE.read_text(encoding="utf-8")
    assert source.count(old) == 1, f"mutation {name!r} matched {source.count(old)} sites"
    mutated = source.replace(old, new)
    assert mutated != source
    path = tmp_path / f"{name}.py"
    path.write_text(mutated, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(f"app.pantry.{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[spec.name]
        raise
    return module


def _real_module() -> ModuleType:
    import app.pantry.stock as stock_module

    return stock_module


def _frozen_join(module: ModuleType, catalog: _SnapshotCatalog) -> StockJoin:
    sections = parse_pantry(PANTRY_NOTE.read_bytes()).sections
    return module.join_stock(catalog.snapshot(), sections)


def test_every_mutant_module_differs_from_the_real_one() -> None:
    """The harness itself: a mutant that is not a mutant must be an error.

    Without this, a refactor that moved `if tier == 0:` would make every mutant
    below a no-op and the whole section pass vacuously — which is the exact
    green-test-that-cannot-fail problem, one level up.
    """
    for name, old, new, _broken in MUTANTS:
        source = STOCK_SOURCE.read_text(encoding="utf-8")
        assert source.count(old) == 1, name
        assert old != new, name
        assert new not in source or new == old, f"{name}: the mutant is already the source"


@pytest.mark.parametrize(
    ("name", "old", "new", "broken"),
    MUTANTS,
    ids=[mutant[0] for mutant in MUTANTS],
)
def test_each_join_mutant_is_killed(
    tmp_path: Path, settings, store, catalog, name: str, old: str, new: str, broken: str
) -> None:
    """Each mutant must break the named test, and the real module must not.

    The second half matters as much as the first: it proves the assertion used to
    kill the mutant is satisfied by the shipped code, so "the mutant broke it" is
    a difference between the two rather than a property of a test that never
    passes.
    """
    module = _mutant_module(tmp_path, name.replace(" ", "_").replace(":", ""), old, new)
    real = _real_module()

    real_sections = parse_pantry(PANTRY_NOTE.read_bytes()).sections
    if name.startswith("tier order"):
        # The synthetic ORDER_ROWS catalog, where `yuzu tart` is a key the
        # catalog explains *and* a key the override map claims. The order is only
        # observable where both apply; on the frozen note the two measured misses
        # are the only overridden keys and neither is a catalog hit, so the
        # frozen note cannot tell tier 1 from tier 3 here.
        order_catalog = build_snapshot(ORDER_ROWS)
        sections = parse_pantry(ORDER_NOTE.encode()).sections
        overrides = {"yuzu tart": "Yuzu Tart 4 盎司"}
        real_join = real.join_stock(order_catalog, sections, overrides)
        mutant_join = module.join_stock(order_catalog, sections, overrides)
        assert (real_join.exact_tier_count, real_join.override_tier_count) == (1, 0)
        # The mutation lets the override answer a line the catalog explains on
        # its own, so a typo in a repair file would silently take over a product
        # the catalog already resolves.
        assert (mutant_join.exact_tier_count, mutant_join.override_tier_count) == (0, 1)
    elif name.startswith("override value"):
        real_join = real.join_stock(catalog.snapshot(), real_sections)
        mutant_join = module.join_stock(catalog.snapshot(), real_sections)
        assert real_join.override_tier_count == 2
        assert mutant_join.override_tier_count == 0
        assert [miss.text for miss in mutant_join.unjoined] == [
            line for line, _core, _id in MEASURED_MISSES
        ]
    elif name.startswith("unjoined bucket"):
        real_join = real.join_stock(catalog.snapshot(), real_sections)
        mutant_join = module.join_stock(catalog.snapshot(), real_sections)
        assert real_join.unjoined_count == 0
        synthetic = parse_pantry("# 1 冰箱\n- [ ] 完全无法解释的商品 ➕ 2026-09-22\n".encode())
        assert module.join_stock(catalog.snapshot(), synthetic.sections).unjoined_count == 0
        assert real.join_stock(catalog.snapshot(), synthetic.sections).unjoined_count == 1
    else:  # the fail-closed raise
        assert not (settings.vault_path / settings.pantry_note_relative).exists()
        real_index = real.PantryStockIndex(
            store, settings.pantry_note_relative, settings.stock_cache_seconds, catalog
        )
        mutant_index = module.PantryStockIndex(
            store, settings.pantry_note_relative, settings.stock_cache_seconds, catalog
        )
        try:
            with pytest.raises(PantryError, match="pantry_source_missing"):
                real_index.in_stock_ids  # noqa: B018 — the property *is* the assertion
            # The mutant answers, and answers "you hold nothing" — the
            # plausible-looking wrong answer F1 refuses to ship. Every chip on
            # every recipe in the house drops to `have-been-buying`, with no
            # error anywhere in sight.
            assert mutant_index.in_stock_ids == frozenset()
            assert mutant_index.in_stock_names == frozenset()
            assert mutant_index.unjoined_line_count == 0
        finally:
            real_index.close()
            mutant_index.close()

    del broken  # the id exists so a failing test names the mutant it came from

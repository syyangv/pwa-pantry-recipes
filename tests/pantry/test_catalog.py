"""The PantryCatalog: read-only access, the indexes, and `catalog_revision`.

Run alone:  .venv/bin/python -m pytest tests/pantry/test_catalog.py -q

Ten obligations, deliberately kept apart because each has a different way of
failing quietly:

1. **The catalog is read-only.** `pantry_items.db` is a tracked file in another
   repository, so the strongest guarantee available is the one SQLite itself
   enforces. The write attempt is first made against the *same file* through a
   *writable* connection and required to succeed, so a passing negative test
   cannot be explained by bad SQL or a schema that refuses the row.
2. **`area` removes a row from the candidate universe**, not merely from a name
   index, and the removal is counted instead of vanishing.
3. **`catalog_revision` is content-addressed**: stable across reads and across
   processes, and changing on exactly what can break a mapping — which is a
   different guarantee from the 300 s TTL, so the two are asserted against each
   other.
4. **The name indexes are folded**, and folding is NFKC + trim + casefold and
   *not* internal-whitespace collapsing, because that is tier 5's rule.
5. **`by_family` groups the Pantry Category codes** the way tier 6's `families`
   allowlist needs them grouped.
6. **A name lookup surfaces both duplicates.** Five real products are ingested
   twice, and a lookup that silently picked one would be indistinguishable from
   a correct one.
7. **The normalizer is the shared one**, proved by substituting it and watching
   the index move rather than by comparing outputs.
8. **The lifecycle closes the descriptor** and does not reopen afterwards.
9. **A malformed row fails closed** rather than being skipped.
10. **The purchase-history columns are not projected**, because the catalog is a
    lifetime purchase history and a row existing there says nothing about stock.

The catalog used here is a `tmp_path` SQLite file carrying the producer's real
`items` schema and rows copied from it, so nothing above depends on the sibling
checkout. The last section reads the real 178-row catalog and skips when it is
absent, exactly as `tests/recipes/test_brand_lexicon.py`'s drift gate does.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import os
import re
import sqlite3
import threading
import unicodedata
from collections.abc import Iterator
from dataclasses import fields
from pathlib import Path
from typing import Any, Final

import pytest

from app.config import Settings
from app.pantry import catalog
from app.pantry.catalog import (
    CatalogError,
    CatalogRow,
    CatalogSnapshot,
    PantryCatalog,
    build_snapshot,
    category_family,
    effective_basename,
    fold_name,
    open_read_only,
)
from app.recipes.normalize import normalize_ingredient

# The producer's default catalog location. `PANTRY_ITEMS_DB` overrides it, so the
# live section follows the same server-owned root the app itself reads.
PRODUCER_CATALOG: Final = (
    Path.home() / "projects" / "wholefoods-to-pantry" / "assets" / "pantry_items.db"
)

# Copied from the producer's `assets/pantry_items.db`, column order and types
# included. It is a READ-ONLY producer asset; this app never writes it.
_ITEMS_SCHEMA: Final = """
CREATE TABLE items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name TEXT NOT NULL UNIQUE,
    category       TEXT NOT NULL,
    variants       TEXT DEFAULT '[]',
    first_seen     TEXT,
    last_seen      TEXT,
    order_count    INTEGER DEFAULT 1,
    area           TEXT,
    last_price     REAL
)
"""

# 17 rows, all taken from the real catalog, chosen so every index has something
# to prove: a duplicate pair that collapses onto one basename, a second pair that
# collapses only after the `Chobani` / `Chobani®` distinction, a fullwidth name
# for the NFKC fold, a populated `variants`, the full category-code spread, a
# `skip` row that must NOT be filtered, and the two non-food `area` rows.
SEED_ROWS: Final[tuple[tuple[Any, ...], ...]] = (
    (1, "优质白桃礼盒", "1.1f", "[]", None),
    (2, "优质白桃礼盒 4 磅", "1.1f", "[]", None),
    (3, "Mushroom Dried Morel Mushrooms", "1.2v", "[]", None),
    (4, "Wang Korea 有机去壳甘栗仁 60g*5 300 克", "2", "[]", None),
    (5, "李锦记 蒸鱼豉油 14 盎司", "1.1c", '["蒸鱼豉油"]', None),
    (6, "Acure Ultra Hydrating Shampoo", "6", "[]", "Shampoo"),
    (7, "Dr.Reju-All PDRN Rejuvenating Cream", "6", "[]", "Serum"),
    (8, "Chobani® 20g Protein Lowfat Greek Yogurt Vanilla 6.7oz", "1.1", "[]", None),
    (9, "Chobani 20g Protein Lowfat Greek Yogurt Vanilla 6.7oz", "1.1", "[]", None),
    (10, "空心菜嫩苗 0.95-1.05 磅", "1.1", "[]", None),
    (11, "アイスクリーム 1 リットル", "1.2d", "[]", None),
    (12, "Bell & Evans Chicken Breast, 8 Oz", "1.1", "[]", None),
    (13, "Matchaful Original Matcha Granola 8oz", "3", '["Matchaful Granola"]', None),
    (14, "Whole Foods Market, Organic Baby Kale, 5 oz", "1.1", "[]", None),
    (15, "Coconut Water, 330 ml", "5", "[]", None),
    (16, "Vitamin D3 1000 IU", "skip", "[]", None),
    (17, "Ｅｘｏｔｉｃ Ｆｒｕｉｔ Ｓａｌａｄ", "1.1", "[]", None),
)

AREA_ROW_IDS: Final = (6, 7)
CANDIDATE_COUNT: Final = len(SEED_ROWS) - len(AREA_ROW_IDS)

# Every Pantry Category code the real catalog uses, and the family each belongs
# to. `1.1c` / `1.1d` / `1.1f` are sub-codes of `1.1` and `1.2d` / `1.2m` /
# `1.2s` / `1.2v` of `1.2`; the rest are their own families.
REAL_CATEGORY_CODES: Final[tuple[str, ...]] = (
    "1.1",
    "1.1c",
    "1.1d",
    "1.1f",
    "1.2",
    "1.2d",
    "1.2m",
    "1.2s",
    "1.2v",
    "2",
    "3",
    "4",
    "5",
    "6",
    "skip",
)
EXPECTED_FAMILIES: Final[dict[str, tuple[str, ...]]] = {
    "1.1": ("1.1", "1.1c", "1.1d", "1.1f"),
    "1.2": ("1.2", "1.2d", "1.2m", "1.2s", "1.2v"),
    "2": ("2",),
    "3": ("3",),
    "4": ("4",),
    "5": ("5",),
    "6": ("6",),
    "skip": ("skip",),
}

# The 5 products the real catalog ingests twice. Each pair is one product under
# two names, so a name-based lookup has to surface both members.
REAL_DUPLICATE_PAIRS: Final[tuple[tuple[int, int], ...]] = (
    (104, 128),
    (39, 105),
    (155, 167),
    (27, 38),
    (111, 124),
)

_MUTATING_VERB: Final = re.compile(
    r"\b(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|REPLACE)\b", re.IGNORECASE
)


def _write_catalog(path: Path, rows: tuple[tuple[Any, ...], ...] = SEED_ROWS) -> Path:
    """A producer-shaped catalog file. A test-owned fixture, not a vendor asset."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(_ITEMS_SCHEMA)
        connection.executemany(
            "INSERT INTO items (id, canonical_name, category, variants, area)"
            " VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()
    return path


def _records(rows: tuple[tuple[Any, ...], ...] = SEED_ROWS) -> list[tuple[Any, ...]]:
    """`build_snapshot()`'s input shape, without a database."""
    return [row[:5] for row in rows]


def _env_for(db_path: Path, root: Path) -> dict[str, str]:
    vault = root / "vault"
    data = root / "data"
    vault.mkdir(exist_ok=True)
    data.mkdir(exist_ok=True)
    return {
        "OBSIDIAN_VAULT_PATH": str(vault),
        "APP_DATA_DIR": str(data),
        "PANTRY_ITEMS_DB": str(db_path),
        "PUBLIC_ORIGIN": "https://recipes.test.invalid",
        "TAILSCALE_OWNER_LOGIN": "owner@test.invalid",
        "DEV_IDENTITY": "owner@test.invalid",
        "BIND_HOST": "127.0.0.1",
        "APP_TIMEZONE": "America/New_York",
        "TRUST_TAILSCALE_HEADERS": "false",
    }


def _must(row: CatalogRow | None, row_id: int) -> CatalogRow:
    """`row()` for a test that has already decided the id is live.

    Without it every `catalog.row(83).basename` is a `| None` dereference, and a
    renamed id would surface as a type error rather than as the assertion failure
    it is.
    """
    assert row is not None, row_id
    return row


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _module_ast() -> ast.Module:
    return ast.parse(Path(inspect.getfile(catalog)).read_text(encoding="utf-8"))


def _executed_statements() -> list[str]:
    """Every string this module hands to `Connection.execute`, constants resolved.

    Resolving the module-global names matters: `_SELECT_ITEMS` is the statement
    that matters most, and a checker that only saw literals would miss it.
    """
    statements: list[str] = []
    for node in ast.walk(_module_ast()):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "execute":
            continue
        for argument in node.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                statements.append(argument.value)
            elif isinstance(argument, ast.Name):
                value = getattr(catalog, argument.id, None)
                if isinstance(value, str):
                    statements.append(value)
    return statements


def _non_docstring_strings() -> list[str]:
    """Every string literal in the module that is not a docstring."""
    tree = _module_ast()
    docstrings = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


@pytest.fixture
def catalog_db(tmp_path: Path) -> Path:
    return _write_catalog(tmp_path / "pantry_items.db")


@pytest.fixture
def catalog_settings(tmp_path: Path, catalog_db: Path) -> Settings:
    return Settings.from_mapping(_env_for(catalog_db, tmp_path))


@pytest.fixture
def open_catalog(catalog_settings: Settings) -> Iterator[PantryCatalog]:
    instance = PantryCatalog(catalog_settings)
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def recording_opener(monkeypatch: pytest.MonkeyPatch) -> list[sqlite3.Connection]:
    """The connections `PantryCatalog` actually opened, for lifecycle assertions."""
    opened: list[sqlite3.Connection] = []
    real = catalog.open_read_only

    def recording(db_path: Path) -> sqlite3.Connection:
        connection = real(db_path)
        opened.append(connection)
        return connection

    monkeypatch.setattr(catalog, "open_read_only", recording)
    return opened


# --- 1. The catalog is read-only ------------------------------------------


def test_the_connection_is_opened_through_sqlites_own_read_only_uri(
    open_catalog: PantryCatalog,
) -> None:
    assert open_catalog.connection_uri == f"{open_catalog.database_path.as_uri()}?mode=ro"
    assert open_catalog.connection_uri.endswith("?mode=ro")


def test_a_write_is_refused_through_the_read_only_connection_and_accepted_otherwise(
    catalog_db: Path,
) -> None:
    """The negative control that makes the refusal mean something.

    A test asserting only that the write failed passes just as happily when the
    SQL was nonsense or the schema forbade the row. So the *same statement* runs
    first against a writable connection to the *same file* and must succeed. Only
    then is the failure through `open_read_only()` attributable to the URI — and
    dropping `mode=ro` fails this test, which is the mutation that matters.
    """
    statement = "INSERT INTO items (canonical_name, category) VALUES (?, ?)"

    writable = sqlite3.connect(catalog_db)
    try:
        writable.execute(statement, ("Probe Item", "1.1"))
        writable.commit()
        assert writable.execute("SELECT count(*) FROM items").fetchone() == (
            len(SEED_ROWS) + 1,
        )
    finally:
        writable.close()

    before = _digest(catalog_db)
    connection = open_read_only(catalog_db)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute(statement, ("Another Probe Item", "1.1"))
        assert connection.execute("SELECT count(*) FROM items").fetchone() == (
            len(SEED_ROWS) + 1,
        )
    finally:
        connection.close()
    assert _digest(catalog_db) == before, "the catalog was modified on disk"


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO items (canonical_name, category) VALUES ('x', '1.1')",
        "UPDATE items SET category = '4'",
        "DELETE FROM items",
        "CREATE TABLE forged (id INTEGER)",
        "CREATE INDEX forged_items ON items (canonical_name)",
        "ALTER TABLE items ADD COLUMN forged TEXT",
        "DROP TABLE items",
        "REPLACE INTO items (id, canonical_name, category) VALUES (1, 'x', '1.1')",
        "CREATE TABLE items_mirror AS SELECT * FROM items",
    ],
)
def test_every_mutating_statement_is_refused(catalog_db: Path, statement: str) -> None:
    """Not only `INSERT`.

    A catalog this app cannot write also cannot be re-created, re-indexed, or
    mirrored, and every one of those is a route to turning another repository's
    asset into this app's own writable copy of catalog truth.
    """
    before = _digest(catalog_db)
    connection = open_read_only(catalog_db)
    try:
        with pytest.raises(sqlite3.Error):
            connection.execute(statement)
    finally:
        connection.close()
    assert _digest(catalog_db) == before


def test_the_connection_is_also_statement_level_read_only(catalog_db: Path) -> None:
    """`PRAGMA query_only` is the second, independent layer.

    `mode=ro` is a property of how the file was opened; `query_only` is a property
    of the connection that can be read back off it, which is what makes the
    guarantee assertable instead of inferred from the connect call.
    """
    connection = open_read_only(catalog_db)
    try:
        assert connection.execute("PRAGMA query_only").fetchone() == (1,)
    finally:
        connection.close()


def test_a_path_with_a_space_and_non_ascii_is_opened_correctly(tmp_path: Path) -> None:
    """`as_uri()` percent-encodes, so string-concatenating a raw path into a URI
    would truncate at the first space or `?` and silently open nothing."""
    path = _write_catalog(tmp_path / "producer assets" / "パントリー 棚.db")
    connection = open_read_only(path)
    try:
        assert connection.execute("SELECT count(*) FROM items").fetchone() == (
            len(SEED_ROWS),
        )
    finally:
        connection.close()


def test_the_catalog_actually_connects_through_the_read_only_opener(
    recording_opener: list[sqlite3.Connection], catalog_settings: Settings
) -> None:
    """Proves the wiring, so the write tests above are about *this* module.

    Without the substitution, "the connection refuses a write" is a fact about a
    connection the test built itself, and dropping `mode=ro` from
    `open_read_only` would leave every other test green.
    """
    instance = PantryCatalog(catalog_settings)
    try:
        assert instance.row_count() == CANDIDATE_COUNT
    finally:
        instance.close()
    assert len(recording_opener) == 1


def test_the_module_issues_one_select_one_pragma_and_nothing_else() -> None:
    """A read-only URI is only as good as the SQL sent over it.

    `mode=ro` already blocks every write, so this is a statement about intent:
    the module must not be *reaching* for a write, because the day the connection
    is opened differently that reach would land.
    """
    statements = _executed_statements()
    assert sorted(statements) == sorted([catalog._SELECT_ITEMS, "PRAGMA query_only=ON"])
    assert catalog._SELECT_ITEMS.strip().upper().startswith("SELECT ID,")


def test_the_module_holds_no_mutating_sql_as_a_string() -> None:
    offenders = [
        text for text in _non_docstring_strings() if _MUTATING_VERB.search(text)
    ]
    assert not offenders, offenders


def test_the_producer_file_is_untouched_by_a_full_exercise(
    open_catalog: PantryCatalog, catalog_db: Path
) -> None:
    before = _digest(catalog_db)
    snapshot = open_catalog.snapshot()
    open_catalog.refresh()
    assert len(snapshot.rows) == CANDIDATE_COUNT
    assert open_catalog.row(4) is not None
    assert open_catalog.find_by_basename("空心菜嫩苗") != ()
    assert open_catalog.catalog_revision() == snapshot.catalog_revision
    assert _digest(catalog_db) == before


# --- 2. `area` removes a row from the candidate universe -------------------


def test_a_row_with_an_area_is_absent_from_every_index(open_catalog: PantryCatalog) -> None:
    snapshot = open_catalog.snapshot()
    for row_id in AREA_ROW_IDS:
        assert row_id not in snapshot.by_id
        assert all(row.id != row_id for row in snapshot.rows)
        assert open_catalog.row(row_id) is None
    for index in (snapshot.by_name, snapshot.by_variant, snapshot.by_basename):
        for key, group in index.items():
            assert all(row.id not in AREA_ROW_IDS for row in group), key
    for group in snapshot.by_family.values():
        assert all(row.id not in AREA_ROW_IDS for row in group)
    for name in ("Acure Ultra Hydrating Shampoo", "Dr.Reju-All PDRN Rejuvenating Cream"):
        assert open_catalog.find_by_name(name) == ()
        assert open_catalog.find_by_basename(name) == ()
    # `category` 6 exists on those rows only: a category code is not a reason to
    # keep a non-food row, and `area` is not a synonym for `category`.
    assert open_catalog.family("6") == ()


def test_an_empty_string_area_still_excludes_the_row() -> None:
    """The rule is "non-NULL `area`", not "a non-food `area`".

    Written as a unit because the alternative reading — treat blank as absent — is
    a plausible refactor that would quietly put a non-food row back into the
    candidate universe, and `CONTEXT.md` offers no blank-string evidence to
    settle it by.
    """
    snapshot = build_snapshot([(1, "Blank Area Item", "1.1", "[]", "")])
    assert snapshot.rows == ()
    assert snapshot.excluded_row_count == 1
    assert snapshot.total_row_count == 1


def test_the_excluded_rows_are_counted_rather_than_vanished(
    open_catalog: PantryCatalog,
) -> None:
    snapshot = open_catalog.snapshot()
    assert snapshot.total_row_count == len(SEED_ROWS)
    assert snapshot.excluded_row_count == len(AREA_ROW_IDS)
    assert len(snapshot.rows) == CANDIDATE_COUNT
    assert open_catalog.row_count() == CANDIDATE_COUNT


def test_an_excluded_row_still_contributes_to_the_revision() -> None:
    """A row entering or leaving the candidate universe must register as a change.

    Hashing only the surviving rows would make a newly-set `area` invisible, and
    every index this module hands out would change with no revision to show for
    it — the exact silent drift `catalog_revision` exists to prevent.
    """
    before = build_snapshot([(1, "Item", "1.1", "[]", "Shampoo")]).catalog_revision
    after = build_snapshot([(1, "Item", "1.1", "[]", None)]).catalog_revision
    assert before != after


def test_a_skip_category_row_is_kept() -> None:
    """`skip` is a Pantry Category code, not a synonym for `area`.

    Excluding it here would be this reader making the matcher's food-class
    decision, and it would drop 14 real rows on a judgement no matcher test could
    review.
    """
    snapshot = build_snapshot([(1, "Bulk Water 40 Pack", "skip", "[]", None)])
    assert [row.id for row in snapshot.rows] == [1]
    assert snapshot.by_family["skip"][0].canonical_name == "Bulk Water 40 Pack"


# --- 3. `catalog_revision` ------------------------------------------------


def test_the_revision_is_a_prefixed_sha256(open_catalog: PantryCatalog) -> None:
    revision = open_catalog.catalog_revision()
    assert revision.startswith("sha256:")
    assert len(revision) == len("sha256:") + 64
    int(revision.removeprefix("sha256:"), 16)


def test_the_revision_is_stable_across_two_reads(open_catalog: PantryCatalog) -> None:
    first = open_catalog.snapshot()
    assert open_catalog.snapshot().catalog_revision == first.catalog_revision
    assert open_catalog.refresh().catalog_revision == first.catalog_revision


def test_the_revision_is_stable_across_two_independent_readers(
    catalog_settings: Settings,
) -> None:
    """Not a cached value: two connections, one answer.

    A revision that depended on anything but the rows would differ here, which is
    the property that makes it usable as a value persisted in
    `ingredient_mappings`.
    """
    with PantryCatalog(catalog_settings) as first:
        with PantryCatalog(catalog_settings) as second:
            assert first.catalog_revision() == second.catalog_revision()


def test_the_revision_changes_when_a_canonical_name_changes() -> None:
    mutated = _records()
    mutated[9] = (10, "空心菜嫩苗 0.95-1.05 斤", "1.1", "[]", None)
    assert build_snapshot(mutated).catalog_revision != build_snapshot(_records()).catalog_revision


@pytest.mark.parametrize(
    ("index", "replacement"),
    [
        (9, (10, "空心菜嫩苗 0.95-1.05 斤", "1.1", "[]", None)),
        (3, (4, "Wang Korea 有机去壳甘栗仁 60g*5 300 克", "4", "[]", None)),
        (
            12,
            (
                13,
                "Matchaful Original Matcha Granola 8oz",
                "3",
                '["Matchaful Granola", "Granola"]',
                None,
            ),
        ),
        (15, (16, "Vitamin D3 1000 IU", "skip", "[]", "Shelf")),
    ],
    ids=["name", "category", "variants", "area"],
)
def test_the_revision_changes_on_exactly_what_can_break_a_mapping(
    index: int, replacement: tuple[Any, ...]
) -> None:
    mutated = list(_records())
    mutated[index] = replacement
    assert replacement[0] == mutated[index][0]
    assert build_snapshot(mutated).catalog_revision != build_snapshot(_records()).catalog_revision


def test_a_added_or_removed_row_changes_the_revision() -> None:
    records = _records()
    before = build_snapshot(records).catalog_revision
    added = build_snapshot(records + [(99, "Brand New Row", "1.1", "[]", None)])
    removed = build_snapshot([row for row in records if row[0] != 16])
    assert added.catalog_revision != before
    assert removed.catalog_revision != before


def test_a_renumbered_id_is_a_change() -> None:
    """The reason re-resolution exists at all: `items.id` is `AUTOINCREMENT`, and a
    producer re-import can renumber it out from under a stored mapping."""
    records = _records()
    renumbered = [(row[0] + 1000, *row[1:]) for row in records]
    assert build_snapshot(renumbered).catalog_revision != build_snapshot(records).catalog_revision


def test_the_row_order_does_not_change_the_revision() -> None:
    """`id` order is the hash input, so a differently-ordered feed still hashes
    equal — otherwise every re-import would read as a catalog change."""
    records = _records()
    assert (
        build_snapshot(list(reversed(records))).catalog_revision
        == build_snapshot(records).catalog_revision
    )


def test_the_revision_ignores_the_purchase_history_columns(catalog_db: Path) -> None:
    """`first_seen`, `last_seen`, `order_count`, and `last_price` cannot break a
    mapping, so a re-import that only moves them must not cost a full
    `resolve_all()` of every stored mapping."""
    before = build_snapshot(_records()).catalog_revision
    writable = sqlite3.connect(catalog_db)
    try:
        writable.execute(
            "UPDATE items SET first_seen = '2020-01-01', last_seen = '2026-01-01',"
            " order_count = order_count + 7, last_price = 99.99"
        )
        writable.commit()
        connection = open_read_only(catalog_db)
        try:
            records = connection.execute(catalog._SELECT_ITEMS).fetchall()
        finally:
            connection.close()
    finally:
        writable.close()
    assert build_snapshot(records).catalog_revision == before


def test_a_reformatted_variants_value_is_not_a_catalog_change() -> None:
    """The revision hashes the *parsed* aliases, so JSON whitespace churn from a
    producer re-import does not trigger a re-resolve of every mapping."""
    tight = build_snapshot([(1, "Item", "1.1", '["A","B"]', None)]).catalog_revision
    loose = build_snapshot([(1, "Item", "1.1", '[ "A", "B" ]', None)]).catalog_revision
    assert tight == loose


def test_the_revision_is_unambiguous_across_field_boundaries() -> None:
    """Not a bare concatenation, so a reshuffle of name and category cannot
    reproduce another row's bytes."""
    assert (
        build_snapshot([(1, "AB", "1.1", "[]", None)]).catalog_revision
        != build_snapshot([(1, "A", "B1.1", "[]", None)]).catalog_revision
    )


def test_the_ttl_bounds_staleness_and_the_revision_signals_change(
    tmp_path: Path, catalog_db: Path
) -> None:
    """F11 and §9.11.2 answer different questions, and the difference is the point.

    The TTL decides *when to look again*; the revision decides *whether anything
    changed*. A rebuild that changes nothing must not read as a change, and a
    change must be visible on the first read after it even while the TTL has not
    expired — which is exactly the renumbering case `resolve_all()` exists for.
    """
    now = [1000.0]
    environment = {**_env_for(catalog_db, tmp_path), "CATALOG_CACHE_SECONDS": "300"}
    settings = Settings.from_mapping(environment)
    instance = PantryCatalog(settings, clock=lambda: now[0])
    try:
        first = instance.snapshot()
        producer = sqlite3.connect(catalog_db)
        try:
            producer.execute(
                "UPDATE items SET canonical_name = ? WHERE id = 10", ("空心菜嫩苗 2 磅",)
            )
            producer.commit()
        finally:
            producer.close()

        now[0] += 1.0
        within_ttl = instance.snapshot()
        assert within_ttl is first, "a read inside the TTL window is served from cache"
        assert within_ttl.catalog_revision == first.catalog_revision
        assert within_ttl.by_id[10].canonical_name == "空心菜嫩苗 0.95-1.05 磅"

        now[0] += 298.0
        assert now[0] - 1000.0 < 300.0
        assert instance.snapshot() is first

        now[0] += 2.0
        assert now[0] - 1000.0 >= 300.0
        after_ttl = instance.snapshot()
        assert after_ttl is not first
        assert after_ttl.by_id[10].canonical_name == "空心菜嫩苗 2 磅"
        assert after_ttl.catalog_revision != first.catalog_revision
    finally:
        instance.close()


def test_invalidate_rebuilds_immediately(catalog_settings: Settings) -> None:
    instance = PantryCatalog(catalog_settings)
    try:
        first = instance.snapshot()
        instance.invalidate()
        rebuilt = instance.snapshot()
        assert rebuilt is not first
        assert rebuilt.catalog_revision == first.catalog_revision
    finally:
        instance.close()


# --- 4. The name indexes are folded ---------------------------------------


def test_fold_name_normalizes_trims_and_casefolds() -> None:
    assert fold_name("  Chobani  ") == "chobani"
    assert fold_name("Ｅｘｏｔｉｃ") == "exotic"
    assert fold_name("Chobani®") != fold_name("Chobani")
    assert fold_name("KALE") == fold_name("kale")


def test_fold_name_casefolds_rather_than_lowercases() -> None:
    """`casefold`, not `lower`.

    `lower()` is not a case-insensitive comparison — `STRASSE`.lower() and
    `straße`.lower() differ, and the `ﬁ` ligature lowercases to itself — so a
    `lower()`-based key would silently fail to join a recipe Ingredient to a
    catalog row whose name differs only in case, and would do so without an error
    anywhere.
    """
    assert "STRASSE".lower() != "straße".lower()
    assert "ﬁve".lower() == "ﬁve"
    assert fold_name("STRASSE") == fold_name("straße") == "strasse"
    assert fold_name("ﬁve") == "five"
    assert fold_name("ＦＩＶＥ") == "five"


def test_fold_name_does_not_collapse_internal_whitespace() -> None:
    """Collapsing is tier 5's rule, not the tier-2 key's.

    A fold that collapsed runs would make tier 2 quietly answer for a name it was
    defined not to match, and the two tiers would stop being distinguishable.
    """
    assert fold_name("Bell  &  Evans") != fold_name("Bell & Evans")


def test_by_name_is_casefolded(open_catalog: PantryCatalog) -> None:
    expected = "Chobani® 20g Protein Lowfat Greek Yogurt Vanilla 6.7oz"
    assert open_catalog.find_by_name(expected) == (open_catalog.row(8),)
    assert open_catalog.find_by_name(expected.lower()) == (open_catalog.row(8),)
    assert open_catalog.find_by_name(expected.upper()) == (open_catalog.row(8),)


def test_by_name_is_nfkc_folded(open_catalog: PantryCatalog) -> None:
    fullwidth = "Ｅｘｏｔｉｃ Ｆｒｕｉｔ Ｓａｌａｄ"
    halfwidth = "Exotic Fruit Salad"
    assert fullwidth != halfwidth
    assert open_catalog.find_by_name(fullwidth) == (open_catalog.row(17),)
    assert open_catalog.find_by_name(halfwidth) == (open_catalog.row(17),)
    assert open_catalog.find_by_name("ＥＸＯＴＩＣ ＦＲＵＩＴ ＳＡＬＡＤ") == (
        open_catalog.row(17),
    )


def test_by_variant_is_casefolded_and_nfkc_folded(open_catalog: PantryCatalog) -> None:
    assert open_catalog.find_by_variant("Matchaful Granola") == (open_catalog.row(13),)
    assert open_catalog.find_by_variant("matchaful granola") == (open_catalog.row(13),)
    assert open_catalog.find_by_variant("MATCHAFUL GRANOLA") == (open_catalog.row(13),)
    assert open_catalog.find_by_variant("  Matchaful Granola  ") == (open_catalog.row(13),)
    assert open_catalog.find_by_variant("蒸鱼豉油") == (open_catalog.row(5),)
    assert open_catalog.find_by_variant("Not An Alias") == ()


def test_by_basename_is_folded_too(open_catalog: PantryCatalog) -> None:
    assert open_catalog.find_by_basename("MUSHROOM DRIED MOREL MUSHROOMS") == (
        open_catalog.row(3),
    )
    assert open_catalog.find_by_basename("有机去壳甘栗仁") == (open_catalog.row(4),)


def test_the_key_is_folded_but_the_value_stays_verbatim(
    open_catalog: PantryCatalog,
) -> None:
    """`CatalogRow.canonical_name` keeps the producer's own spelling, so a
    response can show it; only the index key is folded."""
    snapshot = open_catalog.snapshot()
    key = fold_name("Chobani® 20g Protein Lowfat Greek Yogurt Vanilla 6.7oz")
    assert key in snapshot.by_name
    assert snapshot.by_name[key][0].canonical_name == (
        "Chobani® 20g Protein Lowfat Greek Yogurt Vanilla 6.7oz"
    )


def test_the_whole_universe_is_available_for_the_contains_tiers(
    open_catalog: PantryCatalog,
) -> None:
    """`rows` is the tier ladder's scan source, so it must be the full candidate
    universe, in a determinate order, agreeing with `by_id`."""
    snapshot = open_catalog.snapshot()
    assert len(snapshot.rows) == CANDIDATE_COUNT
    assert [row.id for row in snapshot.rows] == sorted(snapshot.by_id)
    assert tuple(snapshot.by_id.values()) == snapshot.rows


# --- 5. `by_family` groups the Pantry Category codes -----------------------


@pytest.mark.parametrize("family", sorted(EXPECTED_FAMILIES))
def test_by_family_groups_the_real_category_codes(family: str) -> None:
    """Every code the real catalog uses, and the family it must land in.

    §9.8 tier 6's `families` allowlist is checked against these, so a family that
    silently absorbed a neighbour would let `芝麻` reach a snack row.
    """
    for code in EXPECTED_FAMILIES[family]:
        assert code in REAL_CATEGORY_CODES
        assert category_family(code) == family, code


def test_every_real_category_code_belongs_to_a_known_family() -> None:
    known: dict[str, str] = {}
    for category in REAL_CATEGORY_CODES:
        family = category_family(category)
        assert family in EXPECTED_FAMILIES, (category, family)
        assert category in EXPECTED_FAMILIES[family]
        known[category] = family
    assert len(known) == len(REAL_CATEGORY_CODES)


def test_a_sub_code_is_not_collapsed_into_its_first_component() -> None:
    """The negative control: taking the *first* component would fuse the two food
    classes the guard exists to separate."""
    assert category_family("1.1c") == "1.1"
    assert category_family("1.2m") == "1.2"
    assert category_family("1.1") != category_family("1.2")
    assert category_family("1.1c") != category_family("1.2c")
    assert category_family("2") == "2"
    assert category_family("skip") == "skip"


def test_by_family_collects_every_row_of_the_family(open_catalog: PantryCatalog) -> None:
    snapshot = open_catalog.snapshot()
    refrigerated = snapshot.by_family["1.1"]
    assert {row.id for row in refrigerated} == {1, 2, 5, 8, 9, 10, 12, 14, 17}
    assert {row.category for row in refrigerated} == {"1.1", "1.1c", "1.1f"}
    assert {row.family for row in refrigerated} == {"1.1"}


def test_the_family_lookup_accepts_a_sub_code(open_catalog: PantryCatalog) -> None:
    """A caller holding a row's `category` gets that category's whole family."""
    assert open_catalog.family("1.1f") == open_catalog.family("1.1")
    assert {row.id for row in open_catalog.family("1.1f")} >= {1, 2}
    assert open_catalog.family("1.2v") == open_catalog.family("1.2")
    assert [row.id for row in open_catalog.family("1.2v")] == [3, 11]
    assert [row.id for row in open_catalog.family("skip")] == [16]
    assert open_catalog.family("9.9z") == ()


# --- 6. A name lookup surfaces both duplicates ----------------------------


def test_a_duplicate_product_surfaces_both_candidates(open_catalog: PantryCatalog) -> None:
    """`优质白桃礼盒` and `优质白桃礼盒 4 磅` are one product ingested twice.

    Stripping the size collapses them onto one basename key, so a
    `dict[str, CatalogRow]` would have picked one by insertion order and dropped
    the other with no diagnostic at all.
    """
    candidates = open_catalog.find_by_basename("优质白桃礼盒")
    assert [row.id for row in candidates] == [1, 2]
    assert {row.canonical_name for row in candidates} == {"优质白桃礼盒", "优质白桃礼盒 4 磅"}


def test_the_exact_name_lookup_still_distinguishes_the_pair(
    open_catalog: PantryCatalog,
) -> None:
    assert [row.id for row in open_catalog.find_by_name("优质白桃礼盒")] == [1]
    assert [row.id for row in open_catalog.find_by_name("优质白桃礼盒 4 磅")] == [2]


def test_the_brand_lexicon_distinction_does_not_collapse_two_products(
    open_catalog: PantryCatalog,
) -> None:
    """`Chobani` and `Chobani®` are separate rows the shared lexicon keeps apart;
    the sizes then make both basenames identical. Two rows, still two."""
    candidates = open_catalog.find_by_basename("Protein Lowfat Greek Yogurt Vanilla")
    assert [row.id for row in candidates] == [8, 9]
    assert open_catalog.find_by_name("Chobani® 20g Protein Lowfat Greek Yogurt Vanilla 6.7oz")
    assert open_catalog.find_by_name("Chobani 20g Protein Lowfat Greek Yogurt Vanilla 6.7oz")


def test_candidates_are_ordered_by_id_so_the_ordering_is_determinate() -> None:
    rows = (
        (7, "Thing", "1.1", "[]", None),
        (3, "Thing 2 磅", "1.1", "[]", None),
        (5, "Thing 3 磅", "1.1", "[]", None),
    )
    assert [row.id for row in build_snapshot(rows).by_basename["thing"]] == [3, 5, 7]


def test_each_duplicate_is_still_reachable_by_its_own_id(open_catalog: PantryCatalog) -> None:
    first = open_catalog.row(1)
    second = open_catalog.row(2)
    assert first is not None and second is not None
    assert first is not second


def test_candidates_for_merges_the_three_name_indexes_without_repeating_a_row() -> None:
    """One lookup across tier 2, tier 3, and tier 4 / the Stock Join.

    A row reachable by more than one route comes back once, keyed by `id`, so a
    merge cannot inflate a candidate count.
    """
    snapshot = build_snapshot(
        [(1, "Matchaful Original Matcha Granola 8oz", "3", '["Matchaful Granola"]', None)]
    )
    assert [row.id for row in snapshot.candidates_for("Matchaful Granola")] == [1]
    assert [row.id for row in snapshot.candidates_for("matchafUl Granola")] == [1]
    assert [
        row.id for row in snapshot.candidates_for("Matchaful Original Matcha Granola 8oz")
    ] == [1]
    assert snapshot.candidates_for("Nothing Like This") == ()


# --- 7. The normalizer is the shared one ----------------------------------


def test_every_basename_is_the_shared_normalizers_output(open_catalog: PantryCatalog) -> None:
    for row in open_catalog.snapshot().rows:
        assert row.basename == normalize_ingredient(row.canonical_name), row.canonical_name
        assert row.basename == effective_basename(row.canonical_name), row.canonical_name


def test_a_food_word_in_the_brand_position_survives(open_catalog: PantryCatalog) -> None:
    """`Mushroom` is a food, and the closed lexicon is why it is not stripped."""
    assert _must(open_catalog.row(3), 3).basename == "Mushroom Dried Morel Mushrooms"
    assert "mushroom dried morel mushrooms" in open_catalog.snapshot().by_basename


def test_the_size_before_brand_order_is_inherited_not_re_derived(
    open_catalog: PantryCatalog,
) -> None:
    """`Wang Korea 有机去壳甘栗仁 60g*5 300 克` reduces to `有机去壳甘栗仁` only
    because size stripping runs first. Re-deriving that order here would be the
    second normalizer this module is forbidden to have."""
    assert _must(open_catalog.row(4), 4).basename == "有机去壳甘栗仁"


def test_the_catalog_routes_its_basenames_through_the_shared_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mutation test: swap the normalizer and every basename must move.

    Asserting equality with `normalize_ingredient()` proves only that the two
    agree today. Substituting a different implementation and watching the index
    change is what proves the catalog *calls* the shared one rather than carrying
    a copy of the logic that happens to match today.
    """
    assert normalize_ingredient("优质白桃礼盒 4 磅") == "优质白桃礼盒"
    monkeypatch.setattr(catalog, "normalize_ingredient", lambda value: "SENTINEL")
    assert {row.basename for row in build_snapshot(_records()).rows} == {"SENTINEL"}


def test_the_module_carries_no_second_normalizer() -> None:
    """Structural, because a private helper would not be imported at all."""
    source = Path(inspect.getfile(catalog)).read_text(encoding="utf-8")
    assert "re.compile" not in source, "a compiled pattern here is a second stripper"
    assert not re.search(r"(?m)^\s*def .*normali", source)
    assert "from ..recipes.normalize import normalize_ingredient" in source


# --- 8. Lifecycle ---------------------------------------------------------


def test_close_releases_the_descriptor(
    recording_opener: list[sqlite3.Connection], catalog_settings: Settings
) -> None:
    instance = PantryCatalog(catalog_settings)
    instance.snapshot()
    assert len(recording_opener) == 1
    instance.close()
    with pytest.raises(sqlite3.ProgrammingError):
        recording_opener[0].execute("SELECT 1")


def test_a_closed_catalog_refuses_to_read_rather_than_reopening(
    catalog_settings: Settings,
) -> None:
    """Closing is terminal on purpose: a catalog that quietly reopens after
    `close()` is the descriptor leak the lifespan's `finally` exists to prevent."""
    instance = PantryCatalog(catalog_settings)
    instance.snapshot()
    instance.close()
    with pytest.raises(CatalogError, match="closed"):
        instance.snapshot()
    with pytest.raises(CatalogError, match="closed"):
        instance.open()


def test_close_is_safe_before_anything_was_opened_and_is_idempotent(
    catalog_settings: Settings,
) -> None:
    instance = PantryCatalog(catalog_settings)
    instance.close()
    instance.close()


def test_the_catalog_works_as_a_context_manager(catalog_settings: Settings) -> None:
    with PantryCatalog(catalog_settings) as instance:
        assert instance.row_count() == CANDIDATE_COUNT
    with pytest.raises(CatalogError):
        instance.snapshot()


def test_a_snapshot_can_be_read_from_another_thread(catalog_settings: Settings) -> None:
    """The lifespan opens on the event loop's thread and FastAPI serves synchronous
    routes in its threadpool, so a default `check_same_thread` connection would
    raise on the first request after startup.

    The read is forced to actually touch SQLite: a snapshot served from cache
    never calls into the driver, so a warm-cache read would pass against a
    connection that could not survive a cold one.
    """
    instance = PantryCatalog(catalog_settings)
    try:
        instance.snapshot()
        instance.invalidate()
        collected: list[CatalogSnapshot] = []
        failure: list[BaseException] = []

        def read() -> None:
            try:
                collected.append(instance.refresh())
            except BaseException as exc:  # noqa: BLE001 - reported as a failure below
                failure.append(exc)

        thread = threading.Thread(target=read)
        thread.start()
        thread.join()
        assert not failure, failure
        assert collected[0].total_row_count == len(SEED_ROWS)
    finally:
        instance.close()



def test_the_uri_mode_alone_refuses_a_write_with_the_statement_layer_disabled(
    catalog_db: Path,
) -> None:
    """The layer `query_only` cannot stand in for.

    `query_only` is a per-connection switch that anything holding the connection
    can turn back off, so it proves nothing about how the *file* was opened. This
    test switches it off and writes again: the refusal that remains is SQLite's
    own read-only URI, and it is the layer that also makes a missing catalog
    un-creatable. Without this, deleting `?mode=ro` from `open_read_only` would
    leave every other read-only test green.
    """
    connection = open_read_only(catalog_db)
    try:
        connection.execute("PRAGMA query_only=OFF")
        assert connection.execute("PRAGMA query_only").fetchone() == (0,)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute(
                "INSERT INTO items (canonical_name, category) VALUES (?, ?)", ("x", "1.1")
            )
    finally:
        connection.close()


def test_the_read_only_opener_never_creates_a_missing_catalog(tmp_path: Path) -> None:
    """AGENTS.md #4: never create that file.

    A writable open of a non-existent path creates an empty database, which would
    present as a permanently empty pantry rather than as a wrong `PANTRY_ITEMS_DB`
    — the exact silent failure the read-only URI rules out. The negative control
    is the same path opened writable, which does create the file.
    """
    missing = tmp_path / "absent" / "pantry_items.db"
    with pytest.raises(sqlite3.Error):
        open_read_only(missing)
    assert not missing.exists()
    assert not missing.parent.exists()

    created = tmp_path / "control" / "pantry_items.db"
    created.parent.mkdir()
    sqlite3.connect(created).close()
    assert created.exists(), "a writable open is what creates a file; the read-only one did not"


def test_a_missing_catalog_fails_closed(tmp_path: Path, catalog_settings: Settings) -> None:
    catalog_settings.pantry_items_db.unlink()
    instance = PantryCatalog(catalog_settings)
    try:
        with pytest.raises(CatalogError, match="pantry_catalog_unreadable"):
            instance.snapshot()
    finally:
        instance.close()


def test_the_error_is_the_repo_fail_closed_type() -> None:
    assert issubclass(CatalogError, ValueError)


# --- 9. Fail closed on a malformed row -------------------------------------


@pytest.mark.parametrize(
    ("record", "code"),
    [
        ((1, "", "1.1", "[]", None), "pantry_item_name_missing"),
        ((1, "   ", "1.1", "[]", None), "pantry_item_name_missing"),
        ((1, None, "1.1", "[]", None), "pantry_item_name_missing"),
        ((1, "Item", "", "[]", None), "pantry_item_category_missing"),
        ((1, "Item", None, "[]", None), "pantry_item_category_missing"),
        (("1", "Item", "1.1", "[]", None), "pantry_item_id_not_integer"),
        ((True, "Item", "1.1", "[]", None), "pantry_item_id_not_integer"),
        ((1, "Item", "1.1", None, None), "pantry_item_variants_not_text"),
        ((1, "Item", "1.1", "[not json", None), "pantry_item_variants_not_json"),
        ((1, "Item", "1.1", '{"a": 1}', None), "pantry_item_variants_not_a_list"),
        ((1, "Item", "1.1", "[]", 7), "pantry_item_area_not_text"),
        ((1, "Item", "1.1"), "unexpected_pantry_catalog_shape"),
    ],
)
def test_a_malformed_row_fails_closed(record: tuple[Any, ...], code: str) -> None:
    with pytest.raises(CatalogError) as raised:
        build_snapshot([record])
    assert code in str(raised.value)


@pytest.mark.parametrize(
    "variants", ['[""]', '["   "]', "[1]", "[null]", '["ok", 2]', "[[]]"]
)
def test_a_malformed_variant_entry_fails_closed(variants: str) -> None:
    """`variants` is where a Pantry Item Alias lives, so a broken one read as "no
    aliases" would quietly resolve fewer rows than the producer published."""
    with pytest.raises(CatalogError, match="pantry_item_variant_not_a_name"):
        build_snapshot([(1, "Item", "1.1", variants, None)])


def test_a_duplicate_id_fails_closed() -> None:
    with pytest.raises(CatalogError, match="duplicate_pantry_item_id"):
        build_snapshot([(1, "One", "1.1", "[]", None), (1, "Two", "1.1", "[]", None)])


def test_an_empty_catalog_is_a_real_answer_not_an_error() -> None:
    """Distinct from a *missing* catalog, which raises. An empty table is
    something the producer did; refusing to start would aim the fail-closed rule
    at the wrong failure."""
    snapshot = build_snapshot([])
    assert snapshot.rows == ()
    assert snapshot.total_row_count == 0
    assert snapshot.excluded_row_count == 0
    assert snapshot.catalog_revision.startswith("sha256:")


# --- 10. The CatalogRow projection ----------------------------------------


def test_the_row_carries_the_identity_columns_and_two_derived_ones() -> None:
    assert {field.name for field in fields(CatalogRow)} == {
        "id",
        "canonical_name",
        "category",
        "variants",
        "family",
        "basename",
    }


@pytest.mark.parametrize(
    "withheld", ["first_seen", "last_seen", "order_count", "last_price"]
)
def test_the_purchase_history_columns_are_not_projected(withheld: str) -> None:
    """`pantry_items.db` is a lifetime *purchase* history. Carrying these on the row
    would invite a reader to treat the catalog as stock, and `红苋菜苗` (139) sits
    in the catalog without being held."""
    assert withheld not in {field.name for field in fields(CatalogRow)}


def test_the_variants_are_the_producers_own_strings(open_catalog: PantryCatalog) -> None:
    """Verbatim, not trimmed, lowercased, or re-serialized on the way in.

    `variants` is a Pantry Item Alias, so the value stored on the row is a value
    a response could show and an override file could be written against; folding
    it here would make the stored form and the producer's form disagree.
    """
    assert _must(open_catalog.row(5), 5).variants == ("蒸鱼豉油",)
    assert _must(open_catalog.row(13), 13).variants == ("Matchaful Granola",)
    assert _must(open_catalog.row(1), 1).variants == ()


def test_the_row_is_frozen() -> None:
    row = CatalogRow(
        id=1,
        canonical_name="Item",
        category="1.1",
        variants=(),
        family="1.1",
        basename="Item",
    )
    with pytest.raises(AttributeError):
        row.canonical_name = "Renamed"  # type: ignore[misc]


# --- 11. Against the real 178-row catalog (skipped when absent) -----------


def _live_path() -> Path:
    configured = os.environ.get("PANTRY_ITEMS_DB", "")
    for candidate in (Path(configured) if configured else None, PRODUCER_CATALOG):
        if candidate is not None and candidate.is_file():
            return candidate
    pytest.skip(
        "pantry_items.db is a read-only asset of the wholefoods-to-pantry "
        "producer and is not in this clone; set PANTRY_ITEMS_DB to run the live "
        "catalog checks. Ticket #9 replaces this with a committed snapshot."
    )


@pytest.fixture
def live(tmp_path: Path) -> Iterator[PantryCatalog]:
    instance = PantryCatalog(Settings.from_mapping(_env_for(_live_path(), tmp_path)))
    try:
        yield instance
    finally:
        instance.close()


def test_the_live_catalog_is_the_178_row_producer_asset(live: PantryCatalog) -> None:
    snapshot = live.snapshot()
    assert snapshot.total_row_count == 178
    assert len(snapshot.rows) == 176
    assert snapshot.excluded_row_count == 2


def test_the_live_area_rows_are_the_two_non_food_products(live: PantryCatalog) -> None:
    assert live.row(49) is None
    assert live.row(50) is None
    for name in ("Acure Ultra Hydrating Shampoo", "Dr.Reju-All PDRN Rejuvenating Cream"):
        assert live.find_by_name(name) == ()
    assert live.find_by_name("Acure Ultra Hydrating Shampoo") == ()
    # `category` 6 is not only on the excluded rows: the catalog also carries one
    # household row with a NULL `area`. So the assertion is about *which* rows
    # reach family `6`, not about the family being empty.
    assert [row.id for row in live.family("6")] == [24]


def test_the_live_duplicate_pairs_both_surface(live: PantryCatalog) -> None:
    """All 5 measured duplicate products: each member present, and a pair that
    collapsed to a single candidate would be a silent pick."""
    for first_id, second_id in REAL_DUPLICATE_PAIRS:
        first = live.row(first_id)
        second = live.row(second_id)
        assert first is not None and second is not None, (first_id, second_id)
        assert first.canonical_name != second.canonical_name
        assert (first.id,) == tuple(row.id for row in live.find_by_name(first.canonical_name))
        assert (second.id,) == tuple(row.id for row in live.find_by_name(second.canonical_name))


def test_the_live_pairs_that_collapse_onto_one_basename_both_surface(
    live: PantryCatalog,
) -> None:
    """Four of the five pairs differ only by a size, so the basename index is
    where they collide. `155` / `167` differ by container as well as size and
    stay apart, which is the size stripper's boundary, not a lost row."""
    for first_id, second_id in REAL_DUPLICATE_PAIRS:
        first = live.row(first_id)
        second = live.row(second_id)
        assert first is not None and second is not None
        candidates = live.find_by_basename(first.basename)
        if first.basename == second.basename:
            assert {row.id for row in candidates} >= {first_id, second_id}, (
                first_id,
                second_id,
            )
        else:
            assert (first.id,) == tuple(row.id for row in candidates)


def test_the_live_basename_index_holds_every_collision(live: PantryCatalog) -> None:
    collisions = {
        key: [row.id for row in rows]
        for key, rows in live.snapshot().by_basename.items()
        if len(rows) > 1
    }
    assert collisions == {
        "小白菜心": [18, 106],
        "台湾旺旺浪味仙 熔岩辣起司口味": [27, 38],
        "韩国紫苏叶": [39, 105],
        "organic 1% milk": [90, 116],
        "优质白桃礼盒": [104, 128],
        "2026fifa世界杯限定联名薯片牛肉派味": [111, 124],
        "poland spring maine spring bottled water": [156, 165],
        "chocolate crepe": [168, 169],
    }


def test_the_live_folded_name_index_loses_no_row(live: PantryCatalog) -> None:
    """`canonical_name` is UNIQUE, so a folded collision is not expected — and if
    one appeared it would be a second row under one key, never a dropped one."""
    snapshot = live.snapshot()
    assert sum(len(rows) for rows in snapshot.by_name.values()) == len(snapshot.rows)
    for row in snapshot.rows:
        key = fold_name(row.canonical_name)
        assert [candidate.id for candidate in snapshot.by_name[key]] == [row.id]


def test_the_fold_is_needed_because_some_real_names_are_not_nfkc_normal(
    live: PantryCatalog,
) -> None:
    """`柴米 薄百叶（干豆腐皮） 冷冻 227 克` carries fullwidth parentheses, so its
    NFKC form differs from the stored bytes. The key folds it; the value does not,
    which is why the row can still be shown with the producer's own spelling."""
    unnormalized = [
        row.canonical_name
        for row in live.snapshot().rows
        if unicodedata.normalize("NFKC", row.canonical_name) != row.canonical_name
    ]
    assert unnormalized, "the real catalog is expected to carry fullwidth punctuation"
    assert "柴米 薄百叶（干豆腐皮） 冷冻 227 克" in unnormalized
    row = live.row(44)
    assert row is not None
    assert live.find_by_name("柴米 薄百叶(干豆腐皮) 冷冻 227 克") == (row,)


def test_the_live_basenames_are_the_shared_normalizers_output(live: PantryCatalog) -> None:
    for row in live.snapshot().rows:
        assert row.basename == normalize_ingredient(row.canonical_name), row.canonical_name


def test_the_live_food_class_cases_the_spec_names(live: PantryCatalog) -> None:
    assert _must(live.row(83), 83).basename == "空心菜嫩苗"
    assert live.find_by_basename("空心菜嫩苗") == (live.row(83),)
    assert _must(live.row(177), 177).category == "1.1c"
    assert _must(live.row(177), 177).family == "1.1"
    assert _must(live.row(155), 155).basename != _must(live.row(167), 167).basename


def test_the_live_variant_alias_index_is_near_dead_by_design(live: PantryCatalog) -> None:
    """3 of 178 rows carry a `variants` value and two of those are just the long
    product name. Near-zero tier-3 coverage is the expected outcome, not a bug."""
    populated = [row for row in live.snapshot().rows if row.variants]
    assert len(populated) == 3
    for row in populated:
        for alias in row.variants:
            assert (row.id,) == tuple(
                candidate.id for candidate in live.find_by_variant(alias)
            )


def test_the_live_revision_is_stable_and_never_pinned(live: PantryCatalog) -> None:
    """Stability is asserted; the digest itself deliberately is not.

    Pinning the hash here would make every producer re-import a local test
    failure — the exact behaviour F14's committed snapshot exists to replace.
    """
    revision = live.catalog_revision()
    assert revision == live.refresh().catalog_revision
    assert revision.startswith("sha256:")
    assert len(revision) == len("sha256:") + 64


def test_the_live_catalog_holds_products_that_are_not_currently_held(
    live: PantryCatalog,
) -> None:
    """`红苋菜苗` (139) and `新鲜小叶茼蒿` (70) are catalog rows and not stock.

    Stated as a test because it is the fact most likely to be misread: nothing in
    this module answers "do I have it right now".
    """
    assert live.row(139) is not None
    assert live.row(70) is not None
    assert _must(live.row(139), 139).basename == "红苋菜苗"
    assert _must(live.row(70), 70).basename == "新鲜小叶茼蒿"

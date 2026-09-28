"""The read-only Pantry Item catalog: `pantry_items.db`'s `items` table, in memory.

`wholefoods-to-pantry` owns `pantry_items.db` and commits it to its own git
repository, so this module is a strictly read-only *reader* of another project's
tracked file (AGENTS.md #4). Two independent mechanisms enforce that, and both
are asserted in `tests/pantry/test_catalog.py`:

- **SQLite's own read-only URI.** `open_read_only()` builds
  `file:<path>?mode=ro` and passes `uri=True`, so the file is never opened
  read-write and a write statement fails inside SQLite rather than in a reviewer's
  head. `mode=ro` also means this code cannot create the file: a missing catalog
  is an error, never a plausible-looking empty pantry.
- **`PRAGMA query_only`.** A statement-level layer costing one line, which makes
  the invariant readable back off a live connection instead of only inferable from
  the connect call.

No `CREATE`, no `ALTER`, no `INSERT` appears in this module, and the SQL it does
issue is a single `SELECT` over `items`.

**Identity, not stock.** The catalog is a lifetime *purchase* history: it has
`order_count`, `first_seen`, `last_seen`, and `last_price`, and it has no
consumption state whatsoever. `红苋菜苗` (139) and `新鲜小叶茼蒿` (70) are catalog
rows and neither is currently held, so a row's existence is not an inventory
claim. F1 therefore splits the two questions across two sources, and this module
is only the first one: it supplies the rename-stable `id`, the name, and the
effective basename. The four purchase-history columns are **not** projected into
`CatalogRow` at all — projecting them would invite a future reader to treat the
catalog as stock, which is the Pantry Item / Pantry Stock conflation
`CONTEXT.md` forbids. Reading `Logistics/库存/Pantry.md` is a different module's
job and nothing here may be described as answering it.

**One normalizer, not two.** `basename` is `normalize_ingredient()` from
`app.recipes.normalize` — the shipped, tested product-core stripper. A second
stripper here would be a second answer to the same question, and the two would
drift the first time a brand entered the lexicon. `Mushroom Dried Morel
Mushrooms` keeps its leading `Mushroom` because that is what the closed lexicon
says, not because this module guessed.

**A name index is one-to-many, and this one is a `tuple` for a measured reason.**
Five products are ingested twice under two names (`优质白桃礼盒` 104/128,
`韩国紫苏叶` 39/105, `POM … 16 Ounce` 155/167, `浪味仙 86 克` 27/38,
`乐事…牛肉派味` 111/124), and once sizes and brands are stripped those pairs
collapse onto one key — 8 keys hold more than one row. A `dict[str, CatalogRow]`
would pick a winner by insertion order and the loser would vanish with no
diagnostic, which is the one failure a *name* lookup must never have. So every
string-keyed index maps to a tuple of candidates in ascending `id` order, and
picking between them is the matcher's decision with the `调试` provenance
toggle beside it, not this index's.

**`area` removes a row from the candidate universe.** The producer's `area`
column holds a non-food area (`Shampoo`, `Serum`) and `CONTEXT.md` is explicit
that it "is not a synonym for `category` and must not be treated as one". The
filter is applied once, to the row stream, before any index is built — so an
excluded row is not merely unreachable by name, it is absent from `rows`,
`by_id`, and every other projection. The count is carried on the snapshot
(`excluded_row_count`) so the loss is visible rather than silent. Rows whose
`category` is the `skip` code are **not** filtered: that code is a Pantry
Category, and what to do with a non-food category is the matcher's food-class
guard, not a decision this reader gets to make.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Final

from ..config import Settings
from ..recipes.normalize import normalize_ingredient

#: The one statement this module issues. Five columns, `id` order, no filter:
#: `catalog_revision` is a hash of the *table*, and filtering the rows that feed
#: it would make a row entering or leaving the candidate universe invisible to
#: the very signal that exists to detect exactly that.
_SELECT_ITEMS: Final = (
    "SELECT id, canonical_name, category, variants, area FROM items ORDER BY id"
)

#: SQLite's read-only URI mode. The value is part of the URI, not a flag.
_READ_ONLY_MODE: Final = "ro"

_REVISION_PREFIX: Final = "sha256:"

#: The `variants` value the producer's schema defaults to, and the one its three
#: populated rows are the opposite of. Parsed, not string-compared, so a
#: re-import that reformats the JSON does not read as a catalog change.
_EMPTY_VARIANTS: Final = "[]"

#: A sub-code's numeric head: `1c` -> `1`, `2m` -> `2`. Spelled as a digit set
#: rather than a pattern so this module compiles no regular expression, which is
#: what keeps "there is no second normalizer here" a structural fact.
_DIGITS: Final = frozenset("0123456789")


class CatalogError(ValueError):
    """The catalog could not be read as a Pantry Item catalog.

    Raised per defect and never swallowed into an empty index: an empty pantry is
    indistinguishable from a misconfigured `PANTRY_ITEMS_DB`, and AGENTS.md #3
    forbids the second reading silently.
    """


@dataclass(frozen=True)
class CatalogRow:
    """One Pantry Item, projected from the producer's `items` row.

    `family` is the leading component of the Pantry Category code — `1.1`,
    `1.1c`, `1.1d`, and `1.1f` all share family `1.1` — which is the
    food-class guard the matcher's synonym tier needs. It is a code and is never
    rendered as a label: `CONTEXT.md` defines no code-to-display-text mapping, so
    inferring one from the digits would be inventing vocabulary.

    `basename` is the tier-4 effective basename, `canonical_name` reduced by the
    shared `normalize_ingredient()`.

    The producer's `first_seen`, `last_seen`, `order_count`, and `last_price` are
    absent by design. See the module docstring.
    """

    id: int
    canonical_name: str
    category: str
    variants: tuple[str, ...]
    family: str
    basename: str


@dataclass(frozen=True)
class CatalogSnapshot:
    """One materialization of the `items` table and its six indexes.

    A frozen dataclass holding mutable dicts is deliberate: the indexes are built
    once, handed out read-only by convention, and replaced wholesale on the next
    refresh rather than mutated. There is no write path that could reach them —
    the connection they were read through cannot be written to at all.
    """

    rows: tuple[CatalogRow, ...]
    by_id: dict[int, CatalogRow]
    by_name: dict[str, tuple[CatalogRow, ...]]
    by_variant: dict[str, tuple[CatalogRow, ...]]
    by_basename: dict[str, tuple[CatalogRow, ...]]
    by_family: dict[str, tuple[CatalogRow, ...]]
    catalog_revision: str
    #: Every row in `items`, including the `area`-excluded ones, so
    #: `/health`'s `pantry_db.rowCount` reports the table and not the projection.
    total_row_count: int
    #: How many rows left the candidate universe because `area` was not NULL.
    excluded_row_count: int

    def candidates_for(self, name: str) -> tuple[CatalogRow, ...]:
        """Every row reachable from `name` by exact name, alias, or basename.

        The three name-keyed indexes in one call, because a lookup that has to
        pick which index to consult *before* it knows which one holds the answer
        is how a recipe Ingredient silently fails to resolve.
        """
        key = fold_name(name)
        merged: dict[int, CatalogRow] = {}
        for source in (self.by_name, self.by_variant, self.by_basename):
            for row in source.get(key, ()):
                merged.setdefault(row.id, row)
        return tuple(merged[row_id] for row_id in sorted(merged))


def fold_name(value: str) -> str:
    """NFKC-normalize, trim, and casefold a name into an index key.

    Trimming is part of the rule rather than cosmetic convenience: tier 2 is
    "exact after NFKC + whitespace trim only", so a key that did not trim could
    not be looked up by the value tier 2 compares. Collapsing internal
    whitespace would be a *different* rule and belongs to the tier-5
    normalization, not here.
    """
    return unicodedata.normalize("NFKC", value).strip().casefold()


def effective_basename(canonical_name: str) -> str:
    """The tier-4 basename of a `canonical_name`, through the shared normalizer.

    A named re-export rather than a private helper so the reuse is visible at the
    call site and so a test can substitute a wrong implementation and watch the
    index move. Writing a second stripper here is the failure this forbids.
    """
    return normalize_ingredient(canonical_name)


def category_family(category: str) -> str:
    """The leading component of a Pantry Category code: `1.1c` -> `1.1`.

    The numeric head of the *second* component, not the second component itself:
    `1.1c` is a sub-code of the `1.1` refrigerated family, and taking the whole
    component would make every sub-code its own family, while taking only the
    first component would collapse `1.1` and `1.2` into `1` and leave the
    food-class guard unable to tell produce from frozen. A code with no dot is its
    own family (`2`, `skip`).
    """
    head, separator, tail = category.partition(".")
    if not separator:
        return head
    digits = 0
    while digits < len(tail) and tail[digits] in _DIGITS:
        digits += 1
    return f"{head}.{tail[:digits] or tail}"


def open_read_only(db_path: Path) -> sqlite3.Connection:
    """Open the producer's catalog through SQLite's own read-only URI.

    `Path.as_uri()` percent-encodes the path, so a catalog whose directory
    contains a space or a non-ASCII character is opened correctly rather than
    being truncated at the first `?` — the failure mode of string-concatenating
    a raw path into a URI.

    `check_same_thread=False` is required, not a convenience: the lifespan opens
    this connection on the event loop's thread and FastAPI serves synchronous
    routes in its worker threadpool, so a default connection would raise
    `ProgrammingError` on the first request after startup. Every read in this
    module goes through `Connection.execute`, which allocates a fresh cursor, and
    each result is consumed immediately, so two readers of the same read-only
    connection never share cursor state.

    `PRAGMA query_only` is set on top of the URI mode as a second, independent
    statement-level layer. It is readable back off a live connection, which is
    what makes the guarantee assertable rather than merely inferable from the
    connect call.
    """
    connection = sqlite3.connect(
        f"{db_path.as_uri()}?mode={_READ_ONLY_MODE}", uri=True, check_same_thread=False
    )
    connection.execute("PRAGMA query_only=ON")
    return connection


class PantryCatalog:
    """A TTL snapshot provider over `PANTRY_ITEMS_DB` (spec §9.11.1).

    Construction opens nothing; the first `snapshot()` opens the read-only
    connection and builds the indexes, and later calls are served from them until
    `catalog_cache_seconds` has elapsed on a monotonic clock. `refresh()` forces
    a rebuild, `invalidate()` drops the cache, and `close()` releases the
    descriptor. `close()` is terminal, so the lifespan's `finally` — spec §9.19,
    whose wiring belongs to a later ticket — is the whole of the shutdown
    contract and a descriptor leak cannot outlive it.

    The 300 s TTL and `catalog_revision` answer different questions and both are
    needed. The TTL bounds *staleness*: the producer re-imports on its own
    schedule, and re-reading 178 rows on every request would be pure cost. The
    revision is a *change signal*: it is compared against the revision recorded
    at the last resolve, and a difference runs `resolve_all()` once. So a
    renumbered `id` is repaired at the next boot even if the TTL has not expired,
    and a TTL expiry that changes nothing costs one wasted rebuild instead of a
    re-resolve of every mapping.
    """

    def __init__(
        self, settings: Settings, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._path: Path = settings.pantry_items_db
        self._ttl: float = settings.catalog_cache_seconds
        self._clock: Callable[[], float] = clock
        self._connection: sqlite3.Connection | None = None
        self._cached: CatalogSnapshot | None = None
        self._loaded_at: float = 0.0
        self._closed: bool = False

    @property
    def database_path(self) -> Path:
        """The server-owned catalog path, resolved by `Settings`."""
        return self._path

    @property
    def connection_uri(self) -> str:
        """The exact URI `open_read_only()` will connect through.

        Exposed so the read-only guarantee is inspectable from outside the module
        rather than something a reader has to take on trust.
        """
        return f"{self._path.as_uri()}?mode={_READ_ONLY_MODE}"

    def open(self) -> None:
        """Open the read-only connection. Idempotent; called for you on demand.

        `Settings` already proved the file exists at startup, so a failure here is
        a path that went away between validation and first read. It raises
        `CatalogError` rather than yielding an empty catalog, because an empty
        catalog is the one result that cannot be told apart from a wrong setting.
        """
        if self._closed:
            raise CatalogError("pantry_catalog_closed")
        if self._connection is not None:
            return
        try:
            self._connection = open_read_only(self._path)
        except sqlite3.Error as exc:
            raise CatalogError("pantry_catalog_unreadable") from exc

    def close(self) -> None:
        """Close the connection, terminally. Idempotent, and safe before `open()`.

        The only descriptor this module holds is the SQLite connection, so this is
        the whole of the shutdown contract. Two things go with it. The cached
        snapshot is dropped, because a snapshot outliving its connection is a
        valid answer to a question about a file this process no longer has open.
        And the catalog is marked closed rather than merely reset, because a
        resource that quietly reopens on the next read is the descriptor leak the
        lifespan's `finally` exists to prevent — the leak would reappear on the
        first request after shutdown instead of at shutdown.
        """
        self._closed = True
        connection = self._connection
        self._connection = None
        self._cached = None
        self._loaded_at = 0.0
        if connection is not None:
            connection.close()

    def __enter__(self) -> PantryCatalog:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        self.close()

    def snapshot(self) -> CatalogSnapshot:
        """The current snapshot, rebuilding it once the TTL has elapsed."""
        cached = self._cached
        if cached is not None and (self._clock() - self._loaded_at) < self._ttl:
            return cached
        return self.refresh()

    def refresh(self) -> CatalogSnapshot:
        """Re-read the table unconditionally and replace the cache."""
        self.open()
        connection = self._connection
        if connection is None:  # pragma: no cover — open() either sets or raises
            raise CatalogError("pantry_catalog_unreadable")
        try:
            records = connection.execute(_SELECT_ITEMS).fetchall()
        except sqlite3.Error as exc:
            raise CatalogError("pantry_catalog_unreadable") from exc
        snapshot = build_snapshot(records)
        self._cached = snapshot
        self._loaded_at = self._clock()
        return snapshot

    def invalidate(self) -> None:
        """Drop the cache so the next read rebuilds it.

        Wired for symmetry with `RecipeIndex` and for the future boot path that
        compares `catalog_revision` against the revision recorded at the last
        resolve. Nothing in this app writes the catalog, so there is no call site
        for it yet.
        """
        self._cached = None
        self._loaded_at = 0.0

    def catalog_revision(self) -> str:
        """The revision of the table as it stands, `"sha256:"` + hex digest."""
        return self.snapshot().catalog_revision

    def row_count(self) -> int:
        """Rows in the candidate universe: every row whose `area` is NULL."""
        return len(self.snapshot().rows)

    def row(self, item_id: int) -> CatalogRow | None:
        """One row by its `id`, or `None` when the catalog has no such row.

        `None` is the normal answer after a producer re-import renumbers an id,
        which is exactly the case `catalog_revision` exists to detect.
        """
        return self.snapshot().by_id.get(item_id)

    def find_by_name(self, name: str) -> tuple[CatalogRow, ...]:
        """Candidates whose folded `canonical_name` equals `name` (tier 2)."""
        return self.snapshot().by_name.get(fold_name(name), ())

    def find_by_variant(self, alias: str) -> tuple[CatalogRow, ...]:
        """Candidates carrying `alias` in `variants[]` (tier 3, near-dead by design)."""
        return self.snapshot().by_variant.get(fold_name(alias), ())

    def find_by_basename(self, name: str) -> tuple[CatalogRow, ...]:
        """Candidates whose folded effective basename equals `name` (tier 4 / join)."""
        return self.snapshot().by_basename.get(fold_name(name), ())

    def family(self, category: str) -> tuple[CatalogRow, ...]:
        """Every candidate in the family of `category`: `1.1c` -> family `1.1`."""
        return self.snapshot().by_family.get(category_family(category), ())


def build_snapshot(records: Sequence[Sequence[object]]) -> CatalogSnapshot:
    """Project raw `items` records into a `CatalogSnapshot`.

    Public and connection-free on purpose: the golden-fixture tests and ticket #9's
    snapshot tooling need to build the same indexes from a committed JSON
    snapshot (F14) without opening `pantry_items.db` at all, which is the whole
    point of never letting CI depend on a sibling checkout.

    Every malformation raises `CatalogError` rather than dropping the row. A
    silently skipped row is a Pantry Item that stops matching, with nothing to
    point at; a raised error names the row.
    """
    candidates: list[CatalogRow] = []
    revision_records: list[tuple[int, str, str, list[str], str | None]] = []
    excluded = 0
    for record in records:
        row_id, name, category, variants_text, area = _unpack(record)
        parsed_variants = _parse_variants(variants_text, row_id)
        revision_records.append((row_id, name, category, list(parsed_variants), area))
        if area is not None:
            excluded += 1
            continue
        candidates.append(
            CatalogRow(
                id=row_id,
                canonical_name=name,
                category=category,
                variants=parsed_variants,
                family=category_family(category),
                basename=effective_basename(name),
            )
        )

    # `id` order is the contract (§9.11.2), enforced here rather than assumed from
    # the `ORDER BY` in `_SELECT_ITEMS`. A differently-ordered feed then hashes
    # and indexes identically, so "the rows changed" and "the order the rows
    # arrived in changed" cannot be confused for one another.
    revision_records.sort(key=lambda entry: entry[0])
    candidates.sort(key=lambda row: row.id)

    by_id: dict[int, CatalogRow] = {}
    named: dict[str, list[CatalogRow]] = {}
    aliased: dict[str, list[CatalogRow]] = {}
    based: dict[str, list[CatalogRow]] = {}
    families: dict[str, list[CatalogRow]] = {}
    # Ascending `id` because the candidates were sorted above; the ordering is
    # what makes "both duplicates surface, lowest id first" a determinate answer
    # instead of an artifact of a dict's insertion order.
    for row in candidates:
        if row.id in by_id:
            raise CatalogError("duplicate_pantry_item_id")
        by_id[row.id] = row
        named.setdefault(fold_name(row.canonical_name), []).append(row)
        based.setdefault(fold_name(row.basename), []).append(row)
        families.setdefault(row.family, []).append(row)
        for alias in row.variants:
            aliased.setdefault(fold_name(alias), []).append(row)

    return CatalogSnapshot(
        rows=tuple(candidates),
        by_id=by_id,
        by_name=_freeze(named),
        by_variant=_freeze(aliased),
        by_basename=_freeze(based),
        by_family=_freeze(families),
        catalog_revision=_revision_digest(revision_records),
        total_row_count=len(records),
        excluded_row_count=excluded,
    )


def _freeze(index: dict[str, list[CatalogRow]]) -> dict[str, tuple[CatalogRow, ...]]:
    """The candidate lists as the immutable tuples the public indexes expose."""
    return {key: tuple(rows) for key, rows in index.items()}


def _unpack(record: Sequence[object]) -> tuple[int, str, str, str, str | None]:
    if len(record) != 5:
        raise CatalogError("unexpected_pantry_catalog_shape")
    raw_id, raw_name, raw_category, raw_variants, raw_area = record
    if isinstance(raw_id, bool) or not isinstance(raw_id, int):
        raise CatalogError("pantry_item_id_not_integer")
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise CatalogError("pantry_item_name_missing")
    if not isinstance(raw_category, str) or not raw_category.strip():
        raise CatalogError("pantry_item_category_missing")
    if not isinstance(raw_variants, str):
        raise CatalogError("pantry_item_variants_not_text")
    if raw_area is not None and not isinstance(raw_area, str):
        raise CatalogError("pantry_item_area_not_text")
    return raw_id, raw_name, raw_category, raw_variants, raw_area


def _parse_variants(raw: str, row_id: int) -> tuple[str, ...]:
    """The `variants[]` aliases of one row, strictly.

    A malformed value is an error rather than an empty tuple: `variants` is
    where `CONTEXT.md` puts a Pantry Item Alias, so reading a broken one as "no
    aliases" turns a producer's bug into this app quietly resolving fewer rows,
    which is the "plausible-looking wrong answer" AGENTS.md #3 rules out. A blank
    entry is refused for the same reason — it is not a name, and indexing it would
    make an empty lookup hit something.
    """
    if raw == _EMPTY_VARIANTS:
        return ()
    try:
        loaded = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise CatalogError("pantry_item_variants_not_json") from exc
    if not isinstance(loaded, list):
        raise CatalogError("pantry_item_variants_not_a_list")
    aliases: list[str] = []
    for entry in loaded:
        if not isinstance(entry, str) or not entry.strip():
            raise CatalogError("pantry_item_variant_not_a_name")
        aliases.append(entry)
    return tuple(aliases)


def _revision_digest(records: Sequence[Sequence[object]]) -> str:
    """`"sha256:" + sha256` over the identity-bearing columns of every row.

    Length-unambiguous by construction: the payload is JSON, not a concatenation,
    so no combination of names and categories can be re-split into a different
    row set with the same bytes. `first_seen`, `last_seen`, `order_count`, and
    `last_price` are excluded, and that is the point — they cannot break a
    mapping, so a re-import that only moves them must not cost a full
    `resolve_all()`. `area` **is** included even though its rows are filtered out,
    because a row entering or leaving the candidate universe changes every index
    this module hands out.
    """
    payload = json.dumps(list(records), ensure_ascii=False, separators=(",", ":"))
    return _REVISION_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()

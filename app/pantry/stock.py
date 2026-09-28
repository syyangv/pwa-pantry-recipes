"""Pantry Stock from the live `Pantry.md`, and the Stock Join that gives it identity.

**F1's two halves, and this file is the seam between them.** `pantry_items.db`
answers *"which product is this?"* — the rename-stable `pantry_item_id` and the
name. `Logistics/库存/Pantry.md` answers *"do I have it right now?"* —
`open` / `in_progress` / `done` / `cancelled`, per Pantry Unit. Neither alone
answers D4's question, and the split is not negotiable: `红苋菜苗` (139) and
`新鲜小叶茼蒿` (70) are catalog rows and **neither is open in the pantry**, so a
catalog-only answer renders both as `chip--in-stock` while they sit off the
shelf. The state half is parsed by the ported `app/vault/pantry.py` — this module
reads it through `PantryIndex` and never re-parses a line, never re-derives a
status, and never re-implements the per-unit money math. It adds exactly two
things: the **join**, and a TTL cache in front of the join.

**The join is deliberately the weakest link in this app, and it is weaker than
the recipe→ingredient join on purpose.** A `Pantry.md` line is free text; a
catalog row is a normalized name. So the join is a normalized-name index built
on `normalize_ingredient()` — *the same* function the matcher uses, which is the
only reason the two sides can agree at all — with, **in this order and no
further**:

1. **exact** — the line's folded product core against `by_name`;
2. **basename** — the same key against the tier-4 `by_basename` index, so
   `空心菜嫩苗 0.95-1.05 磅` reaches id 83;
3. **override** — one entry in the committed `app/pantry/line_overrides.yaml`.

Tiers 1 and 2 are one lookup with two indexes: a key present in **either** takes
the candidates from **both**, and the reported tier is the best one that fired.
So a key that is one row's `canonical_name` and *also* a basename shared with a
re-ingested duplicate — four of the eight measured collisions are exactly that —
reports both rows rather than letting the exact tier short-circuit the sibling.
The stock question is why: a pantry line says "I have 小白菜心" and cannot say
which of two purchase records of the same product it means, so both are held.

**There is no tier ladder beyond those three, and no re-resolution pass.** No
synonym tier, no fuzzy tier, no `contains`, and no second sweep when the
catalog changes — because the recipe→ingredient join persists its result in
`ingredient_mappings` and can be re-resolved against it, and **this one has no
materialized table to re-resolve against** (there is deliberately no stock-join
table and no `in_stock` column: stock is volatile, the user toggles a task in
Obsidian while the PWA is open, and persisting it would make the chip colour
wrong). So a line all three tiers miss is **simply absent from `in_stock_ids`**,
and it surfaces in `unjoined` rather than being silently dropped. The honest
consequence, stated here because the spec states it (§13.14, R9): a recipe
depending on a missed line renders `chip--have-been-buying` while the item is on
the shelf. The mitigations are the `unjoined` bucket, the `stockUnjoinedCount`
on `GET /api/recipes`, `stock.unjoinedLineCount` in `/health`, and the override
file — which is **load-bearing, not a nicety**, because without it the only
remaining fix for a miss is editing the user's vault.

**A duplicate catalog name surfaces both candidates, and both stay in
`in_stock_ids`.** Eight basename keys hold more than one row (`小白菜心`
[18, 106], `organic 1% milk` [90, 116], …). `dict[str, CatalogRow]` would pick a
winner by insertion order and the loser would vanish with no diagnostic, which is
the one failure a name lookup must never have, so `by_name` / `by_basename` hold
tuples and a hit here is a tuple of ids in ascending order. For *stock* the
ambiguity is harmless rather than fatal: a recipe is in stock if the row it
resolved to is in `in_stock_ids`, and both duplicates of a re-ingested product
are held by definition. Picking between them is the matcher's decision with the
`调试` provenance toggle beside it, not this file's.

**The override file is keyed by name, not by id, and that is the whole reason
it works.** `items.id` is `AUTOINCREMENT`: a producer re-import can renumber it,
which is §9.8's "never store a Pantry Item id in a lexicon file" rule verbatim
and the same reason `catalog_revision` exists. So `line_overrides.yaml` is keyed
by the **normalized line name** and valued by a `canonical_name` that is looked
up in the **live** catalog at read time. Renumber `items.id` and the override
follows the product; key it by id and it points at a different product.

**Fails closed, and the blast radius is why.** If `Pantry.md` is missing,
unreadable, or unparseable, `PantryStockIndex` raises `PantryError` — with the
vault-relative path in the message — and every property that would have answered
"what is in stock" raises with it. There is deliberately no fallback in either
direction: "assume in stock" silently inflates every score and is the exact
Pantry Item / Pantry Stock conflation `AGENTS.md` and `CONTEXT.md` forbid, and
"assume not in stock" silently deflates every score to `have-been-buying`, a
plausible-looking wrong answer. The recipe index, the note bodies, and the
cooking history stay readable; only the stock-derived chip colours are
unavailable. Mapping `PantryError` to `503 pantry_stock_unreadable` is the
router's obligation (`GET /api/recipes`, ticket #15); the raise is this file's,
and a failure also leaves the cache **unpopulated** rather than caching an empty
snapshot, so a transient read error cannot pin "nothing held" for a whole TTL.

**The 💵 price comes from the LINE, not from the catalog.** `CatalogRow` does not
project `last_price` and must not: the catalog is a lifetime purchase history
(§9.20), and the effective per-unit price is a property of the pantry line.
`PantryStockIndex.inventory_figure` reuses `untagged_inventory()` — #7's
mutation-tested implementation of the per-unit invariant — rather than
summing prices here. The invariant, restated because it is the single easiest
thing in this app to get wrong: **`💵 $X.XX` on a parent line is the effective
per-unit price**, every consumer excludes the unit parent, and each open `k/N`
subtask is counted at that price. Counting the parent *and* its units
double-counts; counting the parent *instead of* its units halves a half-used
item.

**Which rows the join sees, and the one measured reconciliation.** A listed row
that is a bare `k/N` unit (`1/2`) carries **no product identity** — its parent's
text does — so it is not a join input and cannot become an `unjoined` miss
either; letting it through would report 5 permanent misses and make
`stockUnjoinedCount` useless as a signal. Everything else listed is a product
line, **including a unit parent**, because a half-used multipack *is* held: its
open units are what the money math counts, so dropping the parent would let the
figure count an item the stock answer denies. On the frozen note that is 50
listed rows → 5 unit rows → **45 product lines**, of which 3 resolve on the
exact tier, 40 on the basename tier, 2 through the override, and **0 are
unjoined** with the shipped override file. The spec's "2 failures in 46 open
lines" is the same measurement over a slightly different denominator: 46 is
`PantrySnapshot.total`, which counts the 5 unit rows and excludes the 4 unit
parents, and the 2 named misses are the two this file's shipped overrides cover.
Both numbers are asserted in `tests/pantry/test_stock_join.py`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Final, Literal, Protocol

import yaml
from yaml.nodes import MappingNode

from ..config import ConfigurationError, Settings
from ..recipes.normalize import normalize_ingredient
from ..vault.atomic_write import AtomicNoteStore
from ..vault.pantry import (
    InventoryFigure,
    PantryError,
    PantryIndex,
    PantrySection,
    untagged_inventory,
)
from .catalog import CatalogRow, CatalogSnapshot, fold_name

#: The override file, as package data resolved through `importlib.resources` so
#: it is correct from a source checkout and from an installed wheel alike.
#: `line_overrides.yaml` sits directly in the `app.pantry` package (not in a
#: `lexicon/` subdirectory) because it is a join's escape hatch, not a vocabulary
#: the matcher reads: only `app/pantry/stock.py` consumes it.
LINE_OVERRIDES_SOURCE: Final[Traversable] = files(__package__).joinpath("line_overrides.yaml")

#: The override file's schema revision, mirroring `brand_lexicon.py`: a loader
#: that does not recognise it refuses to start rather than misreading the file.
LINE_OVERRIDES_VERSION: Final = 1
_KNOWN_OVERRIDE_KEYS: Final = frozenset({"version", "overrides"})
#: A bound on the file's size, so a runaway edit cannot turn every join into a
#: large dict scan. 200 is roughly an order of magnitude above the two entries
#: this app ships and far above any plausible miss rate for a 178-row catalog.
_MAX_OVERRIDES: Final = 200
#: A name is not a paragraph. Also what keeps a pasted dataview block or a whole
#: note out of the index.
_MAX_NAME_CHARS: Final = 200

#: The three join tiers, in the order they are tried. Named because the order is
#: the contract, and `tests/pantry/test_stock_join.py` mutates it.
TIER_EXACT: Final = 1
TIER_BASENAME: Final = 2
TIER_OVERRIDE: Final = 3
#: `stockJoinState` in the `调试` provenance payload (F1). `unresolved` is a
#: third state, not an absent one: a pantry line that no catalog row explains has
#: to be visible somewhere other than "not in the list".
StockJoinState = Literal["joined", "override", "unresolved"]

#: A `k/N` unit row: the per-unit split marker #7's parser uses, kept identical
#: so the join's universe is exactly the parser's notion of a unit. This is a
#: *filter* on the join's input, not a second parser and not a second
#: normalizer — `tests/pantry/test_stock_join.py` asserts it agrees with
#: `app.vault.pantry._UNIT` over every row of the frozen note.
_UNIT_ROW = re.compile(r"^\d+\/\d+")


def is_unit_row(text: str) -> bool:
    """True for a bare `k/N` unit row, which carries no product identity."""
    return _UNIT_ROW.match(text) is not None


class CatalogSource(Protocol):
    """What the join needs from the catalog: a snapshot, and nothing else.

    `PantryCatalog` satisfies it. A `Protocol` rather than the concrete class so
    the join can be driven from a committed JSON snapshot
    (`build_snapshot()`) without opening `pantry_items.db` at all — F14's
    discipline, applied to the stock side as well.
    """

    def snapshot(self) -> CatalogSnapshot: ...


class _UniqueKeyLoader(yaml.SafeLoader):
    """A `SafeLoader` that refuses a duplicate mapping key.

    `yaml.safe_load` keeps the **last** of two identical keys and says nothing,
    so a copied-and-pasted override line would silently delete the entry above
    it — a repair file that quietly stops repairing, with the `unjoined` counter
    as the only symptom. A duplicate is a typo, and a typo should be loud.
    """

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[object, object]:
        seen: set[object] = set()
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in seen:
                    raise yaml.YAMLError(f"duplicate key: {key!r}")
                seen.add(key)
            except TypeError:  # an unhashable key; the shape check names it
                continue
        return super().construct_mapping(node, deep=deep)


def load_line_overrides(source: Traversable | None = None) -> dict[str, str]:
    """Return the committed override map, or raise `ConfigurationError`.

    The file is reviewed source and the app's **only** repair path for a join
    miss, so it fails closed on every malformation rather than degrading to
    "no overrides" — which would look exactly like "nothing to repair" and would
    show up only as a rising `unjoined` count. Refused: a missing file,
    unparseable YAML, a wrong top-level shape, an unrecognised key or `version`,
    a missing `overrides` mapping, a key that is not already NFKC + trimmed +
    casefolded, a blank / padded / control-character name, and a duplicate key.

    An **empty** `overrides` mapping is legal. A fresh checkout has nothing to
    repair, tiers 1 and 2 are unaffected by this file, and the `unjoined`
    counter is the honest report. What is *not* legal is a key that is not in
    normalized form: pasting a raw `Pantry.md` line into a key would never
    match, and an override that can never fire is worse than no override
    because it reads as a repair that is in place. The product-core half of that
    check — `key == fold_name(normalize_ingredient(key))`, which proves the key
    is a product core and not merely a folded name — is asserted on the shipped
    file by `tests/pantry/test_stock_join.py` rather than here, because
    `normalize.py` is expected to evolve and a lexicon change must not be able
    to stop the app from starting.

    `source` exists for the tests that prove the failure modes are loud. It is
    not an application-level injection hook and no request can reach it.
    """
    resolved = LINE_OVERRIDES_SOURCE if source is None else source
    if not resolved.is_file():
        raise ConfigurationError("missing_pantry_line_overrides")
    try:
        document = yaml.load(resolved.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ConfigurationError("unreadable_pantry_line_overrides") from exc
    if not isinstance(document, dict):
        raise ConfigurationError("invalid_pantry_line_overrides_document")
    unknown = sorted(str(key) for key in set(document) - _KNOWN_OVERRIDE_KEYS)
    if unknown:
        raise ConfigurationError(f"unknown_pantry_line_overrides_keys:{','.join(unknown)}")
    if "version" not in document:
        raise ConfigurationError("missing_pantry_line_overrides_version")
    if document["version"] != LINE_OVERRIDES_VERSION:
        raise ConfigurationError("unsupported_pantry_line_overrides_version")
    entries = document.get("overrides")
    if not isinstance(entries, dict):
        raise ConfigurationError("invalid_pantry_line_overrides_section")
    if len(entries) > _MAX_OVERRIDES:
        raise ConfigurationError("too_many_pantry_line_overrides")
    overrides: dict[str, str] = {}
    for key, value in entries.items():
        if not isinstance(key, str) or not _is_name(key):
            raise ConfigurationError(f"pantry_line_override_key_not_a_name:{key!r}")
        if fold_name(key) != key:
            raise ConfigurationError(f"pantry_line_override_key_not_normalized:{key!r}")
        if not isinstance(value, str) or not _is_name(value):
            raise ConfigurationError(f"pantry_line_override_value_not_a_name:{key!r}")
        overrides[key] = value
    return overrides


def _is_name(value: str) -> bool:
    """A non-blank, unpadded, control-character-free name of sane length."""
    return (
        0 < len(value) <= _MAX_NAME_CHARS
        and value == value.strip()
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


#: The committed overrides, read once at import for the same reason
#: `BRAND_LEXICON` is: the join is on the request path, the file cannot change
#: under a running process, and a malformed file must stop the boot rather than
#: silently un-repair the join. Injectable per instance, for tests only.
LINE_OVERRIDES: Final[Mapping[str, str]] = load_line_overrides()


@dataclass(frozen=True)
class StockJoinMiss:
    """One open pantry line that no tier resolved.

    `text` is the line's cleaned display text — the string the join consumed,
    with money, emoji dates, and `#tags` already lifted out into structured
    fields by the ported parser — and `core` is its `normalize_ingredient()`
    product core, which is the string the override file is keyed by. Both are
    carried so a repair is one line: paste `core` into `line_overrides.yaml` and
    the miss is fixed without touching the user's vault.

    `override` is set only when the override tier **fired and did not resolve**:
    the line has an entry, and the entry's `canonical_name` is not in the live
    catalog. That is a typo or a stale entry in a reviewed file, and it is worth
    distinguishing from "nothing tried", because nothing else will report it.
    """

    text: str
    core: str
    line_index: int
    section: str | None = None
    override: str | None = None


@dataclass(frozen=True)
class StockJoinHit:
    """One product line and what the join made of it.

    `item_ids` is a tuple, not a single id: a key two catalog rows reduce to
    resolves to **every** candidate, in ascending id order, and all of them land
    in `in_stock_ids` — on either catalog tier. `tier` is the best tier that
    fired (1 exact, 2 basename, 3 override) and `state` is the `stockJoinState`
    the provenance payload reports.
    """

    text: str
    core: str
    key: str
    state: StockJoinState
    tier: int
    item_ids: tuple[int, ...]
    line_index: int
    section: str | None = None


@dataclass(frozen=True)
class StockJoin:
    """One materialization of the join: the stock answer, and its own evidence.

    `in_stock_names` is the *unjoined* name set — every product line's folded
    product core, whether or not it reached the catalog — because it answers
    "what is written on the shelf". `in_stock_ids` is the joined view, and that
    is the one the chip classifier consumes: a name nobody could resolve must not
    be able to make a recipe look in stock.

    The tier counters are on the value rather than recomputed by a caller,
    because "how much of the join is each tier carrying" is the number that says
    whether the join is healthy. On the frozen note it is 3 / 40 / 2 / 0.
    """

    in_stock_names: frozenset[str]
    in_stock_ids: frozenset[int]
    hits: tuple[StockJoinHit, ...]
    unjoined: tuple[StockJoinMiss, ...]

    @property
    def line_count(self) -> int:
        """Product lines the join saw — the denominator of the miss rate.

        Hits **and** misses: a line nothing could resolve was still a line the
        join looked at, and a miss rate computed over resolved lines only would
        read 0% however badly the join was doing.
        """
        return len(self.hits) + len(self.unjoined)

    @property
    def unjoined_count(self) -> int:
        """`stock.unjoinedLineCount` / `stockUnjoinedCount`."""
        return len(self.unjoined)

    def tier_count(self, tier: int) -> int:
        """How many lines this tier resolved."""
        return sum(1 for hit in self.hits if hit.tier == tier)

    @property
    def exact_tier_count(self) -> int:
        return self.tier_count(TIER_EXACT)

    @property
    def basename_tier_count(self) -> int:
        return self.tier_count(TIER_BASENAME)

    @property
    def override_tier_count(self) -> int:
        return self.tier_count(TIER_OVERRIDE)


def join_stock(
    catalog: CatalogSnapshot,
    sections: Sequence[PantrySection],
    overrides: Mapping[str, str] | None = None,
) -> StockJoin:
    """Join every open pantry product line to a live catalog row, or record the miss.

    Pure: a `CatalogSnapshot` and parsed sections in, a `StockJoin` out. No file,
    no clock, no environment — so the whole join is testable against a committed
    catalog snapshot and a committed note, which is what F14 requires and what
    makes the three tiers individually observable.

    The tiers are ordered and the order is a contract rather than a preference:
    the override is consulted **only** for a key neither catalog index holds, so
    a typo in a repair file can never pre-empt a product the catalog already
    explains. Tier 3's `canonical_name` is resolved against the **catalog passed
    in**, which is the live one, at this call — that is what makes the override
    survive a renumbered `items.id`.
    """
    table = LINE_OVERRIDES if overrides is None else overrides
    hits: list[StockJoinHit] = []
    misses: list[StockJoinMiss] = []
    names: set[str] = set()
    ids: set[int] = set()

    for section in sections:
        for item in section.items:
            if is_unit_row(item.text):
                # A `k/N` unit carries no product identity; its parent's text is
                # the product, and the parent is a product line in its own right
                # because the open units are what the money math counts.
                continue
            core = normalize_ingredient(item.text)
            key = fold_name(core)
            names.add(key)
            candidates, tier, state, override_name = _resolve(catalog, key, table)
            if tier == 0:
                misses.append(
                    StockJoinMiss(
                        text=item.text,
                        core=core,
                        line_index=item.line_index,
                        section=section.number,
                        override=override_name,
                    )
                )
                continue
            ids.update(row.id for row in candidates)
            hits.append(
                StockJoinHit(
                    text=item.text,
                    core=core,
                    key=key,
                    state=state,
                    tier=tier,
                    item_ids=tuple(row.id for row in candidates),
                    line_index=item.line_index,
                    section=section.number,
                )
            )

    return StockJoin(
        in_stock_names=frozenset(names),
        in_stock_ids=frozenset(ids),
        hits=tuple(hits),
        unjoined=tuple(misses),
    )


def _resolve(
    catalog: CatalogSnapshot, key: str, overrides: Mapping[str, str]
) -> tuple[tuple[CatalogRow, ...], int, StockJoinState, str | None]:
    """The three tiers, in order. `(candidates, tier, state, override_name)`.

    Tier 0 is the miss, and it is a value rather than a sentinel exception so a
    caller cannot forget to handle it. The fourth element is the override's
    `canonical_name` when tier 3 fired but resolved to nothing, which is the one
    failure the `unjoined` bucket cannot otherwise explain.

    **A key present in either catalog index takes the candidates from both.**
    `by_name` and `by_basename` are unioned for the same key, and the reported
    tier is the *best* one that fired — so a key that is one row's
    `canonical_name` and *also* a basename shared with a re-ingested duplicate
    (four of the eight measured collisions are exactly that) reports both rows
    rather than letting tier 1 short-circuit the sibling. The reason is the
    stock question, not the matcher question: a pantry line says "I have
    小白菜心", and it cannot say which of two purchase records of the same
    product it means, so **both** are held. Choosing between duplicates is the
    ingredient matcher's decision, with the `调试` provenance toggle and the
    food-class guards beside it; this join only has to refuse to hide one.

    The tiers stay distinct and ordered: the override fires **only** when the
    key is in neither catalog index, so it can never pre-empt a real catalog row.
    """
    exact = catalog.by_name.get(key)
    basenamed = catalog.by_basename.get(key)
    if exact or basenamed:
        merged: dict[int, CatalogRow] = {}
        for row in (*(exact or ()), *(basenamed or ())):
            merged.setdefault(row.id, row)
        candidates = tuple(merged[row_id] for row_id in sorted(merged))
        return candidates, TIER_EXACT if exact else TIER_BASENAME, "joined", None
    canonical_name = overrides.get(key)
    if canonical_name is None:
        return (), 0, "unresolved", None
    folded = fold_name(canonical_name)
    # The override names a *catalog* product, so it is looked up with the same
    # indexes the tiers above use: an operator may write either the row's own
    # `canonical_name` or the product core it reduces to, and both are resolved
    # against the live catalog here rather than being trusted as an answer.
    resolved = catalog.by_name.get(folded) or catalog.by_basename.get(folded)
    if not resolved:
        return (), 0, "unresolved", canonical_name
    return resolved, TIER_OVERRIDE, "override", None


class PantryStockIndex(PantryIndex):
    """The TTL-cached, fail-closed stock answer: state, joined to identity.

    The ported `PantryIndex`, with the catalog added and the join in front of
    every answer. Inheriting rather than re-implementing is deliberate: the TTL
    cache, the `PathSafetyError` → `PantryError` mapping, the `None` →
    `PantryError` mapping, and above all *"a failed refresh leaves the cache
    unpopulated"* are the fail-closed contract, and they are inherited verbatim
    instead of being written a second time where they could drift.

    **The TTL is `stock_cache_seconds` (F11, 30 s) and the number is
    load-bearing.** It is the **maximum staleness a chip colour can have**,
    because the user toggles a task in `Pantry.md` from Obsidian while this PWA
    is open: 30 s is how long a chip may show `in-stock` for something just
    finished. The catalog's 300 s TTL is not comparable — a catalog re-import
    happens on the producer's schedule and changes only *identity*, never
    whether something is held — and the recipe index's 60 s changes neither. So
    stock gets the shortest of the three, and it is the only one whose staleness
    a user can see on a screen they are looking at.

    **The join is recomputed on every pantry refresh and cached with it.** The
    catalog has its own, longer TTL inside `PantryCatalog`, so an identity
    change can take up to `catalog_cache_seconds` to reach the join *unless* the
    pantry refresh lands first; `invalidate()` drops both. That is deliberate:
    the join is derived state, recomputed rather than persisted, and there is no
    table to keep consistent with.

    **`close()` does not close the catalog.** The catalog connection belongs to
    the lifespan, which opens and closes both; closing a collaborator this object
    does not own would make the shutdown order significant for no reason.
    """

    def __init__(
        self,
        store: AtomicNoteStore,
        relative: str,
        cache_seconds: float,
        catalog: CatalogSource,
        *,
        max_bytes: int = 2_000_000,
        overrides: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(store, relative, cache_seconds, max_bytes=max_bytes)
        self._catalog = catalog
        self._overrides = LINE_OVERRIDES if overrides is None else overrides
        self._join: StockJoin | None = None

    @classmethod
    def from_settings_with_catalog(
        cls,
        settings: Settings,
        store: AtomicNoteStore,
        catalog: CatalogSource,
        *,
        max_bytes: int = 2_000_000,
    ) -> PantryStockIndex:
        """Build from the fail-closed `Settings` surface, with the catalog.

        `os.environ` is never read here: the note path and the TTL are both
        server-owned, and an index assembled from the environment directly would
        be a second, unvalidated copy of `Settings`. The TTL is
        `settings.stock_cache_seconds` for the reason in the class docstring.

        The name is not `from_settings` because this class **inherits** that
        factory and the catalog is a required collaborator: overriding it with an
        extra required argument is a signature incompatibility, and *not*
        overriding it would leave a `PantryStockIndex` constructible with no
        catalog at all — a `from_settings` that "works" and then raises on
        every stock property is worse than a name nobody likes.
        """
        return cls(
            store,
            settings.pantry_note_relative,
            settings.stock_cache_seconds,
            catalog,
            max_bytes=max_bytes,
        )

    # --- the fail-closed refresh -------------------------------------------

    def _refresh(self) -> None:
        """Refresh the note, then drop the join. The raise is **not** caught.

        `PantryIndex._refresh` already raises `PantryError` for a missing,
        unreadable, or unparseable note and leaves `_cached` unpopulated when it
        does. The only thing added here is the vault-relative **path** in the
        message: `pantry_source_missing` on its own tells a user nothing they
        can act on, and F1's failure is a whole-layer one, so the message has to
        say which file. The path is vault-relative and never absolute, so the
        error envelope leaks no filesystem layout (§9.19).

        The join is dropped *after* the refresh, not before: a failed refresh
        must leave the previous state alone rather than half-clearing it, and
        because the raise propagates, nothing is served either way. Nothing here
        substitutes an empty snapshot, a default name set, or an empty id set —
        every stock property calls this, so every one of them raises.
        """
        try:
            super()._refresh()
        except PantryError as exc:
            raise PantryError(f"{exc.args[0]}: {self._relative}") from exc
        self._join = None

    def invalidate(self) -> None:
        """Drop the cached snapshot *and* the join, so the next read re-derives both."""
        super().invalidate()
        self._join = None

    # --- the answers -------------------------------------------------------

    @property
    def join(self) -> StockJoin:
        """The joined view of the current snapshot, recomputed once per refresh."""
        self._refresh()
        join = self._join
        if join is None:
            join = join_stock(self._catalog.snapshot(), self.snapshot().sections, self._overrides)
            self._join = join
        return join

    @property
    def in_stock_names(self) -> frozenset[str]:
        """Folded product core of every open product line, joined or not."""
        return self.join.in_stock_names

    @property
    def in_stock_ids(self) -> frozenset[int]:
        """**The** in-stock signal: every catalog id an open pantry line reached.

        This, not `in_stock_names`, is what the chip classifier consumes. A name
        that no catalog row explains is absent here on purpose: inventing a
        mapping would be the confident-wrong-answer class `AGENTS.md` #3 rules
        out, and the miss is reported in `unjoined` instead.
        """
        return self.join.in_stock_ids

    @property
    def unjoined(self) -> tuple[StockJoinMiss, ...]:
        """Open lines no tier resolved — the `调试` toggle's own bucket."""
        return self.join.unjoined

    @property
    def sections(self) -> tuple[PantrySection, ...]:
        """The parsed sections, for the `/health` counters and a future Pantry view."""
        return self.snapshot().sections

    @property
    def stock_revision(self) -> str:
        """`stockRevision` in the API payload: `"sha256:" + sha256(note bytes)`."""
        return self.snapshot().note_revision

    @property
    def open_count(self) -> int:
        """`stock.openCount` — the parser's open-row count, `PantrySnapshot.total`.

        It counts a `k/N` unit as its own row, which is the figure the note's own
        库存总览 dataviewjs prints (`📋 46 项 · 💵 $198.44`). The join's denominator
        is `join.line_count` (45 on the same note) because the 5 unit rows are not
        product lines. Both are reported rather than reconciled by fiat.
        """
        return self.snapshot().total

    @property
    def unjoined_line_count(self) -> int:
        """`stock.unjoinedLineCount` — how much of the join is unexplained."""
        return self.join.unjoined_count

    @property
    def inventory_figure(self) -> InventoryFigure:
        """The per-unit money figure, from #7's `untagged_inventory()`.

        Not re-derived here. The `💵` math exists in three places and this app
        owns one of them; a fourth would be a fourth thing to keep in parity, and
        `F1`'s whole point is that this one is the tested one
        (`tests/pantry/test_stock_math.py` freezes the number, and
        `scripts/snapshot_pantry_catalog.py --job stock` refuses to re-freeze it
        while this and `Helper/scripts/pantry_snapshot.py` disagree).
        """
        return untagged_inventory(self.snapshot())

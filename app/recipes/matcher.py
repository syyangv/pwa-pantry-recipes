"""The eight-tier match ladder: one `材料` / `调料` value to at most one Pantry Item.

**First hit wins, and the order is the contract.** Tiers 1-7 are tried in
sequence and the first one that produces an accepted result is the answer; a
weaker tier can never pre-empt a stronger one. That is why each tier is its own
function with its own candidate builder, and why
`tests/recipes/test_matcher.py` mutates the order to watch a test fail rather
than trusting the loop.

**D1's measured ceiling, recorded here so it is not rediscovered as a bug.**
Measured against the committed 178-row catalog snapshot and the 26 distinct
`材料` values of the 16 real recipe notes:

* **0 of 26** match a `canonical_name` exactly, and **0 of 26** match a
  `variants` alias. Tier 3 is near-dead by construction and that is the
  expected outcome, not a regression: only 3 of 178 rows carry a non-empty
  `variants` and two of those are just the long product name. The tier stays
  because `CONTEXT.md` defines Pantry Item Alias in terms of `variants` and a
  future catalog re-import could populate it.
* A naive Levenshtein-≤2 join over the raw strings has a **~27% false-positive
  rate**. That number is the reason the two guards exist, and the reason the
  ladder below is shaped to refuse rather than to guess.
* A large share of distinct values has no match at all. That is the *accepted*
  answer for D1, measured against the live vault and locked by the user: the
  headline is frequently "you cannot make this", and a wrong fuzzy match is
  permanent once it has been persisted. A miss renders a chip. A false positive
  renders a chip claiming you have something you do not.

**This implementation's own measurement of that ceiling**, so a future reader can
tell "known-bad matching" from "an undiscovered bug" without re-deriving it. Over
the 25 distinct parsed `材料` names (the 26 distinct raw values, of which `🍄/香菇`
and `香菇` are one food) of the 16 real notes against the committed 176-row
candidate universe:

| Tier | Distinct values resolved | Which |
|---|---|---|
| 4 `note_basename` | 1 | `Kale` → 56 |
| 5 `normalized_exact` | 1 | `Mackerel` → 22 |
| 6 `synonym` | 5 | `空心菜`→83, `红苋菜`→139, `茼蒿`→70, `西兰花`→14, `鸡翅`→166 |
| 8 `unresolved` | 18 | — |

So **7 of 25 names resolve, 18 of 25 (72%) do not**, and **5 of 16 recipes are
fully cookable** — beating D1's recorded 3 of 16. The 72% miss rate is *worse* than
D1's quoted 58%, and honestly so: that figure is not reproducible from any join
over this corpus (tiers 2, 3 and 5-equality together resolve **0** of 26, so a
58% miss rate cannot come from them), and this ladder is strictly more tiers than
the three D1's number was measured over. The number to compare against is the
per-recipe one, and the one that is a *false positive* rate, which is the thing
the guards exist to hold at zero.

`调料` coverage is **17 of 19 distinct values (89.5%)**, against §9.9's claimed
"~90% with `staples.yaml`, ~5% without". The two misses are `芝麻` (seeds, not a
bottle of oil — and the row a substring rule would wrongly claim) and `青柠` (no
lime exists in the catalog, and letting it reach `柠檬` is the substitution §9.8
forbids).

**A false positive is worse than a miss, so both guards are filters over one
shared candidate pool and both are separately switchable.** Each has its own
`MatchGuards` flag, because a guard nobody can delete is a guard nobody has
tested:

1. **Segment boundary** (§9.7 rule 1). A name may only match where it is a
   WHOLE segment of the row's product core: a whitespace-delimited run compared
   whole, with punctuation such as `!` explicitly *not* a delimiter, so a Latin
   token gets the same `\b` treatment for free. A token may never match as a
   fragment of a longer word. This stops `蒜` from reaching `柴米 蒜香蒸茄子`
   (72) or `乐事 …薯片蒜蓉面包味` (110), `土豆` from reaching `好丽友 呀!土豆
   薯条` (62), `芝麻` from reaching `芝麻烧饼` (102), `葱` from reaching
   `…葱香馅 冷冻` (160) and `开心果` from reaching `…开心果抹茶脆曲奇饼干` (29).
   It is the guard that stops **every** live collision in the committed corpus.
2. **Category family** (§9.7 rule 2), on the synonym tier. Every candidate must
   be in a Pantry Category family named by the entry's own `families`
   allowlist, **and** the survivors must share ONE family. Two candidates from
   different families are rejected. This refuses `好丽友 高笑美芝麻饼干` (25,
   family `4`) and (110) on category alone, with no tokenization involved, which
   is why the rule is a per-candidate allowlist and not merely a homogeneity
   check.

On the committed corpus the segment guard alone stops all the live collisions,
so the family guard is *redundant with it* for exactly those rows. That is not a
reason to drop it — a guard is redundant only against the rows someone thought
to check — and it is why the negative control for the family guard
(`tests/recipes/test_must_not_match.py`) is built on a catalog where the two
rules disagree, which is the only place the question "is this guard doing
anything?" has an answer that is not an artifact of which six rows the spec
happened to name.

**Confidence is a tier + method + evidence tuple, not a float.** `confidence` is
§9.8's per-tier number and nothing more. The tuple is `match_tier` (which rung),
`match_method` (the stable string the API and the `调试` provenance view explain
themselves with) and `candidates` (every row the ladder looked at, with its
name, id, family and the guards that refused it). A float could not say *why*,
and "why" is the whole product question D4 asks.

**Tier 8 is the "nothing above me accepted it" bucket and holds two methods.**
`fuzzy_normalized` is §9.7's final tolerance and is deliberately *not* a numbered
rung: Levenshtein ≤ 1 on the normalized strings only, both ≥ 6 characters, both
single-script, the best candidate unique, and its family among the ones this
name's own evidence already allows. It runs after every real tier has declined,
so it cannot pre-empt one. `unresolved` is the miss.
**`pantry_item_id is None` is the definitive test for a miss**; both report
`match_tier == 8` and `match_method` is what tells them apart.

**Never a Pantry Item id.** No lexicon file carries one and no tier stores one
from a file; `items.id` is `AUTOINCREMENT` and a producer re-import renumbers it.
Tier 1's id is read out of the *value the user wrote*, checked against the live
`by_id` at this call, so a stale id is a miss rather than a pointer at a
different product. Ids are persisted in exactly one place, `ingredient_mappings`,
which is a later ticket's.

**Pure.** Frozen dataclasses and a `CatalogSnapshot` in, a dataclass out. No
file, no clock, no environment, no database. `stock` is accepted so a result can
report `in_stock` for the chip, and is never consulted to choose *which* row
matched: Pantry Stock is volatile and Pantry Item identity is not, and letting
"do I have it right now" pick between two purchase records of one product would
be a second join hiding inside the first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Final, Literal, Protocol

from ..pantry.catalog import CatalogRow, CatalogSnapshot, fold_name
from .ingredients import ParsedIngredient, ParseMethod
from .match_lexicon import (
    STAPLES_SOURCE,
    SYNONYMS_SOURCE,
    StaplesLexicon,
    SynonymEntry,
    load_staples,
    load_synonyms,
    synonym_index,
)
from .normalize import normalize_ingredient

#: The two closed lexicons, read once at import. Bound here rather than in
#: `match_lexicon.py` for the reason `normalize.py` binds `BRAND_LEXICON`: the
#: module that reads them is the module that refuses to start without them, and
#: the per-Ingredient work stays pure.
SYNONYMS: Final[tuple[SynonymEntry, ...]] = load_synonyms()
STAPLES: Final[StaplesLexicon] = load_staples()
#: One folded-name lookup per Ingredient, built at import. A linear scan over 29
#: entries times every Ingredient of every recipe is the kind of cost that makes
#: people weaken a guard to make it fast.
SYNONYM_INDEX: Final[dict[str, SynonymEntry]] = synonym_index(SYNONYMS)

#: The frontmatter key a value came from. Bound vocabulary, never flattened:
#: `CONTEXT.md` keeps `材料` and `调料` apart and §9.9 makes the staples tier
#: depend on which one it is, so the key is part of the matcher's input rather
#: than something the display layer remembers.
IngredientSource = Literal["材料", "调料"]

MatchMethod = Literal[
    "unparseable",
    "exact_id",
    "exact_name",
    "variant_alias",
    "note_basename",
    "normalized_exact",
    "synonym",
    "staples",
    "fuzzy_normalized",
    "unresolved",
]

#: How the recipe-side name reached a row. Stable strings — the provenance view
#: renders them, so renaming one is an API change.
Relation = Literal[
    "exact_id",
    "exact_name",
    "variant_alias",
    "basename_prefix",
    "normalized_exact",
    "token_contains",
    "synonym_exact",
    "synonym_token",
    "fuzzy_normalized",
]

#: Why a candidate was not adopted. Stable strings, for the same reason.
RejectionReason = Literal[
    "segment_boundary",
    "category_family",
    "mixed_families",
    "fuzzy_ambiguous",
    "fuzzy_family_not_allowed",
]

#: §9.8's table, in full. The tier number is a stable identifier and is never
#: renumbered; the method is the string the API speaks; the confidence is that
#: row's number and is never interpolated.
TIER_METHOD: Final[dict[int, MatchMethod]] = {
    1: "exact_id",
    2: "exact_name",
    3: "variant_alias",
    4: "note_basename",
    5: "normalized_exact",
    6: "synonym",
    7: "staples",
}
TIER_CONFIDENCE: Final[dict[int, float]] = {
    1: 1.0,
    2: 1.0,
    3: 0.95,
    4: 0.9,
    5: 0.85,
    6: 0.7,
    7: 0.6,
}

#: §9.8 tier 5 demands a normalized form of at least this many characters. One
#: character is not a name, and two is the shortest real one — `米` is a
#: Seasoning and reaches tier 7, where a class assumption is the honest answer
#: rather than a match against a two-character catalog key.
MIN_NORMALIZED_CHARS: Final = 2
#: The Pantry Category families the fuzzy tolerance is allowed to adopt from —
#: the "in an allowed family" half of §9.7's rule, spelled out as a closed set
#: because the fuzzy tier has no other evidence to reason from: a **misspelled**
#: name is by definition not a prefix of, a token of, or an entry for anything, so
#: there is no food-class evidence to derive an allowlist from. It is data for
#: the same reason a synonym entry's `families` is data.
#:
#: Deliberately **excluded**, and each exclusion is a specific false positive:
#:
#: * `4` (snack) — `好丽友 高笑美芝麻饼干` (25) is the row every flavour-descriptor
#:   guard in this ladder exists to refuse, and a fuzzy tolerance with no class
#:   check would be the back door to it.
#: * `5` (drinks) — no Ingredient in the 26 real `材料` values is a beverage, and
#:   a drink row is the most plausible near-miss for a short seasoning-shaped
#:   name. Excluding a class that cannot help costs nothing and removes a class
#:   of wrong answer.
#: * `6` (non-food) and `skip` — `area` filtering already removes most of these,
#:   but `skip` is a real Pantry Category whose rows are still in the candidate
#:   universe, and `日本KAO花王 … 卫生巾短裤` (24) is family `6`.
#:
#: The families are the **leading component** of the Pantry Category code, so
#: `1.1`, `1.1c`, `1.1d` and `1.1f` are all family `1.1` and one entry covers all
#: four. These are Pantry Category CODES and are never rendered as labels —
#: `CONTEXT.md` defines no code-to-display-text mapping.
FUZZY_FAMILIES: Final[frozenset[str]] = frozenset({"1.1", "1.2", "2", "3"})
#: §9.7 fuzzy precondition: both normalized strings must be at least this long.
#: Six is where one edit is evidence rather than coincidence, and it puts `蒜`
#: vs `柴米 蒜香蒸茄子` — many edits apart — structurally out of reach rather than
#: merely far away.
MIN_FUZZY_CHARS: Final = 6
#: The most near-misses surfaced for one-tap binding. Three is deliberate: enough
#: to recognise a row you meant, few enough to fit a phone sheet without
#: scrolling.
MAX_NEAR_MISSES: Final = 3
#: How far a same-script row may be and still be offered. 3 rather than the fuzzy
#: tolerance's 1 on purpose: a near miss is a suggestion a human rejects, and a
#: suggestion list of only exact hits is not a suggestion list. Still tight
#: enough that `包菜` is not offered `必品阁 紫菜汤`.
NEAR_MISS_MAX_DISTANCE: Final = 3
#: The whole-name-segment delimiter set. ASCII whitespace and NBSP only, which is
#: what makes the guard reject `土豆` inside `好丽友 呀!土豆 薯条 里脊牛排味` (62):
#: `!` splits nothing, so the segment is `呀!土豆` and `土豆` is a fragment of it
#: rather than the whole of it. CJK punctuation is a delimiter nowhere in this
#: module, on purpose.
_DELIMITERS: Final = re.compile(r"[ \t\n\r\f\v\u00a0]+")
#: CJK ideographs, the blocks NFKC never rewrites. CJK *punctuation* is
#: deliberately excluded: `，` inside a product name is a separator the
#: normalizer already handled, not part of a word.
_IDEOGRAPH: Final = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3005]")
_LATIN_LETTER: Final = re.compile(r"[a-z]", re.IGNORECASE)
#: A Pantry Item id written into a value: `[[id:83]]`, `[[83]]`, `id:83`, `83`.
#: This is tier 1's entire reason to exist — `CONTEXT.md` defines a Pantry Item
#: Alias as a *name*, so a user who has learned one id writes it down, and a
#: renumbered catalog must not turn that override into a different product.
_WRITTEN_ID: Final = re.compile(r"\A(?:\[\[)?(?:id:)?(?P<number>[0-9]+)(?:\]\])?\Z")

#: `parse_ingredient_value` recovered no name at all. A hard stop, never a silent
#: miss: the value was written in a shape the parser does not recognise, which
#: is a different problem from a name no row happens to carry.
UNPARSEABLE_TIER: Final = 0
UNPARSEABLE_METHOD: Final[MatchMethod] = "unparseable"
#: Tier 8 is the "nothing above me accepted it" bucket and holds both the fuzzy
#: tolerance's acceptance and the miss. They share the number because the
#: tolerance is not a rung; `pantry_item_id is None` is the miss test.
FUZZY_TIER: Final = 8
FUZZY_METHOD: Final[MatchMethod] = "fuzzy_normalized"
#: Below `staples` on purpose. The tolerance is the last thing tried, so its
#: number only has to be lower than everything above it.
FUZZY_CONFIDENCE: Final = 0.55
UNRESOLVED_TIER: Final = 8
UNRESOLVED_METHOD: Final[MatchMethod] = "unresolved"


class StockSignal(Protocol):
    """The one thing the ladder needs from Pantry Stock: which ids are held.

    A `Protocol` rather than `PantryStockIndex` so the matcher can be driven
    from the committed catalog snapshot with no collaborator at all, and so the
    signal cannot grow into a second join. `in_stock` on a result is a *display*
    fact (`chip--in-stock`); the ladder never consults it to choose a row.
    """

    @property
    def in_stock_ids(self) -> frozenset[int]: ...


@dataclass(frozen=True)
class MatchGuards:
    """The two flavour-descriptor guards, each separately deletable.

    Both default on. A caller wanting a weaker matcher has to say so explicitly,
    which is the whole reason this is a parameter and not two private helpers:
    `tests/recipes/test_must_not_match.py` needs the deletion to be a one-line
    change it can then assert the consequences of.
    """

    segment_boundary: bool = True
    category_family: bool = True


#: The default, as a module-level singleton so `resolve_ingredient`'s default
#: argument is a name rather than a call. `MatchGuards` is frozen and holds no
#: state, so one shared instance is exactly as safe as a fresh one per call.
DEFAULT_GUARDS: Final[MatchGuards] = MatchGuards()


@dataclass(frozen=True)
class MatchCandidate:
    """One catalog row the ladder looked at, and what it decided about it.

    `rejected_by` holds **every** guard that refused the row, in tier order,
    and more than one entry is the normal case and is the point: `高笑美芝麻饼干`
    (25) is refused by the segment rule *and* by the family rule, and an audit
    trail that recorded only the first would understate what is holding it back.
    """

    pantry_item_id: int
    canonical_name: str
    pantry_category: str
    family: str
    relation: Relation
    rejected_by: tuple[RejectionReason, ...] = ()


@dataclass(frozen=True)
class NearMiss:
    """One row offered for one-tap binding on a value nothing matched.

    Near misses are **not** filtered by the guards, and that is deliberate. The
    rows a user most needs to see and reject are exactly the flavour-descriptor
    rows the guards refuse: `芝麻` resolving to nothing is only repairable if the
    provenance view can offer `芝麻烧饼` (102) and let the user say no. A near
    miss never resolves anything — the result's `pantry_item_id` stays `None`
    however good it looks — so offering it costs no false positive.
    """

    pantry_item_id: int
    canonical_name: str
    pantry_category: str
    family: str
    distance: int
    relation: str


@dataclass(frozen=True)
class MatchResult:
    """One Ingredient's resolution, and the evidence behind it.

    `pantry_item_id is None` is the only definitive statement that nothing
    matched. Tier 0 with `unparseable` is a value this parser cannot read; tier
    8 with `fuzzy_normalized` is a resolution; tier 8 with `unresolved` is a
    miss.
    """

    raw_value: str
    parsed_name: str | None
    parse_method: ParseMethod
    source: IngredientSource
    match_method: MatchMethod
    match_tier: int
    pantry_item_id: int | None
    confidence: float
    candidates: tuple[MatchCandidate, ...] = ()
    near_misses: tuple[NearMiss, ...] = ()
    in_stock: bool = False
    misfiled_staple: bool = False

    @property
    def resolved(self) -> bool:
        """Whether a Pantry Item was adopted. The chip colour reads this."""
        return self.pantry_item_id is not None

    def candidate_ids(self) -> tuple[int, ...]:
        """Every id the ladder looked at, ascending. The provenance view's list."""
        return tuple(sorted(candidate.pantry_item_id for candidate in self.candidates))

    def rejected_by(self, pantry_item_id: int) -> tuple[RejectionReason, ...]:
        """Every guard that refused one row, in tier order. Empty when adopted."""
        for candidate in self.candidates:
            if candidate.pantry_item_id == pantry_item_id:
                return candidate.rejected_by
        return ()

    def candidate(self, pantry_item_id: int) -> MatchCandidate | None:
        """The audit record for one row, or `None` when the ladder never saw it."""
        for candidate in self.candidates:
            if candidate.pantry_item_id == pantry_item_id:
                return candidate
        return None


@dataclass
class _Audit:
    """Mutable per-call accumulator. Never escapes the function that owns it.

    A row the ladder meets twice — `芝麻` reaches 102 on tier 5's `contains` and
    again on tier 6's synonym expansion — is **one row with two reasons**, not
    two rows. First-sighting order is preserved, so the provenance view can also
    say which tier first raised the objection.
    """

    _rows: dict[int, MatchCandidate] = field(default_factory=dict)

    def note(
        self, row: CatalogRow, relation: Relation, reason: RejectionReason | None
    ) -> None:
        existing = self._rows.get(row.id)
        if existing is None:
            self._rows[row.id] = MatchCandidate(
                pantry_item_id=row.id,
                canonical_name=row.canonical_name,
                pantry_category=row.category,
                family=row.family,
                relation=relation,
                rejected_by=() if reason is None else (reason,),
            )
            return
        if reason is None:
            # A row the ladder eventually **adopted** must not keep the
            # objections it raised on the way there. `茼蒿` is refused by the
            # segment rule on tier 5 and adopted on tier 6, and a record that
            # still said `segment_boundary` would be a provenance view lying
            # about the row it just adopted.
            if existing.rejected_by:
                self._rows[row.id] = replace(existing, rejected_by=())
            return
        if reason not in existing.rejected_by:
            # `replace` rather than an attribute write: a frozen candidate the
            # audit could mutate in place would be mutable in every other
            # caller's hands too, and that immutability is what lets a
            # `MatchResult` be cached.
            self._rows[row.id] = replace(
                existing, rejected_by=(*existing.rejected_by, reason)
            )

    def snapshot(self) -> tuple[MatchCandidate, ...]:
        return tuple(self._rows[row_id] for row_id in sorted(self._rows))


@dataclass(frozen=True)
class _Hit:
    """One pool row, and whether the segment-boundary guard admitted it.

    The pool is always built naively and the guard is always a separate decision,
    which is what makes each guard independently deletable and its deletion
    observable. Building the pool already-guarded would leave nothing to delete.
    """

    row: CatalogRow
    admitted: bool
    relation: Relation


def resolve_ingredient(
    parsed: ParsedIngredient,
    stock: StockSignal | None,
    catalog: CatalogSnapshot,
    *,
    source: IngredientSource = "材料",
    guards: MatchGuards = DEFAULT_GUARDS,
) -> MatchResult:
    """Resolve one parsed frontmatter value to at most one Pantry Item.

    Tiers 1-7 in order, first accepted result wins; then the §9.7 fuzzy
    tolerance; then the miss with its top-3 near misses. Pure.

    `source` defaults to `材料`, the **safe** default: a caller that forgets to
    pass the key gets the stricter answer, because the staples tier may only
    assume a Seasoning is on hand when the value came from `调料`. A `材料`
    value on the staples list resolves to `unresolved` with
    `misfiled_staple=True` — the provenance view's "probable mis-filed
    frontmatter entry", which keeps `CONTEXT.md`'s `调料`/`材料` separation
    enforced inside the matcher rather than only at the display layer.
    """
    audit = _Audit()
    in_stock = frozenset(stock.in_stock_ids) if stock is not None else frozenset()
    core = _core_of(parsed)

    def finish(
        method: MatchMethod,
        tier: int,
        confidence: float,
        row: CatalogRow | None,
        relation: Relation | None,
        near_misses: tuple[NearMiss, ...] = (),
        misfiled_staple: bool = False,
    ) -> MatchResult:
        if row is not None and relation is not None:
            audit.note(row, relation, None)
        return _result(
            parsed=parsed,
            source=source,
            method=method,
            tier=tier,
            pantry_item_id=row.id if row is not None else None,
            confidence=confidence,
            audit=audit,
            in_stock=row is not None and row.id in in_stock,
            near_misses=near_misses,
            misfiled_staple=misfiled_staple,
        )

    # --- Tier 0: nothing to match ---------------------------------------
    # A value written in a shape the parser does not recognise. Reported as a
    # hard "needs a constant" rather than folded into the miss, because a miss
    # says "no row has this name" and this says "we never got a name at all" —
    # and only the second one is repairable by editing the frontmatter.
    if parsed.parse_method == "unparseable" or parsed.parsed_name is None:
        return finish(UNPARSEABLE_METHOD, UNPARSEABLE_TIER, 0.0, None, None)

    # --- Tier 1: an id the user wrote down ------------------------------
    # Checked against the live `by_id`, so a renumbered `items.id` is a miss
    # rather than a pointer at a different product.
    item_id = _written_id(parsed)
    if item_id is not None and item_id in catalog.by_id:
        return finish("exact_id", 1, TIER_CONFIDENCE[1], catalog.by_id[item_id], "exact_id")

    # --- Tier 2: the whole product name, exactly ------------------------
    # NFKC + trim + casefold only. No size stripping, no brand stripping —
    # nothing that could make two different products compare equal.
    exact = catalog.by_name.get(fold_name(parsed.parsed_name))
    if exact:
        _note_all(exact, "exact_name", audit)
        return finish("exact_name", 2, TIER_CONFIDENCE[2], exact[0], "exact_name")

    # --- Tier 3: a `variants` alias --------------------------------------
    # **Expected to match nothing** on the real catalog: 3 of 178 rows carry a
    # non-empty `variants` and two of those are just the long product name. It
    # exists because `CONTEXT.md` defines Pantry Item Alias in terms of
    # `variants`. Near-zero coverage here is the correct outcome and must not be
    # "fixed" by loosening the tier, by widening the synonym set to compensate,
    # or by deleting it.
    aliased = catalog.by_variant.get(fold_name(parsed.parsed_name))
    if aliased:
        _note_all(aliased, "variant_alias", audit)
        return finish(
            "variant_alias", 3, TIER_CONFIDENCE[3], aliased[0], "variant_alias"
        )

    # --- Tier 4: a bounded prefix of a row's effective basename ----------
    # Separate from tier 5 because tier 5 demands full equality while this
    # accepts a prefix. `空心菜` reaches `空心菜嫩苗` (83) here.
    prefix_pool = _prefix_pool(catalog, core, guards)
    winner = _first_admitted(prefix_pool, audit)
    if winner is not None:
        return finish(
            "note_basename", 4, TIER_CONFIDENCE[4], winner, "basename_prefix"
        )

    # --- Tier 5: the normalized product core ------------------------------
    # Equal to a row's, or present as a whole token of one. The `contains` half
    # is what reaches the catalog-side `Protion` typo through `Mackerel` (§9.7) —
    # a token rule, not an edit distance — and it is where the segment guard
    # does most of its work.
    if len(core) >= MIN_NORMALIZED_CHARS:
        equal = catalog.by_basename.get(core)
        if equal:
            _note_all(equal, "normalized_exact", audit)
            return finish(
                "normalized_exact", 5, TIER_CONFIDENCE[5], equal[0], "normalized_exact"
            )
    # The `contains` half carries **no** length floor, and that asymmetry is the
    # point rather than an oversight. §9.8's "≥ 2 characters" qualifies the
    # *equality* claim — a one-character name cannot be equal to a two-character
    # row's core in any useful way. The `contains` claim is a different claim
    # about a different thing, and a single CJK character is exactly what §9.7's
    # segment rule is written for: `蒜` is one character and it is one character
    # that must never reach `蒜香蒸茄子` (72) or `薯片蒜蓉面包味` (110). Gating the
    # contains rule on two characters would have made that guard untestable by
    # deleting it, because the tier would decline to run at all.
    if core:
        token_pool = _contains_pool(catalog, core, guards)
        winner = _first_admitted(token_pool, audit)
        if winner is not None:
            return finish(
                "normalized_exact", 5, TIER_CONFIDENCE[5], winner, "token_contains"
            )

    # --- Tier 6: the closed synonym set, with the family guard -----------
    entry = SYNONYM_INDEX.get(fold_name(parsed.parsed_name))
    if entry is not None:
        rows = _family_filter(
            entry, _synonym_pool(catalog, entry, core, guards), guards, audit
        )
        if rows:
            _note_all(rows, "synonym_exact", audit)
            return finish("synonym", 6, TIER_CONFIDENCE[6], rows[0], "synonym_exact")

    # --- Tier 7: a Seasoning assumed on hand -----------------------------
    # `调料` only. A `材料` value on the list is a data smell, not a Seasoning:
    # a 材料 of `油` would be a real bulk purchase, and counting it as found
    # would inflate the headline D4 exists to keep honest.
    is_staple = STAPLES.is_staple(core)
    if is_staple and source == "调料":
        return finish("staples", 7, TIER_CONFIDENCE[7], None, None)

    # --- The §9.7 fuzzy tolerance: the last tolerance, not a numbered rung
    fuzzy = _fuzzy_row(catalog, core, audit)
    if fuzzy is not None:
        return finish(FUZZY_METHOD, FUZZY_TIER, FUZZY_CONFIDENCE, fuzzy, "fuzzy_normalized")

    return finish(
        UNRESOLVED_METHOD,
        UNRESOLVED_TIER,
        0.0,
        None,
        None,
        near_misses=_near_misses(catalog, core),
        misfiled_staple=is_staple and source == "材料",
    )


# --- tier 4 -----------------------------------------------------------------


def _prefix_pool(
    catalog: CatalogSnapshot, core: str, guards: MatchGuards
) -> tuple[_Hit, ...]:
    """Tier 4's pool: rows whose product core **begins with** the recipe core.

    A suffix rule, not a whole-segment rule, and the distinction is the guard's
    whole content. `空心菜嫩苗` (83) is the *same food* written more precisely, so
    a prefix relation is right; `芝麻烧饼` (102) is a *different food* that merely
    starts with `芝麻`, so it is not. §9.8 requires the unmatched remainder to
    hold no CJK character, which admits the first and refuses the second.

    The floor is `MIN_NORMALIZED_CHARS`, so a one-character name never gets the
    benefit of the doubt: `蒜` is a prefix of `蒜香蒸茄子` (72) and one character is
    not a word.
    """
    if len(core) < MIN_NORMALIZED_CHARS:
        return ()
    hits: list[_Hit] = []
    for key, rows in catalog.by_basename.items():
        # A **proper** prefix, and the strictness is load-bearing: an *improper*
        # one would make tier 4 a superset of tier 5's equality, so every
        # normalized-exact answer would be reported as `note_basename` at 0.9 and
        # tier 5 would only ever fire on a catalog whose basenames no recipe name
        # happens to start with. §9.8 draws the line exactly here: tier 4 accepts
        # a bounded prefix, tier 5 demands full equality.
        if not key.startswith(core) or len(key) == len(core):
            continue
        admitted = (not guards.segment_boundary) or (
            _IDEOGRAPH.search(key[len(core) :]) is None
        )
        hits.extend(
            _Hit(row=row, admitted=admitted, relation="basename_prefix") for row in rows
        )
    return tuple(sorted(hits, key=lambda hit: hit.row.id))


# --- tier 5 -----------------------------------------------------------------


def _note_all(rows: tuple[CatalogRow, ...], relation: Relation, audit: _Audit) -> None:
    """Record every row a tier's pool held, not only the one it adopted.

    Eight catalog keys hold more than one row and a name lookup that hid the
    loser would be indistinguishable from a catalog with one row. The unadopted
    sibling is recorded with an **empty** `rejected_by`, which is the honest
    encoding: it was considered, it was not refused, and it was not chosen —
    those are three different facts and only the middle one is empty.
    """
    for row in rows:
        audit.note(row, relation, None)


def _first_admitted(pool: tuple[_Hit, ...], audit: _Audit) -> CatalogRow | None:
    """The lowest-id admitted row of a pool, with the whole pool recorded.

    The pool is walked in `id` order and *every* row is noted before the winner
    is taken, so the audit trail is a property of the pool rather than of how far
    the loop happened to get. First-hit-wins is about which row is **adopted**,
    not about which rows are **seen**.
    """
    winner: CatalogRow | None = None
    for hit in pool:
        audit.note(hit.row, hit.relation, None if hit.admitted else "segment_boundary")
        if hit.admitted and winner is None:
            winner = hit.row
    return winner


def _contains_pool(
    catalog: CatalogSnapshot, core: str, guards: MatchGuards
) -> tuple[_Hit, ...]:
    """Tier 5's `contains` pool: `core` appearing anywhere in a row's core.

    Built as a **naive substring pool** and then admitted or refused per row, so
    the guard is a decision the ladder records rather than a filter baked into
    the pool. With the guard on, `core` must be a whole segment of the row's
    core: `Organic Baby Kale` contains `Kale`, `呀!土豆 薯条 里脊牛排味` does not
    contain `土豆`.
    """
    if not core:
        return ()
    hits: list[_Hit] = []
    for key, rows in catalog.by_basename.items():
        if core not in key:
            continue
        admitted = (not guards.segment_boundary) or _is_whole_segment(key, core)
        hits.extend(
            _Hit(row=row, admitted=admitted, relation="token_contains") for row in rows
        )
    return tuple(sorted(hits, key=lambda hit: hit.row.id))


# --- tier 6 -----------------------------------------------------------------


def _synonym_pool(
    catalog: CatalogSnapshot,
    entry: SynonymEntry,
    core: str,
    guards: MatchGuards,
) -> tuple[_Hit, ...]:
    """Tier 6's pool: every row any name in the entry reaches, guard-annotated.

    A name in the entry reaches a row by exact normalized equality (against both
    the name and the basename indexes) or by whole-token `contains` in the
    basename.

    **The recipe's own name is deliberately included**, even though tier 5 has
    already compared it, and that is what makes the category-family guard
    observable on the live catalog. `芝麻` is scanned as `芝麻` here, so
    `高笑美芝麻饼干` (25, family `4`) and `思念 黑芝麻开心果玉汤圆` (66, family
    `1.2`) reach the entry's `families` allowlist and are refused on **category
    alone** — which is §3.2's claim that the family guard "rejects it
    independently of any tokenization", and it is the only way that claim can be
    observed from a result rather than read in a comment. Skipping the recipe's
    own name would have made the guard's second half dead code on real data.
    """
    best: dict[int, _Hit] = {}
    for name in entry.names:
        folded = fold_name(normalize_ingredient(name))
        for row in _rows_named(catalog, folded):
            _merge_hit(best, _Hit(row=row, admitted=True, relation="synonym_exact"))
        for hit in _contains_pool(catalog, folded, guards):
            _merge_hit(
                best, _Hit(row=hit.row, admitted=hit.admitted, relation="synonym_token")
            )
    return tuple(best[row_id] for row_id in sorted(best))


def _merge_hit(best: dict[int, _Hit], hit: _Hit) -> None:
    """Keep the *strongest* sighting of one row, not the first.

    One row can be reached twice by the same entry — `红苋菜苗` is the basename
    that `红苋菜` is a fragment of **and** an exact alias in the same entry — and
    a first-wins merge would keep the fragment sighting, record it as
    segment-rejected, and then refuse to let the exact equality through. An
    admitted sighting beats a refused one; `synonym_exact` beats
    `synonym_token` among equals, because whole-name equality is a stronger
    claim about *why* the row matched than a token appearing in it.
    """
    existing = best.get(hit.row.id)
    if existing is None:
        best[hit.row.id] = hit
        return
    if existing.admitted and not hit.admitted:
        return
    if hit.admitted and not existing.admitted:
        best[hit.row.id] = hit
        return
    if hit.relation == "synonym_exact" and existing.relation != "synonym_exact":
        best[hit.row.id] = hit


def _family_filter(
    entry: SynonymEntry,
    pool: tuple[_Hit, ...],
    guards: MatchGuards,
    audit: _Audit,
) -> tuple[CatalogRow, ...]:
    """The category-family guard, and the only place it runs.

    Two rules, both required, both independently switchable:

    * **Allowlist.** A candidate survives only if its Pantry Category *family* is
      one the entry's own `families` names. This is what refuses
      `高笑美芝麻饼干` (25, family `4`) and `薯片蒜蓉面包味` (110, family `4`) for
      `芝麻` and `蒜` on category alone, with no tokenization involved.
    * **Single class.** The survivors must share ONE family. Two candidates from
      different families are rejected outright, because picking one of them is
      the matcher's guess and a guess is what this ladder refuses. This is what
      makes `西兰花` resolve to broccoli florets (14, 118 — family `1.2`) instead
      of florets *plus* microgreens (154 — family `1.1`) *plus* nothing.
    """
    admitted: list[CatalogRow] = []
    for hit in pool:
        # Both objections are recorded when both apply, and both are checked
        # before the row is admitted. A row that the segment rule refused *and*
        # the allowlist refused — `高笑美芝麻饼干` (25) is exactly that — must show
        # both reasons, because an audit trail that reported only the first would
        # understate what is holding it back and would make §3.2's claim that the
        # family guard "independently" rejects 25 unobservable from the result.
        on_class = (not guards.category_family) or entry.allows(hit.row.family)
        if not on_class:
            audit.note(hit.row, hit.relation, "category_family")
        if not hit.admitted:
            audit.note(hit.row, hit.relation, "segment_boundary")
        if on_class and hit.admitted:
            admitted.append(hit.row)
    if not admitted:
        return ()
    if guards.category_family and len({row.family for row in admitted}) > 1:
        for row in admitted:
            audit.note(row, "synonym_exact", "mixed_families")
        return ()
    return tuple(admitted)


def _rows_named(catalog: CatalogSnapshot, folded: str) -> tuple[CatalogRow, ...]:
    """Rows a folded name reaches by exact equality, from both name indexes.

    Both indexes, and both candidates kept, because a key that is one row's
    `canonical_name` and also a basename shared with a re-ingested duplicate is
    one key and two rows — and a name lookup that silently drops one of them is
    the failure the tuple-valued indexes exist to prevent.
    """
    merged: dict[int, CatalogRow] = {}
    for source in (catalog.by_name, catalog.by_basename):
        for row in source.get(folded, ()):
            merged.setdefault(row.id, row)
    return tuple(merged[row_id] for row_id in sorted(merged))


# --- the §9.7 fuzzy tolerance ----------------------------------------------


def _fuzzy_row(catalog: CatalogSnapshot, core: str, audit: _Audit) -> CatalogRow | None:
    """The one row the fuzzy tolerance accepts, or `None`.

    Levenshtein ≤ 1 on the **normalized** strings only — never on raw text — and
    only when both are ≥ 6 characters, single-script, uniquely closest, and in
    `FUZZY_FAMILIES`.

    The class check is a **closed allowlist** rather than evidence derived from
    this name, and the reason is structural: a misspelling is by definition not a
    prefix of, a whole token of, or a lexicon entry for anything, so there is no
    evidence to derive one from. `FUZZY_FAMILIES` is therefore the guard, and it
    excludes `4` (snack) precisely so the tolerance cannot become the back door
    to the rows `segment_boundary` and `category_family` refuse. On the real
    corpus the tolerance never fires, and it *cannot*: every name the two guards
    refuse is shorter than the six characters the tolerance requires, which is
    §9.7's own structural argument for why `蒜` cannot reach `柴米 蒜香蒸茄子`.
    """
    if len(core) < MIN_FUZZY_CHARS or not _is_single_script(core):
        return None
    scored: list[tuple[int, int, CatalogRow]] = []
    for key, rows in catalog.by_basename.items():
        if len(key) < MIN_FUZZY_CHARS or not _is_single_script(key):
            continue
        distance = _levenshtein_within(core, key, 1)
        if distance is None:
            continue
        for row in rows:
            scored.append((distance, row.id, row))
    if not scored:
        return None
    best = min(distance for distance, _item_id, _row in scored)
    winners = sorted(
        (row for distance, _item_id, row in scored if distance == best),
        key=lambda row: row.id,
    )
    if len(winners) != 1:
        for row in winners:
            audit.note(row, "fuzzy_normalized", "fuzzy_ambiguous")
        return None
    if winners[0].family not in FUZZY_FAMILIES:
        audit.note(winners[0], "fuzzy_normalized", "fuzzy_family_not_allowed")
        return None
    return winners[0]


# --- near misses ------------------------------------------------------------


def _near_misses(catalog: CatalogSnapshot, core: str) -> tuple[NearMiss, ...]:
    """The top-3 rows for one-tap binding, ranked deterministically.

    Unguarded on purpose, for the reason `NearMiss` gives: the repair path for a
    miss is a human saying "yes, that one", so the list has to be able to show
    the rows the guards refused — `土豆` offering `好丽友 呀!土豆 薯条` (62) is
    exactly the case where the user is the only thing that can tell a potato chip
    from a potato.

    Ranked by **how the name relates to the row** before distance, and with
    cross-script pairs dropped outright. Distance alone is a bad rank for CJK:
    every three-character Chinese name is within three edits of every other one,
    so `包菜` would offer `小白菜心` and `必品阁 紫菜汤` as equally good guesses,
    and comparing a Chinese name against a Latin one is a distance so large it
    only ever produces noise. Neither tells the user anything.
    """
    if not core:
        return ()
    scored: list[tuple[int, int, int, str, CatalogRow]] = []
    for key, rows in catalog.by_basename.items():
        if not key or _is_single_script(key) != _is_single_script(core):
            continue
        if key == core:
            rank, distance, relation = 0, 0, "normalized_exact"
        elif _is_whole_segment(key, core):
            rank, distance, relation = 0, 0, "whole_segment"
        elif core in key:
            rank, distance, relation = 1, 0, "contains"
        else:
            found = _levenshtein_within(core, key, NEAR_MISS_MAX_DISTANCE)
            if found is None:
                continue
            rank, distance, relation = 2, found, "near"
        for row in rows:
            scored.append((rank, distance, row.id, relation, row))
    scored.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
    return tuple(
        NearMiss(
            pantry_item_id=row.id,
            canonical_name=row.canonical_name,
            pantry_category=row.category,
            family=row.family,
            distance=distance,
            relation=relation,
        )
        for _rank, distance, _item_id, relation, row in scored[:MAX_NEAR_MISSES]
    )


# --- the segment-boundary rule, in one place -------------------------------


def _is_whole_segment(text: str, token: str) -> bool:
    """True when `token` is a whole whitespace-delimited segment of `text`.

    §9.7's flavour-descriptor guard, rule 1, in its one implementation. A Latin
    token needs no separate `\\b` rule: a whitespace-delimited segment of a Latin
    name **is** a word, so deriving the boundary from the same function is what
    keeps the two halves of the rule from disagreeing about `Protion-cut`.
    """
    target = fold_name(token)
    if not target:
        return False
    return any(fold_name(segment) == target for segment in _segments(text))


def _segments(text: str) -> tuple[str, ...]:
    stripped = text.strip()
    if not stripped:
        return ()
    return tuple(segment for segment in _DELIMITERS.split(stripped) if segment)


def _has_cjk(value: str) -> bool:
    return _IDEOGRAPH.search(value) is not None


def _is_single_script(value: str) -> bool:
    """§9.7's fuzzy precondition: `value` is all CJK or all non-CJK.

    A mixed-script name's edit distance to anything is dominated by tokenisation
    rather than by spelling, so `香菇` and `Shiitake` are never within distance
    1 of each other and must not be measured as if they were.
    """
    return not (_has_cjk(value) and _LATIN_LETTER.search(value))


def _core_of(parsed: ParsedIngredient) -> str:
    """The shared product core of a parsed name, folded for comparison.

    `normalize_ingredient()` is the ONE normalizer (F15, §9.7) and `fold_name()`
    the ONE index-key folding, both reused rather than reimplemented — a second
    normalizer here would be a second answer to the same question, and the two
    would drift the first time a brand entered the lexicon. Tiers 4 and 5 and the
    fuzzy tolerance are the only places a core is compared against a row's core;
    tiers 2 and 3 deliberately are not, because "exact after NFKC + trim only" is
    a differently-typed and weaker claim than "equal after every strip step".
    """
    return fold_name(normalize_ingredient(parsed.parsed_name or ""))


def _written_id(parsed: ParsedIngredient) -> int | None:
    """The Pantry Item id a value spells out, or `None`.

    Read from `raw` and `parsed_name` both, because `[[id:83]]` reaches the
    parser as a wikilink whose target is `id:83` while a bare `83` is a `bare`
    value whose name *is* the digits. Only a number that is live in `by_id` at
    this call is ever returned, so a stale or mistyped id is simply not tier 1.
    """
    for value in (parsed.raw, parsed.parsed_name):
        if value is None:
            continue
        found = _WRITTEN_ID.match(value.strip())
        if found is not None:
            return int(found.group("number"))
    return None


def _levenshtein_within(left: str, right: str, cap: int) -> int | None:
    """Levenshtein distance, or `None` when it exceeds `cap`.

    A bounded two-row dynamic program with a per-row early exit. The cap keeps
    the near-miss sweep linear in practice instead of quadratic over all 176
    basenames for every unresolved value, and it is exact rather than
    approximate: once a row's best cell exceeds the cap no later row can bring
    the total back under it, so `None` is a real answer and not a guess.
    """
    if abs(len(left) - len(right)) > cap:
        return None
    previous = list(range(len(right) + 1))
    for index, left_character in enumerate(left, start=1):
        current = [index]
        row_best = index
        for position, right_character in enumerate(right, start=1):
            cost = 0 if left_character == right_character else 1
            cell = min(
                previous[position] + 1,
                current[position - 1] + 1,
                previous[position - 1] + cost,
            )
            current.append(cell)
            row_best = min(row_best, cell)
        if row_best > cap:
            return None
        previous = current
    distance = previous[len(right)]
    return distance if distance <= cap else None


def _result(
    *,
    parsed: ParsedIngredient,
    source: IngredientSource,
    method: MatchMethod,
    tier: int,
    pantry_item_id: int | None,
    confidence: float,
    audit: _Audit,
    in_stock: bool,
    near_misses: tuple[NearMiss, ...] = (),
    misfiled_staple: bool = False,
) -> MatchResult:
    """Assemble the result. One place, so the field set cannot drift per tier."""
    return MatchResult(
        raw_value=parsed.raw,
        parsed_name=parsed.parsed_name,
        parse_method=parsed.parse_method,
        source=source,
        match_method=method,
        match_tier=tier,
        pantry_item_id=pantry_item_id,
        confidence=confidence,
        candidates=audit.snapshot(),
        near_misses=near_misses,
        in_stock=in_stock,
        misfiled_staple=misfiled_staple,
    )


#: Re-exported so a caller — or the test that proves which files this module
#: reads — can reach the two source paths without importing the loader module.
#: Both are resolved through `importlib.resources` against the `app.recipes`
#: package, so they are correct from a source checkout and from a wheel alike.
__all__ = [
    "SYNONYMS_SOURCE",
    "STAPLES_SOURCE",
    "MatchCandidate",
    "MatchGuards",
    "MatchResult",
    "NearMiss",
    "STAPLES",
    "SYNONYM_INDEX",
    "SYNONYMS",
    "StockSignal",
    "resolve_ingredient",
]

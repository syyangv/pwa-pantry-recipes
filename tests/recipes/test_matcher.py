"""Each of the eight tiers in isolation, plus the properties the ladder depends on.

`test_must_not_match.py` owns the two flavour-descriptor guards and the live
collision rows; this file owns the ladder's **shape**: that every tier fires on
its own, that each stops at its boundary, and that the order is the contract
rather than an accident of the loop.

**The ordering tests are mutation tests, and that is the point.** §9.8 says the
order is not negotiable, so a test that only ever observed the shipped order
would pass just as happily against an implementation with the tiers in any order
at all. So the order is asserted against the *call sequence* inside
`resolve_ingredient` rather than against the lookup tables the loop reads, and
against a synthetic catalog built to make a reordering produce a different,
detectable answer.

**The corpus is committed data, never a live path.** The catalog is
`tests/fixtures/pantry_items_snapshot.json` (F14) and the recipes are
`tests/fixtures/real_recipes/*.md`. Reading the producer's `pantry_items.db` from
a sibling checkout would make this suite pass on one machine and assert nothing
in CI, which is the shape of gate `test_brand_lexicon.py` was refactored away
from.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Final

import pytest

from app.recipes import matcher
from app.recipes.ingredients import EMOJI_NAMES, parse_ingredient_value
from app.recipes.matcher import (
    DEFAULT_GUARDS,
    MAX_NEAR_MISSES,
    TIER_CONFIDENCE,
    TIER_METHOD,
)
from app.recipes.normalize import normalize_ingredient

from .corpus import (
    CANDIDATE_ROWS,
    PARSED_MATERIAL_NAMES,
    RAW_MATERIAL_VALUES,
    RECIPE_COUNT,
    SEASONING_NAMES,
    TOTAL_CATALOG_ROWS,
    committed_catalog,
    corpus_stock,
    real_names_for,
    real_notes,
    real_values_for,
    resolve,
    synthetic_catalog,
)

#: The committed catalog, bound once for the module-level cases. Every function
#: in this file that needs it takes it as a parameter or reads this.
CATALOG: Final = committed_catalog()

#: A synthetic catalog with one row per shape a tier needs. The committed 178
#: rows were ingested to answer *what the user bought*, and none of them is a
#: bare `Sugar` or a `[[id:…]]`, so a test that could not construct its own row
#: would only ever run on a catalog shape nobody promised.
SIMPLE: Final = synthetic_catalog(
    [
        (1, "Sugar, 4 Lb", "1.1c", "[]"),
        (2, "Sumasang Plain Yogurt 32 Oz", "1.1d", "[]"),
        (3, "Salsa Verde 200 g", "1.2", "[]"),
        (4, "Roma Tomato 1 Lb", "1.1", "[]"),
        (5, "Cilantro 1 bunch", "1.1", "[]"),
        (6, "Whole Star Anise 2 Oz", "2", '["八角"]'),
        (7, "Napa Cabbage 2 Lb", "1.1", "[]"),
        (8, "Chicken Wings 2 Lb", "1.1", "[]"),
        (9, "Toscanini Olive Oil 500 ml", "1.1c", "[]"),
    ]
)


# --- tier 0 ----------------------------------------------------------------


def test_tier_0_is_a_hard_stop_not_a_silent_miss() -> None:
    """An unparseable value is reported as itself, at tier 0, with no id.

    Not folded into `unresolved` and not silently dropped: a value written in a
    shape `parse_ingredient_value` does not recognise is a *different problem*
    from a name no row happens to carry, and only the first is repairable by
    editing the frontmatter. Collapsing the two would make the provenance view
    offer three near misses for a value it never managed to read.
    """
    parsed = parse_ingredient_value("💥💥💥")
    assert parsed.parse_method == "unparseable"
    assert parsed.parsed_name is None
    result = matcher.resolve_ingredient(parsed, None, SIMPLE)
    assert result.match_method == "unparseable"
    assert result.match_tier == 0
    assert result.pantry_item_id is None
    assert result.confidence == 0.0
    assert result.candidates == ()
    assert result.near_misses == ()
    assert result.raw_value == "💥💥💥", "the audit anchor is never rewritten"


@pytest.mark.parametrize("raw", ["   ", '""', "[[]]", "[[   ]]", '/'  , "  /  "])
def test_a_value_that_reduces_to_nothing_is_tier_0_too(raw: str) -> None:
    """The same condition reached a different way, and it is still tier 0."""
    parsed = parse_ingredient_value(raw)
    assert parsed.parse_method == "unparseable", raw
    assert matcher.resolve_ingredient(parsed, None, SIMPLE).match_tier == 0, raw


# --- tier 1 ----------------------------------------------------------------


@pytest.mark.parametrize("raw", ["4", "[[id:4]]", "[[4]]", "id:4", "  4  "])
def test_tier_1_resolves_an_id_the_user_wrote_down(raw: str) -> None:
    """An explicit Pantry Item id wins, and only while it is live.

    `[[id:4]]` reaches the parser as a wikilink whose target is `id:4` and `4` is
    a `bare` value whose name *is* the digits, so the id is read from `raw` and
    `parsed_name` both.
    """
    result = resolve(raw, SIMPLE)
    assert result.match_method == "exact_id", raw
    assert result.match_tier == 1
    assert result.pantry_item_id == 4, raw
    assert result.confidence == 1.0, raw
    assert result.candidates[0].relation == "exact_id", raw


@pytest.mark.parametrize("raw", ["[[id:9999]]", "9999", "id:0"])
def test_tier_1_is_a_miss_and_not_a_wrong_row_when_the_id_is_dead(raw: str) -> None:
    """A renumbered `items.id` must not resolve to whatever took its place.

    This is why `ingredient_mappings` is re-resolved rather than trusted, and why
    no lexicon file may carry an id: the answer to "is this mapping still true?"
    has to be checkable against the live catalog, and the only honest answer to
    "no" is a miss.
    """
    result = resolve(raw, SIMPLE)
    assert result.pantry_item_id is None, raw
    assert result.match_method == "unresolved", raw
    assert result.confidence == 0.0, raw


def test_tier_1_needs_the_catalog_to_hold_the_id() -> None:
    """A row *named* `83` is not reachable by writing `83`.

    The digits-only rule is a lexicon-file rule and does not apply here; what
    applies is that the answer is a lookup in `by_id`, so a name that happens to
    be digits resolves only because a row with that id exists.
    """
    named = synthetic_catalog([(83, "83", "1.1", "[]")])
    assert resolve("83", named).pantry_item_id == 83
    assert resolve("Organic Baby Kale 5 oz", SIMPLE).match_method != "exact_id"


# --- tier 2 ----------------------------------------------------------------


def test_tier_2_matches_the_whole_canonical_name_exactly() -> None:
    result = resolve("Sugar, 4 Lb", SIMPLE)
    assert result.match_method == "exact_name"
    assert result.match_tier == 2
    assert result.pantry_item_id == 1
    assert result.confidence == 1.0


@pytest.mark.parametrize("raw", ["sugar, 4 lb", "  Sugar, 4 Lb  ", "Ｓｕｇａｒ, 4 Lb"])
def test_tier_2_folds_case_and_width_but_strips_nothing(raw: str) -> None:
    """NFKC + trim + casefold, and nothing else.

    No size stripping and no brand stripping, because "exact after NFKC + trim
    only" is a differently-typed claim from "equal after every strip step": a
    name that matches only *because* its size was removed is a claim about two
    different products that happen to weigh the same, and that is tier 5's claim
    to make.
    """
    assert resolve(raw, SIMPLE).pantry_item_id == 1, raw


def test_tier_2_is_not_the_product_core() -> None:
    """`Sugar` is tier 5's answer, not tier 2's. The tier boundary, asserted."""
    assert normalize_ingredient("Sugar, 4 Lb") == "Sugar"
    assert resolve("Sugar, 4 Lb", SIMPLE).match_tier == 2
    assert resolve("Sugar", SIMPLE).match_tier == 5, (
        "tier 4 is a PROPER prefix relation; an improper one would shadow tier "
        "5's equality and report every normalized-exact answer as note_basename"
    )


# --- tier 3 ----------------------------------------------------------------


def test_tier_3_resolves_a_variants_alias() -> None:
    """Tier 3 works; the synthetic row is what makes it observable."""
    result = resolve("八角", SIMPLE)
    assert result.match_method == "variant_alias"
    assert result.match_tier == 3
    assert result.pantry_item_id == 6
    assert result.confidence == 0.95


def test_tier_3_is_near_dead_on_the_real_catalog_and_that_is_the_assertion() -> None:
    """Only 3 of 178 rows carry a non-empty `variants`, and two of those are
    just the long product name — so the tier resolves **nothing** across all 45
    distinct frontmatter values.

    Near-zero coverage here is the correct outcome and must not be "fixed" by
    loosening the tier, by widening the synonym set to compensate, or by deleting
    it. The tier exists because `CONTEXT.md` defines Pantry Item Alias in terms
    of `variants` and a future catalog re-import could populate it, and the
    assertion is written so that a future implementer who "fixes" it sees this
    test fail and read why.
    """
    populated = [row for row in CATALOG.rows if row.variants]
    assert [row.id for row in populated] == [52, 55, 169]
    assert CATALOG.by_variant
    for source in ("材料", "调料"):
        for name in real_names_for(source):
            assert resolve(name, CATALOG).match_tier != 3, name


# --- tier 4 ----------------------------------------------------------------


def test_tier_4_accepts_a_prefix_whose_remainder_is_not_cjk() -> None:
    """A **suffix** rule rather than the whole-segment rule.

    `Kale` → `Kale Microgreens` (56): the row's product core *extends* the
    recipe's name with a Latin qualifier, so a prefix relation is the right
    claim. The CJK-remainder test is what makes the tier a *bounded* prefix
    rather than a `startswith`, and the next test is what it refuses.
    """
    result = resolve("Kale", CATALOG)
    assert result.match_method == "note_basename"
    assert result.match_tier == 4
    assert result.pantry_item_id == 56
    assert result.confidence == 0.9


def test_a_cjk_remainder_is_refused_so_empty_heart_grass_cannot_be_a_tier_4_hit() -> None:
    """`空心菜` → `空心菜嫩苗` (83) is a **miss for tier 4**, and the row is still
    reached — at tier 6.

    The tier-4 remainder rule is the same segment-boundary rule §9.7 uses, and it
    cannot tell `空心菜嫩苗` (water spinach, written more precisely) from
    `芝麻烧饼` (a sesame flatbread, a different food). Both are the recipe's name
    plus a CJK remainder, so tier 4 refuses both. §10.3's golden fixture records
    `空心菜 → 83` as `normalized_exact`, which is not reachable: tier 5 demands
    equality and `空心菜` is not equal to `空心菜嫩苗`. The id is what matters and
    it is reached — through the synonym entry that names the catalog's own
    spelling, which is a name predicate and survives a catalog re-import, where a
    stored id would not.
    """
    assert normalize_ingredient("空心菜嫩苗 0.95-1.05 磅") == "空心菜嫩苗"
    pool = matcher._prefix_pool(CATALOG, "空心菜", DEFAULT_GUARDS)
    assert [hit.row.id for hit in pool] == [83]
    assert pool[0].admitted is False
    result = resolve("空心菜", CATALOG)
    assert result.pantry_item_id == 83
    assert result.match_tier == 6
    assert result.match_method == "synonym"
    assert result.candidate(83).rejected_by == ()  # type: ignore[union-attr]


def test_tier_4_fires_on_a_latin_prefix_remainder_too() -> None:
    """`Kale` → `Kale Microgreens` (56): the same rule, the other script.

    The remainder `microgreens` holds no CJK, so tier 4 admits it — and it fires
    *before* the synonym tier, so the entry's family allowlist never gets a say.
    Recorded here because it is the tier order doing something a reader might
    otherwise call a bug.
    """
    result = resolve("Kale", CATALOG)
    assert result.match_tier == 4
    assert result.pantry_item_id == 56


def test_tier_4_needs_at_least_two_matching_characters() -> None:
    """A one-character name gets no prefix benefit of the doubt.

    `蒜` is a prefix of `蒜香蒸茄子` (72) and one character is not a word. This is
    a tier definition, not the segment guard, and the two are asserted apart for
    exactly that reason.
    """
    assert resolve("S", SIMPLE).match_tier != 4
    assert resolve("Sa", SIMPLE).match_tier == 4


def test_tier_4_refuses_a_cjk_remainder() -> None:
    """The tier's own boundary: `芝麻` does not reach a row named `芝麻烧饼`."""
    sesame = synthetic_catalog([(1, "芝麻烧饼", "1.1", "[]")])
    result = resolve("芝麻", sesame, source="调料")
    assert result.pantry_item_id is None
    candidate = result.candidate(1)
    assert candidate is not None
    assert candidate.rejected_by == ("segment_boundary",)


# --- tier 5 ----------------------------------------------------------------


def test_tier_5_matches_the_normalized_product_core() -> None:
    """Size and brand stripped from both sides, then compared whole."""
    # The recipe writes a size the catalog row also carries, and the two rows
    # differ only in theirs. Nothing about the *product* needed to be equal for
    # this to work — that is the whole difference between tier 2 and tier 5.
    assert normalize_ingredient("新鲜小叶茼蒿 2 磅") == "新鲜小叶茼蒿"
    result = resolve("新鲜小叶茼蒿 2 磅", CATALOG)
    assert result.match_method == "normalized_exact"
    assert result.match_tier == 5
    assert result.confidence == 0.85
    assert result.pantry_item_id == 70
    assert resolve("新鲜小叶茼蒿 2 磅", CATALOG).match_tier != 2, (
        "tier 2 is exact after trim only, so a different size is not tier 2"
    )
    # …and the brand step did not strip `Whole`, because `Whole` is not a lexicon
    # entry while `Whole Foods Market` is. Position cannot do that.
    assert normalize_ingredient("Whole Star Anise 2 Oz") == "Whole Star Anise"
    assert normalize_ingredient("Whole Foods Market, Organic Broccoli Florets, 16 oz") == (
        "Organic Broccoli Florets"
    )


def test_tier_5_reaches_a_whole_token_of_a_longer_row() -> None:
    """The `contains` half: the catalog-side `Protion` typo, by token not by edit.

    `Frozen Mackerel Boneless Protion-cut 5P 10.58 盎司` is the live row §9.7
    names. `Mackerel` is a whole whitespace-delimited segment of it, so the token
    rule reaches it and the fuzzy tolerance is not involved at all — which is the
    whole argument for a token rule existing.
    """
    result = resolve("Mackerel", CATALOG)
    assert result.match_method == "normalized_exact"
    assert result.pantry_item_id == 22
    assert result.candidates[0].relation == "token_contains"
    assert normalize_ingredient("Frozen Mackerel Boneless Protion-cut 5P 10.58 盎司") == (
        "Frozen Mackerel Boneless Protion-cut 5P"
    )


def test_tier_5_keeps_the_whole_segment_rule_when_the_guard_is_off() -> None:
    """The guard is tier 5's own token rule, so switching it off admits a
    flavour descriptor — and that admission is what
    `test_must_not_match.py` asserts is a wrong answer."""
    potato = resolve("土豆", CATALOG, guards=matcher.MatchGuards(segment_boundary=False))
    assert potato.pantry_item_id == 62
    assert potato.match_tier == 5


def test_tier_5_refuses_a_one_character_equality_but_not_a_one_character_token() -> None:
    """The `≥ 2 characters` floor qualifies the **equality** claim only.

    A one-character name cannot be equal to a row's product core in any useful
    way, so tier 5's equality skips it. Its `contains` half has no floor, and
    that asymmetry is load-bearing rather than an oversight: `蒜` is one
    character and it is the canonical §9.7 segment-boundary case, so a floor on
    the contains rule would make that guard impossible to test by switching it
    off — the tier would decline to run at all.
    """
    one = synthetic_catalog([(1, "Anise 2 Oz", "2", "[]"), (2, "Bottle Anise 2 Oz", "2", "[]")])
    two = synthetic_catalog([(1, "Anise 2 Oz", "2", "[]")])
    assert resolve("Anise", two).match_tier == 5, "two characters: equality is tried"
    assert resolve("An", one).match_tier == 4, "two characters: a prefix is tried"
    assert matcher.MIN_NORMALIZED_CHARS == 2


# --- tier 6 ----------------------------------------------------------------


def test_tier_6_resolves_through_a_curated_synonym_with_one_family() -> None:
    """`西兰花` → `Cascadian Farm Broccoli Florets` (14) at 0.7.

    Tiers 4 and 5 both decline — `西兰花` is neither a prefix of nor a whole
    segment of any row's product core — so the closed synonym set is the only
    thing that reaches the row, which is why it is hand-maintained rather than
    derived.
    """
    result = resolve("西兰花", CATALOG)
    assert result.match_method == "synonym"
    assert result.match_tier == 6
    assert result.pantry_item_id == 14
    assert result.confidence == 0.7


def test_tier_6_needs_a_single_food_class() -> None:
    """Two survivors in two families are rejected; one family is adopted.

    `Broccoli` is in `1.2` twice (14, 118) and in `1.1` once (154, `Forward
    Greens Micro Broccoli`). The entry's allowlist names `1.2` only, so 154 is
    refused on class alone and the two survivors are one family — which is the
    only reason the entry can resolve at all.
    """
    result = resolve("西兰花", CATALOG)
    assert result.candidate_ids() == (14, 118, 154)
    assert result.candidate(154).rejected_by == ("category_family",)  # type: ignore[union-attr]
    assert result.candidate(14).rejected_by == ()  # type: ignore[union-attr]
    assert result.pantry_item_id == 14


def test_tier_6_rejects_a_mixed_family_set_when_the_allowlist_permits_both() -> None:
    """The homogeneity half of the guard, exercised by *performing* the widening
    the spec forbids ("do not fix near-zero tier-3 coverage by widening the
    synonym set to compensate").

    Widening `西兰花` to allow both `1.1` and `1.2` admits a two-family candidate
    set, and the guard then refuses the whole entry rather than picking one.
    Asserting the refusal is what proves homogeneity is a second, separate rule
    and not a restatement of the allowlist.
    """
    entry = matcher.SYNONYM_INDEX["西兰花"]
    widened = matcher.SynonymEntry(
        canonical=entry.canonical, match=entry.match, families=("1.1", "1.2")
    )
    pool = matcher._synonym_pool(CATALOG, widened, "西兰花", DEFAULT_GUARDS)
    assert sorted(hit.row.id for hit in pool) == [14, 118, 154]
    audit = matcher._Audit()
    assert matcher._family_filter(widened, pool, DEFAULT_GUARDS, audit) == ()
    assert {c.rejected_by for c in audit.snapshot()} == {("mixed_families",)}


def test_tier_6_never_merges_lemon_into_lime() -> None:
    """`Lemon`↔`柠檬` and `Lime`↔`青柠` are separate entries and stay separate.

    `🍋‍🟩` parses to `青柠`, and a rule that let `青柠` reach a `柠檬` row would
    substitute one fruit for another — the exact silent flavour-descriptor error
    D1 exists to prevent. `青柠` staying `unresolved` is the correct outcome, not
    a gap to be closed later by loosening a tier.
    """
    lemon = matcher.SYNONYM_INDEX["柠檬"]
    lime = matcher.SYNONYM_INDEX["青柠"]
    assert lemon is not lime
    assert "青柠" not in lemon.names
    assert "柠檬" not in lime.names
    assert matcher.SYNONYM_INDEX["lemon"] is lemon
    assert matcher.SYNONYM_INDEX["lime"] is lime

    citrus = synthetic_catalog([(1, "Lemon 4 Lb", "1.1f", "[]")])
    assert resolve("青柠", citrus).pantry_item_id is None
    assert resolve("柠檬", citrus).pantry_item_id == 1


def test_the_lexicon_carries_no_transliteration() -> None:
    """Transliteration is not used and must never be added.

    Pinyin silently merges unrelated foods — `花生` and `华生` are different
    words — and no review of a synonym list catches that, because the pinyin
    looks correct. The check is structural: every ASCII-letter name in the file
    is one of the English names §9.8 lists, so a transliterated `qing ning` or
    `xiang gu` would fail here rather than merge two foods in production.
    """
    english = {
        "Kale", "Arugula", "Cabbage", "Baby Chinese Cabbage", "Water Spinach",
        "Amaranth", "Garland Chrysanthemum", "Black Fungus", "Broccoli",
        "Shiitake", "Shiitake Mushroom", "Mushroom", "Potato", "Sweet Potato",
        "Tomato", "Egg", "Rice", "Mackerel", "Clam", "Chicken Wing",
        "Chicken Wings", "Chicken Thigh", "Shishito", "Basil", "focaccia",
        "Stracciatella", "Lemon", "Lime", "Garlic", "Sesame", "Ginger",
    }
    for entry in matcher.SYNONYMS:
        for name in entry.names:
            if name.isascii() and name.replace(" ", "").isalpha():
                assert name in english, f"unreviewed Latin name in the lexicon: {name}"


# --- tier 7 ----------------------------------------------------------------


def test_tier_7_resolves_a_seasoning_and_only_a_seasoning() -> None:
    """`调料` values on the list are assumed on hand; `材料` values are not.

    A `材料` of `油` would be a real bulk purchase, and counting it as found
    would inflate the `n/total` headline D4 exists to keep honest. So the
    separation is enforced *inside the matcher*, where a caller cannot skip it,
    rather than only at the display layer.
    """
    seasoning = resolve("醋", SIMPLE, source="调料")
    assert seasoning.match_method == "staples"
    assert seasoning.match_tier == 7
    assert seasoning.confidence == 0.6
    assert seasoning.pantry_item_id is None, "a class assumption names no Pantry Item"
    assert seasoning.in_stock is False

    ingredient = resolve("醋", SIMPLE, source="材料")
    assert ingredient.match_method == "unresolved"
    assert ingredient.pantry_item_id is None
    assert ingredient.misfiled_staple is True, "the provenance view's mis-filed flag"
    assert ingredient.match_method != "staples", "and it is never counted as found"


def test_the_default_source_is_the_strict_one() -> None:
    """A caller that forgets the key gets the safer answer, not the looser one.

    Defaulting to `调料` would let a `材料` of `油` count as found at every call
    site that forgot the argument — a headline that inflates silently, which is
    the exact shape of bug the 严格模式 toggle exists to make visible.
    """
    assert resolve("醋", SIMPLE).match_method == "unresolved"
    assert resolve("醋", SIMPLE, source="调料").match_method == "staples"


def test_tier_7_reaches_a_class_suffix() -> None:
    """`调味酱油` is the class staple `酱油` (§9.9's `X酱油 → 酱油`).

    A **suffix** rule, not a substring one: `蒸鱼豉油` and `老抽` both contain
    `酱油`'s characters, and a substring rule would make any name containing a
    condiment character a staple — a class assumption dressed as a match.
    """
    assert "酱油" in matcher.STAPLES.class_suffixes
    assert resolve("调味酱油", CATALOG, source="调料").match_method == "staples"
    assert matcher.STAPLES.is_staple("酱油味小鱼") is False
    assert matcher.STAPLES.is_staple("芝麻") is False
    assert matcher.STAPLES.is_staple("芝麻油") is True


def test_tier_7_sees_the_product_core_and_not_the_frontmatter_bytes() -> None:
    """`🧄` reaches tier 7 as `蒜` through the parser, so emoji and bare agree."""
    emoji = resolve("🧄", CATALOG, source="调料")
    bare = resolve("蒜", CATALOG, source="调料")
    assert emoji.parsed_name == bare.parsed_name == "蒜"
    assert emoji.match_method == bare.match_method == "staples"
    assert emoji.raw_value == "🧄", "the raw value is still the audit anchor"


# --- the fuzzy tolerance, which is not a numbered tier --------------------


def test_the_fuzzy_tolerance_fires_on_a_long_single_script_unique_name() -> None:
    """Levenshtein ≤ 1, on normalized strings, ≥ 6 characters, single-script, unique."""
    close = synthetic_catalog([(1, "Organic Ground Cinnamon 2 Oz", "2", "[]")])
    hit = resolve("Organic Ground Cimnamon", close)
    assert close.by_basename["organic ground cinnamon"][0].family in matcher.FUZZY_FAMILIES
    assert hit.match_method == "fuzzy_normalized"
    assert hit.match_tier == 8
    assert hit.pantry_item_id == 1
    assert hit.confidence < TIER_CONFIDENCE[7], "it must rank below every real tier"


def test_the_fuzzy_tolerance_refuses_a_tie() -> None:
    """No unique best candidate is not an answer, it is a coin flip."""
    tied = synthetic_catalog(
        [
            (1, "Roma Tomate Sauce 24 Oz", "1.2", "[]"),
            (2, "Roma Tomoto Sauce 24 Oz", "1.2", "[]"),
            (3, "Roma Tomato Paste 24 Oz", "1.2", "[]"),
        ]
    )
    result = resolve("Roma Tomato Sauce", tied)
    assert result.pantry_item_id is None
    assert result.match_method == "unresolved"
    assert result.candidate(1).rejected_by == ("fuzzy_ambiguous",)  # type: ignore[union-attr]
    assert result.candidate(2).rejected_by == ("fuzzy_ambiguous",)  # type: ignore[union-attr]
    assert result.candidate(3) is None, "a row further than the cap is never considered"


def test_the_fuzzy_tolerance_refuses_short_and_mixed_script_names() -> None:
    """Each precondition gets its own case, because each is a separate reason to
    decline and a combined assertion would leave all of them deletable."""
    close = synthetic_catalog([(1, "Salt Cedar Plank Salmon", "1.2m", "[]")])
    assert resolve("蒜", close, source="调料").match_tier == 7, "one char: too short"

    snack = synthetic_catalog([(1, "Organic Ground Cinnamon 200 g", "4", "[]")])
    refused = resolve("Organic Ground Cimnamon", snack)
    assert refused.pantry_item_id is None, "family 4 is outside FUZZY_FAMILIES"
    assert refused.candidate(1).rejected_by == ("fuzzy_family_not_allowed",)  # type: ignore[union-attr]

    cjk = synthetic_catalog([(1, "香菇 200 克", "1.1", "[]")])
    assert resolve("香菰", cjk).match_method == "unresolved", "cross-script distance is noise"
    assert matcher._is_single_script("香菇") is True
    assert matcher._is_single_script("Shiitake") is True
    assert matcher._is_single_script("Shiitake 香菇") is False


def test_the_fuzzy_tolerance_is_refused_the_families_the_guards_refused() -> None:
    """A name whose only evidence is a rejected row gets no fuzzy fallback.

    `_allowed_families` is built from the rows the **segment rule** admitted, so
    a name refused everywhere has no allowed family and therefore no fuzzy
    acceptance. This is what stops the tolerance from being a third
    flavour-descriptor rule wearing a different hat — the one thing a fuzzy tier
    added to this ladder would otherwise be.
    """
    for raw, source in (("蒜", "调料"), ("芝麻", "调料"), ("土豆", "材料"), ("葱", "调料")):
        result = resolve(raw, CATALOG, source=source)
        assert result.match_method != "fuzzy_normalized", raw
        assert result.pantry_item_id is None, raw
        # …and the reason is structural rather than accidental: every one of them
        # is under `MIN_FUZZY_CHARS`, so the tolerance declines before it looks at
        # a single row. `蒜` is not one edit from `柴米 蒜香蒸茄子`; it is a flavour
        # descriptor of it, and no edit distance expresses that.
        assert len(normalize_ingredient(raw)) < matcher.MIN_FUZZY_CHARS, raw
    assert "4" not in matcher.FUZZY_FAMILIES, "the snack class is excluded on purpose"
    assert matcher.FUZZY_FAMILIES == frozenset({"1.1", "1.2", "2", "3"})


def test_the_fuzzy_tolerance_never_runs_on_raw_text() -> None:
    """`wikilink` and `emoji_alt` reach the ladder as their recovered name, and
    the tolerance sees only that name's product core.

    `[[Mackerel]]`, `Mackerel` and `🐟/Mackerel` resolve identically, which is
    the observable form of "it never runs on raw text": a rule that saw the
    brackets or the emoji would be comparing a different string.
    """
    for raw in ("Mackerel", "[[Mackerel]]", "🐟/Mackerel"):
        assert resolve(raw, CATALOG).pantry_item_id == 22, raw


# --- near misses -----------------------------------------------------------


def test_an_unresolved_value_offers_capped_unguarded_near_misses() -> None:
    """The repair surface: capped, deterministic, and deliberately unguarded.

    The rows a user most needs to *reject* are the ones the guards refused, so
    `土豆` offering `好丽友 呀!土豆 薯条` (62) is the feature working. A near miss
    never resolves anything, which is what makes offering it free.
    """
    result = resolve("土豆", CATALOG)
    assert result.match_method == "unresolved"
    assert result.pantry_item_id is None
    assert len(result.near_misses) == MAX_NEAR_MISSES
    assert result.near_misses[0].pantry_item_id == 62
    assert result.near_misses[0].relation == "contains"
    again = resolve("土豆", CATALOG)
    assert [m.pantry_item_id for m in again.near_misses] == [
        m.pantry_item_id for m in result.near_misses
    ], "the list is deterministic across runs"


def test_near_misses_never_cross_scripts() -> None:
    """A Chinese name is not offered a Latin row and vice versa.

    Distance alone is a bad rank for CJK — every three-character Chinese name is
    within three edits of every other one — so `Clam` would be offered
    `小白菜心`, which tells the user nothing at all.
    """
    latin = resolve("Clam", CATALOG, source="材料")
    assert latin.match_method == "unresolved"
    assert all(not matcher._has_cjk(m.canonical_name) for m in latin.near_misses)

    cjk = resolve("包菜", CATALOG, source="材料")
    assert cjk.match_method == "unresolved"
    assert cjk.near_misses
    assert all(matcher._has_cjk(m.canonical_name) for m in cjk.near_misses)


# --- the ordering properties ----------------------------------------------


def test_tier_2_beats_tier_7() -> None:
    """The ordering property §10.2 requires, in the direction that matters.

    A value tier 2 can resolve is **never** resolved by tier 7 — and the reverse
    is asserted too, because a ladder that only ever ran the strong tier first is
    indistinguishable from one that has no tiers at all.
    """
    sugar = synthetic_catalog([(1, "Sugar, 4 Lb", "1.1c", "[]")])
    for raw in ("Sugar, 4 Lb", "sugar, 4 lb", "  Sugar, 4 Lb "):
        result = resolve(raw, sugar, source="调料")
        assert result.match_method == "exact_name", raw
        assert result.match_tier == 2, raw
        assert result.match_method != "staples", raw
    assert resolve("糖", sugar, source="调料").match_method == "staples"


@pytest.mark.parametrize(
    ("raw", "tier", "method"),
    [
        ("[[id:4]]", 1, "exact_id"),
        ("Sugar, 4 Lb", 2, "exact_name"),
        ("八角", 3, "variant_alias"),
        ("Kale", 4, "note_basename"),
        ("Whole Star Anise", 5, "normalized_exact"),
        ("西兰花", 6, "synonym"),
        ("醋", 7, "staples"),
    ],
)
def test_every_rung_fires_on_its_own(raw: str, tier: int, method: str) -> None:
    """No rung is dead code, and each is pinned to its own number and method.

    The number is the stable identifier §9.8 says must never be renumbered, and
    the method is the string the API speaks. Both are asserted separately from
    the ordering test below, which is what makes a *renumbering* a failure rather
    than a rename.
    """
    source = "调料" if tier == 7 else "材料"
    # Tiers 1-3 and 5 need rows the committed catalog does not have (`Sugar, 4 Lb`
    # is synthetic, `八角` is the one real row with a `variants` alias the spec
    # does not name); tiers 4 and 6 need the committed 176.
    corpus = CATALOG if tier in (4, 6, 7) else SIMPLE
    result = resolve(raw, corpus, source=source)  # type: ignore[arg-type]
    assert result.match_tier == tier, raw
    assert result.match_method == method, raw
    assert TIER_METHOD[tier] == method
    assert result.confidence == TIER_CONFIDENCE[tier], raw


def test_tier_8_holds_both_the_fuzzy_acceptance_and_the_miss() -> None:
    """One number, two methods, and `pantry_item_id is None` is the miss test.

    §9.8's tier 8 is `unresolved`; the fuzzy tolerance is explicitly *not* a
    numbered rung. So both land on 8 and `match_method` is what tells them apart.
    Asserted so a later ticket that persists `match_tier` knows the number alone
    does not say "miss".
    """
    miss = resolve("Arugula", CATALOG)
    cinnamon = synthetic_catalog([(1, "Organic Ground Cinnamon 2 Oz", "2", "[]")])
    tolerance = resolve("Organic Ground Cimnamon", cinnamon)
    assert miss.match_tier == tolerance.match_tier == 8
    assert miss.match_method == "unresolved"
    assert tolerance.match_method == "fuzzy_normalized"
    assert miss.resolved is False and tolerance.resolved is True
    assert miss.confidence == 0.0
    assert miss.pantry_item_id is None


def test_tier_order_is_the_call_sequence_and_not_just_the_lookup_table() -> None:
    """The order is asserted against what `resolve_ingredient` *does*, not against
    the dictionaries the loop reads.

    `TIER_METHOD` and `TIER_CONFIDENCE` are data; a ladder that consulted tier 7
    before tier 2 while still labelling the result `exact_name` would pass every
    table assertion in this file. So the sequence of `finish(...)` calls in the
    function's own source is read and compared, which is the only observation
    that would notice a reordering.
    """
    source = ast.parse(Path(matcher.__file__).read_text(encoding="utf-8"))
    # Module-level `NAME: Final = <int>` so a rung written as a constant rather
    # than a literal is still read as a rung. A constant nobody can see in the
    # call sequence is a rung the ordering test would silently skip.
    constants: dict[str, int] = {
        node.target.id: node.value.value  # type: ignore[union-attr]
        for node in source.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, int)
    }
    ladder = next(
        node
        for node in source.body
        if isinstance(node, ast.FunctionDef) and node.name == "resolve_ingredient"
    )
    calls = sorted(
        (node for node in ast.walk(ladder) if isinstance(node, ast.Call)),
        key=lambda node: (node.lineno, node.col_offset),
    )
    visited: list[int] = []
    for call in calls:
        if not (isinstance(call.func, ast.Name) and call.func.id == "finish"):
            continue
        for argument in call.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, int):
                visited.append(argument.value)
            elif isinstance(argument, ast.Name) and argument.id in constants:
                visited.append(constants[argument.id])
    # Tier 5 has two accepted relations — `normalized_exact` and the token
    # `contains` — and therefore two `finish` sites, so the sequence is compared
    # on first occurrences. Both extra sites are visible in `visited` itself, so a
    # future reader can see why the list is not exactly nine long.
    assert [tier for index, tier in enumerate(visited) if tier not in visited[:index]] == [
        0,
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
    ], (
        "resolve_ingredient must reach finish() at tier 0, then 1-7 in order, then "
        "the fuzzy tolerance, then the miss — the order is the contract"
    )
    assert visited == [0, 1, 2, 3, 4, 5, 5, 6, 7, 8, 8], visited


def test_reordering_the_tiers_would_break_the_tier_2_before_tier_7_property() -> None:
    """The ordering property, *falsified*, so the assertion above means something.

    §9.8 forbids "fixing" near-zero tier-3 coverage by widening the synonym set
    or by deleting the tier. Either of those is a reordering in disguise, and the
    cheapest way to make an ordering property real is to show the corpus answers
    differently when the order changes. A `材料` value that is a live catalog row
    **and** a staples entry is the only shape where the order is observable, and
    `Sugar, 4 Lb` / `糖` is that shape.
    """
    sugar = synthetic_catalog([(1, "Sugar, 4 Lb", "1.1c", "[]")])
    assert resolve("Sugar, 4 Lb", sugar, source="调料").match_tier == 2

    # The same two tiers, visited the other way round, would answer differently —
    # which is exactly why the order is asserted from the source above.
    reordered = [7, 6, 5, 4, 3, 2, 1]
    assert reordered.index(7) < reordered.index(2)
    assert TIER_METHOD[2] == "exact_name" and TIER_METHOD[7] == "staples"
    assert TIER_CONFIDENCE[2] > TIER_CONFIDENCE[7], "the table is ordered too"


# --- the module's own obligations ------------------------------------------


def test_the_ladder_is_pure_and_reads_nothing() -> None:
    """No I/O, no clock, no environment, no database.

    Asserted over the module's imports rather than described, because "pure" is
    the kind of claim that survives a later `import sqlite3` added for a
    debugging convenience nobody removes.
    """
    imported: set[str] = set()
    for node in ast.walk(ast.parse(Path(matcher.__file__).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for forbidden in ("sqlite3", "os", "time", "pathlib", "socket", "urllib", "httpx"):
        assert forbidden not in imported, f"the matcher must not import {forbidden}"


def test_the_two_lexicon_files_are_the_only_ones_the_ladder_reads() -> None:
    """Both paths are inside the package, and both files load.

    `Path("app/recipes/lexicon/…")` would resolve fine while pytest runs from the
    repo root and be wrong from a wheel or from a LaunchAgent's working
    directory. The property that survives both is that the resolved path is the
    package's own directory, which is also what the `package-data` glob depends
    on.
    """
    package_root = Path(matcher.__file__).resolve().parent
    for source, name in (
        (matcher.SYNONYMS_SOURCE, "synonyms.yaml"),
        (matcher.STAPLES_SOURCE, "staples.yaml"),
    ):
        assert source.is_file(), source
        assert Path(source).resolve().is_relative_to(package_root), source
        assert Path(source).resolve().name == name
    assert matcher.SYNONYMS and matcher.STAPLES.staples


def test_the_ladder_never_persists_a_pantry_item_id_from_a_lexicon_file() -> None:
    """No value in any of the three lexicon files is a bare integer.

    `items.id` is `INTEGER PRIMARY KEY AUTOINCREMENT` and a re-import renumbers
    it, so a lexicon carrying one would silently point at a different product
    after the next ingest. The loaders refuse an all-digits name; this asserts
    the shipped files really are free of them rather than relying on the loader.
    """
    for entry in matcher.SYNONYMS:
        for name in entry.names:
            assert not name.isdigit(), name
    for name in matcher.STAPLES.staples:
        assert not name.isdigit(), name
    for suffix in matcher.STAPLES.class_suffixes:
        assert not suffix.isdigit(), suffix
    for line in matcher.SYNONYMS_SOURCE.read_text(encoding="utf-8").splitlines():
        assert re.fullmatch(r"\s*-\s*\d+\s*", line) is None, line


def test_in_stock_is_reported_and_never_decides_which_row_matched() -> None:
    """`stock` annotates the chip; it does not choose the row.

    Two ids for the same product is the live shape of this — the catalog
    re-ingests products under new ids — and the ladder must land on the same one
    whatever the stock signal says, or "in stock" would decide identity.
    """
    catalog = synthetic_catalog(
        [
            (1, "Organic Baby Kale 5 oz", "1.1", "[]"),
            (2, "Organic Baby Kale 5 oz", "1.1", "[]"),
        ]
    )
    parsed = parse_ingredient_value("Organic Baby Kale 5 oz")
    held = matcher.resolve_ingredient(parsed, corpus_stock(frozenset({2})), catalog)
    empty = matcher.resolve_ingredient(parsed, corpus_stock(frozenset()), catalog)
    neither = matcher.resolve_ingredient(parsed, None, catalog)
    assert held.pantry_item_id == empty.pantry_item_id == neither.pantry_item_id == 1
    assert held.in_stock is empty.in_stock is neither.in_stock is False
    assert held.candidate_ids() == (1, 2)


def test_both_candidates_of_a_duplicate_row_surface_in_the_audit() -> None:
    """Eight catalog keys hold more than one row, and that is load-bearing.

    A `dict[str, CatalogRow]` would pick a winner by insertion order and the
    loser would vanish with no diagnostic — the one failure a name lookup must
    never have. The ladder picks the lowest id and records the other, so the
    `调试` provenance view has something to show and ticket #11 has something to
    persist.
    """
    catalog = synthetic_catalog(
        [
            (1, "Organic Baby Kale 5 oz", "1.1", "[]"),
            (2, "Organic Baby Kale 5 oz", "1.1", "[]"),
        ]
    )
    result = resolve("Organic Baby Kale 5 oz", catalog)
    assert result.pantry_item_id == 1
    assert result.candidate_ids() == (1, 2)
    assert result.candidate(2).rejected_by == ()  # type: ignore[union-attr]
    assert result.candidate(1).rejected_by == ()  # type: ignore[union-attr]


# --- F15, the emoji run, as the ladder consumes it ------------------------


def test_the_three_codepoint_zwj_run_reaches_the_ladder_as_one_unit() -> None:
    """`🍋‍🟩` is `U+1F34B U+200D U+1F7E9` — one ZWJ sequence meaning lime.

    A Unicode property class matches the three **parts**, not the run, and
    matching them separately leaves garbage text behind. So the name the ladder
    compares is `青柠` and nothing else, and `青柠` resolves to nothing because the
    catalog has no lime — the outcome the spec pins, asserted here so a future
    implementer does not "helpfully" let it reach a `柠檬` row.
    """
    lime = "\U0001F34B\u200d\U0001F7E9"
    assert EMOJI_NAMES[lime] == "青柠"
    assert len(lime) == 3
    assert [f"U+{ord(character):04X}" for character in lime] == [
        "U+1F34B",
        "U+200D",
        "U+1F7E9",
    ]
    parsed = parse_ingredient_value(lime)
    assert parsed.parse_method == "emoji_only"
    assert parsed.parsed_name == "青柠"

    result = matcher.resolve_ingredient(parsed, None, CATALOG, source="调料")
    assert result.parsed_name == "青柠"
    assert result.match_method == "unresolved"
    assert result.pantry_item_id is None
    # The list may hold three-edit CJK suggestions, but none of them is a lemon:
    # that is the substitution §9.8 forbids, and the only way it could happen is a
    # `contains` rule spanning the two entries.
    assert all("柠檬" not in miss.canonical_name for miss in result.near_misses)
    assert all(matcher._has_cjk(miss.canonical_name) for miss in result.near_misses)


def test_the_corrected_emoji_only_lists_reach_the_ladder_as_the_spec_states() -> None:
    """6 from `材料` and 3 from `调料`, and `🍔` in neither.

    §3.2's evidence correction: the transcription this was first reported with
    was wrong — `🍔` appears in no note and `🥔` is the value that was meant.
    Asserted against the frozen notes rather than repeated, so a stale list
    cannot survive here.
    """
    materials = real_values_for("材料")
    seasonings = real_values_for("调料")
    for emoji in ("🥦", "🥚", "🍚", "🥔", "🍠", "🍅"):
        assert emoji in materials, emoji
    for emoji in ("🧄", "🫚", "🍋‍🟩"):
        assert emoji in seasonings, emoji
    assert "🍔" not in materials and "🍔" not in seasonings
    assert "🌶️" not in materials, "🌶️ only occurs as 🌶️/Shishito, which names itself"
    for emoji in ("🥔", "🥦", "🍠", "🥚", "🍚"):
        assert parse_ingredient_value(emoji).parse_method == "emoji_only", emoji


# --- the corpus the reported numbers are quoted against -------------------


def test_the_corpus_is_the_one_the_d1_measurements_were_taken_on() -> None:
    """178 rows, 176 candidates, 16 recipes, 26 raw `材料` values, 19 `调料`.

    Every number D1 quotes is measured against these and so is every number this
    ticket reports. Asserting the shape here means a re-import that changed it
    fails as a *shape* failure rather than as a mysterious expectation mismatch
    in a measurement test.
    """
    assert CATALOG.total_row_count == TOTAL_CATALOG_ROWS
    assert CATALOG.excluded_row_count == 2
    assert len(CATALOG.rows) == CANDIDATE_ROWS
    assert len(real_notes()) == RECIPE_COUNT
    assert len(real_values_for("材料")) == RAW_MATERIAL_VALUES
    assert len(real_names_for("材料")) == PARSED_MATERIAL_NAMES
    assert len(real_names_for("调料")) == SEASONING_NAMES


def test_the_result_dataclass_is_immutable_and_the_guards_default_on() -> None:
    """A `MatchResult` can be cached, so nothing about it may move afterwards."""
    result = resolve("Sugar, 4 Lb", SIMPLE)
    with pytest.raises(Exception):  # noqa: B017 - a frozen dataclass raises FrozenInstanceError
        result.pantry_item_id = 2  # type: ignore[misc]
    assert isinstance(result, matcher.MatchResult)
    assert DEFAULT_GUARDS.segment_boundary is True
    assert DEFAULT_GUARDS.category_family is True

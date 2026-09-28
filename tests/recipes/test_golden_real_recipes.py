"""The golden matcher test: the CI anchor for D1, and the frozen expectation.

Run alone:  .venv/bin/python -m pytest tests/recipes/test_golden_real_recipes.py -q

**What this file is for.** D1 accepted a low ceiling on purpose — 7 of 25 distinct
`材料` names resolve, and a miss is the right answer far more often as a match
is. The risk that creates is not the ceiling; it is a *silent* change to it. A
matcher edit that quietly re-pointed `空心菜` at a different Pantry Item, or that
turned `Clam` into a chip claiming the user has clams, would change what the app
tells somebody they can cook and would break no other test in this repository. So
the whole outcome of the 16 real notes is frozen in
`tests/fixtures/golden_match_results.json` and compared exactly.

**The comparison is against the store, not against the ladder.** Every `材料`
record in the fixture was read back out of `ingredient_mappings` through
`IngredientMappingStore.rows_for()` after a real `ensure_rows()` + `resolve_all()`,
so the anchor covers the parser, the eight-tier ladder, the `candidates_json`
codec, F2's per-slot recovery and the slot index — not just the pure function in
the middle. `tests/recipes/golden.py` is the single implementation of that
measurement and `scripts/snapshot_golden_match_results.py` is the only thing
that writes the file.

**Three things this file freezes that are NOT bugs, and must never be "fixed".**

1. **Tiers 0, 1, 2, 3 and 7 fire on zero `材料` values.** Tier 3 is the spec's
   near-dead tier (3 of 178 rows carry a `variants`, two of them just the long
   product name) and tier 7 is `调料`-only by design. D1 accepted a low ceiling;
   raising coverage by loosening a guard or widening the synonym set would trade
   away the thing this repository is protecting — a miss renders a chip, a false
   positive renders a chip claiming you have something you do not.
2. **`空心菜 → 83` is tier 6 `synonym` at confidence 0.7, not tier 5
   `normalized_exact`.** §10.3's example fixture records the tier-5 string. That
   is not reachable: tier 4's CJK-remainder rule refuses `空心菜` → `空心菜嫩苗`
   (the remainder `嫩苗` is two ideographs) and tier 5 demands equality. The id is
   reached through the synonym set, whose entry names `空心菜` exactly. The truth
   is frozen here and the spec correction is reported, not applied — neither
   `normalize.py` nor a guard is touched to make the spec's string appear.
3. **`Kale → 56` (`Kale Microgreens`), not `Kale → 10` (`Organic Baby Kale`).**
   Tier 4's prefix relation pre-empts tier 6, and the specified order gives the
   *worse* of two acceptable answers. It is frozen with a comment for that
   reason: a future "improvement" that made it pick 10 would be a behaviour
   change, and this is where it would have to be made deliberately.

**The guard relationship is wired explicitly, not left to two suites.** §3.2's
must-not-match cases are imported from `test_must_not_match.py` and asserted
against the fixture's own audit trail, so a guard regression cannot be recorded
in a re-freeze as if it were a new fact about the vault.

**Nothing here reads a live path.** The catalog is the committed 178-row JSON
snapshot, the recipes are the committed `real_recipes/*.md`, and the only database
is the `tmp_path` tree the root `conftest.py` builds.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import shutil
import subprocess
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, Final

import pytest

from app.config import Settings
from app.db.database import connect_db, init_db
from app.mapping.store import IngredientMappingStore
from app.pantry.catalog import build_snapshot
from app.recipes.ingredients import parse_ingredient_value
from app.recipes.matcher import (
    FUZZY_METHOD,
    TIER_CONFIDENCE,
    TIER_METHOD,
    MatchGuards,
    resolve_ingredient,
)

from . import golden
from .corpus import (
    CATALOG_SNAPSHOT,
    REAL_RECIPES,
    committed_catalog,
    real_notes,
    synthetic_catalog,
)
from .test_must_not_match import (
    NO_FAMILY_GUARD,
    NO_SEGMENT_GUARD,
    REVIEWED_GUARD_REFUSALS,
    SPEC_ROWS,
)

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
GOLDEN: Final = golden.GOLDEN_PATH

#: The nine resolved `材料` slots, as a table rather than as prose in a comment,
#: so a reviewer reads the frozen baseline in the same place they read the code:
#: `(recipe, slot index, raw value, Pantry Item id, tier, method, confidence)`.
#:
#: `空心菜 → 83` is the ticket's named case and appears three times, once per note
#: that writes it. `Kale → 56` is the specified-order artefact (module docstring).
#: Nothing else resolves, and that is the ceiling D1 accepted.
FROZEN_RESOLVED: Final[tuple[tuple[str, int, str, int, int, str, float], ...]] = (
    ("Easy Fragrant Fried Rice", 0, "🥦", 14, 6, "synonym", 0.7),
    ("微波菜菜", 0, "Kale", 56, 4, "note_basename", 0.9),
    ("拌空心菜", 0, "空心菜", 83, 6, "synonym", 0.7),
    ("炒红苋菜", 0, "红苋菜", 139, 6, "synonym", 0.7),
    ("烤鲭鱼", 0, "[[Mackerel]]", 22, 5, "normalized_exact", 0.85),
    ("烤鸡翅", 0, "🐔/鸡翅", 166, 6, "synonym", 0.7),
    ("煮菜菜", 0, "空心菜", 83, 6, "synonym", 0.7),
    ("煮菜菜", 1, "茼蒿", 70, 6, "synonym", 0.7),
    ("花蛤拌饭", 3, "空心菜", 83, 6, "synonym", 0.7),
)

#: The five notes whose every `材料` resolves. D1's table says 3 of 16, measured
#: over a three-tier join; the shipped eight-tier ladder reaches 5. The measured
#: number is the frozen one, and the discrepancy is reported rather than
#: reconciled by weakening a tier to land on D1's figure.
FROZEN_FULLY_COOKABLE: Final[tuple[str, ...]] = (
    "拌空心菜",
    "炒红苋菜",
    "烤鲭鱼",
    "烤鸡翅",
    "煮菜菜",
)

#: `空心菜`'s Pantry Item — the id the ticket names — and the three notes that
#: write it. F2's whole uniqueness-*direction* argument is this one id in three
#: different `recipe_note` values, so it is asserted here by name.
KONGXINCAI: Final = 83
KONGXINCAI_RECIPES: Final[tuple[str, ...]] = ("拌空心菜", "煮菜菜", "花蛤拌饭")

#: The two names several ticket texts claim resolve and which do not. Both are
#: frozen as misses, with the reason, because "a synonym entry exists" is not the
#: same claim as "a catalog row answers to it".
#:
#: * `Clam` — the segment-boundary guard refuses both `YABA 花蛤肉` (143) and
#:   `Umji's 蛤蜊拌饭套餐` (161), because `花蛤` / `蛤蜊` are fragments of longer
#:   words rather than whole segments. §9.8's answer here is a miss, and the
#:   guard's whole purpose is to produce it.
#: * `娃娃菜` — the synonym set has an entry and no catalog row answers to it.
#:   D1's ceiling says that is a miss and not a bug.
CLAIMED_RESOLVE_BUT_DOES_NOT: Final[tuple[str, ...]] = ("Clam", "娃娃菜")

#: §9.13.1's four headline forms as literal strings, quoted from
#: `tests/js/logic/format.test.mjs` (F16, F17, #19). They are repeated here as
#: whole lines rather than as templates so a change to the em dash, the separator,
#: or the width of the gap before the strict addendum fails in a Python suite as
#: well as a JS one. `golden.FROZEN_HEADLINE_FORMS` holds the templates they come
#: from and `test_the_four_frozen_forms_are_the_specs` ties the two together.
FROZEN_HEADLINE_LINES: Final[tuple[str, ...]] = (
    "6/6 ingredients found",
    "4/6 ingredients found — missing: 香菇, 娃娃菜",
    "6/7 ingredients found   (严格模式（含调料）: 生抽)",
    "2/5 ingredients found — missing: Clam, 香菇   (严格模式（含调料）: 生抽)",
    # The `0/3` form is the second shape with a zero numerator, asserted as such
    # rather than as a fifth: the denominator tracks the slot count, which is
    # F17's claim and the reason this line is here at all.
    "0/3 ingredients found — missing: 空心菜, 香菇, 娃娃菜",
)


def _run[T](scenario: Coroutine[Any, Any, T]) -> T:
    """Run one coroutine to completion, on a fresh loop per call.

    A fresh loop per call rather than one session-scoped loop, as in
    `tests/mapping/test_store.py`: it is what makes a store that cached a
    loop-bound object fail here instead of working in a test and deadlocking in a
    request.
    """
    return asyncio.run(scenario)


@pytest.fixture
def measured(settings: Settings) -> dict[str, Any]:
    """The committed corpus as the shipped code resolves it, through the store."""
    return _run(golden.measure(settings))


@pytest.fixture
def committed() -> dict[str, Any]:
    """The frozen expectation, decoded."""
    return golden.load(GOLDEN)


#: The two keys the **render layer** owns rather than the matcher. They are
#: compared separately, against §9.13.1's four forms, because they are produced
#: by `format.js` through node and the end-to-end comparison below must not
#: depend on node being installed.
HEADLINE_KEYS: Final = ("headline", "headlineStrict")


def _expectation(committed: dict[str, Any]) -> dict[str, Any]:
    """The committed file as a *matcher* expectation: no date, no headline.

    `generatedAt` is a date rather than an expectation, and the two headline
    strings are `format.js`'s output rather than the ladder's — so both are
    dropped here and asserted on their own, where the failure can say which of
    the two halves moved.
    """
    return {
        "schemaVersion": committed["schemaVersion"],
        "generatedFrom": committed["generatedFrom"],
        "summary": committed["summary"],
        "recipes": {
            name: {key: value for key, value in recipe.items() if key not in HEADLINE_KEYS}
            for name, recipe in committed["recipes"].items()
        },
    }


def _material(committed: dict[str, Any], recipe: str, index: int) -> dict[str, Any]:
    """One `材料` slot of the frozen fixture, by recipe name and index."""
    slot: dict[str, Any] = committed["recipes"][recipe]["materials"][index]
    return slot


def _frozen_materials(committed: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every `材料` slot keyed by its parsed name, last write winning.

    A name that appears in two notes resolves identically — the ladder reads the
    parsed name and nothing else — so the flattening loses nothing. `香菇` and
    `🍄/香菇` are one food written two ways and land on the same key, which is
    the point of keying on the name.
    """
    return {
        str(slot["parsedName"] or slot["rawValue"]): slot
        for recipe in committed["recipes"].values()
        for slot in recipe["materials"]
    }


# --- the anchor -------------------------------------------------------------


def test_the_committed_golden_file_is_exactly_what_the_matcher_produces(
    measured: dict[str, Any], committed: dict[str, Any]
) -> None:
    """The whole comparison, with a diff rather than a bare `==`.

    A plain equality failure on a 2,000-line fixture prints nothing a human can
    act on, and the whole point of this test is that a human *should* act. So the
    committed file and the measured payload are both re-encoded through
    `golden.encode()` and compared as a unified diff — the same artifact a
    reviewer reads when they run the regeneration script.
    """
    expected = _expectation(committed)
    actual = measured
    if expected == actual:
        return
    rendered = list(
        difflib.unified_diff(
            golden.encode(expected, "").splitlines(),
            golden.encode(actual, "").splitlines(),
            fromfile="committed",
            tofile="measured",
            lineterm="",
            n=2,
        )
    )
    pytest.fail(
        "the matcher no longer produces the frozen expectation. If this is a "
        "deliberate change, re-freeze it with "
        "`python scripts/snapshot_golden_match_results.py --regenerate` and review "
        "the diff:\n" + "\n".join(rendered[:160])
    )


def test_the_frozen_baseline_is_nine_resolved_and_twenty_three_unresolved(
    measured: dict[str, Any],
) -> None:
    """D1's ceiling, measured over the committed corpus and frozen.

    32 `材料` slots, 176 candidate rows, 16 notes: **9 resolved, 23 unresolved, 0
    conflicts, 0 stale resets**. Read off the store's own `ResolveReport` rather
    than recounted from the rows, because the report is what `/api/recipes`
    publishes and a golden test that recomputed it would be asserting a second
    derivation of the same number.
    """
    report = measured["summary"]["resolveReport"]
    assert report == {
        "reconsidered": 32,
        "resolved": 9,
        "stillUnresolved": 23,
        "staleReset": 0,
        "duplicateSlotConflicts": 0,
        "conflicts": [],
    }
    assert report["resolved"] + report["stillUnresolved"] == report["reconsidered"]


def test_the_nine_resolved_slots_are_frozen_one_by_one(committed: dict[str, Any]) -> None:
    """Every resolved slot, by recipe and index, with its tier and confidence.

    Written out as a table so "which nine" is a reviewable list rather than a
    count, and so a change to *which* nine is as loud as a change to how many.
    Each slot's confidence is also checked against §9.8's number for its tier, so
    a value relabelled onto a different rung without its confidence moving fails
    here too.
    """
    found: list[tuple[str, int, str, int, int, str, float]] = []
    for name, recipe in sorted(committed["recipes"].items()):
        for slot in recipe["materials"]:
            if slot["pantryItemId"] is None:
                continue
            found.append(
                (
                    name,
                    slot["index"],
                    slot["rawValue"],
                    slot["pantryItemId"],
                    slot["matchTier"],
                    slot["matchMethod"],
                    slot["confidence"],
                )
            )
    assert tuple(found) == FROZEN_RESOLVED
    for _name, _index, _raw, _item_id, tier, method, confidence in FROZEN_RESOLVED:
        assert TIER_CONFIDENCE[tier] == confidence, (tier, method)


def test_every_material_and_seasoning_slot_is_frozen_with_its_whole_answer(
    committed: dict[str, Any],
) -> None:
    """Per slot: the parse, the tier, the method, the confidence, the audit trail.

    The AC asks for every slot of every note, and this is the assertion that
    makes "every" mean something: the keys are exactly the frozen ones, the
    indices are `0..n-1` with no gap, and **both** frontmatter keys are present
    for every note — so a note that grew or lost a `材料` fails rather than
    quietly shifting every later index. `candidates_json`'s `reason` /
    `rejectedBy` split is checked per entry, because F2 fixes it and an audit
    trail that recorded only the first objection would understate what is
    holding a row back.
    """
    required = {
        "index",
        "rawValue",
        "parseMethod",
        "parsedName",
        "matchMethod",
        "matchTier",
        "pantryItemId",
        "confidence",
        "candidates",
    }
    material_slots = 0
    seasoning_slots = 0
    for name, recipe in committed["recipes"].items():
        assert set(recipe) == {
            "notePath",
            "materials",
            "seasonings",
            "materialsFound",
            "materialsTotal",
            "seasoningsFound",
            "seasoningsTotal",
            "missingMaterials",
            "missingSeasonings",
            "allMaterialsResolved",
            "duplicateMaterialItemIds",
            "headline",
            "headlineStrict",
        }, name
        for group in ("materials", "seasonings"):
            slots = recipe[group]
            assert [slot["index"] for slot in slots] == list(range(len(slots))), (name, group)
            for slot in slots:
                assert set(slot) == required, (name, group, slot["index"])
                assert slot["rawValue"], (name, group, slot["index"])
                assert slot["parseMethod"], (name, group, slot["index"])
                assert isinstance(slot["matchTier"], int)
                assert isinstance(slot["confidence"], float)
                assert slot["pantryItemId"] is None or isinstance(slot["pantryItemId"], int)
                # `staples` is a class assumption and names no Pantry Item, so a
                # `None` id is `unresolved` *or* `staples` — never anything else.
                assert (slot["pantryItemId"] is None) == (
                    slot["matchMethod"] in {"unresolved", "staples"}
                ), (name, slot)
                for candidate in slot["candidates"]:
                    assert set(candidate) == set(golden.CANDIDATE_KEYS), (
                        name,
                        group,
                        slot["index"],
                        candidate,
                    )
                    assert candidate["canonicalName"], candidate
                    assert candidate["family"], candidate
                    assert isinstance(candidate["rejectedBy"], list)
                    if candidate["rejectedBy"]:
                        assert candidate["reason"] == candidate["rejectedBy"][0], candidate
                    else:
                        assert candidate["reason"] == "matched", candidate
        material_slots += len(recipe["materials"])
        seasoning_slots += len(recipe["seasonings"])
    assert material_slots == 32
    assert seasoning_slots == 39
    assert len(committed["recipes"]) == 16
    assert committed["schemaVersion"] == golden.SCHEMA_VERSION
    assert committed["generatedAt"], "the freeze records when it was written"


def test_the_seasoning_half_is_never_counted_as_an_ingredient(
    committed: dict[str, Any],
) -> None:
    """34 of 39 `调料` slots are found **under 严格模式 only**, and none names an id.

    The store materializes `材料` only — `app.mapping.store`'s docstring is
    explicit that its `ingredient_index` is a `材料` index and that a
    staples-satisfied Seasoning must never be recorded as one — so the golden
    file resolves the `调料` half through the ladder with `source="调料"`. The
    consequence worth freezing is that F16's "assumed on hand" is a *class
    assumption*: `staples` names no Pantry Item, so a Seasoning can never make a
    recipe cookable on its own.
    """
    for name, recipe in committed["recipes"].items():
        for slot in recipe["seasonings"]:
            assert slot["pantryItemId"] is None, (name, slot)
            assert slot["matchMethod"] in {"staples", "unresolved"}, (name, slot)
    assert sum(r["seasoningsFound"] for r in committed["recipes"].values()) == 34
    seasonings = committed["summary"]["seasoningNames"]
    assert len(seasonings) == 19
    assert sum(1 for method in seasonings.values() if method == "staples") == 17
    # The two misses are the two §10.3 predicted: sesame is a seed and lime does
    # not exist in the catalog, and letting either reach a neighbour is the
    # substitution §9.8 forbids.
    assert seasonings["芝麻"] == "unresolved"
    assert seasonings["青柠"] == "unresolved"
    assert {name for name, m in seasonings.items() if m == "unresolved"} == {"芝麻", "青柠"}


# --- the named assertions the ticket calls out ------------------------------


def test_kongxincai_resolves_to_83_in_all_three_notes_that_write_it(
    committed: dict[str, Any],
) -> None:
    """`空心菜 → 83`, by name, in three recipes — the ticket's named assertion.

    And the tier is asserted too, because the *id* is what the ticket names and
    the *tier* is where the spec and the shipped ladder disagree. §10.3's example
    fixture records `"matchMethod": "normalized_exact"` for this slot. That is
    not reachable: tier 4's CJK-remainder rule refuses `空心菜` → `空心菜嫩苗`
    (the unmatched remainder `嫩苗` is two ideographs, and §9.8 admits only a
    remainder holding no CJK), and tier 5's equality half demands the whole
    basename. The id is reached at **tier 6 `synonym`, confidence 0.7**, through
    the entry that names `空心菜` exactly.

    The truth is frozen here and the correction is reported to the spec's owner.
    Neither `normalize.py` nor a guard is touched to make the spec's string
    appear: tier 4's remainder rule is what refuses `芝麻` → `芝麻烧饼` and `蒜` →
    `蒜香蒸茄子`, and loosening it to rescue one slot would delete the guard that
    stops every live collision in this corpus.
    """
    for name in KONGXINCAI_RECIPES:
        slots = [
            slot
            for slot in committed["recipes"][name]["materials"]
            if slot["parsedName"] == "空心菜"
        ]
        assert len(slots) == 1, name
        slot = slots[0]
        assert slot["rawValue"] == "空心菜"
        assert slot["pantryItemId"] == KONGXINCAI, (name, slot)
        assert slot["matchTier"] == 6
        assert slot["matchMethod"] == "synonym"
        assert slot["confidence"] == 0.7
        # The audit trail names the row it adopted, and the relation is tier 4's
        # `basename_prefix` rather than the tier-6 `synonym_exact` a reader might
        # expect: `_merge_hit` keeps the *strongest* sighting, the exact-equality
        # one, and the relation it recorded is the one that justified the match.
        adopted = [c for c in slot["candidates"] if c["pantryItemId"] == KONGXINCAI]
        assert len(adopted) == 1, (name, slot["candidates"])
        assert adopted[0]["canonicalName"] == "空心菜嫩苗 0.95-1.05 磅"
        assert adopted[0]["rejectedBy"] == [], (
            "a row the ladder adopted must not keep the objections it raised on the way"
        )
    catalog = committed_catalog()
    assert catalog.by_id[KONGXINCAI].canonical_name == "空心菜嫩苗 0.95-1.05 磅"


def test_kale_picks_the_microgreens_and_that_worse_answer_is_frozen_on_purpose(
    committed: dict[str, Any],
) -> None:
    """`Kale → 56` (`Kale Microgreens`), not 10 (`Organic Baby Kale`).

    Both are kale and both are in the catalog; tier 4's prefix relation matches
    `Kale Microgreens` first and pre-empts the synonym entry that would have
    reached `Organic Baby Kale`. The specified order is doing exactly what it
    says, and here it gives the *worse* of two acceptable answers. It is frozen
    **with this comment** because a future implementer who preferred the other
    answer would otherwise be making a silent behaviour change to what the user
    is told they have — and the symptom would be one chip, in one recipe, that
    nobody would think to look for.
    """
    slot = _material(committed, "微波菜菜", 0)
    assert slot["rawValue"] == "Kale"
    assert slot["pantryItemId"] == 56
    assert slot["matchTier"] == 4
    assert slot["matchMethod"] == "note_basename"
    assert slot["confidence"] == 0.9
    catalog = committed_catalog()
    assert catalog.by_id[56].canonical_name == "AeroFarms Kale Microgreens, 2 OZ"
    assert catalog.by_id[10].canonical_name == "Whole Foods Market, Organic Baby Kale, 5 oz"
    # The other row is not *refused* — the ladder stopped before tier 6 ever
    # looked at it, which is what "pre-empted" means here.
    result = resolve_ingredient(parse_ingredient_value("Kale"), None, catalog)
    assert result.pantry_item_id == 56
    assert result.candidate_ids() == (56,)


def test_clam_and_waxxie_cabbage_stay_unresolved_and_the_audit_says_why(
    committed: dict[str, Any],
) -> None:
    """The two misses several ticket texts claim resolve, frozen as misses.

    * `Clam` — the segment-boundary guard refuses **both** `YABA 花蛤肉` (143)
      and `Umji's 蛤蜊拌饭套餐` (161): `花蛤` and `蛤蜊` are fragments of longer
      words, not whole segments. The guard stops every live collision in this
      corpus, and `Clam` is where it is load-bearing on a name the user actually
      wrote.
    * `娃娃菜` — the synonym set has an entry for it and no catalog row answers
      to it. "A synonym entry exists" is not the same claim as "a row exists".

    §13.8 / R1's instruction is the reason this is asserted rather than left to a
    count: widening the synonym set to make coverage look better, or loosening
    the guard to admit 143 and 161, would each raise the number, and each is the
    failure D1 was written to prevent.
    """
    clam = _material(committed, "花蛤拌饭", 0)
    assert clam["rawValue"] == "Clam"
    assert clam["pantryItemId"] is None
    assert clam["matchMethod"] == "unresolved"
    assert clam["matchTier"] == 8
    assert clam["confidence"] == 0.0
    refused = {c["pantryItemId"]: c for c in clam["candidates"]}
    assert set(refused) == {143, 161}
    for row_id in (143, 161):
        assert refused[row_id]["rejectedBy"] == ["segment_boundary"], refused[row_id]
        assert refused[row_id]["reason"] == "segment_boundary"
        assert refused[row_id]["canonicalName"], refused[row_id]

    waxxie = _material(committed, "微波菜菜", 2)
    assert waxxie["rawValue"] == "娃娃菜"
    assert waxxie["pantryItemId"] is None
    assert waxxie["matchMethod"] == "unresolved"
    assert waxxie["candidates"] == [], "no row answers to it, so there is nothing to audit"
    for name in CLAIMED_RESOLVE_BUT_DOES_NOT:
        assert _frozen_materials(committed)[name]["pantryItemId"] is None, name

    # The near-miss list is the repair path, not a resolution: whatever it offers
    # still resolves to nothing.
    result = resolve_ingredient(
        parse_ingredient_value("娃娃菜"), None, committed_catalog()
    )
    assert result.pantry_item_id is None
    assert result.near_misses, "a miss must still be repairable by one tap"


# --- the ceiling: the tiers that never fire, and why that is correct --------


def test_no_tier_that_never_fires_here_starts_firing(committed: dict[str, Any]) -> None:
    """Tiers 0, 1, 2, 3 and 7 fire on **zero** `材料` values, and that is the test.

    Written as its own assertion rather than left to the fixture comparison,
    because the failure it guards against is the one a re-freeze would hide: a
    future change that made, say, tier 3 fire would be *absorbed* by a casual
    regeneration and read as an improvement. Asserted as an expected result, with
    the reason for each, so a reader who sees 0/0 knows what to do (nothing) and a
    reader who sees 1 knows to ask why.

    * tier 0 `unparseable` — every value in the corpus parses; §9.6's five shapes
      cover all of them.
    * tier 1 `exact_id` — no note spells out a Pantry Item id, and `CONTEXT.md`
      defines Pantry Item Alias as a *name*, so this corpus has none.
    * tier 2 `exact_name` — no note's name is a whole `canonical_name`; the
      catalog holds branded, sized product names.
    * tier 3 `variant_alias` — the spec's near-dead tier. 3 of 178 rows carry a
      non-empty `variants` and two of those are just the long product name. It
      stays because `CONTEXT.md` defines Pantry Item Alias in terms of
      `variants` and a future re-import could populate it. **Do not loosen it,
      do not widen the synonym set to compensate, do not delete it.**
    * tier 7 `staples` — a `材料` is never assumed on hand. That is
      `app.mapping.store`'s `source="材料"` and the matcher's safe default, and it
      is why no `材料` slot in this file is `staples`.
    """
    tiers = committed["summary"]["tierFireCountsByDistinctMaterialName"]
    assert golden.DEAD_TIERS == (0, 1, 2, 3, 7)
    for tier in golden.DEAD_TIERS:
        assert tiers[str(tier)] == 0, (
            f"tier {tier} ({TIER_METHOD.get(tier, 'unparseable')}) started firing on a "
            "材料 value; that is a behaviour change, not an improvement to absorb"
        )
    # The §9.7 fuzzy tolerance is tier 8's other method and is *also* zero: a
    # fuzzy adoption is a real resolution, and this corpus produces none.
    assert committed["summary"]["methodFireCountsByDistinctMaterialName"][FUZZY_METHOD] == 0
    # The tiers that do fire, so the assertion above is not a vacuous "nothing
    # fires anywhere".
    assert tiers == {"0": 0, "1": 0, "2": 0, "3": 0, "4": 1, "5": 1, "6": 5, "7": 0, "8": 18}


def test_variant_alias_resolving_nothing_is_an_assertion_not_a_bug(
    committed: dict[str, Any],
) -> None:
    """§10.3's explicit instruction, as its own named test.

    "`variant_alias` (tier 3) resolving nothing is itself an assertion." Recorded
    so a future reader who sees 0/0 tier-3 coverage in the fixture does not "fix"
    it by loosening the tier, by widening the synonym set to compensate, or by
    deleting the tier. Tier 3 exists because `CONTEXT.md` defines Pantry Item
    Alias in terms of `variants`.
    """
    methods = committed["summary"]["methodFireCountsByDistinctMaterialName"]
    assert methods["variant_alias"] == 0
    assert not any(
        slot["matchMethod"] == "variant_alias"
        for recipe in committed["recipes"].values()
        for group in ("materials", "seasonings")
        for slot in recipe[group]
    )
    # The data that makes it near-dead, asserted so the claim is checkable: three
    # populated `variants` in the 176-row candidate universe, out of 178 rows.
    catalog = committed_catalog()
    assert [row.id for row in catalog.rows if row.variants] == [52, 55, 169]
    assert len(catalog.rows) == 176
    assert catalog.total_row_count == 178


def test_the_miss_rate_is_measured_not_quoted(committed: dict[str, Any]) -> None:
    """The measured 72%, and D1's 58% is not reproducible from this corpus.

    D1's table claims 58% of distinct `材料` values have no catalog match,
    obtained by a distinct-value join over exact name, `variants` alias and
    normalized-exact. Those three resolve **0 of 25** on the committed corpus —
    `test_no_tier_that_never_fires_here_starts_firing` is the arithmetic — so no
    three-tier join over these fixtures can produce 58%: three tiers that resolve
    nothing resolve 100% of the misses. The shipped ladder has eight tiers and
    reaches 7 of 25 names, so the honest figure is **18 of 25 = 72%**, worse than
    the number D1 recorded.

    The measured number is what is frozen. The discrepancy is reported rather than
    reconciled by loosening a guard until coverage looks like the spec's.
    """
    summary = committed["summary"]
    assert summary["distinctMaterialNames"] == 25
    assert summary["distinctMaterialNamesResolved"] == 7
    miss_rate = (
        summary["distinctMaterialNames"] - summary["distinctMaterialNamesResolved"]
    ) / summary["distinctMaterialNames"]
    assert miss_rate == pytest.approx(0.72)
    assert miss_rate != pytest.approx(0.58)


def test_five_of_sixteen_notes_have_every_material_resolved(committed: dict[str, Any]) -> None:
    """The per-recipe count, which is the number worth comparing against.

    D1's 3 of 16 was measured over a three-tier join; the shipped eight-tier
    ladder reaches 5. Frozen as measured. The five are all one- and two-slot
    notes, which is worth stating: no six-slot note in this corpus is fully
    cookable, and `Paradiso三明治` (0 of 6) is what a shopping-list-shaped note
    does to the ceiling.
    """
    fully = tuple(
        name
        for name, recipe in sorted(committed["recipes"].items())
        if recipe["allMaterialsResolved"]
    )
    assert fully == FROZEN_FULLY_COOKABLE
    assert len(fully) == 5
    sandwich = committed["recipes"]["Paradiso三明治"]
    assert (sandwich["materialsFound"], sandwich["materialsTotal"]) == (0, 6)
    # F17's `n/total` denominator is the `材料` slot count and nothing else, and
    # the numerator is the resolved count — no `调料` and no staples assumption.
    for recipe in committed["recipes"].values():
        assert recipe["materialsTotal"] == len(recipe["materials"])
        assert recipe["materialsFound"] == sum(
            1 for slot in recipe["materials"] if slot["pantryItemId"] is not None
        )
        assert recipe["allMaterialsResolved"] == (
            recipe["materialsFound"] == recipe["materialsTotal"]
        )


def test_every_distinct_material_name_is_frozen_with_the_slots_that_carry_it(
    committed: dict[str, Any],
) -> None:
    """Per **distinct** name, not only per slot, and the two must agree.

    32 slots carry 25 distinct names: `🍄/香菇` and `香菇` are one food written
    two ways, and the parse happens before the match. Keying the record by parsed
    name is sound only because the ladder reads the parsed name and nothing else,
    so a name that resolved two ways would make the record meaningless — which
    `measure()` refuses rather than averages, and which this asserts by
    re-deriving the map from the per-slot records.
    """
    by_name: dict[str, list[dict[str, Any]]] = {}
    for recipe in committed["recipes"].values():
        for slot in recipe["materials"]:
            by_name.setdefault(str(slot["parsedName"] or slot["rawValue"]), []).append(slot)
    assert len(by_name) == 25
    assert set(committed["summary"]["materialNames"]) == set(by_name)
    for name, slots in by_name.items():
        record = committed["summary"]["materialNames"][name]
        first = slots[0]
        assert record["matchMethod"] == first["matchMethod"], name
        assert record["matchTier"] == first["matchTier"], name
        assert record["pantryItemId"] == first["pantryItemId"], name
        assert record["confidence"] == first["confidence"], name
        assert len(record["slots"]) == len(slots), name
        # One food, two spellings, one answer.
        assert len({(s["matchMethod"], s["pantryItemId"]) for s in slots}) == 1, name
    assert len(by_name["香菇"]) == 3, "🍄/香菇 and 香菇 are one food written two ways"
    assert {s["rawValue"] for s in by_name["香菇"]} == {"🍄/香菇", "香菇"}
    assert by_name["香菇"][0]["pantryItemId"] is None, "and neither spelling resolves"
    assert sorted(committed["summary"]["materialNames"]) == sorted(by_name)


# --- F2: the constraint is not load-bearing, and the fixture says so --------


def test_no_recipe_lists_the_same_pantry_item_twice_and_no_pass_conflicted(
    committed: dict[str, Any],
) -> None:
    """F2's constraint, proven *not* load-bearing by the committed corpus.

    §10.3 asks the fixture to record, per recipe, that no recipe has two `材料`
    slots on the same `pantry_item_id`. Every recipe's `duplicateMaterialItemIds`
    is therefore **empty**, and `ResolveReport.duplicate_slot_conflicts` is 0 —
    which is what makes F2's per-slot catch a mechanism for a data defect this
    corpus does not have rather than a load-bearing part of every request.

    If a regeneration ever produces a recipe where it *is* true, that is a new
    fact about the vault: it must be called out in the regeneration commit by
    name, and the honest answer is that F2 keeps the app honest about it rather
    than failing. The assertions below are what make that event loud.
    """
    for name, recipe in committed["recipes"].items():
        assert recipe["duplicateMaterialItemIds"] == [], (
            name,
            recipe["duplicateMaterialItemIds"],
        )
        ids = [s["pantryItemId"] for s in recipe["materials"] if s["pantryItemId"] is not None]
        assert len(ids) == len(set(ids)), (name, ids)
    report = committed["summary"]["resolveReport"]
    assert report["duplicateSlotConflicts"] == 0
    assert report["conflicts"] == []


# --- the guards: wired to the must-not-match suite, and mutation-tested ------


def test_the_must_not_match_refusals_are_exactly_the_frozen_ones(
    committed: dict[str, Any],
) -> None:
    """The two suites are wired together, so neither can go green over the other.

    `test_must_not_match.py` proves the guards hold, on synthetic rows and on
    individual live rows, and keeps its own reviewed list of every guard refusal
    the corpus produces. This asserts the **same list** out of the golden file's
    own audit trail — set equality in both directions, the discipline that file
    uses — so a guard that stopped refusing a row cannot be recorded in a
    re-freeze as a new fact about the vault, and a reviewed row that stopped
    occurring fails in both suites at once.

    The import is the wiring. Without it the two suites would each be right about
    their own inputs and a guard regression would have to be noticed twice,
    independently, by two people.
    """
    refused: set[tuple[str, int]] = set()
    for recipe in committed["recipes"].values():
        for group in ("materials", "seasonings"):
            for slot in recipe[group]:
                name = str(slot["parsedName"] or slot["rawValue"])
                for candidate in slot["candidates"]:
                    if candidate["rejectedBy"]:
                        refused.add((name, candidate["pantryItemId"]))
    assert refused == REVIEWED_GUARD_REFUSALS, (
        "the golden fixture's guard footprint moved; new: "
        f"{sorted(refused - REVIEWED_GUARD_REFUSALS)}; stale: "
        f"{sorted(REVIEWED_GUARD_REFUSALS - refused)}"
    )
    # And the spec's own five are inside it, and none of them is adopted anywhere.
    for raw, row_ids in SPEC_ROWS.items():
        for row_id in row_ids:
            assert (raw, row_id) in refused, (raw, row_id)
    for recipe in committed["recipes"].values():
        for group in ("materials", "seasonings"):
            for slot in recipe[group]:
                refused_ids = {
                    c["pantryItemId"] for c in slot["candidates"] if c["rejectedBy"]
                }
                assert refused_ids.isdisjoint({slot["pantryItemId"]}), slot
    # 20 pairs across the corpus: 12 are the spec's five collisions, the other 8
    # were found by the same naive join and are the same defect.
    assert len(refused) == 20


def test_deleting_either_guard_would_change_what_this_fixture_records(
    committed: dict[str, Any],
) -> None:
    """The guard regressions this file exists to catch, run rather than described.

    **The segment-boundary guard** is load-bearing for the *adopted id* on this
    corpus: with it deleted, the two names the fixture records as misses start
    resolving to the very rows its audit trail names as refused — `Clam` → 143
    and `土豆` → 62 — so the frozen `unresolved` on those slots would be wrong and
    this file would fail.

    **The category-family guard** is *not* load-bearing for any adopted id on
    this corpus, and `app/recipes/matcher.py` says so: the segment guard alone
    refuses every live collision, and `test_must_not_match.py` proves the family
    guard on a synthetic catalog where the two rules disagree instead. What the
    family guard changes here is the **audit trail** — 154 (`Forward Greens Micro
    Broccoli`), 110, 86, 89, 148, 174, 20, 25, 66 and 102 stop being recorded as
    refused on category. That is precisely why this fixture records the
    rejection reasons from `candidates_json` and not only the id: a guard
    deletion that moved no id would otherwise be invisible here.
    """
    catalog = committed_catalog()
    frozen = _frozen_materials(committed)

    for raw, wrong in (("Clam", 143), ("土豆", 62)):
        slot = frozen[raw]
        assert slot["pantryItemId"] is None, raw
        assert wrong in {c["pantryItemId"] for c in slot["candidates"]}, raw
        unguarded = resolve_ingredient(
            parse_ingredient_value(raw), None, catalog, guards=NO_SEGMENT_GUARD
        )
        assert unguarded.pantry_item_id == wrong, (
            f"{raw}: the segment guard stopped being load-bearing, so this "
            f"fixture's `unresolved` on {raw} is no longer what the ladder says"
        )
        assert unguarded.candidate(wrong).rejected_by == ()  # type: ignore[union-attr]

    # The family guard, on the row it refuses *alone*: `Broccoli` genuinely is a
    # whole segment of `Forward Greens Micro Broccoli` (154).
    broccoli = frozen["西兰花"]
    assert broccoli["pantryItemId"] == 14
    refused_alone = [
        c["pantryItemId"] for c in broccoli["candidates"] if c["rejectedBy"] == ["category_family"]
    ]
    assert refused_alone == [154]
    without_family = resolve_ingredient(
        parse_ingredient_value("🥦"), None, catalog, guards=NO_FAMILY_GUARD
    )
    assert without_family.pantry_item_id == 14, (
        "the adopted id does not move on this corpus, which is why the refusal "
        "reasons are recorded and not only the id"
    )
    assert without_family.candidate(154).rejected_by == ()  # type: ignore[union-attr]
    # Four more `材料` slots lose a recorded refusal with the guard off, and three
    # `调料` slots do — 8 slots in total whose audit trail the fixture freezes.
    for raw in ("🥚", "🧄", "芝麻", "芝麻油"):
        without = resolve_ingredient(
            parse_ingredient_value(raw),
            None,
            catalog,
            source="材料" if raw in ("🥚",) else "调料",
            guards=NO_FAMILY_GUARD,
        )
        assert all(
            "category_family" not in candidate.rejected_by for candidate in without.candidates
        ), raw
        assert any(
            {"category_family", "segment_boundary"} <= set(candidate.rejected_by)
            for candidate in resolve_ingredient(
                parse_ingredient_value(raw),
                None,
                catalog,
                source="材料" if raw in ("🥚",) else "调料",
            ).candidates
        ), raw
    # Neither guard is a substitute for the other: with both off the answer is a
    # third value for `Clam`, which is `test_must_not_match`'s four-configuration
    # claim seen from the golden side.
    both_off = resolve_ingredient(
        parse_ingredient_value("Clam"),
        None,
        catalog,
        guards=MatchGuards(segment_boundary=False, category_family=False),
    )
    assert both_off.pantry_item_id == 143
    assert both_off.match_tier == 6


# --- non-vacuity: the comparison can fail -----------------------------------


def test_perturbing_the_catalog_makes_this_fixture_wrong(
    settings: Settings, measured: dict[str, Any]
) -> None:
    """**Non-vacuity proof.** A changed answer must fail the comparison.

    The store's output is compared against the same measurement taken over a
    catalog with one row added: a bare `空心菜`, so the three `空心菜` slots would
    resolve to *that* row — at tier 2 `exact_name`, which is the strongest rung
    there is and the one §10.3's example fixture implies. The payloads must
    differ, and they must differ in exactly the three recipes the fixture names
    and nowhere else.

    This is the test that makes the others mean something: a golden comparison no
    input could move would pass over any regression, and the only way to know it
    can move is to move it.
    """
    rows = json.loads(CATALOG_SNAPSHOT.read_text(encoding="utf-8"))
    perturbed_catalog = build_snapshot([*rows, (900, "空心菜", "1.1", "[]", None)])
    perturbed = _run(golden.measure(settings, catalog=perturbed_catalog))
    assert perturbed != measured
    differing = {
        name
        for name in measured["recipes"]
        if measured["recipes"][name] != perturbed["recipes"][name]
    }
    assert differing == set(KONGXINCAI_RECIPES)
    for name in KONGXINCAI_RECIPES:
        moved = [
            slot
            for slot in perturbed["recipes"][name]["materials"]
            if slot["parsedName"] == "空心菜"
        ]
        assert len(moved) == 1
        assert moved[0]["pantryItemId"] == 900
        assert moved[0]["matchMethod"] == "exact_name"
        assert moved[0]["matchTier"] == 2
    # The headline count does not move — the slot was resolved either way — which
    # is the honest shape of this perturbation and the reason the fixture records
    # the tier as well as the id.
    assert perturbed["recipes"]["拌空心菜"]["materialsFound"] == 1
    assert perturbed["summary"]["materialResolvedSlots"] == 9


def test_a_hand_fix_moves_the_recorded_answer(
    settings: Settings, measured: dict[str, Any]
) -> None:
    """The second non-vacuity proof, through the store's own mutation path.

    `set_manual` is the one way a slot's recorded answer changes without the
    matcher changing, and it writes the same fields the golden file freezes.
    Rebinding 微波菜菜's `Kale` to id 10 must change the measurement, which proves
    the comparison reads the *persisted* answer rather than re-deriving one and
    comparing that.

    It is also the user-facing half of the `Kale → 56` story: id 10 is
    `Organic Baby Kale`, the row tier 4's prefix pre-empted, so a hand fix is
    exactly how a user overrules the specified order's worse answer — and the
    frozen fixture records the order's answer until they do. F6's immutability is
    exercised in passing: the `measure()` that follows calls `resolve_all()`, and a
    `manual` row is skipped rather than re-derived.
    """
    catalog = committed_catalog()

    async def scenario() -> dict[str, Any]:
        await init_db(settings)
        store = IngredientMappingStore(lambda: connect_db(settings), lambda: catalog)
        await store.ensure_rows(real_notes())
        await store.resolve_all()
        await store.set_manual("fake/微波菜菜.md", 0, 10)
        return await golden.measure(settings)

    perturbed = _run(scenario())
    slot = _material(perturbed, "微波菜菜", 0)
    assert slot["pantryItemId"] == 10
    assert slot["matchMethod"] == "manual"
    assert slot["matchTier"] == 0
    assert slot["confidence"] == 1.0
    assert slot["candidates"] == [], "a hand fix carries no audit trail of its own"
    assert perturbed != measured
    # The frozen answer is the ladder's, and it survives the re-resolve: F6 skips
    # the row rather than putting `Kale → 56` back.
    assert _material(golden.load(GOLDEN), "微波菜菜", 0)["pantryItemId"] == 56
    assert perturbed["summary"]["materialResolvedSlots"] == (
        measured["summary"]["materialResolvedSlots"]
    ), "the slot is still resolved, just to a different Pantry Item"
    # The user-visible score does not move: `Kale` was found and still is. That is
    # the whole point of F6 — a hand fix changes *which* product an Ingredient is,
    # never whether the recipe is cookable.
    assert perturbed["recipes"]["微波菜菜"]["materialsFound"] == 1
    assert perturbed["recipes"]["微波菜菜"]["missingMaterials"] == ["香菇", "娃娃菜"]


def test_measurement_refuses_when_one_name_resolves_two_ways(
    settings: Settings,
) -> None:
    """The fixture's per-name record is only meaningful if a name resolves once.

    Keying a record by parsed name is sound only because the ladder reads the
    parsed name and nothing else. A `manual` row on one of three notes that write
    `空心菜` breaks exactly that assumption, so `measure()` raises rather than
    recording whichever slot it saw last — a golden file whose per-name record
    depended on iteration order would be a file nobody could review.
    """
    catalog = committed_catalog()

    async def scenario() -> None:
        await init_db(settings)
        store = IngredientMappingStore(lambda: connect_db(settings), lambda: catalog)
        await store.ensure_rows(real_notes())
        await store.resolve_all()
        await store.set_manual("fake/拌空心菜.md", 0, 102)  # 芝麻烧饼, deliberately wrong

    _run(scenario())
    with pytest.raises(golden.GoldenSnapshotError, match="resolved two ways"):
        _run(golden.measure(settings))


def test_the_regeneration_scripts_literals_are_this_corpus() -> None:
    """The script's numbers and the frozen file must not drift apart.

    `scripts/snapshot_golden_match_results.py` carries `EXPECTED_SUMMARY` and
    `EXPECTED_TIER_FIRES` as reviewable numbers, and the file carries the same
    figures as data. Editing the literals without re-freezing — or the reverse —
    would leave the two disagreeing, and the script's second flag would then
    re-baseline the wrong thing. Compared function to function, the same way
    `tests/pantry/test_snapshot_script.py` compares its own.
    """
    from scripts.snapshot_golden_match_results import (
        EXPECTED_SUMMARY,
        EXPECTED_TIER_FIRES,
        guarded_summary,
    )

    committed = golden.load(GOLDEN)
    assert guarded_summary(committed["summary"]) == {
        **EXPECTED_SUMMARY,
        "tierFireCountsByDistinctMaterialName": EXPECTED_TIER_FIRES,
    }


# --- the headline strings: F16 and F17's frozen forms -----------------------


def test_the_four_frozen_forms_are_the_specs(committed: dict[str, Any]) -> None:
    """`golden.FROZEN_HEADLINE_FORMS` and #19's literals are the same four lines.

    The templates in `golden.py` and the strings in
    `tests/js/logic/format.test.mjs` are two halves of one contract, in two
    languages. Asserting that the templates instantiate to #19's exact strings —
    including the `0/3` form with a zero numerator and the `2/5` form with both
    clauses — is what stops either half drifting away from the other.
    """
    forms = golden.FROZEN_HEADLINE_FORMS
    assert forms[0].format(found=6, total=6) == FROZEN_HEADLINE_LINES[0]
    assert forms[1].format(found=4, total=6, materials="香菇, 娃娃菜") == FROZEN_HEADLINE_LINES[1]
    assert forms[2].format(found=6, total=7, seasonings="生抽") == FROZEN_HEADLINE_LINES[2]
    assert (
        forms[3].format(found=2, total=5, materials="Clam, 香菇", seasonings="生抽")
        == FROZEN_HEADLINE_LINES[3]
    )
    assert (
        forms[1].format(found=0, total=3, materials="空心菜, 香菇, 娃娃菜")
        == FROZEN_HEADLINE_LINES[4]
    )
    # Nothing outside the four forms is ever produced.
    for found, total, materials, seasonings in (
        (6, 6, [], []),
        (4, 6, ["香菇", "娃娃菜"], []),
        (0, 1, ["黑木耳"], []),
        (2, 5, ["Clam", "香菇"], ["生抽"]),
        (1, 1, [], ["芝麻"]),
    ):
        line = golden.frozen_headline(
            found=found,
            total=total,
            missing_materials=materials,
            missing_seasonings=seasonings,
        )
        candidates = {
            forms[0].format(found=found, total=total),
            forms[1].format(found=found, total=total, materials=", ".join(materials)),
            forms[2].format(found=found, total=total, seasonings=", ".join(seasonings)),
            forms[3].format(
                found=found,
                total=total,
                materials=", ".join(materials),
                seasonings=", ".join(seasonings),
            ),
        }
        assert line in candidates, line


def test_every_recipe_headline_is_its_own_counts_in_one_of_the_four_forms(
    committed: dict[str, Any],
) -> None:
    """Per-recipe `n/total`, in both modes, for all 16 notes.

    The committed strings are rendered by `app/static/js/logic/format.js` through
    `scripts/render_headlines.mjs` — this repository's one implementation of F16
    and F17, called rather than re-spelled. This asserts them against the counts
    the store produced, so the two must agree: a headline that was not the render
    layer's output, or one whose counts drifted from the slots, fails here. The
    strict line's denominator is `材料` **plus** `调料`, because F16 is what
    re-admits the Seasonings.
    """
    for name, recipe in committed["recipes"].items():
        assert recipe["headline"] == golden.frozen_headline(
            found=recipe["materialsFound"],
            total=recipe["materialsTotal"],
            missing_materials=recipe["missingMaterials"],
            missing_seasonings=[],
        ), name
        assert recipe["headlineStrict"] == golden.frozen_headline(
            found=recipe["materialsFound"] + recipe["seasoningsFound"],
            total=recipe["materialsTotal"] + recipe["seasoningsTotal"],
            missing_materials=recipe["missingMaterials"],
            missing_seasonings=recipe["missingSeasonings"],
        ), name
        # F17: the Materials-only clause never names a 调料, in either mode, and
        # the missing lists keep slot order.
        assert not set(recipe["missingMaterials"]) & set(recipe["missingSeasonings"]), name
        assert recipe["missingMaterials"] == [
            str(slot["parsedName"] or slot["rawValue"])
            for slot in recipe["materials"]
            if slot["pantryItemId"] is None
        ], name


def test_the_corpus_reproduces_the_frozen_lines_it_should(
    committed: dict[str, Any]
) -> None:
    """The shapes #19 froze, over the real corpus rather than over synthetic slots.

    Three of the four forms occur here, and each occurrence is a line this file
    freezes:

    * the fully-found form — five notes, `1/1`, `1/1`, `1/1`, `1/1`, `2/2`. No
      six-slot note is fully found, so the literal `6/6 ingredients found` is
      *not* produced by this corpus; it is #19's, and the shape is asserted above.
    * the Materials-only list — `微波菜菜` reads `1/3 ingredients found — missing:
      香菇, 娃娃菜`, which is #19's `4/6` line with the same two missing names and
      this corpus's own numerator and denominator. Same shape, same two names, and
      `Kale → 56` is the one slot that resolves.
    * the strict addendum — `拌空心菜` reads `5/6 ingredients found   (严格模式（含调
      料）: 芝麻)`, which is #19's third form: a fully-found Recipe under 严格模式
      with one unresolved Seasoning named and no Materials clause at all.

    The `0/3` form is also produced: `0/1` and `0/2` lines exist on four notes, so
    the zero-numerator shape is here even though the exact `0/3` line is not.
    """
    lines = {name: recipe["headline"] for name, recipe in committed["recipes"].items()}
    assert lines["微波菜菜"] == "1/3 ingredients found — missing: 香菇, 娃娃菜"
    assert lines["拌空心菜"] == "1/1 ingredients found"
    assert (
        committed["recipes"]["拌空心菜"]["headlineStrict"]
        == "5/6 ingredients found   (严格模式（含调料）: 芝麻)"
    )
    assert lines["Paradiso三明治"] == (
        "0/6 ingredients found — missing: Mortadella, 开心果酱, Stracciatella, 香料,"
        " Arugula, focaccia"
    ), "six missing Materials, untruncated, in slot order"
    assert (
        committed["recipes"]["花蛤拌饭"]["headlineStrict"]
        == "6/10 ingredients found — missing: Clam, 香菇, Shishito   (严格模式（含调料）: 芝麻)"
    ), "both clauses, separated by exactly three spaces"
    fully_found = [line for line in lines.values() if " — missing: " not in line]
    assert len(fully_found) == 5
    assert not any(line.startswith("6/6") for line in lines.values()), (
        "no six-slot note is fully cookable in this corpus; do not quote 6/6 as its output"
    )


def test_the_render_layer_and_the_four_frozen_forms_agree_on_every_recipe(
    measured: dict[str, Any],
) -> None:
    """`format.js` itself, called through node, produces the frozen lines.

    `check_headlines()` is the same cross-check the regeneration script refuses
    on, run here so a disagreement is a test failure rather than something a
    future `--regenerate` would quietly write. It shells out to `node` for the
    two browser modules, and skips — rather than reimplementing them — when node
    is unavailable, because a Python copy of `format.js` is the thing this
    repository has been refusing for several tickets.
    """
    if shutil.which("node") is None:  # pragma: no cover — node is a repo gate
        pytest.skip("node is not on PATH; the headline renderer is a browser module")
    assert golden.check_headlines(measured) == []


# --- the corpus itself, and the suite's own properties ----------------------


def test_the_candidate_schema_is_the_one_f2_fixed(
    settings: Settings, committed: dict[str, Any]
) -> None:
    """The golden file's candidate keys are the store's own `candidates_json` keys.

    The fixture projects them from a literal (`golden.CANDIDATE_KEYS`) rather than
    by importing the store's projection, because that function's name is not part
    of the store's public vocabulary and a golden file must not break on a
    rename. So the literal is checked against the *encoded bytes* the store wrote,
    read straight out of SQLite: a change to F2's schema fails here instead of
    producing a quietly different fixture.
    """
    _run(golden.measure(settings))
    rows = _run(_raw_candidates(settings))
    assert len(rows) == 32, "the 材料 slots are the ones with an audit trail"
    for recipe_note, index, candidates_json in rows:
        encoded = json.loads(candidates_json)
        for entry in encoded:
            assert tuple(sorted(entry)) == tuple(sorted(golden.CANDIDATE_KEYS)), (
                recipe_note,
                index,
                sorted(entry),
            )
        recipe_name = Path(recipe_note).stem
        slot = _material(committed, recipe_name, index)
        assert slot["candidates"] == encoded, (recipe_note, index)


def test_nothing_here_reads_a_live_path(
    settings: Settings, measured: dict[str, Any]
) -> None:
    """The suite-wide property, asserted for this file rather than assumed.

    The producer's `pantry_items.db` lives in a sibling checkout CI does not
    have, and the vault is the user's live, machine-rewritten folder. A test that
    reached for either would pass on one machine and assert nothing on every
    other one — and would start failing the day the user bought something or
    cooked a meal. So: the database is under `tmp_path` and outside the vault, the
    catalog is the committed JSON snapshot built through the connection-free
    `build_snapshot`, and the recipes are the committed frozen bytes.
    """
    database = settings.app_data_dir / "recipes.sqlite3"
    assert database.is_file()
    assert not database.is_relative_to(settings.vault_path)
    assert settings.app_data_dir != settings.vault_path
    catalog = committed_catalog()
    assert len(catalog.rows) == 176
    assert catalog.total_row_count == 178
    assert CATALOG_SNAPSHOT.is_file()
    assert REAL_RECIPES.is_dir()
    assert len(tuple(REAL_RECIPES.glob("*.md"))) == 17, "16 notes plus the folder index"
    # The synthetic catalog the perturbation builds is in memory, with no file
    # behind it — the same helper `test_must_not_match.py` uses.
    synthetic = synthetic_catalog([(900, "Kale Microgreens 2 OZ", "1.1", "[]")])
    assert len(synthetic.rows) == 1


def test_the_committed_golden_fixture_is_unchanged_since_it_was_committed() -> None:
    """Drift has to be *reported*, never absorbed.

    `--check` is the default and writes nothing, and the comparison above fails
    on any change — but a re-freeze that was run and committed anyway, or a
    hand-edited number, would leave nothing behind to fail. This is the guard
    that makes the freeze a review event: if these bytes are not what the commit
    says they are, CI says so. It needs no vault, so it works in a clone that has
    none, and it skips only before the file has ever been committed, where there
    is nothing yet to compare against.
    """
    if _git("rev-parse", "--is-inside-work-tree").returncode != 0:  # pragma: no cover
        pytest.skip("not a git checkout; there is nothing to compare the fixture against")
    relative = GOLDEN.relative_to(REPO_ROOT)
    if _git("ls-files", "--error-unmatch", "--", str(relative)).returncode != 0:
        pytest.skip("the golden file is not committed yet; the drift gate has nothing to compare")
    changed = _git("status", "--porcelain", "--", str(relative))
    assert changed.returncode == 0, changed.stderr
    assert changed.stdout == "", (
        "the frozen golden expectation differs from the commit; a re-freeze is a "
        f"deliberate act, reviewed in its own commit:\n{changed.stdout}"
    )


# --- helpers ----------------------------------------------------------------


async def _raw_candidates(settings: Settings) -> tuple[tuple[str, int, str], ...]:
    """Every `材料` slot's `candidates_json`, as raw text, straight SQL.

    Straight SQL on purpose: `rows_for()` decodes the audit trail, and this
    assertion is about the *bytes* the store wrote, so it must not go through the
    reader whose decoding it is checking. `调料` slots have no row by design.
    """
    async with connect_db(settings) as conn:
        cursor = await conn.execute(
            "SELECT recipe_note, ingredient_index, candidates_json FROM ingredient_mappings"
            " ORDER BY recipe_note, ingredient_index"
        )
        return tuple(
            (str(row[0]), int(row[1]), str(row[2])) for row in await cursor.fetchall()
        )


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )

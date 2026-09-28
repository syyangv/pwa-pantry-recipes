"""The golden expectation for the matcher, measured once and frozen.

**This module is the single answer to "what does the matcher do with the
committed corpus?", and it is shared rather than written twice.** Both consumers
call the same functions:

* `tests/recipes/test_golden_real_recipes.py` measures the corpus and asserts the
  result equals `tests/fixtures/golden_match_results.json` exactly.
* `scripts/snapshot_golden_match_results.py` measures it and either refuses to
  write or writes it, behind a reviewed flag.

A second copy of this measurement would be a golden file that could disagree with
the golden test, which is the one thing a golden file must never do.

**The `材料` half is the store's output, not a direct ladder call.** Every
`材料` record here is an `ingredient_mappings` row read back through
`rows_for()` after `ensure_rows()` + `resolve_all()`, so the anchor covers the
whole path — the parser, the ladder, the `candidates_json` codec, F2's per-slot
recovery, and the slot index — rather than the pure function in the middle of it.
That is the difference between this being an end-to-end anchor and being a
unit test with a JSON file next to it.

**The `调料` half is a direct ladder call, because the store deliberately does
not materialize one.** `app.mapping.store`'s module docstring is explicit: the
table's `ingredient_index` is a `材料` index, `CONTEXT.md` keeps Seasonings and
Ingredients apart, and a staples-satisfied Seasoning must never be recorded as
a `材料` row. So the Seasoning slots are resolved with `source="调料"` — the one
call shape the store refuses to make — and the golden file says which half of
each recipe came from where.

**The headline strings are rendered by the render layer, not composed here.**
`app/static/js/logic/format.js` is F16/F17's implementation and this repository
has exactly one of it, so `render_headlines()` calls it through
`scripts/render_headlines.mjs` and the strings in the golden file are the strings
the browser will produce. What this module *also* carries is
`FROZEN_HEADLINE_FORMS` — the four shapes §9.13.1 freezes, quoted from
`tests/js/logic/format.test.mjs` — so the committed strings can be cross-checked
against the spec's own four sentences without a second renderer existing. See
`frozen_headline()` for why that is a template selection and not a port.

**No live path, no clock, no network.** The catalog is the committed
`pantry_items_snapshot.json` through `tests/recipes/corpus.py`, the recipes are
the committed `tests/fixtures/real_recipes/*.md`, and the only database is
whatever `connect_db(settings)` opens under the caller's `tmp_path`. The one
exception is `render_headlines()`, which runs `node` — not to read anything, but
to execute the render layer's own two pure functions.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Final, cast

from app.config import Settings
from app.db.database import connect_db, init_db
from app.mapping.store import (
    CandidateRecord,
    IngredientMapping,
    IngredientMappingStore,
)
from app.pantry.catalog import CatalogSnapshot
from app.recipes.ingredients import parse_ingredient_value
from app.recipes.matcher import (
    FUZZY_METHOD,
    TIER_METHOD,
    UNPARSEABLE_METHOD,
    UNPARSEABLE_TIER,
    UNRESOLVED_METHOD,
    UNRESOLVED_TIER,
    MatchCandidate,
    MatchResult,
    resolve_ingredient,
)
from app.recipes.reader import IngredientEntry, RecipeNote

from .corpus import committed_catalog, real_notes

#: The ladder's whole tier range, tier 0 through tier 8, and the matcher's own
#: `MatchMethod` vocabulary in tier order. Reused rather than re-listed so a
#: future rung cannot be added without the golden file's histograms noticing.
_LAST_TIER: Final = UNRESOLVED_TIER
_METHOD_VOCABULARY: Final[tuple[str, ...]] = (
    UNPARSEABLE_METHOD,
    *(TIER_METHOD[tier] for tier in range(1, UNRESOLVED_TIER)),
    FUZZY_METHOD,
    UNRESOLVED_METHOD,
)
#: The tiers that fire on **zero** `材料` values in the committed corpus, named
#: one by one rather than derived, because the number *is* the assertion: a
#: future change that made one of them fire has to be noticed, and a derived set
#: could not be wrong in a reviewable way. Tier 3 is the spec's near-dead tier
#: (3 of 178 rows carry a `variants`, two of them the long product name) and
#: tier 7 is `调料`-only by design, so both are *expected* to be empty here.
DEAD_TIERS: Final[tuple[int, ...]] = (
    UNPARSEABLE_TIER,  # 0 `unparseable` — every value in the corpus parses
    1,  # `exact_id`       — no note spells out a Pantry Item id
    2,  # `exact_name`     — no note's name is a whole `canonical_name`
    3,  # `variant_alias`  — the spec's near-dead tier; see the docstring
    7,  # `staples`        — `材料` only, and the ladder refuses that on purpose
)

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
GOLDEN_PATH: Final = REPO_ROOT / "tests" / "fixtures" / "golden_match_results.json"
#: The node half of the headline render. It imports `format.js` and
#: `chip-class.js` and calls them; it contains no formatting of its own.
RENDERER: Final = REPO_ROOT / "scripts" / "render_headlines.mjs"

#: The seven keys of one `candidates_json` entry, in F2's spelling.
#:
#: Spelled out here rather than borrowed from the store's own projection on
#: purpose. That projection is a module-level function whose name is not part of
#: the store's public vocabulary, and a golden file that imported it would break
#: on a rename — which is precisely the kind of churn a *frozen* artifact must
#: not be subject to. The schema is therefore a literal, and
#: `test_the_candidate_schema_is_the_one_f2_fixed` asserts it against the store's
#: own encoded bytes, so a change to the projection is a failure here rather than
#: a silently different golden file.
CANDIDATE_KEYS: Final[tuple[str, ...]] = (
    "pantryItemId",
    "canonicalName",
    "pantryCategory",
    "family",
    "relation",
    "reason",
    "rejectedBy",
)

#: Bumped only when a key in this file's payload is renamed or removed. A
#: mismatch between the committed `schemaVersion` and this one is a test failure,
#: not a migration: the golden file is regenerated deliberately, in its own
#: commit, and a shape change is part of that review.
SCHEMA_VERSION: Final = 1

#: §9.13.1's four headline forms, quoted from `tests/js/logic/format.test.mjs`
#: (F16, F17). Exactly four, exactly as frozen there:
#:
#: * `6/6 ingredients found` — nothing missing, so no clause at all;
#: * `4/6 ingredients found — missing: 香菇, 娃娃菜` — the Materials-only list;
#: * `2/5 ingredients found — missing: Clam, 香菇   (严格模式（含调料）: 生抽)` —
#:   both clauses, separated by exactly three spaces;
#: * `6/7 ingredients found   (严格模式（含调料）: 生抽)` — the addendum with no
#:   Materials clause, which is what a fully-found Recipe under 严格模式 reads.
#:
#: A `0/3` line is the second form with `found=0`, and is asserted as such rather
#: than as a fifth shape: the denominator tracks the slot count, which is F17's
#: whole claim.
FROZEN_HEADLINE_FORMS: Final[tuple[str, ...]] = (
    "{found}/{total} ingredients found",
    "{found}/{total} ingredients found — missing: {materials}",
    "{found}/{total} ingredients found   (严格模式（含调料）: {seasonings})",
    "{found}/{total} ingredients found — missing: {materials}"
    "   (严格模式（含调料）: {seasonings})",
)


class GoldenSnapshotError(RuntimeError):
    """A measurement that cannot be frozen as it stands.

    Four refusals, all of them refusing rather than writing:

    * `render_headlines()` — `node` is missing, or the renderer exited non-zero.
      There is no Python re-spelling of `format.js` to fall back to, and adding
      one would be a second implementation of F16/F17.
    * `check_headlines()` — the render layer and §9.13.1's four frozen forms
      disagree about a line, so the string is one neither the browser nor the
      spec would produce.
    * `measure()` — one `材料` name resolved two ways, which makes the per-name
      record depend on iteration order.
    * `load()` — the committed file is not a JSON object.
    """


def frozen_headline(
    *,
    found: int,
    total: int,
    missing_materials: list[str],
    missing_seasonings: list[str],
) -> str:
    """One headline, by **selecting and filling** one of the four frozen forms.

    Deliberately a template choice rather than a second renderer. `format.js`
    decides *which slots are missing* by running `chipClass()`, and that
    classifier is F16's contract in `app/static/js/logic/chip-class.js`; the four
    sentences above are §9.13.1's, and choosing between them and filling in the
    numbers is all that is left once the missing lists are known. The lists
    themselves are computed by `is_missing()` below, from the same three
    conditions `chipClass()` uses.
    """
    if missing_materials and missing_seasonings:
        form = FROZEN_HEADLINE_FORMS[3]
    elif missing_materials:
        form = FROZEN_HEADLINE_FORMS[1]
    elif missing_seasonings:
        form = FROZEN_HEADLINE_FORMS[2]
    else:
        form = FROZEN_HEADLINE_FORMS[0]
    return form.format(
        found=found,
        total=total,
        materials=", ".join(missing_materials),
        seasonings=", ".join(missing_seasonings),
    )


def is_missing(slot: dict[str, Any], *, is_seasoning: bool, strict: bool) -> bool:
    """`chipClass()`'s `missing` flag, restated as the three conditions it uses.

    A hand fix and a staples assumption answer before the stock tiers are
    consulted, so neither is ever missing; an unresolved Seasoning outside
    严格模式 is excluded rather than missing, which is F16's gap. This is the
    classifier's rule in six lines, and it is the **only** place this file
    decides what is missing — `render_headlines()` runs the real classifier for
    the committed strings, and `check_headlines()` fails if the two answers ever
    differ.
    """
    if slot["matchMethod"] in {"manual", "staples"}:
        return False
    if slot["pantryItemId"] is not None:
        return False
    return not (is_seasoning and not strict)


def _slot(
    *,
    index: int,
    raw: str,
    parsed_name: str | None,
    parse_method: str,
    match_method: str,
    match_tier: int,
    pantry_item_id: int | None,
    confidence: float,
    candidates: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    return {
        "index": index,
        "rawValue": raw,
        "parseMethod": parse_method,
        "parsedName": parsed_name,
        "matchMethod": match_method,
        "matchTier": match_tier,
        "pantryItemId": pantry_item_id,
        "confidence": confidence,
        "candidates": list(candidates),
    }


def candidate_payload(candidate: CandidateRecord) -> dict[str, Any]:
    """One audit entry, in F2's spelling. See `CANDIDATE_KEYS`.

    `reason` is the single string (the first guard that refused the row, or
    `matched`, or F2's `duplicate_slot_conflict`) and `rejectedBy` is the
    complete list, which is the split F2 fixed and which `CandidateRecord` already
    carries: a row two guards refused appears once, naming both. Projected from
    the decoded row rather than from `candidates_json`'s text, so the golden file
    records the same object the `调试` view reads back — the codec's own round
    trip is `test_store.py`'s subject, not this file's.
    """
    return {
        "pantryItemId": candidate.pantry_item_id,
        "canonicalName": candidate.canonical_name,
        "pantryCategory": candidate.pantry_category,
        "family": candidate.family,
        "relation": candidate.relation,
        "reason": candidate.reason,
        "rejectedBy": list(candidate.rejected_by),
    }


def matcher_candidate_payload(candidate: MatchCandidate) -> dict[str, Any]:
    """The same seven keys for a raw ladder candidate, which is all a `调料` has.

    `MatchCandidate` carries `rejected_by` and no single `reason` — the split is
    the store's, and this is where it is reconstructed for the one half of the
    corpus the store does not materialize. `MATCHED` is the store's own name for
    "looked at, not refused, adopted".
    """
    return {
        "pantryItemId": candidate.pantry_item_id,
        "canonicalName": candidate.canonical_name,
        "pantryCategory": candidate.pantry_category,
        "family": candidate.family,
        "relation": candidate.relation,
        "reason": candidate.rejected_by[0] if candidate.rejected_by else "matched",
        "rejectedBy": list(candidate.rejected_by),
    }


def _mapping_slot(row: IngredientMapping) -> dict[str, Any]:
    """One `材料` slot, as the **store** holds it.

    Read back out of SQLite through `rows_for()`, so `candidates_json` has been
    encoded and decoded to get here. `reason` and `rejectedBy` are the split F2
    fixes, so a row two guards refused carries both — an audit trail recording
    only the first would understate what is holding a row back.
    """
    return _slot(
        index=row.ingredient_index,
        raw=row.raw_value,
        parsed_name=row.parsed_name,
        parse_method=row.parse_method,
        match_method=row.match_method,
        match_tier=row.match_tier,
        pantry_item_id=row.pantry_item_id,
        confidence=row.confidence,
        candidates=tuple(candidate_payload(candidate) for candidate in row.candidates),
    )


def _seasoning_slot(entry: IngredientEntry, result: MatchResult) -> dict[str, Any]:
    """One `调料` slot, as the ladder answers it with `source="调料"`.

    See the module docstring for why this is the only half of the golden file
    that does not come out of the store. The candidate keys are the same seven,
    so the two halves of the file are one schema.
    """
    return _slot(
        index=entry.index,
        raw=entry.raw,
        parsed_name=result.parsed_name,
        parse_method=result.parse_method,
        match_method=result.match_method,
        match_tier=result.match_tier,
        pantry_item_id=result.pantry_item_id,
        confidence=result.confidence,
        candidates=tuple(
            matcher_candidate_payload(candidate) for candidate in result.candidates
        ),
    )


def _missing_names(
    slots: list[dict[str, Any]], *, is_seasoning: bool, strict: bool
) -> list[str]:
    """The missing names of one group, in slot order, never sorted.

    `parsedName` first and `rawValue` as the fallback, which is what
    `format.js`'s `missingNames` does; slot order is what makes the list read as
    the note reads.
    """
    return [
        str(slot["parsedName"] or slot["rawValue"] or "")
        for slot in slots
        if is_missing(slot, is_seasoning=is_seasoning, strict=strict)
    ]


def _recipe_payload(
    materials: list[dict[str, Any]], seasonings: list[dict[str, Any]], note_path: str
) -> dict[str, Any]:
    """One recipe's slots and its per-recipe figures.

    `materialsFound` / `materialsTotal` are F17's `n/total`, counted from `材料`
    only. `seasoningsFound` / `seasoningsTotal` are the 严格模式 pair: under
    F16 a staples-satisfied Seasoning counts as found and only an unresolved one
    is missing, so this is the count 严格模式 would score, and it is recorded
    even though the default view excludes Seasonings from the headline entirely.

    `duplicateMaterialItemIds` is F2's constraint made observable: **empty** on
    every recipe today, which is the fact §10.3 asks the fixture to record. A
    recipe that listed the same Pantry Item twice would show the id here, and
    `ResolveReport.duplicate_slot_conflicts` would be non-zero.
    """
    found_ids = [slot["pantryItemId"] for slot in materials if slot["pantryItemId"] is not None]
    duplicated = sorted({item for item in found_ids if found_ids.count(item) > 1})
    missing_materials = _missing_names(materials, is_seasoning=False, strict=False)
    missing_seasonings = _missing_names(seasonings, is_seasoning=True, strict=True)
    return {
        "notePath": note_path,
        "materials": materials,
        "seasonings": seasonings,
        "materialsFound": len(materials) - len(missing_materials),
        "materialsTotal": len(materials),
        "seasoningsFound": len(seasonings) - len(missing_seasonings),
        "seasoningsTotal": len(seasonings),
        "missingMaterials": missing_materials,
        "missingSeasonings": missing_seasonings,
        "allMaterialsResolved": not missing_materials,
        "duplicateMaterialItemIds": duplicated,
    }


def _summary(
    recipes: dict[str, dict[str, Any]], report_summary: dict[str, Any]
) -> dict[str, Any]:
    """The corpus-level figures §10.3 lists as secondary golden assertions.

    Every number here is derived from the slots above and nothing else, so the
    summary cannot disagree with the records it summarises.

    The two coverage figures are quoted against **distinct parsed names**, which
    is not the same denominator as slots: 32 slots carry 25 distinct `材料`
    names (`🍄/香菇` and `香菇` are one food written twice) and 39 Seasoning
    slots carry 19 distinct names. §10.3 quotes D1's figures per distinct value,
    so this is the same denominator D1 used and the one a reader can compare
    against.
    """
    by_name: dict[str, dict[str, Any]] = {}
    for recipe in recipes.values():
        for slot in recipe["materials"]:
            name = str(slot["parsedName"] or slot["rawValue"])
            record = by_name.setdefault(
                name,
                {
                    "matchMethod": slot["matchMethod"],
                    "matchTier": slot["matchTier"],
                    "pantryItemId": slot["pantryItemId"],
                    "confidence": slot["confidence"],
                    "slots": [],
                },
            )
            record["slots"].append(f"{recipe['notePath']}#{slot['index']}")
            if (record["matchMethod"], record["matchTier"], record["pantryItemId"]) != (
                slot["matchMethod"],
                slot["matchTier"],
                slot["pantryItemId"],
            ):
                # Two raw values that parse to one name must resolve identically:
                # the ladder reads the parsed name and nothing else. If that ever
                # stops being true the per-name record below is meaningless, so
                # it is raised here rather than averaged away.
                raise GoldenSnapshotError(
                    f"{name!r} resolved two ways: {record} and {slot}"
                )
    seasoning_by_name: dict[str, str] = {}
    for recipe in recipes.values():
        for slot in recipe["seasonings"]:
            seasoning_by_name.setdefault(
                str(slot["parsedName"] or slot["rawValue"]), slot["matchMethod"]
            )
    tiers: dict[str, int] = {str(tier): 0 for tier in range(_LAST_TIER + 1)}
    methods: dict[str, int] = {method: 0 for method in _METHOD_VOCABULARY}
    for record in by_name.values():
        # `+= 1` through `get` rather than `[key] += 1`: a `manual` row is the
        # store's own method and is in neither histogram's vocabulary, and a
        # measurement that has to survive one (the hand-fix perturbation) must
        # count it under its own key rather than raise. The committed file is
        # measured from a freshly resolved table, so its histograms carry exactly
        # the ladder's vocabulary and nothing else.
        tiers[str(record["matchTier"])] = tiers.get(str(record["matchTier"]), 0) + 1
        method = str(record["matchMethod"])
        methods[method] = methods.get(method, 0) + 1
    resolved_names = sum(
        1 for record in by_name.values() if record["pantryItemId"] is not None
    )
    return {
        "recipeCount": len(recipes),
        "materialSlotCount": sum(len(r["materials"]) for r in recipes.values()),
        "materialResolvedSlots": sum(r["materialsFound"] for r in recipes.values()),
        "materialUnresolvedSlots": sum(
            len(r["materials"]) - r["materialsFound"] for r in recipes.values()
        ),
        "seasoningSlotCount": sum(len(r["seasonings"]) for r in recipes.values()),
        "seasoningFoundSlotsStrict": sum(r["seasoningsFound"] for r in recipes.values()),
        "allMaterialsResolvedRecipes": sum(
            1 for r in recipes.values() if r["allMaterialsResolved"]
        ),
        "duplicateSlotConflicts": report_summary["duplicate_slot_conflicts"],
        "staleResets": report_summary["stale_reset"],
        "distinctMaterialNames": len(by_name),
        "distinctMaterialNamesResolved": resolved_names,
        "distinctSeasoningNames": len(seasoning_by_name),
        "distinctSeasoningNamesStaples": sum(
            1 for method in seasoning_by_name.values() if method == "staples"
        ),
        "tierFireCountsByDistinctMaterialName": tiers,
        "methodFireCountsByDistinctMaterialName": methods,
        "materialNames": dict(sorted(by_name.items())),
        "seasoningNames": dict(sorted(seasoning_by_name.items())),
    }


async def measure(
    settings: Settings,
    *,
    catalog: CatalogSnapshot | None = None,
    notes: tuple[RecipeNote, ...] | None = None,
) -> dict[str, Any]:
    """Measure the whole committed corpus and return the golden payload.

    `catalog` and `notes` exist so a test can perturb one of them and watch the
    comparison fail — the non-vacuity proof, and the guard-sensitivity proof. They
    default to the committed fixtures, which is what the golden file is about.
    """
    the_catalog = catalog if catalog is not None else committed_catalog()
    the_notes = notes if notes is not None else real_notes()
    # Idempotent by design, and called here rather than left to the caller so
    # `measure()` is the whole measurement: the schema it reads through is part
    # of what is being frozen, and a caller that forgot `init_db` would get an
    # `OperationalError` instead of a golden file.
    await init_db(settings)
    store = IngredientMappingStore(lambda: connect_db(settings), lambda: the_catalog)
    await store.ensure_rows(the_notes)
    report = await store.resolve_all()
    recipes: dict[str, dict[str, Any]] = {}
    for note in the_notes:
        rows = await store.rows_for(note.note_path)
        materials = [_mapping_slot(row) for row in rows]
        seasonings = [
            _seasoning_slot(
                entry,
                resolve_ingredient(
                    parse_ingredient_value(entry.raw), None, the_catalog, source="调料"
                ),
            )
            for entry in note.seasonings
        ]
        recipes[note.note_name] = _recipe_payload(materials, seasonings, note.note_path)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedFrom": {
            "catalogSnapshot": "tests/fixtures/pantry_items_snapshot.json",
            "realRecipes": "tests/fixtures/real_recipes",
            "catalogRows": the_catalog.total_row_count,
            "candidateRows": len(the_catalog.rows),
        },
        "summary": {
            **_summary(
                recipes,
                {
                    "duplicate_slot_conflicts": report.duplicate_slot_conflicts,
                    "stale_reset": report.stale_reset,
                },
            ),
            # The store's own pass report, verbatim. F2's zero-conflict claim is
            # about *this* object rather than about a count derived from the rows
            # afterwards, and `conflicts` names the recipe, the slot and the
            # Pantry Item that lost if it is ever not empty.
            "resolveReport": {
                "reconsidered": report.reconsidered,
                "resolved": report.resolved,
                "stillUnresolved": report.still_unresolved,
                "staleReset": report.stale_reset,
                "duplicateSlotConflicts": report.duplicate_slot_conflicts,
                "conflicts": [
                    {
                        "recipeNote": conflict.recipe_note,
                        "ingredientIndex": conflict.ingredient_index,
                        "rawValue": conflict.raw_value,
                        "pantryItemId": conflict.pantry_item_id,
                        "canonicalName": conflict.canonical_name,
                        "reason": conflict.reason,
                    }
                    for conflict in report.conflicts
                ],
            },
        },
        "recipes": dict(sorted(recipes.items())),
    }


def headline_requests(payload: dict[str, Any]) -> dict[str, Any]:
    """The `format.js` input for one payload: slots only, no counts.

    Exactly the four fields `chipClass()` reads plus the names the missing list
    falls back to. `inStock` is not among them and is not invented: this corpus
    exercises the recipe→catalog join, which never consults Pantry Stock, and
    `inStock` changes a chip's class and never its `missing` flag.
    """
    return {
        "recipes": {
            name: {
                group: [
                    {
                        "rawValue": slot["rawValue"],
                        "parsedName": slot["parsedName"],
                        "matchMethod": slot["matchMethod"],
                        "pantryItemId": slot["pantryItemId"],
                    }
                    for slot in recipe[group]
                ]
                for group in ("materials", "seasonings")
            }
            for name, recipe in payload["recipes"].items()
        }
    }


def render_headlines(payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Render every recipe's two headlines with the app's own `format.js`.

    Through `node`, because `format.js` is the implementation of F16/F17 and a
    Python copy of it would be a second answer to the same question — the exact
    failure this repository keeps refusing. The subprocess reads nothing and
    writes nothing: the request is the payload above, the answer is a dict of
    strings.
    """
    node = shutil.which("node")
    if node is None:
        raise GoldenSnapshotError(
            "node is not on PATH; the headline strings are rendered by "
            "app/static/js/logic/format.js and cannot be composed in Python"
        )
    completed = subprocess.run(  # noqa: S603
        [node, str(RENDERER)],
        input=json.dumps(headline_requests(payload), ensure_ascii=False),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        raise GoldenSnapshotError(
            f"{RENDERER.name} exited {completed.returncode}: {completed.stderr.strip()}"
        )
    return cast("dict[str, dict[str, str]]", json.loads(completed.stdout))


def check_headlines(
    payload: dict[str, Any], rendered: dict[str, dict[str, str]] | None = None
) -> list[str]:
    """Cross-check the render layer against the four frozen forms.

    Returns one line per disagreement, so a caller can print them before
    refusing. Empty means the browser and §9.13.1 agree on all 16 recipes, which
    is the state in which the golden file's strings are worth committing.

    `rendered` may be handed in so a caller that has already run the renderer
    does not run it twice.
    """
    rendered = render_headlines(payload) if rendered is None else rendered
    problems: list[str] = []
    for name, recipe in payload["recipes"].items():
        expected = {
            "headline": frozen_headline(
                found=recipe["materialsFound"],
                total=recipe["materialsTotal"],
                missing_materials=recipe["missingMaterials"],
                missing_seasonings=[],
            ),
            "headlineStrict": frozen_headline(
                found=recipe["materialsFound"] + recipe["seasoningsFound"],
                total=recipe["materialsTotal"] + recipe["seasoningsTotal"],
                missing_materials=recipe["missingMaterials"],
                missing_seasonings=recipe["missingSeasonings"],
            ),
        }
        for key, text in expected.items():
            got = rendered.get(name, {}).get(key)
            if got != text:
                problems.append(f"{name}.{key}: format.js says {got!r}, §9.13.1 says {text!r}")
    return problems


def with_headlines(
    payload: dict[str, Any], rendered: dict[str, dict[str, str]] | None = None
) -> dict[str, Any]:
    """The payload with each recipe's two rendered headline strings attached."""
    rendered = render_headlines(payload) if rendered is None else rendered
    out = dict(payload)
    out["recipes"] = {
        name: {**recipe, **rendered.get(name, {})}
        for name, recipe in payload["recipes"].items()
    }
    return out


def encode(payload: dict[str, Any], generated_at: str) -> str:
    """The committed file's bytes.

    `generated_at` is stamped in, not merely passed: it is the one field that
    records *when the file was written* rather than what it says, and the caller
    passes the file's existing date back when nothing else moved so a no-op
    regeneration does not churn the line.

    `sort_keys=True` and `indent=2` for the two reasons the catalog snapshot's
    one-row-per-line encoding has: a regeneration is reviewed as a diff, so the
    key order must not be Python's, and a slot must be readable without
    flattening the object onto one line. `ensure_ascii=False` so `空心菜` is
    stored as itself — the whole file is CJK-heavy and an escaped fixture is one
    nobody can read in a review.
    """
    stamped = {**payload, "generatedAt": generated_at}
    return json.dumps(stamped, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def load(path: Path = GOLDEN_PATH) -> dict[str, Any]:
    """The committed golden file, decoded. The file the test compares against."""
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise GoldenSnapshotError(f"{path} is not a JSON object")
    return cast("dict[str, Any]", loaded)


__all__ = [
    "FROZEN_HEADLINE_FORMS",
    "GOLDEN_PATH",
    "RENDERER",
    "CANDIDATE_KEYS",
    "SCHEMA_VERSION",
    "GoldenSnapshotError",
    "check_headlines",
    "encode",
    "frozen_headline",
    "is_missing",
    "load",
    "measure",
    "render_headlines",
    "with_headlines",
]

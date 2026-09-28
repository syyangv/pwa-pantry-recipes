"""Shared corpus fixtures and helpers for the matcher's three test files.

**Nothing here reads a live path.** The catalog is
`tests/fixtures/pantry_items_snapshot.json` — the committed 178-row dump that
`scripts/snapshot_pantry_catalog.py` regenerates deliberately in its own commit
(F14) — and the recipes are `tests/fixtures/real_recipes/*.md`, frozen copies of
the 16 real notes. The producer's `pantry_items.db` lives in a sibling checkout
that CI does not have, so a test that reached for it would pass on one machine
and assert nothing on every other one, which is worse than not asserting at all.
The synthetic catalogs are built from in-test records through the shipped
`build_snapshot()`, which is connection-free and public for exactly this reason.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Final

import pytest

from app.pantry.catalog import CatalogSnapshot, build_snapshot
from app.recipes.ingredients import parse_ingredient_value
from app.recipes.matcher import MatchResult, resolve_ingredient
from app.recipes.reader import RecipeNote, parse_recipe

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
CATALOG_SNAPSHOT: Final = REPO_ROOT / "tests" / "fixtures" / "pantry_items_snapshot.json"
REAL_RECIPES: Final = REPO_ROOT / "tests" / "fixtures" / "real_recipes"

#: 178 rows in the producer's table, 176 in the candidate universe: two carry a
#: non-food `area` and leave before any index is built. Both numbers are
#: asserted by `tests/pantry/test_catalog.py` and repeated here so a fixture
#: change that shifted them fails in this package too.
TOTAL_CATALOG_ROWS: Final = 178
CANDIDATE_ROWS: Final = 176
#: The 16 real recipe notes. `Recipes.md` is the vault's own index page, carries
#: no `材料` at all, and is deliberately not one of the 16.
RECIPE_COUNT: Final = 16
#: 26 distinct `材料` **raw** frontmatter values, which collapse to 25 distinct
#: parsed names: `🍄/香菇` and `香菇` are one food written two ways, and the
#: parse happens before the match. D1's headline figures are quoted against the
#: 26; per-name coverage is quoted against the 25.
RAW_MATERIAL_VALUES: Final = 26
PARSED_MATERIAL_NAMES: Final = 25
#: 19 distinct `调料` values, all single-token after the parse.
SEASONING_NAMES: Final = 19


def catalog_records() -> list[list[object]]:
    """The committed 178 `items` rows, exactly as the producer wrote them."""
    loaded = json.loads(CATALOG_SNAPSHOT.read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    return loaded


@lru_cache(maxsize=1)
def committed_catalog() -> CatalogSnapshot:
    """The candidate universe over the committed rows, built once per process.

    `lru_cache` rather than a fixture because two of the three test modules want
    it at collection time as well as at call time, and building 176 rows and
    their five indexes is cheap but not free.
    """
    return build_snapshot(catalog_records())


def real_notes() -> tuple[RecipeNote, ...]:
    """The 16 real recipe notes, projected from the frozen `.md` bytes."""
    notes: list[RecipeNote] = []
    for path in sorted(REAL_RECIPES.glob("*.md")):
        note = parse_recipe(
            path.read_bytes(), note_name=path.stem, note_path=f"fake/{path.name}"
        )
        if note.ingredients:
            notes.append(note)
    return tuple(notes)


def _slots(seasonings: bool) -> list[tuple[str, str]]:
    return [
        (entry.raw, "调料" if seasonings else "材料")
        for note in real_notes()
        for entry in (note.seasonings if seasonings else note.ingredients)
    ]


def real_values_for(source: str) -> list[str]:
    """The distinct **raw** frontmatter values of one key, sorted and deduplicated.

    `source` is the frontmatter key, not a boolean, because the whole point of
    the `材料` / `材料`-vs-`调料` distinction in this app is that the key is
    part of a value's meaning.
    """
    return sorted({raw for raw, key in _slots(source == "调料") if key == source})


def real_names_for(source: str) -> list[str]:
    """The distinct **parsed** names of one key, sorted and deduplicated.

    An unparseable value contributes its raw text, so a name this list does not
    expect to see is a parser change and shows up here as a surprising entry
    rather than silently disappearing.
    """
    names = set()
    for raw, key in _slots(source == "调料"):
        if key != source:
            continue
        parsed = parse_ingredient_value(raw)
        names.add(parsed.parsed_name if parsed.parsed_name is not None else raw)
    return sorted(names)


def synthetic_catalog(rows: list[tuple[int, str, str, str]]) -> CatalogSnapshot:
    """A `CatalogSnapshot` over in-test rows. No file, no connection, no live path.

    `variants` is passed through verbatim so a synthetic row can exercise tier 3
    the way the producer's own `[]` default would, and `area` is always NULL so
    the row is in the candidate universe.
    """
    return build_snapshot(
        [(row_id, name, category, variants, None) for row_id, name, category, variants in rows]
    )


def resolve(raw: str, catalog: CatalogSnapshot, **kwargs: object) -> MatchResult:
    """`parse_ingredient_value` + `resolve_ingredient`, the way a caller does it.

    Parsing and resolution are two modules and the composition lives here rather
    than in either, so a test states the *frontmatter value* it means and the
    parse method is still asserted where it matters.
    """
    return resolve_ingredient(parse_ingredient_value(raw), None, catalog, **kwargs)  # type: ignore[arg-type]


class corpus_stock:
    """A `StockSignal` over a fixed id set, so `in_stock` is testable.

    The `Protocol` is satisfied by anything exposing `in_stock_ids`, which is
    the whole contract the ladder needs — see `app.recipes.matcher.StockSignal`.
    """

    def __init__(self, ids: frozenset[int]) -> None:
        self._ids = ids

    @property
    def in_stock_ids(self) -> frozenset[int]:
        return self._ids


@pytest.fixture(scope="session")
def catalog() -> CatalogSnapshot:
    """The committed catalog, as a fixture for tests that prefer one."""
    return committed_catalog()


@pytest.fixture(scope="session")
def notes() -> tuple[RecipeNote, ...]:
    """The 16 real recipe notes, as a fixture."""
    return real_notes()

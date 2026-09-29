"""`/api/recipes` — every route, every status code, and the fail-closed 503.

Run alone:  .venv/bin/python -m pytest tests/api/test_recipes_api.py -q

**Every request here goes through the real `create_app(settings)`.** Nothing is
hand-built and no router is spliced in: the wiring is the thing under test, and a
route exercised through an app the ticket assembled itself is evidence about that
app, not about this one. `tests/scaffold/test_routes.py` pins the registration
*order*, which is the half a request cannot observe.

**The 503 section is the most important file content here, and it is written to
fail in one specific way.** The spec chose fail-closed over two fallbacks, and
both fallbacks produce a *plausible* response — a 200 whose recipes all read
`have-been-buying`, or a 200 with an empty `recipes` list. So the tests assert
the absence of the plausible answer, not just the presence of the 503: three
failure modes, and beside each one an assertion that no list, no slot, and no
count came back. A positive control closes the trap from the other side — a
`Pantry.md` that is readable and holds *nothing* is a 200 with real recipes,
because "unreadable" and "empty" are different facts and only one of them is 503.

**Every payload assertion is an exact key set.** F7 says provenance is
unconditional and F17 says no boolean is ever published, and both of those are
claims about a *set*, so a `set(body) == {...}` is the only assertion that tests
them: `assert "cookable" not in body` would pass just as happily on a response
that grew a `canCook` instead.

**Nothing here reads a live path.** The vault, the `Pantry.md`, and the catalog
are all written per test under `tmp_path` from the committed literals in
`tests/api/conftest.py`.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.recipes import (
    MAX_INGREDIENT_INDEX,
    PANTRY_DB_UNREADABLE,
    PANTRY_STOCK_UNREADABLE,
    RECIPE_NOT_FOUND,
    RESOLVE_IN_FLIGHT,
    RESOLVE_LOCK_STATE_KEY,
    build_recipes_router,
)
from app.config import Settings
from tests.api.conftest import (
    API_CATALOG_ROWS,
    DUPLICATE_RECIPE,
    DUPLICATE_RECIPE_BYTES,
    MAIN_RECIPE,
    MAIN_RECIPE_BYTES,
    ORIGIN,
    PANTRY_NOTE,
    RECIPES_ROOT,
    client_for,
    make_settings,
    seed_catalog,
    write_pantry_note,
    write_recipe,
)

#: A third note whose name sorts AFTER both shared fixtures, written from
#: `MAIN_RECIPE`'s own bytes. It exists so the enumeration order provably differs
#: from code-point order, which is what gives the "the server does not sort" test
#: teeth — see that test's docstring. It is not a new fixture: it is the same note
#: under a third name, and F2's duplicate-slot rule is per-recipe, so it cannot
#: interact with the other two.
UNSORTED_PROBE = "zzz probe"

#: §9.16's success key set for the list, plus the `skipped` count §5 requires
#: next to the other two, plus §9.13.4's `stockJoin` table. Asserted as an exact
#: set on every list test, so a payload that grew a field — or lost one — fails
#: here rather than being noticed by a user.
LIST_KEYS: frozenset[str] = frozenset(
    {
        "recipes",
        "catalogRevision",
        "stockRevision",
        "strict",
        "staleMappingCount",
        "stockUnjoinedCount",
        "skipped",
        "stockJoin",
    }
)

#: §9.13.4's per-line table. Six keys on a row, identical for a hit and a miss
#: except where the join's answer differs (`stockJoinState`, `tier`,
#: `pantryItemIds`, and the override half), so a client renders one shape.
#: `overrideName` is `null` on a plain miss by design: the server names the key
#: to paste and refuses to invent the `canonical_name` beside it.
JOIN_TABLE_KEYS: frozenset[str] = frozenset(
    {"lineCount", "unjoinedCount", "tierCounts", "guidance", "lines"}
)
JOIN_LINE_KEYS: frozenset[str] = frozenset(
    {
        "lineIndex",
        "section",
        "text",
        "core",
        "stockJoinState",
        "tier",
        "pantryItemIds",
        "overrideKey",
        "overrideName",
        "repairHint",
    }
)
JOIN_TIER_COUNT_KEYS: frozenset[str] = frozenset({"exact", "basename", "override"})

#: One recipe row's key set. `steps` and `history` are the *detail* additions and
#: must not leak into the list — a list that carried every step body would be a
#: payload nobody can page, and F20's "return every recipe" only works because
#: each row is small.
LIST_RECIPE_KEYS: frozenset[str] = frozenset(
    {"noteName", "notePath", "found", "total", "lastCooked", "ingredients", "tools"}
)

#: F7's unconditional provenance, per slot. Twelve keys, none optional, and
#: `stockJoinState` beside them because F1's own acknowledged weakness has no
#: other defence than its visibility.
SLOT_KEYS: frozenset[str] = frozenset(
    {
        "index",
        "rawValue",
        "parsedName",
        "parseMethod",
        "matchMethod",
        "matchTier",
        "pantryItemId",
        "confidence",
        "candidatesJson",
        "inStock",
        "stockJoinState",
        "isSeasoning",
    }
)

#: §9.16's `PUT` row. `createdAt` / `updatedAt` are here and not on a slot: "when
#: did this row last change" is a fact about a write, and the chip row is not one.
MAPPING_KEYS: frozenset[str] = frozenset(
    {
        "recipeNote",
        "ingredientIndex",
        "rawValue",
        "parsedName",
        "parseMethod",
        "matchMethod",
        "matchTier",
        "pantryItemId",
        "confidence",
        "candidatesJson",
        "createdAt",
        "updatedAt",
    }
)

#: §9.10.1's resolve report, plus the conflict entries §9.16 requires to travel
#: with the count.
REPORT_KEYS: frozenset[str] = frozenset(
    {
        "reconsidered",
        "resolved",
        "stillUnresolved",
        "staleReset",
        "duplicateSlotConflicts",
        "conflicts",
    }
)

CONFLICT_KEYS: frozenset[str] = frozenset(
    {"recipeNote", "ingredientIndex", "rawValue", "pantryItemId", "canonicalName", "reason"}
)

#: The catalog id a hand fix points at: `豆腐` (6), which no `材料` slot in the
#: fixture recipe already claims, so the re-map is legal. `HELD_SLOT_ITEM_ID`
#: (3) is the one slot 2 already holds, and pointing a second slot at it is F2's
#: inverted index — a 409, not a 500.
FREE_ITEM_ID: int = 6
HELD_SLOT_ITEM_ID: int = 3

#: The three ways F1 names an unreadable source.
ABSENT: str = "absent"
UNREADABLE: str = "unreadable"
UNPARSEABLE: str = "unparseable"

#: Not a UTF-8 byte sequence, so `parse_sections` raises `SectionError` and
#: `parse_pantry` raises `PantryError("pantry_unparseable")`. Bytes rather than a
#: broken heading on purpose: a heading malformation is a parser detail that could
#: be reclassified, whereas "this file is not text" is a fact about the file that
#: no parser change can talk its way out of.
NOT_UTF8: bytes = b"---\nmodified_at: \xff\xfe not text\n---\n# 1 \xba\xf3\n"

#: A note whose frontmatter names `材料` twice, which `RecipeIndex` refuses rather
#: than letting PyYAML's last-one-wins silently drop a list of Ingredients.
UNREADABLE_RECIPE: bytes = (
    "---\n材料:\n  - 番茄\n材料:\n  - 鸡蛋\n---\n# 步骤\n1. \n"
).encode()


@pytest.fixture
def settings(runtime_root: Path) -> Settings:
    """A catalog the domain fixtures will read, seeded before the app is built."""
    configured = make_settings(runtime_root)
    seed_catalog(configured.pantry_items_db, API_CATALOG_ROWS)
    return configured


@pytest.fixture
def vault(settings: Settings) -> Path:
    return settings.vault_path


@pytest.fixture
def note(vault: Path) -> Path:
    return write_recipe(vault, MAIN_RECIPE)


@pytest.fixture
def stock(vault: Path) -> Path:
    return write_pantry_note(vault)


@pytest.fixture
def client(settings: Settings, note: Path, stock: Path) -> Iterator[TestClient]:
    """The real app, lifespan running, with one recipe and a readable pantry."""
    with client_for(settings) as test_client:
        yield test_client


def _csrf(test_client: TestClient) -> str:
    """The token `GET /api/session` hands out, taken from the real route.

    Read through the route rather than off `app.state.csrf` so the whole boot
    contract — session → token → guard → route — is what the mutation tests
    depend on, and so no test can pass against a token the app would not issue.
    """
    return str(test_client.get("/api/session").json()["csrfToken"])


def _mutation(test_client: TestClient, **extra: str) -> dict[str, str]:
    return {"Origin": ORIGIN, "X-CSRF-Token": _csrf(test_client), **extra}


def _slot(recipe: dict[str, Any], index: int) -> dict[str, Any]:
    """The `材料` slot at `index` — seasonings reuse indices, so the caller says
    which list it is in and this only narrows the lookup."""
    return next(slot for slot in recipe["ingredients"] if slot["index"] == index)


def _by_value(recipe: dict[str, Any], raw: str) -> dict[str, Any]:
    return next(slot for slot in recipe["ingredients"] if slot["rawValue"] == raw)


def _names(body: dict[str, Any]) -> list[str]:
    return [recipe["noteName"] for recipe in body["recipes"]]


def _scandir_recipe_names(vault: Path) -> list[str]:
    """The recipe names in the order `app/recipes/reader.py` will enumerate them.

    Mirrors the reader's own admission rule — a `.md` suffix, not dot-prefixed,
    a regular file — so the comparison below is "the server returned the reader's
    order", not "the server returned whatever `ls` says". Deliberately a bare
    `os.scandir` with **no sort**: sorting here would re-introduce the very
    platform assumption that made the hardcoded version of this assertion fail on
    Linux, and it would hide a server that started sorting.
    """
    root = vault / RECIPES_ROOT
    with os.scandir(root) as entries:
        return [
            entry.name[: -len(".md")]
            for entry in entries
            if entry.name.lower().endswith(".md")
            and not entry.name.startswith(".")
            and entry.is_file(follow_symlinks=False)
        ]


# --- the list ---------------------------------------------------------------


def test_the_list_carries_exactly_the_specified_keys(client: TestClient) -> None:
    response = client.get("/api/recipes")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == LIST_KEYS
    assert body["strict"] == 0
    assert body["catalogRevision"].startswith("sha256:")
    assert body["stockRevision"].startswith("sha256:")
    assert _names(body) == [MAIN_RECIPE]
    assert set(body["recipes"][0]) == LIST_RECIPE_KEYS


def test_the_list_carries_provenance_on_every_single_slot(client: TestClient) -> None:
    """F7: unconditional. Six slots, six complete provenance records.

    Asserted per slot rather than on the first one, because "the first slot has
    provenance" is exactly the shape a partially-implemented F7 takes.
    """
    recipe = client.get("/api/recipes").json()["recipes"][0]

    assert len(recipe["ingredients"]) == 6
    for slot in recipe["ingredients"]:
        assert set(slot) == SLOT_KEYS, slot
        assert slot["matchMethod"]
        assert isinstance(slot["matchTier"], int)
        assert isinstance(slot["confidence"], float)
        assert isinstance(slot["candidatesJson"], list)
        assert slot["stockJoinState"] in {"joined", "override", "unresolved"}
    # F7 again, from the other side: the audit is not a summary of refusals
    # either. A slot the ladder adopted still carries the row it adopted, with
    # the name and the id, so the provenance view can name what was chosen and not
    # only what was turned down.
    adopted = _by_value(recipe, "番茄")["candidatesJson"]
    assert len(adopted) == 1
    assert set(adopted[0]) == {
        "pantryItemId",
        "canonicalName",
        "pantryCategory",
        "family",
        "relation",
        "reason",
        "rejectedBy",
    }
    assert adopted[0]["pantryItemId"] == 1
    assert adopted[0]["canonicalName"] == "番茄"


def test_the_order_is_vault_enumeration_order_and_is_not_sorted(
    settings: Settings, vault: Path, stock: Path
) -> None:
    """D4's sort is client-side (§9.13.3); the server does not pre-empt it.

    Two recipes come back in the order the folder holds them, not in the order
    their headlines would rank them. A server-side sort would be a second sort to
    keep correct, and the two would disagree the moment D4's rules changed.

    Both notes are written **before** the app boots, and that is not incidental:
    `RecipeIndex` holds a `recipe_cache_seconds` (60 s) snapshot, so a note added
    while the app is running is invisible for up to a minute. That is the designed
    staleness of a projection the user edits in Obsidian, and the reason the
    fixture cannot add a note mid-test and expect it to appear.

    **The expected order is read from `os.scandir`, not hardcoded, and that is
    the fix rather than a softening.** `app/recipes/reader.py` enumerates with a
    bare `os.scandir(self._root)` and deliberately does not sort, so "enumeration
    order" is by definition whatever the filesystem hands back. A literal
    `[MAIN_RECIPE, DUPLICATE_RECIPE]` therefore asserted *the test runner's
    filesystem*, and it failed on `ubuntu-latest` for exactly that reason while
    passing on macOS: the server was right on both.

    **AND THE TEST IS NOT ALLOWED TO BE VACUOUS.** Comparing against `scandir`
    only detects a server-side sort when the enumeration *differs* from code-point
    order — and on a filesystem where the two coincide, the old assertion and this
    one are equally blind. So a third note (`zzz probe`, written from the same
    bytes) is added to force a difference, and if the filesystem still enumerates
    in sorted order the test **skips and says so** instead of reporting a green it
    has not earned. The three outcomes are then distinct and each means one thing:
    a pass means "did not sort", a failure means "sorted", and a skip means "this
    filesystem cannot tell". A silently-vacuous pass is the one outcome this
    cannot produce.
    """
    write_recipe(vault, MAIN_RECIPE)
    write_recipe(vault, DUPLICATE_RECIPE, DUPLICATE_RECIPE_BYTES)
    write_recipe(vault, UNSORTED_PROBE, MAIN_RECIPE_BYTES)
    with client_for(settings) as client:
        body = client.get("/api/recipes").json()

    enumerated = _scandir_recipe_names(vault)
    assert sorted(enumerated) == sorted([MAIN_RECIPE, DUPLICATE_RECIPE, UNSORTED_PROBE])
    if enumerated == sorted(enumerated):
        pytest.skip(
            "this filesystem enumerates in code-point order, so a server-side sort "
            "would be indistinguishable from the reader's order here"
        )
    assert _names(body) == enumerated
    # A `1/2` recipe is a first-class row. D4 forbids a threshold, a collapse and
    # a "show more", and the only way to assert that is to show the worse one.
    # `MAIN_RECIPE` scores `2/4` because `空心菜` is bought-and-finished and
    # `🐟 不存在的鱼` resolved to nothing; `DUPLICATE_RECIPE` scores `1/2` because
    # F2's duplicate-slot catch downgrades its second `番茄` and the pantry holds
    # that one open. Neither row is hidden or collapsed. Compared as a set,
    # because the order is the thing under test and must not be assumed here.
    assert sorted(r["found"] / r["total"] for r in body["recipes"]) == [0.5, 0.5, 0.5]


def test_no_response_in_any_mode_publishes_a_cookable_boolean(client: TestClient) -> None:
    """F17, asserted recursively, over every response this router can produce.

    A top-level `"cookable" not in body` would miss a per-slot one, a per-recipe
    one, and a `canCook` / `isCookable` rename of the same idea. The walk below
    covers every dict and every list in the payload, so the only way to satisfy it
    is to publish no single-boolean summary anywhere — which is the decision, not
    an implementation detail of one field name.
    """
    for response in (
        client.get("/api/recipes"),
        client.get("/api/recipes", params={"strict": 1}),
        client.get(f"/api/recipes/{MAIN_RECIPE}"),
        client.get(f"/api/recipes/{MAIN_RECIPE}", params={"strict": 1}),
    ):
        assert response.status_code == 200, response.text
        _assert_no_boolean_summary(response.json(), where=response.request.url.path)


def _assert_no_boolean_summary(payload: object, *, where: str) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert key not in {"cookable", "canCook", "isCookable", "readyToCook"}, (
                f"{where} publishes a single-boolean summary as {key!r}"
            )
            _assert_no_boolean_summary(value, where=f"{where}.{key}")
    elif isinstance(payload, list):
        for position, value in enumerate(payload):
            _assert_no_boolean_summary(value, where=f"{where}[{position}]")


# --- the headline: F17's n/total, and F16's strict addendum ------------------


def test_the_default_headline_counts_materials_only(client: TestClient) -> None:
    """`2/4`, and the denominator is four slots, not a free literal (§9.13.1).

    **The numerator counts open stock, not resolutions, and this fixture is the
    case that proves it.** `空心菜` resolves through the synonym tier to
    `空心菜嫩苗` (3) and that product is a **`[x]` finished line** in
    `Pantry.md`, so it publishes `inStock: false` and is missing: a product the
    household bought once and finished is not on the shelf. Only `番茄` and
    `鸡蛋` count. `🐟 不存在的鱼` matches nothing at all, so the two missing
    Materials fail for two different reasons that the payload keeps apart
    (`pantryItemId` null versus an id with `inStock: false`).

    The two Seasonings are present in the payload and deliberately **not** in the
    arithmetic — that is F17, and it is the reason a `调料` name can never appear
    in the default missing list.
    """
    recipe = client.get("/api/recipes").json()["recipes"][0]

    assert (recipe["found"], recipe["total"]) == (2, 4)
    空心菜 = _by_value(recipe, "空心菜")
    assert (空心菜["pantryItemId"], 空心菜["inStock"]) == (
        3,
        False,
    ), "resolved, bought, finished — and still missing"
    assert [s["rawValue"] for s in recipe["ingredients"] if s["isSeasoning"]] == ["生抽", "盐"]
    assert _by_value(recipe, "生抽")["matchMethod"] == "staples"
    assert _by_value(recipe, "生抽")["matchTier"] == 7


def test_strict_adds_the_seasonings_and_a_staple_satisfied_one_still_counts(
    client: TestClient,
) -> None:
    """F16: `4/6`. Only an *unresolved* Seasoning is missing, and the staples
    tier is why this toggle is not useless.

    Pantry Category `1.1c` has one row in 178, so a Seasoning could almost never
    be resolved against the catalog; counting those as missing would drive every
    recipe's strict score to near zero. Here both Seasonings are adopted by the
    staples tier and both count as found, which is the arithmetic F16 locks in.
    """
    recipe = client.get("/api/recipes", params={"strict": 1}).json()["recipes"][0]

    assert (recipe["found"], recipe["total"]) == (4, 6)
    assert not any(
        _is_missing(slot, strict=True) for slot in recipe["ingredients"] if slot["isSeasoning"]
    )


def test_a_seasoning_slot_is_resolved_and_never_materialized(
    settings: Settings, vault: Path, stock: Path
) -> None:
    """Why a `调料` row has a `matchMethod` at all, asserted as a consequence.

    `ingredient_mappings.ingredient_index` is a `材料` index by schema, so a
    Seasoning has no row to read — and nothing may invent one, because that would
    put a frontmatter string into an audit anchor the app never read. So the
    Seasoning's answer is derived per response by calling the pure ladder with
    `source="调料"`, which is the only call to it outside the store, and F16 is
    what it is for.

    Two assertions make the whole design observable. First, the answer is a real
    tier-7 adoption with a real confidence — not a placeholder. Second, the table
    still holds only the four Material rows, so the derivation really is
    per-response and not a write in disguise: `mappings.unresolvedCount` counts one
    unresolved row, and it is the one *Material* the ladder could not resolve.
    """
    write_recipe(vault, MAIN_RECIPE)
    with client_for(settings) as client:
        body = client.get("/api/recipes").json()
        seasonings = [s for s in body["recipes"][0]["ingredients"] if s["isSeasoning"]]
        health = client.get("/health").json()

    assert [s["matchMethod"] for s in seasonings] == ["staples", "staples"]
    assert [s["matchTier"] for s in seasonings] == [7, 7]
    assert all(s["pantryItemId"] is None for s in seasonings)
    assert all(s["parseMethod"] == "bare" for s in seasonings)
    # Four Material rows materialized; one of them unresolved. Had a `调料` row
    # been written, this would be 2 and the schema's `材料`-index claim would be
    # false.
    assert health["mappings"]["unresolvedCount"] == 1


def _is_missing(slot: dict[str, Any], *, strict: bool) -> bool:
    """`app/static/js/logic/chip-class.js`'s ladder, restated as the test's oracle.

    Restated rather than imported on purpose: the module under test computes
    `found` from these very fields, so importing its helper would make the
    assertion circular. The two must agree — the JS half is frozen for the
    rendered string by `tests/js/logic/format.test.mjs`.
    """
    if slot["matchMethod"] in {"manual", "staples"}:
        return False
    if slot["pantryItemId"] is not None:
        # Resolved is not the same as held: a Pantry Item with no open line is
        # missing, which is the whole of the stock rule this oracle restates.
        return not slot["inStock"]
    return not (slot["isSeasoning"] and not strict)


def test_strict_is_a_stateless_query_parameter(client: TestClient) -> None:
    """F8: the flag is the request and nothing else.

    Two reads, one strict and one not, from the same app with no write between
    them and no cookie, no header and no SQLite row carrying the preference. A
    persisted setting would need a round-trip before the first paint and would
    create a second source of truth.
    """
    first = client.get("/api/recipes", params={"strict": 1}).json()
    second = client.get("/api/recipes").json()
    third = client.get("/api/recipes", params={"strict": 1}).json()

    assert (first["strict"], second["strict"]) == (1, 0)
    assert third == first
    assert (first["recipes"][0]["found"], second["recipes"][0]["found"]) == (4, 2)


@pytest.mark.parametrize("value", ["2", "-1", "yes", "true", ""])
def test_a_strict_that_is_not_zero_or_one_is_422(client: TestClient, value: str) -> None:
    """A value the flag does not have is a malformed request, not "off".

    `?strict=7` silently meaning "off" would make the parameter's value not
    matter, and a headline whose accuracy depends on a parameter nobody has to
    get right is a headline nobody can rely on.
    """
    response = client.get("/api/recipes", params={"strict": value})
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert set(response.json()) == {"requestId", "code"}


# --- F1: the fail-closed 503, in all three failure modes --------------------


def _break(vault: Path, mode: str) -> Path:
    """Put `Pantry.md` into failure mode `mode` and return the file to restore."""
    target = vault / PANTRY_NOTE
    if mode == ABSENT:
        target.unlink(missing_ok=True)
        return target
    if mode == UNREADABLE:
        write_pantry_note(vault)
        os.chmod(target, 0)
        return target
    write_pantry_note(vault, NOT_UTF8)
    return target


def _restore(target: Path, mode: str) -> None:
    """Undo `_break` so a failing assertion cannot cascade into the next test."""
    if mode == UNREADABLE:
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
    target.unlink(missing_ok=True)


def _root_can_read_mode_zero() -> bool:
    """Whether a mode-`0` file is actually unreadable for this uid.

    Under `sudo` the "unreadable" case would silently become a *readable* one and
    the test would assert a 200 while claiming a 503, which is worse than skipping.
    """
    return hasattr(os, "geteuid") and os.geteuid() == 0


@pytest.mark.parametrize("mode", [ABSENT, UNREADABLE, UNPARSEABLE])
def test_an_unreadable_pantry_note_is_503_on_both_read_routes(
    settings: Settings, note: Path, mode: str
) -> None:
    """F1's fail-closed branch, once per failure mode, on both routes that join.

    The blast radius is why this is parametrized: an unreadable `Pantry.md` takes
    out **every** recipe's chip colour, not one, so both the list and the detail
    must refuse.
    """
    if mode == UNREADABLE and _root_can_read_mode_zero():
        pytest.skip("root reads a mode-0 file; the unreadable mode needs a non-root uid")
    with client_for(settings) as client:
        target = _break(settings.vault_path, mode)
        try:
            for path in ("/api/recipes", f"/api/recipes/{MAIN_RECIPE}"):
                response = client.get(path)
                assert response.status_code == 503, f"{mode} / {path}: {response.text}"
                assert response.json()["code"] == PANTRY_STOCK_UNREADABLE
        finally:
            _restore(target, mode)


@pytest.mark.parametrize("mode", [ABSENT, UNREADABLE, UNPARSEABLE])
def test_a_degraded_no_items_held_list_is_never_returned(
    settings: Settings, note: Path, mode: str
) -> None:
    """**The assertion whose absence would let the 503 quietly become a 200.**

    The two rejected fallbacks both produce a *plausible* body: "assume not in
    stock" serves the list with every chip reading `have-been-buying`, and
    "assume in stock" serves it with every chip green. Neither is an error, so a
    test that only checked the status could be made to pass by an implementation
    that quietly served a degraded list instead. So the body itself is the
    assertion: no `recipes` key, no slot, no count, and the two-key envelope —
    nothing a client could render as "these are your recipes".
    """
    if mode == UNREADABLE and _root_can_read_mode_zero():
        pytest.skip("root reads a mode-0 file; the unreadable mode needs a non-root uid")
    with client_for(settings) as client:
        target = _break(settings.vault_path, mode)
        try:
            response = client.get("/api/recipes")
            body = response.json()
            assert response.status_code == 503
            assert set(body) == {"requestId", "code"}
            assert body["code"] == PANTRY_STOCK_UNREADABLE
            # Each plausible degraded answer, named and refused.
            assert "recipes" not in body
            assert "stockUnjoinedCount" not in body
            assert "stockRevision" not in body
            assert "have-been-buying" not in response.text
            assert "chip--" not in response.text
            assert MAIN_RECIPE not in response.text
        finally:
            _restore(target, mode)


def test_a_readable_but_empty_pantry_is_a_200_with_real_recipes(
    settings: Settings, note: Path
) -> None:
    """The positive control, and the reason the 503 above cannot be an empty 200.

    A `Pantry.md` that parses and holds nothing is a real answer: "you have
    nothing in the pantry", which renders every resolved slot
    `have-been-buying` — correctly, and for a reason the user can act on. If this
    were also a 503 the app would be unusable on an empty shelf; if the 503 above
    were a 200 the app would be confidently wrong on a broken file. The two cases
    differ only in whether the note could be read, and both are asserted.
    """
    with client_for(settings) as client:
        write_pantry_note(
            settings.vault_path,
            "---\nmodified_at: 2026-09-27\n---\n# 1 冰箱\n".encode(),
        )
        response = client.get("/api/recipes")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == LIST_KEYS
    assert _names(body) == [MAIN_RECIPE]
    assert body["stockUnjoinedCount"] == 0
    assert body["stockRevision"].startswith("sha256:")
    recipe = body["recipes"][0]
    # The identity half survives — the recipe still knows which product it wants.
    # Only the *stock* half is unavailable, and it says so rather than guessing.
    assert _by_value(recipe, "番茄")["pantryItemId"] == 1
    assert _by_value(recipe, "番茄")["inStock"] is False
    assert _by_value(recipe, "番茄")["stockJoinState"] == "unresolved"
    # Nothing is in stock, so nothing is counted as found — the headline follows
    # the shelf, not the catalog, so an empty pantry reads `0/4` rather than
    # `3/4`. This is the one shape that is *both* a 200 and every chip
    # downgraded, and it is honest, because the note said so.
    assert recipe["found"] == 0
    assert recipe["total"] == 4


def test_the_503_leaks_no_absolute_path(settings: Settings, note: Path, vault: Path) -> None:
    """F1's `PantryError` carries the vault-relative path; §9.19's rule still holds.

    The envelope is public and an absolute path there would disclose the server's
    filesystem layout to anyone who can reach the port. The code is deliberately
    generic — §9.16 widens the envelope for exactly two codes, both the Cooking
    Log's, and nothing here may add a `message` — so the diagnostic the user gets
    is the UI's own copy of the note name, and the server contributes only a
    stable, path-free code.
    """
    with client_for(settings) as client:
        response = client.get("/api/recipes")
        body = response.json()

    assert response.status_code == 503
    assert set(body) == {"requestId", "code"}
    assert str(vault) not in response.text
    assert str(settings.app_data_dir) not in response.text
    assert PANTRY_NOTE not in response.text


def test_an_unreadable_catalog_is_503_pantry_db_unreadable(
    settings: Settings, note: Path, stock: Path
) -> None:
    """The *other* 503, and it is a different code from the stock one.

    The two sources fail for different reasons and are fixed by different user
    actions, so a UI cannot render them from one branch. Corrupting the file is
    what makes `mode=ro` SQLite refuse it; the app never writes to the catalog, so
    the corruption is by hand and the fixture is per-test.
    """
    settings.pantry_items_db.write_bytes(b"this is not a SQLite database")
    with client_for(settings) as client:
        for path in ("/api/recipes", f"/api/recipes/{MAIN_RECIPE}"):
            response = client.get(path)
            assert response.status_code == 503, path
            assert response.json()["code"] == PANTRY_DB_UNREADABLE
            assert set(response.json()) == {"requestId", "code"}
        # A resolvable failure is a 503 on the write route too, and the catalog is
        # the one source that route reads.
        assert client.post("/api/recipes/resolve", headers=_mutation(client)).status_code == 503
        # `/health` still answers: the probe a user reads to find out *why* must
        # not be the thing that stops answering.
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["pantry_db"]["rowCount"] is None


# --- F1's join, and the two facts it must not conflate ----------------------


def test_a_resolved_but_not_held_slot_is_not_in_stock_and_join_unresolved(
    client: TestClient,
) -> None:
    """F1's measured evidence, in one fixture: `空心菜` → id 3, and `[x]`.

    The recipe resolves to Pantry Item 3, and `Pantry.md` holds 3's line as
    **done** — bought before, not on the shelf. A catalog-only answer would render
    this `chip--in-stock`; the two fields below are what prevent it, and they are
    asserted separately because they answer different questions. `pantryItemId` is
    the recipe→catalog resolution; `stockJoinState` is the `Pantry.md`→catalog
    join, which never saw the line at all.
    """
    slot = _by_value(client.get("/api/recipes").json()["recipes"][0], "空心菜")

    assert slot["matchMethod"] == "synonym"
    assert slot["pantryItemId"] == 3
    assert slot["inStock"] is False
    assert slot["stockJoinState"] == "unresolved"
    assert slot["confidence"] == 0.7


def test_a_line_the_catalog_explains_reports_the_join_that_explained_it(
    client: TestClient,
) -> None:
    """`joined` for the two open lines, and `inStock: true` beside them.

    The join state and the stock flag agree here and they are still separate
    fields, which is the point: `joined` says *the join explained this product*,
    `inStock` says *and it is on the shelf*. A product the join explained on the
    override tier would be `override` and equally in stock.
    """
    recipe = client.get("/api/recipes").json()["recipes"][0]

    for name, item_id in (("番茄", 1), ("鸡蛋", 2)):
        slot = _by_value(recipe, name)
        assert slot["stockJoinState"] == "joined", name
        assert slot["inStock"] is True, name
        assert slot["pantryItemId"] == item_id, name
    unmatched = _by_value(recipe, "🐟 不存在的鱼")
    assert unmatched["stockJoinState"] == "unresolved"
    assert unmatched["pantryItemId"] is None


def test_stock_unjoined_count_surfaces_f1s_misses_without_the_debug_view(
    client: TestClient,
) -> None:
    """F1's escape hatch is load-bearing, and the counter is how it is noticed.

    Two of the five pantry lines resolve to nothing in the catalog, so
    `stockUnjoinedCount` is 2 without the client opening the provenance view —
    and §9.13.4's table, which now ships in the same payload, must agree with it
    row for row. The agreement is the assertion: a table from a second read of
    `Pantry.md` could show one miss while this counter said two, and two numbers
    about one join that disagree is worse than no second number.
    """
    body = client.get("/api/recipes").json()

    assert body["stockUnjoinedCount"] == 2
    assert _by_value(body["recipes"][0], "🐟 不存在的鱼")["pantryItemId"] is None
    table = body["stockJoin"]
    assert table["unjoinedCount"] == 2 == body["stockUnjoinedCount"]
    unresolved = [line for line in table["lines"] if line["stockJoinState"] == "unresolved"]
    assert len(unresolved) == 2 == body["stockUnjoinedCount"]


def test_a_pantry_line_travels_only_inside_the_stock_join_table(client: TestClient) -> None:
    """A pantry line reaches the wire, and **only** inside `stockJoin`.

    This test used to assert the opposite — that no line text is published
    anywhere — which was the state before §9.13.4's table existed and is exactly
    what this ticket exists to change. The claim that survived the change is the
    one worth keeping, and it is a *scope* claim rather than an absence claim: a
    `Pantry.md` line is the user's own free text, and it belongs in the one
    structure that is about lines and nowhere else. A line leaking into a
    recipe's `rawValue`, into `notePath`, or into a top-level key would be a
    pantry task masquerading as a recipe Ingredient, and the client cannot tell
    the difference once it is a string in the wrong field.
    """
    body = client.get("/api/recipes").json()

    # `完全不存在的商品` is a pantry line and no recipe's `材料`, so finding it
    # anywhere outside the table is the whole assertion. `🐟 不存在的鱼` is
    # deliberately **not** the probe: it is also slot 3 of the fixture recipe, and
    # a recipe's own `rawValue` is the one string here that has to be there —
    # D1's audit anchor, and a line whose two homes are indistinguishable in a
    # substring search.
    pantry_only = "完全不存在的商品"
    table_texts = [line["text"] for line in body["stockJoin"]["lines"]]
    assert pantry_only in table_texts, "the table does not carry the line at all"
    assert set(body["recipes"][0]["ingredients"][0]) == SLOT_KEYS

    without_table = {key: value for key, value in body.items() if key != "stockJoin"}
    assert pantry_only not in json.dumps(without_table, ensure_ascii=False)

    # The note's own HEADINGS are the other half: a section number is published
    # (`section`, which is `1`, not a path) but the title the user wrote beside it
    # is not, because the title is not what identifies a line for a repair.
    assert "冰箱" not in json.dumps(body["stockJoin"], ensure_ascii=False)
    for section in (line["section"] for line in body["stockJoin"]["lines"]):
        assert section in {"1", "2"}


# --- §9.13.4's per-line Stock Join table --------------------------------------


def test_the_join_table_carries_every_product_line_with_its_own_provenance(
    client: TestClient,
) -> None:
    """The whole join, in note order, with a real hit and a real miss in it.

    **The row shape is asserted as an exact set**, hit and miss alike, because a
    client that renders one shape cannot render a miss that grew a key: the
    `unresolved` row is the actionable one and a conditional key on it is a
    crash on the row that matters most.
    """
    table = client.get("/api/recipes").json()["stockJoin"]

    assert set(table) == JOIN_TABLE_KEYS
    assert set(table["tierCounts"]) == JOIN_TIER_COUNT_KEYS
    for line in table["lines"]:
        assert set(line) == JOIN_LINE_KEYS, line

    # Five open lines in the fixture note, one of which is a `k/N`-free product
    # line per row; the `[x]` 空心菜嫩苗 row is `done` and therefore not a line
    # the join sees at all. `lineCount` counts hits AND misses — a miss rate
    # computed over resolved lines only reads 0% however badly the join is doing.
    assert table["lineCount"] == len(table["lines"]) == 4
    assert sum(table["tierCounts"].values()) == table["lineCount"] - table["unjoinedCount"]

    # Note order, so a row's position on screen is its position in the vault.
    indexes = [line["lineIndex"] for line in table["lines"]]
    assert indexes == sorted(indexes)
    assert len(set(indexes)) == len(indexes), "one note line produced two rows"

    joined = [line for line in table["lines"] if line["stockJoinState"] == "joined"]
    misses = [line for line in table["lines"] if line["stockJoinState"] == "unresolved"]
    assert joined and misses, "the fixture must exercise both answers"
    # The catalog is read-only to this app, so every resolved id is one the
    # catalog really holds — a join that invented an id would be the confident
    # wrong answer this whole module exists to avoid.
    assert {line["text"]: line["pantryItemIds"] for line in joined} == {
        "番茄": [1],
        "鸡蛋": [2],
    }
    for line in misses:
        assert line["pantryItemIds"] == [], line
        assert line["tier"] == 0, line


def test_a_miss_row_names_the_key_to_paste_and_refuses_to_invent_the_name(
    client: TestClient,
) -> None:
    """§9.13.4's actionable row, and the honesty constraint on it.

    The spec asks each `unresolved` row to name the `canonical_name` to add. The
    server cannot know one: this join has three tiers and no fourth, a miss is
    by definition a line no tier could explain, and naming a value would be a
    fourth tier in disguise. So the row names the half the server *does* know —
    `overrideKey`, the normalized core the file is keyed by — and its
    `repairHint` says plainly that the value is the user's reviewed choice. The
    test pins both halves, because the tempting failure is a server that fills in
    a plausible name to make the row look complete.
    """
    table = client.get("/api/recipes").json()["stockJoin"]
    misses = [line for line in table["lines"] if line["stockJoinState"] == "unresolved"]

    for line in misses:
        assert line["overrideName"] is None, "the server invented a canonical_name"
        assert line["overrideKey"] == line["core"], "the key to paste is not the core"
        # The key is in the form `load_line_overrides` accepts: a raw line pasted
        # as a key produces an entry that can never fire, and the loader refuses
        # to start on one.
        assert line["overrideKey"] == line["overrideKey"].strip().casefold()
        hint = line["repairHint"]
        assert isinstance(hint, str) and hint, line
        assert "app/pantry/line_overrides.yaml" in hint, hint
        assert line["overrideKey"] in hint, "the hint does not name the key to paste"
        # The refusal, stated rather than implied. A miss the server had "helpfully"
        # filled in would read as a repair and be one.
        assert "canonical_name" in hint and "不替你猜" in hint, hint

    # A HIT publishes no hint at all: there is nothing to repair, and a hint on
    # a resolved row would invite a repair of a line the join already explained.
    for line in table["lines"]:
        if line["stockJoinState"] != "unresolved":
            assert line["repairHint"] is None, line
            assert line["overrideName"] is None, line


def test_a_stale_override_reports_the_name_it_tried(settings: Settings, note: Path) -> None:
    """An override that fired and did *not* resolve names the name it tried.

    A distinct failure from a plain miss, and worth reporting: the key is in the
    reviewed file, the value is a name the live catalog does not hold, and
    nothing else in the app would ever say so. The remedy is also different —
    fix or delete the existing entry, do not add a second one.
    """
    stale = (
        "---\nmodified_at: 2026-09-27\n---\n# 1 冰箱\n"
        "- [ ] 一个目录里没有的商品 💵 $1.00 ➕ 2026-09-22\n"
    ).encode()
    write_pantry_note(settings.vault_path, stale)
    import app.pantry.stock as stock_module

    original = stock_module.LINE_OVERRIDES
    # An entry whose value is a real name for nothing: the loader's own schema
    # accepts it, and the join is what discovers it does not resolve.
    stock_module.LINE_OVERRIDES = {"一个目录里没有的商品": "A Product That Does Not Exist"}
    try:
        with client_for(settings) as client:
            table = client.get("/api/recipes").json()["stockJoin"]
    finally:
        stock_module.LINE_OVERRIDES = original

    line = next(row for row in table["lines"] if row["stockJoinState"] == "unresolved")
    assert line["overrideName"] == "A Product That Does Not Exist"
    assert "A Product That Does Not Exist" in line["repairHint"]
    assert "查不到" in line["repairHint"], "a stale entry is not a missing one"
    assert line["tier"] == 0 and line["pantryItemIds"] == []


def test_a_duplicate_name_publishes_both_candidate_ids(settings: Settings, note: Path) -> None:
    """Eight catalog keys hold two rows each, and the table shows both.

    §9.13.4 requires it so the ambiguity is visible *before* it becomes a wrong
    chip. A single id here would have been a winner chosen by insertion order, and
    the loser would have vanished with no diagnostic — the one failure a name
    lookup must never have.
    """
    rows = (
        (1, "番茄", "1.1", "[]", None),
        (2, "鸡蛋", "1.1d", "[]", None),
        (3, "空心菜嫩苗", "1.1", "[]", None),
        (4, "李锦记 蒸鱼豉油 14 盎司", "1.1c", "[]", None),
        (5, "Shampoo", "3.1", "[]", "Shampoo"),
        (6, "豆腐", "1.1", "[]", None),
        # Two rows that reduce to the SAME product core, so one pantry line
        # reaches both and the union in `_resolve` has to keep them both. The
        # second row's name carries a size the stripper removes, which is the
        # real shape of a re-ingested duplicate (the same product bought twice,
        # catalogued under two names) rather than two arbitrary rows.
        (7, "小白菜心", "1.1", "[]", None),
        (8, "小白菜心 300 克", "1.1", "[]", None),
    )
    seed_catalog(settings.pantry_items_db, rows)
    write_pantry_note(
        settings.vault_path,
        ("---\nmodified_at: 2026-09-27\n---\n# 1 冰箱\n- [ ] 小白菜心 300 克\n").encode(),
    )
    with client_for(settings) as client:
        table = client.get("/api/recipes").json()["stockJoin"]

    line = next(row for row in table["lines"] if row["stockJoinState"] == "joined")
    assert line["pantryItemIds"] == [7, 8], "one candidate was dropped"
    # `text` is the line as written and `core` is what the index was asked with,
    # and the two are different strings on purpose: the size survived the line and
    # not the core, which is exactly the asymmetry the basename tier exists for.
    assert line["text"] == "小白菜心 300 克"
    assert line["core"] == "小白菜心"
    # The join reported the BEST tier that fired, and the key it looked up, so a
    # reader can see that one line reached a key two catalog rows hold.
    assert line["tier"] == 1
    assert line["overrideKey"] == "小白菜心"


def test_the_join_table_publishes_no_path_and_no_boolean(
    client: TestClient, runtime_root: Path
) -> None:
    """Two whole-payload claims, asserted by walking the JSON rather than the top
    level — a nested `notePath` or a boolean three levels down is exactly what a
    `set(body) == LIST_KEYS` cannot see.

    No absolute path: the line text is the user's own, the section is the note's
    own heading number, and neither may ever carry server-owned filesystem layout
    (§9.19). No boolean: F17 forbids a `cookable` in any response in any casing,
    and a per-line table is 45 more rows in which one could hide.
    """
    body = client.get("/api/recipes").json()
    encoded = json.dumps(body, ensure_ascii=False)

    # The server's own root, spelled out rather than pattern-matched: the vault
    # and the catalog DB both live under it, so this is the one string that would
    # appear in a leaked path and in nothing else.
    assert str(runtime_root) not in encoded, "an absolute server path is on the wire"
    for line in body["stockJoin"]["lines"]:
        for key, value in line.items():
            assert not str(value).startswith("/"), (key, value)
            assert not isinstance(value, bool), (key, value)
        assert isinstance(line["tier"], int) and not isinstance(line["tier"], bool)
        assert isinstance(line["lineIndex"], int)
    assert "cookable" not in encoded.lower()

    # NON-VACUITY: the fixture really does carry a vault-relative recipe path, so
    # "no absolute path" is a decision this module made rather than an accident of
    # a fixture that had none to leak. The server root itself is `runtime_root`,
    # which is under `tmp_path` and so is named in every path the app owns.
    assert body["recipes"][0]["notePath"].startswith(RECIPES_ROOT)
    assert not body["recipes"][0]["notePath"].startswith("/")
    for section in (line["section"] for line in body["stockJoin"]["lines"]):
        # A heading number, never a directory: `1` is what a repair needs to find
        # the line, and `Logistics/库存` is server-owned layout this table must not
        # republish.
        assert section is None or (section.isdigit())


def test_the_join_table_guidance_states_the_three_tiers_and_the_absence_of_a_repass(
    client: TestClient,
) -> None:
    """The weakness has to be *legible*, and this is the sentence that makes it so.

    Three tiers and no fourth, and **no re-resolution pass** — because there is
    no materialized stock-join table to re-sweep, so a miss does not repair itself
    when the catalog changes. That is the fact a user has to know before deciding
    a `have-been-buying` chip is wrong, and it is server text because
    `line_overrides.yaml`'s key format is a fact the client cannot see.
    """
    guidance = client.get("/api/recipes").json()["stockJoin"]["guidance"]

    assert isinstance(guidance, str) and guidance
    for fragment in ("三层", "line_overrides.yaml", "没有第二次重扫", "重扫"):
        assert fragment in guidance, guidance
    # The sentence that keeps the whole table from reading as a verdict, and the
    # name of the only file a user may edit to fix a row.
    assert "家里没有" in guidance
    assert "app/pantry/line_overrides.yaml" in guidance
    # Not a boolean and not a number dressed as one: the tier counts are the
    # numbers, and they are three separate named keys rather than a total.
    assert isinstance(guidance, str)


# --- the detail route -------------------------------------------------------


def test_the_detail_recipe_carries_the_list_keys_plus_steps_and_history(
    client: TestClient,
) -> None:
    recipe = client.get(f"/api/recipes/{MAIN_RECIPE}").json()["recipe"]

    assert set(recipe) == (
        LIST_RECIPE_KEYS
        | {
            "steps",
            "history",
            "source",
            "durationMinutes",
            "pendingCookDates",
            "retractableCooks",
            "pendingRetractionDates",
        }
    )
    assert set(recipe["history"]) == {
        "firstCooked",
        "lastCooked",
        "cookingCount",
        "cookingFrequency",
        "cookingYears",
        "recentActivity",
        "favoriteSeason",
        "cookingPatterns",
        "autoUpdated",
    }
    # The nine tracker-owned fields, verbatim, including the one the reader keeps
    # as a string because PyYAML does — a space is not the `T` its timestamp
    # resolver accepts, and a cosmetic field must never blank a recipe.
    assert recipe["history"]["lastCooked"] == "2026-09-20"
    assert recipe["history"]["firstCooked"] == "2026-03-01"
    assert recipe["history"]["cookingCount"] == 7
    assert recipe["history"]["autoUpdated"] == "2026-09-21 12:00"
    assert recipe["steps"] == "\n1. 热锅。\n2. 炒蛋盛出。\n3. 炒番茄，回锅。\n"
    # The date is a projection, not a re-parse, and the two agree.
    assert recipe["lastCooked"] == recipe["history"]["lastCooked"]
    # Nothing has been logged through the PWA in this fixture, so there is
    # nothing pending. An empty list here is a real answer, not a default: the
    # key is present and empty rather than absent, so a client cannot tell it
    # from one it failed to receive.
    assert recipe["pendingCookDates"] == []


# ---------------------------------------------------------------------------
# `pendingCookDates`: how far `cooking_count` is behind.
#
# The nine tracker-owned fields are recomputed ONLY when the note is opened in
# Obsidian, so a recipe cooked through this PWA shows a `cooking_count` that is
# wrong until the user opens the note — with nothing on the surface saying so.
# These tests pin the one comparison that makes it visible: a logged cook is
# pending exactly when `last_cooked` has not reached its date.
# ---------------------------------------------------------------------------


def _seed_receipts(runtime_root: Path, rows: tuple[tuple[str, str], ...]) -> None:
    """Insert `cook_log_receipts` rows directly, as `(recipe_note, log_date)`.

    Written straight to SQLite rather than through `POST /api/cook-logs`, because
    the write path needs a daily note to exist and this is about the *read* of a
    ledger that already has rows. The two columns this query reads are the only
    ones given values; the rest take their schema defaults.
    """
    db_path = runtime_root / "data" / "recipes.sqlite3"
    connection = sqlite3.connect(db_path)
    try:
        connection.executemany(
            "INSERT INTO cook_log_receipts (recipe_note, log_date, relative_path,"
            " note_revision) VALUES (?, ?, '日记/2026/does-not-matter.md', 'sha256:x')",
            rows,
        )
        connection.commit()
    finally:
        connection.close()


def _seed_retracted(runtime_root: Path, recipe: str, date: str) -> None:
    connection = sqlite3.connect(runtime_root / "data" / "recipes.sqlite3")
    try:
        connection.execute(
            "INSERT INTO cook_log_receipts (recipe_note, log_date, relative_path,"
            " note_revision, retracted_at) VALUES (?, ?, 'x.md', 'sha256:x',"
            " strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
            (recipe, date),
        )
        connection.commit()
    finally:
        connection.close()


def test_a_fresh_receipt_is_listed_as_retractable_with_its_deadline(
    client: TestClient, runtime_root: Path
) -> None:
    _seed_receipts(runtime_root, ((MAIN_RECIPE, "2026-09-27"),))

    recipe = client.get(f"/api/recipes/{MAIN_RECIPE}").json()["recipe"]

    [cook] = recipe["retractableCooks"]
    assert set(cook) == {"date", "writtenAt", "retractableUntil"}
    assert cook["date"] == "2026-09-27"
    assert cook["retractableUntil"] > cook["writtenAt"]
    assert recipe["pendingRetractionDates"] == []


def test_retracting_the_counted_date_is_pending_until_the_tracker_runs(
    client: TestClient, runtime_root: Path
) -> None:
    """`MAIN_RECIPE` has `last_cooked: 2026-09-20` and `auto_updated: 2026-09-21`."""
    _seed_retracted(runtime_root, MAIN_RECIPE, "2026-09-20")

    recipe = client.get(f"/api/recipes/{MAIN_RECIPE}").json()["recipe"]

    assert recipe["pendingRetractionDates"] == ["2026-09-20"]
    assert recipe["retractableCooks"] == []


def test_retracting_a_date_the_count_never_included_is_not_pending(
    client: TestClient, runtime_root: Path
) -> None:
    _seed_retracted(runtime_root, MAIN_RECIPE, "2026-09-27")

    recipe = client.get(f"/api/recipes/{MAIN_RECIPE}").json()["recipe"]

    assert recipe["pendingRetractionDates"] == []


def test_a_cook_logged_after_the_newest_counted_one_is_pending(
    client: TestClient, runtime_root: Path
) -> None:
    """`MAIN_RECIPE`'s fixture has `last_cooked: 2026-09-20`."""
    _seed_receipts(runtime_root, ((MAIN_RECIPE, "2026-09-27"),))

    recipe = client.get(f"/api/recipes/{MAIN_RECIPE}").json()["recipe"]

    assert recipe["pendingCookDates"] == ["2026-09-27"]


def test_a_cook_on_or_before_last_cooked_is_not_pending(
    client: TestClient, runtime_root: Path
) -> None:
    """`>` and not `>=`, and this is the test that says why.

    A cook dated the same day as `last_cooked` is *in* the count — that is what
    "last cooked on this day" means. Using `>=` would report a permanently
    pending cook on precisely the recipe most likely to be up to date, which is
    the kind of always-wrong annotation people learn to ignore.
    """
    _seed_receipts(
        runtime_root,
        (
            (MAIN_RECIPE, "2026-03-01"),  # == first_cooked, long counted
            (MAIN_RECIPE, "2026-09-20"),  # == last_cooked, the boundary
            (MAIN_RECIPE, "2026-09-21"),  # one day past: pending
        ),
    )

    recipe = client.get(f"/api/recipes/{MAIN_RECIPE}").json()["recipe"]

    assert recipe["pendingCookDates"] == ["2026-09-21"]


def test_pending_dates_come_back_ascending(
    client: TestClient, runtime_root: Path
) -> None:
    _seed_receipts(
        runtime_root,
        (
            (MAIN_RECIPE, "2026-10-02"),
            (MAIN_RECIPE, "2026-09-25"),
            (MAIN_RECIPE, "2026-09-28"),
        ),
    )

    recipe = client.get(f"/api/recipes/{MAIN_RECIPE}").json()["recipe"]

    assert recipe["pendingCookDates"] == ["2026-09-25", "2026-09-28", "2026-10-02"]


def test_another_recipes_receipts_are_never_this_recipes_pending(
    client: TestClient, runtime_root: Path
) -> None:
    """The join is on the exact basename, so a neighbour's cooks cannot leak in."""
    _seed_receipts(
        runtime_root,
        (
            ("盐焗鸡", "2026-10-01"),
            ("盐焗鸡 副本", "2026-10-02"),
            (MAIN_RECIPE, "2026-10-03"),
        ),
    )

    recipe = client.get(f"/api/recipes/{MAIN_RECIPE}").json()["recipe"]

    assert recipe["pendingCookDates"] == ["2026-10-03"]


def test_a_recipe_the_tracker_never_ran_on_has_every_logged_cook_pending(
    runtime_root: Path,
) -> None:
    """A `None` `last_cooked` makes ALL of them pending, not none of them.

    The tracker has never run on this note, so it has counted nothing. Reporting
    an empty pending list there would assert a sync that did not happen — the
    precise shape of the plausible-looking wrong answer this key exists to
    replace, reached by inverting the comparison.

    Built on its own client rather than the shared `client` fixture because the
    note has to exist *before* the recipe index's first snapshot: the index is TTL
    cached, so a note written after boot is simply not in it.
    """
    never_cooked = "没做过"
    write_recipe(
        runtime_root / "vault",
        never_cooked,
        "---\n材料:\n  - 番茄\n---\n# 步骤\n1. 炒。\n".encode(),
    )
    write_pantry_note(runtime_root / "vault")

    with client_for(make_settings(runtime_root)) as test_client:
        # After boot, not before: the schema is created by the lifespan, and the
        # note above had to be written before it for the index to hold it. Those
        # two orderings are opposite, which is the whole reason this test builds
        # its own client instead of using the shared one.
        _seed_receipts(
            runtime_root,
            ((never_cooked, "2026-01-02"), (never_cooked, "2026-09-27")),
        )
        recipe = test_client.get(f"/api/recipes/{never_cooked}").json()["recipe"]

    assert recipe["history"]["lastCooked"] is None
    assert recipe["history"]["cookingCount"] is None
    assert recipe["pendingCookDates"] == ["2026-01-02", "2026-09-27"]


def test_the_list_route_does_not_carry_the_staleness_key(client: TestClient) -> None:
    """The per-recipe ledger read is a detail-only cost, asserted as an absence.

    A list load would otherwise run one receipts query per recipe to render a
    panel nobody opened. `LIST_RECIPE_KEYS` is exact, so this is really a second
    reader of that same constant — stated separately because the reason (cost on
    a route that cannot show it) is not visible in a set comparison.
    """
    rows = client.get("/api/recipes").json()["recipes"]
    assert rows, "the list must not be empty for this assertion to mean anything"
    for row in rows:
        assert "pendingCookDates" not in row
        assert "history" not in row


def test_an_unpublished_cook_log_writer_is_503_and_never_a_stale_count(
    runtime_root: Path,
) -> None:
    """Fail closed on the ledger rather than publishing an empty pending list.

    An unreadable ledger is not evidence of a synced tracker. Answering `[]`
    would be indistinguishable from "nothing is behind", which is the one answer
    that is definitely wrong whenever the 503 is right.
    """
    write_recipe(runtime_root / "vault", MAIN_RECIPE)
    write_pantry_note(runtime_root / "vault")
    with client_for(make_settings(runtime_root)) as test_client:
        del test_client.app.state.cook_log_writer
        response = test_client.get(f"/api/recipes/{MAIN_RECIPE}")

    assert response.status_code == 503
    assert response.json()["code"] == "resource_unavailable"
    assert "recipe" not in response.json()


def test_a_missing_recipe_is_still_404_when_the_writer_is_unpublished(
    runtime_root: Path,
) -> None:
    """404 outranks 503: "this recipe does not exist" is true either way.

    The writer lookup is deliberately *after* the index lookup. Were it before,
    an unrelated missing collaborator would answer a question about a recipe that
    was never there, and the 404 contract that every other name test pins would
    quietly depend on lifespan wiring.
    """
    write_recipe(runtime_root / "vault", MAIN_RECIPE)
    write_pantry_note(runtime_root / "vault")
    with client_for(make_settings(runtime_root)) as test_client:
        del test_client.app.state.cook_log_writer
        response = test_client.get("/api/recipes/不存在")

    assert response.status_code == 404
    assert response.json()["code"] == RECIPE_NOT_FOUND


@pytest.mark.parametrize("name", ["不存在", "%E4%B8%8D%E5%AD%98%E5%9C%A8", "empty", "a" * 200])
def test_a_name_the_index_does_not_hold_is_404_recipe_not_found(
    client: TestClient, name: str
) -> None:
    response = client.get(f"/api/recipes/{name}")
    assert response.status_code == 404
    assert response.json()["code"] == RECIPE_NOT_FOUND
    assert set(response.json()) == {"requestId", "code"}


@pytest.mark.parametrize("name", ["%2E%2E", "..%2E", ".hidden", "%2E%2E%2E%2E"])
def test_a_path_shaped_name_that_reaches_the_route_is_404_recipe_not_found(
    client: TestClient, name: str
) -> None:
    """Server-Owned Root, asserted as an absence, on the names that *do* match.

    The router looks the name up in the in-memory index; it never concatenates it
    onto anything. So a path-shaped name is simply a name nothing holds, and the
    answer is the same 404 as any other miss — which is what makes the endpoint
    unable to read the filesystem even in principle. `%2E%2E` is percent-encoded
    precisely so the HTTP client cannot normalize it away before the request
    leaves: a single decoded segment is a name, not a path.
    """
    response = client.get(f"/api/recipes/{name}")

    assert response.status_code == 404
    assert response.json()["code"] == RECIPE_NOT_FOUND
    assert "root:x:" not in response.text
    assert "OBSIDIAN_VAULT_PATH" not in response.text


@pytest.mark.parametrize("name", ["%2Fetc%2Fpasswd", "..%2F", "%2F%2F%2Fetc"])
def test_a_name_with_an_encoded_slash_lands_on_the_catch_all_not_the_route(
    client: TestClient, name: str
) -> None:
    """A slash in the name is not a name, and the catch-all says so.

    The decoded name is several path segments, so `{note_name}` cannot match and
    the request reaches the scaffold's catch-all. Asserting the *code* is the
    point: it is `not_found` rather than `recipe_not_found`, which says the
    traversal was neutralized by the router before any handler of ours ran. The
    difference is worth pinning rather than papering over.
    """
    response = client.get(f"/api/recipes/{name}")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
    assert set(response.json()) == {"requestId", "code"}
    assert "root:x:" not in response.text


@pytest.mark.parametrize("path", ["/api/recipes/../../etc/passwd", "/api/recipes/.."])
def test_a_traversal_that_escapes_the_api_prefix_gets_the_static_mounts_html_404(
    client: TestClient, path: str
) -> None:
    """The strongest form of the guarantee: the answer is not even our envelope.

    URL normalization resolves these before the request is sent, so `/api/recipes/..
    ` arrives as `/api/` and `/api/recipes/../../etc/passwd` as `/etc/passwd`. The
    first matches nothing (the catch-all needs a segment) and the second leaves
    `/api` entirely, so both land on the static mount and get its HTML 404. A
    JSON body here would be a *worse* outcome to assert — it would mean one of our
    handlers had been reached at all.
    """
    response = client.get(path)

    assert response.status_code == 404
    # Not even a `content-type`: the static mount's bare 404. A JSON body here
    # would be a *worse* outcome to assert — it would mean one of our handlers
    # had been reached at all.
    assert not response.headers.get("content-type", "").startswith("application/json")
    assert "root:x:" not in response.text
    assert "OBSIDIAN_VAULT_PATH" not in response.text


def test_the_name_is_the_wikilink_text_and_the_path_is_vault_relative(
    client: TestClient, note: Path, vault: Path
) -> None:
    """D2: the field `note.note_name` is the same one the cook log writes.

    `RecipeIndex` documents `note_name` as the basename precisely so the
    `[[wikilink]]` text, this route, and §9.12's `⚠ 已重命名` drift check read one
    value. So the payload's `noteName` **is** the filename, `notePath` is the
    vault-relative location, and no client can reconstruct a server-owned absolute
    path from either.
    """
    body = client.get(f"/api/recipes/{MAIN_RECIPE}").json()["recipe"]

    assert body["noteName"] == note.stem == MAIN_RECIPE
    assert body["notePath"] == f"{RECIPES_ROOT}/{MAIN_RECIPE}.md"
    assert str(vault) not in str(body)


# --- a note the index could not read ----------------------------------------


def test_a_note_the_index_cannot_read_is_skipped_and_counted(
    settings: Settings, vault: Path, stock: Path
) -> None:
    """F7's provenance at the *list* level: `skipped`, and the note is not there.

    `RecipeSnapshot.skipped` exists because a note that vanished without a number
    attached is indistinguishable from one that was never there. So this asserts
    both halves together: the bad note is absent from `recipes`, and `skipped` is
    `1` so the absence is reported. A `skipped` of 0 would be a silent drop, and a
    `skipped` of 1 with the note still listed would be a count about nothing.
    """
    write_recipe(vault, MAIN_RECIPE)
    write_recipe(vault, "坏菜谱", UNREADABLE_RECIPE)
    with client_for(settings) as client:
        body = client.get("/api/recipes").json()
        health = client.get("/health").json()

    assert set(body) == LIST_KEYS
    assert body["skipped"] == 1
    assert _names(body) == [MAIN_RECIPE]
    assert "坏菜谱" not in str(body)
    # The same number on `/health`, because §5 requires it on both and a count
    # that lived on only one surface would be one someone has to remember.
    assert health["recipes"] == {"count": 1, "skipped": 1}


# --- the resolve route ------------------------------------------------------


def test_resolve_reports_the_five_numbers_and_the_duplicate_slot_conflict(
    settings: Settings, vault: Path, stock: Path
) -> None:
    """F2 made countable, over HTTP, on a real note with a real duplicated `材料`.

    A recipe listing `番茄` twice is a data defect in a user-authored note, not an
    engine error — which is why §9.16 requires it to be visible *without* opening
    the 调试 view. So the response carries the count **and** the entries: the
    recipe, the slot, and the Pantry Item that lost, so a UI can say "this
    Ingredient also matched 番茄" rather than rendering an unexplained miss.
    """
    write_recipe(vault, MAIN_RECIPE)
    write_recipe(vault, DUPLICATE_RECIPE, DUPLICATE_RECIPE_BYTES)
    with client_for(settings) as client:
        response = client.post("/api/recipes/resolve", headers=_mutation(client))
        assert response.status_code == 200, response.text
        body = response.json()

        assert set(body) == REPORT_KEYS
        assert body["duplicateSlotConflicts"] == 1
        assert body["reconsidered"] == 6
        assert body["resolved"] + body["stillUnresolved"] == body["reconsidered"]
        assert body["staleReset"] == 0
        assert len(body["conflicts"]) == 1
        conflict = body["conflicts"][0]
        assert set(conflict) == CONFLICT_KEYS
        assert conflict["canonicalName"] == "番茄"
        assert conflict["pantryItemId"] == 1
        assert conflict["reason"] == "duplicate_slot_conflict"
        assert conflict["ingredientIndex"] == 1
        assert conflict["recipeNote"].endswith(f"{DUPLICATE_RECIPE}.md")
        assert str(vault) not in str(conflict)

        # F2's actual guarantee: the FIRST slot committed and only the second was
        # downgraded, and the other recipe's slots were untouched by the conflict.
        # A whole-transaction abort would have reverted all of them, which is the
        # failure the override exists to prevent.
        recipes = {r["noteName"]: r for r in client.get("/api/recipes").json()["recipes"]}
        assert _slot(recipes[DUPLICATE_RECIPE], 0)["pantryItemId"] == 1
        assert _slot(recipes[DUPLICATE_RECIPE], 1)["pantryItemId"] is None
        assert _slot(recipes[DUPLICATE_RECIPE], 1)["matchMethod"] == "unresolved"
        assert _slot(recipes[DUPLICATE_RECIPE], 1)["matchTier"] == 0
        assert _slot(recipes[DUPLICATE_RECIPE], 1)["candidatesJson"][0]["reason"] == (
            "duplicate_slot_conflict"
        )
        assert (recipes[MAIN_RECIPE]["found"], recipes[MAIN_RECIPE]["total"]) == (2, 4)


def test_a_second_resolve_is_idempotent(client: TestClient) -> None:
    """F2's fixed point: the downgraded slot stays downgraded and stops moving.

    The downgraded slot holds `NULL`, so a pass re-derives the same Pantry Item
    for it and skips the write because the resolution is unchanged — which is what
    makes `updated_at` mean "when this row's resolution last changed" rather than
    "when a pass last walked over it". The count is now 0 because the recipe was
    never in this fixture, so there is nothing to collide with.
    """
    first = client.post("/api/recipes/resolve", headers=_mutation(client)).json()
    second = client.post("/api/recipes/resolve", headers=_mutation(client)).json()

    assert first["duplicateSlotConflicts"] == 0
    assert second == first
    assert second["reconsidered"] == 4


def test_resolve_is_409_while_a_pass_is_in_flight(client: TestClient) -> None:
    """The guard, asserted against a genuinely held lock rather than a mock.

    Two concurrent passes would run the same ladder over the same rows and race
    each other for the same write lock, and a user who presses "re-resolve" twice
    deserves one report rather than two racing ones. The lock lives on
    `app.state` and is released on every path, so holding it here exercises the
    real object the route reads — and the release afterwards is itself asserted,
    because a guard that wedges is worse than one that is absent.
    """
    # The lock is created on first use rather than published by the `lifespan`,
    # so one real pass runs first and the guard below holds the *live* object the
    # route reads. Creating it in the lifespan instead would be one less moving
    # part, and would also make an app with no `lifespan` a 503 rather than a 409.
    assert client.post("/api/recipes/resolve", headers=_mutation(client)).status_code == 200
    lock = client.app.state[RESOLVE_LOCK_STATE_KEY]
    client.portal.call(lock.acquire)
    try:
        response = client.post("/api/recipes/resolve", headers=_mutation(client))
    finally:
        client.portal.call(lock.release)

    assert response.status_code == 409
    assert response.json()["code"] == RESOLVE_IN_FLIGHT
    assert set(response.json()) == {"requestId", "code"}
    assert client.post("/api/recipes/resolve", headers=_mutation(client)).status_code == 200


# --- the two manual-mapping routes (F6) -------------------------------------


def test_put_mapping_returns_the_row_and_reached_it_through_set_manual(
    client: TestClient,
) -> None:
    """F6: a hand fix is a delete + insert, and the response is the new row.

    The engine's answer for slot 3 was `unresolved`; the user's is Pantry Item 3.
    `match_method: 'manual'` and `confidence: 1.0` are the schema's own encoding
    for "no tier, full claim", and the audit anchor survives — a hand fix changes
    which product this Ingredient is, not what the frontmatter says.
    """
    before = _by_value(
        client.get(f"/api/recipes/{MAIN_RECIPE}").json()["recipe"], "🐟 不存在的鱼"
    )
    assert before["matchMethod"] == "unresolved"

    response = client.put(
        f"/api/recipes/{MAIN_RECIPE}/ingredients/3/mapping",
        json={"pantryItemId": FREE_ITEM_ID},
        headers=_mutation(client),
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"mapping"}
    mapping = body["mapping"]
    assert set(mapping) == MAPPING_KEYS
    assert mapping["matchMethod"] == "manual"
    assert mapping["pantryItemId"] == FREE_ITEM_ID
    assert mapping["confidence"] == 1.0
    assert mapping["matchTier"] == 0
    assert mapping["rawValue"] == "🐟 不存在的鱼"
    assert mapping["parsedName"] == "不存在的鱼"
    # And the read path agrees, which is what a hand fix is for: the headline
    # moves from 2/4 to 3/4 because the user said they have it.
    #
    # **A hand fix outranks the stock tier, and this is where that is worth
    # saying.** `FREE_ITEM_ID` has no line in `Pantry.md`, so `inStock` is false
    # and the same slot would be missing on the machine's own reading of the
    # shelf. It counts anyway: the ladder answers `manual` before it consults
    # stock, so a mapped ingredient is the user's own statement and the app does
    # not overrule it. A catalog match is evidence; a hand fix is a decision.
    # The 3 is not 4 because the *other* missing slot, `空心菜`, is not hand-fixed
    # and its `[x]` line is still the truth about that product.
    after = _by_value(
        client.get(f"/api/recipes/{MAIN_RECIPE}").json()["recipe"], "🐟 不存在的鱼"
    )
    assert after["matchMethod"] == "manual"
    assert after["pantryItemId"] == FREE_ITEM_ID
    assert after["inStock"] is False
    assert client.get("/api/recipes").json()["recipes"][0]["found"] == 3


def test_delete_mapping_clears_the_hand_fix_and_is_idempotent(client: TestClient) -> None:
    """Clearing restores `unresolved`, and clearing again is a 200, not a 404.

    Idempotence is the point: a retry that reported a failure for work already
    done is the plausible-looking wrong answer this app does not produce. Only a
    slot with **no row at all** is a 404, because `ensure_rows` is how a slot comes
    into existence and nothing here creates one.
    """
    client.put(
        f"/api/recipes/{MAIN_RECIPE}/ingredients/3/mapping",
        json={"pantryItemId": FREE_ITEM_ID},
        headers=_mutation(client),
    )
    path = f"/api/recipes/{MAIN_RECIPE}/ingredients/3/mapping"

    first = client.delete(path, headers=_mutation(client))
    second = client.delete(path, headers=_mutation(client))

    assert first.status_code == 200
    assert first.json() == {"mapping": None}
    assert second.status_code == 200
    assert second.json() == {"mapping": None}
    after = _by_value(
        client.get(f"/api/recipes/{MAIN_RECIPE}").json()["recipe"], "🐟 不存在的鱼"
    )
    assert after["matchMethod"] == "unresolved"
    assert after["pantryItemId"] is None
    # The audit anchor still describes the note, not the repair.
    assert after["rawValue"] == "🐟 不存在的鱼"


def test_a_manual_row_survives_a_resolve_pass(client: TestClient) -> None:
    """F6's guarantee is a property of the *row*, not of one call site.

    `resolve_all` skips `manual`, so a catalog re-import, a future backfill, a
    repair script and a hand-edited query all behave identically — because none of
    them is what enforces it. A `POST /api/recipes/resolve` is the cheapest
    demonstration of that, and the count is the evidence: four Material rows, one
    of them manual, three reconsidered.
    """
    client.put(
        f"/api/recipes/{MAIN_RECIPE}/ingredients/3/mapping",
        json={"pantryItemId": FREE_ITEM_ID},
        headers=_mutation(client),
    )
    report = client.post("/api/recipes/resolve", headers=_mutation(client)).json()

    assert report["reconsidered"] == 3
    after = _by_value(
        client.get(f"/api/recipes/{MAIN_RECIPE}").json()["recipe"], "🐟 不存在的鱼"
    )
    assert after["matchMethod"] == "manual"
    assert after["pantryItemId"] == FREE_ITEM_ID


def test_binding_a_slot_to_an_item_the_recipe_already_holds_is_409(client: TestClient) -> None:
    """F2's inverted index, hit by a hand fix — a 409, and nothing is written.

    Slot 2 (`空心菜`) already holds Pantry Item 3, so pointing slot 3 at the same
    product trips `ux_ingredient_mappings_recipe_item`. `set_manual`'s
    transaction rolls back whole, so the previous row is intact; the request is a
    genuine conflict rather than a malformed one, and the answer carries F2's own
    reason string so one code covers both the ladder's catch and this one. The
    user who wants a recipe to list one Pantry Item twice edits the note.

    Without this mapping the exception escaped the handler as a 500 with a
    traceback in the log and a blank screen in the UI.
    """
    response = client.put(
        f"/api/recipes/{MAIN_RECIPE}/ingredients/3/mapping",
        json={"pantryItemId": HELD_SLOT_ITEM_ID},
        headers=_mutation(client),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "duplicate_slot_conflict"
    assert set(response.json()) == {"requestId", "code"}
    recipe = client.get(f"/api/recipes/{MAIN_RECIPE}").json()["recipe"]
    assert _slot(recipe, 2)["pantryItemId"] == HELD_SLOT_ITEM_ID
    assert _slot(recipe, 3)["pantryItemId"] is None
    assert _slot(recipe, 3)["matchMethod"] == "unresolved"


def test_a_pantry_item_the_live_catalog_does_not_have_is_422(client: TestClient) -> None:
    """A 422, not a 404: the request is well formed, the id is not a member.

    F6 makes a `manual` row immutable, so a bad id accepted once could only ever
    be repaired by another delete-and-insert — and until then it renders a chip
    claiming a product that does not exist.
    """
    response = client.put(
        f"/api/recipes/{MAIN_RECIPE}/ingredients/3/mapping",
        json={"pantryItemId": 9999},
        headers=_mutation(client),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "unknown_pantry_item"
    assert set(response.json()) == {"requestId", "code"}


def test_a_mapping_write_for_an_unknown_recipe_is_404(client: TestClient) -> None:
    for method in ("put", "delete"):
        response = client.request(
            method.upper(),
            "/api/recipes/不存在/ingredients/0/mapping",
            json={"pantryItemId": 1},
            headers=_mutation(client),
        )
        assert response.status_code == 404, method
        assert response.json()["code"] == RECIPE_NOT_FOUND


@pytest.mark.parametrize("index", [-1, MAX_INGREDIENT_INDEX + 1])
def test_an_ingredient_index_that_cannot_exist_is_422(client: TestClient, index: int) -> None:
    response = client.put(
        f"/api/recipes/{MAIN_RECIPE}/ingredients/{index}/mapping",
        json={"pantryItemId": 1},
        headers=_mutation(client),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_ingredient_index"


def test_a_mapping_body_that_carries_an_extra_field_is_refused_not_ignored(
    client: TestClient,
) -> None:
    """`extra="forbid"`, for the same reason the cook log's body is.

    An ignored field is a field that looks like it worked, and a body carrying a
    `path`-shaped key beside a valid `pantryItemId` is exactly the shape of a
    client that thinks it can name a file.
    """
    response = client.put(
        f"/api/recipes/{MAIN_RECIPE}/ingredients/3/mapping",
        json={"pantryItemId": 1, "path": "../../etc/passwd"},
        headers=_mutation(client),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"


def test_read_only_refuses_every_mutation_before_any_existence_check(
    settings: Settings,
) -> None:
    """403 `read_only` precedes the 404, so a read-only install cannot probe.

    Comparing 403 against 404 is otherwise a way to enumerate which recipes and
    which slots exist without being able to write one — the same obligation
    `tests/api/test_auth.py` states for the cook log, on the three routes that
    write here. Neither the recipe nor the pantry note exists in this fixture, so
    a 404 is the other possible answer and the ordering is what is asserted.
    """
    read_only = make_settings(
        settings.app_data_dir.parent, OBSIDIAN_READ_ONLY="true"
    )
    seed_catalog(read_only.pantry_items_db, API_CATALOG_ROWS)
    with client_for(read_only) as client:
        for method, path in (
            ("put", f"/api/recipes/{MAIN_RECIPE}/ingredients/0/mapping"),
            ("delete", f"/api/recipes/{MAIN_RECIPE}/ingredients/0/mapping"),
        ):
            response = client.request(
                method.upper(), path, json={"pantryItemId": 1}, headers=_mutation(client)
            )
            assert response.status_code == 403, method
            assert response.json()["code"] == "read_only"
        # The resolve pass writes too, so it is refused by the same gate.
        assert client.post("/api/recipes/resolve", headers=_mutation(client)).status_code == 403
        # And the reads still work: read-only is about writing the vault, not
        # about refusing to tell the user what is in it.
        assert client.get("/api/recipes").status_code == 503


# --- the guards, in the shipped order ---------------------------------------
#
# Order is host → identity → read_only → body size → Origin → content-type →
# CSRF. Each test omits exactly one header so the 403 names the guard under test;
# a test that omitted two would prove nothing about which one fired.


def test_a_resolve_without_an_origin_is_403_origin_not_allowed(client: TestClient) -> None:
    response = client.post("/api/recipes/resolve")
    assert response.status_code == 403
    assert response.json()["code"] == "origin_not_allowed"


def test_a_resolve_without_a_csrf_token_is_403_csrf_required(client: TestClient) -> None:
    response = client.post("/api/recipes/resolve", headers={"Origin": ORIGIN})
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_required"


def test_a_foreign_host_is_400_before_anything_else(client: TestClient) -> None:
    response = client.post(
        "/api/recipes/resolve", headers={"Host": "evil.example", "Origin": ORIGIN}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_host"


def test_a_spoofed_identity_header_is_401_in_trusted_header_mode(
    runtime_root: Path, note: Path, stock: Path
) -> None:
    """Identity precedes every other guard, so a spoofed header is 401 and not 403.

    In trusted-header mode the app believes Tailscale and a `Tailscale-User-Login`
    the caller chose *is* the spoof, so `origin_not_allowed` would be the wrong
    answer: it would tell the caller their Origin failed when what actually
    happened is that they are not who they said.
    """
    trusted = make_settings(runtime_root, TRUST_TAILSCALE_HEADERS="true")
    seed_catalog(trusted.pantry_items_db, API_CATALOG_ROWS)
    with client_for(trusted) as client:
        response = client.post(
            "/api/recipes/resolve",
            headers={"Origin": ORIGIN, "Tailscale-User-Login": "attacker@evil.invalid"},
        )
    assert response.status_code == 401
    assert response.json()["code"].startswith("identity_")


# --- the surface itself -----------------------------------------------------


def test_the_router_registers_exactly_the_five_recipe_routes() -> None:
    """§9.16's table is the whole surface, asserted on the router object.

    `/api/recipes/resolve` is registered **before** `/api/recipes/{note_name}` on
    purpose: a `POST` would not match the `GET`-only detail route today, but a
    future `POST /api/recipes/{note_name}` would, and the literal path must win.
    The order is asserted so a reordering has to be deliberate.
    """
    routes = [
        (route.path, sorted(route.methods - {"HEAD"}))  # type: ignore[union-attr]
        for route in build_recipes_router().routes
    ]
    assert routes == [
        ("/api/recipes", ["GET"]),
        ("/api/recipes/{note_name}", ["GET"]),
        ("/api/recipes/resolve", ["POST"]),
        ("/api/recipes/{note_name}/ingredients/{index}/mapping", ["PUT"]),
        ("/api/recipes/{note_name}/ingredients/{index}/mapping", ["DELETE"]),
    ]


def test_every_recipe_response_carries_the_cache_and_request_id_headers(
    client: TestClient,
) -> None:
    """`no-store` on every `/api/*`, and a `requestId` that matches the header.

    §9.16's first sentence, asserted rather than assumed, and asserted together
    because the two are what let a UI error and a log line be joined after the
    fact.
    """
    token = _csrf(client)
    for response in (
        client.get("/api/recipes"),
        client.get(f"/api/recipes/{MAIN_RECIPE}"),
        client.post("/api/recipes/resolve", headers={"Origin": ORIGIN, "X-CSRF-Token": token}),
    ):
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store", response.request.url
        assert response.headers["x-request-id"]
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    # The `requestId` the body and the header share is on the *error* envelope —
    # a success payload has no envelope. It is asserted here because joining a UI
    # message to a log line is the whole purpose of the field.
    refused = client.get("/api/recipes/不存在")
    assert refused.status_code == 404
    assert refused.headers["cache-control"] == "no-store"
    assert refused.json()["requestId"] == refused.headers["x-request-id"]


def test_the_read_routes_write_nothing_to_the_vault(client: TestClient, vault: Path) -> None:
    """F20's list, and every read, are read-only with respect to the vault.

    The PWA-owned SQLite file **is** written — `ensure_rows` materializes the
    mapping table, which is D1's whole point and is not a vault write. The vault
    is not: a byte-for-byte comparison of every note before and after a list, a
    detail and a resolve pass.
    """
    before = {
        path: path.read_bytes() for path in sorted(vault.rglob("*.md")) if path.is_file()
    }
    client.get("/api/recipes")
    client.get(f"/api/recipes/{MAIN_RECIPE}")
    client.post("/api/recipes/resolve", headers=_mutation(client))
    after = {path: path.read_bytes() for path in sorted(vault.rglob("*.md")) if path.is_file()}

    assert set(after) == set(before)
    assert after == before

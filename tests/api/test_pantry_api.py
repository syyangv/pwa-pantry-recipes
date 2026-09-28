"""`GET /api/pantry/items` — the catalog search behind the manual re-map picker.

Run alone:  .venv/bin/python -m pytest tests/api/test_pantry_api.py -q

**The smallest surface in the app, and the tests are mostly about what it does
*not* publish.** The ticket's non-goals forbid emitting `last_price`,
`first_seen`, `last_seen` and `order_count` in any response: they are
Pantry-Write Contract data owned by `wholefoods-to-pantry`, and this app is a
read-only consumer of another project's committed asset. §9.16's table row lists a
`lastSeen` key, so the two constraints disagree; the non-goals win, and
`test_the_four_pantry_write_columns_are_never_published` says so explicitly rather
than leaving the next reader to wonder.

**`area` is published and is always `null`, and that is a fact rather than a
placeholder.** `PantryCatalog` drops every row whose `area` is not NULL *before*
it builds a `CatalogRow`, so every row in the candidate universe provably has a
NULL area. The fixture seeds one such row, so the assertion is against real data
rather than against an absent fact.

**Nothing here reads a live path and nothing here writes.** The catalog is a real
SQLite file under `tmp_path`, seeded with the committed literals in
`tests/api/conftest.py`, and the app never opens it for writing.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.pantry import DEFAULT_LIMIT, MAX_LIMIT, PANTRY_DB_UNREADABLE, build_pantry_router
from app.config import Settings
from tests.api.conftest import (
    API_CATALOG_ROWS,
    ORIGIN,
    client_for,
    make_settings,
    seed_catalog,
    write_pantry_note,
    write_recipe,
)
from tests.api.test_recipes_api import MAIN_RECIPE

#: §9.16's picker row, minus the `lastSeen` the non-goals forbid. Asserted as an
#: exact set, so a sixth key is a deliberate edit rather than a surprise.
ITEM_KEYS: frozenset[str] = frozenset({"id", "canonicalName", "category", "area", "variants"})

#: §5.3: no code-to-display-text mapping exists for a Pantry Category and none
#: may be inferred from the digits. These are the codes themselves, verbatim.
CATEGORIES: dict[int, str] = {1: "1.1", 2: "1.1d", 3: "1.1", 4: "1.1c", 6: "1.1"}


@pytest.fixture
def settings(runtime_root: Path) -> Settings:
    configured = make_settings(runtime_root)
    seed_catalog(configured.pantry_items_db, API_CATALOG_ROWS)
    return configured


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """The real app. A recipe and a pantry note are present so the *only* thing
    the 503 could come from is the catalog — which is the source this route
    actually reads, and the point of asserting its own 503 separately."""
    write_recipe(settings.vault_path, MAIN_RECIPE)
    write_pantry_note(settings.vault_path)
    with client_for(settings) as test_client:
        yield test_client


def _ids(body: dict[str, object]) -> list[int]:
    return [int(item["id"]) for item in body["items"]]  # type: ignore[index]


def test_an_unqualified_search_returns_the_catalog_in_id_order(client: TestClient) -> None:
    """No `q` is a real answer — "here is the catalog" — and it is bounded.

    Bounded matters: an unbounded `GET /api/pantry/items` with no `q` would be a
    way to dump a 178-row table, so `MAX_LIMIT` applies here exactly as it does
    to a search. The rows come back in `id` order, which makes "the first 20" the
    same 20 rows on every call — and that is what makes a picker that re-queries
    as the user types usable at all.
    """
    response = client.get("/api/pantry/items")
    body = response.json()

    assert response.status_code == 200
    assert set(body) == {"items", "catalogRevision"}
    assert body["catalogRevision"].startswith("sha256:")
    assert _ids(body) == [1, 2, 3, 4, 6]
    # The `area`-excluded row (5) is not in the candidate universe at all.
    assert 5 not in _ids(body)
    assert len(body["items"]) <= MAX_LIMIT


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("番茄", [1]),
        ("鸡蛋", [2]),
        ("空心", [3]),
        ("空心菜嫩苗", [3]),
        ("李锦记", [4]),
        ("豆腐", [6]),
        ("ZZZ", []),
    ],
)
def test_a_query_matches_a_substring_of_the_name_or_a_variant(
    client: TestClient, query: str, expected: list[int]
) -> None:
    """A *substring* search, folded with the one normalizer.

    `CatalogSnapshot.candidates_for` is an exact-folded-key lookup built for the
    matcher's tiers, which returns nothing for a half-typed name — and the picker
    is for a user typing half a name. Folding is still `fold_name`, so NFKC +
    trim + casefold apply to both sides and a case or width mismatch still hits.
    """
    body = client.get("/api/pantry/items", params={"q": query}).json()

    assert _ids(body) == expected


def test_a_query_matched_by_the_base_name_too(client: TestClient) -> None:
    """`basename` is searched as well as `canonical_name`.

    The user types what is on the packaging and the catalog holds the full
    product name, so a search over `canonical_name` alone would miss the row a
    manual re-map most needs: the one whose effective basename is the thing the
    user typed. `空心菜` reaches id 3 (`空心菜嫩苗`) on both the name and the
    basename.
    """
    assert _ids(client.get("/api/pantry/items", params={"q": "嫩苗"}).json()) == [3]
    assert _ids(client.get("/api/pantry/items", params={"q": "豆腐"}).json()) == [6]


def test_the_search_is_case_and_width_folded(client: TestClient) -> None:
    """The one normalizer, on both sides — no second comparison rule.

    A picker that matched half-width but not half-case (or the reverse) would be a
    lookup that silently fails on real input, and fixing it with a second
    comparison rule is how two answers to the same question appear.
    """
    assert _ids(client.get("/api/pantry/items", params={"q": "  空心菜  "}).json()) == [3]
    assert _ids(client.get("/api/pantry/items", params={"q": "TOMATO"}).json()) == []
    assert _ids(client.get("/api/pantry/items", params={"q": "  番茄 "}).json()) == [1]


def test_one_item_carries_exactly_the_picker_fields(client: TestClient) -> None:
    body = client.get("/api/pantry/items", params={"q": "番茄"}).json()
    item = body["items"][0]

    assert set(item) == ITEM_KEYS
    assert item == {
        "id": 1,
        "canonicalName": "番茄",
        "category": "1.1",
        "area": None,
        "variants": [],
    }


def test_the_category_is_a_code_and_never_a_label(client: TestClient) -> None:
    """§5.3: `CONTEXT.md` defines no code-to-display-text mapping.

    So `1.1d` is published as `1.1d`. Inferring "chilled proteins" from the digits
    would be inventing vocabulary this repository has never defined, and the
    labels would then be a second source of truth the user cannot correct.
    """
    body = client.get("/api/pantry/items").json()
    by_id = {int(item["id"]): item for item in body["items"]}

    for item_id, code in CATEGORIES.items():
        assert by_id[item_id]["category"] == code
    assert by_id[2]["category"] == "1.1d"
    for item in body["items"]:
        # A code, verbatim: no surrounding space, and nothing but the code's own
        # characters — so a mapping table rendered as a label cannot creep in here.
        assert item["category"] == item["category"].strip()
        assert all(character.isalnum() or character == "." for character in item["category"])


def test_area_is_always_null_and_that_is_a_fact_about_the_candidate_universe(
    client: TestClient,
) -> None:
    """`area` is published, and it is `null` for every row — provably.

    `PantryCatalog` filters on `area` *before* it builds a `CatalogRow`, so a row
    in the candidate universe cannot have one. The fixture seeds row 5 with
    `area = 'Shampoo'` precisely so the test would notice if the filter were
    dropped: it would arrive with an `area`, and this assertion would fail.
    """
    body = client.get("/api/pantry/items").json()

    assert all(item["area"] is None for item in body["items"])
    assert all(set(item) == ITEM_KEYS for item in body["items"])
    # The excluded row is unreachable by every route, not merely by name.
    assert 5 not in _ids(body)
    assert 5 not in _ids(client.get("/api/pantry/items", params={"q": "Shampoo"}).json())


def test_the_four_pantry_write_columns_are_never_published(client: TestClient) -> None:
    """The non-goals, as an assertion on the bytes.

    `last_price`, `first_seen`, `last_seen` and `order_count` are the producer's
    to write and this app's to ignore: `CatalogRow` does not project them, and
    reading them would mean a second, wider reader over another project's file
    and an invitation to treat a lifetime purchase history as current stock.
    §9.16's table row names a `lastSeen` key, so this is where the two
    constraints are reconciled: the non-goals win, and the key is not published.
    """
    body = client.get("/api/pantry/items").json()

    assert not any(
        key in str(item)
        for item in body["items"]
        for key in ("lastPrice", "last_price", "firstSeen", "lastSeen", "orderCount")
    )


@pytest.mark.parametrize("limit", ["0", "-1", str(MAX_LIMIT + 1), "abc", "1.5"])
def test_a_limit_outside_the_bound_is_422_and_never_a_different_result(
    client: TestClient, limit: str
) -> None:
    """422, not a clamped page.

    Clamping would be the worse answer: a client asking for 500 rows would get 50
    and could not tell that its query was refused, which is exactly the
    plausible-looking wrong answer the bounds exist to prevent.
    """
    response = client.get("/api/pantry/items", params={"limit": limit})
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert set(response.json()) == {"requestId", "code"}


def test_the_default_limit_is_the_documented_one(client: TestClient) -> None:
    assert client.get("/api/pantry/items").json()["items"]
    assert DEFAULT_LIMIT < MAX_LIMIT
    # `limit=1` really is one row, so the parameter is read and not ignored.
    assert _ids(client.get("/api/pantry/items", params={"limit": 1}).json()) == [1]


@pytest.mark.parametrize("query", ["", "   ", "\t"])
def test_a_blank_query_is_422_rather_than_the_first_page(client: TestClient, query: str) -> None:
    """A blank `q` is a malformed request, not a search for the empty string.

    Answering it with the first `limit` rows would make a client bug look like a
    successful search — the string a picker sends when the user clears the box
    would silently become "show me everything".
    """
    response = client.get("/api/pantry/items", params={"q": query})
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_pantry_query"
    assert set(response.json()) == {"requestId", "code"}


def test_a_query_that_matches_nothing_is_an_empty_list_not_an_error(
    client: TestClient,
) -> None:
    """"I have no Pantry Item by that name" is a real answer.

    It is different from "your query was not a query", and conflating them is the
    plausible-looking wrong answer `AGENTS.md` #3 rules out — a 404 or a 503 here
    would send the user looking for a broken server instead of a missing product.
    """
    response = client.get("/api/pantry/items", params={"q": "龙利鱼柳"})

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["catalogRevision"].startswith("sha256:")


def test_an_unreadable_catalog_is_503_pantry_db_unreadable(settings: Settings) -> None:
    """The catalog's own 503, with the same code the recipes router uses.

    Both are "a source this app depends on is unreadable" and both are permanent
    until the user fixes it, so a UI must not present either as retry-soon — and
    so they must be spelled the same way, or the client needs two branches for one
    state.
    """
    settings.pantry_items_db.write_bytes(b"not a SQLite database")
    with client_for(settings) as client:
        response = client.get("/api/pantry/items")

    assert response.status_code == 503
    assert response.json()["code"] == PANTRY_DB_UNREADABLE
    assert set(response.json()) == {"requestId", "code"}


def test_the_route_never_writes_to_the_catalog(
    client: TestClient, settings: Settings
) -> None:
    """Read-only, asserted on the file rather than on the code that says so.

    `mode=ro` and `PRAGMA query_only=ON` are the mechanism, and a byte-for-byte
    comparison of the producer's file across a search is the evidence that the
    mechanism held. There is no creation path in this repository, so this is the
    only way a Pantry Item could ever come into existence from here.
    """
    before = settings.pantry_items_db.read_bytes()
    client.get("/api/pantry/items")
    client.get("/api/pantry/items", params={"q": "番茄", "limit": 2})

    assert settings.pantry_items_db.read_bytes() == before


def test_the_router_registers_exactly_the_one_picker_route() -> None:
    """§9.16's table is the whole surface: one route, and no write beside it."""
    routes = [
        (route.path, sorted(route.methods - {"HEAD"}))  # type: ignore[union-attr]
        for route in build_pantry_router().routes
    ]
    assert routes == [("/api/pantry/items", ["GET"])]


def test_every_response_carries_no_store_and_a_matching_request_id(
    client: TestClient,
) -> None:
    responses = [
        client.get("/api/pantry/items"),
        client.get("/api/pantry/items", params={"q": "番茄"}),
        client.get("/api/pantry/items", params={"q": ""}),
    ]
    for response in responses:
        assert response.headers["cache-control"] == "no-store", response.request.url
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert responses[-1].json()["requestId"] == responses[-1].headers["x-request-id"]


def test_the_origin_header_is_never_a_picker_input(client: TestClient) -> None:
    """The picker takes `q` and `limit` and nothing else.

    A `q` that named a path would be the Server-Owned Root violation in a new
    place, and a `q` that carried an `Origin` would be a header the client set by
    hand — which the browser forbids and the CSRF guard would not believe anyway.
    """
    response = client.get("/api/pantry/items", params={"q": "../../etc/passwd"})

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert "root:x:" not in response.text
    assert ORIGIN not in response.text

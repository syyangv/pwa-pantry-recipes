"""The four `/api/shortlists*` routes — every status code and every key set.

Run alone:  .venv/bin/python -m pytest tests/api/test_shortlists_api.py -q

**Every request goes through the real `create_app(settings)`.** Nothing is
hand-built and no router is spliced in: the wiring is the thing under test, and a
route exercised through an app the ticket assembled itself is evidence about that
app, not about this one. `tests/scaffold/test_routes.py` pins the registration
*order*, which is the half a request cannot observe.

**The empty-state section is the most important content here, and it is written
to fail in one specific way.** D3 says an empty shortlist is a normal empty state,
so `GET /api/shortlists` on a fresh install is a 200 with three empty arrays. The
temptation is a 404, a 503, an `error` key, or a body that omits the empty slots —
all four of which a user would read as something broken. So the tests assert the
*exact* three keys and the *absence of any error code*, not merely that the
status is 200, and a positive control drives the same three-key shape with rows
in it so a shape assertion cannot pass vacuously.

**Every payload assertion is an exact key set.** F17 says no `cookable` boolean is
ever published and F7 says provenance is unconditional; both are claims about a
*set*, so `set(body) == {...}` is the only assertion that tests them. `assert
"cookable" not in body` would pass just as happily on a response that grew a
`canCook` instead.

**The rename-drift section asserts the absence of the plausible answers.** A row
whose recipe was renamed must come back marked `resolved: false` and **still be
in the response at all** — a route that filtered unresolved rows out would look
correct to any test that only checked the flag, and would be the exact silent
drop §9.12 forbids.

**Nothing here reads a live path.** The vault, the `Pantry.md`, and the catalog
are all written per test under `tmp_path` from the committed literals in
`tests/api/conftest.py`, and the database is the one `lifespan` creates there.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient

from app.api.resources import MEAL_SHORTLIST_STORE_STATE_KEY
from app.api.shortlists import (
    ORDER_MISMATCH,
    SHORTLIST_ENTRY_MISSING,
    SHORTLIST_FULL,
    SHORTLIST_ORDER_TOO_LONG,
    UNKNOWN_MEAL_SLOT,
    UNKNOWN_RECIPE_NOTE,
    build_shortlists_router,
)
from app.config import Settings
from app.db.database import connect_db
from app.shortlists.store import MEAL_SLOTS, MealShortlistStore
from tests.api.conftest import (
    API_CATALOG_ROWS,
    MAIN_RECIPE,
    ORIGIN,
    RECIPES_ROOT,
    client_for,
    make_settings,
    seed_catalog,
    write_pantry_note,
    write_recipe,
)

#: §9.16's `GET /api/shortlists` success key set: the three slots, and nothing
#: else. No wrapper object, no revision, no count — an empty list is a 200 with an
#: empty array, and a payload that grew a `status` or an `error` would be a second
#: way to say what the HTTP status already says.
LIST_KEYS: frozenset[str] = frozenset(MEAL_SLOTS)

#: §9.16's success key set for all three mutations. Identical for `POST`,
#: `DELETE`, and `PUT`, which is deliberate: the client re-renders the one panel
#: from `items` whichever verb produced it, so a difference would be a difference
#: the UI has to know about for no reason.
MUTATION_KEYS: frozenset[str] = frozenset({"slot", "items"})

#: One item's key set, F7 in-band. `resolved` is D3's drift flag and the input to
#: the client's `⚠ 已重命名`; nothing else may appear, so a future addition to the
#: entry dataclass fails here rather than being noticed by a user.
ITEM_KEYS: frozenset[str] = frozenset({"noteName", "resolved"})

#: The two-key error envelope, and the store's own code as `code`. §9.16 widens
#: the envelope for exactly two codes, both belonging to the Cooking Log, so every
#: refusal below is asserted to be exactly `{requestId, code}`.
ENVELOPE_KEYS: frozenset[str] = frozenset({"requestId", "code"})

#: A second recipe note, so the reorder and remove tests have something to
#: distinguish. Named like a real note because §9.12's `⚠ 已重命名` treatment is
#: about real basenames.
SECOND_RECIPE: Final = "盐焗鸡"
SECOND_RECIPE_BYTES: bytes = (
    "---\n"
    "材料:\n"
    "  - 鸡\n"
    "调料:\n"
    "  - 盐\n"
    "---\n"
    "# 步骤\n"
    "1. 焗。\n"
).encode()


@dataclass
class _App:
    """A client, the settings it booted with, and the two shortlist helpers."""

    client: TestClient
    settings: Settings
    origin: str

    def add(self, slot: str, note: str) -> Any:
        return self.client.post(
            f"/api/shortlists/{slot}",
            json={"recipeNote": note},
            headers=self.mutation,
        )

    def remove(self, slot: str, note: str) -> Any:
        return self.client.delete(f"/api/shortlists/{slot}/{note}", headers=self.mutation)

    def order(self, slot: str, order: list[str]) -> Any:
        return self.client.put(
            f"/api/shortlists/{slot}/order", json={"order": order}, headers=self.mutation
        )

    @property
    def mutation(self) -> dict[str, str]:
        return {"Origin": self.origin, "X-CSRF-Token": self.token}

    @property
    def token(self) -> str:
        return str(self.client.get("/api/session").json()["csrfToken"])

    def rename(self, old: str, new: str) -> None:
        """Rename a recipe note in the vault, the way Obsidian would.

        Done through the filesystem and then through an index invalidation, which
        is the only thing a real rename looks like to this app. The recipe cache's
        TTL is 60 s, and a test that waited for it would be both slow and flaky —
        so the fixture invalidates it explicitly, which is what
        `RecipeIndex.invalidate()` exists for.
        """
        source = self.settings.vault_path / RECIPES_ROOT / f"{old}.md"
        source.rename(source.with_name(f"{new}.md"))
        self.client.app.state.recipe_index.invalidate()


@pytest.fixture
def app(runtime_root: Path) -> Iterator[_App]:
    """The real app, with two recipes, a pantry note, and a seeded catalog.

    The catalog is seeded from the same committed literals the recipe suite uses
    so the lifespan's boot-time resolve is not a 503 on every shortlist test — the
    shortlist routes never read stock, but the boot sequence does, and a boot that
    raised would take every route with it.
    """
    configured = make_settings(runtime_root)
    seed_catalog(configured.pantry_items_db, API_CATALOG_ROWS)
    write_recipe(configured.vault_path, MAIN_RECIPE)
    write_recipe(configured.vault_path, SECOND_RECIPE, SECOND_RECIPE_BYTES)
    write_pantry_note(configured.vault_path)
    with client_for(configured) as client:
        yield _App(client=client, settings=configured, origin=ORIGIN)


def _envelope(response: Any) -> dict[str, Any]:
    """Assert the exact two-key envelope and hand the body back."""
    body: dict[str, Any] = response.json()
    assert set(body) == ENVELOPE_KEYS, body
    assert body["requestId"] == response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"
    return body


def _assert_items(response: Any, expected: list[tuple[str, bool]]) -> None:
    """The `{"slot", "items"}` shape, key-for-key, order-for-order."""
    body = response.json()
    assert set(body) == MUTATION_KEYS, body
    assert [(item["noteName"], item["resolved"]) for item in body["items"]] == expected
    for item in body["items"]:
        assert set(item) == ITEM_KEYS, item


# --- 1. the empty state, which is the load-bearing behaviour ---------------


def test_a_fresh_install_publishes_three_empty_slots_and_no_error(
    app: _App,
) -> None:
    """**The D3 rule, asserted in the shape that could only be got wrong one way.**

    A 200, three keys, three empty arrays. Not a 404, not a 503, no `error` key,
    and — the part a status assertion cannot see — **no slot omitted**. A body
    carrying only the slots that have rows would make "the user has no lunch list"
    and "this build forgot lunch" the same wire answer, and the client renders
    three panels from whatever keys it finds, so a missing one silently loses a
    panel the user curated.
    """
    response = app.client.get("/api/shortlists")

    assert response.status_code == 200
    assert set(response.json()) == LIST_KEYS
    assert response.json() == {"breakfast": [], "lunch": [], "dinner": []}
    # The keys, in order. `GET` is not a refusal, so there is no `code` to look
    # for — asserted by the exact key set above; this is the readable restatement.
    assert list(response.json()) == ["breakfast", "lunch", "dinner"]


def test_the_empty_state_carries_no_error_code_and_no_retryable_shape(
    app: _App,
) -> None:
    """F7's in-band rule, pointed at the case that is most tempted to break it.

    A 200 whose body grew a `status`, a `warning`, a `retryable`, or an
    `unresolvedCount` would be a *third* way to express the drift — and for the
    empty case it would be a way to express "nothing here" as data the UI has to
    interpret. The exact key set is the assertion; the enumeration is only there
    to make a failure legible.
    """
    body = app.client.get("/api/shortlists").json()

    for forbidden in ("status", "error", "warning", "retryable", "code", "requestId"):
        assert forbidden not in body, forbidden


def test_a_populated_shortlist_has_the_same_shape_as_an_empty_one(app: _App) -> None:
    """The positive control, so the shape assertion above cannot pass vacuously.

    One row in `lunch`, and the other two slots are still `[]` — the same
    three-key body as the fresh install, with one array non-empty. If the route
    ever grew a key *because* a slot had content, this fails where the empty-state
    test would not.
    """
    assert app.add("lunch", MAIN_RECIPE).status_code == 200

    body = app.client.get("/api/shortlists").json()

    assert set(body) == LIST_KEYS
    assert body["breakfast"] == []
    assert body["dinner"] == []
    assert body["lunch"] == [{"noteName": MAIN_RECIPE, "resolved": True}]


def test_a_removed_row_returns_to_the_empty_state_rather_than_a_404(app: _App) -> None:
    """Remove the last row and the slot is empty again — still a 200, still `[]`.

    The user-facing statement of the same rule: an empty shortlist is a state the
    app returns to, not a terminal condition. A client that treated "empty" as an
    error would have to special-case the state it just created.
    """
    app.add("lunch", MAIN_RECIPE)

    removed = app.remove("lunch", MAIN_RECIPE)

    assert removed.status_code == 200
    assert removed.json()["items"] == []
    assert app.client.get("/api/shortlists").json()["lunch"] == []


# --- 2. add ----------------------------------------------------------------


def test_add_publishes_the_slot_and_its_items(app: _App) -> None:
    """§9.16's `POST` success shape, exactly.

    `{"slot", "items"}` and no `recipeNote` echoed back: the client sent it and
    now has it in `items`, so a third copy of the same fact is a field a second
    surface would have to agree on.
    """
    app.add("breakfast", MAIN_RECIPE)

    response = app.add("breakfast", SECOND_RECIPE)

    assert response.status_code == 200
    _assert_items(response, [(MAIN_RECIPE, True), (SECOND_RECIPE, True)])


def test_add_appends_rather_than_prepending(app: _App) -> None:
    """A `+ 早餐` tap appends. The user reorders explicitly; `add` never guesses.

    Prepending would be defensible for a "most recent first" list and wrong for
    this one: §8 story 37 is "so that my most-used dish is first", which is a
    curation decision the user makes with the stepper, not something a tap should
    do behind their back.
    """
    app.add("dinner", MAIN_RECIPE)

    response = app.add("dinner", SECOND_RECIPE)

    assert [item["noteName"] for item in response.json()["items"]] == [
        MAIN_RECIPE,
        SECOND_RECIPE,
    ]


def test_adding_the_same_recipe_twice_is_a_200_with_the_same_items(app: _App) -> None:
    """A double tap is a no-op, at the route, with no error and no second row.

    Not a 409 and not a second row. The unique index makes the write a no-op, the
    store returns the current list, and the user's actual question — "is it on my
    list?" — is answered `true`. A 409 here would make the ordinary consequence of
    a fast double tap look like a failure.
    """
    first = app.add("lunch", MAIN_RECIPE)
    second = app.add("lunch", MAIN_RECIPE)
    listing = app.client.get("/api/shortlists").json()

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    _assert_items(second, [(MAIN_RECIPE, True)])
    assert listing["lunch"] == [{"noteName": MAIN_RECIPE, "resolved": True}]


def test_add_accepts_a_recipe_on_all_three_slots_at_once(app: _App) -> None:
    """One recipe, three lists. The uniqueness is `(slot, recipe_note)`, and the
    route is where a client would discover that."""
    for slot in MEAL_SLOTS:
        assert app.add(slot, MAIN_RECIPE).status_code == 200

    body = app.client.get("/api/shortlists").json()

    assert {slot: body[slot] for slot in MEAL_SLOTS} == {
        slot: [{"noteName": MAIN_RECIPE, "resolved": True}] for slot in MEAL_SLOTS
    }


@pytest.mark.parametrize("slot", ["snack", "brunch", "Breakfast", "LUNCH", "lunch "])
def test_a_fourth_slot_is_a_422_with_a_named_code(app: _App, slot: str) -> None:
    """F12 at the route: 422, and a code a client can branch on.

    `Breakfast` and `LUNCH ` are here because a case-insensitive or
    whitespace-tolerant check would accept them, and the `CHECK` in `schema.sql`
    would then refuse the write with a 500 instead of a 422. The code is
    `unknown_meal_slot` rather than the generic `invalid_request`, because "there
    is no such slot" and "your body is malformed" are different things for a UI
    rendering three fixed panels.
    """
    response = app.add(slot, MAIN_RECIPE)

    assert response.status_code == 422
    assert _envelope(response)["code"] == UNKNOWN_MEAL_SLOT == "unknown_meal_slot"
    # And no fourth panel appeared: the listing is still exactly three keys, so a
    # route that had widened the enum to echo the requested slot would fail here
    # rather than shipping a panel the schema cannot hold.
    body = app.client.get("/api/shortlists").json()
    assert set(body) == LIST_KEYS
    assert all(items == [] for items in body.values())


def test_add_is_a_422_for_a_recipe_the_vault_does_not_have(app: _App) -> None:
    """The request names a recipe, so the server decides whether it exists.

    A client may select *which* recipe; it may not assert that a recipe exists.
    The name is validated against the in-memory index and never joined to a path,
    which is the same rule `GET /api/recipes/{note_name}` follows and the reason
    both routes 404-or-422 the same invented name.
    """
    response = app.add("lunch", "不存在的菜")

    assert response.status_code == 422
    assert _envelope(response)["code"] == UNKNOWN_RECIPE_NOTE == "unknown_recipe_note"
    assert app.client.get("/api/shortlists").json()["lunch"] == []


def test_a_refusal_never_reflects_the_name_back(app: _App) -> None:
    """F7, and the reason the codes are class attributes.

    A `message` naming the recipe the user typed would widen the envelope (§9.16
    permits exactly two widened codes, both the Cooking Log's) and would make
    every refusal a reflection of user input. The two-key envelope is asserted
    key-for-key, not by the absence of one field name.
    """
    response = app.add("lunch", "不存在的菜")

    assert set(response.json()) == ENVELOPE_KEYS
    assert "不存在的菜" not in response.text


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"recipeNote": "番茄炒蛋", "path": "Hobbies/做饭/Recipes/番茄炒蛋.md"},
        {"recipeNote": "番茄炒蛋", "slot": "lunch"},
        {"recipeNote": ""},
        {"recipeNote": 7},
        {"recipeNote": ["番茄炒蛋"]},
        {"noteName": "番茄炒蛋"},
    ],
)
def test_add_refuses_a_body_that_is_not_exactly_the_documented_shape(
    app: _App, body: dict[str, Any]
) -> None:
    """`extra="forbid"`, and a 422 for every shape that is not `{recipeNote: str}`.

    The `path` case is the Server-Owned Root rule as a body: an **ignored** field
    is a field that looks like it worked, so a body carrying a path-shaped key is
    refused rather than dropped — the same rule F4 set for the Cooking Log's
    `recipeNote`, and the reason a client cannot believe it sent a path and had it
    quietly honoured. The `slot` case is the same idea for the slot: it is in the
    path, and a body that also names it is a request that disagrees with itself.
    """
    response = app.client.post(
        "/api/shortlists/lunch", json=body, headers=app.mutation
    )

    assert response.status_code == 422, body
    _envelope(response)


def test_add_accepts_the_snake_case_field_name_as_the_shipped_models_do(
    app: _App,
) -> None:
    """`populate_by_name=True`, because both shipped request models do it.

    `ManualMappingRequest` and `CookLogRequest` both set it, so both the wire
    spelling and the Python field name are accepted. Following the convention
    rather than diverging from it is worth the note: it is a permissive *input*
    alias, not a second response shape, and F7's rule is about the payload the
    client parses. The test is here so the permissiveness is a decision on the
    record instead of an accident nobody notices.
    """
    by_wire = app.add("lunch", MAIN_RECIPE)
    by_field = app.client.post(
        "/api/shortlists/lunch", json={"recipe_note": SECOND_RECIPE}, headers=app.mutation
    )

    assert by_wire.status_code == by_field.status_code == 200
    _assert_items(by_field, [(MAIN_RECIPE, True), (SECOND_RECIPE, True)])


def test_add_refuses_a_path_where_a_basename_belongs(app: _App) -> None:
    """A well-formed body naming a *path* is still refused, as a 422.

    Distinct from the previous test, which is about the body's shape: this one
    passes pydantic and is stopped by the store, because `recipeNote` is a
    perfectly good string that just happens to contain a `/`. The client
    contributes *which recipe*, never *which path*.
    """
    response = app.add("lunch", "Hobbies/做饭/Recipes/番茄炒蛋.md")

    assert response.status_code == 422
    _envelope(response)
    assert app.client.get("/api/shortlists").json()["lunch"] == []


# --- 3. remove -------------------------------------------------------------


def test_remove_publishes_the_slot_and_the_remaining_items(app: _App) -> None:
    """§9.16's `DELETE` success shape, and the re-sequence is visible in it.

    Removing the middle of three leaves two, dense from zero, in the original
    relative order. Asserted on the route's own response because that is what the
    client re-renders from — a store that re-sequenced correctly but a route that
    returned a stale cache would pass a store test and fail this one.
    """
    for note in (MAIN_RECIPE, SECOND_RECIPE):
        app.add("lunch", note)

    response = app.remove("lunch", MAIN_RECIPE)

    assert response.status_code == 200
    _assert_items(response, [(SECOND_RECIPE, True)])


def test_remove_is_a_404_for_a_row_that_is_not_there(app: _App) -> None:
    """404, and it is a 404 rather than a 200 no-op.

    "You asked to remove something from a list it is not on" and "removed" are
    different answers, and a UI that confused them would drop a row the user is
    still looking at. The empty-state rule does not apply here: an empty *slot* is
    a state, a missing *row* is a client error.
    """
    app.add("lunch", MAIN_RECIPE)

    wrong_slot = app.remove("breakfast", MAIN_RECIPE)
    absent_row = app.remove("lunch", SECOND_RECIPE)

    assert wrong_slot.status_code == 404
    assert absent_row.status_code == 404
    for response in (wrong_slot, absent_row):
        assert _envelope(response)["code"] == SHORTLIST_ENTRY_MISSING
    assert app.client.get("/api/shortlists").json()["lunch"] == [
        {"noteName": MAIN_RECIPE, "resolved": True}
    ]


def test_remove_validates_the_slot_before_the_row(app: _App) -> None:
    """A bad slot is a 422 even when the row is also absent.

    The order `app/api/recipes.py::_locate` fixes, for the same reason: a
    malformed request gets the cheaper, state-independent answer, so a client can
    always tell "you named a slot that cannot exist" from "no such row".
    """
    response = app.remove("snack", MAIN_RECIPE)

    assert response.status_code == 422
    assert _envelope(response)["code"] == UNKNOWN_MEAL_SLOT


def test_remove_touches_no_other_slot(app: _App) -> None:
    """Positions are per-slot, and a `DELETE` renumbers only its own."""
    app.add("lunch", MAIN_RECIPE)
    app.add("dinner", MAIN_RECIPE)
    app.add("dinner", SECOND_RECIPE)

    app.remove("lunch", MAIN_RECIPE)

    body = app.client.get("/api/shortlists").json()
    assert body["lunch"] == []
    assert [item["noteName"] for item in body["dinner"]] == [MAIN_RECIPE, SECOND_RECIPE]


# --- 4. reorder ------------------------------------------------------------


def test_reorder_publishes_the_new_order(app: _App) -> None:
    """§9.16's `PUT` success shape, and the new order survives the round trip.

    The re-read through `GET` is the half that matters: a `PUT` that returned the
    new order without writing it would satisfy the first assertion and leave the
    next read — and the next boot — reporting the old one.
    """
    for note in (MAIN_RECIPE, SECOND_RECIPE):
        app.add("lunch", note)

    response = app.order("lunch", [SECOND_RECIPE, MAIN_RECIPE])

    assert response.status_code == 200
    _assert_items(response, [(SECOND_RECIPE, True), (MAIN_RECIPE, True)])
    body = app.client.get("/api/shortlists").json()
    assert [item["noteName"] for item in body["lunch"]] == [SECOND_RECIPE, MAIN_RECIPE]


def test_reorder_of_an_empty_slot_is_a_200_not_a_mismatch(app: _App) -> None:
    """The empty state through the mutation path too.

    `order: []` against a slot with no rows is exactly consistent, so it succeeds
    and changes nothing. This is the assertion that separates the membership rule
    from "reject anything empty": the *populated*-slot case below is the 422, and
    having both is what proves the rule is about membership rather than about
    emptiness.
    """
    response = app.order("lunch", [])

    assert response.status_code == 200
    _assert_items(response, [])
    assert app.client.get("/api/shortlists").json()["lunch"] == []


@pytest.mark.parametrize(
    ("order", "why"),
    [
        ([MAIN_RECIPE], "a dropped member"),
        ([MAIN_RECIPE, SECOND_RECIPE, "清蒸鲈鱼"], "an invented member"),
        ([MAIN_RECIPE, MAIN_RECIPE], "a repeated member"),
    ],
)
def test_reorder_is_a_422_unless_the_membership_is_exact(
    app: _App, order: list[str], why: str
) -> None:
    """422 `shortlist_order_mismatch`, and **nothing was reconciled** — `why` is in
    the message so a failure says which rule broke.

    A reorder is the one write with a whole-list argument, so a request naming a
    subset, a superset, or a different set is asking for something the client
    cannot have meant. Reconciling it silently would either lose a curated row or
    add one the user never chose, and the client would render a list it did not
    ask for as though it had.
    """
    for note in (MAIN_RECIPE, SECOND_RECIPE):
        app.add("lunch", note)
    before = app.client.get("/api/shortlists").json()

    response = app.order("lunch", order)

    assert response.status_code == 422, why
    assert _envelope(response)["code"] == ORDER_MISMATCH == "shortlist_order_mismatch"
    assert app.client.get("/api/shortlists").json() == before, f"{why} was reconciled"


def test_a_populated_shortlist_cannot_be_cleared_by_ordering_it_empty(app: _App) -> None:
    """`order: []` against a populated slot is the dangerous shape, and it is a 422.

    This is the one that matters most, and the reason §9.12 says a reorder that
    drops a member is refused rather than reconciled: a client bug, or a list
    rendered from a stale read, would otherwise delete a curated list by asking for
    it with the wrong array. Clearing a list is what the `×` on each row is for.
    """
    app.add("lunch", MAIN_RECIPE)
    app.add("lunch", SECOND_RECIPE)

    response = app.order("lunch", [])

    assert response.status_code == 422
    assert _envelope(response)["code"] == ORDER_MISMATCH
    assert len(app.client.get("/api/shortlists").json()["lunch"]) == 2


def test_reorder_is_a_422_for_a_fourth_slot(app: _App) -> None:
    """The enum is checked before the body, so a bad slot wins over a bad order."""
    response = app.order("snack", [])

    assert response.status_code == 422
    assert _envelope(response)["code"] == UNKNOWN_MEAL_SLOT


@pytest.mark.parametrize("body", [{}, {"items": []}, {"order": "番茄炒蛋"}, {"order": [7]}])
def test_reorder_refuses_a_body_that_is_not_exactly_the_documented_shape(
    app: _App, body: dict[str, Any]
) -> None:
    """`extra="forbid"`, and no coercion: `"番茄炒蛋"` is a string, not a one-item list.

    A pydantic `list[str]` will not accept a bare string, and that refusal is the
    point — an `order` that had been coerced from `"番茄炒蛋"` into `["番茄炒蛋"]`
    would be indistinguishable from a deliberate one-member reorder, and would
    reconcile against a two-row list by dropping a row.
    """
    response = app.client.put(
        "/api/shortlists/lunch/order", json=body, headers=app.mutation
    )

    assert response.status_code == 422, body
    _envelope(response)


def test_reorder_is_bounded_at_fifty_rows(app: _App) -> None:
    """§7.4's bound, and the code is a shortlist one rather than a generic 422.

    The bound is the store's rather than pydantic's so a too-long order gets a code
    a client can act on, and so the same bound reaches a list the store never
    filled. Unreachable through `add` — which is capped at the same number, so a
    list cannot grow past what `reorder` can handle — so the rows are written
    directly, which is how a restored backup would present them.
    """
    async def seed_over_limit() -> None:
        async with connect_db(app.settings) as conn:
            await conn.executemany(
                "INSERT INTO meal_lists (slot, position, recipe_note) VALUES ('dinner', ?, ?)",
                [(index, f"菜{index}") for index in range(51)],
            )
            await conn.commit()

    asyncio.run(seed_over_limit())
    over = [f"菜{index}" for index in range(51)]

    response = app.order("dinner", over)

    assert response.status_code == 422
    assert _envelope(response)["code"] == SHORTLIST_ORDER_TOO_LONG
    # And a `remove` is the way out, so the bound is a refusal the user can act on
    # rather than a permanently frozen list.
    assert app.remove("dinner", over[0]).status_code == 200
    assert app.order("dinner", over[1:]).status_code == 200


def test_add_is_refused_once_a_slot_is_at_its_bound(app: _App) -> None:
    """The cap is on `add` too, so a list can never become one that cannot be
    reordered — and the code is `shortlist_full`."""
    async def seed_at_limit() -> None:
        async with connect_db(app.settings) as conn:
            await conn.executemany(
                "INSERT INTO meal_lists (slot, position, recipe_note) VALUES ('breakfast', ?, ?)",
                [(index, f"菜{index}") for index in range(50)],
            )
            await conn.commit()

    asyncio.run(seed_at_limit())

    response = app.add("breakfast", MAIN_RECIPE)

    assert response.status_code == 422
    assert _envelope(response)["code"] == SHORTLIST_FULL == "shortlist_full"


# --- 5. rename drift: `⚠ 已重命名` ------------------------------------------


def test_a_renamed_recipe_comes_back_marked_unresolved_and_is_still_listed(
    app: _App,
) -> None:
    """**The drift treatment, asserted as the two things it must both be.**

    `resolved: false` — the input to the client's dimmed `⚠ 已重命名` row — **and
    the row is still in the response at all.** A route that filtered unresolved
    rows out would pass a test that only checked a flag, and would be the exact
    silent drop §9.12 forbids: dropping user data on a rename is worse than
    showing it broken.

    The rename is a real file rename plus an index invalidation, not a stub, so
    this is evidence about the shipped read path.
    """
    app.add("lunch", MAIN_RECIPE)
    app.add("lunch", SECOND_RECIPE)

    app.rename(MAIN_RECIPE, "番茄炒蛋（新）")

    items = app.client.get("/api/shortlists").json()["lunch"]

    assert [(item["noteName"], item["resolved"]) for item in items] == [
        (MAIN_RECIPE, False),
        (SECOND_RECIPE, True),
    ]
    assert len(items) == 2, "the renamed row was dropped"


def test_a_renamed_recipe_is_not_a_404_on_read_and_not_a_500_either(
    app: _App,
) -> None:
    """A broken row is a *row*, so the list reads 200 and the slot is not empty.

    The three plausible wrong answers, all refused: a 404 for the whole route (the
    empty-state rule misapplied to a broken row), a 503 because the index "cannot
    resolve it", and a 200 with the row filtered out. The empty-state rule is
    about a slot with no *rows*; this is a slot with a row whose name is stale.
    """
    app.add("dinner", MAIN_RECIPE)
    app.rename(MAIN_RECIPE, "番茄炒蛋（新）")

    response = app.client.get("/api/shortlists")

    assert response.status_code == 200
    assert response.json()["dinner"] == [{"noteName": MAIN_RECIPE, "resolved": False}]


def test_a_renamed_recipe_can_still_be_removed_by_its_stored_name(
    app: _App,
) -> None:
    """**The half of the drift treatment that is easy to get wrong backwards.**

    If `DELETE` resolved `note_name` against the live index — which is what §9.16
    says about `{note_name}` generally — this would be a **404**: not by the name
    the user sees (which no longer resolves) and not by the old one (which the
    validator would refuse). The `⚠ 已重命名` row would then be permanently
    unreachable, and keeping it would be pointless.

    So the delete route looks the name up in the slot's stored rows, and the test
    asserts the 200 *and* the row's disappearance.
    """
    app.add("lunch", MAIN_RECIPE)
    app.add("lunch", SECOND_RECIPE)
    app.rename(MAIN_RECIPE, "番茄炒蛋（新）")

    response = app.remove("lunch", MAIN_RECIPE)

    assert response.status_code == 200
    _assert_items(response, [(SECOND_RECIPE, True)])
    assert app.client.get("/api/shortlists").json()["lunch"] == [
        {"noteName": SECOND_RECIPE, "resolved": True}
    ]


def test_a_renamed_recipe_still_participates_in_a_reorder(app: _App) -> None:
    """A broken row is still a member of its slot.

    Both halves, because either one alone would be a bug: the reorder that
    *includes* it succeeds, and the one that *omits* it is a 422. If unresolved
    rows were excludable, the UI would have to send a subset and the membership
    rule that protects the list would be unreachable for exactly the rows that
    most need the user's attention.
    """
    for note in (MAIN_RECIPE, SECOND_RECIPE):
        app.add("lunch", note)
    app.rename(MAIN_RECIPE, "番茄炒蛋（新）")

    complete = app.order("lunch", [SECOND_RECIPE, MAIN_RECIPE])
    subset = app.order("lunch", [SECOND_RECIPE])

    assert complete.status_code == 200
    _assert_items(complete, [(SECOND_RECIPE, True), (MAIN_RECIPE, False)])
    assert subset.status_code == 422
    assert _envelope(subset)["code"] == ORDER_MISMATCH


def test_a_renamed_recipe_cannot_be_added_again_under_its_old_name(
    app: _App,
) -> None:
    """`POST` *is* index-validated, so the stale name is a 422.

    The asymmetry with `DELETE` is the design: `add` may only introduce a recipe
    that exists, and `remove` must be able to reach a row that no longer does. So
    the user cannot grow a second, permanently-broken row by tapping `+ 晚餐` on a
    note that has been renamed — and the broken row they already have stays
    repairable by remove-then-add.
    """
    app.add("lunch", MAIN_RECIPE)
    app.rename(MAIN_RECIPE, "番茄炒蛋（新）")

    response = app.add("lunch", MAIN_RECIPE)

    assert response.status_code == 422
    assert _envelope(response)["code"] == UNKNOWN_RECIPE_NOTE
    assert len(app.client.get("/api/shortlists").json()["lunch"]) == 1


def test_a_deleted_recipe_reads_exactly_like_a_renamed_one(app: _App) -> None:
    """Rename and removal are the same fact here, and the app must not guess.

    The index knows only that the name it stored is gone. Distinguishing "renamed"
    from "deleted" would need a tombstone this app has no way to keep, and both
    answers are the same thing to the user: this row points at nothing and only a
    `DELETE` clears it. The test removes the file and asserts the identical shape.
    """
    app.add("lunch", MAIN_RECIPE)
    (app.settings.vault_path / RECIPES_ROOT / f"{MAIN_RECIPE}.md").unlink()
    app.client.app.state.recipe_index.invalidate()

    body = app.client.get("/api/shortlists").json()

    assert body["lunch"] == [{"noteName": MAIN_RECIPE, "resolved": False}]


# --- 6. the guards, the registration, and the wiring ------------------------


def test_read_only_refuses_every_mutation_before_any_existence_check(
    runtime_root: Path,
) -> None:
    """**403 `read_only` on all three mutations, and `GET` still answers.**

    The guard order `app/auth.py` fixes is `host → identity → read_only → body
    size → origin → content-type → CSRF`, and `read_only` sits third — after
    identity, before everything that would let a request say anything about
    state. A read-only install must not be able to learn whether a recipe exists by
    attempting to add it, so the `read_only` 403 wins over the 422 the same
    request would get on a writable one. That is asserted by using a *real*
    recipe name and a full set of mutation headers, so nothing but the flag can be
    what answered.
    """
    configured = make_settings(runtime_root, OBSIDIAN_READ_ONLY="true")
    seed_catalog(configured.pantry_items_db, API_CATALOG_ROWS)
    write_recipe(configured.vault_path, MAIN_RECIPE)
    write_pantry_note(configured.vault_path)
    with client_for(configured) as client:
        token = str(client.get("/api/session").json()["csrfToken"])
        authorized = {"Origin": ORIGIN, "X-CSRF-Token": token}

        mutations = [
            client.post(
                "/api/shortlists/lunch",
                json={"recipeNote": MAIN_RECIPE},
                headers=authorized,
            ),
            client.delete(f"/api/shortlists/lunch/{MAIN_RECIPE}", headers=authorized),
            client.put(
                "/api/shortlists/lunch/order", json={"order": []}, headers=authorized
            ),
        ]
        for response in mutations:
            assert response.status_code == 403, response.request.url
            assert _envelope(response)["code"] == "read_only"
        # And the read path is untouched, including the empty state.
        listing = client.get("/api/shortlists")
        assert listing.status_code == 200
        assert listing.json() == {"breakfast": [], "lunch": [], "dinner": []}


def test_a_mutation_without_a_csrf_token_is_403_before_the_route(app: _App) -> None:
    """The guard order's tail, on a shortlist path specifically.

    `app/auth.py` is route-agnostic, so this is a wiring assertion: the new paths
    are under `/api/` and are therefore guarded, rather than being a set of
    unguarded writes someone forgot about.
    """
    for method, path in (
        ("post", "/api/shortlists/lunch"),
        ("delete", f"/api/shortlists/lunch/{MAIN_RECIPE}"),
        ("put", "/api/shortlists/lunch/order"),
    ):
        response = getattr(app.client, method)(path, headers={"Origin": ORIGIN})

        assert response.status_code == 403, path
        assert _envelope(response)["code"] == "csrf_required"


def test_the_order_route_is_not_shadowed_by_the_delete_route(app: _App) -> None:
    """Both verbs on the same shape, each reaching the route it should.

    `/api/shortlists/{slot}/order` also matches
    `/api/shortlists/{slot}/{note_name}` with `note_name == "order"`. Starlette
    keeps scanning past a path-match whose method does not fit, so `PUT` finds its
    own route and only `DELETE …/order` finds the delete one — and that is a real
    property of the router rather than something a declaration order guarantees,
    so it is driven for both verbs.
    """
    app.add("lunch", MAIN_RECIPE)

    put = app.order("lunch", [MAIN_RECIPE])
    delete = app.remove("lunch", "order")

    assert put.status_code == 200, put.json()
    _assert_items(put, [(MAIN_RECIPE, True)])
    # `DELETE …/order` reaches the *delete* route, and there is no row by that
    # name, so it is the delete route's own 404 — not a 405 and not a reorder.
    assert delete.status_code == 404
    assert _envelope(delete)["code"] == SHORTLIST_ENTRY_MISSING


def test_no_shortlist_route_reads_a_query_parameter(app: _App) -> None:
    """F8: `strict` is a query parameter on `/api/recipes` and appears nowhere here.

    A shortlist entry is a name and a resolution flag; nothing about it is scored
    against stock, so there is nothing for `严格模式` to change. Asserted by
    sending `?strict=1`, `?debug=1`, and a `limit` and showing the body is
    byte-identical to the plain read — a second way to express the flag is exactly
    what F8 forbids, and it would be introduced by the kind of "harmless" extra
    parameter this router could otherwise have grown.
    """
    app.add("lunch", MAIN_RECIPE)
    plain = app.client.get("/api/shortlists")

    for query in ("?strict=1", "?debug=1", "?limit=1", "?strict=0&debug=0"):
        response = app.client.get(f"/api/shortlists{query}")

        assert response.status_code == 200, query
        assert response.json() == plain.json(), query


def test_no_shortlist_payload_ever_carries_a_cookable_boolean(app: _App) -> None:
    """F17, asserted as an exact key set at both levels.

    `assert "cookable" not in body` would pass just as happily on a response that
    grew a `canCook` instead, so the key sets are asserted instead — and the
    `⚠ 已重命名` entry proves the drift is carried by `resolved` and not by a
    boolean of its own.
    """
    app.add("lunch", MAIN_RECIPE)
    app.rename(MAIN_RECIPE, "番茄炒蛋（新）")

    body = app.client.get("/api/shortlists").json()
    assert set(body) == LIST_KEYS
    for items in body.values():
        for item in items:
            assert set(item) == ITEM_KEYS
            assert isinstance(item["resolved"], bool)


def test_the_store_is_published_on_app_state_under_the_key_the_router_reads(
    app: _App,
) -> None:
    """The handoff: `app.state[MEAL_SHORTLIST_STORE_STATE_KEY]` is the store, and
    the route really finds it.

    Asserted as the concrete class, not as "something is there": a key resolving
    to the wrong object would satisfy `is not None` and then fail at the first
    request. The route call is the second half, and it is the half that matters —
    it is the `ResourceUnavailable` → 503 branch that a missing publication takes.
    """
    published = app.client.app.state[MEAL_SHORTLIST_STORE_STATE_KEY]

    assert isinstance(published, MealShortlistStore)
    assert MEAL_SHORTLIST_STORE_STATE_KEY == "meal_shortlist_store"
    assert app.client.get("/api/shortlists").status_code == 200


def test_a_read_without_the_published_store_is_a_503_not_an_attribute_error(
    runtime_root: Path,
) -> None:
    """The lifespan never ran, so the store is absent — and the route says so.

    `app/api/resources.py` exists so the two sides cannot disagree on a key, and
    this is the assertion that a disagreement would be a 503 with a named code
    rather than the `AttributeError`-on-`None` 500 the module's own docstring
    predicts. Built without entering the `TestClient` context manager, so the
    `lifespan` genuinely has not published anything.
    """
    from fastapi.testclient import TestClient

    from app.main import create_app

    configured = make_settings(runtime_root)
    client = TestClient(create_app(configured), base_url=ORIGIN)

    for response in (
        client.get("/api/shortlists"),
        client.post(
            "/api/shortlists/lunch",
            json={"recipeNote": MAIN_RECIPE},
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": str(client.get("/api/session").json()["csrfToken"]),
            },
        ),
    ):
        assert response.status_code == 503, response.request.url
        assert _envelope(response)["code"] == "resource_unavailable"


def test_the_router_publishes_only_codes_the_store_declares() -> None:
    """Every 4xx the router can answer is a `ShortlistError` class attribute.

    The router maps exception types to statuses and publishes `exception.code`. A
    status the router invented inline would be a code no client could rely on and
    no test in `tests/shortlists/test_store.py` would have declared, so the
    exported names and the store's codes are compared as sets.
    """
    from app.shortlists import store as module

    assert {
        UNKNOWN_MEAL_SLOT,
        UNKNOWN_RECIPE_NOTE,
        ORDER_MISMATCH,
        SHORTLIST_FULL,
        SHORTLIST_ORDER_TOO_LONG,
        SHORTLIST_ENTRY_MISSING,
    } == {
        module.UnknownMealSlot.code,
        module.UnknownRecipeNote.code,
        module.OrderMembershipMismatch.code,
        module.ShortlistFull.code,
        module.ShortlistOrderTooLong.code,
        module.ShortlistEntryMissing.code,
    }


def test_the_router_and_the_store_agree_on_the_slot_names_and_the_bound() -> None:
    """One enum, imported, not restated.

    The route renders whatever keys `entries()` returns, so the two cannot disagree
    today. They could tomorrow, if a fourth slot were added to the store and the
    router kept a local copy of the three — and then a panel would render with no
    data, or a slot would render with data the route never validated. The import
    is the assertion.
    """
    from app.api import shortlists as router_module
    from app.shortlists import store as module

    assert router_module.MEAL_SLOTS is MEAL_SLOTS is module.MEAL_SLOTS
    assert router_module.MAX_SHORTLIST_ENTRIES == module.MAX_SHORTLIST_ENTRIES


def test_the_four_route_paths_are_the_ones_the_spec_names() -> None:
    """§9.16's four paths, spelled out in §9.16's order.

    `tests/scaffold/test_routes.py` pins the *position* of every route in the
    flattened table; this pins the *names*, which is the other half. An order test
    cannot tell that `/api/shortlists/{slot}/order` had been spelled `reorder`.
    """
    from cooklog.harness import registered_paths_of

    assert registered_paths_of(build_shortlists_router()) == [
        "/api/shortlists",
        "/api/shortlists/{slot}",
        "/api/shortlists/{slot}/{note_name}",
        "/api/shortlists/{slot}/order",
    ]


def test_a_shortlist_mutation_never_writes_to_the_vault(app: _App) -> None:
    """D3's load-bearing negative, at the route, across a full cycle.

    A Meal Shortlist is PWA-owned state and is never synced back. So the vault is
    byte-identical before and after add / reorder / remove, no recipe note gains a
    `餐次` key, and the shortlist rows are only in the PWA's SQLite. This is the
    assertion that would fail if a future ticket ever decided a Meal Shortlist
    "should really" live in Obsidian.
    """
    vault = app.settings.vault_path
    before = {path.name: path.read_bytes() for path in vault.rglob("*.md")}

    app.add("lunch", MAIN_RECIPE)
    app.add("lunch", SECOND_RECIPE)
    app.order("lunch", [SECOND_RECIPE, MAIN_RECIPE])
    app.remove("lunch", MAIN_RECIPE)

    after = {path.name: path.read_bytes() for path in vault.rglob("*.md")}
    assert after == before
    assert "餐次".encode() not in b"".join(after.values())


def test_the_shortlist_rows_live_in_the_pwa_database_and_nowhere_else(
    app: _App,
) -> None:
    """The rows are in `APP_DATA_DIR/recipes.sqlite3`, and in no vault file.

    Reads the table directly rather than through the store so the assertion is
    about where the bytes are. `APP_DATA_DIR` is outside the vault by
    `Settings`' own validation and by `AtomicNoteStore`'s refusal, which is what
    makes "PWA-owned, never synced back" a property of the layout and not only of
    a docstring.
    """
    app.add("lunch", MAIN_RECIPE)

    async def stored() -> list[tuple[str, str, int]]:
        async with connect_db(app.settings) as conn:
            cursor = await conn.execute(
                "SELECT slot, recipe_note, position FROM meal_lists ORDER BY id"
            )
            return [
                (str(row[0]), str(row[1]), int(row[2])) for row in await cursor.fetchall()
            ]

    assert asyncio.run(stored()) == [("lunch", MAIN_RECIPE, 0)]
    database = app.settings.app_data_dir / "recipes.sqlite3"
    assert database.is_file()
    assert not database.is_relative_to(app.settings.vault_path)


def test_a_shortlist_mutation_enqueues_nothing(app: _App) -> None:
    """F5: the outbox is #23's, and this ticket adds none of it.

    `shortlist_intents` exists from #3 and is **unwired**, so the table stays
    empty across a full mutation cycle. That is the negative the ticket asks for:
    #23 will wrap these three routes, so a line that enqueued here would be a
    second enqueue path for #23 to find and reason about, and would violate §9.18.1
    for the same reason the cook log is off the outbox — a blind replay of a
    `PUT` is a lost update.

    `X-Client-Id` is not read here either: accepted-and-ignored would imply a
    dedupe guarantee this ticket does not provide, and there is no reason for the
    client to send it until #23 says so.
    """
    app.add("lunch", MAIN_RECIPE)
    app.add("lunch", SECOND_RECIPE)
    app.order("lunch", [SECOND_RECIPE, MAIN_RECIPE])
    app.remove("lunch", MAIN_RECIPE)

    async def intents() -> int:
        async with connect_db(app.settings) as conn:
            cursor = await conn.execute("SELECT COUNT(*) FROM shortlist_intents")
            row = await cursor.fetchone()
            assert row is not None
            return int(row[0])

    assert asyncio.run(intents()) == 0


def test_a_client_id_header_changes_nothing_yet(app: _App) -> None:
    """The header is not read, so a replayed request is applied twice, once #23.

    Asserted because "not wired" must mean *not wired*, not "wired but the ledger
    row is missing". Sending the header today is a no-op, and a test that asserted
    the opposite would be asserting a guarantee that does not exist yet.
    """
    plain = app.add("lunch", MAIN_RECIPE)

    with_header = app.client.post(
        "/api/shortlists/lunch",
        json={"recipeNote": MAIN_RECIPE},
        headers={**app.mutation, "X-Client-Id": "abc-123"},
    )

    assert plain.status_code == with_header.status_code == 200
    assert with_header.json() == plain.json()

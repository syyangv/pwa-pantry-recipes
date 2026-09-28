"""`GET /api/shortlists`, `POST /api/shortlists/{slot}`,
`DELETE /api/shortlists/{slot}/{note_name}`, and
`PUT /api/shortlists/{slot}/order` — the four Meal Shortlist routes (D3, F12).

**Everything is read off `app.state`; this module opens nothing.** Same argument
as `app/api/recipes.py`, and the same §9.19 `lifespan` behind it: a router that
constructed a store would be a second, unclosed source of descriptors. The key
lives in `app/api/resources.py` so the two sides cannot disagree on it.

**The refusal order is fixed and each step is load-bearing.** `slot` is validated
against the closed enum first, because a malformed request is the cheapest thing
to refuse and the one that can never depend on stored state; then the *body* is
parsed, because a body that is not the documented shape is a malformed request
too; and only then does the store act. A client can therefore always tell "you
named a slot that cannot exist" from "you named a recipe that does not exist"
from "there is no such row to remove" from "that order is not this list" — four
different situations that a single generic 422 or a single 404 would collapse.

**`read_only` has already refused every mutation with 403 `read_only`** inside
`app/auth.py`, before this router was reached and before any existence check. The
order the guard order fixes is `host → identity → read_only → body size → origin
→ content-type → CSRF`, and every one of those runs in middleware, so a
read-only install cannot probe which recipes exist by attempting a removal.

**The empty state is the load-bearing behaviour of this file, and it is the one
thing a route is most tempted to get wrong.** `GET /api/shortlists` on a fresh
install is a **200** with `{"breakfast": [], "lunch": [], "dinner": []}`. Not a
404, not a 503, not an error code, and not a body that omits the empty slots: a
missing key makes "the user has no lunch list" and "this build forgot lunch" the
same wire answer, and a 404 makes a normal first-run state look like a failure.
Story 39 asks for an invitation, and the only way to render one is for the empty
list to arrive as an empty list. There is no code path here that can turn an
empty slot into a refusal — `entries()` returns a tuple for every slot and
`app/shortlists/store.py` raises nothing for zero rows.

**The wire item is `{noteName, resolved}`, and `resolved` is D3's drift, in-band.**
F7: provenance is unconditional, so there is no `?debug=1`, no reduced shape, and
no second endpoint that carries the flag instead. A `false` is what the client
renders as a dimmed `⚠ 已重命名` row, and **this server never emits that string** —
for the same reason `app/api/recipes.py` publishes no CSS class name: the evidence
travels, the rendering is the client's. F17: no `cookable` boolean anywhere, and
`tests/api/test_shortlists_api.py` asserts the exact key set rather than the
absence of one name, because `assert "cookable" not in body` would pass just as
happily on a response that grew a `canCook` instead.

**`{note_name}` is a basename resolved against the live index, and `DELETE` does
not resolve it at all.** §9.16's own rule is that `{note_name}` is
"URL-encoded … resolved against the in-memory index" and 404s if absent. For
`POST` that is exactly right: a shortlist may only hold recipes that exist, and a
bad name is a 422. For `DELETE` it would be a bug, and the store's docstring says
why at length: the whole point of keeping a `⚠ 已重命名` row is that the user can
remove it, and a `DELETE` that validated the name against the live index would
make that row un-removeable — not by the name the user sees (which no longer
resolves) and not by the old one (which the validator would refuse). So `DELETE`
looks the name up **in the slot's stored rows**, and a name that is not there is
`404 shortlist_entry_missing`. Renamed-and-present and deleted-outright are the
same fact to this store, and they get the same treatment: a `⚠ 已重命名` row, a
reachable `×`, and nothing auto-pruned.

**F8: `strict` is a query parameter on `/api/recipes` and appears nowhere here.**
A shortlist entry is a name and a resolution flag; nothing about it is scored
against stock, so there is nothing for a `严格模式` to change. These four routes
read no query parameter at all, which is asserted in the tests rather than left
as a convention — a second way to express the flag is what F8 forbids, and it
would be introduced by exactly the kind of "harmless" extra parameter this file
could have taken.

**F5: nothing here enqueues anything, and that is this ticket's whole outbox
commitment.** The offline outbox is #23's; `shortlist_intents` exists in the
schema from #3 and is unwired. These routes do not read `X-Client-Id`, do not
touch the ledger, and do not behave differently offline. §9.18.1's rule is the
one #16 must not violate: cook logs are not on the outbox either, and the reason
there — a 409 resolve panel that a blind retry turns into a duplicate — applies to
a shortlist `PUT` for exactly the same reason. What #23 has to do, and in which
layer, is stated in `app/shortlists/store.py`'s docstring.
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..shortlists.store import (
    MAX_SHORTLIST_ENTRIES,
    MEAL_SLOTS,
    OrderMembershipMismatch,
    ShortlistEntry,
    ShortlistEntryMissing,
    ShortlistError,
    ShortlistFull,
    ShortlistOrderTooLong,
    UnknownMealSlot,
    UnknownRecipeNote,
)
from .envelope import api_error
from .resources import ResourceUnavailable, meal_shortlist_store

#: §9.16's four 4xx codes, exported rather than spelled at the call sites so the
#: handler's mapping and the tests that assert it cannot drift. Each is the
#: `code` of the `ShortlistError` the store raises, except the two pydantic
#: refusals, which `app/main.py`'s `RequestValidationError` handler answers as
#: `invalid_request` before this router is reached at all.
UNKNOWN_MEAL_SLOT: Final = UnknownMealSlot.code
UNKNOWN_RECIPE_NOTE: Final = UnknownRecipeNote.code
ORDER_MISMATCH: Final = OrderMembershipMismatch.code
#: The two refusals whose codes come from the exception class rather than from a
#: module constant, re-exported so a test can assert "the router publishes only
#: codes the store declares" without importing the store's exception classes.
SHORTLIST_FULL: Final = ShortlistFull.code
SHORTLIST_ORDER_TOO_LONG: Final = ShortlistOrderTooLong.code
SHORTLIST_ENTRY_MISSING: Final = ShortlistEntryMissing.code


class AddToShortlistRequest(BaseModel):
    """`POST /api/shortlists/{slot}`'s whole body. `extra="forbid"`.

    An ignored field is a field that looks like it worked, so a body carrying a
    `path`-shaped key is refused rather than dropped — the same rule F4 set for
    the Cooking Log's `recipeNote`, and the same Server-Owned Root reason. The
    store validates the value as a basename as well; this model only says the
    shape is exactly `{recipeNote: string}` and nothing else.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    recipe_note: str = Field(alias="recipeNote", min_length=1)


class ReorderShortlistRequest(BaseModel):
    """`PUT /api/shortlists/{slot}/order`'s whole body. `extra="forbid"`.

    `order` is the **whole** list, not a delta and not a pair of indices. That is
    §9.12's rule and it is what makes the operation safe under concurrency: a
    client that sends the complete membership is asserting what it believes the
    list is, and the store either agrees exactly or refuses. A delta would have
    nothing to validate against and would silently apply to a list the client has
    not seen.

    No `max_length` here on purpose. The bound is the store's
    (`MAX_SHORTLIST_ENTRIES`, answered as `shortlist_order_too_long`) rather than
    pydantic's, so a too-long order gets a *shortlist* code a client can branch on
    instead of a generic `invalid_request` — and so the same bound applies to a
    list the store never filled, which a request-time bound alone would not reach.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    order: list[str]


def build_shortlists_router() -> APIRouter:
    """§9.16's four shortlist routes, and only those.

    Takes no arguments, for the reason `build_recipes_router` gives: the
    `lifespan` owns the store and its connection factory, and a router that
    constructed one would be a second thing nothing closes.
    """
    router = APIRouter()

    @router.get("/api/shortlists")
    async def list_shortlists(request: Request) -> JSONResponse:
        """All three slots. 200 even when all three are empty — that is the point.

        Answered from `entries()` alone. The drift flag is a per-row fact derived
        from the same read that produced the names, so a rename cannot be visible
        in the names and not in the flags, which is the plausible-looking wrong
        answer a second read would produce.

        The three keys are emitted from `MEAL_SLOTS` in order, so the payload
        always has exactly those three and never a fourth.
        """
        try:
            store = meal_shortlist_store(request)
        except ResourceUnavailable as exc:
            return _fail(request, 503, exc.code)
        listed = await store.entries()
        return JSONResponse(
            {slot: [_entry_body(item) for item in listed[slot]] for slot in MEAL_SLOTS}
        )

    @router.post("/api/shortlists/{slot}")
    async def add_to_shortlist(
        request: Request, slot: str, payload: AddToShortlistRequest
    ) -> JSONResponse:
        """Add one Recipe by **name**. 422 on a bad slot or an unknown recipe.

        The response is `{"slot", "items"}` rather than a 201 with no body,
        because the one-tap `+ 早餐` control navigates to `#/shortlists`
        afterwards and needs the slot's new contents to render without a second
        round trip. Re-adding a recipe already on the list is a 200 with the same
        items, not a 409: the unique index makes it a natural no-op and a double
        tap is exactly what it is protecting against.
        """
        try:
            store = meal_shortlist_store(request)
            items = await store.add(slot, payload.recipe_note)
        except ResourceUnavailable as exc:
            return _fail(request, 503, exc.code)
        except ShortlistError as exc:
            return _fail(request, 422, exc.code)
        return JSONResponse({"slot": slot, "items": _items_body(items)})

    @router.delete("/api/shortlists/{slot}/{note_name}")
    async def remove_from_shortlist(
        request: Request, slot: str, note_name: str
    ) -> JSONResponse:
        """Remove one row by its **stored** name, and re-sequence. 404 if absent.

        `note_name` is deliberately *not* resolved against the recipe index. See
        the module docstring: a `⚠ 已重命名` row has to stay removable by the name
        it is filed under, or keeping it would be pointless.
        """
        try:
            store = meal_shortlist_store(request)
            items = await store.remove(slot, note_name)
        except ResourceUnavailable as exc:
            return _fail(request, 503, exc.code)
        except ShortlistEntryMissing as exc:
            return _fail(request, 404, exc.code)
        except ShortlistError as exc:
            return _fail(request, 422, exc.code)
        return JSONResponse({"slot": slot, "items": _items_body(items)})

    @router.put("/api/shortlists/{slot}/order")
    async def reorder_shortlist(
        request: Request, slot: str, payload: ReorderShortlistRequest
    ) -> JSONResponse:
        """Rewrite one slot's order. 422 unless the order is exactly its contents.

        Declared after the `DELETE` route, in §9.16's table order, and the two
        have the same shape — `/api/shortlists/{slot}/order` also matches
        `/api/shortlists/{slot}/{note_name}` with `note_name == "order"`. Starlette
        keeps scanning past a path-match whose method does not fit (a *partial*
        match) until it finds a full one, so `PUT` reaches this route and only
        `DELETE …/order` reaches the delete route. That is a real interaction
        rather than an assumption, so
        `tests/api/test_shortlists_api.py::test_the_order_route_is_not_shadowed_by_the_delete_route`
        drives both verbs and asserts each reached the route it should.
        """
        try:
            store = meal_shortlist_store(request)
            items = await store.reorder(slot, payload.order)
        except ResourceUnavailable as exc:
            return _fail(request, 503, exc.code)
        except ShortlistError as exc:
            return _fail(request, 422, exc.code)
        return JSONResponse({"slot": slot, "items": _items_body(items)})

    return router


def _items_body(items: tuple[ShortlistEntry, ...]) -> list[dict[str, Any]]:
    """One slot's items, in order, each projected by `_entry_body`."""
    return [_entry_body(item) for item in items]


def _entry_body(entry: ShortlistEntry) -> dict[str, Any]:
    """`{noteName, resolved}` — exactly two keys, in this order, on every route.

    The two keys are the whole story: which recipe, and whether it still
    resolves. `position` is **not** published, because the list order already
    carries it and publishing an ordinal too would be a second source of truth
    about the same thing for a client to keep consistent. Nor is `createdAt`:
    when a row was added is not a fact any of the four routes needs, and
    `app/api/recipes.py` publishes timestamps only where a *write* is the thing
    being asked about.

    `resolved: false` is the `⚠ 已重命名` affordance's input, not the affordance
    itself. The string is never emitted here; see the module docstring.
    """
    return {"noteName": entry.note_name, "resolved": entry.resolved}


def _fail(request: Request, status: int, code: str) -> JSONResponse:
    """The two-key envelope, and nothing more.

    §9.16 widens the envelope for exactly two codes, both belonging to the
    Cooking Log. No shortlist code may add a `message`, a `date`, or a
    `relativePath` — in particular nothing here may echo the `recipe_note` the
    caller sent, since a refusal must not become a reflection of user input and
    the codes are class attributes precisely so that cannot happen by accident
    (`app/shortlists/store.py::ShortlistError`).
    """
    return api_error(request, status, code)


__all__ = [
    "MAX_SHORTLIST_ENTRIES",
    "MEAL_SLOTS",
    "ORDER_MISMATCH",
    "SHORTLIST_ENTRY_MISSING",
    "SHORTLIST_FULL",
    "SHORTLIST_ORDER_TOO_LONG",
    "UNKNOWN_MEAL_SLOT",
    "UNKNOWN_RECIPE_NOTE",
    "AddToShortlistRequest",
    "ReorderShortlistRequest",
    "build_shortlists_router",
]

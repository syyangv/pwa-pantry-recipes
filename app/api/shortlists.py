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

**F5: nothing here enqueues a Cooking Log, and that is this ticket's whole
commitment on the client side too.** The offline outbox is #23's; these three
routes are its **server half**, and the fourth (`POST /api/cook-logs`) stays
offline-excluded for the reason §9.18.1 gives in full. What #23 did here, in this
layer, is stated in `app/shortlists/intents.py`.

**`X-Client-Id` turns the three mutating routes into exactly-once, and the
wrapping is around the ROUTE, never inside the store.** #16's docstring fixed
this before the code existed: the store stays the single writer of `meal_lists`,
so this module *calls* `add` / `remove` / `reorder` and brackets the call with
§9.18.3's `BEGIN IMMEDIATE` → `SELECT` → mutate → `INSERT` → `COMMIT`. The
re-sequence, the position arithmetic, and the `ON CONFLICT DO NOTHING` are all
still the store's, in the store's transaction; nothing below re-implements one
line of them, and there is no second writer.

**The two refusal shapes a replayed intent can produce, and they are different
answers.** A hit with the *same* fingerprint returns the stored `response_json`
byte-for-byte, which is what makes delivery exactly-once from the client's point
of view. A hit with a *different* fingerprint is **409 `client_id_reused`**, and
the store is untouched — a key is bound to one intent, and silently accepting a
reuse would make the replay history unauditable. And a *refusal* — the 422 a
replayed `reorder` earns when the list moved under it — is **not recorded at
all**, so the client's corrected retry with the same key is a fresh miss rather
than a 409. That asymmetry is the reason this module is more than three
`try` blocks: recording the 422 would turn "your list changed" into "your key is
broken", which is a strictly worse thing to show a user.

**A replayed `reorder` is answered with §9.12's 422 and is never reconciled.**
The `order` body is fingerprinted as sent, so the server knows this is the
reorder the client meant, and the store then says the membership is not what it
was. Reconciling would mean choosing between two of the user's intentions
without asking.

**F1's fail-closed 503 cannot reach these three routes, and that is structural
rather than lucky.** An unreadable `Pantry.md` 503s `GET /api/recipes` and
`GET /api/recipes/{note}` — the whole list, with no partial answer — because the
Stock Join feeds every chip. A shortlist row is a *basename* and a position; it
is scored against nothing. So the three routes below read exactly two things off
`app.state`, the store and the ledger, and neither has ever opened `Pantry.md`.
The queued edit therefore replays cleanly **through and after** a 503, and a test
in `tests/api/test_shortlist_intents.py` drives that exact sequence.
"""


from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Final

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from ..shortlists.intents import (
    CLIENT_ID_REUSED,
    ClientIdReused,
    add_fingerprint,
    canonical_json,
    remove_fingerprint,
    reorder_fingerprint,
    serialized,
)
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
from .client_id import normalize_client_id
from .envelope import api_error
from .resources import ResourceUnavailable, meal_shortlist_store, shortlist_intent_ledger

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

#: The two codes the outbox's `X-Client-Id` contract adds, and the **only** two
#: §9.18.3 introduces. They are named here, rather than spelled at the call
#: sites, so the route's mapping and the tests that assert it cannot drift.
#:
#: `client_id_invalid` is a 400 and `client_id_reused` is a 409, matching
#: `pwa-deals`: a present-but-unusable key is a malformed request, and a key
#: bound to a different intent is a conflict with prior state rather than a
#: semantic failure of the mutation.
CLIENT_ID_INVALID: Final = "client_id_invalid"
CLIENT_ID_REUSED_CODE: Final = CLIENT_ID_REUSED

#: **Per-code discipline, the same shape as `app/api/cooklog.py`'s
#: `PRESENCE_CODES` / `CONFLICT_CODES`.** §9.16 widens the error envelope for
#: exactly two codes, both belonging to the Cooking Log, so every other code
#: still emits exactly `{"requestId", "code"}` — and that is a claim about a
#: *set*, which is why it lives in a named set rather than in prose. A shortlist
#: code that grew a `message` would be able to echo what the caller sent, and the
#: codes are class attributes precisely so that cannot happen by accident. This
#: set is the assertion: both ledger codes are in it, and nothing in it may
#: publish an optional key.
LEDGER_CODES: Final[frozenset[str]] = frozenset({CLIENT_ID_INVALID, CLIENT_ID_REUSED_CODE})

#: `ShortlistEntryMissing` is the one shortlist refusal that is not a 422: "you
#: asked to remove something from a list it is not on" and "removed" are
#: different answers, and a client that confused them would drop a row the user
#: is still looking at. Declared as a set so the mapping is one table rather than
#: three `except` clauses that could disagree, and so `tests/api/
#: test_shortlists_api.py` can assert the table rather than the branches.
REFUSAL_STATUS: Final[dict[type[ShortlistError], int]] = {
    ShortlistEntryMissing: 404,
}
DEFAULT_REFUSAL_STATUS: Final = 422


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
    ) -> Response:
        """Add one Recipe by **name**. 422 on a bad slot or an unknown recipe.

        The response is `{"slot", "items"}` rather than a 201 with no body,
        because the one-tap `+ 早餐` control navigates to `#/shortlists`
        afterwards and needs the slot's new contents to render without a second
        round trip. Re-adding a recipe already on the list is a 200 with the same
        items, not a 409: the unique index makes it a natural no-op and a double
        tap is exactly what it is protecting against.

        **A replay of this exact intent does not re-add.** The `X-Client-Id`
        lookup runs before the store is touched at all, and a matching
        fingerprint short-circuits to the stored bytes.
        """
        client_id = normalize_client_id(request.headers.get("X-Client-Id"))
        return await _guarded_mutation(
            request,
            client_id,
            slot=slot,
            fingerprint=add_fingerprint(slot, payload.recipe_note),
            perform=lambda store: store.add(slot, payload.recipe_note),
        )

    @router.delete("/api/shortlists/{slot}/{note_name}")
    async def remove_from_shortlist(
        request: Request, slot: str, note_name: str
    ) -> Response:
        """Remove one row by its **stored** name, and re-sequence. 404 if absent.

        `note_name` is deliberately *not* resolved against the recipe index. See
        the module docstring: a `⚠ 已重命名` row has to stay removable by the name
        it is filed under, or keeping it would be pointless.

        The fingerprint uses that same stored name, so a queued removal of a
        renamed row is recognised as the same intent when it replays.
        """
        client_id = normalize_client_id(request.headers.get("X-Client-Id"))
        return await _guarded_mutation(
            request,
            client_id,
            slot=slot,
            fingerprint=remove_fingerprint(slot, note_name),
            perform=lambda store: store.remove(slot, note_name),
        )

    @router.put("/api/shortlists/{slot}/order")
    async def reorder_shortlist(
        request: Request, slot: str, payload: ReorderShortlistRequest
    ) -> Response:
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

        **A replay of a reorder captured offline against a list edited elsewhere
        is answered with the 422, not a reconciliation, and records nothing.** The
        order is fingerprinted as sent, the store refuses the changed membership,
        and the client is expected to re-read and re-offer. See the module
        docstring for why recording that refusal would be worse than the refusal.
        """
        client_id = normalize_client_id(request.headers.get("X-Client-Id"))
        return await _guarded_mutation(
            request,
            client_id,
            slot=slot,
            fingerprint=reorder_fingerprint(slot, list(payload.order)),
            perform=lambda store: store.reorder(slot, payload.order),
        )

    return router


#: `store.<method>(...)` returning the slot's new entries. Named because every
#: call site is a one-line lambda over it, and the type is what tells a reader
#: that `_guarded_mutation` never touches `meal_lists` itself.
Perform = Callable[[Any], Awaitable[tuple[ShortlistEntry, ...]]]


class _Refusal(Exception):
    """A store refusal, already shaped into its envelope response.

    Carried as an exception rather than returned as a value because the guarded
    path has to be able to abandon the ledger transaction *and* hand the exact
    response object back, and a `(response | None)` union would push a `None`
    check into both halves. It is a private control-flow signal inside this one
    function: nothing outside catches it, `ledger.transaction` sees it as a
    `BaseException` and rolls back (correctly — nothing was written), and the
    caller's `except _Refusal` is the only place it becomes a response again.
    """

    def __init__(self, response: JSONResponse) -> None:
        super().__init__("shortlist_mutation_refused")
        self.response = response


async def _guarded_mutation(
    request: Request,
    client_id: str | None,
    *,
    slot: str,
    fingerprint: str,
    perform: Perform,
) -> Response:
    """One shortlist mutation, optionally exactly-once via `X-Client-Id`.

    **Without a key this is the plain path and the ledger is not constructed,
    not opened, and not touched.** That is deliberate: the ledger must not be a
    tax on a client with no offline queue, and a regression there would show up
    as a changed response for an entirely ordinary `+ 早餐` tap.

    **With a key, the order is fixed and each step is load-bearing** (§9.18.3):

    1. `BEGIN IMMEDIATE` — take the write lock *before* reading, so two requests
       carrying the same key are ordered rather than both reading "no row".
    2. `SELECT … WHERE client_id = ?`.
       - a hit with the **same** fingerprint → `COMMIT` and return the stored
         `response_json` **verbatim**. This is the exactly-once.
       - a hit with a **different** fingerprint → `ROLLBACK` and 409
         `client_id_reused`. Nothing has been written, and the store has not
         been touched at all.
    3. Perform the mutation. The store opens its own connection and runs its own
       transaction; this transaction is deliberately *not* extended across it,
       because a second write transaction on a second connection deadlocks
       against `BEGIN IMMEDIATE`, and because #16 fixed the store as the single
       writer with no transaction hook.
    4. `INSERT` the intent row carrying the canonical response, and return those
       same bytes — the first delivery and every replay are one string.

    **A refusal leaves at step 3 and records nothing.** The 422 a replayed
    `reorder` earns when the list moved, the 404 a replayed `remove` of an
    already-removed row earns, and the 422 for an unknown recipe are all
    answered as themselves with no ledger row written. That is what leaves the
    client's *corrected* retry under the same `client_id` a fresh miss instead of
    a 409.

    **Both collaborators are checked before any of this**, and either being
    absent answers 503 `resource_unavailable` — the same code the store alone
    would have produced, so a caller cannot tell the two apart and does not need
    to.
    """
    try:
        store = meal_shortlist_store(request)
    except ResourceUnavailable as exc:
        return _fail(request, 503, exc.code)
    if client_id is not None and not client_id:
        return _fail(request, 400, CLIENT_ID_INVALID)

    if client_id is None:
        try:
            return _canonical(await _body(request, store, slot, perform))
        except _Refusal as refusal:
            return refusal.response

    try:
        ledger = shortlist_intent_ledger(request)
    except ResourceUnavailable as exc:
        return _fail(request, 503, exc.code)

    # One holder at a time for this key, across the read, the store's own
    # transaction, and the write. See `app/shortlists/intents.py` for why the
    # three cannot be one SQLite transaction.
    async with serialized(client_id):
        try:
            cached = await ledger.lookup(client_id, fingerprint)
            if cached is not None:
                return _replayed(cached)
            # A `_Refusal` raised here leaves before `record`, so a refusal
            # becomes an intent row for nothing.
            body = await _body(request, store, slot, perform)
            return _replayed(await ledger.record(client_id, fingerprint, body))
        except _Refusal as refusal:
            return refusal.response
        except ClientIdReused:
            return _fail(request, 409, CLIENT_ID_REUSED_CODE)


async def _body(
    request: Request, store: Any, slot: str, perform: Perform
) -> dict[str, Any]:
    """The store call, or a `_Refusal` carrying the envelope it should answer.

    This is the **only** place a shortlist mutation reaches `meal_lists`, and it
    reaches it through the store and never around it. The re-sequence, the
    position arithmetic, and `ON CONFLICT DO NOTHING` are all still the store's,
    in the store's transaction; a second writer would have to re-implement one
    line of them, and none is re-implemented here.
    """
    try:
        items = await perform(store)
    except ShortlistError as exc:
        raise _Refusal(_fail(request, _refusal_status(exc), exc.code)) from exc
    return {"slot": slot, "items": _items_body(items)}


def _refusal_status(exc: ShortlistError) -> int:
    """The one table every shortlist refusal is answered through.

    Walked over the MRO rather than looked up by exact type, because the failure
    mode of a `dict[type, int]` lookup is precisely that a future subclass is
    "not found" and falls through to the default — a subclass of
    `ShortlistEntryMissing` would answer 422 for "that row is not on this list",
    which is the one answer §9.12 says must be 404.
    """
    for klass in type(exc).__mro__:
        status = REFUSAL_STATUS.get(klass)
        if status is not None:
            return status
    return DEFAULT_REFUSAL_STATUS


def _canonical(body: dict[str, Any]) -> Response:
    """`Response`, not `JSONResponse` — the bytes are the contract.

    The ledger stores `canonical_json(body)` and hands those exact bytes back on
    every replay, so a first delivery and its replay are the same string. A
    `JSONResponse` here would re-serialise with Starlette's own separators and
    `ensure_ascii=False` but *without* `sort_keys`, so the two would agree on the
    parsed object and disagree on the bytes — which is the one property this
    whole module exists to provide.
    """
    return Response(canonical_json(body), media_type="application/json")


def _replayed(cached: str) -> Response:
    """The stored `response_json`, sent as-is. Not re-parsed, not re-encoded."""
    return Response(cached, media_type="application/json")


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

"""`GET /api/recipes`, `GET /api/recipes/{note_name}`, `POST /api/recipes/resolve`,
and the two manual-mapping routes. D4's honest match display, F7's unconditional
provenance, F1's fail-closed 503, F17's `n/total` headline, and F2's visible
duplicate-slot conflicts.

**Everything is read off `app.state`; this module opens nothing.** §9.19 gives
the `lifespan` that job and says so in as many words, and a router that
constructed a reader would be a second, unclosed source of file descriptors. The
keys live in `app/api/resources.py` so the two sides cannot disagree on them.

**The 503 is the largest single obligation in this file, and its blast radius is
the whole app.** F1 splits "which product is this?" (the catalog) from "do I have
it right now?" (`Pantry.md`), and only the second one can fail at read time. When
`Logistics/库存/Pantry.md` is missing, unreadable, or unparseable,
`PantryStockIndex` raises `PantryError` and **every** recipe's chip colours are
unavailable — not one recipe's. The two rejected fallbacks are named in F1 and
are not negotiable:

- "assume in stock" silently inflates every score and is exactly the Pantry
  Item / Pantry Stock conflation `AGENTS.md` and `CONTEXT.md` forbid;
- "assume not in stock" silently deflates every score to `have-been-buying`,
  a plausible-looking wrong answer — the precise thing `AGENTS.md` #3 forbids;
- "serve the list with every chip downgraded" *looks like* data.

So `_load` reads the stock **first** and lets `PantryError` escape; the route
catches it and answers 503 `pantry_stock_unreadable` and nothing else. There is
no `except PantryError` anywhere in this module that returns a list, and
`tests/api/test_recipes_api.py` asserts that for all three failure modes *and*
asserts that no degraded list is ever a body.

**F17: no `cookable` boolean, in any response, in any mode.** Not the list, not
the detail, not under `?strict=1`. What the client gets is the evidence — the
per-slot chip inputs plus `found` / `total` — and
`app/static/js/logic/chip-class.js` and `format.js` turn that into a colour and
a sentence. `CONTEXT.md`'s boolean *Cookable* stays defined but unrendered.

**F7: provenance is unconditional.** Every slot in every response carries
`matchMethod`, `matchTier`, `pantryItemId`, `confidence`, `candidatesJson`, and
`stockJoinState`. There is no `?debug=1`, no reduced shape, and no second
response that could drift from this one; the 调试 toggle is a render switch over
`localStorage` (`app/static/js/prefs.js`).

**F8: `strict` is a query parameter, `0`/`1`, default `0`, stateless.** No
persisted setting, no SQLite row, no server-side counterpart.

**F20: no `limit`, no `offset`, no cursor.** Every recipe, always, including a
`0/6` one. `strict` is the only parameter these routes read, so a `?limit=` is
simply not consulted rather than silently truncating anything.

**`{note_name}` is looked up in the in-memory index, never joined to a path.**
D2 makes `note.note_name` the wikilink text, so the cook log, this route, and the
`⚠ 已重命名` drift check of §9.12 all read the same one field. The client selects
*which* recipe; the server still decides what that means, and a name the index
does not hold is a 404 rather than a path.

**Material slots read the mapping table; Seasoning slots are resolved per
response, and the reason is the schema.** `ingredient_mappings.
ingredient_index` is documented in `schema.sql` as a `材料` index, so a `调料`
slot has no row to read and nothing here may invent one — that would put a
frontmatter string into an audit anchor the app never read. Calling the ladder
for a Seasoning is pure and cheap (`resolve_ingredient` is pure and the catalog
snapshot is already in hand), and it is what F16 requires: strict mode counts a
Seasoning as found when the staples tier adopts it, and only an *unresolved*
Seasoning as missing. Without it, every Seasoning in the vault would read
"missing" under `?strict=1` and the toggle would be worthless — which is F16's
whole argument for the rule. A Seasoning's answer is therefore **derived per
response and never written**, which is also why the manual-mapping routes are
`ingredients/{index}` and reach `材料` slots only.

**`stockJoinState` is the Stock Join's own state, distinct from `matchMethod`.**
`matchMethod` describes the recipe→catalog resolution; `stockJoinState` describes
whether the *other* join — the one F1 calls the app's weakest link — could
explain the product this slot landed on, and by which tier. `joined` when the
slot's `pantryItemId` appears in a hit made on either catalog tier, `override`
when it appears only in a hit that fired the override tier, `unresolved`
otherwise — including when the slot resolved to no Pantry Item at all. It is
looked up **by id, never by a name and never by a line**, so a slot the join
never explained reads `unresolved` rather than inheriting a sibling's join, and
it is never derived from `inStock`: "the join did not explain this product" and
"this product is not on the shelf" are different facts, and conflating them would
reproduce the bug F1 exists to prevent.

**Nothing here publishes a pantry line's text.** §9.13.4's join table — the open
lines with their normalized cores — is the provenance *view*, and rendering it is
a later ticket's job. The state counters (`stockUnjoinedCount`,
`unjoinedLineCount`) are what this ticket owes, and they are counts.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Sequence
from typing import Annotated, Any, Final

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..config import ConfigurationError
from ..mapping.store import (
    DUPLICATE_SLOT_CONFLICT,
    CandidateRecord,
    CorruptMappingRow,
    IngredientMapping,
    IngredientMappingStore,
    MappingError,
    ResolveReport,
    UnknownIngredientSlot,
    UnknownPantryItem,
    candidate_payload,
)
from ..pantry.catalog import CatalogError, CatalogSnapshot
from ..pantry.stock import PantryStockIndex, StockJoin, StockJoinState
from ..recipes.ingredients import parse_ingredient_value
from ..recipes.matcher import MatchCandidate, resolve_ingredient
from ..recipes.reader import RecipeNote, RecipeSnapshot
from ..vault.pantry import PantryError
from .envelope import api_error
from .resources import (
    ResourceUnavailable,
    mapping_store,
    pantry_catalog,
    pantry_stock,
    recipe_index,
)

#: §9.16's two fail-closed 503s. Both mean "a source this app depends on is
#: unreadable", and both are **permanent until the user fixes the vault** — which
#: is why the UI must not present them as retry-soon. Named rather than inlined
#: so the handler's mapping and the tests that assert it cannot drift.
PANTRY_STOCK_UNREADABLE: Final = "pantry_stock_unreadable"
PANTRY_DB_UNREADABLE: Final = "pantry_db_unreadable"
RECIPE_NOT_FOUND: Final = "recipe_not_found"
RESOLVE_IN_FLIGHT: Final = "resolve_in_flight"

#: A bound on the `材料` index a client may name. It is a validation, not a
#: lookup: a negative index is a malformed request (422), and a `材料` list in the
#: real vault is 3–6 slots long, so 500 is a ceiling no recipe can reach and a
#: client cannot use the parameter to probe for a note.
MAX_INGREDIENT_INDEX: Final = 500

#: F8's parameter, as `Annotated` rather than a `Literal`.
#:
#: A `Literal[0, 1]` annotation looks like the better statement of the rule and
#: does not work: FastAPI hands the validator the *wire* string, and pydantic
#: matches a `Literal` of integers against `"1"` without coercing, so every
#: `?strict=1` would 422. `ge=0, le=1` is the same rule expressed as a range the
#: transport layer already coerces, and `?strict=2` is a 422 `invalid_request`
#: rather than a parameter that silently means "off" — which is the one thing a
#: headline-counting flag must not be.
Strict = Annotated[int, Query(ge=0, le=1)]

#: Where the in-flight resolve guard lives. On `app.state`, not at module scope:
#: an `asyncio.Lock` binds to the loop that first awaits it, and each
#: `TestClient` (and each uvicorn worker) has its own loop, so a module-level lock
#: would be a cross-loop hazard. Lazily created, so the route is still correct on
#: an app whose `lifespan` never ran.
RESOLVE_LOCK_STATE_KEY: Final = "recipes_resolve_lock"

#: The two methods D4's classifier treats as found without checking stock. Named
#: once, with a comment, because §9.13.2's ladder is the authority and this must
#: not grow a sixth name independently of it.
FOUND_WITHOUT_STOCK: Final[frozenset[str]] = frozenset({"manual", "staples"})


class ManualMappingRequest(BaseModel):
    """`PUT …/ingredients/{index}/mapping`'s whole body. `extra="forbid"`.

    An ignored field is a field that looks like it worked, so a body carrying a
    `path`-shaped key is refused rather than dropped — the same rule F4 set for
    the Cooking Log's `recipeNote`.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    pantry_item_id: int = Field(alias="pantryItemId", gt=0)


def build_recipes_router() -> APIRouter:
    """§9.16's five recipe routes, and only those.

    Takes no arguments on purpose, for the same reason `build_cook_log_router`
    does not (§4.2, §9.19): the `lifespan` owns the readers and their `finally`
    blocks. Everything is read from `app.state` via `app/api/resources.py`.
    """
    router = APIRouter()

    @router.get("/api/recipes")
    async def list_recipes(
        request: Request, strict: Strict = 0
    ) -> JSONResponse:
        """Every recipe, every Ingredient, every provenance field. F20: no page."""
        try:
            context = await _load(request, strict=strict == 1)
        except _SourceUnavailable as exc:
            return _fail(request, 503, exc.code)
        await _current(request, context.store, context.snapshot.notes)
        stale = await context.store.stale_rows()
        return JSONResponse(
            {
                "recipes": [await context.project(note) for note in context.snapshot.notes],
                "catalogRevision": context.catalog.catalog_revision,
                "stockRevision": context.stock.stock_revision,
                "strict": strict,
                "staleMappingCount": len(stale),
                "stockUnjoinedCount": context.join.unjoined_count,
                # F7's unconditional provenance, at the same level as the two
                # counters above: a note that vanished from the list without a
                # number attached is indistinguishable from one that was never
                # there, which is `RecipeSnapshot.skipped`'s own docstring.
                "skipped": context.snapshot.skipped,
            }
        )

    @router.get("/api/recipes/{note_name}")
    async def read_recipe(
        request: Request, note_name: str, strict: Strict = 0
    ) -> JSONResponse:
        """One Recipe, full. The same fields as the list, plus steps and history."""
        try:
            context = await _load(request, strict=strict == 1)
        except _SourceUnavailable as exc:
            return _fail(request, 503, exc.code)
        note = _find(context.snapshot, note_name)
        if note is None:
            return _fail(request, 404, RECIPE_NOT_FOUND)
        await _current(request, context.store, (note,))
        return JSONResponse({"recipe": await context.detail(note)})

    @router.post("/api/recipes/resolve")
    async def resolve(request: Request) -> JSONResponse:
        """Re-derive every non-`manual` row, and report the five numbers.

        409 while a pass is in flight. Two concurrent passes would run the same
        ladder over the same rows and race each other for the same write lock,
        and a user who presses "re-resolve" twice deserves one report rather than
        two racing ones.
        """
        try:
            store = mapping_store(request)
            notes = recipe_index(request).snapshot().notes
        except ResourceUnavailable as exc:
            return _fail(request, 503, str(exc))
        lock = _resolve_lock(request)
        if lock.locked():
            return _fail(request, 409, RESOLVE_IN_FLIGHT)
        async with lock:
            try:
                await store.ensure_rows(notes)
                report = await store.resolve_all()
            except CatalogError:
                return _fail(request, 503, PANTRY_DB_UNREADABLE)
            except (ConfigurationError, CorruptMappingRow, MappingError) as exc:
                return _fail(request, 503, _code_of(exc))
        return JSONResponse(_report_body(report))

    @router.put("/api/recipes/{note_name}/ingredients/{index}/mapping")
    async def put_mapping(
        request: Request,
        note_name: str,
        index: int,
        payload: ManualMappingRequest,
    ) -> JSONResponse:
        """Bind one `材料` slot to one Pantry Item by hand (F6).

        Goes through `set_manual`, which is a `DELETE` plus a fresh `INSERT`
        rather than an overwrite: the `BEFORE UPDATE` trigger makes an update of
        a manual row impossible, and the pair is auditable. `read_only` has
        already refused this request with 403 `read_only` inside
        `app/auth.py`, before the route was reached and before any existence
        check — so a read-only install cannot probe which recipes exist.
        """
        located = _locate(request, note_name, index)
        if isinstance(located, JSONResponse):
            return located
        store, note = located
        try:
            await store.ensure_rows((note,))
            mapping = await store.set_manual(note.note_path, index, payload.pantry_item_id)
        except sqlite3.IntegrityError:
            # F2's inverted index, hit by a hand fix rather than by the ladder:
            # the user is binding this slot to a Pantry Item another slot of the
            # *same recipe* already holds. `set_manual`'s transaction rolls back
            # whole and the previous row is intact, so nothing is corrupted — but
            # the request is a genuine conflict and not a malformed one, so it is
            # a 409 with F2's own reason string and not a 500. A hand fix is not a
            # way around "one recipe lists the same Pantry Item twice"; the user
            # who wants that outcome edits the note.
            return _fail(request, 409, DUPLICATE_SLOT_CONFLICT)
        except (
            UnknownPantryItem,
            UnknownIngredientSlot,
            CatalogError,
            CorruptMappingRow,
            MappingError,
            ConfigurationError,
        ) as exc:
            refusal = _map_write_refusal(exc)
            return _fail(request, refusal.status, refusal.code)
        return JSONResponse({"mapping": _mapping_body(mapping)})

    @router.delete("/api/recipes/{note_name}/ingredients/{index}/mapping")
    async def delete_mapping(request: Request, note_name: str, index: int) -> JSONResponse:
        """Clear a hand fix, leaving the slot `unresolved` (F6).

        `{"mapping": null}` rather than 404 when there was no hand fix to clear:
        "no manual row on this slot" is the state the caller asked about, and a
        404 for it would make an idempotent retry look like a failure. A slot
        with no row *at all* is a 404, because `ensure_rows` is how a slot comes
        into existence and nothing here creates one.
        """
        located = _locate(request, note_name, index)
        if isinstance(located, JSONResponse):
            return located
        store, note = located
        try:
            await store.ensure_rows((note,))
            await store.clear_manual(note.note_path, index)
        except (
            UnknownPantryItem,
            UnknownIngredientSlot,
            CatalogError,
            CorruptMappingRow,
            MappingError,
            ConfigurationError,
        ) as exc:
            refusal = _map_write_refusal(exc)
            return _fail(request, refusal.status, refusal.code)
        return JSONResponse({"mapping": None})

    return router


class _SourceUnavailable(Exception):
    """A projection this response needs could not be read. Always a 503.

    Wrapping `PantryError`, `CatalogError`, `ConfigurationError`, and
    `CorruptMappingRow` into one internal type is what keeps the fail-closed
    promise *checkable*: there is exactly one place that turns a source failure
    into an HTTP status, so a future `except PantryError` that returned a list
    would have to be added to `_load`, and this class makes the addition a
    deliberate edit rather than a `return []` three lines from the top.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _WriteRefusal(Exception):
    """A manual-mapping route's own refusal, with its status already decided."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def _locate(
    request: Request, note_name: str, index: int
) -> tuple[IngredientMappingStore, RecipeNote] | JSONResponse:
    """Resolve `(store, note)` for a mapping write, or the refusal to return.

    The order is load-bearing and identical for `PUT` and `DELETE`: the bound on
    the **index** first (422 — a malformed request, cheapest to refuse and the one
    that can never depend on vault state), then the existence of the **recipe**
    (404), and only then the action. A client can therefore tell "you asked for a
    slot that cannot exist" from "no such recipe" from "that slot has no
    mapping", and the `read_only` 403 has already happened in `app/auth.py`
    before any of it.
    """
    if index < 0 or index > MAX_INGREDIENT_INDEX:
        return _fail(request, 422, "invalid_ingredient_index")
    try:
        store = mapping_store(request)
        snapshot = recipe_index(request).snapshot()
    except (ResourceUnavailable, ConfigurationError, CorruptMappingRow) as exc:
        return _fail(request, 503, _code_of(exc))
    note = _find(snapshot, note_name)
    if note is None:
        return _fail(request, 404, RECIPE_NOT_FOUND)
    return store, note


class _Context:
    """One response's read view: the three projections and the join between them.

    Assembled once per request so the stock, the catalog, and the mapping table
    are each read **once**. A payload that read the stock twice could be
    internally inconsistent — a chip colour from one state of `Pantry.md` and a
    `stockRevision` from another — which is a wrong answer dressed as a right one.
    """

    def __init__(
        self,
        snapshot: RecipeSnapshot,
        catalog: CatalogSnapshot,
        stock: PantryStockIndex,
        join: StockJoin,
        store: IngredientMappingStore,
        *,
        strict: bool,
    ) -> None:
        self.snapshot = snapshot
        self.catalog = catalog
        self.stock = stock
        self.join = join
        self.store = store
        self.strict = strict
        self._in_stock: frozenset[int] = join.in_stock_ids
        #: `pantry_item_id` -> the join state that explained it. Built from the
        #: hits once, so a slot's answer is a dict lookup and not a scan of 46
        #: pantry lines for each of ~120 slots.
        self._join_state: dict[int, StockJoinState] = {}
        for hit in join.hits:
            for item_id in hit.item_ids:
                # Tier 1/2 (`joined`) outranks tier 3 (`override`) for a product
                # two lines both reached, so a slot is never reported as
                # `override` while a real catalog match for it exists.
                self._join_state.setdefault(item_id, hit.state)
                if hit.state == "joined":
                    self._join_state[item_id] = "joined"

    async def project(self, note: RecipeNote) -> dict[str, Any]:
        """One row of the list: the headline numbers and the whole chip row."""
        return await self._recipe(note, include_body=False)

    async def detail(self, note: RecipeNote) -> dict[str, Any]:
        """The detail body: the same, plus `steps` and the Cooking History."""
        return await self._recipe(note, include_body=True)

    async def _recipe(self, note: RecipeNote, *, include_body: bool) -> dict[str, Any]:
        slots = await self._slots(note)
        found, total = self._score(slots)
        body: dict[str, Any] = {
            "noteName": note.note_name,
            # Vault-relative, never absolute: §9.19's "no server-owned path on
            # any surface" applies to the payload exactly as it does to /health.
            "notePath": note.note_path,
            "found": found,
            "total": total,
            # §9.13.3 sorts on it client-side; a recipe that has never been
            # cooked has no `last_cooked` and publishes `null`, never a sentinel
            # date that would sort it into the middle of the list.
            "lastCooked": note.history.last_cooked.isoformat()
            if note.history.last_cooked
            else None,
            "ingredients": slots,
            "tools": list(note.tools),
        }
        if include_body:
            body["steps"] = note.steps
            body["history"] = _history(note)
        return body

    async def _slots(self, note: RecipeNote) -> list[dict[str, Any]]:
        """One slot list: `材料` first, then `调料`, each in its own slot order.

        Slot order is information — §9.13.2 forbids sorting it — and the two
        lists stay distinguishable within it because `isSeasoning` is exactly
        what F17's headline counts over.
        """
        rows = {row.ingredient_index: row for row in await self.store.rows_for(note.note_path)}
        slots: list[dict[str, Any]] = [
            self._slot(entry.index, entry.raw, rows.get(entry.index))
            for entry in note.ingredients
        ]
        slots.extend(
            _seasoning_slot(entry.index, entry.raw, self.catalog, self._in_stock, self._join_state)
            for entry in note.seasonings
        )
        return slots

    def _slot(
        self, index: int, raw: str, row: IngredientMapping | None
    ) -> dict[str, Any]:
        if row is not None:
            return _slot_body(
                index=index,
                raw=row.raw_value,
                parsed_name=row.parsed_name,
                parse_method=row.parse_method,
                match_method=row.match_method,
                match_tier=row.match_tier,
                pantry_item_id=row.pantry_item_id,
                confidence=row.confidence,
                candidates=row.candidates,
                in_stock=self._in_stock,
                join_state=self._join_state,
                is_seasoning=False,
            )
        # No row for a `材料` slot means `ensure_rows` has not covered this note
        # yet. It runs on every list and every detail read, so this is only
        # reachable for a recipe added between the two calls; the honest
        # "nothing is known about this slot" is better than a fabricated
        # `unresolved` row, which would look like a real resolution attempt and
        # carry its own audit trail.
        parsed = parse_ingredient_value(raw)
        return _slot_body(
            index=index,
            raw=raw,
            parsed_name=parsed.parsed_name,
            parse_method=parsed.parse_method,
            match_method="unresolved",
            match_tier=0,
            pantry_item_id=None,
            confidence=0.0,
            candidates=(),
            in_stock=self._in_stock,
            join_state=self._join_state,
            is_seasoning=False,
        )

    def _score(self, slots: Sequence[dict[str, Any]]) -> tuple[int, int]:
        """`(found, total)` — D4's `n/total`, and never a boolean.

        F17: the default view counts `材料` only. F16: under `严格模式` the `调料`
        slots join the count and a staples-satisfied Seasoning counts as found.
        The predicate below is `app/static/js/logic/chip-class.js`'s ladder, in
        its order, and it is duplicated here deliberately: the server publishes
        `found` / `total` because the headline is a *server* answer (§9.13.1) and
        the client re-derives `missing` from the same five fields to render the
        string. Two implementations of a five-branch rule is the price of that
        split. The alternative — publishing a server-rendered `chipClass` string
        — would be a *sixth* place to change the answer, and would have Python
        owning a CSS class name, so this file does not publish one.
        """
        scored = slots if self.strict else [slot for slot in slots if not slot["isSeasoning"]]
        found = sum(1 for slot in scored if not _is_missing(slot, self.strict))
        return found, len(scored)


def _is_missing(slot: dict[str, Any], strict: bool) -> bool:
    """`chip-class.js`'s five branches, in order, as one predicate."""
    if slot["matchMethod"] in FOUND_WITHOUT_STOCK:
        return False
    if slot["pantryItemId"] is not None:
        return False
    # A Seasoning outside strict mode is *ignored*, not missing — which is what
    # keeps the default view's missing list Materials-only (F17).
    return not (slot["isSeasoning"] and not strict)


def _join_state_for(
    join_state: dict[int, StockJoinState], pantry_item_id: int | None
) -> StockJoinState:
    """The Stock Join's state for one resolved id, or `unresolved` for no id.

    A slot with no Pantry Item has no product for the join to have explained,
    and `unresolved` is the honest reading of that. The two cases share a
    string but are not derived from one another: an unresolved *match* is a
    statement about the recipe->catalog join, and a `joined` stock state is a
    statement about the Pantry.md->catalog join.
    """
    if pantry_item_id is None:
        return "unresolved"
    return join_state.get(pantry_item_id, "unresolved")


def _slot_body(
    *,
    index: int,
    raw: str,
    parsed_name: str | None,
    parse_method: str,
    match_method: str,
    match_tier: int,
    pantry_item_id: int | None,
    confidence: float,
    candidates: Sequence[MatchCandidate | CandidateRecord],
    in_stock: frozenset[int],
    join_state: dict[int, StockJoinState],
    is_seasoning: bool,
) -> dict[str, Any]:
    """One slot's exact wire shape. F7: every key below is unconditional.

    `inStock` is the raw fact the classifier consumes, published rather than a
    rendered class: the client owns the CSS and the class name, and a server that
    owned a class name too would be a second vocabulary for one thing.

    `stockJoinState` is looked up by the resolved id only. An unresolved slot
    publishes `unresolved` because that is the honest join state for "no product",
    and the two happen to share a name — they are not derived from one another.
    """
    return {
        "index": index,
        "rawValue": raw,
        "parsedName": parsed_name,
        "parseMethod": parse_method,
        "matchMethod": match_method,
        "matchTier": match_tier,
        "pantryItemId": pantry_item_id,
        "confidence": confidence,
        # F7's audit, decoded through the store's own encoder: the provenance
        # view needs no second fetch and no second shape, and the keys are the
        # ones `_candidate_record` reads back.
        "candidatesJson": [candidate_payload(candidate) for candidate in candidates],
        "inStock": pantry_item_id is not None and pantry_item_id in in_stock,
        "stockJoinState": _join_state_for(join_state, pantry_item_id),
        "isSeasoning": is_seasoning,
    }


def _seasoning_slot(
    index: int,
    raw: str,
    catalog: CatalogSnapshot,
    in_stock: frozenset[int],
    join_state: dict[int, StockJoinState],
) -> dict[str, Any]:
    """A `调料` slot, resolved live against the ladder and never written.

    The one place this app calls `resolve_ingredient` outside
    `app/mapping/store.py`, and it is the *documented* exception rather than a
    second resolution path: the table has no row for a `调料` index
    (`schema.sql`), the matcher is pure, and the alternative — reporting every
    Seasoning as `unresolved` — is exactly what F16 forbids. `stock=None` because
    the ladder answers *which product*, never *do I have it*; the stock answer
    comes from the Stock Join below, per response.
    """
    parsed = parse_ingredient_value(raw)
    result = resolve_ingredient(parsed, None, catalog, source="调料")
    return _slot_body(
        index=index,
        raw=raw,
        parsed_name=parsed.parsed_name,
        parse_method=parsed.parse_method,
        match_method=result.match_method,
        match_tier=result.match_tier,
        pantry_item_id=result.pantry_item_id,
        confidence=result.confidence,
        candidates=result.candidates,
        in_stock=in_stock,
        join_state=join_state,
        is_seasoning=True,
    )


def _mapping_body(mapping: IngredientMapping) -> dict[str, Any]:
    """`PUT`'s one row, camel-cased. The same keys a slot carries, plus the
    timestamps — which a slot does not publish, because "when did this row last
    change" is a fact about a *write* and the chip row is not one."""
    return {
        "recipeNote": mapping.recipe_note,
        "ingredientIndex": mapping.ingredient_index,
        "rawValue": mapping.raw_value,
        "parsedName": mapping.parsed_name,
        "parseMethod": mapping.parse_method,
        "matchMethod": mapping.match_method,
        "matchTier": mapping.match_tier,
        "pantryItemId": mapping.pantry_item_id,
        "confidence": mapping.confidence,
        "candidatesJson": [candidate_payload(candidate) for candidate in mapping.candidates],
        "createdAt": mapping.created_at,
        "updatedAt": mapping.updated_at,
    }


def _history(note: RecipeNote) -> dict[str, Any]:
    """Cooking History: the nine tracker-owned fields and nothing else.

    The reader already refuses a hand-authored key in this set, so projecting the
    dataclass as-is cannot leak a non-tracker field. `auto_updated` stays the
    verbatim frontmatter *string* for the reason `RecipeCookingHistory` gives.
    """
    history = note.history
    return {
        "firstCooked": history.first_cooked.isoformat() if history.first_cooked else None,
        "lastCooked": history.last_cooked.isoformat() if history.last_cooked else None,
        "cookingCount": history.cooking_count,
        "cookingFrequency": history.cooking_frequency,
        "cookingYears": list(history.cooking_years),
        "recentActivity": history.recent_activity,
        "favoriteSeason": history.favorite_season,
        "cookingPatterns": list(history.cooking_patterns),
        "autoUpdated": history.auto_updated,
    }


def _find(snapshot: RecipeSnapshot, note_name: str) -> RecipeNote | None:
    """Exact basename match against the in-memory index. Never a path join."""
    for note in snapshot.notes:
        if note.note_name == note_name:
            return note
    return None


def _report_body(report: ResolveReport) -> dict[str, Any]:
    """§9.10.1's five numbers, camel-cased, plus the conflicts behind the count.

    `duplicateSlotConflicts` > 0 is a **data defect in a user-authored recipe
    note**, not an engine error, and §9.16 requires it to be visible *without*
    opening the 调试 view — so the entries travel with the count. Each names the
    recipe, the slot, and the Pantry Item that lost, so a UI can say "this
    Ingredient also matched X" instead of rendering an unexplained miss.
    """
    return {
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
    }


async def _load(request: Request, *, strict: bool) -> _Context:
    """Read the four projections once, in the order the 503 branch needs.

    **Stock first, deliberately.** `PantryStockIndex` raises `PantryError` for a
    missing, unreadable, or unparseable `Pantry.md` and offers no fallback, so a
    catalog snapshot is built and a mapping pass is run only once the answer is
    known to be servable at all. This function never catches `PantryError` and
    never substitutes an empty join; every failure is re-raised as
    `_SourceUnavailable` so the route's mapping to 503 is the single place it
    happens.
    """
    try:
        stock = pantry_stock(request)
        join = stock.join  # raises PantryError — F1's fail-closed branch
        catalog = pantry_catalog(request).snapshot()  # raises CatalogError
        snapshot = recipe_index(request).snapshot()  # raises ConfigurationError
        store = mapping_store(request)
    except PantryError as exc:
        raise _SourceUnavailable(PANTRY_STOCK_UNREADABLE) from exc
    except CatalogError as exc:
        raise _SourceUnavailable(PANTRY_DB_UNREADABLE) from exc
    except ResourceUnavailable as exc:
        raise _SourceUnavailable(str(exc)) from exc
    except (ConfigurationError, CorruptMappingRow) as exc:
        raise _SourceUnavailable(_code_of(exc)) from exc
    return _Context(snapshot, catalog, stock, join, store, strict=strict)


def _code_of(exc: Exception) -> str:
    """A named 503 code for a source failure that is not stock or catalog.

    `ConfigurationError` and the `MappingError` family each carry a bare
    `snake_case` code as their message, and that code is more useful than any one
    bucket name — `missing_recipes_root` and `corrupt_ingredient_mapping_row` say
    what to fix where `resource_unreadable` would not. The `isidentifier` guard is
    the safety property: anything that is not a bare lowercase identifier cannot
    become a published code, so no exception message that happens to embed a path
    can leak one through this function.
    """
    text = str(exc)
    return text if text.islower() and text.isidentifier() else "resource_unreadable"


def _fail(request: Request, status: int, code: str) -> JSONResponse:
    """A two-key envelope for every refusal this router makes.

    §9.16 widens the envelope for exactly two codes, both belonging to the
    Cooking Log. Nothing here may add a `message` or a path: a 503 is permanent
    until the user fixes a vault file, and publishing the note's path would put
    server-owned layout on a surface that needs no reason to carry it.
    """
    return api_error(request, status, code)


def _resolve_lock(request: Request) -> asyncio.Lock:
    """The per-app in-flight resolve guard, created on first use."""
    lock = getattr(request.app.state, RESOLVE_LOCK_STATE_KEY, None)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        setattr(request.app.state, RESOLVE_LOCK_STATE_KEY, lock)
    return lock


async def _current(
    request: Request, store: IngredientMappingStore, notes: Sequence[RecipeNote]
) -> None:
    """Make the materialized mapping current, then return.

    **`ensure_rows` alone is not enough, and the difference is the headline.**
    `ensure_rows` materializes a *new* slot as `unresolved` with a null
    `pantry_item_id` — the audit anchor, deliberately, because a slot must be
    auditable before anything has tried to resolve it. So a mapping table that
    has never been passed over answers `0/6` for every recipe in the vault, and
    F17's rule makes that a *published, confident* wrong answer rather than a
    blank one: the whole point of `n/total` is that it means something.

    So the read path runs the ladder when §9.11.2's `catalog_changed` says the
    table is stale — `True` before any full pass, and `True` again after the
    producer re-imports and renumbers an `items.id`. After the first pass the
    check is a string comparison against a revision the TTL-cached catalog
    snapshot already produced, so the steady-state cost per request is a dict
    lookup.

    The boot-time half of the same signal is the `lifespan`'s; repeating it here
    is what makes a boot whose resolve failed (or an app created without one)
    self-repairing on the next read rather than serving a wrong headline
    forever. A pass already in flight is left to finish: the lock is taken
    non-blocking, and the response it is producing is the up-to-date one.
    """
    await store.ensure_rows(notes)
    if not store.catalog_changed:
        return
    lock = _resolve_lock(request)
    if lock.locked():
        return
    async with lock:
        if store.catalog_changed:
            try:
                await store.resolve_all()
            except (CatalogError, ConfigurationError, CorruptMappingRow, MappingError):
                # Not fatal. The 503 branch for a source that cannot be read is
                # `_load`'s job, and a mapping pass that fails leaves the previous
                # state intact by construction (one `BEGIN IMMEDIATE`, rolled
                # back whole). Answering with a stale mapping is the honest
                # state; refusing the request would not be.
                return


def _map_write_refusal(exc: Exception) -> _WriteRefusal:
    """One typed refusal per exception a manual-mapping write can raise.

    `UnknownPantryItem` is a 422 and not a 404: the request is well formed, the id
    is simply not a member of the target set, and F6's immutability means an
    accepted bad id could only ever be repaired by another delete-and-insert.
    `UnknownIngredientSlot` is a 404 because the slot is the resource. Everything
    else is a source this app depends on being unreadable, which is a 503.
    """
    if isinstance(exc, UnknownPantryItem):
        return _WriteRefusal(422, UnknownPantryItem.code)
    if isinstance(exc, UnknownIngredientSlot):
        return _WriteRefusal(404, UnknownIngredientSlot.code)
    if isinstance(exc, CatalogError):
        return _WriteRefusal(503, PANTRY_DB_UNREADABLE)
    if isinstance(exc, (ConfigurationError, CorruptMappingRow, MappingError)):
        return _WriteRefusal(503, _code_of(exc))
    raise exc


__all__ = [
    "MAX_INGREDIENT_INDEX",
    "PANTRY_DB_UNREADABLE",
    "PANTRY_STOCK_UNREADABLE",
    "RECIPE_NOT_FOUND",
    "RESOLVE_IN_FLIGHT",
    "build_recipes_router",
]

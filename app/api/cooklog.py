"""`POST /api/cook-logs` and `GET /api/cook-logs?date=` — the Cooking Log routes.

**The browser supplies a recipe and a date, never a path** (D2, Server-Owned
Root). `{"recipeNote": "盐焗鸡", "date": "2026-09-27"}` is the whole request
shape; `日记/2026/2026-09-27.md` is derived server-side from
`DAILY_NOTES_ROOT`. The request model sets `extra="forbid"` so a body that
carries a path-shaped field is **refused** rather than silently ignored — an
ignored field is a field that looks like it worked.

**The error envelope extension lives here, and it is additive** (F4, §9.15).
Every response below emits exactly the scaffold's `{"requestId", "code"}` unless
it is one of the two codes F4 names, which add `message` / `date` /
`relativePath` / `retryable` (and, for the 409, `currentRevision`). Key order
and the two-key baseline are produced by `cook_log_error`, which is now a
**delegation** to `app/api/envelope.py:api_error` — the same function
`app/main.py`'s `_api_error` delegates to, which is how §9.19's fold happens
without the circular import it would otherwise be.
`tests/api/test_cooklog_api.py` asserts the two-key case is key-for-key identical
to that function, so the fold changed nothing observable. `relativePath` is
**vault-relative**, never absolute, so the Server-Owned Root invariant and
`/health` hold.

**Nothing here creates a daily note** (F4). A missing note is a 404 that names
the date and the expected path and is retryable once the user creates it in
Obsidian, and a note that appeared between the app's two reads is a 409 — not a
404, because reporting a lost race as "not found" sends the user to create a note
that already exists.

**F5: this route is online-only and is not on the offline outbox.** The button
is disabled offline and says why. There is no enqueue call here, and adding one
"just in case" would re-open the 409-resolve-panel problem §9.18.1 exists to
prevent.
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..cooklog.writer import CookingLogWriter, CookLogError, CookLogResult
from .envelope import api_error

#: Same bound, same trim, same non-empty rule as `pwa-deals` (§9.16).
MAX_CLIENT_ID_LENGTH: Final = 200

#: The state key `app/main.py`'s `lifespan` is expected to publish. Kept as a
#: constant because the wiring ticket and this one have to agree on it, and a
#: bare string literal in two files is how they would not.
COOK_LOG_WRITER_STATE_KEY: Final = "cook_log_writer"

#: The optional keys F4 adds. Named once, asserted once, and defaulting to
#: absent — a code that publishes none of them emits exactly `{"requestId",
#: "code"}` and nothing else.
OPTIONAL_ERROR_KEYS: Final[tuple[str, ...]] = (
    "message",
    "date",
    "relativePath",
    "retryable",
    "currentRevision",
)

#: The **only** two codes that may publish `message` / `date` / `relativePath` /
#: `retryable`. §9.16 is explicit: "**Two** error codes add optional fields …
#: every other code still emits exactly `{"requestId", "code"}`", so the gate
#: lives here as a named set rather than as "every CookLogError carries a
#: message". A refusal like `invalid_recipe_note` has a perfectly good string
#: representation and still must not widen the envelope.
PRESENCE_CODES: Final[frozenset[str]] = frozenset(
    {"daily_note_missing", "daily_note_created_concurrently"}
)

#: The codes that may publish `currentRevision`, so the client can re-offer
#: against fresh bytes: the two presence conflicts, and the stale-revision 409
#: whose whole contract is "refetch and re-offer".
CONFLICT_CODES: Final[frozenset[str]] = PRESENCE_CODES | {"daily_note_changed"}

_MISSING_WRITER: Final = "cook_log_unavailable"


class CookLogRequest(BaseModel):
    """The whole request body. `date` and `recipeNote` only — no path, no slot."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    recipe_note: str = Field(alias="recipeNote")
    log_date: str = Field(alias="date")
    base_revision: str | None = Field(default=None, alias="baseRevision")


def cook_log_error(
    request: Request,
    status_code: int,
    code: str,
    *,
    message: str | None = None,
    log_date: str | None = None,
    relative_path: str | None = None,
    retryable: bool | None = None,
    current_revision: str | None = None,
) -> JSONResponse:
    """The shared envelope, delegated.

    The body used to live here. It does not any more: #15 folds it into
    `app/main.py`'s `_api_error` (§9.19), and the only way to do that without an
    `ImportError` is to put the body in a module both sides can import — see
    `app/api/envelope.py` for the full argument. This signature is unchanged, so
    every call site below and the return annotation stay as they were, and
    `tests/api/test_cooklog_api.py::test_the_two_key_envelope_is_key_for_key_the_scaffolds`
    still compares byte-for-byte against `_api_error`.
    """
    return api_error(
        request,
        status_code,
        code,
        message=message,
        log_date=log_date,
        relative_path=relative_path,
        retryable=retryable,
        current_revision=current_revision,
    )


def cook_log_failure(request: Request, error: CookLogError) -> JSONResponse:
    """One typed refusal to one envelope; the writer owns the codes and the copy.

    The optional fields are gated per code, not per error: a `CookLogError`
    always *has* a message, a date, and a path, and publishing them for the
    twenty other refusals this route can make would turn F4's narrow extension
    into a wide one and change the shape every existing consumer already parses.
    """
    if error.code in PRESENCE_CODES:
        return cook_log_error(
            request,
            error.status_code,
            error.code,
            message=error.user_message,
            log_date=error.log_date,
            relative_path=error.relative_path,
            retryable=error.retryable,
            current_revision=(
                error.current_revision if error.code in CONFLICT_CODES else None
            ),
        )
    if error.code in CONFLICT_CODES:
        return cook_log_error(
            request,
            error.status_code,
            error.code,
            current_revision=error.current_revision,
        )
    return cook_log_error(request, error.status_code, error.code)


def normalize_client_id(raw: str | None) -> str | None:
    """Trim, require non-empty, and bound `X-Client-Id` exactly as `pwa-deals` does.

    The value is validated and then **ignored**: cook logs do not travel through
    the outbox (§9.18.1), so there is no replay to deduplicate. It is accepted
    only as defence in depth against a user-driven double submit, and the real
    guarantee is the note plus `cook_log_receipts`.
    """
    if raw is None:
        return None
    normalized = raw.strip()
    if not normalized or len(normalized) > MAX_CLIENT_ID_LENGTH:
        return ""
    return normalized


def _writer(request: Request) -> CookingLogWriter | None:
    return getattr(request.app.state, COOK_LOG_WRITER_STATE_KEY, None)


def build_cook_log_router() -> APIRouter:
    """`POST /api/cook-logs` and `GET /api/cook-logs`.

    Takes no arguments on purpose (§4.2, §9.19): the `lifespan` owns the
    `AtomicNoteStore`, the receipts connection factory, and their `finally`
    blocks, and a router that constructed them would be a second, unclosed
    source of file descriptors. The writer is read from `app.state` under
    `COOK_LOG_WRITER_STATE_KEY`.
    """
    router = APIRouter()

    @router.post("/api/cook-logs")
    async def log_cook(payload: CookLogRequest, request: Request) -> JSONResponse:
        writer = _writer(request)
        if writer is None:
            return cook_log_error(request, 503, _MISSING_WRITER)
        client_id = normalize_client_id(request.headers.get("X-Client-Id"))
        if client_id is not None and not client_id:
            # 400, matching `pwa-deals`: a present-but-unusable idempotency key
            # is a malformed request, not a 422 semantic failure.
            return cook_log_error(request, 400, "client_id_invalid")
        try:
            result = await writer.append(
                payload.recipe_note,
                payload.log_date,
                base_revision=payload.base_revision,
                client_id=client_id,
            )
        except CookLogError as error:
            return cook_log_failure(request, error)
        return JSONResponse(
            _logged_body(result),
            status_code=201 if result.status == "logged" else 200,
        )

    @router.get("/api/cook-logs")
    async def read_cook_log(
        request: Request, log_date: str = Query(default="", alias="date")
    ) -> JSONResponse:
        writer = _writer(request)
        if writer is None:
            return cook_log_error(request, 503, _MISSING_WRITER)
        try:
            relative, revision, entries = await writer.read_back(log_date)
        except CookLogError as error:
            return cook_log_failure(request, error)
        return JSONResponse(
            {
                "date": log_date,
                "relativePath": relative,
                "noteRevision": revision,
                "entries": [
                    {
                        "recipeNote": entry.recipe_note,
                        "writtenAt": entry.written_at,
                        "trackerSynced": entry.tracker_synced,
                    }
                    for entry in entries
                ],
            }
        )

    return router


def _logged_body(result: CookLogResult) -> dict[str, Any]:
    """Exactly three keys, on both the 201 and the 200 duplicate.

    The response does not widen to carry `recipeNote` or `writtenAt`: the client
    already holds both (it just sent the note name, and it re-reads the date's
    entries for the timestamps), and every added field is a field a second
    surface has to agree on. The audit row is readable through
    `GET /api/cook-logs?date=`.
    """
    return {
        "status": result.status,
        "relativePath": result.relative_path,
        "noteRevision": result.note_revision,
    }

"""The one API error envelope, in a module both `app/main.py` and the routers
can import.

**Why this module exists at all.** §9.19 says `_api_error` gains §9.15's
additive optional detail so the Cooking Log's envelope extension and the
scaffold's are the *same* function rather than two that agree today. The obvious
implementation — move `cook_log_error`'s body into `app/main.py:_api_error` and
have the router call it — does not work, and it does not work *structurally*:

```
app/main.py  --include_router-->  app/api/cooklog.py  --import-->  app/main.py
```

Python resolves that cycle by executing `app.main` first, so the router's
`from ..main import _api_error` runs while `app.main` is still half-initialised
and raises `ImportError: cannot import name '_api_error' from partially
initialized module 'app.main'`. The cycle is not removable by reordering the
imports either, because `include_router` is what needs the name and the import is
what needs the router to exist. (This was proven experimentally, not assumed —
see the report on #18.)

So the shared body lives **here**: a module that imports nothing from `app` and
is therefore reachable from both sides. `app.main._api_error` and
`app.api.cooklog.cook_log_error` are both thin, additive delegations to
`api_error` below, and both keep their own return annotation.

**What this module owns, and what it deliberately does not.**

- It owns the *shape*: `requestId` first, `code` second, optional keys appended
  in `OPTIONAL_ORDER` order, omitted rather than sent as `null`.
- It does **not** own *which* code may publish which optional key. That is F4
  policy and it stays in `app/api/cooklog.py` as `PRESENCE_CODES` /
  `CONFLICT_CODES`, because the rule is a statement about the Cooking Log's two
  presence conflicts, not a property of the envelope.

**`OPTIONAL_ORDER` is load-bearing, not cosmetic.** `tests/api/test_cooklog_api.py`
asserts the 404 body is *key-for-key* and *in order* `{requestId, code, message,
date, relativePath, retryable}` — a Python dict preserves insertion order and
`JSONResponse` serialises it, so the order of the assignments below is the order
on the wire. Appending one key out of order would fail a test that exists
precisely to catch a silent reordering.

**`relativePath` is vault-relative, never absolute.** Nothing in this module
interpolates a filesystem path; the caller passes one it already built from
`Settings.*_relative`. That is what keeps §9.19's "`/health` and the envelope both
leak no absolute path" true of the folded function as well as of the original.
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import Request
from fastapi.responses import JSONResponse

#: The optional keys F4 adds, in the exact order they are appended. The two
#: names above are the public contract; this tuple is the implementation of it,
#: and `OPTIONAL_ERROR_KEYS` in `app/api/cooklog.py` is the same set in
#: membership (that one is a tuple too, and the order agrees, so a reader can
#: diff the two and find nothing).
OPTIONAL_ORDER: Final[tuple[str, ...]] = (
    "message",
    "date",
    "relativePath",
    "retryable",
    "currentRevision",
)


def api_error(
    request: Request,
    status: int,
    code: str,
    *,
    message: str | None = None,
    log_date: str | None = None,
    relative_path: str | None = None,
    retryable: bool | None = None,
    current_revision: str | None = None,
) -> JSONResponse:
    """`{"requestId", "code"}` plus whichever optional fields were supplied.

    Every optional field defaults to **absent**, and absent means *omitted* —
    never `null` — so a caller that passes nothing gets byte-identical output to
    the pre-F4 two-key envelope. That is the whole of "additive": a pre-existing
    code cannot acquire a key by way of a refactor that touched this function.

    `request_id` is read tolerantly. `app/auth.py`'s middleware stamps
    `request.state.request_id` before any handler or exception handler can run,
    so the tolerant read is unreachable in the real app; it is here so a unit
    test can call this function with a hand-built `Request` without having to
    reproduce the middleware's ordering first.
    """
    payload: dict[str, Any] = {
        "requestId": getattr(request.state, "request_id", None),
        "code": code,
    }
    if message is not None:
        payload["message"] = message
    if log_date is not None:
        payload["date"] = log_date
    if relative_path is not None:
        payload["relativePath"] = relative_path
    if retryable is not None:
        payload["retryable"] = retryable
    if current_revision is not None:
        payload["currentRevision"] = current_revision
    return JSONResponse(payload, status_code=status)


__all__ = ["OPTIONAL_ORDER", "api_error"]

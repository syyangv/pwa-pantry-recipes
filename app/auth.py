"""Trusted-proxy identity, Origin/CSRF, host, content-type, and security headers.

Ported from `pwa-obsidian-daily/app/auth.py` and trimmed to this app's route
set. All guards run in a single http middleware that is registered *after* the
cache-policy middleware, so security runs outermost: a hostile ``Host`` header
is rejected before any middleware parses ``request.url`` (Starlette builds the
URL from the Host header), and every rejection still carries the standard
``{requestId, code}`` envelope with ``X-Request-ID`` and
``Cache-Control: no-store``.

Identity rules
    * Trusted-header mode (``TRUST_TAILSCALE_HEADERS=true``): the request
      identity is the exact normalized ``Tailscale-User-Login`` header value,
      compared byte-for-byte (after OWS trimming) with
      ``TAILSCALE_OWNER_LOGIN``.  Missing header => 401 ``identity_missing``;
      multiple, empty-after-trim, oversized (> 254 chars), or control-character
      values => 401 ``identity_invalid``; well-formed but mismatched (including
      case differences) => 401 ``identity_denied``.
    * Development mode: no client-supplied identity header is accepted. Any
      ``Tailscale-User-Login`` or ``Tailscale-User-Name`` header (regardless of
      value) is rejected with 401 ``identity_spoof``.  The effective identity
      is the configured ``DEV_IDENTITY`` (falling back to
      ``TAILSCALE_OWNER_LOGIN``).

Read-only mode (``OBSIDIAN_READ_ONLY=true``)
    Every ``/api/*`` mutation (POST/PUT/PATCH/DELETE) is rejected with 403
    ``read_only`` **after** identity verification and **before** every other
    mutation check.  Guard order for a mutation is therefore:
    host (400) -> identity (401) -> read_only (403) -> body size (413) ->
    origin (403) -> content-type (415) -> CSRF (403).  Reads, health, static,
    OPTIONS, and ``/api/session`` still work, so an operator can leave the PWA
    projected read-only while still reading it.

    F18 (locked): there is **no** write allowlist. ``pwa-obsidian-daily`` exempts
    the Open Items paths when ``OPEN_ITEMS_WRITES_ENABLED=true``; that carve-out
    is deliberately not ported, because it is an exception list that has to be
    remembered on every new route. F4 removed the note-creation service, so this
    gate is the only thing between a mutation and the vault.

Origin / CSRF / content-type / host
    * All non-GET/HEAD/OPTIONS ``/api/*`` requests are mutations: they require
      an exact ``Origin`` equal to ``PUBLIC_ORIGIN`` (403 ``origin_not_allowed``),
      a server-issued ``X-CSRF-Token`` (403 ``csrf_required`` / ``csrf_invalid``),
      and, when a body is present, a JSON media type
      (``application/json`` or ``application/*+json``; 415
      ``unsupported_media_type``).  The body-free create mutation needs no
      Content-Type unless one is supplied.
    * ``OPTIONS`` preflights from a foreign origin are rejected with 403; no
      response ever carries ``Access-Control-Allow-*`` headers.
    * GET/HEAD/OPTIONS/static are exempt from Origin and content-type but are
      still identity-gated.
    * ``Host`` must be the public-origin host (with matching port) or a
      loopback/localhost host (any port); anything else is 400 ``invalid_host``.
    * ``/api/*`` request bodies are capped at 1 MiB (413 ``request_too_large``);
      chunked transfer encoding is rejected for the same reason. This app has no
      upload route, so there is no multipart / chunked exception.

CSRF tokens
    * Cryptographically random (``secrets.token_urlsafe(32)``), 43 characters,
      bounded at 128 characters on input.  Issued by ``GET /api/session``,
      valid for 3600 seconds, rotated continuously (a small rolling window is
      kept so concurrent tabs do not race), and cleared on process restart.

Security headers are applied to every response; the exact set is
``SECURITY_HEADERS``. The ``pwa-obsidian-daily`` carve-out that relaxes
``frame-ancestors`` for a sibling PWA's embedded dashboard views is **not**
ported: this app embeds nothing, so ``frame-ancestors 'none'`` stands.
"""

from __future__ import annotations

import hmac
import ipaddress
import secrets
import time
import uuid
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint

from .config import Settings

MAX_IDENTITY_CHARS = 254
MAX_CSRF_TOKEN_CHARS = 128
MAX_REQUEST_BYTES = 1_048_576  # 1 MiB
CSRF_TTL_SECONDS = 3600.0
CSRF_MAX_TOKENS = 16
CSRF_TOKEN_LENGTH = 43  # secrets.token_urlsafe(32)

MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
IDENTITY_HEADERS = ("tailscale-user-login", "tailscale-user-name")

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
        "manifest-src 'self'; worker-src 'self'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'; object-src 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def normalize_identity(value: str) -> str | None:
    """Trim OWS and validate the Tailscale identity header value."""
    cleaned = value.strip(" \t")
    if not cleaned or len(cleaned) > MAX_IDENTITY_CHARS:
        return None
    # One rule: the value must be printable ASCII. Control characters are
    # refused, and so is anything above U+007E — Starlette decodes headers as
    # latin-1, so a non-ASCII byte arrives as a high code point, and
    # `hmac.compare_digest` raises `TypeError` on a `str` that has one. Both
    # fields are ASCII by construction (a Tailscale login is an email or a
    # username, a CSRF token is `secrets.token_urlsafe(32)`), so rejecting
    # non-ASCII here costs nothing and keeps a 4xx where a 500 belongs. The
    # bound is `> 126`, not `> 127`, so U+007F stays rejected as it was.
    if any(ord(character) < 32 or ord(character) > 126 for character in cleaned):
        return None
    return cleaned


def is_loopback_hostname(hostname: str) -> bool:
    """``localhost`` or any loopback IP address literal."""
    lowered = hostname.lower()
    if lowered == "localhost":
        return True
    try:
        return ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        return False


class IdentityPolicy:
    """Enforce the owner allowlist for the configured trust mode."""

    def __init__(self, settings: Settings) -> None:
        self._trusted = settings.trust_tailscale_headers
        if self._trusted:
            self._owner = settings.tailscale_owner_login
        else:
            self._owner = settings.dev_identity

    def check(self, request: Request) -> tuple[bool, int, str]:
        """Return (allowed, status, code); ``allowed`` implies status/code 0."""
        if self._trusted:
            values = request.headers.getlist("tailscale-user-login")
            if len(values) > 1:
                return False, 401, "identity_invalid"
            if not values:
                return False, 401, "identity_missing"
            identity = normalize_identity(values[0])
            if identity is None:
                return False, 401, "identity_invalid"
            # Bytes, not `str`: `hmac.compare_digest` raises `TypeError` on a
            # `str` with any code point above U+007F — on *either* side — so the
            # comparison itself has to be incapable of raising, not just its
            # input filtered. Otherwise the next field compared here re-opens
            # the same 500, and this one still can: `self._owner` is operator
            # config that `normalize_identity` never sees, so a non-ASCII
            # `TAILSCALE_OWNER_LOGIN` reaches this line. Encoded, that is a
            # plain mismatch and a 401 `identity_denied`.
            if not hmac.compare_digest(identity.encode(), self._owner.encode()):
                return False, 401, "identity_denied"
            return True, 0, ""
        for name in IDENTITY_HEADERS:
            if request.headers.getlist(name):
                return False, 401, "identity_spoof"
        return True, 0, ""


class CsrfTokenStore:
    """Server-issued, rotating, bounded CSRF tokens with a TTL."""

    def __init__(
        self, ttl_seconds: float = CSRF_TTL_SECONDS, max_tokens: int = CSRF_MAX_TOKENS
    ) -> None:
        self._ttl = ttl_seconds
        self._max_tokens = max_tokens
        self._tokens: dict[str, float] = {}

    def issue(self) -> str:
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        # Prune *after* inserting, so the bound is an invariant on the way out
        # of issue(): `_prune` trims with `while len(self._tokens) >
        # self._max_tokens`, so pruning first runs one insert behind and the
        # store settles at `_max_tokens + 1` live tokens and stays there.
        # Pruning after the insert evicts the *oldest* entry, never the one just
        # added: the new token carries the largest `created`, and `min` over a
        # timestamp tie falls to the earliest-inserted key.
        self._tokens[token] = now
        self._prune(now)
        return token

    def verify(self, token: str) -> bool:
        if not token or len(token) > MAX_CSRF_TOKEN_CHARS:
            return False
        # Same rule as `normalize_identity`, and for the same reason: a
        # non-ASCII header value would make `hmac.compare_digest` raise, and the
        # token alphabet is `secrets.token_urlsafe(32)`, i.e. ASCII only.
        if not token.isascii():
            return False
        now = time.monotonic()
        self._prune(now)
        for candidate, created in self._tokens.items():
            # Bytes, not `str`: see `IdentityPolicy.check`.
            if (
                hmac.compare_digest(token.encode(), candidate.encode())
                and now - created <= self._ttl
            ):
                return True
        return False

    def _prune(self, now: float) -> None:
        for token, created in list(self._tokens.items()):
            if now - created > self._ttl:
                del self._tokens[token]
        while len(self._tokens) > self._max_tokens:
            oldest = min(self._tokens.items(), key=lambda item: item[1])[0]
            del self._tokens[oldest]


def _ows_trim(value: str) -> str:
    return value.strip(" \t")


def check_host(request: Request, settings: Settings) -> bool:
    """Host header must be the public origin host or a loopback host."""
    values = request.headers.getlist("host")
    if len(values) != 1:
        return False
    raw = _ows_trim(values[0])
    if not raw or len(raw) > 300:
        return False
    parsed = _parse_host(raw)
    if parsed is None:
        return False
    hostname, port = parsed
    if is_loopback_hostname(hostname):
        return True
    origin = urlsplit(settings.public_origin)
    origin_host = origin.hostname or ""
    if origin_host.lower() != hostname.lower():
        return False
    if origin.port is None:
        return port is None
    return port == origin.port


def _parse_host(raw: str) -> tuple[str, int | None] | None:
    try:
        parsed = urlsplit("//" + raw)
    except ValueError:
        return None
    hostname = parsed.hostname
    if hostname is None or parsed.username is not None or parsed.password is not None:
        return None
    if parsed.path or parsed.query or parsed.fragment or "%" in hostname:
        return None
    if len(hostname) > 253:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is not None and not 1 <= port <= 65_535:
        return None
    return hostname, port


def check_origin(request: Request, settings: Settings) -> bool:
    """Exact (OWS-trimmed) Origin equality with PUBLIC_ORIGIN."""
    values = request.headers.getlist("origin")
    if len(values) != 1:
        return False
    return _ows_trim(values[0]) == settings.public_origin


def _content_length(request: Request) -> int | None:
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def check_body_size(
    request: Request, *, limit: int = MAX_REQUEST_BYTES
) -> tuple[bool, int, str]:
    """Reject chunked API bodies and bodies larger than ``limit``.

    There is no multipart exception: this app has no upload route, so
    chunked transfer encoding is refused outright rather than bounded later by
    a route-level check that may not exist yet.
    """
    if request.headers.get("transfer-encoding") is not None:
        return False, 413, "request_too_large"
    length = _content_length(request)
    if length is not None and length > limit:
        return False, 413, "request_too_large"
    return True, 0, ""


def check_mutation_content_type(request: Request) -> tuple[bool, int, str]:
    """JSON media type required for mutation bodies. Absent content-type is
    allowed only for body-free mutations (a create with no body)."""
    values = request.headers.getlist("content-type")
    length = _content_length(request)
    has_body = length is not None and length > 0
    if not values:
        if has_body:
            return False, 415, "unsupported_media_type"
        return True, 0, ""
    if len(values) > 1:
        return False, 415, "unsupported_media_type"
    media = values[0].split(";", 1)[0].strip().lower()
    if media == "application/json" or (
        media.startswith("application/") and media.endswith("+json")
    ):
        return True, 0, ""
    return False, 415, "unsupported_media_type"


def check_csrf(request: Request, store: CsrfTokenStore) -> tuple[bool, int, str]:
    values = request.headers.getlist("x-csrf-token")
    if len(values) > 1:
        return False, 403, "csrf_invalid"
    if not values:
        return False, 403, "csrf_required"
    token = values[0]
    if len(token) > MAX_CSRF_TOKEN_CHARS or not token:
        return False, 403, "csrf_invalid"
    if not store.verify(token):
        return False, 403, "csrf_invalid"
    return True, 0, ""


def install_security_middleware(application: FastAPI, settings: Settings) -> None:
    """Register the security http middleware and attach shared state.

    Must be called *after* the cache-policy middleware is registered so that
    security runs outermost: a malformed ``Host`` header must be rejected
    before any other middleware parses ``request.url`` (Starlette builds the
    URL from the Host header), and every rejection must still carry the
    standard request-ID envelope.
    """
    policy = IdentityPolicy(settings)
    csrf = CsrfTokenStore()
    application.state.csrf = csrf
    application.state.identity_policy = policy

    @application.middleware("http")
    async def security(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if getattr(request.state, "request_id", None) is None:
            request.state.request_id = str(uuid.uuid4())
        response = _guard(request, settings, policy, csrf)
        rejected = response is not None
        if response is None:
            response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        response.headers.setdefault("X-Request-ID", request.state.request_id)
        # The raw scope path, never request.url: a hostile Host header must not
        # be able to break the rejection path.
        path = request.scope.get("path", "")
        if rejected or path == "/health" or path.startswith("/api/"):
            # A rejection never reaches cache_policy, so no-store has to be set
            # here or a refused request would be cacheable. setdefault, not
            # assignment: /api/version's stronger vendored headers must survive.
            response.headers.setdefault("Cache-Control", "no-store")
        return response


def _guard(
    request: Request,
    settings: Settings,
    policy: IdentityPolicy,
    csrf: CsrfTokenStore,
) -> Response | None:
    if not check_host(request, settings):
        return _reject(request, 400, "invalid_host")
    allowed, status, code = policy.check(request)
    if not allowed:
        return _reject(request, status, code)
    # Use the raw scope path: parsing request.url with a hostile Host header
    # can raise before the host check would be reached.
    path = request.scope.get("path", "")
    if not path.startswith("/api/"):
        return None
    method = request.method.upper()
    if method in MUTATION_METHODS and settings.read_only:
        # Read-only mode is a policy gate placed immediately after identity
        # and before every other mutation check (body size, Origin,
        # content-type, CSRF), so a read-only server rejects every mutation
        # with the same stable 403 envelope and never inspects bodies. It
        # cannot run before identity: a blocked actor must not be able to
        # distinguish the read-only flag from the identity checks. F4 removed
        # the creation service, so this gate is also the only thing standing
        # between a mutation and the vault.
        return _reject(request, 403, "read_only")
    allowed, status, code = check_body_size(request)
    if not allowed:
        return _reject(request, status, code)
    if method == "OPTIONS":
        if "origin" in request.headers and not check_origin(request, settings):
            return _reject(request, 403, "origin_not_allowed")
        return None
    if method in MUTATION_METHODS:
        if not check_origin(request, settings):
            return _reject(request, 403, "origin_not_allowed")
        allowed, status, code = check_mutation_content_type(request)
        if not allowed:
            return _reject(request, status, code)
        allowed, status, code = check_csrf(request, csrf)
        if not allowed:
            return _reject(request, status, code)
    return None


def _reject(request: Request, status: int, code: str) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    return JSONResponse(
        {"requestId": request_id, "code": code}, status_code=status
    )

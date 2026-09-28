"""Identity / Origin / CSRF / host / content-type / body-cap guards.

Ported from `pwa-obsidian-daily/tests/security/`, trimmed to this app's route
set: the only `/api/*` routes that exist are `/api/version`, `/api/session`, and
the 404 catch-all, so a "fully authorized mutation" is proven by it reaching the
catch-all with `not_found`.

The load-bearing pair is `test_each_guard_alone_fails_with_its_own_envelope`
plus `test_the_earlier_guard_wins`. An ordering claim is circular if the only
evidence is that a request with two broken guards returns the first guard's
code — that is also what a guard that never runs would return. So each injected
fault is first asserted to fail on its own, and only then paired.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.auth import (
    CSRF_MAX_TOKENS,
    CSRF_TOKEN_LENGTH,
    CSRF_TTL_SECONDS,
    MAX_CSRF_TOKEN_CHARS,
    MAX_IDENTITY_CHARS,
    MAX_REQUEST_BYTES,
    SECURITY_HEADERS,
)
from app.config import Settings
from app.main import APP_VERSION, create_app
from tests.api.conftest import ATTACKER, ORIGIN, OWNER, client_for, make_settings

MUTATION_PATH = "/api/does-not-exist"
HOST = ORIGIN.split("://", 1)[1]
TOKEN_ALPHABET = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)
REQUIRED_HEADERS = {
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
}
# Mutation methods are a set, so the guard order has to be asserted for each.
MUTATION_METHODS = ("post", "put", "patch", "delete")


# --- the guard table ------------------------------------------------------


@dataclass
class Spec:
    """A mutation request under construction. The defaults satisfy every guard;
    a fault mutates exactly one thing."""

    headers: list[tuple[str, str]] = field(default_factory=list)
    body: str | None = None
    padding: int = 0
    origin: str | None = ORIGIN
    with_csrf: bool = True

    @property
    def content(self) -> str | None:
        if self.body is None and not self.padding:
            return None
        return (self.body or "") + "x" * self.padding

    def kwargs(self, token: str) -> dict[str, Any]:
        headers = list(self.headers)
        if self.origin is not None:
            headers.append(("Origin", self.origin))
        if self.with_csrf:
            headers.append(("X-CSRF-Token", token))
        return {"headers": headers, "content": self.content}


def _break_host(spec: Spec) -> None:
    spec.headers.append(("Host", "evil.example"))


def _break_identity(spec: Spec) -> None:
    # Dev mode: any client-supplied identity header is a spoof.
    spec.headers.append(("Tailscale-User-Login", OWNER))


def _break_read_only(spec: Spec) -> None:
    # Server-side policy, not a header: the fixture app is already read-only,
    # so there is nothing to inject. The pairs that use it say so.
    del spec


def _break_body_size(spec: Spec) -> None:
    # Padding, not replacement, so pairing it with the content-type fault keeps
    # a body that is both oversized and wrongly typed. One byte over the cap:
    # the gate is `> limit`, so exactly 1 MiB is allowed through.
    spec.padding = MAX_REQUEST_BYTES + 1


def _break_origin(spec: Spec) -> None:
    spec.origin = None


def _break_content_type(spec: Spec) -> None:
    # A body with no Content-Type header at all.
    spec.body = "{}"


def _break_csrf(spec: Spec) -> None:
    spec.with_csrf = False


GUARDS: dict[str, tuple[int, str]] = {
    "host": (400, "invalid_host"),
    "identity": (401, "identity_spoof"),
    "read_only": (403, "read_only"),
    "body_size": (413, "request_too_large"),
    "origin": (403, "origin_not_allowed"),
    "content_type": (415, "unsupported_media_type"),
    "csrf": (403, "csrf_required"),
}
FAULTS: dict[str, Callable[[Spec], None]] = {
    "host": _break_host,
    "identity": _break_identity,
    "read_only": _break_read_only,
    "body_size": _break_body_size,
    "origin": _break_origin,
    "content_type": _break_content_type,
    "csrf": _break_csrf,
}
# read_only is server-side, so a pair containing it needs the read-only app.
PAIRS: list[tuple[str, str, bool]] = [
    (earlier, later, False)
    for earlier, later in itertools.combinations(
        [name for name in GUARDS if name != "read_only"], 2
    )
] + [
    (earlier, later, True)
    for earlier, later in (
        ("host", "read_only"),
        ("identity", "read_only"),
        ("read_only", "body_size"),
        ("read_only", "origin"),
        ("read_only", "content_type"),
        ("read_only", "csrf"),
    )
]
PAIR_IDS = [f"{earlier}-before-{later}" for earlier, later, _ in PAIRS]


def _token(client: TestClient, login: str | None = None) -> str:
    headers = {"Tailscale-User-Login": login} if login else {}
    response = client.get("/api/session", headers=headers)
    assert response.status_code == 200
    return str(response.json()["csrfToken"])


def _post(client: TestClient, spec: Spec, token: str) -> Response:
    return cast(Response, client.post(MUTATION_PATH, **spec.kwargs(token)))


def _send(client: TestClient, method: str, spec: Spec, token: str) -> Response:
    # client.delete()/get() take no content= argument, so every method goes
    # through request() and stays comparable.
    return cast(Response, client.request(method.upper(), MUTATION_PATH, **spec.kwargs(token)))


def _assert_envelope(response: Response) -> dict[str, object]:
    body: dict[str, object] = dict(response.json())
    assert body["requestId"] == response.headers["x-request-id"]
    return body


def _app_for(dev_client: TestClient, read_only_client: TestClient, read_only: bool) -> TestClient:
    return read_only_client if read_only else dev_client


# --- 1. The guard order as a table ---------------------------------------


@pytest.mark.parametrize(
    "method", MUTATION_METHODS, ids=[f"{method}-clean" for method in MUTATION_METHODS]
)
def test_a_fully_authorized_mutation_reaches_the_router(
    dev_client: TestClient, method: str
) -> None:
    # The terminal state the whole table is measured against: every guard
    # satisfied means the request is routed, not rejected.
    token = _token(dev_client)
    response = _send(dev_client, method, Spec(), token)
    assert response.status_code == 404
    assert _assert_envelope(response)["code"] == "not_found"


@pytest.mark.parametrize(
    "guard", list(GUARDS), ids=[f"{name}-alone" for name in GUARDS]
)
@pytest.mark.parametrize("method", MUTATION_METHODS)
def test_each_guard_alone_fails_with_its_own_envelope(
    dev_client: TestClient,
    read_only_client: TestClient,
    guard: str,
    method: str,
) -> None:
    client = _app_for(dev_client, read_only_client, guard == "read_only")
    token = _token(client)
    spec = Spec()
    FAULTS[guard](spec)
    response = _send(client, method, spec, token)
    status, code = GUARDS[guard]
    assert (response.status_code, _assert_envelope(response)["code"]) == (status, code)


@pytest.mark.parametrize(("earlier", "later", "read_only"), PAIRS, ids=PAIR_IDS)
def test_the_earlier_guard_wins(
    dev_client: TestClient,
    read_only_client: TestClient,
    earlier: str,
    later: str,
    read_only: bool,
) -> None:
    client = _app_for(dev_client, read_only_client, read_only)
    token = _token(client)
    spec = Spec()
    FAULTS[earlier](spec)
    FAULTS[later](spec)
    response = _post(client, spec, token)
    status, code = GUARDS[earlier]
    assert (response.status_code, _assert_envelope(response)["code"]) == (status, code)
    assert response.headers["cache-control"] == "no-store"


def test_read_only_never_inspects_a_body(read_only_client: TestClient) -> None:
    """read_only is a policy gate: an oversized, wrongly-typed, foreign-origin
    body is still reported as read_only, never as 413/415/origin."""
    token = _token(read_only_client)
    spec = Spec(origin=ATTACKER, body="{", padding=MAX_REQUEST_BYTES + 1)
    response = _post(read_only_client, spec, token)
    assert (response.status_code, _assert_envelope(response)["code"]) == (403, "read_only")


# --- 2. Trusted-header mode: identity before every later guard -----------


@pytest.mark.parametrize("later", ["read_only", "body_size", "origin", "content_type", "csrf"])
@pytest.mark.parametrize("method", MUTATION_METHODS)
def test_trusted_mode_identity_precedes_every_later_guard(
    prod_read_only_client: TestClient, later: str, method: str
) -> None:
    # A blocked actor must not be able to tell the read-only flag from the
    # identity check, so identity wins even on a read-only server.
    token = _token(prod_read_only_client, OWNER)
    spec = Spec()
    FAULTS[later](spec)
    response = _send(prod_read_only_client, method, spec, token)
    assert response.status_code == 401
    assert _assert_envelope(response)["code"] == "identity_missing"


def test_trusted_mode_host_precedes_identity(prod_client: TestClient) -> None:
    spec = Spec()
    _break_host(spec)
    response = _post(prod_client, spec, "unused")
    assert (response.status_code, _assert_envelope(response)["code"]) == (400, "invalid_host")


def test_trusted_mode_owner_may_mutate(prod_client: TestClient) -> None:
    token = _token(prod_client, OWNER)
    spec = Spec()
    spec.headers.append(("Tailscale-User-Login", OWNER))
    response = _post(prod_client, spec, token)
    assert (response.status_code, _assert_envelope(response)["code"]) == (404, "not_found")


# --- 3. Identity matrix ---------------------------------------------------


@pytest.mark.parametrize("name", ["Tailscale-User-Login", "Tailscale-User-Name"])
@pytest.mark.parametrize("value", [OWNER, ATTACKER, ""])
def test_development_rejects_any_supplied_identity_header(
    dev_client: TestClient, name: str, value: str
) -> None:
    response = dev_client.get("/api/session", headers={name: value})
    assert (response.status_code, _assert_envelope(response)["code"]) == (401, "identity_spoof")


def test_development_rejects_repeated_identity_headers(dev_client: TestClient) -> None:
    response = dev_client.get(
        "/api/session",
        headers=[("Tailscale-User-Login", OWNER), ("Tailscale-User-Login", OWNER)],
    )
    assert (response.status_code, _assert_envelope(response)["code"]) == (401, "identity_spoof")


def test_development_without_an_identity_header_is_the_configured_identity(
    dev_client: TestClient,
) -> None:
    response = dev_client.get("/api/session")
    assert response.status_code == 200
    assert response.json()["identity"] == OWNER


def test_development_identity_prefers_dev_identity(runtime_root: Path) -> None:
    settings = make_settings(runtime_root, DEV_IDENTITY="dev@test.invalid")
    with client_for(settings) as client:
        assert client.get("/api/session").json()["identity"] == "dev@test.invalid"


@pytest.mark.parametrize(
    "path", ["/", "/health", "/sw.js", "/manifest.webmanifest", "/js/main.js", "/api/session"]
)
def test_trusted_mode_missing_identity_rejects_every_surface(
    prod_client: TestClient, path: str
) -> None:
    response = prod_client.get(path)
    assert (response.status_code, _assert_envelope(response)["code"]) == (401, "identity_missing")


@pytest.mark.parametrize(
    "login",
    [
        ATTACKER,
        "owner @test.invalid",
        " Owner@test.invalid",
        "owner@test.invalid.evil.com",
        "owner@test.invalid\x00",
        "x" * (MAX_IDENTITY_CHARS + 1),
    ],
    ids=("attacker", "inner-space", "case-mismatch", "suffix", "nul-byte", "oversized"),
)
def test_trusted_mode_wrong_or_malformed_identity_is_denied(
    prod_client: TestClient, login: str
) -> None:
    response = prod_client.get("/api/session", headers={"Tailscale-User-Login": login})
    assert response.status_code == 401
    assert _assert_envelope(response)["code"] in {"identity_denied", "identity_invalid"}


def test_trusted_mode_ows_padding_is_normalized(prod_client: TestClient) -> None:
    response = prod_client.get("/api/session", headers={"Tailscale-User-Login": f" \t{OWNER}\t "})
    assert response.status_code == 200
    assert response.json()["identity"] == OWNER


@pytest.mark.parametrize("login", ["", "   ", "\t"])
def test_trusted_mode_blank_identity_is_invalid(prod_client: TestClient, login: str) -> None:
    response = prod_client.get("/api/session", headers={"Tailscale-User-Login": login})
    assert (response.status_code, _assert_envelope(response)["code"]) == (401, "identity_invalid")


def test_trusted_mode_multiple_identity_headers_are_invalid(prod_client: TestClient) -> None:
    response = prod_client.get(
        "/api/session",
        headers=[("Tailscale-User-Login", OWNER), ("Tailscale-User-Login", ATTACKER)],
    )
    assert (response.status_code, _assert_envelope(response)["code"]) == (401, "identity_invalid")


# --- 4. Read-only mode: F18, no write allowlist --------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/cook-logs",
        "/api/shortlists/breakfast",
        "/api/shortlists/order",
        "/api/recipes/resolve",
        "/api/pantry/items",
        "/api/anything-at-all",
    ],
)
def test_read_only_rejects_every_mutation_path(read_only_client: TestClient, path: str) -> None:
    # A nonexistent path is the proof there is no allowlist: an allowlist has to
    # enumerate the paths it permits, so it would have to be extended for every
    # new route and would go stale.
    token = _token(read_only_client)
    spec = Spec()
    spec.headers.append(("X-CSRF-Token", token))
    response = read_only_client.post(path, **spec.kwargs(token))
    assert (response.status_code, _assert_envelope(response)["code"]) == (403, "read_only")


def test_read_only_has_no_allowlist_setting(read_only_settings: Settings) -> None:
    assert read_only_settings.read_only is True
    assert not hasattr(read_only_settings, "open_items_writes_enabled")


@pytest.mark.parametrize("method", ["get", "options"])
def test_read_only_leaves_reads_working(read_only_client: TestClient, method: str) -> None:
    response = getattr(read_only_client, method)(MUTATION_PATH)
    # OPTIONS is not a mutation, so read_only must not touch it; the catch-all
    # answers 404 and proves the request was routed rather than rejected.
    assert (response.status_code, _assert_envelope(response)["code"]) == (404, "not_found")


def test_read_only_surfaces_the_flag_on_session_and_health(
    read_only_client: TestClient,
) -> None:
    session = read_only_client.get("/api/session")
    assert session.status_code == 200
    assert session.json()["readOnly"] is True
    assert read_only_client.get("/health").status_code == 200


def test_read_only_leaves_a_same_origin_preflight_working(
    read_only_client: TestClient,
) -> None:
    # A foreign-origin preflight is 403 and a same-origin one must not be, or a
    # read-only server would refuse the browser's own preflight.
    foreign = read_only_client.options(MUTATION_PATH, headers={"Origin": ATTACKER})
    same_origin = read_only_client.options(
        MUTATION_PATH, headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"}
    )
    assert (foreign.status_code, _assert_envelope(foreign)["code"]) == (
        403,
        "origin_not_allowed",
    )
    assert same_origin.status_code == 404
    assert _assert_envelope(same_origin)["code"] == "not_found"


def test_read_only_still_serves_the_shell_and_health(read_only_client: TestClient) -> None:
    assert read_only_client.get("/").status_code == 200
    assert read_only_client.get("/health").status_code == 200


# --- 5. CSRF lifecycle ---------------------------------------------------


def test_session_issues_a_43_character_urlsafe_token(dev_client: TestClient) -> None:
    token = _token(dev_client)
    assert len(token) == CSRF_TOKEN_LENGTH
    assert set(token) <= TOKEN_ALPHABET


def test_only_session_issues_tokens(dev_settings: Settings) -> None:
    application = create_app(dev_settings)
    with TestClient(application, base_url=ORIGIN) as client:
        for path in ("/", "/health", "/js/main.js", "/api/version", "/api/does-not-exist"):
            assert client.get(path).status_code in {200, 404}, path
        assert application.state.csrf._tokens == {}
        client.get("/api/session")
        assert len(application.state.csrf._tokens) == 1


def test_a_fresh_session_rotates_without_invalidating_the_previous_token(
    dev_client: TestClient,
) -> None:
    first = _token(dev_client)
    second = _token(dev_client)
    assert first != second
    for token in (first, second):
        spec = Spec()
        spec.with_csrf = False
        spec.headers.append(("X-CSRF-Token", token))
        response = _post(dev_client, spec, token)
        assert (response.status_code, _assert_envelope(response)["code"]) == (404, "not_found")


def test_the_window_is_bounded_and_the_oldest_tokens_are_evicted(
    dev_settings: Settings,
) -> None:
    application = create_app(dev_settings)
    store = application.state.csrf
    issued = [store.issue() for _ in range(CSRF_MAX_TOKENS + 4)]
    # The store is bounded, so the window cannot grow with session refreshes.
    # The invariant is on the way *out* of issue(): at most CSRF_MAX_TOKENS
    # live tokens. This used to allow CSRF_MAX_TOKENS + 1, which pinned the
    # prune-before-insert overshoot described in docs/spec §9.17 as expected
    # behaviour; the code now prunes after inserting, so the documented number
    # is the one asserted.
    assert len(store._tokens) <= CSRF_MAX_TOKENS
    assert store.verify(issued[0]) is False
    assert store.verify(issued[3]) is False
    for token in issued[4:]:
        assert store.verify(token) is True


def test_the_token_just_issued_survives_eviction(dev_settings: Settings) -> None:
    # A prune that runs after the insert is only correct if it evicts the
    # *oldest* entry. Invert the comparison in `_prune` (evict the newest) and
    # the window still holds CSRF_MAX_TOKENS tokens, so only this test notices:
    # the newest token is the one the caller just handed to the client, and
    # losing it turns a successful /api/session into a guaranteed 403.
    application = create_app(dev_settings)
    store = application.state.csrf
    issued = [store.issue() for _ in range(CSRF_MAX_TOKENS + 1)]
    assert len(store._tokens) == CSRF_MAX_TOKENS
    newest = issued[-1]
    assert store.verify(newest) is True
    assert newest in store._tokens
    assert store._tokens[newest] == max(store._tokens.values())
    assert store.verify(issued[0]) is False


def test_verify_and_prune_agree_at_the_ttl_boundary(dev_settings: Settings) -> None:
    # `_prune` drops on `now - created > self._ttl` while `verify` accepts on
    # `now - created <= self._ttl`, so the two agree at the boundary: TTL
    # exactly is still live, one step past it is dead and gone from the dict.
    # Pinned because moving the prune to after the insert makes the store's
    # contents depend on when `_prune` runs, and a boundary the two halves
    # disagreed on would silently shorten the window.
    application = create_app(dev_settings)
    store = application.state.csrf
    token = store.issue()
    created = store._tokens[token]
    # Offsets are taken from `created`, not from a second `time.monotonic()`:
    # `created` is stamped a few microseconds *after* the clock read that a
    # separate reference would use, which is enough to land inside the TTL.
    # The 1 ms step past the boundary keeps the assertion off the knife-edge.
    past = created - CSRF_TTL_SECONDS - 0.001
    assert time.monotonic() - past > CSRF_TTL_SECONDS
    store._tokens[token] = past
    assert store.verify(token) is False
    # Back inside the window: same token, same code path, still valid.
    inside = created - CSRF_TTL_SECONDS + 1.0
    assert time.monotonic() - inside <= CSRF_TTL_SECONDS
    store._tokens[token] = inside
    assert store.verify(token) is True


def test_an_expired_token_is_rejected(dev_settings: Settings) -> None:
    application = create_app(dev_settings)
    with TestClient(application, base_url=ORIGIN) as client:
        token = str(client.get("/api/session").json()["csrfToken"])
        # Age the token past the TTL without touching wall-clock time.
        application.state.csrf._tokens[token] = time.monotonic() - (CSRF_TTL_SECONDS + 1)
        response = client.post(
            MUTATION_PATH, headers={"Origin": ORIGIN, "X-CSRF-Token": token}
        )
    assert (response.status_code, _assert_envelope(response)["code"]) == (403, "csrf_invalid")


def test_tokens_are_cleared_on_process_restart(dev_settings: Settings) -> None:
    with client_for(dev_settings) as first:
        token = str(first.get("/api/session").json()["csrfToken"])
    # A second process over the same settings starts with an empty store.
    with client_for(dev_settings) as second:
        response = second.post(
            MUTATION_PATH, headers={"Origin": ORIGIN, "X-CSRF-Token": token}
        )
    assert (response.status_code, _assert_envelope(response)["code"]) == (403, "csrf_invalid")


@pytest.mark.parametrize("token", ["", "A" * (MAX_CSRF_TOKEN_CHARS + 1), "forged-token"])
def test_csrf_input_is_bounded(dev_client: TestClient, token: str) -> None:
    spec = Spec()
    spec.with_csrf = False
    spec.headers.append(("X-CSRF-Token", token))
    response = _post(dev_client, spec, token)
    assert (response.status_code, _assert_envelope(response)["code"]) == (403, "csrf_invalid")


def test_repeated_csrf_headers_are_invalid(dev_client: TestClient) -> None:
    token = _token(dev_client)
    spec = Spec()
    spec.with_csrf = False
    spec.headers.extend([("X-CSRF-Token", token), ("X-CSRF-Token", token)])
    response = _post(dev_client, spec, token)
    assert (response.status_code, _assert_envelope(response)["code"]) == (403, "csrf_invalid")


# --- 6. Origin -----------------------------------------------------------


@pytest.mark.parametrize(
    "origin", [ATTACKER, f"{ORIGIN}/", "null", "http://localhost:8123", f" {ORIGIN} extra"]
)
def test_a_foreign_origin_is_rejected(dev_client: TestClient, origin: str) -> None:
    token = _token(dev_client)
    spec = Spec(origin=origin)
    response = _post(dev_client, spec, token)
    assert (response.status_code, _assert_envelope(response)["code"]) == (403, "origin_not_allowed")


def test_repeated_origin_headers_are_rejected(dev_client: TestClient) -> None:
    token = _token(dev_client)
    spec = Spec()
    spec.with_csrf = False
    spec.headers.extend([("Origin", ORIGIN), ("Origin", ATTACKER)])
    response = _post(dev_client, spec, token)
    assert (response.status_code, _assert_envelope(response)["code"]) == (403, "origin_not_allowed")


def test_a_foreign_origin_preflight_is_rejected(dev_client: TestClient) -> None:
    response = dev_client.options(
        MUTATION_PATH,
        headers={
            "Origin": ATTACKER,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        },
    )
    assert (response.status_code, _assert_envelope(response)["code"]) == (403, "origin_not_allowed")


def test_no_response_ever_advertises_cors(dev_client: TestClient) -> None:
    token = _token(dev_client)
    same_origin_preflight = dev_client.options(
        MUTATION_PATH, headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"}
    )
    authorized = _post(dev_client, Spec(), token)
    foreign = _post(dev_client, Spec(origin=ATTACKER), token)
    for response in (same_origin_preflight, authorized, foreign):
        assert response.headers.get("access-control-allow-origin") is None
        assert response.headers.get("access-control-allow-methods") is None


@pytest.mark.parametrize("path", ["/", "/health", "/api/session", "/api/does-not-exist"])
def test_reads_are_origin_exempt(dev_client: TestClient, path: str) -> None:
    response = dev_client.get(path, headers={"Origin": ATTACKER})
    assert response.status_code in {200, 404}


# --- 7. Content type -----------------------------------------------------


@pytest.mark.parametrize(
    "content_type",
    ["text/plain", "application/x-www-form-urlencoded", "application/json-patch+xml", "text/json"],
)
def test_a_non_json_mutation_body_is_415(dev_client: TestClient, content_type: str) -> None:
    token = _token(dev_client)
    response = dev_client.post(
        MUTATION_PATH,
        content="{}",
        headers={"Origin": ORIGIN, "X-CSRF-Token": token, "Content-Type": content_type},
    )
    assert (response.status_code, _assert_envelope(response)["code"]) == (
        415,
        "unsupported_media_type",
    )


def test_a_mutation_body_without_a_content_type_is_415(dev_client: TestClient) -> None:
    token = _token(dev_client)
    response = dev_client.post(
        MUTATION_PATH, content="{}", headers={"Origin": ORIGIN, "X-CSRF-Token": token}
    )
    assert (response.status_code, _assert_envelope(response)["code"]) == (
        415,
        "unsupported_media_type",
    )


def test_repeated_content_type_headers_are_415(dev_client: TestClient) -> None:
    token = _token(dev_client)
    response = dev_client.post(
        MUTATION_PATH,
        content="{}",
        headers=[
            ("Origin", ORIGIN),
            ("X-CSRF-Token", token),
            ("Content-Type", "application/json"),
            ("Content-Type", "text/plain"),
        ],
    )
    assert (response.status_code, _assert_envelope(response)["code"]) == (
        415,
        "unsupported_media_type",
    )


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json",
        "application/json; charset=utf-8",
        "application/ld+json",
        "application/vnd.pantry+json",
    ],
)
def test_json_media_types_pass_the_content_type_guard(
    dev_client: TestClient, content_type: str
) -> None:
    token = _token(dev_client)
    response = dev_client.post(
        MUTATION_PATH,
        content="{}",
        headers={"Origin": ORIGIN, "X-CSRF-Token": token, "Content-Type": content_type},
    )
    assert (response.status_code, _assert_envelope(response)["code"]) == (404, "not_found")


def test_a_body_free_mutation_needs_no_content_type(dev_client: TestClient) -> None:
    token = _token(dev_client)
    response = dev_client.post(MUTATION_PATH, headers={"Origin": ORIGIN, "X-CSRF-Token": token})
    assert (response.status_code, _assert_envelope(response)["code"]) == (404, "not_found")


def test_reads_are_not_content_type_gated(dev_client: TestClient) -> None:
    response = dev_client.get("/api/session", headers={"Content-Type": "text/plain"})
    assert response.status_code == 200


# --- 8. Body cap and transfer encoding -----------------------------------


def test_a_body_over_one_mebibyte_is_413(dev_client: TestClient) -> None:
    token = _token(dev_client)
    response = dev_client.post(
        MUTATION_PATH,
        content="[" + "x" * MAX_REQUEST_BYTES + "]",
        headers={"Origin": ORIGIN, "X-CSRF-Token": token, "Content-Type": "application/json"},
    )
    assert (response.status_code, _assert_envelope(response)["code"]) == (
        413,
        "request_too_large",
    )


def test_a_body_at_exactly_one_mebibyte_passes_the_cap(dev_client: TestClient) -> None:
    token = _token(dev_client)
    response = dev_client.post(
        MUTATION_PATH,
        content="[" + "x" * (MAX_REQUEST_BYTES - 2) + "]",
        headers={"Origin": ORIGIN, "X-CSRF-Token": token, "Content-Type": "application/json"},
    )
    assert response.status_code == 404


def test_a_read_with_an_oversized_body_is_413(dev_client: TestClient) -> None:
    response = dev_client.request(
        "GET", "/api/does-not-exist", content="y" * (MAX_REQUEST_BYTES + 1)
    )
    assert (response.status_code, _assert_envelope(response)["code"]) == (
        413,
        "request_too_large",
    )


def test_chunked_transfer_encoding_is_413(dev_client: TestClient) -> None:
    # There is no upload route, so there is no chunked exception: a chunked
    # body has no Content-Length to bound it up front.
    token = _token(dev_client)
    response = dev_client.post(
        MUTATION_PATH,
        content="{}",
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": token,
            "Content-Type": "application/json",
            "Transfer-Encoding": "chunked",
        },
    )
    assert (response.status_code, _assert_envelope(response)["code"]) == (
        413,
        "request_too_large",
    )


@pytest.mark.parametrize("method", MUTATION_METHODS)
def test_every_mutation_method_is_body_capped(dev_client: TestClient, method: str) -> None:
    token = _token(dev_client)
    response = dev_client.request(
        method.upper(),
        MUTATION_PATH,
        content="[" + "x" * MAX_REQUEST_BYTES + "]",
        headers={"Origin": ORIGIN, "X-CSRF-Token": token, "Content-Type": "application/json"},
    )
    assert response.status_code == 413


# --- 9. Host -------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        f"{HOST}.evil.com",
        f"{HOST}:9999",
        "127.0.0.1:8123@evil.example",
        f"user@{HOST}",
        f"{HOST}/extra",
        f"{HOST}?q=1",
        "[::1",
        f"{HOST}:not-a-port",
        f"{HOST}:0",
        f"{HOST}:99999",
        HOST + ":" + "9" * 400,
    ],
    ids=(
        "foreign",
        "suffix",
        "wrong-port",
        "userinfo",
        "userinfo2",
        "path",
        "query",
        "broken-ipv6",
        "bad-port",
        "zero-port",
        "huge-port",
        "oversized",
    ),
)
def test_a_host_that_is_neither_origin_nor_loopback_is_400(
    dev_client: TestClient, host: str
) -> None:
    response = dev_client.get("/api/session", headers={"Host": host})
    assert (response.status_code, _assert_envelope(response)["code"]) == (400, "invalid_host")


@pytest.mark.parametrize(
    "host",
    [
        HOST,
        HOST.upper(),
        "127.0.0.1:8123",
        "127.42.1.2",
        "localhost:8123",
        "localhost",
        "[::1]:8123",
        "[::1]",
        f"  {HOST}  ",
    ],
    ids=(
        "origin-host",
        "origin-host-case",
        "loopback-ip-port",
        "loopback-class",
        "localhost-port",
        "localhost",
        "ipv6-loopback-port",
        "ipv6-loopback",
        "ows-padded",
    ),
)
def test_the_origin_host_and_any_loopback_host_pass(dev_client: TestClient, host: str) -> None:
    response = dev_client.get("/api/session", headers={"Host": host})
    assert response.status_code == 200


def test_repeated_host_headers_are_400(dev_client: TestClient) -> None:
    response = dev_client.get(
        "/api/session", headers=[("Host", HOST), ("Host", "evil.example")]
    )
    assert (response.status_code, _assert_envelope(response)["code"]) == (400, "invalid_host")


def test_host_is_checked_before_the_path_is_parsed(dev_client: TestClient) -> None:
    # A hostile Host on a *static* path is rejected too, not only on /api/*:
    # otherwise the rejection leaks which paths exist.
    response = dev_client.get("/", headers={"Host": "evil.example"})
    assert (response.status_code, _assert_envelope(response)["code"]) == (400, "invalid_host")
    assert response.headers["cache-control"] == "no-store"


# --- 10. Security headers and the session contract ----------------------


def _assert_security_headers(response: Response) -> None:
    for name in REQUIRED_HEADERS:
        assert name in response.headers, name
    csp = response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "form-action 'none'" in csp
    assert "object-src 'none'" in csp
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_the_security_header_set_is_exactly_the_documented_one() -> None:
    assert {name.lower() for name in SECURITY_HEADERS} == REQUIRED_HEADERS
    assert SECURITY_HEADERS["Content-Security-Policy"] == (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'; manifest-src 'self'; worker-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'"
    )


def test_the_header_set_is_present_on_every_auth_rejection(
    read_only_client: TestClient, prod_client: TestClient
) -> None:
    read_only = _post(read_only_client, Spec(), _token(read_only_client))
    untrusted_identity = prod_client.get("/api/session")
    hostile_host = prod_client.get("/api/session", headers={"Host": "evil.example"})
    for response in (read_only, untrusted_identity, hostile_host):
        assert response.status_code >= 400
        assert response.headers["x-request-id"]
        assert response.headers["cache-control"] == "no-store"
        _assert_security_headers(response)


def test_session_publishes_exactly_the_contracted_shape(client: TestClient) -> None:
    response = client.get("/api/session")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"identity", "csrfToken", "version", "readOnly", "appTimezone"}
    assert body["identity"] == OWNER
    assert body["version"] == APP_VERSION
    assert body["readOnly"] is False
    assert body["appTimezone"] == "America/New_York"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]


def test_session_leaks_no_server_owned_path(dev_client: TestClient, dev_settings: Settings) -> None:
    response = dev_client.get("/api/session")
    for path in (
        dev_settings.vault_path,
        dev_settings.app_data_dir,
        dev_settings.pantry_items_db,
    ):
        assert str(path) not in response.text
    assert "TAILSCALE" not in response.text

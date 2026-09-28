"""The §9.16 route surface that needs no domain routes yet.

Only `/api/version`, `/api/session`, and the 404 catch-all exist in this phase,
so this file covers the parts of the API contract that are already true: the
error envelope on every unmatched `/api/*` method, the traversal refusal on
`/js/{path}`, and the fact that a mutation is refused before it can be routed.

Run alone:  .venv/bin/python -m pytest tests/scaffold/test_routes.py -q
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.auth import MAX_REQUEST_BYTES, SECURITY_HEADERS
from tests.api.conftest import ORIGIN

UNMATCHED = "/api/does-not-exist"
FUTURE_MUTATIONS = ["/api/cook-logs", "/api/shortlists/breakfast", "/api/recipes/resolve"]
SHELL_SURFACE = [
    "/",
    "/js/main.js",
    "/css/styles.css",
    "/css/tokens.css",
    "/css/pwa.css",
    "/css/pull-refresh.css",
    "/manifest.webmanifest",
    "/sw.js",
    "/icons/icon-192.png",
    "/icons/icon-512.png",
    "/icons/icon-180-apple.png",
    "/icons/icon-monochrome-512.png",
    "/health",
    "/api/version",
    "/api/session",
    "/api/does-not-exist",
]


def _assert_security_headers(response: Response) -> None:
    headers = response.headers
    for name, value in SECURITY_HEADERS.items():
        assert headers[name.lower()] == value, name
    # No inline script and no framing, which is why index.html carries the
    # version in a <meta> tag and nothing assigns window.__APP_VERSION__.
    csp = headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    assert headers["x-frame-options"] == "DENY"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("path", SHELL_SURFACE)
def test_every_response_carries_the_security_header_set(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code in {200, 404}, path
    _assert_security_headers(response)


def test_every_rejection_carries_the_security_header_set(client: TestClient) -> None:
    token = client.get("/api/session").json()["csrfToken"]
    authorized = {"Origin": ORIGIN, "X-CSRF-Token": token}
    responses = [
        client.get("/api/session", headers={"Host": "evil.example"}),  # 400 invalid_host
        client.get("/api/session", headers={"Tailscale-User-Login": "spoof"}),  # 401
        client.post(UNMATCHED),  # 403 origin_not_allowed
        client.post(UNMATCHED, headers={"Origin": ORIGIN}),  # 403 csrf_required
        client.post(UNMATCHED, content="{}", headers=authorized),  # 415
        client.post(  # 413
            UNMATCHED, content="x" * (MAX_REQUEST_BYTES + 1), headers=authorized
        ),
        client.get(UNMATCHED),  # 404
    ]
    for response in responses:
        assert response.status_code >= 400, response.request.url
        assert response.headers["x-request-id"]
        assert response.headers["cache-control"] == "no-store"
        _assert_security_headers(response)


@pytest.mark.parametrize(
    "method", ["get", "post", "put", "patch", "delete", "options"]
)
def test_every_unmatched_api_method_returns_the_error_envelope(
    client: TestClient, method: str
) -> None:
    # A mutation has to clear Origin and CSRF before it can be routed, so an
    # unmatched mutation needs a real token; otherwise the 403 would answer
    # instead of the 404 and this test would prove nothing about the envelope.
    token = client.get("/api/session").json()["csrfToken"]
    response = client.request(
        method.upper(),
        UNMATCHED,
        headers={"Origin": ORIGIN, "X-CSRF-Token": token},
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    # Exactly two keys: the envelope extension in §9.15 is additive and no
    # other code may grow one.
    assert set(response.json()) == {"requestId", "code"}
    assert response.json()["code"] == "not_found"
    assert response.json()["requestId"] == response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]


def test_a_mutation_with_no_csrf_token_is_403(client: TestClient) -> None:
    for path in FUTURE_MUTATIONS:
        response = client.post(path, headers={"Origin": ORIGIN})
        assert response.status_code == 403, path
        assert response.json()["code"] == "csrf_required"
        assert response.json()["requestId"] == response.headers["x-request-id"]
        assert response.headers["cache-control"] == "no-store"


def test_a_mutation_with_no_origin_is_403(client: TestClient) -> None:
    for path in FUTURE_MUTATIONS:
        response = client.post(path)
        assert response.status_code == 403, path
        # Origin is checked before CSRF, so a request that satisfies neither is
        # reported as the earlier failure.
        assert response.json()["code"] == "origin_not_allowed"


def test_the_js_route_still_refuses_traversal(client: TestClient) -> None:
    for path in ("/js/../config.py", "/js/../../etc/passwd", "/js/nope.js"):
        response = client.get(path)
        assert response.status_code in {307, 404}, path
        assert "../config.py" not in response.text
        assert "root:x:" not in response.text


def test_a_traversal_that_survives_normalization_still_404s(client: TestClient) -> None:
    # The encoded form reaches the app un-normalized, so the route's own
    # resolved-path guard is what refuses it.
    response = client.get("/js/%2e%2e/config.py")
    assert response.status_code == 404
    assert "OBSIDIAN_VAULT_PATH" not in response.text

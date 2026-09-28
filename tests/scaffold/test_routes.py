"""The §9.16 route surface, and the registration order it depends on.

Run alone:  .venv/bin/python -m pytest tests/scaffold/test_routes.py -q

**The order test at the bottom is the one that matters most in this file.** Route
registration order in Starlette is not a style preference: a route is matched
against the table front to back, so a route registered after a catch-all is not a
differently-shaped 404, it is a **dead route** — unreachable, silently, with every
test that exercises it passing against a hand-built app instead. The static mount
has path `""` and matches everything, so the same is true of anything registered
after it.

`test_the_route_table_is_registered_in_the_load_bearing_order` therefore asserts
the whole flattened table, in order, and the mutation run reported with #15 kills
all three ways of breaking it: unmounting a router, moving a domain router below
the catch-all, and moving the static mount off last.

**The routes exercised here are the mounted ones.** The `client` fixture runs the
real `lifespan` through `create_app`, so `GET /api/recipes` is answered by the
router `app/main.py` registered — not by one a test assembled.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from cooklog.harness import CATCH_ALL_PATH, registered_paths
from fastapi.testclient import TestClient
from httpx import Response

from app.auth import MAX_REQUEST_BYTES, SECURITY_HEADERS
from app.config import Settings
from app.main import create_app
from tests.api.conftest import ORIGIN, make_settings, seed_catalog, write_pantry_note, write_recipe
from tests.api.test_recipes_api import API_CATALOG_ROWS, MAIN_RECIPE

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

#: §9.19's order, as a list. The whole table, in match order, flattened through
#: FastAPI's `include_router` wrapper.
#:
#: The two trailing entries are the load-bearing pair: the catch-all must be after
#: every domain route, and the static mount — path `""`, matches everything — must
#: be last of all. The four `/openapi`-family routes at the front are FastAPI's own
#: setup and are listed so the assertion is of the *whole* table rather than a
#: suffix of it.
EXPECTED_ROUTE_TABLE: list[str] = [
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
    "/api/version",
    "/health",
    "/api/session",
    "/",
    "/sw.js",
    "/manifest.webmanifest",
    "/js/{path:path}",
    "/api/recipes",
    "/api/recipes/{note_name}",
    "/api/recipes/resolve",
    "/api/recipes/{note_name}/ingredients/{index}/mapping",
    "/api/recipes/{note_name}/ingredients/{index}/mapping",
    "/api/pantry/items",
    "/api/cook-logs",
    "/api/cook-logs",
    CATCH_ALL_PATH,
    "",
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


# --- §9.16's route surface, on the mounted app -----------------------------
#
# The `client` fixture above runs the real `lifespan`, so everything below is
# answered by the routers `app/main.py` registered. `settings` re-seeds the
# catalog and writes one recipe and one `Pantry.md` so the read routes have
# something to answer *with* rather than failing closed for want of a fixture.


@pytest.fixture
def domain_client(runtime_root: Path) -> Iterator[TestClient]:
    """The real app with a recipe, a pantry note, and a seeded catalog.

    The catalog is seeded from the same committed literals the domain suites use
    so `/api/recipes` can answer 200 rather than 503 here — the point of this file
    is *reachability*, and a 503 would be a reachable route that could not say
    anything.
    """
    configured = make_settings(runtime_root)
    seed_catalog(configured.pantry_items_db, API_CATALOG_ROWS)
    write_recipe(configured.vault_path, MAIN_RECIPE)
    write_pantry_note(configured.vault_path)
    with TestClient(create_app(configured), base_url=ORIGIN) as mounted:
        yield mounted


def test_every_spec_route_responds_through_the_mounted_app(domain_client: TestClient) -> None:
    """Every row of §9.16's table that this phase owns answers, and answers as itself.

    The point is not the status — it is that a 200 came from *this* route rather
    than from the catch-all wearing its status. So each row asserts the body shape
    that distinguishes it: the picker's `items`, the list's `recipes`, the
    resolve's report, the cook log's 404 envelope with its six keys.
    """
    session = domain_client.get("/api/session")
    assert session.status_code == 200
    assert set(session.json()) == {
        "identity",
        "csrfToken",
        "version",
        "readOnly",
        "appTimezone",
    }
    assert domain_client.get("/api/version").json()["version"] == session.json()["version"]

    picker = domain_client.get("/api/pantry/items")
    assert picker.status_code == 200
    assert set(picker.json()) == {"items", "catalogRevision"}

    listing = domain_client.get("/api/recipes")
    assert listing.status_code == 200
    assert set(listing.json()) == {
        "recipes",
        "catalogRevision",
        "stockRevision",
        "strict",
        "staleMappingCount",
        "stockUnjoinedCount",
        "skipped",
    }

    detail = domain_client.get(f"/api/recipes/{MAIN_RECIPE}")
    assert detail.status_code == 200
    assert "recipe" in detail.json()

    token = session.json()["csrfToken"]
    mutation = {"Origin": ORIGIN, "X-CSRF-Token": token}
    resolved = domain_client.post("/api/recipes/resolve", headers=mutation)
    assert resolved.status_code == 200
    assert "reconsidered" in resolved.json()

    mapped = domain_client.put(
        f"/api/recipes/{MAIN_RECIPE}/ingredients/3/mapping",
        json={"pantryItemId": 6},
        headers=mutation,
    )
    assert mapped.status_code == 200
    assert set(mapped.json()) == {"mapping"}
    cleared = domain_client.delete(
        f"/api/recipes/{MAIN_RECIPE}/ingredients/3/mapping", headers=mutation
    )
    assert cleared.status_code == 200
    assert cleared.json() == {"mapping": None}

    # The cook log's 404, and its code is what proves the route answered rather
    # than the catch-all — both are 404s.
    read_back = domain_client.get("/api/cook-logs", params={"date": "2026-09-27"})
    assert read_back.status_code == 404
    assert read_back.json()["code"] == "daily_note_missing"


def test_an_unknown_api_path_never_falls_through_to_the_static_mount(
    domain_client: TestClient,
) -> None:
    """`/api/does-not-exist` is the JSON envelope, not the shell's HTML 404.

    The static mount has `html=True` and path `""`, so anything that fails to
    match an earlier route gets an HTML document. For an API client that is a
    parse error at best, and a rendered 404 page at worst.
    """
    response = domain_client.get(UNMATCHED)

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["code"] == "not_found"
    assert "<html" not in response.text.lower()


@pytest.fixture
def built_app(settings: Settings) -> object:
    """A built-but-unstarted app: the route table with no lifespan run.

    The order is a property of `create_app`, not of a request, so the app is never
    entered here — which also means this test touches no vault and opens no
    descriptor, and cannot be the reason a descriptor count moved.
    """
    return create_app(settings)


def test_the_route_table_is_registered_in_the_load_bearing_order(built_app: object) -> None:
    """§9.19's order, asserted as a whole table, in order.

    **The single most load-bearing test in this repository after the guard-order
    one.** Starlette matches front to back, so this list is not documentation:

    - a domain router after the catch-all is a **dead route** — unreachable, with
      no error anywhere, which is the failure mode where tests exercise a
      hand-built app and pass;
    - the static mount, path `""`, matches everything, so anything after it is
      equally dead;
    - `/js/{path}` must precede the mount, or the module-version injection never
      runs and every nested ESM import goes 304-stale after a deploy.

    Built from `create_app` with no request and no lifespan, so it observes the
    table and nothing else.
    """
    assert registered_paths(built_app) == EXPECTED_ROUTE_TABLE


def test_the_static_mount_is_last_and_the_catch_all_is_after_the_domain_routers(
    built_app: object,
) -> None:
    """The two relationships that make the order above matter, stated separately.

    Asserting them on their own is what makes the failure legible: a failure here
    says *which* relationship broke, and the whole-table assertion above is then
    a formality by comparison.
    """
    paths = registered_paths(built_app)
    domain = ["/api/recipes", "/api/pantry/items", "/api/cook-logs"]
    catch_all = paths.index(CATCH_ALL_PATH)

    assert paths[-1] == "", "the static mount must be registered last"
    for path in domain:
        assert paths.index(path) < catch_all, f"{path} is registered after the catch-all"
    assert paths.index("/js/{path:path}") < paths.index("/api/recipes")


def test_the_cook_log_harness_splice_is_a_no_op_now_that_the_wiring_exists(
    settings: Settings,
) -> None:
    """`tests/cooklog/harness.py` must not double-register against the real wiring.

    The harness used to splice `/api/cook-logs` into `create_app` itself, at §9.19's
    position, because nothing registered it. It still offers that, and it is now
    a **no-op** — which is a claim that has to be tested, not assumed, for two
    reasons.

    First, a double registration is not a visible error: Starlette matches front to
    back, so the first copy answers and the second is dead code that still appears
    in the route table. Second, the harness's own check for "is it already
    registered?" scans `route.path`, and FastAPI >= 0.141 keeps an
    `include_router` result as a *wrapper* whose `path` is `None` — so a
    wrapper-blind scan would report the route as absent and the splice would run.
    That is a real interaction between this ticket and #18's harness, and it is
    why `registered_paths` exists.
    """
    from cooklog.harness import mount_cook_logs

    before = registered_paths(create_app(settings))
    spliced = mount_cook_logs(create_app(settings), writer=None)  # type: ignore[arg-type]

    assert registered_paths(spliced) == before
    assert before.count("/api/cook-logs") == 2, "one POST and one GET, and no duplicate"


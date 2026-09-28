"""FastAPI application factory for the Pantry Recipes PWA.

Scaffold only: the routes below are the shell contract (version, session,
health, the HTML shell, the module-version injection route, and the API 404
envelope). `install_security_middleware` wraps all of it with the identity /
Origin / CSRF / host guards in `app/auth.py`. Domain routers — pantry catalog,
recipe index, daily-note cooking logs — arrive under `app/api/` with the
feature implementation; nothing here reads the vault or the pantry catalog yet.

Route order is load-bearing. `/js/{path}` is registered BEFORE the static mount
so it wins for `/js/*`, and the static mount is registered LAST so it can never
shadow an API route.

Start the server with the factory so the environment is read at boot, not at
import:  uvicorn app.main:create_app --factory
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint

from .auth import install_security_middleware
from .config import Settings, validate_bind_invariant
from .pwa_version import derive_version, install_pwa_version

PACKAGE_ROOT = Path(__file__).parent
STATIC_ROOT = PACKAGE_ROOT / "static"

# Pattern A (pwa-infra): the Service Worker's CACHE_VERSION is the single source
# of truth. derive_version() regex-parses it out of sw.js at import time, so the
# served SW cache name, the HTML shell's version pin, the FastAPI app version,
# /health, and the ESM import-specifier injection below can never drift.
# Bump CACHE_VERSION in app/static/sw.js per release — there is no VERSION file.
APP_VERSION = derive_version(STATIC_ROOT)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Environment loading occurs only when no settings are
    injected, so tests can supply an isolated vault + data dir."""
    runtime = settings or Settings.from_environment()
    validate_bind_invariant(runtime)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        # TODO(implementation): open the pantry SQLite catalog read-only, build
        # the recipe index over RECIPES_ROOT, and create the atomic note store
        # used for daily-note cooking-log writes. Close them in a finally so a
        # descriptor leak cannot outlive shutdown.
        yield

    application = FastAPI(title="Pantry Recipes", version=APP_VERSION, lifespan=lifespan)
    application.state.settings = runtime

    # Mounts GET /api/version (no-store + X-PWA-Backend-Started-At) derived
    # from the same sw.js CACHE_VERSION as APP_VERSION.
    install_pwa_version(application, static_dir=STATIC_ROOT)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> Response:
        del exc
        return _api_error(request, 422, "invalid_request")

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if request.url.path.startswith("/api/"):
            code = "not_found" if exc.status_code == 404 else "http_error"
            return _api_error(request, exc.status_code, code)
        return Response(str(exc.detail), status_code=exc.status_code)

    @application.middleware("http")
    async def cache_policy(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.request_id = str(uuid.uuid4())
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        path = request.url.path
        # /api/version is exempt: the vendored pwa_version route owns its
        # headers and already sends the stronger
        # "no-store, no-cache, must-revalidate". Overwriting it here would
        # silently weaken the update check's cache contract.
        if path == "/health" or (path.startswith("/api/") and path != "/api/version"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # Security must register *after* cache_policy so it runs outermost: a
    # hostile Host is rejected before any middleware parses request.url
    # (Starlette builds the URL from the Host header), and every rejection
    # still gets X-Request-ID and the no-store envelope.
    install_security_middleware(application, runtime)

    @application.get("/health", include_in_schema=False)
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "version": APP_VERSION,
            "vault": {"readable": os.access(runtime.vault_path, os.R_OK)},
            "app_data": {"operational": os.access(runtime.app_data_dir, os.R_OK | os.W_OK)},
            "pantry_db": {"readable": os.access(runtime.pantry_items_db, os.R_OK)},
        }

    @application.get("/api/session")
    def session(request: Request) -> JSONResponse:
        """The boot contract: who the caller is, the CSRF token every mutation
        must echo, and the capability flags the UI renders. `version` is the
        same derive_version() value as /api/version and the sw.js CACHE_VERSION."""
        identity = (
            runtime.tailscale_owner_login
            if runtime.trust_tailscale_headers
            else runtime.dev_identity
        )
        return JSONResponse(
            {
                "identity": identity,
                "csrfToken": request.app.state.csrf.issue(),
                "version": APP_VERSION,
                "readOnly": runtime.read_only,
                "appTimezone": runtime.app_timezone,
            }
        )

    @application.get("/", include_in_schema=False)
    def shell() -> Response:
        """Serve the HTML shell with __APP_VERSION__ replaced by the value
        derived from sw.js CACHE_VERSION. CSP-clean: a meta tag, not an inline
        script (docs/pwa-template.md 3b)."""
        html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
        return Response(
            html.replace("__APP_VERSION__", APP_VERSION),
            media_type="text/html",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @application.get("/sw.js", include_in_schema=False)
    def service_worker() -> FileResponse:
        return FileResponse(
            STATIC_ROOT / "sw.js",
            media_type="text/javascript",
            headers={
                "Service-Worker-Allowed": "/",
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
            },
        )

    @application.get("/manifest.webmanifest", include_in_schema=False)
    def manifest() -> FileResponse:
        return FileResponse(
            STATIC_ROOT / "manifest.webmanifest", media_type="application/manifest+json"
        )

    @application.get("/js/{path:path}", include_in_schema=False)
    def js_module(path: str) -> Response:
        """Serve /js/*.js with __APP_VERSION__ injected into import specifiers.

        A `?v=` pin on the entry URL does NOT propagate to relative ES-module
        imports (`import './views/list.js?v=X'` inside `/js/main.js?v=X`
        resolves to `/js/views/list.js`, query dropped), so pinning only the
        entry leaves every submodule stale-304-able after a deploy.
        Injecting the token into module *content* version-pins the whole graph
        from the single CACHE_VERSION bump (docs/pwa-template.md 3b). The
        resolved-path guard below rejects traversal outside the js/ root.
        """
        base = (STATIC_ROOT / "js").resolve()
        target = (base / path).resolve()
        if not str(target).startswith(str(base) + os.sep) or not target.is_file():
            raise HTTPException(status_code=404, detail="Not found")
        content = target.read_text(encoding="utf-8").replace("__APP_VERSION__", APP_VERSION)
        return Response(
            content,
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    # TODO(implementation): include the domain routers here, e.g.
    #   application.include_router(build_pantry_router())
    #   application.include_router(build_recipes_router())
    #   application.include_router(build_cook_log_router())

    @application.api_route(
        "/api/{unmatched_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    def unmatched_api(unmatched_path: str, request: Request) -> Response:
        """Catch-all so an unknown /api/* path returns the JSON error envelope
        instead of falling through to the static mount's HTML 404."""
        del unmatched_path
        return _api_error(request, 404, "not_found")

    # Registered LAST: a catch-all mount shadows anything added after it.
    application.mount("/", StaticFiles(directory=STATIC_ROOT, html=True), name="static")
    return application


def _api_error(request: Request, status: int, code: str) -> Response:
    return JSONResponse({"requestId": request.state.request_id, "code": code}, status_code=status)

"""FastAPI application factory for the Pantry Recipes PWA.

The shell contract — version, session, health, the HTML shell, the module-version
injection route, and the API 404 envelope — plus the domain routers under
`app/api/`, and the `lifespan` that opens and closes what they read.

**Route order is load-bearing, and it is the whole reason this docstring
exists.** `/js/{path}` is registered BEFORE the static mount so it wins for
`/js/*`; the **domain routers are registered AFTER `/js/{path}` and BEFORE the
`/api/{unmatched_path:path}` catch-all**, because a catch-all route shadows
anything registered after it; and the static mount is registered LAST so it can
never shadow an API route. A router moved below the catch-all is not a
different-shaped 404, it is a *dead route*: nothing can reach it, nothing fails
loudly, and the tests that "cover" it pass against a hand-built app instead.
`tests/scaffold/test_routes.py::test_the_route_table_is_registered_in_the
load_bearing_order` pins the whole sequence, and the mutation run in #15's report
killed three ways of breaking it.

**The `lifespan` owns every resource, and its `finally` owns every descriptor.**
§9.19: open the `PantryCatalog`, the `RecipeIndex`, the `PantryStockIndex`, the
`AtomicNoteStore`, the `IngredientMappingStore`, the `CookingLogWriter`, and run
`init_db()`; close all of them in a `finally`. A descriptor leak must not outlive
shutdown — the LaunchAgent's `SoftResourceLimits NumberOfFiles 8192` exists
because launchd's default 256 is not the shell's `ulimit -n`, so a leak here only
ever shows up in production. **There is no creation service in the lifespan**
(F4): `DailyNoteCreationService` does not exist, and a missing daily note is a
404.

Start the server with the factory so the environment is read at boot, not at
import:  uvicorn app.main:create_app --factory
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint

from .api.cooklog import COOK_LOG_WRITER_STATE_KEY, build_cook_log_router
from .api.envelope import api_error
from .api.pantry import build_pantry_router
from .api.recipes import build_recipes_router
from .api.resources import (
    MAPPING_STORE_STATE_KEY,
    PANTRY_CATALOG_STATE_KEY,
    PANTRY_STOCK_STATE_KEY,
    RECIPE_INDEX_STATE_KEY,
    ResourceUnavailable,
)
from .auth import install_security_middleware
from .config import ConfigurationError, Settings, validate_bind_invariant
from .cooklog.writer import CookingLogWriter
from .db.database import connect_db, init_db
from .mapping.store import CorruptMappingRow, IngredientMappingStore, MappingError
from .pantry.catalog import CatalogError, PantryCatalog
from .pantry.stock import PantryStockIndex
from .pwa_version import derive_version, install_pwa_version
from .recipes.reader import RecipeIndex
from .vault.atomic_write import AtomicNoteStore
from .vault.daily_paths import DailyNotePathPolicy
from .vault.pantry import PantryError

PACKAGE_ROOT = Path(__file__).parent
STATIC_ROOT = PACKAGE_ROOT / "static"

#: The `AtomicNoteStore` recovery root, as a **name** under `app_data_dir`.
#:
#: It is a name and not a `Settings` field on purpose. `Settings` validates what
#: the *operator* may point at, and this is not the operator's to point at: it is
#: derived data this app writes, beside `recipes.sqlite3`, and it must be outside
#: the vault (`AtomicNoteStore` refuses a recovery root inside it). A field would
#: be one more environment variable guarding something with exactly one correct
#: value. `AtomicNoteStore` **never creates a directory** — F4 removed the only
#: reason it had one — so the `lifespan` is obliged to `mkdir` it before
#: constructing the store, and `tests/api/test_lifespan.py` asserts both the
#: creation and that nothing is left behind on the exception path.
RECOVERY_DIRNAME = "vault-recovery"

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
        # The create/close sequence, in order, and the reason for each step is
        # on the function it calls. Every closer is appended the moment its
        # resource is constructed, so a failure *between* two constructions still
        # closes the ones already open — the alternative (a `try` per resource)
        # is how a partially-constructed boot leaks a descriptor.
        closers: list[Callable[[], None]] = []
        try:
            # 1. The PWA-owned database. First, because the mapping table the
            #    recipes router reads and the `cook_log_receipts` ledger the
            #    writer appends to both live in it, and a boot that served
            #    requests before the schema existed would answer "no such table".
            await init_db(runtime)

            # 2. `mkdir` the recovery root. `AtomicNoteStore` will not create it,
            #    and its absence is the difference between a boot and a
            #    `PathSafetyError("recovery_root_missing")`.
            recovery_root = runtime.app_data_dir / RECOVERY_DIRNAME
            recovery_root.mkdir(parents=True, exist_ok=True)

            # 3. The read-only catalog. **Not** `open()`ed eagerly: `PantryCatalog`
            #    opens on the first `snapshot()` by design, and forcing it here
            #    would turn a `mode=ro` failure into a process that refuses to
            #    start — replacing a 503 the UI can render ("your catalog is
            #    unreadable") with a crash it cannot. `close()` is still
            #    registered, because an unopened catalog is one the lifespan
            #    still owns.
            catalog = PantryCatalog(runtime)
            closers.append(catalog.close)

            # 4. The one store both vault readers share: the recipes' atomic note
            #    store is the same object the stock index reads `Pantry.md`
            #    through and the cook log writer writes daily notes through, so
            #    there is one set of pinned descriptors rather than three.
            store = AtomicNoteStore(runtime.vault_path, recovery_root)
            closers.append(store.close)

            # 5. The recipe index. Construction reads nothing — `Settings`
            #    validated that `recipes_root` is a safe *relative* path but not
            #    that it exists, so a missing folder raises on the first read and
            #    surfaces as 503 `missing_recipes_root` rather than an empty
            #    list that is indistinguishable from a wrong setting.
            recipes = RecipeIndex(runtime)
            closers.append(recipes.close)

            # 6. Pantry Stock. F11's 30 s TTL lives in `stock_cache_seconds` and
            #    is load-bearing: it is the maximum staleness a chip colour can
            #    have, because the user toggles a `Pantry.md` task from Obsidian
            #    while this PWA is open. Nothing is read here, so an unreadable
            #    `Pantry.md` still boots and still 503s per request.
            stock = PantryStockIndex.from_settings_with_catalog(runtime, store, catalog)
            closers.append(stock.close)

            # 7. The materialized mapping. It holds no connection (the factory
            #    opens and closes one per operation) and no catalog snapshot
            #    (`PantryCatalog` owns that TTL), so it is closed by not being
            #    closed — the uniform `close()` is on the store's collaborators
            #    only. §9.11.2's boot-time `catalog_changed` re-resolve runs
            #    below, and only when it is true: before any full pass, or after
            #    the producer re-imports and renumbers an `items.id`, which is
            #    the case a persisted id cannot detect (§7.3's substitute for
            #    the cross-database foreign key the schema cannot have).
            mappings = IngredientMappingStore(partial(connect_db, runtime), catalog.snapshot)
            if mappings.catalog_changed:
                try:
                    # `ensure_rows` **first**, and the order is not stylistic:
                    # `resolve_all` re-derives rows that already exist, so a boot
                    # that resolved an empty table would record the revision,
                    # flip `catalog_changed` false, and leave every slot
                    # `unresolved` for the life of the process — a fabricated
                    # `0/6` headline that nothing would ever repair.
                    await mappings.ensure_rows(recipes.snapshot().notes)
                    await mappings.resolve_all()
                except (CatalogError, ConfigurationError, MappingError, CorruptMappingRow, OSError):
                    # **Not fatal, on purpose.** The read path repeats exactly
                    # this check (`app/api/recipes.py::_current`), so a boot
                    # whose catalog is unreadable still serves, still answers 503
                    # `pantry_db_unreadable` per request, and repairs itself the
                    # moment the catalog becomes readable. Refusing to start
                    # would turn a state the UI can explain into a crash it
                    # cannot.
                    pass

            # 8. The Cooking Log writer (F4, D2). It writes the daily note and
            #    appends to `cook_log_receipts`, both through the same store and
            #    the same database. F5: it is online-only and is **not** wired
            #    into the offline outbox, and nothing here enqueues anything.
            writer = CookingLogWriter(
                store,
                DailyNotePathPolicy.from_settings(runtime),
                partial(connect_db, runtime),
            )

            application.state[RECIPE_INDEX_STATE_KEY] = recipes
            application.state[PANTRY_CATALOG_STATE_KEY] = catalog
            application.state[PANTRY_STOCK_STATE_KEY] = stock
            application.state[MAPPING_STORE_STATE_KEY] = mappings
            application.state[COOK_LOG_WRITER_STATE_KEY] = writer
            yield
        finally:
            # Reverse order, so a resource is closed before the one it borrows
            # from. Every close is a documented no-op except the catalog's
            # (the SQLite descriptor) and the store's (two pinned directory
            # descriptors); keeping the call uniform means a resource that grows
            # a descriptor later cannot be forgotten here.
            for close in reversed(closers):
                close()

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
    async def health() -> dict[str, object]:
        """Liveness, plus §9.19's five count groups.

        **Counts, never paths, and never a raise.** Each counter is computed
        independently and a source that cannot be read contributes `None` while
        the rest of the probe still answers — a `/health` that 500s because
        `Pantry.md` is missing would take down the one endpoint a user (or the
        LaunchAgent) can read to find out *why* the app is answering 503. `None`
        is the honest "not available" and is distinguishable from `0` ("readable,
        and empty"), which is exactly the distinction the fail-closed branch
        exists to preserve.

        The three original `readable` keys are unchanged and are still the cheap
        `os.access` answer, so an app whose `lifespan` never ran reports them
        without touching SQLite.

        The new groups are not free, and the bound on their cost is the readers'
        own TTLs rather than anything here: a recipe scan at most every 60 s, a
        pantry parse at most every 30 s, a catalog read at most every 300 s, and
        one short-lived SQLite connection for the count. §9.19 asks for counts
        precisely because counts are safe to publish on an unauthenticated probe —
        and this one still publishes no path.
        """
        recipes = _guard(lambda: recipe_counts(application))
        stock = _guard(lambda: stock_counts(application))
        return {
            "status": "ok",
            "version": APP_VERSION,
            "vault": {"readable": os.access(runtime.vault_path, os.R_OK)},
            "app_data": {"operational": os.access(runtime.app_data_dir, os.R_OK | os.W_OK)},
            "pantry_db": {
                "readable": os.access(runtime.pantry_items_db, os.R_OK),
                "rowCount": _guard(lambda: catalog_row_count(application)),
            },
            "recipes": {
                "count": recipes[0] if recipes is not None else None,
                "skipped": recipes[1] if recipes is not None else None,
            },
            "stock": {
                "openCount": stock[0] if stock is not None else None,
                "unjoinedLineCount": stock[1] if stock is not None else None,
            },
            "mappings": {
                "unresolvedCount": await _guard_async(lambda: unresolved_count(application))
            },
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

    # The domain routers, and the position is the whole point. They go after
    # `/js/{path}` (so the ESM injection route keeps winning for `/js/*`) and
    # BEFORE the `/api/{unmatched_path:path}` catch-all below, because a
    # catch-all route shadows anything registered after it. A router moved down
    # is a dead route, not a failing one.
    application.include_router(build_recipes_router())
    application.include_router(build_pantry_router())
    application.include_router(build_cook_log_router())

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
    """The scaffold's two-key envelope — a **delegation**, since #15.

    §9.19 says this function gains §9.15's additive optional detail, so the
    Cooking Log and the scaffold share one implementation. It cannot simply be
    *moved* here: `app/main.py` includes the router, and a router that imported
    `_api_error` from this module would hit
    `ImportError: cannot import name '_api_error' from partially initialized
    module 'app.main'` (#18 proved it). So the shared body lives in
    `app/api/envelope.py` — which imports nothing from `app` — and both sides
    delegate to it.

    The `Response` return annotation is unchanged, and that is the other half of
    the answer to "does `_api_error` widen or does `cook_log_error` narrow?":
    `envelope.api_error` returns a `JSONResponse`, which *is* a `Response`, so
    no annotation had to move and no call site changed.
    `tests/api/test_cooklog_api.py::test_the_two_key_envelope_is_key_for_key_the_scaffolds`
    is what proves the fold changed nothing observable.
    """
    return api_error(request, status, code)


def _guard[T](read: Callable[[], T]) -> T | None:
    """One health counter, or `None` when its source could not be read.

    Deliberately broad. This is a liveness probe over a file the user may have
    deleted: a `PantryError`, a `CatalogError`, a `ConfigurationError`, a missing
    resource because the `lifespan` never ran, and even an `OSError` all mean the
    same thing here — "this number is not available" — and none of them may stop
    the other five from being reported.
    """
    try:
        return read()
    except (PantryError, CatalogError, ConfigurationError, ResourceUnavailable, OSError):
        return None


async def _guard_async[T](read: Callable[[], Awaitable[T]]) -> T | None:
    """`_guard` for the one counter that needs the event loop. See its docstring."""
    try:
        return await read()
    except (
        PantryError,
        CatalogError,
        ConfigurationError,
        CorruptMappingRow,
        ResourceUnavailable,
        OSError,
        sqlite3.Error,
    ):
        return None


def catalog_row_count(application: FastAPI) -> int:
    """`pantry_db.rowCount` — the `items` table, not the projection.

    `CatalogSnapshot.total_row_count` is every row in the table, including the
    `area`-excluded ones the candidate universe drops, because a row entering or
    leaving that universe is a change worth seeing. The name says `pantry_db` and
    the number is the table's.
    """
    catalog = _published(application, PANTRY_CATALOG_STATE_KEY, PantryCatalog)
    return catalog.snapshot().total_row_count


def recipe_counts(application: FastAPI) -> tuple[int, int]:
    """`(recipes.count, recipes.skipped)` — the index's own snapshot numbers.

    `skipped` is on `RecipeSnapshot` precisely so a note that vanished from the
    list can be told apart from one that was never there; §5 requires it on
    `/health` and it is published as a count, never as a path.
    """
    snapshot = _published(application, RECIPE_INDEX_STATE_KEY, RecipeIndex).snapshot()
    return len(snapshot.notes), snapshot.skipped


def stock_counts(application: FastAPI) -> tuple[int, int]:
    """`(stock.openCount, stock.unjoinedLineCount)`.

    `openCount` is the parser's `PantrySnapshot.total` — the figure the note's own
    `库存总览` dataviewjs prints, where a `k/N` unit counts as its own row.
    `unjoinedLineCount` is the Stock Join's miss count, whose denominator is 45
    product lines on the same note. Both are reported rather than reconciled by
    fiat; see `PantryStockIndex.open_count`'s docstring.
    """
    stock = _published(application, PANTRY_STOCK_STATE_KEY, PantryStockIndex)
    return stock.open_count, stock.unjoined_line_count


async def unresolved_count(application: FastAPI) -> int:
    """`mappings.unresolvedCount` — §7.3's repair-queue size.

    The one counter that touches the PWA-owned database, hence the one that needs
    `await`. It is the count §9.10's "re-resolve the unresolved ones" action
    acts on, and it is the number a user watches to see a mapping repair land.
    """
    store = _published(application, MAPPING_STORE_STATE_KEY, IngredientMappingStore)
    return len(await store.unresolved_rows())


def _published[R](application: FastAPI, key: str, expected: type[R]) -> R:
    value = getattr(application.state, key, None)
    if not isinstance(value, expected):
        raise ResourceUnavailable(key)
    return value

"""The shared pieces of the Cooking Log test harness.

**These are plain functions, not fixtures.** Each conftest — and the API test
module in `tests/api/` — wraps them in its own `@pytest.fixture`. That is
deliberate: a fixture exported by `from … import name` and then used as a test
parameter is a redefinition to `ruff`, and a fixture re-declared in two conftests
is a second copy that can drift. Plain functions plus explicit wrappers give one
implementation, one name per module, and no import trickery.

**Nothing here reads a live path.** The vault is built per test under `tmp_path`,
the `AtomicNoteStore`'s recovery root is a sibling directory outside that vault,
and the receipts database is a real SQLite file created by the shipped
`init_db()` — so the schema, `ux_cook_log_receipts_recipe_date`, and the pragmas
are the real ones rather than a hand-written stand-in. A test that needs a
particular daily note writes it by hand from the bytes in `tests/cooklog/notes.py`.

**The security middleware is the shipped one, installed outermost**, exactly as
`app/main.py` does, so `read_only`, Origin, CSRF, the 1 MiB body cap, and the
host guard are the real guards and not a test-only imitation. The two exception
handlers are `app.main._api_error` verbatim, so the envelope under test is the
envelope the app emits.

`app/main.py` does **not** register the Cooking Log router — that registration
belongs between `/js/{path}` and the `/api/{unmatched_path}` catch-all and is
#15's wiring — so this harness is what makes the routes reachable.
`tests/api/test_cooklog_api.py` asserts the two-key envelope is key-for-key
identical to `_api_error`'s and that F4's extension is the only thing added, which
is what keeps that harness honest rather than a second, divergent app. It also
splices the same router into the **real** `create_app` at §9.19's position
(`mount_cook_logs`), because a router that no request can reach has not been
tested; that splice is a no-op once the wiring lands.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.cooklog import COOK_LOG_WRITER_STATE_KEY, build_cook_log_router
from app.auth import install_security_middleware
from app.config import Settings
from app.cooklog.writer import CookingLogWriter
from app.db.database import connect_db, init_db
from app.main import _api_error
from app.vault.atomic_write import AtomicNoteStore
from app.vault.daily_paths import DailyNotePathPolicy

from .notes import DAILY_NOTE_PATH, daily_note_bytes

ORIGIN = "https://recipes.test.invalid"
OWNER = "owner@test.invalid"

#: The documented convention, and the reason the fixture has to create it:
#: `AtomicNoteStore` **never creates a directory**, so whatever opens the store
#: has to `mkdir` this first. That is a `lifespan` obligation (wiring), not a
#: writer obligation, and creating it here is how the tests keep saying so.
RECOVERY_DIRNAME = "vault-recovery"


def anyio_backend() -> str:
    """`anyio`'s pytest plugin is already registered through FastAPI.

    Nothing new is installed for the async writer tests: `anyio` ships a
    `pytest11` entry point, and every module opts in with
    `pytestmark = pytest.mark.anyio` rather than the suite growing a
    `pytest-asyncio` dependency it does not otherwise have.
    """
    return "asyncio"


def make_settings(runtime_root: Path) -> Settings:
    return Settings.from_mapping(
        {
            "OBSIDIAN_VAULT_PATH": str(runtime_root / "vault"),
            "APP_DATA_DIR": str(runtime_root / "data"),
            "PANTRY_ITEMS_DB": str(runtime_root / "pantry_items.db"),
            "PUBLIC_ORIGIN": ORIGIN,
            "TAILSCALE_OWNER_LOGIN": OWNER,
            "DEV_IDENTITY": OWNER,
            "BIND_HOST": "127.0.0.1",
            "APP_TIMEZONE": "America/New_York",
            "DAILY_NOTES_ROOT": "日记",
            "DAILY_NOTES_YEAR_POLICY": "2020-2030",
        }
    )


def make_recovery_root(settings: Settings) -> Path:
    root = settings.app_data_dir / RECOVERY_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def write_note(vault: Path, source: bytes, relative: str = DAILY_NOTE_PATH) -> bytes:
    """Write a daily note by hand, byte for byte, and return what was written."""
    target = vault / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source)
    return source


def open_store(vault: Path, recovery_root: Path) -> AtomicNoteStore:
    return AtomicNoteStore(vault, recovery_root)


def initialise_db(settings: Settings) -> None:
    """Run the shipped `init_db()` so `cook_log_receipts` and its index are real.

    Its own event loop, closed before it returns, so the only connections the
    writer opens afterwards are the ones it opens and closes itself, on whatever
    loop is serving the request.
    """
    asyncio.run(init_db(settings))


def writer_for(settings: Settings, store: AtomicNoteStore) -> CookingLogWriter:
    """A writer over the real store, the real path policy, and a real database."""
    return CookingLogWriter(
        store, DailyNotePathPolicy.from_settings(settings), partial(connect_db, settings)
    )


def racing_writer(
    settings: Settings,
    vault: Path,
    recovery_root: Path,
    *,
    race_hook: Callable[[str], None] | None = None,
    store_factory: Callable[..., AtomicNoteStore] = AtomicNoteStore,
) -> tuple[CookingLogWriter, AtomicNoteStore]:
    """A second writer over its own store, so a test can inject without poking.

    `AtomicNoteStore`'s `race_hook` is the seam §10.1 requires for races — "races
    need hooks, not luck" — and it is a constructor argument, so a racing writer
    is an ordinary instance rather than a live store with a private attribute
    overwritten on it. `store_factory` exists for the other half: a store whose
    `read_existing_if_exists` lies about absence, which is the only way to reach
    F4's second read deterministically.
    """
    opened = store_factory(vault, recovery_root, race_hook=race_hook)
    return writer_for(settings, opened), opened


def build_app(
    settings: Settings,
    *,
    writer: CookingLogWriter | None = None,
    runtime: Settings | None = None,
) -> FastAPI:
    """The Cooking Log app, with the shipped guards and the shipped envelope."""
    application = FastAPI()
    effective = runtime or settings
    application.state.settings = effective
    if writer is not None:
        application.state[COOK_LOG_WRITER_STATE_KEY] = writer

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        del exc
        return _api_error(request, 422, "invalid_request")

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "http_error"
        return _api_error(request, exc.status_code, code)

    application.include_router(build_cook_log_router())
    # Security last, so it is outermost: it must be able to reject before any
    # handler parses request.url, exactly as in app/main.py.
    install_security_middleware(application, effective)
    return application


def make_app_factory(
    settings: Settings, writer: CookingLogWriter
) -> Callable[..., FastAPI]:
    """A builder whose default is the fully wired app; `with_writer=False` is the
    state `app/main.py` is in until #15's `lifespan` publishes one."""

    def build(**kwargs: object) -> FastAPI:
        return build_app(settings, writer=writer, **kwargs)  # type: ignore[arg-type]

    return build


#: §9.19's registration point, named so the splice below cannot drift from the
#: documented order: the domain routers go after `/js/{path}` and **before** the
#: `/api/{unmatched_path:path}` catch-all, because a catch-all route shadows
#: anything registered after it.
CATCH_ALL_PATH: Final = "/api/{unmatched_path:path}"


def mount_cook_logs(application: FastAPI, writer: CookingLogWriter) -> FastAPI:
    """Put the Cooking Log routes on the **real** app, where §9.19 says they go.

    `app/main.py` is not this ticket's to edit, so the two `TODO(implementation)`
    markers are still there and `create_app` mounts no domain router. This
    inserts the router's own routes at the documented position in the app's
    route table, which is the part that cannot be unit-tested any other way:
    whether the route is reachable at all, whether `/api/{unmatched_path}`
    shadows it, whether the static mount registered last still serves `/`, and
    whether the real `cache_policy` and `/api/session` reach the route.

    It is a splice rather than `include_router` because the catch-all and the
    static mount are already registered by then, and `include_router` appends —
    which would be the wrong order and would produce a test that passes for the
    wrong reason.

    **Idempotent by design:** if the routes are already registered, this does
    nothing and the test exercises the real registration. The same test
    therefore holds before and after the wiring lands, and nothing here has to
    be undone when it does.
    """
    application.state[COOK_LOG_WRITER_STATE_KEY] = writer
    table = application.router.routes
    if any(getattr(route, "path", None) == "/api/cook-logs" for route in table):
        return application
    index = next(
        position
        for position, route in enumerate(table)
        if getattr(route, "path", None) == CATCH_ALL_PATH
    )
    table[index:index] = list(build_cook_log_router().routes)
    return application


def client_for(application: FastAPI) -> TestClient:
    """A `TestClient` on the public origin, which is what the host guard requires."""
    return TestClient(application, base_url=ORIGIN)


#: Documentation only. A test that needs one of these imports it by name from
#: `cooklog.harness`, which is what keeps the fixture wrappers in each conftest
#: explicit about what they depend on.
__all__ = [
    "CATCH_ALL_PATH",
    "DAILY_NOTE_PATH",
    "ORIGIN",
    "anyio_backend",
    "build_app",
    "racing_writer",
    "client_for",
    "connect_db",
    "daily_note_bytes",
    "initialise_db",
    "make_app_factory",
    "make_recovery_root",
    "make_settings",
    "mount_cook_logs",
    "open_store",
    "writer_for",
    "write_note",
]

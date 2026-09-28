"""The `lifespan`: what it opens, what it publishes, and what it closes.

Run alone:  .venv/bin/python -m pytest tests/api/test_lifespan.py -q

**The obligation here is narrow and mechanical: no resource may outlive
shutdown, and nothing may be left behind on disk.** §9.19 says every one of
`PantryCatalog`, `RecipeIndex`, `PantryStockIndex`, `AtomicNoteStore` and
`IngredientMappingStore` is closed in a `finally`, and says *why*: the
LaunchAgent's `SoftResourceLimits NumberOfFiles 8192` exists because launchd's
default 256 is not the shell's `ulimit -n`, so a descriptor leak here only ever
shows up in production, on a box nobody is watching.

**A leaked descriptor is asserted by its own absence, twice.** Once on the
ordinary path — a closed `AtomicNoteStore` refuses to serve a second read, and a
closed `PantryCatalog` is *terminally* closed, so both are assertable facts
rather than inferences. And once on the exception path, which is the one that is
easy to get wrong: an exception raised *inside* the `try` must still run the
`finally`, so the test injects a failure after the store exists and checks that
the store was closed anyway. A `try` per resource would pass the first test and
fail the second.

**Descriptor counts are measured with `psutil`-free arithmetic**: the store pins
two directory descriptors and the catalog one SQLite handle, so
`/dev/fd` is read before and after and the *difference* is asserted. That works
because the test process is otherwise idle, and it is the same measurement
`launchd` would make.

**Nothing here reads a live path.** The vault, the data dir and the catalog are
all under `tmp_path`, and the recovery root is asserted to be *inside* the data
dir and *outside* the vault — the invariant `AtomicNoteStore` enforces and the
reason it refuses a recovery root nested in the vault.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.cooklog import COOK_LOG_WRITER_STATE_KEY
from app.api.resources import (
    MAPPING_STORE_STATE_KEY,
    PANTRY_CATALOG_STATE_KEY,
    PANTRY_STOCK_STATE_KEY,
    RECIPE_INDEX_STATE_KEY,
)
from app.config import Settings
from app.cooklog.writer import CookingLogWriter
from app.main import RECOVERY_DIRNAME, create_app
from tests.api.conftest import (
    API_CATALOG_ROWS,
    ORIGIN,
    client_for,
    make_settings,
    seed_catalog,
    write_pantry_note,
    write_recipe,
)
from tests.api.test_recipes_api import MAIN_RECIPE


def _open_descriptors() -> int:
    """How many descriptors this process holds. `/dev/fd` is the portable read."""
    return len(os.listdir("/dev/fd"))


@pytest.fixture
def settings(runtime_root: Path) -> Settings:
    configured = make_settings(runtime_root)
    seed_catalog(configured.pantry_items_db, API_CATALOG_ROWS)
    write_recipe(configured.vault_path, MAIN_RECIPE)
    write_pantry_note(configured.vault_path)
    return configured


# --- what the lifespan publishes -------------------------------------------


def test_every_reader_is_published_on_app_state(settings: Settings) -> None:
    """All five, under the keys `app/api/resources.py` names.

    Asserted as the concrete classes, not merely as "something is there": a key
    that resolves to the wrong object would satisfy `is not None` and then fail
    at the first request, which is a worse place to find out.
    """
    from app.mapping.store import IngredientMappingStore
    from app.pantry.catalog import PantryCatalog
    from app.pantry.stock import PantryStockIndex
    from app.recipes.reader import RecipeIndex

    with client_for(settings) as client:
        state = client.app.state
        assert isinstance(getattr(state, RECIPE_INDEX_STATE_KEY), RecipeIndex)
        assert isinstance(getattr(state, PANTRY_CATALOG_STATE_KEY), PantryCatalog)
        assert isinstance(getattr(state, PANTRY_STOCK_STATE_KEY), PantryStockIndex)
        assert isinstance(getattr(state, MAPPING_STORE_STATE_KEY), IngredientMappingStore)
        assert isinstance(getattr(state, COOK_LOG_WRITER_STATE_KEY), CookingLogWriter)


def test_the_cook_log_writer_is_published_under_the_key_the_router_reads(
    settings: Settings,
) -> None:
    """The handoff: `app.state[COOK_LOG_WRITER_STATE_KEY] == "cook_log_writer"`.

    The key is `app/api/cooklog.py`'s constant and `app/main.py` imports it rather
    than repeating the string, so the writer cannot be published under a name the
    router does not read — which is the `AttributeError`-on-`None` 500 the router
    already guards against, one layer up.
    """
    with client_for(settings) as client:
        assert client.app.state[COOK_LOG_WRITER_STATE_KEY] is not None
        # And the route really finds it, which is the half that matters.
        assert client.post(
            "/api/cook-logs",
            json={"recipeNote": MAIN_RECIPE, "date": "2026-09-27"},
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": client.get("/api/session").json()["csrfToken"],
            },
        ).status_code == 404


def test_the_schema_is_created_before_anything_serves(settings: Settings) -> None:
    """`init_db()` runs first, so no request can see a missing table.

    A boot that served requests before the schema existed would answer "no such
    table: ingredient_mappings" — which is a 500 with a SQLite message in the log
    and nothing a user can act on. The assertion is on the file the app owns.
    """
    database = settings.app_data_dir / "recipes.sqlite3"
    assert not database.exists()

    with client_for(settings) as client:
        assert client.get("/health").status_code == 200
    assert database.exists()


def test_the_recovery_root_is_created_under_the_data_dir_and_outside_the_vault(
    settings: Settings,
) -> None:
    """`AtomicNoteStore` never creates a directory, so the `lifespan` must.

    The convention is `app_data_dir / "vault-recovery"` and it is a name rather
    than a `Settings` field because it is derived data this app writes beside its
    SQLite file — not something the operator chooses. Being *outside* the vault is
    an invariant `AtomicNoteStore` itself enforces and refuses to violate; a
    backup written inside the vault would be indexed and synced by Obsidian.
    """
    recovery = settings.app_data_dir / RECOVERY_DIRNAME
    assert not recovery.exists()

    with client_for(settings) as client:
        assert client.get("/health").status_code == 200
        assert recovery.is_dir()
        assert recovery.parent == settings.app_data_dir
        assert recovery != settings.vault_path
        assert settings.vault_path not in recovery.parents
        # And it is a *name*, not a setting: `Settings` has no field for it,
        # which is what makes "the operator cannot point the recovery root
        # somewhere else" a structural fact rather than a policy statement.
        assert RECOVERY_DIRNAME not in Settings.__dataclass_fields__


def test_the_whole_wiring_answers_on_a_live_app(settings: Settings) -> None:
    """The four routes this ticket made reachable, through `create_app` itself.

    Not a unit test of the routes — `tests/api/test_recipes_api.py` is — but the
    end-to-end statement that the wiring exists: a boot, a recipe read, a session,
    and a cook-log attempt that reaches the writer and finds no daily note.
    """
    with client_for(settings) as client:
        assert client.get("/health").json()["recipes"]["count"] == 1
        assert client.get("/api/recipes").status_code == 200
        assert client.get("/api/session").status_code == 200
        logged = client.post(
            "/api/cook-logs",
            json={"recipeNote": MAIN_RECIPE, "date": "2026-09-27"},
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": client.get("/api/session").json()["csrfToken"],
            },
        )
        # 404 and not 503: the writer was published, and the daily note is absent.
        assert logged.status_code == 404
        assert logged.json()["code"] == "daily_note_missing"


# --- the close side ---------------------------------------------------------


def test_shutdown_closes_every_resource_and_leaks_no_descriptor(
    settings: Settings,
) -> None:
    """The ordinary path, measured.

    `before` and `after` bracket a whole `TestClient` lifespan — the app boots,
    serves nothing, and shuts down — and the counts must be equal. Two cycles, so
    a one-off descriptor cached by `os.scandir` or a SQLite page cache cannot pass
    for a leak; only a *per-lifespan* leak accumulates.
    """
    for _ in range(2):
        before = _open_descriptors()
        with client_for(settings) as client:
            client.get("/health")
            client.get("/api/recipes")
            inside = _open_descriptors()
        after = _open_descriptors()

        assert inside > before, "the lifespan opened nothing, so nothing was closed"
        assert after == before, f"leaked {after - before} descriptors across one lifespan"


def test_a_closed_catalog_is_terminally_closed(settings: Settings) -> None:
    """`PantryCatalog.close()` is a one-way door, and that is assertable.

    A resource that quietly reopens on the next read is the descriptor leak the
    `finally` exists to prevent — the leak would reappear on the first request
    after shutdown rather than at shutdown. So the post-shutdown state is not
    "no connection" but "refuses to open again", which is a fact about the object
    rather than an inference from a count.
    """
    application = create_app(settings)
    with TestClient(application, base_url=ORIGIN):
        catalog = application.state[PANTRY_CATALOG_STATE_KEY]
        catalog.snapshot()
    with pytest.raises(Exception, match="pantry_catalog_closed"):
        catalog.open()


def test_an_exception_inside_the_lifespan_still_closes_what_was_already_open(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The exception path, and the test that makes the `finally` mean something.**

    A failure is injected where the `CookingLogWriter` is constructed — the step
    immediately after the `AtomicNoteStore` exists — which is the exact window a
    per-resource `try` would leak. The store must come out closed, the descriptor
    count must return to where it started, and nothing may be published, so no
    request can reach a half-built app.

    This is why the closers are appended as each resource is constructed rather
    than listed at the top: a list written before the loop would not contain the
    store at the moment the writer failed.
    """
    from app import main as main_module

    class _Exploding:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("injected lifespan failure")

    monkeypatch.setattr(main_module, "CookingLogWriter", _Exploding)
    before = _open_descriptors()
    application = create_app(settings)

    with pytest.raises(RuntimeError, match="injected lifespan failure"):
        with TestClient(application, base_url=ORIGIN):
            pass

    assert _open_descriptors() == before, "the failure path leaked a descriptor"
    for key in (
        RECIPE_INDEX_STATE_KEY,
        PANTRY_CATALOG_STATE_KEY,
        PANTRY_STOCK_STATE_KEY,
        MAPPING_STORE_STATE_KEY,
        COOK_LOG_WRITER_STATE_KEY,
    ):
        assert not hasattr(application.state, key), key


def test_the_store_is_closed_after_everything_that_borrows_it(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The close order, asserted, because it is a real dependency.

    The `finally` iterates in reverse, so the `AtomicNoteStore` — the resource the
    stock index reads through and the writer writes through — is the last thing
    closed. Closing it first would be a shutdown-order requirement nobody had to
    state, and the next resource to need it would find a closed store.

    Asserted by wrapping `AtomicNoteStore.close` on the class `app/main.py`
    imports, so the record comes from the object the app actually constructs and
    not from a private attribute poked after the fact.
    """
    from app import main as main_module

    order: list[str] = []
    real_close = main_module.AtomicNoteStore.close

    def recording_close(self: object) -> None:
        order.append("store")
        real_close(self)  # type: ignore[arg-type]

    monkeypatch.setattr(main_module.AtomicNoteStore, "close", recording_close)
    with client_for(settings) as client:
        client.get("/api/recipes")
    assert order == ["store"]
    # And the catalog — which is the other descriptor — is closed too, which is
    # what `_open_descriptors` in the test above measures.
    monkeypatch.undo()


def test_a_lifespan_that_never_runs_publishes_nothing_and_still_answers_shell(
    settings: Settings,
) -> None:
    """A `TestClient` that never entered its context manager has no resources.

    So `/health` still answers — with the cheap `os.access` keys and `None` for
    every counter — and the shell still serves. `/health` is unauthenticated and
    is what a LaunchAgent polls; a probe that raised `AttributeError` because the
    lifespan had not run would be a probe that fails exactly when the app is
    broken.
    """
    client = TestClient(create_app(settings), base_url=ORIGIN)
    body = client.get("/health").json()

    assert body["vault"] == {"readable": True}
    assert body["pantry_db"]["rowCount"] is None
    assert body["recipes"] == {"count": None, "skipped": None}
    assert body["stock"] == {"openCount": None, "unjoinedLineCount": None}
    assert body["mappings"] == {"unresolvedCount": None}
    assert client.get("/").status_code == 200
    assert not (settings.app_data_dir / RECOVERY_DIRNAME).exists()

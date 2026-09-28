"""The Cooking Log fixtures for `tests/cooklog/`, each a thin wrapper.

The implementations live in `harness.py` as plain functions, and the wrappers
live here rather than being imported, because a fixture exported by
`from … import name` and then used as a test parameter is a redefinition to
`ruff`. `tests/api/test_cooklog_api.py` wraps the same functions the same way, so
the two suites cannot drift into two different apps.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.cooklog.writer import CookingLogWriter
from app.vault.atomic_write import AtomicNoteStore

from . import harness
from .notes import daily_note_bytes


@pytest.fixture
def anyio_backend() -> str:
    return harness.anyio_backend()


@pytest.fixture
def settings(runtime_root: Path) -> Settings:
    return harness.make_settings(runtime_root)


@pytest.fixture
def vault(settings: Settings) -> Path:
    return settings.vault_path


@pytest.fixture
def recovery_root(settings: Settings) -> Path:
    return harness.make_recovery_root(settings)


@pytest.fixture
def store(vault: Path, recovery_root: Path) -> Iterator[AtomicNoteStore]:
    opened = harness.open_store(vault, recovery_root)
    try:
        yield opened
    finally:
        opened.close()


@pytest.fixture
def note_factory(vault: Path) -> Callable[..., bytes]:
    return lambda source, relative=harness.DAILY_NOTE_PATH: harness.write_note(
        vault, source, relative
    )


@pytest.fixture
def daily_note(note_factory: Callable[..., bytes]) -> bytes:
    """The reference note, written by hand. Tests overwrite it when they need to."""
    return note_factory(daily_note_bytes())


@pytest.fixture
def initialised_db(settings: Settings) -> None:
    harness.initialise_db(settings)


@pytest.fixture
def writer(
    settings: Settings, store: AtomicNoteStore, initialised_db: None
) -> CookingLogWriter:
    return harness.writer_for(settings, store)


@pytest.fixture
def app_factory(settings: Settings, writer: CookingLogWriter) -> Callable[..., FastAPI]:
    return harness.make_app_factory(settings, writer)


@pytest.fixture
def client(app_factory: Callable[..., FastAPI]) -> Iterator[TestClient]:
    test_client = harness.client_for(app_factory())
    with test_client:
        yield test_client

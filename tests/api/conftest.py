"""Settings factories and raw (unauthorized) clients for the security tests.

These clients are deliberately *raw*: they carry the public origin as the Host
so the host guard passes, and nothing else. A test that needs a satisfied guard
adds the header itself, so "which guard rejected this" stays visible in the
test body instead of hiding in a client subclass.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ORIGIN = "https://recipes.test.invalid"
OWNER = "owner@test.invalid"
ATTACKER = "attacker@evil.invalid"


def make_settings(runtime_root: Path, **overrides: str) -> Settings:
    """Development-mode settings unless an override says otherwise."""
    values: dict[str, str] = {
        "OBSIDIAN_VAULT_PATH": str(runtime_root / "vault"),
        "APP_DATA_DIR": str(runtime_root / "data"),
        "PANTRY_ITEMS_DB": str(runtime_root / "pantry_items.db"),
        "PUBLIC_ORIGIN": ORIGIN,
        "TAILSCALE_OWNER_LOGIN": OWNER,
    }
    values.update(overrides)
    return Settings.from_mapping(values)


@pytest.fixture
def dev_settings(runtime_root: Path) -> Settings:
    return make_settings(runtime_root)


@pytest.fixture
def prod_settings(runtime_root: Path) -> Settings:
    """Trusted-header mode: identity comes from Tailscale, not from the app."""
    return make_settings(runtime_root, TRUST_TAILSCALE_HEADERS="true")


@pytest.fixture
def read_only_settings(runtime_root: Path) -> Settings:
    return make_settings(runtime_root, OBSIDIAN_READ_ONLY="true")


@pytest.fixture
def prod_read_only_settings(runtime_root: Path) -> Settings:
    return make_settings(runtime_root, TRUST_TAILSCALE_HEADERS="true", OBSIDIAN_READ_ONLY="true")


def client_for(settings: Settings) -> TestClient:
    return TestClient(create_app(settings), base_url=ORIGIN)


@pytest.fixture
def dev_client(dev_settings: Settings) -> Iterator[TestClient]:
    with client_for(dev_settings) as test_client:
        yield test_client


@pytest.fixture
def read_only_client(read_only_settings: Settings) -> Iterator[TestClient]:
    with client_for(read_only_settings) as test_client:
        yield test_client


@pytest.fixture
def prod_client(prod_settings: Settings) -> Iterator[TestClient]:
    with client_for(prod_settings) as test_client:
        yield test_client


@pytest.fixture
def prod_read_only_client(prod_read_only_settings: Settings) -> Iterator[TestClient]:
    with client_for(prod_read_only_settings) as test_client:
        yield test_client

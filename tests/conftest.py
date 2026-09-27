"""Test configuration: an isolated vault, data dir, and pantry catalog per test.

The app refuses to start without a real `PANTRY_ITEMS_DB` file (a misconfigured
path must never masquerade as an empty pantry), so each test gets a real
SQLite file with the wholefoods-to-pantry `items` schema and one seeded row.
No test ever touches the production vault or the real catalog.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

TEST_ORIGIN = "https://recipes.test.invalid"

# Mirrors wholefoods-to-pantry assets/pantry_items.db. Column order and types
# are copied from that project's schema; it is a READ-ONLY producer asset, so
# this app never writes it.
_ITEMS_SCHEMA = """
CREATE TABLE items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name TEXT NOT NULL UNIQUE,
    category       TEXT NOT NULL,
    variants       TEXT DEFAULT '[]',
    first_seen     TEXT,
    last_seen      TEXT,
    order_count    INTEGER DEFAULT 1,
    area           TEXT,
    last_price     REAL
)
"""


def _seed_pantry_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(_ITEMS_SCHEMA)
        connection.execute(
            "INSERT INTO items (canonical_name, category, variants, order_count, last_price)"
            " VALUES (?, ?, ?, ?, ?)",
            ("Bell & Evans Chicken Breast, 8 Oz", "1.1", "[]", 3, 6.99),
        )
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def runtime_root(tmp_path: Path) -> Path:
    """A throwaway OBSIDIAN_VAULT_PATH + APP_DATA_DIR + PANTRY_ITEMS_DB tree."""
    vault = tmp_path / "vault"
    data = tmp_path / "data"
    (vault / "日记" / "2026").mkdir(parents=True)
    (vault / "Hobbies" / "做饭" / "Recipes").mkdir(parents=True)
    data.mkdir()
    _seed_pantry_db(tmp_path / "pantry_items.db")
    return tmp_path


@pytest.fixture
def settings(runtime_root: Path) -> Settings:
    return Settings.from_mapping(
        {
            "OBSIDIAN_VAULT_PATH": str(runtime_root / "vault"),
            "APP_DATA_DIR": str(runtime_root / "data"),
            "PANTRY_ITEMS_DB": str(runtime_root / "pantry_items.db"),
            "PUBLIC_ORIGIN": TEST_ORIGIN,
            "TAILSCALE_OWNER_LOGIN": "owner@test.invalid",
            "DEV_IDENTITY": "owner@test.invalid",
            "BIND_HOST": "127.0.0.1",
            "APP_TIMEZONE": "America/New_York",
            "TRUST_TAILSCALE_HEADERS": "false",
        }
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """A TestClient with the lifespan running, so /health sees real state."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client

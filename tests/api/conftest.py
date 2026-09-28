"""Settings factories and raw (unauthorized) clients for the security tests.

These clients are deliberately *raw*: they carry the public origin as the Host
so the host guard passes, and nothing else. A test that needs a satisfied guard
adds the header itself, so "which guard rejected this" stays visible in the
test body instead of hiding in a client subclass.

**The domain fixtures below are all built from committed bytes under `tmp_path`.**
Nothing here reads a live path, and that is the property the whole suite is
built to keep: the recipe notes and the `Pantry.md` are written by hand from the
literals in this module, and the catalog is a real SQLite file with real rows.
The only thing these fixtures do *not* do is use the production vault, and the
only thing they assert about the real data is its shape, never its contents.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ORIGIN = "https://recipes.test.invalid"
OWNER = "owner@test.invalid"
ATTACKER = "attacker@evil.invalid"

#: `Settings.pantry_note_relative`'s default, spelled out so a test that moves or
#: breaks the note names the server-owned path rather than repeating a literal.
PANTRY_NOTE = "Logistics/库存/Pantry.md"
RECIPES_ROOT = "Hobbies/做饭/Recipes"

#: The catalog every domain test runs against: three products a fixture recipe
#: resolves to, one condiment that only the staples tier ever reaches, one spare
#: product no fixture slot claims (so a manual re-map has somewhere legal to point),
#: and one `area`-excluded row so the reader's filter is exercised by the same
#: fixture. `番茄` and `鸡蛋` are exact-name matches, `空心菜` needs the synonym
#: tier to reach `空心菜嫩苗` (3), and `🐟 不存在的鱼` matches nothing at all.
API_CATALOG_ROWS: tuple[tuple[int, str, str, str, str | None], ...] = (
    (1, "番茄", "1.1", "[]", None),
    (2, "鸡蛋", "1.1d", "[]", None),
    (3, "空心菜嫩苗", "1.1", "[]", None),
    (4, "李锦记 蒸鱼豉油 14 盎司", "1.1c", "[]", None),
    (5, "Shampoo", "3.1", "[]", "Shampoo"),
    (6, "豆腐", "1.1", "[]", None),
)

#: The one recipe most of the domain tests read. Three Materials resolve and one
#: does not, so the default headline is `3/4`; two Seasonings, both satisfied by
#: `staples.yaml`, so the strict headline is `5/6` — which is F16's whole point,
#: asserted as arithmetic rather than as prose.
MAIN_RECIPE: str = "番茄炒蛋"
MAIN_RECIPE_BYTES: bytes = (
    "---\n"
    "材料:\n"
    "  - 番茄\n"
    "  - 鸡蛋\n"
    "  - 空心菜\n"
    "  - 🐟 不存在的鱼\n"
    "调料:\n"
    "  - 生抽\n"
    "  - 盐\n"
    "烹饪工具: 炒锅\n"
    "来源:\n"
    "  - 测试夹具\n"
    "first_cooked: 2026-03-01\n"
    "last_cooked: 2026-09-20\n"
    "cooking_count: 7\n"
    "auto_updated: 2026-09-21 12:00\n"
    "---\n"
    "# 步骤\n"
    "1. 热锅。\n"
    "2. 炒蛋盛出。\n"
    "3. 炒番茄，回锅。\n"
).encode()

#: F2's data defect, as a real note: one recipe listing the same Pantry Item
#: twice. The per-slot `IntegrityError` catch downgrades the *second* slot and
#: the first still commits, which is the whole of what F2's override buys.
DUPLICATE_RECIPE: str = "番茄炒蛋重复"
DUPLICATE_RECIPE_BYTES: bytes = (
    "---\n"
    "材料:\n"
    "  - 番茄\n"
    "  - 番茄\n"
    "调料:\n"
    "  - 盐\n"
    "---\n"
    "# 步骤\n"
    "1. 炒。\n"
).encode()

#: A `Pantry.md` with one open line per resolved product, one *done* line for the
#: product `空心菜` resolves to, and two lines nothing in the catalog explains.
#:
#: The `[x] 空心菜嫩苗` line is the load-bearing one: the recipe resolves
#: `空心菜` to id 3, and because that line is done the Stock Join never sees it,
#: so the slot publishes `inStock: false` **and** `stockJoinState: "unresolved"`.
#: That is F1's measured evidence in one fixture — a catalog-only answer would
#: render it `chip--in-stock` — and it is why `stockJoinState` is not derived from
#: `inStock` and the two are not the same field.
PANTRY_NOTE_BYTES: bytes = (
    "---\n"
    "modified_at: 2026-09-27\n"
    "---\n"
    "# 1 冰箱\n"
    "- [ ] 番茄\n"
    "- [ ] 鸡蛋\n"
    "- [x] 空心菜嫩苗\n"
    "\n"
    "# 2 干货\n"
    "- [ ] 🐟 不存在的鱼\n"
    "- [ ] 完全不存在的商品\n"
).encode()


def write_recipe(vault: Path, name: str, source: bytes = MAIN_RECIPE_BYTES) -> Path:
    """Write one recipe note under `RECIPES_ROOT` and return the file."""
    target = vault / RECIPES_ROOT / f"{name}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source)
    return target


def write_pantry_note(vault: Path, source: bytes = PANTRY_NOTE_BYTES) -> Path:
    """Write `Pantry.md` at the server-owned relative path and return the file."""
    target = vault / PANTRY_NOTE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source)
    return target


def seed_catalog(db_path: Path, rows: tuple[tuple[int, str, str, str, str | None], ...]) -> None:
    """Replace the `items` table's contents with `rows`, keeping the real schema.

    `tests/conftest.py`'s `runtime_root` already created the file with
    wholefoods-to-pantry's own column list, so this inserts into the real table
    rather than creating a stand-in — a domain test that read a different schema
    would not be testing the reader.
    """
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("DELETE FROM items")
        connection.executemany(
            "INSERT INTO items (id, canonical_name, category, variants, area)"
            " VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()


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
    """Trusted-header mode: identity comes from Tailscale, not the app."""
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

"""Gates for the PWA-owned SQLite layer: schema, pragmas, and the ledger.

Run alone:  .venv/bin/python -m pytest tests/db/test_migrations.py -q

These assert the four failure modes the data layer has that no other suite can
reach:

  1. A silently-short schema. `executescript` runs at every startup, so a
     missing table is not a crash on a fresh install — it is a table that
     appears once a later code path touches it, months later.
  2. A missing pragma. All six are per-connection, so a second connection
     opened anywhere in the app is a second, differently-configured database
     as far as SQLite is concerned.
  3. A migration chain that is not idempotent. A re-run either re-applies a
     shape change to populated rows or is a no-op; the second is the only
     acceptable outcome, and the ledger is what makes it observable.
  4. A data file the wheel does not carry. `init_db()` reads `schema.sql` from
     beside itself, so an installed app fails at startup while the whole source
     tree passes every test in this file.
"""

from __future__ import annotations

import asyncio
import sqlite3
import tomllib
from fnmatch import fnmatch
from pathlib import Path

import aiosqlite
import pytest

from app.config import Settings
from app.db.database import (
    MIGRATION_001,
    MIGRATION_002,
    apply_pragmas,
    connect_db,
    db_path,
    init_db,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

PWA_OWNED_TABLES = {
    "schema_migrations",
    "ingredient_mappings",
    "meal_lists",
    "cook_log_receipts",
    "shortlist_intents",
}

# Every runtime data file the app reads from inside its own package. A glob in
# pyproject that matches nothing is a silent-green hole from the other side of
# the wheel-content check, so the paths are named explicitly here.
SHIPPED_DATA_FILES = (
    "app/db/schema.sql",
    "app/recipes/lexicon/brands.yaml",
)


def _init_and_inspect(settings: Settings) -> dict[str, set[str]]:
    """Run init_db twice — a second startup must be a no-op, not a repair."""

    async def scenario() -> dict[str, set[str]]:
        await init_db(settings)
        await init_db(settings)
        async with connect_db(settings) as conn:
            tables = {
                str(row[0])
                for row in await (
                    await conn.execute(
                        "SELECT name FROM sqlite_master"
                        " WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                    )
                ).fetchall()
            }
            indexes = {
                str(row[0])
                for row in await (
                    await conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
                ).fetchall()
            }
            triggers = {
                str(row[0])
                for row in await (
                    await conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
                ).fetchall()
            }
            return {"tables": tables, "indexes": indexes, "triggers": triggers}

    return asyncio.run(scenario())


# --- 1. The schema -------------------------------------------------------


def test_init_db_creates_every_pwa_owned_table(settings: Settings) -> None:
    found = _init_and_inspect(settings)
    assert PWA_OWNED_TABLES.issubset(found["tables"])


def test_init_db_creates_the_database_inside_the_server_owned_data_dir(
    settings: Settings,
) -> None:
    asyncio.run(init_db(settings))
    target = db_path(settings)
    assert target == settings.app_data_dir / "recipes.sqlite3"
    assert target.is_file()
    # Never in the vault: Obsidian would index and sync derived data.
    assert settings.vault_path not in target.parents


def test_ingredient_mappings_carries_both_unique_indexes_and_the_partial_one(
    settings: Settings,
) -> None:
    indexes = _init_and_inspect(settings)["indexes"]
    assert {
        "ux_ingredient_mappings_slot",
        "ux_ingredient_mappings_recipe_item",
        "ix_ingredient_mappings_item",
        "ix_ingredient_mappings_unresolved",
    }.issubset(indexes)


def test_ingredient_mappings_has_no_foreign_key_to_the_pantry_catalog(
    settings: Settings,
) -> None:
    # A REFERENCES clause here would need a same-named `items` table in this
    # database, i.e. a second writable copy of catalog truth. `foreign_keys=ON`
    # is on, so if such a clause ever lands it becomes load-bearing.
    asyncio.run(init_db(settings))
    ddl = _table_sql(settings, "ingredient_mappings")
    assert "REFERENCES" not in ddl.upper()
    assert "in_stock" not in ddl


def test_one_pantry_item_may_resolve_in_many_recipes(settings: Settings) -> None:
    # F2: pantry_item_id alone is not unique. A bare UNIQUE on this nullable
    # column would make the second of these two inserts fail silently.
    asyncio.run(init_db(settings))
    asyncio.run(_insert_mapping(settings, "拌空心菜", 0, 83))
    asyncio.run(_insert_mapping(settings, "煮空心菜", 0, 83))

    async def count() -> int:
        async with connect_db(settings) as conn:
            cursor = await conn.execute("SELECT COUNT(*) FROM ingredient_mappings")
            row = await cursor.fetchone()
            assert row is not None
            return int(row[0])

    assert asyncio.run(count()) == 2


def test_one_recipe_may_not_list_the_same_pantry_item_twice(
    settings: Settings,
) -> None:
    asyncio.run(init_db(settings))
    asyncio.run(_insert_mapping(settings, "盐焗鸡", 0, 83))

    async def duplicate() -> None:
        await _insert_mapping(settings, "盐焗鸡", 1, 83)

    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(duplicate())


def test_the_manual_trigger_aborts_an_update_but_allows_delete_and_reinsert(
    settings: Settings,
) -> None:
    # F6: immutability is a property of the row, not of one call site, so it
    # lives in the schema. A repair is an auditable delete + create.
    asyncio.run(init_db(settings))
    asyncio.run(_insert_mapping(settings, "盐焗鸡", 0, 83, match_method="manual", tier=0))

    async def update_to_other() -> None:
        async with connect_db(settings) as conn:
            await conn.execute(
                "UPDATE ingredient_mappings SET pantry_item_id = 42 WHERE recipe_note = ?",
                ("盐焗鸡",),
            )
            await conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="manual"):
        asyncio.run(update_to_other())

    async def replace() -> int:
        async with connect_db(settings) as conn:
            await conn.execute(
                "DELETE FROM ingredient_mappings WHERE recipe_note = ?", ("盐焗鸡",)
            )
            await conn.execute(
                "INSERT INTO ingredient_mappings"
                " (recipe_note, ingredient_index, raw_value, parse_method,"
                "  match_method, match_tier, pantry_item_id, confidence)"
                " VALUES (?, 0, 'x', 'bare', 'manual', 0, 42, 1.0)",
                ("盐焗鸡",),
            )
            await conn.commit()
            cursor = await conn.execute(
                "SELECT pantry_item_id FROM ingredient_mappings WHERE recipe_note = ?",
                ("盐焗鸡",),
            )
            row = await cursor.fetchone()
            assert row is not None
            return int(row[0])

    assert asyncio.run(replace()) == 42


def test_meal_lists_slot_check_is_closed_to_exactly_three_values(
    settings: Settings,
) -> None:
    # F12. A fourth value is a table rebuild, and the outbox replays this table.
    asyncio.run(init_db(settings))

    async def insert_slot(slot: str) -> None:
        async with connect_db(settings) as conn:
            await conn.execute(
                "INSERT INTO meal_lists (slot, position, recipe_note) VALUES (?, 0, 'x')",
                (slot,),
            )
            await conn.commit()

    for slot in ("breakfast", "lunch", "dinner"):
        asyncio.run(insert_slot(slot))

    for slot in ("snack", "brunch", "Breakfast"):
        with pytest.raises(sqlite3.IntegrityError):
            asyncio.run(insert_slot(slot))


def test_the_migration_drops_the_dead_column_from_a_populated_legacy_database(
    settings: Settings,
) -> None:
    """The upgrade path, which is the only one that matters for a deployed table.

    A fresh install never had the column: `schema.sql` stopped declaring it, so
    `init_db` on a new database has nothing to drop and the migration must be a
    no-op. The case that can actually break is the one this builds by hand — a
    database shaped like the *old* schema, holding rows, with the UNIQUE index
    already on it — because that is the only shape `ALTER TABLE … DROP COLUMN`
    could be refused by.

    Rows must survive: the column was `DEFAULT 0` on every row ever written, so
    there is nothing in it to lose, and that is the whole reason dropping a
    column on a live table is safe here rather than in general.
    """
    path = db_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy = sqlite3.connect(path)
    try:
        legacy.executescript(
            "CREATE TABLE cook_log_receipts ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " recipe_note TEXT NOT NULL,"
            " log_date TEXT NOT NULL,"
            " relative_path TEXT NOT NULL,"
            " note_revision TEXT NOT NULL,"
            " recipe_tracker_synced INTEGER NOT NULL DEFAULT 0,"
            " written_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"
            ");"
            "CREATE UNIQUE INDEX ux_cook_log_receipts_recipe_date"
            " ON cook_log_receipts(recipe_note, log_date);"
            "INSERT INTO cook_log_receipts"
            " (recipe_note, log_date, relative_path, note_revision, recipe_tracker_synced)"
            " VALUES ('盐焗鸡', '2026-09-27', '日记/x.md', 'sha256:x', 0);"
        )
        legacy.commit()
    finally:
        legacy.close()

    asyncio.run(init_db(settings))

    async def state() -> tuple[list[str], list[tuple[str, str]]]:
        async with connect_db(settings) as conn:
            columns = [
                str(row["name"])
                for row in await (
                    await conn.execute("PRAGMA table_info(cook_log_receipts)")
                ).fetchall()
            ]
            rows = await (
                await conn.execute(
                    "SELECT recipe_note, log_date FROM cook_log_receipts ORDER BY log_date"
                )
            ).fetchall()
            return columns, [(str(r["recipe_note"]), str(r["log_date"])) for r in rows]

    columns, rows = asyncio.run(state())
    assert "recipe_tracker_synced" not in columns
    # The audit columns are all still there, and the row survived the rebuild.
    assert {"recipe_note", "log_date", "relative_path", "note_revision", "written_at"} <= set(
        columns
    )
    assert rows == [("盐焗鸡", "2026-09-27")]

    # And the dedupe index still works, because the migration dropped a column
    # the index does not mention.
    async def duplicate() -> None:
        async with connect_db(settings) as conn:
            await conn.execute(
                "INSERT INTO cook_log_receipts"
                " (recipe_note, log_date, relative_path, note_revision)"
                " VALUES ('盐焗鸡', '2026-09-27', '日记/x.md', 'sha256:y')"
            )
            await conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(duplicate())


def test_the_migration_is_a_no_op_on_a_fresh_install(settings: Settings) -> None:
    """A new database has no such column, so `DROP COLUMN` must not be attempted.

    Without the `PRAGMA table_info` guard this raises on every fresh start, which
    is the failure mode that would make a correct migration look like a broken
    one.
    """
    asyncio.run(init_db(settings))
    # Twice: the second run is the "already migrated" path.
    asyncio.run(init_db(settings))

    async def versions() -> list[str]:
        async with connect_db(settings) as conn:
            rows = await (
                await conn.execute("SELECT version FROM schema_migrations ORDER BY version")
            ).fetchall()
            return [str(row[0]) for row in rows]

    assert asyncio.run(versions()) == [MIGRATION_001, MIGRATION_002]


def test_cook_log_receipts_dedupes_the_same_recipe_and_date(settings: Settings) -> None:
    # F13: the unique index is the double-submit ledger. The daily-note wikilink,
    # not this table, is what keeps cooking_count correct.
    asyncio.run(init_db(settings))

    async def receipt(note: str, date: str) -> None:
        async with connect_db(settings) as conn:
            await conn.execute(
                "INSERT INTO cook_log_receipts"
                " (recipe_note, log_date, relative_path, note_revision)"
                " VALUES (?, ?, '日记/2026/2026-09-27.md', 'sha256:x')",
                (note, date),
            )
            await conn.commit()

    asyncio.run(receipt("盐焗鸡", "2026-09-27"))
    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(receipt("盐焗鸡", "2026-09-27"))
    # A different date is a real second cook, not a duplicate submit.
    asyncio.run(receipt("盐焗鸡", "2026-09-28"))


def test_shortlist_intents_creates_the_exactly_once_ledger(settings: Settings) -> None:
    asyncio.run(init_db(settings))

    async def intent(client_id: str) -> None:
        async with connect_db(settings) as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO shortlist_intents"
                " (client_id, request_fingerprint, response_json) VALUES (?, 'fp', '{}')",
                (client_id,),
            )
            await conn.commit()

    asyncio.run(intent("c1"))
    asyncio.run(intent("c1"))

    async def responses() -> int:
        async with connect_db(settings) as conn:
            cursor = await conn.execute("SELECT COUNT(*) FROM shortlist_intents")
            row = await cursor.fetchone()
            assert row is not None
            return int(row[0])

    assert asyncio.run(responses()) == 1


# --- 2. The six pragmas --------------------------------------------------


def test_all_six_pragmas_are_applied_on_every_connection(settings: Settings) -> None:
    asyncio.run(init_db(settings))

    async def read_all() -> list[dict[str, int | str]]:
        # Two independent connections, not one: a pragma is per-connection, so a
        # second reader anywhere in the app is only safe if every open applies
        # them. `synchronous` and `temp_store` read back as their numeric codes.
        readings = []
        for _ in range(2):
            async with connect_db(settings) as conn:
                reading: dict[str, int | str] = {}
                for pragma in (
                    "journal_mode",
                    "synchronous",
                    "foreign_keys",
                    "busy_timeout",
                    "cache_size",
                    "temp_store",
                ):
                    row = await (await conn.execute(f"PRAGMA {pragma}")).fetchone()
                    assert row is not None, pragma
                    value = row[0]
                    reading[pragma] = value.lower() if isinstance(value, str) else int(value)
                readings.append(reading)
        return readings

    for reading in asyncio.run(read_all()):
        assert reading["journal_mode"] == "wal"
        assert reading["synchronous"] == 1
        assert reading["foreign_keys"] == 1
        assert reading["busy_timeout"] == 5000
        assert reading["cache_size"] == -64000
        assert reading["temp_store"] == 2


def test_row_factory_is_the_shared_row_type(settings: Settings) -> None:
    asyncio.run(init_db(settings))

    async def factory() -> object:
        async with connect_db(settings) as conn:
            cursor = await conn.execute("SELECT version FROM schema_migrations")
            row = await cursor.fetchone()
            assert row is not None
            return conn.row_factory

    assert asyncio.run(factory()) is aiosqlite.Row


def test_apply_pragmas_is_idempotent(settings: Settings) -> None:
    # WAL in particular is a mode change, not a setting: applying it twice must
    # not be an error on a connection that already runs in it.
    asyncio.run(init_db(settings))

    async def twice() -> None:
        async with connect_db(settings) as conn:
            await apply_pragmas(conn)
            await apply_pragmas(conn)

    asyncio.run(twice())


def test_connect_db_closes_the_connection_on_an_exception(settings: Settings) -> None:
    # A descriptor leak must not outlive the request. WAL leaves a -wal and -shm
    # beside the database only while a connection is open, so their presence
    # inside the context and absence after it is the observable.
    asyncio.run(init_db(settings))

    async def observe() -> tuple[bool, bool]:
        base = db_path(settings)
        sides = [base.with_name(base.name + suffix) for suffix in ("-wal", "-shm")]
        with pytest.raises(RuntimeError):
            async with connect_db(settings) as conn:
                await conn.execute("SELECT 1")
                inside = all(side.exists() for side in sides)
                raise RuntimeError("boom")
        return inside, any(side.exists() for side in sides)

    inside, after = asyncio.run(observe())
    assert inside is True
    assert after is False


# --- 3. The ledger -------------------------------------------------------


def test_schema_migrations_records_every_version(settings: Settings) -> None:
    asyncio.run(init_db(settings))

    async def versions() -> list[str]:
        async with connect_db(settings) as conn:
            rows = await (
                await conn.execute("SELECT version FROM schema_migrations ORDER BY version")
            ).fetchall()
            return [str(row[0]) for row in rows]

    # BOTH versions, in order. The list is the migration ledger's whole
    # contract, so a new migration that is not added here fails rather than
    # being applied silently forever.
    assert asyncio.run(versions()) == [MIGRATION_001, MIGRATION_002]


def test_a_rerun_is_a_no_op_and_does_not_disturb_ledger_rows(settings: Settings) -> None:
    asyncio.run(init_db(settings))

    async def seed() -> str:
        async with connect_db(settings) as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO shortlist_intents"
                " (client_id, request_fingerprint, response_json) VALUES ('c1', 'fp', '{}')"
            )
            await conn.commit()
            row = await (
                await conn.execute("SELECT applied_at FROM schema_migrations WHERE version = ?",
                                   (MIGRATION_001,))
            ).fetchone()
            assert row is not None
            return str(row[0])

    async def after_rerun() -> tuple[str, int, int]:
        await init_db(settings)
        async with connect_db(settings) as conn:
            applied_at = await (
                await conn.execute(
                    "SELECT applied_at FROM schema_migrations WHERE version = ?",
                    (MIGRATION_001,),
                )
            ).fetchone()
            assert applied_at is not None
            ledger = await (
                await conn.execute("SELECT COUNT(*) FROM schema_migrations")
            ).fetchone()
            intents = await (
                await conn.execute("SELECT COUNT(*) FROM shortlist_intents")
            ).fetchone()
            assert ledger is not None and intents is not None
            return str(applied_at[0]), int(ledger[0]), int(intents[0])

    first_applied = asyncio.run(seed())
    applied_after, ledger_count, intent_count = asyncio.run(after_rerun())

    assert applied_after == first_applied
    assert ledger_count == 2
    # The guard short-circuits, so a re-run cannot destroy a replayed intent.
    assert intent_count == 1


# --- 4. The wheel carries the data files ----------------------------------


def test_the_wheel_would_carry_every_runtime_data_file() -> None:
    # The failure this closes is invisible from the source tree: `init_db()`
    # reads `schema.sql` from beside itself, so the whole suite above passes
    # while an installed app dies at startup. Resolving the nearest owning
    # package and checking the build's own `package-data` table against it is
    # the predicate CI applies to the built zip, minus the zip.
    config = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    package_data: dict[str, list[str]] = config["tool"]["setuptools"]["package-data"]

    for relative in SHIPPED_DATA_FILES:
        owner = _owning_package(REPO_ROOT / relative)
        assert owner in package_data, f"{relative} has no package-data entry ({owner!r})"
        assert any(
            fnmatch(relative, f"{owner.replace('.', '/')}/{pattern}")
            for pattern in package_data[owner]
        ), f"no package-data glob in {owner!r} matches {relative}"


# --- helpers -------------------------------------------------------------


def _owning_package(path: Path) -> str:
    """The nearest ancestor of a shipped data file that is a real package.

    Data directories are not packages — `app/recipes/lexicon/` has no
    `__init__.py` and must not grow one, because its files only ship if
    `app.recipes` claims them via `package-data`.
    """

    for directory in (path.parent.resolve(), *path.parent.resolve().parents):
        if (directory / "__init__.py").is_file():
            return ".".join(directory.relative_to(REPO_ROOT.resolve()).parts)
        if directory == REPO_ROOT.resolve():
            break
    raise AssertionError(f"no package owns {path}")


def _table_sql(settings: Settings, table: str) -> str:
    async def read() -> str:
        async with connect_db(settings) as conn:
            row = await (
                await conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
                )
            ).fetchone()
            assert row is not None, table
            return str(row[0])

    return asyncio.run(read())


async def _insert_mapping(
    settings: Settings,
    recipe_note: str,
    ingredient_index: int,
    pantry_item_id: int,
    match_method: str = "exact",
    tier: int = 1,
) -> None:
    async with connect_db(settings) as conn:
        await conn.execute(
            "INSERT INTO ingredient_mappings"
            " (recipe_note, ingredient_index, raw_value, parse_method,"
            "  match_method, match_tier, pantry_item_id, confidence)"
            " VALUES (?, ?, 'raw', 'bare', ?, ?, ?, 1.0)",
            (recipe_note, ingredient_index, match_method, tier, pantry_item_id),
        )
        await conn.commit()

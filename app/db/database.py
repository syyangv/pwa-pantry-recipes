from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from app.config import Settings

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DB_FILENAME = "recipes.sqlite3"

# The order the numbered migrations are applied in is the order they are listed
# in `init_db`. There is no sort and no dependency graph: a later version that
# depends on an earlier one must be appended after it.
MIGRATION_001 = "001_shortlist_intents"
MIGRATION_002 = "002_drop_recipe_tracker_synced"
MIGRATION_003 = "003_receipt_retraction"


def db_path(settings: Settings) -> Path:
    """The one database this app owns, derived from the server-owned data dir."""
    return settings.app_data_dir / DB_FILENAME


async def apply_pragmas(conn: aiosqlite.Connection) -> None:
    """Apply required SQLite runtime settings on every connection.

    WAL plus `busy_timeout` is what lets a read overlap a write; `foreign_keys`
    is per-connection, not per-database, so a connection that skipped this
    would silently accept the orphans the schema exists to prevent.
    """

    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA synchronous=NORMAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.execute("PRAGMA busy_timeout=5000")
    await conn.execute("PRAGMA cache_size=-64000")
    await conn.execute("PRAGMA temp_store=MEMORY")


@asynccontextmanager
async def connect_db(settings: Settings | None = None) -> AsyncIterator[aiosqlite.Connection]:
    runtime = settings or Settings.from_environment()
    conn = await aiosqlite.connect(db_path(runtime))
    conn.row_factory = aiosqlite.Row
    try:
        await apply_pragmas(conn)
        yield conn
    finally:
        await conn.close()


async def init_db(settings: Settings | None = None) -> None:
    """Create or update the schema. Safe to call on every startup."""

    runtime = settings or Settings.from_environment()
    runtime.app_data_dir.mkdir(parents=True, exist_ok=True)

    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    async with connect_db(runtime) as conn:
        await conn.executescript(schema)
        await conn.commit()
        await _migrate_001_shortlist_intents(conn)
        await _migrate_002_drop_recipe_tracker_synced(conn)
        await _migrate_003_receipt_retraction(conn)


async def _migrate_001_shortlist_intents(conn: aiosqlite.Connection) -> None:
    """Create `shortlist_intents` and record the version.

    `schema.sql` already carries the table, so this is the ledger row that makes
    the version observable; the `IF NOT EXISTS` create is the same lazy
    guarantee the mutation handler makes, so an install that reaches the handler
    before this runs is still safe.
    """

    row = await (
        await conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?", (MIGRATION_001,)
        )
    ).fetchone()
    if row is not None:
        return

    await conn.execute(
        "CREATE TABLE IF NOT EXISTS shortlist_intents ("
        "client_id TEXT PRIMARY KEY,"
        "request_fingerprint TEXT NOT NULL,"
        "response_json TEXT NOT NULL,"
        "created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"
        ")"
    )
    await conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)", (MIGRATION_001,)
    )
    await conn.commit()


async def _migrate_002_drop_recipe_tracker_synced(conn: aiosqlite.Connection) -> None:
    """Drop `cook_log_receipts.recipe_tracker_synced`, and record the version.

    **The column was a lie in the schema, not only in the code.** It was inserted
    as 0 and nothing ever wrote 1 — the read-path flip spec §13 step 8 required
    was never implemented — so the `待 Obsidian 同步` badge keyed on it sat on the
    screen permanently. The comparison that answers the real question is
    `GET /api/recipes/{note_name}`'s `pendingCookDates`, which reads receipts and
    frontmatter and writes nothing. The column is removed rather than left inert
    because an inert column is one read away from being load-bearing again, and
    nothing in the app selects it any more.

    **`PRAGMA table_info` first, so this is a no-op on a fresh install.**
    `schema.sql` no longer declares the column, so a new database is created
    without it and an unconditional `DROP COLUMN` would raise on every fresh
    start. Asking the table what it actually has is what makes one code path
    correct for both a fresh install and a populated one.

    **`ALTER TABLE … DROP COLUMN`, not the create-copy-rename rebuild.** SQLite
    has supported it since 3.35 and the runtime here is far past that. The
    rebuild is the portable form and it is the right choice when a column is
    indexed or referenced; this one is neither, and the rebuild would rewrite the
    table's `sqlite_sequence` and rowids for no benefit. `cook_log_receipts` has
    a UNIQUE index on `(recipe_note, log_date)` and neither column is the one
    being dropped, so the index is untouched.

    **Rows are preserved.** The column was `DEFAULT 0` on every row ever written,
    so there is no information in it to lose — which is the only reason dropping a
    column on a live table is safe here rather than in general.
    """
    columns = {
        str(row["name"])
        for row in await (await conn.execute("PRAGMA table_info(cook_log_receipts)")).fetchall()
    }
    if "recipe_tracker_synced" in columns:
        await conn.execute("ALTER TABLE cook_log_receipts DROP COLUMN recipe_tracker_synced")
    await conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)", (MIGRATION_002,)
    )
    await conn.commit()


async def _migrate_003_receipt_retraction(conn: aiosqlite.Connection) -> None:
    """Add `cook_log_receipts.retracted_at` and make the dedupe index partial.

    A retracted Cooking Record keeps its row (the audit trail can still answer
    "did the app write this?"), so the unique index on `(recipe_note, log_date)`
    has to stop covering it: with the total index a retracted row would reserve
    its pair forever and the cook could never be logged again.

    **`schema.sql` cannot do this on an existing database**, because its `CREATE
    UNIQUE INDEX IF NOT EXISTS` is skipped by name, so the old total index stays.
    The migration therefore drops and recreates it. Both steps are guarded by
    asking the database what it has, so the same code is a no-op on a fresh
    install (where `schema.sql` already made the partial index) and on a re-run.
    Existing rows get `retracted_at IS NULL`, i.e. they stay active.
    """
    columns = {
        str(row["name"])
        for row in await (await conn.execute("PRAGMA table_info(cook_log_receipts)")).fetchall()
    }
    if "retracted_at" not in columns:
        await conn.execute("ALTER TABLE cook_log_receipts ADD COLUMN retracted_at TEXT")
    index = await (
        await conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'ux_cook_log_receipts_recipe_date'"
        )
    ).fetchone()
    if index is None or "retracted_at IS NULL" not in str(index["sql"]):
        await conn.execute("DROP INDEX IF EXISTS ux_cook_log_receipts_recipe_date")
        await conn.execute(
            "CREATE UNIQUE INDEX ux_cook_log_receipts_recipe_date"
            " ON cook_log_receipts(recipe_note, log_date) WHERE retracted_at IS NULL"
        )
    await conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)", (MIGRATION_003,)
    )
    await conn.commit()

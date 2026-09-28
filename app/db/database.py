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

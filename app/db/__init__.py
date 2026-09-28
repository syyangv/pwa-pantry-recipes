"""PWA-owned SQLite: connection layer, schema bootstrap, and migrations.

`APP_DATA_DIR/recipes.sqlite3` — never in the vault, so Obsidian does not index
or sync derived data. The `apply_pragmas()` / `connect_db()` / `_migrate_NNN_*`
idiom is copied from `pwa-wardrobe/app/database.py` (F9): re-expressing it over
`asyncio.to_thread` would port the code without porting the idiom.

`schema.sql` is executed with `executescript` and holds only idempotent
`IF NOT EXISTS` DDL. Anything that changes the shape of a populated table is a
numbered `_migrate_NNN_*` coroutine appended to `init_db()` in call order, so
the startup path never re-applies a versioned change to existing rows.
"""

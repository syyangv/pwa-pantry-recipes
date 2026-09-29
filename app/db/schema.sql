-- PWA-owned SQLite: APP_DATA_DIR/recipes.sqlite3 (spec §7).
--
-- Every statement is IF NOT EXISTS so `executescript` at startup is idempotent
-- and safe to re-run. Versioned changes are numbered `_migrate_NNN_*` in
-- database.py, not applied here, so a running install never re-runs a shape
-- change on a populated table.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- The materialized Recipe -> Pantry Item mapping (D1).
--
-- `pantry_item_id` carries NO REFERENCES: pantry_items.db is a different
-- database, opened read-only, and a same-named table here purely to hang a
-- foreign key off would be a second writable copy of catalog truth.
CREATE TABLE IF NOT EXISTS ingredient_mappings (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_note      TEXT    NOT NULL,   -- vault-relative: 'Hobbies/做饭/Recipes/盐焗鸡.md'
    ingredient_index INTEGER NOT NULL,   -- 0-based index within the 材料 list
    raw_value        TEXT    NOT NULL,   -- the frontmatter string, verbatim
    parsed_name      TEXT,               -- NULL when unparseable
    parse_method     TEXT    NOT NULL,   -- §9.6 enum
    match_method     TEXT    NOT NULL,   -- §9.8 enum
    match_tier       INTEGER NOT NULL,   -- 1..8, or 0 for 'manual'
    pantry_item_id   INTEGER,            -- cross-database; NO foreign key
    confidence       REAL    NOT NULL,   -- 0.0 .. 1.0
    candidates_json  TEXT    NOT NULL DEFAULT '[]',  -- rejected candidates, for audit
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Slot identity: one row per Ingredient position, so re-resolution updates in
-- place and the row is stable across resolution passes.
CREATE UNIQUE INDEX IF NOT EXISTS ux_ingredient_mappings_slot
    ON ingredient_mappings(recipe_note, ingredient_index);

-- The inverted constraint. pantry_item_id alone is NOT unique — one Pantry Item
-- may satisfy Ingredients in many recipes — and a bare UNIQUE on this nullable
-- column would make two recipes' resolution of the same id mutually exclusive,
-- silently dropping one. Scoped to the recipe it still catches a real defect:
-- one recipe listing the same Pantry Item twice (F2).
CREATE UNIQUE INDEX IF NOT EXISTS ux_ingredient_mappings_recipe_item
    ON ingredient_mappings(recipe_note, pantry_item_id);

CREATE INDEX IF NOT EXISTS ix_ingredient_mappings_item
    ON ingredient_mappings(pantry_item_id);

CREATE INDEX IF NOT EXISTS ix_ingredient_mappings_unresolved
    ON ingredient_mappings(match_method) WHERE match_method = 'unresolved';

-- F6: a hand fix is a property of the row, not of one call site, so the
-- guarantee lives in the schema. Changing one is a DELETE plus a fresh INSERT,
-- which is auditable; a silent overwrite is not.
CREATE TRIGGER IF NOT EXISTS trg_ingredient_mappings_manual_immutable
BEFORE UPDATE ON ingredient_mappings
FOR EACH ROW
WHEN OLD.match_method = 'manual'
BEGIN
    SELECT RAISE(ABORT, 'manual_ingredient_mapping_is_immutable');
END;

-- Exactly three slots, closed (F12). Adding one is a table rebuild, and the
-- outbox replays this table.
CREATE TABLE IF NOT EXISTS meal_lists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slot        TEXT    NOT NULL CHECK (slot IN ('breakfast','lunch','dinner')),
    position    INTEGER NOT NULL,
    recipe_note TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Makes "add" naturally idempotent, and bounds a reorder to <= 50 rows.
CREATE UNIQUE INDEX IF NOT EXISTS ux_meal_lists_slot_recipe
    ON meal_lists(slot, recipe_note);

CREATE INDEX IF NOT EXISTS ix_meal_lists_slot_position
    ON meal_lists(slot, position);

-- The PWA-owned mirror of a Cooking Record: the audit trail and the
-- double-submit dedupe ledger. NOT the mechanism that keeps cooking_count
-- correct — the daily-note wikilink is (F13).
CREATE TABLE IF NOT EXISTS cook_log_receipts (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    -- A BARE BASENAME ('盐焗鸡'), not a vault-relative path. `_require_recipe_note`
    -- refuses `/` and every wikilink metacharacter, and the client sends the name
    -- D2 makes the wikilink text. `ingredient_mappings.recipe_note` above is
    -- therefore NOT comparable to this column: that one is written from
    -- `RecipeNote.note_path` and does carry the path. The per-recipe staleness
    -- aggregate joins on the basename because the tracker resolves daily-note
    -- outlinks by wikilink text, which is the basename.
    recipe_note              TEXT    NOT NULL,
    log_date                 TEXT    NOT NULL,   -- 'YYYY-MM-DD'
    relative_path            TEXT    NOT NULL,   -- '日记/2026/2026-09-27.md'
    -- `recipe_tracker_synced` USED to live here and was removed by
    -- `_migrate_002_drop_recipe_tracker_synced`. It was inserted as 0 and never
    -- written, so the `待 Obsidian 同步` badge keyed on it was permanently on. The
    -- question it was meant to answer is answered by `pendingCookDates` on the
    -- recipe detail route, as a read. Do NOT re-add it: a column that means
    -- "nobody implemented this" is one read away from being load-bearing again.
    note_revision            TEXT    NOT NULL,   -- 'sha256:…' of the committed note
    written_at               TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    -- Set when the user retracts the Cooking Record inside the undo window. The
    -- row is kept (audit trail: "the app wrote this, then removed it") and stops
    -- counting as a cook; `_migrate_003_receipt_retraction` adds the column to a
    -- table created before it existed.
    retracted_at             TEXT
);

-- Active rows only: a retracted row must not keep its recipe and date reserved,
-- or a cook retracted by mistake could never be logged again. On a database
-- created before `retracted_at`, this statement is skipped by name and the
-- migration replaces the old total index.
CREATE UNIQUE INDEX IF NOT EXISTS ux_cook_log_receipts_recipe_date
    ON cook_log_receipts(recipe_note, log_date)
    WHERE retracted_at IS NULL;

-- Exactly-once offline replay of a shortlist mutation. The startup migration
-- _migrate_001_shortlist_intents also stamps the ledger version; the mutation
-- handler creates it lazily, so a direct ASGI test client and a rolling deploy
-- are both safe.
CREATE TABLE IF NOT EXISTS shortlist_intents (
    client_id           TEXT PRIMARY KEY,
    request_fingerprint TEXT NOT NULL,
    response_json       TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

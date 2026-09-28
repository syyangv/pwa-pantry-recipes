# AGENTS.md — pwa-pantry-recipes

A local-only PWA that answers **"what can I cook from what I already have?"**
and records the cook in the Obsidian daily note.

**State (2026-09-27): infrastructure and domain primitives, no user-facing
feature.** The shell, `/health`, `/api/version`, and `/api/session` are wired and
the auth guards are installed, and the domain primitives below ship and are
tested — but **none of them is reachable from a request**: the `lifespan` is a
bare `yield`, there are no domain routers, and the two `TODO(implementation)`
markers in `app/main.py` name exactly what is missing. Do not describe this app
as working — read `README.md` § *What does not work yet*.

## Non-negotiables

1. **The Obsidian vault is canonical.** This app *projects* existing Markdown
   and performs only narrowly defined, revision-checked transformations. It
   never rewrites recipe steps and never creates a missing daily note.
2. **Server-owned roots.** `OBSIDIAN_VAULT_PATH`, `APP_DATA_DIR`,
   `PANTRY_ITEMS_DB`, `RECIPES_ROOT`, and `DAILY_NOTES_ROOT` come from the
   environment only. A request may contribute *which recipe*, *which date*, and
   *which quantities* — never *which paths*. There is no request field that
   reaches `Settings`.
3. **Fail closed.** A missing or invalid core value raises
   `ConfigurationError` and refuses startup. Never add a soft fallback that
   turns a misconfiguration into a plausible-looking empty result.
4. **`wholefoods-to-pantry` owns `pantry_items.db`.** Open it read-only. Never
   write, migrate, or create that file from this repo.
5. **Vendored infra stays byte-identical.** `app/static/js/pwa/*`,
   `app/static/css/pwa.css`, `app/static/css/pull-refresh.css`, and
   `app/pwa_version.py` are copies from `syyangv/pwa-template`. Re-vendor with
   `python3 ~/projects/pwa-template/scripts/vendor.py .`; never hand-edit. The
   pre-commit hook and CI fail on drift. `app/static/sw.js` is the one
   configured exception (marker-only check; `vendor.py` never clobbers it).
6. **No inline `serviceWorker.register`.** The vendored `update-manager` owns
   registration and the update lifecycle. `tests/js/scaffold.test.mjs` fails if
   `index.html` ever contains the string `serviceWorker`.
7. **Never `git push --force`.**
8. **Bump `CACHE_VERSION` in `app/static/sw.js` on every deploy**, and add any
   new static file to `SHELL_ASSETS` in the same commit.

## Vocabulary

Use `CONTEXT.md`'s terms exactly: Pantry Item, Pantry Stock, Pantry Unit,
Ingredient, Seasoning (调料), Cooking Tool, Cookable, Cooking Log, Cooking
Record, Stock Movement, Server-Owned Root. Read the *Terminology* table before
introducing a name — the list of forbidden synonyms is there because each one
already caused a real ambiguity (flattening 调料 into 材料, or counting a
Pantry Unit's parent at its per-unit price).

Two rules that are easy to get wrong:

- **`Pantry Unit`:** a multi-package purchase splits into N units and the
  parent line's amount is a *per-unit* price. Count and money totals iterate
  units and skip the parent, or a half-used item counts at twice its value.
- **Pantry Item ≠ Pantry Stock:** the catalog is an immutable record of what
  has been *bought*; stock is what the household *has*. Conflating them makes a
  re-buy look like a duplicate.

## Layout

```
app/config.py        typed, fail-closed Settings.from_environment()
app/main.py          create_app() factory; route order is load-bearing
app/auth.py          identity / Origin / CSRF / Host guards; CSRF token store
app/pwa_version.py   VENDORED pwa-infra — do not edit
app/db/              the owned SQLite layer: schema.sql, aiosqlite connect,
                     pragmas, numbered migrations (init_db)
app/pantry/          the read-only PantryCatalog over pantry_items.db `items`,
                     plus PantryStockIndex: the live Pantry.md, the three-tier
                     Stock Join, and line_overrides.yaml
app/recipes/         the recipe index + note parser, the ingredient value
                     parser, the product-core normalizer, and lexicon/brands.yaml
app/vault/           atomic_write (AtomicNoteStore), frontmatter, sections,
                     daily_paths — all byte-span primitives
app/static/          sw.js (configured) + the vendored js/pwa/*, css/*
app/static/js/logic/ chipClass, headline, sortRecipes — pure, node --test ed,
                     and imported by nothing in app/static but sw.js's
                     SHELL_ASSETS list
scripts/             the example LaunchAgent plist + generate_icons.py
tests/conftest.py    tmp vault + tmp data dir + seeded pantry catalog
tests/api/           the auth guards, the session contract, CSRF
tests/db/            migrations and pragmas
tests/pantry/        the catalog, the Stock Join, the per-unit money parity
tests/recipes/       reader, ingredients, normalize, brand lexicon
tests/vault/         atomic_write, frontmatter, sections, daily_paths, the
                     Pantry.md parser and the stock-facing line it feeds
tests/scaffold/      the convergence gate, route table, static shell, icons
tests/js/            node --test gates for the boot contract + SHELL_ASSETS
tests/js/logic/      node --test gates for the three logic modules
previews/            VENDORED pwa-infra device frames (dev-only)
```

Two things about that tree that a `find` will not tell you:

- **`package-data` is registered per directory, one level deep.** A glob's `*`
  does not cross a `/`, so `static/js/*` matches `main.js` and the `logic/` and
  `pwa/` *directories* and none of their contents. A new subdirectory under
  `static/js/` needs its own `static/js/<name>/*` entry, or its modules are
  precached by `sw.js`, absent from every wheel, and 404 offline.
  `test_every_static_file_is_covered_by_a_package_data_glob` fails that commit.
- **`package.json`'s test glob is quoted on purpose.** `'tests/js/**/*.test.mjs'`
  reaches `/bin/sh`, which has no `globstar`; unquoted, the runner silently
  collects a subset and reports 0 failures. `tests/js/collection-probe.mjs`
  guards it. Never unquote it.

## Commands

```bash
.venv/bin/python -m pytest                     # suite
.venv/bin/python -m ruff check app tests
.venv/bin/python -m mypy app
npm test && npm run check                      # frontend gates
python3 ~/projects/pwa-template/scripts/vendor.py --check .   # drift gate

# Package check. `build/` and the egg-info MUST go first: `pip wheel .` reuses
# a stale `build/lib` and a stale git-ignored SOURCES.txt, and reports success
# either way. See README.md § "Why the wheel command cleans two directories".
rm -rf /tmp/wheel build pwa_pantry_recipes.egg-info
.venv/bin/python -m pip wheel . --no-deps \
  --no-build-isolation --wheel-dir /tmp/wheel

.venv/bin/python -m uvicorn app.main:create_app --factory \
  --host 127.0.0.1 --port 8007
```

## Agent skills

### Issue tracker

GitHub issues on `syyangv/pwa-pantry-recipes` via the `gh` CLI.

### Triage labels

The five canonical triage roles map 1:1 to GitHub labels (`needs-triage`, `needs-info`,
`ready-for-agent`, `ready-for-human`, `wontfix`). All five exist on the tracker. When a
skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding
label string.

Source of truth for the role→label mapping and each role's meaning:
`~/projects/pwa-template/docs/agents/triage-labels.md`.

Roles are not decoration. A ticket is `ready-for-agent` only when its `## Blocked by` is
closed, its acceptance criteria name concrete files/functions/routes, and its verification
command is one the repo can actually run today. A ticket with an unresolved design
question belongs in `needs-triage`, not in a worker's queue.

### Domain docs

Single-context repository: `CONTEXT.md` at the root is the glossary. Add a
domain term there — with its forbidden synonyms — in the same commit that
introduces the code that implements it, not later.

### Deploy

Not yet authorized. `scripts/pwa-pantry-recipes.example.plist` is a template
only. When deployment is authorized, the release sequence is: bump
`CACHE_VERSION` → add new static files to `SHELL_ASSETS` → commit → push →
`launchctl kickstart -k gui/$(id -u)/com.syang.pwa-pantry-recipes` → verify
`/api/version` matches `CACHE_VERSION` and the `X-PWA-Backend-Started-At`
header is newer than every changed startup-loaded file → only then reinstall the
Home Screen PWA (iOS caches manifest metadata longer than page content).

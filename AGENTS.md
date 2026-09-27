# AGENTS.md — pwa-pantry-recipes

A local-only PWA that answers **"what can I cook from what I already have?"**
and records the cook in the Obsidian daily note.

**Scaffold state (2026-09-27): infrastructure only.** No feature is
implemented. `app/main.py` serves the shell, `/health`, and `/api/version`; the
`TODO(implementation)` markers name exactly what is missing. Do not describe
this app as working — read `README.md` § *What does not work yet*.

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
app/pwa_version.py   VENDORED pwa-infra — do not edit
app/static/          sw.js (configured) + the vendored js/pwa/*, css/*
scripts/             the example LaunchAgent plist
tests/conftest.py    tmp vault + tmp data dir + seeded pantry catalog
tests/scaffold/      the convergence gate and the static shell contract
tests/js/            node --test structural gates for the boot contract
previews/            VENDORED pwa-infra device frames (dev-only)
```

## Commands

```bash
.venv/bin/python -m pytest                     # suite
.venv/bin/python -m ruff check app tests
.venv/bin/python -m mypy app
npm test && npm run check                      # frontend structural gates
python3 ~/projects/pwa-template/scripts/vendor.py --check .   # drift gate
.venv/bin/python -m uvicorn app.main:create_app --factory \
  --host 127.0.0.1 --port 8007
```

## Agent skills

### Issue tracker

GitHub issues on `syyangv/pwa-pantry-recipes` via the `gh` CLI.

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

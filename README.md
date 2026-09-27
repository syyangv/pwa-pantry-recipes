# Pantry Recipes PWA

A local, installable Progressive Web App for **deciding what to cook from what
is already in the pantry**, and for recording the cook in the Obsidian daily
note.

> **Scaffold status (2026-09-27): infrastructure only.** The repository contains
> the vendored pwa-infra shell, a fail-typed configuration surface, the
> LaunchAgent template, CI, and a green scaffold test. **No feature is
> implemented** — there is no pantry view, no recipe index, and no cooking-log
> write. The app boots and serves `/`, `/health`, `/api/version`, `/sw.js`, the
> manifest, and the static assets; the placeholder panel says so. See
> [What does not work yet](#what-does-not-work-yet).

## Architecture

```
FastAPI (loopback only)  ──  vanilla ES modules  ──  no build step
  reads: Obsidian vault (recipes, daily notes)
  reads: wholefoods-to-pantry/assets/pantry_items.db  (READ-ONLY)
  writes: the dated Obsidian daily note  (cooking log — the only write)
  serves: app/static/ straight from disk
```

- **No bundler, no `node_modules` at runtime.** `app/static/` is served as-is.
  `npm` exists only to run `node --test` on the scaffold's structural gates.
- **Vendored infra, not a dependency.** `app/static/js/pwa/*`, `css/pwa.css`,
  `css/pull-refresh.css`, and `app/pwa_version.py` are byte-identical copies of
  https://github.com/syyangv/pwa-template, gated by a pre-commit hook and a CI
  step. Re-vendor with `python3 ~/projects/pwa-template/scripts/vendor.py .`.
- **Pattern A versioning.** `CACHE_VERSION` in `app/static/sw.js` is the single
  source: `/api/version`, `/health`, the FastAPI app version, and the version
  injected into the HTML shell all derive from it. There is no `VERSION` file.
- **Route order is load-bearing.** `/js/{path}` is registered before the static
  mount so it can inject `__APP_VERSION__` into ES-module *content* (a `?v=` pin
  on the entry URL does not propagate to nested imports), and the static mount
  is registered **last** so it cannot shadow an API route.

## Local development

```bash
cd ~/projects/pwa-pantry-recipes

# 1. venv + dependencies (setuptools is explicit so the local venv matches CI,
#    which needs it for the `pip wheel --no-build-isolation` package check)
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install "setuptools>=69" -e '.[test,dev]'

# 2. configure (SERVER-OWNED values; see .env.example for the full contract)
cp .env.example .env
$EDITOR .env

# 3. install the pre-commit drift gate (one-time per clone)
git config core.hooksPath git-hooks

# 4. run
.venv/bin/python -m uvicorn app.main:create_app --factory \
  --host 127.0.0.1 --port 8007 --reload
```

Then open <http://127.0.0.1:8007/>. The PWA needs a secure context for service
workers, so use the loopback host (or a Tailscale Serve HTTPS origin) — not
`http://<lan-ip>:8007`, which will register no worker.

`--reload` watches `app/`, so `sw.js`/`index.html` edits are picked up
automatically. Because the app reads its configuration at **startup**, changes
to `.env` need a restart.

## Verification

```bash
# Scaffold test — the convergence gate (/api/version == sw.js CACHE_VERSION)
.venv/bin/python -m pytest tests/scaffold/test_app.py -q

# Whole Python suite
.venv/bin/python -m pytest

# Lint / types
.venv/bin/python -m ruff check app tests
.venv/bin/python -m mypy app

# Frontend structural gates (no dependencies; `npm install` is not required)
npm test          # node --test tests/js/*.test.mjs
npm run check     # node --check on the module graph entry + the worker

# Vendored-infra drift gate (what the pre-commit hook runs)
python3 ~/projects/pwa-template/scripts/vendor.py --check .

# The package check CI runs: the built wheel must carry the static shell
rm -rf /tmp/wheel && .venv/bin/python -m pip wheel . --no-deps \
  --no-build-isolation --wheel-dir /tmp/wheel
```

## Configuration

Every value is read from the process environment at startup by
`app/config.py`, and is **server-owned**: no request parameter, header, or body
field can select a vault, a database path, a recipe root, or a daily-notes root.
Invalid values raise `ConfigurationError` and the process refuses to start —
it never degrades to a silently empty surface. See `.env.example` for the
annotated list; the four that matter most are:

| Variable | Meaning |
|---|---|
| `OBSIDIAN_VAULT_PATH` | Absolute path to the vault. |
| `APP_DATA_DIR` | Absolute, outside the vault; locks and recovery data. |
| `PANTRY_ITEMS_DB` | Absolute, outside the vault. The `wholefoods-to-pantry` SQLite catalog (`items` table), opened **read-only**. Missing file ⇒ startup failure, so a bad path can never look like an empty pantry. |
| `RECIPES_ROOT` / `DAILY_NOTES_ROOT` | Vault-relative roots: recipe notes (`Hobbies/做饭/Recipes`) and year-subfoldered daily notes (`日记`). |

## Deployment (planned)

`scripts/pwa-pantry-recipes.example.plist` is a fully commented LaunchAgent
template with `__UPPER_CASE__` placeholders, a loopback bind on port **8007**,
and `SoftResourceLimits NumberOfFiles 8192` (launchd's default 256 is not the
shell's `ulimit -n`; a descriptor leak otherwise only appears in production).

It is an **example**. Bootstrap it only with explicit authorization, and only
after the implementation lands — installing it today starts a service whose
domain routes do not exist. Tailscale Serve ingress has **not** been configured;
it would take `:8452` (8443 and 8445–8451 are allocated).

## What does not work yet

Honest list of every gap, so nothing here reads as finished:

- **No feature code.** No `/api/*` route exists except the version endpoint.
  The `[[apps]]` portfolio entry therefore pins the scaffold test only.
- **No auth.** The identity / CSRF / Origin middleware is a `TODO` in
  `create_app`. The scaffold is loopback-safe by construction only — do not
  expose it off loopback, and do not treat it as production-ready.
- **No icons.** `app/static/icons/` is a placeholder README; all four PNGs the
  manifest and the `apple-touch-icon` link reference are missing. iOS falls back
  to a page screenshot for the Home Screen icon. See that README.
- **`sw.js` `SHELL_ASSETS` must be extended for every new static file.** The
  precache list covers every file currently in the tree, and
  `tests/js/shell_assets.test.mjs` fails a commit that adds a module or
  stylesheet under `app/static/js` (beyond the vendored `js/pwa/` boot modules)
  or `app/static/css` without a matching precache entry, in either direction. An
  omission is a silent offline-shell hole: the file works online and is simply
  absent from the installed app.
- **No `.env` loading library.** The app reads `os.environ`; use `env`, a
  launchd `EnvironmentVariables` dict, or a `direnv`/shell export. Copying
  `.env.example` to `.env` documents intent but nothing parses it yet.
- **No installer script.** `APP_DATA_DIR` is not created for you; `mkdir -p` it
  with mode `0700`.
- **`mypy` scope is `app` only** (mirroring the sibling's command, whose
  `files` list also names `tests` but whose CI step checks `app/...` paths).
  `tests/` is not type-checked yet.
- **`[dev]` has no browser tooling.** There are no Playwright tests; the
  visual/interaction surface is unverified.

## Related projects

- Infrastructure package: [`syyangv/pwa-template`](https://github.com/syyangv/pwa-template)
  (`docs/pwa-template.md` is the pattern reference this scaffold follows).
- Closest sibling: [`pwa-obsidian-daily`](https://github.com/syyangv/pwa-obsidian-daily)
  — vault reads, daily-note writes, ESM, launchd.
- Catalog producer: [`wholefoods-to-pantry`](https://github.com/syyangv/wholefoods-to-pantry)
  — sole writer of `pantry_items.db`; this app only reads it.
- Domain vocabulary: [`CONTEXT.md`](CONTEXT.md).

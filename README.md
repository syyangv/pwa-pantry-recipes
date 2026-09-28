# Pantry Recipes PWA

A local, installable Progressive Web App for **deciding what to cook from what
is already in the pantry**, and for recording the cook in the Obsidian daily
note.

> **Status (2026-09-27): infrastructure and domain primitives, no user-facing
> feature yet.** The repository contains the vendored pwa-infra shell, a
> fail-typed configuration surface, the LaunchAgent template, CI, and — shipped
> since the scaffold — the identity/CSRF/Origin guards with `GET /api/session`,
> the four PWA icons and their generator, the owned SQLite schema and connection
> layer, the read-only `PantryCatalog`, the recipe index and note parser over
> `RECIPES_ROOT`, the atomic-write / frontmatter / section / daily-path vault
> primitives, and the pure frontend logic modules (chip classifier, headline
> formatter, recipe sort). **None of it is reachable from the UI.** The app boots
> and serves `/`, `/health`, `/api/version`, `/api/session`, `/sw.js`, the
> manifest, and the static assets; the placeholder panel says so. There is no
> pantry view, no recipe list, and no cooking-log write, and the two engines that
> produce them are unbuilt. See
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
  `npm` exists only to run `node --test` over the boot-contract and pure-logic
  gates.
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

# 2. configure (SERVER-OWNED values; see .env.example for the full contract).
#    Nothing parses a .env file — app/config.py reads os.environ — so either
#    `set -a; . ./.env; set +a` in this shell, or copy the file and load it with
#    `direnv`, or pass the variables inline. `cp .env.example .env` on its own
#    records intent and configures nothing.
cp .env.example .env
$EDITOR .env
set -a; . ./.env; set +a

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
automatically. Because the app reads its configuration at **startup** and
`.env` is never read, changes to those variables need a restart — `--reload`
will not pick them up.

## Verification

```bash
# Convergence gate (/api/version == sw.js CACHE_VERSION), session contract,
# route table, security headers, and the icon generator's --check
.venv/bin/python -m pytest tests/scaffold/ -q

# Whole Python suite
.venv/bin/python -m pytest

# Lint / types
.venv/bin/python -m ruff check app tests
.venv/bin/python -m mypy app

# Frontend gates (no dependencies; `npm install` is not required)
npm test          # node --test 'tests/js/**/*.test.mjs' — the glob is QUOTED.
                  # /bin/sh has no globstar, so an unquoted `**` narrows to one
                  # level and the runner silently collects a subset. See
                  # tests/js/collection-probe.mjs, which re-runs this exact
                  # script and fails if any expected test file is missing.
npm run check     # node --check on the module graph entry + the worker

# Vendored-infra drift gate (what the pre-commit hook runs)
python3 ~/projects/pwa-template/scripts/vendor.py --check .

# The package check CI runs: the built wheel must carry the static shell.
# `build/` and the egg-info MUST be removed first — see the note below.
rm -rf /tmp/wheel build pwa_pantry_recipes.egg-info
.venv/bin/python -m pip wheel . --no-deps \
  --no-build-isolation --wheel-dir /tmp/wheel
```

### Why the wheel command cleans two directories

`pip wheel .` is not hermetic here, and it fails **silently** — the build
reports success either way. `setuptools` copies sources into `build/lib` and
**never cleans it**, so any file present in a previous build ships in the next
wheel even if it no longer exists in the tree. Verified here: with a leftover:
`build/lib/app/STALE_LEFTOVER.txt`, the same command produced a **48**-file wheel
containing that file; after `rm -rf build` it produced the correct **47**-file
wheel. `include-package-data = true` also reads
`pwa_pantry_recipes.egg-info/SOURCES.txt`, which is git-ignored, so a stale one
can describe a tree that no longer exists. Cleaning both makes the local number
comparable to CI's, which builds from a fresh checkout and therefore never sees
either directory.

CI is not affected: `actions/checkout` gives a clean tree, and the
`pip install ".[test,dev]"` step that runs before the wheel build regenerates
`SOURCES.txt` from that same clean tree, so there is no stale input to inherit.

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

Honest list of every gap, so nothing here reads as finished. `AGENTS.md` tells
readers to trust this section, so every bullet below is stated as something you
can check against the tree, not as a claim about intent.

- **No domain routes.** The only `/api/*` endpoints are `GET /api/version` and
  `GET /api/session`; anything else under `/api/` returns the 404 envelope
  (`GET /api/recipes` → `404 {"code":"not_found"}`). The `TODO(implementation)`
  block in `app/main.py` names the three routers that do not exist yet. Nothing
  in `app/recipes/`, `app/pantry/`, `app/vault/`, or `app/db/` is reachable from
  a request.
- **No lifespan wiring.** `create_app`'s `lifespan` is a bare `yield` with a
  `TODO(implementation)`. The SQLite connection, the migration run, the
  `PantryCatalog`, the recipe index, and the `AtomicNoteStore` are all
  constructed nowhere at startup. Every shipped module is exercised only by its
  own tests.
- **No frontend views.** `app/static/index.html` renders a "Scaffold" panel that
  says infrastructure-only. There is no `app/static/js/views/`, no router, and
  nothing imports the three `logic/` modules: `chipClass()`, `headline()`, and
  `sortRecipes()` are exercised only by their `node --test` files. They are in
  `sw.js`'s `SHELL_ASSETS`, and nothing else in `app/static/` references them
  but that precache list.
- **Both engines are unbuilt.** The pantry **match** engine (F1's stock join and
  the tier ladder, `app/pantry/stock_join.py` and `app/recipes/matcher.py`) and
  the **daily-note cooking-log write** (`append_cook_link`, and the
  `AtomicNoteStore` commit around it) do not exist. The recipe index, the
  catalog, and the vault primitives they would call are the *inputs*, already
  shipped; the matching and the write are not.
- **No deployment.** `scripts/pwa-pantry-recipes.example.plist` is a template
  only and has never been bootstrapped; Tailscale Serve ingress is not
  configured. See [Deployment](#deployment-planned). The one access control that
  *is* real today is `app/auth.py` — the identity / Origin / CSRF / Host guards
  wrap every response and `validate_bind_invariant` refuses startup on a
  non-loopback `BIND_HOST` in every mode, so the earlier "loopback-safe by
  construction only" caveat no longer applies. It guards an app with no domain
  routes behind it, which is not the same as being production-ready.
- **`sw.js` `SHELL_ASSETS` and `package-data` must be extended for every new
  static file.** The precache list covers every file currently in the tree, and
  `tests/js/shell_assets.test.mjs` fails a commit that adds a module or
  stylesheet under `app/static/js` (beyond the vendored `js/pwa/` boot modules)
  or `app/static/css` without a matching precache entry, in either direction. An
  omission is a silent offline-shell hole: the file works online and is simply
  absent from the installed app. The wheel side of the same hole is gated too —
  `test_every_static_file_is_covered_by_a_package_data_glob` fails a commit that
  adds a file under `app/static/` that no `package-data` glob matches, which is
  what let `app/static/js/logic/*.js` ship in `SHELL_ASSETS` but in no wheel
  (`static/js/*` matched `main.js` and the *subdirectories*, and glob `*` does
  not cross a `/`). Note the registration is per directory: a new subdirectory
  under `static/js/` needs its own `static/js/<name>/*` entry, and all three of
  `SHELL_ASSETS`, `package-data`, and CI's wheel `required` list have to be
  extended together or the app works online and 404s offline.
- **`npm test`'s glob is load-bearing and fragile.** `package.json` runs
  `node --test 'tests/js/**/*.test.mjs'` with the pattern **quoted**, because
  `npm` invokes the script through `/bin/sh`, which has no `globstar`: an
  unquoted `**` narrows to one level, the runner is handed a list with whole test
  files missing, and the suite reports 0 failures having never started them. No
  reporter on Node 22 names the files it ran, so
  `tests/js/collection-probe.mjs` re-runs the real script through the same shell
  and asserts the collected set. Do not unquote that pattern.
- **No `.env` loading library.** The app reads `os.environ`; use `env`, a
  launchd `EnvironmentVariables` dict, or a `direnv`/shell export. Copying
  `.env.example` to `.env` documents intent but nothing parses it yet.
- **No installer script.** `APP_DATA_DIR` is not created for you; `mkdir -p` it
  with mode `0700`.
- **`mypy` scope is `app` only.** `pyproject.toml`'s `files` list names both
  `app` and `tests`, but the command this repo documents, runs in `AGENTS.md`,
  and runs in CI is `mypy app`, so `tests/` is not type-checked.
- **`[dev]` has no browser tooling.** Playwright lives in the separate `browser`
  extra and CI never installs it, so there are no Playwright tests; the visual
  and interaction surface is unverified.

## Related projects

- Infrastructure package: [`syyangv/pwa-template`](https://github.com/syyangv/pwa-template)
  (`docs/pwa-template.md` is the pattern reference this scaffold follows).
- Closest sibling: [`pwa-obsidian-daily`](https://github.com/syyangv/pwa-obsidian-daily)
  — vault reads, daily-note writes, ESM, launchd.
- Catalog producer: [`wholefoods-to-pantry`](https://github.com/syyangv/wholefoods-to-pantry)
  — sole writer of `pantry_items.db`; this app only reads it.
- Domain vocabulary: [`CONTEXT.md`](CONTEXT.md).

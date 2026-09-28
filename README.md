# Pantry Recipes PWA

A local, installable Progressive Web App for **deciding what to cook from what
is already in the pantry**, and for recording the cook in the Obsidian daily
note.

> **Status (2026-09-28): the features are shipped; the app is not deployed.**
> Every route in `docs/spec/2026-09-27-pantry-recipes.md` §9.16 exists and is
> reachable: the recipe list and detail views, F1's three-tier Stock Join, D4's
> honest match display and the 溯源 provenance table with F2's two repair
> actions, the three Meal Shortlists, the page-owned offline outbox with the
> `X-Client-Id` exactly-once ledger, and the Cooking Log write. The `lifespan`
> opens and closes all of it. The identity / Origin / CSRF / Host / content-type
> guards are real, and an unauthenticated mutation is refused before it reaches
> a route.
>
> **It has never been run as a service.** `AGENTS.md` records deployment as not
> authorized, `scripts/pwa-pantry-recipes.example.plist` is a template, there is
> no LaunchAgent in `~/Library/LaunchAgents`, and no Tailscale Serve route exists
> on the planned ingress port 8452. The deploy path — the rendered plist, the
> staged installer, the Tailscale plan, and a **runnable** converge gate — is
> built and verified; see [Deployment](#deployment) and
> [`docs/runbook/deployment.md`](docs/runbook/deployment.md). What is genuinely
> missing is in [What does not work yet](#what-does-not-work-yet).

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

# The release gate. Exit 0 converged, 1 failed, 2 a condition could not be
# evaluated — and 2 is NOT a pass. See docs/runbook/deployment.md § 7.
# --owner-login is required in the production posture (TRUST_TAILSCALE_HEADERS=
# true), because app/auth.py guards every path. It is a Settings value, not a
# secret: it is the login the Tailscale proxy already injects.
.venv/bin/python scripts/converge_gate.py \
  --local-origin http://127.0.0.1:8007 \
  --deployed-origin https://home-macbook-air.tailcd6e49.ts.net:8452 \
  --owner-login <TAILSCALE_OWNER_LOGIN> \
  --vault /Users/syang/obsidian/syang
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

## Deployment

**Not authorized, and not done.** `AGENTS.md` § *Deploy* records deployment as
not yet authorized. There is no plist in `~/Library/LaunchAgents`, nothing named
`com.syang.pwa-pantry-recipes` in `launchctl list`, and no Tailscale Serve route
on the planned ingress port. What exists is the *path*: a template that renders
and lints, a staged installer, a Serve plan, and a converge gate that runs.

Two ports, never one end-to-end: the app binds loopback **8007**, the phone
reaches **8452**, and Tailscale Serve proxies between them. Never `:443`; 8000
and 8002–8006 are allocated to sibling PWAs, as are Serve ingresses 8443 and
8445–8451.

### The artifacts

| File | What it is |
|---|---|
| `scripts/pwa-pantry-recipes.example.plist` | The template. `__UPPER_CASE__` placeholders, loopback bind on 8007, `SoftResourceLimits NumberOfFiles 8192`, `ThrottleInterval 2`, `KeepAlive`, `--timeout-graceful-shutdown 5`, and the full `EnvironmentVariables` contract. `tests/deploy/test_launchagent_template.py` asserts each of those against the file and against `app/config.py`. |
| `scripts/install_launchagent.sh` | Three stages: render + `plutil -lint` (no side effects), `--apply` (creates `APP_DATA_DIR` 0700 and the log directory, copies the plist, **starts nothing**), `--bootstrap` (bootout + bootstrap — the step that needs authorization). Plus `--teardown` for the rollback. |
| `scripts/converge_gate.py` | The release gate: nine conditions, stdlib-only, exit 0 / 1 / 2. |
| `scripts/converge-smoke.json` | The release-specific half of condition 4. **Edit it in every release.** |
| [`docs/runbook/deployment.md`](docs/runbook/deployment.md) | The operational form: the Serve commands with the audit before and after, the install stages, the restart rules, and the participant-identity validation. |

### The converge gate, in one line

```bash
.venv/bin/python scripts/converge_gate.py \
  --local-origin http://127.0.0.1:8007 \
  --deployed-origin https://home-macbook-air.tailcd6e49.ts.net:8452 \
  --vault /Users/syang/obsidian/syang
```

It checks the listener (not the process), whether the origin answers the gate at
all and in which identity posture, the source `CACHE_VERSION` against
local `/api/version`, local against deployed, the `X-PWA-Backend-Started-At`
timestamp against every changed startup-loaded file, a release-specific live API
smoke check, that the **served** `/sw.js` carries this `CACHE_VERSION` *and* that
`CACHE_VERSION` was rotated since the last mutable frontend change, that the
served HTML pins the same versioned assets as the SW cache name, that the served
update manager re-checks on resume, and that an open confirmation flow postpones
the reload. **Exit 2 means a condition could not be evaluated, which is not a
pass** — before a Serve route exists, that is the correct answer.

### The auth posture this deploy path would publish

Real, installed, and wrapping every response. An unauthenticated mutation is
refused before it reaches a route: `403 origin_not_allowed` without a matching
`Origin`, `403 csrf_required` without a token, `401 identity_spoof` for any
client-supplied `Tailscale-*` header in development-identity mode. With
`OBSIDIAN_READ_ONLY=true` every mutation is `403 read_only` with **no write
allowlist** (F18). The stopping point is not the guards — it is that
`bootstrap` starts a service which appends to the real daily note, and
`tailscale serve` puts it on a surface a phone can reach. Both are one command
away and neither was run.

## What does not work yet

Honest list of every gap, so nothing here reads as finished. `AGENTS.md` tells
readers to trust this section, so every bullet below is stated as something you
can check against the tree, not as a claim about intent.

- **No deployment — the real one.** There is no
  `~/Library/LaunchAgents/com.syang.pwa-pantry-recipes.plist`, no
  `com.syang.pwa-pantry-recipes` in `launchctl list`, and no Tailscale Serve
  route on the planned ingress port 8452. The app has only ever run as a
  foreground dev server. The deploy path is built and verified
  ([Deployment](#deployment), `docs/runbook/deployment.md`) but
  `install_launchagent.sh --bootstrap` and `tailscale serve` have never been run,
  because `AGENTS.md` records deployment as unauthorized and neither was
  authorized in the session that built it. **Everything below this bullet is a
  smaller gap than this one.**
- **The converge gate has never seen a deployed origin.** It exits 2 against a
  loopback server, which is the honest answer: conditions 2, 3 and 4 have no
  second origin to compare against until the Serve route exists. Its nine
  conditions are each proven able to fail (`tests/deploy/test_converge_gate.py`,
  and against a live server in the session that wrote it), but a gate that has
  only ever seen one origin has only ever seen half of the problem.
- **`scripts/converge-smoke.json` is a one-release artifact.** It is populated for
  v0.6.0 and nothing rewrites it. A release that forgets to edit it asserts that
  *last* release's fields still exist, which is weaker than it looks; the gate
  cannot detect the omission, because "the spec was not updated" is not a fact
  visible from the running server. The runbook makes it a release step.
- **`CACHE_VERSION` is behind, and the converge gate says so on the first real
  run.** `cf39101` changed two mutable frontend files —
  `app/static/js/router.js` and `app/static/js/views/recipe.js` — without
  rotating `CACHE_VERSION`, which is still `v0.6.0` from `b5292b9`. Condition 5
  of the gate reports exactly that, against the real tree:

  > `CACHE_VERSION was NOT rotated even though these mutable frontend files
  > changed since the commit that last rotated CACHE_VERSION (b5292b96691f):
  > app/static/js/router.js, app/static/js/views/recipe.js`

  This is the recorded incident class — a deploy that is internally consistent
  while re-using the previous Service Worker cache key — found by the check that
  was written to find it, on its first run against a real HEAD. It is **not**
  fixed here: the rotation belongs to the release that carries those changes, and
  editing another agent's `CACHE_VERSION` from this ticket would be exactly the
  kind of quiet cross-commit edit the rule exists to prevent. The next release
  bumps it (`sw.js` CONFIG block only) and re-runs the gate.
- **The browser suite is opt-in and CI never runs it.** `playwright` lives in the
  separate `browser` extra (F10), so `pip install ".[test,dev]"` never installs
  it, a default `pytest` *collects and skips* both flows, and neither the visual
  nor the interaction surface is verified on CI. Run it deliberately:
  `.venv/bin/python -m pip install ".[browser]" && .venv/bin/python -m pytest tests/browser -q`.
- **`sw.js` `SHELL_ASSETS` and `package-data` must be extended together, every
  time.** The precache list covers every file currently in the tree, and
  `tests/js/shell_assets.test.mjs` fails a commit that adds a module or
  stylesheet under `app/static/js` (beyond the vendored `js/pwa/`) or
  `app/static/css` without a matching precache entry, in either direction. An
  omission is a silent offline-shell hole: the file works online and is simply
  absent from the installed app. The wheel side is gated by
  `test_every_static_file_is_covered_by_a_package_data_glob`, and the registration
  is **per directory, one level deep** — a new subdirectory under `static/js/`
  needs its own `static/js/<name>/*` entry, because glob `*` does not cross a `/`.
  All three of `SHELL_ASSETS`, `package-data`, and CI's wheel `required` list
  have to move together or the app works online and 404s offline.
- **`npm test`'s glob is load-bearing and fragile.** `package.json` runs
  `node --test 'tests/js/**/*.test.mjs'` with the pattern **quoted**, because
  `npm` invokes the script through `/bin/sh`, which has no `globstar`: an
  unquoted `**` narrows to one level, the runner is handed a list with whole test
  files missing, and the suite reports 0 failures having never started them. No
  reporter on Node 22 names the files it ran, so
  `tests/js/collection-probe.mjs` re-runs the real script through the same shell
  and asserts the collected set. Do not unquote that pattern.
- **No `.env` loading library.** The app reads `os.environ`, full stop. Use
  `env`, a launchd `EnvironmentVariables` dict (which is what the LaunchAgent
  template uses, and the only mechanism that configures the service), or a
  `direnv`/shell export. Copying `.env.example` to `.env` documents intent and
  configures nothing — and `install_launchagent.sh` deliberately does not create
  one, because a second source of truth for the same values is how they drift.
- **`mypy` scope is `app` only.** `pyproject.toml`'s `files` list names both
  `app` and `tests`, but the command this repo documents, runs in `AGENTS.md`,
  and runs in CI is `mypy app`, so `tests/` and `scripts/` are not
  type-checked. `scripts/converge_gate.py` in particular is untyped-checked and
  is gated by its own tests instead.
- **`scripts/` is not linted.** `ruff check app tests` covers `app/` and
  `tests/`, so the two deploy scripts are held to review rather than to `ruff`.

## Related projects

- Infrastructure package: [`syyangv/pwa-template`](https://github.com/syyangv/pwa-template)
  (`docs/pwa-template.md` is the pattern reference this scaffold follows).
- Closest sibling: [`pwa-obsidian-daily`](https://github.com/syyangv/pwa-obsidian-daily)
  — vault reads, daily-note writes, ESM, launchd.
- Catalog producer: [`wholefoods-to-pantry`](https://github.com/syyangv/wholefoods-to-pantry)
  — sole writer of `pantry_items.db`; this app only reads it.
- Domain vocabulary: [`CONTEXT.md`](CONTEXT.md).

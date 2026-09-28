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
> **It is now running as a service, and its verification is not finished.**
> Deployed 2026-09-28: the plist is installed, `com.syang.pwa-pantry-recipes` is
> loaded and `state = running`, and a tailnet-only Tailscale Serve route proxies
> **8452 → 127.0.0.1:8007**, so the deployed origin is
> `https://home-macbook-air.tailcd6e49.ts.net:8452`. Two things are genuinely
> open, and neither is cosmetic: the converge gate **exits 1** when run from the
> serving host (Serve attributes no identity to a self-originated request, so
> the seven deployed-origin conditions return `401 identity_missing`), and the
> runbook's **participant-identity check is not done** — it needs a phone, and
> so no observer has yet seen an authenticated 200 from the deployed origin.
> Every request this deploy made over the network was refused, which is the safe
> direction to be wrong in. See [Deployment](#deployment) and
> [`docs/runbook/deployment.md`](docs/runbook/deployment.md); both gaps are
> stated in [What does not work yet](#what-does-not-work-yet).

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
.venv/bin/python -m ruff check app tests scripts
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

**Deployed 2026-09-28, on a tailnet-only origin.** The user authorized it
in-session, so `~/Library/LaunchAgents/com.syang.pwa-pantry-recipes.plist` is
installed, `com.syang.pwa-pantry-recipes` is loaded with `state = running`, and
`tailscale serve status` lists the ingress. What is *not* true is that the
release gate has passed: it exits **1** when run from the serving host, and the
participant-identity check the runbook requires has not been done. Both are
stated precisely in [What does not work yet](#what-does-not-work-yet); read them
before claiming the deploy is verified.

Two ports, never one end-to-end: the app binds loopback **8007**, the phone
reaches **8452**, and Tailscale Serve proxies between them. Never `:443`; 8000
and 8002–8006 are allocated to sibling PWAs, as are Serve ingresses 8443 and
8445–8451.

| | |
|---|---|
| Deployed origin | `https://home-macbook-air.tailcd6e49.ts.net:8452` (tailnet only) |
| App bind | `127.0.0.1:8007` — loopback, never `0.0.0.0` |
| `TAILSCALE_OWNER_LOGIN` | `syyangv@github` |
| `TRUST_TAILSCALE_HEADERS` | `true` (behind the loopback proxy) |
| `OBSIDIAN_READ_ONLY` | `true` — every mutation is `403 read_only` |
| `APP_DATA_DIR` | `~/.local/share/pwa-pantry-recipes`, mode `0700` |
| `SoftResourceLimits` | `NumberOfFiles 8192` — required, see below |
| Logs | `~/Library/Logs/pwa-pantry-recipes/server.log` |

`NumberOfFiles 8192` is not decoration. `AtomicNoteStore` pins directory
descriptors for the process lifetime by design, and launchd gives a LaunchAgent a
**256** soft limit, not the shell's `ulimit -n`. Under exhaustion the vault
writes fail. Confirm it in the rendered plist and in `launchctl print` before
trusting it.

### Restart rules

```bash
# code-only change: the job is already loaded, so this is correct and enough
launchctl kickstart -k gui/$(id -u)/com.syang.pwa-pantry-recipes

# plist edit: kickstart IGNORES the plist, so this is the only correct pair
launchctl bootout   gui/$(id -u)/com.syang.pwa-pantry-recipes
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.syang.pwa-pantry-recipes
```

Never `--force`. After **any** restart, prove the socket rather than the process:
`launchctl print` showing `state = running` means a process is alive, not that
the port is listening.

### The artifacts

| File | What it is |
|---|---|
| `scripts/pwa-pantry-recipes.example.plist` | The template, now actually rendered and installed. `__UPPER_CASE__` placeholders, loopback bind on 8007, `SoftResourceLimits NumberOfFiles 8192`, `ThrottleInterval 2`, `KeepAlive`, `--timeout-graceful-shutdown 5`, and the full `EnvironmentVariables` contract. `tests/deploy/test_launchagent_template.py` asserts each of those against the file and against `app/config.py`. |
| `scripts/install_launchagent.sh` | Three stages: render + `plutil -lint` (no side effects), `--apply` (creates `APP_DATA_DIR` 0700 and the log directory, copies the plist, **starts nothing**), `--bootstrap` (bootout + bootstrap; run 2026-09-28). Plus `--teardown` for the rollback. |
| `scripts/converge_gate.py` | The release gate: nine conditions, stdlib-only, exit 0 / 1 / 2. |
| `scripts/converge-smoke.json` | The release-specific half of condition 4. **Edit it in every release.** |
| [`docs/runbook/deployment.md`](docs/runbook/deployment.md) | The operational form: the Serve commands with the audit before and after, the install stages, the restart rules, and the participant-identity validation. |

### The converge gate, in one line

```bash
.venv/bin/python scripts/converge_gate.py \
  --local-origin http://127.0.0.1:8007 \
  --deployed-origin https://home-macbook-air.tailcd6e49.ts.net:8452 \
  --owner-login syyangv@github \
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
pass.** Run it from a host that is **not** the serving node — see below.

### The auth posture the deployed origin actually publishes

Real, installed, and wrapping every response, in this order: **host (400) →
identity (401) → read-only (403) → body size (413) → origin (403) →
content-type (415) → CSRF (403)**.

Measured against the deployed origin over the network, unauthenticated:

| Request | Response |
|---|---|
| `POST /api/cook-logs`, no `Origin`, no CSRF | `401 {"code":"identity_missing"}` |
| `POST /api/cook-logs`, `Origin: https://evil.example` | `401 {"code":"identity_missing"}` |
| `POST /api/cook-logs`, `Tailscale-User-Login: attacker@evil` | `401 {"code":"identity_missing"}` |
| `DELETE …/ingredients/0/mapping` spoofing the **owner** login | `401 {"code":"identity_missing"}` |

Two things about that table are load-bearing. First, **Serve strips the client's
`Tailscale-User-Login`**, so spoofing the owner is refused exactly as a missing
header is — the app never sees the forged value. Second, **identity precedes
`Origin`**, so an unauthenticated request is stopped at identity and never
reaches the `Origin` or CSRF checks; that is why every row above is `401` and not
`403`.

With `OBSIDIAN_READ_ONLY=true`, `read_only` also precedes `Origin`/CSRF, so on
the deployed instance every authenticated mutation is `403 read_only` and the
Origin and CSRF codes are unreachable. They were verified on a **throwaway
instance against a copied vault** (never the real one):

| Request | Response |
|---|---|
| no `Origin` | `403 {"code":"origin_not_allowed"}` |
| `Origin: https://evil.example` | `403 {"code":"origin_not_allowed"}` |
| correct `Origin`, no CSRF token | `403 {"code":"csrf_required"}` |
| correct `Origin`, bad CSRF token | `403 {"code":"csrf_invalid"}` |
| correct `Origin`, `Content-Type: text/plain` | `415 {"code":"unsupported_media_type"}` |
| `Tailscale-User-Login: attacker@evil` | `401 {"code":"identity_denied"}` |

Note the last row: in the **production** posture a mismatched login is
`identity_denied`. The code `identity_spoof` belongs to the *development*
posture (`TRUST_TAILSCALE_HEADERS=false`), where any client-supplied
`Tailscale-*` header is refused; it cannot appear on a correctly deployed
instance.

## What does not work yet

Honest list of every gap, so nothing here reads as finished. `AGENTS.md` tells
readers to trust this section, so every bullet below is stated as something you
can check against the tree, not as a claim about intent.

- **The converge gate has never exited 0, and cannot be made to from the serving
  host.** As deployed it exits **1**, identically before and after a
  `launchctl kickstart -k`. Conditions 0, 0b and 1 pass; the seven
  deployed-origin conditions all fail with `401 identity_missing`. The cause is
  diagnosed and is not a defect in the deploy: **Tailscale Serve injects no
  identity header for a request originating from the node doing the serving.** It
  strips the client's `Tailscale-User-Login` (so a spoofed header is discarded
  rather than trusted) and has no remote peer to attribute the request to. The
  gate's fetcher presents `Tailscale-User-Login` on *both* origins, so over the
  proxy that header is thrown away and the backend answers `identity_missing`.
  Proven, not inferred: the same header against loopback returns `200`, and a
  request through the proxy returns `401` for no header, a forged header, and
  the *correct owner* header alike. This is a **vantage-point** limit, so the
  fix is to run the gate from a host that is not the serving node. What must not
  happen is what was available and was refused: passing `--baseline`, or editing
  a condition to agree. The gate's exit 2 is gone — conditions 2/3/4 now have a
  second origin and are *evaluated and failing*, which is a stronger statement
  than "unevaluated".
- **The participant-identity check is NOT DONE — this is the real remaining
  gap.** `docs/runbook/deployment.md` §8 requires a phone off the host's own
  network: the PWA port succeeds, unrelated HTTPS ports fail, SSH fails. The
  deploy session had no phone reachable from it, so this was not performed and
  is recorded as not done rather than approximated. Consequently **no observer
  has yet seen an authenticated 200 from the deployed origin**, and the whole
  identity chain past the proxy is unexercised. A loopback check does not
  substitute: it shares the host's tailnet position and its loopback, which is
  the exact thing §8 rules out. The tailnet does contain online phones
  (`mieiphone`, `100.99.212.85`, logged in as `syyangv@`), so this is finishable
  in one step by the user from a phone — it was not finishable from a shell.
- **The deployed service has only ever been observed failing closed.** Every
  request this deploy made over the network was refused, which is the safe
  direction, but it means the success path — a participant opening the PWA and
  getting data — is unverified. Loopback with the owner login does return `200`
  with a full `/health` payload (vault readable, `pantry_items.db` 178 rows, 16
  recipes), so the app is healthy; it is the *exposed* surface that is unproven.
- **`scripts/converge-smoke.json` is a one-release artifact.** It is populated for
  v0.6.0 and nothing rewrites it. A release that forgets to edit it asserts that
  *last* release's fields still exist, which is weaker than it looks; the gate
  cannot detect the omission, because "the spec was not updated" is not a fact
  visible from the running server. The runbook makes it a release step.
- **The `CACHE_VERSION`-behind incident: found by the gate, fixed by the release.
  Kept here as the worked example of what condition 5 is for.** `cf39101` changed
  two mutable frontend files — `app/static/js/router.js` and
  `app/static/js/views/recipe.js` — without rotating `CACHE_VERSION`, which was
  still `v0.6.0` from `b5292b9`. This is the recorded incident class: a deploy
  that is *internally consistent* (clean tree, matching versions, correct
  `SHELL_ASSETS`) while re-using the previous Service Worker cache key, which
  every other condition passes in. Condition 5 of the gate reported exactly that,
  against the real tree, on its first live run:

  > `CACHE_VERSION was NOT rotated even though these mutable frontend files
  > changed since the commit that last rotated CACHE_VERSION (b5292b96691f):
  > app/static/js/router.js, app/static/js/views/recipe.js`

  **It is fixed.** `a303d4a` carried the rotation to `v0.7.0`, and `CACHE_VERSION`
  has since been rotated again to **`v0.7.1` in `fb2577e`**. The anchor
  `git log -1 -G"const CACHE_VERSION" -- app/static/sw.js` therefore resolves to
  **`fb2577e`** (HEAD), not to `a303d4a`, and `git diff --name-only fb2577e..HEAD
  -- app/static ':!app/static/sw.js'` is **empty** — so condition 5's rotation
  half passes against the tree as it stands, and the gate's
  fixture-follows-shipping behaviour is in `516675f`. Two things are worth
  keeping from this. First, **the check works**: the one condition that can catch
  a self-consistent bad deploy caught a real one, unprompted, before anything was
  installed. Second, **the lesson is now in the release sequence** rather than in
  this list — `AGENTS.md` § *Deploy* puts the `CACHE_VERSION` bump first in that
  sequence and `docs/runbook/deployment.md` § 7 makes the gate the thing that
  confirms it, so a repeat is a release-sequencing error rather than a discovery
  this section has to keep re-issuing.
  **What is still true, and is the real limit of the fix:** a rotation is a
  *commit-order* obligation, not a code property. Nothing in the app can detect a
  missing bump at review time — only the gate, at release time, against a real
  HEAD. The rotation is not retroactive, so a frontend change made now and
  released without the bump is invisible to every test in this repo.
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
- **`scripts/` is linted, with one quarantined file.** The gate is `ruff check
  app tests scripts`, so the deploy scripts are held to the same rules as the
  app. `scripts/converge_gate.py` is the exception: it carries a 37-error
  backlog (33 `E501`, 3 `UP017`, 1 `F541`) listed by rule in
  `pyproject.toml`'s `[tool.ruff.lint.per-file-ignores]`, because a concurrent
  hand-edit of it was not available when the gate widened. A *new* class of
  error in that file still fails the gate. Clear it and delete the entry.

## Related projects

- Infrastructure package: [`syyangv/pwa-template`](https://github.com/syyangv/pwa-template)
  (`docs/pwa-template.md` is the pattern reference this scaffold follows).
- Closest sibling: [`pwa-obsidian-daily`](https://github.com/syyangv/pwa-obsidian-daily)
  — vault reads, daily-note writes, ESM, launchd.
- Catalog producer: [`wholefoods-to-pantry`](https://github.com/syyangv/wholefoods-to-pantry)
  — sole writer of `pantry_items.db`; this app only reads it.
- Domain vocabulary: [`CONTEXT.md`](CONTEXT.md).

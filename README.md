# Pantry Recipes PWA

A local, installable Progressive Web App for **deciding what to cook from what
is already in the pantry**, and for recording the cook in the Obsidian daily
note.

> **Status (2026-09-28): the features are shipped and the app is deployed as a
> service; its verification is not finished.**
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
> open, and neither is cosmetic: the converge gate **exits 3** when run from the
> serving host (conditions 2–8 come back `VANTAGE-LIMITED`, because Serve
> attributes no identity to a self-originated request and the deployed origin
> therefore answers `401 identity_missing` to every identity the gate can
> present). The runbook's **participant-identity check has been performed** — a
> phone (`mieiphone`, `syyangv@`, `100.99.212.85`) loaded the app through the
> proxy on 2026-09-28 — but it is a **user attestation, not a machine-verified
> result**: no tool in this repo observed it, and it establishes reachability
> and identity, not convergence. Every request **the gate** makes over the
> network is still refused, so from the serving node the deployed origin is
> still only ever observed **failing closed** and the gate still exits 3, which
> is the correct and permanent answer for a single-node deploy. See
> [Deployment](#deployment) and
> [`docs/runbook/deployment.md`](docs/runbook/deployment.md); what remains open
> is in [What does not work yet](#what-does-not-work-yet).

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
- **Two tabs, one payload.** The bar is `菜谱` (`#/`, the recipe list) and `食材`
  (`#/pantry`, what the house has and what each thing makes); 清单, 溯源 and
  调试 live behind 更多. **Both tabs read the same `GET /api/recipes`.** That is
  the load-bearing part: the response already publishes two derived views of one
  `StockJoin` (`stockUnjoinedCount` and every slot's `stockJoinState`), so a
  second route re-reading `Pantry.md` through a second TTL window could
  contradict a number printed beside it in the first — and two tabs that
  disagree about the same jar is worse than one tab. `logic/ingredient-index.js`
  is the pure projection that groups slots and `Pantry.md` lines by Pantry Item;
  `js/tabbar.js` holds only the 更多 menu and the current-route mark, over static
  markup in `index.html` that exists so the bar is on screen at first paint
  offline.
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

# The release gate. Exit 0 converged, 1 a real failure, 2 a condition could not
# be evaluated because the invocation was under-specified, 3 a condition could
# not be evaluated because the deployed origin is not observable from this host.
# Only 0 is a pass. See docs/runbook/deployment.md § 7.
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

**Deployed 2026-09-28, serving `v0.7.3`, on a tailnet-only origin.** The user
authorized it in-session, so `~/Library/LaunchAgents/com.syang.pwa-pantry-recipes.plist` is
installed, `com.syang.pwa-pantry-recipes` is loaded with `state = running`, and
`tailscale serve status` lists the ingress. What is *not* true is that the
release gate has passed: it has never exited 0, and run from the serving host it
exits **3**, with conditions 2–8 `VANTAGE-LIMITED`. The runbook's
**participant-identity check has been performed** — attested by the user for its
positive half, machine-observed for its two negative halves, both negatives from
the serving node — which is a different fact and does not move the gate. Both are
stated precisely in
[What does not work yet](#what-does-not-work-yet); read them before claiming the
deploy is verified.

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
| `scripts/converge_gate.py` | The release gate: eleven checks — the ten numbered conditions `0`–`9` plus the `0b` identity precondition — stdlib-only, exit `0` converged / `1` a real failure / `2` under-specified invocation / `3` vantage-limited. Only `0` is a pass. |
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
  host.** Run from the serving host it exits **3**, identically before and after
  a `launchctl kickstart -k`. Conditions 0, 0b, 1 and 9 pass; conditions 2–8
  report
  **`VANTAGE-LIMITED`**, a fourth outcome that means *the deployed half of this
  comparison is not observable from this machine*. The cause is diagnosed and is
  not a defect in the deploy: **Tailscale Serve injects no identity header for a
  request originating from the node doing the serving.** It strips the client's
  `Tailscale-User-Login` (so a spoofed header is discarded rather than trusted)
  and has no remote peer to attribute the request to. The gate's fetcher
  presents `Tailscale-User-Login` on *both* origins, so over the proxy that
  header is thrown away and the backend answers `identity_missing`. Proven, not
  inferred: the same header against loopback returns `200`, and a request
  through the proxy returns `401` for no header, a forged header, and the
  *correct owner* header alike.

  The gate does not take that on trust. `classify_vantage` requires **two**
  positive observations before it will use the soft outcome: that the deployed
  origin refuses *every* identity the gate can present with the identical
  `identity_missing` envelope (a header that arrived and was rejected would say
  `identity_denied`, so this reads `app/auth.py`'s own vocabulary rather than
  assuming anything about Tailscale), **and** that the deployed origin's address
  is an address of this machine, measured by asking the routing table rather than
  by matching a hostname. Either alone stays `FAIL` — in particular, a deployed
  origin on *another* node refusing every identity is a real, reportable defect
  and is still `FAIL`. The reclassification is then applied only to a response
  that is exactly `401 identity_missing`, and only after each condition's real
  problems have been tested for, so a version mismatch, a stale
  `X-PWA-Backend-Started-At`, a missing response key or an un-rotated
  `CACHE_VERSION` still fails and still exits `1`.
  `tests/deploy/test_converge_gate_vantage.py` asserts each of those four
  failures *inside* an already-vantage-limited world, which is the only way a
  regression that widened the soft branch could be caught.

  The fix is to run the gate from a host that is not the serving node. What must
  not happen is what was available and was refused: passing `--baseline`, or
  editing a condition to agree.
- **The participant-identity check has been performed, on the user's
  attestation — and both negative checks are now closed, from the serving node.**
  `docs/runbook/deployment.md` §8 asked for a phone off the host's own network:
  the PWA port succeeds, unrelated HTTPS ports fail, SSH fails. On 2026-09-28
  the user did the first from `mieiphone` (`syyangv@`, `100.99.212.85`) and
  reported that **the app loads**, so the identity chain past the proxy is now
  exercised once and an authenticated 200 has been seen. This is a **user report,
  not a machine-verified result** — no tool here observed it — and it establishes
  reachability and identity, not convergence. The two negative checks were not
  part of what was reported and are now settled separately: unrelated HTTPS
  ports were probed and **refused** on 2026-09-28, and **SSH was verified
  refused on `100.87.56.102:22` on 2026-09-28** after the user turned macOS
  Remote Login off themselves with `sudo systemsetup -setremotelogin off` — the
  user, not an agent, and with no `/etc/sudoers.d` grant created or needed. Both
  were run from the **serving node**, and a probe from this node to its own
  `100.x` address never leaves the machine, so **neither exercises tailnet ACLs
  from a genuinely remote peer**; only a probe from a phone would. A loopback
  check still does not substitute for the positive one: it shares the host's
  tailnet position and its loopback, which is the exact thing §8 rules out.
- **The deployed service has only ever been observed failing closed *by the
  gate*.** Every request the gate made over the network was refused, because a
  self-originated request has no remote peer for Serve to attribute an identity
  to — which is also why the gate exits 3, permanently, from this host. The one
  authenticated success came from a different observer on a different machine. A
  `VANTAGE-LIMITED` run is a statement about the *observer*, never a statement
  that the deploy is fine. Loopback with the owner login does return `200` with a
  full `/health` payload (vault readable, `pantry_items.db` 178 rows, 16 recipes),
  so the app is healthy; it is the *converged exposed* surface that remains
  unobserved by machine.
- **A static change reached `main` unrotated, on 2026-09-28, and only the phone
  could prove the fix.** `66c43cd` shipped ~1,400 lines of new static code (the
  `食材` tab, its view, its logic module, three new `SHELL_ASSETS` entries) with
  `CACHE_VERSION` still at the `v0.7.2` set earlier the same day. On a
  cache-first shell that means an installed PWA kept executing the pre-`食材`
  code while `/api/version` reported a version it had no assets for — the same
  failure as the `cf39101` incident below, and the same one `pwa-wardrobe` PR
  `0fe6c1a` produced. The rotation (`1bf51d8`, `v0.7.3`) is what fixed it, and
  **what confirmed the fix is the user reporting the tab appearing on the phone**:
  that is a client observation no gate run from the serving host can make, and
  it is recorded here as a user attestation rather than as a machine result.
  The rule it violated is now written into `AGENTS.md`'s release sequence *and*
  enforced fleet-wide by `pwa-template/scripts/release_check.py`, which this repo
  runs as the floor under `converge_gate.py`.

- **`scripts/converge-smoke.json` cannot detect a release that adds a key and
  forgets to record it. That residue is irreducibly human; the rest is now
  checked on every `pytest`.** The gate fetches live URLs, so it can only see a
  *deployed* server, at *release* time, from whichever host it runs on — which on
  the serving node is the `VANTAGE-LIMITED` world above, where it never executes
  conditions 2–8 at all. A stale spec was therefore caught late, from one vantage,
  if at all. But the key sets are not only a fact about a running server: they are
  also constants in this repository, and two sources of one fact that are never
  compared are one source plus one source of drift.
  `tests/scaffold/test_smoke_spec.py` is that comparison, offline, on every run:
  the spec's `array_item_keys.recipes` against `LIST_RECIPE_KEYS`, its nested
  ingredient list against `SLOT_KEYS`, `top_level_keys` sorted so an addition is
  a line rather than a rewrite, and an assertion that no retired key
  (`trackerSynced`) survives anywhere in the file. **What it cannot catch is
  one-directional ADDITION**: a new key in a live response with nothing added to
  the spec leaves a perfectly consistent pair — the spec is a strict subset of the
  truth — so neither the gate nor this module fails. That is what the runbook's
  release step is for.

  The detail route `/api/recipes/{note_name}` is deliberately **not** in the
  spec, though it is where the 2026-09-28 release added three keys. The gate
  builds `f"{origin}{path}"` literally, so a parameterised path cannot resolve,
  and naming one concretely means hardcoding a vault recipe basename — which then
  fails for a reason unrelated to the release the moment that note is renamed.
  That is §9.12's `⚠ 已重命名` problem reintroduced into the release gate, trading
  a real regression for a spurious one. The detail shape is pinned exactly by
  `tests/api/test_recipes_api.py` instead. `/api/cook-logs` is likewise absent:
  it needs a `date` whose daily note may not exist, so a missing note would fail
  the gate for a reason that is not a regression.
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
  has since been rotated again — to **`v0.7.1` in `fb2577e`**, and to
  **`v0.7.2`** for the 食材 tab, which added `app/static/js/tabbar.js`,
  `app/static/js/views/pantry.js` and `app/static/js/logic/ingredient-index.js`
  and therefore re-used the same obligation: a new module that is precached but
  not reachable from the boot graph is exactly the `cf39101` incident class
  again. The anchor `git log -1 -G"const CACHE_VERSION" -- app/static/sw.js`
  therefore resolves to whichever commit last rotated it — **not** to
  `a303d4a` — and the property condition 5 actually checks is that
  `git diff --name-only <that anchor>..HEAD -- app/static ':!app/static/sw.js'`
  is **empty**, so the rotation half passes against the tree as it stands, and
  the gate's fixture-follows-shipping behaviour is in `516675f`. Re-run those two
  commands after any rotation rather than trusting the version number quoted
  here: the number moves every release and this sentence is the thing that goes
  stale quietly. Two things are worth
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
- **The browser suite now runs in CI, and a green tick there means it ran.**
  This was the worst gap on this list, because a green job that ran no browser is
  indistinguishable from one that passed: `playwright` lived in the separate
  `browser` extra, `pip install ".[test,dev]"` never installed it, a default
  `pytest` *collected and skipped* both flows, and CI reported success for the
  whole life of the suite. `.github/workflows/ci.yml` now has a second `browser`
  job that installs `.[browser]`, runs `playwright install --with-deps chromium`,
  and runs `pytest tests/browser` with **`PANTRY_BROWSER_REQUIRED=1`**.

  That env var is the load-bearing part, and it exists because a skip is not a
  failure. `tests/browser/optional.py` replaces both module-scope
  `pytest.importorskip` calls and the missing-Chromium skip with a helper that
  **fails** when the flows are required, so the job cannot pass by not running
  what it exists to run. Verified by hiding the browser binary: exit `1` with
  `PANTRY_BROWSER_REQUIRED=1`, and the flows still skip by default so a local
  `pytest` without the extra stays usable. The two failure modes are kept
  separate on purpose — a resolvable `playwright` with a missing binary is
  normal, because `pip install playwright` does not download Chromium, and
  telling someone to `pip install ".[browser]"` three times is how that advice
  stops working.

  It is a separate job rather than extra steps in `verify` because
  `playwright install --with-deps` downloads a browser and its system libraries;
  in `verify` that would push a 10-minute job past its budget on a cold runner
  and couple a module-parse check to a browser download. Locally it is still
  opt-in:
  `.venv/bin/python -m pip install ".[browser]" && .venv/bin/python -m playwright install chromium && .venv/bin/python -m pytest tests/browser -q`.
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
- **`npm test` no longer depends on a shell at all.** It was
  `node --test 'tests/js/**/*.test.mjs'`, and the quoting was load-bearing in a
  way nothing checked: `npm` runs scripts through `/bin/sh`, which has no
  `globstar`, so an unquoted `**` narrows to one level, whole test files go
  missing, and the suite reports **0 failures having started none of them**. No
  reporter on Node 22 names the files it ran, so the only guard was
  `tests/js/collection-probe.mjs` re-running the command through the same shell.
  There was a second, undiscovered failure of the same kind: `node --test $FILES`
  under zsh does not word-split, so Node receives ONE argument and reports
  `Could not find 'a b c'` — also zero tests, also no failure.

  `npm test` is now `node scripts/run-js-tests.mjs`, which walks `tests/js`
  itself, sorts the result, and spawns `node --test` with an **argv array** that
  no shell word-splits. There is no `*` in the command for a shell to mangle, and
  `tests/js/logic/sort.test.mjs` now asserts that absence rather than the old
  exact string. Two properties are pinned because both were the original defect:
  the runner's `collectTestFiles()` is compared against `EXPECTED_TESTS` so a new
  file cannot be added without being listed, and an **empty** tree exits `2` with
  a message instead of `0`. That last one is the point — `node --test` on an empty
  file list exits `0`, so any discovery bug that collects nothing is a green suite
  that ran nothing. Do not reintroduce a glob into `package.json`'s `test`.
- **No `.env` loading library.** The app reads `os.environ`, full stop. Use
  `env`, a launchd `EnvironmentVariables` dict (which is what the LaunchAgent
  template uses, and the only mechanism that configures the service), or a
  `direnv`/shell export. Copying `.env.example` to `.env` documents intent and
  configures nothing — and `install_launchagent.sh` deliberately does not create
  one, because a second source of truth for the same values is how they drift.
- **`mypy` scope is `app`, and the config says so.** `pyproject.toml`'s `files`
  is `["app"]`, the documented command here and in `AGENTS.md` is `mypy app`, and
  that is what CI runs — so a bare `mypy` in this directory is the same run as
  `mypy app` (clean, 38 files) and there is no scope the config advertises and
  the gate does not check. `tests/` and `scripts/` are **not** type-checked.
  `files` used to name `app` and `tests`; that claimed a gate the repo never ran
  (`mypy app tests` is 119 errors in 21 files, all pre-existing and unrelated to
  this app's work), so the config was narrowed rather than the gate widened. The
  measurements, and the case for a future `scripts/`, are recorded at the
  `[tool.mypy]` block itself — the reasoning belongs next to the value it
  justifies, not in a README that will drift from it. Do not assume `tests/` is
  type-clean; it is not checked. `scripts/converge_gate.py` in particular is
  untyped-checked and is gated by its own tests instead.
- **`scripts/` is linted, and the one quarantine that used to be here is gone.**
  This bullet is kept because it records a resolved gap, and because a stale
  claim in this section is worse than a missing one — `AGENTS.md` tells readers
  to trust this list, so an entry that is no longer true is a small lie with
  authority. The gate is `ruff check app tests scripts`, so the deploy scripts
  are held to the same rules as the app. `scripts/converge_gate.py` used to be
  the exception: a 37-error backlog (33 `E501`, 3 `UP017`, 1 `F541`) listed by
  rule in `pyproject.toml`'s `[tool.ruff.lint.per-file-ignores]`. **That backlog
  has been cleared and the `per-file-ignores` entry no longer exists** —
  `scripts/converge_gate.py` is now clean under the same `ruff check` as
  everything else, and `scripts/run-js-tests.mjs` was added under the same gate.
  Re-verify with `ruff check app tests scripts` and
  `grep -c per-file-ignores pyproject.toml` (expect `All checks passed!` and
  `0`) rather than trusting this paragraph.

## Related projects

- Infrastructure package: [`syyangv/pwa-template`](https://github.com/syyangv/pwa-template)
  (`docs/pwa-template.md` is the pattern reference this scaffold follows).
- Closest sibling: [`pwa-obsidian-daily`](https://github.com/syyangv/pwa-obsidian-daily)
  — vault reads, daily-note writes, ESM, launchd.
- Catalog producer: [`wholefoods-to-pantry`](https://github.com/syyangv/wholefoods-to-pantry)
  — sole writer of `pantry_items.db`; this app only reads it.
- Domain vocabulary: [`CONTEXT.md`](CONTEXT.md).

# AGENTS.md — pwa-pantry-recipes

A local-only PWA that answers **"what can I cook from what I already have?"**
and records the cook in the Obsidian daily note.

**State (2026-09-28): deployed, on a tailnet-only origin, with two honest
caveats that are not "it works".** The user authorized the deploy in-session, so
`~/Library/LaunchAgents/com.syang.pwa-pantry-recipes.plist` is installed,
`com.syang.pwa-pantry-recipes` is loaded and `state = running`, and a Tailscale
Serve route proxies **8452 → 127.0.0.1:8007**. The deployed origin is
`https://home-macbook-air.tailcd6e49.ts.net:8452` (tailnet only).

Two caveats, both load-bearing, both in `README.md` § *What does not work yet*:

1. **The converge gate exits 3 when it is run from the serving host**, not 0.
   Conditions 0, 0b, 1 and 9 pass; conditions 2–8 report **`VANTAGE-LIMITED`**, the
   gate's fourth outcome, meaning *the deployed half of this comparison is not
   observable from this host*. The cause is that **Tailscale Serve injects no
   identity header for a request that originates from the node doing the
   serving** — it strips the client's `Tailscale-User-Login` (so spoofing fails
   too) and has no remote peer to attribute. The gate's fetcher presents
   `Tailscale-User-Login` on both origins, so over the proxy that header is
   discarded and the backend answers `401 identity_missing` to *every* identity.
   The gate proves this rather than assuming it — see the detection note below —
   and it was diagnosed, not worked around: no `--baseline`, no relaxed check,
   no claim of a pass that was not observed. `VANTAGE-LIMITED` is **not** a pass
   and its exit code is **3**, not 0.
2. **The participant-identity validation has NEVER been performed.**
   `docs/runbook/deployment.md` §8 requires a phone off the host's network, and
   no phone was reachable from the deploy session. **No observer has ever seen
   an authenticated 200 from the deployed origin**; every network request the
   deploy made was refused, so the service has only ever been observed *failing
   closed*. Loopback with the owner login returns 200 on everything, which proves
   the app, not the exposed surface. Nothing in this repository may be read as
   evidence that the authenticated path works.

Do not describe the gate as passing, and do not substitute a loopback or
desktop check for the participant check. `docs/runbook/deployment.md` is the
operational form.

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
scripts/             the LaunchAgent TEMPLATE (pwa-pantry-recipes.example.plist,
                     rendered by the installer into the installed plist),
                     generate_icons.py, and the three deploy-path scripts:
                     install_launchagent.sh (staged; --bootstrap is the gate
                     and was crossed 2026-09-28),
                     converge_gate.py (the release gate), and converge-smoke.json
                     (the release-specific half of its condition 4).
                     Also render_headlines.mjs, check-modules.mjs (what
                     `npm run check` runs), snapshot_pantry_catalog.py and
                     snapshot_golden_match_results.py. `scripts/` IS linted by
                     `ruff check app tests scripts`; converge_gate.py is the
                     one quarantined file, by rule, in pyproject.toml.
docs/runbook/        deployment.md — the Serve plan, the install stages, the
                     restart rules, the participant-identity validation
tests/conftest.py    tmp vault + tmp data dir + seeded pantry catalog
tests/api/           the auth guards, the session contract, CSRF
tests/deploy/        the LaunchAgent template (checked against app/config.py)
                     and every converge-gate condition, each one proved able
                     to fail
tests/db/            migrations and pragmas
tests/pantry/        the catalog, the Stock Join, the per-unit money parity
tests/recipes/       reader, ingredients, normalize, brand lexicon
tests/vault/         atomic_write, frontmatter, sections, daily_paths, the
                     Pantry.md parser and the stock-facing line it feeds
tests/cooklog/       the daily-note write, and the 404 that F4 makes of a
                     missing note
tests/shortlists/    the three Meal Shortlists
tests/mapping/       the materialized ingredient mapping and its repair path
tests/browser/       two opt-in Playwright flows, NOT collected by a default run
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
.venv/bin/python -m ruff check app tests scripts
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

Deployment-path commands. Stages 1 and 2 start nothing; stage 3 crossed the
authorization gate on 2026-09-28 and the service is running. Re-running `--apply`
or `--bootstrap` is idempotent:

```bash
# render + lint the LaunchAgent. No side effects.
./scripts/install_launchagent.sh

# install APP_DATA_DIR 0700 and the plist. Starts NOTHING.
./scripts/install_launchagent.sh --apply

# bootout + bootstrap. Crossed 2026-09-28; also the required pair after ANY
# plist edit, because kickstart ignores the plist.
./scripts/install_launchagent.sh --apply --bootstrap

# code-only change: the job is already loaded, so this is the correct restart.
launchctl kickstart -k gui/$(id -u)/com.syang.pwa-pantry-recipes

# the release gate. Run from the serving host it exits 3 with conditions 2-8
# VANTAGE-LIMITED: the deployed origin refuses every identity the gate can
# present, so those comparisons cannot be made from here. To get a real
# answer, run it from a host that is NOT the serving node. Never make it pass
# by passing --baseline, and never by editing a condition to agree.
.venv/bin/python scripts/converge_gate.py \
  --local-origin http://127.0.0.1:8007 \
  --deployed-origin https://home-macbook-air.tailcd6e49.ts.net:8452 \
  --owner-login syyangv@github \
  --vault /Users/syang/obsidian/syang

# prove the socket, not the process, after ANY restart
python3 ~/.agent/skills/port-manager/scripts/port_manager.py inspect 8007

# rollback, in either order
tailscale serve --https=8452 off
./scripts/install_launchagent.sh --teardown
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

**Deployed 2026-09-28, with the gate at exit 3 (`VANTAGE-LIMITED`) and the
participant-identity check never performed — see the state header at the top of
this file and `README.md` § *What does not work yet`.** Concretely: the plist is installed at
`~/Library/LaunchAgents/com.syang.pwa-pantry-recipes.plist`, the label
`com.syang.pwa-pantry-recipes` is loaded and `state = running`, and
`tailscale serve status` lists `8452 → http://127.0.0.1:8007`. Do not re-bootstrap
it casually; `--bootstrap` is `bootout` + `bootstrap` and is safe, but a plist
edit still needs that pair, never a kickstart.

**Two ports, never one end-to-end.** The app binds loopback **8007**; the phone
reaches **8452**; Tailscale Serve proxies between them. Never `:443`, and never
a bare `tailscale serve <target>` — state the port, the path and the backend URL
explicitly, and run `port-manager audit --json` **before and after**. The
2026-09-27 port audit is recorded in `.env.example`; verify it against live
`lsof` and `tailscale serve status` rather than trusting the file.
`port-manager audit --json` classifies 8452 as `ok`, because
`~/.agent/skills/port-manager/references/port-allocations.json` now declares it
(entry `pantry-recipes`, bind `8007`, ingress `8452`, added 2026-09-28 in the
port-manager repo, commit `ebb7291`). That file is the port-manager skill's, not
this repo's; the launchd label `com.syang.pwa-pantry-recipes` and the `launchd`
deploy posture went into that skill's `known-ports.md`, because the JSON schema
has no field for either and inventing one in a shared ledger would be worse than
the gap.

**The deployed identity is `TAILSCALE_OWNER_LOGIN=syyangv@github`, and it is not
a guess**: it is the value the sibling `com.syang.pwa-obsidian-daily` plist
already runs with in the identical posture (`TRUST_TAILSCALE_HEADERS=true`
behind a tailnet-only Serve route), and the tailnet's own peers are logged in as
`syyangv@`. `PUBLIC_ORIGIN` is the deployed origin byte for byte — scheme, host,
port, no path, no trailing slash — because the Origin guard compares it exactly.
`OBSIDIAN_READ_ONLY=true` as installed, so every mutation is `403 read_only`.

**Run the gate from a host that is not the serving node, or expect exit 3.**
From the serving host the deployed-origin conditions are `VANTAGE-LIMITED`,
because Serve attributes no identity to a self-originated request. That is a
property of the vantage point, not of the deploy, and it must not be taught away
by passing `--baseline` or weakening a condition.

The gate distinguishes that from a real failure by **detecting** it, not by
guessing: `classify_vantage` requires (1) the deployed origin to refuse *every*
identity the gate can present with the identical `401 identity_missing`
envelope — a header that arrived and was rejected answers `identity_denied`, so
this reads `app/auth.py`'s own vocabulary — **and** (2) the deployed origin's
resolved address to be an address of this machine, measured by `connect()`ing a
`SOCK_DGRAM` socket and reading `getsockname()`, never by matching a hostname or
taking a flag. Either observation alone leaves the condition `FAIL`, so a deployed
origin on another node that refuses every identity is still reported as the real
defect it is. The soft outcome is then applied only to a response that is exactly
`401 identity_missing`, and only after that condition's real problems have been
tested for: a version mismatch, a stale `X-PWA-Backend-Started-At`, a missing
response key and an un-rotated `CACHE_VERSION` all still `FAIL` and still exit
`1`. `tests/deploy/test_converge_gate_vantage.py` asserts those four inside an
already-vantage-limited world.

**The release sequence is:** bump `CACHE_VERSION` → add new static files to
`SHELL_ASSETS` → **edit `scripts/converge-smoke.json`** → commit → push →
`launchctl kickstart -k gui/$(id -u)/com.syang.pwa-pantry-recipes` → verify
`/api/version` matches `CACHE_VERSION` and the `X-PWA-Backend-Started-At` header
is newer than every changed startup-loaded file → only then reinstall the Home
Screen PWA (iOS caches manifest metadata longer than page content).

`kickstart -k` is for a **code** change. A plist edit needs `launchctl bootout`
followed by `launchctl bootstrap`; a kickstart re-reads the binary and ignores
the plist, so a `NumberOfFiles` fix that is only kickedstart leaves the old
settings running and reports success. After any restart, prove the listener with
`port_manager.py inspect 8007` — `launchctl print` showing `state = running`
proves a process is alive, not that the socket is listening.

**The converge gate is `scripts/converge_gate.py`, and it is the release check,
not a checklist.** Nine conditions, stdlib-only, and four exit codes of which
**only `0` is a pass**: `0` converged · `1` a real failure · `2` a condition
could not be evaluated because the invocation was under-specified (a missing
flag, an unreadable file) · `3` a condition could not be evaluated because the
deployed origin is not observable from this host. Precedence is `1 > 2 > 3 > 0`.
Neither `2` nor `3` is a soft 0: an unevaluated condition is exactly the state a
stale backend is in, and `3` in particular asserts no convergence at all — it
says the gate could not look, not that what it would have found was fine. `2`
and `3` are separate codes on purpose, because their remedies are different in
kind: `2` is fixed by adding an argument in this shell, `3` only by moving to a
different computer. It checks the
**served** `/sw.js` cache version *and* that `CACHE_VERSION` was rotated since
the last mutable frontend change, because a deploy can be internally consistent
— clean tree, matching versions, correct shell assets — while re-using the
previous Service Worker cache key, and every other condition passes in that state.

**The displayed PWA version proves frontend/static freshness only. It is never
evidence that an already-running backend reloaded configuration.** The gate's
`backend-freshness` condition is what backs that up, and the sentence is printed
on every run.

Full operational detail, including the participant-identity validation and the
rollback commands: `docs/runbook/deployment.md`.

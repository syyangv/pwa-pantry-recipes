# Runbook — deploying pwa-pantry-recipes

> **Deployment is NOT authorized.** `AGENTS.md` § *Deploy* records it as not yet
> authorized, and `scripts/pwa-pantry-recipes.example.plist` is a template. This
> document is the *plan*, plus the scripts that execute it and the verification
> that shows they work. Nothing here bootstraps the LaunchAgent or runs
> `tailscale serve`; both need explicit authorization from the user in the same
> session, and the installer refuses to start a service without it.

The plan itself is `docs/spec/2026-09-27-pantry-recipes.md` §12 and
`~/projects/pwa-template/docs/pwa-template.md` §1a, §1c, §1d, §3e. This runbook
is the operational form of it: the exact commands, in the order that does not
create a half-deployed state, and the reason each one is where it is.

## 1. What ships, and what is not

| Artifact | State |
|---|---|
| `scripts/pwa-pantry-recipes.example.plist` | A template. Renders, lints, and is asserted against `app/config.py` by `tests/deploy/test_launchagent_template.py`. **Not installed.** |
| `scripts/install_launchagent.sh` | Three stages. The first two are verified; the third (`--bootstrap`) has deliberately never been run. |
| `scripts/converge_gate.py` | The release gate. Runs against a live service; verified in full against a loopback dev server and proved able to fail on all nine conditions. |
| `scripts/converge-smoke.json` | The release-specific half of gate condition 4. Edited every release. |
| `~/Library/LaunchAgents/com.syang.pwa-pantry-recipes.plist` | **Does not exist.** |
| `com.syang.pwa-pantry-recipes` in `launchctl` | **Not loaded.** |
| Tailscale Serve route on `:8452` | **Not configured.** |

## 2. The two ports

```
┌──────────────────────┐  loopback   ┌────────────────────┐  Tailscale Serve  ┌────────┐
│ uvicorn              │ ──────────► │ 127.0.0.1:8007     │ ────────────────► │ :8452  │
│ (LaunchAgent)        │             │ the app's bind     │                   │ ingress│
│ app.main:create_app  │             │ NOTHING else       │                   │        │
└──────────────────────┘             └────────────────────┘                   └────────┘
```

**Two ports, never one end-to-end.** The app listens on **8007**; the phone
reaches **8452**; Tailscale proxies between them. A diagram, a smoke check, or a
`base_url` that collapses them into one port is wrong.

| Port | Role | Status on 2026-09-28 |
|---|---|---|
| 8007 | the app's loopback bind | free at the time of writing; the audit run here found no listener |
| 8452 | the Tailscale Serve ingress | free; `tailscale serve status` lists 8443, 8445–8451 as taken and 8452 as unallocated |

Allocated elsewhere: 8000 (wardrobe prod), 8002 (deals), 8003 (wardrobe dev),
8004 (obsidian-daily), 8005 (obsidian-monthly), 8006 (obsidian-editor), and the
Serve ingresses 443 (trek-web / trek-deploy), 8443, 8445, 8446, 8447, 8448, 8449,
8450, 8451. **Never `:443`** — it is shared with the tailnet's other routes, so a
mistake there is not scoped to this app.

**Verify against the live machine, not against this table.** The table is a
record of one audit; the audit is the record:

```bash
python3 ~/.agent/skills/port-manager/scripts/port_manager.py list
python3 ~/.agent/skills/port-manager/scripts/port_manager.py inspect 8007
tailscale serve status
```

Note: `port-manager audit --json` classifies a route by
`~/.agent/skills/port-manager/references/port-allocations.json`, and **that file
has no `pwa-pantry-recipes` entry.** After the route on 8452 is created the audit
will report it as an `unmanaged_extra` rather than `ok`. That file is owned by the
`port-manager` skill, not by this repo, so it is reported here rather than edited;
adding the entry is a follow-up ticket.

## 3. The Tailscale Serve plan — not executed

State the port, the path, and the backend URL explicitly. **Never run a bare
`tailscale serve <target>`.** Run the read-only allocation audit before and
after:

```bash
# 1. BEFORE — read-only, changes nothing
python3 ~/.agent/skills/port-manager/scripts/port_manager.py audit --json

# 2. preserve rollback evidence before mutating
tailscale serve get-config --all > ~/Library/Logs/pantry-serve-config-$(date +%Y%m%d-%H%M%S).json
tailscale serve status --json  > ~/Library/Logs/pantry-serve-status-$(date +%Y%m%d-%H%M%S).json

# 3. the change — port, path and backend URL all explicit
tailscale serve --bg --https=8452 http://127.0.0.1:8007

# 4. AFTER — the same audit, and the result must be unchanged apart from the
#    expected `unmanaged_extra` for 8452 (see §2)
python3 ~/.agent/skills/port-manager/scripts/port_manager.py audit --json
tailscale serve status

# 5. rollback
tailscale serve --bg --https=8452 off
```

`PUBLIC_ORIGIN` must then be `https://home-macbook-air.tailcd6e49.ts.net:8452`,
byte for byte: scheme, host, port, no path, no trailing slash. The Origin guard
compares it exactly, so one extra slash answers every mutation
`403 origin_not_allowed`.

`TAILSCALE_OWNER_LOGIN` must be the exact normalized login the proxy injects:

```bash
tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["LoginName"])'
```

## 4. Install the LaunchAgent — the staged installer

```bash
# stage 1: render + plutil -lint. No side effects, no authorization needed.
./scripts/install_launchagent.sh

# stage 2: APP_DATA_DIR 0700, the log directory, and the plist copied into
# ~/Library/LaunchAgents. Still starts NOTHING.
./scripts/install_launchagent.sh --apply

# stage 3: bootout + bootstrap. THIS starts a service that writes cooking logs
# into the real vault. Requires explicit authorization.
./scripts/install_launchagent.sh --apply --bootstrap

# rollback
./scripts/install_launchagent.sh --teardown
```

The installer's configuration is by environment variable, because **this app has
no `.env` parser** — `Settings.from_environment()` reads `os.environ` and nothing
else. `set -a; . ./.env; set +a` is how a *shell* picks the values up for a dev
server; launchd does not run a shell, so the plist's `EnvironmentVariables` dict
is the only mechanism that actually configures the service. Copying
`.env.example` to `.env` configures nothing.

`--apply` refuses to run while `TAILSCALE_OWNER_LOGIN` / `DEV_IDENTITY` are still
`__PLACEHOLDER__`, and refuses a rendered plist with any unsubstituted
placeholder in a value. `install -m 700` rather than `mkdir` + `chmod`: there is a
window between the two in which `APP_DATA_DIR` is world-readable, and it holds
the idempotency ledgers and the vault-recovery snapshots.

## 5. Restart, after the fact

```bash
# code-only change: the job must already be loaded
launchctl kickstart -k gui/$(id -u)/com.syang.pwa-pantry-recipes

# plist edit: kickstart IGNORES the plist, so this is the only correct pair
launchctl bootout   gui/$(id -u)/com.syang.pwa-pantry-recipes
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.syang.pwa-pantry-recipes.plist
```

Never `--force`.

## 6. The release sequence, verbatim from `AGENTS.md`

> bump `CACHE_VERSION` → add new static files to `SHELL_ASSETS` → commit → push →
> `launchctl kickstart -k gui/$(id -u)/com.syang.pwa-pantry-recipes` → verify
> `/api/version` matches `CACHE_VERSION` and the `X-PWA-Backend-Started-At`
> header is newer than every changed startup-loaded file → only then reinstall the
> Home Screen PWA.

Two steps are the ones that get skipped:

* **Edit `scripts/converge-smoke.json` when — and only when — the release changes
  the API contract.** It is the release-specific half of gate condition 4, and a
  release that adds an endpoint key without adding it to the spec is asserting
  that last release's fields still exist, which is a strictly weaker check than
  it looks. A release that changes no API contract legitimately leaves the file
  untouched: `v0.7.0` (`cf39101`, the per-date cook-log revision binding and the
  Back scroll restore) was frontend-only, added no endpoint key, and did not
  touch the spec. Its `$release` labels therefore still read `v0.6.0` on purpose —
  `$release` is the release that *introduced* the keys in that entry, and it is
  the clock the "remove a key once its release is two releases back" rule runs
  on. Relabelling them `v0.7.0` would claim a release introduced keys it did not
  and would push the removal of those keys a release further out. What is never
  acceptable is adding a key this release did not add: it hides a regression
  behind a field that was always there.
* **A plist edit is not a code change.** The sequence above says `kickstart -k`;
  that is correct for code and wrong for the plist. Use §5's pair.

**No test is re-pinned per release, and none should be.**
`tests/deploy/test_converge_gate.py` builds its world from the real
`app/static/sw.js` by locating `CACHE_VERSION` with the gate's own regex
(`CACHE_VERSION_RE` in `scripts/converge_gate.py`) and rewriting whatever the
file currently carries, so a `CACHE_VERSION` bump touches no test. It used to
replace a literal `'v0.6.0'` instead, and the `v0.7.0` bump in `a303d4a` turned
that replacement into a silent no-op: the fixture's worker kept answering
`v0.6.0` while every response in the world claimed `v9.9.9`, and condition 1
failed across thirteen tests that had nothing wrong with them. A pin that has to
be re-edited on every release is a release step nobody performs, and §6 is where
that step would have to be written down. The failure it was guarding against —
a fixture that quietly disagrees with the file it mirrors — is now caught by the
comparison itself: `test_a_version_mismatch_in_either_half_fails_condition_one`
breaks each half of condition 1 on purpose and asserts it fails, and
`rewrite_cache_version` asserts it matched exactly one constant. If a future
bump ever does need a test edit, that is a bug to file, not a step to perform.

iOS caches manifest metadata longer than page content, so an icon change may need
remove-and-re-add from the Home Screen, not a reinstall over the top.

## 7. The converge gate

```bash
# development-identity posture (TRUST_TAILSCALE_HEADERS=false)
.venv/bin/python scripts/converge_gate.py \
  --local-origin http://127.0.0.1:8007 \
  --vault /Users/syang/obsidian/syang

# production posture (TRUST_TAILSCALE_HEADERS=true, what the plist sets)
.venv/bin/python scripts/converge_gate.py \
  --local-origin http://127.0.0.1:8007 \
  --deployed-origin https://home-macbook-air.tailcd6e49.ts.net:8452 \
  --owner-login <TAILSCALE_OWNER_LOGIN> \
  --vault /Users/syang/obsidian/syang
```

`--owner-login` is a `Settings.tailscale_owner_login` value, **not a secret** —
it is the login the proxy already injects, and the app compares it byte for
byte. It is required in the production posture and ignored in the development
one.

It is stdlib-only on purpose: a gate that needs the project venv cannot be run by
the person deciding whether to bootstrap the agent.

| Condition | §12 | What it compares |
|---|---|---|
| 0 `listener` | — | `port_manager.py inspect 8007`. `launchctl print` showing `state = running` proves a process is alive, not that the socket is listening. |
| 0b `identity` | — | Can the gate talk to the origin at all, and in which posture. `app/auth.py` guards **every** path, so with `TRUST_TAILSCALE_HEADERS=true` a bare loopback GET is `401 identity_missing` — correctly, because the proxy is then the only intended caller. One diagnosis, and the other nine are suppressed rather than all reporting the same 401. |
| 1 `source-version` | 1 | `CACHE_VERSION` in `app/static/sw.js` == local `/api/version`. |
| 2 `deployed-version` | 2 | local `/api/version` == deployed `/api/version`. |
| 3 `backend-freshness` | 3 | `X-PWA-Backend-Started-At` present and equal on both origins, and newer than every changed startup-loaded file (`app/config.py`, `app/db/schema.sql`, the recipe/pantry index inputs, and the rest of the `STARTUP_LOADED_GLOBS` list in the script). |
| 4 `release-smoke` | 4 | every key `scripts/converge-smoke.json` declares is in the **live** `/api/recipes`, `/health` and `/api/session`, on both origins. |
| 5 `cache-rotation` | 3b.1 | the **served** `/sw.js` carries this `CACHE_VERSION` on every origin, **and** no mutable frontend file under `app/static` changed since the commit that last rotated it. |
| 6 `shell-assets` | 5 | every `?v=` in the served HTML is pinned to `CACHE_VERSION` and appears in `SHELL_ASSETS`. |
| 7 `resume-check` | 6 | the **served** `update-manager.js` re-checks on `pageshow`, `visibilitychange` and `focus`. |
| 8 `busy-guard` | 7 | `WAIT_FOR_MESSAGE` is `true` and the served update manager exposes `canApplyUpdate` / `requestUpdateReload`. |

**Exit codes are the design.** `0` every condition passed. `1` at least one
failed — do not reinstall the PWA. `2` at least one could not be evaluated.
Exit 2 is not a soft 0: before a Serve route exists, conditions 2, 3 and 4 have no
second origin to compare against, and the gate says so rather than passing on the
local half alone.

Condition 5's second half is the one with a recorded incident behind it: a deploy
can be internally consistent — clean tree, matching versions, correct shell
assets — while re-using the previous Service Worker cache key, and every other
condition passes in that state. The anchor is
`git log -1 -G"const CACHE_VERSION" -- app/static/sw.js`, the most recent commit
that changed a `CACHE_VERSION` line in either direction. An `SHELL_ASSETS`-only
edit does not move it, so fixing a precache hole cannot blind the check. A
*revert* of `CACHE_VERSION` does reset it, because a revert is itself a rotation;
that limitation is stated in the script and asserted in the test suite rather than
papered over.

**The displayed PWA version proves frontend/static freshness only. It is never
evidence that an already-running backend reloaded configuration.** That sentence
is printed on every run, and condition 3 is the check that backs it up.

## 7a. What the gate found the first time it ran

On its first run against a real HEAD the gate failed, against this repository's
own most recent commit:

> `CACHE_VERSION was NOT rotated even though these mutable frontend files
> changed since the commit that last rotated CACHE_VERSION (b5292b96691f):
> app/static/js/router.js, app/static/js/views/recipe.js`

`cf39101` changed two mutable frontend files and left `CACHE_VERSION` at
`v0.6.0`. That is the recorded incident class — a deploy that is internally
consistent (versions match, the shell pins correctly, `SHELL_ASSETS` is right)
while re-using the previous Service Worker cache key, so every installed PWA is
handed the previous exact-versioned bundle. The rotation was deliberately left
to the release that carries those changes, and that is what `a303d4a` is:
`CACHE_VERSION = 'v0.7.0'`, CONFIG block only, with the gate re-run after it.

## 8. Validation from a participant identity

Before the LaunchAgent exists, a foreground loopback dev server is enough:

```bash
set -a; . ./.env; set +a
.venv/bin/python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8007
```

Then, **from a phone off the host's network** (not the host's own browser — that
is not a participant identity, it shares the host's tailnet position and its
loopback):

- the PWA port **succeeds**;
- unrelated HTTPS ports (8443, 8445–8451) **fail**;
- SSH **fails**.

Only after that is `PUBLIC_ORIGIN=https://home-macbook-air.tailcd6e49.ts.net:8452`
and `TRUST_TAILSCALE_HEADERS=true` correct, and only then is the LaunchAgent
bootstrapped.

## 9. Where the auth posture stops

`app/auth.py` is real and it wraps every response. Verified in the session this
runbook was written in, against a loopback server on the production vault:

| Request | Response |
|---|---|
| `POST /api/cook-logs`, no `Origin`, no CSRF | `403 {"code":"origin_not_allowed"}` |
| `POST /api/cook-logs`, correct `Origin`, no CSRF | `403 {"code":"csrf_required"}` |
| `POST /api/cook-logs`, foreign `Origin` | `403 {"code":"origin_not_allowed"}` |
| `DELETE /api/recipes/<note>/ingredients/0/mapping` | `403 {"code":"origin_not_allowed"}` |
| any request with a client-supplied `Tailscale-User-Login` | `401 {"code":"identity_spoof"}` |

In trusted-header mode (`TRUST_TAILSCALE_HEADERS=true`, the production posture) the
app additionally refuses a *missing* login with `401 identity_missing` and a
mismatched one with `401 identity_denied`. With
`OBSIDIAN_READ_ONLY=true` every `/api/*` mutation is refused with
`403 read_only` after identity and before every other mutation check, and there
is deliberately **no write allowlist** (F18): with F4 removing the note-creation
service, that flag is the only gate between a mutation and the vault.

**This is where the ticket stops.** The identity, Origin, CSRF, Host,
content-type and body-size guards are shipped, tested, and installed. What is
*not* authorized is turning the process on: `bootstrap` starts a service that
appends `[[Recipe]]` lines to the real `日记/2026/2026-09-28.md`, and `tailscale
serve` puts it on a surface the user's phone can reach. Both are one command
away (§3, §4) and neither was run. The deploy path is built and verified; the
decision to cross the line is the user's, and it has to be made in a session
where they say so.

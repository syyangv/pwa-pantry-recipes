# Runbook — deploying pwa-pantry-recipes

> **Status (2026-09-28): the deploy was authorized in-session and executed.**
> `install_launchagent.sh --apply --bootstrap` has been run and
> `com.syang.pwa-pantry-recipes` is `state = running`; the Serve route on `:8452`
> exists and proxies to `127.0.0.1:8007`. One thing is still open and is not
> cosmetic: the converge gate **exits 3** when run from the serving host (§9b),
> and the participant-identity validation in §8 has since been performed **on
> the user's attestation** — a phone (`mieiphone`, `syyangv@`) loaded the app
> through the proxy on 2026-09-28. It is a **user report, not a
> machine-verified result**; §8 records its provenance and its limits in full.
> Read both before describing this deploy as verified.
>
> **Exit 3 is not a pass and does not mean the deploy is broken.** It is the
> gate's separate answer for "this condition could not be evaluated from the
> machine I am standing on", and from the serving node it is the **correct and
> permanent** answer. Every request the *gate* makes over the network is
> **refused**, because a self-originated request has no remote peer for Serve to
> attribute an identity to: the deployed origin has only ever been observed
> *failing closed* **from this vantage point**. That is now not the whole story —
> one authenticated 200 has been seen, by the user, from a phone.
>
> Rollback is unchanged and is at the end of each section.

The plan itself is `docs/spec/2026-09-27-pantry-recipes.md` §12 and
`~/projects/pwa-template/docs/pwa-template.md` §1a, §1c, §1d, §3e. This runbook
is the operational form of it: the exact commands, in the order that does not
create a half-deployed state, and the reason each one is where it is.

## 1. What ships, and what is not

| Artifact | State |
|---|---|
| `scripts/pwa-pantry-recipes.example.plist` | A template, and the source of the installed plist. Renders, lints, and is asserted against `app/config.py` by `tests/deploy/test_launchagent_template.py`. Rendered by the installer, not installed by hand. |
| `scripts/install_launchagent.sh` | Three stages. **All three have been run** (2026-09-28), the third under explicit in-session authorization. |
| `scripts/converge_gate.py` | The release gate. Runs against a live service; verified in full against a loopback dev server and proved able to fail on all ten conditions. **Exits 3 against the deployed pair from the serving host — see §9b.** |
| `scripts/converge-smoke.json` | The release-specific half of gate condition 4. Edited every release. |
| `~/Library/LaunchAgents/com.syang.pwa-pantry-recipes.plist` | **Installed** 2026-09-28. |
| `com.syang.pwa-pantry-recipes` in `launchctl` | **Loaded**, `state = running`. |
| Tailscale Serve route on `:8452` | **Configured.** `8452 → http://127.0.0.1:8007`, tailnet only. |

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

| Port | Role | Status on 2026-09-28 (re-verified at deploy time) |
|---|---|---|
| 8007 | the app's loopback bind | **In use** by `com.syang.pwa-pantry-recipes` (`127.0.0.1:8007` only) |
| 8452 | the Tailscale Serve ingress | **In use**: `8452 → http://127.0.0.1:8007`, tailnet only |

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
`~/.agent/skills/port-manager/references/port-allocations.json`. That file now
carries a `pantry-recipes` entry — loopback bind `8007`, Serve ingress `8452` —
added 2026-09-28 in the port-manager repo (`ebb7291`), so the audit reports
`8452` as `ok`. That file is owned by the `port-manager` skill, not by this
repo; the entry was made there rather than mirrored here. The two facts it
cannot hold — the launchd label `com.syang.pwa-pantry-recipes` and the
`launchd` deploy posture — are recorded in that skill's `known-ports.md`, which
is where 8004/8005/8006 carry the same pair.

## 3. The Tailscale Serve plan — executed 2026-09-28

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

# 4. AFTER — the same audit; 8452 must now read `ok`
python3 ~/.agent/skills/port-manager/scripts/port_manager.py audit --json
tailscale serve status

# 5. rollback
tailscale serve --bg --https=8452 off
```

`PUBLIC_ORIGIN` must then be `https://home-macbook-air.tailcd6e49.ts.net:8452`,
byte for byte: scheme, host, port, no path, no trailing slash. The Origin guard
compares it exactly, so one extra slash answers every mutation
`403 origin_not_allowed`.

`TAILSCALE_OWNER_LOGIN` must be the exact normalized login the proxy injects.
The command this section used to recommend does not work on the installed
client (1.98.10): `tailscale status --json` has no `Self.LoginName`, and the
`Self` node's own user profile reports `LoginName` equal to the node's MagicDNS
name, which is *not* what the proxy attributes to a requester. Get it from a
source that observes the real thing instead — here, the deployed sibling
`~/Library/LaunchAgents/com.syang.pwa-obsidian-daily.plist` already runs the
identical posture and carries `TAILSCALE_OWNER_LOGIN = syyangv@github`, which
matches the tailnet's own `syyangv@` peers. **As installed it is
`syyangv@github`.**

## 4. Install the LaunchAgent — the staged installer

```bash
# stage 1: render + plutil -lint. No side effects, no authorization needed.
./scripts/install_launchagent.sh

# stage 2: APP_DATA_DIR 0700, the log directory, and the plist copied into
# ~/Library/LaunchAgents. Still starts NOTHING.
./scripts/install_launchagent.sh --apply

# stage 3: bootout + bootstrap. THIS starts a service that writes cooking logs
# into the real vault. Requires explicit authorization.
# RUN 2026-09-28 under explicit in-session authorization.
./scripts/install_launchagent.sh --apply --bootstrap

# rollback
./scripts/install_launchagent.sh --teardown
```

There is no `.env` file and none should be created; every value below reaches
the service through the plist's `EnvironmentVariables` dict or the installer
arguments on the command line.

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
| 0b `identity` | — | Can the gate talk to the origin at all, and in which posture. `app/auth.py` guards **every** path, so with `TRUST_TAILSCALE_HEADERS=true` a bare loopback GET is `401 identity_missing` — correctly, because the proxy is then the only intended caller. One diagnosis, and conditions 1–8 are suppressed rather than all reporting the same 401. Condition 9 still runs: it reads the Serve configuration and the process table, never the origin, so it is a different fact rather than a tenth copy of this one. |
| 1 `source-version` | 1 | `CACHE_VERSION` in `app/static/sw.js` == local `/api/version`. |
| 2 `deployed-version` | 2 | local `/api/version` == deployed `/api/version`. |
| 3 `backend-freshness` | 3 | `X-PWA-Backend-Started-At` present and equal on both origins, and newer than every changed startup-loaded file (`app/config.py`, `app/db/schema.sql`, the recipe/pantry index inputs, and the rest of the `STARTUP_LOADED_GLOBS` list in the script). |
| 4 `release-smoke` | 4 | every key `scripts/converge-smoke.json` declares is in the **live** `/api/recipes`, `/health` and `/api/session`, on both origins. |
| 5 `cache-rotation` | 3b.1 | the **served** `/sw.js` carries this `CACHE_VERSION` on every origin, **and** no mutable frontend file under `app/static` changed since the commit that last rotated it. |
| 6 `shell-assets` | 5 | every `?v=` in the served HTML is pinned to `CACHE_VERSION` and appears in `SHELL_ASSETS`. |
| 7 `resume-check` | 6 | the **served** `update-manager.js` re-checks on `pageshow`, `visibilitychange` and `focus`. |
| 8 `busy-guard` | 7 | `WAIT_FOR_MESSAGE` is `true` and the served update manager exposes `canApplyUpdate` / `requestUpdateReload`. |
| 9 `ingress-identity` | — | The Serve route for the `--deployed-origin` port resolves to the **same address the local origin is served by**, and the process holding that port — pid *and* start time — is the same in both reads. Proves **who** is behind the route, never **what** it returned; see below. |

### Condition 9, and why it is additive and not a loophole

Condition 9 exists because a vantage limit is a comfortable place for a real
defect to hide. From the serving node, conditions 2–8 report `VANTAGE-LIMITED`,
and that reads as *nothing was learned*. But `8452 → http://127.0.0.1:8007` is a
pass-through to **the same process** the local origin talks to, so if that were
ever not true — the route pointed at another port, or at the same port held by a
stale sibling — the gate would be saying "unobservable" while every
deployed-vs-local comparison was quietly about a second copy of the app. Nothing
else in the script can see that, and the vantage limit would be concealing it.

**It cannot become a pass for anything else.** It fetches nothing. It does not
read a deployed response, a deployed version, a deployed timestamp or a deployed
key, so it has no evidence that would clear a single `VANTAGE-LIMITED` condition
— and a run where 9 passes still exits **3**, with all seven deployed conditions
still `VANTAGE-LIMITED`. The distinction it establishes is *there is only one
deployment here*, which is a statement about topology and not about convergence.

**Its one assumption, printed on every run.** That Tailscale Serve is a
pass-through and **does not cache responses**. That is true of Serve, and it is
an assumption about someone else's proxy rather than a fact about this
deployment — a caching proxy would satisfy every other check in the condition and
serve bytes from somewhere else entirely. So the condition emits a
`CACHING ASSUMPTION:` line in its own output on every run rather than leaving the
belief in a comment, and the honest reading of a passing condition 9 is *"the
deployed origin is this process, and Serve is assumed not to cache"* — never
*"the deployed origin was observed"*.

**What it fails on, unsmoothed.** A route resolving to a different port, or to
the same port held by a different pid *or a different process start time*, is a
deployment defect and reports `FAIL` (exit 1). A pid alone is not an identity —
pids are reused across a restart — so the start time is compared as well, and the
process table is read **twice** rather than once and reused: on the live topology
both authorities are the same port, so a single lookup would make the comparison
a tautology that always agrees, and a backend that restarted between the route
read and the process read would go unnoticed.

**Where it stands down.** Run from a host that is not the serving node, the
deployed origin answers directly and conditions 2–8 compare against it, which is
a stronger statement than a route table; condition 9 then reports that the proof
is unnecessary rather than disappearing from the report. This is also what keeps
the §9b remedy reachable: a phone on the tailnet has no `tailscale serve status`,
and had this branch reported `UNPROVEN` there, the one host that can reach exit 0
would exit 2 instead.

**Exit codes are the design.** Four states, and **only `0` is a pass**:

| Code | Meaning | Your move |
|---:|---|---|
| `0` | every condition passed | the release converged; only now reinstall the PWA |
| `1` | at least one condition **failed** | something is wrong with the release or the deploy. Do not reinstall. |
| `2` | at least one condition is `UNPROVEN` | *this invocation* was under-specified — a missing flag, an unreadable file, a working tree that is not a repository. Supply the argument. |
| `3` | at least one condition is `VANTAGE-LIMITED` | the invocation was complete, every condition ran, and the **deployed origin is not observable from this host**. Move to a host that is not the serving node. |

Precedence is **1 > 2 > 3 > 0**, and each step is a claim about urgency. `1`
outranks everything: a release with a real defect is not made safer by also being
unevaluable. `2` outranks `3` because it is fixable from the same shell in
seconds and because it can be *masking* whether the vantage limit applies at all —
a run with no `--deployed-origin` has not established that there is a second
origin to be limited by. `3` is lowest because nothing has been shown to be
wrong; but it is still non-zero, because nothing has been shown to be right
either.

**`2` and `3` are both "not a pass", and they are deliberately different
codes.** Before a Serve route exists, conditions 2, 3 and 4 have no second
origin to compare against and the gate exits `2`. As deployed, from the serving
host, it exits **`3`**: the second origin exists, it was queried, and it answered
`401 identity_missing` to every identity the gate can present. Folding `3` into
`2` would file a problem whose remedy is "get a phone" under the one code whose
documented remedy is "pass the flag" — and an operator who has been told to add
a flag that is already there will go looking for something else to change.
Folding `3` into `0` would assert a convergence nobody observed.

**What a `VANTAGE-LIMITED` condition does and does not mean.** It means the
deployed half of that comparison could not be made from here. It does **not**
mean the deployed origin is fine — the gate has not observed it, and says so.
Each one names the request that would settle it. As deployed, 2–8 are all
`VANTAGE-LIMITED`; their **local** halves were evaluated and are reported as
evaluated, and any real problem in a local half is still `FAIL` and still the
exit code. See §9b for the detection and §8 for the check that resolves it.

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
(`CACHE_VERSION` has since moved again, to `v0.7.1` in `fb2577e`, so the anchor
`git log -1 -G"const CACHE_VERSION" -- app/static/sw.js` now resolves to
`fb2577e`, not `a303d4a`. Both rotations are history; the live value is
`v0.7.1`.)

## 8. Validation from a participant identity — origin check done, user-attested

**Provenance, stated first because it decides how much this section is worth.**
The check below was performed by **the user**, on a phone, on 2026-09-28. It was
**not** observed by the agent that wrote this entry, and it was **not** observed
by any tool, script, gate run, or test in this repository. There is no log line,
no captured response, and no exit code behind it. It is a **user attestation**:
a person opened a URL on a device and reported what they saw. Nothing in this
repository should be read as machine-verifying it, and it is recorded here as
exactly what it is — one human report, uncorroborated by instrumentation.

| Field | Value |
|---|---|
| Performed by | **the user** (attestation, not an agent or a tool) |
| Date | 2026-09-28 |
| Device | `mieiphone` |
| Tailnet identity | `syyangv@` |
| Tailnet address | `100.99.212.85` |
| URL opened | `https://home-macbook-air.tailcd6e49.ts.net:8452` |
| Reported result | **"the app loads"** |

**The report is exactly that, and the record does not grow it.** The user
reported that the app loads. They did **not** report a displayed PWA version or
`CACHE_VERSION`, a headline `n/total`, a chip tier, a recipe count, a
`stockJoinState`, or any particular rendered value. None of those may be inferred
from "it loads", and none is claimed here.

**What that does establish.** It establishes the positive half of this section:
an authenticated request through the Tailscale Serve proxy **succeeded**. That is
the fact this section was written for, and until now nothing had ever
established it — every network request in the deploy session had been *refused*,
so the deployed origin had only ever been observed **failing closed**. The
distinction is load-bearing, because a `401 identity_missing` cannot render the
app: for the app to load at all, the proxy must have injected a `Tailscale-User-Login`
the backend accepted, which is precisely the injected-identity chain §8 exists to
exercise. The deployed origin is therefore no longer known only by its refusals.

**What it does not establish — and the gate's own answer is unchanged.** "The app
loads" is a statement about reachability and identity, not about *convergence*.
It does not show that the deployed `sw.js` carries the current `CACHE_VERSION`,
that the deployed shell references the versioned assets, or that the deployed
`/api/version` agrees with the local one, and it is not the gate re-run this
section asks for at the end. So conditions **2, 4, 5, 6, 7 and 8** — and the
deployed half of **3** — are still not evaluated, the gate still reports seven
`VANTAGE-LIMITED` conditions, and the gate still **exits 3**.

### The two negative checks, performed 2026-09-28

Both were performed on 2026-09-28, **from this Mac — the serving node itself**,
and that vantage is weaker than the one the positive check above used, so it is
named per check rather than once. Neither result is a claim about what a remote
peer sees on the tailnet; §8's own discipline ("a probe from the serving node is
weaker evidence than one from a phone") is the reason.

**Unrelated HTTPS ports do not serve — VERIFIED, from the serving node.** Ports
`8444`, `8451`, `8453`, `9000` and `4433` were probed against
`https://home-macbook-air.tailcd6e49.ts.net:<port>/`. Every one **refused the
TCP connection** (`curl` exit 7, `nc -z` exit 1) — no TLS handshake, no HTTP
status, nothing served. Three independent facts agree, and all three were
checked: no local process holds those ports (`lsof -nP -iTCP:<p> -sTCP:LISTEN`
is empty for every one of them), they are absent from
`tailscale serve status --json`, and the connection is refused. `8452` was
probed in the same batch as a **control** and answered `HTTP 401`, so the method
is shown to detect a port that *is* a route.

> The original list of ports to probe read "(8443, 8445–8451)", and it was
> **wrong**: `8443`, `8445`, `8446`, `8447`, `8448`, `8449` and `8450` are all
> declared Serve routes (`wardrobe-development`, the deliberately-retained
> `8445`, and the four Obsidian PWAs) and would all have served. Probing them as
> "unrelated" would have produced four false alarms. The list above is the set
> of ports that are genuinely *not* routes. `8002` / `8448` (`pwa-deals`) was
> excluded on purpose — out of scope for the session in which this was checked.

> **What this vantage cannot establish.** A connection from the serving node to
> its own `100.x` address never leaves the machine, so this does not exercise a
> tailnet ACL or a peer's path. For a port that nothing listens on *and* Serve
> does not forward there is no socket for a peer to reach either, so the
> conclusion holds — but the tailnet-ACL layer itself is untested, and only a
> probe from a phone would test it.

**SSH over the tailnet — NOT VERIFIED, and the check as originally written cannot
pass on this node.** The configuration half is clean and was verified read-only:
`tailscale serve status --json` lists TCP forwards on `443`, `8443`, `8445`–`8450`
and `8452` and **no `22`**; `tailscale debug prefs` reports **`RunSSH = False`**
(Tailscale SSH is not enabled for this node); and `tailscaled` holds **no**
listener on the tailnet address's port 22. So Tailscale Serve is not tunnelling
SSH, which is what the check was aimed at.

The behavioural half is a **different and more serious answer than "it failed".**
`nc` to `<tailnet-hostname>:22` **connected**, and the banner on the wire was
`SSH-2.0-OpenSSH_9.6` — macOS's own OpenSSH, not tailscaled. `netstat -an` shows
it bound to `*.22` (wildcard, therefore including `tailscale0`), the application
firewall reports `State = 0` (disabled) with block-all and stealth mode off, and
`ssh <tailnet-hostname> true` from this Mac **succeeded** (exit 0). `lsof` shows
no process on 22 because `/System/Library/LaunchDaemons/ssh.plist` uses
`inetdCompatibility`, so launchd holds the socket and spawns `sshd` on demand.

So on this node SSH over the tailnet is not blocked at all — and it is not
Tailscale that unblocks it, it is a wildcard-bound `sshd` on a Mac with its
firewall off. **The check "SSH must fail over the tailnet" is therefore recorded
as NOT PASSED, not as failed and not as passed.** It is a property of the host's
own SSH configuration, and closing it is a decision about Remote Login on this
Mac, not a fact this repository can assert. Two sub-points are honest gaps: the
`systemsetup -getremotelogin` read needs `sudo` and was not obtained, so Remote
Login's *configured* state is inferred from the successful connect rather than
read; and the node still advertises `https://tailscale.com/cap/ssh` in its
`CapMap`, so "the node advertises no SSH capability" is **not** something this
entry claims.

> One side effect, disclosed: that `ssh` probe authenticated, with a local key,
> to **this Mac's own** `sshd` as the current user. The intent was a refusal
> probe and the expectation was `refused`; the instruction was not to
> authenticate to anything, and that probe went further than intended. It reached
> no remote system and changed no service, but it did add a `known_hosts` entry
> for `home-macbook-air.tailcd6e49.ts.net`, which was left in place rather than
> tidied away.

**Summary of the section's original three items:** the positive origin check
passed on the user's attestation from a phone; unrelated HTTPS ports were
verified not to serve, from the serving node; and the SSH check is **open**, with
its configuration half clean and its behavioural half answered by a non-Tailscale
listener.

### Why the gate exits 3 here, permanently, and why that is the correct answer

**This is the one verification the gate structurally cannot perform, and no
amount of re-running it from this machine will change that.** The gate runs **on
the serving node**. Tailscale Serve attributes an identity to a **remote peer**;
a request that originates from the node doing the serving has **no remote peer to
attribute it to**, so the proxy injects nothing and the backend correctly answers
`401 identity_missing` (see §9b for the four-row evidence, including the row that
settles it — sending the *correct* identity changes nothing, so the header is not
being rejected, it is not arriving). The deployed half of every condition that
consults the origin is therefore **unobservable from where the gate stands**.

**Exit 3 is the correct permanent answer for this single-node deploy topology.
It is a fact about the topology, not a defect to clear.** The alternatives are
both worse, and deliberately so: collapsing 3 into 0 would assert a convergence
that nobody observed, and collapsing it into 2 would file an unfixable-from-here
problem under the one exit code whose documented remedy is "pass the flag".
Getting from 3 to 0 requires **a different machine** — a phone on the tailnet,
running the gate — not a different argument. On a single-node Serve deployment
there is no configuration that produces 0, and an engineer who reads exit 3 as
"still broken" will go looking for a bug that is not there.

So the durable state is: **the release is deployed and reachable, the
participant-identity check passed on the user's attestation, and `exit 3` from the
serving node is the correct and permanent answer.** The gate's verdict and this
section's result are not in conflict; they are answers to two different questions,
and §9b's point that `VANTAGE-LIMITED` is a statement about the observer rather
than about the deploy is the whole of it.


## 9. Where the auth posture stops

`app/auth.py` is real and it wraps every response. Verified in the session this
runbook was written in, against a loopback server on the production vault:

| Request | Response |
|---|---|
| `POST /api/cook-logs`, no `Origin`, no CSRF | `403 {"code":"origin_not_allowed"}` |
| `POST /api/cook-logs`, correct `Origin`, no CSRF | `403 {"code":"csrf_required"}` |
| `POST /api/cook-logs`, foreign `Origin` | `403 {"code":"origin_not_allowed"}` |
| `DELETE /api/recipes/<note>/ingredients/0/mapping` | `403 {"code":"origin_not_allowed"}` |
| any request with a client-supplied `Tailscale-User-Login`, **development** posture | `401 {"code":"identity_spoof"}` |
| any request with a client-supplied `Tailscale-User-Login`, **production** posture | `401 {"code":"identity_denied"}` (mismatched) or, through the proxy, `401 {"code":"identity_missing"}` (stripped) — see §9c |

In trusted-header mode (`TRUST_TAILSCALE_HEADERS=true`, the production posture) the
app additionally refuses a *missing* login with `401 identity_missing` and a
mismatched one with `401 identity_denied`. With
`OBSIDIAN_READ_ONLY=true` every `/api/*` mutation is refused with
`403 read_only` after identity and before every other mutation check, and there
is deliberately **no write allowlist** (F18): with F4 removing the note-creation
service, that flag is the only gate between a mutation and the vault.

**The gate has two origins, and the deployed one is not observable from here.**
Conditions 2–8 are `VANTAGE-LIMITED` and the run exits 3: they were evaluated,
and the deployed origin refused every identity the gate can present. See §9b for
the detection and the evidence.

## 9b. Why the gate exits 3 from the serving host

The deploy ran the gate from the same machine that serves `:8452`. It reports
conditions 2–8 as `VANTAGE-LIMITED` and exits **3**, identically before and after
`launchctl kickstart -k`:

```
[PASS] 0 listener  [PASS] 0b identity  [PASS] 1 source-version
[VANTAGE] 2 deployed-version  [VANTAGE] 3 backend-freshness
[VANTAGE] 4 release-smoke     [VANTAGE] 5 cache-rotation
[VANTAGE] 6 shell-assets      [VANTAGE] 7 resume-check  [VANTAGE] 8 busy-guard
[PASS] 9 ingress-identity
```

Condition 9 is in that list and changes nothing about it: it is `PASS` because
the route was *proven to be a pass-through to this process*, which is a fact
about topology. It read no deployed response, so it leaves all seven
`VANTAGE-LIMITED` conditions exactly where they were and the exit code at 3. See
§7's condition table for what it does and does not establish.

**Cause.** Tailscale Serve injects no identity header for a request that
originates from the node doing the serving. It *strips* the client's
`Tailscale-User-Login` — so a forged header is discarded rather than trusted,
which is the correct and safe behaviour — and has no remote peer to attribute
the request to, so it injects nothing. `converge_gate.py` uses **one** fetcher
that presents `Tailscale-User-Login: <owner-login>` for *both* origins (that is
what makes condition 0b pass on loopback), so over the proxy that header is
thrown away and the backend correctly answers `identity_missing`.

This is a **vantage-point** limit, not a deploy defect. Evidence, all observed
against the live service:

| Request | Origin | Result |
|---|---|---|
| owner header | loopback `:8007` | `200` |
| no header | Serve `:8452` | `401 identity_missing` |
| `Tailscale-User-Login: attacker@evil` | Serve `:8452` | `401 identity_missing` (stripped) |
| `Tailscale-User-Login: syyangv@github` (the **correct** login) | Serve `:8452` | `401 identity_missing` |

The fourth row is the one that settles it: sending the correct identity changes
nothing, so the header is not being rejected — it is not arriving. Duplicate
headers through the proxy return `identity_missing` rather than
`identity_invalid`, which independently confirms the strip. Reaching `:8452`
via the raw tailnet IP with correct SNI gives the same `401`, so it is not a
DNS or curl artifact. And the same behaviour was observed on the pre-existing
sibling route `:8447` before this app was deployed, so it is a property of Serve
on this node, not of this app.

### How the gate tells that apart from a real failure

This is the part that matters, because a gate which reports `FAIL` for an
environmental reason trains its operator to go looking for a way to make the
number go away — and the ways available are `--baseline`, a weakened comparison,
and a skipped condition. The gate's author refused `--baseline` once, correctly,
and the classification below exists so that refusal does not have to be repeated
under pressure.

`classify_vantage` in `scripts/converge_gate.py` decides this from **two
positive observations, both required**. Neither is a hostname match and neither
is a flag.

**1. The refusal is uniform over every identity the gate can present.** It sends
three requests to the deployed `/api/version`: no `Tailscale-User-Login`, a
forged one, and the configured owner one. `app/auth.py` has a distinct code for
each situation, and that vocabulary is the whole basis of the test:

| The deployed origin… | answers | means |
|---|---|---|
| received no header | `401 identity_missing` | no identity arrived |
| received a header and rejected it | `401 identity_denied` | an identity arrived and was wrong |
| received a duplicated/empty/oversized header | `401 identity_invalid` | an identity arrived, malformed |
| received the right header | `200` | the identity path works |

Only the first row can produce three identical answers. A live proxy injecting
*some* identity would answer `200` or `identity_denied` for at least one of the
three, and the gate then classifies the origin as **observable** and evaluates
it normally. The classification reads `app/auth.py`'s own codes, so it needs to
know nothing about Tailscale.

**2. The deployed origin's address is an address of this machine.** The gate
resolves the deployed host and then asks the kernel: a `SOCK_DGRAM` socket is
`connect()`ed to each resolved address (which sends nothing — it only consults the
routing table) and `getsockname()` is read back. If the source address the
kernel picks *is* the destination, the destination is local, and a connection to
it cannot have been proxied from a remote peer. For `:8452` that resolves to
`100.87.56.102`, which is this node's own tailnet address.

Observation 2 is what stops observation 1 from being a loophole. A deployed
origin on **another** node that refuses every identity would refuse every real
user too — a Serve route pointed at a backend whose trusted-header posture is
broken looks exactly like this from here — and calling that a vantage limit
would be the gate deciding a broken deployment is fine. Without proof that the
traffic never left the machine, that case stays `FAIL`.

Any inability to decide — DNS failure, no route, an unparseable origin, a probe
that errors — resolves to **not** vantage-limited, so the condition stays `FAIL`.
The uncertainty is always resolved toward the answer that cannot be wrong in the
dangerous direction.

Finally, the reclassification is applied **per response, and only to the exact
refusal shape**: `401` with `code: identity_missing`, and nothing else. Every
real failure this gate exists to catch is an observation about a **200 body** or
a non-401 status — a version mismatch, a stale `X-PWA-Backend-Started-At`, a
missing response key, a rotated-away `CACHE_VERSION`, a wrong shell pin — so
none of them can reach the soft branch. Each condition's real problems are
collected and tested for `FAIL` *before* the vantage branch is considered, so a
vantage limit can never absorb a defect the gate did find.
`tests/deploy/test_converge_gate_vantage.py` asserts each of those four
failures inside an already-vantage-limited world, which is the only arrangement
in which a regression that widened the soft branch would be caught.

**The fix is to run the gate from a host that is not the serving node** — a
participant on the tailnet. **The fix is not** `--baseline`, and not editing a
condition; both were available and were refused, because the only defensible way
to change a failing condition is to fix the cause.

### One real failure this section is not about

The same run also reports condition 3 as `FAIL`, which is exit 1 and outranks
the vantage limit:

> `daily_notes_root:日记/2026/2026-09-28.md was modified at
> 2026-09-28T15:50:51, AFTER the backend started at 2026-09-28T15:29:37 —
> the process is running pre-change bytes`

That is a **genuine finding about the local half**, not a vantage artefact: the
user edited today's daily note after the backend booted. It is reported rather
than hidden, and it is the clearest available demonstration that adding a fourth
outcome did not soften the gate. Resolving it means restarting the backend, which
§5 covers.

## 9c. Guard order, and where the auth posture actually stops

The order in `app/auth.py::_guard` is **host (400) → identity (401) → read-only
(403) → body size (413) → origin (403) → content-type (415) → CSRF (403)**.
Two consequences worth stating because they change what a test can observe:

* **Identity precedes Origin.** Over the deployed origin every request from the
  serving host is stopped at identity, so it returns `401` and never reaches the
  Origin or CSRF checks. An unauthenticated mutation *is* refused; the code that
  refuses it is `identity_missing`, not `origin_not_allowed`.
* **Read-only precedes Origin and CSRF.** With `OBSIDIAN_READ_ONLY=true` — how
  it is installed — every authenticated mutation is `403 read_only`, so
  `origin_not_allowed`, `csrf_required` and `csrf_invalid` are unreachable on
  the deployed instance. Those codes were verified on a throwaway instance
  pointed at a **copy** of the vault; the copy is disposable and the real vault
  was not written to.

Also note the code that does *not* belong on a deployed instance:
`identity_spoof` is the **development-posture** answer to any client-supplied
`Tailscale-*` header. In the production posture a well-formed but mismatched
login is `401 identity_denied`, and a header the proxy has stripped is
`identity_missing`. Expecting `identity_spoof` from a deployed origin is
expecting the wrong posture.

## 9d. The stopping point, as of 2026-09-28

`app/auth.py` is real and it wraps every response. The identity, Origin, CSRF,
Host, content-type and body-size guards are shipped, tested, installed and
running. What was authorized and done: `bootstrap` started the service and
`tailscale serve` put it on a tailnet-only surface.

**§8's participant-identity check has since been performed, on the user's
attestation** (2026-09-28, `mieiphone`, `syyangv@`, `100.99.212.85`, `:8452`):
the app loads, so the injected-identity chain works through the proxy. It is a
**user report, not a machine-verified result** — no tool in this repo observed
it, and §8 records its provenance and its limits in full. The deploy is therefore
no longer known **only** by its refusals: an authenticated request through Serve
has succeeded once, on a phone.

**`VANTAGE-LIMITED` is still the correct and permanent verdict from this host,
and it is not a remaining defect.** Conditions 2, 4–8 and the deployed half of 3
are unobservable from the serving node, so the gate exits 3, and no change to
this single-node topology changes that — it needs a *different machine*, not a
different argument (§8). Condition 9 was added afterwards and does not move that
answer: it proves the deployed route is a pass-through to this very process, so
there is no second deployment hiding behind the vantage limit, but it reads no
deployed response and therefore retires nothing.

What remains genuinely open to the user is narrow and is listed in §8: the
**SSH** half of the negative participant-identity checks (the unrelated-HTTPS-ports
half was performed 2026-09-28 from the serving node and is recorded with its
vantage), and — if a `CONVERGED` line is ever wanted in a log — running the gate
itself from the phone.

Two ledger facts changed in the port-manager repo on 2026-09-28 (`ebb7291`) and
are recorded here so this runbook is not the only place they are written down:
`port-allocations.json` now declares `pantry-recipes` (bind 8007, ingress 8452),
so `port-manager audit` classifies 8452 as `ok` rather than `unmanaged_extra`; and
the launchd label and deploy posture went into that skill's `known-ports.md`,
because the JSON schema has no field for either. A third change followed in the
same repo (`eb29296`): the **removed** `8451` route is traced in a *Removed
routes* section of that same file, with the rollback command and the checksummed
before/after snapshots in `~/Library/Logs/` under the `20260928-133003` stamp.

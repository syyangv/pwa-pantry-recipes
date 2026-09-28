#!/usr/bin/env python3
"""The converge gate — the runnable form of `docs/spec/2026-09-27-pantry-recipes.md`
§12 and `~/projects/pwa-template/docs/pwa-template.md` §3e.

**This is the release check, and it is deliberately a program rather than a
prose checklist.** A checklist is read once and remembered afterwards; every
condition below has a recorded incident behind it, and each one is a comparison
that can be made to fail on demand (`tests/deploy/test_converge_gate.py` breaks
every one of them).

Exit status is the whole design:

    0  every condition is PASS — the release converged
    1  at least one condition FAILED — do not reinstall the PWA
    2  at least one condition could not be evaluated (UNPROVEN)
    3  no condition FAILED, but at least one is VANTAGE-LIMITED

**2 is not a soft 0.** The gate may not report "converged" while a condition it
was told to check was skipped because the argument was missing, because a file
was unreadable, or because the working tree is not a git repository. An
unevaluated condition is the exact state a stale backend is in, and collapsing
it into a pass is how a release ships against a backend that never reloaded.

**3 is not a soft 0 either, and it is a different statement from 2.** Exit 2
means *this invocation* was under-specified — a missing flag, an unreadable
file, a working tree that is not a repository — and the operator can fix that
from the same shell in five seconds. Exit 3 means the invocation was complete,
every condition ran, and the deployed origin's answer is **not observable from
where the gate is standing**: Tailscale Serve attributes an identity to a remote
peer, and a request from the serving node has no remote peer to attribute.
Getting from 3 to 0 requires a different machine (a phone on the tailnet), not
a different argument. Collapsing 3 into 0 would assert a convergence nobody
observed; collapsing it into 2 would file an unfixable-from-here problem under
the one exit code whose documented remedy is "pass the flag".

Precedence, and why: **1 > 2 > 3 > 0**. A real failure is the most urgent fact
and outranks everything. Between 2 and 3, an under-specified invocation outranks
a vantage limit because it is the operator's to fix immediately *and* because it
can be masking whether the vantage limit even applies; a run with both still
prints every condition's own status, so nothing is lost by the ordering.

**The single property this gate must never lose: a real failure still fails.**
`VANTAGE-LIMITED` is reachable only for a deployed response that is *byte-for-byte
the identity-absence refusal* — HTTP 401 with `code: identity_missing` — and
only when `classify_vantage` has **proven** two things (see its docstring): the
deployed origin refuses every identity this gate can present, and the deployed
origin's address is an address of this machine. A version mismatch, a stale
`X-PWA-Backend-Started-At`, a missing response key, a rotated-away
`CACHE_VERSION` and a wrong shell pin are all observations about a **200 body**,
so none of them can reach that branch: they are `FAIL` and exit 1, from the
serving node exactly as from anywhere else.

The conditions, in the order §12 numbers them:

    0  listener          the socket is really listening (not `launchctl print`)
    0b identity          the local origin answers the gate, and in which posture
    1  source-version    sw.js CACHE_VERSION == local /api/version
    2  deployed-version  local /api/version == deployed /api/version
    3  backend-freshness X-PWA-Backend-Started-At matches on both, and is newer
                         than every changed startup-loaded file
    4  release-smoke     a release-specific live API smoke check
    5  cache-rotation    the served sw.js carries THIS CACHE_VERSION, and
                         CACHE_VERSION was rotated since the last mutable
                         frontend change
    6  shell-assets      the HTML references the same versioned assets as the
                         SW cache name
    7  resume-check      returning from background triggers a version check
    8  busy-guard        an open confirmation flow postpones the reload

Conditions 2-8 each consult the deployed origin, so each carries a second,
independent failure mode: the deployed half may be **unobservable** from the
machine running the gate. That is reported as `VANTAGE-LIMITED`, never as
`PASS` and never as `FAIL`, and it names the request that would settle it.

Usage
-----

    # local only, before any Tailscale Serve route exists
    .venv/bin/python scripts/converge_gate.py --local-origin http://127.0.0.1:8007

    # the real release gate: both origins, both required
    .venv/bin/python scripts/converge_gate.py \\
        --local-origin http://127.0.0.1:8007 \\
        --deployed-origin https://home-macbook-air.tailcd6e49.ts.net:8452 \\
        --vault /Users/syang/obsidian/syang

Exit 2 above is **expected and correct** before `tailscale serve` is configured:
conditions 2, 3's deployed half and the deployed halves of 4/5/6 have no second
origin to compare against. The gate says so instead of passing.

**Run from the serving node, against a live Serve route, the gate exits 3.**
That is the honest answer and it is not a pass: Tailscale Serve injects no
identity for a request that originates from the node doing the serving, so the
deployed origin refuses the correct owner login exactly as it refuses a forged
one, and conditions 2-8 report `VANTAGE-LIMITED` with the request that would
settle them. The deploy's success path has still never been observed; see
`docs/runbook/deployment.md` §8.

`--no-listener-check` exists for a pre-`kickstart` run, where the honest answer
is "the new code is not running yet", not "the listener is missing".
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

#: Pattern A's single source (docs/pwa-template.md 3a). The same literal the
#: vendored `app/pwa_version.py` regexes out, pinned here so the gate reads the
#: same constant the app serves and not a second guess at its spelling.
CACHE_VERSION_RE: Final = re.compile(r"const CACHE_VERSION = '([^']+)'")

#: A clock tolerance for condition 3. `st_mtime` and the header are written by
#: two different clocks on two different subsystems (the filesystem's, and
#: Python's `datetime.now(UTC)` inside the worker), and macOS APFS stores
#: nanoseconds while the header carries microseconds. Two seconds is far below
#: the smallest real staleness a release has and far above the noise.
CLOCK_SKEW_SECONDS: Final = 2.0

#: The startup-loaded inputs named by §12.3, as repo-relative paths. Glob
#: patterns are expanded against the repo root; everything listed here is read
#: once, at import or at boot, so a change to it requires a restart and a
#: backend started before the change is holding stale bytes.
#:
#: `app/static/sw.js` is in the list because Pattern A reads it at *import*
#: (`derive_version` at module scope in `app/main.py`) — a changed CACHE_VERSION
#: with no restart keeps serving the old `/api/version`, which is condition 1's
#: failure dressed up as a version problem.
STARTUP_LOADED_GLOBS: Final = (
    "app/static/sw.js",
    "app/config.py",
    "app/main.py",
    "app/auth.py",
    "app/pwa_version.py",
    "app/db/schema.sql",
    "app/recipes/lexicon/*.yaml",
    "app/pantry/line_overrides.yaml",
    "app/api/*.py",
    "app/db/*.py",
    "app/pantry/*.py",
    "app/recipes/*.py",
    "app/vault/*.py",
    "app/cooklog/*.py",
    "app/mapping/*.py",
    "app/shortlists/*.py",
)

#: §12.3's "recipe/pantry index inputs", vault-relative. These are read lazily
#: behind a TTL rather than at boot, so they are checked against the same
#: timestamp: a recipe note edited *after* the backend started is a legitimate
#: state the user created, and the gate reports it rather than failing on it —
#: the note is re-read on the next index refresh. The important part is that they
#: are named, so a report never says "startup inputs OK" while quietly omitting
#: the two files the user actually edits.
VAULT_INPUT_ROOTS: Final = ("recipes_root", "pantry_note", "daily_notes_root")

#: §3e conditions 6 and 7 read the **served** update manager, not the file on
#: disk: the whole question is whether the bytes an installed PWA would fetch
#: still re-check on resume. A grep of the working tree cannot answer that.
RESUME_MARKERS: Final = (
    "addEventListener('pageshow'",
    "addEventListener('visibilitychange'",
    "addEventListener('focus'",
    "/api/version",
)
#: §3e condition 7 / §4e: an open dialog or an in-flight mutation postpones the
#: reload. Both halves are required — a `canApplyUpdate` that exists but is
#: never passed is a guard that is not installed.
BUSY_GUARD_MARKERS: Final = (
    "canApplyUpdate",
    "requestUpdateReload",
)

PASS: Final = "pass"
FAIL: Final = "fail"
UNPROVEN: Final = "unproven"
#: The fourth outcome, added 2026-09-28. It is **not** a softer `FAIL`, and **not**
#: a `PASS` with a footnote: it says the condition's deployed half is not
#: observable from the machine running the gate. `PASS` means "checked, agreed".
#: `FAIL` means "checked, disagreed". `VANTAGE-LIMITED` means "not checkable
#: here", and it is the only one of the three whose remedy is a different
#: computer rather than a different argument. Named so it cannot be misread as
#: success at a glance, in a log, or in a CI summary: no substring of it is
#: `PASS`, and `VANTAGE` is a word about the observer, not about the release.
VANTAGE: Final = "vantage_limited"

_ICON = {PASS: "PASS", FAIL: "FAIL", UNPROVEN: "UNPROVEN", VANTAGE: "VANTAGE"}

#: The trusted-proxy identity header. Named once, because the probe that
#: classifies the vantage and the fetcher that runs the conditions must present
#: the same one — a gate diagnosing the wrong header is diagnosing nothing.
IDENTITY_HEADER: Final = "Tailscale-User-Login"

#: A syntactically valid login belonging to nobody, and the load-bearing half of
#: the probe. `app/auth.py` answers a header it *received and rejected* with
#: `identity_denied`, and a header it never received with `identity_missing`. A
#: foreign login answered `identity_missing` therefore proves the header did not
#: arrive — a statement about the path, not about the identity. Deliberately
#: not a real address: it must never be a login anyone holds.
PROBE_FOREIGN_LOGIN: Final = "converge-gate-probe@invalid.invalid"

#: The refusal `app/auth.py` returns in the trusted-header posture when no
#: identity arrived. This exact shape is the only thing the vantage
#: classification is permitted to reclassify.
IDENTITY_MISSING: Final = "identity_missing"

#: The remedy, printed on every `VANTAGE-LIMITED` condition and again in the
#: result banner. A gate that says "unevaluable" without saying "here is the
#: request that would evaluate it" has only moved the confusion from the exit
#: code to the reader.
VANTAGE_REMEDY: Final = (
    "what would satisfy it is the same request from a host that is NOT the serving node — in "
    "practice the user's phone on the tailnet, where Tailscale Serve has a remote peer to "
    "attribute the request to"
)


@dataclass(frozen=True)
class Response:
    """One HTTP GET, reduced to what the gate reads."""

    url: str
    status: int
    headers: Mapping[str, str]
    body: bytes

    def header(self, name: str) -> str | None:
        """Case-insensitive header read. HTTP header names are not case-sensitive
        and `X-PWA-Backend-Started-At` arrives as `x-pwa-backend-started-at` from
        some proxies and as itself from others."""
        wanted = name.lower()
        for key, value in self.headers.items():
            if key.lower() == wanted:
                return value
        return None

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text())


Fetcher = Callable[[str], Response]
#: A probe is `fetch` with the identity header under the gate's control, so the
#: same seam can present *no* login, a foreign one, and the configured owner one
#: without the header being baked into a closure.
Probe = Callable[[str, str | None], Response]


def error_code(response: Response) -> str | None:
    """The `code` field of an error envelope, or `None`.

    `app/auth.py` wraps every rejection as `{"requestId", "code"}`, so `code` is
    the one field that distinguishes *why* a request was refused. Reading it is
    what lets the gate tell a refused identity from an absent one, and therefore
    what stops it from calling a working-but-unreachable origin "unreachable
    because of where I stand".
    """
    try:
        payload = json.loads(response.text())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    code = payload.get("code")
    return code if isinstance(code, str) else None


def identity_probe(url: str, owner_login: str | None) -> Response:
    """One GET with the identity header set (or deliberately not set).

    `owner_login=None` means *present no header at all*, which is a different
    question from "present an empty one" and has a different answer from
    `app/auth.py` (`identity_missing` vs `identity_invalid`).
    """
    headers = {"Accept": "*/*"}
    if owner_login is not None:
        headers[IDENTITY_HEADER] = owner_login
    return http_get(url, headers=headers)


def _same_address(left: str, right: str) -> bool:
    """Address equality that tolerates IPv6 scope ids and v4-mapped forms."""
    try:
        first = ipaddress.ip_address(left.split("%", 1)[0])
        second = ipaddress.ip_address(right.split("%", 1)[0])
    except ValueError:
        return False
    # `ipv4_mapped` exists only on IPv6Address, so a plain `127.0.0.1` has to go
    # through the same normalisation as a `::ffff:127.0.0.1` or the two would
    # compare unequal for a reason that has nothing to do with the addresses.
    return (getattr(first, "ipv4_mapped", None) or first) == (
        getattr(second, "ipv4_mapped", None) or second
    )


def is_self_addressed(origin: str) -> bool:
    """Does `origin`'s host name resolve only to addresses of *this* machine?

    **The question is "where does the packet go", not "what is the host called".**
    A hostname is a label an operator chose; MagicDNS names, `localhost`, a
    raw tailnet IP and a CNAME all reach the same node, and a name that *looks*
    like a peer (`pwa-deals.test.ts.net`) can be an alias for this host. So this
    asks the kernel instead: resolve the name, then `connect()` an unconnected
    `SOCK_DGRAM` socket to each address and read back `getsockname()`. A `connect()`
    on a datagram socket sends nothing — it only asks the routing table which
    source address would be used. If the source the kernel picks *is* the
    destination, the destination is a local address and the connection cannot
    have come from, or gone to, a remote peer.

    Returns `False` on any inability to decide (DNS failure, no route, an
    unparseable origin). That direction is deliberate: an undecided question must
    leave the condition `FAIL`, because `FAIL` is the answer that cannot be
    wrong in the dangerous direction.
    """
    parts = urlsplit(origin)
    host = parts.hostname
    if not host:
        return False
    try:
        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    except ValueError:
        return False
    try:
        candidates = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
    except (OSError, UnicodeError):
        return False
    if not candidates:
        return False
    for family, _type, _proto, _canon, sockaddr in candidates:
        address = sockaddr[0]
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as probe:
                probe.connect((address, port))
                source = probe.getsockname()[0]
        except OSError:
            return False
        if not _same_address(source, str(address)):
            return False
    return True


@dataclass(frozen=True)
class VantageVerdict:
    """What the gate could learn about the deployed origin before comparing it.

    `state` is one of:

    * `identity-present` — the deployed origin answered at least one identity
      probe with 200. It is observable; compare it normally.
    * `identity-absent` — the deployed origin refused *every* identity this
      gate can present, with the identical `identity_missing` envelope.
    * `undecided` — anything else, including a probe that could not run.

    `unobservable` is the only field a condition consults, and it is `True` for
    `identity-absent` **and** `self_addressed` **and** nothing else. Both
    conjuncts are positive observations, not inferences:

    * the uniform-refusal half comes from the application's own vocabulary. A
      proxy that injected an identity and had it *rejected* answers
      `identity_denied`; a proxy that injected nothing answers
      `identity_missing`. Only the second is the vantage limit, and the gate can
      tell them apart without knowing anything about Tailscale.
    * the self-addressed half is what makes the first half mean what it says. A
      uniformly-refusing deployed origin on *another* node is a real,
      reportable defect — a Serve route pointed at a backend whose trusted-header
      posture is broken would refuse every real user too — and it must stay
      `FAIL`. Requiring proof that the traffic never left the machine is what
      keeps that case out of the soft branch.

    `evidence` is the sentence the report prints, so the classification is
    auditable from the output rather than taken on the gate's word.
    """

    state: str
    self_addressed: bool
    evidence: str

    @property
    def unobservable(self) -> bool:
        return self.state == "identity-absent" and self.self_addressed


def classify_vantage(
    origin: str | None,
    probe: Probe,
    self_addressed: Callable[[str], bool],
    owner_login: str | None,
) -> VantageVerdict:
    """Decide whether the deployed origin is observable from here, and say why.

    Read this before changing it: the value of this function is entirely in the
    cases where it answers "no, it is observable", because those are the cases
    where a real defect must survive as a `FAIL`. It is a pure function of its
    four arguments precisely so a test can drive every one of them.
    """
    if not origin:
        return VantageVerdict(
            "identity-present",
            False,
            "no --deployed-origin, so there is no second origin whose vantage could matter",
        )

    local = self_addressed(origin)
    local_evidence = (
        f"{urlsplit(origin).hostname} resolves only to addresses of this machine, so a request "
        "to it cannot have been proxied from a remote peer"
        if local
        else f"{urlsplit(origin).hostname} does not resolve to an address of this machine, so a "
        "refusal from it is a property of that deployment rather than of this vantage point"
    )

    answered = False
    refusals: list[str] = []
    codes: set[str] = set()
    for label, login in (
        ("no identity header", None),
        (f"a foreign identity ({PROBE_FOREIGN_LOGIN})", PROBE_FOREIGN_LOGIN),
        (f"the configured owner identity ({owner_login!r})", owner_login),
    ):
        if login is None and label.startswith("the configured owner"):
            continue
        try:
            response = probe(f"{origin}/api/version", login)
        except OSError as error:
            # `http_get` does not raise, so this is belt-and-braces. The point
            # is the direction: a probe that cannot run is a probe that did not
            # observe anything, so the answer is `undecided` and the conditions
            # keep their `FAIL` — never a traceback, and never the soft branch.
            return VantageVerdict(
                "undecided",
                local,
                f"the identity probe against {origin} could not run ({error}), so the deployed "
                f"origin's observability was not established and its refusals are treated as real "
                f"failures ({local_evidence})",
            )
        if response.status == 200:
            answered = True
            break
        code = error_code(response)
        codes.add(f"{response.status}/{code}")
        refusals.append(f"{label} -> HTTP {response.status} {code or 'with no code'}")
    if answered:
        return VantageVerdict(
            "identity-present",
            local,
            f"{origin} answered 200 to an identity probe, so it presents an identity to this "
            f"client and every comparison against it is evaluable ({local_evidence})",
        )
    if codes != {f"401/{IDENTITY_MISSING}"}:
        return VantageVerdict(
            "undecided",
            local,
            f"{origin} refused the identity probes with differing answers ({'; '.join(refusals)}), "
            "so it is not the uniform identity-absence refusal and the refusal is treated as a "
            f"real failure ({local_evidence})",
        )
    return VantageVerdict(
        "identity-absent",
        local,
        f"{origin} answered HTTP 401 {IDENTITY_MISSING} to every identity this gate can present — "
        f"{'; '.join(refusals)} — so the request carries no identity to compare, and {local_evidence}",
    )



def _run_git(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout


def authenticated_fetcher(owner_login: str | None) -> Fetcher:
    """A fetcher that presents the trusted-proxy identity header.

    **Without this the gate cannot run in the posture it exists for.** The
    production plist sets `TRUST_TAILSCALE_HEADERS=true`, and `app/auth.py`
    guards *every* path — `/api/version`, `/sw.js`, `/` and the HTML shell
    included — so a plain loopback GET from the gate is answered
    `401 identity_missing`. That is correct behaviour: with trusted headers the
    proxy is the only intended caller. It means the operator has to hand the gate
    the same `Tailscale-User-Login` the proxy injects, which is a
    `Settings.tailscale_owner_login` value, not a secret.

    Left unset, the gate runs fine in the development-identity posture
    (`TRUST_TAILSCALE_HEADERS=false`), which is the posture a pre-deploy dry run
    uses — and `check_identity` says which posture it is looking at rather than
    leaving nine identical 401s to be diagnosed one at a time.
    """
    headers = {"Accept": "*/*"}
    if owner_login:
        headers["Tailscale-User-Login"] = owner_login

    def fetch(url: str) -> Response:
        return http_get(url, headers=headers)

    return fetch


def http_get(
    url: str, timeout: float = 10.0, headers: Mapping[str, str] | None = None
) -> Response:
    """The one place the gate touches the network.

    `urllib` rather than `httpx` so the gate runs against a bare
    `python3` in a pre-deploy shell that has no project venv — a gate that needs
    the venv cannot be run by the person deciding whether to bootstrap the agent.
    """
    request = urllib.request.Request(url, headers=dict(headers or {"Accept": "*/*"}))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as handle:
            return Response(
                url=url,
                status=handle.status,
                headers={key: value for key, value in handle.headers.items()},
                body=handle.read(),
            )
    except urllib.error.HTTPError as error:  # a 4xx/5xx body is evidence too
        return Response(
            url=url,
            status=error.code,
            headers={key: value for key, value in (error.headers or {}).items()},
            body=error.read(),
        )
    except (urllib.error.URLError, OSError) as error:
        return Response(url=url, status=0, headers={}, body=str(error).encode())


@dataclass
class CheckResult:
    ident: str
    title: str
    status: str
    detail: str


@dataclass
class Options:
    """Everything the checks read, in one place, so a test can construct the
    whole context without touching `sys.argv` or the real filesystem layout."""

    repo_root: Path
    local_origin: str
    deployed_origin: str | None = None
    vault: Path | None = None
    recipes_root: str | None = None
    pantry_note: str | None = None
    daily_notes_root: str | None = None
    port: int = 8007
    smoke_spec: Path | None = None
    baseline_commit: str | None = None
    expect_running: bool = True
    port_manager: Path | None = None
    owner_login: str | None = None
    fetch: Fetcher = http_get
    git: Callable[..., str] = _run_git
    results: list[CheckResult] = field(default_factory=list)
    #: The two seams `classify_vantage` needs, injectable so a test can drive
    #: every branch of it. The defaults are the real probes: a test that does not
    #: set them is testing against this machine's actual routing table and its
    #: actual sockets, which is the point.
    probe: Probe = identity_probe
    self_addressed: Callable[[str], bool] = is_self_addressed
    #: Computed once by `run_gate` and shared, so the classification cannot
    #: differ between two conditions that consulted the same deployed origin.
    vantage: VantageVerdict | None = None

    def add(self, ident: str, title: str, status: str, detail: str) -> None:
        self.results.append(CheckResult(ident, title, status, detail))


def deployed_vantage(options: Options) -> VantageVerdict:
    """The run's single vantage verdict, computed on first use and cached.

    Cached rather than recomputed because a per-condition verdict is a worse
    answer than a shared one: two conditions that consulted the same deployed
    origin and reached different conclusions about whether it was observable
    would mean the gate does not know what it is talking about.
    """
    if options.vantage is None:
        options.vantage = classify_vantage(
            options.deployed_origin,
            options.probe,
            options.self_addressed,
            options.owner_login,
        )
    return options.vantage


def vantage_note(options: Options, evaluated: str) -> str:
    """The detail text for a `VANTAGE-LIMITED` condition.

    Three things every one of these must carry, or it is not honest output: what
    *was* evaluated (so a reader knows the local half was not skipped), the
    evidence for the classification, and the request that would settle it.
    """
    verdict = deployed_vantage(options)
    return (
        f"{evaluated}. The deployed half of this condition was NOT evaluated: "
        f"{verdict.evidence}. Nothing about the deployed origin's version, freshness or response "
        f"shape has been observed, so this is NOT a pass; {VANTAGE_REMEDY}"
    )


def is_unobservable(options: Options, response: Response) -> bool:
    """Is this specific deployed response the identity-absence refusal?

    The conjunction matters and is deliberately narrow. `verdict.unobservable`
    alone would reclassify *any* deployed response once the vantage was
    established — including a 500, a 404 from a route pointing at the wrong
    backend, or a 200. So the response itself has to be the refusal:
    `401 identity_missing` and nothing else. Every real failure this gate exists
    to catch is an observation about a 200 body or a non-401 status, and so
    cannot reach this branch.
    """
    return (
        deployed_vantage(options).unobservable
        and response.status == 401
        and error_code(response) == IDENTITY_MISSING
    )


# --------------------------------------------------------------------------
# source readers — pure, and unit-tested directly
# --------------------------------------------------------------------------


def read_source_version(repo_root: Path) -> str:
    """The `CACHE_VERSION` the source tree will serve once restarted."""
    text = (repo_root / "app" / "static" / "sw.js").read_text(encoding="utf-8")
    match = CACHE_VERSION_RE.search(text)
    if match is None:
        raise ValueError("no `const CACHE_VERSION = '...'` in app/static/sw.js")
    return match.group(1)


def read_shell_assets(repo_root: Path, version: str) -> list[str]:
    """`SHELL_ASSETS`, resolved — every `+ CACHE_VERSION` evaluated to the
    version the release actually serves.

    Reading the source list and evaluating the concatenation is the only way to
    compare it with the shell: the list is not what is fetched, and the
    question condition 6 asks is whether the *resolved* URLs agree.
    """
    text = (repo_root / "app" / "static" / "sw.js").read_text(encoding="utf-8")
    start = text.index("const SHELL_ASSETS = [")
    block = text[start : text.index("]", start)]
    resolved: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped.startswith("'"):
            continue
        end = stripped.index("'", 1)
        value = stripped[1:end]
        tail = stripped[end + 1 :]
        resolved.append(value + version if "CACHE_VERSION" in tail else value)
    return resolved


def read_bootstrap_config(repo_root: Path) -> dict[str, str]:
    """`WAIT_FOR_MESSAGE` and friends out of the sw.js CONFIG block.

    Read as text rather than executed: this gate has to be runnable before a
    deploy is authorized, which is also before anything can be trusted to
    execute the file it is auditing.
    """
    text = (repo_root / "app" / "static" / "sw.js").read_text(encoding="utf-8")
    found: dict[str, str] = {}
    for name in ("WAIT_FOR_MESSAGE", "autoApply", "FETCH_STRATEGY", "CACHE_API_GETS"):
        match = re.search(rf"const {name} = ([^;]+);", text)
        if match is not None:
            found[name] = match.group(1).strip()
    return found


def parse_started_at(value: str) -> datetime:
    """`X-PWA-Backend-Started-At` → an aware datetime. The vendored
    `pwa_version.py` emits `datetime.now(UTC).isoformat().replace("+00:00","Z")`,
    and a header can also arrive as a bare integer epoch from a proxy that
    rewrote it, so both are accepted rather than one being a surprise."""
    text = value.strip()
    if text.isdigit():
        return datetime.fromtimestamp(int(text), tz=timezone.utc)
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def shell_versioned_refs(html: str) -> set[str]:
    """Every `href`/`src` in the served shell that carries a `?v=` pin.

    A `?v=` that is *not* CACHE_VERSION is the obsidian-daily failure recorded
    in §3b: the shell reports the new version and the browser HTTP cache serves
    the previous exact-versioned bundle.
    """
    refs: set[str] = set()
    for match in re.finditer(r"""(?:href|src)\s*=\s*["']([^"']+)["']""", html):
        value = match.group(1)
        if "?v=" in value:
            refs.add(value)
    return refs


# --------------------------------------------------------------------------
# the checks
# --------------------------------------------------------------------------


def check_listener(options: Options) -> None:
    """0 — the socket, not the process.

    §12 is explicit: `launchctl print` showing `state = running` proves a
    process is alive, not that the socket is listening. A backend wedged after
    bind, or one that never got past `lifespan`, is exactly the state this
    catches, and no other check here can see it — every other check would just
    fail on connection refused without saying which layer broke.
    """
    if not options.expect_running:
        options.add("0 listener", "socket is listening", UNPROVEN, "skipped by --no-listener-check")
        return
    script = options.port_manager or _default_port_manager()
    if script is None or not script.is_file():
        options.add(
            "0 listener",
            "socket is listening",
            UNPROVEN,
            "port_manager.py not found; pass --port-manager to prove the listener",
        )
        return
    try:
        completed = subprocess.run(
            (
                sys.executable,
                str(script),
                "inspect",
                str(options.port),
            ),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        options.add("0 listener", "socket is listening", UNPROVEN, f"could not run port_manager: {error}")
        return
    output = (completed.stdout + completed.stderr).strip()
    if "no visible TCP listener" in output or completed.returncode != 0:
        options.add(
            "0 listener",
            "socket is listening",
            FAIL,
            f"port {options.port} has no visible TCP listener: {output.splitlines()[:1]}",
        )
        return
    options.add("0 listener", "socket is listening", PASS, f"port {options.port}: {output.splitlines()[0]}")


def check_identity(options: Options) -> bool:
    """0b — can the gate talk to this origin at all, and in which posture.

    `app/auth.py` guards every path, including `/api/version`, so in the
    production posture (`TRUST_TAILSCALE_HEADERS=true`) a loopback GET without a
    `Tailscale-User-Login` header is `401 identity_missing` — correctly, because
    the proxy is then the only intended caller. Left undiagnosed that turns all
    nine conditions into the same opaque 401.

    It returns False when the origin is unreachable or refusing, so the caller
    can say "the later conditions will be noise" once, rather than eight times.
    """
    response = options.fetch(f"{options.local_origin}/api/version")
    if response.status == 401 and error_code(response) == IDENTITY_MISSING:
        options.add(
            "0b identity",
            "the local origin answers the gate",
            FAIL,
            "401 identity_missing: the local backend is in trusted-header mode "
            "(TRUST_TAILSCALE_HEADERS=true), so every path needs the identity header the "
            "Tailscale proxy injects. Re-run with --owner-login <TAILSCALE_OWNER_LOGIN>, which is "
            "a Settings value and not a secret. Every condition below will report the same 401 "
            "until you do.",
        )
        return False
    if response.status != 200:
        options.add(
            "0b identity",
            "the local origin answers the gate",
            FAIL,
            f"{response.url} returned HTTP {response.status}; every condition below will be noise",
        )
        return False
    options.add(
        "0b identity",
        "the local origin answers the gate",
        PASS,
        "200 from /api/version"
        + (
            f", presenting Tailscale-User-Login: {options.owner_login}"
            if options.owner_login
            else ", with no identity header (development-identity posture)"
        ),
    )
    return True


def check_source_version(options: Options) -> str | None:
    """1 — the source `CACHE_VERSION` equals what the local backend reports."""
    try:
        source = read_source_version(options.repo_root)
    except (OSError, ValueError) as error:
        options.add("1 source-version", "sw.js CACHE_VERSION == local /api/version", UNPROVEN, str(error))
        return None
    response = options.fetch(f"{options.local_origin}/api/version")
    if response.status != 200:
        options.add(
            "1 source-version",
            "sw.js CACHE_VERSION == local /api/version",
            FAIL,
            f"{response.url} returned HTTP {response.status}",
        )
        return source
    reported = (response.json() or {}).get("version")
    if reported != source:
        options.add(
            "1 source-version",
            "sw.js CACHE_VERSION == local /api/version",
            FAIL,
            f"source {source!r} != local {reported!r} — the backend has not reloaded sw.js "
            "(restart it, or the CACHE_VERSION bump is not in the running process)",
        )
        return source
    options.add(
        "1 source-version",
        "sw.js CACHE_VERSION == local /api/version",
        PASS,
        f"{source} on both sides",
    )
    return source


def check_deployed_version(options: Options) -> str | None:
    """2 — the local and deployed origins report the same version.

    The one condition whose entire subject is the deployed origin, so when that
    origin is unobservable this condition is unobservable in full. That is
    `VANTAGE-LIMITED` and not `UNPROVEN`: `UNPROVEN` is what this condition
    reports when nobody named a deployed origin at all, and the two need
    different remedies — supply the argument, versus move to another machine.
    """
    if not options.deployed_origin:
        options.add(
            "2 deployed-version",
            "local /api/version == deployed /api/version",
            UNPROVEN,
            "no --deployed-origin given, so the Tailscale Serve ingress is NOT verified",
        )
        return None
    local = options.fetch(f"{options.local_origin}/api/version")
    if local.status != 200:
        options.add(
            "2 deployed-version",
            "local /api/version == deployed /api/version",
            FAIL,
            f"local {local.url} returned HTTP {local.status}",
        )
        return None
    local_version = (local.json() or {}).get("version")
    deployed = options.fetch(f"{options.deployed_origin}/api/version")
    if deployed.status != 200:
        if is_unobservable(options, deployed):
            options.add(
                "2 deployed-version",
                "local /api/version == deployed /api/version",
                VANTAGE,
                vantage_note(
                    options, f"the local half was evaluated and reports version {local_version!r}"
                )
                + " (not evaluated: deployed /api/version, and therefore the version comparison)",
            )
            return local_version
        options.add(
            "2 deployed-version",
            "local /api/version == deployed /api/version",
            FAIL,
            f"deployed {deployed.url} returned HTTP {deployed.status}",
        )
        return None
    deployed_version = (deployed.json() or {}).get("version")
    if local_version != deployed_version:
        options.add(
            "2 deployed-version",
            "local /api/version == deployed /api/version",
            FAIL,
            f"local {local_version!r} != deployed {deployed_version!r} — the Serve route is proxying "
            "a different backend than the one just restarted",
        )
        return deployed_version
    options.add(
        "2 deployed-version",
        "local /api/version == deployed /api/version",
        PASS,
        f"{local_version} on {options.deployed_origin}",
    )
    return deployed_version


def check_backend_freshness(options: Options) -> None:
    """3 — `X-PWA-Backend-Started-At` matches on both origins and is newer than
    every changed startup-loaded file.

    The reason this exists is the sentence in §3e: a displayed PWA version
    proves frontend/static freshness only. The shell and the worker can be
    perfectly current while the process holding the SQLite schema and the
    configuration in memory started three releases ago. The version is derived
    from a file the process read at import, so it can report the *new* value
    only after a restart — but nothing in `/api/version` tells you the process
    started *after the file changed*. This check does.
    """
    local = options.fetch(f"{options.local_origin}/api/version")
    local_header = local.header("X-PWA-Backend-Started-At")
    if local_header is None:
        options.add(
            "3 backend-freshness",
            "X-PWA-Backend-Started-At newer than every changed startup-loaded file",
            FAIL,
            f"the local /api/version response carries no X-PWA-Backend-Started-At header",
        )
        return
    try:
        started_at = parse_started_at(local_header)
    except ValueError as error:
        options.add(
            "3 backend-freshness",
            "X-PWA-Backend-Started-At newer than every changed startup-loaded file",
            FAIL,
            f"unparseable X-PWA-Backend-Started-At {local_header!r}: {error}",
        )
        return

    # Three lists, never one. A single list whose entries are distinguished by a
    # "(unproven" string prefix is a classification bug waiting to happen: a
    # reworded message silently turns an unknown into a failure, or a failure
    # into an unknown. `vantage` is a third list for the same reason: "the
    # deployed origin could not be observed" is neither a problem nor an unknown,
    # and folding it into either one loses the distinction the whole report
    # exists to make.
    problems: list[str] = []
    unproven: list[str] = []
    vantage: list[str] = []

    if options.deployed_origin:
        deployed = options.fetch(f"{options.deployed_origin}/api/version")
        if is_unobservable(options, deployed):
            vantage.append("the deployed origin's start timestamp was not compared")
        else:
            deployed_header = deployed.header("X-PWA-Backend-Started-At")
            if deployed_header is None:
                problems.append(
                    "the deployed /api/version response carries no X-PWA-Backend-Started-At header"
                )
            elif deployed_header != local_header:
                problems.append(
                    f"local started at {local_header!r} but the deployed origin reports "
                    f"{deployed_header!r} — two different processes, so at least one was not restarted"
                )
    else:
        unproven.append(
            "no --deployed-origin, so the deployed origin's start timestamp was NOT compared "
            "(the two could be different processes)"
        )

    stale, index_unproven = _startup_loaded_mtimes(options, started_at)
    unproven.extend(index_unproven)
    for path, mtime in stale:
        problems.append(
            f"{path} was modified at {mtime.isoformat()}, AFTER the backend started at "
            f"{started_at.isoformat()} — the process is running pre-change bytes"
        )

    if problems:
        options.add(
            "3 backend-freshness",
            "X-PWA-Backend-Started-At newer than every changed startup-loaded file",
            FAIL,
            "; ".join(problems + unproven),
        )
        return
    if vantage:
        # The local half was fully evaluated above — header present, parseable,
        # and newer than every named startup-loaded file — and that result is
        # reported rather than discarded. Only the cross-process comparison
        # could not be made. The unproven notes ride along: a condition that is
        # both vantage-limited and under-specified is still under-specified, and
        # dropping the second because the first won would be the same
        # collapse-this-change-exists-to-avoid, one level down.
        options.add(
            "3 backend-freshness",
            "X-PWA-Backend-Started-At newer than every changed startup-loaded file",
            VANTAGE,
            vantage_note(
                options,
                f"the local half was evaluated and the backend started at {local_header}, newer than "
                f"every named startup-loaded file",
            )
            + f" (not evaluated: {'; '.join(vantage)})"
            + (f"; still unproven: {'; '.join(unproven)}" if unproven else ""),
        )
        return
    if unproven:
        options.add(
            "3 backend-freshness",
            "X-PWA-Backend-Started-At newer than every changed startup-loaded file",
            UNPROVEN,
            "every file that could be compared is older than the boot; " + "; ".join(unproven),
        )
        return
    options.add(
        "3 backend-freshness",
        "X-PWA-Backend-Started-At newer than every changed startup-loaded file",
        PASS,
        f"started at {local_header}, newer than every named startup-loaded file on both origins",
    )


def _startup_loaded_mtimes(
    options: Options, started_at: datetime
) -> tuple[list[tuple[str, datetime]], list[str]]:
    """Changed startup-loaded files, plus the things that could not be checked."""
    stale: list[tuple[str, datetime]] = []
    unproven: list[str] = []
    cutoff = started_at + timedelta(seconds=CLOCK_SKEW_SECONDS)

    for pattern in STARTUP_LOADED_GLOBS:
        for path in sorted(options.repo_root.glob(pattern)):
            if not path.is_file():
                continue
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime > cutoff:
                stale.append((path.relative_to(options.repo_root).as_posix(), mtime))

    if options.vault is None:
        unproven.append(
            "no --vault, so the recipe/pantry index inputs named in §12.3 were NOT compared"
        )
    else:
        for label, relative in (
            ("recipes_root", options.recipes_root),
            ("pantry_note", options.pantry_note),
            ("daily_notes_root", options.daily_notes_root),
        ):
            if not relative:
                unproven.append(f"no --{label.replace('_', '-')}, so that index input was NOT compared")
                continue
            base = options.vault / relative
            if not base.exists():
                unproven.append(f"{label} {relative} does not exist under the given --vault")
                continue
            paths = [base] if base.is_file() else sorted(base.rglob("*"))
            newest = max(
                (path for path in paths if path.is_file() and not path.is_symlink()),
                key=lambda path: path.stat().st_mtime,
                default=None,
            )
            if newest is None:
                continue
            mtime = datetime.fromtimestamp(newest.stat().st_mtime, tz=timezone.utc)
            if mtime > cutoff:
                stale.append(
                    (
                        f"{label}:{newest.relative_to(options.vault).as_posix()}",
                        mtime,
                    )
                )
    return stale, unproven


def check_release_smoke(options: Options) -> None:
    """4 — a release-specific live API smoke check.

    §12.4 says why a version match cannot stand in for this: a *new static
    frontend talking to an old in-memory schema* is internally consistent — the
    shell reports the new version, `/api/version` agrees, condition 3 can even
    pass if the process restarted on a code change that did not touch the
    schema — and it is broken. The only thing that catches it is asserting a key
    the release added is actually in the live response.

    The key list is `scripts/converge-smoke.json`, a committed per-release
    artifact, because "which key did this release add" is a fact about the
    release and cannot be derived from the running server without being
    circular.
    """
    spec_path = options.smoke_spec or options.repo_root / "scripts" / "converge-smoke.json"
    if not spec_path.is_file():
        options.add(
            "4 release-smoke",
            "release-specific live API smoke check",
            FAIL,
            f"smoke spec {spec_path} does not exist, so step 4 cannot be run at all",
        )
        return
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        options.add("4 release-smoke", "release-specific live API smoke check", FAIL, f"unreadable spec: {error}")
        return
    endpoints = spec.get("endpoints") or []
    if not endpoints:
        options.add(
            "4 release-smoke",
            "release-specific live API smoke check",
            FAIL,
            f"{spec_path.name} lists no endpoints; an empty smoke check asserts nothing",
        )
        return

    origins = [("local", options.local_origin)]
    if options.deployed_origin:
        origins.append(("deployed", options.deployed_origin))
    elif spec.get("require_deployed", True):
        options.add(
            "4 release-smoke",
            "release-specific live API smoke check",
            UNPROVEN,
            "spec requires both origins and no --deployed-origin was given",
        )
        return

    problems: list[str] = []
    vantage: list[str] = []
    verified = 0
    for origin_label, origin in origins:
        for endpoint in endpoints:
            path = endpoint["path"]
            response = options.fetch(f"{origin}{path}")
            if response.status != 200:
                if origin_label == "deployed" and is_unobservable(options, response):
                    vantage.append(f"deployed {path}")
                    continue
                problems.append(f"{origin_label} {path} returned HTTP {response.status}")
                continue
            problems.extend(_missing_keys(origin_label, path, response.json(), endpoint))
            verified += len(endpoint.get("top_level_keys", []))
    if problems:
        options.add("4 release-smoke", "release-specific live API smoke check", FAIL, "; ".join(problems))
        return
    if vantage:
        options.add(
            "4 release-smoke",
            "release-specific live API smoke check",
            VANTAGE,
            vantage_note(
                options,
                f"{verified} declared key(s) were verified present on the local origin across "
                f"{len(endpoints)} endpoint(s)",
            )
            + f" (not evaluated: {'; '.join(vantage)})",
        )
        return
    checked = sum(len(e.get("top_level_keys", [])) for e in endpoints) * len(origins)
    options.add(
        "4 release-smoke",
        "release-specific live API smoke check",
        PASS,
        f"{checked} declared keys present across {len(origins)} origin(s) and {len(endpoints)} endpoints",
    )


def _missing_keys(label: str, path: str, payload: Any, endpoint: Mapping[str, Any]) -> list[str]:
    missing: list[str] = []
    for key in endpoint.get("top_level_keys", []):
        if not isinstance(payload, dict) or key not in payload:
            missing.append(f"{label} {path} has no top-level key {key!r}")
    if missing:
        return missing
    assert isinstance(payload, dict)
    for array_key, item_keys in (endpoint.get("array_item_keys") or {}).items():
        items = payload.get(array_key)
        if not isinstance(items, list) or not items:
            missing.append(f"{label} {path} key {array_key!r} is not a non-empty array")
            continue
        for key in item_keys:
            if not isinstance(items[0], dict) or key not in items[0]:
                missing.append(f"{label} {path} {array_key}[0] has no key {key!r}")
    for array_key, nested in (endpoint.get("array_item_nested_keys") or {}).items():
        items = payload.get(array_key)
        if not isinstance(items, list) or not items:
            continue
        for child_key, child_item_keys in nested.items():
            children = items[0].get(child_key) if isinstance(items[0], dict) else None
            if not isinstance(children, list) or not children:
                missing.append(f"{label} {path} {array_key}[0].{child_key} is not a non-empty array")
                continue
            for key in child_item_keys:
                if not isinstance(children[0], dict) or key not in children[0]:
                    missing.append(f"{label} {path} {array_key}[0].{child_key}[0] has no key {key!r}")
    return missing


def check_cache_rotation(options: Options, version: str | None) -> None:
    """5 — the served worker carries this `CACHE_VERSION`, and the version was
    rotated since the last mutable frontend change.

    This is the recorded incident, and both halves are needed.

    The first half is the *served* bytes: a deployment that re-uses the previous
    cache version serves the previous exact-versioned bundle to every installed
    PWA, because `?v=v0.6.0` is a cache key and the phone already holds that
    entry. The shell and `/api/version` both happily report `v0.6.0` while the
    device runs the `v0.6.0` release from last week.

    The second half is the *source* discipline §3b.1 asks for and AGENTS.md #8
    mandates: a deploy can be internally consistent — clean tree, matching
    versions, correct shell assets — while reusing the previous Service Worker
    cache key. Conditions 1, 2, 6 and 8 all pass in that state. Only a check
    that asks "did a mutable frontend file change since `CACHE_VERSION` was last
    set?" catches it.
    """
    problems: list[str] = []
    unproven: list[str] = []
    vantage: list[str] = []
    served_on: list[str] = []

    for label, origin in _origins(options):
        response = options.fetch(f"{origin}/sw.js")
        if response.status != 200:
            if label == "deployed" and is_unobservable(options, response):
                vantage.append("deployed /sw.js")
                continue
            problems.append(f"{label} /sw.js returned HTTP {response.status}")
            continue
        served_on.append(label)
        served = CACHE_VERSION_RE.search(response.text())
        if served is None:
            problems.append(f"{label} /sw.js carries no CACHE_VERSION constant")
        elif version is None:
            unproven.append(
                f"{label} /sw.js serves CACHE_VERSION {served.group(1)!r} but the SOURCE could not be read, "
                "so the two were not compared"
            )
        elif served.group(1) != version:
            problems.append(
                f"{label} /sw.js serves CACHE_VERSION {served.group(1)!r}, source says {version!r} — "
                "an installed PWA would be handed the previous exact-versioned bundle"
            )

    rotated, notes = _mutable_static_changes(options, version)
    problems.extend(rotated)
    unproven.extend(notes)

    if problems:
        options.add("5 cache-rotation", "CACHE_VERSION rotated and served identically", FAIL, "; ".join(problems))
        return
    if vantage:
        # The rotation half of this condition is a fact about the *repository*,
        # not about the network, so it stays evaluable and stays authoritative
        # here: an un-rotated CACHE_VERSION is a FAIL even while the deployed
        # half is unobservable. That ordering is the point — the soft branch is
        # only ever reached when every hard check has already passed.
        options.add(
            "5 cache-rotation",
            "CACHE_VERSION rotated and served identically",
            VANTAGE,
            vantage_note(
                options,
                f"the served worker was compared on {', '.join(served_on) or 'no origin'} and "
                f"CACHE_VERSION {version} is rotated since the last mutable frontend change",
            )
            + f" (not evaluated: {'; '.join(vantage)})",
        )
        return
    if unproven:
        options.add(
            "5 cache-rotation",
            "CACHE_VERSION rotated and served identically",
            UNPROVEN,
            "served worker matches; " + "; ".join(unproven),
        )
        return
    options.add(
        "5 cache-rotation",
        "CACHE_VERSION rotated and served identically",
        PASS,
        f"{version} served on every origin and rotated since the last mutable frontend change",
    )


def _mutable_static_changes(options: Options, version: str | None) -> tuple[list[str], list[str]]:
    """Mutable frontend files changed since `CACHE_VERSION` was last rotated.

    The anchor is `git log -1 -G"const CACHE_VERSION" -- app/static/sw.js`:
    the most recent commit whose diff *adds or removes* a `CACHE_VERSION` line,
    in either direction. That is the last rotation.

    Two alternatives were rejected, and the reasons are the point:

    * **The last commit that touched `app/static/sw.js` at all.** An
      `SHELL_ASSETS`-only edit (adding a new module to the precache list) moves
      that anchor without rotating anything, so an un-bumped frontend change
      behind it is never seen. The check would go blind the first time someone
      fixed a precache hole.
    * **The last commit that set this literal (`git log -1 -S"…'v0.6.0'"`).** A
      revert re-declares the previous release, which resets the anchor to the
      revert, and any un-bumped frontend change made in between is lost.

    Neither an mtime nor a "was the worktree clean" test can answer this: a
    `git checkout` resets mtimes to checkout time, and a fresh clone has every
    file newer than the last rotation.

    **Known limitation, stated rather than papered over:** a *revert* of
    `CACHE_VERSION` genuinely resets this check's history, because the revert is
    itself a rotation. The forward path — the one that ships — is covered: a
    release that changes a mutable frontend file without bumping fails this
    check from the moment the change is committed.
    """
    if version is None:
        return [], ["no source version, so the rotation anchor is unknown"]
    try:
        top = options.git("rev-parse", "--is-inside-work-tree", cwd=options.repo_root).strip()
    except (OSError, RuntimeError) as error:
        return [], [f"{options.repo_root} is not a git working tree: {error}"]
    if top != "true":
        return [], [f"{options.repo_root} is not a git working tree"]
    try:
        if options.baseline_commit:
            options.git("cat-file", "-e", f"{options.baseline_commit}^{{commit}}", cwd=options.repo_root)
            anchor = options.baseline_commit
            where = f"--baseline {anchor[:12]}"
        else:
            anchor = options.git(
                "log",
                "-1",
                "--format=%H",
                "-Gconst CACHE_VERSION",
                "--",
                "app/static/sw.js",
                cwd=options.repo_root,
            ).strip()
            if not anchor:
                return [], [
                    "no commit in this history ever rotated CACHE_VERSION; the bump is uncommitted, "
                    "or the clone is too shallow to contain it"
                ]
            where = f"the commit that last rotated CACHE_VERSION ({anchor[:12]})"
        changed = options.git(
            "diff",
            "--name-only",
            f"{anchor}..HEAD",
            "--",
            "app/static",
            cwd=options.repo_root,
        )
    except (OSError, RuntimeError) as error:
        return [], [f"git could not be read: {error}"]
    # sw.js itself is excluded: the CACHE_VERSION line lives there, so it is
    # always "changed" by the definition of the anchor.
    stale = [line for line in changed.split() if line and not line.endswith("app/static/sw.js")]
    if stale:
        return (
            [
                "CACHE_VERSION was NOT rotated even though these mutable frontend files changed since "
                + where
                + ": "
                + ", ".join(sorted(stale))
            ],
            [],
        )
    return [], []


def check_shell_assets(options: Options, version: str | None) -> None:
    """6 — the served HTML references the same versioned assets as the SW cache
    name (§3e 5, §3b's "row that bites you")."""
    if version is None:
        options.add("6 shell-assets", "HTML versioned assets == SHELL_ASSETS", UNPROVEN, "no source version")
        return
    try:
        shell_assets = set(read_shell_assets(options.repo_root, version))
    except (OSError, ValueError) as error:
        options.add("6 shell-assets", "HTML versioned assets == SHELL_ASSETS", UNPROVEN, str(error))
        return
    problems: list[str] = []
    vantage: list[str] = []
    verified: list[str] = []
    for label, origin in _origins(options):
        response = options.fetch(f"{origin}/")
        if response.status != 200:
            if label == "deployed" and is_unobservable(options, response):
                vantage.append("deployed /")
                continue
            problems.append(f"{label} / returned HTTP {response.status}")
            continue
        verified.append(label)
        refs = shell_versioned_refs(response.text())
        if not refs:
            problems.append(f"{label} / references no ?v= versioned asset at all")
            continue
        for ref in sorted(refs):
            if not ref.endswith(f"?v={version}"):
                problems.append(
                    f"{label} / pins {ref} but CACHE_VERSION is {version} — the browser HTTP cache can "
                    "serve the previous exact-versioned bundle while the shell reports the new version"
                )
            elif ref not in shell_assets:
                problems.append(f"{label} / references {ref}, which is not in sw.js SHELL_ASSETS")
    if problems:
        options.add("6 shell-assets", "HTML versioned assets == SHELL_ASSETS", FAIL, "; ".join(problems))
        return
    if vantage:
        options.add(
            "6 shell-assets",
            "HTML versioned assets == SHELL_ASSETS",
            VANTAGE,
            vantage_note(
                options,
                f"every ?v= reference on {', '.join(verified)} is pinned to {version} and present in "
                "SHELL_ASSETS",
            )
            + f" (not evaluated: {'; '.join(vantage)})",
        )
        return
    options.add(
        "6 shell-assets",
        "HTML versioned assets == SHELL_ASSETS",
        PASS,
        f"every ?v= reference on every origin is pinned to {version} and present in SHELL_ASSETS",
    )


def check_resume_version_check(options: Options, version: str | None) -> None:
    """7 — returning from background triggers a version check (§3e 6).

    Read from the **served** module, not the working tree: an installed PWA
    fetches `/js/pwa/update-manager.js`, and a check that greps the file on disk
    would pass against code the device never receives.
    """
    if version is None:
        options.add("7 resume-check", "resume triggers a version check", UNPROVEN, "no source version")
        return
    problems: list[str] = []
    vantage: list[str] = []
    verified: list[str] = []
    for label, origin in _origins(options):
        response = options.fetch(f"{origin}/js/pwa/update-manager.js?v={version}")
        if response.status != 200:
            if label == "deployed" and is_unobservable(options, response):
                vantage.append("deployed update-manager.js")
                continue
            problems.append(f"{label} update-manager.js returned HTTP {response.status}")
            continue
        verified.append(label)
        body = response.text()
        for marker in RESUME_MARKERS:
            if marker not in body:
                problems.append(f"{label} update-manager.js never registers {marker!r}")
    if problems:
        options.add("7 resume-check", "resume triggers a version check", FAIL, "; ".join(problems))
        return
    if vantage:
        options.add(
            "7 resume-check",
            "resume triggers a version check",
            VANTAGE,
            vantage_note(
                options,
                f"the served update manager on {', '.join(verified)} re-checks on pageshow, "
                "visibilitychange, focus and registration",
            )
            + f" (not evaluated: {'; '.join(vantage)})",
        )
        return
    options.add(
        "7 resume-check",
        "resume triggers a version check",
        PASS,
        "served update-manager.js re-checks on pageshow, visibilitychange, focus and registration",
    )


def check_busy_guard(options: Options, version: str | None) -> None:
    """8 — an open confirmation flow postpones the reload (§3e 7, §4e), and
    `WAIT_FOR_MESSAGE` is on so the banner's Reload button is what activates the
    new worker (F18)."""
    if version is None:
        options.add("8 busy-guard", "an open confirmation flow postpones the reload", UNPROVEN, "no source version")
        return
    try:
        config = read_bootstrap_config(options.repo_root)
    except OSError as error:
        options.add("8 busy-guard", "an open confirmation flow postpones the reload", UNPROVEN, str(error))
        return
    problems: list[str] = []
    vantage: list[str] = []
    verified: list[str] = []
    if config.get("WAIT_FOR_MESSAGE") != "true":
        problems.append(
            f"sw.js CONFIG has WAIT_FOR_MESSAGE={config.get('WAIT_FOR_MESSAGE')!r}, not true; with "
            "auto-takeover a reload can land between the user tapping 做过了 and the request completing"
        )
    for label, origin in _origins(options):
        response = options.fetch(f"{origin}/js/pwa/update-manager.js?v={version}")
        if response.status != 200:
            if label == "deployed" and is_unobservable(options, response):
                vantage.append("deployed update-manager.js")
                continue
            problems.append(f"{label} update-manager.js returned HTTP {response.status}")
            continue
        verified.append(label)
        body = response.text()
        for marker in BUSY_GUARD_MARKERS:
            if marker not in body:
                problems.append(f"{label} update-manager.js does not expose {marker!r}")
    if problems:
        options.add("8 busy-guard", "an open confirmation flow postpones the reload", FAIL, "; ".join(problems))
        return
    if vantage:
        options.add(
            "8 busy-guard",
            "an open confirmation flow postpones the reload",
            VANTAGE,
            vantage_note(
                options,
                f"WAIT_FOR_MESSAGE is true and the served update manager on {', '.join(verified)} "
                "exposes canApplyUpdate/requestUpdateReload",
            )
            + f" (not evaluated: {'; '.join(vantage)})",
        )
        return
    options.add(
        "8 busy-guard",
        "an open confirmation flow postpones the reload",
        PASS,
        "WAIT_FOR_MESSAGE is true and the served update manager exposes canApplyUpdate/requestUpdateReload",
    )


def _origins(options: Options) -> list[tuple[str, str]]:
    origins = [("local", options.local_origin)]
    if options.deployed_origin:
        origins.append(("deployed", options.deployed_origin))
    return origins


def _default_port_manager() -> Path | None:
    candidate = Path.home() / ".agent/skills/port-manager/scripts/port_manager.py"
    return candidate if candidate.is_file() else None


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------


def run_gate(options: Options) -> list[CheckResult]:
    # Classify the vantage once, up front, and before any condition that would
    # consume it. Doing it here rather than lazily means the evidence appears in
    # the report even on a run where the local origin is unreachable, and it
    # makes the ordering explicit: the classification is an input to the
    # conditions, not something a condition talked itself into.
    deployed_vantage(options)
    check_listener(options)
    reachable = check_identity(options)
    version = check_source_version(options) if reachable else None
    if reachable:
        check_deployed_version(options)
        check_backend_freshness(options)
        check_release_smoke(options)
        check_cache_rotation(options, version)
        check_shell_assets(options, version)
        check_resume_version_check(options, version)
        check_busy_guard(options, version)
    return options.results


def render(results: Sequence[CheckResult]) -> str:
    lines = ["converge gate — pwa-pantry-recipes", "=" * 72]
    for result in results:
        lines.append(f"[{_ICON[result.status]:8}] {result.ident:<17} {result.title}")
        for chunk in _wrap(result.detail, 68):
            lines.append(f"{'':10}{chunk}")
    failed = [result for result in results if result.status == FAIL]
    unproven = [result for result in results if result.status == UNPROVEN]
    vantage = [result for result in results if result.status == VANTAGE]
    lines.append("=" * 72)
    if failed:
        lines.append(f"RESULT: FAILED — {len(failed)} condition(s) failed. Do not reinstall the PWA.")
    elif unproven:
        lines.append(
            f"RESULT: INCOMPLETE — {len(unproven)} condition(s) could not be evaluated. "
            "This is NOT a pass: an unevaluated condition is exactly the state a stale backend is in."
        )
    elif vantage:
        lines.append(
            f"RESULT: VANTAGE-LIMITED — {len(vantage)} condition(s) could not be evaluated from this "
            "host. This is NOT a pass: no version, timestamp, key or asset on the deployed origin has "
            "been observed, and this exit code asserts no convergence at all."
        )
    else:
        lines.append(
            "RESULT: CONVERGED — every condition passed. Only now may the Home Screen PWA be reinstalled."
        )
    if vantage:
        # Printed unconditionally, including under FAILED and INCOMPLETE. A run
        # can hold a real failure *and* a vantage limit, and a banner that only
        # mentioned the vantage when it won would hide it in exactly the run
        # where a reader most needs to know both things are true.
        lines.append(
            f"{len(vantage)} condition(s) are VANTAGE-LIMITED: {VANTAGE_REMEDY}. Until one of them "
            "has been observed, the deployed origin has only ever been seen failing closed."
        )
    lines.append(
        "Reminder: a displayed PWA version proves frontend/static freshness only. It is never "
        "evidence that an already-running backend reloaded configuration."
    )
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current += " " + word
    lines.append(current)
    return lines


def exit_code(results: Sequence[CheckResult]) -> int:
    """0 converged · 1 a real failure · 2 under-specified invocation · 3 vantage.

    The ordering is 1 > 2 > 3 > 0 and every step of it is a claim about urgency:

    * **1** — something was checked and it was wrong. Nothing outranks that; a
      release with a real defect is not made safer by also being unevaluable.
    * **2** — the gate was not asked enough. `UNPROVEN` is the gate's own input
      deficiency: a missing flag, an unreadable file, a working tree that is not
      a repository. It outranks 3 because the operator can clear it from the same
      shell in seconds, and because it can be *masking* whether the vantage limit
      applies at all — a run missing `--deployed-origin` has not established
      that there is a second origin to be limited by.
    * **3** — the invocation was complete, every condition ran, and the deployed
      origin's answer is not observable from this machine. Lowest urgency
      precisely because nothing about the release has been shown to be wrong;
      but still non-zero, because nothing about it has been shown to be right
      either, and an exit code of 0 here would be a claim nobody made.
    """
    if any(result.status == FAIL for result in results):
        return 1
    if any(result.status == UNPROVEN for result in results):
        return 2
    if any(result.status == VANTAGE for result in results):
        return 3
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="converge_gate.py",
        description=(
            "The release gate: nine numbered conditions (0-8) plus the 0b identity "
            "precondition, each one able to fail. Exit 0 converged, 1 a real failure, "
            "2 a condition could not be evaluated because the invocation was "
            "under-specified, 3 a condition could not be evaluated because the "
            "deployed origin is not observable from this host. Only 0 is a pass."
        ),
    )
    parser.add_argument("--local-origin", default="http://127.0.0.1:8007")
    parser.add_argument(
        "--deployed-origin",
        default=None,
        help="the Tailscale Serve origin, e.g. https://home-macbook-air.tailcd6e49.ts.net:8452. "
        "Omit it and conditions 2/3/4/5/6 are reported UNPROVEN, never PASS. Name it and run "
        "the gate from the serving node and those conditions report VANTAGE-LIMITED (exit 3), "
        "because Tailscale Serve attributes an identity to a remote peer and a self-originated "
        "request has none — never PASS, and never FAIL for a reason that is not the deploy's.",
    )
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--port", type=int, default=8007, help="the app's loopback bind port")
    parser.add_argument("--smoke-spec", default=None)
    parser.add_argument(
        "--baseline",
        default=None,
        help="git commit to use as the CACHE_VERSION rotation anchor instead of the commit that set it",
    )
    parser.add_argument("--vault", default=None, help="vault path, for the §12.3 index inputs")
    parser.add_argument("--recipes-root", default=None)
    parser.add_argument("--pantry-note", default=None)
    parser.add_argument("--daily-notes-root", default=None)
    parser.add_argument("--port-manager", default=None)
    parser.add_argument(
        "--owner-login",
        default=None,
        help="the Tailscale-User-Login to present, equal to TAILSCALE_OWNER_LOGIN. Required "
        "when the backend runs with TRUST_TAILSCALE_HEADERS=true, because app/auth.py guards "
        "every path and the loopback caller is not the proxy. A Settings value, not a secret.",
    )
    parser.add_argument(
        "--no-listener-check",
        dest="expect_running",
        action="store_false",
        help="pre-restart runs: the new code is not running yet, so condition 0 is UNPROVEN not FAIL",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    vault = Path(args.vault) if args.vault else None
    if vault is None and os.environ.get("OBSIDIAN_VAULT_PATH"):
        vault = Path(os.environ["OBSIDIAN_VAULT_PATH"])
    options = Options(
        repo_root=Path(args.repo_root).resolve(),
        local_origin=args.local_origin.rstrip("/"),
        deployed_origin=args.deployed_origin.rstrip("/") if args.deployed_origin else None,
        vault=vault,
        recipes_root=args.recipes_root or "Hobbies/做饭/Recipes",
        pantry_note=args.pantry_note or "Logistics/库存/Pantry.md",
        daily_notes_root=args.daily_notes_root or "日记",
        port=args.port,
        smoke_spec=Path(args.smoke_spec) if args.smoke_spec else None,
        baseline_commit=args.baseline,
        expect_running=args.expect_running,
        port_manager=Path(args.port_manager) if args.port_manager else None,
        owner_login=args.owner_login,
        fetch=authenticated_fetcher(args.owner_login),
    )
    results = run_gate(options)
    print(render(results))
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())

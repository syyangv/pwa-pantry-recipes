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

**2 is not a soft 0.** The gate may not report "converged" while a condition it
was told to check was skipped because the argument was missing, because a file
was unreadable, or because the working tree is not a git repository. An
unevaluated condition is the exact state a stale backend is in, and collapsing
it into a pass is how a release ships against a backend that never reloaded.

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

`--no-listener-check` exists for a pre-`kickstart` run, where the honest answer
is "the new code is not running yet", not "the listener is missing".
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final

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

_ICON = {PASS: "PASS", FAIL: "FAIL", UNPROVEN: "UNPROVEN"}


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

    def add(self, ident: str, title: str, status: str, detail: str) -> None:
        self.results.append(CheckResult(ident, title, status, detail))


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
    if response.status == 401 and '"identity_missing"' in response.text():
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
    """2 — the local and deployed origins report the same version."""
    if not options.deployed_origin:
        options.add(
            "2 deployed-version",
            "local /api/version == deployed /api/version",
            UNPROVEN,
            "no --deployed-origin given, so the Tailscale Serve ingress is NOT verified",
        )
        return None
    local = options.fetch(f"{options.local_origin}/api/version")
    deployed = options.fetch(f"{options.deployed_origin}/api/version")
    for label, response in (("local", local), ("deployed", deployed)):
        if response.status != 200:
            options.add(
                "2 deployed-version",
                "local /api/version == deployed /api/version",
                FAIL,
                f"{label} {response.url} returned HTTP {response.status}",
            )
            return None
    local_version = (local.json() or {}).get("version")
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

    # Two lists, never one. A single list whose entries are distinguished by a
    # "(unproven" string prefix is a classification bug waiting to happen: a
    # reworded message silently turns an unknown into a failure, or a failure
    # into an unknown.
    problems: list[str] = []
    unproven: list[str] = []

    if options.deployed_origin:
        deployed = options.fetch(f"{options.deployed_origin}/api/version")
        deployed_header = deployed.header("X-PWA-Backend-Started-At")
        if deployed_header is None:
            problems.append("the deployed /api/version response carries no X-PWA-Backend-Started-At header")
        elif deployed_header != local_header:
            problems.append(
                f"local started at {local_header!r} but the deployed origin reports {deployed_header!r} "
                "— two different processes, so at least one was not restarted"
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
    for origin_label, origin in origins:
        for endpoint in endpoints:
            path = endpoint["path"]
            response = options.fetch(f"{origin}{path}")
            if response.status != 200:
                problems.append(f"{origin_label} {path} returned HTTP {response.status}")
                continue
            problems.extend(_missing_keys(origin_label, path, response.json(), endpoint))
    if problems:
        options.add("4 release-smoke", "release-specific live API smoke check", FAIL, "; ".join(problems))
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

    for label, origin in _origins(options):
        response = options.fetch(f"{origin}/sw.js")
        if response.status != 200:
            problems.append(f"{label} /sw.js returned HTTP {response.status}")
            continue
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
    for label, origin in _origins(options):
        response = options.fetch(f"{origin}/")
        if response.status != 200:
            problems.append(f"{label} / returned HTTP {response.status}")
            continue
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
    for label, origin in _origins(options):
        response = options.fetch(f"{origin}/js/pwa/update-manager.js?v={version}")
        if response.status != 200:
            problems.append(f"{label} update-manager.js returned HTTP {response.status}")
            continue
        body = response.text()
        for marker in RESUME_MARKERS:
            if marker not in body:
                problems.append(f"{label} update-manager.js never registers {marker!r}")
    if problems:
        options.add("7 resume-check", "resume triggers a version check", FAIL, "; ".join(problems))
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
    if config.get("WAIT_FOR_MESSAGE") != "true":
        problems.append(
            f"sw.js CONFIG has WAIT_FOR_MESSAGE={config.get('WAIT_FOR_MESSAGE')!r}, not true; with "
            "auto-takeover a reload can land between the user tapping 做过了 and the request completing"
        )
    for label, origin in _origins(options):
        response = options.fetch(f"{origin}/js/pwa/update-manager.js?v={version}")
        if response.status != 200:
            problems.append(f"{label} update-manager.js returned HTTP {response.status}")
            continue
        body = response.text()
        for marker in BUSY_GUARD_MARKERS:
            if marker not in body:
                problems.append(f"{label} update-manager.js does not expose {marker!r}")
    if problems:
        options.add("8 busy-guard", "an open confirmation flow postpones the reload", FAIL, "; ".join(problems))
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
        lines.append(f"[{_ICON[result.status]:7}] {result.ident:<17} {result.title}")
        for chunk in _wrap(result.detail, 68):
            lines.append(f"{'':9}{chunk}")
    failed = [result for result in results if result.status == FAIL]
    unproven = [result for result in results if result.status == UNPROVEN]
    lines.append("=" * 72)
    if failed:
        lines.append(f"RESULT: FAILED — {len(failed)} condition(s) failed. Do not reinstall the PWA.")
    elif unproven:
        lines.append(
            f"RESULT: INCOMPLETE — {len(unproven)} condition(s) could not be evaluated. "
            "This is NOT a pass: an unevaluated condition is exactly the state a stale backend is in."
        )
    else:
        lines.append(
            "RESULT: CONVERGED — every condition passed. Only now may the Home Screen PWA be reinstalled."
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
    if any(result.status == FAIL for result in results):
        return 1
    if any(result.status == UNPROVEN for result in results):
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="converge_gate.py",
        description=(
            "The release gate: nine numbered conditions (0-8) plus the 0b identity "
            "precondition, each one able to fail."
        ),
    )
    parser.add_argument("--local-origin", default="http://127.0.0.1:8007")
    parser.add_argument(
        "--deployed-origin",
        default=None,
        help="the Tailscale Serve origin, e.g. https://home-macbook-air.tailcd6e49.ts.net:8452. "
        "Omit it and conditions 2/3/4/5/6 are reported UNPROVEN, never PASS.",
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

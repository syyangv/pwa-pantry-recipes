"""Every converge-gate condition, proven able to fail.

**A gate that has never been observed failing is not a gate.** Each test below
builds one coherent world and then breaks exactly one thing in it, and asserts
which condition notices. The world is coherent by construction: a real
`app/static/sw.js`, a real `index.html` and the real release smoke spec are read
into `tmp_path`, and the responses are generated *from those files*, so a passing
test means the checks agree with the shipped bytes rather than with a
hand-written stand-in.

Two seams make this possible without a server:

* `Options.fetch` is the gate's only network call. A dict-backed closure answers
  URLs from a table, and "breaking a condition" is editing one table entry.
* `Options.git` is the gate's only git call, so the rotation check is testable
  without a repository — plus two tests that use a **real** scratch repository,
  because the anchor logic (`git log -1 -S` on the version literal) is exactly
  the kind of thing a fake would agree with and the real thing would not.

What is deliberately NOT faked: the status→exit-code mapping, the check
identifiers, and the fact that an unevaluable condition is never reported as a
pass. Those are the properties a release depends on, and
`test_every_condition_is_reachable_and_individually_breakable` is the test that
would notice if someone softened one.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts import converge_gate as gate

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_SW = REPO_ROOT / "app" / "static" / "sw.js"
REAL_INDEX = REPO_ROOT / "app" / "static" / "index.html"
REAL_SMOKE_SPEC = REPO_ROOT / "scripts" / "converge-smoke.json"
REAL_UPDATE_MANAGER = REPO_ROOT / "app" / "static" / "js" / "pwa" / "update-manager.js"

VERSION = "v9.9.9"
LOCAL = "http://127.0.0.1:8007"
DEPLOYED = "https://recipes.test.invalid:8452"
#: The Serve port `DEPLOYED` names, kept as its own name because condition 9
#: resolves a route by port and the fixture's Serve config has to agree with it.
DEPLOYED_PORT = 8452

#: Stands in for "a version this world is not running": a stale local backend, a
#: stale deployed origin, a stale shell. A sentinel rather than a real release
#: number, so no fixture here has to be re-pinned when a release ships and so
#: grepping for a shipped literal never lands on a test that reads as coupled to
#: it. It has to differ from `VERSION`, and nothing else.
STALE_VERSION = "v0.0.0-stale"

#: Two absolute epochs, so the tests do not decay. `OLD` predates every fixture
#: file this module writes, so the world is coherent; `YOUNG` postdates the boot
#: header, so touching a file to it is a change the backend never saw.
OLD = 1_000_000_000  # 2001-09-09
YOUNG = 4_000_000_000  # 2096-12-29

#: The `X-PWA-Backend-Started-At` a coherent world reports on both origins.
STARTED_AT = "2026-01-02T03:04:05.000000Z"

#: One representative changed file per startup-loaded class, so a test can move
#: exactly one of them into the future and prove condition 3 notices *which*.
STARTUP_LOADED_SAMPLES = (
    "app/static/sw.js",
    "app/config.py",
    "app/db/schema.sql",
)


def cache_version_of(sw_text: str) -> str:
    """The `CACHE_VERSION` the gate would read out of this `sw.js` text."""
    match = gate.CACHE_VERSION_RE.search(sw_text)
    assert match is not None, "no CACHE_VERSION constant in this sw.js text"
    return match.group(1)


def rewrite_cache_version(sw_text: str, version: str) -> str:
    """Put `version` in this `sw.js` text, using the gate's own regex.

    **The fixture tracks the shipped version; it does not assert one.** An
    earlier version of this module replaced a literal `'v0.6.0'`, so the a303d4a
    bump to `v0.7.0` turned the substitution into a silent no-op: the fixture's
    worker kept answering `v0.6.0` while every response in the world claimed
    `v9.9.9`, and condition 1 failed for 13 tests that had nothing wrong with
    them. A pin like that is a release step nothing documents and nobody
    performs — the runbook's release sequence (§6) never mentioned it.

    The failure that pin was guarding against is real, and it is what the
    `count == 1` assertion below is for: a rewrite that matches nothing leaves
    the fixture disagreeing with the file it claims to mirror, which must be a
    failure with a name rather than a literal to keep in step by hand. From here
    the mismatch is caught by the *comparison* —
    `test_a_version_mismatch_in_either_half_fails_condition_one` breaks each
    half on purpose and asserts condition 1 notices — so the loud failure the
    old pin bought is now bought by the thing under test.
    """
    rewritten, count = gate.CACHE_VERSION_RE.subn(
        lambda _match: f"const CACHE_VERSION = '{version}'", sw_text
    )
    assert count == 1, f"expected exactly one CACHE_VERSION constant in sw.js, found {count}"
    return rewritten


class World:
    """A coherent release, plus the table a test breaks one cell of."""

    #: Whether this world's deployed origin is *this* machine. `False` is the
    #: honest default rather than a convenience: `recipes.test.invalid` is
    #: nowhere near whatever host runs the suite, so a refusal from it is a
    #: property of that origin, not of the observer — and a test that wants the
    #: soft branch has to say so by flipping this.
    self_addressed: bool = False
    #: When true, **every** deployed-origin response is the identity-absence
    #: refusal — HTTP 401 `identity_missing`, the envelope `app/auth.py` returns
    #: in the trusted-header posture when no identity arrived. This reproduces
    #: the real Serve route's behaviour for a self-originated request rather
    #: than modelling it: the vantage probe and conditions 2-8 read the same
    #: bytes through the same seam, so a test cannot accidentally give the probe
    #: a friendlier world than the conditions get.
    identity_absent: bool = False

    def __init__(self, root: Path) -> None:
        self.root = root
        static = self.root / "app" / "static"
        (self.root / "app" / "db").mkdir(parents=True)
        static.mkdir(parents=True)
        # A scratch vault with the three server-owned roots §12.3 names, so
        # condition 3's index-input half is evaluated rather than reported
        # unproven in the baseline. The files are old, so the world is coherent.
        self.vault = root / "vault"
        for relative in ("Hobbies/做饭/Recipes", "日记", "Logistics/库存"):
            (self.vault / relative).mkdir(parents=True)
        for relative in (
            "Hobbies/做饭/Recipes/烤红薯.md",
            "日记/2026/2026-01-02.md",
            "Logistics/库存/Pantry.md",
        ):
            path = self.vault / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {relative}\n", encoding="utf-8")
            os.utime(path, (OLD, OLD))

        # Real bytes wherever the gate parses real bytes, and exactly one
        # substitution: the CACHE_VERSION literal is located with the gate's own
        # regex and rewritten to VERSION, so the fixture follows whatever version
        # the shipped file currently carries. The reasoning, and why a literal
        # pin was removed rather than re-pointed at v0.7.0, is on
        # `rewrite_cache_version`.
        self.sw_text = rewrite_cache_version(REAL_SW.read_text(encoding="utf-8"), VERSION)
        self.index_text = REAL_INDEX.read_text(encoding="utf-8")
        self.update_manager = REAL_UPDATE_MANAGER.read_text(encoding="utf-8")
        self.spec: dict[str, Any] = json.loads(REAL_SMOKE_SPEC.read_text(encoding="utf-8"))
        self.git_calls: list[tuple[str, ...]] = []
        self.git_overrides: dict[str, str] = {}
        self._write_sw(self.sw_text)
        for relative in ("app/config.py", "app/db/schema.sql"):
            path = self.root / relative
            path.write_text(f"# {relative}\n", encoding="utf-8")
            os.utime(path, (OLD, OLD))
        self.responses = self._table()
        #: The Serve route this world is served through, as condition 9 reads it
        #: from `tailscale serve status --json`. Modelled rather than left to the
        #: real binary: a test that only ever saw the coherent route could not
        #: tell a pass-through proof from one that rubber-stamps whatever it is
        #: handed. Shape and key naming are the real ones.
        self.serve_config: dict[str, Any] = {
            "TCP": {"8452": {"HTTPS": True}},
            "Web": {
                f"recipes.test.invalid:{DEPLOYED_PORT}": {
                    "Handlers": {"/": {"Proxy": "http://127.0.0.1:8007"}}
                }
            },
        }
        #: What is holding each port, keyed by port. The (pid, start time) pair
        #: is the identity condition 9 compares, so a test that wants "same
        #: port, different process" changes `pid` here and nothing else.
        self.listeners: dict[int, gate.Listener] = {
            8007: gate.Listener(
                port=8007,
                pid=47250,
                started="Mon Sep 28 12:26:32 2026",
                command="python -m uvicorn app.main:create_app --port 8007",
            )
        }
        #: Applied by `options()` under any per-call override. See there.
        self.option_overrides: dict[str, Any] = {}

    # -- one knob per condition -------------------------------------------

    def _write_sw(self, text: str) -> None:
        (self.root / "app" / "static" / "sw.js").write_text(text, encoding="utf-8")
        os.utime(self.root / "app" / "static" / "sw.js", (OLD, OLD))

    def _table(self) -> dict[str, gate.Response]:
        responses: dict[str, gate.Response] = {}
        for origin in (LOCAL, DEPLOYED):
            responses[f"{origin}/api/version"] = self._json({"version": VERSION})
            responses[f"{origin}/"] = self._html()
            responses[f"{origin}/sw.js"] = self._text(self.sw_text)
            responses[f"{origin}/js/pwa/update-manager.js?v={VERSION}"] = self._text(
                self.update_manager
            )
            for path in ("/health", "/api/recipes", "/api/session"):
                responses[f"{origin}{path}"] = self._spec_payload(path)
        return responses

    def _json(self, payload: Any, started_at: str | None = STARTED_AT) -> gate.Response:
        headers = {} if started_at is None else {"X-PWA-Backend-Started-At": started_at}
        return gate.Response(
            url="", status=200, headers=headers, body=json.dumps(payload).encode()
        )

    def _text(self, body: str) -> gate.Response:
        return gate.Response(url="", status=200, headers={}, body=body.encode())

    def _html(self) -> gate.Response:
        return self._text(self.index_text.replace("__APP_VERSION__", VERSION))

    def _spec_payload(self, path: str) -> gate.Response:
        endpoint = next(e for e in self.spec["endpoints"] if e["path"] == path)
        payload: dict[str, Any] = {key: "x" for key in endpoint.get("top_level_keys", [])}
        for array_key, item_keys in (endpoint.get("array_item_keys") or {}).items():
            payload[array_key] = [{key: "x" for key in item_keys}]
        for array_key, nested in (endpoint.get("array_item_nested_keys") or {}).items():
            for child_key, child_keys in nested.items():
                payload[array_key][0][child_key] = [{key: "x" for key in child_keys}]
        return self._json(payload)

    # -- the two seams ----------------------------------------------------

    def fetch(self, url: str) -> gate.Response:
        if self.identity_absent and url.startswith(DEPLOYED):
            return gate.Response(
                url=url,
                status=401,
                headers={},
                body=b'{"requestId":"probe","code":"identity_missing"}',
            )
        response = self.responses.get(url)
        if response is None:
            return gate.Response(url=url, status=0, headers={}, body=b"not in the world")
        return gate.Response(
            url=url, status=response.status, headers=response.headers, body=response.body
        )

    def probe(self, url: str, owner_login: str | None) -> gate.Response:
        """The identity probe, which presents the header under the gate's control.

        It answers from the same table as `fetch`, and deliberately ignores
        `owner_login`: a real Tailscale Serve route *strips* the header for a
        self-originated request, so the correct login and a forged one get the
        same bytes. A double that honoured the header would be modelling a
        working proxy, which is the case the vantage limit must never be
        confused with — `test_a_foreign_login_rejected_by_the_backend_is_not_a_vantage_limit`
        covers that half separately."""
        del owner_login
        return self.fetch(url)

    def self_addressed_for(self, origin: str) -> bool:
        del origin
        return self.self_addressed

    def serve_status(self) -> str:
        return json.dumps(self.serve_config)

    def listener(self, port: int) -> gate.Listener | None:
        return self.listeners.get(port)

    def git(self, *args: str, cwd: Path | None = None) -> str:
        self.git_calls.append(args)
        for prefix, value in self.git_overrides.items():
            if args[0] == prefix:
                return value
        if args[0] == "rev-parse":
            return "true\n"
        if args[0] == "log":
            return "a" * 40 + "\n"
        if args[0] == "diff":
            return ""
        raise AssertionError(f"unexpected git call: {args}")

    def options(self, **overrides: Any) -> gate.Options:
        defaults: dict[str, Any] = {
            "repo_root": self.root,
            "local_origin": LOCAL,
            "deployed_origin": DEPLOYED,
            "port": 8007,
            "expect_running": False,
            "vault": self.vault,
            # The spec is read from the real repository, not from the scratch
            # tree: it is a committed release artifact, and a world that carried
            # its own copy would let a typo in the real one pass.
            "smoke_spec": REAL_SMOKE_SPEC,
            "recipes_root": "Hobbies/做饭/Recipes",
            "pantry_note": "Logistics/库存/Pantry.md",
            "daily_notes_root": "日记",
            "fetch": self.fetch,
            "git": self.git,
            "probe": self.probe,
            "self_addressed": self.self_addressed_for,
            "serve_status": self.serve_status,
            "listener": self.listener,
        }
        # World-level defaults, applied *under* the call's overrides so a test
        # can still contradict the world. Exists so a fixture can change how
        # every `run()` in a test behaves — proving the listener, say — without
        # that fact having to be repeated at each call site, where one forgotten
        # copy would silently produce a different exit code.
        defaults.update(self.option_overrides)
        defaults.update(overrides)
        return gate.Options(**defaults)


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def run(world: World, **overrides: Any) -> tuple[list[gate.CheckResult], int]:
    results = gate.run_gate(world.options(**overrides))
    return results, gate.exit_code(results)


def by_id(results: list[gate.CheckResult], ident: str) -> gate.CheckResult:
    matches = [result for result in results if result.ident.startswith(ident)]
    assert len(matches) == 1, f"expected exactly one check starting {ident!r}, got {matches}"
    return matches[0]


# ---------------------------------------------------------------------------
# the coherent world first — every other test is a delta from this
# ---------------------------------------------------------------------------


def test_a_coherent_world_converges(world: World) -> None:
    """The baseline. If this fails, every other test in the file is meaningless:
    a gate that fails everything catches nothing.

    Condition 0 is left `UNPROVEN` because the fixture does not claim to be a
    running service — that is what `--no-listener-check` means, and the exit
    code has to say so rather than wave it through."""
    results, code = run(world)
    statuses = {result.ident: result.status for result in results}
    assert statuses["1 source-version"] == gate.PASS
    assert statuses["5 cache-rotation"] == gate.PASS
    assert code == 2
    assert "INCOMPLETE" in gate.render(results)
    assert "CONVERGED" not in gate.render(results)


@pytest.mark.parametrize(
    "shipped", ["v0.1.0", "v0.6.0", "v0.7.0", "v1.2.3", "v2.0.0-rc.1", "v10.20.30"]
)
def test_the_fixture_rewrites_whatever_version_sw_js_carries(shipped: str) -> None:
    """The version-agnostic property, asserted rather than demonstrated once.

    `a303d4a` bumped the shipped `CACHE_VERSION` to v0.7.0 and the fixture's
    pinned `'v0.6.0'` replacement silently matched nothing, so the coherent
    world above stopped being coherent and took 13 tests with it. Each of these
    is a version this fixture has to survive without anyone editing it: the
    world is built from the real file either way, and the rewrite has to land on
    `VERSION` and leave no trace of what was there before — the absence of that
    trace is precisely what the pinned `.replace()` failed to guarantee."""
    real = REAL_SW.read_text(encoding="utf-8")
    as_shipped = real.replace(cache_version_of(real), shipped)
    assert cache_version_of(as_shipped) == shipped, "the rewrite below is not what shipped"

    rewritten = rewrite_cache_version(as_shipped, VERSION)
    assert cache_version_of(rewritten) == VERSION
    assert shipped not in rewritten, (
        "the old literal survived the rewrite — as it did at a303d4a. If sw.js now "
        "names its version in a second place, that place has to say which one is "
        "authoritative."
    )


def test_a_sw_js_with_no_cache_version_fails_the_fixture_loudly() -> None:
    """The old pin's stated purpose — a fixture that has stopped matching the
    shipped file must fail loudly — is kept, as a count assertion instead of a
    literal to maintain."""
    with pytest.raises(AssertionError, match="exactly one CACHE_VERSION"):
        rewrite_cache_version("const CACHE_NAME = 'pwa-shell';\n", VERSION)


def test_the_baseline_has_exactly_the_eleven_numbered_checks(world: World) -> None:
    """§12 numbers eight steps, and the two preconditions on them — the socket,
    and whether the gate can talk to the origin at all — are numbered 0 and 0b.
    All ten of those were pinned here so adding, merging or dropping a check is a
    deliberate diff rather than an accident, and condition 9 is the eleventh,
    added for the pass-through proof in `check_ingress_identity`.

    The identifiers are read back off a **real** run rather than a literal, so
    this test counts what the gate evaluates instead of restating a list that has
    to be edited in two places. `0b` is the one condition whose ident is not
    `<n> <name>`, so the numbering is derived from the digit prefix and `0b` is
    matched separately."""
    results, _ = run(world)
    idents = [result.ident for result in results]
    numbered = [ident for ident in idents if re.match(r"^\d+ ", ident)]
    lettered = [ident for ident in idents if re.match(r"^\d+[a-z] ", ident)]
    assert lettered == ["0b identity"], f"expected exactly the 0b precondition, got {lettered}"
    assert len(numbered) == 10, f"expected ten numbered conditions, got {numbered}"
    assert len(idents) == 11, f"expected eleven checks in total, got {idents}"
    # The numbers are consecutive from 0, so a dropped check cannot hide behind a
    # rename: a merge that kept the count would still fail here.
    assert [int(ident.split(" ", 1)[0]) for ident in numbered] == list(range(10))


def test_the_help_text_counts_the_conditions_the_gate_evaluates(world: World) -> None:
    """The `--help` description must state the count the gate actually runs.

    It said "eight conditions" while the gate ran ten checks, and nothing
    caught it, because a wrong count in prose is only a wrong count in prose
    until someone reads the wrong number. The count is derived from a real run
    and compared with the number *spelled* in the help text, so the sentence
    cannot drift away from the code again without a red test.

    Ten is the numbered conditions (`0`-`9`) and eleven is those plus the `0b`
    identity precondition — which is why both are asserted: dropping either
    number from the description fails here.
    """
    help_text = gate.build_parser().format_help()
    results, _ = run(world)
    idents = [result.ident for result in results]
    numbered_count = sum(1 for ident in idents if re.match(r"^\d+ ", ident))
    total_count = len(idents)

    assert str(numbered_count) in help_text or _number_word(numbered_count) in help_text, (
        f"--help never states the numbered-condition count ({numbered_count}):\n{help_text}"
    )
    assert str(total_count) in help_text or _number_word(total_count) in help_text, (
        f"--help never states the total check count ({total_count}):\n{help_text}"
    )
    # The old, wrong count must be gone — spelled or numeric.
    assert "eight condition" not in help_text, (
        "--help still says 'eight conditions', which is not what the gate runs"
    )
    assert "nine numbered" not in help_text, (
        "--help still says 'nine numbered conditions', which is not what the gate runs"
    )


def _number_word(value: int) -> str:
    """`9` -> `'nine'`, so the assertion above accepts either spelling.

    The help text is prose, so a spelled-out number is the natural way to write
    it; the test must not force an Arabic numeral just to be able to read it
    back."""
    words = {
        1: "one",
        2: "two",
        3: "three",
        4: "four",
        5: "five",
        6: "six",
        7: "seven",
        8: "eight",
        9: "nine",
        10: "ten",
        11: "eleven",
        12: "twelve",
    }
    return words[value]


def test_an_unreachable_origin_is_reported_once_not_ten_times(world: World) -> None:
    """`app/auth.py` guards every path, so a wrong identity or a dead backend
    would otherwise turn every condition into the same 401. One diagnosis, and
    the rest are suppressed — a gate that says the same thing ten times is a
    gate nobody reads.

    Condition 9 is the one that still runs, and it is not an exception to the
    rule: it reads the Serve configuration and the process table, never the
    origin, so it is a different fact rather than the same 401 restated. It is
    asserted here so that stays true — a future change that made 9 probe the
    origin would put a tenth copy of the diagnosis back in the report."""
    world.responses[f"{LOCAL}/api/version"] = gate.Response(
        url="", status=401, headers={}, body=b'{"code":"identity_missing"}'
    )
    results, code = run(world)
    assert [result.ident for result in results] == [
        "0 listener",
        "0b identity",
        "9 ingress-identity",
    ]
    identity = by_id(results, "0b identity")
    assert identity.status == gate.FAIL
    assert "--owner-login" in identity.detail
    assert "TAILSCALE_OWNER_LOGIN" in identity.detail
    assert code == 1


def test_the_owner_login_is_reported_in_the_identity_detail(world: World) -> None:
    results, _ = run(world, owner_login="owner@test.invalid")
    identity = by_id(results, "0b identity")
    assert identity.status == gate.PASS
    assert "owner@test.invalid" in identity.detail
    results, _ = run(world)
    assert [result.ident for result in results] == [
        "0 listener",
        "0b identity",
        "1 source-version",
        "2 deployed-version",
        "3 backend-freshness",
        "4 release-smoke",
        "5 cache-rotation",
        "6 shell-assets",
        "7 resume-check",
        "8 busy-guard",
        "9 ingress-identity",
    ]


def test_a_condition_left_unproven_is_never_reported_as_a_pass(world: World) -> None:
    """The `expect_running=False` baseline leaves condition 0 unproven, and the
    exit code must say so. This is the property that makes exit 2 worth having:
    a gate that cannot distinguish "checked" from "not checked" cannot refuse to
    certify a release.

    A second world, identical except that a real listener was proven, is green —
    so the difference is condition 0 and nothing else."""
    results, code = run(world)
    assert by_id(results, "0 listener").status == gate.UNPROVEN
    assert code == 2, "an unevaluated condition must not exit 0"
    assert "INCOMPLETE" in gate.render(results)


# ---------------------------------------------------------------------------
# 0 — the listener
# ---------------------------------------------------------------------------


def test_a_dead_socket_fails_condition_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`launchctl print` would say `state = running`; the socket is gone."""
    world = World(tmp_path)
    port_manager = tmp_path / "port_manager.py"
    port_manager.write_text("", encoding="utf-8")

    class Completed:
        returncode = 0
        stdout = "Port 8007 has no visible TCP listener.\n"
        stderr = ""

    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: Completed())
    results, code = run(world, expect_running=True, port_manager=port_manager)
    assert by_id(results, "0 listener").status == gate.FAIL
    assert code == 1


def test_a_listening_socket_passes_condition_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(tmp_path)
    port_manager = tmp_path / "port_manager.py"
    port_manager.write_text("", encoding="utf-8")

    class Completed:
        returncode = 0
        stdout = "8007         62454    PWA  .venv/bin/python -m uvicorn app.main:create_app\n"
        stderr = ""

    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: Completed())
    results, code = run(world, expect_running=True, port_manager=port_manager)
    assert by_id(results, "0 listener").status == gate.PASS
    assert code == 0, gate.render(results)


# ---------------------------------------------------------------------------
# 1 — source CACHE_VERSION == local /api/version
# ---------------------------------------------------------------------------


def test_a_backend_that_has_not_reloaded_sw_js_fails_condition_one(world: World) -> None:
    """The bump is committed, the shell is right, and the process is still
    answering with last release's version — the restart was skipped."""
    world.responses[f"{LOCAL}/api/version"] = world._json({"version": STALE_VERSION})
    results, code = run(world)
    result = by_id(results, "1 source-version")
    assert result.status == gate.FAIL
    assert "has not reloaded" in result.detail
    assert code == 1


def test_a_missing_cache_version_is_unproven_not_a_crash(world: World) -> None:
    world._write_sw("const CACHE_NAME = 'pwa-shell';\n")
    results, code = run(world)
    assert by_id(results, "1 source-version").status == gate.UNPROVEN
    assert code == 2


@pytest.mark.parametrize("side", ["worker", "responses"])
def test_a_version_mismatch_in_either_half_fails_condition_one(world: World, side: str) -> None:
    """The reverse direction: the rewrite above is not a rubber stamp.

    A world whose fixture `sw.js` carries any other `CACHE_VERSION`, and a world
    whose `/api/version` reports any other version, must both fail condition 1
    and exit 1. This is the failure the removed `'v0.6.0'` pin was guarding
    against — a fixture that quietly disagrees with the file it mirrors — and it
    is now proven against the comparison itself, in both directions, rather than
    against a literal that has to be re-pinned on every release."""
    other = "v9.9.8-not-this-one"
    if side == "worker":
        world._write_sw(rewrite_cache_version(world.sw_text, other))
    else:
        world.responses[f"{LOCAL}/api/version"] = world._json({"version": other})
    results, code = run(world)
    result = by_id(results, "1 source-version")
    assert result.status == gate.FAIL, result.detail
    assert other in result.detail
    assert code == 1


def test_a_dead_backend_is_named_as_dead_with_its_status(world: World) -> None:
    world.responses[f"{LOCAL}/api/version"] = gate.Response(
        url="", status=0, headers={}, body=b"connection refused"
    )
    results, code = run(world)
    identity = by_id(results, "0b identity")
    assert identity.status == gate.FAIL
    assert "HTTP 0" in identity.detail
    assert "will be noise" in identity.detail
    assert code == 1


# ---------------------------------------------------------------------------
# 2 — local == deployed
# ---------------------------------------------------------------------------


def test_a_serve_route_pointing_at_another_backend_fails_condition_two(world: World) -> None:
    world.responses[f"{DEPLOYED}/api/version"] = world._json({"version": STALE_VERSION})
    results, code = run(world)
    result = by_id(results, "2 deployed-version")
    assert result.status == gate.FAIL
    assert STALE_VERSION in result.detail
    assert code == 1


def test_omitting_the_deployed_origin_is_unproven_never_pass(world: World) -> None:
    results, code = run(world, deployed_origin=None)
    result = by_id(results, "2 deployed-version")
    assert result.status == gate.UNPROVEN
    assert "NOT verified" in result.detail
    assert code == 2, "a pre-Serve run must not certify a release"


# ---------------------------------------------------------------------------
# 3 — X-PWA-Backend-Started-At
# ---------------------------------------------------------------------------


def test_a_startup_loaded_file_edited_after_the_boot_fails_condition_three(world: World) -> None:
    """The exact sentence from §3e: a displayed PWA version is never evidence
    that a running backend reloaded configuration. Here `/api/version` is
    perfectly current and condition 1 passes; the schema on disk is not what the
    process is holding."""
    os.utime(world.root / "app" / "db" / "schema.sql", (YOUNG, YOUNG))
    results, code = run(world)
    result = by_id(results, "3 backend-freshness")
    assert result.status == gate.FAIL
    assert "app/db/schema.sql" in result.detail
    assert "running pre-change bytes" in result.detail
    assert by_id(results, "1 source-version").status == gate.PASS
    assert code == 1


@pytest.mark.parametrize("relative", STARTUP_LOADED_SAMPLES)
def test_every_startup_loaded_class_is_actually_checked(
    world: World, relative: str
) -> None:
    """Not "one file happened to be caught" but *each* class: the version source
    itself, the configuration, and the schema. A glob that silently stopped
    matching `app/db/schema.sql` would leave the schema class unchecked and the
    single-sample test above still green."""
    os.utime(world.root / relative, (YOUNG, YOUNG))
    results, _ = run(world)
    result = by_id(results, "3 backend-freshness")
    assert result.status == gate.FAIL
    assert relative in result.detail


def test_a_missing_started_at_header_fails_condition_three(world: World) -> None:
    world.responses[f"{LOCAL}/api/version"] = world._json({"version": VERSION}, started_at=None)
    results, _ = run(world)
    assert by_id(results, "3 backend-freshness").status == gate.FAIL


def test_two_different_processes_behind_one_origin_fails_condition_three(world: World) -> None:
    world.responses[f"{DEPLOYED}/api/version"] = world._json(
        {"version": VERSION}, started_at="2026-01-02T03:04:06.000000Z"
    )
    results, _ = run(world)
    result = by_id(results, "3 backend-freshness")
    assert result.status == gate.FAIL
    assert "two different processes" in result.detail


def test_the_vault_index_inputs_are_checked_when_a_vault_is_named(
    world: World, tmp_path: Path
) -> None:
    """§12.3 names the recipe/pantry index inputs as well. A vault whose
    `Pantry.md` is newer than the boot is reported, and a run that named no
    vault says the half was NOT compared rather than implying it was clean."""
    vault = world.vault
    note = vault / "Hobbies" / "做饭" / "Recipes" / "烤红薯.md"
    assert note.is_file()

    options = world.options(vault=vault)
    options.results = []
    gate.check_backend_freshness(options)
    unproven = by_id(options.results, "3 backend-freshness")
    assert unproven.status == gate.PASS, unproven.detail

    os.utime(note, (YOUNG, YOUNG))
    options.results = []
    gate.check_backend_freshness(options)
    stale = by_id(options.results, "3 backend-freshness")
    assert stale.status == gate.FAIL
    assert "recipes_root:" in stale.detail


def test_no_vault_means_the_index_input_half_is_unproven(world: World) -> None:
    options = world.options(vault=None)
    options.results = []
    gate.check_backend_freshness(options)
    result = by_id(options.results, "3 backend-freshness")
    assert result.status == gate.UNPROVEN
    assert "NOT compared" in result.detail


# ---------------------------------------------------------------------------
# 4 — the release smoke check
# ---------------------------------------------------------------------------


def test_a_new_frontend_against_an_old_schema_fails_condition_four(world: World) -> None:
    """The failure condition 1, 2, 3 and 6 all miss: the versions agree, the
    backend restarted, the shell is consistent, and the response is missing a
    key this release added."""
    payload = world._spec_payload("/api/recipes")
    body = json.loads(payload.body)
    del body["staleMappingCount"]
    world.responses[f"{LOCAL}/api/recipes"] = world._json(body)
    results, code = run(world)
    result = by_id(results, "4 release-smoke")
    assert result.status == gate.FAIL
    assert "staleMappingCount" in result.detail
    for ident in ("1 source-version", "2 deployed-version", "6 shell-assets"):
        assert by_id(results, ident).status == gate.PASS
    assert code == 1


def test_a_missing_nested_key_fails_condition_four(world: World) -> None:
    payload = world._spec_payload("/api/recipes")
    body = json.loads(payload.body)
    del body["recipes"][0]["ingredients"][0]["stockJoinState"]
    world.responses[f"{LOCAL}/api/recipes"] = world._json(body)
    results, _ = run(world)
    result = by_id(results, "4 release-smoke")
    assert result.status == gate.FAIL
    assert "recipes[0].ingredients[0] has no key 'stockJoinState'" in result.detail


def test_a_missing_top_level_key_on_the_deployed_origin_only_fails(world: World) -> None:
    """A working backend behind a stale proxy is the case a local-only smoke
    check cannot see, and the reason the spec is fetched from both origins."""
    payload = world._spec_payload("/health")
    body = json.loads(payload.body)
    del body["mappings"]
    world.responses[f"{DEPLOYED}/health"] = world._json(body)
    results, _ = run(world)
    result = by_id(results, "4 release-smoke")
    assert result.status == gate.FAIL
    assert "deployed" in result.detail


def test_an_empty_smoke_spec_is_a_failure_not_a_pass(world: World, tmp_path: Path) -> None:
    spec = tmp_path / "empty.json"
    spec.write_text(json.dumps({"endpoints": []}), encoding="utf-8")
    results, code = run(world, smoke_spec=spec)
    result = by_id(results, "4 release-smoke")
    assert result.status == gate.FAIL
    assert "asserts nothing" in result.detail
    assert code == 1


def test_a_missing_smoke_spec_is_a_failure(world: World, tmp_path: Path) -> None:
    results, code = run(world, smoke_spec=tmp_path / "absent.json")
    assert by_id(results, "4 release-smoke").status == gate.FAIL
    assert code == 1


def test_an_unreachable_smoke_endpoint_fails_condition_four(world: World) -> None:
    world.responses[f"{LOCAL}/health"] = gate.Response(
        url="", status=503, headers={}, body=b"unavailable"
    )
    results, _ = run(world)
    result = by_id(results, "4 release-smoke")
    assert result.status == gate.FAIL
    assert "HTTP 503" in result.detail


# ---------------------------------------------------------------------------
# 5 — CACHE_VERSION rotation, and the served worker
# ---------------------------------------------------------------------------


def test_a_reused_cache_version_serves_the_previous_bundle_fails_condition_five(
    world: World,
) -> None:
    """The recorded incident. `/api/version` reports the new version because the
    source says so, and the *deployed* worker still names the previous cache
    name — so every installed PWA is handed the previous exact-versioned
    bundle while the shell claims the new release."""
    world.responses[f"{DEPLOYED}/sw.js"] = world._text(
        REAL_SW.read_text(encoding="utf-8")
    )
    results, code = run(world)
    result = by_id(results, "5 cache-rotation")
    assert result.status == gate.FAIL
    assert "previous exact-versioned bundle" in result.detail
    assert by_id(results, "1 source-version").status == gate.PASS
    assert by_id(results, "2 deployed-version").status == gate.PASS
    assert code == 1


def test_an_unrotated_cache_version_after_a_frontend_change_fails_condition_five(
    world: World,
) -> None:
    """§3b.1: a deploy can be internally consistent — clean tree, matching
    versions, correct shell assets — while reusing the previous cache key.
    Every other condition passes here."""
    world.git_overrides["diff"] = "app/static/js/views/home.js\napp/static/css/styles.css\n"
    results, code = run(world)
    result = by_id(results, "5 cache-rotation")
    assert result.status == gate.FAIL
    assert "was NOT rotated" in result.detail
    assert "app/static/js/views/home.js" in result.detail
    assert "app/static/css/styles.css" in result.detail
    for ident in ("1 source-version", "2 deployed-version", "6 shell-assets", "8 busy-guard"):
        assert by_id(results, ident).status == gate.PASS
    assert code == 1


def test_a_change_to_sw_js_alone_is_not_a_failure(world: World) -> None:
    """sw.js is the anchor's own file, so it is always "changed" by the
    definition. Excluding it is what stops the check from failing on every
    release that only bumps the version."""
    world.git_overrides["diff"] = "app/static/sw.js\n"
    results, code = run(world)
    assert by_id(results, "5 cache-rotation").status == gate.PASS
    assert code == 2  # only condition 0 is unproven


def test_a_non_git_tree_is_unproven_not_a_pass(world: World) -> None:
    def no_git(*args: str, cwd: Path | None = None) -> str:
        del args, cwd
        raise RuntimeError("not a git repository")

    results, code = run(world, git=no_git)
    result = by_id(results, "5 cache-rotation")
    assert result.status == gate.UNPROVEN
    assert "not a git working tree" in result.detail
    assert code == 2


def test_a_version_no_commit_ever_set_is_unproven(world: World) -> None:
    world.git_overrides["log"] = "\n"
    results, _ = run(world)
    result = by_id(results, "5 cache-rotation")
    assert result.status == gate.UNPROVEN
    assert "uncommitted" in result.detail


def test_the_rotation_anchor_comes_from_a_real_repository(tmp_path: Path) -> None:
    """The one thing a fake git could agree with and the real one would not: the
    anchor is the commit that last changed a `CACHE_VERSION` line, found with
    `git log -1 -G"const CACHE_VERSION"`. A release that bumps, then reverts,
    must anchor at the revert — otherwise the reverted release inherits a month
    of 'unchanged since' and the gate goes blind.

    The two versions below are arbitrary. The repository is built here, and the
    anchor matches the *line* rather than the value, so a real release number
    would only suggest a coupling to `app/static/sw.js` that does not exist."""
    first_version, second_version = "v1.0.0", "v2.0.0"
    repo = tmp_path / "real-repo"
    (repo / "app" / "static" / "js").mkdir(parents=True)
    (repo / "app" / "static" / "sw.js").write_text(
        f"const CACHE_VERSION = '{first_version}';\n", encoding="utf-8"
    )
    (repo / "app" / "static" / "js" / "main.js").write_text("// v1\n", encoding="utf-8")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t.invalid",
    }
    subprocess.run(("git", "init", "-q"), cwd=repo, check=True, env=env)
    subprocess.run(("git", "add", "-A"), cwd=repo, check=True, env=env)
    subprocess.run(
        ("git", "commit", "-qm", f"bump {first_version}"), cwd=repo, check=True, env=env
    )
    first = subprocess.run(
        ("git", "rev-parse", "HEAD"), cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    def read_git(*args: str, cwd: Path | None = None) -> str:
        completed = subprocess.run(
            ("git", *args), cwd=str(cwd or repo), capture_output=True, text=True, check=True
        )
        return completed.stdout

    def anchor_for(version: str) -> tuple[list[str], list[str]]:
        return gate._mutable_static_changes(
            gate.Options(repo_root=repo, local_origin=LOCAL, git=read_git), version
        )

    def write_version(value: str, message: str) -> None:
        (repo / "app" / "static" / "sw.js").write_text(
            f"const CACHE_VERSION = '{value}';\n", encoding="utf-8"
        )
        subprocess.run(("git", "add", "-A"), cwd=repo, check=True, env=env)
        subprocess.run(("git", "commit", "-qm", message), cwd=repo, check=True, env=env)

    # A release that bumped and changed nothing else is clean...
    write_version(second_version, f"bump {second_version}")
    assert anchor_for(second_version) == ([], [])

    # ...and a frontend change with no bump is caught against the old anchor.
    (repo / "app" / "static" / "js" / "main.js").write_text("// v2\n", encoding="utf-8")
    subprocess.run(("git", "add", "-A"), cwd=repo, check=True, env=env)
    subprocess.run(
        ("git", "commit", "-qm", "a frontend change with no bump"), cwd=repo, check=True, env=env
    )
    stale, _ = anchor_for(first_version)
    assert len(stale) == 1
    assert "app/static/js/main.js" in stale[0]

    # An sw.js edit that does NOT rotate the version must not move the anchor.
    # This is the case that rules out anchoring on "the last commit that touched
    # app/static/sw.js": someone fixing a SHELL_ASSETS hole would reset the
    # window and every un-bumped frontend change behind it would be forgotten.
    (repo / "app" / "static" / "sw.js").write_text(
        f"const CACHE_VERSION = '{second_version}';\nconst SHELL_ASSETS = ['/js/new.js'];\n",
        encoding="utf-8",
    )
    subprocess.run(("git", "add", "-A"), cwd=repo, check=True, env=env)
    subprocess.run(
        ("git", "commit", "-qm", "add a precache entry, no bump"), cwd=repo, check=True, env=env
    )
    still_caught, _ = anchor_for(first_version)
    assert len(still_caught) == 1, still_caught
    assert "app/static/js/main.js" in still_caught[0]
    # The same commit is still a pending un-bumped change when read at the
    # current version, which is the other half of "the anchor did not move":
    # neither version sees a clean tree, so the hole cannot be walked in through
    # an SHELL_ASSETS edit.
    also_caught, _ = anchor_for(second_version)
    assert len(also_caught) == 1, also_caught

    # The documented limitation, asserted rather than hidden: a *revert* of
    # CACHE_VERSION is itself a rotation, so it resets the anchor and the
    # un-bumped change made before it stops being visible. The forward path — the
    # one that ships — is covered by the two assertions above.
    write_version(first_version, f"revert to {first_version}")
    after_revert, _ = anchor_for(first_version)
    assert after_revert == [], (
        "a revert is a rotation; see the limitation note in _mutable_static_changes"
    )

    # ...and a frontend change made *after* the revert is caught against it.
    (repo / "app" / "static" / "js" / "main.js").write_text("// v3\n", encoding="utf-8")
    subprocess.run(("git", "add", "-A"), cwd=repo, check=True, env=env)
    subprocess.run(
        ("git", "commit", "-qm", "another un-bumped frontend change"),
        cwd=repo,
        check=True,
        env=env,
    )
    after_revert, _ = anchor_for(first_version)
    assert len(after_revert) == 1, after_revert
    assert "app/static/js/main.js" in after_revert[0]
    head = subprocess.run(
        ("git", "rev-parse", "HEAD"), cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    assert first != head

# ---------------------------------------------------------------------------
# 6 — the shell's versioned assets
# ---------------------------------------------------------------------------


def test_a_shell_pinned_to_the_previous_version_fails_condition_six(world: World) -> None:
    """obsidian-daily's recorded failure: the shell reports the new version and
    the browser HTTP cache (304) serves the previous exact-versioned bundle."""
    stale = world.index_text.replace("__APP_VERSION__", STALE_VERSION)
    world.responses[f"{DEPLOYED}/"] = world._text(stale)
    results, code = run(world)
    result = by_id(results, "6 shell-assets")
    assert result.status == gate.FAIL
    assert "browser HTTP cache" in result.detail
    assert by_id(results, "1 source-version").status == gate.PASS
    assert code == 1


def test_a_shell_referencing_an_unprecached_asset_fails_condition_six(world: World) -> None:
    """A module the shell loads but the worker does not precache works online
    and is simply absent from the installed app — the offline-shell hole."""
    world._write_sw(
        world.sw_text.replace("  '/js/main.js?v=' + CACHE_VERSION,\n", "")
    )
    results, _ = run(world)
    result = by_id(results, "6 shell-assets")
    assert result.status == gate.FAIL
    assert "not in sw.js SHELL_ASSETS" in result.detail


def test_a_shell_with_no_versioned_assets_fails_condition_six(world: World) -> None:
    world.responses[f"{LOCAL}/"] = world._text("<html><body>no pins</body></html>")
    results, _ = run(world)
    assert by_id(results, "6 shell-assets").status == gate.FAIL


# ---------------------------------------------------------------------------
# 7 / 8 — the update lifecycle, read from the SERVED module
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("marker", "replacement"),
    [
        ("addEventListener('pageshow'", "addEventListener('kindaPageshow'"),
        ("addEventListener('visibilitychange'", "addEventListener('visibilityish'"),
        ("addEventListener('focus'", "addEventListener('blurred'"),
        ("/api/version", "/api/build"),
    ],
)
def test_removing_a_resume_trigger_fails_condition_seven(
    world: World, marker: str, replacement: str
) -> None:
    assert marker in world.update_manager, "the fixture no longer matches the shipped module"
    world.update_manager = world.update_manager.replace(marker, replacement)
    world.responses = world._table()
    results, code = run(world)
    result = by_id(results, "7 resume-check")
    assert result.status == gate.FAIL
    assert code == 1


def test_removing_the_busy_guard_fails_condition_eight(world: World) -> None:
    world.update_manager = world.update_manager.replace("canApplyUpdate", "applyWhenever")
    world.responses = world._table()
    results, code = run(world)
    result = by_id(results, "8 busy-guard")
    assert result.status == gate.FAIL
    assert "canApplyUpdate" in result.detail
    assert code == 1


def test_auto_takeover_fails_condition_eight(world: World) -> None:
    """F18: a forced auto-apply reload can land between the user tapping
    做过了 and the request completing, losing the log."""
    world._write_sw(
        world.sw_text.replace("const WAIT_FOR_MESSAGE = true;", "const WAIT_FOR_MESSAGE = false;")
    )
    results, code = run(world)
    result = by_id(results, "8 busy-guard")
    assert result.status == gate.FAIL
    assert "WAIT_FOR_MESSAGE" in result.detail
    assert code == 1


# ---------------------------------------------------------------------------
# the gate's own contract
# ---------------------------------------------------------------------------


def test_exit_codes_are_three_distinct_states() -> None:
    assert gate.exit_code([gate.CheckResult("x", "t", gate.PASS, "")]) == 0
    assert gate.exit_code([gate.CheckResult("x", "t", gate.UNPROVEN, "")]) == 2
    assert (
        gate.exit_code(
            [gate.CheckResult("x", "t", gate.PASS, ""), gate.CheckResult("y", "t", gate.FAIL, "")]
        )
        == 1
    )


def test_a_failure_outranks_an_unproven_condition() -> None:
    """Exit 1 must win over exit 2: 'something is wrong' is a more actionable
    answer than 'something is unknown', and collapsing them would let a run with
    both report the softer one."""
    results = [
        gate.CheckResult("x", "t", gate.UNPROVEN, ""),
        gate.CheckResult("y", "t", gate.FAIL, ""),
    ]
    assert gate.exit_code(results) == 1
    assert "FAILED" in gate.render(results)


def test_the_rendered_report_never_calls_an_unproven_run_converged() -> None:
    rendered = gate.render([gate.CheckResult("0 listener", "t", gate.UNPROVEN, "not checked")])
    assert "CONVERGED" not in rendered
    assert "INCOMPLETE" in rendered


def test_the_report_repeats_the_version_is_not_backend_freshness_evidence() -> None:
    """§3e's standing caution, printed on every run so a reader who scrolls
    past nine green lines still meets it."""
    assert "never" in gate.render([gate.CheckResult("x", "t", gate.PASS, "")])
    assert "evidence" in gate.render([gate.CheckResult("x", "t", gate.PASS, "")])


def test_the_shipped_smoke_spec_is_not_empty_and_covers_the_domain_routes() -> None:
    """The committed spec is the release's own half of condition 4. A run that
    never got a spec edit silently degrades to asserting nothing, so the routes
    §12.4 names are pinned here."""
    spec = json.loads(REAL_SMOKE_SPEC.read_text(encoding="utf-8"))
    paths = {endpoint["path"] for endpoint in spec["endpoints"]}
    assert paths == {"/api/recipes", "/health", "/api/session"}
    for endpoint in spec["endpoints"]:
        assert endpoint.get("top_level_keys"), f"{endpoint['path']} asserts no keys"
        assert endpoint.get("$release"), f"{endpoint['path']} names no release"


def test_every_key_the_shipped_smoke_spec_requires_is_in_the_live_contract() -> None:
    """Against `app/`'s own response models, not the fake world: a key the spec
    demands that the code stopped publishing would make every release fail
    condition 4 forever, and the fix belongs in the spec."""
    from app.api import cooklog, recipes  # noqa: F401  (import proves the modules load)

    assert recipes is not None and cooklog is not None
    source = (
        (REPO_ROOT / "app" / "api" / "recipes.py").read_text(encoding="utf-8")
        + (REPO_ROOT / "app" / "main.py").read_text(encoding="utf-8")
    )
    spec = json.loads(REAL_SMOKE_SPEC.read_text(encoding="utf-8"))
    required: set[str] = set()
    for endpoint in spec["endpoints"]:
        required.update(endpoint.get("top_level_keys", []))
        for item_keys in (endpoint.get("array_item_keys") or {}).values():
            required.update(item_keys)
        for nested in (endpoint.get("array_item_nested_keys") or {}).values():
            for child_keys in nested.values():
                required.update(child_keys)
    missing = sorted(key for key in required if f'"{key}"' not in source)
    assert not missing, (
        f"converge-smoke.json requires keys app/ never publishes: {missing}. Either the release "
        "removed the field (drop it from the spec) or the spec is wrong."
    )

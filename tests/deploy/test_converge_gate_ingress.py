"""Condition 9 — the pass-through proof — and the four properties that make it
safe to add a condition to a gate that has just been hardened against itself.

**The condition exists because a vantage limit is a comfortable place to hide a
real defect.** Conditions 2-8 report `VANTAGE-LIMITED` from the serving node, and
`VANTAGE-LIMITED` reads as "nothing was learned". But the deployed origin is
`8452 -> http://127.0.0.1:8007`: a pass-through to the same process the local
origin talks to. If that were ever *not* true — a route pointed at another port,
or at the same port held by a stale sibling — the gate would be reporting
"unobservable" while every deployed-vs-local comparison was quietly about a
second copy of the app. Nothing else in the script can see that, and the vantage
limit would be concealing it.

**What it is not.** It proves *who* is behind the route. It never observes
*what* the route returned, and the module's first substantive test is the one
that says so: on a successful run the seven deployed conditions are still
`VANTAGE-LIMITED` and the exit code is still 3. Adding a condition that passes
must not move a single other condition's outcome, or it is not additive.

The caching assumption gets its own test because it is the one load-bearing
belief in the reasoning that this repository cannot verify on its own: Serve is
assumed not to cache. If that stopped being true, condition 9 would keep
reporting agreement while serving bytes from somewhere else entirely. The
assumption is therefore printed in the output of every run, and a test holds it
there.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts import converge_gate as gate
from tests.deploy.test_converge_gate import (
    DEPLOYED,
    DEPLOYED_PORT,
    LOCAL,
    World,
    by_id,
    run,
)
from tests.deploy.test_converge_gate_vantage import (
    DEPLOYED_CONDITIONS,
    OWNER,
    touch_young,
)

#: The identity the two listener reads must agree on. A pid alone is not an
#: identity — pids get reused — so the pair is what the condition compares, and
#: the start time is what makes it a claim about one process *instance*.
BACKEND_PID = 47250
BACKEND_STARTED = "Mon Sep 28 12:26:32 2026"

#: A different process on the same port: the stale-sibling case. Same port, same
#: command line, different pid — which is exactly why the start time is compared
#: and not the pid alone.
STALE_PID = 51388


@pytest.fixture
def world(tmp_path: Path) -> World:
    """The shipped release as a coherent world, rebuilt here for the reason the
    sibling module gives: a pytest fixture is not exported by being defined in
    another module."""
    return World(tmp_path)


@pytest.fixture
def vantage_world(world: World) -> World:
    """The serving node's view: no identity arrives, ever, and this host is the
    deployed origin's host. The conjunction `classify_vantage` requires."""
    world.identity_absent = True
    world.self_addressed = True
    return world


@pytest.fixture
def serving_node(vantage_world: World, monkeypatch: pytest.MonkeyPatch) -> World:
    """`vantage_world` with the listener **proven**.

    Without it the run would exit 2 rather than 3, because the fixture does not
    claim to be a running service and condition 0 is then `UNPROVEN` — and
    `2 > 3` is the precedence these tests are about. `subprocess.run` is stubbed
    for condition 0 only; condition 9's seams come from the world, so the
    pass-through proof is still read through its own fixtures.
    """
    vantage_world.option_overrides = {
        "expect_running": True,
        "port_manager": Path(__file__).resolve().parents[2] / "scripts" / "converge_gate.py",
    }

    class Completed:
        returncode = 0
        stdout = "8007 55053 PWA .venv/bin/python -m uvicorn app.main:create_app\n"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Completed())
    return vantage_world


def ingress(world: World, **overrides: Any) -> gate.CheckResult:
    return by_id(run(world, **overrides)[0], "9 ingress-identity")


def reads_as(*listeners: gate.Listener | None) -> Any:
    """A listener seam that answers successive reads from a fixed sequence.

    This is how "the same port, a different process" is expressed at all. On one
    host, one port is one socket held by one process, so a steady-state world
    cannot produce two different pids for it — the only way the condition's
    identity comparison can disagree is if the two reads disagree, which is what
    a restart between them looks like. A seam that returned one answer for both
    reads would make the comparison a tautology, so it is spelled out here
    rather than left to the fixture's map."""

    def seam(port: int) -> gate.Listener | None:
        index = min(len(seen), len(listeners) - 1)
        seen.append(port)
        return listeners[index]

    seen: list[int] = []
    return seam


# ---------------------------------------------------------------------------
# it passes on the topology it was written for
# ---------------------------------------------------------------------------


def test_the_pass_through_is_proven_on_the_live_topology(vantage_world: World) -> None:
    """The baseline: `8452 -> 127.0.0.1:8007`, 8007 held by one process, and the
    local origin is that same address. If this fails, every other test in this
    module is testing a fiction."""
    result = ingress(vantage_world)
    assert result.status == gate.PASS, result.detail
    assert "http://127.0.0.1:8007" in result.detail
    assert str(BACKEND_PID) in result.detail
    assert BACKEND_STARTED in result.detail


def test_proving_the_pass_through_agrees_about_who_and_says_nothing_about_what(
    serving_node: World,
) -> None:
    """**The additive property, asserted as one test.**

    Condition 9 passing must leave every `VANTAGE-LIMITED` condition
    `VANTAGE-LIMITED` and the exit code at 3. This is the test that fails if
    someone later decides a proven pass-through is close enough to an
    observation to retire the soft branch — which is the one change that would
    make this condition a liability rather than an addition.
    """
    results, code = run(serving_node, owner_login=OWNER)
    assert by_id(results, "9 ingress-identity").status == gate.PASS
    for ident in DEPLOYED_CONDITIONS:
        assert by_id(results, ident).status == gate.VANTAGE, by_id(results, ident).detail
    assert code == 3
    rendered = gate.render(results)
    assert "RESULT: VANTAGE-LIMITED" in rendered
    assert "CONVERGED" not in rendered


def test_the_caching_assumption_is_printed_on_a_passing_run(vantage_world: World) -> None:
    """The one belief in the reasoning this repository cannot check for itself.

    Serve is assumed to be a pass-through that does not cache. That is true, and
    it is an assumption about someone else's proxy rather than a fact about this
    deployment: a caching proxy would satisfy every other assertion in this
    module and serve bytes from somewhere else. So the assumption is stated in
    the output of every passing run, in words, and a test keeps it there."""
    result = ingress(vantage_world)
    assert "CACHING ASSUMPTION" in result.detail
    assert "does not cache" in result.detail
    assert "not an observation of the deployed origin" in result.detail
    # And the same sentence must not let a reader think a deployed response was
    # read: the detail has to say that explicitly, too.
    assert "no deployed response was read" in result.detail
    assert "VANTAGE-LIMITED" in result.detail


def test_the_passing_detail_never_calls_the_deployed_origin_observed(
    vantage_world: World,
) -> None:
    """`PASS` on a condition about the deployed origin is the most misreadable
    line the report can contain. The detail has to name what was proven (who is
    behind the route) separately from what was not (what it returned), so the
    next reader does not inherit the mistake."""
    detail = ingress(vantage_world).detail
    assert "proves is who is behind the route, not what the route returned" in detail.lower()
    assert "it is this process, reached through the proxy" in detail
    # The word "observed" must not appear attached to the deployed origin
    # anywhere in a passing detail.
    assert "deployed origin was observed" not in detail


# ---------------------------------------------------------------------------
# it fails on a real deployment defect, unsmoothed
# ---------------------------------------------------------------------------


def test_a_route_pointing_at_another_port_fails(vantage_world: World) -> None:
    """The defect the condition was added to catch: `8452` proxied to some other
    backend, so the deployed half of every comparison was about a second copy of
    the app while the gate reported the deployed origin as unobservable.

    This is a `FAIL`, not `UNPROVEN`. The gate can read the Serve configuration
    perfectly well here; it read it and the answer is wrong."""
    vantage_world.serve_config["Web"][f"recipes.test.invalid:{DEPLOYED_PORT}"] = {
        "Handlers": {"/": {"Proxy": "http://127.0.0.1:8006"}}
    }
    result = ingress(vantage_world)
    assert result.status == gate.FAIL
    assert "http://127.0.0.1:8006" in result.detail
    assert "not a pass-through" in result.detail
    # A different port is not a different *spelling* of the same one.
    assert "same backend" not in result.detail


def test_a_route_on_the_same_port_held_by_another_process_fails(vantage_world: World) -> None:
    """The same port, a different process: the stale sibling, and the case a port
    comparison alone cannot see — which is the entire reason the condition reads
    the process identity as well as the address. See `reads_as` for why this is
    a two-read scenario rather than a steady state: a backend that restarted
    between the route read and the process read lands here, and so would a route
    whose target were rebound by something else, and both are deployment
    defects."""
    stale = gate.Listener(
        port=8007,
        pid=STALE_PID,
        started=BACKEND_STARTED,
        command="python -m uvicorn app.main:create_app --port 8007",
    )
    current = vantage_world.listeners[8007]
    result = ingress(vantage_world, listener=reads_as(current, stale))
    assert result.status == gate.FAIL
    assert "disagree about what is holding that port" in result.detail
    assert str(BACKEND_PID) in result.detail
    assert str(STALE_PID) in result.detail
    assert "stale sibling" in result.detail


def test_a_restarted_process_at_the_same_pid_is_caught_by_the_start_time(
    vantage_world: World,
) -> None:
    """A pid is not an identity. Two processes can hold the same port across a
    restart and land on the same pid, and comparing pids alone would call that a
    pass — so the start time is compared as well, and this test is the reason."""
    restarted = gate.Listener(
        port=8007,
        pid=BACKEND_PID,
        started="Tue Sep 29 09:15:00 2026",
        command="python -m uvicorn app.main:create_app --port 8007",
    )
    current = vantage_world.listeners[8007]
    result = ingress(vantage_world, listener=reads_as(current, restarted))
    assert result.status == gate.FAIL
    assert "Tue Sep 29 09:15:00 2026" in result.detail


def test_the_two_listener_reads_are_independent(vantage_world: World) -> None:
    """Both ports are the same on the live topology, so a single lookup reused
    twice would make the identity comparison a tautology that always agrees. The
    seam is called once per authority, and a test that made it return one answer
    for both would be the shape of that bug."""
    seen: list[int] = []
    world = vantage_world

    def counting_listener(port: int) -> gate.Listener | None:
        seen.append(port)
        return world.listeners.get(port)

    run(world, listener=counting_listener)
    assert seen == [8007, 8007], f"expected one read per authority, got {seen}"


# ---------------------------------------------------------------------------
# the unevaluable branches, which must not be failures
# ---------------------------------------------------------------------------


def test_no_deployed_origin_is_unproven_never_a_defect(world: World) -> None:
    """Nothing to resolve, so nothing is claimed. Not `FAIL`: a pre-Serve run has
    not misconfigured anything."""
    result = ingress(world, deployed_origin=None)
    assert result.status == gate.UNPROVEN
    assert "no --deployed-origin" in result.detail


def test_a_port_with_no_serve_route_is_unproven_not_a_defect(vantage_world: World) -> None:
    """`--deployed-origin` naming a port Serve does not serve is a premise the
    condition cannot speak to, not a broken deployment. Reported as neither."""
    vantage_world.serve_config["Web"] = {}
    result = ingress(vantage_world)
    assert result.status == gate.UNPROVEN
    assert f"no Serve route is configured on port {DEPLOYED_PORT}" in result.detail
    assert "neither reported as agreeing nor as a defect" in result.detail


def test_a_tcp_forward_is_unproven_not_a_defect(vantage_world: World) -> None:
    """A `TCP` forward has no `Proxy` URL to resolve. Guessing at one would be
    exactly the kind of inference this condition exists to avoid, so the branch
    is named instead."""
    vantage_world.serve_config["Web"][f"recipes.test.invalid:{DEPLOYED_PORT}"] = {
        "Handlers": {"/": {"TCPForward": {"target": "127.0.0.1:8007", "terminateTLS": False}}}
    }
    result = ingress(vantage_world)
    assert result.status == gate.UNPROVEN
    assert "not a Proxy" in result.detail


def test_an_unreadable_serve_config_is_unproven_not_a_defect(vantage_world: World) -> None:
    """`tailscale` missing, or refusing, is an unavailable tool — not evidence."""
    def unavailable() -> str:
        raise OSError("`tailscale serve status --json` exited 1: not a tailscale node")

    result = ingress(vantage_world, serve_status=unavailable)
    assert result.status == gate.UNPROVEN
    assert "not a deployment defect" in result.detail


def test_an_unidentifiable_listener_is_unproven_not_a_defect(vantage_world: World) -> None:
    """Same port, but no process could be named for it. "Same port" is not
    "same process", and the condition says so rather than inferring the second
    from the first."""
    result = ingress(vantage_world, listener=lambda _port: None)
    assert result.status == gate.UNPROVEN
    assert "could not be identified" in result.detail
    assert "rather than merely the same port" in result.detail


# ---------------------------------------------------------------------------
# it stands down when the deployed origin is directly observed
# ---------------------------------------------------------------------------


def test_an_observable_deployed_origin_makes_the_proof_unnecessary(world: World) -> None:
    """Run from a host that is *not* the serving node, the deployed origin
    answers, and conditions 2-8 compare against it directly. That is a stronger
    statement than a route table, so the proof is not needed — and it is reported
    as agreeing-with-the-substitution-stated rather than dropped, because a
    condition that silently vanishes from the report is a condition nobody knows
    was considered.

    This branch is also what keeps the documented remedy reachable: a phone on the
    tailnet has no `tailscale serve status`, and had this reported `UNPROVEN`
    there, the one host that can actually reach exit 0 would exit 2 instead."""
    results, code = run(world)
    result = by_id(results, "9 ingress-identity")
    assert result.status == gate.PASS
    assert "not needed and therefore not asserted" in result.detail
    assert "stronger statement than a route table" in result.detail
    assert code == 2, "only condition 0 is unproven in the coherent world"


# ---------------------------------------------------------------------------
# the seams, against the real machine
# ---------------------------------------------------------------------------


def test_the_two_real_seams_read_this_machine() -> None:
    """The one place the doubles stop.

    Every other test in this module drives `serve_status` and `listener` from the
    fixture, so a seam that could only ever work against a hand-written value —
    a mis-parsed `lsof` field, a `Web` key that does not match what the real
    binary prints — would be green across the whole file. This asks the real
    `tailscale` and the real `lsof`, and skips when the live topology is not
    present rather than failing, because a test that fails on someone else's
    checkout is a test that gets deleted."""
    try:
        status = json.loads(gate.tailscale_serve_status())
    except (OSError, ValueError) as error:
        pytest.skip(f"no readable Tailscale Serve configuration here: {error}")
    proxy, found = gate._serve_route_backend(status, DEPLOYED_PORT)
    if proxy is None:
        pytest.skip(f"port {DEPLOYED_PORT} is not a Serve route on this machine ({found})")
    host, port = gate._split_authority(proxy)
    assert port == 8007, f"the live route now points at {proxy}, not this app's port"
    listener = gate.probe_listener(port or 0)
    assert listener is not None, f"no listening process could be identified on port {port}"
    assert listener.pid is not None
    assert listener.started, "ps produced no start time, so the identity is only a pid"
    # And the two spellings of the same socket must compare equal, or the live
    # run would fail the very condition these doubles were built to satisfy.
    assert gate._same_backend(proxy, LOCAL)
    assert not gate._same_backend(proxy, "http://127.0.0.1:8006")


def test_probe_listener_names_nothing_on_a_port_nobody_holds() -> None:
    """The negative half of the seam, and the direction that matters: a port with
    no listener must read as "no listener", never as an unidentified process."""
    assert gate.probe_listener(1) is None
    assert gate.probe_listener(8451) is None


@pytest.mark.parametrize(
    ("left", "right", "same"),
    [
        # The live topology, in the two spellings Serve and an operator produce.
        ("http://127.0.0.1:8007", "http://127.0.0.1:8007", True),
        ("http://127.0.0.1:8007", "http://localhost:8007", True),
        ("http://127.0.0.1:8007", "http://[::1]:8007", True),
        # A different port is a different deployment, whatever the host.
        ("http://127.0.0.1:8007", "http://127.0.0.1:8006", False),
        ("http://localhost:8007", "http://127.0.0.1:8008", False),
        # The same port on a *different host* is a different deployment, and this
        # is the case a port-only comparison would wave through.
        ("http://127.0.0.1:8007", "http://10.0.0.9:8007", False),
        ("http://127.0.0.1:8007", "http://recipes.test.invalid:8007", False),
        # A wildcard bind is not a destination, so it is not loopback.
        ("http://127.0.0.1:8007", "http://0.0.0.0:8007", False),
        # Serve's `https+insecure` scheme, which is what 8446 actually proxies.
        ("https+insecure://127.0.0.1:8000", "https+insecure://127.0.0.1:8000", True),
        # A port-less authority defaults on scheme, so https and http differ.
        ("https://127.0.0.1", "https://127.0.0.1", True),
        ("http://127.0.0.1", "https://127.0.0.1", False),
    ],
)
def test_authority_equality_is_strict_about_host_and_port(
    left: str, right: str, same: bool
) -> None:
    """The comparison the whole pass-through proof rests on, as a table.

    Two rows carry the weight. "Same port, different host" is the false
    agreement a port-only check would produce, and "loopback versus wildcard" is
    the row that stops `0.0.0.0` being waved through as loopback — it is a bind
    address, and a proxy target naming it is not this machine's socket."""
    assert gate._same_backend(left, right) is same, f"{left} vs {right}"


def test_serve_route_resolution_names_what_it_found_instead() -> None:
    """Every shape that is not a pass-through `/` Proxy has to be *named*, so an
    `UNPROVEN` branch tells the reader what it actually read. A generic
    "no route" would make all four of these indistinguishable in the report."""
    status = {
        "Web": {
            "host:8452": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8007"}}},
            "host:9000": {"Handlers": {"/": {"TCPForward": {"target": "127.0.0.1:1"}}}},
            "host:9001": {"Handlers": {"/redirect": {"Redirect": "https://elsewhere"}}},
            "host:9002": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8007"}}},
            # A second key claiming the *same* port under a different host name:
            # the collision branch, which must refuse to pick one.
            "other:9002": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8006"}}},
        }
    }
    proxy, found = gate._serve_route_backend(status, 8452)
    assert proxy == "http://127.0.0.1:8007"
    assert found == "host:8452"

    assert gate._serve_route_backend(status, 9000)[0] is None
    assert "not a Proxy" in gate._serve_route_backend(status, 9000)[1]
    assert "no `/` handler" in gate._serve_route_backend(status, 9001)[1]
    assert "claimed by 2 Serve endpoints" in gate._serve_route_backend(status, 9002)[1]
    assert "no Serve route is configured on port 9100" == gate._serve_route_backend(
        status, 9100
    )[1]
    assert "no Web routes at all" in gate._serve_route_backend({"TCP": {}}, 8452)[1]


# ---------------------------------------------------------------------------
# the real failures, re-asserted next to the condition that must not soften them
# ---------------------------------------------------------------------------


def test_a_real_failure_outranks_a_proven_pass_through(vantage_world: World) -> None:
    """A startup-loaded file edited after the boot, in a world whose pass-through
    is proven. `1 > 2 > 3 > 0` puts the real failure first, and a proven
    pass-through is not a reason to demote it — the route being correct says
    nothing about the bytes on disk behind it."""
    touch_young(vantage_world.root / "app" / "db" / "schema.sql")
    results, code = run(vantage_world, owner_login=OWNER)
    assert by_id(results, "9 ingress-identity").status == gate.PASS
    assert by_id(results, "3 backend-freshness").status == gate.FAIL
    assert code == 1


def test_a_deployed_version_mismatch_still_fails_with_condition_nine_present(
    world: World,
) -> None:
    """The four real failures in this repository's own terms: the deployed
    origin's `/api/version` disagrees with the local one. Here the deployed
    origin *answers*, so condition 9 stands down as unnecessary — and the
    failure still fails."""
    world.responses[f"{DEPLOYED}/api/version"] = world._json({"version": "v0.0.0-stale"})
    results, code = run(world)
    assert by_id(results, "2 deployed-version").status == gate.FAIL
    assert by_id(results, "9 ingress-identity").status == gate.PASS
    assert code == 1


def test_the_exit_code_contract_is_unchanged_by_the_new_condition() -> None:
    """Four outcomes, and the precedence between them, are the gate's public
    interface. A new condition is allowed to add statuses to a run; it is not
    allowed to reorder what those statuses mean."""
    def result(status: str) -> gate.CheckResult:
        return gate.CheckResult(ident="9 ingress-identity", title="", status=status, detail="")

    assert gate.exit_code([result(gate.PASS), result(gate.PASS)]) == 0
    assert gate.exit_code([result(gate.VANTAGE), result(gate.PASS)]) == 3
    assert gate.exit_code([result(gate.UNPROVEN), result(gate.VANTAGE)]) == 2
    assert gate.exit_code([result(gate.FAIL), result(gate.UNPROVEN), result(gate.VANTAGE)]) == 1
    # A passing condition must not, on its own, manufacture a zero out of a run
    # that has not earned one.
    assert gate.exit_code([result(gate.PASS), result(gate.VANTAGE)]) == 3


def test_the_number_nine_does_not_collide_with_an_existing_condition(
    vantage_world: World,
) -> None:
    """0-8 plus 0b was the whole numbering before this. A collision would make
    `by_id` ambiguous and a reader unable to tell which condition a line is."""
    results, _ = run(vantage_world, owner_login=OWNER)
    idents = [result.ident for result in results]
    assert len(idents) == len(set(idents))
    assert idents.count("9 ingress-identity") == 1
    for existing in ("0 listener", "0b identity", "8 busy-guard"):
        assert existing in idents


def test_the_fixture_never_reaches_for_the_real_machine(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With both of condition 9's seams injected, the gate must make no
    `subprocess` call at all.

    That is the property that makes every other test in this file hermetic: a
    fixture that quietly shelled out to the real `tailscale` or the real `lsof`
    would be testing this machine's routing table and process table while
    appearing to test a world. It also pins the direction the runbook's own
    constraint cares about — the only `tailscale` command this condition can
    issue is the read-only `serve status --json`, and a future edit that reached
    for a mutating subcommand would have to be written past this."""
    calls: list[Any] = []

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        raise AssertionError(f"the fixture shelled out: {args}")

    monkeypatch.setattr(subprocess, "run", forbidden)
    results = gate.run_gate(
        world.options(owner_login=OWNER, expect_running=False, deployed_origin=None)
    )
    assert calls == [], f"expected no subprocess calls, got {calls}"
    # Both seams are the world's, so condition 9 resolved entirely from them.
    assert by_id(results, "9 ingress-identity").status == gate.UNPROVEN

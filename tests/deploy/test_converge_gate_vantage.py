"""The `VANTAGE-LIMITED` outcome, and — the reason this file exists — the proof
that adding it did not soften a single real failure.

**A fourth outcome is only worth having if it is harder to reach than `FAIL`.**
A gate that reports "I could not evaluate this" for something it could have
evaluated is worse than the honest `FAIL` it replaced: it teaches an operator to
look for a way to make the number go away, and the ways available are
`--baseline`, a relaxed comparison, and a skipped condition. So this module is
written in two halves that pull against each other on purpose:

* the first half proves the soft outcome is reachable **only** through two
  independent positive observations — a uniform identity-absence refusal, and
  proof from the routing table that the deployed origin is this machine;
* the second half breaks one real thing at a time, *in a world that is already
  vantage-limited*, and asserts `FAIL` and exit 1 every time. Running the real
  failures against a vantage-limited world rather than a healthy one is the
  whole point: it is the only arrangement in which a regression that widened the
  soft branch would be caught.

The world itself is the shipped one, from `test_converge_gate.World`: a real
`app/static/sw.js`, a real `index.html` and the real release smoke spec, so a
passing assertion means the checks agree with the shipped bytes. Nothing about
the classification is faked except the two seams it needs, and both are declared
on the world rather than stubbed per test.

`test_the_address_test_reads_this_machines_own_routing_table` is the one place
the fake stops: it asks the real kernel whether a loopback address and a public
one are this machine's, because a detection that only ever ran against a
hand-written double would be a detection nobody had ever run.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts import converge_gate as gate
from tests.deploy.test_converge_gate import (
    DEPLOYED,
    LOCAL,
    STALE_VERSION,
    VERSION,
    World,
    by_id,
    rewrite_cache_version,
    run,
)

OWNER = "syyangv@github"

#: The conditions whose subject includes the deployed origin. 2 is the only one
#: that is *entirely* about it; 3-8 each compare a local half against a deployed
#: half. Both shapes are in the list because both have to reach the soft branch.
DEPLOYED_CONDITIONS = (
    "2 deployed-version",
    "3 backend-freshness",
    "4 release-smoke",
    "5 cache-rotation",
    "6 shell-assets",
    "7 resume-check",
    "8 busy-guard",
)

REFUSAL = b'{"requestId":"probe","code":"identity_missing"}'

#: An epoch far enough in the future that touching a file to it is unambiguously
#: "this file changed after the backend booted". The same sentinel the sibling
#: module uses, repeated here so neither file has to import the other's private
#: vocabulary.
YOUNG = 4_000_000_000


@pytest.fixture
def world(tmp_path: Path) -> World:
    """The shipped release as a coherent world.

    Rebuilt here rather than imported: a pytest fixture is not exported by being
    defined in another module, and a test file that quietly depended on a
    sibling's fixture would break the moment that sibling was reorganised.
    """
    return World(tmp_path)


@pytest.fixture
def vantage_world(world: World) -> World:
    """The world as the serving node actually sees it: no identity, ever.

    Both switches are set together, because that is the conjunction the
    classifier requires and because a test that set only one would be testing a
    different machine than the one the bug report was filed against.
    """
    world.identity_absent = True
    world.self_addressed = True
    return world


@pytest.fixture
def serving_node(vantage_world: World, monkeypatch: pytest.MonkeyPatch) -> World:
    """The vantage world with the listener **proven**.

    Without this the run would exit 2 rather than 3, because the fixture does
    not claim to be a running service and condition 0 is then `UNPROVEN` — and
    `2 > 3` is the precedence under test elsewhere. Stubbing `subprocess.run`
    is how the sibling module proves condition 0, and it is used here for the
    same reason: the point of these tests is the deployed origin, not the
    socket.
    """
    vantage_world.option_overrides = {
        "expect_running": True,
        "port_manager": Path(__file__).resolve().parents[2] / "scripts" / "converge_gate.py",
    }

    class Completed:
        returncode = 0
        stdout = "8007 55053 PWA .venv/bin/python -m uvicorn app.main:create_app\n"
        stderr = ""

    # Patched on the module rather than as `gate.subprocess.run`, which is the
    # same object but reads as a private re-export of the gate's.
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Completed())
    return vantage_world


def touch_young(path: Path) -> None:
    """Make one startup-loaded file newer than the backend's boot."""
    os.utime(path, (YOUNG, YOUNG))


def refusing(_url: str, login: str | None) -> gate.Response:
    """What the proxy does with a header it stripped: the same bytes regardless."""
    del login
    return gate.Response(url="", status=401, headers={}, body=REFUSAL)


# ---------------------------------------------------------------------------
# the classifier, as a pure function of its four arguments
# ---------------------------------------------------------------------------


def test_a_uniform_identity_absence_from_this_machine_is_the_vantage_limit() -> None:
    verdict = gate.classify_vantage(DEPLOYED, refusing, lambda _o: True, OWNER)
    assert verdict.unobservable
    assert verdict.state == "identity-absent"
    assert verdict.self_addressed
    # The evidence has to name all three probes. A classification whose own
    # justification is not in the output is a classification the reader has to
    # take on trust, which is the thing this whole change exists to avoid.
    for fragment in ("no identity header", "a foreign identity", "the configured owner identity"):
        assert fragment in verdict.evidence, verdict.evidence
    assert DEPLOYED in verdict.evidence


def test_the_same_refusal_from_another_node_is_a_real_defect_not_a_vantage_limit() -> None:
    """**The anti-misclassification test that matters most.**

    A uniformly-refusing deployed origin on a *different* machine would refuse
    every real user too — a Serve route pointed at a backend whose
    trusted-header posture is broken looks exactly like this from here. Calling
    that a vantage limit would be the gate quietly deciding a broken deployment
    is fine, which is the failure mode this change is most able to cause and the
    one it must be least able to.
    """
    verdict = gate.classify_vantage(DEPLOYED, refusing, lambda _o: False, OWNER)
    assert not verdict.unobservable
    assert verdict.state == "identity-absent", (
        "the refusal itself is still identity-absence; it is the locality proof "
        "that is missing, and the locality proof is what withholds the soft branch"
    )


def test_a_foreign_login_the_backend_rejected_is_not_a_vantage_limit() -> None:
    """`identity_denied` means the header *arrived* and was refused.

    That is an identity problem with a real cause and a real fix, and it is
    exactly the answer a live proxy produces when it injects an identity the
    backend does not expect. Reading it as "no identity arrived" would invert
    the meaning of the two codes and hide the one case an operator can fix
    without leaving the room.
    """

    def probe(_url: str, login: str | None) -> gate.Response:
        if login is None:
            return gate.Response(url="", status=401, headers={}, body=REFUSAL)
        return gate.Response(
            url="", status=401, headers={}, body=b'{"requestId":"p","code":"identity_denied"}'
        )

    verdict = gate.classify_vantage(DEPLOYED, probe, lambda _o: True, OWNER)
    assert not verdict.unobservable
    assert verdict.state == "undecided"
    assert "differing answers" in verdict.evidence


def test_a_deployed_origin_that_answers_200_is_observable() -> None:
    """The common case, and the one that must never be reclassified."""
    ok = gate.Response(url="", status=200, headers={}, body=b'{"version":"v1"}')
    verdict = gate.classify_vantage(DEPLOYED, lambda _u, _l: ok, lambda _o: True, OWNER)
    assert not verdict.unobservable
    assert verdict.state == "identity-present"
    assert "answered 200" in verdict.evidence


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (500, b"upstream exploded"),
        (404, b"not found"),
        (403, b'{"requestId":"p","code":"origin_not_allowed"}'),
        (401, b'{"requestId":"p","code":"identity_invalid"}'),
        (401, b"not json at all"),
    ],
)
def test_anything_other_than_the_exact_refusal_is_a_real_failure(status: int, body: bytes) -> None:
    """The narrowness of the branch, asserted against its neighbours.

    `VANTAGE-LIMITED` is only ever allowed to swallow `401 identity_missing`.
    A 500 from a wedged upstream, a 404 from a route pointing at the wrong
    backend, a 403 from a posture problem, `identity_invalid` from a duplicated
    header, and an unparseable body are all real, all reportable, and all have
    to keep failing.
    """
    response = gate.Response(url="", status=status, headers={}, body=body)
    verdict = gate.classify_vantage(DEPLOYED, lambda _u, _l: response, lambda _o: True, OWNER)
    assert not verdict.unobservable, f"{status} was reclassified as a vantage limit"
    assert verdict.state == "undecided"


def test_no_deployed_origin_is_not_a_vantage_limit() -> None:
    verdict = gate.classify_vantage(None, refusing, lambda _o: True, OWNER)
    assert not verdict.unobservable
    assert "no --deployed-origin" in verdict.evidence


def test_the_probe_is_skipped_when_no_owner_login_is_known() -> None:
    """With no `--owner-login` the gate cannot present the correct identity, and
    must not pretend to have. The two probes it can run still carry the
    classification, because a foreign login answered `identity_missing` is
    already proof the header did not arrive."""
    seen: list[str | None] = []

    def probe(_url: str, login: str | None) -> gate.Response:
        seen.append(login)
        return refusing("", login)

    verdict = gate.classify_vantage(DEPLOYED, probe, lambda _o: True, None)
    assert verdict.unobservable
    assert seen == [None, gate.PROBE_FOREIGN_LOGIN]
    assert "the configured owner identity" not in verdict.evidence


def test_error_code_reads_the_envelope_and_tolerates_junk() -> None:
    assert gate.error_code(gate.Response(url="", status=401, headers={}, body=REFUSAL)) == (
        gate.IDENTITY_MISSING
    )
    assert gate.error_code(gate.Response(url="", status=200, headers={}, body=b"<html>")) is None
    assert gate.error_code(gate.Response(url="", status=200, headers={}, body=b"[1,2]")) is None
    assert (
        gate.error_code(gate.Response(url="", status=200, headers={}, body=b'{"code": 7}')) is None
    )


# ---------------------------------------------------------------------------
# the address test, against this machine's real routing table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "origin",
    [
        "http://127.0.0.1:8007",
        "http://localhost:8007",
        "https://home-macbook-air.tailcd6e49.ts.net:8452",
    ],
)
def test_the_address_test_reads_this_machines_own_routing_table(origin: str) -> None:
    """Run for real, because a detector that has only ever met a hand-written
    double has not been run at all.

    The third case is the load-bearing one and it is machine-specific: the
    deployed origin's MagicDNS name resolves to this node's own tailnet address,
    which is the entire fact the vantage limit turns on. On a host that is *not*
    the serving node it resolves elsewhere and returns `False` — which is the
    correct answer, and the reason the check is a measurement and not a name
    match.
    """
    assert gate.is_self_addressed(origin) is True, origin


@pytest.mark.parametrize(
    "origin",
    [
        "http://1.1.1.1:8007",
        "https://203.0.113.9:8452",
        "https://no-such-host-xyz.invalid:8452",
        "not-a-url",
        "",
    ],
)
def test_the_address_test_says_no_when_the_answer_is_not_this_machine(origin: str) -> None:
    """Including every way it can fail to decide.

    The last two are the important ones: an unparseable origin and an empty
    string must both answer `False` rather than raise or guess, because `False`
    is the answer that leaves the condition `FAIL` and `FAIL` is the answer that
    cannot be wrong in the dangerous direction.
    """
    assert gate.is_self_addressed(origin) is False, origin


def test_address_equality_tolerates_scope_ids_and_v4_mapped_forms() -> None:
    assert gate._same_address("100.87.56.102", "100.87.56.102")
    assert gate._same_address("fe80::1%en0", "fe80::1")
    assert gate._same_address("::ffff:127.0.0.1", "127.0.0.1")
    assert not gate._same_address("100.87.56.102", "100.99.212.85")
    assert not gate._same_address("100.87.56.102", "not-an-address")


# ---------------------------------------------------------------------------
# end to end: the soft branch, reached the honest way
# ---------------------------------------------------------------------------


def test_the_serving_node_reports_every_deployed_condition_as_vantage_limited(
    serving_node: World,
) -> None:
    results, code = run(serving_node, owner_login=OWNER)
    for ident in DEPLOYED_CONDITIONS:
        assert by_id(results, ident).status == gate.VANTAGE, by_id(results, ident).detail
    assert code == 3
    assert code != 0, "exiting 0 here would assert a convergence nobody observed"


def test_the_local_conditions_are_still_passed_not_skipped(serving_node: World) -> None:
    """The soft branch must not become a hole in the local checks.

    Every local half was evaluated and is reported as evaluated, so a reader can
    tell "the local side is clean, the deployed side is invisible" from "nothing
    was checked". That distinction is the difference between a useful report and
    a shrug.
    """
    results, _ = run(serving_node, owner_login=OWNER)
    assert by_id(results, "1 source-version").status == gate.PASS
    for ident in DEPLOYED_CONDITIONS:
        detail = by_id(results, ident).detail
        # The local half is named as done, and the deployed half as not done.
        # Both halves appearing in one detail is what makes the condition
        # readable as "clean here, invisible there" rather than "unknown".
        assert "local" in detail, f"{ident}: {detail}"
        assert "not evaluated: " in detail, f"{ident}: {detail}"


def test_every_vantage_limited_condition_names_the_request_that_would_settle_it(
    serving_node: World,
) -> None:
    """"Unevaluable" without "here is what would evaluate it" only moves the
    confusion from the exit code to the reader, so the remedy is asserted
    per-condition rather than once in the banner."""
    results, _ = run(serving_node, owner_login=OWNER)
    for ident in DEPLOYED_CONDITIONS:
        detail = by_id(results, ident).detail
        assert "NOT a pass" in detail, f"{ident}: {detail}"
        assert "host that is NOT the serving node" in detail, f"{ident}: {detail}"
        assert "phone on the tailnet" in detail, f"{ident}: {detail}"


def test_the_vantage_report_is_never_called_converged(serving_node: World) -> None:
    results, _ = run(serving_node, owner_login=OWNER)
    rendered = gate.render(results)
    assert "CONVERGED" not in rendered
    assert "RESULT: VANTAGE-LIMITED" in rendered
    assert "asserts no convergence at all" in rendered
    assert "only ever been seen failing closed" in rendered
    assert "VANTAGE" in rendered


def test_the_vantage_icon_cannot_be_read_as_a_pass(serving_node: World) -> None:
    """A log line is often all anyone reads, and `VANTAGE` next to a green
    `PASS` column has to be unmistakable at a glance."""
    rendered = gate.render(run(serving_node, owner_login=OWNER)[0])
    assert "[VANTAGE ]" in rendered
    assert "PASS" not in rendered.split("RESULT:")[0].replace("[PASS    ]", "")


def test_the_vantage_banner_survives_a_run_that_also_has_a_real_failure(
    vantage_world: World,
) -> None:
    """A run can hold a real defect *and* an invisible deployed half, and that
    is the run where a reader most needs to know both. The banner is printed
    whenever any condition is vantage-limited, not only when it wins."""
    touch_young(vantage_world.root / "app" / "db" / "schema.sql")
    results, code = run(vantage_world, owner_login=OWNER)
    assert by_id(results, "3 backend-freshness").status == gate.FAIL
    assert by_id(results, "2 deployed-version").status == gate.VANTAGE
    rendered = gate.render(results)
    assert "RESULT: FAILED" in rendered
    assert "condition(s) are VANTAGE-LIMITED" in rendered
    assert code == 1, "a real failure must still be the exit code"


# ---------------------------------------------------------------------------
# the half that matters: real failures, in a world that is already soft
# ---------------------------------------------------------------------------


def test_a_version_mismatch_still_fails_and_exits_one(world: World) -> None:
    """**Requirement: a real failure must still fail.**

    A Serve route proxying a different backend is the failure condition 2
    exists for, and it is visible precisely when the deployed origin *does*
    answer — which is the same situation the vantage probe treats as
    observable. The soft branch is on the other side of a 200, so it cannot
    reach this.
    """
    world.responses[f"{DEPLOYED}/api/version"] = world._json({"version": STALE_VERSION})
    results, code = run(world, owner_login=OWNER)
    result = by_id(results, "2 deployed-version")
    assert result.status == gate.FAIL, result.detail
    assert STALE_VERSION in result.detail
    assert code == 1


def test_a_stale_backend_timestamp_still_fails_and_exits_one(world: World) -> None:
    world.responses[f"{DEPLOYED}/api/version"] = world._json(
        {"version": VERSION}, started_at="2026-01-02T03:04:06.000000Z"
    )
    results, code = run(world, owner_login=OWNER)
    result = by_id(results, "3 backend-freshness")
    assert result.status == gate.FAIL, result.detail
    assert "two different processes" in result.detail
    assert code == 1


def test_a_missing_response_key_still_fails_and_exits_one(world: World) -> None:
    payload = world._spec_payload("/api/recipes")
    body = json.loads(payload.body)
    del body["staleMappingCount"]
    world.responses[f"{DEPLOYED}/api/recipes"] = world._json(body)
    results, code = run(world, owner_login=OWNER)
    result = by_id(results, "4 release-smoke")
    assert result.status == gate.FAIL, result.detail
    assert "staleMappingCount" in result.detail
    assert code == 1


def test_an_unrotated_cache_version_still_fails_and_exits_one(world: World) -> None:
    """§3b.1's recorded incident: internally consistent, previous cache key."""
    world.git_overrides["diff"] = "app/static/js/views/home.js\n"
    results, code = run(world, owner_login=OWNER)
    result = by_id(results, "5 cache-rotation")
    assert result.status == gate.FAIL, result.detail
    assert "was NOT rotated" in result.detail
    assert code == 1


def test_a_wrong_shell_pin_still_fails_and_exits_one(world: World) -> None:
    stale = world.index_text.replace("__APP_VERSION__", STALE_VERSION)
    world.responses[f"{DEPLOYED}/"] = world._text(stale)
    results, code = run(world, owner_login=OWNER)
    result = by_id(results, "6 shell-assets")
    assert result.status == gate.FAIL, result.detail
    assert "browser HTTP cache" in result.detail
    assert code == 1


# ...and the same four, now inside a world whose deployed half is invisible.
# Each of these is the regression that widening the soft branch would cause.


def test_an_unrotated_cache_version_fails_even_while_vantage_limited(vantage_world: World) -> None:
    """**The sharpest of the four.** Condition 5's rotation half is a fact about
    the repository, not the network, so it stays evaluable while the deployed
    half is not — and it must still be the exit code. A gate that let a
    vantage-limited condition absorb a repository fact would be reporting
    "cannot evaluate" about something it had in fact evaluated and disliked.
    """
    vantage_world.git_overrides["diff"] = "app/static/js/router.js\n"
    results, code = run(vantage_world, owner_login=OWNER)
    result = by_id(results, "5 cache-rotation")
    assert result.status == gate.FAIL, result.detail
    assert "app/static/js/router.js" in result.detail
    assert code == 1


def test_a_stale_startup_file_fails_even_while_vantage_limited(vantage_world: World) -> None:
    touch_young(vantage_world.root / "app" / "db" / "schema.sql")
    results, code = run(vantage_world, owner_login=OWNER)
    result = by_id(results, "3 backend-freshness")
    assert result.status == gate.FAIL, result.detail
    assert "running pre-change bytes" in result.detail
    assert code == 1


def test_a_missing_local_key_fails_even_while_vantage_limited(vantage_world: World) -> None:
    """A new frontend against an old in-memory schema, on the one origin the
    gate can see. The deployed half being invisible must not soften the local
    half, because the deployed half is not where this defect lives."""
    payload = vantage_world._spec_payload("/health")
    body = json.loads(payload.body)
    del body["mappings"]
    vantage_world.responses[f"{LOCAL}/health"] = vantage_world._json(body)
    results, code = run(vantage_world, owner_login=OWNER)
    result = by_id(results, "4 release-smoke")
    assert result.status == gate.FAIL, result.detail
    assert "mappings" in result.detail
    assert code == 1


def test_a_local_served_worker_mismatch_fails_even_while_vantage_limited(
    vantage_world: World,
) -> None:
    vantage_world.responses[f"{LOCAL}/sw.js"] = vantage_world._text(
        rewrite_cache_version(vantage_world.sw_text, STALE_VERSION)
    )
    results, code = run(vantage_world, owner_login=OWNER)
    result = by_id(results, "5 cache-rotation")
    assert result.status == gate.FAIL, result.detail
    assert "previous exact-versioned bundle" in result.detail
    assert code == 1


def test_a_local_shell_pin_fails_even_while_vantage_limited(vantage_world: World) -> None:
    stale = vantage_world.index_text.replace("__APP_VERSION__", STALE_VERSION)
    vantage_world.responses[f"{LOCAL}/"] = vantage_world._text(stale)
    results, code = run(vantage_world, owner_login=OWNER)
    result = by_id(results, "6 shell-assets")
    assert result.status == gate.FAIL, result.detail
    assert code == 1


def test_a_deployed_origin_answering_500_fails_even_though_it_is_this_machine(
    world: World,
) -> None:
    """The narrowness, end to end.

    `self_addressed` alone must never be enough: with the deployed origin
    answering `500`, the vantage has been established and every other condition
    would be comparing a healthy local half — and the deployed half is broken in
    a way this host can see perfectly well. `FAIL`, exit 1.
    """
    world.self_addressed = True
    for path in ("/api/version", "/", "/sw.js", "/api/recipes", "/health", "/api/session"):
        world.responses[f"{DEPLOYED}{path}"] = gate.Response(
            url="", status=500, headers={}, body=b"upstream exploded"
        )
    results, code = run(world, owner_login=OWNER)
    assert by_id(results, "2 deployed-version").status == gate.FAIL
    for ident in ("4 release-smoke", "5 cache-rotation", "6 shell-assets"):
        assert by_id(results, ident).status == gate.FAIL, ident
    assert code == 1


def test_a_deployed_origin_404ing_is_a_wrong_route_not_a_vantage_limit(world: World) -> None:
    world.self_addressed = True
    world.responses[f"{DEPLOYED}/api/version"] = gate.Response(
        url="", status=404, headers={}, body=b"no such route"
    )
    results, code = run(world, owner_login=OWNER)
    assert by_id(results, "2 deployed-version").status == gate.FAIL
    assert code == 1


def test_the_local_origin_still_gates_the_whole_run(vantage_world: World) -> None:
    """If the *local* origin will not answer, the gate says so once and stops.
    The vantage classification does not become a way to run the rest of the gate
    against a backend it could not reach.

    Condition 9 still runs, and the reason it is not a violation is the whole
    reason it is allowed to exist: it never touches the origin. It reads the
    Serve configuration and the process table, so in this world it reports
    *where the route points* — a routing fact that stays true while the backend
    behind it stops answering. Listing it here is what keeps that honest: were 9
    ever to start probing the origin, this list would gain an entry the
    suppression is supposed to have prevented."""
    vantage_world.responses[f"{LOCAL}/api/version"] = gate.Response(
        url="", status=0, headers={}, body=b"connection refused"
    )
    results, code = run(vantage_world, owner_login=OWNER)
    assert [result.ident for result in results] == [
        "0 listener",
        "0b identity",
        "9 ingress-identity",
    ]
    assert by_id(results, "9 ingress-identity").status == gate.PASS
    assert code == 1


def test_a_vantage_limited_run_never_reports_a_source_version_mismatch(
    vantage_world: World,
) -> None:
    """Condition 1 has no deployed half, so there is nothing for the vantage to
    excuse. It is the control: the same world, the same gate, one condition that
    cannot be softened."""
    vantage_world.responses[f"{LOCAL}/api/version"] = vantage_world._json(
        {"version": STALE_VERSION}
    )
    results, code = run(vantage_world, owner_login=OWNER)
    assert by_id(results, "1 source-version").status == gate.FAIL
    assert code == 1


# ---------------------------------------------------------------------------
# the exit-code contract, now four states
# ---------------------------------------------------------------------------


def _result(status: str) -> gate.CheckResult:
    return gate.CheckResult("x", "t", status, "")


def test_the_four_outcomes_are_four_distinct_exit_codes() -> None:
    assert gate.exit_code([_result(gate.PASS)]) == 0
    assert gate.exit_code([_result(gate.UNPROVEN)]) == 2
    assert gate.exit_code([_result(gate.VANTAGE)]) == 3
    assert gate.exit_code([_result(gate.FAIL)]) == 1
    assert gate.exit_code([_result(gate.VANTAGE)]) != 0, "a vantage limit is not a pass"


def test_the_precedence_is_one_two_three_and_zero() -> None:
    """Each step is a claim about urgency, and every step is load-bearing.

    `1 > 3` because a release with a real defect is not made safer by also being
    unevaluable. `2 > 3` because an under-specified invocation is the operator's
    to fix in the same shell and may be *masking* whether the vantage limit
    applies at all. `3 > 0` because nothing about the release has been shown to
    be right, and a zero would claim otherwise.
    """
    assert gate.exit_code([_result(gate.FAIL), _result(gate.VANTAGE)]) == 1
    assert gate.exit_code([_result(gate.UNPROVEN), _result(gate.VANTAGE)]) == 2
    assert gate.exit_code([_result(gate.VANTAGE), _result(gate.PASS)]) == 3
    assert gate.exit_code([_result(gate.FAIL), _result(gate.UNPROVEN)]) == 1


def test_no_combination_of_non_passing_outcomes_reaches_zero() -> None:
    statuses = [gate.PASS, gate.FAIL, gate.UNPROVEN, gate.VANTAGE]
    for size in range(1, len(statuses) + 1):
        for combination in itertools.combinations(statuses, size):
            if gate.PASS in combination:
                continue
            results = [_result(status) for status in combination]
            assert gate.exit_code(results) != 0, combination


def test_the_help_text_states_all_four_exit_codes() -> None:
    """The contract lives in `--help` for anyone who runs the gate without
    reading the runbook, and it is asserted rather than trusted."""
    help_text = gate.build_parser().format_help()
    for fragment in ("Exit 0", "1", "2", "3", "Only 0 is a pass", "VANTAGE-LIMITED"):
        assert fragment in help_text, fragment


def test_omitting_the_deployed_origin_is_still_unproven_not_vantage_limited(
    vantage_world: World,
) -> None:
    """Two different "I could not evaluate this", kept apart.

    Exit 2 is a missing argument and its remedy is to supply it; exit 3 is a
    present argument that cannot be exercised from here and its remedy is a
    different computer. Folding them would file an unfixable-from-here problem
    under the one code whose documented fix is a command line.
    """
    results, code = run(vantage_world, deployed_origin=None, owner_login=OWNER)
    assert by_id(results, "2 deployed-version").status == gate.UNPROVEN
    assert "NOT verified" in by_id(results, "2 deployed-version").detail
    assert code == 2


def test_an_unproven_condition_outranks_a_vantage_limited_one_without_hiding_it(
    serving_node: World,
) -> None:
    """`2 > 3` in the gate's own output, not just in the exit-code function.

    The listener check is off in this run, so condition 0 is `UNPROVEN` while
    conditions 2-8 are `VANTAGE-LIMITED`. The exit code is 2 — the deficiency
    the operator can clear in the same shell — and no condition has been
    reclassified to make that happen: the vantage statuses are still their own
    status, with their own remedy text.
    """
    results, code = run(serving_node, owner_login=OWNER, expect_running=False)
    assert by_id(results, "0 listener").status == gate.UNPROVEN
    for ident in DEPLOYED_CONDITIONS:
        assert by_id(results, ident).status == gate.VANTAGE, ident
    assert code == 2
    assert gate.exit_code([_result(gate.UNPROVEN), _result(gate.VANTAGE)]) == 2
    rendered = gate.render(results)
    assert "condition(s) are VANTAGE-LIMITED" in rendered, (
        "a vantage limit that lost the exit code must still be reported"
    )


def test_a_vantage_limited_condition_still_reports_its_own_unproven_notes(
    serving_node: World,
) -> None:
    """Condition 3 is vantage-limited *and* under-specified when no vault is
    named. The vantage status wins — it is the one that means "this cannot be
    checked from here at all" — but the unproven note rides along rather than
    being dropped, because losing it is the same collapse one level down."""
    results, code = run(serving_node, owner_login=OWNER, vault=None)
    result = by_id(results, "3 backend-freshness")
    assert result.status == gate.VANTAGE
    assert "still unproven" in result.detail
    assert "recipe/pantry index inputs" in result.detail
    assert code == 3


# ---------------------------------------------------------------------------
# the classification is computed once and shared
# ---------------------------------------------------------------------------


def test_every_condition_sees_the_same_vantage_verdict(vantage_world: World) -> None:
    """Seven conditions consulting one deployed origin and reaching seven
    different answers about whether it was observable would mean the gate does
    not know what it is talking about. One classification, one object."""
    options = vantage_world.options(owner_login=OWNER)
    gate.run_gate(options)
    assert options.vantage is not None
    first = options.vantage
    assert gate.deployed_vantage(options) is first


def test_the_vantage_probe_reads_no_console_input_and_writes_nothing(vantage_world: World) -> None:
    """The classifier's only I/O is three GETs against the deployed origin and a
    routing-table query. Asserted rather than assumed, because a gate that grew a
    side effect would be a gate nobody could run against production."""
    asked: list[tuple[str, str | None]] = []

    def probe(url: str, login: str | None) -> gate.Response:
        asked.append((url, login))
        return refusing("", login)

    options = vantage_world.options(probe=probe, owner_login=OWNER)
    results = gate.run_gate(options)
    assert len(asked) == 3, asked
    assert all(url == f"{DEPLOYED}/api/version" for url, _ in asked), asked
    assert [login for _, login in asked] == [None, gate.PROBE_FOREIGN_LOGIN, OWNER]
    assert gate.deployed_vantage(options).unobservable
    assert {result.status for result in results} == {
        gate.PASS,
        gate.UNPROVEN,
        gate.VANTAGE,
    }


def test_the_probe_never_sends_the_owner_login_when_the_gate_was_not_told_it(
    vantage_world: World,
) -> None:
    """`--owner-login` is a `Settings` value, not a secret, so this is not about
    disclosure. It is that a gate with no owner login must not invent one: the
    probe would then be presenting a login it has no reason to believe in and
    calling the answer evidence."""
    asked: list[str | None] = []

    def probe(_url: str, login: str | None) -> gate.Response:
        asked.append(login)
        return refusing("", login)

    gate.run_gate(vantage_world.options(probe=probe))
    assert asked == [None, gate.PROBE_FOREIGN_LOGIN]


def test_a_failing_probe_does_not_take_the_run_down(tmp_path: Path) -> None:
    """A classification that raises would turn a diagnosis into a traceback.

    The classifier is written so it cannot: `is_self_addressed` answers `False`
    on every failure and `classify_vantage` answers `undecided` on anything but
    the exact refusal, so the worst case is the pre-existing honest `FAIL`.
    """
    world = World(tmp_path)
    world.identity_absent = True
    world.self_addressed = True

    def broken(url: str, login: str | None) -> gate.Response:
        if url.endswith("/api/version"):
            raise OSError("probe exploded")
        return world.fetch(url)

    results, code = run(world, probe=broken, owner_login=OWNER)
    assert code in (1, 2), code
    assert all(result.status != gate.VANTAGE for result in results), [
        result.ident for result in results
    ]


def test_the_gate_runs_the_whole_release_sequence_against_a_healthy_pair(world: World) -> None:
    """The control for the whole file: with nothing broken, the new outcome does
    not appear at all, and the run is exactly what it was before."""
    results, code = run(world, owner_login=OWNER)
    assert not [result.ident for result in results if result.status == gate.VANTAGE]
    assert code == 2, "only condition 0 is unproven in this fixture"
    statuses: dict[str, Any] = {result.ident: result.status for result in results}
    assert statuses["2 deployed-version"] == gate.PASS
    assert statuses["8 busy-guard"] == gate.PASS

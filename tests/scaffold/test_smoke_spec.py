"""`scripts/converge-smoke.json` must agree with the code it asserts about.

**The gap this closes, and the part of it that cannot close.**

`README.md`'s declared gap was: a release that changes the API contract and
forgets to edit the smoke spec leaves it asserting the *last* release's fields,
which is weaker than it looks — and the gate cannot detect the omission, "because
'spec was not updated' is not a fact visible from the running server." That
sentence is right about the gate and wrong about the repo.

The gate fetches live URLs and can only see a *deployed* server, at *release*
time, on whichever host it runs from — which on the serving node is the
`VANTAGE-LIMITED` world where conditions 2–8 never execute. A stale spec is
therefore caught late, from one vantage, if at all.

But the key sets are not only a fact about the running server. They are also
constants in this repository: `LIST_RECIPE_KEYS` here, the `/health` and
`/api/session` shapes in `tests/api/`. Two sources of the same fact that are
never compared are one source of the fact plus one source of drift, and this
module is the comparison. It runs on every `pytest`, offline, in a second.

**What is still undetectable, stated so nobody re-files this as a bug:** a
release that ADDS a key to a live response and adds nothing to the spec leaves a
perfectly consistent pair — the spec is a strict subset of the truth, the gate
passes, and this module passes. One-directional addition cannot be caught by any
comparison between a subset and its superset. That residue is what the runbook's
"edit the smoke spec" release step is for, and it is irreducibly a human
obligation; this narrows the gap from "unchecked until a release, from one host"
to "unchecked, but a REMOVAL or a RENAME is caught on every run."

**Why the detail route is deliberately absent from the spec.** `/api/recipes/
{note_name}` is where this release's three new keys live, and it is not here: the
gate builds `f"{origin}{path}"` literally, so a parameterised path cannot resolve,
and the only way to name one is to hardcode a vault recipe basename — which fails
for a reason that has nothing to do with the release the moment that note is
renamed. That is the same class of problem §9.12's `⚠ 已重命名` drift check exists
for, and reintroducing it into the release gate would trade a real regression for
a spurious one. The detail shape is pinned exactly, offline, by
`tests/api/test_recipes_api.py::test_the_detail_recipe_carries_the_list_keys_plus_
steps_and_history`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.api.test_recipes_api import LIST_RECIPE_KEYS

REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE_SPEC = REPO_ROOT / "scripts" / "converge-smoke.json"


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return json.loads(SMOKE_SPEC.read_text(encoding="utf-8"))


def _entry(spec: dict[str, Any], path: str) -> dict[str, Any]:
    for endpoint in spec["endpoints"]:
        if endpoint["path"] == path:
            return endpoint
    raise AssertionError(
        f"converge-smoke.json has no entry for {path}; the spec and the routes have drifted"
    )


def test_the_spec_parses_and_is_not_empty(spec: dict[str, Any]) -> None:
    assert spec["endpoints"], "an empty smoke spec asserts nothing"
    for endpoint in spec["endpoints"]:
        assert endpoint.get("top_level_keys"), f"{endpoint['path']} asserts no top-level keys"
        assert endpoint.get("$release"), f"{endpoint['path']} has no $release label"


def test_the_recipes_list_row_keys_match_the_code(spec: dict[str, Any]) -> None:
    """The one this repository can be wrong about most easily.

    The spec's `array_item_keys.recipes` and `LIST_RECIPE_KEYS` are the same fact
    written twice. A key added to one and not the other is invisible to the gate
    until a deploy, and invisible to `pytest` entirely without this.
    """
    entry = _entry(spec, "/api/recipes")
    declared = set(entry["array_item_keys"]["recipes"])
    assert declared == set(LIST_RECIPE_KEYS), (
        "converge-smoke.json and LIST_RECIPE_KEYS disagree about a recipes row: "
        f"spec-only={sorted(declared - set(LIST_RECIPE_KEYS))} "
        f"code-only={sorted(set(LIST_RECIPE_KEYS) - declared)}"
    )


def test_every_slot_key_the_spec_asserts_is_still_a_slot_key(spec: dict[str, Any]) -> None:
    """The nested per-slot list is the F7 provenance contract, in two places."""
    from tests.api.test_recipes_api import SLOT_KEYS

    entry = _entry(spec, "/api/recipes")
    nested = entry["array_item_nested_keys"]["recipes"]["ingredients"]
    assert set(nested) == set(SLOT_KEYS), (
        "converge-smoke.json and SLOT_KEYS disagree about an ingredient slot: "
        f"spec-only={sorted(set(nested) - set(SLOT_KEYS))} "
        f"code-only={sorted(set(SLOT_KEYS) - set(nested))}"
    )


def test_the_top_level_keys_are_sorted_so_a_diff_is_readable(spec: dict[str, Any]) -> None:
    """A stable order is the whole value of a committed list of keys.

    An unsorted list makes every addition a rewrite of the line, which is how a
    list stops being reviewed.
    """
    for endpoint in spec["endpoints"]:
        keys = endpoint["top_level_keys"]
        assert keys == sorted(keys), f"{endpoint['path']} top_level_keys is not sorted: {keys}"


def test_the_spec_does_not_assert_a_key_the_route_no_longer_publishes(
    spec: dict[str, Any],
) -> None:
    """The removal case this module can actually catch.

    `trackerSynced` was removed from `GET /api/cook-logs` by this release. That
    route is not in the spec — it needs a `date` whose daily note may not exist,
    and the gate would then fail for a reason unrelated to any release — so the
    removal is pinned here instead, by asserting the retired key appears nowhere
    in the spec. A key the server no longer sends, asserted by a release gate, is
    a gate that fails on a correct deploy.
    """
    raw = SMOKE_SPEC.read_text(encoding="utf-8")
    assert "trackerSynced" not in raw, (
        "converge-smoke.json still asserts trackerSynced, which the cook-log route "
        "no longer publishes"
    )

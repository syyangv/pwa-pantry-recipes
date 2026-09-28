#!/usr/bin/env python3
"""Re-freeze `tests/fixtures/golden_match_results.json`, deliberately, in its own commit.

    .venv/bin/python scripts/snapshot_golden_match_results.py --check          # the default
    .venv/bin/python scripts/snapshot_golden_match_results.py --regenerate     # write, after review
    .venv/bin/python scripts/snapshot_golden_match_results.py --regenerate --accept-measurements

**Why the default writes nothing.** This file is the CI anchor for the matcher
(D1, §10.3): the whole point of freezing nine resolutions and twenty-three misses
is that a change to any of them is a *visible* event. A stray run that rewrote it
would leave the anchor holding whatever the last person to touch the matcher
happened to produce, and the golden test would go quietly green over a regression
in what the user is told they can cook. So the default compares, prints a unified
diff, and exits non-zero on drift; only `--regenerate` writes. Same contract as
`scripts/snapshot_pantry_catalog.py --check`, which is where it comes from.

**This job is hermetic, and that is the difference from the catalog script's
other three.** Every
input is a committed file: `tests/fixtures/real_recipes/*.md` and
`tests/fixtures/pantry_items_snapshot.json`. Nothing here reads the live vault,
the producer's `pantry_items.db`, the network, or the clock except for the
`generatedAt` line. So unlike `--job recipes` there is no "the world moved" to
absorb — a diff from this script means *this repository's matcher or fixtures
changed*, which is exactly the event a reviewer must see. The real-recipes
fixture stays frozen; re-freezing those bytes is `--job recipes`'s job and
requires the vault.

**Two flags, because one is not enough.** `--regenerate` alone still refuses
while a measured figure has moved, since a moved figure is a change to every
golden expectation in the suite. `--accept-measurements` is the second,
deliberate half. Re-baselining `EXPECTED_SUMMARY` and `EXPECTED_TIER_FIRES` in
this file is a **hand edit** the flag does not perform: the flag records the
intent, which is the whole point of carrying the numbers as literals in a
reviewable file rather than only inside the JSON they check.

**The headline strings are rendered by the render layer, and cross-checked
against the spec's own four forms.** `format.js` is F16/F17's implementation and
this repository has exactly one of it, so the strings come from
`scripts/render_headlines.mjs` calling the real `chipClass()` and `headline()`.
Because a subprocess is an easy thing to get wrong, `check_headlines()` also
compares every rendered string against the four §9.13.1 forms quoted in
`tests/recipes/golden.py`, and this script **refuses to write** if they disagree.
A golden file whose strings neither the browser nor the spec would produce is
worse than no strings at all.

**`node` is a requirement of `--regenerate`, and its absence is a refusal, not a
fallback.** There is no Python re-spelling of `format.js` to fall back to, and
adding one would be a second implementation of F16/F17 — see that file's
docstring.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Final

_REPO_ROOT: Final = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:  # runnable as `python scripts/…` from anywhere
    sys.path.insert(0, str(_REPO_ROOT))

from app.config import Settings  # noqa: E402
from tests.recipes import golden  # noqa: E402

#: The measured shape of the corpus as of this commit, and the second flag's
#: whole reason for existing. Written out rather than read from the fixture for
#: the same reason `scripts/snapshot_pantry_catalog.py` carries `EXPECTED_*`:
#: a number a reviewer has to see in a *code* diff is a number that gets
#: discussed, and a number that only exists inside the JSON it is checking is a
#: number that gets absorbed.
EXPECTED_SUMMARY: Final[dict[str, Any]] = {
    "recipeCount": 16,
    "materialSlotCount": 32,
    "materialResolvedSlots": 9,
    "materialUnresolvedSlots": 23,
    "allMaterialsResolvedRecipes": 5,
    "duplicateSlotConflicts": 0,
    "staleResets": 0,
    "distinctMaterialNames": 25,
    "distinctMaterialNamesResolved": 7,
    "distinctSeasoningNames": 19,
    "distinctSeasoningNamesStaples": 17,
}

#: The tier histogram, guarded separately because it is the one summary key a
#: *matcher* change moves without moving any headline count — a value that
#: started resolving on a different tier changes no user's answer and is exactly
#: the change §9.8's ordering is supposed to be visible for. Tiers 0, 1, 2, 3
#: and 7 are **zero and are meant to stay zero**: no note in the corpus spells
#: out an id, no note's name is a whole `canonical_name`, `variants` is
#: effectively empty, and the staples tier is `调料`-only. Tier 8 is the miss
#: bucket, and its 18 of 25 is D1's accepted ceiling measured over eight tiers
#: rather than the three D1's own 58% was measured over.
EXPECTED_TIER_FIRES: Final[dict[str, int]] = {
    "0": 0,
    "1": 0,
    "2": 0,
    "3": 0,
    "4": 1,
    "5": 1,
    "6": 5,
    "7": 0,
    "8": 18,
}


def guarded_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """The summary keys a regeneration has to justify, and nothing else."""
    return {
        **{key: summary[key] for key in EXPECTED_SUMMARY},
        "tierFireCountsByDistinctMaterialName": summary[
            "tierFireCountsByDistinctMaterialName"
        ],
    }


def summary_drift(measured: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    """One line per measured figure that moved, naming the old and the new value."""
    return [
        f"{key}: was {expected[key]!r}, now {measured[key]!r}"
        for key in expected
        if measured.get(key) != expected[key]
    ]


def report_measurements(summary: dict[str, Any]) -> None:
    """Print the measured shape, so a refusal below carries the new numbers."""
    print("measured:")
    for key, value in summary.items():
        if key in {"materialNames", "seasoningNames"}:
            print(f"  {key} = {len(value)} names (listed in the fixture)")
            continue
        print(f"  {key} = {value!r}")
    print()


def settings_under(root: Path) -> Settings:
    """A `Settings` over a throwaway tree, because the store needs a database.

    `tmp_path`-shaped rather than the developer's: this script must not be able
    to write anything into `APP_DATA_DIR` that matters. The `pantry_items.db` is
    created empty and is **never opened** — the golden path reads the committed
    JSON snapshot through `build_snapshot()`, and `PANTRY_ITEMS_DB` is only
    required to exist and to sit outside the vault (`Settings` says so, and it is
    the same check the app makes at startup).
    """
    vault = root / "vault"
    data = root / "data"
    vault.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    catalog = root / "pantry_items.db"
    connection = sqlite3.connect(catalog)
    connection.close()
    return Settings.from_mapping(
        {
            "OBSIDIAN_VAULT_PATH": str(vault),
            "APP_DATA_DIR": str(data),
            "PANTRY_ITEMS_DB": str(catalog),
            "PUBLIC_ORIGIN": "https://recipes.test.invalid",
            "TAILSCALE_OWNER_LOGIN": "owner@test.invalid",
            "DEV_IDENTITY": "owner@test.invalid",
            "BIND_HOST": "127.0.0.1",
            "APP_TIMEZONE": "America/New_York",
            "TRUST_TAILSCALE_HEADERS": "false",
        }
    )


def unified(committed: str | None, candidate: str) -> list[str]:
    """The diff a reviewer would read, or `["(identical)"]`."""
    if committed == candidate:
        return ["(the committed golden file is already identical)"]
    return list(
        difflib.unified_diff(
            (committed or "").splitlines(),
            candidate.splitlines(),
            fromfile="committed" if committed is not None else "(absent)",
            tofile="regenerated",
            lineterm="",
            n=1,
        )
    )


def run(out_path: Path, *, regenerate: bool, accept_measurements: bool) -> int:
    """Measure, compare, and either refuse or write. Prints before it refuses."""
    print(f"golden source: {golden.GOLDEN_PATH.name} (measured from the committed fixtures)")
    print(f"  catalog:   {golden.REPO_ROOT / 'tests/fixtures/pantry_items_snapshot.json'}")
    print(f"  recipes:   {golden.REPO_ROOT / 'tests/fixtures/real_recipes'}")

    with tempfile.TemporaryDirectory(prefix="golden-snapshot-") as workdir:
        payload = asyncio.run(golden.measure(settings_under(Path(workdir))))

    summary = payload["summary"]
    report_measurements(summary)

    rendered = golden.render_headlines(payload)
    problems = golden.check_headlines(payload, rendered)
    if problems:
        sys.stdout.flush()
        print(
            "the render layer and §9.13.1's four frozen headline forms disagree; "
            "refusing to freeze strings neither of them would produce:",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    measured = guarded_summary(summary)
    expected = {**EXPECTED_SUMMARY, "tierFireCountsByDistinctMaterialName": EXPECTED_TIER_FIRES}
    drift = summary_drift(measured, expected)

    frozen = golden.with_headlines(payload, rendered)
    committed_text = out_path.read_text(encoding="utf-8") if out_path.is_file() else None
    committed = golden.load(out_path) if committed_text is not None else {}
    # `generatedAt` is the one field a no-op regeneration must not move: it
    # records when the file was last written, not anything about the matcher, so
    # a run that changes nothing keeps the date already there. A run that changes
    # anything stamps the new one, which is what makes the date mean "this is
    # the commit where the numbers moved".
    existing_date = str(committed.get("generatedAt", ""))
    # A file with no date is not "up to date" even if the bytes otherwise agree:
    # an unstamped expectation is one whose provenance nobody recorded.
    unchanged = (
        committed_text is not None
        and existing_date != ""
        and committed_text == golden.encode(frozen, existing_date)
    )
    candidate = golden.encode(
        frozen, existing_date if unchanged else date.today().isoformat()
    )

    print(f"diff {out_path}:")
    for line in unified(committed_text, candidate):
        print(f"  {line}")
    print()

    if drift and not accept_measurements:
        # The measured numbers and the diff are already on stdout; flush before
        # the refusal so the new values are never buffered behind the message
        # that is about them.
        sys.stdout.flush()
        print("a measured figure moved; refusing to re-freeze it:", file=sys.stderr)
        for line in drift:
            print(f"  {line}", file=sys.stderr)
        print(
            "a moved figure is a change to what the user is told they can cook: call it "
            "out in the commit message, update EXPECTED_SUMMARY and EXPECTED_TIER_FIRES in "
            "this script in the same commit, then rerun with --accept-measurements",
            file=sys.stderr,
        )
        return 1
    if drift:
        print("re-baselining the measured figures on request:")
        for line in drift:
            print(f"  {line}")

    if not regenerate:
        if committed_text == candidate:
            print("up to date; nothing to do (pass --regenerate to write)")
            return 0
        sys.stdout.flush()
        print("would write; pass --regenerate to do it", file=sys.stderr)
        return 1

    if committed_text == candidate:
        print("up to date; wrote nothing")
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(candidate, encoding="utf-8")
    print(f"wrote {out_path} ({len(candidate)} bytes)")
    print()
    print("commit message material — the numbers a reviewer needs, because they are")
    print("what moved:")
    print(
        f"  材料 slots {summary['materialSlotCount']}: "
        f"{summary['materialResolvedSlots']} resolved, "
        f"{summary['materialUnresolvedSlots']} unresolved"
    )
    print(
        f"  {summary['allMaterialsResolvedRecipes']} of {summary['recipeCount']} notes have "
        "every 材料 resolved"
    )
    print(
        f"  distinct 材料 names {summary['distinctMaterialNames']}, "
        f"{summary['distinctMaterialNamesResolved']} resolved; distinct 调料 names "
        f"{summary['distinctSeasoningNames']}, "
        f"{summary['distinctSeasoningNamesStaples']} staples-satisfied"
    )
    print(f"  tier fires by distinct name: {summary['tierFireCountsByDistinctMaterialName']}")
    print(
        f"  duplicate-slot conflicts {summary['duplicateSlotConflicts']}, "
        f"stale resets {summary['staleResets']}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Re-freeze the golden matcher expectation. The default writes nothing: "
            "it prints the diff and exits non-zero on drift."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="compare only and write nothing (the default)",
    )
    mode.add_argument(
        "--regenerate",
        action="store_true",
        help="write the golden file after reviewing the diff",
    )
    parser.add_argument(
        "--accept-measurements",
        action="store_true",
        help="re-baseline the measured figures on purpose; requires --regenerate",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=golden.GOLDEN_PATH,
        help="the golden file to compare against / write",
    )
    args = parser.parse_args(argv)

    if args.accept_measurements and not args.regenerate:
        parser.error("--accept-measurements only means something with --regenerate")
    return run(args.out, regenerate=args.regenerate, accept_measurements=args.accept_measurements)


if __name__ == "__main__":
    raise SystemExit(main())

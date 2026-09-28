#!/usr/bin/env python3
"""Regenerate the two frozen test inputs, deliberately, in their own commit.

    .venv/bin/python scripts/snapshot_pantry_catalog.py --check          # the default
    .venv/bin/python scripts/snapshot_pantry_catalog.py --regenerate     # write, after review
    .venv/bin/python scripts/snapshot_pantry_catalog.py --job recipes    # one job only
    .venv/bin/python scripts/snapshot_pantry_catalog.py --job stock      # the 💵 parity gate

**Why the default writes nothing.** These fixtures are the CI anchor for the
golden matcher test, and CI never runs this script. If a stray run could rewrite
a committed fixture, then the fixture would be whatever the last person to touch
the producer's catalog happened to be holding, and a golden test would quietly
stop being golden. So the default mode compares, prints a unified diff, and exits
non-zero on drift; only `--regenerate` writes. That is the same
`--check`-means-don't-write contract `scripts/generate_icons.py` uses, with the
default flipped so the unsafe direction has to be typed.

**F14.** `tests/fixtures/pantry_items_snapshot.json` is a committed dump of all
178 `items` rows, and the golden tests read *it* rather than the producer's
`pantry_items.db`. Reading the live sibling database would make CI depend on
another repository's on-disk state and turn every catalog re-import into a CI
failure — a failure about the world rather than about this code, which is
precisely what teaches a team to ignore CI.

**What is and is not frozen.** The snapshot is the five identity-bearing
columns, as *stored*: `variants` is the producer's raw JSON text and `area` is
`null` where it is NULL. It is emphatically **not** pre-filtered, pre-derived,
or pre-normalized. `area` filtering, `family`, and `basename` are all
`build_snapshot()`'s job, and freezing their output here would (a) make the
snapshot's own `total_row_count` / `excluded_row_count` unreconstructable, and
(b) silently freeze whatever the filter was on the day it ran, so a later change
to the normalizer could never reach the golden test.

**Because those five columns are all `catalog_revision` hashes, the snapshot *is*
the revision.** The digest is printed on every run and belongs in the commit
message; it is deliberately *not* asserted in any test. A pinned digest turns
every producer re-import into a red suite, which is the exact failure F14 exists
to remove.

**Two jobs, one script.** `--job catalog` refreshes the snapshot from
`pantry_items.db`; `--job recipes` re-freezes `tests/fixtures/real_recipes/`
from the vault folder. The recipe notes are frozen the same way for the same
reason — the vault is canonical and CI must never read it — and they were
already byte-identical when this landed, so `--check` on that job is the proof
and `--regenerate` is a no-op until a note actually changes. The folder is copied
as the vault holds it: 16 recipe notes plus `Recipes.md`, the folder's own table
of contents, which is *not* one of the 16 (`tests/recipes/test_reader.py` asserts
both halves). This script never deletes a committed note — a note the vault
dropped is reported for a human to remove, not removed by a script.

**The third job is the `💵` parity gate (F1), and it is the only one that reads
both sides of a computation this app does not own.** The per-unit money math
exists three times: the `existingPantryValue` dataviewjs in `Pantry.md`,
`Helper/scripts/pantry_snapshot.py`, and `app.vault.pantry.untagged_inventory`.
Only the third is in this repository's test suite, so `--job stock` computes the
untagged, open, non-unit-parent total **twice** — once with the PWA's rule over
the note's bytes, once by running `pantry_snapshot.py --dry-run` in a
subprocess and parsing its `📦 N 项 · 💵 $X.XX` line — and **refuses to emit** a
refreshed `tests/fixtures/pantry_stock_math_parity.json` when the two disagree.
A parity break therefore blocks the regeneration instead of being merged
silently, which is §13.16's stated and *procedural* mitigation: it is caught at
regeneration time by a human, deliberately, and it is not caught automatically
and will not be.

That job is the one place in this script that shells out. It runs the helper
with `sys.executable` and a `--dry-run` flag, from a caller-chosen path, and
never writes to the vault: `pantry_snapshot.py` without `--dry-run` rewrites
`Logistics/库存/Pantry 快照.md`, and passing the flag is the whole reason this is
safe to run. The committed fixture records *both* sides' numbers and the note's
sha256, so a later reader can see what was compared rather than only what this
app believes. CI never runs this job — F14's rule applies to the live vault and
the live `Helper/` script as much as to the live catalog.

**Read-only on both inputs.** The catalog is opened through
`app.pantry.catalog.open_read_only()`, the same `file:<path>?mode=ro` URI plus
`PRAGMA query_only` the app itself uses, so a regeneration run cannot write the
producer's tracked file either (AGENTS.md #4). The vault is only ever read.

**The measurements are a second, independent gate.** Before emitting anything the
script re-derives the shape #8 measured — 178 rows, 176 candidates, 2 excluded
by `area`, the 3 populated `variants`, the 8 basename collisions with their exact
id lists, the 15 category codes and 8 families — and refuses to write if any of
them moved. A re-import is expected to move them, so the numbers live here as
literals a maintainer updates in the same commit as the golden tests: that is the
"call the change out" the issue asks for, done where a reviewer will see it. The
measured values are always printed first, so a refusal carries the new numbers
rather than only the old ones. `--accept-measurements` re-baselines them on
purpose, for the two-flag deliberate act a real catalog change is meant to be.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

_REPO_ROOT: Final = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:  # runnable as `python scripts/…` from anywhere
    sys.path.insert(0, str(_REPO_ROOT))

from app.pantry.catalog import build_snapshot, open_read_only  # noqa: E402
from app.pantry.stock import is_unit_row, join_stock  # noqa: E402
from app.vault.pantry import parse_pantry, untagged_inventory  # noqa: E402

#: The two frozen inputs and their live sources' defaults. `PANTRY_ITEMS_DB` and
#: `OBSIDIAN_VAULT_PATH` override the producer default, so a machine that keeps
#: the catalog or the vault elsewhere still regenerates the same bytes.
DEFAULT_DB: Final = Path(
    os.environ.get("PANTRY_ITEMS_DB")
    or Path.home() / "projects" / "wholefoods-to-pantry" / "assets" / "pantry_items.db"
)
DEFAULT_VAULT: Final = Path(
    os.environ.get("OBSIDIAN_VAULT_PATH") or Path.home() / "obsidian" / "syang"
)
DEFAULT_RECIPES_ROOT: Final = os.environ.get("RECIPES_ROOT") or "Hobbies/做饭/Recipes"

SNAPSHOT_PATH: Final = _REPO_ROOT / "tests" / "fixtures" / "pantry_items_snapshot.json"
RECIPES_FIXTURE_DIR: Final = _REPO_ROOT / "tests" / "fixtures" / "real_recipes"
#: F1's third frozen input: the agreed per-unit money total, and the two
#: implementations it was agreed between.
PARITY_PATH: Final = _REPO_ROOT / "tests" / "fixtures" / "pantry_stock_math_parity.json"

#: The vault-relative pantry note — the app's own `PANTRY_NOTE_RELATIVE`
#: default, repeated here so a regeneration run does not depend on this
#: repository's own `Settings` (which would need a full environment to build).
PANTRY_NOTE_RELATIVE: Final = "Logistics/库存/Pantry.md"
#: The sibling implementation of the same `💵` math.
DEFAULT_HELPER: Final = DEFAULT_VAULT / "Helper" / "scripts" / "pantry_snapshot.py"
#: `pantry_snapshot.py --dry-run` prints `📦 40 项 · 💵 $166.90 (2026-09-27 09:41)`.
#: Only the count and the amount are read; the trailing timestamp is the
#: snapshot's own clock and would make the output non-reproducible if pinned.
HELPER_OUTPUT: Final = re.compile(r"(\d+)\s*项\s*·\s*💵\s*\$([\d,]+\.\d{2})")

#: The Stock Join's measured shape on the same note, as of this commit. Same
#: discipline as `EXPECTED_*` above, for the same reason: a re-import or a
#: normalizer change that moves a golden expectation has to be a deliberate,
#: reviewed edit. 50 listed rows, 5 of them bare `k/N` units, so 45 product
#: lines: 3 exact, 40 basename, 2 override, 0 unresolved.
EXPECTED_STOCK_JOIN: Final[dict[str, int]] = {
    "listedRows": 50,
    "unitRows": 5,
    "lineCount": 45,
    "exactTier": 3,
    "basenameTier": 40,
    "overrideTier": 2,
    "unjoined": 0,
}

#: The five columns, in the order `_SELECT_ITEMS` projects them. Everything the
#: snapshot freezes is here, which is also exactly what `catalog_revision` hashes.
COLUMNS: Final = ("id", "canonical_name", "category", "variants", "area")

# --- The measured shape of the catalog, as of this commit -------------------
#
# Measured, not copied: ticket #8 measured the catalog and ticket #9 re-measured
# it from the same file and got the same numbers. They are literals here so that
# a regeneration which would move a golden expectation has to be a deliberate,
# reviewed edit rather than a rerun.

EXPECTED_TOTAL_ROWS: Final = 178
EXPECTED_CANDIDATE_ROWS: Final = 176
#: `(id, canonical_name, area)` of every row `area` removes, and the only two.
EXPECTED_EXCLUDED_ROWS: Final = (
    (49, "Acure Ultra Hydrating Shampoo", "Shampoo"),
    (50, "Dr.Reju-All PDRN Rejuvenating Cream", "Serum"),
)
#: The three rows whose `variants` is populated. Two of the three aliases are
#: just the long product name, which is why tier 3 is near-dead by design.
EXPECTED_POPULATED_VARIANT_IDS: Final = (52, 55, 169)
#: Every basename key holding more than one row, with the exact ids in it. A
#: `dict[str, CatalogRow]` would silently drop the loser of each of these.
EXPECTED_BASENAME_COLLISIONS: Final[dict[str, list[int]]] = {
    "小白菜心": [18, 106],
    "台湾旺旺浪味仙 熔岩辣起司口味": [27, 38],
    "韩国紫苏叶": [39, 105],
    "organic 1% milk": [90, 116],
    "优质白桃礼盒": [104, 128],
    "2026fifa世界杯限定联名薯片牛肉派味": [111, 124],
    "poland spring maine spring bottled water": [156, 165],
    "chocolate crepe": [168, 169],
}
#: Every Pantry Category code the catalog uses, and the family each belongs to.
EXPECTED_CATEGORY_CODES: Final[dict[str, str]] = {
    "1.1": "1.1",
    "1.1c": "1.1",
    "1.1d": "1.1",
    "1.1f": "1.1",
    "1.2": "1.2",
    "1.2d": "1.2",
    "1.2m": "1.2",
    "1.2s": "1.2",
    "1.2v": "1.2",
    "2": "2",
    "3": "3",
    "4": "4",
    "5": "5",
    "6": "6",
    "skip": "skip",
}


def read_items(db_path: Path) -> list[tuple[object, ...]]:
    """Every `items` row, through the app's own read-only opener.

    `open_read_only` rather than a `sqlite3.connect` of this script's own: it is
    the one read path the product is tested against, and reusing it means a
    regeneration run inherits `mode=ro` and `PRAGMA query_only` rather than
    re-arguing the guarantee. It also means a missing catalog raises here instead
    of creating an empty one.
    """
    connection = open_read_only(db_path)
    try:
        statement = (
            f"SELECT {', '.join(COLUMNS)} FROM items ORDER BY id"  # noqa: S608
        )
        return [tuple(row) for row in connection.execute(statement)]
    finally:
        connection.close()


def measure(rows: Sequence[Sequence[object]]) -> dict[str, object]:
    """Everything about the catalog this script is willing to freeze, as data.

    Built through `build_snapshot()` — the same projection the app uses — so the
    numbers below are read off the indexes the product actually hands out rather
    than recomputed by a second implementation that could disagree with it.
    """
    snapshot = build_snapshot(rows)
    collisions = {
        key: [row.id for row in group]
        for key, group in snapshot.by_basename.items()
        if len(group) > 1
    }
    categories = {
        row.category: row.family for row in snapshot.rows
    }
    excluded = [
        (record[0], record[1], record[4])
        for record in rows
        if record[4] is not None
    ]
    return {
        "total_row_count": snapshot.total_row_count,
        "candidate_row_count": len(snapshot.rows),
        "excluded_row_count": snapshot.excluded_row_count,
        "excluded_rows": sorted(excluded),
        "populated_variant_ids": sorted(
            row.id for row in snapshot.rows if row.variants
        ),
        "basename_collisions": collisions,
        "category_codes": categories,
        "catalog_revision": snapshot.catalog_revision,
    }


def expected_measurements() -> dict[str, object]:
    """The same shape as `measure()`, holding what this commit froze."""
    return {
        "total_row_count": EXPECTED_TOTAL_ROWS,
        "candidate_row_count": EXPECTED_CANDIDATE_ROWS,
        "excluded_row_count": len(EXPECTED_EXCLUDED_ROWS),
        "excluded_rows": sorted(EXPECTED_EXCLUDED_ROWS),
        "populated_variant_ids": list(EXPECTED_POPULATED_VARIANT_IDS),
        "basename_collisions": {
            key: list(value) for key, value in EXPECTED_BASENAME_COLLISIONS.items()
        },
        "category_codes": dict(EXPECTED_CATEGORY_CODES),
    }


#: Which keys are compared, and what they are called when they disagree. The
#: revision is absent on purpose: it is a function of the rows, so pinning it
#: would be pinning the other seven keys under a name nobody can read.
_COMPARED: Final = (
    "total_row_count",
    "candidate_row_count",
    "excluded_row_count",
    "excluded_rows",
    "populated_variant_ids",
    "basename_collisions",
    "category_codes",
)


def measurement_drift(
    measured: dict[str, object], expected: dict[str, object]
) -> list[str]:
    """One line per measurement that moved, naming the old and the new value."""
    return [
        f"{key}: was {expected[key]!r}, now {measured[key]!r}"
        for key in _COMPARED
        if measured[key] != expected[key]
    ]


def report_measurements(measured: dict[str, object]) -> None:
    """Print the measured shape, so a refusal below carries the new numbers."""
    print("measured:")
    for key in _COMPARED:
        value = measured[key]
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
        print(f"  {key} = {rendered}")
    print(f"  catalog_revision = {measured['catalog_revision']}")
    print()


def encode_snapshot(rows: Sequence[Sequence[object]]) -> str:
    """The snapshot file: one 5-tuple per line, ascending by `id`.

    One row per line is the whole reason this is not `json.dumps(indent=2)`: a
    regeneration is reviewed as a diff, and a single re-import must show up as a
    handful of changed lines rather than as a 1,200-line reindent. Still ordinary
    JSON — `json.load` reads it back, and `build_snapshot()` takes it unchanged.
    """
    body = ",\n".join(json.dumps(list(row), ensure_ascii=False) for row in rows)
    return f"[\n{body}\n]\n"


def unified(committed: str | None, candidate: str) -> list[str]:
    """The diff a reviewer would read, or `["(identical)"]`."""
    if committed == candidate:
        return ["(the committed snapshot is already identical)"]
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


# --- the catalog job --------------------------------------------------------


def run_catalog(
    db_path: Path,
    out_path: Path,
    *,
    regenerate: bool,
    accept_measurements: bool,
) -> int:
    """Freeze, or refuse to freeze, `pantry_items_snapshot.json`."""
    print(f"catalog source: {db_path}")
    if not db_path.is_file():
        print(
            f"no catalog at {db_path}; set PANTRY_ITEMS_DB or pass --db",
            file=sys.stderr,
        )
        return 1

    rows = read_items(db_path)
    measured = measure(rows)
    report_measurements(measured)

    drift = measurement_drift(measured, expected_measurements())
    committed = out_path.read_text(encoding="utf-8") if out_path.is_file() else None
    candidate = encode_snapshot(rows)
    lines = unified(committed, candidate)
    print(f"diff {out_path}:")
    for line in lines:
        print(f"  {line}")
    print()

    if drift and not accept_measurements:
        print("the catalog's measured shape moved; refusing to freeze it:", file=sys.stderr)
        for line in drift:
            print(f"  {line}", file=sys.stderr)
        print(
            "review the change, update EXPECTED_* in this script and the golden "
            "tests in the same commit, then rerun with --accept-measurements",
            file=sys.stderr,
        )
        return 1
    if drift:
        print("re-baselining the measurements on request:")
        for line in drift:
            print(f"  {line}")

    if not regenerate:
        if committed == candidate:
            print("up to date; nothing to do (pass --regenerate to write)")
            return 0
        print("would write; pass --regenerate to do it", file=sys.stderr)
        return 1

    if committed == candidate:
        print("up to date; wrote nothing")
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(candidate, encoding="utf-8")
    print(f"wrote {out_path} ({len(rows)} rows, {len(candidate)} bytes)")
    print()
    print("commit message material — the numbers a reviewer needs, because they are")
    print("what moved:")
    print(
        f"  rows {measured['total_row_count']}, candidates "
        f"{measured['candidate_row_count']}, excluded by area "
        f"{measured['excluded_row_count']}, with variants "
        f"{measured['populated_variant_ids']}"
    )
    print(f"  basename collisions: {len(measured['basename_collisions'])}")
    print(f"  category codes: {len(measured['category_codes'])}")
    print(f"  catalog_revision: {measured['catalog_revision']}  <- in the message, never in a test")
    return 0


# --- the recipes job --------------------------------------------------------


def run_recipes(vault: Path, recipes_root: str, out_dir: Path, *, regenerate: bool) -> int:
    """Verify, or re-freeze, `tests/fixtures/real_recipes/`."""
    source_dir = vault / recipes_root
    print(f"recipes source: {source_dir}")
    if not source_dir.is_dir():
        print(f"no recipe folder at {source_dir}; set OBSIDIAN_VAULT_PATH", file=sys.stderr)
        return 1

    live = {path.name: path for path in sorted(source_dir.glob("*.md"))}
    frozen = {path.name: path for path in sorted(out_dir.glob("*.md"))} if out_dir.is_dir() else {}

    problems: list[str] = []
    for name, source in live.items():
        target = frozen.get(name)
        if target is None:
            problems.append(f"{name}: in the vault, not frozen here")
        elif target.read_bytes() != source.read_bytes():
            problems.append(f"{name}: frozen bytes differ from the vault")
    for name in frozen:
        if name not in live:
            # Reported, never removed. A note the vault dropped is a decision
            # about the vault, and a script that deletes committed data on a
            # regeneration run is the failure mode this whole file is about.
            problems.append(f"{name}: frozen here, absent from the vault (delete it by hand)")

    print(f"vault notes: {len(live)}   frozen notes: {len(frozen)}")
    for problem in problems:
        print(f"  {problem}")

    if not regenerate:
        if problems:
            print("would re-freeze; pass --regenerate to do it", file=sys.stderr)
            return 1
        print("every frozen note is byte-identical to the vault")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, source in live.items():
        target = out_dir / name
        if target.is_file() and target.read_bytes() == source.read_bytes():
            continue
        shutil.copyfile(source, target)
        print(f"wrote {target}")
        written += 1
    print(f"froze {len(live)} notes, {written} written")
    return 1 if any("absent from the vault" in problem for problem in problems) else 0


# --- the stock job: F1's per-unit 💵 parity gate ----------------------------


def pwa_stock_figure(note: Path) -> tuple[int, float, int]:
    """`(count, total, excluded_parents)` from the PWA's own rule.

    `untagged_inventory()` is the one implementation in this repository, and
    `tests/pantry/test_stock_math.py` pins its three shape cases. Reading it
    through the public parser rather than re-deriving the arithmetic is the
    point: a second copy of the math in this script would be a third thing to
    keep in parity, and the whole job exists to compare *two* sides, not three.
    """
    figure = untagged_inventory(parse_pantry(note.read_bytes()))
    return figure.count, figure.total, figure.excluded_parents


def helper_stock_figure(helper: Path) -> tuple[int, float]:
    """`(count, total)` from `pantry_snapshot.py --dry-run`, in a subprocess.

    The helper reaches into the vault by absolute path derived from its own
    location, so it is *run* rather than imported — importing it would execute
    nothing useful and re-implementing its `compute()` would make the comparison
    meaningless (a copy of the thing it is checking proves nothing). `--dry-run`
    is not optional here: without it the script rewrites
    `Logistics/库存/Pantry 快照.md`, and a fixture-regeneration tool must never
    write to the vault.
    """
    result = subprocess.run(
        [sys.executable, str(helper), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{helper} --dry-run exited {result.returncode}: {result.stderr.strip()}"
        )
    found = HELPER_OUTPUT.search(result.stdout)
    if found is None:
        raise RuntimeError(f"could not read a figure out of {helper}: {result.stdout.strip()!r}")
    return int(found.group(1)), float(found.group(2).replace(",", ""))


def encode_parity(
    note: Path,
    count: int,
    total: float,
    excluded_parents: int,
    helper: tuple[int, float],
    helper_label: str,
) -> str:
    """The parity fixture, with **both** sides recorded and the note pinned.

    `noteSha256` is what makes the fixture mean something later: a total is only
    comparable against the bytes it came from, and the note is edited constantly
    from Obsidian. `helperPath` is recorded vault-relative so a reader knows
    which of the two other implementations was run.
    """
    payload = {
        "generatedFrom": PANTRY_NOTE_RELATIVE,
        "noteSha256": hashlib.sha256(note.read_bytes()).hexdigest(),
        "count": count,
        "total": round(total, 2),
        "excludedParents": excluded_parents,
        "helperPath": helper_label,
        "helperCount": helper[0],
        "helperTotal": round(helper[1], 2),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _render_source(path: Path, *roots: Path) -> str:
    """A path as the shortest of `roots` that contains it, else as it is.

    A regeneration run on the developer's own machine must not write their
    absolute home directory into a committed fixture. The vault is tried first
    because `Helper/scripts/pantry_snapshot.py` is the meaningful name there,
    and the repo second so a path into this checkout still reads as relative.
    """
    for root in roots:
        if _is_within(path, root):
            return str(path.relative_to(root))
    return str(path)


def run_stock(
    vault: Path,
    helper: Path,
    out_path: Path,
    *,
    regenerate: bool,
    explicit: bool,
    db_path: Path | None = None,
) -> int:
    """Verify parity, then freeze — or refuse to freeze — the money figure."""
    note = vault / PANTRY_NOTE_RELATIVE
    print(f"stock source: {note}")
    print(f"parity other side: {helper}")
    for source in (note, helper):
        if not source.is_file():
            message = f"no {source} to compare against"
            if explicit:
                # Asked for this job by name and it cannot run: that is an
                # error, not a skip. Answering "fine" would make `--job stock`
                # look like it verified something.
                print(f"{message}; set OBSIDIAN_VAULT_PATH / --helper", file=sys.stderr)
                return 1
            # Part of the default `all` sweep on a machine that has no vault:
            # a notice, because a hard failure here would train everyone to
            # stop reading this tool's output.
            print(f"{message}; skipping the stock job (pass --job stock to require it)")
            return 0

    count, total, excluded_parents = pwa_stock_figure(note)
    mine = (count, round(total, 2))
    theirs = helper_stock_figure(helper)
    theirs = (theirs[0], round(theirs[1], 2))
    print(
        f"  PWA:            {mine[0]} 项 · 💵 ${mine[1]:.2f}"
        f"  (excluded parents {excluded_parents})"
    )
    print(f"  pantry_snapshot: {theirs[0]} 项 · 💵 ${theirs[1]:.2f}")

    if db_path is not None and db_path.is_file():
        measured = measure_stock_join(note, db_path)
        print("  stock join measured:")
        for key, value in measured.items():
            print(f"    {key} = {value}")
        if measured != EXPECTED_STOCK_JOIN:
            print(
                "the stock join's measured shape moved; refusing to freeze: update "
                "EXPECTED_STOCK_JOIN and the golden tests in the same commit",
                file=sys.stderr,
            )
            return 1

    if mine != theirs:
        print(
            "the per-unit 💵 math disagrees between this app and pantry_snapshot.py; "
            "refusing to freeze a figure that is not agreed",
            file=sys.stderr,
        )
        print(f"  PWA: {mine[0]} 项 · ${mine[1]:.2f}", file=sys.stderr)
        print(f"  helper: {theirs[0]} 项 · ${theirs[1]:.2f}", file=sys.stderr)
        return 1

    candidate = encode_parity(
        note, count, total, excluded_parents, theirs, _render_source(helper, vault, _REPO_ROOT)
    )
    committed = out_path.read_text(encoding="utf-8") if out_path.is_file() else None
    print(f"diff {out_path}:")
    for line in unified(committed, candidate):
        print(f"  {line}")
    print()
    if not regenerate:
        if committed == candidate:
            print("up to date; nothing to do (pass --regenerate to write)")
            return 0
        print("would write; pass --regenerate to do it", file=sys.stderr)
        return 1
    if committed == candidate:
        print("up to date; wrote nothing")
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(candidate, encoding="utf-8")
    print(f"wrote {out_path} ({mine[0]} 项 · ${mine[1]:.2f}, agreed with {helper.name})")
    return 0


def measure_stock_join(note: Path, db_path: Path) -> dict[str, int]:
    """The Stock Join's measured shape on `note`, as data.

    The other thing a regeneration has to re-derive: whether the join still
    carries the note the way this commit's golden test says it does. A new
    catalog row, a lexicon change, or a normalizer change can all move those
    numbers without anything being wrong, which is exactly why they are literals
    a maintainer updates in the same commit as the tests.
    """
    catalog = build_snapshot(read_items(db_path))
    sections = parse_pantry(note.read_bytes()).sections
    join = join_stock(catalog, sections)
    return {
        "listedRows": sum(len(section.items) for section in sections),
        "unitRows": sum(
            1 for section in sections for item in section.items if is_unit_row(item.text)
        ),
        "lineCount": join.line_count,
        "exactTier": join.exact_tier_count,
        "basenameTier": join.basename_tier_count,
        "overrideTier": join.override_tier_count,
        "unjoined": join.unjoined_count,
    }


# --- the CLI ----------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Regenerate the frozen test inputs. The default writes nothing: it "
            "prints the diff and exits non-zero on drift."
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
        help="write the snapshot / re-freeze the notes after reviewing the diff",
    )
    parser.add_argument(
        "--job",
        choices=("all", "catalog", "recipes", "stock"),
        default="all",
        help="which frozen input to work on (default: all three)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help="the producer's pantry_items.db (default: $PANTRY_ITEMS_DB)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=SNAPSHOT_PATH,
        help="the snapshot file to compare against / write",
    )
    parser.add_argument(
        "--parity-out",
        type=Path,
        default=PARITY_PATH,
        help="the frozen per-unit money parity file to compare against / write",
    )
    parser.add_argument(
        "--helper",
        type=Path,
        default=DEFAULT_HELPER,
        help="the sibling pantry_snapshot.py the 💵 math is compared against",
    )
    parser.add_argument(
        "--vault",
        type=Path,
        default=DEFAULT_VAULT,
        help="the Obsidian vault (default: $OBSIDIAN_VAULT_PATH)",
    )
    parser.add_argument(
        "--recipes-root",
        default=DEFAULT_RECIPES_ROOT,
        help="the recipes folder, relative to the vault (default: $RECIPES_ROOT)",
    )
    parser.add_argument(
        "--recipes-out",
        type=Path,
        default=RECIPES_FIXTURE_DIR,
        help="the frozen recipe-notes directory",
    )
    parser.add_argument(
        "--accept-measurements",
        action="store_true",
        help="re-baseline the measured shape on purpose; requires --regenerate",
    )
    args = parser.parse_args(argv)

    if args.accept_measurements and not args.regenerate:
        parser.error("--accept-measurements only means something with --regenerate")

    if args.job in ("all", "catalog"):
        status = run_catalog(
            args.db,
            args.out,
            regenerate=args.regenerate,
            accept_measurements=args.accept_measurements,
        )
        if status:
            return status

    if args.job in ("all", "recipes"):
        status = run_recipes(
            args.vault, args.recipes_root, args.recipes_out, regenerate=args.regenerate
        )
        if status:
            return status

    if args.job in ("all", "stock"):
        status = run_stock(
            args.vault,
            args.helper,
            args.parity_out,
            regenerate=args.regenerate,
            explicit=args.job == "stock",
            db_path=args.db,
        )
        if status:
            return status

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

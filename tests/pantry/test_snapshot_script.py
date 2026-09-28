"""`scripts/snapshot_pantry_catalog.py`: the deliberate-regeneration contract.

Run alone:  .venv/bin/python -m pytest tests/pantry/test_snapshot_script.py -q

The script is the only thing in this repository that writes the golden fixtures,
so its behaviour when it is *wrong* matters more than its behaviour when it is
right. What is asserted here:

1. **The default writes nothing.** A run with no flags compares, prints a diff,
   and exits non-zero. A stray invocation — a muscle-memory `python
   scripts/snapshot_pantry_catalog.py` from an unrelated afternoon — must not be
   able to rewrite a committed fixture, because a golden test that anyone can
   quietly re-baseline is a golden test that has stopped meaning anything.
2. **Writing needs two flags after a real catalog change.** `--regenerate` alone
   still refuses while the measured shape has moved, because a moved measurement
   is a change to every golden expectation in the suite and belongs in a reviewed
   commit, not in a rerun. `--accept-measurements` is the second, deliberate
   half.
3. **The catalog is opened read-only.** Proved by chmod-ing the file to `0444`
   and requiring the run to succeed: a writable `sqlite3.connect` cannot open a
   file its own user has no write bit on, so a passing run is only possible
   through `open_read_only()`.
4. **The recipes job verifies, and never deletes.** A note the vault gained is
   frozen on request; a note the vault dropped is *reported* and left alone,
   because a regeneration script that removes committed data on its own initiative
   is the failure this file is about.
5. **The script's own expected numbers are the committed snapshot's numbers.**
   Compared function to function, so editing `EXPECTED_*` without regenerating
   fails here instead of waiting for the next real re-import to notice.
6. **The `💵` parity gate compares two implementations and refuses to freeze a
   disagreement** — the only job in this script that reads something this
   repository does not own. The "other side" is a stub script written into
   `tmp_path`, so the refusal is proved with no vault and no `Helper/`
   directory, and the production script's own `--dry-run` guarantee is asserted
   structurally (the flag is passed, and it is the flag that stops the real
   script rewriting the user's snapshot note).

Everything runs against `tmp_path` copies, so this file needs no sibling
repository, no vault, and no network — it is a CI-resident test of a developer
tool, which is the only way a safety property in a script gets enforced at all.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

import pytest

from app.pantry.catalog import build_snapshot

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
SCRIPT: Final = REPO_ROOT / "scripts" / "snapshot_pantry_catalog.py"
SNAPSHOT: Final = REPO_ROOT / "tests" / "fixtures" / "pantry_items_snapshot.json"
PARITY: Final = REPO_ROOT / "tests" / "fixtures" / "pantry_stock_math_parity.json"
PANTRY_NOTE: Final = REPO_ROOT / "tests" / "fixtures" / "pantry" / "Pantry.md"

# The producer's schema, three columns wide on purpose: the point of these
# fixtures is the script's *contract*, and a 178-row catalog is not needed to
# show that a refusal refuses.
_SCHEMA: Final = """
CREATE TABLE items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name TEXT NOT NULL UNIQUE,
    category       TEXT NOT NULL,
    variants       TEXT DEFAULT '[]',
    first_seen     TEXT,
    last_seen      TEXT,
    order_count    INTEGER DEFAULT 1,
    area           TEXT,
    last_price     REAL
)
"""
_SEED: Final = (
    (1, "Yuzu Tart", "1.1", "[]", None),
    (2, "Bell & Evans Chicken Breast, 8 Oz", "1.1", "[]", None),
    (3, "Mushroom Dried Morel Mushrooms", "1.2v", "[]", None),
    (4, "Acure Ultra Hydrating Shampoo", "6", "[]", "Shampoo"),
    (5, "Matchaful Original Matcha Granola 8oz", "3", '["Matchaful Granola"]', None),
)


def _write_db(path: Path, rows: tuple[tuple[Any, ...], ...] = _SEED) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(_SCHEMA)
        connection.executemany(
            "INSERT INTO items (id, canonical_name, category, variants, area)"
            " VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()
    return path


def _run(
    *args: str,
    db: Path,
    out: Path,
    job: str = "catalog",
    vault: Path | None = None,
    helper: Path | None = None,
    parity_out: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """The script as a developer runs it: a fresh process, `cwd` at the repo root.

    Subprocess rather than an in-process call because the property under test is
    the *command line*: which flag combination writes and which does not. An
    in-process `main([...])` would also exercise the argparse branch, but the
    exit code a shell sees is the contract a human or a pre-commit hook reads.

    `--job` is always passed, never left to the `all` default, and that is the
    point: `all` runs the recipes and stock jobs too, so a run without it
    inherits the vault — and a machine without the vault would fail a catalog
    assertion for a reason that has nothing to do with the catalog. The CLI keeps
    `all` as its default because "are my frozen inputs current?" is the question a
    developer usually has; a test asks a narrower one and has to say so.
    """
    command = [
        sys.executable,
        str(SCRIPT),
        "--job",
        job,
        "--db",
        str(db),
        "--out",
        str(out),
    ]
    if vault is not None:
        command += ["--vault", str(vault)]
    if helper is not None:
        command += ["--helper", str(helper)]
    if parity_out is not None:
        command += ["--parity-out", str(parity_out)]
    command += list(args)
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.fixture
def small_db(tmp_path: Path) -> Path:
    """Five rows — a catalog whose measured shape is *not* the frozen one."""
    return _write_db(tmp_path / "small" / "pantry_items.db")


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """The committed 178 rows, as a real SQLite file.

    The frozen shape is the only shape the script will write without
    `--accept-measurements`, so this is what makes "the default refuses" and "a
    regeneration reproduces the committed bytes" testable at all.
    """
    records = [tuple(row) for row in json.loads(SNAPSHOT.read_text(encoding="utf-8"))]
    return _write_db(tmp_path / "full" / "pantry_items.db", tuple(records))


@pytest.fixture
def out(tmp_path: Path) -> Path:
    return tmp_path / "pantry_items_snapshot.json"


@pytest.fixture(scope="module")
def module() -> Any:
    """The script, imported for its pure functions.

    `spec_from_file_location` rather than an import of a `scripts.` package:
    there is no such package, and this file needs the arithmetic — not a second
    copy of it.
    """
    spec = importlib.util.spec_from_file_location("snapshot_pantry_catalog", SCRIPT)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


# --- 1. The default writes nothing -----------------------------------------


def test_a_default_run_refuses_to_write_and_exits_non_zero(db: Path, out: Path) -> None:
    result = _run(db=db, out=out)
    assert result.returncode == 1, result.stdout
    assert not out.exists(), "the default mode wrote a fixture"
    assert "would write" in result.stderr


def test_regenerating_from_the_committed_rows_reproduces_the_committed_bytes(
    db: Path, out: Path
) -> None:
    """The reproducibility claim, executed.

    The 178 rows are read back out of the committed snapshot, written into a real
    SQLite file, and regenerated from there. The result has to be the committed
    file, byte for byte — otherwise the fixture is not reproducible from its own
    recorded source and a re-import review is comparing against something nobody
    can regenerate.
    """
    assert _run("--regenerate", db=db, out=out).returncode == 0
    assert out.read_bytes() == SNAPSHOT.read_bytes()


def test_a_stray_run_cannot_rewrite_a_committed_fixture(db: Path, out: Path) -> None:
    """The safety property, stated as damage prevented.

    A committed file is put in place, a run that would change it happens, and the
    bytes afterwards are the bytes before. The CLI has no path from "something
    changed" to "committed" that does not go through `--regenerate`.
    """
    out.write_text('[1, "Committed", "1.1", "[]", null]\n', encoding="utf-8")
    before = out.read_bytes()
    assert _run(db=db, out=out).returncode == 1
    assert out.read_bytes() == before
    assert _run("--regenerate", db=db, out=out).returncode == 0
    assert out.read_bytes() != before


def test_an_identical_snapshot_is_reported_as_up_to_date(db: Path, out: Path) -> None:
    assert _run("--regenerate", db=db, out=out).returncode == 0
    frozen = out.read_bytes()
    result = _run(db=db, out=out)
    assert result.returncode == 0, result.stdout
    assert out.read_bytes() == frozen
    assert "up to date" in result.stdout


# --- 2. Writing takes two deliberate flags ---------------------------------


def test_regenerate_alone_still_refuses_after_a_real_catalog_change(
    small_db: Path, out: Path
) -> None:
    result = _run("--regenerate", db=small_db, out=out)
    assert result.returncode == 1
    assert not out.exists()
    assert "was 178, now 5" in result.stderr, result.stderr
    assert "--accept-measurements" in result.stderr


def test_the_two_flags_together_write_and_print_the_commit_message(
    small_db: Path, out: Path
) -> None:
    result = _run("--regenerate", "--accept-measurements", db=small_db, out=out)
    assert result.returncode == 0, result.stderr
    assert out.is_file()
    # The numbers a reviewer needs are printed, and the digest is offered as
    # commit-message material rather than as something to assert on.
    assert "rows 5, candidates 4, excluded by area 1" in result.stdout
    assert "catalog_revision: sha256:" in result.stdout
    assert "never in a test" in result.stdout


def test_accept_measurements_without_regenerate_is_a_usage_error(db: Path, out: Path) -> None:
    result = _run("--accept-measurements", db=db, out=out)
    assert result.returncode == 2, result.stdout
    assert "--accept-measurements only means something with --regenerate" in result.stderr


def test_a_missing_catalog_is_an_error_and_not_an_empty_snapshot(
    tmp_path: Path, out: Path
) -> None:
    result = _run("--regenerate", db=tmp_path / "absent.db", out=out)
    assert result.returncode == 1
    assert not out.exists()
    assert "no catalog at" in result.stderr


# --- 3. The producer's tracked file is never written -----------------------


def test_the_catalog_is_read_through_a_read_only_handle(db: Path, out: Path) -> None:
    """Proved by removing the owner's write bit, not by reading the source.

    `open_read_only()` is the product's read path and `PantryCatalog` already
    tests that it cannot write; what is untested is whether a *regeneration* uses
    it. A `0444` file cannot be opened `O_RDWR` by its own owner, so a run that
    succeeds against one is necessarily a read-only open — and the bytes are
    compared afterwards so a partial write could not pass either.
    """
    before = db.read_bytes()
    os.chmod(db, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    try:
        result = _run("--regenerate", db=db, out=out)
        assert result.returncode == 0, result.stderr
    finally:
        os.chmod(db, stat.S_IRUSR | stat.S_IWUSR)
    assert db.read_bytes() == before


def test_the_script_routes_through_the_products_own_read_only_opener() -> None:
    """Structural, and about the *code* rather than the prose.

    Parsed rather than grepped so this sentence — and the docstring above it —
    cannot satisfy the assertion by mentioning the thing it forbids. The script
    has no `sqlite3` import at all: if the shared opener were bypassed, the
    module would have to grow one.
    """
    imported: set[str] = set()
    for node in ast.walk(ast.parse(SCRIPT.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "sqlite3" not in imported
    assert "app.pantry.catalog" in imported


# --- 4. The recipes job verifies, and never deletes ------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    notes = tmp_path / "vault" / "Hobbies" / "做饭" / "Recipes"
    notes.mkdir(parents=True)
    (notes / "盐焗鸡.md").write_text("---\nname: 盐焗鸡\n---\n", encoding="utf-8")
    (notes / "番茄炒蛋.md").write_text("---\nname: 番茄炒蛋\n---\n", encoding="utf-8")
    return tmp_path / "vault"


def _frozen_dir(tmp_path: Path, names: tuple[str, ...]) -> Path:
    directory = tmp_path / "frozen"
    directory.mkdir()
    for name in names:
        (directory / name).write_text("stale\n", encoding="utf-8")
    return directory


def test_a_note_the_vault_gained_is_reported_and_not_frozen_without_the_flag(
    db: Path, out: Path, vault: Path, tmp_path: Path
) -> None:
    frozen = _frozen_dir(tmp_path, ("盐焗鸡.md",))
    result = _run(
        "--recipes-out", str(frozen), db=db, out=out, job="recipes", vault=vault
    )
    assert result.returncode == 1
    assert "番茄炒蛋.md: in the vault, not frozen here" in result.stdout
    assert "would re-freeze" in result.stderr
    assert sorted(path.name for path in frozen.glob("*.md")) == ["盐焗鸡.md"]


def test_regenerating_the_recipes_copies_bytes_and_reports_rather_than_deletes(
    db: Path, out: Path, vault: Path, tmp_path: Path
) -> None:
    frozen = _frozen_dir(tmp_path, ("盐焗鸡.md", "_deleted_upstream.md"))
    result = _run(
        "--regenerate",
        "--recipes-out",
        str(frozen),
        db=db,
        out=out,
        job="recipes",
        vault=vault,
    )
    assert result.returncode == 1, "a stale frozen note is a problem to report"
    assert "_deleted_upstream.md: frozen here, absent from the vault" in result.stdout
    assert (frozen / "_deleted_upstream.md").read_text(encoding="utf-8") == "stale\n"
    for name in ("盐焗鸡.md", "番茄炒蛋.md"):
        assert (frozen / name).read_bytes() == (
            vault / "Hobbies" / "做饭" / "Recipes" / name
        ).read_bytes()


def test_the_recipes_job_is_clean_when_every_note_matches(
    db: Path, out: Path, vault: Path, tmp_path: Path
) -> None:
    notes = vault / "Hobbies" / "做饭" / "Recipes"
    frozen = _frozen_dir(tmp_path, ("盐焗鸡.md", "番茄炒蛋.md"))
    for name in ("盐焗鸡.md", "番茄炒蛋.md"):
        (frozen / name).write_bytes((notes / name).read_bytes())
    result = _run(
        "--recipes-out", str(frozen), db=db, out=out, job="recipes", vault=vault
    )
    assert result.returncode == 0, result.stdout
    assert "every frozen note is byte-identical to the vault" in result.stdout


def test_a_missing_vault_folder_is_an_error_not_a_silent_pass(
    db: Path, out: Path, tmp_path: Path
) -> None:
    frozen = _frozen_dir(tmp_path, ())
    result = _run(
        "--recipes-out",
        str(frozen),
        db=db,
        out=out,
        job="recipes",
        vault=tmp_path / "no-such-vault",
    )
    assert result.returncode == 1
    assert "no recipe folder at" in result.stderr


# --- 5. The committed fixture and the script agree -------------------------


def test_the_scripts_expected_numbers_are_the_committed_snapshots(
    module: Any,
) -> None:
    """`EXPECTED_*` cannot drift away from the artifact it describes.

    The script is the only place the measured shape is written down twice — once
    as literals, once as the committed file. Comparing them means a hand-edited
    constant fails here, in CI, instead of quietly disagreeing with the fixture
    until the next genuine re-import runs the script and trips over it.
    """
    records = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert module.measurement_drift(module.measure(records), module.expected_measurements()) == []


def test_the_committed_real_recipe_notes_are_the_vault_folder(
    module: Any, tmp_path: Path
) -> None:
    """The 17 files this script would freeze, proven so rather than assumed.

    Ticket #11 copied the folder; nothing recorded *where from* or that the copy
    was faithful, so the claim was unreproducible. This is the claim, executed:
    the recipe job is pointed at a scratch copy of the committed fixture and at
    the real vault, and the two must agree byte for byte. It skips without the
    vault, which is the honest answer — the committed bytes are still gated in CI
    by `tests/recipes/test_reader.py`'s 16-note count.
    """
    vault = Path(os.environ.get("OBSIDIAN_VAULT_PATH") or Path.home() / "obsidian" / "syang")
    source = vault / module.DEFAULT_RECIPES_ROOT
    if not source.is_dir():
        pytest.skip(f"no vault at {source}; the frozen notes are gated by test_reader.py instead")
    frozen = REPO_ROOT / "tests" / "fixtures" / "real_recipes"
    result = module.run_recipes(vault, module.DEFAULT_RECIPES_ROOT, frozen, regenerate=False)
    assert result == 0


def test_the_snapshot_the_script_writes_is_the_shape_build_snapshot_takes(
    small_db: Path, out: Path
) -> None:
    """The round trip the whole design rests on: file in, `CatalogSnapshot` out.

    `build_snapshot()` is public and connection-free precisely so a committed
    JSON snapshot can stand in for `PantryCatalog.snapshot()`. If the encoding
    drifted — parsed `variants`, a pre-filtered `area`, an extra column — this
    would be where it showed, with the fixture's own numbers to compare against.
    """
    assert _run("--regenerate", "--accept-measurements", db=small_db, out=out).returncode == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    direct = build_snapshot(_SEED)
    assert build_snapshot(written) == direct
    assert direct.total_row_count == 5
    assert direct.excluded_row_count == 1
    assert [row.id for row in direct.by_variant["matchaful granola"]] == [5]


def test_the_encoding_is_one_row_per_line_so_a_reimport_diffs_readably(
    module: Any,
) -> None:
    """The formatting is a review affordance, so it is asserted.

    A whole-catalog re-import has to be reviewable as a handful of changed lines.
    Re-indenting 178 rows into `json.dumps(indent=2)` would bury the one row that
    moved under 1,200 lines of whitespace change, which is the same "silent
    regeneration" failure in a different costume.
    """
    text = module.encode_snapshot(_SEED)
    assert text.startswith("[\n")
    assert text.endswith("\n]\n")
    assert len(text.splitlines()) == len(_SEED) + 2
    assert json.loads(text) == [list(row) for row in _SEED]


def test_the_diff_is_printed_rather_than_assumed(module: Any) -> None:
    assert module.unified("a\n", "a\n") == ["(the committed snapshot is already identical)"]
    diff = module.unified("a\nb\n", "a\nc\n")
    assert any(line.startswith("-b") for line in diff)
    assert any(line.startswith("+c") for line in diff)
    assert module.unified(None, "a\n")




# --- 6. The 💵 parity gate ---------------------------------------------------
#
# The other two implementations of the per-unit math live outside this
# repository, so this job is the whole of §13.16's procedural mitigation. Its
# test therefore has to prove the *refusal*, not the arithmetic: a gate that
# writes on disagreement is the failure F1's parity clause exists to prevent.
#
# The "other side" is a stub. It prints exactly what the real
# `pantry_snapshot.py --dry-run` prints, so the parsing and the comparison are
# the script's own; what it does not do is read the vault, which is what keeps
# this file runnable in CI (F14's rule, extended to the note and the Helper
# script).


HELPER_STUB: Final = '''#!/usr/bin/env python3
"""A stand-in for `Helper/scripts/pantry_snapshot.py`, for the parity gate.

Prints the same line the real script's `--dry-run` prints. The real one is not
used here because it resolves the vault from its own `__file__`, and a CI test
must not read the user's pantry.
"""
import sys

if "--dry-run" not in sys.argv:
    raise SystemExit("this stub only implements --dry-run")
print("📦 {count} 项 · 💵 ${total:.2f} (2026-09-27 09:41)")
'''


@pytest.fixture
def parity_vault(tmp_path: Path) -> Path:
    """A vault holding a byte-identical copy of the frozen pantry note."""
    vault = tmp_path / "parity-vault"
    target = vault / "Logistics" / "库存" / "Pantry.md"
    target.parent.mkdir(parents=True)
    target.write_bytes(PANTRY_NOTE.read_bytes())
    return vault


def _stub_helper(tmp_path: Path, count: int, total: float) -> Path:
    path = tmp_path / "pantry_snapshot_stub.py"
    path.write_text(HELPER_STUB.format(count=count, total=total), encoding="utf-8")
    return path


def _run_stock(
    *args: str, db: Path, vault: Path, helper: Path, out: Path
) -> subprocess.CompletedProcess[str]:
    return _run(
        *args,
        db=db,
        out=out,
        job="stock",
        vault=vault,
        helper=helper,
        parity_out=out,
    )


def test_the_stock_job_refuses_to_freeze_when_the_two_implementations_disagree(
    tmp_path: Path, db: Path, parity_vault: Path, out: Path
) -> None:
    """The load-bearing behaviour: disagreement blocks the write, not warns it.

    The stub reports one cent less than the PWA computes. That is the smallest
    possible divergence and the most likely to slip through a comparison with a
    tolerance in it, which is why the tolerance is zero and why the refusal is
    asserted on the *file* — the fixture must not exist afterwards, whatever the
    exit code says.
    """
    helper = _stub_helper(tmp_path, 40, 166.89)
    result = _run_stock("--regenerate", db=db, vault=parity_vault, helper=helper, out=out)
    assert result.returncode == 1, result.stdout
    assert "disagrees" in result.stderr
    assert "$166.89" in result.stderr and "$166.90" in result.stderr
    assert not out.exists(), "a parity break still wrote the fixture"


def test_the_stock_job_agrees_and_freezes_both_sides(
    tmp_path: Path, db: Path, parity_vault: Path, out: Path
) -> None:
    """Agreement writes a fixture recording *both* numbers and the note's sha256.

    Both sides, not just this app's: the other implementation is not in this
    repository's test suite, so the number it reported is the only surviving
    record of what was compared against. The sha is what makes it comparable at
    all — a total is a claim about particular bytes of a note that is edited from
    Obsidian constantly.
    """
    helper = _stub_helper(tmp_path, 40, 166.90)
    written_run = _run_stock("--regenerate", db=db, vault=parity_vault, helper=helper, out=out)
    assert written_run.returncode == 0, written_run.stderr
    written = json.loads(out.read_text(encoding="utf-8"))
    assert (written["count"], written["total"], written["excludedParents"]) == (40, 166.9, 4)
    assert (written["helperCount"], written["helperTotal"]) == (40, 166.9)
    assert written["noteSha256"] == hashlib.sha256(PANTRY_NOTE.read_bytes()).hexdigest()
    assert "40 项" in _run_stock(db=db, vault=parity_vault, helper=helper, out=out).stdout
    # The second run, with no flags, must find nothing to do.
    again = _run_stock(db=db, vault=parity_vault, helper=helper, out=out)
    assert again.returncode == 0
    assert "up to date" in again.stdout


def test_the_stock_job_writes_nothing_by_default(
    tmp_path: Path, db: Path, parity_vault: Path, out: Path
) -> None:
    """Same contract as the catalog job: the default mode compares and refuses."""
    helper = _stub_helper(tmp_path, 40, 166.90)
    result = _run_stock(db=db, vault=parity_vault, helper=helper, out=out)
    assert result.returncode == 1, result.stdout
    assert not out.exists()
    assert "would write" in result.stderr


def test_a_stray_stock_run_cannot_rewrite_the_committed_parity_fixture(
    tmp_path: Path, db: Path, parity_vault: Path
) -> None:
    """The committed file is protected exactly as the catalog snapshot is.

    A run that would change the committed bytes — and a *disagreeing* helper on
    top of that, so the two guards are tested together — leaves the bytes alone.
    """
    committed = tmp_path / "committed.json"
    committed.write_text(PARITY.read_text(encoding="utf-8"), encoding="utf-8")
    before = committed.read_bytes()
    disagreeing = _run_stock(
        "--regenerate",
        db=db,
        vault=parity_vault,
        helper=_stub_helper(tmp_path, 40, 1.0),
        out=committed,
    )
    assert disagreeing.returncode == 1
    assert committed.read_bytes() == before


def test_the_stock_job_refuses_when_the_join_shape_moved(db: Path, parity_vault: Path) -> None:
    """A five-row catalog makes the join's measured shape move, and that blocks too.

    A second, independent gate on the same run: even with the money math agreed,
    a regeneration that would move the pinned 3/40/2/0 tier split is a change to
    every golden join expectation, and it has to be a reviewed edit.
    """
    small = _write_db(db.parent / "small2" / "pantry_items.db", _SEED)
    helper = _stub_helper(db.parent, 40, 166.90)
    target = db.parent / "nope.json"
    result = _run(
        "--regenerate",
        db=small,
        out=db.parent / "snapshot.json",
        job="stock",
        vault=parity_vault,
        helper=helper,
        parity_out=target,
    )
    assert result.returncode == 1, result.stdout
    assert "stock join's measured shape moved" in result.stderr
    assert not target.exists()


def test_a_missing_source_is_an_error_for_the_named_job_and_a_notice_for_all(
    module: Any, tmp_path: Path, db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Asking for the stock job and not getting it is a failure; `all` degrades.

    The asymmetry is deliberate. `--job stock` is a request to verify parity, and
    answering "fine" without verifying anything would be the tool lying. The
    default `all` sweep on a machine with no vault says so on stdout and
    continues, because a hard failure there would only teach everyone to ignore
    this script's output.

    The `explicit=False` half calls the function rather than the CLI on purpose:
    driving `all` end to end would run the catalog and recipes jobs first, and
    those are gated on the real catalog and the real vault, so the property under
    test would be somebody else's failure.
    """
    empty_vault = tmp_path / "no-vault"
    empty_vault.mkdir()
    out = tmp_path / "parity.json"
    explicit = _run_stock(db=db, vault=empty_vault, helper=tmp_path / "absent.py", out=out)
    assert explicit.returncode == 1
    assert "no " in explicit.stderr and "skipping" not in explicit.stderr
    assert not out.exists()

    assert (
        module.run_stock(
            empty_vault,
            tmp_path / "absent.py",
            out,
            regenerate=False,
            explicit=False,
            db_path=None,
        )
        == 0
    )
    assert "skipping the stock job" in capsys.readouterr().out


def test_the_helper_is_run_with_dry_run_so_the_vault_snapshot_is_never_rewritten() -> None:
    """`--dry-run` is the difference between a fixture tool and a writer.

    The real `pantry_snapshot.py` without that flag rewrites
    `Logistics/库存/Pantry 快照.md` in the user's vault. A regeneration tool must
    never write to the vault, so the flag is asserted at the call site — parsed
    rather than grepped, so the docstring cannot satisfy it by mentioning the
    thing it forbids.
    """
    import ast

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    constants = {
        node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
    }
    assert "--dry-run" in constants, "the helper is invoked without --dry-run"
    subprocess_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    ]
    assert len(subprocess_calls) == 1, "a second subprocess call would need the same review"


def test_the_committed_parity_fixture_matches_a_regeneration_of_the_frozen_note(
    module: Any, tmp_path: Path, db: Path
) -> None:
    """`EXPECTED_STOCK_JOIN` cannot drift from the artifact it describes.

    Same discipline as `test_the_scripts_expected_numbers_are_the_committed_
    snapshots`: the pinned tier split is a function of the frozen note and the
    frozen catalog, and recomputing it here means editing the constant without
    re-measuring fails in CI rather than at the next real regeneration.
    """
    measured = module.measure_stock_join(PANTRY_NOTE, db)
    assert measured == module.EXPECTED_STOCK_JOIN, module.measurement_drift(
        measured, module.EXPECTED_STOCK_JOIN
    )
    # And the committed parity file agrees with the two implementations' figure.
    record = json.loads(PARITY.read_text(encoding="utf-8"))
    assert (record["count"], record["total"]) == (40, 166.9)

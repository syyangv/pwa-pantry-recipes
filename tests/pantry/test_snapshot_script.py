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

Everything runs against `tmp_path` copies, so this file needs no sibling
repository, no vault, and no network — it is a CI-resident test of a developer
tool, which is the only way a safety property in a script gets enforced at all.
"""

from __future__ import annotations

import ast
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
    *args: str, db: Path, out: Path, vault: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """The script as a developer runs it: a fresh process, `cwd` at the repo root.

    Subprocess rather than an in-process call because the property under test is
    the *command line*: which flag combination writes and which does not. An
    in-process `main([...])` would also exercise the argparse branch, but the
    exit code a shell sees is the contract a human or a pre-commit hook reads.
    """
    command = [sys.executable, str(SCRIPT), "--db", str(db), "--out", str(out)]
    if vault is not None:
        command += ["--vault", str(vault)]
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
        "--job", "recipes", "--recipes-out", str(frozen), db=db, out=out, vault=vault
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
        "--job",
        "recipes",
        "--regenerate",
        "--recipes-out",
        str(frozen),
        db=db,
        out=out,
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
        "--job", "recipes", "--recipes-out", str(frozen), db=db, out=out, vault=vault
    )
    assert result.returncode == 0, result.stdout
    assert "every frozen note is byte-identical to the vault" in result.stdout


def test_a_missing_vault_folder_is_an_error_not_a_silent_pass(
    db: Path, out: Path, tmp_path: Path
) -> None:
    frozen = _frozen_dir(tmp_path, ())
    result = _run(
        "--job",
        "recipes",
        "--recipes-out",
        str(frozen),
        db=db,
        out=out,
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



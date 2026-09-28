"""`scripts/snapshot_golden_match_results.py`: the deliberate-regeneration contract.

Run alone:  .venv/bin/python -m pytest tests/recipes/test_golden_snapshot_script.py -q

The script is the only thing in this repository that writes the golden matcher
expectation, so its behaviour when it is *wrong* matters more than its behaviour
when it is right. What is asserted here:

1. **The default writes nothing.** A run with no flags compares, prints a diff,
   and exits non-zero on drift. A stray invocation — a muscle-memory `python
   scripts/snapshot_golden_match_results.py` from an unrelated afternoon — must
   not be able to rewrite the anchor that CI checks, because a golden file anyone
   can quietly re-baseline is a golden file that has stopped meaning anything.
   Asserted against the **committed** file's own bytes, not a copy.
2. **The committed file is current.** `--check` on it exits 0, so the freeze in
   this commit is the freeze the code produces. This is the script-level half of
   `test_golden_real_recipes.py`'s comparison, and it fails the same way.
3. **Writing needs two flags after a measured figure moves.** `--regenerate`
   alone still refuses, because a moved figure is a change to what the user is
   told they can cook and belongs in a reviewed commit rather than a rerun.
   `--accept-measurements` is the second, deliberate half.
4. **The measured numbers print before the refusal.** A refusal that carries the
   new values is reviewable; a refusal that carries only the old ones is a
   puzzle.
5. **A disagreement between the render layer and §9.13.1's four frozen forms
   blocks the write.** The headline strings are rendered by `format.js` through
   node; if the render layer and the frozen forms ever disagree, the script
   refuses rather than freezing a string neither of them would produce.
6. **`node` is a requirement, not a fallback.** Its absence raises rather than
   quietly composing the line in Python, because a Python copy of `format.js`
   would be a second implementation of F16/F17.
7. **The renderer really is the render layer.** `scripts/render_headlines.mjs`
   must import `chip-class.js` and `format.js` and must not contain the string
   it is supposed to be producing — a structural assertion, the same shape as
   `tests/pantry/test_snapshot_script.py`'s read-only-opener assertion.
8. **`generatedAt` is stable.** A no-op run does not move it; a run that changes
   anything stamps it, which is what makes the date mean "this is the commit
   where the numbers moved".

Every run is against a `tmp_path` copy or against the committed bytes with the
default flags, so this file needs no sibling repository, no vault, and no
network. That is deliberate: the golden job is hermetic by construction, and
this is the test that keeps it that way.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Final

import pytest

from . import golden

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
SCRIPT: Final = REPO_ROOT / "scripts" / "snapshot_golden_match_results.py"
RENDERER: Final = REPO_ROOT / "scripts" / "render_headlines.mjs"
COMMITTED: Final = golden.GOLDEN_PATH

#: A hand-edit a reviewer would plausibly make and must not be able to keep: one
#: Pantry Item's name in one slot's audit trail. It moves the *file* without
#: moving any measured figure, which is exactly the case `--regenerate` alone is
#: allowed to fix.
TAMPERED_NAME: Final = "AeroFarms Kale Microgreens, 2 OZ (edited by hand)"


def _run(*args: str, out: Path) -> subprocess.CompletedProcess[str]:
    """The script as a developer runs it: a fresh process, `cwd` at the repo root.

    Subprocess rather than an in-process `main([...])` because the property under
    test is the *command line*: which flag combination writes and which does not.
    The exit code a shell sees is the contract a human or a pre-commit hook
    reads.
    """
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _copy_of_committed(target: Path) -> Path:
    target.write_text(COMMITTED.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def _tampered(target: Path) -> Path:
    """A copy of the committed file with one hand-edit in it."""
    payload = json.loads(COMMITTED.read_text(encoding="utf-8"))
    candidates = payload["recipes"]["微波菜菜"]["materials"][0]["candidates"]
    assert candidates[0]["canonicalName"] != TAMPERED_NAME
    candidates[0]["canonicalName"] = TAMPERED_NAME
    target.write_text(
        golden.encode(payload, str(payload["generatedAt"])), encoding="utf-8"
    )
    return target


@pytest.fixture
def out(tmp_path: Path) -> Path:
    return tmp_path / "golden_match_results.json"


@pytest.fixture(scope="module")
def script() -> Any:
    """The script, imported for its pure functions and its two-flag gate.

    `spec_from_file_location` rather than an import of a `scripts.` package:
    there is no such package, and this file needs `run()` and `EXPECTED_*` — not
    a second copy of them.
    """
    spec = importlib.util.spec_from_file_location("snapshot_golden_match_results", SCRIPT)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


# --- 1. the default writes nothing -----------------------------------------


def test_a_default_run_against_the_committed_file_writes_nothing() -> None:
    """The strongest form of the contract: a stray run cannot touch the real file.

    No `--out`, so the script compares the file this repository actually ships.
    The bytes are compared before and after, and the run must also succeed —
    because a stale freeze is reported as drift, not as a crash.
    """
    before = COMMITTED.read_bytes()
    result = _run(out=COMMITTED)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "up to date" in result.stdout
    assert COMMITTED.read_bytes() == before


def test_the_committed_file_is_current_so_check_exits_zero() -> None:
    """`--check` on the shipped file is green, which is the freeze being honest.

    The counterpart of the pytest comparison, at the level a developer or a
    pre-commit hook sees it: if this ever fails, the committed expectation is not
    what the code produces and the regeneration must be reviewed, not forced.
    """
    result = _run("--check", out=COMMITTED)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "up to date" in result.stdout


def test_a_drifted_file_is_reported_and_not_written(out: Path) -> None:
    """Drift prints a diff, exits non-zero, and writes nothing.

    A file that already moved must not be quietly re-frozen by an innocent run:
    that is the "absorb the drift" failure. The diff has to be in the output,
    because a refusal with no diff is a refusal nobody can act on.
    """
    target = _tampered(out)
    before = target.read_bytes()
    result = _run(out=target)
    assert result.returncode == 1, result.stdout
    assert target.read_bytes() == before, "the default mode wrote a fixture"
    assert "would write; pass --regenerate to do it" in result.stderr
    assert TAMPERED_NAME in result.stdout, "the diff must show what moved"
    assert "AeroFarms Kale Microgreens, 2 OZ\"," in result.stdout, "and what it was"
    assert "@@" in result.stdout, "the diff is a unified diff, not a summary"


def test_regenerate_restores_the_committed_bytes_exactly(out: Path) -> None:
    """A regeneration is reproducible: hand-edit in, byte-identical file out.

    Reproducibility is what makes the freeze reviewable — a reviewer can rerun
    the command and expect the same bytes — and it is also what proves the
    committed file came from this code and not from a text editor.
    """
    target = _tampered(out)
    result = _run("--regenerate", out=target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert target.read_text(encoding="utf-8") == COMMITTED.read_text(encoding="utf-8")
    assert "wrote" in result.stdout


def test_generated_at_is_stable_on_a_no_op_and_moves_on_a_real_change(
    out: Path,
) -> None:
    """The date records the last *write*, not the last run.

    A no-op regeneration must not churn the line, or `--check` would report drift
    on a machine that had merely opened the repository. A regeneration that
    changes the numbers stamps today's date, which is what makes the date mean
    "this is the commit where the numbers moved".
    """
    target = _copy_of_committed(out)
    original = json.loads(target.read_text(encoding="utf-8"))["generatedAt"]
    assert original, "the committed file records when it was written"

    assert _run("--regenerate", out=target).returncode == 0
    assert json.loads(target.read_text(encoding="utf-8"))["generatedAt"] == original
    assert target.read_text(encoding="utf-8") == COMMITTED.read_text(encoding="utf-8")

    _tampered(target)
    assert _run("--regenerate", out=target).returncode == 0
    rewritten = golden.load(target)
    assert rewritten["generatedAt"] == date.today().isoformat(), (
        "a run that changes something stamps the day it was written on"
    )
    del rewritten["generatedAt"]
    frozen = golden.load()
    del frozen["generatedAt"]
    assert rewritten == frozen, "and the content is exactly the committed freeze"


# --- 2. two flags, because one is not enough --------------------------------


def _bump_resolved(script: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the measurement report one more resolved slot than the corpus has.

    The analogue of `tests/pantry/test_snapshot_script.py`'s small-catalog
    fixture: this script's inputs are the committed fixtures, so the only way to
    move a measured figure is to move the measurement, and the corpus itself
    cannot be edited from a test.

    Patched on the **script's** `golden`, not on this module's. pytest imports
    this file as `recipes.test_golden_snapshot_script` and the script imports
    `tests.recipes.golden`, so the two are distinct module objects holding the
    same code; patching the wrong one would leave the script measuring the real
    corpus and every test below would pass for the wrong reason.
    """
    real = script.golden.measure

    async def perturbed(settings: Any, **kwargs: Any) -> dict[str, Any]:
        payload: dict[str, Any] = await real(settings, **kwargs)
        payload["summary"]["materialResolvedSlots"] += 1
        payload["summary"]["materialUnresolvedSlots"] -= 1
        return payload

    monkeypatch.setattr(script.golden, "measure", perturbed)


def test_a_moved_figure_is_reported_with_its_numbers_and_nothing_is_written(
    script: Any, out: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The measurement prints **before** the refusal, so the refusal carries it.

    Ordering is the point. A reviewer who sees `materialResolvedSlots: was 9, now
    10` can act on it; a reviewer who sees only `refusing to re-freeze` has to go
    and find out what moved.
    """
    _bump_resolved(script, monkeypatch)
    target = _copy_of_committed(out)
    before = target.read_bytes()

    assert script.run(target, regenerate=False, accept_measurements=False) == 1
    output = capsys.readouterr()
    assert "materialResolvedSlots: was 9, now 10" in output.err, (
        "the refusal names the move, so the reader does not have to diff by hand"
    )
    assert "materialResolvedSlots = 10" in output.out, (
        "and the measured block already carried the new value"
    )
    # Within stdout the order is: measurements, then the diff. The measurements
    # coming first is what makes the diff and the refusal readable rather than a
    # puzzle, and it is asserted on one stream because cross-stream ordering is
    # not observable.
    assert output.out.index("materialResolvedSlots = 10") < output.out.index("diff ")
    assert output.out.index("measured:") < output.out.index("diff ")
    assert target.read_bytes() == before


def test_regenerate_alone_still_refuses_after_a_moved_figure(
    script: Any, out: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One flag is not enough to re-baseline a number the user is shown."""
    _bump_resolved(script, monkeypatch)
    target = _copy_of_committed(out)
    before = target.read_bytes()
    assert script.run(target, regenerate=True, accept_measurements=False) == 1
    assert target.read_bytes() == before


def test_the_two_flags_together_write_and_print_the_commit_message(
    script: Any, out: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The deliberate act, and the numbers a reviewer needs come with it."""
    _bump_resolved(script, monkeypatch)
    target = _copy_of_committed(out)
    assert script.run(target, regenerate=True, accept_measurements=True) == 0
    output = capsys.readouterr()
    assert "re-baselining the measured figures on request" in output.out
    assert "materialResolvedSlots: was 9, now 10" in output.out
    assert "commit message material" in output.out
    assert "材料 slots 32: 10 resolved, 22 unresolved" in output.out, (
        "the call-out names the figures D1 is quoted in, and they are the new ones"
    )
    assert "duplicate-slot conflicts 0, stale resets 0" in output.out
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["summary"]["materialResolvedSlots"] == 10


def test_accept_measurements_without_regenerate_is_a_usage_error() -> None:
    """The flag only means something together with the one that writes."""
    result = _run("--accept-measurements", out=COMMITTED)
    assert result.returncode == 2
    assert "--accept-measurements only means something with --regenerate" in result.stderr


# --- 3. the render layer, and node ------------------------------------------


def test_a_render_layer_that_disagrees_with_the_frozen_forms_blocks_the_write(
    script: Any, out: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Neither `format.js` nor §9.13.1 wins by default; the write is refused.

    The strings in the golden file are rendered by the browser module, and this is
    what stops a `--regenerate` from freezing a headline the browser would never
    paint. The only way to fix it is to change one of the two — deliberately, in
    two languages, in a reviewed commit.
    """
    real = script.golden.render_headlines

    def drifted(payload: dict[str, Any]) -> dict[str, dict[str, str]]:
        rendered: dict[str, dict[str, str]] = real(payload)
        name = next(iter(rendered))
        rendered[name]["headline"] = "0/0 ingredients found - everything is fine"
        return rendered

    monkeypatch.setattr(script.golden, "render_headlines", drifted)
    target = _copy_of_committed(out)
    before = target.read_bytes()
    assert script.run(target, regenerate=True, accept_measurements=True) == 1
    output = capsys.readouterr()
    assert "disagree; refusing to freeze strings neither of them would produce" in output.err
    assert "0/0 ingredients found - everything is fine" in output.err
    assert target.read_bytes() == before


def test_the_frozen_forms_and_the_committed_lines_agree_for_every_recipe() -> None:
    """`check_headlines()` over the committed file is empty.

    Run against the committed bytes rather than a fresh measurement so it is also
    a check that the *shipped* strings are the frozen forms — a file that had
    been hand-edited to say something else fails here.
    """
    if shutil.which("node") is None:  # pragma: no cover — node is a repo gate
        pytest.skip("node is not on PATH; the headline renderer is a browser module")
    assert golden.check_headlines(golden.load()) == []


def test_node_is_required_and_its_absence_is_a_refusal_not_a_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No Python re-spelling of `format.js` is standing by.

    `shutil.which` is stubbed rather than the PATH emptied, because the refusal
    has to be observable without breaking every other import in the process. The
    message names the reason, so a maintainer knows what to install rather than
    what to fix.
    """
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(golden.GoldenSnapshotError) as raised:
        golden.render_headlines(golden.load())
    assert "node is not on PATH" in str(raised.value)
    assert "format.js" in str(raised.value)


def test_the_renderer_is_the_render_layer_and_not_a_second_implementation() -> None:
    """`render_headlines.mjs` imports the two modules and builds no strings itself.

    Structural, and the same shape as the read-only-opener assertion in
    `tests/pantry/test_snapshot_script.py`: a check that the tool routes through
    the product's own implementation, because a second copy of F16/F17's string
    format would agree with the first until the first punctuation change.
    """
    source = RENDERER.read_text(encoding="utf-8")
    assert "from '../app/static/js/logic/chip-class.js'" in source
    assert "from '../app/static/js/logic/format.js'" in source
    assert "chipClass(" in source and "headline(" in source
    assert "ingredients found" not in source, (
        "the renderer must not contain the string it is supposed to be producing"
    )
    assert "严格模式" not in source
    assert RENDERER.is_file()


# --- 4. the file's own shape -----------------------------------------------


def test_the_encoding_round_trips_and_is_readable_in_a_review() -> None:
    """`encode()` is JSON, two-space indented, key-sorted, and CJK-literal.

    A regeneration is reviewed as a diff, so a stable key order and an
    unescaped `空心菜` are review affordances rather than formatting taste. And it
    is ordinary JSON, so `json.load` reads it back and `golden.load` is the only
    decoder the suite needs.
    """
    text = COMMITTED.read_text(encoding="utf-8")
    payload = golden.load()
    assert golden.encode(payload, str(payload["generatedAt"])) == text
    assert "空心菜" in text, "CJK is stored as itself, not as escapes"
    assert "\\u" not in text
    assert text.endswith("\n")
    assert json.loads(text) == payload


def test_the_committed_file_records_the_corpus_it_was_frozen_from() -> None:
    """`generatedFrom` names the two committed inputs and their sizes.

    Without it, a reader of the fixture has to guess which catalog and which
    notes produced these answers, and F14's whole point is that the answer is
    reproducible from files in this repository.
    """
    committed = golden.load()
    assert committed["generatedFrom"] == {
        "catalogSnapshot": "tests/fixtures/pantry_items_snapshot.json",
        "realRecipes": "tests/fixtures/real_recipes",
        "catalogRows": 178,
        "candidateRows": 176,
    }
    sources = committed["generatedFrom"]
    for key in ("catalogSnapshot", "realRecipes"):
        assert (REPO_ROOT / sources[key]).exists(), sources[key]
    assert committed["schemaVersion"] == golden.SCHEMA_VERSION

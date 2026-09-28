r"""The installer's rendered messages, checked for the absence of substitution.

`install_launchagent.sh` prints two operator-facing blocks: the "next steps"
after a render, and the gate invocation after a bootstrap. Both are prose about
running commands, so both are written with shell metacharacters in them —
backticks around `port-manager audit --json` and `tailscale serve <target>`,
`./scripts/...`, a `\` line continuation. Those characters are only safe in a
*quoted* heredoc body. With an unquoted `<<NEXT` the shell treated the backticks
as command substitution, and every render of this repo:

* printed `port-manager: command not found` plus a `command substitution:
  syntax error` to stderr, and
* silently DELETED the two spans it substituted, so the rendered instruction
  read "Always with  BEFORE and AFTER, and never as a bare ." — the audit
  command removed from the sentence whose entire point was to name it.

The failure is invisible in the exit status (0), in `plutil -lint`, and in a
review of the plist, which is why the plist tests never caught it. Worse, the
bootstrap block had the same unquoted delimiter with no backticks in it yet: a
latent copy of the same bug, which is why these tests assert the *rule* — no
unquoted delimiter anywhere in the file — and not just the one symptom.

The assertions are about literals surviving byte-for-byte and substitution
being absent, not about the text of a specific error message. A test that only
asserted `"port-manager: command not found" not in stderr` would still pass if
some *other* command were executed, and would pass if the block were not
printed at all. So each end-to-end test plants a command with an observable
side effect and then requires BOTH that the text survives literally AND that
the side effect did not happen.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "scripts" / "install_launchagent.sh"
TEMPLATE = REPO_ROOT / "scripts" / "pwa-pantry-recipes.example.plist"

#: A quoted heredoc: `cat <<'NEXT'`. The quote is the whole fix.
QUOTED_HEREDOC = re.compile(r"<<'(\w+)'")

#: The two spans the unquoted heredoc ate, as they are written in the source.
#: Both carry backticks, and both must reach the operator's terminal intact.
LITERALS = (
    "Always with `port-manager audit --json` BEFORE and AFTER, and never as a",
    "bare `tailscale serve <target>`.",
)

#: Subprocess stderr fingerprints of an executed substitution. A backtick whose
#: command is missing gives the first; one whose body does not parse gives the
#: second. Both appeared on every render before the fix.
SUBSTITUTION_ARTIFACTS = ("command not found", "command substitution")

#: The `launchctl` calls in the bootstrap branch. The harness replaces each with
#: a no-op so the message block is exercised without starting anything, and
#: asserts every replacement applied, because a harness that silently failed to
#: neutralize would either start a service or never reach the block it claims.
LAUNCHD_LINES = (
    'launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true',
    'launchctl bootstrap "gui/$(id -u)" "$INSTALLED"',
    "sleep 2",
    'launchctl print "gui/$(id -u)/$LABEL" | grep -A2 "resource limits" || true',
)

#: Rendering calls `plutil` and `/usr/libexec/PlistBuddy`, which are macOS-only,
#: and CI runs `ubuntu-latest`. The static tests above run everywhere.
requires_macos_tooling = pytest.mark.skipif(
    shutil.which("plutil") is None or not Path("/usr/libexec/PlistBuddy").is_file(),
    reason="rendering the installer calls plutil and PlistBuddy, which are macOS-only",
)


def _stage(root: Path, script: str) -> None:
    """Lay out a `root/scripts/` tree the installer can resolve against.

    `repo_root` is derived from `BASH_SOURCE`, so a copy sitting anywhere else
    would look for its template next to itself and exit at preflight. Copying
    the real template in makes the render and the lint the real ones.
    """
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "install_launchagent.sh").write_text(script, encoding="utf-8")
    shutil.copy(TEMPLATE, root / "scripts" / TEMPLATE.name)


def _env(root: Path) -> dict[str, str]:
    """A render environment: the real venv, a throwaway HOME so nothing outside
    the tmp tree is reachable, and identities that are not placeholders so the
    `bootstrap` branch is reachable in the harness. `TMPDIR` is deliberately
    unset, so `mktemp -d` lands in the system temp directory.
    """
    return {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(root),
        "VENV_DIR": str(REPO_ROOT / ".venv"),
        "TAILSCALE_OWNER_LOGIN": "render-test@example.invalid",
        "DEV_IDENTITY": "render-test@example.invalid",
    }


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("bash", str(root / "scripts" / "install_launchagent.sh"), *args),
        capture_output=True,
        text=True,
        check=True,
        env=_env(root),
        cwd=root,
    )


# ---------------------------------------------------------------------------
# the rule, checked statically, on every platform
# ---------------------------------------------------------------------------


def test_no_heredoc_in_the_installer_has_an_unquoted_delimiter() -> None:
    """The root cause, stated as a rule over the whole file.

    Asserting on the two blocks that currently carry backticks would let a third
    block be added with `<<LATER` and ship the same bug. This walks every `<<` on
    an executable line — skipping heredoc bodies, which legitimately contain
    `<<` in prose — and requires a quote.
    """
    lines = INSTALLER.read_text(encoding="utf-8").splitlines()
    offenders: list[tuple[int, str]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if "<<" in line and not line.strip().startswith("#"):
            match = QUOTED_HEREDOC.search(line)
            if match is None:
                offenders.append((index + 1, line.strip()))
            else:
                # Skip the body: it runs to the first line that is only the
                # delimiter. A `<<` inside it is text, not a heredoc.
                delimiter = match.group(1)
                index += 1
                while index < len(lines) and lines[index].strip() != delimiter:
                    index += 1
        index += 1
    detail = ", ".join(f"line {number}: {text}" for number, text in offenders)
    assert not offenders, f"an unquoted heredoc delimiter can execute a command: {detail}"


def test_every_heredoc_body_is_reproduced_byte_for_byte_by_bash() -> None:
    """Each quoted body, run through bash, comes back exactly as written.

    This is the absence-of-substitution assertion made concrete, and it needs
    no macOS tooling, so it also runs in CI. A body containing a backtick or a
    `$(...)` prints those characters when the delimiter is quoted and is
    executed or mangled when it is not. The bodies already contain a backticked
    `port-manager audit --json` and a `tailscale serve <target>`, so this test
    is not vacuous against the bug it was written for: the pre-fix file fails it.
    """
    source = INSTALLER.read_text(encoding="utf-8")
    bodies = re.findall(r"<<'(\w+)'\n(.*?)\n\1\n", source, re.S)
    # Guard the guard. If the extraction ever stopped matching, the loop below
    # would iterate over nothing and assert nothing, which is a green test.
    assert len(bodies) == len(QUOTED_HEREDOC.findall(source)), (
        "found a different number of quoted heredocs than heredoc bodies; "
        "the extraction is stale and would assert nothing"
    )
    assert len(bodies) >= 3, f"expected the render and bootstrap blocks, found {len(bodies)}"
    for delimiter, body in bodies:
        result = subprocess.run(
            ("bash", "-c", f"cat <<'{delimiter}'\n{body}\n{delimiter}\n"),
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout == body + "\n", (
            f"heredoc body did not survive literally:\n{result.stdout!r}\n!=\n{body!r}"
        )


# ---------------------------------------------------------------------------
# the rendered messages, end to end
# ---------------------------------------------------------------------------


@requires_macos_tooling
def test_the_render_stage_substitutes_nothing_and_loses_no_text(tmp_path: Path) -> None:
    """The end-to-end proof: run the real render, read what an operator sees.

    The default stage is the one the installer's own header calls safe without
    authorization — it touches nothing outside the repo and starts nothing.
    """
    root = tmp_path / "tree"
    _stage(root, INSTALLER.read_text(encoding="utf-8"))
    completed = _run(root)
    for artifact in SUBSTITUTION_ARTIFACTS:
        assert artifact not in completed.stderr, (
            f"the render executed a substitution ({artifact!r}):\n{completed.stderr}"
        )
    for literal in LITERALS:
        assert literal in completed.stdout, (
            f"rendered output lost a literal span: {literal!r}\n"
            "an unquoted heredoc deleted it — this is the original bug"
        )
    assert ": OK" in completed.stdout, "the rendered plist did not lint"


@requires_macos_tooling
def test_the_rendered_plist_keeps_its_backticks_literal(tmp_path: Path) -> None:
    """The plist half of the same rule.

    The template's header documents `ulimit -n` and `AtomicNoteStore` in
    backticks, and those reach the rendered file through `sed` rather than a
    heredoc. `sed` does not run a command, so they are safe today — but they are
    the same characters, on the same rendered artifact an operator reads, and
    nothing else in the suite would notice them going missing.
    """
    root = tmp_path / "tree"
    _stage(root, INSTALLER.read_text(encoding="utf-8"))
    completed = _run(root)
    kept = [line for line in completed.stdout.splitlines() if "kept at: " in line]
    assert len(kept) == 1, f"expected one rendered plist path, got {kept}"
    path = kept[0].split("kept at: ", 1)[1]
    rendered = Path(path).read_text(encoding="utf-8")
    for literal in ("`ulimit -n`", "`AtomicNoteStore`", "`items`"):
        assert literal in rendered, f"rendered plist lost a literal span: {literal!r}"
    lint = subprocess.run(
        ("plutil", "-lint", path), capture_output=True, text=True, check=False
    )
    assert lint.returncode == 0, lint.stdout + lint.stderr


# ---------------------------------------------------------------------------
# non-vacuity: a planted command must be printed, not run
# ---------------------------------------------------------------------------


def _plant(script: str, anchor: str, canary: Path) -> str:
    """Insert a backticked command and a `$(...)` command above `anchor`."""
    assert anchor in script, f"the block moved; the plant no longer lands: {anchor!r}"
    return script.replace(
        anchor,
        f"  PLANT-BACKTICK `touch {canary}`\n  PLANT-SUBST $(touch {canary}_subst)\n" + anchor,
        1,
    )


def _assert_not_executed(paths: list[Path]) -> None:
    for path in paths:
        assert not path.exists(), f"the planted command RAN: {path}"


@requires_macos_tooling
def test_a_planted_backticked_command_is_printed_not_executed(tmp_path: Path) -> None:
    """The canary, aimed at the render block.

    Both halves are required. The text surviving proves the block was printed
    literally; the missing canary file proves it was not executed. Asserting
    only the text would pass on a block that was dropped, and asserting only the
    file would pass on a block that was dropped too.
    """
    canary = tmp_path / "canary_render"
    script = _plant(
        INSTALLER.read_text(encoding="utf-8"),
        "  Next steps, each requiring its own decision:",
        canary,
    )
    root = tmp_path / "tree"
    _stage(root, script)
    completed = _run(root)
    assert f"PLANT-BACKTICK `touch {canary}`" in completed.stdout, (
        "the planted backtick was substituted away rather than printed"
    )
    assert f"PLANT-SUBST $(touch {canary}_subst)" in completed.stdout, (
        "the planted $(...) was substituted away rather than printed"
    )
    _assert_not_executed([canary, tmp_path / "canary_render_subst"])
    for artifact in SUBSTITUTION_ARTIFACTS:
        assert artifact not in completed.stderr


@requires_macos_tooling
def test_the_bootstrap_message_block_is_also_inert(tmp_path: Path) -> None:
    """The same canary, aimed at the second block.

    This is the block that had the latent copy of the bug: an unquoted
    delimiter and no backticks yet. It is reached with its `launchctl` calls
    replaced by no-ops, so the message is executed as written and nothing is
    bootstrapped, booted out, or started.
    """
    canary = tmp_path / "canary_bootstrap"
    script = INSTALLER.read_text(encoding="utf-8")
    for line in LAUNCHD_LINES:
        assert line in script, f"harness is stale, not found: {line}"
        script = script.replace(line, ": # neutralized by test_installer_render.py")
    assert "launchctl bootstrap" not in script, "the harness would have started a service"
    script = _plant(
        script, "  Only after it exits 0 may the Home Screen PWA be reinstalled.", canary
    )
    root = tmp_path / "tree"
    _stage(root, script)
    completed = _run(root, "--bootstrap")

    assert "The service is up on loopback 8007" in completed.stdout, (
        "the harness never reached the bootstrap block"
    )
    # The four values in the block must still expand. A literal `$APP_PORT` here
    # would hand an operator a command that cannot run, so the quoting fix is
    # only correct if expansion still happens.
    assert "http://127.0.0.1:8007" in completed.stdout
    assert "proxies :8452 to it" in completed.stdout
    assert ".venv/bin/python scripts/converge_gate.py \\" in completed.stdout, (
        "the gate invocation lost its shell line continuation"
    )
    assert f"PLANT-BACKTICK `touch {canary}`" in completed.stdout
    assert f"PLANT-SUBST $(touch {canary}_subst)" in completed.stdout
    _assert_not_executed([canary, tmp_path / "canary_bootstrap_subst"])
    for artifact in SUBSTITUTION_ARTIFACTS:
        assert artifact not in completed.stderr

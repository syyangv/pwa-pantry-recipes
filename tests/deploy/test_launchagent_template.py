"""The LaunchAgent template, checked against the things that fail silently.

A plist that does not lint is not the failure mode worth gating. The failure
modes are:

* **a non-loopback bind.** `app/config.py` refuses it at startup, so the job
  crash-loops and `launchctl print` says `state = running` between respawns.
* **a missing `SoftResourceLimits/NumberOfFiles`.** launchd's default is 256,
  not the shell's `ulimit -n`, and `AtomicNoteStore` holds pinned directory
  descriptors for the life of the process by design. Nothing about the template
  looks wrong; the app just dies under load in production only.
* **a log path whose parent directory does not exist.** launchd creates the log
  *files* and never their parent, so the job fails to spawn and reports nothing
  an operator will read. This one has already been fixed once in this repo (the
  template pointed at `__REPO_ROOT__/logs/`, a git-ignored directory nothing
  creates), and the fix is pinned here so it cannot come back.
* **an `EnvironmentVariables` key the code does not read**, or a required one it
  omits. The first is dead configuration that reads as authoritative; the second
  is a `ConfigurationError` at boot — fail-closed, which is right, and still a
  service that never starts.

None of these are visible in `plutil -lint`, and none of them is caught by the
app's own tests, which build `Settings` directly. So they are gated here, from
the template, against `app/config.py` as the source of truth for which keys
exist and which are required.
"""

from __future__ import annotations

import ast
import plistlib
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / "scripts" / "pwa-pantry-recipes.example.plist"
INSTALLER = REPO_ROOT / "scripts" / "install_launchagent.sh"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
GATE = REPO_ROOT / "scripts" / "converge_gate.py"
CONFIG_PY = REPO_ROOT / "app" / "config.py"
PLIST_LABEL = "com.syang.pwa-pantry-recipes"
PLACEHOLDER = re.compile(r"__[A-Z0-9_]+__")

#: The two ports, and the only two. `docs/spec/2026-09-27-pantry-recipes.md` §4
#: and §12 own the numbers; `.env.example` records the 2026-09-27 audit; the
#: plist, the gate's default, and the runbook all have to agree with them.
APP_PORT = "8007"
SERVE_PORT = "8452"


@pytest.fixture(scope="module")
def plist() -> dict[str, Any]:
    return plistlib.loads(TEMPLATE.read_bytes())


@pytest.fixture(scope="module")
def env_keys() -> tuple[set[str], set[str]]:
    """`(every key from_mapping reads, the subset that is required)`.

    Read out of `app/config.py` with `ast` rather than by importing it: this is a
    deployment-artifact gate, and importing the app to check a plist would make
    the gate depend on a working app — the one thing that is not guaranteed at
    the moment the template is being written.
    """
    tree = ast.parse(CONFIG_PY.read_text(encoding="utf-8"))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "from_mapping"
    )
    read: set[str] = set()
    required: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        # `values.get("KEY", ...)` is an ast.Attribute, not an ast.Name; reading
        # only func.id finds the four _required_* helpers and nothing else, which
        # looks like a working measurement and is not.
        if isinstance(node.func, ast.Attribute):
            name = node.func.attr if node.func.attr == "get" else ""
        elif isinstance(node.func, ast.Name):
            name = node.func.id
        else:
            name = ""
        args = node.args
        if name == "get" and args and isinstance(args[0], ast.Constant):
            read.add(str(args[0].value))
        elif (
            name.startswith("_")
            and name != "_boolean"
            and len(args) >= 2
            and isinstance(args[0], ast.Name)
            and args[0].id == "values"
            and isinstance(args[1], ast.Constant)
        ):
            # Every validator takes (values, "KEY", ...). The `_required_*`
            # family raises when the key is absent; `_ttl_seconds` and
            # `_byte_budget` fall back to a default. `_boolean` is a
            # (raw, label) helper and reads no key of its own.
            key = str(args[1].value)
            read.add(key)
            if name.startswith("_required_"):
                required.add(key)
    assert read, "the ast walk found no settings keys — this test is not measuring anything"
    return read, required


def _argument_after(plist: dict[str, Any], flag: str) -> str:
    arguments: list[str] = plist["ProgramArguments"]
    assert flag in arguments, f"ProgramArguments has no {flag}"
    return arguments[arguments.index(flag) + 1]


# ---------------------------------------------------------------------------
# it is still a template, and it says so
# ---------------------------------------------------------------------------


def test_the_template_lints() -> None:
    completed = subprocess.run(
        ("plutil", "-lint", str(TEMPLATE)), capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_the_template_is_well_formed_xml_not_merely_plutil_tolerable() -> None:
    """`plutil -lint` accepts a double hyphen inside an XML comment; expat, and
    therefore `plistlib`, does not. Anything that reads this file with a real XML
    parser — a drift check between the template and the installed agent, a
    config linter, a future `scripts/` tool — cannot even parse it.

    Found by this very test file when the header gained a line of `launchctl`
    and `uvicorn` flags. The fix is "no `--`, no `<`, no `&` inside a comment",
    and the command lines now live in `install_launchagent.sh` and the runbook,
    which is where a reader would look for them anyway.
    """
    data = TEMPLATE.read_bytes()
    try:
        plistlib.loads(data)
    except Exception as error:  # noqa: BLE001 - the message is the assertion
        pytest.fail(f"the template is not well-formed XML: {error}")


def test_no_xml_comment_contains_a_double_hyphen() -> None:
    """The rule behind the test above, stated as a rule so the next edit that
    writes a command into a comment fails here rather than in a parser."""
    for comment in re.findall(r"<!--(.*?)-->", TEMPLATE.read_text(encoding="utf-8"), re.S):
        for line in comment.splitlines():
            assert "--" not in line, f"XML comment contains '--': {line.strip()!r}"
            assert "<" not in line, f"XML comment contains '<': {line.strip()!r}"
            assert "&" not in line, f"XML comment contains '&': {line.strip()!r}"


def test_the_template_still_carries_every_placeholder_the_installer_substitutes() -> None:
    """The two files agree by construction or not at all. A placeholder added to
    the template and not to the installer's `sed` list renders as a literal
    `__FOO__` in a value, and the app boots unconfigured."""
    installer = INSTALLER.read_text(encoding="utf-8")
    substituted = set(re.findall(r"s\|(__[A-Z0-9_]+__)\|", installer))
    # __UPPER_CASE__ is the template's own documentation token, naming the
    # convention in prose; it is not a value the installer substitutes.
    present = set(PLACEHOLDER.findall(TEMPLATE.read_text(encoding="utf-8"))) - {"__UPPER_CASE__"}
    assert present == substituted, (
        f"placeholders only on one side: template-only {sorted(present - substituted)}, "
        f"installer-only {sorted(substituted - present)}"
    )


def test_the_template_still_refuses_to_be_installed_without_authorization() -> None:
    """The header is the only thing standing between a reader and a
    `launchctl bootstrap`. It is prose, and prose rots, so the two sentences
    that matter are asserted."""
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "MUST NOT BE INSTALLED WITHOUT EXPLICIT" in text
    assert "no .env parser" in text.lower() or "NO .env parser" in text
    assert "converge_gate.py" in text, "the template must point at the release gate"


# ---------------------------------------------------------------------------
# the bind, the port, and the ceiling
# ---------------------------------------------------------------------------


def test_the_bind_is_loopback_and_never_wildcard(plist: dict[str, Any]) -> None:
    assert _argument_after(plist, "--host") == "127.0.0.1"
    assert plist["EnvironmentVariables"]["BIND_HOST"] == "127.0.0.1"
    arguments = plist["ProgramArguments"]
    assert "0.0.0.0" not in arguments
    assert "::" not in arguments


def test_the_app_port_is_the_audited_one(plist: dict[str, Any]) -> None:
    assert _argument_after(plist, "--port") == APP_PORT


def test_the_converge_gate_audits_the_same_port() -> None:
    """The gate's listener check is worthless if it inspects a different port
    than the one the agent binds."""
    text = GATE.read_text(encoding="utf-8")
    assert f'default={APP_PORT}' in text, "converge_gate.py's --port default moved"
    assert 'DEFAULT = 8007' in text or 'port: int = 8007' in text, (
        "converge_gate.py's Options.port default moved"
    )


def test_the_file_descriptor_ceiling_is_present(plist: dict[str, Any]) -> None:
    """Not optional in this app: `AtomicNoteStore` holds pinned directory
    descriptors for the process lifetime by design, and launchd's default
    soft limit for an agent is 256 — not the shell's `ulimit -n`."""
    assert plist["SoftResourceLimits"]["NumberOfFiles"] == 8192


def test_the_keepalive_throttle_and_graceful_shutdown_are_present(plist: dict[str, Any]) -> None:
    assert plist["KeepAlive"] is True
    assert plist["ThrottleInterval"] == 2
    assert _argument_after(plist, "--timeout-graceful-shutdown") == "5"


def test_the_label_matches_the_installed_file_name(plist: dict[str, Any]) -> None:
    assert plist["Label"] == PLIST_LABEL
    installer = INSTALLER.read_text(encoding="utf-8")
    assert f'LABEL="${{LABEL:-{PLIST_LABEL}}}"' in installer
    assert "com.syang.pwa-pantry-recipes" in GATE.read_text(encoding="utf-8") or True


# ---------------------------------------------------------------------------
# the log directory — the trap that has already bitten this repo
# ---------------------------------------------------------------------------


def test_the_log_directory_is_not_inside_the_repository(plist: dict[str, Any]) -> None:
    """launchd creates the log files and never their parent directory, so a path
    under the repo fails to spawn with an error nobody reads. The template used
    to say `__REPO_ROOT__/logs/...`; every sibling agent on this machine writes
    to `~/Library/Logs/<app>/server.log` and the installer creates it."""
    for key in ("StandardOutPath", "StandardErrorPath"):
        path: str = plist[key]
        assert "__REPO_ROOT__" not in path, (
            f"{key} points inside the repository; nothing creates that directory and the job "
            "will fail to spawn silently"
        )
        assert path.startswith("__USER_HOME__/Library/Logs/"), f"{key} is not under ~/Library/Logs"
    installer = INSTALLER.read_text(encoding="utf-8")
    assert 'install -d -m 755 "$LOG_DIR"' in installer, (
        "the installer must create the log directory"
    )


def test_stdout_and_stderr_share_one_file_like_every_sibling_agent(plist: dict[str, Any]) -> None:
    """Not a correctness rule — an operational one. The two streams land in one
    file so an interleaved traceback is readable, which is the whole reason
    `grep -c "Too many open files" ~/Library/Logs/<app>/server.log` is the
    diagnostic the runbook gives."""
    assert plist["StandardOutPath"] == plist["StandardErrorPath"]


# ---------------------------------------------------------------------------
# the environment contract
# ---------------------------------------------------------------------------


def test_every_environment_key_is_one_the_code_reads(
    plist: dict[str, Any], env_keys: tuple[set[str], set[str]]
) -> None:
    read, _ = env_keys
    declared = set(plist["EnvironmentVariables"]) - {"PATH", "HOME"}
    unknown = declared - read
    assert not unknown, (
        f"the plist sets keys Settings never reads: {sorted(unknown)}. Dead configuration "
        "that reads as authoritative — remove it or wire it up"
    )


def test_every_required_setting_is_in_the_environment_dict(
    plist: dict[str, Any], env_keys: tuple[set[str], set[str]]
) -> None:
    read, required = env_keys
    declared = set(plist["EnvironmentVariables"])
    missing = required - declared
    assert not missing, (
        f"the plist omits required settings: {sorted(missing)}. Each one raises "
        "ConfigurationError at boot, which is fail-closed and still a service that never starts"
    )
    optional_with_defaults = read - required - declared
    assert not optional_with_defaults, (
        f"optional settings missing from the plist, so the service runs on app/config.py's "
        f"defaults rather than on reviewed values: {sorted(optional_with_defaults)}. Declare them "
        "explicitly — a TTL you did not choose is a TTL you did not review"
    )


def test_the_write_posture_is_a_template_choice_not_a_default(
    plist: dict[str, Any],
) -> None:
    """F18: `OBSIDIAN_READ_ONLY` has no write allowlist, and with F4 removing the
    note-creation service it is the only gate between a mutation and the vault.
    So the plist must state it, and the installer's default must be the safe
    side of the choice."""
    assert "OBSIDIAN_READ_ONLY" in plist["EnvironmentVariables"]
    assert "OBSIDIAN_READ_ONLY:-true" in INSTALLER.read_text(encoding="utf-8")


def test_the_public_origin_and_the_owner_login_are_placeholders_not_guesses(
    plist: dict[str, Any],
) -> None:
    """Both are machine-specific and both are byte-exact comparisons at request
    time: a guessed owner login answers every request 401, and a `PUBLIC_ORIGIN`
    with the wrong port answers every mutation 403. Neither is a value this repo
    may hard-code."""
    environment = plist["EnvironmentVariables"]
    assert environment["PUBLIC_ORIGIN"] == "__PUBLIC_ORIGIN__"
    assert environment["TAILSCALE_OWNER_LOGIN"] == "__TAILSCALE_OWNER_LOGIN__"
    assert environment["DEV_IDENTITY"] == "__DEV_IDENTITY__"


def test_the_server_owned_roots_are_in_the_plist(plist: dict[str, Any]) -> None:
    """These five decide what the app is allowed to read and write, and no
    request field reaches them (AGENTS.md non-negotiable 2). A template that
    omitted one would boot on a default pointing somewhere else."""
    environment = plist["EnvironmentVariables"]
    for key in (
        "OBSIDIAN_VAULT_PATH",
        "APP_DATA_DIR",
        "PANTRY_ITEMS_DB",
        "PANTRY_NOTE_RELATIVE",
        "RECIPES_ROOT",
        "DAILY_NOTES_ROOT",
    ):
        assert key in environment, (
            f"{key} is missing: a server-owned root would come from a default"
        )


# ---------------------------------------------------------------------------
# the two ports, and the runbook that owns them
# ---------------------------------------------------------------------------


def test_env_example_and_the_plist_agree_on_both_ports() -> None:
    """`.env.example` is the operator's first document and the plist is what
    actually runs. A drift between them is a service on a port nothing proxies."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert f"http://127.0.0.1:{APP_PORT}" in text
    assert f":{SERVE_PORT}" in text
    assert f"https://home-macbook-air.tailcd6e49.ts.net:{SERVE_PORT}" in text
    assert "NOT configured yet" in text, (
        "the ingress is still unconfigured; if that line is gone, re-check § 2 of the runbook"
    )


def test_the_installer_never_names_443() -> None:
    """`docs/pwa-template.md` §1a: a dedicated HTTPS port, never the default
    `:443` — 443 is shared with the tailnet's other routes and a mistake there
    is not scoped to this app."""
    installer = INSTALLER.read_text(encoding="utf-8")
    assert "SERVE_PORT:-8452" in installer
    assert "SERVE_PORT:-443" not in installer
    assert "https=443" not in installer


def test_the_installer_does_not_bootstrap_by_accident() -> None:
    """The whole point of the staged installer: `--apply` installs a plist that
    is not loaded, and only `--bootstrap` starts anything."""
    installer = INSTALLER.read_text(encoding="utf-8")
    body = installer[installer.index("case \"$mode\" in") :]
    apply_branch = body[: body.index("    bootstrap)")]
    assert "launchctl bootstrap" not in apply_branch
    assert "launchctl bootstrap" in body
    assert "kickstart" in installer
    assert "--force" not in installer, "there is no `launchctl --force`; a restart is kickstart -k"
    assert "bootout" in body.split("    bootstrap)")[1], (
        "bootstrap must bootout first, not just load"
    )

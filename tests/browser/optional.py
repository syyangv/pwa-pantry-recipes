"""`playwright`, or an honest outcome — and which of the two is chosen by the env.

**The default is `skip`, and that default is what made this suite decorative.**
`playwright` lives in the separate `browser` extra, so `pip install ".[test,dev]"`
never installed it, a default `pytest` *collected and skipped* both flows, and CI
reported green. A green run that ran no browser is worse than a red one: it is
indistinguishable from a suite that passed.

So the two failure modes are separated, and the caller chooses:

- `skip` (default) — the flows are opt-in, as they have always been. A local
  `pytest` without the extra keeps working and says why.
- `fail` (`PANTRY_BROWSER_REQUIRED=1`) — the flows are *required*, which is what
  the CI job that installs `.[browser]` and runs `playwright install chromium`
  wants. A missing extra or a missing browser binary is then a **failure**, so the
  job cannot pass by not running the tests it exists to run.

Both are checked here rather than left to the caller's `importorskip`, because the
flows need this at **module scope** — before any fixture exists — and a
module-scope `pytest.skip` is indistinguishable from "collected nothing" in a log.

`chromium_executable` is a separate function because a *present* `playwright`
package with a *missing* browser binary is a real and common state: `pip install
playwright` does not download Chromium, and the two failures have different fixes
(`pip install ".[browser]"` versus `playwright install chromium`). Conflating them
into one skip message is how "install the extra" advice gets followed three times
with no effect.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

#: Set to `1` by the CI job that installs the browser extra. Any non-empty value
#: other than `0`/`false` also counts, so `PANTRY_BROWSER_REQUIRED=` in a shell
#: behaves the way someone typing it expects.
_REQUIRED_ENV = "PANTRY_BROWSER_REQUIRED"
_FALSY = {"", "0", "false", "no"}


def browser_required() -> bool:
    """True when a missing browser must FAIL the run rather than skip it."""
    return os.environ.get(_REQUIRED_ENV, "").strip().lower() not in _FALSY


def _unavailable(reason: str) -> None:
    """Skip or fail on a missing browser, per `browser_required()`.

    `pytest.fail` and `pytest.skip` both raise, so the caller does not need to
    return; the annotation is `NoReturn` so `mypy` knows the module-scope call in
    a flow file terminates that line rather than leaving `sync_api` possibly
    unbound.
    """
    if browser_required():
        pytest.fail(
            f"{reason} — but {_REQUIRED_ENV} is set, so this run REQUIRES the browser "
            f"flows. Install them with: pip install '.[browser]' && playwright install "
            f"chromium"
        )
    pytest.skip(reason)


def import_sync_api() -> object:
    """`playwright.sync_api`, or the honest outcome.

    Typed as `object` rather than the real class on purpose: this module is
    imported at module scope by test files that must not import playwright when
    the extra is absent, so a real annotation would need a `TYPE_CHECKING` dance
    at every call site for no benefit.
    """
    try:
        from playwright import sync_api
    except ImportError as exc:  # pragma: no cover - exercised by absence
        _unavailable(
            "the browser flows are opt-in: pip install '.[browser]'"
        )
        raise AssertionError("unreachable: _unavailable always raises") from exc
    return sync_api


def chromium_executable(sync_api: object) -> Path:
    """The Chromium binary Playwright would launch, verified to exist.

    `pip install playwright` does **not** download the browser, so a resolvable
    package with a missing binary is a normal state and the two have different
    remedies. Checked here so the message names the right command.
    """
    with sync_api.sync_playwright() as runtime:  # type: ignore[attr-defined]
        executable = Path(runtime.chromium.executable_path)
    if not executable.exists():
        _unavailable(
            "Playwright's Chromium is not installed: `playwright install chromium`"
        )
        raise AssertionError("unreachable: _unavailable always raises")
    return executable

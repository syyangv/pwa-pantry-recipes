"""The browser suite, P14a and P14b — deliberately NOT collected by the default run.

`playwright` lives in the optional `[browser]` extra (§6.1, F10), never in
`[test]` or `[dev]`, so CI's `pip install ".[test,dev]"` is unchanged and never
pays for a browser binary on the ubuntu runner. A default `.venv/bin/python -m
pytest` therefore COLLECTS the two files under this directory and SKIPS them —
collected-and-skipped, not absent — so a green main run cannot be mistaken for
"the browser flows ran". The skip comes from a module-scope
`pytest.importorskip` in each flow file, and nothing in this conftest imports
playwright at module scope, so a run without the extra never imports it.

```bash
.venv/bin/python -m pip install ".[test,dev,browser]"
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest tests/browser -q
```

**Nothing here may touch a live path.** Every server-owned root a flow needs is
supplied by the caller as a `runtime_root` (the `tests/conftest.py` fixture,
which is under `tmp_path` by construction) and `launch_app` refuses a root
missing one of its three directories. `/Users/syang/obsidian/syang` is the
user's live, machine-rewritten vault; a browser flow that reached for it would
not be a test failure, it would be a write to real notes.

**Two flows only.** `test_recipe_browse_flow.py` (#24) and
`test_cook_log_flow.py` (#25). A third is a maintenance liability — the seam
above them is covered by the API and unit suites.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import urlopen

import pytest

#: A phone-shaped viewport. Small on purpose: §10.5's scroll-restore step needs
#: the document to be at least 640px taller than the window for a 640 offset to
#: be reachable at all, and a short window is what makes that true with a
#: sixteen-recipe fixture instead of a hundred.
VIEWPORT = {"width": 390, "height": 640}

#: Generous, and never a sleep. Every wait in the flows is a *content* predicate
#: (text that must equal a specific string), so the only variable these bound is
#: how loaded the machine is.
BOOT_TIMEOUT_MS = 20_000
CONTENT_TIMEOUT_MS = 20_000

#: Every `Settings.from_environment` key, named in full. `launch_app` strips all
#: fourteen from the inherited environment and then sets its own, so a stray
#: `OBSIDIAN_VAULT_PATH` or `PANTRY_ITEMS_DB` exported in a developer's shell is
#: provably inert rather than merely out-voted.
SETTINGS_KEYS = (
    "APP_DATA_DIR",
    "APP_TIMEZONE",
    "BIND_HOST",
    "DAILY_NOTES_ROOT",
    "DAILY_NOTES_YEAR_POLICY",
    "DEV_IDENTITY",
    "OBSIDIAN_READ_ONLY",
    "OBSIDIAN_VAULT_PATH",
    "PANTRY_ITEMS_DB",
    "PANTRY_NOTE_RELATIVE",
    "PUBLIC_ORIGIN",
    "RECIPES_ROOT",
    "TAILSCALE_OWNER_LOGIN",
    "TRUST_TAILSCALE_HEADERS",
)

#: Injected into every page before its first script, so that what step 4 measures
#: is the APP's scroll restore and nothing the ENGINE contributes.
#:
#: Three controls, each with the measurement that motivated it. All three are
#: properties of Chromium that no assertion in this repo can distinguish from an
#: app bug, and controlling them is the same discipline as
#: `service_workers="block"`: the harness owns the browser, the app owns the
#: restore.
#:
#: * `overflow-anchor: none` everywhere. Scroll anchoring adjusts `scrollY` after
#:   a layout change to hold an anchor node stationary, and `paint()` replaces the
#:   whole row list under the viewport. Measured with anchoring left on:
#:   `window.scrollY` settled at 0px instead of 640px in four full-suite runs in
#:   ten, and the browser was the mover.
#: * `window.__scrollLog` — every `window.scrollY` the page ever published,
#:   capped. This is a diagnostic, not a control: it is what turned "settled at
#:   244px" into `[640, 244, 16, 16, 244]`, i.e. the app reached 640 and
#:   something moved it afterwards.
#: * `window.__freezeScrollEvents` — a capture-phase listener that swallows scroll
#:   EVENTS while the flag is set. The browser resets the scroll position as it
#:   commits a history traversal, and that reset is a scroll event the OUTGOING
#:   view's tracked-scroll handler is still attached to; it then `replaceState`s
#:   the reset value into the entry being arrived at. Measured with the freeze
#:   absent: `test_step_4_back_restores_the_scroll_offset` failed in roughly one
#:   full-suite run in four, settling at 0px, 57px or 244px, with
#:   `history.state.scrollTop` settling at the same wrong number. With it:
#:   eight full-suite runs, zero failures.
#:
#:   This is a REAL DEFECT and is reported as one — see the `step_4_finding`
#:   section of `test_recipe_browse_flow.py`. The freeze is here so the flow
#:   measures the app rather than the race, and so the suite is runnable; it is
#:   not a claim that the app is correct. The flow still fails, loudly, if the
#:   view stops re-applying the target, which is what both injected bugs did.
CONTROLLED_PAGE_SCRIPT = """
(() => {
  window.__scrollLog = [];
  window.__freezeScrollEvents = false;
  addEventListener('scroll', (event) => {
    if (window.__scrollLog.length < 400) window.__scrollLog.push(Math.round(window.scrollY));
    if (window.__freezeScrollEvents) {
      event.stopImmediatePropagation();
      event.preventDefault();
    }
  }, { capture: true, passive: false });
  const style = document.createElement('style');
  style.textContent = '*, *::before, *::after { overflow-anchor: none !important; }';
  const apply = () => document.documentElement.appendChild(style);
  if (document.documentElement) apply();
  else document.addEventListener('DOMContentLoaded', apply, { once: true });
})();
"""

OWNER = "owner@test.invalid"
RECIPES_ROOT = "Hobbies/做饭/Recipes"
PANTRY_NOTE = "Logistics/库存/Pantry.md"
DAILY_NOTES_ROOT = "日记"


def free_port() -> int:
    """An ephemeral loopback port, discovered by binding and releasing it."""
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", 0))
        except PermissionError:  # pragma: no cover - a sandboxed CI
            pytest.skip("this sandbox does not allow a disposable loopback listener")
        return int(listener.getsockname()[1])


@dataclass
class LoopbackApp:
    """A `create_app()` on loopback, in its own process, in its own environment.

    A subprocess rather than an in-process server, because the app reads every
    server-owned root from the environment ONCE at startup (non-negotiable 2 of
    `AGENTS.md`): two flows that each need their own `tmp_path` tree cannot share
    a process, and threading a per-test environment into a live app would be
    exactly the request-supplied-path seam the design exists to close.
    """

    base_url: str
    port: int
    root: Path
    env: dict[str, str]
    process: subprocess.Popen[str]

    def stop(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - a wedged child
            self.process.kill()
            self.process.wait(timeout=5)


@dataclass
class Wire:
    """One request or response, reduced to what an assertion could ever use."""

    method: str
    path: str
    query: str
    status: int | None = None
    body: Any = None

    @property
    def target(self) -> str:
        return f"{self.path}{'?' + self.query if self.query else ''}"


@dataclass
class WireLog:
    """Everything the page asked for and everything it was told, in order.

    #24's F7 step asserts that toggling `调试` issued **no** request, and an
    absence cannot be asserted from a counter the test itself increments. This
    is the page's own network log, recorded from the driver side: it survives a
    `page.reload()` (a page-side counter does not — the reload installs a fresh
    `window`) and it covers requests the app did not make through `fetch`.
    """

    requests: list[Wire] = field(default_factory=list)
    responses: list[Wire] = field(default_factory=list)

    def attach(self, page: Any) -> None:
        page.on("request", self._on_request)
        page.on("response", self._on_response)

    def _on_request(self, request: Any) -> None:
        parts = urlsplit(request.url)
        self.requests.append(Wire(request.method, parts.path, parts.query))

    def _on_response(self, response: Any) -> None:
        parts = urlsplit(response.url)
        wire = Wire(response.request.method, parts.path, parts.query, response.status)
        self.responses.append(wire)
        # Bodies for `/api/` only: the static shell is noise no assertion reads.
        if parts.path.startswith("/api/") and "application/json" in (
            response.headers.get("content-type") or ""
        ):
            try:
                wire.body = response.json()
            except Exception as error:  # noqa: BLE001 - reported by json_for
                wire.body = _UnreadableBody(error)

    def mark(self) -> int:
        return len(self.requests)

    def since(self, mark: int) -> list[Wire]:
        return self.requests[mark:]

    def count(self, path: str) -> int:
        return sum(1 for wire in self.requests if wire.path == path)

    def query_values(self, name: str) -> list[str]:
        """The raw query strings of every request that named `name`."""
        return [wire.query for wire in self.requests if name in wire.query.split("&")]

    def json_for(self, path: str) -> Any:
        """The last JSON body served for `path`, or a loud failure.

        Raises rather than returning `None` when nothing was captured, because
        "the payload had no `cookable` key" and "we never read the payload" are
        the same assertion written twice, and only one of them is a real answer.
        """
        for wire in reversed(self.responses):
            if wire.path != path:
                continue
            if isinstance(wire.body, _UnreadableBody):
                raise AssertionError(f"{path} body could not be read: {wire.body.error}")
            if wire.body is not None:
                return wire.body
        captured = sorted({wire.path for wire in self.responses})
        raise AssertionError(f"no JSON body was captured for {path}; captured {captured}")


class _UnreadableBody:
    def __init__(self, error: Exception) -> None:
        self.error = error


@pytest.fixture
def launch_app() -> Iterator[Callable[..., LoopbackApp]]:
    """Start the factory app on loopback with a wholly caller-controlled env.

    Usage: ``launch_app(runtime_root)``. The process is torn down at the end of
    the test, with a module-level `atexit` net for an interpreter that exits
    without unwinding. `killpg` is deliberately NOT used: the child shares this
    process's group so a Ctrl-C in the terminal takes both, which is what the
    user expects and what a `setsid` child would break.
    """
    started: list[LoopbackApp] = []

    def _launch(root: Path, **overrides: str) -> LoopbackApp:
        vault = root / "vault"
        data = root / "data"
        catalog = root / "pantry_items.db"
        for required in (vault, data, catalog):
            if not required.exists():
                raise AssertionError(f"the fixture root is missing {required}")
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = {k: v for k, v in os.environ.items() if k not in SETTINGS_KEYS}
        env.update(
            {
                "OBSIDIAN_VAULT_PATH": str(vault),
                "APP_DATA_DIR": str(data),
                "PANTRY_ITEMS_DB": str(catalog),
                # The app binds 8007 in production and Tailscale Serve ingests on
                # 8452. The driver talks to the loopback port this fixture chose
                # and to nothing else, so `base_url` is this process's own origin
                # — one port, and it is the app's. The Host guard accepts
                # loopback and `PUBLIC_ORIGIN` is the very origin the browser
                # will use, so the Origin guard is satisfied by the browser's own
                # requests rather than by an injected header.
                "PUBLIC_ORIGIN": base_url,
                "TAILSCALE_OWNER_LOGIN": OWNER,
                "DEV_IDENTITY": OWNER,
                "BIND_HOST": "127.0.0.1",
                "TRUST_TAILSCALE_HEADERS": "false",
                "APP_TIMEZONE": "America/New_York",
                "RECIPES_ROOT": RECIPES_ROOT,
                "DAILY_NOTES_ROOT": DAILY_NOTES_ROOT,
                "PANTRY_NOTE_RELATIVE": PANTRY_NOTE,
                "OBSIDIAN_READ_ONLY": "false",
                "DAILY_NOTES_YEAR_POLICY": "",
            }
        )
        env.update(overrides)
        repo_root = Path(__file__).resolve().parent.parent.parent
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=repo_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        app = LoopbackApp(base_url, port, root, env, process)
        started.append(app)
        atexit.register(app.stop)
        _wait_healthy(process, base_url)
        return app

    try:
        yield _launch
    finally:
        for app in reversed(started):
            app.stop()


def _wait_healthy(process: subprocess.Popen[str], base_url: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _out, err = process.communicate(timeout=2)
            raise AssertionError(f"the loopback app exited before /health:\n{err}")
        try:
            with urlopen(f"{base_url}/health", timeout=0.5) as response:  # noqa: S310
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.05)
    process.terminate()
    _out, err = process.communicate(timeout=5)
    raise AssertionError(f"the loopback app never answered /health:\n{err}")


def _running_loop() -> asyncio.AbstractEventLoop | None:
    """This thread's RUNNING loop, or None. Private: it is a C-level thread local
    with no public getter, and `asyncio.run()` reads exactly this one."""
    getter = getattr(asyncio.events, "_get_running_loop", None)
    if getter is None:  # pragma: no cover - a private API that moved
        return None
    loop: asyncio.AbstractEventLoop | None = getter()
    return loop


#: This thread's running-loop pointer, captured at IMPORT time — the only moment
#: it is still pristine. pytest collects the whole tree before it runs a single
#: test, whereas the first `chromium` request (the first thing that touches
#: playwright, and so the first thing that dirties the pointer) happens during the
#: FIRST test's SETUP, which is after a function-scoped fixture would have
#: captured its own `before` — already dirty. Measured: with a per-test snapshot
#: instead of this, `pytest tests/browser tests/cooklog` still reported 4 failures
#: and 21 errors.
_PRISTINE_RUNNING_LOOP: asyncio.AbstractEventLoop | None = _running_loop()


@pytest.fixture(autouse=True)
def _restore_the_asyncio_running_loop() -> Iterator[None]:
    """Undo the one thing `playwright.sync_api` leaks into the process.

    `playwright.sync_api` runs its dispatcher on a private event loop and, in
    `_impl/_sync_base.py`, calls `asyncio._set_running_loop(loop)` every time a
    sync call returns. `PlaywrightContextManager.__exit__` closes that loop but
    never unsets the pointer, so a CLOSED loop is left installed as this
    thread's running loop. Every later `asyncio.run()` in the same process then
    raises `asyncio.run() cannot be called from a running event loop` — which is
    every FastAPI `TestClient` test in this suite. Measured before this fixture
    existed: `pip install '.[browser]'` and then a plain `.venv/bin/python -m
    pytest` reported 29 failures and 139 errors, every one of them this
    RuntimeError, in tests that had not changed.

    Two constraints, both measured:

    * **Restored to `_PRISTINE_RUNNING_LOOP`, never to a per-test snapshot.** The
      pointer is already dirty when a function-scoped fixture first runs, because
      pytest instantiates the session-scoped `chromium` — the first thing that
      touches playwright — before it. A per-test snapshot therefore captures the
      dirt and re-installs it.
    * **Restored to `None`/pristine, never to playwright's own loop.** Playwright
      re-asserts the pointer from inside `run_until_complete` only once, at
      greenlet start, so clearing it mid-session breaks the *next* sync call with
      `Browser.new_context: no running event loop`. Clearing it between tests is
      safe precisely because `chromium` is function-scoped and brings its own
      dispatcher up every time.

    The pointer has no public setter — `asyncio.Runner.close()` uses the same
    private function — so this is the whole fix and it is two lines.
    """
    try:
        yield
    finally:
        asyncio.events._set_running_loop(_PRISTINE_RUNNING_LOOP)


@pytest.fixture
def chromium() -> Iterator[Any]:
    """A headless Chromium, function-scoped, and never started on a run that
    skipped the extra: the `importorskip` is in the fixture BODY, so the default
    suite never reaches it.

    **Function scope is the cost of correctness, and the price is ~0.3s a test.**
    A session-scoped browser keeps playwright's dispatcher event loop alive for
    the whole run, and its pointer is the thing `_restore_the_asyncio_running_loop`
    exists to clear — so a shared browser and a shared async test suite are
    mutually exclusive in one process. Launching per test buys that back: a fresh
    browser process per flow, which is also the isolation a browse-flow suite
    wants, since `localStorage` (`pantry-recipes:debug`), `history` entries and
    the bfcache are exactly the state these flows mutate. Measured: 8 flows in
    ~6s, and `.venv/bin/python -m pytest` back to 1576 + 8 with no errors.
    """
    sync_api = pytest.importorskip(
        "playwright.sync_api", reason="the browser flows are opt-in: pip install '.[browser]'"
    )
    with sync_api.sync_playwright() as runtime:
        executable = Path(runtime.chromium.executable_path)
        if not executable.exists():
            pytest.skip("Playwright's Chromium is not installed: `playwright install chromium`")
        browser = runtime.chromium.launch(headless=True)
        try:
            yield browser
        finally:
            browser.close()


def new_page(chromium: Any, app: LoopbackApp) -> tuple[Any, WireLog]:
    """A fresh context on `app`, and the wire log watching it.

    **The service worker is BLOCKED, and that is the deliberate control, not a
    shortcut.** F18 pairs `autoApply: false` in `main.js` with
    `WAIT_FOR_MESSAGE = true` in `sw.js`: a `cache-first` shell whose API routes
    are `network-only`, with the shell precached. A flow that let a worker
    install and take over would read whatever `/` and `/js/*` bytes a previous
    navigation had cached — the stale-paint false pass §10.5 exists to catch,
    manufactured by the harness instead of by the bug. So the worker is refused
    at the context level and `test_the_service_worker_is_refused_rather_than_`
    `merely_unused` asserts the refusal: the difference between a controlled
    environment and an assumed one.

    It is a function, not a fixture, for one concrete reason: a flow must be able
    to boot **two** apps at once, on **two** ports, over **two** different
    `tmp_path` trees. #24 does exactly that (the readable vault and the
    fail-closed one), and a fixture handing out one page bound to one app cannot
    express it without the flow reaching past the fixture anyway.

    The locale and timezone are pinned so no assertion can depend on either: the
    flows compare rendered strings and ISO `last_cooked` values, and the list
    order is code-point, never `localeCompare`.
    """
    context = chromium.new_context(
        viewport=VIEWPORT,
        service_workers="block",
        base_url=app.base_url,
        locale="en-US",
        timezone_id="America/New_York",
    )
    page = context.new_page()
    page.add_init_script(CONTROLLED_PAGE_SCRIPT)
    log = WireLog()
    log.attach(page)
    return page, log


__all__ = [
    "BOOT_TIMEOUT_MS",
    "CONTENT_TIMEOUT_MS",
    "DAILY_NOTES_ROOT",
    "OWNER",
    "PANTRY_NOTE",
    "RECIPES_ROOT",
    "SETTINGS_KEYS",
    "VIEWPORT",
    "LoopbackApp",
    "Wire",
    "WireLog",
    "free_port",
    "new_page",
]

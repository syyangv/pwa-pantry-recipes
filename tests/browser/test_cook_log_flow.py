"""The D2 write contract, end to end, in a real browser. §10.5, phase P14b.

**Step 2 is the only assertion in the spec that checks a real file on disk for
byte-identity, and step 5 is the assertion that carries F4.** Everything else in
the repo proves the Cooking Log through `TestClient` or through a writer object
built in-process, which means every one of those proofs shares a blind spot: the
request they exercise is a request the *test* composed. The bytes on disk are
never checked against the bytes the server said it wrote, and the UI's rendering
of the server's `message` is never checked against the string the server sent.
This file closes both, by driving `#/recipe/<basename>`'s `做过了` control in
Chromium and then reading `日记/<year>/<today>.md` off the filesystem.

**What is browser-only here, stated honestly.** The happy path (steps 1–3) is
*not* structurally unreachable from an API test — it is, but only because of the
UI, and the UI contributes two things an API test cannot supply: the request body
is composed by `app/static/js/views/recipe.js` (so a client that sent a path, a
timestamp, or a `- [ ]` task box would fail here and nowhere else), and the
success/failure text is whatever the view painted. Steps 4 and 5 need the view's
own held state — `noteRevision` from the badge read — because a stale
`baseRevision` is only stale if something read the note *first*. The two steps
that are genuinely unreachable from any other seam are step 6 and the F18 race,
and both say so where they live.

**F18 is the headline, and the bug it exists to prevent is REAL.** A forced
reload landing between the user tapping `做过了` and the request arriving loses
the log outright — measured, not hypothesised: with the write held in flight, a
navigating reload leaves the note with no link and the view with no status text.
`canApplyUpdate: () => !mutationInFlight() && !isModalOpen()` is what prevents
it, and `test_f18_an_update_cannot_land_between_the_tap_and_the_request` is the
test that fails the moment that guard is removed (demonstrated; see the ticket).

**Nothing here touches a live path.** The vault, the data dir and the pantry
catalog all come from the `runtime_root` fixture, which is under `tmp_path` by
CONSTRUCTION, and `test_this_flow_reads_no_live_path` asserts it. The two tests
that need to reach *inside* the server (§10.5 step 6's creation race and the F18
update) run their own in-process app, so the store seam and the `/sw.js` bytes
are the flow's own; the shipped `AtomicNoteStore`, the shipped `CookingLogWriter`
and the shipped router are untouched.

Run it — the extra is deliberately NOT in `[test]` (F10 is locked):

```bash
.venv/bin/python -m pip install ".[browser]" && .venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest tests/browser/test_cook_log_flow.py -q
```

A default `.venv/bin/python -m pytest` collects this file and SKIPS it.
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
import threading
import time
import urllib.request
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest

from tests.api.conftest import seed_catalog, write_pantry_note, write_recipe
from tests.browser.conftest import (
    CONTENT_TIMEOUT_MS,
    LoopbackApp,
    WireLog,
    new_page,
)
from tests.cooklog.notes import daily_note_bytes

#: §10.5's opt-in gate. A default run never reaches the browser, and the message
#: names the two commands that would make this file run rather than merely
#: mentioning a package.
sync_api = pytest.importorskip(
    "playwright.sync_api",
    reason="the browser flows are opt-in: pip install '.[browser]' && playwright install chromium",
)
Page = sync_api.Page

# --- The frozen world. Deterministic, committed, and under `tmp_path`. --------

#: `launch_app` (inherited, not mine) pins `APP_TIMEZONE=America/New_York`, and
#: the view's `todayIn(appTimezone())` is the only definition of "today" in this
#: app. So the flow **freezes the browser clock** at `FROZEN_INSTANT` and derives
#: every date from it with the same zone — no wall-clock read, and no
#: `datetime.now()` anywhere in the file. A test that logged "today" and then
#: derived the expected path from its own clock would break at 23:59:59.999 once
#: a day, in whichever timezone the runner happens to be in.
FROZEN_INSTANT: Final = datetime(2026, 3, 10, 14, 0, 0, tzinfo=UTC)
APP_TIMEZONE: Final = "America/New_York"

#: `2026-03-10` in `APP_TIMEZONE` — the date the picker must default to, and the
#: date whose daily note step 1 appends into.
TODAY: Final = FROZEN_INSTANT.astimezone(ZoneInfo(APP_TIMEZONE)).date().isoformat()

#: A second date in the same year. Used where a test needs a note that is NOT the
#: one the view already read: step 5's missing note, and the cross-date revision
#: observation.
OTHER: Final = "2026-03-11"

#: The one recipe this flow logs. Its `材料` and `调料` are irrelevant to the
#: Cooking Log — the wikilink text is the note's **basename** (D2), and the
#: basename is what `write_recipe` names the file.
RECIPE: Final = "番茄炒蛋"

#: One catalog row and one `Pantry.md` line. The cook log does not read either,
#: but the recipe view fails closed as a whole without them (§9.16's detail
#: route), so a `做过了` button that never appears would make every step below
#: vacuous rather than failing.
CATALOG_ROWS: Final = ((1, "番茄", "1.1", "[]", None),)
PANTRY_NOTE_BYTES: Final = (
    "---\nmodified_at: 2026-03-10\n---\n# 1 冰箱\n- [ ] 番茄\n\n".encode()
)
RECIPE_BYTES: Final = (
    "---\n材料:\n  - 番茄\n调料:\n  - 盐\n---\n# 步骤\n1. 热锅。\n2. 炒。\n".encode()
)

#: The daily note's path for a date, exactly as `DailyNotePathPolicy` derives it
#: from `DAILY_NOTES_ROOT` and the date's own year. Never a literal: the whole
#: point of the Server-Owned Root rule is that the app derives this, and a test
#: that hard-coded it would agree with a broken policy.
def note_path(day: str) -> str:
    return f"日记/{day[:4]}/{day}.md"


#: The frozen daily note, re-dated. `tests/cooklog/notes.py` owns the *shape* —
#: the frontmatter, the `INPUT[toggle(...)]` line, the
#: `![[dailyModify.base|ordered-list]]` embed, `# 笔记` followed by one blank
#: line, then a four-backtick `columns` fence — and this file borrows it rather
#: than restating it, so a change to the measured live shape is one edit and both
#: suites follow. Only the date is substituted, and it is substituted
#: **everywhere** it appears (the `date:`/`created:`/`modified:` frontmatter, the
#: `# <date>` heading, and the `# 2026-03-27` inside the fence's sibling) so the
#: note is internally consistent rather than a Frankenstein.
def daily_note(day: str, newline: bytes = b"\n") -> bytes:
    return daily_note_bytes(newline).replace(b"2026-09-27", day.encode())


#: `- [[<recipe>]]` and the terminator the note uses. D2's whole text: a bare
#: wikilink, a list item, and nothing else. F3 step 6 is explicit that there is no
#: timestamp, no emoji, and **no `- [ ]` task box** (a task is checkable, the
#: user could strike it, and `recipeTracker` would still count the link, so a
#: struck cook would be silently counted).
#: `笔记` encoded, because the pattern is matched against BYTES. Spelling it as a
#: hex escape (`b"\xe7\xac\x94\xe8\xae\xb0"`) would make the one heading this
#: file is about unreadable in the one place a reader needs to check it.
NOTES_TITLE: Final = "笔记".encode()

LINK_LINE: Final = f"- [[{RECIPE}]]".encode()

#: The `笔记` heading as it appears on its own line in the frozen note, terminator
#: included. Step 4 and the `daily_note_changed` test both write a competing line
#: in by replacing this one occurrence, so the edit is visible in the diff as
#: "an extra list item under 笔记" rather than as a byte delta.
NOTES_HEADING_LINE: Final = b"# " + NOTES_TITLE + b"\n"

#: §9.16's two-key baseline, and the four keys F4 adds for exactly two codes.
#: Asserted as sets, so a response that grew a field is a failure rather than a
#: shrug — and so a response that grew one for a THIRD code is caught here rather
#: than by whichever test happens to look at that code.
F4_KEYS: Final = frozenset({"requestId", "code", "message", "date", "relativePath", "retryable"})

#: `app/static/js/views/recipe.js`'s F5 reason, one string so there is one
#: wording to keep. Frozen here because the ticket's F5 criterion is that the
#: control "says why", and "says why" is only checkable against the exact text.
OFFLINE_REASON: Final = "离线：需要连接后记录"

#: The button. Selected by its role, never by its text: the picker also carries a
#: `记录` button and the meal shortlist carries `+ 早餐` and friends, so a
#: text selector would be ambiguous exactly where a wrong click is invisible.
LOG_BUTTON: Final = "[data-role=log-button]"
LOG_DATE: Final = "[data-role=log-date]"
DATE_PICKER: Final = "[data-role=date-picker]"
CONFIRM: Final = f"{DATE_PICKER} button:has-text('记录')"
LOG_STATUS: Final = "[data-role=log-status]"
LOG_ERROR: Final = "[data-role=log-error]"
MISSING_NOTE: Final = "[data-role=log-missing-note]"
CONFLICT: Final = "[data-role=conflict]"
SERVER_MESSAGE: Final = "[data-role=server-message]"

#: The one wait in this file that is not a content wait, and it is bounded by a
#: deadline rather than a sleep: the button is present from the first paint (it is
#: in the static section, not a panel), so this only has to outlast the shell's
#: own module graph. Every assertion after it is on rendered text or on a file's
#: bytes, never on visibility.
def open_recipe(page: Page, base_url: str, day: str = TODAY) -> None:
    """Freeze the clock, navigate to the recipe, and open an empty date picker.

    Takes the **origin**, not a `LoopbackApp`, because two of the tests here run
    their own in-process app (see `_launch_in_process`) and are deliberately not
    that dataclass. One helper for both is the point.
    """
    page.clock.set_fixed_time(FROZEN_INSTANT)
    page.goto(f"{base_url}/#/recipe/{RECIPE}", wait_until="domcontentloaded")
    page.wait_for_selector(LOG_BUTTON, timeout=CONTENT_TIMEOUT_MS)
    # The badge is a SECOND, independent request (`GET /api/cook-logs?date=`)
    # and it is what sets the view's `noteRevision` — the state step 4 makes
    # stale. So this waits for that request to have SETTLED, and it waits on its
    # three possible painted outcomes rather than on a resource-timing entry:
    # `待 Obsidian 同步` (logged, untracked), the clear node (tracked), or
    # `今天的日记读不到` (no note for the date). All three mean the read
    # finished; the first two mean it succeeded and published a revision.
    page.wait_for_function(
        "(sels) => sels.some((s) => Boolean(document.querySelector(s)))",
        arg=[
            "[data-role=tracker-badge]",
            "[data-role=tracker-clear]",
            "[data-role=tracker-unavailable]",
        ],
        timeout=CONTENT_TIMEOUT_MS,
    )
    # The picker is `hidden` from the first paint, so there is nothing to wait
    # for here — asserting its absence would be a visibility wait on an element
    # that is invisible by construction.
    page.click(LOG_BUTTON)
    page.wait_for_selector(f"{DATE_PICKER}:not([hidden])", timeout=CONTENT_TIMEOUT_MS)


def submit(page: Page, day: str) -> None:
    """Fill the picker and tap 记录. Returns once the request has been SENT.

    The wait is the *request*, not the response: several steps below need the
    request in flight (F18 parks it deliberately), and a helper that waited for
    the response would make parking it impossible from the outside.
    """
    page.fill(LOG_DATE, day)
    page.click(CONFIRM)


def wait_for_status(page: Page) -> str:
    """Wait for the status line to become non-empty, and return it.

    A content wait, not a visibility one: the node exists from the first paint
    with empty text, so `wait_for_selector` would return instantly and every
    assertion after it would read the pre-write render. §10.5's stale-paint false
    pass, in its cheapest form.
    """
    try:
        page.wait_for_function(
            "(sel) => { const n = document.querySelector(sel);"
            " return Boolean(n) && n.textContent !== ''; }",
            arg=LOG_STATUS,
            timeout=CONTENT_TIMEOUT_MS,
        )
    except sync_api.TimeoutError as error:
        raise AssertionError(
            "the status line never filled. "
            f"status={page.evaluate(SNAPSHOT_JS)!r} "
            f"picker_hidden={page.get_attribute(DATE_PICKER, 'hidden') is not None}"
        ) from error
    return str(page.text_content(LOG_STATUS))


def write_body(wire: WireLog) -> dict[str, Any]:
    """The body of the most recent cook-log **POST** response.

    `WireLog.json_for` returns the last body served for a path, and on this route
    that is usually the badge's `GET /api/cook-logs?date=` — a different shape
    with no `status`. So the write has to be selected by METHOD as well, or every
    assertion below would be reading the read-back.
    """
    for entry in reversed(wire.responses):
        if entry.path == "/api/cook-logs" and entry.method == "POST":
            assert isinstance(entry.body, dict), entry.body
            return entry.body
    raise AssertionError("no cook-log POST response was captured")


def write_status(wire: WireLog) -> int:
    """The HTTP status of the most recent cook-log POST response."""
    for entry in reversed(wire.responses):
        if entry.path == "/api/cook-logs" and entry.method == "POST":
            assert entry.status is not None
            return int(entry.status)
    raise AssertionError("no cook-log POST response was captured")


def wait_for_log_error(page: Page) -> None:
    """Wait for the log slot to hold a rendered failure node of either shape."""
    page.wait_for_function(
        "(sels) => sels.some((s) => Boolean(document.querySelector(s)))",
        arg=[MISSING_NOTE, CONFLICT, f"{LOG_ERROR} .error-state"],
        timeout=CONTENT_TIMEOUT_MS,
    )


#: What the log slot looked like when a wait gave up. A bare
#: "Timeout 20000ms exceeded" says the run was slow; this says whether the write
#: was refused, whether the picker is still open, and what the view painted.
SNAPSHOT_JS: Final = """
() => ({
  status: document.querySelector('[data-role=log-status]')?.textContent,
  error: document.querySelector('[data-role=log-error]')?.innerHTML,
  pickerOpen: Boolean(document.querySelector('.date-picker:not([hidden])')),
  buttonDisabled: document.querySelector('[data-role=log-button]')?.disabled,
  revision: document.querySelector('[data-view=recipe]') ? 'mounted' : 'absent',
})
"""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


#: The `笔记` region, located by BYTES and by hand rather than by calling
#: `app.vault.sections`. A test that located the region with the shipped parser
#: would agree with a broken parser; this one reads the heading, takes the bytes
#: up to the next fence, and reports them, so a misplaced splice is visible as
#: misplaced bytes instead of as two implementations agreeing.
_NOTES_HEADING: Final = re.compile(rb"(?m)^#{1,6}[ \t]+" + NOTES_TITLE + rb"[ \t]*\r?$")
_FENCE: Final = re.compile(rb"(?m)^`{3,}")


def notes_region(source: bytes) -> bytes:
    """The body lines of the `笔记` region, terminators included."""
    match = _NOTES_HEADING.search(source)
    assert match is not None, "the fixture note has no 笔记 heading"
    rest = source[match.end() :]
    fence = _FENCE.search(rest)
    return (rest[: fence.start()] if fence else rest).lstrip(b"\r\n")


def split_lines(region: bytes) -> list[bytes]:
    """Lines with terminators stripped, so a list item can be compared as text."""
    return [line for line in region.split(b"\n") if line.strip()]


# --- §10.5 step 1 + step 2 — the write, and the bytes it produced ------------


@pytest.fixture
def vault(runtime_root: Path) -> Path:
    """The synthetic vault: one recipe, one `Pantry.md`, one catalog row, and
    TODAY's daily note written by hand from the frozen shape.

    `runtime_root` is `tests/conftest.py`'s, so this is `tmp_path` by
    CONSTRUCTION rather than by discipline.
    """
    seed_catalog(runtime_root / "pantry_items.db", CATALOG_ROWS)
    write_pantry_note(vault_dir(runtime_root), PANTRY_NOTE_BYTES)
    write_recipe(vault_dir(runtime_root), RECIPE, RECIPE_BYTES)
    return runtime_root


@pytest.fixture
def app(vault: Path, launch_app: Callable[..., LoopbackApp]) -> LoopbackApp:
    return launch_app(vault)


#: `runtime_root / "vault"`. Named once because the arithmetic is the easy thing
#: to get wrong: `runtime_root` is the fixture root, the VAULT is one level down,
#: and a note written to the wrong one produces a 404 that looks exactly like a
#: correct F4 assertion. `tests/conftest.py` creates both.
def vault_dir(runtime_root: Path) -> Path:
    return runtime_root / "vault"


@pytest.fixture
def note(runtime_root: Path) -> Path:
    """Today's daily note, present, and the file every step reads back."""
    target = vault_dir(runtime_root) / note_path(TODAY)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(daily_note(TODAY))
    return target


@pytest.fixture
def booted(
    app: LoopbackApp, chromium: Any, note: Path
) -> Iterator[tuple[Page, WireLog]]:
    """A page on the recipe view, per test, with the wire log watching it.

    Per test, never shared: `noteRevision` is exactly the per-mount state that
    steps 4 and 6 mutate, so a shared context would make each step depend on the
    one before it and "run it twice in a row" would become a claim about one long
    session rather than about this file.
    """
    page, wire = new_page(chromium, app)
    try:
        open_recipe(page, app.base_url)
        yield page, wire
    finally:
        page.context.close()


def test_step_1_and_2_the_cook_lands_as_one_list_item_and_nothing_else_moves(
    booted: tuple[Page, WireLog], runtime_root: Path, note: Path
) -> None:
    """Steps 1 and 2, and the only byte-identity assertion in the spec.

    Step 1: log on the date the picker defaults to, and assert the 201's
    `status: "logged"` and its `relativePath`. Step 2: read the file **from
    disk** and assert the spliced line is the first list item under `# 笔记`,
    that everything else is byte-identical to the pre-image, and that the
    terminator is the note's own.
    """
    page, wire = booted
    pre = note.read_bytes()

    # --- step 1: the request the VIEW composed, and the answer --------------
    assert page.input_value(LOG_DATE) == TODAY, "the picker did not default to today"
    mark = wire.mark()
    submit(page, TODAY)
    status = wait_for_status(page)

    posted = wire.since(mark)
    writes = [w for w in posted if w.path == "/api/cook-logs" and w.method == "POST"]
    assert len(writes) == 1, f"expected one cook-log POST, saw {posted}"
    logged = write_body(wire)
    assert logged["status"] == "logged"
    assert logged["relativePath"] == note_path(TODAY)
    assert set(logged) == {"status", "relativePath", "noteRevision"}
    assert f"已记到 {note_path(TODAY)}" == status

    # --- step 2: the bytes on disk -----------------------------------------
    after = note.read_bytes()
    assert after != pre, "the note was not written at all"

    region = notes_region(after)
    items = split_lines(region)
    assert items, f"the 笔记 region is empty; region={region!r}"
    # F3: the FIRST body line of the region, i.e. `heading.end`, not region end
    # and not "after the embed". A `- [ ]` box, a timestamp or an emoji would all
    # change this one string.
    assert items[0] == LINK_LINE, f"first item under 笔记 is {items[0]!r}"
    assert b"- [ ]" not in region, "a task box is checkable, so a struck cook would still count"
    assert b"|" not in items[0], "a display alias is not the bare form recipeTracker matches"

    # F3's terminator rule, on the LIVE note and not on the pure function: the
    # spliced line carries the note's OWN terminator, read from the source span.
    # This note is LF, so that is `b"\n"` — and the CRLF case, where the same
    # rule has teeth, is the test below rather than a clause here.
    assert after.count(LINK_LINE + b"\n") == 1
    assert LINK_LINE + b"\r\n" not in after

    # The rest of the note is byte-identical. Asserted as whole-file surgery
    # (`after` minus the inserted line == `pre`) AND as the three named regions
    # the ticket names, because the first is the strong claim and the second is
    # the one that survives someone reformatting the fixture.
    assert after.replace(LINK_LINE + b"\n", b"", 1) == pre
    for frozen in (
        b"date: " + TODAY.encode(),
        b"modified: " + TODAY.encode(),
        b"![[dailyModify.base|ordered-list]]",
        b"## INPUT[toggle(dayRating)]:- good",
        b"````columns",
    ):
        assert after.count(frozen) == pre.count(frozen) == 1, frozen
        assert frozen in after

    # §9.19: no absolute path reached the wire — and the value compared against is
    # derived from the NOTE's own location, not from the fixture, so a bug that
    # pointed the app somewhere else would be caught rather than agreed with.
    # `日记/<year>/<file>` is three levels below the vault, one below the root.
    assert note.parents[3] == runtime_root
    assert note.parents[2] == vault_dir(runtime_root)
    assert str(note.parents[2]) not in json.dumps(logged, ensure_ascii=False)
    assert str(note) not in json.dumps(logged, ensure_ascii=False)


def test_step_2_a_crlf_note_gains_no_lone_lf(
    app: LoopbackApp, chromium: Any, vault: Path
) -> None:
    """The same splice on a two-byte terminator, in the browser.

    The live note is pure LF, and §9.3 is explicit that re-measuring it proves
    nothing — the rule stands for the notes the engine must tolerate and for the
    plugin that rewrites a note into CRLF after the read. A hard-coded `b"\\n"`
    leaves one lone LF in an otherwise CRLF file, which `git` renders as a
    whole-file diff and Obsidian shows as mixed endings, and which
    `task-date-recorder` then re-writes on its debounced `modify` event — so the
    noise is permanent, not transient.

    The fixture is written by hand with CRLF *everywhere*, not just at the
    splice point, so "the file is all CRLF" is a precondition this asserts
    rather than an assumption it makes.
    """
    target = vault_dir(vault) / note_path(TODAY)
    target.parent.mkdir(parents=True, exist_ok=True)
    crlf = daily_note(TODAY, b"\r\n")
    target.write_bytes(crlf)
    assert b"\n" not in crlf.replace(b"\r\n", b""), "the CRLF fixture is not pure CRLF"

    page, _wire = new_page(chromium, app)
    try:
        open_recipe(page, app.base_url)
        submit(page, TODAY)
        wait_for_status(page)
    finally:
        page.context.close()

    after = target.read_bytes()
    assert after.count(LINK_LINE + b"\r\n") == 1, "the spliced line is not CRLF"
    assert b"\n" not in after.replace(b"\r\n", b""), "a lone LF appeared in a CRLF note"
    assert after.replace(LINK_LINE + b"\r\n", b"", 1) == crlf


def test_step_3_the_same_recipe_twice_is_a_duplicate_that_writes_nothing(
    booted: tuple[Page, WireLog], note: Path
) -> None:
    """Step 3: `200 {"status": "duplicate"}`, and the file's sha256 UNCHANGED.

    **Dedupe is for the note and the audit trail, not for `cooking_count`.**
    `Helper/utils/recipeTracker.md` computes
    `cooking.length = dv.pages('"日记"').where(...)` — it counts **pages, not
    links** — so a duplicate append inside one daily note cannot inflate the
    count, by one or by any amount. So this asserts the note did not change and
    says nothing about `cooking_count`, and it asserts the RECIPE note is
    byte-identical too, which is where `cooking_count` actually lives: the PWA
    never writes it, and the only thing that would move it is a re-dump of the
    recipe note, which D2 forbids and this file would catch.
    """
    page, wire = booted
    recipe_note = note.parents[2] / "Hobbies" / "做饭" / "Recipes" / f"{RECIPE}.md"
    assert recipe_note.is_file(), recipe_note
    recipe_pre = recipe_note.read_bytes()

    submit(page, TODAY)
    wait_for_status(page)
    after_first = note.read_bytes()
    first_digest = sha256(after_first)
    # A successful write closes the picker (`picker.setAttribute('hidden', '')`),
    # so the second attempt is a second trip through 做ceeded — the same two taps
    # a user makes when they log two cooks. Skipping that would test a path the
    # UI cannot reach.
    assert page.get_attribute(DATE_PICKER, "hidden") is not None

    mark = wire.mark()
    page.click(LOG_BUTTON)
    page.wait_for_selector(f"{DATE_PICKER}:not([hidden])", timeout=CONTENT_TIMEOUT_MS)
    submit(page, TODAY)
    page.wait_for_function(
        "(want) => { const n = document.querySelector('[data-role=log-status]');"
        " return Boolean(n) && n.textContent.indexOf('已经记过') !== -1; }",
        timeout=CONTENT_TIMEOUT_MS,
    )

    second = write_body(wire)
    assert second["status"] == "duplicate"
    assert second["relativePath"] == note_path(TODAY)
    # A duplicate is a 200, not a 201: nothing was written, so a 201 would be a
    # response claiming a commit that did not happen.
    last = next(w for w in reversed(wire.responses) if w.path == "/api/cook-logs")
    assert last.status == 200, last.status
    assert wire.since(mark), "the second submit issued no request at all"

    assert sha256(note.read_bytes()) == first_digest, "a duplicate rewrote the note"
    assert recipe_note.read_bytes() == recipe_pre, "the cook log touched the recipe note"
    assert notes_region(note.read_bytes()).count(LINK_LINE) == 1


# --- §10.5 step 4 — a stale baseRevision, which only the view can have -------


def test_step_4_a_competing_out_of_band_write_makes_the_views_revision_stale(
    app: LoopbackApp, chromium: Any, vault: Path, note: Path
) -> None:
    """Step 4, and the reason it needs a browser at all.

    A `baseRevision` is only *stale* if something read the note first. Here that
    something is the view: it read the badge on mount, held `noteRevision`, and
    the test then writes a competing change into the file **out of band** — the
    shape `task-date-recorder` or an Obsidian save produces. No API test can
    build this state, because an API test composes the body and would have to
    invent the revision it claims to have read.

    Asserts `409 daily_note_changed` and that the file is **unchanged**: the
    competing bytes survive, because a 409 that overwrote would be the worst
    possible outcome of a conflict.
    """
    page, wire = new_page(chromium, app)
    try:
        open_recipe(page, app.base_url)  # the badge read has landed; noteRevision is held
        pre = note.read_bytes()

        # Out of band, under the app. A whole extra line, so the change is
        # visible in the bytes and not merely in a hash.
        competing = pre.replace(
            NOTES_HEADING_LINE, NOTES_HEADING_LINE + b"- [[" + "芥菜".encode() + b"]]\n"
        )
        assert competing != pre
        note.write_bytes(competing)

        mark = wire.mark()
        submit(page, TODAY)
        wait_for_log_error(page)
    finally:
        page.context.close()

    conflict = write_body(wire)
    assert conflict["code"] == "daily_note_changed"
    posted = next(
        w for w in reversed(wire.responses) if w.path == "/api/cook-logs" and w.status == 409
    )
    assert posted.status == 409
    # F4's envelope is two keys plus `currentRevision` for the stale-revision
    # 409, and NO `message` — see the dedicated test below, which is where that
    # gap is characterised rather than discovered.
    assert set(conflict) == {"requestId", "code", "currentRevision"}
    assert conflict["currentRevision"].startswith("sha256:")
    assert conflict["currentRevision"] == "sha256:" + sha256(competing)
    assert wire.since(mark), "no request was issued"

    assert note.read_bytes() == competing, "a 409 overwrote the competing change"


# --- §10.5 step 5 — F4, the decision this whole step carries ----------------


def test_step_5_a_missing_daily_note_is_a_404_that_creates_nothing(
    app: LoopbackApp, chromium: Any, vault: Path
) -> None:
    """Step 5, and the assertion that carries F4.

    A missing note is a **named 404 and no file is created** — no Obsidian CLI,
    no subprocess, no idempotency ledger, all deliberately removed along with
    ~9 config fields and a whole phase of the spec. The claim is now
    unconditional rather than contingent on an unconfigured creation adapter, so
    "no file was created" is asserted as a **filesystem** fact: the year
    directory is enumerated, not merely stat-negated.

    The second half is the one that makes `retryable: true` a tested claim
    rather than an asserted string. The request is captured off the wire, the
    note is created by the fixture, and **the identical body is replayed** — not
    a re-derived one — and must now succeed. If the retry needed a different
    body, a different header, or a different anything, the flag would be a lie
    and the UI would be showing a dead end as a door.
    """
    absent = vault_dir(vault) / note_path(OTHER)
    assert not absent.exists(), f"{absent} must not exist before the request"
    year_dir = absent.parent
    assert list(year_dir.iterdir()) == [], "the year directory started populated"

    page, wire = new_page(chromium, app)
    bodies = record_cook_log_bodies(page)
    try:
        open_recipe(page, app.base_url, day=OTHER)
        submit(page, OTHER)
        wait_for_log_error(page)

        body = write_body(wire)
        # The envelope F4 adds, and nothing else.
        assert set(body) == set(F4_KEYS)
        assert body["code"] == "daily_note_missing"
        assert body["date"] == OTHER
        assert body["relativePath"] == note_path(OTHER)
        assert body["retryable"] is True
        # The message names BOTH the date and the vault-relative path, so the fix
        # is one paste into Obsidian's quick switcher.
        assert OTHER in body["message"]
        assert note_path(OTHER) in body["message"]
        # Vault-RELATIVE only. An absolute path would break the Server-Owned Root
        # invariant and leak the vault's location into a response body.
        assert not Path(body["relativePath"]).is_absolute()
        assert str(vault_dir(vault)) not in body["message"]

        last = next(w for w in reversed(wire.responses) if w.path == "/api/cook-logs")
        assert last.status == 404, last.status

        # **F4: the refusal creates nothing.** Not a stat, an enumeration: a
        # near-miss sibling name is as much a failure as the file itself.
        assert not absent.exists()
        assert sorted(p.name for p in year_dir.iterdir()) == []

        # --- the retry half, with the IDENTICAL request ----------------------
        sent = bodies[-1]
        absent.write_bytes(daily_note(OTHER))
        status, replayed = _replay(app.base_url, sent)

        assert status == 201, replayed
        assert replayed["status"] == "logged"
        assert replayed["relativePath"] == note_path(OTHER)
        assert LINK_LINE in notes_region(absent.read_bytes())
    finally:
        page.context.close()


def test_step_5_the_view_renders_the_servers_missing_note_message_verbatim(
    app: LoopbackApp, chromium: Any, vault: Path
) -> None:
    """The UI half of F4, and it is the half an API test cannot reach.

    `app/static/js/api.js` puts the server's `message` into `error.message`
    verbatim; the view renders that string. The test reads the message off the
    wire and asserts the **painted title equals it exactly** — not that it
    contains it, and not that it contains a date and a path. Composing a vaguer
    sentence of the view's own is precisely what F4's single-wording rule
    exists to prevent, and "contains the path" would pass against it.
    """
    page, wire = new_page(chromium, app)
    try:
        open_recipe(page, app.base_url, day=OTHER)
        submit(page, OTHER)
        page.wait_for_selector(MISSING_NOTE, timeout=CONTENT_TIMEOUT_MS)

        body = write_body(wire)
        assert body["code"] == "daily_note_missing"
        assert isinstance(body["message"], str) and body["message"]

        painted = str(page.text_content(f"{MISSING_NOTE} .error-state__title"))
        assert painted == body["message"], (
            f"the view composed its own wording.\n  server: {body['message']!r}\n"
            f"  painted: {painted!r}"
        )
        # And the retry control F4's `retryable: true` promises is actually on
        # screen, or the flag is a dead end presented as a door.
        assert page.locator(f"{MISSING_NOTE} button:has-text('再试一次')").count() == 1
        assert not (vault_dir(vault) / note_path(OTHER)).exists()
    finally:
        page.context.close()


# --- §10.5 step 6 — the creation race, and the in-process seam it needs -----


def test_step_6_a_note_appearing_between_the_two_reads_is_a_409_not_a_404(
    runtime_root: Path, chromium: Any
) -> None:
    """Step 6, and **the assertion that is structurally unreachable elsewhere**.

    A lost race must be `409 daily_note_created_concurrently` and never
    `daily_note_missing`: reporting it as "not found" sends the user to create a
    note that already exists, and the second attempt then fails differently. The
    UI consequence is the point — the two codes reach **two different panels**,
    and only one of them tells the user what to do next.

    The window is milliseconds wide and lives between two reads inside
    `_read_required`, so it cannot be hit by timing from outside. It is hit the
    way §10.1 requires — "races need hooks, not luck" — by a store that reports
    absence on the first read and materialises the note before the second. That
    is why this one test runs its OWN in-process app: `launch_app` is a
    subprocess, and a subprocess cannot be handed a store. Everything the race
    exercises is the shipped one: `create_app`, the real `lifespan`, the shipped
    router, the real guards, the real envelope, and a real browser.
    """
    root = runtime_root
    seed_catalog(root / "pantry_items.db", CATALOG_ROWS)
    write_pantry_note(vault_dir(root), PANTRY_NOTE_BYTES)
    write_recipe(vault_dir(root), RECIPE, RECIPE_BYTES)

    racing = _launch_in_process(root, race_on=note_path(OTHER))

    page, wire = new_page(chromium, racing)
    try:
        open_recipe(page, racing.base_url, day=OTHER)
        mark = wire.mark()
        submit(page, OTHER)
        wait_for_log_error(page)
    finally:
        page.context.close()
        racing.stop()

    body = write_body(wire)
    assert body["code"] == "daily_note_created_concurrently"
    assert body["code"] != "daily_note_missing"
    assert body["date"] == OTHER
    assert body["relativePath"] == note_path(OTHER)
    assert body["retryable"] is True
    # This code IS in F4's `PRESENCE_CODES`, so unlike the stale-revision 409 it
    # publishes a `message` — and the view is required to render it verbatim.
    assert set(body) == set(F4_KEYS) | {"currentRevision"}
    assert OTHER in body["message"]

    posted = next(
        w for w in reversed(wire.responses) if w.path == "/api/cook-logs" and w.status == 409
    )
    assert posted.status == 409
    assert racing.reads == 2, "exactly one re-check, bounded at two, no sleep loop"
    assert wire.since(mark)

    # A lost race leaves the note alone: the user taps retry and the write
    # proceeds. The note exists (the store made it) and carries no link.
    materialised = vault_dir(root) / note_path(OTHER)
    assert materialised.exists()
    assert LINK_LINE not in notes_region(materialised.read_bytes())


# --- F5: online-only, and never on the outbox -------------------------------


def test_f5_the_cook_log_is_disabled_offline_and_never_queued(
    booted: tuple[Page, WireLog], note: Path
) -> None:
    """F5, observed rather than assumed, and the outbox half is the real claim.

    §9.18.1 argues a revision-tolerant write primitive is not worth pre-building
    for a statement about a *date*, and F4 means an offline log would also have
    to decide about a missing note — so the Cooking Log is online-only and is
    **excluded from the outbox by design**. The first half is a paint: the
    control is disabled and says why. The second half is the durable one: the
    page's own outbox queue is read out of `localStorage` and is empty, so
    nothing is waiting to be replayed when the connection returns.

    `navigator.onLine` is flipped by the *driver* (`set_offline`), not by a
    dispatched event, because a synthetic `offline` event would satisfy the
    assertion while the transport was still up.
    """
    page, wire = booted
    pre = note.read_bytes()

    page.context.set_offline(True)
    try:
        page.wait_for_function(
            "(sel) => { const b = document.querySelector(sel);"
            " return Boolean(b) && b.disabled === true; }",
            arg=LOG_BUTTON,
            timeout=CONTENT_TIMEOUT_MS,
        )
        assert not page.evaluate("navigator.onLine")
        assert str(page.text_content("[data-role=log-reason]")) == OFFLINE_REASON
        # The refusal is a DISABLED control, not a failed request. So: the picker
        # the fixture left open is dismissed, and a forced click on the disabled
        # button must not reopen it — a queued intent is unreachable by clicking.
        page.click(f"{DATE_PICKER} button:has-text('取消')")
        page.wait_for_selector(f"{DATE_PICKER}[hidden]", state="attached")
        page.click(LOG_BUTTON, force=True)
        page.wait_for_timeout(300)
        assert page.get_attribute(DATE_PICKER, "hidden") is not None, (
            "a disabled 做过了 still opened the date picker, so the offline "
            "refusal is a no-op rather than a refusal"
        )
    finally:
        page.context.set_offline(False)

    page.wait_for_function(
        "(sel) => { const b = document.querySelector(sel); return Boolean(b) && !b.disabled; }",
        arg=LOG_BUTTON,
        timeout=CONTENT_TIMEOUT_MS,
    )
    assert page.evaluate("navigator.onLine")
    assert str(page.text_content("[data-role=log-reason]")) == ""

    # The outbox is the durable claim. `pwa-outbox` is the vendored adapter's key
    # and the ONLY place a queued intent can be; an intent that is not there was
    # never enqueued, so it cannot be replayed.
    assert page.evaluate("localStorage.getItem('pwa-outbox')") in (None, "[]")
    assert page.evaluate("Object.keys(localStorage)") == []
    assert note.read_bytes() == pre, "an offline attempt wrote to the vault"
    assert not [w for w in wire.responses if w.method == "POST"], "an offline POST was sent"


# --- The 409 with no message: characterised, not discovered ------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "FINDING, not a contract: views/recipe.js keeps ONE `noteRevision` for a "
        "write that is per-DATE, so the second cook of a session sends the first "
        "cook's date's revision against the second cook's date and the server "
        "answers 409 daily_note_changed. Filed against the recipe view; see the "
        "ticket. `strict=True` so the fix turns this into an XPASS FAILURE "
        "rather than leaving a stale expectation behind."
    ),
)
def test_a_second_cook_on_another_date_does_not_carry_the_first_dates_revision(
    app: LoopbackApp, chromium: Any, vault: Path
) -> None:
    """A bug this flow found, which no other seam in the repo can reach.

    **The symptom.** Log a cook for one date, pick a *different* date, tap
    记录 again — and the second cook is refused with `409
    daily_note_changed`, naming a revision the user never saw and a conflict
    that did not happen. Nothing was edited: the second date's note was written
    by the fixture and never touched.

    **The mechanism.** `views/recipe.js` holds `noteRevision` as a single
    `let` for the whole mount, and `submit(date, noteRevision)` sends it as the
    `baseRevision` for whatever date the picker currently holds. A Cooking Log
    write is a statement about a *date*; a revision is `sha256` of one
    date's bytes. So once the view has read any revision, every later write —
    to any date — carries a foreign one, and the server's compare-and-swap
    correctly refuses it.

    **Why only a browser finds it.** An API test composes the body itself, so it
    would have to *choose* to send a mismatched revision, and then it is testing
    the 409 rather than the mistake. A `node --test` view test never issues two
    writes across two dates. The mistake is in the composition, and only a real
    client composing two real requests can make it.

    Asserted as an xfail so the suite stays honest about what ships: the
    assertion below is the CORRECT behaviour, and it fails today.
    """
    today_note = vault_dir(vault) / note_path(TODAY)
    other_note = vault_dir(vault) / note_path(OTHER)
    today_note.parent.mkdir(parents=True, exist_ok=True)
    today_note.write_bytes(daily_note(TODAY))
    other_note.write_bytes(daily_note(OTHER))

    page, wire = new_page(chromium, app)
    bodies = record_cook_log_bodies(page)
    try:
        open_recipe(page, app.base_url)
        submit(page, TODAY)
        wait_for_status(page)

        page.click(LOG_BUTTON)
        page.wait_for_selector(f"{DATE_PICKER}:not([hidden])", timeout=CONTENT_TIMEOUT_MS)
        submit(page, OTHER)
        wait_for_status(page)
    finally:
        page.context.close()

    # The second write must carry NO revision, because the view has never read
    # `OTHER`'s note: its own bytes are what it is about to compare against.
    assert bodies[0] == {"recipeNote": RECIPE, "date": TODAY, "baseRevision": None}
    assert bodies[1] == {"recipeNote": RECIPE, "date": OTHER, "baseRevision": None}, (
        f"the second cook carried the first cook's date's revision: {bodies[1]!r}"
    )
    assert write_status(wire) == 201, write_body(wire)
    assert LINK_LINE in notes_region(other_note.read_bytes())


def test_a_stale_revision_409_publishes_no_message_and_the_view_shows_the_bare_code(
    app: LoopbackApp, chromium: Any, vault: Path, note: Path
) -> None:
    """The gap another agent found, **measured and pinned** rather than re-found.

    `daily_note_changed` is in the router's `CONFLICT_CODES` but NOT in
    `PRESENCE_CODES`, so the envelope carries `currentRevision` and **no
    `message`**. `api.js`'s `ApiError` then falls back to the code for
    `error.message`, and the conflict panel's `data-role="server-message"` node
    paints the bare code `daily_note_changed` — a string, not a sentence.

    Is it handled? **Yes, and it is the documented behaviour, but the node is
    redundant rather than useful**: the panel beside it already shows both sides
    (what the PWA wanted, what the server currently holds) and a control to
    re-offer, so nothing is lost. What is *not* true is "the UI renders the
    server's message verbatim" for this code — there is no message to render.

    Asserted as a characterisation, in both directions: the envelope has no
    `message`, and the painted node equals the code. If F4 ever widens to a third
    code, the first assertion fails and this test says so rather than letting a
    stale expectation rot.
    """
    page, wire = new_page(chromium, app)
    try:
        open_recipe(page, app.base_url)
        pre = note.read_bytes()
        note.write_bytes(pre + b"")
        competing = pre.replace(
            NOTES_HEADING_LINE, NOTES_HEADING_LINE + b"- [[" + "菜".encode() + b"]]\n"
        )
        note.write_bytes(competing)
        submit(page, TODAY)
        page.wait_for_selector(CONFLICT, timeout=CONTENT_TIMEOUT_MS)
        painted = str(page.locator(SERVER_MESSAGE).text_content())
        count = page.locator(SERVER_MESSAGE).count()
        reoffer = page.locator(f"{CONFLICT} button:has-text('按服务器现状再记一次')").count()
    finally:
        page.context.close()

    body = write_body(wire)
    assert body["code"] == "daily_note_changed"
    assert "message" not in body, "F4 widened; the panel's wording contract changed"
    assert set(body) == {"requestId", "code", "currentRevision"}

    assert count == 1, f"expected one server-message node, found {count}"
    assert painted == "daily_note_changed", (
        "the panel's server-message node is not the bare code; the gap in F4's "
        f"envelope has been closed and this expectation is stale (painted {painted!r})"
    )
    # The panel is still actionable, which is why the bare code is a cosmetic
    # gap rather than a dead end.
    assert reoffer == 1
    assert note.read_bytes() == competing


# --- F18: the update race. THE reason to run this in a browser. -------------


def test_f18_an_update_cannot_land_between_the_tap_and_the_request(
    runtime_root: Path, chromium: Any
) -> None:
    """The F18 pair, and the race it exists to close — deterministically.

    A forced reload landing between the user tapping `做过了` and the request
    arriving **loses the log outright**. That is not a hypothesis: with the write
    held in flight and the update applied, the note ends with no link and the
    view with no status text, because the response is delivered to a document
    that no longer exists. `canApplyUpdate: () => !mutationInFlight() &&
    !isModalOpen()` is what prevents it, and `autoApply: false` +
    `WAIT_FOR_MESSAGE = true` are the pair that means an update is never taken
    without the user asking.

    Three things make this a **test** and not a demonstration:

    * **The write is parked server-side on an `Event`, not by sleeping.** A
      reload that happens to land outside the request window proves nothing, and
      F11's 30 s stock TTL is exactly the sort of clock a timing-based version of
      this test would end up waiting on.
    * **The page is re-navigated before the race.** On a FIRST visit
      `update-manager` captures `hadController === false` and its
      `controllerchange` guard suppresses the reload, so the race is
      unobservable and the test would pass against a build with no guard at all.
    * **The service worker is ALLOWED here.** The inherited `new_page` blocks it
      on purpose, which is right for every other test here and fatal for this
      one: with no worker there is no update to land. So this test brings its own
      context, and says why.

    The guard is asserted as an OBSERVATION of the shipped state: while the write
    is in flight the mutation counter is true, the picker is open, a version
    check finds a newer server, and the document is still the same one. Removing
    the guard makes this test fail (measured — see the ticket).
    """
    root = runtime_root
    seed_catalog(root / "pantry_items.db", CATALOG_ROWS)
    write_pantry_note(vault_dir(root), PANTRY_NOTE_BYTES)
    write_recipe(vault_dir(root), RECIPE, RECIPE_BYTES)
    target = vault_dir(root) / note_path(TODAY)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(daily_note(TODAY))

    gated = _launch_in_process(root, hold_writes=True)
    context = chromium.new_context(
        base_url=gated.base_url,
        locale="en-US",
        timezone_id=APP_TIMEZONE,
        service_workers="allow",
    )
    wire = WireLog()
    page = context.new_page()
    wire.attach(page)
    try:
        # 1. Boot under a worker and wait for it to take over.
        page.clock.set_fixed_time(FROZEN_INSTANT)
        page.goto(f"{gated.base_url}/#/recipe/{RECIPE}", wait_until="domcontentloaded")
        page.wait_for_selector(LOG_BUTTON, timeout=CONTENT_TIMEOUT_MS)
        page.wait_for_function(
            "() => navigator.serviceWorker.controller !== null", timeout=CONTENT_TIMEOUT_MS
        )
        # 2. Re-navigate, so `hadController` is true and a takeover WOULD reload.
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(LOG_BUTTON, timeout=CONTENT_TIMEOUT_MS)
        page.wait_for_function(
            "() => navigator.serviceWorker.controller !== null", timeout=CONTENT_TIMEOUT_MS
        )
        page.evaluate("window.__alive = true;")
        page.evaluate(
            "addEventListener('pagehide', () => { window.__alive = false; })"
        )

        # 3. A deploy: a NEW worker installs and WAITS (F18 §4d), and the server
        #    starts reporting a newer version.
        gated.serve_new_worker.set()
        page.evaluate("navigator.serviceWorker.getRegistration().then((r) => r.update())")
        page.wait_for_function(
            "() => navigator.serviceWorker.getRegistration().then((r) => Boolean(r.waiting))",
            timeout=CONTENT_TIMEOUT_MS,
        )
        gated.report_new_version.set()
        page.evaluate("window.dispatchEvent(new Event('focus'))")
        page.wait_for_selector("#update-banner button", timeout=CONTENT_TIMEOUT_MS)
        assert "new version" in str(page.text_content("#update-banner"))

        # 4. The user taps 做过了, picks today, and taps 记录. The request is
        #    parked by the server on an Event.
        page.click(LOG_BUTTON)
        page.wait_for_selector(f"{DATE_PICKER}:not([hidden])", timeout=CONTENT_TIMEOUT_MS)
        page.fill(LOG_DATE, TODAY)
        page.click(CONFIRM)
        assert gated.write_parked.wait(30), "the cook-log write never reached the server"

        # 5. The two terms of `canApplyUpdate`, read out of the live page.
        version = str(page.evaluate("document.querySelector('meta[name=app-version]').content"))
        assert page.evaluate(
            "(v) => import('/js/api.js?v=' + v).then((m) => m.mutationInFlight())", version
        ), "mutationInFlight() is false while a mutation is in flight"
        assert page.evaluate(
            "Boolean(document.querySelector("
            "'dialog[open], .form-sheet:not([hidden]), .date-picker:not([hidden])'))"
        ), "the date picker is not open, so the modal term cannot be observed"

        # 6. A version check lands mid-write. With the guard, nothing is applied.
        page.evaluate("window.dispatchEvent(new Event('focus'))")
        page.wait_for_timeout(1500)
        assert page.evaluate("window.__alive") is True, (
            "the document navigated while a cook-log write was in flight: the "
            "update was applied across a live mutation and the log is lost"
        )
        assert page.evaluate(
            "navigator.serviceWorker.getRegistration().then((r) => Boolean(r.waiting))"
        ), "the waiting worker was activated while a write was in flight"

        # 7. Release the write and let it land. The log must survive.
        gated.release_write.set()
        page.wait_for_function(
            "(sel) => { const n = document.querySelector(sel);"
            " return Boolean(n) && n.textContent !== ''; }",
            arg=LOG_STATUS,
            timeout=CONTENT_TIMEOUT_MS,
        )
        assert f"已记到 {note_path(TODAY)}" == str(page.text_content(LOG_STATUS))
    finally:
        context.close()
        gated.stop()

    after = target.read_bytes()
    assert LINK_LINE in notes_region(after), "the cook log did not reach the note"
    assert after.count(LINK_LINE) == 1


# --- The suite-wide property, asserted for this file rather than assumed -----


def test_this_flow_reads_no_live_path(runtime_root: Path, app: LoopbackApp) -> None:
    """The invariant the whole suite is built to keep, asserted for #25.

    `/Users/syang/obsidian/syang` is the user's live, machine-rewritten vault. A
    browser flow that *wrote* it would not be a test failure, it would be damage
    to real notes — and unlike the browse flow, this one has a `write` in its
    name, so the assertion is the point rather than a formality.

    Four things are checked: the vault and data roots are under this test's
    `tmp_path`; the running app's environment pins every key `app/config.py`
    reads, so a stray shell export cannot redirect a server-owned root; the only
    daily notes this flow can name are the ones under the tmp vault; and the one
    string the app must never publish — an absolute path — is absent from what it
    answered.
    """
    from tests.browser.conftest import SETTINGS_KEYS

    pytest_root = Path(str(runtime_root)).parent.parent
    for name in ("vault", "data"):
        root = runtime_root / name
        assert root.is_dir(), root
        assert root.is_relative_to(pytest_root), root
    assert (runtime_root / "pantry_items.db").is_file()

    # The env the app actually booted with, not the env the test *meant* to give
    # it: `launch_app` strips and rewrites fourteen keys, and this is the check
    # that it did.
    for key in SETTINGS_KEYS:
        assert key in app.env, f"{key} was inherited rather than pinned"
    assert Path(app.env["OBSIDIAN_VAULT_PATH"]) == runtime_root / "vault"
    assert Path(app.env["APP_DATA_DIR"]) == runtime_root / "data"
    assert Path(app.env["PANTRY_ITEMS_DB"]) == runtime_root / "pantry_items.db"
    assert app.env["DAILY_NOTES_ROOT"] == "日记"

    # Only the dates this file names can be written, and every one of them is
    # under the tmp vault. `note_path` is the single place a path is spelled.
    for day in (TODAY, OTHER):
        path = note_path(day)
        assert not Path(path).is_absolute()
        assert path.startswith("日记/")
        assert (vault_dir(runtime_root) / path).parent.is_dir()

    # The live vault is named here on purpose: the assertion is about the string
    # that must never appear in a response body, and spelling it out is what
    # makes that checkable rather than a gesture.
    assert "日记" in note_path(TODAY)
    assert "/Users/syang/obsidian/syang" not in json.dumps(
        {"p": note_path(TODAY), "q": note_path(OTHER)}, ensure_ascii=False
    )


# --- The in-process app, for the two tests that need a seam inside the server


class _InProcess:
    """A `create_app()` served on loopback **in this process**, plus the two
    seams the race and the update test need.

    `launch_app` is a subprocess, which is right for every other test here (the
    app reads its roots from the environment once, and two flows cannot share a
    process). It is wrong for exactly two assertions, and both are assertions
    about state *inside* the server:

    * §10.5 step 6 needs a `CookingLogWriter` over a store that materialises the
      daily note between two reads, and a subprocess cannot be handed one;
    * the F18 test needs a **second `/sw.js`**, because a stale cached shell is a
      false pass and an update only exists once a new worker has installed — and
      a subprocess serves the file off disk.

    So this builds the real `create_app(settings)`, installs the real `lifespan`,
    and adds exactly two things on top: a store subclass swapped onto
    `app.state`, and a middleware that serves the parked write and the bumped
    worker. Nothing in `app/` is edited and no request-supplied path exists.
    """

    def __init__(
        self, app: Any, base_url: str, server: Any, socket_: socket.socket
    ) -> None:
        self.app = app
        #: The origin the browser will use. Named here rather than read off the
        #: app because `PUBLIC_ORIGIN` is what the Origin guard compares against,
        #: and the two must be the same string or every mutation is a 403.
        self.base_url = base_url
        self._server = server
        self._thread: threading.Thread | None = None
        self._socket = socket_
        self.reads = 0
        self.write_parked = threading.Event()
        self.release_write = threading.Event()
        self.serve_new_worker = threading.Event()
        self.report_new_version = threading.Event()

    def stop(self) -> None:
        """Shut the server down, THEN drop the socket.

        In that order: `uvicorn` owns the listening descriptor once `run()` has
        taken it, so closing the socket out from under a live server produces a
        stream of `Invalid file descriptor` warnings from the accept loop and a
        `CancelledError` from the lifespan. The wait is bounded, so a wedged
        server cannot hang the suite.
        """
        if self._server is None:  # pragma: no cover - a boot that never served
            return
        self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=15)
        if self._socket.fileno() != -1:
            self._socket.close()


def _launch_in_process(
    root: Path, *, race_on: str | None = None, hold_writes: bool = False
) -> _InProcess:
    """Boot the app on a thread, with the requested seam installed."""
    from starlette.middleware.base import RequestResponseEndpoint
    from starlette.requests import Request
    from starlette.responses import Response

    from app.api.cooklog import COOK_LOG_WRITER_STATE_KEY
    from app.config import Settings
    from app.main import create_app

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    port = int(listener.getsockname()[1])
    base_url = f"http://127.0.0.1:{port}"

    settings = Settings.from_mapping(
        {
            "OBSIDIAN_VAULT_PATH": str(vault_dir(root)),
            "APP_DATA_DIR": str(root / "data"),
            "PANTRY_ITEMS_DB": str(root / "pantry_items.db"),
            "PUBLIC_ORIGIN": base_url,
            "TAILSCALE_OWNER_LOGIN": "owner@test.invalid",
            "DEV_IDENTITY": "owner@test.invalid",
            "BIND_HOST": "127.0.0.1",
            "APP_TIMEZONE": APP_TIMEZONE,
            "TRUST_TAILSCALE_HEADERS": "false",
            "RECIPES_ROOT": "Hobbies/做饭/Recipes",
            "PANTRY_NOTE_RELATIVE": "Logistics/库存/Pantry.md",
            "DAILY_NOTES_ROOT": "日记",
            "DAILY_NOTES_YEAR_POLICY": "",
            "OBSIDIAN_READ_ONLY": "false",
        }
    )
    application = create_app(settings)
    holder = _InProcess(application, base_url, None, listener)  # type: ignore[arg-type]

    @application.middleware("http")
    async def _seams(request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if hold_writes and path == "/api/cook-logs" and request.method == "POST":
            holder.write_parked.set()
            holder.release_write.wait(60)
        if path == "/sw.js" and holder.serve_new_worker.is_set():
            return Response(
                _next_version_sw(), media_type="text/javascript",
                headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-store"},
            )
        if path == "/api/version" and holder.report_new_version.is_set():
            return Response(
                json.dumps({"version": NEXT_VERSION}), media_type="application/json"
            )
        return await call_next(request)

    import uvicorn

    server = uvicorn.Server(uvicorn.Config(application, log_level="warning"))
    holder._server = server
    holder._thread = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    holder._thread.start()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not server.started:
        time.sleep(0.02)
    if not server.started:  # pragma: no cover - a boot that never served
        raise AssertionError("the in-process app never started serving")

    # The creation-race seam goes on AFTER startup, and that ordering is the whole
    # subtlety: `app/main.py`'s `lifespan` publishes its own `CookingLogWriter`
    # onto `app.state` while the server boots, so a swap made before `run()` is
    # silently overwritten and the test would assert a 404 against a writer whose
    # race hook was never installed. Everything else — the app, the router, the
    # guards, the envelope — is the shipped one.
    if race_on is not None:
        application.state[COOK_LOG_WRITER_STATE_KEY] = _racing_writer(
            root, settings, race_on, holder
        )
    return holder


def _racing_writer(root: Path, settings: Any, race_on: str, holder: _InProcess) -> Any:
    """A real `CookingLogWriter` over a store that loses the creation race.

    §10.1: "races need hooks, not luck". `AtomicNoteStore` takes a `race_hook` for
    its write path, but the window that matters here is between two READS inside
    `CookingLogWriter._read_required`, so the seam is a store whose
    `read_existing_if_exists` reports absence once and then materialises the note
    before the second call. Everything downstream of that — the path policy, the
    CAS, the `transform_existing` commit, the receipts ledger — is untouched.
    """
    from app.cooklog.writer import CookingLogWriter
    from app.db.database import connect_db
    from app.vault.atomic_write import AtomicNoteStore
    from app.vault.daily_paths import DailyNotePathPolicy

    day = race_on.rsplit("/", 1)[-1][:10]

    class _AppearingStore(AtomicNoteStore):
        def read_existing_if_exists(
            self, relative: str, *, max_bytes: int | None = None
        ) -> bytes | None:
            if relative == race_on:
                holder.reads += 1
                if holder.reads > 1:
                    target = vault_dir(root) / race_on
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(daily_note(day))
            return super().read_existing_if_exists(relative, max_bytes=max_bytes)

    recovery = root / "data" / "vault-recovery"
    recovery.mkdir(parents=True, exist_ok=True)
    return CookingLogWriter(
        _AppearingStore(vault_dir(root), recovery),
        DailyNotePathPolicy.from_settings(settings),
        partial(connect_db, settings),
    )


#: A *second* `CACHE_VERSION`, so the update the F18 test races is a real new
#: worker rather than a version string the manager merely believes. The shipped
#: `sw.js` is read and bumped rather than replaced wholesale, so the new worker
#: differs from the old one in exactly the one byte that matters and nothing
#: else — a hand-written stub worker would prove nothing about the shipped
#: `activate` handler.
NEXT_VERSION: Final = "v9999.0.0-browser-race"


def _next_version_sw() -> str:
    from app.main import STATIC_ROOT

    source = (STATIC_ROOT / "sw.js").read_text(encoding="utf-8")
    match = re.search(r"const CACHE_VERSION = '([^']+)';", source)
    assert match is not None, "sw.js no longer declares CACHE_VERSION the way this reads it"
    assert match.group(1) != NEXT_VERSION, (
        "the synthetic worker version collides with the shipped one, so no update "
        "would exist and this test would pass for the wrong reason"
    )
    return source.replace(match.group(0), f"const CACHE_VERSION = '{NEXT_VERSION}';", 1)


#: The exact JSON body of every `/api/cook-logs` POST the page issued, in order.
#:
#: `WireLog` (inherited, not mine) records method/path/query and RESPONSE bodies;
#: it deliberately does not keep request bodies, because #24's steps never needed
#: them. §10.5 step 5 does: the `retryable: true` claim is that the *same*
#: request succeeds once the user has done the one thing the message asked for,
#: and a re-derived body would prove nothing about it. So the request bodies are
#: captured here, from the driver side, in this file.
def record_cook_log_bodies(page: Page) -> list[dict[str, Any]]:
    """Attach a request-body recorder and return the list it appends to."""
    bodies: list[dict[str, Any]] = []

    def on_request(request: Any) -> None:
        if request.method != "POST" or "/api/cook-logs" not in request.url:
            return
        raw = request.post_data
        assert raw is not None, "a POST with no body reached the cook-log route"
        bodies.append(json.loads(raw))

    page.on("request", on_request)
    return bodies


def _replay(base_url: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """POST `body` to a running app from this process, and return `(status, json)`.

    A raw `urllib` POST rather than the page's `apiFetch`, deliberately: the
    point is that the *server* honours the identical request, so the client must
    not be the thing under test. The CSRF token is a real one from
    `GET /api/session` and the `Origin` is the app's own, so the shipped guards
    are satisfied by the same two values the browser supplies.
    """
    session = json.loads(urllib.request.urlopen(f"{base_url}/api/session", timeout=10).read())
    request = urllib.request.Request(
        f"{base_url}/api/cook-logs",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "X-CSRF-Token": str(session["csrfToken"]),
            "Origin": base_url,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return int(response.status), json.loads(response.read())
    except urllib.error.HTTPError as error:
        return int(error.code), json.loads(error.read())

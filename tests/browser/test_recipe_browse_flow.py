"""The D4 display contract, end to end, in a real browser. §10.5, phase P14a.

**This is one of two test files in the repo that open a browser**, and it earns
its keep on step 4 alone. A `history.back()` that restores a scroll offset is
not observable from any other seam: the API tests never render, the `node
--test` gates drive pure functions with a fake `window`, and an integration test
against `TestClient` receives HTML it never executes. #21 found the bug this
file is written to prevent by measuring it in Chromium — the router called
`scrollTo` immediately after `mount()`, but a still-loading view is ~16px tall,
so a 640px target **clamped to 16**. `history.state` held 640 the whole time;
only the DOM was too short. A test that asserted "the hash went back to `#/`"
would have passed against that build forever.

Steps 1–3 exist for a second reason: the **stale-paint false pass**. A cached
snapshot fills an element with old text, so `wait_for(selector)` returns too
early and every assertion after it reads the previous render. So no wait in this
file is a visibility wait, and every assertion is on rendered class names and
text rather than on a request having been made.

Run it — the extra is deliberately NOT in `[test]` (F10 is locked):

```bash
.venv/bin/python -m pip install ".[browser]" && .venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest tests/browser/test_recipe_browse_flow.py -q
```

A default `.venv/bin/python -m pytest` collects this file and SKIPS it.

**Nothing here touches a live path.** The vault, the data dir and the pantry
catalog all come from the `runtime_root` fixture, which is under `tmp_path` by
construction, and `test_this_flow_reads_no_live_path` asserts it. The user's
`/Users/syang/obsidian/syang` is a real folder that a real vault rewrite would
damage; a browser flow that reached for it would not be a test failure, it would
be a data-loss incident. `launch_app` pins all fourteen `Settings` keys, so a
stray shell export cannot redirect a server-owned root either.

## step_4_finding — a defect this flow found, in a real browser only — FIXED

`#step_4_back_restores_the_scroll_offset` was written, watched fail on its own
code, and the cause is worth recording rather than hiding in a fixture.

**The router's own defence was defeated by a scroll event, not by a teardown.**
`router.js` documents the #20 trap and defends against it: read the entry's
offset BEFORE unmounting anything, because by the time a `hashchange` handler
runs the browser has already pushed the destination entry. That defence assumed
the outgoing view writes its state at TEARDOWN. It also writes on every SCROLL,
through the same `trackScroll` → `saveViewState` path, and that listener is
attached until teardown.

On a **Back traversal** the browser commits the arrival and resets the scroll
position as part of the commit. The reset is an ordinary scroll event, the
outgoing detail view's listener is still attached, its rAF-throttled handler
runs `saveViewState({ scrollTop: <the reset value> })`, and by then the current
entry is the ORIGIN's. So the origin's own 640 is overwritten before
`home.js`'s `mount()` reads it, `resumeTop` is 0, the guard `resumeTop > 0` is
false, and nothing re-applies the offset.

**The event order, measured in Chromium rather than assumed** (a capture-phase
scroll listener next to the router's own `popstate`/`hashchange` handlers, over
the real app and a real vault):

```
  189ms  popstate    y=  0  hist=640   hash=#/
  189ms  scroll      y=244  hist=640   hash=#/
  191ms  hashchange  y=244  hist=244   hash=#/      <-- already corrupted
  192ms  scroll      y= 16  hist=244
  202ms  scroll      y= 16  hist= 16
  252ms  scroll      y=244  hist= 16
```

So the reset arrives **after `popstate` and before `hashchange`**, and it is the
rAF-throttled save — not the event — that does the damage, which is why the
write lands *before* the router's read.

Measured in Chromium, under a full-suite run (so the machine is loaded, which is
when it shows up): `window.scrollY` settled at **0px, 57px or 244px** instead of
640 in roughly **one full-suite run in four**, and `history.state.scrollTop`
settled at the same wrong number — so the damage is *persisted*, and a second
Back keeps it wrong. The captured scroll log is `[640, 244, 16, 16, 244]`: the
app reached 640 and the engine moved it.

**No other seam can see this, which is the point of the file.**
`tests/js/router.test.mjs` drives a fake window whose `history.back()` restores
a hash and fires `popstate`/`hashchange` — it never changes `scrollY` and never
fires a scroll event, so the write cannot happen in it.
`tests/js/views.test.mjs` models the clamp faithfully but drives a *forward*
re-render, never a traversal. The API suite never renders. So both unit gates are
green against a build a user would experience as "Back sometimes drops me 200px
from where I was".

**What fixed it, and why the harness workaround is gone.** Two guards, in the
two files that own the two halves of the contract:

* `router.js` raises a **traversal latch** on `popstate` and lowers it at the end
  of the `render()` that follows, and `saveViewState` is a no-op while it is up.
  Between those two moments no view owns the current history entry, so no view
  may write it. This is at the router rather than in one view on purpose:
  `home.js`, `shortlists.js`, `provenance.js` and `settings.js` all keep the same
  tracked-scroll listener, and a rule only one view honours is a rule the fourth
  view silently breaks.
* `views/recipe.js` captures the **entry epoch** at mount and drops its own
  tracked-scroll save once it moves — the same decision, made at the view, and
  made at the *deferred* save rather than at the event.

So this step no longer freezes scroll events, and it measures the app.
Re-measured through the same round trip, freeze absent: **0 failures in 48 round
trips** with the fix, **2 failures in 48** without it, both with the reported
`[244, 16, 16, 244]`; and the step itself, run 30 times on its own, **0 failures
with the fix and a failure on run 8 without it** (`settled at 57px, not 640px`,
`saved=57`, log `[640, 57, 16, 57, 57]`).

**What removing the control did and did not weaken.** Re-injected, measured:

* deleting the re-application from `home.js` — still fails, loudly, at
  `settled at 16px, not 640px`. That is the step's own claim and it is intact.
* adding a `saveViewState` to `recipe.js`'s `unmount()` — **now passes, 5 for 5**,
  and that is the fix being stronger than the test rather than the test going
  blind. #24 could not fix the bug, so the router could only DEFEND against this
  one by ordering (read the offset before tearing anything down). The latch
  refuses the write outright, because during a traversal the teardown is inside
  the window in which no view owns the entry. The two controls this step used to
  rely on — the harness freeze and the router's read-first ordering — are now
  redundant for this class, and only the re-application assertion is left holding
  the clamp.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from tests.api.conftest import seed_catalog, write_pantry_note, write_recipe
from tests.browser.conftest import (
    CONTENT_TIMEOUT_MS,
    PANTRY_NOTE,
    RECIPES_ROOT,
    SETTINGS_KEYS,
    VIEWPORT,
    LoopbackApp,
    WireLog,
    new_page,
)
from tests.browser.optional import import_sync_api

#: §10.5's opt-in gate. A default run never reaches the browser, and the message
#: names the two commands that would make this file run rather than merely
#: mentioning a package.
if TYPE_CHECKING:  # a type-only import: never executed, so a default run without
    # the extra still cannot import playwright, and the annotations below stay
    # real types under `mypy` instead of collapsing to `Any`.
    from playwright.sync_api import Page

#: Resolved through `optional`, not `pytest.importorskip`, because this is
#: MODULE scope: a skip here is indistinguishable in a log from "collected
#: nothing", which is how this suite stayed green in CI for its whole life. With
#: `PANTRY_BROWSER_REQUIRED=1` the same call FAILS, so the CI job that installs
#: the extra cannot pass without running these flows.
sync_api = import_sync_api()

# --- The frozen strings. §9.13.1, and the reason step 1 exists. --------------

#: The four exact forms of §9.13.1. Frozen identically by
#: `tests/js/logic/format.test.mjs`; asserted here against a real render, which
#: is the only place the string is proven to survive the trip from the payload
#: through `logic/format.js` and into a DOM node. The fourth form needs `?strict=1`
#: and a Seasoning the staples tier cannot satisfy, so it is in the vocabulary
#: without appearing in the default view's rows.
FROZEN_HEADLINES = frozenset(
    {
        "4/6 ingredients found — missing: 香菇, 娃娃菜",
        "6/6 ingredients found",
        "0/3 ingredients found — missing: 空心菜, 香菇, 娃娃菜",
        "2/5 ingredients found — missing: Clam, 香菇   (严格模式（含调料）: 生抽)",
    }
)

#: D4's five buckets, and the sixth modifier `chip--manual` rides alongside
#: `chip--in-stock` / `chip--have-been-buying`. A chip carrying none of the five
#: is a sixth vocabulary, which is a bug in `logic/chip-class.js`.
FIVE_BUCKETS = frozenset(
    {
        "chip--in-stock",
        "chip--have-been-buying",
        "chip--assumed-staple",
        "chip--missing",
        "chip--ignored-seasoning",
    }
)
MANUAL_MODIFIER = "chip--manual"
BUCKET_PAIRS_WITH_MANUAL = frozenset({"chip--in-stock", "chip--have-been-buying"})

#: §9.16's list-response keys, asserted as a set so a second response shape
#: cannot be added without this file noticing (F7's "no reduced shape").
LIST_KEYS = frozenset(
    {
        "catalogRevision",
        "recipes",
        "skipped",
        "staleMappingCount",
        "stockRevision",
        "stockUnjoinedCount",
        "stockJoin",
        "strict",
    }
)

#: `tier N · match_method · #id · confidence` — §9.13.4's 调试 secondary line.
#: `id` is `#3` or the em-dash for an unresolved slot and `confidence` is a bare
#: number, so the shape is regexed rather than pinned to one fixture row.
PROVENANCE_LINE = re.compile(r"^tier \d+ · [a-z_]+ · (?:#\d+|—) · [\d.]+$")

#: §9.13.1's RULE, not one of its examples: `n/total ingredients found`, then
#: optionally the ` — missing: ` clause. The strict addendum is absent in the
#: default view (F17), so it is not in the pattern.
HEADLINE_SHAPE = re.compile(r"^\d+/\d+ ingredients found(?: — missing: .+)?$")
_MISSING_CLAUSE = re.compile(r"^(\d+)/(\d+) ingredients found(?: — missing: (?P<names>.+))?$")


def missing_names(headline: str) -> list[str]:
    """The ` — missing: ` entries, or `[]`. One entry per missing `材料` slot."""
    found = _MISSING_CLAUSE.match(headline)
    assert found, headline
    names = found.group("names")
    return names.split(", ") if names else []

# --- The synthetic world. Deterministic, committed, and under `tmp_path`. ----

#: Nine catalog rows, so every one of the five buckets is reachable. The last
#: row carries an `area` and the reader excludes it, which exercises the filter
#: with the same fixture rather than a second one. `空心菜`, `香菇` and `娃娃菜`
#: are deliberately ABSENT: the frozen `0/3` and `4/6` forms are about names that
#: resolve to nothing, and a catalog row for any of them would make those
#: strings unproducible.
CATALOG_ROWS: tuple[tuple[int, str, str, str, str | None], ...] = (
    (1, "番茄", "1.1", "[]", None),
    (2, "鸡蛋", "1.1d", "[]", None),
    (3, "豆腐", "1.1", "[]", None),
    (4, "蒜苗", "1.1", "[]", None),
    (5, "花生米", "1.1b", "[]", None),
    (6, "茼蒿", "1.1", "[]", None),
    (7, "李锦记 蒸鱼豉油 14 盎司", "1.1c", "[]", None),
    (8, "Shampoo", "3.1", "[]", "Shampoo"),
    (9, "黄瓜", "1.1", "[]", None),
)

#: Seven open lines (so seven products are `chip--in-stock`), **one** catalog
#: product with no line at all — `蒸鱼豉油`, which is what makes this fixture able
#: to prove the stock rule end to end — and one line nothing in the catalog
#: explains, the `stockUnjoinedCount` of 1, so the header's 库存行没对上 counter
#: is exercised by the same fixture. The "bought and finished" `[x]` case is NOT
#: here: `tests/api/test_recipes_api.py` owns it, and mixing the two would make
#: this fixture's expected headlines harder to read.
#:
#: `豆腐`, `茼蒿` and `黄瓜` are stocked rather than left catalog-only **on
#: purpose**. Every recipe but one is fully stocked on purpose too, so that the
#: four frozen strings are still producible byte for byte; the single exception
#: is `蒸鱼豉油拌面`, and it is where the rule is asserted (see
#: `PINNED_HEADLINES`). Stocking everything else is what keeps that one row the
#: only place the reader has to think.
PANTRY_NOTE_BYTES = (
    "---\n"
    "modified_at: 2026-09-27\n"
    "---\n"
    "# 1 冰箱\n"
    "- [ ] 番茄\n"
    "- [ ] 鸡蛋\n"
    "- [ ] 蒜苗\n"
    "- [ ] 豆腐\n"
    "- [ ] 茼蒿\n"
    "- [ ] 黄瓜\n"
    "\n"
    "# 2 干货\n"
    "- [ ] 花生米\n"
    "- [ ] 完全不存在的商品\n"
).encode()

#: Sixteen recipes: `noteName -> (材料, 调料, last_cooked)`.
#:
#: **Why sixteen.** The document has to be at least `SCROLL_TARGET` taller than
#: the 640px window for step 4's offset to be reachable at all, and step 4
#: asserts that as a precondition, so a thinner fixture fails loudly instead of
#: quietly turning the reason this file exists into a tautology.
#:
#: **Why these particular numbers.** The rows the assertions name are chosen so
#: their rendered headline is *literally* one of §9.13.1's four frozen strings
#: rather than merely the same shape: `六味俱全` is the only all-found `6/6`,
#: `香菇合炒` misses exactly `香菇, 娃娃菜` in that slot order, and `空心三缺` is
#: `0/3` over exactly those three names. `last_cooked` is set on every note so
#: the rendered order is fully determined by §9.13.3 — code point, never
#: `localeCompare` — and asserted as a whole sequence below.
RECIPES: dict[str, tuple[list[str], list[str], str | None]] = {
    "六味俱全": (
        ["番茄", "鸡蛋", "蒜苗", "花生米", "豆腐", "黄瓜"],
        ["生抽", "味精"],
        "2026-09-25",
    ),
    "鸡蛋羹": (["鸡蛋"], ["蒸鱼豉油"], "2026-05-01"),
    "拌空心菜": (["空心菜", "豆腐", "蒜苗", "番茄"], ["蚝油", "香菜"], "2026-09-20"),
    "豆腐羹": (["豆腐", "番茄", "鸡蛋", "甲鱼"], ["生抽", "糖"], "2026-09-10"),
    "蒸鱼豉油拌面": (["蒸鱼豉油", "面条", "番茄", "鸡蛋"], ["香菜"], "2026-06-30"),
    "香菇合炒": (["番茄", "鸡蛋", "豆腐", "茼蒿", "香菇", "娃娃菜"], [], "2026-09-22"),
    "番茄炒蛋": (["番茄", "鸡蛋", "香菇"], ["生抽", "花椒"], "2026-09-18"),
    "蒜苗小炒": (["蒜苗", "豆腐", "羊腰子"], ["老抽"], "2026-08-20"),
    "豆腐蒸蛋": (["豆腐", "鸡蛋", "羊腰子"], ["生抽"], "2026-02-08"),
    "清炒茼蒿": (["茼蒿", "香菇"], ["盐"], "2026-09-02"),
    "花生糖": (["花生米", "盐"], [], "2026-07-15"),
    "三缺其二": (["番茄", "香菇", "娃娃菜"], [], "2026-04-20"),
    "干拌花生": (["花生米", "燕窝", "熊掌"], ["老抽"], "2026-03-11"),
    "空心三缺": (["空心菜", "香菇", "娃娃菜"], ["蚝油"], "2026-09-05"),
    "零样菜": (["甲鱼", "羊腰子", "鱼翅", "熊掌", "燕窝", "鲍鱼"], ["蚝油"], "2026-08-01"),
    "空白配菜": (["鱼翅", "鲍鱼", "甲鱼", "燕窝"], [], "2026-01-05"),
}

#: The exact rendered order, `(noteName, found, total)`, as §9.13.3 produces it:
#: `foundRatio` desc, then `lastCooked` desc (absent last), then `noteName` by
#: code point. Asserted whole, so a change in the comparator — or in one recipe's
#: score — is a named diff rather than a vague "the order looks wrong".
#: `蒸鱼豉油拌面` reads `2/4`, not `3/4`, and it is the one row in this fixture
#: whose score the stock rule moved: its `蒸鱼豉油` resolves to a Pantry Item with
#: no open line, so it is missing. That drops the row from the `0.75` band to the
#: `0.50` band, which is why it now sits below `清炒茼蒿` and `花生糖` rather than
#: above `香菇合炒` — the sort is over the same numbers the headline prints.
EXPECTED_ROWS: tuple[tuple[str, int, int], ...] = (
    ("六味俱全", 6, 6),
    ("鸡蛋羹", 1, 1),
    ("拌空心菜", 3, 4),
    ("豆腐羹", 3, 4),
    ("香菇合炒", 4, 6),
    ("番茄炒蛋", 2, 3),
    ("蒜苗小炒", 2, 3),
    ("豆腐蒸蛋", 2, 3),
    ("清炒茼蒿", 1, 2),
    ("花生糖", 1, 2),
    ("蒸鱼豉油拌面", 2, 4),
    ("三缺其二", 1, 3),
    ("干拌花生", 1, 3),
    ("空心三缺", 0, 3),
    ("零样菜", 0, 6),
    ("空白配菜", 0, 4),
)

#: Step 1's row, and the exact frozen string it must render.
FIRST_ROW = "六味俱全"
FIRST_HEADLINE = "6/6 ingredients found"

#: Step 3's row. §9.13.3's "a 0/6 Recipe is a first-class row" and §13.1's F20: a
#: shopping-list seed the list hides is the most user-visible misreading of D4.
#: §9.13.1 blesses this six-name `0/6` rendering as equally producible with the
#: three-name `0/3`, so it is asserted by string and not by shape.
ZERO_SIX_ROW = "零样菜"
ZERO_SIX_HEADLINE = "0/6 ingredients found — missing: 甲鱼, 羊腰子, 鱼翅, 熊掌, 燕窝, 鲍鱼"

#: The rows whose rendered headline must be a §9.13.1 frozen string BYTE for
#: byte. Three of the four forms are produced by this fixture, and the fourth
#: needs `?strict=1` plus a Seasoning the staples tier cannot satisfy, so it is
#: never rendered here. The `0/6` row is the spec's second, equally-producible
#: rendering of the `0/3` form rather than one of the four literals.
PINNED_HEADLINES: dict[str, str] = {
    "六味俱全": "6/6 ingredients found",
    "香菇合炒": "4/6 ingredients found — missing: 香菇, 娃娃菜",
    "空心三缺": "0/3 ingredients found — missing: 空心菜, 香菇, 娃娃菜",
    ZERO_SIX_ROW: ZERO_SIX_HEADLINE,
    # The stock rule, pinned in a real browser. `蒸鱼豉油` resolves to catalog row
    # 7 and has NO open line, so its chip is `chip--have-been-buying` — bought
    # before, not on the shelf — and it is **missing** here, counted in the
    # denominator and named in the list beside the never-resolved `面条`. Under
    # the old ladder the same chip was scored as found and this row read `3/4`.
    # This is the string that would have caught `煮菜菜`'s `2/2`.
    "蒸鱼豉油拌面": "2/4 ingredients found — missing: 蒸鱼豉油, 面条",
}

#: The recipe step 4 enters and leaves, and the offset it is measured at. The
#: shape is `640 → 0 → 640 → 0 → 640`: two full round trips, because the second
#: is the one that fails if the first round trip's restore left a coincident
#: value behind rather than a mechanism.
DETAIL_ROUTE = "拌空心菜"
SCROLL_TARGET = 640

#: F7's `localStorage` key, §9.13.4's key by name.
DEBUG_KEY = "pantry-recipes:debug"

#: F1's code for "the stock note could not be read", §9.16's one 503 code.
STOCK_UNREADABLE_CODE = "pantry_stock_unreadable"

#: How long step 5 lets a *deferred* request land before it claims there was
#: none. Not a sleep for the app to finish: the toggle itself is synchronous, so
#: this only has to outlast a `setTimeout(0)`-style fetch, and it is there so a
#: toggle that re-fetches on a timer cannot pass.
QUIET_WINDOW_MS = 500

#: The scroll waits are short on purpose. A scroll offset either lands within a
#: frame or two of the rows painting, so a long bound here only buys a slower
#: failure — and a slow failure is what makes a red build look like a flaky one.
SCROLL_TIMEOUT_MS = 5_000


def note_bytes(materials: list[str], seasonings: list[str], last: str | None) -> bytes:
    """One recipe note. The `last_cooked` frontmatter is what orders the list."""
    lines = ["---", "材料:"]
    lines += [f"  - {name}" for name in materials]
    if seasonings:
        lines.append("调料:")
        lines += [f"  - {name}" for name in seasonings]
    if last:
        lines.append(f"last_cooked: {last}")
    lines += ["---", "# 步骤", "1. 洗。", "2. 炒。", ""]
    return "\n".join(lines).encode()


def build_vault(runtime_root: Path) -> None:
    """Write the whole synthetic world: the catalog, `Pantry.md`, sixteen notes."""
    seed_catalog(runtime_root / "pantry_items.db", CATALOG_ROWS)
    write_pantry_note(runtime_root / "vault", PANTRY_NOTE_BYTES)
    for name, (materials, seasonings, last) in RECIPES.items():
        write_recipe(runtime_root / "vault", name, note_bytes(materials, seasonings, last))


@pytest.fixture
def vault(runtime_root: Path) -> Path:
    """The synthetic vault.

    `runtime_root` is the `tests/conftest.py` fixture, so this is `tmp_path` by
    CONSTRUCTION rather than by discipline: there is no code path from here to
    `/Users/syang/obsidian/syang`, only to a directory pytest made and deletes.
    """
    build_vault(runtime_root)
    return runtime_root


@pytest.fixture
def app(vault: Path, launch_app: Any) -> LoopbackApp:
    """A loopback app on its own port over the synthetic vault."""
    app: LoopbackApp = launch_app(vault)
    return app


@pytest.fixture
def booted(app: LoopbackApp, chromium: Any) -> Iterator[tuple[Any, WireLog]]:
    """A page, a wire log, and the list already painted.

    Per test, never shared: `localStorage`, `history` entries, and the bfcache
    are exactly the state a browse flow mutates, so a shared context would make
    each step depend on the one before it — and "run it twice in a row" would
    then be a claim about one long session rather than about this file.
    """
    page, wire = new_page(chromium, app)
    try:
        # `#/` and not a bare `/`: the app is a hash router (§9.3), and a PWA is
        # launched at its home route. Booting at `/` would leave the first history
        # entry with an EMPTY hash, and step 4's "the URL is `#/` after Back"
        # would then be asserting `''`, which is a different claim.
        page.goto(f"{app.base_url}/#/", wait_until="domcontentloaded")
        wait_for_rows(page)
        yield page, wire
    finally:
        page.context.close()


# --- Waiting on CONTENT. §9.4's discipline, and the anti-stale-paint guard. --

#: Not "is the element visible" — "does every row carry a non-empty headline and
#: at least one chip, and is the loading panel gone". A cached snapshot satisfies
#: a visibility check with the PREVIOUS render's text still in the node, which is
#: the exact false pass this predicate exists to prevent.
_WAIT_FOR_ROWS = """
(expected) => {
  const body = document.querySelector('[data-view="home"] [data-role="body"]');
  if (!body) return false;
  if (body.querySelector('[data-panel-state="loading"]')) return false;
  const rows = body.querySelectorAll('[data-role="recipe"]');
  if (rows.length !== expected) return false;
  for (const row of rows) {
    const headline = row.querySelector('[data-role="headline"]');
    if (!headline || headline.textContent === '') return false;
    if (row.querySelectorAll('[data-role="chips"] .chip').length === 0) return false;
  }
  return true;
}
"""

#: The first row's headline must EQUAL this string. Not merely be non-empty: the
#: whole value of the step is that the painted text is the frozen one, so the
#: wait itself is an equality and a stale paint simply never satisfies it.
_WAIT_FOR_FIRST_HEADLINE = """
(expected) => {
  const first = document.querySelector('[data-role="recipe"] [data-role="headline"]');
  return Boolean(first) && first.textContent === expected;
}
"""


def wait_for_rows(page: Page) -> None:
    page.wait_for_function(_WAIT_FOR_ROWS, arg=len(EXPECTED_ROWS), timeout=CONTENT_TIMEOUT_MS)


def wait_for_first_headline(page: Page) -> None:
    page.wait_for_function(
        _WAIT_FOR_FIRST_HEADLINE, arg=FIRST_HEADLINE, timeout=CONTENT_TIMEOUT_MS
    )


def rendered_rows(page: Page) -> list[dict[str, Any]]:
    """The list exactly as painted: one record per row, in DOM order."""
    rows: list[dict[str, Any]] = page.evaluate(
        """
        () => [...document.querySelectorAll('[data-role="recipe"]')].map((row) => ({
          note: row.dataset.note,
          found: Number(row.dataset.found),
          total: Number(row.dataset.total),
          headline: row.querySelector('[data-role="headline"]').textContent,
          chips: [...row.querySelectorAll('[data-role="chips"] .chip')].map((chip) => ({
            label: chip.textContent,
            classes: [...chip.classList],
          })),
        }))
        """
    )
    return rows


def payload_keys(node: Any) -> set[str]:
    """Every key name anywhere in a decoded payload, at any depth."""
    if isinstance(node, dict):
        found = {str(key) for key in node}
        for value in node.values():
            found |= payload_keys(value)
        return found
    if isinstance(node, list):
        found = set()
        for item in node:
            found |= payload_keys(item)
        return found
    return set()


def buckets_in(rows: list[dict[str, Any]]) -> set[str]:
    return {
        cls
        for row in rows
        for chip in row["chips"]
        for cls in chip["classes"]
        if cls in FIVE_BUCKETS
    }


def scroll_offset(page: Page) -> int:
    return int(page.evaluate("window.scrollY"))


def saved_scroll_top(page: Page) -> int | None:
    saved: int | None = page.evaluate("() => window.history.state?.scrollTop ?? null")
    return saved


def scroll_diagnosis(page: Page) -> str:
    """The three numbers that say WHY an offset did not land.

    `scrollY` alone cannot distinguish the three failures this step can have: a
    restore that never fired, a restore that fired against a short document
    (clamped to `scrollHeight - innerHeight`), and a restore that landed and was
    then moved. So the clamp bound is reported next to the offset, together with
    the entry's own saved state — a `history.state.scrollTop` of 0 with a
    non-zero clamp bound means the ENTRY was corrupted before the view read it,
    which is a different defect from a missing re-application.
    """
    facts = page.evaluate(
        """
        () => ({
          scrollY: window.scrollY,
          saved: window.history.state?.scrollTop ?? null,
          bound: document.documentElement.scrollHeight - window.innerHeight,
          height: document.documentElement.scrollHeight,
          window: window.innerHeight,
          rows: document.querySelectorAll('[data-role="recipe"]').length,
        })
        """
    )
    return (
        f"scrollY={facts['scrollY']} saved={facts['saved']} "
        f"clampBound={facts['bound']} (doc {facts['height']}px / window {facts['window']}px) "
        f"rows={facts['rows']}"
        f"\n  scrollEvents={page.evaluate('() => window.__scrollLog')}"
    )


def wait_for_scroll(page: Page, expected: int, where: str = "") -> None:
    """Wait for the exact offset, and name the offset we actually got.

    `wait_for_function` on its own reports "Timeout 5000ms exceeded", which says
    the run was slow and not that Back landed 16 pixels from the top. So the
    timeout is caught, the real `window.scrollY` is read, and the failure
    becomes the measurement #21 took.
    """
    try:
        page.wait_for_function(
            "target => window.scrollY === target", arg=expected, timeout=SCROLL_TIMEOUT_MS
        )
    except sync_api.TimeoutError as error:
        raise AssertionError(
            f"window.scrollY settled at {scroll_offset(page)}px, not {expected}px"
            f"{where}"
            f"\n  {scroll_diagnosis(page)}"
        ) from error


# --- §10.5 step 1 — the headline is one of the four frozen forms -------------


def test_step_1_the_first_row_paints_a_frozen_headline_and_no_boolean(
    booted: tuple[Page, WireLog],
) -> None:
    """Boot, wait for CONTENT, read the exact string — F17's whole claim.

    Two claims, and they are different ones. The *string* is the display
    contract: one of §9.13.1's four exact forms, byte for byte, with the
    `n/total` shape and a missing list over `材料` only. The *absence* is F17: no
    `cookable` boolean anywhere in the payload, at any depth, in any casing.

    The absence is checked against the payload the server actually sent, read off
    the wire rather than off the DOM — the DOM cannot show a field the view chose
    not to render, and F17 says the boolean must not be *published*.
    """
    page, wire = booted
    wait_for_first_headline(page)

    assert page.locator('[data-role="recipe"]').first.get_attribute("data-note") == FIRST_ROW
    headline = page.locator('[data-role="recipe"] [data-role="headline"]').first.text_content()
    assert headline is not None
    assert headline in FROZEN_HEADLINES
    # Not just "one of the four": the exact one this fixture is built to produce.
    assert headline == FIRST_HEADLINE

    payload = wire.json_for("/api/recipes")
    assert set(payload) == LIST_KEYS
    assert "cookable" not in payload_keys(payload)
    # And the substring, so a `canCookable` or a `cookableCount` cannot hide
    # behind an exact-key check. The payload is JSON, so lower-casing is lossless
    # for this purpose: an uppercase spelling is still a violation.
    assert "cookable" not in json.dumps(payload, ensure_ascii=False).lower()

    # F11's 30s TTL makes a revision a CONTENT hash, not a clock reading, so this
    # is a determinism claim and not a wall-clock one: two reads of the same
    # bytes must publish the same revision. `json_for` returns the LAST body, so
    # the two lines compare the final two reads; the property is that the answer
    # did not move between them.
    assert payload["stockRevision"].startswith("sha256:")
    assert payload["catalogRevision"].startswith("sha256:")
    assert wire.count("/api/recipes") >= 1
    revisions = {
        wire_entry.body["stockRevision"]
        for wire_entry in wire.responses
        if wire_entry.path == "/api/recipes" and isinstance(wire_entry.body, dict)
    }
    assert len(revisions) == 1, f"the stock revision moved between reads: {revisions}"


# --- §10.5 step 2 — the chip vocabulary actually PAINTS ----------------------


def test_step_2_every_chip_paints_a_five_bucket_class(booted: tuple[Page, WireLog]) -> None:
    """Rendered class names, not "a request was made".

    Asserting that `/api/recipes` was fetched proves nothing about the chips: the
    five buckets are produced by `logic/chip-class.js` in the browser, and a
    build that painted every chip `chip--in-stock` would satisfy a
    request-counting test perfectly. So this reads `classList` off every chip in
    the document, and then asserts that **all five buckets were observed** — the
    non-vacuity guard, without which a view that painted one correct chip and
    fifteen unclassified ones would still be "every chip carries a class from the
    vocabulary" for as long as the vocabulary list stayed empty.
    """
    page, _wire = booted
    rows = rendered_rows(page)
    assert len(rows) == len(EXPECTED_ROWS)

    for row in rows:
        assert row["chips"], f"{row['note']} painted a chip row with no chips in it"
        for chip in row["chips"]:
            classes = set(chip["classes"])
            assert "chip" in classes
            assert classes & FIVE_BUCKETS, (
                f"{row['note']}/{chip['label']!r} carries none of the five buckets: "
                f"{sorted(classes)}"
            )
            # `chip--manual` is a MODIFIER, never a bucket. The classifier emits it
            # alongside one of two stock classes, so a chip carrying it alone
            # would mean the ladder grew a sixth answer.
            if MANUAL_MODIFIER in classes:
                assert classes & BUCKET_PAIRS_WITH_MANUAL, (
                    f"{row['note']}/{chip['label']!r} has {MANUAL_MODIFIER} with no stock "
                    f"bucket: {sorted(classes)}"
                )

    assert buckets_in(rows) == FIVE_BUCKETS, (
        f"the fixture did not exercise the whole vocabulary; missing "
        f"{sorted(FIVE_BUCKETS - buckets_in(rows))}"
    )


# --- §10.5 step 3 — the order, and the 0/6 row that is never hidden ----------


def test_step_3_the_order_is_non_increasing_and_the_zero_row_is_visible(
    booted: tuple[Page, WireLog],
) -> None:
    """The comparator and D4's no-threshold rule, on the painted DOM.

    Non-increasing is the *property*; the full `EXPECTED_ROWS` sequence is the
    *pin*. Non-increasing alone would pass for a list sorted by nothing in
    particular — or for a list that dropped every 0-ratio row, which is exactly
    the defect F20 names. So both are asserted.

    "Visible" then means rendered, not merely non-`display:none`: a real
    bounding box, a headline with height, and no `hidden` ancestor. The viewport
    is deliberately not scrolled first, because Playwright's `is_visible()` is
    satisfied by a node outside the viewport and the question here is whether the
    row was painted at all.
    """
    page, _wire = booted
    rows = rendered_rows(page)

    assert [(row["note"], row["found"], row["total"]) for row in rows] == list(EXPECTED_ROWS)

    ratios = [row["found"] / max(row["total"], 1) for row in rows]
    assert all(
        later <= earlier for earlier, later in zip(ratios, ratios[1:], strict=False)
    ), f"foundRatio is not non-increasing along the rendered order: {ratios}"

    # §9.13.1's RULE, on every row, rather than a literal set. The four frozen
    # strings are four *examples*; the rule they illustrate is that the
    # denominator counts exactly the slots the numerator counted and the missing
    # list carries one entry per missing `材料` slot. `1/1 ingredients found` is
    # a legal rendering of that rule and is not one of the four strings, so
    # asserting membership in the four would be a fixture-shaped assertion that
    # fails the moment a recipe is added.
    for row in rows:
        assert HEADLINE_SHAPE.match(row["headline"]), (
            f"{row['note']}: {row['headline']!r} is not the n/total shape"
        )
        names = missing_names(row["headline"])
        assert len(names) == row["total"] - row["found"], (
            f"{row['note']}: {row['total'] - row['found']} missing slot(s) but "
            f"{len(names)} name(s) in {row['headline']!r} — §9.13.1 says one entry per slot"
        )

    # And the three rows whose rendered text must be a frozen string BYTE for
    # byte, not merely the right shape.
    for note, expected in PINNED_HEADLINES.items():
        row = next(row for row in rows if row["note"] == note)
        assert row["headline"] == expected
    assert set(PINNED_HEADLINES.values()) <= FROZEN_HEADLINES | {
        ZERO_SIX_HEADLINE,
        # `蒸鱼豉油拌面` is the fifth pinned line and is deliberately NOT one of
        # §9.13.1's four literals: it is the spec's RULE over a slot that
        # resolved to a Pantry Item with nothing on the shelf, which the four
        # examples do not contain. A rule that cannot render a line outside its
        # own examples has not been tested.
        "2/4 ingredients found — missing: 蒸鱼豉油, 面条",
    }

    zero = next(row for row in rows if row["note"] == ZERO_SIX_ROW)
    assert (zero["found"], zero["total"]) == (0, 6)
    assert zero["headline"] == ZERO_SIX_HEADLINE
    # Six 材料 plus one 调料, so a chip per slot: a row that lost slots is a
    # threshold filter wearing a different hat.
    assert len(zero["chips"]) == 7

    locator = page.locator(f'[data-role="recipe"][data-note="{ZERO_SIX_ROW}"]')
    assert locator.is_visible()
    box = locator.bounding_box()
    assert box is not None and box["height"] > 0
    assert locator.evaluate(
        """
        (row) => {
          const headline = row.querySelector('[data-role="headline"]');
          return headline.getBoundingClientRect().height > 0
            && !row.closest('[hidden]')
            && getComputedStyle(row).display !== 'none';
        }
        """
    )
    # The three 0-ratio rows are all present. A threshold would drop or collapse
    # one, and either shows up here by name rather than as a count.
    assert [row["note"] for row in rows if row["found"] == 0] == [
        "空心三缺",
        "零样菜",
        "空白配菜",
    ]


# --- §10.5 step 4 — back-button scroll restore. THE REASON FOR THIS FILE. -----


def test_step_4_back_restores_the_scroll_offset(booted: tuple[Page, WireLog]) -> None:
    """`640 → 0 → 640 → 0 → 640`, round-tripped twice.

    The measurement, in the shape #21 took: the router calls `scrollTo` right
    after `mount()` returns, but at that instant the list is the one-line loading
    panel, roughly 16px tall, so `scrollTo(0, 640)` **clamps to 16**.
    `history.state` holds 640 the whole time — only the DOM is too short — and
    `home.js` re-applies the offset once the rows exist.

    So the assertion is deliberately about the DOM: `window.scrollY`, exact. A
    version of this step that checked `location.hash` and `history.state` would
    pass against the broken build, which is why the ticket forbids relaxing it.
    The state is asserted too, and the two together are the point: in the broken
    build the state is right and the offset is wrong, so an assertion on either
    alone is a coin flip.

    Two details that are load-bearing rather than cosmetic:

    * **The link is clicked from inside the page.** Playwright's actionability
      check scrolls an element into view first, and that scroll fires the view's
      own tracked scroll handler, which would `replaceState` a *different* number
      into the very `history.state.scrollTop` this step is about. A real
      thumb-tap does not scroll the page before it lands, so `element.click()` is
      the more faithful event as well as the more controlled one.
    * **The document must be tall enough.** Asserted, not assumed: if a future
      fixture change made 640 unreachable, the round trip would "restore" 0 to 0
      and pass forever.
    """
    page, _wire = booted

    headroom = page.evaluate("document.documentElement.scrollHeight - window.innerHeight")
    assert headroom > SCROLL_TARGET, (
        f"the fixture document is only {headroom}px scrollable, so a {SCROLL_TARGET}px "
        f"offset is unreachable and this step would be vacuous"
    )

    for round_number in (1, 2):
        # Scroll the list, and let the view's rAF-throttled scroll listener save
        # it into ITS OWN history entry before anything navigates.
        page.evaluate("top => window.scrollTo(0, top)", SCROLL_TARGET)
        wait_for_scroll(page, SCROLL_TARGET)
        page.wait_for_function(
            "top => window.history.state?.scrollTop === top", arg=SCROLL_TARGET
        )
        assert saved_scroll_top(page) == SCROLL_TARGET, (
            f"round {round_number}: the view did not record the offset it was scrolled to"
        )

        # Into the detail. In-page click, so no actionability scroll.
        page.evaluate(
            "note => document.querySelector("
            "`[data-role='recipe-link'][data-note='${note}']`).click()",
            DETAIL_ROUTE,
        )
        page.wait_for_function(
            "() => Boolean(document.querySelector('[data-view=\"recipe\"]'))"
        )
        # A forward navigation starts at the top — that is what stops the detail
        # from inheriting the list's offset, so it is part of the shape.
        wait_for_scroll(page, 0)
        # The route is percent-encoded (§9.3: the basename is URL-encoded, so a
        # decoded `/` inside a name cannot invent a path segment), so the hash is
        # decoded before it is compared to the recipe name.
        assert page.evaluate("() => decodeURIComponent(window.location.hash)") == (
            f"#/recipe/{DETAIL_ROUTE}"
        )

        # And back. The entry's state is re-pinned to the target immediately
        # before the traversal, so the measurement is the VIEW's restore rather
        # than whatever the browser's per-entry scroll memory happens to hold.
        page.evaluate(
            "top => window.history.replaceState("
            "{ ...window.history.state, scrollTop: top }, '')",
            SCROLL_TARGET,
        )
        # NO control here. #24 froze scroll EVENTS across this traversal because
        # the engine's reset-to-0 is a scroll event the OUTGOING detail view's
        # tracked-scroll handler was still attached to; that write landed in the
        # entry being arrived at, so `restoreTop` was read back already corrupted
        # and the flow failed in roughly one full-suite run in four. The fix is
        # in `router.js` (a traversal latch on `saveViewState`) and in
        # `views/recipe.js` (the entry epoch on its own tracked-scroll save), so
        # the freeze is gone and the measurement is the app's.
        page.evaluate("window.history.back()")
        wait_for_rows(page)
        assert page.evaluate("window.location.hash") == "#/"

        # THE assertion, and it is deliberately first. In the broken build the
        # router's too-early `scrollTo(0, 640)` clamps to 16 and nothing re-applies
        # it, so `window.scrollY` stays at 16 forever and this times out with a
        # message that says so. A version of this step that checked the hash and
        # the state would pass against that build, which is why the ticket
        # forbids relaxing it into one.
        wait_for_scroll(page, SCROLL_TARGET, where=f" (round {round_number})")
        assert scroll_offset(page) == SCROLL_TARGET, (
            f"round {round_number}: Back landed at {scroll_offset(page)}px, not "
            f"{SCROLL_TARGET}px — the restore clamped against a too-short document"
            f"\n  {scroll_diagnosis(page)}"
        )
        # The entry's own state converges on 640 too, but only AFTER the offset
        # has been restored — for one frame it reads the router's clamped 16
        # again, because the too-early `scrollTo` fires this view's own tracked
        # scroll listener, and the view then writes 640 back from the `resumeTop`
        # it captured at mount. So this WAITS rather than reads. It is a secondary
        # guard, not the claim: under either injected bug the state settles at 16
        # and this fails as well.
        page.wait_for_function("top => window.history.state?.scrollTop === top", arg=SCROLL_TARGET)
        assert saved_scroll_top(page) == SCROLL_TARGET

    # Still on the list, and still whole: the two round trips cannot have passed
    # by leaving the page somewhere degenerate.
    assert page.evaluate("window.location.hash") == "#/"
    assert len(rendered_rows(page)) == len(EXPECTED_ROWS)


# --- §10.5 step 5 — 调试 is render-only, observed not assumed -----------------


def test_step_5_the_debug_toggle_repaints_without_a_request(booted: tuple[Page, WireLog]) -> None:
    """F7's claim, watched rather than trusted.

    F7: provenance is in every `GET /api/recipes` payload, `调试` is a switch over
    `localStorage`, and there is no `?debug=1`, no reduced response, and no second
    request. That is four negative claims, and this test turns three of them into
    assertions:

    * toggling issues **zero** requests of any kind;
    * no request anywhere in the session ever carried a `debug` query parameter;
    * the provenance text is present after a **reload**, from `localStorage`
      alone, with no server round-trip having happened to enable it.

    A `?debug=1` route or a second response shape breaks the second assertion
    outright, and a toggle that re-fetched breaks the first.
    """
    page, wire = booted

    page.evaluate("() => { window.location.hash = '#/settings'; }")
    page.wait_for_function("() => Boolean(document.querySelector('[data-view=\"settings\"]'))")
    off = page.get_by_role("button", name="调试：关")
    off.wait_for(state="visible", timeout=CONTENT_TIMEOUT_MS)
    assert page.evaluate(f"localStorage.getItem('{DEBUG_KEY}')") is None

    mark = wire.mark()
    off.click()
    page.get_by_role("button", name="调试：开").wait_for(
        state="visible", timeout=CONTENT_TIMEOUT_MS
    )
    # Let any DEFERRED request land before claiming there was none. Without this,
    # a toggle that fetched on a `setTimeout(0)` would pass.
    page.wait_for_timeout(QUIET_WINDOW_MS)
    assert wire.since(mark) == [], (
        f"toggling 调试 issued {len(wire.since(mark))} request(s): "
        f"{[wire.target for wire in wire.since(mark)]}"
    )
    assert page.evaluate(f"localStorage.getItem('{DEBUG_KEY}')") == "1"

    # Now the render consequence: the tier line is on the chips, with no fetch
    # beyond the list read the re-mount itself performs.
    page.evaluate("() => { window.location.hash = '#/'; }")
    wait_for_rows(page)
    tiers = page.evaluate(
        """
        () => [...document.querySelectorAll('[data-role="chips"] [data-role="provenance"]')]
          .map((node) => node.textContent)
        """
    )
    assert tiers, "the 调试 toggle painted no provenance line"
    assert all(PROVENANCE_LINE.match(text) for text in tiers), (
        f"a provenance line does not match §9.13.4's shape: {tiers[:3]}"
    )
    # §9.13.4: one per slot, so one per chip. The render is a switch over the SAME
    # payload, not a re-derivation from a second source, and the counts prove it.
    chip_count = page.evaluate(
        "document.querySelectorAll('[data-role=\"chips\"] .chip').length"
    )
    assert len(tiers) == chip_count

    # And it survives a reload, from localStorage alone.
    before = wire.count("/api/recipes")
    page.reload(wait_until="domcontentloaded")
    wait_for_rows(page)
    wait_for_first_headline(page)
    after_tiers = page.evaluate(
        "document.querySelectorAll('[data-role=\"chips\"] [data-role=\"provenance\"]').length"
    )
    assert after_tiers == chip_count
    assert page.evaluate(f"localStorage.getItem('{DEBUG_KEY}')") == "1"
    # A reload re-reads the list once, because a reload is a boot. The claim under
    # test is that the TOGGLE costs nothing, and the two are kept apart by
    # measuring across the reload rather than around the click.
    assert wire.count("/api/recipes") == before + 1

    assert wire.query_values("debug") == [], (
        "a request carried a `debug` query parameter — F7's no-`?debug=1` route is back"
    )
    # The five buckets are unchanged by the toggle, so the provenance is an overlay
    # on the SAME classification rather than a second one.
    assert buckets_in(rendered_rows(page)) == FIVE_BUCKETS


# --- Beyond §10.5's five steps, on the same seam ------------------------------


def test_the_service_worker_is_refused_rather_than_merely_unused(
    booted: tuple[Page, WireLog],
) -> None:
    """The harness's own control, asserted, so "deterministic" is not a hope.

    F18 pairs `autoApply: false` with `sw.js`'s `WAIT_FOR_MESSAGE = true`; move
    either alone and the banner never appears. A flow that let a worker install
    would read whatever precached bytes a prior navigation had left, which is the
    stale-paint false pass manufactured by the harness instead of by the bug. So
    the context blocks it — and here the block is checked, so a future change
    that "helpfully" allows the worker back in fails rather than flaking.
    """
    page, wire = booted
    assert page.evaluate("() => navigator.serviceWorker.controller") is None
    registrations = page.evaluate(
        "async () => (await navigator.serviceWorker.getRegistrations()).length"
    )
    assert registrations == 0
    # The APIs are `network-only` and the shell is `no-store` regardless, so the
    # list was read from the loopback app rather than from a cache.
    assert wire.count("/api/recipes") >= 1
    assert wire.count("/") >= 1


def test_an_unreadable_pantry_note_fails_the_whole_list_closed(
    runtime_root: Path, launch_app: Any, chromium: Any
) -> None:
    """F1: a 503 for the WHOLE list, and a real panel rather than a blank screen.

    `Pantry.md` is DELETED rather than chmod-ed, because the unreadable mode
    needs a non-root uid and root reads a mode-0 file happily — the skip the API
    tests carry. A missing note raises the same `PantryError`, so the published
    code is the same one, and this way the flow is deterministic on any machine.
    It also means the fixture never depends on a permission bit that a
    filesystem could ignore.

    The point of the test is the PAIR: the envelope is exactly
    `{requestId, code}` — no `recipes`, no slot, no count, nothing a client could
    render as "these are your recipes" — AND the screen shows a titled panel that
    says out loud that no chip is being shown and why. A 503 that rendered an
    empty list would be the confident-wrong answer F1 exists to prevent.
    """
    build_vault(runtime_root)
    (runtime_root / "vault" / PANTRY_NOTE).unlink()

    app = launch_app(runtime_root)
    page, wire = new_page(chromium, app)
    try:
        page.goto(app.base_url, wait_until="domcontentloaded")
        panel = page.locator('[data-role="source-unavailable"]')
        panel.wait_for(state="visible", timeout=CONTENT_TIMEOUT_MS)

        # Nothing was served that could be mistaken for a list.
        assert page.locator('[data-role="recipe"]').count() == 0
        assert page.locator('[data-role="chips"]').count() == 0
        assert page.locator('[data-panel-state="unavailable"]').count() == 1

        # The panel says the three things a blank screen cannot: what broke, that
        # no chip is being shown, and the trail a support answer needs.
        text = panel.text_content() or ""
        assert "读不到" in text
        # The UI's own copy names the note, because the user has to go and fix
        # that file. It is a VAULT-RELATIVE path the user already knows, which is
        # exactly why §9.19's rule is about server-owned ABSOLUTE paths.
        assert "Pantry.md" in text
        note_line = page.locator('[data-role="unavailable-note"]').text_content() or ""
        assert "这里没有显示任何食材 chip" in note_line
        trail = page.locator('[data-role="error-trail"]').text_content() or ""
        assert STOCK_UNREADABLE_CODE in trail
        assert "requestId" in trail

        # And the envelope itself, read off the wire.
        failure = next(
            entry
            for entry in reversed(wire.responses)
            if entry.path == "/api/recipes" and entry.status == 503
        )
        assert isinstance(failure.body, dict)
        assert set(failure.body) == {"requestId", "code"}
        assert failure.body["code"] == STOCK_UNREADABLE_CODE
        assert isinstance(failure.body["requestId"], str) and failure.body["requestId"]
        # §9.16 widens the envelope for exactly two codes, both the Cooking Log's,
        # so a 503 here carries no `message` and nothing a client could render as
        # a list. The API suite asserts that; here it is the same bytes on screen.
        assert "message" not in failure.body
        assert "recipes" not in failure.body
        assert str(runtime_root) not in json.dumps(failure.body, ensure_ascii=False)

        # §9.19: no server-owned ABSOLUTE path on any surface. The tmp root the
        # vault lives under must not leak into what the user is shown.
        assert str(runtime_root) not in f"{text}{note_line}{trail}"
    finally:
        page.context.close()


# --- The suite-wide property, asserted for this file rather than assumed -----


def test_this_flow_reads_no_live_path(runtime_root: Path, app: LoopbackApp) -> None:
    """The invariant the whole suite is built to keep, asserted for #24.

    `/Users/syang/obsidian/syang` is the user's live, machine-rewritten vault. A
    test that read it would pass on one machine and assert nothing on every
    other, and would start failing the day the user bought something — but worse,
    a test that *wrote* it would damage real notes. So the four server-owned
    roots are asserted to be under this test's `tmp_path`, the recipes this flow
    browsed are asserted to be exactly the sixteen this file names, and the
    running app's environment is asserted to pin every key `app/config.py` reads,
    so a stray shell export cannot redirect one either.
    """
    pytest_root = Path(str(runtime_root)).parent.parent
    for name in ("vault", "data"):
        root = runtime_root / name
        assert root.is_dir(), root
        assert root.is_relative_to(pytest_root), root
    assert (runtime_root / "pantry_items.db").is_file()

    recipes = runtime_root / "vault" / RECIPES_ROOT
    assert recipes.is_dir()
    assert sorted(path.stem for path in recipes.glob("*.md")) == sorted(RECIPES)
    assert len(RECIPES) == 16

    # The app that answered this test's browser really was pointed at that tree,
    # and at loopback on the port the driver chose — one port for both ends.
    assert app.env["OBSIDIAN_VAULT_PATH"] == str(runtime_root / "vault")
    assert app.env["APP_DATA_DIR"] == str(runtime_root / "data")
    assert app.env["PANTRY_ITEMS_DB"] == str(runtime_root / "pantry_items.db")
    assert app.env["PUBLIC_ORIGIN"] == app.base_url
    assert app.base_url == f"http://127.0.0.1:{app.port}"
    assert app.base_url.startswith("http://127.0.0.1:")

    # All fourteen `Settings` keys are pinned, so the developer's live
    # `OBSIDIAN_VAULT_PATH` was removed before the four were set: inert, not
    # merely out-voted.
    for key in SETTINGS_KEYS:
        assert key in app.env, key

    # The driver's viewport is pinned, so a step that depends on document height
    # is pinned too rather than inheriting whatever window ran last.
    assert VIEWPORT == {"width": 390, "height": 640}

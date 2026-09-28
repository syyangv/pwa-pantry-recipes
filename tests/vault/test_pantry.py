"""The ported `Pantry.md` state parser, and the money math that must not drift.

**No test here reads or writes `/Users/syang/obsidian/syang`.** The live note is
committed frozen at `tests/fixtures/pantry/Pantry.md` (sha256 asserted in
`test_frozen_note_fixture_is_the_note_it_claims_to_be`), and every other vault a
test needs is built under `tmp_path` from the `settings` fixture. A test that
reads the user's real vault is a defect, not a convenience.

**Where the money figure comes from.** Three implementations of the per-unit
`💵` math exist and must agree: the `existingPantryValue` dataviewjs in
`Logistics/库存/Pantry.md`, `Helper/scripts/pantry_snapshot.py`, and
`app.vault.pantry.untagged_inventory` here. Only the third is in this repo, so
parity is asserted two ways: against a *transcription* of the second one's
arithmetic over the same frozen bytes, and against the frozen number itself
(40 项 · $166.90, verified against
`python3 Helper/scripts/pantry_snapshot.py --dry-run` at freeze time). The
transcription is a copy of live code and cannot track it automatically — §13.16
states that plainly — so the frozen numbers are the part that fails loudly.

**Why there are three separate "mutant" tests.** A parity test that cannot fail
is worse than none, and the two things a false-passing test would hide are the
per-unit parent exclusion and `derived_status`. Each rule below therefore has a
test that pins the correct answer *and* the plausible-wrong answer, so the
assertion is proven to distinguish them, and each is additionally run against a
deliberately broken copy of the rule in `test_money_math_mutants_are_killed`.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path

import pytest

from app.config import Settings
from app.vault.atomic_write import AtomicNoteStore, PathSafetyError
from app.vault.pantry import (
    PantryError,
    PantryIndex,
    PantryToggleConflict,
    derived_status,
    parse_pantry,
    parse_pantry_flow_events,
    product_week,
    toggle_pantry,
    untagged_inventory,
    week_flow,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "pantry" / "Pantry.md"
#: sha256 of `tests/fixtures/pantry/Pantry.md` as frozen from
#: `Logistics/库存/Pantry.md` on 2026-09-27. If a maintainer edits the fixture,
#: this fails and forces the parity numbers below to be re-derived and re-checked
#: against `Helper/scripts/pantry_snapshot.py` rather than quietly updated.
FROZEN_SHA256 = "6a231469ab208483b5dfef0b6720ef46e205b23e21b1d2a50352ddc77dc20aea"
#: `pantry_snapshot.py --dry-run` on that exact note: `📦 40 项 · 💵 $166.90`.
FROZEN_COUNT = 40
FROZEN_TOTAL = 166.90
#: Section 1/2/4 parents skipped in favour of their `k/N` units: 玉子豆腐, 黑米,
#: Wang Korea 甘栗仁, and the [x]-derived 甘栗仁 parent is dropped outright.
FROZEN_EXCLUDED_PARENTS = 4

NOTE = """---
modified_at: 2026-08-07
tags:
cssclasses:
  - hide-frontmatter
---
![[noteNav]]
[[genTOC]]

```dataviewjs
const NAMES = { '1': '冰箱', '2': '干货' };
```

# 1 冰箱

```dataviewjs
const SEC = '1';
```

## 1.1 冷藏
```tasks
filename includes Nini's
not done
```
- [ ] Icelandic Provisions Skyr 4.4 oz 💵 $1.67 ✍️ 2026-06-30
- [ ] 中华 玉子豆腐 245 克 💵 $2.99 ✍️ 2026-08-07
\t- [ ] 1/2 ✍️ 2026-08-07
\t- [ ] 2/2 ✍️ 2026-08-07
\t- [x] 3/3 ✍️ 2026-08-07 ✅ 2026-08-07
- [x] 手作酱香饼 350 克 💵 $5.99 ✍️ 2026-08-07 ✅ 2026-08-07
- [/] 精选顶级白桃礼盒 4 磅 💵 $10.88 ✍️ 2026-08-07 🛫 2026-08-07
- [>] 推迟的牛奶 ✍️ 2026-08-07
- [-] 已取消的麦片 ✍️ 2026-08-07
# 2 干货
- [ ] Manuka Honey MGO 50+ ✍️ 2026-05-18
\t- [ ] 1/2 ✍️ 2026-08-07
\t- [x] 2/2 ✍️ 2026-08-07 ✅ 2026-08-07
# 3 早餐
# 4 零食
- [x] 乐事 2026 薯片 炸鱼薯条味 💵 $1.59 ✅ 2026-08-07
\t- [ ] 2/2 ✍️ 2026-08-07
"""

CRLF_NOTE = NOTE.replace("\n", "\r\n")


def _sections(source: str | bytes):
    return parse_pantry(source.encode() if isinstance(source, str) else source).sections


# --- Structure ------------------------------------------------------------


def test_section_order_and_membership():
    sections = _sections(NOTE)
    assert [s.number for s in sections] == ["1", "2", "3", "4"]
    assert [s.title for s in sections] == ["冰箱", "干货", "早餐", "零食"]
    assert sections[0].heading == "# 1 冰箱"


def test_fence_embed_and_frontmatter_lines_are_skipped():
    # A task-looking line inside a ```tasks fence, one inside a dataviewjs
    # fence, an embed, and an HTML comment: none is a row.
    note = """# 1 冰箱
```tasks
- [ ] this is a query, not an item
```
```dataviewjs
const s = '- [ ] not an item either';
```
![[noteNav]]
<!-- - [ ] not a comment row either -->
- [ ] real item
"""
    sections = _sections(note)
    assert len(sections[0].items) == 1
    assert sections[0].items[0].text == "real item"


def test_items_before_first_section_ignored():
    sections = _sections("- [ ] orphan before any section\n# 1 冰箱\n- [ ] real\n")
    assert len(sections) == 1
    assert len(sections[0].items) == 1


def test_crlf_note_parses_identically():
    lf = _sections(NOTE)
    crlf = _sections(CRLF_NOTE)
    assert [s.count for s in lf] == [s.count for s in crlf]
    assert lf[0].total_money == crlf[0].total_money
    assert [i.text for s in lf for i in s.items] == [
        i.text for s in crlf for i in s.items
    ]


def test_byte_span_start_end_is_exactly_the_task_line():
    """The span discipline: `start`/`end` bracket one whole line, nothing else.

    `line_index` is an index into the **body** line stream, not the file's, so
    the frontmatter's six lines are the offset between them — a consumer that
    wanted a file line number would get the wrong one, which is why the toggle
    locator is the byte span.
    """
    source = NOTE.encode()
    snapshot = parse_pantry(source)
    frontmatter_lines = 6
    for item in snapshot.items:
        span = source[item.start : item.end]
        # The span covers the indent too: the toggle patch needs the whole line.
        assert span.lstrip(b" \t").startswith(b"- [")
        assert b"\n" not in span.rstrip(b"\r\n")
        assert span == source.splitlines(keepends=True)[item.line_index + frontmatter_lines]
    # Distinct rows never share a span, and no row is empty.
    spans = [(i.start, i.end) for i in snapshot.items]
    assert len(set(spans)) == len(spans)
    assert all(end > start for start, end in spans)
    assert snapshot.note_revision == "sha256:" + hashlib.sha256(source).hexdigest()


# --- Status classification and derived_status -----------------------------


def test_open_statuses_and_counts_per_unit():
    sections = _sections(NOTE)
    # Section 1: Skyr, 1/2, 2/2, 白桃(in_progress) = 4 counted units. The
    # 玉子豆腐 parent row is listed but never counted: its 💵 is the per-unit
    # price and its open units carry the money.
    assert sections[0].count == 4
    # Section 2: Manuka 1/2 only (2/2 done, and the parent is a price header).
    assert sections[1].count == 1
    assert sections[2].count == 0
    # Section 4: the [x] parent derives from the open 2/2 unit; only the unit
    # is counted, and the parent is still returned as a header row.
    assert sections[3].count == 1
    assert [i.has_units for i in sections[3].items] == [True, False]


def test_done_parent_context_and_unit_rows():
    section4 = _sections(NOTE)[3]
    assert [item.status for item in section4.items] == ["open", "open"]
    assert section4.items[0].text.startswith("乐事")
    assert section4.items[0].has_units is True
    assert section4.items[1].text.startswith("2/2")


def test_unrecognized_markers_are_never_open():
    section1 = _sections(NOTE)[0]
    texts = [item.text for item in section1.items]
    # [x] deferred, [>] forwarded, [-] cancelled: none is listed, none is done.
    assert not any("手作" in t for t in texts)
    assert not any("推迟" in t for t in texts)
    assert not any("取消" in t for t in texts)
    # The done 3/3 unit of an open parent is dropped too.
    assert not any("3/3" in t for t in texts)
    # [X] uppercase is the done marker, not a distinct status.
    upper = _sections("# 1 冰箱\n- [X] 完成的 💵 $1.00 ✅ 2026-08-07\n")
    assert upper[0].items == ()


def test_derived_status_all_three_branches():
    """`derived_status` is a pure function of the tree, so it is tested as one.

    Sibling-less rows report their own marker; a parent is `done` only when
    **all** children are, `in_progress` when **any** child is, and `open`
    otherwise. Getting the `all`/`any` pair backwards silently hides a finished
    multipack, so the branches are pinned independently of any note.
    """
    # Rows 1..3 are children of row 0; rows 4..5 are children of row 7, which
    # is itself a child of row 6. An acyclic tree, or `derived_status` loops.
    markers = {
        1: "open",
        2: "in_progress",
        3: "done",
        4: "done",
        5: "open",
        6: "open",
        7: "open",
    }
    raw = markers.__getitem__

    # No children -> the row's own raw status, for all three raw values.
    assert derived_status(raw, {}, 1) == "open"
    assert derived_status(raw, {}, 2) == "in_progress"
    assert derived_status(raw, {}, 3) == "done"

    # `all` branch: every child done -> done. One non-done child leaves it.
    assert derived_status(raw, {0: [3, 3]}, 0) == "done"
    assert derived_status(raw, {0: [3, 2]}, 0) == "in_progress"
    assert derived_status(raw, {0: [3, 1]}, 0) == "in_progress"

    # `any` branch: one done or in_progress child, none else done -> in progress.
    assert derived_status(raw, {0: [2, 1]}, 0) == "in_progress"
    assert derived_status(raw, {0: [3, 1]}, 0) == "in_progress"

    # `else` branch: children exist, none done or in progress -> open.
    assert derived_status(raw, {0: [1, 1]}, 0) == "open"

    # It recurses, so a grandchild's status reaches the grandparent: a done
    # grandchild makes its child done, which then makes the parent done.
    assert derived_status(raw, {6: [7], 7: [4]}, 6) == "done"
    assert derived_status(raw, {6: [7], 7: [4, 4]}, 6) == "done"
    assert derived_status(raw, {6: [7], 7: [4, 5]}, 6) == "in_progress"
    assert derived_status(raw, {6: [7], 7: [5, 5]}, 6) == "open"
    # The parent's own marker is irrelevant once it has children.
    assert derived_status(raw, {6: [7], 7: [5, 5]}, 6) == "open"


def test_derived_status_through_a_note():
    note = """# 4 零食
- [ ] 乐事 牛肉派味 90 克 💵 $1.89 ➕ 2026-08-06
\t- [ ] 1/2 ➕ 2026-08-06
\t- [ ] 2/2 ➕ 2026-08-06
- [ ] 甘栗仁 300 克 💵 $7.59 ➕ 2026-08-06
- [ ] 全用完的 💵 $2.00
\t- [x] 1/2
\t- [x] 2/2
- [/] 用了一半的 💵 $4.00
\t- [x] 1/2
\t- [ ] 2/2
"""
    rows = {item.text: item for item in _sections(note)[0].items}
    # All units open -> the parent is open.
    assert rows["乐事 牛肉派味 90 克"].status == "open"
    # A `[ ]` parent above finished units derives to `done` and disappears.
    assert "全用完的" not in rows
    # One unit spent -> the parent is in progress, not open and not done.
    assert rows["用了一半的"].status == "in_progress"
    assert rows["甘栗仁 300 克"].status == "open"
    assert rows["甘栗仁 300 克"].has_units is False


# --- Money: the per-unit invariant ----------------------------------------


def test_unit_rows_inherit_parent_money():
    section1 = _sections(NOTE)[0]
    tofu = next(i for i in section1.items if i.text.startswith("中华"))
    units = [i for i in section1.items if i.text.startswith(("1/2", "2/2"))]
    assert tofu.money == 2.99
    assert [u.money for u in units] == [2.99, 2.99]
    # 1.67 + 2.99*2 + 10.88 = 18.53. The split parent's own 💵 is never added.
    assert section1.total_money == 18.53


def test_money_thousands_format():
    assert _sections("# 1 冰箱\n- [ ] 大件 💵 $1,234.50 ✍️ 2026-08-07\n")[0].items[
        0
    ].money == 1234.5


def test_unit_child_without_money_does_not_reach_a_grandparent_price():
    # The walk stops at the first lower-indent row even when that row has no
    # price: a grandparent's 💵 is not this unit's 💵.
    note = """# 1 冰箱
- [ ] 外层 无价
\t- [ ] 内层 无价
\t\t- [ ] 1/2
"""
    rows = {i.text: i for i in _sections(note)[0].items}
    assert rows["1/2"].money is None


def test_untagged_inventory_excludes_the_unit_parent():
    note = """# 1 冰箱
- [ ] 玉子豆腐 245 克 💵 $2.99 ➕ 2026-09-10
\t- [ ] 1/2 ➕ 2026-09-10
\t- [ ] 2/2 ➕ 2026-09-10
"""
    figure = untagged_inventory(parse_pantry(note.encode()))
    # ONE unit at ONE per-unit price — not the parent plus two units ($8.97),
    # and not the parent instead of its units ($2.99 for two units).
    assert (figure.count, figure.total, figure.excluded_parents) == (2, 5.98, 1)
    snapshot = parse_pantry(note.encode())
    assert snapshot.total == 2 and snapshot.total_money == 5.98


def test_untagged_inventory_counts_in_progress_and_drops_done():
    note = """# 1 冰箱
- [ ] 开的 💵 $1.00
- [/] 进行中 💵 $2.00
- [x] 吃完了 💵 $3.00
- [-] 取消了 💵 $4.00
- [>] 转发了 💵 $5.00
"""
    figure = untagged_inventory(parse_pantry(note.encode()))
    # [/] is held; [x], [-] and [>] are not, and the last two never become rows.
    assert (figure.count, figure.total) == (2, 3.0)


def test_untagged_inventory_drops_tagged_rows():
    note = """# 1 冰箱
- [ ] 无标签 💵 $1.00
- [/] 有标签 #SF 💵 $2.00
- [ ] 两个标签 💵 $3.00 #SF #local
\t- [ ] 1/2 #SF
"""
    figure = untagged_inventory(parse_pantry(note.encode()))
    # The tag lives on the row, so a tagged parent takes its tagged units with
    # it: the figure is the 无标签 chip, not "everything except tagged parents".
    assert (figure.count, figure.total) == (1, 1.0)


# --- The frozen-note parity obligation ------------------------------------


def test_frozen_note_fixture_is_the_note_it_claims_to_be():
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == FROZEN_SHA256
    # Sanity on the shape of the freeze, so a truncated copy cannot pass.
    assert len(raw) == 31_706
    assert len([line for line in raw.decode().split("\n") if re.match(r"^\s*- \[", line)]) == 73


def test_frozen_note_parity_numbers_are_pinned():
    """The agreed figure, from `pantry_snapshot.py --dry-run` on this exact note.

    40 项 · $166.90. This is the number a drift in any of the three
    implementations shows up in, and §13.16's honest limitation is that only
    this one is in CI — so it is pinned literally rather than recomputed.
    """
    figure = untagged_inventory(parse_pantry(FIXTURE.read_bytes()))
    assert figure.count == FROZEN_COUNT
    assert figure.total == FROZEN_TOTAL
    assert figure.excluded_parents == FROZEN_EXCLUDED_PARENTS


def _reference_script_arithmetic(source: bytes) -> tuple[int, float]:
    """A transcription of `Helper/scripts/pantry_snapshot.py::compute`.

    Copied, not imported: the live script reaches into the user's vault by
    absolute path, and a test must not. Its own regexes and its three skips are
    reproduced exactly — `^#\\s+([1-6])\\b` for the section, `x`/`>`/`-` skipped,
    `[/]` counted, the parent of a `\\d+/\\d+` row skipped while that row is
    priced from the parent, and any `#tag` dropped.

    Re-check this transcription whenever `FROZEN_SHA256` changes; the frozen
    numbers above are what catch a divergence, not this function.
    """
    section_re = re.compile(r"^#\s+([1-6])\b")
    task_re = re.compile(r"^(\s*)-\s+\[([^\]])\]\s?(.*)$")
    amount_re = re.compile(r"💵\s*\$?([\d,]+(?:\.\d+)?)")
    unit_re = re.compile(r"\d+/\d+")
    tag_re = re.compile(r"(?:^|[ \t])#[^\s#]+")

    def amount_of(text: str) -> float:
        m = amount_re.search(text)
        return float(m.group(1).replace(",", "")) if m else 0.0

    tasks: list[dict[str, object]] = []
    section: str | None = None
    in_code = False
    for idx, line in enumerate(source.decode("utf-8").split("\n")):
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        found = section_re.match(line)
        if found:
            section = found.group(1)
            continue
        found = task_re.match(line)
        if found:
            tasks.append(
                {
                    "line": idx + 1,
                    "indent": len(found.group(1).expandtabs(4)),
                    "marker": found.group(2),
                    "text": found.group(3),
                    "section": section,
                }
            )

    by_line: dict[int, dict[str, object]] = {}
    parent_of: dict[int, int] = {}
    for task in tasks:
        line = int(task["line"])  # type: ignore[call-overload]
        by_line[line] = task
    for task in tasks:
        line = int(task["line"])  # type: ignore[call-overload]
        candidates = [
            other
            for other in tasks
            if int(other["line"]) < line  # type: ignore[call-overload]
            and int(other["indent"]) < int(task["indent"])  # type: ignore[call-overload]
        ]
        if candidates:
            parent_of[line] = max(candidates, key=lambda o: int(o["line"]))["line"]  # type: ignore[call-overload]

    unit_parents = {
        parent_of[line]  # type: ignore[index]
        for line, task in by_line.items()
        if parent_of.get(line) is not None and unit_re.search(str(task["text"]))
    }

    count = 0
    total = 0.0
    for task in tasks:
        line = int(task["line"])  # type: ignore[call-overload]
        if task["section"] is None:
            continue
        if task["marker"] in ("x", ">", "-"):
            continue
        if line in unit_parents:
            continue
        if tag_re.search(str(task["text"])):
            continue
        count += 1
        text = str(task["text"])
        value = amount_of(text)
        if not value and parent_of.get(line) is not None and unit_re.search(text):
            parent = by_line.get(parent_of[line])
            if parent:
                value = amount_of(str(parent["text"]))
        total += value
    return count, round(total, 2)


def test_money_math_is_in_parity_with_the_reference_script():
    """The parity assertion: same frozen bytes, two implementations, one answer."""
    raw = FIXTURE.read_bytes()
    mine = untagged_inventory(parse_pantry(raw))
    theirs_count, theirs_total = _reference_script_arithmetic(raw)
    assert (mine.count, mine.total) == (theirs_count, theirs_total)
    # And the agreed value, so the transcription cannot drift away silently.
    assert (mine.count, mine.total) == (FROZEN_COUNT, FROZEN_TOTAL)


def test_the_frozen_note_does_not_double_count_its_unit_parents():
    """The four excluded parents would each add money if the rule were dropped."""
    snapshot = parse_pantry(FIXTURE.read_bytes())
    parents = [i for i in snapshot.items if i.has_units]
    assert len(parents) == FROZEN_EXCLUDED_PARENTS
    excluded_money = round(sum(p.money or 0.0 for p in parents), 2)
    assert excluded_money == 17.45
    # Dropping the exclusion rule would charge 玉子豆腐 twice, 2.99 x 2, and so
    # on: the untagged figure moves off the frozen 166.90 by exactly that much.
    assert round(untagged_inventory(snapshot).total + excluded_money, 2) == 184.35
    # 198.44 / 46 项 is the *untagged-filter-off* figure the dataviewjs's
    # 全部分类 chip shows; the untagged one is smaller because tagged rows drop
    # too. Both are pinned, so neither rule can drift unnoticed.
    assert (snapshot.total, snapshot.total_money) == (46, 198.44)


# --- The three shape cases that break a naive implementation ----------------


def test_shape_case_done_3_of_3_parent_contributes_nothing():
    note = """# 1 冰箱
- [ ] 三分之二 没了 💵 $3.00 ➕ 2026-09-22
\t- [x] 1/3 ➕ 2026-09-22
\t- [x] 2/3 ➕ 2026-09-22
\t- [x] 3/3 ➕ 2026-09-22
"""
    figure = untagged_inventory(parse_pantry(note.encode()))
    # The parent derives to `done` and is not listed at all — the naive
    # "count the [ ] parent" reading charges $3.00 for an empty box.
    assert (figure.count, figure.total) == (0, 0.0)
    assert untagged_inventory(parse_pantry(note.encode())).total != 3.0


def test_shape_case_half_used_parent_contributes_one_unit_price():
    note = """# 1 冰箱
- [ ] 甘栗仁 60g*5 300 克 💵 $6.48 ➕ 2026-09-22 🛫 2026-09-24
\t- [x] 1/2 ➕ 2026-09-22 🛫 2026-09-24 ✅ 2026-09-26
\t- [ ] 2/2 ➕ 2026-09-22
"""
    figure = untagged_inventory(parse_pantry(note.encode()))
    assert (figure.count, figure.total, figure.excluded_parents) == (1, 6.48, 1)
    # Not $12.96 (parent + unit) and not $0.00 (units have no price of their own).
    assert figure.total not in {0.0, 12.96}


def test_shape_case_in_progress_unit_counts_as_open():
    note = """# 1 冰箱
- [ ] 父项 💵 $4.00
\t- [/] 1/2 🛫 2026-09-24
\t- [ ] 2/2
- [/] 单独进行中 💵 $7.00
"""
    figure = untagged_inventory(parse_pantry(note.encode()))
    # The two units at $4.00 each, plus the standalone in-progress item.
    assert (figure.count, figure.total, figure.excluded_parents) == (3, 15.0, 1)


# --- Mutation tests: each rule proven distinguishable ----------------------


def _mutant_figure(snapshot, *, keep_unit_parents, keep_in_progress, keep_tagged):
    """`untagged_inventory` with each of its three rules switchable off.

    The deliberately wrong implementations, written out rather than described,
    so each mutant is a value a test can compare against instead of a comment
    claiming a mutation would be caught.
    """
    count = 0
    total = 0.0
    for item in snapshot.items:
        if item.status == "done" or (item.status == "in_progress" and not keep_in_progress):
            continue
        if item.has_units and not keep_unit_parents:
            continue
        if item.tags and not keep_tagged:
            continue
        count += 1
        total += item.money or 0.0
    return count, round(total, 2)


#: (mutant name, kwargs) for each of the three rules `untagged_inventory` owns.
MONEY_MUTANTS = (
    ("per-unit parent exclusion dropped", {"keep_unit_parents": True}),
    ("[/] treated as not held", {"keep_in_progress": False}),
    ("tagged rows counted", {"keep_tagged": True}),
)
#: The correct switch positions: every rule ON.
_CORRECT = {"keep_unit_parents": False, "keep_in_progress": True, "keep_tagged": False}


@pytest.mark.parametrize(
    ("note", "correct", "mutant_name", "mutant"),
    [
        # Rule 1 — the per-unit parent exclusion. Counting the parent *and* its
        # units charges $8.97 for a $2.99 multipack.
        (
            "# 1 冰箱\n- [ ] 玉子豆腐 💵 $2.99\n\t- [ ] 1/2\n\t- [ ] 2/2\n",
            (2, 5.98),
            "per-unit parent exclusion dropped",
            (3, 8.97),
        ),
        # Rule 2 — [/] is held. Dropping it hides everything in progress, which
        # on the live note is 23 of 73 rows.
        (
            "# 1 冰箱\n- [/] 进行中 💵 $7.00\n- [ ] 开着 💵 $1.00\n",
            (2, 8.0),
            "[/] treated as not held",
            (1, 1.0),
        ),
        # Rule 3 — a tagged row is outside the 无标签 figure.
        (
            "# 1 冰箱\n- [ ] 无标签 💵 $1.00\n- [/] 有标签 #SF 💵 $2.00\n",
            (1, 1.0),
            "tagged rows counted",
            (2, 3.0),
        ),
    ],
)
def test_each_money_rule_is_individually_observable(note, correct, mutant_name, mutant):
    """The correct answer and one plausible-wrong answer, pinned side by side.

    This is what makes the parity assertion mean something: it proves each of
    the three rules moves the figure on its own, so a regression in one cannot
    hide behind the other two, and it pins the mutant's exact numbers so the
    comparison in `test_every_money_mutant_moves_the_frozen_figure` is against a
    known value rather than a vague inequality.
    """
    snapshot = parse_pantry(note.encode())
    assert (untagged_inventory(snapshot).count, untagged_inventory(snapshot).total) == correct
    broken = _mutant_figure(snapshot, **{**_CORRECT, **dict(MONEY_MUTANTS)[mutant_name]})
    assert broken == mutant
    assert broken != correct


def test_every_money_mutant_moves_the_frozen_figure():
    """Each mutant is also killed by the frozen note, not only by a toy note."""
    snapshot = parse_pantry(FIXTURE.read_bytes())
    correct = (untagged_inventory(snapshot).count, untagged_inventory(snapshot).total)
    assert correct == (FROZEN_COUNT, FROZEN_TOTAL)
    for name, kwargs in MONEY_MUTANTS:
        broken = _mutant_figure(snapshot, **{**_CORRECT, **kwargs})
        assert broken != correct, name
    # All three together, for the record: nowhere near the frozen number.
    all_wrong = _mutant_figure(
        snapshot, keep_unit_parents=True, keep_in_progress=False, keep_tagged=True
    )
    assert all_wrong != correct


# --- Bounds ---------------------------------------------------------------


def test_item_count_bounds_hold():
    # _MAX_ITEMS_PER_SECTION: 200 listed rows in one section is the last that
    # parses, 201 is a typed failure rather than a truncated list.
    def note(rows: int) -> bytes:
        body = "".join(f"- [ ] 项目 {i} 💵 $1.00 ➕ 2026-08-06\n" for i in range(rows))
        return f"# 1 冰箱\n{body}".encode()

    assert len(_sections(note(200))[0].items) == 200
    with pytest.raises(PantryError, match="pantry_too_many_items"):
        parse_pantry(note(201))


def test_total_item_bound_holds():
    # _MAX_ITEMS_TOTAL = 1000 across every section, not per section: five
    # sections of the per-section maximum is exactly the total maximum, and one
    # more row anywhere is a typed failure.
    def section(number: int, rows: int) -> str:
        body = "".join(f"- [ ] 项目 {i} 💵 $1.00 ➕ 2026-08-06\n" for i in range(rows))
        return f"# {number} 区\n{body}"

    full = "".join(section(n, 200) for n in range(1, 6))
    assert len(parse_pantry(full.encode()).items) == 1000
    with pytest.raises(PantryError, match="pantry_too_many_items"):
        parse_pantry((full + section(6, 1)).encode())


def test_max_bytes_and_undecodable_bytes_fail_closed():
    with pytest.raises(PantryError, match="pantry_too_large"):
        parse_pantry(NOTE.encode(), max_bytes=100)
    with pytest.raises(PantryError, match="pantry_unparseable"):
        parse_pantry(b"\xff\xfe\x00\x01")


# --- Metadata -------------------------------------------------------------


def test_age_metadata_and_clean_text():
    note = """# 1 冰箱
## 1.1 冷藏
- [ ] 中华 玉子豆腐 245 克 💵 $2.99 ✍️ 2026-08-07 ➕ 2026-08-06
## 1.2 冷冻
- [ ] 柴米 薄百叶 冷冻 227 克 ✍️ 2026-05-18 ➕ 2026-05-10
"""
    sections = _sections(note)
    chilled = next(i for i in sections[0].items if "中华" in i.text)
    frozen = next(i for i in sections[0].items if "柴米" in i.text)
    # Clean display text: no dates, no money (money is a structured field).
    assert chilled.text == "中华 玉子豆腐 245 克"
    assert "2026" not in chilled.text and "💵" not in chilled.text
    assert chilled.money == 2.99
    assert chilled.added_at == "2026-08-06"
    assert chilled.ended_at is None
    assert chilled.frozen is False
    assert frozen.frozen is True
    assert frozen.added_at == "2026-05-10"


def test_added_and_ended_dates_are_recovered():
    """`➕` is the age reference; `[✅❌]` is what recovers a cancellation date."""
    note = """# 1 冰箱
- [ ] 刚买的 💵 $2.99 ✍️ 2026-09-10 ➕ 2026-09-10
- [/] 在吃的 💵 $6.88 ➕ 2026-09-22 🛫 2026-09-23
- [ ] 还开着的父项 💵 $1.00 ➕ 2026-09-01 🛫 2026-09-02 ✅ 2026-09-21
\t- [x] 吃掉的 💵 $0.81 ➕ 2026-09-17 🛫 2026-09-20
\t- [ ] 2/2 ➕ 2026-09-17
"""
    rows = {i.text: i for i in _sections(note)[0].items}
    # ➕ is the age reference: first ➕ date, and 🛫/✍️ never shadow it.
    assert rows["刚买的"].added_at == "2026-09-10"
    assert rows["刚买的"].ended_at is None
    # 🛫 alone is a start marker, not an end marker.
    assert rows["在吃的"].ended_at is None
    assert rows["在吃的"].status == "in_progress"
    # ✅ on a row that survives (a closed parent kept as a context header
    # above an open unit) is still the ended date, even though the derived
    # status says the item is not finished.
    assert rows["还开着的父项"].ended_at == "2026-09-21"
    assert rows["还开着的父项"].status == "in_progress"


def test_cancellation_date_is_recovered_on_a_kept_row():
    # A `[x]` parent above an open unit stays listed as context and carries
    # both dates; a `❌` date is the cancelled variant of the same field.
    done_parent = """# 1 冰箱
- [x] 手作酱香饼 350 克 💵 $5.99 ➕ 2026-08-06 ✅ 2026-08-07
\t- [ ] 1/2 ➕ 2026-08-06
"""
    parent = _sections(done_parent)[0].items[0]
    assert parent.text == "手作酱香饼 350 克"
    assert parent.status == "open"
    assert parent.added_at == "2026-08-06"
    assert parent.ended_at == "2026-08-07"

    cancelled = _sections(
        "# 1 冰箱\n- [x] 结束的 💵 $1.00 ➕ 2026-09-10 🛫 2026-09-13 ❌ 2026-09-22\n"
        "\t- [ ] 1/2 ➕ 2026-09-10\n"
    )[0].items[0]
    # `_ENDED` matches `[✅❌]`, so a ✅-and-❌ line reports the ✅ date first.
    assert cancelled.ended_at == "2026-09-22"
    assert cancelled.added_at == "2026-09-10"


def test_the_live_note_shape_the_stock_join_needs():
    """The line the spec names as a tier-4 miss, cleaned and priced.

    `禾苑 蟹粉鱼肉狮子头 冷冻 280 克` yields exactly that product core: money,
    emoji dates, and tags removed, nothing else touched. Whether tier 4 then
    finds catalog id 108 is the Stock Join's question (#10), not this parser's.
    """
    snapshot = parse_pantry(FIXTURE.read_bytes())
    item = next(i for i in snapshot.items if "蟹粉鱼肉狮子头" in i.text)
    assert item.text == "禾苑 蟹粉鱼肉狮子头 冷冻 280 克"
    assert item.money == 4.59
    assert item.added_at == "2026-07-15"
    assert item.ended_at is None
    assert item.frozen is True
    assert item.subsection == "1.2.1 冷冻肉&海鲜"
    assert item.has_units is False
    assert item.tags == ()


def test_subsections_assigned_from_nearest_heading():
    note = """# 1 冰箱
## 1.1 冷藏
- [ ] 中华 玉子豆腐 245 克 💵 $2.99 ➕ 2026-08-06
### 1.1.1 水果
- [ ] 白桃礼盒 4 磅 💵 $10.88 ➕ 2026-08-06
## 1.2 冷冻
- [ ] 柴米 薄百叶 冷冻 227 克 ➕ 2026-05-10
# 2 干货
- [ ] Manuka Honey MGO 50+ ➕ 2026-05-18
"""
    sections = _sections(note)
    rows = {i.text: i for i in sections[0].items}
    assert rows["中华 玉子豆腐 245 克"].subsection == "1.1 冷藏"
    assert rows["白桃礼盒 4 磅"].subsection == "1.1.1 水果"
    assert rows["柴米 薄百叶 冷冻 227 克"].subsection == "1.2 冷冻"
    assert sections[1].items[0].subsection is None


def test_tags_extracted_and_cleaned():
    sample = """---
modified_at: 2026-08-24
---
# 1 冰箱
## 1.1 冷藏
- [ ] 优质白桃礼盒 4 磅 #SF 💵 $9.88 ✍️ 2026-08-24 ➕ 2026-08-24
- [ ] 新鲜大白菜 1 个 #SF #local 💵 $3.99 ➕ 2026-08-24
\t- [ ] 1/2 #SF ➕ 2026-08-24
"""
    items = parse_pantry(sample.encode()).sections[0].items
    assert len(items) == 3
    assert items[0].text == "优质白桃礼盒 4 磅" and items[0].tags == ("SF",)
    assert items[0].money == 9.88
    assert items[1].text == "新鲜大白菜 1 个" and items[1].tags == ("SF", "local")
    assert items[1].money == 3.99
    assert items[2].text == "1/2" and items[2].tags == ("SF",)


# --- Toggle rendering (pure bytes -> bytes; no file is touched) ----------


def _toggle_note() -> bytes:
    return (
        "# 1 冰箱\n"
        "- [ ] 中华 玉子豆腐 245 克 💵 $2.99 ✍️ 2026-08-07\n"
        "\t- [ ] 1/2 ✍️ 2026-08-07\n"
        "- [/] 白桃礼盒 💵 $10.88 🛫 2026-08-07\n"
        "- [x] 手作酱香饼 💵 $5.99 ✅ 2026-08-07\n"
    ).encode()


def test_toggle_completes_appends_done_marker():
    src = _toggle_note()
    snap = parse_pantry(src)
    item = next(i for i in snap.sections[0].items if "白桃" in i.text)
    rendered, revision = toggle_pantry(
        src, item.line_index, date(2026, 8, 7), "done", base_revision=snap.note_revision
    )
    line = rendered.decode().splitlines()[3]
    assert line.startswith("- [x] 白桃礼盒")
    assert "✅ 2026-08-07" in line
    assert revision == "sha256:" + hashlib.sha256(rendered).hexdigest()


def test_toggle_reopen_strips_done_marker():
    src = _toggle_note()
    snap = parse_pantry(src)
    item = next(i for i in snap.sections[0].items if "白桃" in i.text)
    rendered, _ = toggle_pantry(src, item.line_index, date(2026, 8, 7), "done")
    lines = rendered.decode().splitlines()
    done_index = next(i for i, line in enumerate(lines) if line.startswith("- [x] 白桃礼盒"))
    reopened, _ = toggle_pantry(rendered, done_index, date(2026, 8, 7), "open")
    line = reopened.decode().splitlines()[done_index]
    assert line.startswith("- [ ] 白桃礼盒")
    assert "✅ 2026-08-07" not in line


def test_toggle_in_progress_appends_start_marker_and_keeps_it_on_reopen():
    note = """# 1 冰箱
- [ ] 中华 玉子豆腐 245 克 💵 $2.99 ➕ 2026-08-06
- [/] 白桃礼盒 💵 $10.88 ➕ 2026-08-06 🛫 2026-08-06
""".encode()
    snap = parse_pantry(note)
    tofu = next(i for i in snap.sections[0].items if "中华" in i.text)
    rendered, _ = toggle_pantry(note, tofu.line_index, date(2026, 8, 8), "in_progress")
    line = rendered.decode().splitlines()[1]
    assert line.startswith("- [/] 中华") and "🛫 2026-08-08" in line
    # The 🛫 date is informational, so reopening keeps it.
    peach = next(i for i in snap.sections[0].items if "白桃" in i.text)
    again, _ = toggle_pantry(note, peach.line_index, date(2026, 8, 8), "open")
    assert "🛫 2026-08-06" in again.decode().splitlines()[2]


def test_toggle_unit_subtask():
    src = _toggle_note()
    snap = parse_pantry(src)
    unit = next(i for i in snap.sections[0].items if i.text.startswith("1/2"))
    rendered, _ = toggle_pantry(src, unit.line_index, date(2026, 8, 7), "done")
    line = rendered.decode().splitlines()[2]
    assert line.startswith("\t- [x] 1/2")
    assert "✅ 2026-08-07" in line


def test_toggle_syncs_the_parent_status():
    note = (
        "# 4 零食\n"
        "- [ ] 乐事 牛肉派味 90 克 💵 $1.89 ➕ 2026-08-06\n"
        "\t- [ ] 1/2 ➕ 2026-08-06\n"
        "\t- [ ] 2/2 ➕ 2026-08-06\n"
    ).encode()
    snap = parse_pantry(note)
    unit1 = next(i for i in snap.sections[0].items if i.text == "1/2")
    unit2 = next(i for i in snap.sections[0].items if i.text == "2/2")

    # One unit done -> parent in progress ([/] + 🛫).
    step1, _ = toggle_pantry(note, unit1.line_index, date(2026, 8, 8), "done")
    lines = step1.decode().splitlines()
    assert lines[1].startswith("- [/] 乐事") and "🛫 2026-08-08" in lines[1]
    assert lines[2].startswith("\t- [x] 1/2")

    # Both units done -> parent done ([x] + ✅).
    step2, _ = toggle_pantry(step1, unit2.line_index, date(2026, 8, 8), "done")
    lines2 = step2.decode().splitlines()
    assert lines2[1].startswith("- [x] 乐事") and "✅ 2026-08-08" in lines2[1]

    # Reopening one unit drops the parent back to in progress, and the stale
    # ✅ is stripped rather than left contradicting `[/]`.
    step3, _ = toggle_pantry(step2, unit2.line_index, date(2026, 8, 8), "open")
    assert step3.decode().splitlines()[1].startswith("- [/] 乐事")
    assert "✅ 2026-08-08" not in step3.decode().splitlines()[1]

    # Both units open -> parent back to open.
    step4, _ = toggle_pantry(step3, unit1.line_index, date(2026, 8, 8), "open")
    assert step4.decode().splitlines()[1].startswith("- [ ] 乐事")


def test_parent_with_units_is_not_directly_togglable():
    src = _toggle_note()
    snap = parse_pantry(src)
    parent = next(i for i in snap.sections[0].items if "中华" in i.text)
    assert parent.has_units is True
    for target in ("done", "in_progress", "open", "cancelled"):
        with pytest.raises(PantryError, match="pantry_parent_not_togglable"):
            toggle_pantry(src, parent.line_index, date(2026, 8, 7), target)


def test_toggle_rejects_unknown_target_stale_revision_and_bad_lines():
    src = _toggle_note()
    snap = parse_pantry(src)
    item = next(i for i in snap.sections[0].items if "白桃" in i.text)
    with pytest.raises(PantryError, match="pantry_invalid_target"):
        toggle_pantry(src, item.line_index, date(2026, 8, 7), "archived")
    with pytest.raises(PantryToggleConflict):
        toggle_pantry(
            src,
            item.line_index,
            date(2026, 8, 7),
            "done",
            base_revision="sha256:" + "0" * 64,
        )
    with pytest.raises(PantryError, match="pantry_line_not_found"):
        toggle_pantry(src, 999_999, date(2026, 8, 7), "done")
    # A heading line is not a togglable task.
    with pytest.raises(PantryError, match="pantry_line_not_found"):
        toggle_pantry(src, 0, date(2026, 8, 7), "done")
    with pytest.raises(PantryError, match="pantry_too_large"):
        toggle_pantry(src, 1, date(2026, 8, 7), "done", max_bytes=10)


def test_toggle_patches_exactly_one_line_and_preserves_crlf():
    """A surgical byte patch, or `task-date-recorder` reorders the note.

    The `💵`-before-`➕` slot order is the plugin's, and it is maintained by
    rewriting whole task lines; a whole-file rewrite here would scramble it and
    the next Obsidian save would fight this one forever.
    """
    src = _toggle_note()
    snap = parse_pantry(src)
    item = next(i for i in snap.sections[0].items if "白桃" in i.text)
    rendered, _ = toggle_pantry(src, item.line_index, date(2026, 8, 7), "done")
    before, after = src.splitlines(keepends=True), rendered.splitlines(keepends=True)
    assert len(before) == len(after)
    differing = [i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b]
    assert differing == [item.line_index]
    # Every other byte, including the untouched lines' emoji dates, survives.
    assert before[0] == after[0] and before[1] == after[1] and before[2] == after[2]

    crlf = src.replace(b"\n", b"\r\n")
    crlf_snap = parse_pantry(crlf)
    crlf_item = next(i for i in crlf_snap.sections[0].items if "白桃" in i.text)
    out, _ = toggle_pantry(crlf, crlf_item.line_index, date(2026, 8, 7), "done")
    assert out.count(b"\r\n") == crlf.count(b"\r\n")
    assert b"\n" not in out.replace(b"\r\n", b"")
    assert "✅ 2026-08-07\r\n" in out.decode()


# --- Weekly $ flow events (history, not stock) -----------------------------


def _flow(source: str):
    return parse_pantry_flow_events(source.encode())


def _kinds(events):
    return sorted((e.kind, e.day.isoformat(), e.amount) for e in events)


def test_flow_events_kind_and_first_date_per_marker():
    note = """# 1 冰箱
- [x] 白桃礼盒 💵 $10.88 ➕ 2026-09-21 ✅ 2026-09-22
- [-] 空心菜 💵 $2.99 ➕ 2026-09-21 🛫 2026-09-22 ❌ 2026-09-23
- [x] 双日期 💵 $1.00 ✅ 2026-09-21 ✅ 2026-09-22
"""
    assert _kinds(_flow(note)) == [
        ("cancelled", "2026-09-23", 2.99),
        ("consumed", "2026-09-21", 1.0),
        ("consumed", "2026-09-22", 10.88),
        ("ingested", "2026-09-21", 2.99),
        ("ingested", "2026-09-21", 10.88),
    ]


def test_flow_rows_without_money_are_dropped():
    note = """# 1 冰箱
- [x] Manuka Honey ✍️ 2026-05-18 ➕ 2026-05-18 ✅ 2026-05-20
- [x] 有日期无钱 ✅ 2026-05-20
"""
    assert _flow(note) == ()


def test_flow_unit_rows_inherit_parent_money_and_exempt_parent():
    note = """# 1 冰箱
- [x] 中华 玉子豆腐 245 克 💵 $2.99 ➕ 2026-09-21 ✅ 2026-09-22
\t- [x] 1/2 ✅ 2026-09-22
\t- [x] 2/2 ✅ 2026-09-23
"""
    # The parent is exempted (it has unit-split children): its own ➕/✅ are
    # ignored. Each unit row falls back to the parent's 💵 for its own ✅.
    assert _kinds(_flow(note)) == [
        ("consumed", "2026-09-22", 2.99),
        ("consumed", "2026-09-23", 2.99),
    ]


def test_flow_unit_match_is_unanchored_and_marks_parent():
    note = """# 1 冰箱
- [x] 混合装 💵 $4.00 ✅ 2026-09-22
\t- [x] beef 1/2 lb ✅ 2026-09-23
"""
    # The child's `1/2` inside a longer string still exempts the parent and
    # still earns the parent-money fallback (the note's /\d+\/\d+/ test).
    assert _kinds(_flow(note)) == [("consumed", "2026-09-23", 4.0)]


def test_flow_events_come_from_fenced_and_status_variants():
    note = """```dataviewjs
- [x] ghost 💵 $9.99 ✅ 2026-09-22
```
# 1 冰箱
- [/] 进行中 💵 $3.50 ➕ 2026-09-21
- [>] 推迟的 💵 $1.25 ❌ 2026-09-22
- [ ] open 💵 $0.75 ➕ 2026-09-23
"""
    # Fenced rows are invisible; every real task status contributes events.
    assert _kinds(_flow(note)) == [
        ("cancelled", "2026-09-22", 1.25),
        ("ingested", "2026-09-21", 3.5),
        ("ingested", "2026-09-23", 0.75),
    ]


def test_week_flow_uses_the_sunday_to_saturday_product_week():
    note = """# 1 冰箱
- [x] 本周一 💵 $2.00 ✅ 2026-09-21
- [x] 下周日 💵 $3.00 ✅ 2026-09-27
- [x] 上周日 💵 $5.00 ✅ 2026-09-20
- [x] 下周一 💵 $7.00 ✅ 2026-09-28
- [ ] 新进货 💵 $11.00 ➕ 2026-09-22
- [-] 本周取消 💵 $13.00 ❌ 2026-09-23
"""
    flow = week_flow(_flow(note), date(2026, 9, 23))
    # 2026-W39 declares 2026-09-20 (Sun) .. 2026-09-26 (Sat), so Sunday 09-20 is
    # IN and next Sunday 09-27 is OUT.
    assert flow.consumed == 7.00
    assert flow.ingested == 11.00
    assert flow.cancelled == 13.00
    assert (flow.week_start, flow.week_end) == (date(2026, 9, 20), date(2026, 9, 26))
    assert week_flow((), date(2026, 9, 23)).ingested == 0.0


def test_product_week_window_matches_the_vaults_weekly_note_window():
    """Sunday..Saturday of the ISO week containing the window's Monday."""
    from datetime import timedelta

    for day in (
        date(2026, 8, 9),  # a Sunday: starts a product week
        date(2026, 8, 15),  # a Saturday: ends one
        date(2026, 9, 23),  # mid-week
        date(2026, 12, 31),  # year boundary
        date(2027, 1, 3),
    ):
        week_start, week_end = product_week(day)
        monday = week_start + timedelta(days=1)
        assert (week_start, week_end) == (monday - timedelta(days=1), monday + timedelta(days=5))


def test_flow_oversized_and_undecodable_fail_closed():
    with pytest.raises(PantryError):
        parse_pantry_flow_events(b"\xff\xfe\x00\x01")
    with pytest.raises(PantryError, match="pantry_too_large"):
        parse_pantry_flow_events(
            "- [x] a 💵 $1.00 ✅ 2026-09-22".encode(), max_bytes=5
        )


# --- PantryIndex: the fail-closed TTL cache ------------------------------


RELATIVE = "Logistics/库存/Pantry.md"


@pytest.fixture
def pantry_store(settings: Settings) -> AtomicNoteStore:
    root = settings.app_data_dir / "vault-recovery"
    root.mkdir()
    return AtomicNoteStore(settings.vault_path, root)


def _write_note(settings: Settings, body: str) -> None:
    path = settings.vault_path / RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_index_reads_the_configured_note_and_caches_it(settings, pantry_store, monkeypatch):
    _write_note(settings, "# 1 冰箱\n- [ ] 有货 💵 $2.99 ➕ 2026-09-10\n")
    index = PantryIndex.from_settings(settings, pantry_store)
    try:
        assert index.relative == RELATIVE == settings.pantry_note_relative
        first = index.snapshot()
        assert first.total == 1
        assert index.note_revision == first.note_revision

        # Inside the TTL the cached snapshot is returned even if the note on
        # disk changes: that is the staleness `stock_cache_seconds` bounds.
        _write_note(settings, "# 1 冰箱\n- [ ] 没了\n")
        assert index.snapshot().total == 1

        index.invalidate()
        assert index.snapshot().total == 1
    finally:
        index.close()


def test_index_refreshes_once_the_ttl_expires(settings, pantry_store, monkeypatch):
    _write_note(settings, "# 1 冰箱\n- [ ] 有货 💵 $2.99 ➕ 2026-09-10\n")
    clock = [1000.0]
    monkeypatch.setattr("app.vault.pantry.time.monotonic", lambda: clock[0])
    index = PantryIndex.from_settings(settings, pantry_store)
    try:
        assert index.snapshot().total == 1
        _write_note(settings, "# 1 冰箱\n- [ ] 两行 💵 $2.99 ➕ 2026-09-10\n- [ ] 另一行\n")
        clock[0] += settings.stock_cache_seconds - 0.001
        assert index.snapshot().total == 1
        clock[0] += 0.002
        assert index.snapshot().total == 2
    finally:
        index.close()


def test_index_fails_closed_on_a_missing_note(settings, pantry_store):
    index = PantryIndex.from_settings(settings, pantry_store)
    try:
        with pytest.raises(PantryError, match="pantry_source_missing"):
            index.snapshot()
    finally:
        index.close()


def test_index_fails_closed_on_an_unreadable_note(settings, pantry_store, monkeypatch):
    _write_note(settings, "# 1 冰箱\n- [ ] 有货 💵 $2.99 ➕ 2026-09-10\n")
    index = PantryIndex.from_settings(settings, pantry_store)
    try:
        monkeypatch.setattr(
            pantry_store,
            "read_existing_if_exists",
            lambda *a, **k: (_ for _ in ()).throw(PathSafetyError("unsafe_note")),
        )
        with pytest.raises(PantryError, match="pantry_source_unreadable"):
            index.snapshot()
    finally:
        index.close()


def test_index_raises_rather_than_degrading_to_an_empty_pantry(settings, pantry_store):
    """F1's whole point: an unparseable note must not read as "nothing held".

    The blast radius is every recipe's stock colour at once, which is exactly
    why the spec chose a 503 over a fallback in either direction — assuming in
    stock inflates every score, assuming not-in-stock deflates every score to
    `have-been-buying`. So this raises `PantryError` and never returns an empty
    snapshot; the route in #10 maps this type to the 503.
    """
    (settings.vault_path / RELATIVE).parent.mkdir(parents=True, exist_ok=True)
    (settings.vault_path / RELATIVE).write_bytes(b"\xff\xfe\x00\x01")
    index = PantryIndex.from_settings(settings, pantry_store)
    try:
        with pytest.raises(PantryError, match="pantry_unparseable"):
            index.snapshot()
    finally:
        index.close()


def test_index_raises_on_an_oversized_note(settings, pantry_store):
    """Oversize is a failure, not a truncated pantry.

    The store's read bound fires before the parser's, so the code is
    `pantry_source_unreadable` rather than `pantry_too_large` — the same as the
    sibling, and the same 503 for the caller. `parse_pantry` raises
    `pantry_too_large` on its own, asserted above.
    """
    _write_note(settings, "# 1 冰箱\n" + "- [ ] 一行 💵 $1.00 ➕ 2026-09-10\n" * 400)
    index = PantryIndex.from_settings(settings, pantry_store, max_bytes=200)
    try:
        with pytest.raises(PantryError, match="pantry_source_unreadable"):
            index.snapshot()
        # A failure does not poison the cache into an empty snapshot.
        assert index._cached is None
    finally:
        index.close()

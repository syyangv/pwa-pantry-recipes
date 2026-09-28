"""Read-only **state** index over the Pantry note (F1, the state half).

Ported from `pwa-obsidian-daily/app/vault/pantry.py`. **The port is the port**:
the parser is already hardened against this vault's exact failure modes, and
re-deriving it would discard that hardening. Every regex, every bound, and every
typed failure below is carried over rather than re-derived — see
`docs/spec/2026-09-27-pantry-recipes.md` §3.3 F1 for the element-by-element
contract table this module implements.

**F1's split, and what this module is.** `pantry_items.db` answers *"which
product is this?"* (`pantry_item_id`, brand, name). This module answers *"do I
have it right now?"* — `open` / `in_progress` / `done` / `cancelled`, per Pantry
Unit. The join between the two — a catalog id resolved from a line, the
`unjoined` bucket, `line_overrides.yaml` — is **not here**; this module never
consults the catalog and never infers "I have it" from "I have bought it".

**The money invariant.** `💵 $X.XX` on a **parent** line is the **effective
per-unit** price, not an item total. A multipack is a parent plus `1/N`, `2/N`,
… children; a single-unit item is a bare parent with no children. Consumers
**EXCLUDE the unit parent** and count each **open `k/N` subtask at that price** —
`PantrySection.count` / `total_money` and `untagged_inventory` below both do,
and `has_units` is the flag they do it with. Counting the parent *and* its units
double-counts; counting the parent *instead of* its units halves a half-used
item. `untagged_inventory` is the third of the three parity implementations
(this one, the `existingPantryValue` dataviewjs in `Logistics/库存/Pantry.md`,
and `Helper/scripts/pantry_snapshot.py`); `tests/vault/test_pantry.py` asserts
it against the other two's arithmetic on a frozen copy of the real note.

**Failure is closed, loudly.** An unreadable or unparseable note raises
`PantryError`; it never degrades to "nothing held". The blast radius is every
recipe's stock colour at once (F1, §13.15), and both fallbacks — assume in
stock, assume not in stock — are plausible-looking wrong answers, so the
deliberate answer is a 503. `PantryError` is the type a route maps to that 503.

Security contract mirrors `medications.py`: the read goes through the
descriptor-pinned `AtomicNoteStore`, results are bounded, and the source file's
bytes are never modified. `toggle_pantry` renders a byte-span patch and touches
no file; a caller that wants it on disk must go through
`AtomicNoteStore.transform_existing` (F4), because `task-date-recorder` rewrites
whole task lines and a whole-file rewrite would scramble the `💵`-before-`➕`
slot ordering it maintains.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from ..config import Settings
from .atomic_write import AtomicNoteStore, PathSafetyError
from .sections import Heading, SectionError, parse_sections

_LINE = re.compile(r"[^\r\n]*(?:\r\n|\n|\r|$)")
_FENCE = re.compile(r"^(`{3,})(.*)$")
# Task line: leading indent, `- [x]` marker, then the item text. The status is
# a SINGLE CHARACTER and it is matched, never assumed: `[>]` and `[-]` are real
# markers in this vault and neither is open.
_TASK = re.compile(r"^(?P<indent>[ \t]*)- \[(?P<status>.)\][ \t]*(?P<text>.*)$")
# The note's own dataview money regex (parent money falls back to unit rows).
_MONEY = re.compile(r"💵\s*\$?([\d,]+(?:\.\d+)?)")
# Unit-split child: text like `1/2` or `2/2` (the note's per-unit counting).
_UNIT = re.compile(r"^\d+\/\d+")
_EMBED = re.compile(r"^!\[\[")
_COMMENT = re.compile(r"^\s*<!--")
# Any heading line (all levels) — the nearest heading's title decides whether
# a task sits in a 冷冻 (frozen) section, mirroring pantry_controls.md.
_ANY_HEADING = re.compile(r"^(?P<marker>#{1,6})[ \t]+(?P<body>[^\r\n]+?)[ \t]*$")
# Pantry metadata conventions: added ➕ date (age reference), ended ✅/❌ date,
# and the date tokens stripped from display text (✍️ ➕ 🛫 📅 ✅ ❌ + date).
_ADDED = re.compile(r"➕\s*(\d{4}-\d{2}-\d{2})")
_ENDED = re.compile(r"[✅❌]\s*(\d{4}-\d{2}-\d{2})")
_DATE_TOKEN = re.compile(r"(?:✍️|➕|🛫|📅|✅|❌)\s*\d{4}-\d{2}-\d{2}")
# Pipes are escaped as `\|` in rendered GFM tables, not in the note, so these two
# patterns carry no pipe handling — that is the vault's writer's job, and
# re-adding a `\\|` branch here would make the two drift apart.
_TAG = re.compile(r"(?:^|[ \t])#(?P<tag>[^\s#]+)")
_TAG_TOKEN = re.compile(r"(?:^|[ \t])#[^\s#]+")
# Numbered top-level section: `# 1 冰箱` -> number "1", title "冰箱".
_SECTION_NUMBER = re.compile(r"^\d+$")

_OPEN_STATUSES = {" ", "/"}
_MAX_ITEMS_PER_SECTION = 200
_MAX_ITEMS_TOTAL = 1000

#: One task line as the walk collects it, before status is derived and before
#: money/added/ended are pulled out of the text. `text` stays raw.
_RawRow = tuple[int, int, str, str, str | None, int, bool, str | None]


class PantryError(ValueError):
    """A typed failure with a stable code; a route maps it to 503.

    Raised — never swallowed — for an unreadable source
    (`pantry_source_unreadable`), a missing one (`pantry_source_missing`), a
    note that will not decode or will not parse (`pantry_unparseable`), a note
    over `max_bytes` (`pantry_too_large`), and an over-full note
    (`pantry_too_many_items`). F1 fails closed: the caller must not substitute
    an empty stock set.
    """


@dataclass(frozen=True)
class PantryItem:
    """One row. ``indent`` is 0 for a top-level item, 1+ for a unit-split
    child. ``status`` is ``open``, ``in_progress``, or ``done`` (done rows are
    closed parents kept only as context for open units and never counted).
    ``start``/``end`` are the byte span of the task line in the source note
    (the toggle locator); ``line_index`` is the 0-based line number."""

    text: str
    status: str
    indent: int
    money: float | None
    added_at: str | None
    ended_at: str | None
    frozen: bool
    subsection: str | None
    has_units: bool
    start: int
    end: int
    line_index: int
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class PantrySection:
    """One numbered top-level section with its rows, in note order.

    ``count``/``total_money`` skip split parents (``has_units`` rows): their
    ``💵`` is the per-unit price and their open unit rows are counted instead,
    so money is never counted twice. The rows themselves are still returned so
    the UI can render the parent as a header for its units.
    """

    number: str
    title: str
    heading: str
    items: tuple[PantryItem, ...]

    @property
    def count(self) -> int:
        return sum(1 for item in self.items if item.status != "done" and not item.has_units)

    @property
    def total_money(self) -> float:
        return round(
            sum(
                item.money or 0.0
                for item in self.items
                if item.status != "done" and not item.has_units
            ),
            2,
        )


@dataclass(frozen=True)
class PantrySnapshot:
    source: bytes
    note_revision: str
    sections: tuple[PantrySection, ...]

    @property
    def items(self) -> tuple[PantryItem, ...]:
        """Every listed row in note order, sections flattened."""
        return tuple(item for section in self.sections for item in section.items)

    @property
    def total(self) -> int:
        return sum(section.count for section in self.sections)

    @property
    def total_money(self) -> float:
        return round(sum(section.total_money for section in self.sections), 2)


@dataclass(frozen=True)
class InventoryFigure:
    """The 现有库存 figure: what is on hand, and what it is worth.

    This is the whole-note (not per-section) roll-up of the same arithmetic
    `PantrySection.count` / `total_money` perform, restricted to **untagged**
    rows so it lines up with the vault's 无标签 chip and with
    `Helper/scripts/pantry_snapshot.py`'s weekly snapshot. ``excluded_parents``
    is the per-unit-excluded parent count — the number that proves the
    exclusion actually happened rather than the money happening to balance.
    """

    count: int
    total: float
    excluded_parents: int


def untagged_inventory(snapshot: PantrySnapshot) -> InventoryFigure:
    """Open, untagged, non-unit-parent rows: the parity figure.

    Three rules, each of which a false-passing test would hide, so
    `tests/vault/test_pantry.py` pins each one's correct answer *and* the
    plausible-wrong answer, and separately checks that mutating any one of them
    in this function moves the frozen note's figure:

    1. a **unit parent is excluded** — `has_units` rows are a per-unit *price
       header*, not stock, and their open ``k/N`` children are counted instead;
    2. **``done`` is dropped and ``in_progress`` is kept** — a ``[/]`` row is
       held, an ``[x]`` row is not. (Unrecognized markers never reach a
       `PantryItem` at all, so ``[-]`` and ``[>]`` are already gone by here.)
    3. a **tagged row is dropped** — the figure is the 无标签 total, matching
       `pantry_snapshot.py` and the dataviewjs's untagged chip.

    Money comes from `PantryItem.money`, which for a ``k/N`` child is already
    its nearest ancestor's per-unit price. `None` contributes nothing, which is
    how a priceless open row is counted but not summed.
    """
    count = 0
    excluded_parents = 0
    total = 0.0
    for item in snapshot.items:
        if item.status == "done":
            continue
        if item.has_units:
            excluded_parents += 1
            continue
        if item.tags:
            continue
        count += 1
        total += item.money or 0.0
    return InventoryFigure(count=count, total=round(total, 2), excluded_parents=excluded_parents)


def product_week(day: date) -> tuple[date, date]:
    """Sunday→Saturday window of the product week containing ``day``.

    Ported verbatim from `pwa-obsidian-daily`'s `todo_dashboard.product_week`.
    The sibling module does not exist in this app, so the function moves here
    unchanged rather than being re-derived: this is exactly the window the
    vault's 周计划 weekly notes declare (开始日期（周日）/ 结束日期（周六）).
    """
    sunday = day - timedelta(days=(day.weekday() + 1) % 7)
    return sunday, sunday + timedelta(days=6)


def derived_status(
    raw_status: Callable[[int], str],
    children: Mapping[int, Sequence[int]],
    index: int,
) -> str:
    """A row's status, derived from its children when it has any.

    **This is the piece the sibling goes beyond the vault on, and it is
    load-bearing.** A childless row reports its own `raw_status`. A parent is
    ``done`` iff **all** children are ``done``; ``in_progress`` if **any**
    child is ``done`` or ``in_progress``; otherwise ``open``. A multipack is
    finished exactly when all of its units are, and a parent is never
    independently togglable — so the vault can leave a ``[ ]`` parent line above
    three finished ``k/N`` children and the correct answer is still "done".

    It recurses, so a grandchild's status reaches the grandparent: one done
    grandchild makes its child `done` and its grandparent `done`; a done
    grandchild alongside an open sibling makes both `in_progress`. `raw_status`
    is a callback rather than a lookup table so this stays a pure function of
    the tree, which is what lets `tests/vault/test_pantry.py` exercise all three
    branches — and the recursion — directly instead of only through a note.
    """
    kids = list(children.get(index, ()))
    if not kids:
        return raw_status(index)
    kid_statuses = [derived_status(raw_status, children, kid) for kid in kids]
    if all(value == "done" for value in kid_statuses):
        return "done"
    if any(value in {"done", "in_progress"} for value in kid_statuses):
        return "in_progress"
    return "open"


def parse_pantry(source: bytes, *, max_bytes: int = 2_000_000) -> PantrySnapshot:
    """Parse one Pantry note into numbered sections + rows.

    Reuses :func:`app.vault.sections.parse_sections` for the heading stream
    (numbered prefixes, fence/newline/frontmatter handling are already
    correct), then walks the body lines with a local fence state machine.
    Fails closed on malformed input and bounded item counts.
    """
    if len(source) > max_bytes:
        raise PantryError("pantry_too_large")
    try:
        document = parse_sections(source, max_bytes=max_bytes)
    except SectionError as exc:
        raise PantryError("pantry_unparseable") from exc

    body_char = len(document.source[: document.body_start].decode("utf-8", errors="replace"))
    text = document.source.decode("utf-8", errors="replace")
    lines: list[tuple[int, str]] = []
    byte_cursor = document.body_start
    for match in _LINE.finditer(text, body_char):
        line = match.group(0)
        lines.append((byte_cursor, line))
        byte_cursor += len(line.encode("utf-8"))

    # Section headings (level-1, numeric prefix) keyed by their line text, in
    # note order.
    section_headings: dict[str, Heading] = {}
    section_order: list[str] = []
    for heading in document.headings:
        if heading.level != 1 or heading.numbered is None:
            continue
        if _SECTION_NUMBER.fullmatch(heading.numbered) is None:
            continue
        key = heading.line.rstrip("\r\n")
        if key not in section_headings:
            section_headings[key] = heading
            section_order.append(key)

    raw = _collect_rows(lines, section_headings)
    _parent_of, children = _build_tree(raw, _raw_indent)
    raw_statuses = [_raw_status_of(row) for row in raw]

    # Rows: open/in-progress items are listed; done items are dropped (a done
    # parent with open units never occurs — its status derives to in progress).
    items_by_section: dict[str, list[PantryItem]] = {}
    for index, (
        byte_start,
        indent,
        status_char,
        text,
        section,
        line_index,
        frozen,
        subsection,
    ) in enumerate(raw):
        if status_char not in _OPEN_STATUSES and status_char not in {"x", "X"}:
            # Unrecognized marker ([-], [>], …): never listed, but it remains
            # in ``raw`` so deeper open units still get ancestor money.
            continue
        status = derived_status(raw_statuses.__getitem__, children, index)
        if status == "done":
            continue
        line_bytes = lines[line_index][1].encode("utf-8")
        item = PantryItem(
            text=_clean_text(text),
            status=status,
            indent=indent,
            money=_money_of(raw, index, indent),
            added_at=_date_of(text, _ADDED),
            ended_at=_date_of(text, _ENDED),
            frozen=frozen,
            subsection=subsection,
            has_units=bool(children.get(index)),
            start=byte_start,
            end=byte_start + len(line_bytes),
            line_index=line_index,
            tags=_tags_of(text),
        )
        if section is None:
            continue
        bucket = items_by_section.setdefault(section, [])
        if len(bucket) >= _MAX_ITEMS_PER_SECTION:
            raise PantryError("pantry_too_many_items")
        bucket.append(item)

    built: list[PantrySection] = []
    running = 0
    for key in section_order:
        heading = section_headings[key]
        number = heading.numbered or ""
        items = tuple(items_by_section.get(number, ()))
        running += len(items)
        if running > _MAX_ITEMS_TOTAL:
            raise PantryError("pantry_too_many_items")
        built.append(PantrySection(number=number, title=heading.title, heading=key, items=items))
    return PantrySnapshot(
        source=source,
        note_revision="sha256:" + hashlib.sha256(source).hexdigest(),
        sections=tuple(built),
    )


def _collect_rows(
    lines: list[tuple[int, str]], section_headings: Mapping[str, Heading]
) -> list[_RawRow]:
    """The raw task stream, in note order, with each row's section context."""
    rows: list[_RawRow] = []
    in_fence = False
    current_section: str | None = None
    current_heading_title: str | None = None
    current_subsection: str | None = None
    for line_index, (byte_start, line) in enumerate(lines):
        stripped = line.strip()
        if _FENCE.match(stripped):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        key = line.rstrip("\r\n")
        if key in section_headings:
            current_section = section_headings[key].numbered
            current_heading_title = section_headings[key].title
            current_subsection = None
            continue
        heading_match = _ANY_HEADING.match(line)
        if heading_match is not None:
            current_heading_title = heading_match.group("body")
            # H2/H3+ headings open a subsection; an H1 always clears it.
            current_subsection = (
                None if len(heading_match.group("marker")) == 1 else current_heading_title
            )
            continue
        if _EMBED.match(stripped) or _COMMENT.match(stripped):
            continue
        task = _TASK.match(line)
        if task is None:
            continue
        rows.append(
            (
                byte_start,
                _indent_width(task.group("indent")),
                task.group("status"),
                task.group("text").strip(),
                current_section,
                line_index,
                "冷冻" in (current_heading_title or ""),
                current_subsection,
            )
        )
    return rows


def _build_tree[T](
    rows: Sequence[T], indent_of: Callable[[T], int]
) -> tuple[dict[int, int], dict[int, list[int]]]:
    """`parent_of` / `children` over row indices, for any row shape.

    A row's parent is the nearest *preceding* row with a strictly smaller
    indent — the same definition Obsidian's `t.parent` uses, and the same one
    `Helper/scripts/pantry_snapshot.py` reconstructs, which is what lets the
    three money implementations agree on a note whose children use a mix of
    two-space and tab indentation.

    One implementation, two row shapes: the parse stream puts indent at index 1
    and the toggle stream at index 2, so the caller supplies the accessor. The
    *rule* is the part that must not drift.
    """
    parent_of: dict[int, int] = {}
    for index, entry in enumerate(rows):
        indent = indent_of(entry)
        for earlier in range(index - 1, -1, -1):
            if indent_of(rows[earlier]) < indent:
                parent_of[index] = earlier
                break
    children: dict[int, list[int]] = {}
    for index, parent in parent_of.items():
        children.setdefault(parent, []).append(index)
    return parent_of, children


def _raw_indent(row: _RawRow) -> int:
    """The parse stream's row layout: (byte_start, indent, status, text, …)."""
    return row[1]


def _task_indent(task: tuple[int, int, int, str]) -> int:
    """The toggle stream's row layout: (line_index, byte_start, indent, status)."""
    return task[2]


def _raw_status_of(row: _RawRow) -> str:
    """One row's own marker, before children are consulted."""
    status_char = row[2]
    if status_char in _OPEN_STATUSES:
        return "open" if status_char == " " else "in_progress"
    if status_char in {"x", "X"}:
        return "done"
    return "open"


def _clean_text(raw: str) -> str:
    """Display text without the money, tags, and emoji-date metadata (💵 $X,
    #tag, ✍️/➕/🛫/📅/✅/❌ YYYY-MM-DD) — the PWA surfaces those as structured
    fields (money span, tag badges, age badge)."""
    cleaned = _MONEY.sub("", raw)
    cleaned = _DATE_TOKEN.sub("", cleaned)
    cleaned = _TAG_TOKEN.sub("", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _tags_of(raw: str) -> tuple[str, ...]:
    return tuple(_TAG.findall(raw))


def _date_of(raw: str, pattern: re.Pattern[str]) -> str | None:
    match = pattern.search(raw)
    return match.group(1) if match is not None else None


def _indent_width(indent: str) -> int:
    width = 0
    for character in indent:
        width += 4 if character == "\t" else 1
    return 1 if width > 0 else 0


def _money_of(raw: Sequence[_RawRow], index: int, indent: int) -> float | None:
    """Row money: own ``💵`` value, else the nearest open ancestor's money.

    **The per-unit invariant, implemented.** A ``k/N`` child carries no ``💵``
    of its own — the parent's value *is* the per-unit price — so the child
    inherits it and the parent is flagged `has_units` for exclusion. The walk
    stops at the first lower-indent row even when that row has no money, because
    a grandparent's price is not this unit's price.
    """
    text = raw[index][3]
    match = _MONEY.search(text)
    if match is not None:
        try:
            return round(float(match.group(1).replace(",", "")), 2)
        except ValueError:
            return None
    if _UNIT.match(text):
        for earlier in range(index - 1, -1, -1):
            if raw[earlier][1] < indent:
                ancestor_money = _money_of(raw, earlier, raw[earlier][1])
                if ancestor_money is not None:
                    return ancestor_money
                break
    return None


# --- Weekly $ flow events (mirrors the note's `$ flow` dataviewjs) ---------
# Each event kind is the first `emoji + YYYY-MM-DD` on a row: ➕ ingested,
# ✅ consumed, ❌ cancelled. The unit test is the note's unanchored /\d+\/\d+/.
_FLOW_UNIT_ANY = re.compile(r"\d+/\d+")
_FLOW_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ingested", re.compile(r"➕\s*(\d{4}-\d{2}-\d{2})")),
    ("consumed", re.compile(r"✅\s*(\d{4}-\d{2}-\d{2})")),
    ("cancelled", re.compile(r"❌\s*(\d{4}-\d{2}-\d{2})")),
)
_MAX_FLOW_EVENTS = 5000


@dataclass(frozen=True)
class PantryFlowEvent:
    """One dated money event (``➕`` ingested / ``✅`` consumed / ``❌`` cancelled)."""

    day: date
    kind: str
    amount: float


@dataclass(frozen=True)
class PantryWeekFlow:
    """Money totals for the product week containing ``today`` (Sunday→
    Saturday — the window the vault's 周计划 weekly notes declare)."""

    ingested: float
    consumed: float
    cancelled: float
    week_start: date
    week_end: date


def parse_pantry_flow_events(
    source: bytes, *, max_bytes: int = 2_000_000
) -> tuple[PantryFlowEvent, ...]:
    """Money events from one pantry-style task note.

    Mirrors the vault note's ``$ flow`` dataviewjs exactly: a row whose text
    matches ``\\d+/\\d+`` permanently exempts its direct parent from emitting
    events; such unit rows fall back to the direct parent's ``💵`` for their
    own events (one level, not an ancestor walk); rows without any amount are
    dropped; each of ➕/✅/❌ contributes at most one event (its first date).

    Note this is a *history* projection, not the stock figure: it counts
    **closed** rows (a ✅ consumption is the event), so it deliberately does not
    apply the open-only rules `untagged_inventory` uses.
    """
    if len(source) > max_bytes:
        raise PantryError("pantry_too_large")
    try:
        document = parse_sections(source, max_bytes=max_bytes)
    except SectionError as exc:
        raise PantryError("pantry_unparseable") from exc
    text = document.source.decode("utf-8", errors="replace")
    body_char = len(document.source[: document.body_start].decode("utf-8", errors="replace"))
    rows: list[tuple[int, str]] = []
    in_fence = False
    for match in _LINE.finditer(text, body_char):
        line = match.group(0)
        stripped = line.strip()
        if _FENCE.match(stripped):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        task = _TASK.match(line)
        if task is None:
            continue
        rows.append((_indent_width(task.group("indent")), task.group("text").strip()))

    parent_of: dict[int, int] = {}
    for index, (indent, _row_text) in enumerate(rows):
        for earlier in range(index - 1, -1, -1):
            if rows[earlier][0] < indent:
                parent_of[index] = earlier
                break
    exempted: set[int] = set()
    for index, (_indent, row_text) in enumerate(rows):
        parent = parent_of.get(index)
        if parent is not None and _FLOW_UNIT_ANY.search(row_text):
            exempted.add(parent)

    events: list[PantryFlowEvent] = []
    for index, (_indent, row_text) in enumerate(rows):
        if index in exempted:
            continue
        amount = _flow_money(row_text)
        parent = parent_of.get(index)
        if not amount and parent is not None and _FLOW_UNIT_ANY.search(row_text):
            amount = _flow_money(rows[parent][1])
        if not amount:
            continue
        for kind, pattern in _FLOW_KINDS:
            found = pattern.search(row_text)
            if found is None:
                continue
            try:
                day = date.fromisoformat(found.group(1))
            except ValueError:
                continue
            events.append(PantryFlowEvent(day=day, kind=kind, amount=amount))
            if len(events) > _MAX_FLOW_EVENTS:
                raise PantryError("pantry_too_many_items")
    return tuple(events)


def _flow_money(text: str) -> float:
    match = _MONEY.search(text)
    if match is None:
        return 0.0
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return 0.0


def week_flow(events: tuple[PantryFlowEvent, ...], today: date) -> PantryWeekFlow:
    """Sum events inside ``today``'s product week (Sunday→Saturday — the
    window the vault's 周计划 weekly note declares, see :func:`product_week`)."""
    totals = {"ingested": 0.0, "consumed": 0.0, "cancelled": 0.0}
    week_start, week_end = product_week(today)
    for event in events:
        if week_start <= event.day <= week_end:
            totals[event.kind] += event.amount
    return PantryWeekFlow(
        ingested=round(totals["ingested"], 2),
        consumed=round(totals["consumed"], 2),
        cancelled=round(totals["cancelled"], 2),
        week_start=week_start,
        week_end=week_end,
    )


# UTF-8 bytes of the ✅ done marker (U+2705) and 🛫 start marker (U+1F6EB).
_DONE = b"\xe2\x9c\x85"
_START = b"\xf0\x9f\x9b\xab"
_DONE_MARKER = re.compile(rb"\s*" + _DONE + rb"\s*\d{4}-\d{2}-\d{2}\s*$")


class PantryToggleConflict(ValueError):
    """The note changed since the client's snapshot; the toggle must be retried."""


def toggle_pantry(
    source: bytes,
    line_index: int,
    today: date,
    target: str,
    *,
    base_revision: str | None = None,
    max_bytes: int = 2_000_000,
) -> tuple[bytes, str]:
    """Render the task line at ``line_index`` (0-based) to ``target``.

    **A pure `bytes -> bytes` renderer: it touches no file.** The caller is the
    one that writes, and under F4 that caller must be
    `AtomicNoteStore.transform_existing`, which supplies the CAS-on-revision,
    the backup, the `os.replace`, and the post-write byte verification. This
    ticket ships no write path at all, so nothing here can write to the vault.

    The patch is **surgical by construction**: only the status character and,
    where the transition calls for it, a trailing emoji-date token are replaced,
    inside the one line's byte span. Nothing is re-serialized, reordered, or
    reformatted, because `task-date-recorder` owns the recognised emoji-date
    slots and a whole-file rewrite would scramble the `💵`-before-`➕` ordering it
    maintains. It also preserves the line's own newline bytes, so a CRLF note
    stays CRLF.

    A line index is stable across pantry writes: toggles change line *content*
    (status char + date markers) but never add or remove lines, so consecutive
    unit toggles keep working without a refetch. The `base_revision` check still
    rejects any structural change (adds/removes) from Obsidian.

    ``target`` is one of ``open`` / ``in_progress`` / ``done`` / ``cancelled``:
    - ``done``: ``[ ]``/``[/]`` -> ``[x]`` and append `` ✅ YYYY-MM-DD``;
    - ``in_progress``: ``[ ]``/``[x]`` -> ``[/]`` and append `` 🛫 YYYY-MM-DD``
      (the note's start marker) unless a ``🛫`` date is present;
    - ``open``: ``[x]``/``[/]`` -> ``[ ]`` and strip a trailing ``✅ YYYY-MM-DD``
      (the ``🛫`` start date is kept — informational, not completion).
    - ``cancelled``: ``[ ]``/``[/]``/``[x]`` -> ``[-]`` and append
      ``❌ YYYY-MM-DD``. Cancelled rows are terminal and omitted from the
      open-items projection.

    A **parent with sub-items is not directly togglable** (its status derives
    from its units); toggling a unit also re-syncs its parent so the note
    stays consistent (parent ``[x]`` when all units are done, ``[/]`` when any
    is done/in-progress, ``[ ]`` when all are open). When ``base_revision`` is
    given the source must hash to it or a :class:`PantryToggleConflict` is
    raised (optimistic concurrency). Returns ``(new_source, new_note_revision)``.
    """
    if target not in {"open", "in_progress", "done", "cancelled"}:
        raise PantryError("pantry_invalid_target")
    if len(source) > max_bytes:
        raise PantryError("pantry_too_large")
    if (
        base_revision is not None
        and "sha256:" + hashlib.sha256(source).hexdigest() != base_revision
    ):
        raise PantryToggleConflict("pantry_note_changed")
    try:
        document = parse_sections(source, max_bytes=max_bytes)
    except SectionError as exc:
        raise PantryError("pantry_unparseable") from exc

    body_char = len(document.source[: document.body_start].decode("utf-8", errors="replace"))
    text = document.source.decode("utf-8", errors="replace")
    lines: list[tuple[int, str]] = []
    byte_cursor = document.body_start
    for match in _LINE.finditer(text, body_char):
        line = match.group(0)
        lines.append((byte_cursor, line))
        byte_cursor += len(line.encode("utf-8"))

    # Task lines: (note_line_index, byte_start, indent, status_char).
    tasks: list[tuple[int, int, int, str]] = []
    for note_line_index, (byte_start, line) in enumerate(lines):
        task = _TASK.match(line)
        if task is None:
            continue
        tasks.append(
            (
                note_line_index,
                byte_start,
                _indent_width(task.group("indent")),
                task.group("status"),
            )
        )

    target_index = None
    for index, (task_line_index, _byte_start, _indent, _status) in enumerate(tasks):
        if task_line_index == line_index:
            target_index = index
            break
    if target_index is None:
        raise PantryError("pantry_line_not_found")

    parent_of, children = _build_tree(tasks, _task_indent)
    if children.get(target_index):
        raise PantryError("pantry_parent_not_togglable")

    rendered, _revision = _apply_status_to_task(source, tasks, target_index, today, target, lines)
    parent = parent_of.get(target_index)
    if parent is not None:
        derived = _derive_from_children(tasks, children, parent, target_index, target)
        rendered, _revision = _apply_status_to_task(rendered, tasks, parent, today, derived, lines)
    return rendered, "sha256:" + hashlib.sha256(rendered).hexdigest()


# The toggle tree carries the same shape as the parse tree minus the text, so
# `_build_tree` serves both; only the row type differs.
def _task_raw_status(tasks: Sequence[tuple[int, int, int, str]], index: int) -> str:
    status_char = tasks[index][3]
    if status_char in _OPEN_STATUSES:
        return "open" if status_char == " " else "in_progress"
    if status_char in {"x", "X"}:
        return "done"
    return "open"


def _derive_from_children(
    tasks: Sequence[tuple[int, int, int, str]],
    children: Mapping[int, Sequence[int]],
    parent: int,
    changed: int | None = None,
    changed_status: str | None = None,
) -> str:
    """Derived parent status after a (possibly changed) child.

    Asymmetric with :func:`derived_status` on purpose: a `cancelled` child is
    terminal, so a parent whose units are all done-or-cancelled is `done` and a
    parent with any cancelled unit is `in_progress`. `parse_pantry` cannot see a
    cancelled unit — it drops unrecognized markers before deriving — so this is
    the write side's own rule, not a second derivation of the read side's.
    """
    statuses: list[str] = []
    for kid in children.get(parent, ()):
        if kid == changed and changed_status is not None:
            statuses.append(changed_status)
        else:
            statuses.append(_task_raw_status(tasks, kid))
    if not statuses:
        return _task_raw_status(tasks, parent)
    if all(value in {"done", "cancelled"} for value in statuses):
        return "done"
    if any(value in {"done", "cancelled", "in_progress"} for value in statuses):
        return "in_progress"
    return "open"


def _apply_status_to_task(
    source: bytes,
    tasks: Sequence[tuple[int, int, int, str]],
    task_index: int,
    today: date,
    target: str,
    lines: Sequence[tuple[int, str]],
) -> tuple[bytes, str]:
    byte_start = tasks[task_index][1]
    line_index = tasks[task_index][0]
    line_bytes = lines[line_index][1].encode("utf-8")
    return _set_status(source, byte_start, byte_start + len(line_bytes), line_bytes, today, target)


def _set_status(
    source: bytes, start: int, end: int, line_bytes: bytes, today: date, target: str
) -> tuple[bytes, str]:
    """The whole-line byte patch: status char, then a trailing date token."""
    marker = line_bytes.find(b"- [")
    if marker < 0 or marker + 4 >= len(line_bytes):
        raise PantryError("pantry_line_not_togglable")
    status_pos = marker + 3
    status = chr(line_bytes[status_pos])
    if status not in _OPEN_STATUSES and status not in {"x", "X"}:
        raise PantryError("pantry_line_not_togglable")
    if line_bytes.endswith(b"\r\n"):
        newline = b"\r\n"
    elif line_bytes.endswith(b"\r"):
        newline = b"\r"
    elif line_bytes.endswith(b"\n"):
        newline = b"\n"
    else:
        newline = b""
    content = line_bytes[: len(line_bytes) - len(newline)] if newline else line_bytes

    if target == "done":
        flipped = content[:status_pos] + b"x" + content[status_pos + 1 :]
        if _DONE not in flipped:
            flipped = flipped + b" " + _DONE + b" " + today.isoformat().encode()
    elif target == "cancelled":
        flipped = content[:status_pos] + b"-" + content[status_pos + 1 :]
        flipped = _DONE_MARKER.sub(b"", flipped)
        if "❌".encode() not in flipped:
            flipped = flipped + " ❌ ".encode() + today.isoformat().encode()
    elif target == "in_progress":
        flipped = content[:status_pos] + b"/" + content[status_pos + 1 :]
        flipped = _DONE_MARKER.sub(b"", flipped)  # not done: drop a stale ✅
        if _START not in flipped:
            flipped = flipped + b" " + _START + b" " + today.isoformat().encode()
    else:  # open
        flipped = content[:status_pos] + b" " + content[status_pos + 1 :]
        flipped = _DONE_MARKER.sub(b"", flipped)
    new_line = flipped + newline
    new_source = source[:start] + new_line + source[end:]
    return new_source, "sha256:" + hashlib.sha256(new_source).hexdigest()


class PantryIndex:
    """TTL-cached, read-only snapshot provider for the Pantry note.

    #10 inherits this as `PantryStockIndex` with `stock_cache_seconds` as its
    TTL. It is a **single-source** provider on purpose: the only file it reads
    is `Settings.pantry_note_relative`. The sibling also fanned out over
    `Archive/pantry*.md` to build the `$ flow` chart's history; that is a second
    source, it is best-effort-by-design, and nothing in this app consumes it —
    so it is not ported here, and `parse_pantry_flow_events` / `week_flow`
    remain available as pure functions for a caller that wants them.
    """

    def __init__(
        self,
        store: AtomicNoteStore,
        relative: str,
        cache_seconds: float,
        *,
        max_bytes: int = 2_000_000,
    ) -> None:
        self._store = store
        self._relative = relative
        self.cache_seconds = cache_seconds
        self._max_bytes = max_bytes
        self._cached: PantrySnapshot | None = None
        self._expires = 0.0

    @classmethod
    def from_settings(
        cls, settings: Settings, store: AtomicNoteStore, *, max_bytes: int = 2_000_000
    ) -> PantryIndex:
        """Build from the fail-closed `Settings` surface.

        `os.environ` is never read here: the note path and the TTL are both
        server-owned, and an index assembled from the environment directly would
        be a second, unvalidated copy of `Settings`. The TTL is
        `stock_cache_seconds` because that is the maximum staleness a stock
        answer may have — the user toggles `Pantry.md` from Obsidian while this
        PWA is open.
        """
        return cls(
            store,
            settings.pantry_note_relative,
            settings.stock_cache_seconds,
            max_bytes=max_bytes,
        )

    @property
    def relative(self) -> str:
        return self._relative

    @property
    def note_revision(self) -> str:
        """The cached snapshot's revision — `stockRevision` for #10."""
        return self.snapshot().note_revision

    def snapshot(self) -> PantrySnapshot:
        self._refresh()
        assert self._cached is not None
        return self._cached

    def _refresh(self) -> None:
        now = time.monotonic()
        if self._cached is not None and now < self._expires:
            return
        try:
            source = self._store.read_existing_if_exists(self._relative, max_bytes=self._max_bytes)
        except PathSafetyError as exc:
            # The store's read bound fires before the parser's, so an oversized
            # note arrives here as `pantry_source_unreadable` rather than
            # `pantry_too_large`. Same as the sibling, and the same answer for
            # the caller: a 503, not an empty pantry. `parse_pantry` raises
            # `pantry_too_large` on its own when called directly.
            raise PantryError("pantry_source_unreadable") from exc
        if source is None:
            raise PantryError("pantry_source_missing")
        # parse_pantry raises PantryError itself; it is deliberately NOT caught
        # here. An unparseable note must surface, not become an empty pantry.
        snapshot = parse_pantry(source, max_bytes=self._max_bytes)
        self._cached = snapshot
        self._expires = now + self.cache_seconds

    def invalidate(self) -> None:
        """Drop the cached snapshot so the next read reflects the vault."""
        self._cached = None
        self._expires = 0.0

    def close(self) -> None:
        """No persistent descriptors are held; nothing to release."""

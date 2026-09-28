"""Section-region document engine over a note body — a locator, never a writer.

Parses H1/H2 headed regions, ``columns`` fences (3- and 4-backtick, matching the
fence state machine the vault's own scripts use), ``===`` column separators
inside a columns fence, and legacy numbered headings (``## 3.1 课程``). Detects
**all** candidate matches and fails closed on more than one: the vault's own
`sectionUpsert` is first-match-wins substring matching
(`lines[i].includes(keyword)`), and replicating that would make the Cooking Log
write its `- [[Recipe]]` line into whichever region happened to come first.

Computes byte spans for a surgical insert, mirroring `frontmatter.py`'s span
discipline. **A region never spans a heading or a fence boundary**: a region
ends at the first following heading line, fence line, ``===`` separator (inside
a columns fence), or EOF. That is why the live `笔记` region in
`日记/2026/2026-09-27.md` is the heading line alone — the very next line opens a
four-backtick `columns` fence, so the region is *already* over by the time the
fence starts, and the insertion point is `heading.end`.

**This module emits no heading.** A note that genuinely lacks a `笔记` region is
a `require_unique` failure at the call site, never a silent insertion here.

Ported from `pwa-obsidian-daily/app/vault/sections.py`, with the region
insertion primitive added: `insert_after_heading`.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# Line splitter handles LF, CRLF, and lone-CR notes (a lone-CR daily note
# must parse the same headings as its LF equivalent — the vault tolerates
# all three newline styles).
_LINE = re.compile(r"[^\r\n]*(?:\r\n|\n|\r|$)")
# Applied to a stripped line: marker = backtick run, language = rest.
_FENCE = re.compile(r"^(`{3,})(.*)$")
_HEADING_LINE = re.compile(r"^(?P<marker>#{1,6})[ \t]+(?P<body>[^\r\n]+?)[ \t]*$")
# Legacy numbered prefix: `3.1 课程` -> number "3.1", title "课程";
# `3 Event` -> number "3", title "Event".
_NUMBERED = re.compile(r"^(?P<num>\d+(?:\.\d+)*[.)]?)[ \t]+(?P<title>.+)$")
# The Event anchor contract, byte-for-byte the eventNotes heading regex
# (`_HEADING` in event_notes.py): `# Event`, `# 3 Event`, `# 3. Event`.
_EVENT_ANCHOR = re.compile(
    r"(?im)^#{1,6}[ \t]+(?:\d+(?:[.)]|[ \t]+)[ \t]*)?event[ \t\r]*$"
)
_SEPARATOR = re.compile(r"^[ \t]*===[ \t\r]*$")
_DELIMITER = re.compile(r"^(?:\ufeff)?---[ \t]*(?:\r\n|\n|\r)")
_CLOSING_DELIMITER = re.compile(r"(?:(?<=\n)|(?<=\r)|\A)---[ \t]*(?=\r\n|\n|\r|$)")
_ACTIVITY_TITLES: frozenset[str] = frozenset(
    {"Therapy", "游泳课", "Physical Therapy", "Pilates"}
)
_DAILY_STRUCTURAL_TITLES: frozenset[str] = frozenset(
    {"日总结", "笔记", "Daily"}
)


class SectionError(ValueError):
    """Base class for stable section-engine contract failures."""


class AmbiguousSection(SectionError):
    """The note cannot be changed without guessing which region is authoritative."""


@dataclass(frozen=True)
class Heading:
    """One heading line with byte offsets into the source document.

    `newline` is the heading line's **own** terminator, read from the source
    rather than assumed. A daily note that is CRLF must be spliced with CRLF;
    injecting a single LF is a mixed-terminator file, which `git` renders as a
    whole-file diff and which `task-date-recorder` then rewrites on its
    debounced `modify` event, so the noise is permanent rather than transient.
    """

    level: int
    title: str
    numbered: str | None
    raw: str
    start: int
    end: int
    line: str
    newline: bytes

    @property
    def text(self) -> str:
        """The full heading line without the ``#`` marker and trailing spaces."""
        return self.raw


@dataclass(frozen=True)
class Region:
    """A headed region: the heading line plus everything up to its boundary.

    ``end`` is the exclusive byte offset of the first line that bounds the
    region (a heading line, a fence line, a ``===`` separator inside a columns
    fence, or EOF). A region therefore never spans a heading or fence boundary,
    and ``end == heading.end`` is the ordinary case for a region that is
    immediately followed by a fence.
    """

    heading: Heading
    start: int
    end: int
    inside_columns: bool
    blank: bool


@dataclass(frozen=True)
class SectionsDocument:
    """An immutable parsed document with byte offsets into ``source``."""

    source: bytes
    body_start: int
    newline: bytes
    headings: tuple[Heading, ...]
    regions: tuple[Region, ...]

    @property
    def note_revision(self) -> str:
        return "sha256:" + hashlib.sha256(self.source).hexdigest()

    def find(
        self,
        keyword: str,
        *,
        level: int | None = None,
        case_insensitive: bool = False,
    ) -> tuple[Region, ...]:
        """All regions whose heading title equals ``keyword`` (exact match).

        Exact-title matching is deliberate: the vault's ``sectionUpsert`` uses
        ``lines[i].includes(keyword)`` (substring, first-match-wins); the PWA
        must detect every plausible region and fail closed on more than one.
        """
        if not keyword:
            raise SectionError("invalid_section_keyword")
        wanted = keyword.lower() if case_insensitive else keyword
        found: list[Region] = []
        for region in self.regions:
            if level is not None and region.heading.level != level:
                continue
            title = region.heading.title.lower() if case_insensitive else region.heading.title
            if title == wanted:
                found.append(region)
        return tuple(found)

    def require_unique(
        self,
        keyword: str,
        *,
        level: int | None = None,
        case_insensitive: bool = False,
        code: str = "ambiguous_section",
    ) -> Region | None:
        """Return the single matching region, ``None`` when absent, or fail closed.

        ``None`` — not an exception — is the missing case, because "this note has
        no such section" and "this note has several" are different answers and
        the caller's error code differs for each.
        """
        matches = self.find(keyword, level=level, case_insensitive=case_insensitive)
        if len(matches) > 1:
            raise AmbiguousSection(code)
        return matches[0] if matches else None

    def event_anchor(self) -> Region:
        """The single ``# Event``-style anchor line, per the eventNotes regex.

        Exactly one match is required (0 or >1 fails closed with
        ``ambiguous_anchor``). Ported from the sibling with its contract intact;
        this app's only write does not use it, but the anchor lines are present
        in the same daily notes and dropping the check would make an
        ambiguous-anchor note silently writable.
        """
        matches = [region for region in self.regions if _EVENT_ANCHOR.match(region.heading.line)]
        if len(matches) != 1:
            raise AmbiguousSection("ambiguous_anchor")
        return matches[0]

    def region_content(self, region: Region) -> bytes:
        return self.source[region.heading.end : region.end]

    def region_revision(self, region: Region) -> str:
        """Content hash of the region's byte span (heading line + content)."""
        return "sha256:" + hashlib.sha256(self.source[region.start : region.end]).hexdigest()

    def region_lines(self, region: Region, *, limit: int = 200) -> tuple[str, ...]:
        """The region's lines (heading line first) for conflict presentation."""
        content = self.source[region.start : region.end].decode("utf-8", errors="replace")
        lines = content.splitlines()
        if len(lines) > limit:
            raise SectionError("section_too_large_for_presentation")
        return tuple(lines)


def parse_sections(source: bytes, *, max_bytes: int = 2_000_000) -> SectionsDocument:
    """Parse one note into headed regions, failing closed on malformed input."""
    if len(source) > max_bytes:
        raise SectionError("daily_note_too_large")
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SectionError("daily_note_not_utf8") from exc
    body_start_char = _body_start(text)
    newline = _document_newline(text)
    lines: list[tuple[int, int, str]] = []
    for match in _LINE.finditer(text, body_start_char):
        lines.append((match.start(), match.end(), match.group(0)))

    in_columns = False
    columns_marker = ""
    in_code = False
    code_marker = ""
    heading_indices: list[int] = []
    heading_columns: list[bool] = []
    is_separator: list[bool] = []
    for index, (_start, _end, line_text) in enumerate(lines):
        stripped = line_text.strip()
        fence = _FENCE.match(stripped)
        if fence:
            marker = fence.group(1)
            language = fence.group(2).strip()
            if in_columns:
                if stripped.startswith(columns_marker):
                    in_columns = False
                    columns_marker = ""
                is_separator.append(False)
                continue
            if in_code:
                if stripped.startswith(code_marker):
                    in_code = False
                    code_marker = ""
                is_separator.append(False)
                continue
            if language == "columns":
                in_columns = True
                columns_marker = marker
            else:
                in_code = True
                code_marker = marker
            is_separator.append(False)
            continue
        if in_code:
            is_separator.append(False)
            continue
        if _SEPARATOR.match(stripped) and in_columns:
            is_separator.append(True)
            continue
        is_separator.append(False)
        parsed_heading = _parse_heading(line_text)
        if parsed_heading is not None:
            heading_indices.append(index)
            heading_columns.append(in_columns)

    headings: list[Heading] = []
    for index in heading_indices:
        line_start, end, line_text = lines[index]
        level, title, numbered = _heading_parts(line_text)
        headings.append(
            Heading(
                level=level,
                title=title,
                numbered=numbered,
                raw=_heading_body(line_text),
                start=_byte_offset(text, line_start),
                end=_byte_offset(text, end),
                line=line_text,
                newline=_line_newline(line_text),
            )
        )

    regions: list[Region] = []
    for position, index in enumerate(heading_indices):
        heading = headings[position]
        inside_columns = heading_columns[position]
        end = _region_end(lines, index, inside_columns, is_separator, heading=heading)
        region_end_byte = _byte_offset(text, end) if end < len(text) else len(source)
        blank = _region_blank(source, heading, region_end_byte)
        regions.append(
            Region(
                heading=heading,
                start=heading.start,
                end=region_end_byte,
                inside_columns=inside_columns,
                blank=blank,
            )
        )

    return SectionsDocument(
        source=source,
        body_start=_byte_offset(text, body_start_char),
        newline=newline,
        headings=tuple(headings),
        regions=tuple(regions),
    )


def insert_after_heading(source: bytes, region: Region, line: str) -> bytes:
    """Splice ``line`` as the first body line of ``region``, in the source's style.

    Byte contract: ``splice(region.heading.end, 0, line, terminator)`` where
    ``terminator`` is ``region.heading.newline`` — the heading line's **own**
    terminator, read from the source span, never a hard-coded ``b"\\n"``.

    For the live blank `笔记` region (`heading.end == 4274`, a one-byte blank
    line, then a four-backtick `columns` fence) the result is
    `b"# 笔记\\n- [[…]]\\n\\n"`: the heading, the inserted line, and the blank
    line the user already had. Nothing is added after the region and nothing
    before the heading, so a one-line append cannot turn into a whole-file
    reformat.

    The heading line is untouched, so this can never create the `笔记` section
    that `require_unique` was supposed to find: a `Region` is required, so the
    heading necessarily already exists.
    """
    if not line or "\n" in line or "\r" in line:
        raise SectionError("invalid_section_line")
    encoded = line.encode("utf-8")
    at = region.heading.end
    return source[:at] + encoded + region.heading.newline + source[at:]


def section_revision_of(source: bytes, region: Region | None) -> str | None:
    """The snapshot/conflict revision of a section region (``None`` when absent)."""
    if region is None:
        return None
    return "sha256:" + hashlib.sha256(source[region.start : region.end]).hexdigest()


def _body_start(text: str) -> int:
    opening = _DELIMITER.match(text)
    if opening is None:
        return 0
    closing = _CLOSING_DELIMITER.search(text[opening.end() :])
    if closing is None:
        raise AmbiguousSection("unterminated_top_level_frontmatter")
    # The closing delimiter's match ends *before* its line terminator, so the
    # body starts after that terminator. The sibling stops at `closing.end()`
    # and leaves the body one byte early for an empty frontmatter block
    # (`---\n---\n`), where the closing `---` is matched at offset 0 of the
    # searched slice; `frontmatter._after_line_terminator` is the same skip.
    start = opening.end() + closing.end()
    if text[start : start + 2] == "\r\n":
        return start + 2
    if text[start : start + 1] in {"\n", "\r"}:
        return start + 1
    return start


def _document_newline(text: str) -> bytes:
    if "\r\n" in text:
        return b"\r\n"
    if "\r" in text:
        return b"\r"
    return b"\n"


def _parse_heading(line_text: str) -> tuple[int, str, str | None] | None:
    match = _HEADING_LINE.match(line_text.rstrip("\r\n"))
    if match is None:
        return None
    level = len(match.group("marker"))
    body = match.group("body")
    numbered = _NUMBERED.match(body)
    if numbered is not None:
        return level, numbered.group("title"), numbered.group("num")
    return level, body, None


def _heading_parts(line_text: str) -> tuple[int, str, str | None]:
    parsed = _parse_heading(line_text)
    if parsed is None:
        raise SectionError("invalid_heading_line")
    return parsed


def _heading_body(line_text: str) -> str:
    match = _HEADING_LINE.match(line_text.rstrip("\r\n"))
    if match is None:
        raise SectionError("invalid_heading_line")
    return match.group("body")


def _is_activity_section_boundary(line_text: str) -> bool:
    """True if line_text is a columns fence, event anchor, or top-level structural heading."""
    stripped = line_text.strip()
    fence = _FENCE.match(stripped)
    if fence is not None and fence.group(2).strip().startswith("columns"):
        return True
    if _EVENT_ANCHOR.match(line_text):
        return True
    parsed = _parse_heading(line_text)
    if parsed is None:
        return False
    level, title, _ = parsed
    if level != 1:
        return False
    if title in _ACTIVITY_TITLES:
        return True
    clean_title = title.split("%%")[0].strip()
    return clean_title in _DAILY_STRUCTURAL_TITLES


def _region_end(
    lines: list[tuple[int, int, str]],
    heading_index: int,
    inside_columns: bool,
    is_separator: list[bool],
    heading: Heading | None = None,
) -> int:
    """Exclusive char offset where the heading's region ends."""
    is_activity = (
        heading is not None
        and heading.level == 1
        and heading.title in _ACTIVITY_TITLES
    )
    for index in range(heading_index + 1, len(lines)):
        start, _end, line_text = lines[index]
        stripped = line_text.strip()
        fence = _FENCE.match(stripped)
        if inside_columns:
            if is_separator[index] or fence is not None:
                return start
            continue
        if is_activity:
            if _is_activity_section_boundary(line_text):
                return start
            continue
        if fence is not None or _parse_heading(line_text) is not None:
            return start
    return lines[-1][1] if lines else 0


def _region_blank(source: bytes, heading: Heading, region_end: int) -> bool:
    return not source[heading.end : region_end].strip(b" \t\r\n")


def _line_newline(line_text: str) -> bytes:
    if line_text.endswith("\r\n"):
        return b"\r\n"
    if line_text.endswith("\r"):
        return b"\r"
    return b"\n"


def _byte_offset(text: str, char_offset: int) -> int:
    return len(text[:char_offset].encode("utf-8"))

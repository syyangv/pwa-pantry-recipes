"""The daily-note bytes these tests write, and what each shape is for.

These are **frozen fixtures, not a live vault**: every byte is in this file, so
`tmp_path` is the only thing the suite ever opens. The shapes are the ones the
spec measured against the real `日记/2026/2026-09-27.md` — a frontmatter block, an
`INPUT[toggle(...)]` inline field, the `![[dailyModify.base|ordered-list]]` embed,
`# 笔记` followed by a single blank line, and then a four-backtick `columns`
fence, which is the whole reason the live `笔记` region is the heading line plus
one blank byte and nothing else (F3).
"""

from __future__ import annotations

from typing import Final

#: `日记/2026/2026-09-27.md` under `DAILY_NOTES_ROOT=日记`, and the only path any
#: test writes.
DAILY_NOTE_PATH: Final = "日记/2026/2026-09-27.md"

_FRONTMATTER_LINES: Final[tuple[str, ...]] = (
    "---",
    "date: 2026-09-27",
    "type: daily",
    "created: 2026-09-27",
    "modified: 2026-09-27",
    "tags: [日记]",
    "---",
    "",
    "# 2026-09-27",
    "",
    "## INPUT[toggle(dayRating)]:- good",
    "",
    "![[dailyModify.base|ordered-list]]",
    "",
)

#: A four-backtick ``columns`` fence, because the region engine ends a region at
#: the first following fence and that is what makes the `笔记` region short.
_COLUMNS_LINES: Final[tuple[str, ...]] = (
    "````columns",
    "# 日总结",
    "",
    "## 3.1 课程",
    "",
    "```dataviewjs",
    "await this.file;",
    "```",
    "====",
    "## Event",
    "",
    "```dataview",
    "```",
    "````",
    "",
)


def _join(lines: tuple[str, ...], newline: bytes) -> bytes:
    return newline.join(line.encode() for line in lines) + newline


def _note(newline: bytes, notes_lines: tuple[str, ...]) -> bytes:
    return (
        _join(_FRONTMATTER_LINES, newline)
        + _join(notes_lines, newline)
        + _join(_COLUMNS_LINES, newline)
    )


def daily_note_bytes(newline: bytes = b"\n") -> bytes:
    """The reference note: `# 笔记`, one blank line, then the columns fence.

    This is the measured live shape. The `笔记` region is the heading line plus
    the one blank byte — `blank=True` — and inserting at `heading.end` is what
    makes the region two lines with no extra blank line and none consumed.
    """
    return _note(newline, ("# 笔记", ""))


def column_fence_note_bytes(newline: bytes = b"\n") -> bytes:
    """`# 笔记` immediately followed by the columns fence — the other real shape."""
    return _note(newline, ("# 笔记",))


def populated_note_bytes(newline: bytes = b"\n") -> bytes:
    """A note whose `笔记` region already holds two list items.

    The append must still land at `heading.end`, so the new cook becomes the
    **first** item and the two existing ones keep their order below it.
    """
    return _note(newline, ("# 笔记", "- [[花蛤拌饭]]", "- [[烤鸡翅]]"))


def two_notes_sections_bytes(newline: bytes = b"\n") -> bytes:
    """A note with **two** `# 笔记` headings.

    The vault's own `sectionUpsert` is first-match-wins substring matching, so
    picking one of these is a guess. The write fails closed instead.
    """
    return _note(newline, ("# 笔记", "- [[花蛤拌饭]]")) + _join(
        ("# 笔记", "- [[烤鸡翅]]"), newline
    )


def no_notes_section_bytes(newline: bytes = b"\n") -> bytes:
    """A well-formed daily note with no `笔记` heading at all.

    The engine emits no heading, so this case is **unreachable** rather than
    silently created: the append is refused and the note is left byte-identical.
    """
    return _join(_FRONTMATTER_LINES, newline) + _join(_COLUMNS_LINES, newline)


def linked_outside_the_region_bytes(newline: bytes = b"\n") -> bytes:
    """A note whose `[[盐焗鸡]]` sits inside the columns fence, not under `笔记`.

    The two hand-typed cooks in the real vault are in other regions — one right
    after the `![[dailyModify.base|ordered-list]]` embed, one right after
    `# Event` — so a dedupe that only looked inside the `笔记` region would miss
    both and write a second copy. This is the shape that proves it does not.
    """
    columns = (
        "````columns",
        "# 日总结",
        "",
        "- [[盐焗鸡]]",
        "",
        "```dataviewjs",
        "await this.file;",
        "```",
        "````",
        "",
    )
    return _join(_FRONTMATTER_LINES, newline) + _join(("# 笔记", ""), newline) + _join(
        columns, newline
    )

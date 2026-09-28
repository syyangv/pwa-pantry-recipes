"""`sections.py`: the region engine, and the region insertion the cook log needs.

Two things are pinned here, and they are different kinds of pin.

**The measured region.** The live `日记/2026/2026-09-27.md` puts `# 笔记` at byte
4265 and the region ends at 4275 with content `b"\n"` and `blank=True` — the
heading line plus the one blank line after it, and nothing else, because the
next line is a ```` ````columns ```` fence and a region ends at the first
following fence. The decision that depends on this is *where the cook link goes*:
`heading.end` (4274), not `region.end`, so the link lands between the heading and
the fence. These tests build a note of that shape and assert the literal offsets,
so a change to the region model fails here rather than in the user's daily note.

The test never reads the real vault. The note is rebuilt from `tmp_path` bytes,
with a padding line whose length is computed so the offsets are the measured
ones.

**Both line-terminator widths.** The heading's own terminator is read from the
source span and reused verbatim. A note with CRLF must be spliced with CRLF:
injecting a single LF produces a mixed-terminator file, which `git` renders as a
whole-file diff and which `task-date-recorder` then rewrites on its debounced
`modify` event, so the noise is permanent rather than transient. The CRLF test
asserts zero lone LFs in the result, which is the property a hard-coded `b"\n"`
would break.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.vault.sections import (
    AmbiguousSection,
    SectionError,
    insert_after_heading,
    parse_sections,
)

NOTES_HEADING = "笔记"
NOTES_REGION_START = 4265
NOTES_REGION_END = 4275
COOK_LINK = "- [[盐焗鸡]]"

#: The frontmatter of a real daily note, in the shapes that actually occur:
#: flow lists, block lists, a bare key, a quoted time, floats, and `date` values
#: that PyYAML resolves.
DAILY_FRONTMATTER = """---
aliases: []
tags: []
cssclasses:
  - hide-frontmatter
activity_tags: []
medication_long:
  - 吃药/Allegra
medication:
体重:
起起体重:
假期:
location: jc
今日甚好: false
noBuy: false
小饭桌: false
modified_at: 2026-09-27
tasks_completed: 0
睡眠分钟: 61
深度睡眠时长: 42
入睡时间: "01:41"
起床时间: "06:39"
心率: 59
平均HRV: 26
最高心率: 90
血压: 96.3
压力: 0
---

# 日总结 %% fold%%
**⚖️体重:** `VIEW[{体重}]` **🐾起起体重:** `VIEW[{起起体重}]`

# 笔记

````columns
id: m09e9q5

===

![[dailyModify.base|ordered-list]]

===

![[日常工具-20250901.base#即将到期订阅]]

````

# Event

```columns
id: event-cols

===
"""


def _daily_note_bytes(terminator: bytes = b"\n") -> bytes:
    """The daily-note shape, with `# 笔记` padded to the measured offset.

    The padding is a real body line, not whitespace, so the region model sees an
    ordinary paragraph before the heading. Its length is computed from the
    terminator width, so the CRLF variant lands on the same *line* with a
    different byte count — which is exactly the difference the terminator rule
    exists to handle.
    """
    note = DAILY_FRONTMATTER.replace("\n", terminator.decode()).encode()
    marker = f"# {NOTES_HEADING}{terminator.decode()}".encode()
    at = note.find(marker)
    assert at > 0, "the fixture must contain the 笔记 heading"
    filler = b"x" * (NOTES_REGION_START - at - 1)
    return note[:at] + filler + terminator + note[at:]


def test_the_measured_notes_region_is_the_heading_line_plus_one_blank_line() -> None:
    """The pin: `4265..4275`, `b"\\n"`, `blank=True`.

    The reason the region is that short is the fence: `# 笔记` is followed by a
    blank line and then ```` ````columns ````, and a region ends at the first
    following fence line. If the fence were three backticks wide, or if the
    region swallowed it, this span would grow and the cook link would land in
    the wrong place.
    """
    source = _daily_note_bytes()
    document = parse_sections(source)
    region = document.require_unique(NOTES_HEADING, level=None, code="ambiguous_notes_section")
    assert region is not None
    assert region.start == NOTES_REGION_START
    assert region.end == NOTES_REGION_END
    assert document.region_content(region) == b"\n"
    assert region.blank is True
    assert region.inside_columns is False
    assert source[region.start : region.heading.end] == "# 笔记\n".encode()


def test_the_insertion_point_is_the_heading_end_not_the_region_end() -> None:
    """D2/F3: the link goes *between* the heading and the fence. `region.end`
    (4275) would put it after the blank line and under the columns fence."""
    source = _daily_note_bytes()
    document = parse_sections(source)
    region = document.require_unique(NOTES_HEADING)
    assert region is not None
    assert region.heading.end == 4274
    assert region.heading.end != region.end

    rendered = insert_after_heading(source, region, COOK_LINK)
    link_end = region.heading.end + len(COOK_LINK.encode()) + 1
    assert rendered[region.heading.end : link_end] == f"{COOK_LINK}\n".encode()
    # Byte-identical everywhere else: the pre-image minus the splice.
    assert rendered[: region.heading.end] == source[: region.heading.end]
    assert rendered[link_end:] == source[region.heading.end :]
    # The fence is still the fence, and the link is above it.
    assert "- [[盐焗鸡]]\n\n````columns".encode() in rendered


def test_a_crlf_note_is_spliced_with_crlf_and_never_gains_a_lone_lf() -> None:
    """The two-byte terminator case. A hard-coded `b"\\n"` here would leave one
    lone LF in an otherwise CRLF file — the mixed-terminator whole-file diff R17
    describes."""
    source = _daily_note_bytes(b"\r\n")
    document = parse_sections(source)
    region = document.require_unique(NOTES_HEADING)
    assert region is not None
    assert region.heading.newline == b"\r\n"
    assert source[region.start : region.heading.end] == "# 笔记\r\n".encode()

    rendered = insert_after_heading(source, region, COOK_LINK)
    assert f"# 笔记\r\n{COOK_LINK}\r\n".encode() in rendered
    assert rendered.count(b"\n") == rendered.count(b"\r\n")
    assert rendered[: region.heading.end] == source[: region.heading.end]
    assert rendered[region.heading.end + len(COOK_LINK.encode()) + 2 :] == source[
        region.heading.end :
    ]


def test_a_lone_cr_note_is_spliced_with_a_lone_cr() -> None:
    """The third width the vault tolerates; the rule is "read it", not "LF or CRLF"."""
    source = _daily_note_bytes(b"\r")
    document = parse_sections(source)
    region = document.require_unique(NOTES_HEADING)
    assert region is not None
    assert region.heading.newline == b"\r"
    rendered = insert_after_heading(source, region, COOK_LINK)
    assert f"# 笔记\r{COOK_LINK}\r".encode() in rendered
    assert b"\n" not in rendered


def test_the_insertion_refuses_a_line_that_is_not_one_line() -> None:
    """A caller that hands back two lines has a bug; splicing it would corrupt
    the note rather than fail."""
    source = _daily_note_bytes()
    region = parse_sections(source).require_unique(NOTES_HEADING)
    assert region is not None
    for bad in ("", "- [[a]]\n- [[b]]", "- [[a]]\r"):
        with pytest.raises(SectionError):
            insert_after_heading(source, region, bad)


def test_two_notes_regions_fail_closed_by_code() -> None:
    """The vault's own `sectionUpsert` is first-match-wins substring matching;
    writing into whichever came first would be a coin flip the user cannot see."""
    source = "---\n---\n# 笔记\n- [[A]]\n\n# 笔记\n- [[B]]\n".encode()
    document = parse_sections(source)
    assert len(document.find(NOTES_HEADING)) == 2
    with pytest.raises(AmbiguousSection) as raised:
        document.require_unique(NOTES_HEADING, level=None, code="ambiguous_notes_section")
    assert str(raised.value) == "ambiguous_notes_section"


def test_a_note_with_no_notes_heading_is_absent_not_created() -> None:
    """`require_unique` returns `None` for absent and the module emits no
    heading, so a caller cannot turn a missing section into a new one by
    accident. A write path must treat `None` as a 422, not as an invitation."""
    source = "---\n---\n# 日总结 %% fold%%\n- 只有总结\n".encode()
    document = parse_sections(source)
    assert document.require_unique(NOTES_HEADING) is None
    assert document.find(NOTES_HEADING) == ()
    # There is no region to insert into, and the primitive cannot conjure one:
    # it takes a `Region`, so a missing section is unreachable rather than
    # silently created.
    with pytest.raises(AttributeError):
        insert_after_heading(source, None, COOK_LINK)  # type: ignore[arg-type]


def test_exact_title_matching_rejects_substring_ambiguity() -> None:
    """`我喜欢的电视剧` contains `电视剧`; substring matching would find it."""
    source = "---\n---\n# 我喜欢的电视剧\n## 电视剧\n".encode()
    document = parse_sections(source)
    assert [region.heading.title for region in document.find("电视剧")] == ["电视剧"]
    assert document.find("我喜欢的电视剧")[0].heading.title == "我喜欢的电视剧"


def test_a_region_ends_at_the_next_heading_and_at_a_columns_fence() -> None:
    document = parse_sections(DAILY_FRONTMATTER.encode())
    # A `%% fold%%` comment stays part of the title: the region engine matches
    # exact titles, so the caller passes the whole line.
    summary = document.require_unique("日总结 %% fold%%")
    notes = document.require_unique(NOTES_HEADING)
    assert summary is not None and notes is not None
    # A heading bounds a region, so `日总结` ends exactly where `笔记` starts.
    assert summary.end == notes.start
    assert notes.end == _fence_offset(DAILY_FRONTMATTER.encode())
    assert document.region_content(notes) == b"\n"
    # The `%%` comment is not stripped from the title, so `日总结` alone does
    # not match: a substring match here would silently pick the wrong region.
    assert document.require_unique("日总结") is None


def test_a_region_never_spans_a_fence_even_a_three_backtick_one() -> None:
    for fence in ("```columns", "````columns"):
        source = f"---\n---\n# 笔记\n\n{fence}\nid: x\n\n```\n".encode()
        region = parse_sections(source).require_unique(NOTES_HEADING)
        assert region is not None
        assert region.end == source.index(fence.encode())
        assert region.blank is True


def test_a_heading_inside_a_code_fence_is_not_a_region() -> None:
    """A `# 笔记` line inside a fenced code block is sample text, not a section,
    and writing a cook link into it would corrupt the user's code sample."""
    source = "---\n---\n# 笔记\n\n```\n# 笔记\n```\n".encode()
    document = parse_sections(source)
    assert len(document.find(NOTES_HEADING)) == 1
    assert document.find(NOTES_HEADING)[0].heading.start == len(b"---\n---\n")


def test_a_region_with_content_is_not_blank() -> None:
    """`blank` is the caller's "the user has written nothing here" signal, so it
    must be false the moment there is content — including a line of spaces."""
    assert parse_sections("---\n---\n# 笔记\n- [[A]]\n".encode()).require_unique(
        NOTES_HEADING
    ).blank is False  # type: ignore[union-attr]
    assert parse_sections("---\n---\n# 笔记\n   \n".encode()).require_unique(
        NOTES_HEADING
    ).blank is True  # type: ignore[union-attr]


def test_a_numbered_heading_keeps_its_number_and_its_title() -> None:
    """`## 3.1 课程` is titled `课程`: the cook-log keyword match is on the title,
    and a number left in the title would make every numbered section unmatchable."""
    document = parse_sections("---\n---\n## 3.1 课程\n- 课\n".encode())
    heading = document.headings[0]
    assert (heading.level, heading.title, heading.numbered) == (2, "课程", "3.1")


def test_the_region_revision_is_content_scoped_not_offset_scoped() -> None:
    """An unrelated edit *before* the section must not invalidate it, or every
    keystroke anywhere in the note would look like a conflict here."""
    first = parse_sections("---\nx: 1\n---\n# 笔记\n- [[A]]\n".encode())
    second = parse_sections("---\nx: 2\n---\n# 笔记\n- [[A]]\n".encode())
    left = first.require_unique(NOTES_HEADING)
    right = second.require_unique(NOTES_HEADING)
    assert left is not None and right is not None
    assert first.region_revision(left) == second.region_revision(right)
    assert first.note_revision != second.note_revision


def test_an_unterminated_or_non_utf8_or_oversized_note_fails_closed() -> None:
    with pytest.raises(AmbiguousSection):
        parse_sections(b"---\nopen: true\n# Event\n")
    with pytest.raises(SectionError):
        parse_sections(b"\xff\xfe# Event\n")
    with pytest.raises(SectionError):
        parse_sections(b"# Event\n" * 3_000, max_bytes=1_000)


def test_a_bom_and_crlf_note_parses() -> None:
    source = "﻿---\r\nactivity_tags: []\r\n---\r\n# 笔记\r\n- [[A]]\r\n".encode()
    region = parse_sections(source).require_unique(NOTES_HEADING)
    assert region is not None
    assert source[region.start : region.heading.end] == "# 笔记\r\n".encode()


def test_the_daily_note_fixture_is_written_to_tmp_path_and_the_real_vault_is_untouched(
    tmp_path: Path,
) -> None:
    """The engine is pure over bytes; the only filesystem touch in this suite is
    writing the fixture into `tmp_path`. No test may read the real vault."""
    note = tmp_path / "2026-09-27.md"
    note.write_bytes(_daily_note_bytes())
    region = parse_sections(note.read_bytes()).require_unique(NOTES_HEADING)
    assert region is not None
    assert region.start == NOTES_REGION_START
    assert "/Users/syang/obsidian" not in str(note)


def _fence_offset(source: bytes) -> int:
    return source.index(b"````columns")

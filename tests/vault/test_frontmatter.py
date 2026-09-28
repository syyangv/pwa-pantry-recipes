"""`frontmatter.py`: a patch must move bytes, and only the bytes it names.

The property that matters is not "the value round-trips" — `yaml.safe_dump`
would give that. It is **`render()` leaves every unrelated byte byte-identical**:
a daily note is a live Obsidian file that `task-date-recorder` rewrites on a
debounced `modify` event, so a reordered key block, a normalized comment, or a
flow list rewritten as a block list is a permanent whole-file diff, not a
cosmetic one. The adversarial round-trip below is therefore the load-bearing
test in this file: it asserts the pre-image minus the splice, not the post-image.

The three-valued field state is the other half. `absent`, `empty`, and `set` are
three answers, and a parser that had two would silently turn "the user cleared
this key" into "this key does not exist".

Known limitation, asserted here so it cannot be discovered later: `FieldData`
has no `datetime.date`, so `field()` refuses a date-valued key. PyYAML resolves
an unquoted `first_cooked: 2025-08-23` to a `date`, which every real recipe note
carries. Callers reading tracker-owned fields must read `document.values`, not
`document.field()`. Ported unchanged from the sibling, which only ever targeted
string and list keys.
"""

from __future__ import annotations

import random
from datetime import date

import pytest

from app.vault.frontmatter import (
    AmbiguousFrontmatter,
    FieldValue,
    FrontmatterConflict,
    FrontmatterError,
    note_revision,
    parse_frontmatter,
    patch_frontmatter_field,
)

#: Carries a comment, a BOM, CRLF, a block list, an inline field the PWA never
#: writes, and a quoted value that a re-dump would normalize to an int.
SURGICAL = (
    b"\xef\xbb\xbf---\r\n# before\r\nname: old # keep\r\n"
    b"list:\r\n  - alpha\r\n  - beta\r\nother: '01'\r\n---\r\n# body \xe2\x9c\x93\r\n"
)


def test_absent_empty_and_set_are_three_answers_not_two() -> None:
    source = b"---\nempty:\nnullish: null\nflag: false\ntext: ''\nitems: [one, two]\n---\nbody\n"
    document = parse_frontmatter(source)
    assert document.field("missing").state == "absent"
    assert document.field("missing").value is None
    assert document.field("empty") == document.field("nullish")
    assert document.field("empty").state == "empty"
    assert document.field("empty").value is None
    assert document.field("flag").value is False
    assert document.field("text").value == ""
    assert document.field("items").value == ["one", "two"]


def test_absent_empty_and_set_are_three_distinct_revisions() -> None:
    """Collapsing any two of the three states would make a cleared key and a
    missing key share a revision, and a CAS compare would then accept a write
    the user had already superseded."""
    document = parse_frontmatter(b"---\nempty:\nflag: false\n---\nbody\n")
    revisions = {
        document.field("empty").revision,
        document.field("flag").revision,
        document.field("missing").revision,
    }
    assert len(revisions) == 3


def test_render_leaves_every_unrelated_byte_byte_identical() -> None:
    """The pre-image minus the splice, byte for byte."""
    before = parse_frontmatter(SURGICAL)
    rendered, result = patch_frontmatter_field(
        SURGICAL,
        key="list",
        base=before.field("list"),
        candidate=FieldValue("set", ["new", "健身房"]),
        base_note_revision=before.note_revision,
    )
    assert result.value == ["new", "健身房"]
    assert rendered.replace(
        b"  - new\r\n  - \xe5\x81\xa5\xe8\xba\xab\xe6\x88\xbf\r\n",
        b"  - alpha\r\n  - beta\r\n",
    ) == SURGICAL
    # The BOM, the comment, the trailing comment, the quoted '01', and the body
    # are all still exactly where they were.
    assert rendered.startswith(b"\xef\xbb\xbf---\r\n# before\r\n")
    assert b"name: old # keep\r\n" in rendered
    assert b"other: '01'\r\n---\r\n# body \xe2\x9c\x93\r\n" in rendered


def test_a_block_list_stays_a_block_list_byte_identically() -> None:
    """Style-following: the vault's meta-bind shape is `key:` + indented `-`
    items. A re-dump would write flow style and rewrite every line of it."""
    source = "---\nactivity_tags:\n  - 健身房\n---\nbody\n".encode()
    document = parse_frontmatter(source)
    rendered, snapshot = patch_frontmatter_field(
        source,
        key="activity_tags",
        base=document.field("activity_tags"),
        candidate=FieldValue("set", ["健身房", "therapy", "学习"]),
        base_note_revision=document.note_revision,
    )
    assert snapshot.value == ["健身房", "therapy", "学习"]
    assert "activity_tags:\n  - 健身房\n  - therapy\n  - 学习\n".encode() in rendered
    assert rendered.endswith(b"---\nbody\n")
    # An identical candidate over the same span is a no-op, byte for byte.
    unchanged, _ = patch_frontmatter_field(
        rendered,
        key="activity_tags",
        base=snapshot,
        candidate=FieldValue("set", ["健身房", "therapy", "学习"]),
        base_note_revision=parse_frontmatter(rendered).note_revision,
    )
    assert unchanged == rendered


def test_a_block_list_round_trips_through_crlf_and_lone_cr() -> None:
    for source, expected, forbidden in (
        (b"---\r\nlist:\r\n  - alpha\r\n---\r\nbody\r\n", b"list:\r\n  - new\r\n", b"\n\n"),
        (b"---\rlist:\r  - alpha\r---\rbody\r", b"list:\r  - new\r", b"\n"),
    ):
        document = parse_frontmatter(source)
        rendered, snapshot = patch_frontmatter_field(
            source,
            key="list",
            base=document.field("list"),
            candidate=FieldValue("set", ["new"]),
            base_note_revision=document.note_revision,
        )
        assert expected in rendered
        assert snapshot.value == ["new"]
        assert forbidden not in rendered.replace(b"\r\n", b"")


def test_a_flow_list_stays_flow() -> None:
    """The other direction: a flow-style span must not be widened to block style."""
    source = "---\nactivity_tags: [健身房]\n---\nbody\n".encode()
    document = parse_frontmatter(source)
    rendered, _ = patch_frontmatter_field(
        source,
        key="activity_tags",
        base=document.field("activity_tags"),
        candidate=FieldValue("set", ["健身房", "therapy"]),
        base_note_revision=document.note_revision,
    )
    assert "activity_tags: [健身房, therapy]".encode() in rendered


def test_insert_empty_and_delete_are_three_distinct_splices() -> None:
    source = b"---\nexisting: yes\n---\nbody\n"
    document = parse_frontmatter(source)
    inserted, snapshot = patch_frontmatter_field(
        source,
        key="flag",
        base=document.field("flag"),
        candidate=FieldValue("set", False),
        base_note_revision=document.note_revision,
    )
    assert b"flag: false\n---" in inserted
    empty, empty_snapshot = patch_frontmatter_field(
        inserted,
        key="flag",
        base=snapshot,
        candidate=FieldValue("empty", None),
        base_note_revision=parse_frontmatter(inserted).note_revision,
    )
    assert b"flag:\n---" in empty
    assert empty_snapshot.state == "empty"
    deleted, absent = patch_frontmatter_field(
        empty,
        key="flag",
        base=empty_snapshot,
        candidate=FieldValue("absent", None),
        base_note_revision=parse_frontmatter(empty).note_revision,
    )
    assert deleted == source
    assert absent.state == "absent"


def test_a_duplicate_key_fails_closed_at_every_nesting_level() -> None:
    """PyYAML's default is last-one-wins, so a duplicated `材料` would silently
    become the second one and the first would vanish without a trace."""
    for source in (
        b"---\na: one\na: two\n---\n",
        b"---\nouter: {same: one, same: two}\ntarget: old\n---\n",
        b"---\nouter:\n  nested:\n    same: one\n    same: two\ntarget: old\n---\n",
        b"---\nouter:\n  - same: one\n    same: two\ntarget: old\n---\n",
    ):
        with pytest.raises(AmbiguousFrontmatter):
            parse_frontmatter(source)


@pytest.mark.parametrize(
    "source",
    [
        b"body only\n",  # no frontmatter block at all
        b"---\nopen: true\n# Event\n",  # unterminated
        b"---\na: [unterminated\n---\n",  # unparseable
        b"---\n- just\n- a list\n---\n",  # not a mapping
        b"---\n{target: old, other: keep}\n---\n",  # top-level flow mapping
        b"---\nsource: &shared value\na: *shared\n---\n",  # anchors/aliases
        b"---\ntrue: value\n---\n",  # non-string key
        b"---\na:\n<<: {b: 1}\n---\n",  # merge key
        b"---\na:\n\t- tab-indent\n---\n",
    ],
)
def test_every_malformation_fails_closed(source: bytes) -> None:
    with pytest.raises(AmbiguousFrontmatter):
        parse_frontmatter(source)


def test_a_note_that_is_too_large_or_not_utf8_fails_closed() -> None:
    with pytest.raises(FrontmatterError):
        parse_frontmatter(b"---\na: 1\n---\n", max_bytes=8)
    with pytest.raises(FrontmatterError):
        parse_frontmatter(b"---\na: \xff\xfe\n---\n")


def test_adversarial_values_change_only_the_target_span() -> None:
    """100 random values, each of which has broken a naive YAML writer at some
    point: `#`, `:`, quotes, brackets, CJK, emoji."""
    rng = random.Random(0xDA11)
    alphabet = "ab :#[]{}'\"健身房\U0001f3af"
    source = b"---\nleft: untouched\ntarget: old\nright: untouched # comment\n---\nBODY\n"
    for _ in range(100):
        value = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 30)))
        base = parse_frontmatter(source)
        rendered, snapshot = patch_frontmatter_field(
            source,
            key="target",
            base=base.field("target"),
            candidate=FieldValue("set", value),
            base_note_revision=base.note_revision,
        )
        assert snapshot.value == value
        assert rendered.startswith(b"---\nleft: untouched\ntarget:")
        assert rendered.endswith(b"right: untouched # comment\n---\nBODY\n")


def test_an_unrelated_change_rebases_but_a_field_change_conflicts() -> None:
    source = b"---\na: old\nb: one\n---\nbody\n"
    base_document = parse_frontmatter(source)
    changed = source.replace(b"b: one", b"b: two")
    rendered, result = patch_frontmatter_field(
        changed,
        key="a",
        base=base_document.field("a"),
        candidate=FieldValue("set", "pwa"),
        base_note_revision=base_document.note_revision,
    )
    assert b"b: two" in rendered
    assert result.value == "pwa"
    with pytest.raises(FrontmatterConflict) as raised:
        patch_frontmatter_field(
            source.replace(b"a: old", b"a: obsidian"),
            key="a",
            base=base_document.field("a"),
            candidate=FieldValue("set", "pwa"),
            base_note_revision=base_document.note_revision,
        )
    assert raised.value.current.value == "obsidian"


def test_the_note_revision_is_the_identity_of_the_exact_bytes() -> None:
    """The atomic store's CAS compares this string, so it must change when
    *any* byte changes and must not change when none does."""
    source = b"---\na: 1\n---\nbody\n"
    assert parse_frontmatter(source).note_revision == note_revision(source)
    assert parse_frontmatter(source).note_revision.startswith("sha256:")
    assert parse_frontmatter(source + b"x").note_revision != parse_frontmatter(source).note_revision


def test_the_body_is_everything_after_the_frontmatter_block() -> None:
    """And it starts at the first body byte — including the empty-block shape,
    where the closing `---` sits at offset 0 of the searched slice and the
    sibling's one-byte-short body start shows up as a leading newline."""
    assert parse_frontmatter(b"---\na: 1\n---\nbody\n").body == b"body\n"
    assert parse_frontmatter(b"---\n---\nbody\n").body == b"body\n"
    assert parse_frontmatter(b"---\r\na: 1\r\n---\r\nbody\r\n").body == b"body\r\n"


def test_a_date_valued_key_is_readable_from_values_but_refused_by_field() -> None:
    """PyYAML resolves an unquoted date to `datetime.date`, which `FieldData`
    does not admit. Asserted so the workaround (`document.values`) is recorded
    rather than rediscovered — every real recipe note has these keys."""
    source = b"---\nfirst_cooked: 2025-08-23\n---\nbody\n"
    document = parse_frontmatter(source)
    assert document.values["first_cooked"] == date(2025, 8, 23)
    with pytest.raises(AmbiguousFrontmatter) as raised:
        document.field("first_cooked")
    assert str(raised.value) == "unsupported_target_frontmatter_shape"


def test_an_unsupported_value_shape_fails_closed() -> None:
    for bad in ("two\nlines", "tab\tchar", {"nested": 1}, [["deep"]]):
        with pytest.raises(FrontmatterError):
            FieldValue("set", bad)  # type: ignore[arg-type]


def test_the_value_caps_hold() -> None:
    with pytest.raises(FrontmatterError):
        FieldValue("set", [f"item-{index}" for index in range(257)])
    with pytest.raises(FrontmatterError):
        FieldValue("set", ["x" * 4_000] * 8)
    with pytest.raises(FrontmatterError):
        FieldValue("set", "x" * 5_000)
    with pytest.raises(FrontmatterError):
        FieldValue("set", float("nan"))


def test_a_field_value_with_an_impossible_state_value_pair_is_refused() -> None:
    for state, value in (("absent", "x"), ("empty", 1), ("set", None)):
        with pytest.raises(FrontmatterError):
            FieldValue(state, value)  # type: ignore[arg-type]

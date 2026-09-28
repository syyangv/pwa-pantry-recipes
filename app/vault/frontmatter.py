"""Loss-minimizing YAML frontmatter reads and single-field byte patches.

**A note is never re-serialized.** Every write in this module is a byte splice
over offsets computed from the original `source`, so a patch to one key leaves
every unrelated byte — comments, key order, inline `INPUT[toggle(...)]` values,
a block list's block style — exactly as the user wrote it. A `yaml.safe_dump`
round-trip would pass the same equality tests on the *values* and still be
wrong here: the daily note is a live Obsidian file that `task-date-recorder`
rewrites on a debounced `modify` event, so a reordered key block is a permanent
whole-file diff, not a cosmetic one.

**A field is three-valued, not two.** `FieldState` is `absent` / `empty` /
`set`, and `empty` is genuinely distinct from `absent`: `调料:` is present and
blank, `调料` missing is absent, and collapsing them loses the difference
between a key the user cleared and a key they never had. `render()` keeps the
distinction too — removing a key and emptying it are different splices.

**Every parse fails closed.** Duplicate keys are rejected at every nesting
level (PyYAML's default is last-one-wins, which would silently drop a value),
as are anchors/aliases, merge keys, flow-style top-level mappings, non-string
keys, and unterminated blocks. `note_revision(source)` is the
optimistic-concurrency identity the atomic store compares against, so a patch
is bound to the exact bytes it was computed from.

Ported from `pwa-obsidian-daily/app/vault/frontmatter.py`; the loss-minimizing
splice discipline, the `_UniqueSafeLoader`, the caps, and the style-following
block-list writer are all preserved.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Literal, cast

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode
from yaml.tokens import AliasToken, AnchorToken

FieldState = Literal["absent", "empty", "set"]
FieldScalar = str | int | float | bool | None
FieldData = FieldScalar | list[FieldScalar]
_MAX_LIST_ITEMS = 256
_MAX_LIST_STRING_CHARACTERS = 30_000
_MAX_SCALAR_CHARACTERS = 4_096
_MAX_SERIALIZED_CHARACTERS = 65_536


class _UniqueSafeLoader(yaml.SafeLoader):
    """SafeLoader variant that rejects duplicate keys at every nesting level."""

    def construct_mapping(self, node: Node, deep: bool = False) -> dict[Any, Any]:
        if not isinstance(node, MappingNode):
            raise ConstructorError(
                None, None, "expected a mapping node", node.start_mark
            )
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable key",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found duplicate key",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


class FrontmatterError(ValueError):
    """Base class for stable frontmatter contract failures."""


class AmbiguousFrontmatter(FrontmatterError):
    """The document cannot be changed without guessing its structure."""


@dataclass(frozen=True)
class FieldValue:
    state: FieldState
    value: FieldData

    def __post_init__(self) -> None:
        if self.state in {"absent", "empty"} and self.value is not None:
            raise FrontmatterError("invalid_field_state_value")
        if self.state == "set" and self.value is None:
            raise FrontmatterError("invalid_field_state_value")
        _validate_value(self.value)


@dataclass(frozen=True)
class FieldSnapshot:
    state: FieldState
    value: FieldData
    revision: str
    note_revision: str


@dataclass
class FrontmatterConflict(FrontmatterError):
    base: FieldValue
    candidate: FieldValue
    current: FieldSnapshot

    def __post_init__(self) -> None:
        super().__init__("frontmatter_conflict")

    @property
    def conflict_revision(self) -> str:
        return self.current.revision

    @property
    def current_note_revision(self) -> str:
        return self.current.note_revision


@dataclass(frozen=True)
class _Entry:
    key_start: int
    patch_start: int
    value_end: int
    delete_end: int
    node: Node


@dataclass(frozen=True)
class FrontmatterDocument:
    """One parsed note: its key spans, its values, and its whole-note revision.

    `body_start` is the byte offset of the first byte **after** the closing
    delimiter, i.e. where the note body begins. It is carried rather than
    recomputed by callers: the closing delimiter is a second `---` plus its
    terminator, and a caller that re-derived that boundary would hold a second
    copy of the delimiter rule that could drift from this one.
    """

    source: bytes
    text: str
    content_start: int
    content_end: int
    body_start: int
    newline: str
    entries: dict[str, _Entry]
    values: dict[str, FieldData]
    note_revision: str

    @property
    def body(self) -> bytes:
        """Everything after the frontmatter block, byte-for-byte."""
        return self.source[self.body_start :]

    def field(self, key: str) -> FieldSnapshot:
        """One key's three-valued state, its value, and both revisions.

        The target node's *shape* is validated here rather than at parse time so
        that an unsupported shape under an unrelated key never makes a whole
        note unreadable — a mapping under `来源` is fine; a mapping under the key
        about to be patched is not.
        """
        _validate_key(key)
        if key not in self.entries:
            value = FieldValue("absent", None)
        else:
            raw = self.values[key]
            _validate_target_node(self.entries[key].node, raw)
            value = FieldValue("empty", None) if raw is None else FieldValue("set", raw)
        return FieldSnapshot(value.state, value.value, _field_revision(value), self.note_revision)

    def render(self, key: str, candidate: FieldValue) -> bytes:
        """The note with `key` set to `candidate`, every other byte untouched.

        An `absent` candidate over an absent key is a no-op, so a caller can
        offer "remove this" unconditionally and let the render decide.
        """
        _validate_key(key)
        entry = self.entries.get(key)
        if candidate.state == "absent":
            if entry is None:
                return self.source
            return self.source[: entry.key_start] + self.source[entry.delete_end :]

        serialized = "" if candidate.state == "empty" else _dump_value(candidate.value)
        encoded = serialized.encode("utf-8")
        if entry is None:
            key_text = _dump_key(key)
            suffix = "" if candidate.state == "empty" else f" {serialized}"
            insertion = f"{key_text}:{suffix}{self.newline}".encode()
            return self.source[: self.content_end] + insertion + self.source[self.content_end :]

        old = self.source[entry.patch_start : entry.value_end]
        block = b"\n" in old or b"\r" in old
        if block:
            indent = " " * entry.node.start_mark.column
            if candidate.state == "set" and isinstance(candidate.value, list):
                # Style-following block lists: a SET list over a block-style
                # span is emitted as YAML block items (the vault's meta-bind
                # shape ``key:\n  - item``), so on/off toggles round-trip
                # byte-identically instead of being rewritten to flow style.
                encoded = _block_list_encoded(candidate.value, self.newline, indent)
            else:
                encoded = (":" + self.newline + indent + serialized).encode("utf-8")
            if old.endswith(b"\r\n"):
                encoded += b"\r\n"
            elif old.endswith((b"\n", b"\r")):
                encoded += old[-1:]
        else:
            encoded = (":" + ("" if candidate.state == "empty" else " " + serialized)).encode(
                "utf-8"
            )
        return self.source[: entry.patch_start] + encoded + self.source[entry.value_end :]


_DELIMITER = re.compile(r"^(?:\ufeff)?---[ \t]*(?:\r\n|\n|\r)")
_CLOSING_DELIMITER = re.compile(
    r"(?:(?<=\n)|(?<=\r)|\A)---[ \t]*(?=\r\n|\n|\r|$)"
)


def note_revision(source: bytes) -> str:
    """The optimistic-concurrency identity of one note's exact bytes."""
    return "sha256:" + hashlib.sha256(source).hexdigest()


def parse_frontmatter(source: bytes, *, max_bytes: int = 2_000_000) -> FrontmatterDocument:
    """Parse one note's frontmatter, failing closed on every malformation."""
    if len(source) > max_bytes:
        raise FrontmatterError("daily_note_too_large")
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FrontmatterError("daily_note_not_utf8") from exc
    opening = _DELIMITER.match(text)
    if opening is None:
        raise AmbiguousFrontmatter("missing_top_level_frontmatter")
    if opening.group(0).endswith("\r\n"):
        newline = "\r\n"
    elif opening.group(0).endswith("\r"):
        newline = "\r"
    else:
        newline = "\n"
    closing = _CLOSING_DELIMITER.search(text[opening.end() :])
    if closing is None:
        raise AmbiguousFrontmatter("unterminated_top_level_frontmatter")
    content_char_start = opening.end()
    content_char_end = content_char_start + closing.start()
    content = text[content_char_start:content_char_end]
    body_char_start = _after_line_terminator(text, content_char_start + closing.end())
    try:
        if any(isinstance(token, (AliasToken, AnchorToken)) for token in yaml.scan(content)):
            raise AmbiguousFrontmatter("anchors_and_aliases_not_supported")
        node = yaml.compose(content, Loader=yaml.SafeLoader)
        loaded = yaml.load(content, Loader=_UniqueSafeLoader)
    except AmbiguousFrontmatter:
        raise
    except yaml.YAMLError as exc:
        raise AmbiguousFrontmatter("malformed_top_level_frontmatter") from exc
    if node is None:
        node = MappingNode("tag:yaml.org,2002:map", [])
        loaded = {}
    if not isinstance(node, MappingNode) or not isinstance(loaded, dict):
        raise AmbiguousFrontmatter("frontmatter_must_be_mapping")
    if node.flow_style:
        raise AmbiguousFrontmatter("flow_style_frontmatter_not_supported")

    entries: dict[str, _Entry] = {}
    values: dict[str, FieldData] = {}
    for key_node, value_node in node.value:
        if (
            not isinstance(key_node, ScalarNode)
            or key_node.tag != "tag:yaml.org,2002:str"
            or not isinstance(key_node.value, str)
        ):
            raise AmbiguousFrontmatter("frontmatter_key_must_be_string")
        key = key_node.value
        _validate_key(key)
        if key == "<<" or key in entries:
            raise AmbiguousFrontmatter("duplicate_or_merged_frontmatter_key")
        try:
            value = loaded[key]
        except (KeyError, TypeError) as exc:
            raise AmbiguousFrontmatter("ambiguous_frontmatter_key") from exc
        start_char = content_char_start + key_node.start_mark.index
        patch_start_char = content_char_start + key_node.end_mark.index
        value_end_char = content_char_start + value_node.end_mark.index
        delete_end_char = _delete_end(text, start_char, value_end_char, content_char_end)
        entries[key] = _Entry(
            _byte_offset(text, start_char),
            _byte_offset(text, patch_start_char),
            _byte_offset(text, value_end_char),
            _byte_offset(text, delete_end_char),
            value_node,
        )
        values[key] = cast(FieldData, value)
    return FrontmatterDocument(
        source,
        text,
        _byte_offset(text, content_char_start),
        _byte_offset(text, content_char_end),
        _byte_offset(text, body_char_start),
        newline,
        entries,
        values,
        note_revision(source),
    )


def patch_frontmatter_field(
    source: bytes,
    *,
    key: str,
    base: FieldSnapshot,
    candidate: FieldValue,
    base_note_revision: str,
    strict_note_revision: bool = False,
    max_bytes: int = 2_000_000,
) -> tuple[bytes, FieldSnapshot]:
    """Splice one field, rebasing over unrelated edits and refusing field edits.

    The default is deliberately permissive about the *note* revision: an
    Obsidian plugin editing an unrelated part of the same note is not a conflict
    the user should have to resolve, and the field's own revision already pins
    the part that matters. `strict_note_revision` binds the whole-note revision
    too, for the explicit-override path where the caller resolved a conflict and
    the current bytes are the only thing it agreed to.
    """
    document = parse_frontmatter(source, max_bytes=max_bytes)
    current = document.field(key)
    expected_base = FieldValue(base.state, base.value)
    if (
        current.revision != base.revision
        or current.state != base.state
        or current.value != base.value
        or (strict_note_revision and current.note_revision != base_note_revision)
    ):
        raise FrontmatterConflict(expected_base, candidate, current)
    rendered = document.render(key, candidate)
    if len(rendered) > max_bytes:
        raise FrontmatterError("daily_note_too_large")
    verified = parse_frontmatter(rendered, max_bytes=max_bytes).field(key)
    if not field_state_equivalent(candidate, verified):
        raise FrontmatterError("frontmatter_patch_verification_failed")
    return rendered, verified


def field_state_equivalent(candidate: FieldValue, verified: FieldSnapshot) -> bool:
    """True when the verified field matches the candidate, accepting the
    engine's empty-list equivalence: an empty list written over a block-style
    span is the bare empty key (state ``empty``), which callers treat
    identically to ``set []`` (both normalize to no items).
    """
    if candidate.state == verified.state and candidate.value == verified.value:
        return True
    return (
        candidate.state == "set"
        and candidate.value == []
        and verified.state == "empty"
        and verified.value is None
    )


def _field_revision(value: FieldValue) -> str:
    canonical = json.dumps(
        {"state": value.state, "value": value.value},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def field_revision(value: FieldValue) -> str:
    """The public state/value hash, for values a caller holds outside a note."""
    return _field_revision(value)


def _validate_key(key: str) -> None:
    if (
        not isinstance(key, str)
        or not key
        or "\n" in key
        or "\r" in key
        or "\x00" in key
        or key == "<<"
    ):
        raise FrontmatterError("invalid_frontmatter_key")


def _validate_value(value: Any) -> None:
    scalars = (str, int, float, bool, type(None))
    if isinstance(value, list):
        if len(value) > _MAX_LIST_ITEMS:
            raise FrontmatterError("unsupported_frontmatter_value")
        if sum(len(item) for item in value if isinstance(item, str)) > _MAX_LIST_STRING_CHARACTERS:
            raise FrontmatterError("unsupported_frontmatter_value")
        if any(isinstance(item, (list, dict)) or not isinstance(item, scalars) for item in value):
            raise FrontmatterError("unsupported_frontmatter_value")
        for item in value:
            _validate_value(item)
        return
    if isinstance(value, dict) or not isinstance(value, scalars):
        raise FrontmatterError("unsupported_frontmatter_value")
    if isinstance(value, str) and any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise FrontmatterError("unsupported_frontmatter_value")
    if isinstance(value, str) and len(value) > _MAX_SCALAR_CHARACTERS:
        raise FrontmatterError("unsupported_frontmatter_value")
    if isinstance(value, float) and not math.isfinite(value):
        raise FrontmatterError("unsupported_frontmatter_value")
    if isinstance(value, int) and not isinstance(value, bool) and len(str(abs(value))) > 128:
        raise FrontmatterError("unsupported_frontmatter_value")


def _validate_target_node(node: Node, value: Any) -> None:
    if not isinstance(node, (ScalarNode, SequenceNode)):
        raise AmbiguousFrontmatter("unsupported_target_frontmatter_shape")
    if isinstance(node, SequenceNode) and any(
        not isinstance(item, ScalarNode) for item in node.value
    ):
        raise AmbiguousFrontmatter("unsupported_target_frontmatter_shape")
    try:
        _validate_value(value)
    except FrontmatterError as exc:
        raise AmbiguousFrontmatter("unsupported_target_frontmatter_shape") from exc


def _dump_value(value: FieldData) -> str:
    _validate_value(value)
    rendered = yaml.safe_dump(
        value,
        allow_unicode=True,
        default_flow_style=True,
        width=_MAX_SERIALIZED_CHARACTERS,
    ).strip()
    if rendered.endswith("\n..."):
        rendered = rendered[:-4]
    if (
        "\n" in rendered
        or "\r" in rendered
        or len(rendered) > _MAX_SERIALIZED_CHARACTERS
    ):
        raise FrontmatterError("unsupported_frontmatter_value")
    return rendered


def _block_list_encoded(value: list[Any], newline: str, indent: str) -> bytes:
    """Byte encoding of a SET list written over a block-style span.

    ``key:`` + ``<newline>`` + one ``<indent>- <item>`` line per item; an
    empty list is the bare ``key:`` (the vault template's bare form, never
    ``[]``). Each item is a single-line YAML block scalar so quoting matches
    safe YAML; the caller appends the existing value span's trailing newline
    style after the last item.
    """
    if not value:
        return b":"
    items = newline.join(
        f"{indent}- {_dump_block_item(item)}" for item in value
    )
    return (":" + newline + items).encode("utf-8")


def _dump_block_item(value: Any) -> str:
    """One YAML block-list item: ``yaml.safe_dump`` with block style, trailing
    newline stripped. Fail closed when the item cannot be represented as a
    single-line block scalar (embedded newlines/control characters or an
    oversized serialization) — same posture as ``_dump_value``.
    """
    _validate_value(value)
    rendered = yaml.safe_dump(
        value,
        allow_unicode=True,
        default_flow_style=False,
        width=_MAX_SERIALIZED_CHARACTERS,
    ).strip()
    if rendered.endswith("\n..."):
        rendered = rendered[:-4]
    if (
        "\n" in rendered
        or "\r" in rendered
        or len(rendered) > _MAX_SERIALIZED_CHARACTERS
    ):
        raise FrontmatterError("unsupported_frontmatter_value")
    return rendered


def _dump_key(key: str) -> str:
    # Redundant in the sibling (it inlined this call); kept as the one place a
    # key is serialized, so a key-quoting rule has a single site to change.
    return _dump_value(key)


def _byte_offset(text: str, char_offset: int) -> int:
    return len(text[:char_offset].encode("utf-8"))


def _after_line_terminator(text: str, char_offset: int) -> int:
    """Skip one line terminator if one is there.

    `_CLOSING_DELIMITER` ends *after* its `---[ \t]*` and only looks ahead at the
    terminator, so the match's end is the start of the body line, not its end.
    Skipping the terminator here is what makes `body` correct for an **empty**
    frontmatter block (`---\n---\nbody`), where the closing `---` is matched at
    the very start of the searched slice. The sibling's `_body_start` omits this
    skip, so its body begins one byte early in exactly that case; here the skip
    is unconditional and correct in both shapes.
    """
    if text[char_offset : char_offset + 2] == "\r\n":
        return char_offset + 2
    if text[char_offset : char_offset + 1] in {"\n", "\r"}:
        return char_offset + 1
    return char_offset


def _delete_end(text: str, start: int, value_end: int, limit: int) -> int:
    if value_end > start and text[value_end - 1 : value_end] in {"\n", "\r"}:
        return value_end
    match = re.search(r"\r\n|\n|\r", text[value_end:limit])
    return limit if match is None else value_end + match.end()

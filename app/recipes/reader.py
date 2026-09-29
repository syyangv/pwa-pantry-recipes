"""The recipe index: a read projection of `<RECIPES_ROOT>/*.md` into `RecipeNote`.

A Recipe is a *read* projection (spec §9.5). This module never opens the vault
for writing, never advances a Recipe Cooking History field, and never groups
Cooking History by meal slot — `Helper/utils/recipeTracker.md` owns those nine
auto frontmatter fields, and the Cooking Log write path (#17) is the only thing
that ever moves them. There is no write surface here to misuse.

Three things the real data forces, and which the code below is shaped by:

- **A blank value is a value.** `调料:`, `时长（分钟）:` and `来源:` are routinely
  present with nothing after the colon, and `烹饪工具:` appears both as a scalar
  (`炒锅`) and as a block list (`- 煮锅`). Present-but-blank yields an **empty
  list**; an absent key yields the same empty list. Neither is an error, and
  neither is a crash: `Recipes.md`, the vault's own index page, carries no
  `材料` at all.
- **`note_name` is the basename, never a display alias.** D2 requires the cook
  wikilink text to equal the note's basename exactly, so the wikilink and the
  `⚠ 已重命名` drift check of §9.12 both read this one field. The alias lives in
  the filename itself (`Easy Fragrant Fried Rice`), not in a separate key, which
  is precisely why a hand-typed display name cannot drift away from it.
- **One bad note must not blank the list.** A note that cannot be read as a
  Recipe is *skipped and counted*, never fatal, and the count is carried on the
  snapshot so `/health` and the provenance view can surface it. A silent drop
  is the failure this shape exists to prevent.

**`材料` / `调料` values are handed on as `(index, raw)` pairs, not as parsed
names.** `app.recipes.ingredients.parse_ingredient_value()` is the single
parser for all five observed shapes — including the three-codepoint ZWJ key
`🍋‍🟩` under F15 — and this module deliberately does not call it, so that the
value that reaches the matcher is the byte-for-byte `raw` and the parse happens
exactly once, at the point of use. Re-parsing here and storing a name would put
a second, drifting copy of §9.6 in the read path.

**A missing `RECIPES_ROOT` raises `ConfigurationError`; it is not an empty
index.** `Settings` validates that `recipes_root` is a safe *relative* path but
cannot validate that the folder exists at startup, and an empty recipe list is
indistinguishable from "the operator pointed `RECIPES_ROOT` at the wrong
folder". Failing closed is the repo's third non-negotiable.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Final

import yaml

from ..config import ConfigurationError, Settings

#: The `材料` frontmatter key — the Recipe's Ingredients.
MATERIALS_KEY: Final = "材料"
#: The `调料` frontmatter key — the Recipe's Seasonings, never flattened into
#: `材料` (CONTEXT.md's Seasoning entry; flattening inflates the headline).
SEASONINGS_KEY: Final = "调料"
#: The `烹饪工具` frontmatter key — Cooking Tools, requirements and not stock.
TOOLS_KEY: Final = "烹饪工具"
#: The `来源` frontmatter key — where the recipe came from. Hand-authored, and
#: **list-shaped in 13 of the 16 real notes** with only `烤小辣椒` and `番茄炒蛋`
#: carrying a bare scalar, so anything that reads it as a string drops the source
#: of most of the vault on the floor.
SOURCE_KEY: Final = "来源"
#: The `时长（分钟）` frontmatter key — cook time in minutes. An int in 14 of 16,
#: present-but-blank in `烤土豆` and `花蛤拌饭`.
DURATION_KEY: Final = "时长（分钟）"

#: The nine frontmatter fields `Helper/utils/recipeTracker.md` maintains. Read
#: only: the PWA never writes them and never recomputes them. They are named
#: here as a closed set, not inferred, so the index can tell a tracker-owned
#: field from a hand-authored one by key rather than by guessing at shapes.
AUTO_HISTORY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "first_cooked",
        "last_cooked",
        "cooking_count",
        "cooking_frequency",
        "cooking_years",
        "recent_activity",
        "favorite_season",
        "cooking_patterns",
        "auto_updated",
    }
)

#: The number of `<RECIPES_ROOT>/*.md` files one scan will enumerate. Mirrors
#: the ported store's own bound; exceeding it is an error, not a truncation.
MAX_RECIPE_FILES: Final = 500

_OPENING_DELIMITER: Final = re.compile(r"\A(?:\ufeff)?---[ \t]*(?:\r\n|\n|\r)")
_CLOSING_DELIMITER: Final = re.compile(r"(?:(?<=\n)|(?<=\r)|\A)---[ \t]*(?=\r\n|\n|\r|$)")
#: The `… 步骤` heading, at any ATX level: 15 of the 16 real notes write
#: `# 3 步骤`, and 烤鲭鱼 writes its neighbouring 制作记录 heading as `##`.
_STEPS_HEADING_RE: Final = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+[^\n]*步骤[^\n]*$")
_MARKDOWN_SUFFIX: Final = ".md"


class RecipeNoteError(ValueError):
    """One note could not be read as a Recipe.

    Raised per note, never per index: `RecipeIndex` catches it, counts the
    note as skipped, and continues with the rest of the folder.
    """


class RecipeIndexError(ValueError):
    """The folder could not be enumerated — a bound was exceeded.

    Distinct from `RecipeNoteError` because it says nothing about any one
    note's content: the scan itself did not complete, so returning a partial
    index would misrepresent the vault.
    """


@dataclass(frozen=True)
class IngredientEntry:
    """One `材料` / `调料` value: its position in the frontmatter list, verbatim.

    `index` is the zero-based position, which is what `ingredient_mappings`
    keys its per-slot recovery on (F2). `raw` is byte-for-byte the frontmatter
    value and is the audit anchor — the value `parse_ingredient_value()` takes.
    """

    index: int
    raw: str


@dataclass(frozen=True)
class RecipeCookingHistory:
    """The tracker-owned aggregate frontmatter of one Recipe (CONTEXT.md).

    Every field is optional because a note may carry none of them: a Recipe
    that has never been cooked has no history, and that is a state, not a
    defect.

    `auto_updated` stays the **verbatim frontmatter text**, not a `datetime`,
    and that asymmetry with `first_cooked` is the data's, not a shortcut.
    PyYAML resolves `2025-07-24` to a `date` but leaves `2025-09-22 23:55` as a
    string, because a space is not the `T` its timestamp resolver accepts. The
    stamp is a provenance marker the PWA never writes and never recomputes, so
    parsing it would buy type convenience at the price of a failure mode that
    blanks an otherwise-perfect recipe over a cosmetic field.
    """

    first_cooked: date | None = None
    last_cooked: date | None = None
    cooking_count: int | None = None
    cooking_frequency: int | None = None
    cooking_years: tuple[str, ...] = ()
    recent_activity: int | None = None
    favorite_season: str | None = None
    cooking_patterns: tuple[str, ...] = ()
    auto_updated: str | None = None


@dataclass(frozen=True)
class RecipeNote:
    """One recipe note, projected. Never written back.

    `note_name` is the basename without the `.md` suffix — the exact text D2's
    `[[wikilink]]` must carry. `note_path` is the vault-relative path, which is
    what the mapping table stores.
    """

    note_name: str
    note_path: str
    ingredients: tuple[IngredientEntry, ...]
    seasonings: tuple[IngredientEntry, ...]
    tools: tuple[str, ...]
    #: `来源`, as the same `(index, raw)`-free list of plain strings `tools` is.
    #: A tuple because it is a list in 13 of 16 real notes and a scalar in 2, and
    #: a consumer that had to branch on which would branch on the wrong thing.
    source: tuple[str, ...]
    #: `时长（分钟）`, or `None` for both "absent" and "present but blank". `None`
    #: is `int | None` rather than a sentinel: it is the same distinction the
    #: history fields make, and 0 is a real (if silly) cook time, so a sentinel
    #: would have to be something nobody would ever type.
    duration_minutes: int | None
    steps: str
    history: RecipeCookingHistory


@dataclass(frozen=True)
class RecipeSnapshot:
    """One materialization of the folder.

    `skipped` is the number of notes dropped for being unreadable. It is part
    of the snapshot rather than a log line, because §9.5 requires the count to
    reach `/health` and the provenance view: a note that vanished from the list
    without a number attached is indistinguishable from a note that was never
    there.
    """

    notes: tuple[RecipeNote, ...]
    skipped: int


def parse_recipe(
    source: bytes,
    *,
    note_name: str,
    note_path: str,
    max_bytes: int = 2_000_000,
) -> RecipeNote:
    """Project one note's bytes into a `RecipeNote`.

    `note_name` and `note_path` cannot be recovered from the bytes — a note
    does not contain its own filename — so the caller supplies them, and the
    index supplies the basename so D2's wikilink text cannot drift.

    Raises `RecipeNoteError` for anything that makes the note unreadable as a
    Recipe: too large, not UTF-8, no frontmatter block, an unterminated or
    unparseable block, a non-mapping block, a non-string key, a duplicated key,
    or a list entry that is not a string.
    """
    frontmatter, body = _split_frontmatter(source, max_bytes=max_bytes)
    return _project(frontmatter, body, note_name=note_name, note_path=note_path)


class RecipeIndex:
    """A TTL snapshot provider over `<RECIPES_ROOT>/*.md` (spec §9.5).

    Construction reads nothing; the first `snapshot()` builds the tuple, and
    later calls are served from it until `recipe_cache_seconds` has elapsed on
    a monotonic clock. `refresh()` forces a rebuild and `invalidate()` drops
    the cache so the next read rebuilds — `invalidate()` is what a write that
    could change the index calls. The display sort is not applied here: order
    is vault enumeration order, which on APFS is creation order, and D4 sorts
    client-side.
    """

    def __init__(self, settings: Settings, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._root_relative: str = settings.recipes_root
        self._root: Path = settings.vault_path / settings.recipes_root
        self._index_name: str = PurePosixPath(settings.recipes_root).name
        self._ttl: float = settings.recipe_cache_seconds
        self._max_bytes: int = settings.max_recipe_bytes
        self._clock: Callable[[], float] = clock
        self._cached: RecipeSnapshot | None = None
        self._loaded_at: float = 0.0

    def snapshot(self) -> RecipeSnapshot:
        """The current snapshot, rebuilding it once the TTL has elapsed."""
        cached = self._cached
        if cached is not None and (self._clock() - self._loaded_at) < self._ttl:
            return cached
        return self.refresh()

    def notes(self) -> tuple[RecipeNote, ...]:
        """The current recipes, in vault enumeration order."""
        return self.snapshot().notes

    def refresh(self) -> RecipeSnapshot:
        """Re-read the folder unconditionally and replace the cache."""
        snapshot = self._scan()
        self._cached = snapshot
        self._loaded_at = self._clock()
        return snapshot

    def invalidate(self) -> None:
        """Drop the cache so the next read rebuilds it.

        Called after any write that could change the folder. Today no write in
        this app can — the Cooking Log write touches a daily note, never a
        Recipe — so this exists for the call sites that will, not for one that
        does.
        """
        self._cached = None
        self._loaded_at = 0.0

    def close(self) -> None:
        """A documented no-op: the index holds no descriptors and no handles.

        The ported store pins directory descriptors and therefore needs a
        `close()`. This index holds a resolved `Path` and a cached tuple, both
        of which the garbage collector reclaims, so there is nothing to
        release — and inventing a lock or an open file here would create the
        leak the method is meant to document the absence of.
        """
        return None

    def _scan(self) -> RecipeSnapshot:
        notes: list[RecipeNote] = []
        skipped = 0
        for name in self._list_markdown():
            note_name = name[: -len(_MARKDOWN_SUFFIX)]
            try:
                source = (self._root / name).read_bytes()
                frontmatter, body = _split_frontmatter(source, max_bytes=self._max_bytes)
            except (OSError, RecipeNoteError):
                skipped += 1
                continue
            if not _is_recipe_note(frontmatter, note_name, self._index_name):
                continue
            try:
                notes.append(
                    _project(
                        frontmatter,
                        body,
                        note_name=note_name,
                        note_path=f"{self._root_relative}/{name}",
                    )
                )
            except RecipeNoteError:
                skipped += 1
        return RecipeSnapshot(notes=tuple(notes), skipped=skipped)

    def _list_markdown(self) -> tuple[str, ...]:
        """Direct `.md` children of `RECIPES_ROOT`, in enumeration order.

        Only a `.md` suffix is admitted, so a `.base` view can never reach the
        index however it is named. Dot-prefixed, symlinked, and non-regular
        entries are skipped, matching the ported store's scan.
        """
        try:
            with os.scandir(self._root) as entries:
                candidates = tuple(entries)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise ConfigurationError("missing_recipes_root") from exc
        except OSError as exc:
            raise ConfigurationError("unreadable_recipes_root") from exc
        names: list[str] = []
        for entry in candidates:
            name = entry.name
            if name.startswith(".") or not name.lower().endswith(_MARKDOWN_SUFFIX):
                continue
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            names.append(name)
            if len(names) > MAX_RECIPE_FILES:
                raise RecipeIndexError("recipe_directory_limit")
        return tuple(names)


def _split_frontmatter(source: bytes, *, max_bytes: int) -> tuple[dict[str, Any], str]:
    """Split `source` into its frontmatter mapping and its body.

    Fail-closed on every malformation rather than returning a partial mapping:
    a silently half-read frontmatter would drop an Ingredient, and a recipe
    with a missing Ingredient scores one point too high against a headline it
    does not deserve.
    """
    if len(source) > max_bytes:
        raise RecipeNoteError("recipe_note_too_large")
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RecipeNoteError("recipe_note_not_utf8") from exc
    opening = _OPENING_DELIMITER.match(text)
    if opening is None:
        raise RecipeNoteError("missing_top_level_frontmatter")
    remainder = text[opening.end() :]
    closing = _CLOSING_DELIMITER.search(remainder)
    if closing is None:
        raise RecipeNoteError("unterminated_top_level_frontmatter")
    block = remainder[: closing.start()]
    body = remainder[closing.end() :]
    try:
        loaded = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        raise RecipeNoteError("malformed_top_level_frontmatter") from exc
    if loaded is None:
        return {}, body
    if not isinstance(loaded, dict):
        raise RecipeNoteError("frontmatter_must_be_mapping")
    _reject_duplicate_keys(block)
    fields: dict[str, Any] = {}
    for key, value in loaded.items():
        if not isinstance(key, str):
            raise RecipeNoteError("frontmatter_key_must_be_string")
        fields[key] = value
    return fields, body


def _reject_duplicate_keys(block: str) -> None:
    """Refuse a repeated top-level key, which `safe_load` would silently resolve.

    The last occurrence wins inside PyYAML, so a note with two `材料:` blocks
    would read as the second one and the first would disappear without a trace.
    The composed node is the only place both are still visible.
    """
    try:
        node = yaml.compose(block, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        raise RecipeNoteError("malformed_top_level_frontmatter") from exc
    if node is None or not isinstance(node, yaml.MappingNode):
        return
    seen: set[str] = set()
    for key_node, _ in node.value:
        if not isinstance(key_node, yaml.ScalarNode) or not isinstance(key_node.value, str):
            raise RecipeNoteError("frontmatter_key_must_be_string")
        if key_node.value in seen:
            raise RecipeNoteError("duplicate_frontmatter_key")
        seen.add(key_node.value)


def _project(
    frontmatter: Mapping[str, Any],
    body: str,
    *,
    note_name: str,
    note_path: str,
) -> RecipeNote:
    """Build a `RecipeNote` from an already-split note."""
    return RecipeNote(
        note_name=note_name,
        note_path=note_path,
        ingredients=_entries(frontmatter.get(MATERIALS_KEY)),
        seasonings=_entries(frontmatter.get(SEASONINGS_KEY)),
        tools=tuple(entry.raw for entry in _entries(frontmatter.get(TOOLS_KEY))),
        source=tuple(entry.raw for entry in _entries(frontmatter.get(SOURCE_KEY))),
        duration_minutes=_duration(frontmatter.get(DURATION_KEY)),
        steps=_steps(body),
        history=_history(frontmatter),
    )


def _is_recipe_note(frontmatter: Mapping[str, Any], note_name: str, index_name: str) -> bool:
    """True when this note is a Recipe rather than the folder's index page.

    Two conditions, both necessary. A `材料` or `调料` key is what makes a note
    a recipe at all, and it is the same discriminator
    `Bases/Recipes-20250823.base` uses (`file.basename != "Recipes"`) for the
    index page: `Recipes.md` is the vault's table of contents, it carries no
    `材料`, and a folder's own index page is not one of its contents. The
    basename is compared rather than the `Recipes` literal, so a relocated
    `RECIPES_ROOT` keeps excluding its own index.
    """
    if note_name == index_name:
        return False
    return MATERIALS_KEY in frontmatter or SEASONINGS_KEY in frontmatter


def _duration(value: Any) -> int | None:
    """`时长（分钟）` as an int, or `None`.

    **This is deliberately more forgiving than `_entries`, and the asymmetry is
    the point.** `材料`, `调料`, `烹饪工具` and `来源` are lists of strings that
    feed the matcher or the audit anchor, so a non-string item there is a
    malformation and `RecipeIndex` skips the note — loudly, via `skipped`. A cook
    time is a **cosmetic** field: it is displayed and never scored, never joined,
    and never an input to anything. `RecipeCookingHistory` already sets the rule
    this follows, in the reason it keeps `auto_updated` as a string — "a cosmetic
    field must never blank a recipe" — and a recipe that vanished from the list
    because someone typed `时长: 半小时` would be the loudest possible way to
    ignore a typo.

    So: an absent key, a present-but-blank key, a list, a mapping, and a numeric
    *string* all read as `None`; a real `int` reads as itself. A `bool` is refused
    because `isinstance(True, int)` is true in Python and `时长: true` is a
    YAML mistake, not a one-minute recipe.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    # A quoted `时长: "30"` is the one coercion worth making: it is unambiguous,
    # it is what three of the real notes would parse to if anyone had quoted
    # them, and it cannot turn a non-numeric string into a number.
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _entries(value: Any) -> tuple[IngredientEntry, ...]:
    """One frontmatter list as `(index, raw)` pairs.

    Three observed shapes: a block list, a scalar (`烹饪工具: 炒锅`), and a
    present-but-blank key. All three are ordinary; a non-string item inside a
    list is not, and is a malformation rather than something to coerce —
    `str()` of a nested mapping would put a Python repr into the audit anchor.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        return (IngredientEntry(0, value),)
    if not isinstance(value, list):
        raise RecipeNoteError("unsupported_frontmatter_list_shape")
    entries: list[IngredientEntry] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise RecipeNoteError("non_string_frontmatter_list_entry")
        entries.append(IngredientEntry(index, item))
    return tuple(entries)


def _steps(body: str) -> str:
    """The body after the **last** `步骤` heading, verbatim.

    "Last" because a note may legitimately have more than one, and the
    authoritative one is the final one. Verbatim because a Recipe is a read
    projection and rewriting a step is the one thing this app must never do —
    so nothing is stripped, reordered, or re-indented, and the newline that
    terminated the heading is part of the result. A note with no such heading
    has no steps, which is `""` and not an error.
    """
    matches = list(_STEPS_HEADING_RE.finditer(body))
    if not matches:
        return ""
    return body[matches[-1].end() :]


def _history(frontmatter: Mapping[str, Any]) -> RecipeCookingHistory:
    """Read only the nine tracker-owned keys, and nothing else.

    Reading the set by name rather than copying whatever the note happens to
    carry is what keeps the two frontmatter classes apart: a hand-authored key
    cannot leak into Recipe Cooking History, and a tracker field cannot be
    mistaken for hand-authored content.
    """
    return RecipeCookingHistory(
        first_cooked=_as_date(frontmatter.get("first_cooked")),
        last_cooked=_as_date(frontmatter.get("last_cooked")),
        cooking_count=_as_int(frontmatter.get("cooking_count")),
        cooking_frequency=_as_int(frontmatter.get("cooking_frequency")),
        cooking_years=_as_text_tuple(frontmatter.get("cooking_years")),
        recent_activity=_as_int(frontmatter.get("recent_activity")),
        favorite_season=_as_text(frontmatter.get("favorite_season")),
        cooking_patterns=_as_text_tuple(frontmatter.get("cooking_patterns")),
        auto_updated=_as_text(frontmatter.get("auto_updated")),
    )


def _as_date(value: Any) -> date | None:
    """A `first_cooked` / `last_cooked` date, tolerant of the quoting.

    PyYAML resolves an unquoted `2025-07-24` to a `date`; a quoted one stays a
    string, which is a perfectly ordinary thing for a hand-edited vault to
    contain, so an ISO string is parsed rather than refused. Anything else is a
    real malformation — a cooking date the app cannot place on a calendar.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise RecipeNoteError("invalid_cooking_history_date") from exc
    raise RecipeNoteError("invalid_cooking_history_date")


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise RecipeNoteError("invalid_cooking_history_count")
    return int(value)


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool | int | float):
        return str(value)
    raise RecipeNoteError("invalid_cooking_history_text")


def _as_text_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list):
        raise RecipeNoteError("invalid_cooking_history_list")
    items: list[str] = []
    for item in value:
        if isinstance(item, str):
            items.append(item)
        elif isinstance(item, int) and not isinstance(item, bool):
            items.append(str(item))
        else:
            raise RecipeNoteError("invalid_cooking_history_list_entry")
    return tuple(items)

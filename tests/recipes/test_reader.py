"""The recipe index and the note parser, over the 16 real recipe notes.

Test seam 2 (spec §4.1): `parse_recipe` is pure over bytes and `RecipeIndex`
reads one folder, so nothing here needs an app, an HTTP client, or the pantry
catalog. Every test builds its vault from the committed
`tests/fixtures/real_recipes/` snapshot inside `tmp_path` — **no test reads or
writes the real vault.** The snapshot is the point: the parser's tolerance is
set by what the 16 notes actually contain, so a fixture that drifted away from
the vault would make the tolerance a fiction.

What the tests pin, in the order the decisions were made:

- `note_name` is the **basename**, not a display alias (D2) — the wikilink
  text and the `⚠ 已重命名` drift check both read this one field.
- A malformed note is **skipped and counted**, never fatal, and the count
  rides on the snapshot so `/health` can surface it.
- `steps` is the body after the last `步骤` heading, **verbatim**: blank lines,
  a bare URL, and the newline that ended the heading are all still there.
- `材料` values reach the matcher as byte-for-byte `raw`, and the shape work
  stays in `parse_ingredient_value()` — asserted here by feeding the indexed
  values back through that one parser and checking all five shapes plus the
  three-codepoint ZWJ key.
- The nine `recipeTracker` fields are read, never written, and a
  hand-authored key can never leak into Recipe Cooking History.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path

import pytest

from app.config import ConfigurationError, Settings
from app.recipes import reader
from app.recipes.ingredients import parse_ingredient_value
from app.recipes.reader import (
    AUTO_HISTORY_KEYS,
    IngredientEntry,
    RecipeCookingHistory,
    RecipeIndex,
    RecipeIndexError,
    RecipeNote,
    RecipeNoteError,
    parse_recipe,
)

REAL_RECIPES = Path(__file__).resolve().parent.parent / "fixtures" / "real_recipes"
RECIPES_RELATIVE = "Hobbies/做饭/Recipes"
FROZEN_NOTE_COUNT = 16

#: A hand-written recipe carrying Seasonings but no `材料` at all.
SEASONINGS_ONLY = """---
调料:
  - 生抽
---
# 4 步骤
- 拌
""".encode()
#: Valid frontmatter around a value that is not valid UTF-8.
NOT_UTF8 = b"---\n" + "材料: ".encode() + b"\xff\xfe\n---\n"


def _seed_vault(settings: Settings, names: list[str] | None = None) -> Path:
    """Copy the frozen notes into the isolated vault; return the recipes folder."""
    root = settings.vault_path / RECIPES_RELATIVE
    selected = sorted(REAL_RECIPES.glob("*.md")) if names is None else [
        REAL_RECIPES / f"{name}.md" for name in names
    ]
    for source in selected:
        shutil.copyfile(source, root / source.name)
    return root


def _parse(settings: Settings, name: str) -> RecipeNote:
    source = (REAL_RECIPES / f"{name}.md").read_bytes()
    return parse_recipe(
        source,
        note_name=name,
        note_path=f"{RECIPES_RELATIVE}/{name}.md",
        max_bytes=settings.max_recipe_bytes,
    )


def _one(index: RecipeIndex, note_name: str) -> RecipeNote:
    matches = [note for note in index.notes() if note.note_name == note_name]
    assert len(matches) == 1, f"expected exactly one {note_name}, got {len(matches)}"
    return matches[0]


# --- the parsed note shape ------------------------------------------------


def test_note_name_is_the_basename_not_a_display_alias(settings: Settings) -> None:
    """D2: the cook wikilink text equals the note's basename exactly.

    The vault keeps an English display name *in* the filename, so there is no
    second key that could drift away from it — which is the property the
    `⚠ 已重命名` check of §9.12 depends on.
    """
    note = _parse(settings, "Easy Fragrant Fried Rice")
    assert note.note_name == "Easy Fragrant Fried Rice"
    assert note.note_path == f"{RECIPES_RELATIVE}/Easy Fragrant Fried Rice.md"


def test_parse_recipe_carries_every_field(settings: Settings) -> None:
    note = _parse(settings, "盐焗鸡")
    assert note.ingredients == (IngredientEntry(0, "🐔/带皮鸡腿"),)
    assert note.seasonings == (
        IngredientEntry(0, "窑鸡粉"),
        IngredientEntry(1, "生抽"),
        IngredientEntry(2, "🫚"),
        IngredientEntry(3, "葱"),
        IngredientEntry(4, "老抽"),
    )
    assert note.tools == ("电饭煲", "空气炸锅")
    # `来源` is a BLOCK LIST in this fixture, which is the shape 13 of the 16
    # real notes use and the one a string-only reader drops.
    assert note.source == ("小红书",)
    assert note.duration_minutes == 40
    assert note.steps == (
        "\n- 开水烫鸡皮\n"
        "- +姜片+葱+生抽+老抽+油抹匀，腌过夜\n"
        "- 蒸：煮饭的时候用支架架在电饭煲里，快速煮饭模式\n"
        "- 或煎：空气炸锅355F 12min，365F 8min，broil low 3min\n"
    )
    assert note.history == RecipeCookingHistory(
        first_cooked=date(2025, 8, 23),
        last_cooked=date(2026, 3, 10),
        cooking_count=27,
        cooking_frequency=7,
        cooking_years=("2025", "2026"),
        recent_activity=5,
        favorite_season="冬季",
        cooking_patterns=("频繁制作", "常做菜品", "经典菜谱", "长期收藏"),
        auto_updated="2026-03-11 22:54",
    )


# --- the five `材料` / `调料` value shapes ---------------------------------


@pytest.mark.parametrize(
    ("note_name", "slot", "expected_name", "expected_method"),
    [
        # 1 — bare resolvable name.
        ("拌空心菜", 0, "空心菜", "bare"),
        ("微波菜菜", 0, "Kale", "bare"),
        # 2 — an explicit wikilink carrying literal quotes.
        ("烤鲭鱼", 0, "Mackerel", "wikilink"),
        # 3 — emoji/alt pair.
        ("烤小辣椒", 0, "Shishito", "emoji_alt"),
        ("花蛤拌饭", 1, "香菇", "emoji_alt"),
        # 4 — emoji only, named from the explicit key set (F15).
        ("烤土豆", 0, "土豆", "emoji_only"),
        ("Easy Fragrant Fried Rice", 0, "西兰花", "emoji_only"),
        # 5 — `X/Y` with no leading emoji; the left side is the primary.
        ("Paradiso三明治", 3, "香料", "alt_pair"),
    ],
)
def test_indexed_values_parse_through_the_one_shared_parser(
    settings: Settings,
    note_name: str,
    slot: int,
    expected_name: str,
    expected_method: str,
) -> None:
    """The index hands on `raw`; `parse_ingredient_value()` is the only parser.

    Asserting the round trip is what proves the index has not grown a second
    interpretation of a value, and it is the reason `Ingredients` arrive as
    `(index, raw)` rather than as a name this module computed and cached.
    """
    note = _parse(settings, note_name)
    entry = note.ingredients[slot]
    parsed = parse_ingredient_value(entry.raw)
    assert parsed.parsed_name == expected_name
    assert parsed.parse_method == expected_method
    assert parsed.raw == entry.raw


def test_the_zwj_seasoning_survives_the_index(settings: Settings) -> None:
    """`🍋‍🟩` is `U+1F34B U+200D U+1F7E9` and must reach the parser as one unit.

    F15: a Unicode property class matches the three parts, not the run. If the
    index normalized or trimmed the value, the sequence would be split here
    instead of at the parser that knows how to name it.
    """
    note = _parse(settings, "烤鲭鱼")
    lime = next(entry for entry in note.seasonings if entry.raw.startswith("🍋"))
    assert lime.raw == "🍋‍🟩"
    assert parse_ingredient_value(lime.raw).parsed_name == "青柠"


def test_ingredient_indexes_are_their_frontmatter_positions(settings: Settings) -> None:
    """`index` is the zero-based slot, which is what F2's per-slot catch keys on."""
    note = _parse(settings, "花蛤拌饭")
    assert [entry.index for entry in note.ingredients] == [0, 1, 2, 3]
    assert [entry.raw for entry in note.ingredients] == [
        "Clam",
        "🍄/香菇",
        "🌶️/Shishito",
        "空心菜",
    ]


# --- `烹饪工具`: both observed shapes, and a blank value -------------------


def test_tools_parses_both_observed_shapes(settings: Settings) -> None:
    assert _parse(settings, "番茄炒蛋").tools == ("炒锅",)  # scalar `炒锅`
    assert _parse(settings, "盐焗鸡").tools == ("电饭煲", "空气炸锅")  # block list
    assert _parse(settings, "茶碗蒸").tools == ("蒸锅",)  # block list, one item


def test_a_blank_frontmatter_value_is_an_empty_list_not_an_error(settings: Settings) -> None:
    """`烹饪工具:` with nothing after it is a value, not a missing key."""
    assert _parse(settings, "烤土豆").tools == ()


@pytest.mark.parametrize("note_name", ["微波菜菜", "烤红薯", "煮菜菜", "烤土豆"])
def test_blank_seasonings_is_an_empty_list(settings: Settings, note_name: str) -> None:
    """Four of the 16 notes carry a blank `调料:` and every one of them parses."""
    assert _parse(settings, note_name).seasonings == ()


def test_steps_keep_every_byte_after_the_last_heading(settings: Settings) -> None:
    """Verbatim means verbatim: blank lines and a trailing bare URL included.

    番茄炒蛋 is the hard case — three blank lines, then a list, then two more
    blank lines, then a link with query string. Anything that "tidied" the body
    would be rewriting a Recipe, which is the one thing this app must not do.
    """
    assert _parse(settings, "番茄炒蛋").steps == (
        "\n\n\n- 一个番茄切丁\n- 一个番茄切块\n- 打蛋\n- 炒蛋\n"
        "- 炒葱+蕃茄丁 + 蛋 + 番茄块\n- 生抽+醋\n\n\n"
        "https://youtu.be/k_YkQSTvjLk?si=_UigmXnp7cHiFAN5 "
    )


def test_the_last_steps_heading_wins_at_any_level() -> None:
    """15 notes write `# 3 步骤`; a note may use `##`, and may use it twice.

    The heading is matched at any ATX level because the vault is not
    consistent (烤鲭鱼 writes its 制作记录 heading as `##`), and the *last*
    one is authoritative — the steps are the final section of the note.
    """
    source = (
        "---\n材料:\n  - 空心菜\n---\n"
        "# 3 步骤\n旧的草稿\n## 4 步骤\n真正的步骤\n"
    ).encode()
    assert parse_recipe(source, note_name="x", note_path="x.md").steps == "\n真正的步骤\n"


def test_a_note_with_no_steps_heading_has_no_steps() -> None:
    """No steps is `""`, which is a state the detail view renders — not an error."""
    source = "---\n材料:\n  - 空心菜\n---\n# 1 材料\n`VIEW[{材料}]`\n".encode()
    assert parse_recipe(source, note_name="x", note_path="x.md").steps == ""


# --- the index over the folder -------------------------------------------


def test_index_reads_every_real_recipe_and_no_others(settings: Settings) -> None:
    _seed_vault(settings)
    snapshot = RecipeIndex(settings).refresh()
    assert snapshot.skipped == 0
    assert len(snapshot.notes) == FROZEN_NOTE_COUNT
    assert {note.note_name for note in snapshot.notes} == {
        path.stem for path in REAL_RECIPES.glob("*.md")
    } - {"Recipes"}


def test_the_index_page_is_not_a_recipe(settings: Settings) -> None:
    """`Recipes.md` is the folder's table of contents, not one of its contents.

    The same two conditions `Bases/Recipes-20250823.base` uses: a `材料` or
    `调料` key to be a recipe at all, and a basename that is not the folder's
    own index page. `Recipes.md` satisfies neither, and a note with zero
    Materials must still not crash the scan.
    """
    _seed_vault(settings)
    index = RecipeIndex(settings)
    assert "Recipes" not in {note.note_name for note in index.notes()}
    recipes_page = parse_recipe(
        (REAL_RECIPES / "Recipes.md").read_bytes(),
        note_name="Recipes",
        note_path=f"{RECIPES_RELATIVE}/Recipes.md",
    )
    assert recipes_page.ingredients == ()
    assert recipes_page.seasonings == ()


def test_a_recipe_with_no_materials_is_still_a_recipe(settings: Settings) -> None:
    """A note with only `调料` is a Recipe with zero Ingredients, not a failure."""
    _seed_vault(settings)
    (settings.vault_path / RECIPES_RELATIVE / "只有调料.md").write_bytes(SEASONINGS_ONLY)
    note = _one(RecipeIndex(settings), "只有调料")
    assert note.ingredients == ()
    assert note.seasonings == (IngredientEntry(0, "生抽"),)
    assert note.history == RecipeCookingHistory()


def test_only_direct_markdown_children_are_read(settings: Settings) -> None:
    """A `.base` view and a dotfile can never reach the index.

    The `.md` suffix is the whole filter, and it is the third condition
    `Recipes-20250823.base` carries as `file.ext != "base"`. The view file
    lives in `Bases/`, not here, so it is planted rather than assumed.
    """
    root = _seed_vault(settings)
    (root / "Recipes-20250823.base").write_text("filters:\n  and: []\n")
    (root / ".hidden.md").write_bytes(SEASONINGS_ONLY)
    (root / "子目录").mkdir()
    (root / "子目录" / "嵌套.md").write_bytes(SEASONINGS_ONLY)
    snapshot = RecipeIndex(settings).refresh()
    assert len(snapshot.notes) == FROZEN_NOTE_COUNT
    assert "只有调料" not in {note.note_name for note in snapshot.notes}


def test_index_preserves_vault_enumeration_order(settings: Settings) -> None:
    """Order is vault enumeration order; the display sort is D4's, applied client-side."""
    root = _seed_vault(settings)
    enumerated = tuple(
        entry.name[: -len(".md")]
        for entry in os.scandir(root)
        if entry.name.endswith(".md") and entry.name[: -len(".md")] != "Recipes"
    )
    assert tuple(note.note_name for note in RecipeIndex(settings).notes()) == enumerated


def test_a_malformed_note_is_skipped_and_counted_not_fatal(settings: Settings) -> None:
    """One bad note must not blank the list — and its loss must be a number.

    Three distinct malformations, three distinct refusals: an unterminated
    block, YAML that does not parse, and a duplicated `材料` key (which
    `safe_load` would silently resolve to the last one, losing the first
    without a trace).
    """
    root = _seed_vault(settings)
    (root / "没有结尾.md").write_bytes("---\n材料:\n  - 空心菜\n".encode())
    (root / "坏YAML.md").write_bytes(b"---\n\xe6\x9d\x90\xe6\x96\x99: [unclosed\n---\n")
    (root / "重复键.md").write_bytes(
        "---\n材料:\n  - 空心菜\n材料:\n  - 菠菜\n---\n".encode()
    )
    snapshot = RecipeIndex(settings).refresh()
    assert snapshot.skipped == 3
    assert len(snapshot.notes) == FROZEN_NOTE_COUNT
    assert not {"没有结尾", "坏YAML", "重复键"} & {note.note_name for note in snapshot.notes}


def test_an_unreadable_note_is_counted(settings: Settings) -> None:
    root = _seed_vault(settings)
    (root / "不是UTF8.md").write_bytes(NOT_UTF8)
    assert RecipeIndex(settings).refresh().skipped == 1


def test_a_missing_recipes_root_refuses_rather_than_reporting_zero_recipes(
    settings: Settings,
) -> None:
    """An empty list must never be a plausible answer to "the folder moved"."""
    shutil.rmtree(settings.vault_path / RECIPES_RELATIVE)
    with pytest.raises(ConfigurationError):
        RecipeIndex(settings).refresh()


def test_the_scan_bound_is_an_error_not_a_truncation(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exceeding the bound raises; a silently shortened index would misreport."""
    _seed_vault(settings, names=["烤土豆", "烤红薯"])
    monkeypatch.setattr(reader, "MAX_RECIPE_FILES", 1)
    with pytest.raises(RecipeIndexError):
        RecipeIndex(settings).refresh()


# --- the TTL snapshot provider -------------------------------------------


def test_the_snapshot_is_cached_for_the_configured_ttl(settings: Settings) -> None:
    """`recipe_cache_seconds` gates the re-read, on a monotonic clock."""
    _seed_vault(settings)
    now = 100.0
    assert settings.recipe_cache_seconds == 60.0
    index = RecipeIndex(settings, clock=lambda: now)
    first = index.snapshot()
    assert len(first.notes) == FROZEN_NOTE_COUNT
    (settings.vault_path / RECIPES_RELATIVE / "新菜.md").write_bytes(SEASONINGS_ONLY)
    now += 59.0
    assert index.snapshot().notes == first.notes
    now += 2.0
    assert "只有调料" not in {note.note_name for note in index.snapshot().notes}
    assert "新菜" in {note.note_name for note in index.snapshot().notes}


def test_refresh_and_invalidate_bypass_the_cache(settings: Settings) -> None:
    _seed_vault(settings)
    index = RecipeIndex(settings)
    assert len(index.notes()) == FROZEN_NOTE_COUNT
    (settings.vault_path / RECIPES_RELATIVE / "新菜.md").write_bytes(SEASONINGS_ONLY)
    assert len(index.notes()) == FROZEN_NOTE_COUNT
    assert len(index.refresh().notes) == FROZEN_NOTE_COUNT + 1
    index.invalidate()
    assert "新菜" in {note.note_name for note in index.snapshot().notes}


def test_close_is_a_documented_no_op(settings: Settings) -> None:
    """The index holds no descriptors, so closing it releases nothing — and
    the snapshot stays readable, which is what makes the no-op a fact rather
    than a courtesy."""
    _seed_vault(settings)
    index = RecipeIndex(settings)
    before = index.snapshot()
    assert index.close() is None
    assert index.snapshot() == before


def test_the_index_never_writes_to_the_vault(settings: Settings) -> None:
    """A Recipe is a read projection; `recipeTracker` and the Cooking Log own
    every field that could change. Nothing here may touch a byte."""
    root = _seed_vault(settings)
    before = {entry.name: entry.read_bytes() for entry in root.iterdir()}
    index = RecipeIndex(settings)
    index.refresh()
    index.invalidate()
    index.refresh()
    index.close()
    after = {entry.name: entry.read_bytes() for entry in root.iterdir()}
    assert after == before


# --- the nine tracker-owned fields ---------------------------------------


def test_auto_history_is_a_closed_nine_key_set() -> None:
    """Named by key, not inferred by shape, so the two frontmatter classes
    can never be confused: a hand-authored key cannot reach Recipe Cooking
    History, and a tracker field cannot be mistaken for hand-authored content."""
    assert AUTO_HISTORY_KEYS == frozenset(
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


def test_hand_authored_keys_never_leak_into_history(settings: Settings) -> None:
    """`材料`, `调料`, `烹饪工具`, `来源` and `时长（分钟）` are read on their own
    terms. Rewriting one of them leaves Recipe Cooking History untouched."""
    note = _parse(settings, "盐焗鸡")
    original = (REAL_RECIPES / "盐焗鸡.md").read_text(encoding="utf-8")
    edited_source = original.replace("  - 🐔/带皮鸡腿", "  - 盐焗鸡粉").encode()
    assert edited_source != (REAL_RECIPES / "盐焗鸡.md").read_bytes()
    edited = parse_recipe(
        edited_source,
        note_name="盐焗鸡",
        note_path=f"{RECIPES_RELATIVE}/盐焗鸡.md",
    )
    assert edited.ingredients == (IngredientEntry(0, "盐焗鸡粉"),)
    assert edited.history == note.history


def test_source_and_duration_read_every_shape_the_real_vault_uses() -> None:
    """`来源` and `时长（分钟）` were parsed by nothing; these are their real shapes.

    Each body below is lifted from a note that exists, not invented, because the
    two fields disagree about how forgiving they are and that disagreement is the
    thing worth pinning.
    """
    # `来源` as a BLOCK LIST — `凉拌黑木耳`, and 13 of the 16 notes.
    block = parse_recipe(
        "---\n材料: 茄\n来源:\n  - 小红书\n---\n".encode(),
        note_name="x",
        note_path="x.md",
    )
    assert block.source == ("小红书",)

    # `来源` as a SCALAR — `番茄炒蛋`, one of only two. A reader that assumed a
    # list would publish a Python list repr, or drop the value.
    scalar = parse_recipe(
        "---\n材料: 番茄\n来源: 做饭tutorial/小高姐\n---\n".encode(),
        note_name="x",
        note_path="x.md",
    )
    assert scalar.source == ("做饭tutorial/小高姐",)

    # `来源` present-but-blank — `煮菜菜` — and absent entirely. Both are empty,
    # and neither is an error: a blank value is a value (see the module docstring).
    blank = parse_recipe(
        "---\n材料: 番茄\n来源:\n时长（分钟）:\n---\n".encode(), note_name="x", note_path="x.md"
    )
    assert blank.source == ()
    assert blank.duration_minutes is None
    absent = parse_recipe("---\n材料: 番茄\n---\n".encode(), note_name="x", note_path="x.md")
    assert absent.source == ()
    assert absent.duration_minutes is None

    # `时长（分钟）: 0` is a real, if useless, cook time and must not collapse
    # into "absent" — which is why the field is `int | None` and not a sentinel.
    zero = parse_recipe(
        "---\n材料: 番茄\n时长（分钟）: 0\n---\n".encode(), note_name="x", note_path="x.md"
    )
    assert zero.duration_minutes == 0


def test_a_malformed_duration_never_drops_the_recipe() -> None:
    """The asymmetry with `_entries`, asserted rather than described.

    A non-string inside a `材料` list is a malformation and the note is skipped,
    because that value is an audit anchor. A cook time is cosmetic and displayed
    only, so every unparseable shape reads as `None` and the recipe survives —
    the rule `RecipeCookingHistory` already states for `auto_updated`.
    """
    # `时长（分钟）: -` is deliberately absent from this list: it is not a
    # malformed duration, it is invalid YAML, so it fails in the frontmatter
    # splitter and skips the note. That is the frontmatter's fail-closed rule and
    # is not what this function is about.
    for value in ("半小时", "[1, 2]", "{a: 1}", "true", "3.5"):
        note = parse_recipe(
            f"---\n材料: 番茄\n时长（分钟）: {value}\n---\n".encode(),
            note_name="x",
            note_path="x.md",
        )
        assert note.duration_minutes is None, value
        assert note.ingredients == (IngredientEntry(0, "番茄"),), value
    # A quoted integer is the one coercion worth making: unambiguous, and it
    # cannot turn a non-numeric string into a number.
    quoted = parse_recipe(
        '---\n材料: 番茄\n时长（分钟）: "30"\n---\n'.encode(), note_name="x", note_path="x.md"
    )
    assert quoted.duration_minutes == 30


def test_a_non_string_in_source_does_skip_the_note() -> None:
    """The other half of the asymmetry: `来源` is NOT forgiving.

    It goes through `_entries` like `材料` and `烹饪工具`, so a mapping where a
    string belongs raises and the note is skipped-and-counted. That is the louder
    behaviour, and it is the right one for a field whose list shape the rest of
    the app branches on — but it means `来源` and `时长` must never be collapsed
    into one "optional metadata" parser, which is why they are two functions.
    """
    with pytest.raises(RecipeNoteError):
        parse_recipe(
            "---\n材料: 番茄\n来源:\n  - a: b\n---\n".encode(), note_name="x", note_path="x.md"
        )


def test_a_quoted_cooking_date_is_still_a_date(settings: Settings) -> None:
    """PyYAML resolves `2025-07-24` to a `date` and leaves it a string when
    quoted; a hand-edited vault does both, and neither may drop the recipe."""
    source = '---\n材料: 空心菜\nfirst_cooked: "2025-07-24"\n---\n'.encode()
    note = parse_recipe(source, note_name="q", note_path="q.md")
    assert note.history.first_cooked == date(2025, 7, 24)


def test_an_unplaceable_cooking_date_is_a_skip(settings: Settings) -> None:
    source = "---\n材料: 空心菜\nlast_cooked: 去年\n---\n".encode()
    with pytest.raises(RecipeNoteError):
        parse_recipe(source, note_name="bad", note_path="bad.md")


# --- refusals -------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "max_bytes"),
    [
        (NOT_UTF8, 2_000_000),  # not UTF-8
        ("---\n材料: 空心菜\n---\n".encode(), 8),  # too large
        ("材料: 空心菜\n".encode(), 2_000_000),  # no block
        (b"---\n\xe6\x9d\x90\xe6\x96\x99: [unclosed\n---\n", 2_000_000),  # unparseable
        (b"---\n- just\n- a list\n---\n", 2_000_000),  # not a mapping
        ("---\n材料:\n  - 12\n---\n".encode(), 2_000_000),  # non-string item
    ],
)
def test_unreadable_sources_are_refused(source: bytes, max_bytes: int) -> None:
    """A silently half-read frontmatter would drop an Ingredient, and a recipe
    missing an Ingredient scores a point it has not earned."""
    with pytest.raises(RecipeNoteError):
        parse_recipe(source, note_name="x", note_path="x.md", max_bytes=max_bytes)


def test_absent_history_fields_are_none_not_errors() -> None:
    """Absent is not malformed: a recipe no tracker has touched has no history."""
    note = parse_recipe(SEASONINGS_ONLY, note_name="x", note_path="x.md")
    assert note == RecipeNote(
        note_name="x",
        note_path="x.md",
        ingredients=(),
        seasonings=(IngredientEntry(0, "生抽"),),
        tools=(),
        # Absent `来源` is an empty tuple and absent `时长（分钟）` is `None` —
        # the same "absent is a state, not a defect" rule the history fields
        # follow, and the reason `source` is a tuple rather than a list of
        # possibly-`None` entries.
        source=(),
        duration_minutes=None,
        steps="\n- 拌\n",
        history=RecipeCookingHistory(),
    )


def test_recipe_notes_are_immutable(settings: Settings) -> None:
    """One snapshot is shared by every request, so a caller cannot reach
    through it and edit what the next request will read."""
    note = _parse(settings, "烤土豆")
    with pytest.raises(FrozenInstanceError):
        note.tools = ("炒锅",)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        note.history.first_cooked = date(2000, 1, 1)  # type: ignore[misc]

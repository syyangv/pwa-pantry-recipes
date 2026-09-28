"""The five observed `材料` / `调料` value shapes, and the emoji run under F15.

Test seam 2 (spec §4.1): the parser is pure, so nothing here needs an app, an
HTTP client, or the pantry catalog.
"""

from __future__ import annotations

import pytest

from app.recipes.ingredients import (
    EMOJI_NAMES,
    ParsedIngredient,
    parse_ingredient_value,
    strip_leading_emoji_run,
)

BARE_VALUES = [
    "空心菜",
    "Kale",
    "Clam",
    "开心果酱",
    "红苋菜",
    "茼蒿",
    "香菇",
    "娃娃菜",
    "包菜",
    "Arugula",
    "Mortadella",
]
EMOJI_ALT_VALUES = [
    "🌶️/Shishito",
    "🐔/鸡翅",
    "🐔/带皮鸡腿",
    "🍄/香菇",
    "🍄/黑木耳",
    "🧀/Stracciatella",
]
MATERIAL_EMOJI_ONLY = [
    ("🥦", "西兰花"),
    ("🥚", "鸡蛋"),
    ("🍚", "米饭"),
    ("🥔", "土豆"),
    ("🍠", "红薯"),
    ("🍅", "番茄"),
]
SEASONING_EMOJI_ONLY = [("🧄", "蒜"), ("🫚", "姜"), ("🍋‍🟩", "青柠")]
ALT_PAIR_VALUES = [("香料/Basil", "香料"), ("调味酱油/蒸鱼豉油", "调味酱油")]

LIME = "🍋‍🟩"
CHILI_WITH_SELECTOR = "🌶️"
CHILI_BARE = "🌶"


@pytest.mark.parametrize("value", BARE_VALUES)
def test_bare_name_is_its_own_parsed_name(value: str) -> None:
    assert parse_ingredient_value(value) == ParsedIngredient(
        raw=value, parsed_name=value, parse_method="bare"
    )


def test_bare_count_is_eleven() -> None:
    assert len(BARE_VALUES) == 11


def test_quoted_wikilink_takes_the_inner_text() -> None:
    assert parse_ingredient_value('"[[Mackerel]]"') == ParsedIngredient(
        raw='"[[Mackerel]]"', parsed_name="Mackerel", parse_method="wikilink"
    )


def test_unbracketed_wikilink_also_resolves() -> None:
    assert parse_ingredient_value("[[Mackerel]]").parse_method == "wikilink"


def test_only_one_quote_layer_is_stripped() -> None:
    assert parse_ingredient_value('""[[Mackerel]]""').parse_method == "bare"


def test_curly_quotes_are_stripped_like_straight_ones() -> None:
    assert parse_ingredient_value("“香菇”").parsed_name == "香菇"
    assert parse_ingredient_value("‘香菇’").parsed_name == "香菇"


@pytest.mark.parametrize("value", EMOJI_ALT_VALUES)
def test_emoji_alt_takes_the_side_after_the_slash(value: str) -> None:
    assert parse_ingredient_value(value) == ParsedIngredient(
        raw=value, parsed_name=value.split("/", 1)[1], parse_method="emoji_alt"
    )


def test_emoji_alt_count_is_six() -> None:
    assert len(EMOJI_ALT_VALUES) == 6


@pytest.mark.parametrize(("value", "name"), MATERIAL_EMOJI_ONLY)
def test_material_emoji_only_names_itself(value: str, name: str) -> None:
    assert parse_ingredient_value(value) == ParsedIngredient(
        raw=value, parsed_name=name, parse_method="emoji_only"
    )


@pytest.mark.parametrize(("value", "name"), SEASONING_EMOJI_ONLY)
def test_seasoning_emoji_only_uses_the_same_parser(value: str, name: str) -> None:
    assert parse_ingredient_value(value) == ParsedIngredient(
        raw=value, parsed_name=name, parse_method="emoji_only"
    )


def test_emoji_only_count_is_nine_across_both_keys() -> None:
    assert len(MATERIAL_EMOJI_ONLY) == 6
    assert len(SEASONING_EMOJI_ONLY) == 3


def test_potato_is_the_emoji_only_value_not_the_burger() -> None:
    assert "🥔" in EMOJI_NAMES
    assert EMOJI_NAMES["🥔"] == "土豆"
    assert "🍔" not in EMOJI_NAMES


def test_emoji_dictionary_is_the_closed_key_set() -> None:
    assert set(EMOJI_NAMES) == {
        "🥦",
        "🥚",
        "🍚",
        "🥔",
        "🍠",
        "🍅",
        "🧄",
        "🫚",
        "🌶️",
        "🍋‍🟩",
        "🧀",
        "🍄",
        "🐔",
        "🍞",
    }


@pytest.mark.parametrize(("value", "name"), ALT_PAIR_VALUES)
def test_alt_pair_takes_the_left_side(value: str, name: str) -> None:
    assert parse_ingredient_value(value) == ParsedIngredient(
        raw=value, parsed_name=name, parse_method="alt_pair"
    )


def test_alt_pair_needs_no_leading_emoji() -> None:
    assert strip_leading_emoji_run("香料/Basil") == "香料/Basil"
    assert parse_ingredient_value("香料/Basil").parse_method == "alt_pair"


def test_bread_with_an_emoji_is_emoji_alt_not_alt_pair() -> None:
    assert parse_ingredient_value("🍞/focaccia").parse_method == "emoji_alt"


def test_chili_is_one_key_with_a_variation_selector() -> None:
    assert CHILI_WITH_SELECTOR == "🌶️"
    assert [f"U+{ord(c):04X}" for c in CHILI_WITH_SELECTOR] == ["U+1F336", "U+FE0F"]


@pytest.mark.parametrize("value", [CHILI_WITH_SELECTOR, CHILI_BARE])
def test_chili_resolves_with_and_without_the_variation_selector(value: str) -> None:
    assert parse_ingredient_value(value).parsed_name == "小米椒"
    assert parse_ingredient_value(value).parse_method == "emoji_only"


def test_lime_is_one_three_codepoint_zwj_key() -> None:
    assert [f"U+{ord(c):04X}" for c in LIME] == ["U+1F34B", "U+200D", "U+1F7E9"]


def test_lime_run_is_consumed_as_a_single_unit() -> None:
    assert strip_leading_emoji_run(f"{LIME} Lime") == " Lime"
    assert strip_leading_emoji_run(LIME) == ""


def test_lime_has_no_single_codepoint_keys_to_fall_back_on() -> None:
    assert "🍋" not in EMOJI_NAMES
    assert "🟩" not in EMOJI_NAMES
    assert EMOJI_NAMES[LIME] == "青柠"


def test_a_variation_selector_run_is_consumable_without_being_a_key() -> None:
    assert "❤️" not in EMOJI_NAMES
    assert parse_ingredient_value("❤️/Red") == ParsedIngredient(
        raw="❤️/Red", parsed_name="Red", parse_method="emoji_alt"
    )


def test_a_zwj_run_is_consumable_without_being_a_key() -> None:
    assert "👩‍🍳" not in EMOJI_NAMES
    assert parse_ingredient_value("👩‍🍳/Cook").parsed_name == "Cook"


def test_a_key_never_swallows_the_next_emoji() -> None:
    assert parse_ingredient_value("🐔🍄").parse_method == "unparseable"
    assert parse_ingredient_value("🥚🥚").parse_method == "unparseable"


def test_nfkc_folds_before_anything_else_inspects_the_bytes() -> None:
    raw = "Ｋａｌｅ"
    parsed = parse_ingredient_value(raw)
    assert parsed.parsed_name == "Kale"
    assert parsed.raw == raw


@pytest.mark.parametrize(
    "raw",
    [
        "空心菜",
        "  香菇  ",
        "🌶️/Shishito",
        f"{LIME}",
        '"[[Mackerel]]"',
        "Ｋａｌｅ",
        "❤️/Red",
        "柴米 蒜香蒸茄子 300 克",
    ],
)
def test_raw_is_preserved_byte_for_byte(raw: str) -> None:
    assert parse_ingredient_value(raw).raw == raw


@pytest.mark.parametrize("raw", ["", "   ", "[[]]", "🌶️/", "/Basil", "🥚🥚"])
def test_values_with_no_recoverable_name_are_unparseable(raw: str) -> None:
    parsed = parse_ingredient_value(raw)
    assert parsed.parsed_name is None
    assert parsed.parse_method == "unparseable"
    assert parsed.raw == raw

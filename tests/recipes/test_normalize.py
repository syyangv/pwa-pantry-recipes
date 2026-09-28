"""The product core: the 9-step strip order, the size/brand split, and the guards.

Test seam 2 (spec §4.1): `normalize_ingredient` is pure, so these run without an
app, a vault, or the pantry catalog.
"""

from __future__ import annotations

import re

import pytest

from app.recipes import normalize
from app.recipes.ingredients import parse_ingredient_value
from app.recipes.normalize import BRAND_LEXICON, normalize_ingredient

# §9.7's verification table, verbatim. The 柴米 row is left out: the spec itself
# annotates it as a pre-brand-step value ("→ then 蒜香蒸茄子 after the brand
# step") and `test_spec_table_is_the_size_pass_snapshot` covers it there.
SPEC_TABLE = [
    ("Wang Korea 有机去壳甘栗仁 60g*5 300 克", "Wang Korea 有机去壳甘栗仁"),
    ("八道 高级牛骨汤面 125g*4 500 克", "八道 高级牛骨汤面"),
    ("空心菜嫩苗 0.95-1.05 磅", "空心菜嫩苗"),
    ("红苋菜苗 0.95-1.05 磅", "红苋菜苗"),
    ("新鲜小叶茼蒿 1 磅", "新鲜小叶茼蒿"),
    ("Orri 蜜橘 2.8-3.2 磅", "Orri 蜜橘"),
    ("Surasang 韩国年糕片 1.43 磅", "Surasang 韩国年糕片"),
    ("柴米 传统工艺 软心皮蛋 6枚装 65 克", "柴米 传统工艺 软心皮蛋 6枚装"),
    (
        "Icelandic Provisions, Extra Creamy Skyr, Cold Brew Coffee, 4.4 Ounce",
        "Icelandic Provisions Extra Creamy Skyr Cold Brew Coffee",
    ),
    (
        "365 by Whole Foods Market, Organic Broccoli Florets, 16 oz, (Frozen)",
        "365 by Whole Foods Market Organic Broccoli Florets",
    ),
    (
        "Frozen Mackerel Boneless Protion-cut 5P 10.58 盎司",
        "Frozen Mackerel Boneless Protion-cut 5P",
    ),
    ("Mushroom Dried Morel Mushrooms", "Mushroom Dried Morel Mushrooms"),
]

# The same table with the brand step applied, which is what the pipeline returns.
PRODUCT_CORES = [
    ("Wang Korea 有机去壳甘栗仁 60g*5 300 克", "有机去壳甘栗仁"),
    ("八道 高级牛骨汤面 125g*4 500 克", "八道 高级牛骨汤面"),
    ("空心菜嫩苗 0.95-1.05 磅", "空心菜嫩苗"),
    ("红苋菜苗 0.95-1.05 磅", "红苋菜苗"),
    ("新鲜小叶茼蒿 1 磅", "新鲜小叶茼蒿"),
    ("Orri 蜜橘 2.8-3.2 磅", "蜜橘"),
    ("Surasang 韩国年糕片 1.43 磅", "Surasang 韩国年糕片"),
    ("柴米 传统工艺 软心皮蛋 6枚装 65 克", "传统工艺 软心皮蛋 6枚装"),
    (
        "Icelandic Provisions, Extra Creamy Skyr, Cold Brew Coffee, 4.4 Ounce",
        "Extra Creamy Skyr Cold Brew Coffee",
    ),
    (
        "365 by Whole Foods Market, Organic Broccoli Florets, 16 oz, (Frozen)",
        "Organic Broccoli Florets",
    ),
    (
        "Frozen Mackerel Boneless Protion-cut 5P 10.58 盎司",
        "Frozen Mackerel Boneless Protion-cut 5P",
    ),
    ("Mushroom Dried Morel Mushrooms", "Mushroom Dried Morel Mushrooms"),
    ("柴米 蒜香蒸茄子 300 克", "蒜香蒸茄子"),
]

STRIP_ORDER = (
    "_strip_quotes_and_space",
    "_strip_wikilink_brackets",
    "strip_leading_emoji_run",
    "_strip_tags",
    "_strip_date_markers",
    "_strip_flavour_breakdowns",
    "_strip_size",
    "_strip_brand",
    "_tidy",
)

_SIZE_PATTERN_WITHOUT_BOUND = re.compile(
    normalize._SIZE_RE.pattern.replace(normalize._BOUND, ""),
    re.IGNORECASE,
)


def _recorder(name: str, seen: list[str]):  # noqa: ANN202
    real = getattr(normalize, name)

    def recorded(value: str) -> str:
        seen.append(name)
        return real(value)

    return recorded


def _core_without_brand_step(raw: str) -> str:
    """The pipeline with the brand step omitted, i.e. §9.7's table."""
    value = normalize._strip_quotes_and_space(raw)
    value = normalize._strip_wikilink_brackets(value)
    value = normalize.strip_leading_emoji_run(value)
    value = normalize._strip_tags(value)
    value = normalize._strip_date_markers(value)
    value = normalize._strip_flavour_breakdowns(value)
    value = normalize._strip_size(value)
    return normalize._tidy(value)


@pytest.mark.parametrize(("raw", "core"), PRODUCT_CORES)
def test_product_cores(raw: str, core: str) -> None:
    assert normalize_ingredient(raw) == core


@pytest.mark.parametrize(
    ("raw", "core"), [*SPEC_TABLE, ("柴米 蒜香蒸茄子 300 克", "柴米 蒜香蒸茄子")]
)
def test_spec_table_is_the_size_pass_snapshot(raw: str, core: str) -> None:
    assert _core_without_brand_step(raw) == core


def test_strip_order_is_fixed(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    for name in STRIP_ORDER:
        monkeypatch.setattr(normalize, name, _recorder(name, seen))
    assert normalize_ingredient("Wang Korea 有机去壳甘栗仁 60g*5 300 克") == "有机去壳甘栗仁"
    assert seen == list(STRIP_ORDER)


def test_size_pass_runs_to_a_fixpoint() -> None:
    raw = "Wang Korea 有机去壳甘栗仁 60g*5 300 克"
    once = normalize._SIZE_RE.sub(" ", raw)
    assert normalize._strip_size(raw) == normalize._SIZE_RE.sub(" ", once)
    assert _core_without_brand_step(raw) == "Wang Korea 有机去壳甘栗仁"


def test_size_pass_consumes_the_size_before_the_lexicon_sees_the_line() -> None:
    sized = normalize._strip_size("365 by Whole Foods Market, Organic 1% Milk, 32 Fl Oz")
    assert "Fl Oz" not in sized
    assert normalize_ingredient("365 by Whole Foods Market, Organic 1% Milk, 32 Fl Oz") == (
        "Organic 1% Milk"
    )


def test_a_brand_is_never_taken_as_a_bare_digit_run() -> None:
    assert normalize_ingredient("365") == "365"
    assert normalize_ingredient("365 g") == ""
    assert normalize_ingredient("3650 Milk") == "3650 Milk"


def test_bound_guard_keeps_a_unit_that_is_a_word_prefix() -> None:
    assert normalize_ingredient("6枚装") == "6枚装"
    assert normalize._strip_size("6枚装") == "6枚装"


def test_bound_guard_is_load_bearing() -> None:
    assert _SIZE_PATTERN_WITHOUT_BOUND.sub(" ", "6枚装").strip() == "装"


def test_bound_guard_keeps_the_gallon_whole() -> None:
    assert normalize_ingredient("1 Gallon Jug") == "Jug"
    assert "allon" not in normalize_ingredient("1 Gallon Jug")


def test_leading_food_word_is_not_a_brand() -> None:
    assert "Mushroom" not in BRAND_LEXICON
    assert normalize_ingredient("Mushroom Dried Morel Mushrooms") == (
        "Mushroom Dried Morel Mushrooms"
    )


@pytest.mark.parametrize(
    "raw",
    [
        "优质白桃礼盒",
        "韩国紫苏叶",
        "台湾旺旺浪味仙",
        "小白菜心",
        "A级波斯黄瓜",
        "小巷口白糖馅蟹壳黄烧饼",
    ],
)
def test_leading_qualifier_is_not_a_brand(raw: str) -> None:
    assert normalize_ingredient(raw) == raw


def test_brand_entries_are_matched_whole_and_case_insensitively() -> None:
    assert normalize_ingredient("WANG KOREA 有机去壳甘栗仁 60g") == "有机去壳甘栗仁"
    assert normalize_ingredient("wang korea 有机去壳甘栗仁 60g") == "有机去壳甘栗仁"
    assert normalize_ingredient("Wangs 有机去壳甘栗仁 60g") == "Wangs 有机去壳甘栗仁"


def test_brand_lexicon_is_closed() -> None:
    assert isinstance(BRAND_LEXICON, frozenset)
    assert "柴米" in BRAND_LEXICON
    assert "Wang Korea" in BRAND_LEXICON
    assert "Driscoll's" in BRAND_LEXICON
    assert "八道" not in BRAND_LEXICON
    assert "Surasang" not in BRAND_LEXICON


@pytest.mark.parametrize(
    ("raw", "core"),
    [
        ("空心菜嫩苗 ➕ 2026-01-05", "空心菜嫩苗"),
        ("空心菜嫩苗 ✍️ 2026-01-02", "空心菜嫩苗"),
        ("空心菜嫩苗 🛫 2026-01-06", "空心菜嫩苗"),
        ("空心菜嫩苗 ✅ 2026-01-07", "空心菜嫩苗"),
        ("空心菜嫩苗 ❌ 2026-01-08", "空心菜嫩苗"),
        ("空心菜嫩苗 📅 2026-01-09", "空心菜嫩苗"),
        ("空心菜嫩苗 💵 2026-01-10", "空心菜嫩苗"),
        ("空心菜嫩苗 #veggie #fresh", "空心菜嫩苗"),
        ("#veggie 空心菜嫩苗", "空心菜嫩苗"),
        ("空心菜嫩苗 ➕ 2026-01-05 #veggie 300 克", "空心菜嫩苗"),
    ],
)
def test_annotations_are_stripped(raw: str, core: str) -> None:
    assert normalize_ingredient(raw) == core


def test_flavour_breakdowns_are_stripped() -> None:
    raw = "Momofuku Ando  Curry Flavored Ramen（原味*4， 芋头*4， 麻薯*4）"
    assert normalize_ingredient(raw) == "Momofuku Ando Curry Flavored Ramen"


def test_surrounding_quotes_and_wikilink_brackets_are_stripped() -> None:
    assert normalize_ingredient('"[[Mackerel]]"') == "Mackerel"
    assert normalize_ingredient("  “香菇”  ") == "香菇"


def test_leading_emoji_run_is_stripped() -> None:
    assert normalize_ingredient("🥔 300 克") == ""
    assert normalize_ingredient("❤️ Gift Box") == "Gift Box"


def test_nfkc_folds_full_width_before_the_size_pass() -> None:
    assert normalize_ingredient("Ｋａｌｅ") == "Kale"


def test_separators_and_edges_are_tidied() -> None:
    assert normalize_ingredient("、香菇、") == "香菇"
    assert normalize_ingredient("香菇 - ") == "香菇"
    assert normalize_ingredient("香菇,,  ") == "香菇"


def test_brand_is_stripped_even_when_a_size_sat_in_front_of_it() -> None:
    assert normalize_ingredient("300 克 柴米 蒜香蒸茄子") == "蒜香蒸茄子"


@pytest.mark.parametrize(
    ("raw", "core"),
    [
        ('"[[Mackerel]]"', "Mackerel"),
        ("🌶️/Shishito", "Shishito"),
        ("香料/Basil", "香料"),
        ("空心菜", "空心菜"),
    ],
)
def test_a_parsed_name_normalizes_to_a_product_core(raw: str, core: str) -> None:
    parsed = parse_ingredient_value(raw)
    assert parsed.parsed_name is not None
    assert normalize_ingredient(parsed.parsed_name) == core


def test_normalization_is_a_pure_function_of_its_argument() -> None:
    raw = "Wang Korea 有机去壳甘栗仁 60g*5 300 克"
    first = normalize_ingredient(raw)
    assert first == normalize_ingredient(raw)
    assert raw == "Wang Korea 有机去壳甘栗仁 60g*5 300 克"

"""Reduction of a Pantry Item name to the product core both matcher sides share.

`normalize_ingredient()` is the **shared** entry point, not a matcher-private
helper: `resolve_ingredient()` compares a recipe Ingredient with a
`canonical_name` through it, and the F1 Stock Join builds its `by_name` /
`by_basename` indexes on it too. Two sides can only agree if there is exactly
one normalization, which is why it lives in its own module.

The strip order is load-bearing (F15, locked) and is applied exactly as listed
below. Size stripping is numeric-anchored and therefore runs **before** the
brand lexicon, which matches bare tokens: a numeric-anchored size pattern has
to see the digits before a lexicon lookup can take them, or a doubled size tail
is left behind and its digits are re-read as a fresh size of the wrong span.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

from .ingredients import strip_leading_emoji_run

# Closed lexicon of leading brand tokens observed in the 178-row catalog. It is
# closed on purpose: "strip the first token" is wrong in both directions. The
# leading token of `Mushroom Dried Morel Mushrooms` is a food word, and the
# leading token of `优质白桃礼盒` / `韩国紫苏叶` is a product qualifier, while
# `小巷口白糖馅蟹壳黄烧饼` buries a brand mid-name — position says nothing, a
# closed list does.
BRAND_LEXICON: Final[frozenset[str]] = frozenset(
    {
        "盒马",
        "AeroFarms",
        "Chobani",
        "Chobani®",
        "Icelandic Provisions",
        "Lifeway",
        "Loacker",
        "POM",
        "Wonderful",
        "Simple",
        "Nutricost",
        "乐事",
        "喜茶",
        "柴米",
        "好丽友",
        "小巷口",
        "禾苑",
        "晨曦",
        "Fusipim",
        "JAYONE",
        "Koia",
        "MOOALA",
        "HAITAI",
        "ORION",
        "CJ",
        "Asahi",
        "Acure",
        "Manukora",
        "Matchaful",
        "Driscoll's",
        "Beekeeper's",
        "BEEKEEPERS",
        "Bell",
        "Gelatys",
        "Dr.Reju-All",
        "LESSEREVIL",
        "Forward",
        "EVOLUTION",
        "NOW",
        "Orri",
        "Wang Korea",
        "思念",
        "臻品德",
        "中华",
        "江船长&Yaba",
        "超禾",
        "味圈",
        "必品阁",
        "饭匹兄弟",
        "Love Me Sweet",
        "Sanpellegrino",
        "Trader Joe's",
        "365",
        "365 By Whole Foods Market",
        "Whole Foods Market",
        "YABA",
    }
)

_QUOTE_PAIRS: Final[dict[str, str]] = {
    '"': '"',
    "“": "”",
    "‘": "’",
}

_NUM: Final = r"\d+(?:[.,]\d+)?"
_UNIT: Final = (
    r"(?:fl\s*oz|fl\s*ounces?|ounces?|oz|pints?|quarts?|gallons?|gal|pts?|qts?"
    r"|千克|毫升|公斤|盎司|磅|升|克|个|枚|片|罐|瓶|袋|盒|根|颗|条|头|把|支|份|只|粒|入"
    r"|kg|mg|ml|lbs|lb|l|g)"
)
# A unit may not be a PREFIX of a longer word: without it `6枚装` loses its 枚
# and leaves a bare 装, and `1 Gallon Jug` can have its G taken instead of the
# whole Gallon.
_BOUND: Final = r"(?![A-Za-z\u3000-\u9fff])"
_SIZE_RE: Final = re.compile(
    rf"(?:{_NUM}\s*(?:[-–~]\s*{_NUM})?\s*(?:{_UNIT})(?:\s*[x×*]\s*{_NUM})?){_BOUND}"
    rf"|(?:{_NUM}\s*[x×*]\s*{_NUM})"
    rf"|(?:[x×*]\s*{_NUM}(?=\s|$))",
    re.IGNORECASE,
)
_FLAVOUR_BREAKDOWN_RE: Final = re.compile(r"[（(][^）)]*[）)]")
_DATE_TOKEN_RE: Final = re.compile(
    r"(?:💵|✍️|➕|🛫|✅|❌|📅)\s*\d{4}-\d{2}-\d{2}"
)
_TAG_TOKEN_RE: Final = re.compile(r"(?:^|[ \t])#[^\s#]+")
# Longest entries first so a multi-token brand (`Wang Korea`, `365 By Whole
# Foods Market`) is preferred over any shorter entry it begins with.
_BRAND_ALTERNATIVES: Final[tuple[str, ...]] = tuple(
    sorted(BRAND_LEXICON, key=lambda entry: (-len(entry.split()), -len(entry), entry))
)
_BRAND_RE: Final = re.compile(
    r"^[ \t]*(?:"
    + "|".join(re.escape(entry) for entry in _BRAND_ALTERNATIVES)
    + r")[,\u3001]?(?=[ \t])",
    re.IGNORECASE,
)
_SEPARATORS_RE: Final = re.compile(r"[,，、]")
_SPACES_RE: Final = re.compile(r"\s{2,}")
_EDGE_TRIM: Final = ",，、- "


def normalize_ingredient(value: str) -> str:
    """Return the product core of `value`: every size, brand and annotation gone.

    Shared by the matcher and by the F1 Stock Join name index — the only reason
    a recipe Ingredient and a `canonical_name` can be compared at all. Pure:
    `str` in, `str` out, no I/O and no environment.

    Case is preserved. §9.11.3's name index is casefolded on top of this
    function; folding here would change `Mushroom Dried Morel Mushrooms`, which
    the catalog rows of §9.7 require to survive unchanged.
    """
    working = _strip_quotes_and_space(unicodedata.normalize("NFKC", value))
    working = _strip_wikilink_brackets(working)
    working = strip_leading_emoji_run(working)
    working = _strip_tags(working)
    working = _strip_date_markers(working)
    working = _strip_flavour_breakdowns(working)
    working = _strip_size(working)
    working = _strip_brand(working)
    return _tidy(working)


def _strip_quotes_and_space(value: str) -> str:
    """Step 2: one matching layer of surrounding quotes, plus outer whitespace."""
    trimmed = value.strip()
    if len(trimmed) >= 2 and _QUOTE_PAIRS.get(trimmed[0]) == trimmed[-1]:
        return trimmed[1:-1].strip()
    return trimmed


def _strip_wikilink_brackets(value: str) -> str:
    """Step 3: `[[` … `]]`, for a name that arrived bracketed from an override."""
    if value.startswith("[[") and value.endswith("]]"):
        return value[2:-2].strip()
    return value


def _strip_tags(value: str) -> str:
    return _TAG_TOKEN_RE.sub("", value)


def _strip_date_markers(value: str) -> str:
    return _DATE_TOKEN_RE.sub("", value)


def _strip_flavour_breakdowns(value: str) -> str:
    return _FLAVOUR_BREAKDOWN_RE.sub(" ", value)


def _strip_size(value: str) -> str:
    """Step 8a: sizes and quantities, applied until a pass changes nothing.

    `Wang Korea 有机去壳甘栗仁 60g*5 300 克` carries a multiplier (`60g*5`) and
    then a second size (`300 克`); iterating to a fixpoint is the guarantee that
    a tail the first pass only partly exposed is still removed.
    """
    stripped = value
    while True:
        reduced = _SIZE_RE.sub(" ", stripped)
        if reduced == stripped:
            return stripped
        stripped = reduced


def _strip_brand(value: str) -> str:
    """Step 8b: one leading lexicon entry, compared case-insensitively.

    The entry goes with the single space that follows it and nothing else. A
    comma counts as that separator because the catalog separates segments with
    one (`Icelandic Provisions, Extra Creamy Skyr, …`), and a following space is
    required, so a name that is nothing but a lexicon token is left alone and
    `365` can never be taken as a bare digit run before `_strip_size` saw it.
    """
    return _BRAND_RE.sub("", value, count=1)


def _tidy(value: str) -> str:
    """Step 9: separators become spaces, then whitespace collapses and trims."""
    return _SPACES_RE.sub(" ", _SEPARATORS_RE.sub(" ", value)).strip(_EDGE_TRIM)

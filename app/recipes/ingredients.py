"""Parsing of a `材料` / `调料` frontmatter value into a matchable name.

`raw` is the audit anchor and is returned byte-for-byte, so every transformation
happens on a working copy: NFKC folds compatibility forms (`Ｋａｌｅ` → `Kale`)
and must never reach the caller's string.

Five shapes are observed across the 26 distinct `材料` values, plus the three
emoji-only Seasonings in `调料` — the same parser handles both frontmatter keys,
which is why `EMOJI_NAMES` is one key set rather than a `材料`-only table.

The emoji run is a hand-built character-range class over an explicit key set and
not a Unicode property escape (F15, locked). `🍋‍🟩` is `U+1F34B U+200D
U+1F7E9` — one ZWJ sequence meaning lime. A property class matches the three
parts, not the run, and matching them separately leaves garbage behind; only a
set of literal keys can name that sequence. The `regex` module would not help
either, because matching a run rather than its parts needs grapheme
clustering.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final, Literal

ParseMethod = Literal[
    "bare",
    "wikilink",
    "emoji_alt",
    "emoji_only",
    "alt_pair",
    "unparseable",
]

EMOJI_NAMES: Final[dict[str, str]] = {
    "🥦": "西兰花",
    "🥚": "鸡蛋",
    "🍚": "米饭",
    "🥔": "土豆",
    "🍠": "红薯",
    "🍅": "番茄",
    "🧄": "蒜",
    "🫚": "姜",
    "🌶️": "小米椒",
    "🍋‍🟩": "青柠",
    "🧀": "奶酪",
    "🍄": "蘑菇",
    "🐔": "鸡肉",
    "🍞": "面包",
}

# Decision-dense (F15): the run is an explicit range class plus the two
# invisible joiners, consumed greedily from offset 0. U+FE0F is a variation
# selector that NFKC does not remove, and U+200D is the ZWJ that makes
# `🍋‍🟩` a single unit; without either in the class a run is split and the
# text after it turns to garbage.
_EMOJI_RUN: Final = re.compile(
    r"^(?:[\U0001F300-\U0001FAFF\u2600-\u27bf\u2b00-\u2bff"
    r"\U0001F1E6-\U0001F1FF\ufe0f\u200d])+"
)

_KEYS_LONGEST_FIRST: Final[tuple[str, ...]] = tuple(
    sorted(EMOJI_NAMES, key=lambda key: (-len(key), key))
)

_QUOTE_PAIRS: Final[dict[str, str]] = {
    '"': '"',
    "“": "”",
    "‘": "’",
}

_WIKILINK: Final = re.compile(r"\A\[\[(?P<target>.*)\]\]\Z", re.DOTALL)


@dataclass(frozen=True)
class ParsedIngredient:
    """One frontmatter value, its recovered name, and the shape it was written in.

    `parsed_name` is `None` only when the value carries no recoverable name, and
    then `parse_method` is `"unparseable"`. `raw` is always the untouched input.
    """

    raw: str
    parsed_name: str | None
    parse_method: ParseMethod


def parse_ingredient_value(raw: str) -> ParsedIngredient:
    """Parse a `材料` / `调料` value into `(raw, parsed_name, parse_method)`.

    Order: NFKC → one layer of matching surrounding quotes → `[[…]]` wikilink →
    leading emoji run → a leading `/` makes it an emoji+alt pair → an
    emoji-only value names itself from `EMOJI_NAMES` → a remaining `/` makes it
    a bare `X/Y` pair whose **left** side is the CJK primary → otherwise the
    value is the name.
    """
    working = unicodedata.normalize("NFKC", raw)
    working = _strip_one_quote_layer(working.strip())

    wikilink = _WIKILINK.match(working)
    if wikilink is not None:
        return _parsed(raw, wikilink.group("target"), "wikilink")

    run = _EMOJI_RUN.match(working)
    remainder = working if run is None else working[run.end() :]
    if run is not None and remainder.startswith("/"):
        return _parsed(raw, remainder[1:], "emoji_alt")
    if run is not None and not remainder:
        return _parsed(raw, _emoji_key_name(working), "emoji_only")
    if "/" in remainder:
        return _parsed(raw, remainder.split("/", 1)[0], "alt_pair")
    return _parsed(raw, remainder, "bare")


def strip_leading_emoji_run(value: str) -> str:
    """Return `value` without its leading emoji run; other text is untouched.

    The run is a range class rather than a dictionary lookup so a VS-16-only or
    ZWJ-only sequence stays consumable when it names nothing in
    `EMOJI_NAMES`. Shared with `app.recipes.normalize`, which strips the run from
    catalog names too.
    """
    run = _EMOJI_RUN.match(value)
    return value[run.end() :] if run is not None else value


def _strip_one_quote_layer(value: str) -> str:
    """Remove one matching pair of surrounding quotes, then re-trim."""
    if len(value) >= 2:
        opening, closing = value[0], value[-1]
        if _QUOTE_PAIRS.get(opening) == closing:
            return value[1:-1].strip()
    return value


def _emoji_key_name(value: str) -> str | None:
    """Resolve a value that is entirely an emoji run to its dictionary name.

    Longest key first, so a multi-codepoint ZWJ key wins over any shorter one it
    starts with, and a key only matches when the run ends there — `🐔🍄` is two
    emoji, not a `🐔`. Lookups run with and without U+FE0F so `🌶` and `🌶️`
    both reach `小米椒`.
    """
    for key in _KEYS_LONGEST_FIRST:
        if _key_covers(key, value) or _key_covers(_without_selector(key), value):
            return EMOJI_NAMES[key]
    return None


def _key_covers(key: str, value: str) -> bool:
    if not value.startswith(key):
        return False
    return len(value) == len(key) or _EMOJI_RUN.match(value[len(key) :]) is None


def _without_selector(key: str) -> str:
    return key.replace("\ufe0f", "")


def _parsed(raw: str, name: str | None, method: ParseMethod) -> ParsedIngredient:
    trimmed = name.strip() if name is not None else ""
    if not trimmed:
        return ParsedIngredient(raw=raw, parsed_name=None, parse_method="unparseable")
    return ParsedIngredient(raw=raw, parsed_name=trimmed, parse_method=method)

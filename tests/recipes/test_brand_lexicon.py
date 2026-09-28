"""The brand lexicon as data: its schema, its fail-closed loader, and its drift.

Three separate obligations live here, and they are deliberately kept apart:

1. **The file is well-formed** — schema version, entry shape, no case-insensitive
   duplicates. A lexicon file is a hand-edited artifact, so the mistakes a code
   literal cannot make (a typo'd key, a padded entry, `- chobani` next to
   `- Chobani`) are exactly the ones that have to be caught.
2. **The loader fails closed** — a missing or malformed file raises
   `ConfigurationError` instead of degrading to an empty lexicon. A silently
   empty lexicon strips nothing, so every packaged SKU keeps its leading token
   and the matching tiers admit candidates the lexicon was meant to rule out.
3. **The file does not drift from the catalog** — a leading token the 178-row
   catalog contains and the lexicon does not strip must be on a reviewed list, so
   a newly ingested brand is a visible failure rather than a silent mis-strip.

Obligation 3 reads the catalog's `canonical_name` column from the **committed
`tests/fixtures/pantry_items_snapshot.json`**, not from the producer's live
`pantry_items.db`. It used to read the live file and skip when the sibling
checkout was absent, which made the drift gate local-only: a brand newly ingested
on this machine failed here, and CI — which has no such checkout — saw nothing at
all. The snapshot is the same 178 rows, regenerated deliberately by
`scripts/snapshot_pantry_catalog.py` in its own commit (F14), so the gate runs in
every clone and on every re-import review, and a re-import is a change to this
repository's committed data rather than a failure about somebody else's disk.
"""

from __future__ import annotations

import ast
import importlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import Final

import pytest
import yaml

from app.config import ConfigurationError
from app.recipes import brand_lexicon, normalize
from app.recipes.brand_lexicon import BRANDS_SOURCE, load_brand_lexicon
from app.recipes.ingredients import strip_leading_emoji_run
from app.recipes.normalize import BRAND_LEXICON, normalize_ingredient

#: The frozen catalog dump — the drift gate's only catalog source (F14). Never
#: the sibling repository's `pantry_items.db`, which CI does not have.
CATALOG_SNAPSHOT: Final = (
    Path(__file__).resolve().parent.parent / "fixtures" / "pantry_items_snapshot.json"
)

# Every distinct leading token of the 178-row catalog that `brands.yaml`
# deliberately does NOT strip — 76 of the catalog's 129. This list is the
# "reviewed" half of the drift contract: a token absent from both the lexicon
# and this set fails the test, and a token in *both* fails the other direction.
#
# It is derived, not remembered. `test_the_allowlist_matches_the_catalog_exactly`
# asserts set equality against the live catalog, so nothing here can rot into a
# stale promise; and the three groups below are the classification of each
# token, which is the part a maintainer actually has to review.
#
# The set is a mix of three things, and calling all of them "non-brand" would be
# a lie:
#
#   * **The token IS the food.** `Mushroom Dried Morel Mushrooms` and
#     `空心菜嫩苗 0.95-1.05 磅` lose their product entirely if these are stripped.
#     §9.7 calls this out as the proof that position cannot substitute for a
#     closed list. 30 of the 76.
#   * **The token is a product qualifier, a year, or a fused brand+product
#     string.** `A级波斯黄瓜`, `优质白桃礼盒`, `2026 乐事FIFA世界杯限定联名薯片…`.
#     17 of the 76, of which 5 (`小巷口白糖馅蟹壳黄烧饼`, `台湾旺旺浪味仙`,
#     `【柴火大院】五常大米`, `日本KAO花王`, `佳沛红宝石奇异果`) carry a real
#     brand *inside* the token: `小巷口`, `旺旺`, `柴火大院`, `KAO花王` and `佳沛`
#     are brands, but the token that contains them also contains the whole
#     product, so making it a lexicon entry would delete the product rather than
#     the brand. A brand the leading-token rule cannot reach is a limitation of
#     the rule, not a defect in the list.
#   * **The token IS a complete, real brand, deliberately left attached.**
#     29 of the 76. `八道` and `Surasang` are pinned as *not* lexicon entries by
#     `tests/recipes/test_normalize.py::test_brand_lexicon_is_closed`, so their
#     classification is a decision on the record rather than my judgement. The
#     rest follow from the same principle: both sides of the matcher run
#     through the *same* `normalize_ingredient()`, so a brand left attached is
#     symmetric and harmless, while a wrongly-stripped food word is a silent
#     one-sided edit. The lexicon exists to remove a brand that appears on one
#     side only, and a brand that has been ingested on both sides needs nothing
#     removed.
#
# The asymmetry is the whole reason none of the 29 was added. Adding a brand to
# `brands.yaml` is a one-line data edit and needs no code change — that is what
# this refactor bought — but it still changes `normalize_ingredient()`'s output
# on 29 catalog rows, and none of those 29 outputs is asserted anywhere. Making
# that call on the maintainer's behalf, on a refactor whose mandate was to move
# 56 entries from a literal to a file without changing behaviour, would be me
# choosing the answer the gate was built to make them see.
#
# Three of the 29 are near-misses worth the maintainer's eye rather than mine.
# `江船长`: the lexicon carries the longer `江船长&Yaba`, which no catalog row
# begins with, so the row `江船长 鲍鱼 6粒 冷冻 90 克` is unstripped while its
# obvious sibling would strip. `李锦记`: the single `1.1c` condiment row, which
# §9.9's `staples.yaml` already names. `Smile`: the row is
# `Smile 荔枝甜粉红葡萄 一盒 2 磅`, where `荔枝` (Litchi) is the brand and `Smile`
# is an English word with no independent brand reading.
REVIEWED_UNSTRIPPED_PREFIXES: Final = frozenset(
    {
        # --- The token IS the food. Stripping it deletes the product. (30)
        "Banana",
        "Chicken",
        "Chocolate",
        "Frozen",
        "Mushroom",
        "Organic",
        "Pineapple",
        "Watermelon",
        "White",
        "Yellow",
        "Yuzu",
        "去骨牛小排",
        "田螺肉",
        "空心菜嫩苗",
        "红苋菜苗",
        "新鲜小叶茼蒿",
        "新鲜大白菜",
        "小白菜心",
        "荔枝香无籽葡萄",
        "棉花糖葡萄",
        "煎饼果子",
        "绿豆煎饼",
        "芝麻烧饼",
        "手作酱香饼",
        "香甜麻花",
        "香辣鸭血豆腐",
        "辣拌鱿鱼丝",
        "麻辣牛蛙",
        "火锅牛肉卷",
        "爆浆咸蛋黄肉松流沙可頌",
        # --- A qualifier, a year, or a brand fused with its own product. (17)
        "2026",
        "A级波斯黄瓜",
        "优质白桃礼盒",
        "精选顶级华盛顿红樱桃",
        "超值袋装加州脐橙",
        "超大号爆浆蓝莓",
        "佳沛红宝石奇异果",
        "大餐必备",
        "干饭湘味三宝",
        "老北京火锅素菜",
        "农家一碗香",
        "家乡味",
        "小巷口白糖馅蟹壳黄烧饼",
        "台湾旺旺浪味仙",
        "韩国紫苏叶",
        "【柴火大院】五常大米",
        "日本KAO花王",
        # --- A complete real brand, deliberately attached. (29)
        "Blue",
        "Canker-X",
        "Cascadian",
        "Health",
        "La",
        "Maple",
        "Poland",
        "Rawz",
        "Saturnbird",
        "Shout",
        "Smile",
        "Sumo",
        "Sun",
        "Surasang",
        "Umji's",
        "Waku",
        "八道",
        "万寿斋",
        "友臣",
        "北美鹿",
        "熟道",
        "波路梦",
        "小龙坎",
        "李锦记",
        "良品铺子",
        "白象",
        "江船长",
        "咔啰咔曼",
        "鲍师傅",
    }
)


def _write(directory: Path, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "brands.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _core_with(raw: str, pattern: re.Pattern[str]) -> str:
    """`normalize_ingredient()` with the brand step's pattern swapped out.

    The substitute is what makes a *missing* lexicon entry observable: rebuild
    the alternation without the entry under test and the row that entry was
    carrying stops stripping. Asserting on that is a proof the entry is
    load-bearing; asserting only on the shipped pattern is not.
    """
    return normalize._tidy(pattern.sub("", _pre_brand(raw), count=1))


def _pattern_for(entries: Iterable[str]) -> re.Pattern[str]:
    """`_BRAND_RE` rebuilt from `entries`, with the sort and the shape unchanged."""
    ordered = tuple(sorted(entries, key=lambda entry: (-len(entry.split()), -len(entry), entry)))
    return re.compile(
        r"^[ \t]*(?:" + "|".join(re.escape(entry) for entry in ordered) + r")[,\u3001]?(?=[ \t])",
        re.IGNORECASE,
    )


def _pre_brand(value: str) -> str:
    """`canonical_name` as it stands immediately before the brand step.

    A verbatim replay of steps 2-8a in `normalize_ingredient()`, because the
    leading token only means anything *after* those steps — `Wang Korea 有机去壳
    甘栗仁 60g*5 300 克` has no brand left in it until the size pass has run.
    `test_the_pre_brand_replay_matches_the_shipped_pipeline` asserts the replay
    is exact, so an added or reordered strip step fails here rather than
    silently moving the gate's baseline.
    """
    working = unicodedata.normalize("NFKC", value)
    working = normalize._strip_quotes_and_space(working)
    working = normalize._strip_wikilink_brackets(working)
    working = strip_leading_emoji_run(working)
    working = normalize._strip_tags(working)
    working = normalize._strip_date_markers(working)
    working = normalize._strip_flavour_breakdowns(working)
    working = normalize._strip_size(working)
    return normalize._tidy(working)


def _catalog_rows() -> list[str]:
    """The catalog's `canonical_name` column, from the committed snapshot.

    The raw JSON text, never a re-serialization of a parsed copy: the point of
    freezing these five columns is that the fixture is the producer's bytes. The
    `area` column is *not* consulted here — the gate asks what names the catalog
    has, and a non-food row is still a name the brand step will one day be
    handed. `tests/pantry/test_catalog.py` owns the `area` filter.
    """
    loaded = json.loads(CATALOG_SNAPSHOT.read_text(encoding="utf-8"))
    return [row[1] for row in loaded]


def _leading_token(pre_brand: str) -> str:
    """The leading token of a pre-brand name, under the gate's tokenization rule.

    A **leading token is the first whitespace-delimited word of a Latin-script
    name, and the entire unspaced run of a CJK name.** Both fall out of one rule
    — split on ASCII whitespace, take the first field — and neither is a choice
    about scripts, which is what makes the rule safe to state:

    * Latin names are space-separated, so the first field is one word. A CJK
      name is not: `空心菜嫩苗 0.95-1.05 磅` splits into `空心菜嫩苗` and the size.
    * The gate does not carve CJK into words, because there is no marker to carve
      on and the unit it would invent would be a fiction. `韩国紫苏叶` is one
      token, so a brand fused to its own product — `小巷口白糖馅蟹壳黄烧饼`,
      `【柴火大院】五常大米`, `日本KAO花王` — is one token, and no prefix of it can
      be an entry without deleting the product along with the brand.
    * The token is taken from the *pre-brand* string, so a row whose brand
      strips contributes nothing: the gate asks "does the brand step fire on
      this row", not "is this string in a list".

    An empty pre-brand name yields `""`, which no catalog row produces today; it
    is kept rather than skipped so an all-stripped row would surface as a token
    the maintainer must classify instead of vanishing from the comparison.
    """
    return pre_brand.split(" ")[0] if pre_brand else ""


def _uncovered_leading_tokens() -> set[str]:
    """Leading tokens the brand step leaves attached, first token only.

    A name counts as covered when `_BRAND_RE` itself matches it, so a multi-word
    brand (`Icelandic Provisions`, `Wang Korea`, `365 By Whole Foods Market`)
    credits its own first token instead of leaking `Icelandic` / `Wang` / `365`
    into this set. That is the honest question to ask: not "is this token in the
    list" but "does the brand step fire on this row".
    """
    tokens: set[str] = set()
    for name in _catalog_rows():
        pre = _pre_brand(name)
        if not normalize._BRAND_RE.match(pre):
            tokens.add(_leading_token(pre))
    return tokens


# --- 1. The file is well-formed -----------------------------------------


def test_the_packaged_file_exists_where_the_loader_looks() -> None:
    assert BRANDS_SOURCE.is_file()
    assert BRANDS_SOURCE.name == "brands.yaml"
    assert BRANDS_SOURCE.parent.name == "lexicon"


def test_the_path_is_inside_the_package_not_the_working_directory(
    tmp_path: Path,
) -> None:
    """A CWD-relative path passes every source-tree test and fails from a wheel.

    `Path("app/recipes/lexicon/brands.yaml")` resolves fine while pytest runs
    from the repo root and is wrong the moment the app is started from a
    LaunchAgent's `WorkingDirectory`, or installed as a wheel where the file
    lives under `site-packages/`. The property that survives both is the one
    asserted here: the resolved path is the package's own directory, and the
    loader still finds the file with the working directory somewhere else
    entirely. This is the constraint ticket #2's `package-data` entry depends
    on — if the path were CWD-relative, shipping the file would not help.
    """
    package_root = Path(normalize.__file__).resolve().parent
    assert Path(BRANDS_SOURCE).resolve().is_relative_to(package_root)
    assert Path(BRANDS_SOURCE).resolve() == (
        package_root / "lexicon" / "brands.yaml"
    )

    script = (
        "from app.recipes.brand_lexicon import BRANDS_SOURCE, load_brand_lexicon\n"
        "import os\n"
        "assert BRANDS_SOURCE.is_file(), BRANDS_SOURCE\n"
        "assert len(load_brand_lexicon()) == 56, load_brand_lexicon()\n"
        "print(os.getcwd())\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(package_root.parent.parent)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert Path(completed.stdout.strip()).resolve() == tmp_path.resolve()


def test_the_file_declares_the_supported_schema_version() -> None:
    document = yaml.safe_load(BRANDS_SOURCE.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    assert document["version"] == brand_lexicon.SCHEMA_VERSION
    assert document["version"] == 1
    assert set(document) == {"version", "brands"}


def test_the_loaded_lexicon_is_the_file_and_nothing_else() -> None:
    document = yaml.safe_load(BRANDS_SOURCE.read_text(encoding="utf-8"))
    entries = document["brands"]
    assert load_brand_lexicon(BRANDS_SOURCE) == frozenset(entries)
    assert BRAND_LEXICON == frozenset(entries)
    assert isinstance(BRAND_LEXICON, frozenset)


def test_every_entry_is_a_non_empty_unpadded_string_without_control_characters() -> None:
    document = yaml.safe_load(BRANDS_SOURCE.read_text(encoding="utf-8"))
    entries = document["brands"]
    assert isinstance(entries, list)
    assert entries, "an empty lexicon strips nothing and is never a valid answer"
    for index, entry in enumerate(entries):
        assert isinstance(entry, str), index
        assert entry, index
        assert entry == entry.strip(), entry
        assert not any(ord(c) < 32 or ord(c) == 127 for c in entry), repr(entry)


def test_there_are_no_case_insensitive_duplicates() -> None:
    document = yaml.safe_load(BRANDS_SOURCE.read_text(encoding="utf-8"))
    folded: dict[str, list[str]] = {}
    for entry in document["brands"]:
        folded.setdefault(entry.casefold(), []).append(entry)
    duplicates = {key: group for key, group in folded.items() if len(group) > 1}
    assert not duplicates, duplicates


def test_the_entries_that_look_redundant_are_separate_and_load_bearing() -> None:
    """`Chobani` / `Chobani®` and `POM` / `Wonderful` are not duplicates.

    Each is proved load-bearing the only way a redundancy question can be
    answered: by rebuilding the alternation *without* the entry and showing the
    row it was carrying stops stripping. Membership assertions and `casefold()`
    comparisons cannot distinguish "load-bearing" from "unused", so they are not
    what this test rests on.

    `POM` and `Wonderful` are different brands that co-occur, and the brand step
    removes exactly ONE leading entry per name, so
    `POM Wonderful 100% Pomegranate Juice` is not the two stripped — it is `POM`
    stripped and `Wonderful` still standing as part of the product. Collapsing
    `POM` under `Wonderful` would leave that row unstripped entirely, which is
    the breakage. Merging is therefore not a tidy-up; it is a behaviour change
    that loses a row the lexicon exists to cover.
    """
    assert "Chobani" in BRAND_LEXICON
    assert "Chobani®" in BRAND_LEXICON
    assert "POM" in BRAND_LEXICON
    assert "Wonderful" in BRAND_LEXICON

    pom_row = "POM Wonderful 100% Pomegranate Juice, 16 Ounce Bottle"
    assert normalize_ingredient(pom_row) == "Wonderful 100% Pomegranate Juice Bottle"
    without_pom = _pattern_for(BRAND_LEXICON - {"POM"})
    assert _core_with(pom_row, without_pom) == "POM Wonderful 100% Pomegranate Juice Bottle"

    wonderful_row = "Wonderful Pistachios, 10 Oz"
    assert normalize_ingredient(wonderful_row) == "Pistachios"
    without_wonderful = _pattern_for(BRAND_LEXICON - {"Wonderful"})
    assert _core_with(wonderful_row, without_wonderful) == "Wonderful Pistachios"

    # `®` is a distinct codepoint and sits between the entry and the required
    # space, so neither spelling can be stripped by the other's entry. Each one
    # is reachable only through itself.
    assert "Chobani" != "Chobani®"
    assert "Chobani".casefold() != "Chobani®".casefold()
    plain = "Chobani 20g Protein Lowfat Greek Yogurt Vanilla 6.7oz"
    registered = "Chobani® 20g Protein Lowfat Greek Yogurt Vanilla 6.7oz"
    assert normalize_ingredient(plain) == "Protein Lowfat Greek Yogurt Vanilla"
    assert normalize_ingredient(registered) == "Protein Lowfat Greek Yogurt Vanilla"
    assert _core_with(plain, _pattern_for(BRAND_LEXICON - {"Chobani"})) == (
        "Chobani Protein Lowfat Greek Yogurt Vanilla"
    )
    assert _core_with(registered, _pattern_for(BRAND_LEXICON - {"Chobani®"})) == (
        "Chobani® Protein Lowfat Greek Yogurt Vanilla"
    )


def test_beekeepers_two_entries_differ_by_the_apostrophe_not_the_case() -> None:
    assert "Beekeeper's" in BRAND_LEXICON
    assert "BEEKEEPERS" in BRAND_LEXICON
    assert "Beekeeper's".casefold() != "BEEKEEPERS".casefold()
    assert normalize_ingredient("BEEKEEPERS Manuka Honey 250g") == "Manuka Honey"


# --- 2. The loader fails closed, and is read once -----------------------


def test_the_brand_step_does_not_read_the_file_per_call() -> None:
    """The hot path is pure: the lexicon is bound once, at module import."""
    assert "load_brand_lexicon" not in normalize_ingredient.__code__.co_names
    assert BRAND_LEXICON is normalize.BRAND_LEXICON
    assert isinstance(normalize._BRAND_RE, re.Pattern)


def test_normalizing_a_name_never_calls_the_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The caching claim, observed rather than read off the signature.

    Inspecting `co_names` proves the loader is not *named* in the function; it
    cannot prove the compiled pattern is not rebuilt underneath it. Replacing the
    loader with one that raises does: every call below has to be served entirely
    from what was bound at import, or it fails.
    """
    monkeypatch.setattr(
        brand_lexicon,
        "load_brand_lexicon",
        lambda *args, **kwargs: pytest.fail("the lexicon was re-read on the hot path"),
    )
    for name in (
        "Wang Korea 有机去壳甘栗仁 60g*5 300 克",
        "365 By Whole Foods Market, Organic 1% Milk",
        "Mushroom Dried Morel Mushrooms",
        "优质白桃礼盒",
    ):
        assert isinstance(normalize_ingredient(name), str), name
    assert normalize_ingredient("Wang Korea 有机去壳甘栗仁 60g*5 300 克") == "有机去壳甘栗仁"
    assert normalize_ingredient("365 By Whole Foods Market, Organic 1% Milk") == (
        "Organic 1% Milk"
    )
    assert normalize_ingredient("Mushroom Dried Morel Mushrooms") == (
        "Mushroom Dried Morel Mushrooms"
    )


def test_the_loader_reads_its_source_exactly_once() -> None:
    reads: list[str] = []

    class CountingSource:
        def is_file(self) -> bool:
            return True

        def read_text(self, encoding: str = "utf-8") -> str:
            reads.append(encoding)
            return "version: 1\nbrands: [Solo]\n"

    assert load_brand_lexicon(CountingSource()) == frozenset({"Solo"})  # type: ignore[arg-type]
    assert reads == ["utf-8"]


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("", "empty file"),
        ("version: 1\nbrands: [A]\n\tbroken: indent\n", "unparseable YAML"),
        ("- version\n- brands\n", "top-level sequence, not a mapping"),
        ("version: 1\nbrands: [A]\nbrands_extra: 1\n", "unknown top-level key"),
        ("brands: [A]\n", "no version"),
        ("version: 2\nbrands: [A]\n", "a future schema revision"),
        ('version: "1"\nbrands: [A]\n', "a quoted version"),
        ("version: 1\n", "no brands key"),
        ("version: 1\nbrands: Solo\n", "brands is a string, not a list"),
        ("version: 1\nbrands: []\n", "an empty lexicon"),
        ("version: 1\nbrands: ['']\n", "an empty entry"),
        ("version: 1\nbrands: ['  Padded  ']\n", "a padded entry"),
        # A real newline inside single quotes is a YAML *fold*, so it parses to a
        # space and never reaches the loader as a control character. The escape
        # below is what actually puts one inside the entry.
        ('version: 1\nbrands: ["Two\\tLines"]\n', "an entry with a control character"),
        ("version: 1\nbrands: [7]\n", "a numeric entry"),
        ("version: 1\nbrands: [null]\n", "a null entry"),
        ("version: 1\nbrands: [[Nested]]\n", "a nested sequence entry"),
        ("version: 1\nbrands: [{name: Keyed}]\n", "a mapping entry, not a bare string"),
        ("version: 1\nbrands: [Chobani, chobani]\n", "a case-insensitive duplicate"),
        ("version: 1\nbrands: [Chobani, Chobani]\n", "an exact duplicate"),
    ],
)
def test_a_malformed_lexicon_fails_closed(
    tmp_path: Path, body: str, reason: str
) -> None:
    with pytest.raises(ConfigurationError):
        load_brand_lexicon(_write(tmp_path, body))


def test_a_missing_lexicon_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_brand_lexicon(tmp_path / "absent.yaml")


def test_a_control_character_entry_is_valid_yaml_the_loader_refuses(tmp_path: Path) -> None:
    """The refusal is the loader's, not the parser's — which is the whole point.

    A newline written literally inside a single-quoted YAML scalar is a *fold*:
    it parses to a space and the resulting entry is a perfectly ordinary brand.
    Only an escape puts a real control character in the value, and that is the
    case the entry check exists for, so the test has to distinguish the two or it
    is testing nothing.
    """
    folded = _write(tmp_path, "version: 1\nbrands: ['Two\nLines']\n")
    assert yaml.safe_load(folded.read_text(encoding="utf-8")) == {
        "version": 1,
        "brands": ["Two Lines"],
    }
    assert load_brand_lexicon(folded) == frozenset({"Two Lines"})

    raw = _write(tmp_path / "raw", 'version: 1\nbrands: ["Two\\tLines"]\n')
    assert yaml.safe_load(raw.read_text(encoding="utf-8")) == {
        "version": 1,
        "brands": ["Two\tLines"],
    }
    with pytest.raises(ConfigurationError) as raised:
        load_brand_lexicon(raw)
    assert "control_character" in str(raised.value)


def test_an_undecodable_lexicon_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "brands.yaml"
    path.write_bytes(b"version: 1\nbrands: [\xff\xfe]\n")
    with pytest.raises(ConfigurationError):
        load_brand_lexicon(path)


def test_the_error_is_the_repo_fail_closed_type() -> None:
    assert issubclass(ConfigurationError, ValueError)


def test_importing_normalize_refuses_to_start_without_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The guarantee is at import, which is what a startup-time failure needs.

    Reload is used deliberately: it is the only way to observe what a fresh
    process would do. The final reload restores the module so the rest of the
    suite sees the real lexicon.
    """
    monkeypatch.setattr(brand_lexicon, "BRANDS_SOURCE", tmp_path / "absent.yaml")
    try:
        with pytest.raises(ConfigurationError):
            importlib.reload(normalize)
    finally:
        monkeypatch.undo()
        importlib.reload(normalize)
    assert normalize.BRAND_LEXICON == BRAND_LEXICON
    assert normalize_ingredient("Wang Korea 有机去壳甘栗仁 60g*5 300 克") == "有机去壳甘栗仁"


def test_a_malformed_lexicon_also_refuses_to_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _write(tmp_path, "version: 1\nbrands: []\n")
    monkeypatch.setattr(brand_lexicon, "BRANDS_SOURCE", path)
    try:
        with pytest.raises(ConfigurationError):
            importlib.reload(normalize)
    finally:
        monkeypatch.undo()
        importlib.reload(normalize)
    assert normalize.BRAND_LEXICON == BRAND_LEXICON


# --- 3. The file does not drift from the catalog -------------------------


def test_the_catalog_is_the_178_row_producer_asset() -> None:
    rows = _catalog_rows()
    assert len(rows) == 178
    assert "Wang Korea 有机去壳甘栗仁 60g*5 300 克" in rows


def test_the_pre_brand_replay_matches_the_shipped_pipeline() -> None:
    """The gate's baseline is a replay of the pipeline, so the replay is checked.

    `_pre_brand` duplicates steps 2-8a of `normalize_ingredient()` in order to
    read the leading token off the string the brand step actually saw. If a strip
    step is added, removed, or reordered, the duplicate stops being a replay and
    the drift gate would quietly measure a different pipeline than the one that
    ships. Comparing the replay's output against the real function on all 178
    rows turns that silent divergence into a failure here.
    """
    for name in _catalog_rows():
        assert _core_with(name, normalize._BRAND_RE) == normalize_ingredient(name), name


def test_the_gate_asks_whether_the_brand_step_fires_not_whether_a_string_is_listed() -> None:
    """A multi-word brand credits its own first token instead of leaking it.

    This is the distinction that makes the gate answerable. `Wang Korea 有机去壳
    甘栗仁` would contribute `Wang` — a token that is in no list and is not a
    brand on its own — if coverage were decided by membership. Deciding it by
    `_BRAND_RE.match` on the pre-brand string means the question is the one the
    code actually answers, and 53 of the 129 distinct leading tokens come back
    covered that way.
    """
    assert "Wang Korea" in BRAND_LEXICON
    assert "Wang" not in BRAND_LEXICON
    assert "Wang" not in REVIEWED_UNSTRIPPED_PREFIXES
    assert "Wang" not in _uncovered_leading_tokens()
    assert normalize._BRAND_RE.match(_pre_brand("Wang Korea 有机去壳甘栗仁 60g*5 300 克"))


def test_the_allowlist_is_grouped_by_classification_and_the_counts_hold() -> None:
    """The three groups in the allowlist are a claim about the data, so assert it.

    A group boundary that silently reclassifies a token would leave the prose
    wrong while every other test still passed, which is the failure mode a
    classification comment is most exposed to. The counts are pinned so a
    reclassification has to be made in the set *and* in its group, in one diff.
    """
    food = {
        "Banana", "Chicken", "Chocolate", "Frozen", "Mushroom", "Organic",
        "Pineapple", "Watermelon", "White", "Yellow", "Yuzu",
        "去骨牛小排", "田螺肉", "空心菜嫩苗", "红苋菜苗", "新鲜小叶茼蒿", "新鲜大白菜",
        "小白菜心", "荔枝香无籽葡萄", "棉花糖葡萄", "煎饼果子", "绿豆煎饼", "芝麻烧饼",
        "手作酱香饼", "香甜麻花", "香辣鸭血豆腐", "辣拌鱿鱼丝", "麻辣牛蛙", "火锅牛肉卷",
        "爆浆咸蛋黄肉松流沙可頌",
    }
    qualifier = {
        "2026", "A级波斯黄瓜", "优质白桃礼盒", "精选顶级华盛顿红樱桃", "超值袋装加州脐橙",
        "超大号爆浆蓝莓", "佳沛红宝石奇异果", "大餐必备", "干饭湘味三宝", "老北京火锅素菜",
        "农家一碗香", "家乡味", "小巷口白糖馅蟹壳黄烧饼", "台湾旺旺浪味仙", "韩国紫苏叶",
        "【柴火大院】五常大米", "日本KAO花王",
    }
    assert food | qualifier <= REVIEWED_UNSTRIPPED_PREFIXES
    assert REVIEWED_UNSTRIPPED_PREFIXES == food | qualifier | {
        "Blue", "Canker-X", "Cascadian", "Health", "La", "Maple", "Poland", "Rawz",
        "Saturnbird", "Shout", "Smile", "Sumo", "Sun", "Surasang", "Umji's", "Waku",
        "八道", "万寿斋", "友臣", "北美鹿", "熟道", "波路梦", "小龙坎", "李锦记", "良品铺子",
        "白象", "江船长", "咔啰咔曼", "鲍师傅",
    }
    assert (len(food), len(qualifier)) == (30, 17)
    assert len(REVIEWED_UNSTRIPPED_PREFIXES) == 76


def _unreviewed(uncovered: Iterable[str]) -> set[str]:
    """The tokens the gate reports: in the catalog, in neither authority."""
    return set(uncovered) - REVIEWED_UNSTRIPPED_PREFIXES


def test_a_brand_fused_to_its_own_product_cannot_be_made_an_entry() -> None:
    """Why five of the 76 are allowlist entries and not brands.yaml entries.

    `小巷口` IS in the lexicon, and `旺旺`, `柴火大院`, `KAO` and `佳沛` are brands
    too, but the token the brand step sees is the brand *and* the product welded
    together: `小巷口白糖馅蟹壳黄烧饼`, `台湾旺旺浪味仙`, `【柴火大院】五常大米`,
    `日本KAO花王`, `佳沛红宝石奇异果`. `_BRAND_RE` is anchored and consumes the
    whole matched entry plus one separator, so the only entry that could fire on
    such a row is the entire product name — which would leave an empty core, not
    a cleaner one. This is a limit of the leading-token rule, not a gap in the
    list, and it is the concrete form of the spec's own proof that position
    cannot disambiguate a brand.
    """
    fused = (
        ("小巷口白糖馅蟹壳黄烧饼 冷冻 360 克", "小巷口白糖馅蟹壳黄烧饼 冷冻"),
        ("台湾旺旺浪味仙 熔岩辣起司口味 86 克", "台湾旺旺浪味仙 熔岩辣起司口味"),
        ("佳沛红宝石奇异果 1 磅", "佳沛红宝石奇异果"),
        (
            "【柴火大院】五常大米 原粮稻花香2号 特供米 1000 克",
            "【柴火大院】五常大米 原粮稻花香2号 特供米",
        ),
        (
            "日本KAO花王 LAURIER乐而雅 夜用安心裤棉柔裤型卫生巾短裤 M-L 5片 1 份",
            "日本KAO花王 LAURIER乐而雅 夜用安心裤棉柔裤型卫生巾短裤 M-L",
        ),
    )
    for name, core in fused:
        assert normalize_ingredient(name) == core, name
        assert normalize._BRAND_RE.match(_pre_brand(name)) is None, name
        assert _leading_token(_pre_brand(name)) in REVIEWED_UNSTRIPPED_PREFIXES, name
    for brand in ("旺旺", "柴火大院", "KAO", "花王", "佳沛"):
        assert brand not in BRAND_LEXICON, brand
    assert "小巷口" in BRAND_LEXICON  # the bare brand, for a name that leads with it
    assert _unreviewed({_leading_token(_pre_brand(name)) for name, _ in fused}) == set()


def test_catalog_brands_are_all_reviewed() -> None:
    """A leading token the catalog has and the lexicon lacks must be reviewed.

    This is the drift gate the user asked for. It cannot be a plain "every
    leading token is a lexicon entry" assertion: 76 of the 129 distinct leading
    tokens are deliberately not stripped, and a naive form of this test would
    fail on the maintainer's own data. So the assertion is two-sided — the
    uncovered set must equal a reviewed list, and a token in both the lexicon
    and that list is a stale allowlist entry rather than a pass.

    It runs in CI. The gate used to read the producer's `pantry_items.db` from a
    sibling checkout and skip when that was absent, so it was local-only: a brand
    ingested on one machine failed here and was invisible everywhere else, which
    is the worst possible shape for a gate. It reads
    `tests/fixtures/pantry_items_snapshot.json` instead — the same 178 rows,
    committed, regenerated deliberately in its own commit (F14).
    """
    unknown = _unreviewed(_uncovered_leading_tokens())
    assert not unknown, (
        "brands.yaml does not cover these catalog leading tokens; add each to "
        f"brands.yaml or to REVIEWED_UNSTRIPPED_PREFIXES with a reason: {sorted(unknown)}"
    )


def test_no_reviewed_prefix_is_also_a_lexicon_entry() -> None:
    stale = {
        prefix
        for prefix in REVIEWED_UNSTRIPPED_PREFIXES
        if any(prefix.casefold() == entry.casefold() for entry in BRAND_LEXICON)
    }
    assert not stale, f"these are lexicon entries; remove them from the allowlist: {sorted(stale)}"


def test_the_allowlist_matches_the_catalog_exactly() -> None:
    """The other direction: an allowlist that is too big is a promise nobody kept.

    `test_catalog_brands_are_all_reviewed` already fails on a token in neither
    set. This fails on a token in the allowlist that the catalog no longer
    produces, which is how an allowlist rots after a catalog re-ingest — silently
    accepted, because a subset assertion still passes. Equality in both
    directions is what makes the allowlist a record of this catalog rather than
    a growing list of good intentions.
    """
    assert _uncovered_leading_tokens() == REVIEWED_UNSTRIPPED_PREFIXES


def test_the_drift_gate_actually_sees_a_new_brand() -> None:
    """A brand the catalog gains and the lexicon lacks must fail the gate.

    Everything above this is only ever observed passing, and a gate that has
    never been seen to fail is not known to be a gate. This drives the gate's own
    arithmetic on a name the catalog does not contain, so both halves of the
    contract are pinned on one synthetic row: an allowlisted token is not
    reported, and a token in neither authority is — with the token named, which
    is the whole value of the failure message.
    """
    known_noise = "Frozen Blueberry Wobble, 1 Lb"
    assert normalize._BRAND_RE.match(_pre_brand(known_noise)) is None
    assert _unreviewed([_leading_token(_pre_brand(known_noise))]) == set()

    new_brand = "Yeehaw Produce Ranch Sweet Corn, 4 Oz"
    assert normalize._BRAND_RE.match(_pre_brand(new_brand)) is None
    leaked = _leading_token(_pre_brand(new_brand))
    assert leaked == "Yeehaw"
    assert leaked not in BRAND_LEXICON
    assert _unreviewed([leaked]) == {"Yeehaw"}


def test_the_drift_gate_reads_the_committed_snapshot_and_not_a_live_database() -> None:
    """The gate's source is frozen data, asserted rather than documented.

    The 129/53/76 split below is only meaningful against a known catalog, and a
    gate whose source silently went back to a sibling checkout on one machine
    would keep passing on that machine while CI ran nothing. So: the snapshot is
    a file in this repository, it holds the 178 rows, and the module holds no
    `sqlite3` import at all — the drift gate cannot reach a database even by
    accident.
    """
    assert CATALOG_SNAPSHOT.is_file()
    assert CATALOG_SNAPSHOT.is_relative_to(Path(__file__).resolve().parents[2])
    imported: set[str] = set()
    for node in ast.walk(ast.parse(Path(__file__).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "sqlite3" not in imported, "the drift gate must not open a database"
    assert "PRODUCER_CATALOG" not in globals(), (
        "the gate must not resolve the sibling repository's catalog path again"
    )
    assert len(_catalog_rows()) == 178


def test_the_drift_gate_numbers_are_the_ones_this_revision_was_reviewed_at() -> None:
    """The split between covered and uncovered, so a change in either is visible.

    Not a correctness assertion — every one of these tokens is already pinned
    somewhere else. It is here because the numbers are what a maintainer quotes
    when deciding whether the drift failure is a new brand or a known omission,
    and a count that drifts quietly is worse than no count.
    """
    tokens = {_leading_token(_pre_brand(name)) for name in _catalog_rows()}
    assert len(tokens) == 129
    assert len(tokens - _uncovered_leading_tokens()) == 53
    assert len(_uncovered_leading_tokens()) == 76
    assert sum(1 for name in _catalog_rows() if normalize._BRAND_RE.match(_pre_brand(name))) == 85

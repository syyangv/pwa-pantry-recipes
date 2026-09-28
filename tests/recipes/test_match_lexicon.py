"""`synonyms.yaml` and `staples.yaml` as data: schema, fail-closed loading, drift.

Modelled directly on `test_brand_lexicon.py`, because the three files have the
same three obligations and the same reason to be tested together:

1. **The file is well-formed** — schema version, entry shape, no duplicates, no
   Pantry Item id. These are hand-edited artifacts, so the mistakes a code
   literal cannot make (an unquoted `families: [1.1]` that PyYAML reads as the
   float `1.1`, a padded entry, an entry pasted twice) are exactly the ones that
   have to be caught.
2. **The loader fails closed** — a missing or malformed file raises
   `ConfigurationError` rather than degrading to an empty lexicon. The two
   directions are asymmetric, and that is why one test cannot cover both: an
   empty `synonyms.yaml` costs vocabulary and every other tier still refuses to
   guess, so the failure is a *miss*; a malformed one can **widen** the
   category-family guard and admit a snack row for a condiment, which is the
   false-positive class D1 exists to prevent.
3. **The files do not drift from the corpus** — asserted in **both**
   directions, the way the brand-drift gate is, so a new collision fails as
   unreviewed and a stale allowlist entry fails as a promise nobody kept.

Nothing here reads a live path: the corpus is the committed
`tests/fixtures/pantry_items_snapshot.json` and the recipes are the frozen
`tests/fixtures/real_recipes/*.md`.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest
import yaml

from app.config import ConfigurationError
from app.recipes import match_lexicon
from app.recipes.match_lexicon import (
    MAX_NAME_CHARS,
    STAPLES_SOURCE,
    STAPLES_VERSION,
    SYNONYMS_SOURCE,
    SYNONYMS_VERSION,
    StaplesLexicon,
    load_staples,
    load_synonyms,
    synonym_index,
)
from app.recipes.matcher import SYNONYM_INDEX, SYNONYMS

from .corpus import (
    CANDIDATE_ROWS,
    CATALOG_SNAPSHOT,
    SEASONING_NAMES,
    committed_catalog,
    real_names_for,
)

CATALOG = committed_catalog()

#: §9.9's list, in §9.9's order. Asserted by value because the *order* is the
#: file's documented order and a reordering is a diff a reviewer should see.
SPEC_STAPLES: Final[tuple[str, ...]] = (
    "油", "盐", "糖", "生抽", "老抽", "醋", "芝麻油", "蒜", "葱", "姜", "香菜",
    "鸡精", "黑胡椒", "鱼露", "小米椒", "料酒", "dashi", "面粉", "米", "淀粉",
    "蚝油", "蒸鱼豉油", "韩式辣椒粉", "新奥尔良腌料", "窑鸡粉",
)
#: The synonym entries that name a food the 178-row catalog has never held. A
#: curated lexicon covering foods the user has not bought yet is the point — a
#: list restricted to what is already in the catalog would not be curated at all —
#: and pinning the set means a family-code typo cannot silently move an entry
#: between "reachable" and "not reachable" without failing here.
REVIEWED_UNREACHABLE_ENTRIES: Final[tuple[str, ...]] = (
    "Arugula", "Baby Chinese Cabbage", "Basil", "Black Fungus", "Cabbage",
    "Chicken Thigh", "focaccia", "Ginger", "Lemon", "Lime", "Potato", "Rice",
    "Shiitake", "Shishito", "Stracciatella", "Sweet Potato", "Tomato",
)
#: §9.9's `class_suffixes`, verbatim.
SPEC_CLASS_SUFFIXES: Final[tuple[str, ...]] = (
    "酱油", "醋", "油", "粉", "酒", "汁", "酱", "盐", "糖",
)
#: The English names §9.8 lists, which is also the reviewed Latin-name set the
#: transliteration gate in `test_matcher.py` uses.
SPEC_LATIN_NAMES: Final[frozenset[str]] = frozenset(
    {
        "Kale", "Arugula", "Shiitake", "Shiitake Mushroom", "Mackerel", "Shishito",
        "Basil", "focaccia", "Stracciatella", "Clam", "Broccoli", "Potato",
        "Sweet Potato", "Egg", "Tomato", "Rice", "Chicken Wing", "Chicken Wings",
        "Chicken Thigh", "Cabbage", "Water Spinach", "Amaranth",
        "Garland Chrysanthemum", "Baby Chinese Cabbage", "Black Fungus",
        "Mushroom", "Lemon", "Lime", "Ginger", "Garlic", "Sesame",
    }
)


def _write_synonyms(directory: Path, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "synonyms.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _write_staples(directory: Path, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "staples.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# --- 1. the shipped files are well-formed ----------------------------------


def test_both_packaged_files_exist_where_the_loaders_look() -> None:
    for source, name in ((SYNONYMS_SOURCE, "synonyms.yaml"), (STAPLES_SOURCE, "staples.yaml")):
        assert source.is_file(), source
        assert Path(source).resolve().name == name
        assert Path(source).resolve().parent.name == "lexicon"


def test_the_paths_are_inside_the_package_not_the_working_directory(
    tmp_path: Path,
) -> None:
    """A CWD-relative path passes every source-tree test and fails from a wheel.

    `Path("app/recipes/lexicon/synonyms.yaml")` resolves fine while pytest runs
    from the repo root and is wrong the moment the app starts from a
    LaunchAgent's working directory, or installed as a wheel where the file lives
    under `site-packages/`. The property that survives both is that the resolved
    path is the package's own directory — which is also the constraint
    `pyproject.toml`'s `"app.recipes" = ["lexicon/*.yaml"]` glob depends on.
    """
    package_root = Path(match_lexicon.__file__).resolve().parent
    assert Path(SYNONYMS_SOURCE).resolve().is_relative_to(package_root)
    assert Path(STAPLES_SOURCE).resolve().is_relative_to(package_root)

    script = (
        "from app.recipes.match_lexicon import STAPLES_SOURCE, SYNONYMS_SOURCE\n"
        "from app.recipes.match_lexicon import load_staples, load_synonyms\n"
        "import os\n"
        "assert SYNONYMS_SOURCE.is_file() and STAPLES_SOURCE.is_file()\n"
        "assert len(load_synonyms()) >= 29, load_synonyms()\n"
        "assert len(load_staples().staples) == 25\n"
        "print(os.getcwd())\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={**__import__("os").environ, "PYTHONPATH": str(package_root.parent.parent)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert Path(completed.stdout.strip()).resolve() == tmp_path.resolve()


def test_the_package_data_glob_already_ships_both_files() -> None:
    """`"app.recipes" = ["lexicon/*.yaml"]` is a glob, so no packaging change is
    needed for a new lexicon file — asserted rather than assumed.

    The glob matches `brands.yaml`, `synonyms.yaml` and `staples.yaml`, so this
    ticket adds two data files and touches nothing in `pyproject.toml`. If the
    entry were ever narrowed to a literal list, this test is what would say so.
    """
    pyproject = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert '"app.recipes" = ["lexicon/*.yaml"]' in pyproject
    assert "lexicon" in pyproject and "*.yaml" in pyproject
    shipped = sorted(
        path.name for path in (Path(match_lexicon.__file__).parent / "lexicon").glob("*.yaml")
    )
    assert shipped == ["brands.yaml", "staples.yaml", "synonyms.yaml"]


def test_the_schemas_declare_the_versions_the_loaders_accept() -> None:
    synonyms = yaml.safe_load(SYNONYMS_SOURCE.read_text(encoding="utf-8"))
    staples = yaml.safe_load(STAPLES_SOURCE.read_text(encoding="utf-8"))
    assert set(synonyms) == {"version", "synonyms"}
    assert synonyms["version"] == SYNONYMS_VERSION == 1
    assert set(staples) == {"version", "staples", "class_suffixes"}
    assert staples["version"] == STAPLES_VERSION == 1


def test_the_loaded_lexicons_are_the_files_and_nothing_else() -> None:
    staples = yaml.safe_load(STAPLES_SOURCE.read_text(encoding="utf-8"))
    assert tuple(load_synonyms(SYNONYMS_SOURCE)) == tuple(SYNONYMS)
    assert SYNONYMS == tuple(load_synonyms(SYNONYMS_SOURCE))
    lexicon = load_staples(STAPLES_SOURCE)
    assert lexicon.staples == frozenset(staples["staples"])
    assert lexicon.class_suffixes == tuple(staples["class_suffixes"])


def test_the_staples_list_is_nine_9s_specification_verbatim() -> None:
    """25 rows, §9.9's list, §9.9's order, and §9.9's nine class suffixes.

    Pinned by value because the empirical claim attached to this file — that
    `调料` coverage goes from ~5% to ~90% because Pantry Category `1.1c` has one
    row in 178 — is only true for this list. A dropped row is a silent coverage
    regression; a reordered one is a diff a reviewer should see.
    """
    lexicon = load_staples(STAPLES_SOURCE)
    assert tuple(lexicon.staples) != SPEC_STAPLES  # a frozenset has no order
    document = yaml.safe_load(STAPLES_SOURCE.read_text(encoding="utf-8"))
    assert tuple(document["staples"]) == SPEC_STAPLES
    assert len(lexicon.staples) == 25
    assert lexicon.class_suffixes == SPEC_CLASS_SUFFIXES


def test_the_class_suffix_rule_reaches_what_the_spec_lists() -> None:
    """`X酱油 → 酱油` and its eight siblings, on the real `调料` values."""
    lexicon = load_staples(STAPLES_SOURCE)
    assert lexicon.is_staple("调味酱油") is True
    assert lexicon.is_staple("蒸鱼豉油") is True
    assert lexicon.is_staple("老抽") is True
    # A **suffix** rule and not a substring one.
    assert lexicon.is_staple("酱油味小鱼") is False
    assert lexicon.is_staple("豉油") is True, "it ends in 油, which is itself a class suffix"
    # …and the whole-vs-substring distinction the live data turns on.
    assert lexicon.is_staple("芝麻油") is True
    assert lexicon.is_staple("芝麻") is False
    # Case and width folding, because the same name can be written either way.
    assert lexicon.is_staple("DASHI") is lexicon.is_staple("dashi") is True
    assert lexicon.is_staple("Ｄａｓｈｉ") is True


def test_every_synonym_entry_is_complete_and_well_formed() -> None:
    """No entry may be missing a field, and `families` is never optional.

    An entry without `families` would let any Pantry Category family through,
    which is the category-family guard switched off while still looking like a
    guard. So the loader refuses one and this test says the shipped file has
    none.
    """
    assert SYNONYMS
    for entry in SYNONYMS:
        assert entry.canonical
        assert entry.canonical == entry.canonical.strip()
        assert entry.match, entry.canonical
        assert entry.families, entry.canonical
        assert entry.canonical in entry.names
        for name in entry.names:
            assert name and name == name.strip()
            assert len(name) <= MAX_NAME_CHARS
            assert not any(ord(character) < 32 or ord(character) == 127 for character in name)
            assert not name.isdigit(), name
        for family in entry.families:
            assert match_lexicon._CATEGORY_CODE.match(family), family


def test_no_name_appears_in_two_entries() -> None:
    """A name that is ambiguous across entries makes the entries unobservable.

    `Lemon`↔`柠檬` and `Lime`↔`青柠` being separate entries is only a *fact* if
    no name is shared, so this is asserted over the whole file rather than for
    the citrus pair alone.
    """
    seen: dict[str, str] = {}
    for entry in SYNONYMS:
        for name in entry.names:
            key = name.casefold()
            assert key not in seen, f"{name} is in both {seen.get(key)} and {entry.canonical}"
            seen[key] = entry.canonical
    assert len(seen) == sum(len(entry.names) for entry in SYNONYMS)


def test_lemon_and_lime_stay_separate_entries() -> None:
    """The one merge §9.8 forbids, asserted on the shipped file.

    `🍋‍🟩` parses to `青柠`. A `contains` rule that let `青柠` reach a `柠檬` row
    would substitute one fruit for another, and the only symptom would be a chip
    claiming you have a lemon.
    """
    lemon = SYNONYM_INDEX["lemon"]
    lime = SYNONYM_INDEX["lime"]
    assert lemon is not lime
    assert "柠檬" in lemon.names and "青柠" not in lemon.names
    assert "青柠" in lime.names and "柠檬" not in lime.names
    assert lemon.canonical == "Lemon" and lime.canonical == "Lime"


def test_the_synonym_index_is_built_once_and_is_the_one_the_ladder_reads() -> None:
    """The hot path never rebuilds the index or re-reads either file.

    Inspecting `co_names` proves the loader is not *named*; replacing the loaders
    with ones that raise proves the file is not read underneath the ladder, which
    is the claim that matters on a request path.
    """
    assert SYNONYM_INDEX == synonym_index(SYNONYMS)
    assert "load_synonyms" not in match_lexicon.synonym_index.__code__.co_names
    for name in list(SYNONYM_INDEX)[:5]:
        assert isinstance(SYNONYM_INDEX[name].canonical, str)


def test_resolving_every_recipe_name_never_re_reads_either_lexicon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every call above must be served entirely from what was bound at import."""
    monkeypatch.setattr(
        match_lexicon,
        "load_synonyms",
        lambda *args, **kwargs: pytest.fail("synonyms.yaml was re-read on the hot path"),
    )
    monkeypatch.setattr(
        match_lexicon,
        "load_staples",
        lambda *args, **kwargs: pytest.fail("staples.yaml was re-read on the hot path"),
    )
    from app.recipes.ingredients import parse_ingredient_value
    from app.recipes.matcher import resolve_ingredient

    for source in ("材料", "调料"):
        for name in real_names_for(source):
            result = resolve_ingredient(parse_ingredient_value(name), None, CATALOG, source=source)
            assert isinstance(result.match_method, str), name


# --- 2. the loaders fail closed --------------------------------------------


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("", "empty file"),
        ("version: 1\nsynonyms: []\n\tbroken: indent\n", "unparseable YAML"),
        ("- version\n- synonyms\n", "top-level sequence, not a mapping"),
        ("version: 1\nsynonyms: []\nextra: 1\n", "unknown top-level key"),
        ("synonyms: []\n", "no version"),
        ("version: 2\nsynonyms: []\n", "a future schema revision"),
        ('version: "1"\nsynonyms: []\n', "a quoted version"),
        ("version: true\nsynonyms: []\n", "a boolean version"),
        ("version: 1\n", "no synonyms key"),
        ("version: 1\nsynonyms: Sugar\n", "synonyms is a string, not a list"),
        ("version: 1\nsynonyms: []\n", "an empty lexicon"),
        ("version: 1\nsynonyms: [7]\n", "a numeric entry"),
        ("version: 1\nsynonyms: [null]\n", "a null entry"),
        ("version: 1\nsynonyms: [Sugar]\n", "a string entry, not a mapping"),
        (
            "version: 1\nsynonyms: [{canonical: Sugar}]\n",
            "a missing match/families pair",
        ),
        (
            "version: 1\nsynonyms:"
            " [{canonical: Sugar, match: [Sugar], families: ['1.1'], extra: 1}]\n",
            "an unknown per-entry key",
        ),
        (
            "version: 1\nsynonyms: [{canonical: '', match: [Sugar], families: ['1.1']}]\n",
            "an empty canonical",
        ),
        (
            "version: 1\nsynonyms: [{canonical: '  Sugar', match: [Sweets], families: ['1.1']}]\n",
            "a padded name",
        ),
        (
            'version: 1\nsynonyms: [{canonical: "Two\\tLines", match: [X], families: ["1.1"]}]\n',
            "a control character in a name",
        ),
        (
            "version: 1\nsynonyms: [{canonical: '"
            + "A" * (MAX_NAME_CHARS + 1)
            + "', match: [X], families: ['1.1']}]\n",
            "an over-long name",
        ),
        (
            "version: 1\nsynonyms: [{canonical: Sugar, match: [83], families: ['1.1']}]\n",
            "a Pantry Item id in a name",
        ),
        (
            "version: 1\nsynonyms: [{canonical: Sugar, match: [], families: ['1.1']}]\n",
            "an empty match list",
        ),
        (
            "version: 1\nsynonyms: [{canonical: Sugar, match: [X], families: []}]\n",
            "an empty families allowlist",
        ),
        (
            "version: 1\nsynonyms: [{canonical: Sugar, match: [X], families: [1.1]}]\n",
            "an unquoted family code, which PyYAML reads as a float",
        ),
        (
            "version: 1\nsynonyms: [{canonical: Sugar, match: [X], families: ['produce']}]\n",
            "a family that is not a Pantry Category code",
        ),
        (
            "version: 1\nsynonyms: [{canonical: Sugar, match: [X], families: [1]}]\n",
            "a numeric family code, still not a string",
        ),
        (
            "version: 1\nsynonyms: [{canonical: Sugar, match: [X], families: ['1.1']},"
            " {canonical: sugar, match: [Y], families: ['1.1']}]\n",
            "a case-insensitive duplicate canonical",
        ),
        (
            "version: 1\nsynonyms: [{canonical: A, match: [B], families: ['1.1']},"
            " {canonical: C, match: [b], families: ['1.1']}]\n",
            "a name in two entries",
        ),
        (
            "version: 1\nsynonyms: [{canonical: A, match: [A], families: ['1.1']}]\n",
            "a name repeated inside its own entry",
        ),
        (
            "version: 1\nsynonyms: [{canonical: A, match: [B], families: ['1.1']},"
            " {canonical: A, match: [C], families: ['1.1']}]\n",
            "a duplicate mapping key",
        ),
    ],
)
def test_a_malformed_synonym_lexicon_fails_closed(tmp_path: Path, body: str, reason: str) -> None:
    with pytest.raises(ConfigurationError):
        load_synonyms(_write_synonyms(tmp_path, body))


def test_a_missing_synonym_lexicon_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_synonyms(tmp_path / "absent.yaml")


def test_an_undecodable_synonym_lexicon_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "synonyms.yaml"
    path.write_bytes(b"version: 1\nsynonyms: [\xff\xfe]\n")
    with pytest.raises(ConfigurationError):
        load_synonyms(path)


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("", "empty file"),
        ("version: 1\nstaples: [油]\n\tbroken: indent\n", "unparseable YAML"),
        ("- version\n- staples\n", "top-level sequence, not a mapping"),
        ("version: 1\nstaples: [油]\nextra: 1\n", "unknown top-level key"),
        ("version: 1\nstaples: [油]\n", "no class_suffixes key at all"),
        ("staples: [油]\nclass_suffixes: [油]\n", "no version"),
        ("version: 9\nstaples: [油]\nclass_suffixes: [油]\n", "a future schema revision"),
        ("version: 1\nstaples: 油\nclass_suffixes: [油]\n", "staples is a string"),
        ("version: 1\nstaples: []\nclass_suffixes: [油]\n", "an empty allowlist"),
        ("version: 1\nstaples: [油]\nclass_suffixes: []\n", "no class suffixes at all"),
        (
            "version: 1\nstaples: [油]\nclass_suffixes: 油\n",
            "class_suffixes is a string",
        ),
        ("version: 1\nstaples: ['  油  ']\nclass_suffixes: [油]\n", "a padded name"),
        ('version: 1\nstaples: ["A\\tB"]\nclass_suffixes: [油]\n', "a control character"),
        ("version: 1\nstaples: [83]\nclass_suffixes: [油]\n", "a Pantry Item id"),
        (
            "version: 1\nstaples: [油, 油]\nclass_suffixes: [油]\n",
            "a duplicate name",
        ),
    ],
)
def test_a_malformed_staples_lexicon_fails_closed(tmp_path: Path, body: str, reason: str) -> None:
    with pytest.raises(ConfigurationError):
        load_staples(_write_staples(tmp_path, body))


def test_a_missing_staples_lexicon_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_staples(tmp_path / "absent.yaml")


def test_an_empty_class_suffix_list_is_refused_even_though_the_tier_would_still_run(
    tmp_path: Path,
) -> None:
    """A rule that is dead but present is the shape of bug nobody notices.

    With `class_suffixes: []` the staples tier still resolves every listed name —
    so a test that only checked the list would pass — while §9.9's `X酱油 → 酱油`
    rule is silently dead and `调味酱油`, a real `调料` in three recipes, would
    drop out of coverage. The loader refuses it for exactly that reason.
    """
    path = _write_staples(tmp_path, "version: 1\nstaples: [酱油]\nclass_suffixes: []\n")
    with pytest.raises(ConfigurationError) as raised:
        load_staples(path)
    assert "class_suffixes" in str(raised.value)


def test_the_error_is_the_repo_fail_closed_type() -> None:
    assert issubclass(ConfigurationError, ValueError)


def test_importing_matcher_refuses_to_start_without_either_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The guarantee is at import, which is what a startup-time failure needs.

    Reload is used deliberately: it is the only way to observe what a fresh
    process would do. Each case restores the module afterwards so the rest of the
    suite sees the real lexicons.
    """
    import importlib

    from app.recipes import matcher

    for attribute, path in (
        ("SYNONYMS_SOURCE", tmp_path / "absent.yaml"),
        ("STAPLES_SOURCE", tmp_path / "absent.yaml"),
    ):
        monkeypatch.setattr(match_lexicon, attribute, path)
        try:
            with pytest.raises(ConfigurationError):
                importlib.reload(matcher)
        finally:
            monkeypatch.undo()
            importlib.reload(matcher)
    assert matcher.SYNONYMS == load_synonyms(SYNONYMS_SOURCE)


# --- 3. the files do not drift from the corpus -----------------------------


def test_the_corpus_the_drift_gates_read_is_the_committed_snapshot() -> None:
    """F14, asserted: the gate's source is a file in this repository.

    A gate whose source silently went back to a sibling checkout would keep
    passing on that machine while CI ran nothing, so the snapshot's existence, its
    178 rows and the module's own import list are all checked here.
    """
    assert CATALOG_SNAPSHOT.is_file()
    assert CATALOG_SNAPSHOT.is_relative_to(Path(__file__).resolve().parents[2])
    assert len(json.loads(CATALOG_SNAPSHOT.read_text(encoding="utf-8"))) == 178
    assert len(CATALOG.rows) == CANDIDATE_ROWS
    imported: set[str] = set()
    for node in ast.walk(ast.parse(Path(match_lexicon.__file__).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "sqlite3" not in imported, "a lexicon loader must not open a database"


def test_every_staple_is_a_recipe_seasoning_or_a_pantry_condiment_class() -> None:
    """Every staple is either a name the real recipes write or a named class.

    A staple nobody buys and no recipe writes is an assumption that costs
    coverage for nothing — §9.9's "~90% for the cost of one file" is only true
    while the list stays this tight. A *class* suffix is exempt by construction,
    because `X酱油` is not a name anyone writes.
    """
    seasonings = set(real_names_for("调料"))
    # `蒸鱼豉油` is the one Pantry Category `1.1c` row in 178 and the only
    # `staples` entry naming a real product rather than a bulk class.
    exempt = {"米", "面粉", "淀粉", "料酒", "蚝油", "油", "盐", "糖", "蒸鱼豉油"}
    unaccounted = {
        name for name in load_staples(STAPLES_SOURCE).staples
        if name not in seasonings and name not in exempt
    }
    assert unaccounted == set(), (
        "every staple must be a real 调料 value or a documented class; unaccounted: "
        f"{sorted(unaccounted)}"
    )
    # …and the corpus's `调料` values that are NOT staples are exactly the two the
    # spec singles out: `芝麻` (seeds, not a bottle of oil) and `青柠` (no lime
    # row exists, and letting it reach `柠檬` is the forbidden substitution).
    not_staples = {
        name for name in seasonings if not load_staples(STAPLES_SOURCE).is_staple(name)
    }
    assert not_staples == {"芝麻", "青柠"}
    assert len(seasonings) == SEASONING_NAMES


def test_the_staples_share_of_seasoning_coverage_is_the_claimed_number() -> None:
    """17 of 19 distinct `调料` values are assumed on hand — the ~90% §9.9 claims.

    Measured, not quoted: the file's value is that number, and a dropped row is a
    silent coverage regression. The 2 misses are `芝麻` and `青柠`, both
    deliberate (§9.8 and §9.9 respectively).
    """
    lexicon = load_staples(STAPLES_SOURCE)
    seasonings = real_names_for("调料")
    satisfied = [name for name in seasonings if lexicon.is_staple(name)]
    assert len(satisfied) == 17
    assert len(seasonings) == 19
    # Without the file, coverage is the 1.1c row count over the value count:
    # Pantry Category 1.1c has exactly one row in 178 (id 177), so a
    # catalog-only match cannot carry `调料` at all. The ~5% is structural.
    condiments = [row for row in CATALOG.rows if row.category == "1.1c"]
    assert [row.id for row in condiments] == [177]
    assert len(condiments) / len(seasonings) < 0.1


def test_every_synonym_entry_names_a_food_class_the_catalog_actually_has() -> None:
    """A `families` allowlist naming a family the catalog has never held is a
    promise the corpus cannot keep, and would silently stop constraining anything.

    Asserted over the file, so a typo like `families: ["1.9"]` fails here rather
    than quietly admitting every candidate in a family that does not exist. The
    allowlists are allowed to name a family the catalog has not reached **yet**,
    so the assertion is one-sided: every family must be a *code*, and every code
    must be one this Pantry Category scheme can produce. That is what the
    loader's pattern already guarantees, so the real content here is the
    positive one — each entry's families must admit at least one of the rows the
    entry can actually reach, or the entry can never resolve anything.
    """
    reachable: dict[str, set[str]] = {}
    for entry in SYNONYMS:
        for row in CATALOG.rows:
            folded = [row.family]
            if any(
                name.casefold() in row.canonical_name.casefold()
                or name.casefold() in row.basename.casefold()
                for name in entry.names
            ):
                reachable.setdefault(entry.canonical, set()).update(folded)
    dead = sorted(
        entry.canonical
        for entry in SYNONYMS
        if not reachable.get(entry.canonical, set()) & set(entry.families)
    )
    # 12 of the 29 entries can reach a row whose Pantry Category family their own
    # allowlist admits. 16 of the other 17 name foods the user has never bought —
    # a curated list restricted to what is already in a 178-row catalog would not
    # be curated at all — and the seventeenth is `Potato`, which reaches a row and
    # is refused on class: `好丽友 呀!土豆 薯条` (62) is family `4`. Pinned as a
    # reviewed set so a family-code typo, which would silently move an entry
    # between the two halves, fails here.
    assert set(dead) == set(REVIEWED_UNREACHABLE_ENTRIES), (
        f"newly unreachable: {sorted(set(dead) - set(REVIEWED_UNREACHABLE_ENTRIES))}; "
        f"newly reachable: {sorted(set(REVIEWED_UNREACHABLE_ENTRIES) - set(dead))}"
    )
    assert len(reachable) == 13, sorted(reachable)
    for canonical, families in reachable.items():
        if canonical in REVIEWED_UNREACHABLE_ENTRIES:
            continue
        entry = SYNONYM_INDEX[canonical.casefold()]
        assert families & set(entry.families), canonical


def test_the_lexicon_covers_every_english_name_the_spec_lists() -> None:
    """Every Latin name §9.8 lists is in the file, and no others are.

    The spec's list is a *minimum*, and the file adds the catalog's own spellings
    (`Chicken Wings`) and the three `调料` names the flavour rows must not reach.
    Those are separately asserted, so the *minimum* can be checked exactly: a
    missing one fails here, and an unlisted one fails the transliteration gate in
    `test_matcher.py`.
    """
    latin = {
        name
        for entry in SYNONYMS
        for name in entry.names
        if name.isascii() and name.replace(" ", "").replace("-", "").isalpha()
    }
    assert SPEC_LATIN_NAMES <= latin
    assert latin - SPEC_LATIN_NAMES == set(), (
        "an unlisted Latin name is either a spec amendment or a typo: "
        f"{sorted(latin - SPEC_LATIN_NAMES)}"
    )


def test_no_pantry_item_id_appears_in_any_lexicon_file() -> None:
    """§9.8: never store a Pantry Item id in a lexicon file.

    `items.id` is `INTEGER PRIMARY KEY AUTOINCREMENT` and a producer re-import
    renumbers it, so a lexicon carrying one would silently point at a different
    product after the next ingest. Asserted over the shipped files' text as well
    as the loaded values, so a value that the loader happens to coerce away would
    still be visible here.
    """
    for source in (SYNONYMS_SOURCE, STAPLES_SOURCE):
        for line in source.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            assert not stripped.startswith("- ") or not stripped[2:].strip().isdigit(), (
                f"{source.name}: {stripped}"
            )
    for entry in SYNONYMS:
        assert not any(name.isdigit() for name in entry.names)
    lexicon: StaplesLexicon = load_staples(STAPLES_SOURCE)
    assert not any(name.isdigit() for name in lexicon.staples)
    assert not any(suffix.isdigit() for suffix in lexicon.class_suffixes)

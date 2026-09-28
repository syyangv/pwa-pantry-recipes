"""The one pantry line the Stock Join is measured on, and what tier 4 can do with it.

Run alone:  .venv/bin/python -m pytest tests/vault/test_pantry_stock.py -q

**§10.2 asks this file for one thing:** that
`禾苑 蟹粉鱼肉狮子头 冷冻 280 克` yields a product core that the tier-4 basename
index finds at id 108. **It does not, and the reason is worth a file.**

`normalize_ingredient()` strips sizes (numeric-anchored, with a unit word) and
leading lexicon brands. `冷冻` is neither: it is a *condition* token, in the same
family as `冷藏` and `冷冻肉&海鲜`, and no rule removes it because removing it
would be a guess about vocabulary rather than a measurement. So the line's
product core is `蟹粉鱼肉狮子头 冷冻`, while the catalog row
`禾苑 蟹粉鱼肉狮子头` (id 108) reduces to `蟹粉鱼肉狮子头`, and the basename index
misses.

The spec says both things about this line — §10.2 calls it a tier-4 hit and F1
calls it one of the two measured misses needing the override — and only the
second is true of the shipped normalizer. So this file asserts what is actually
true and refuses to paper over the gap:

- the line's product core is `蟹粉鱼肉狮子头 冷冻`, and that is the string
  `line_overrides.yaml` is keyed by;
- the core the *catalog row* reduces to is `蟹粉鱼肉狮子头`, and `by_basename`
  holds 108 under exactly that key;
- the difference between the two is one unstrippable word, and the override
  closes it.

The alternative — a test asserting the basename tier finds 108 — would have to be
written by editing `normalize.py` to strip `冷冻`, which is a vocabulary change
made to satisfy a test rather than a measurement. §13.14's own measured miss rate
(2 of 46) counts this line as a miss, which is the other half of the evidence
that the spec's two statements are not both about the current code.

**No test here reads the live vault.** The note is
`tests/fixtures/pantry/Pantry.md`, the copy #7 froze.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from app.pantry.catalog import build_snapshot, fold_name
from app.pantry.stock import LINE_OVERRIDES
from app.recipes.normalize import normalize_ingredient
from app.vault.pantry import parse_pantry

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
PANTRY_NOTE: Final = REPO_ROOT / "tests" / "fixtures" / "pantry" / "Pantry.md"
SNAPSHOT: Final = REPO_ROOT / "tests" / "fixtures" / "pantry_items_snapshot.json"

#: The line, and the catalog row it should reach.
LINE: Final = "禾苑 蟹粉鱼肉狮子头 冷冻 280 克"
CATALOG_NAME: Final = "禾苑 蟹粉鱼肉狮子头"
CATALOG_ID: Final = 108
#: The product core of the pantry line, and of the catalog row. They differ by
#: one word and that difference is the whole point of this file.
LINE_CORE: Final = "蟹粉鱼肉狮子头 冷冻"
CATALOG_CORE: Final = "蟹粉鱼肉狮子头"


def test_the_line_yields_the_product_core_the_override_file_is_keyed_by() -> None:
    """The parser hands the join a core; the file is keyed by exactly that core.

    Also the parser-side half of the miss: money, the emoji date, and the frozen
    section's own words are already gone from the line's text, so the only thing
    left for the normalizer is the condition token.
    """
    snapshot = parse_pantry(PANTRY_NOTE.read_bytes())
    item = next(item for item in snapshot.items if "蟹粉鱼肉狮子头" in item.text)
    assert item.text == LINE
    assert normalize_ingredient(item.text) == LINE_CORE
    # Folded, because that is the key form: NFKC + trim + casefold.
    assert fold_name(normalize_ingredient(item.text)) == LINE_CORE
    assert LINE_CORE in LINE_OVERRIDES


def test_tier_four_finds_the_row_under_the_catalog_rows_own_core() -> None:
    """`by_basename` holds 108 under `蟹粉鱼肉狮子头` — the *catalog row's* core.

    The lookup itself is correct; what the pantry line produces is the thing that
    does not reach it. Asserting the lookup on its own is worth doing, because it
    is the half that would break if the normalizer changed on either side, and it
    is what makes the gap a measured fact about one word rather than a vague
    "the join is weak".
    """
    records = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    snapshot = build_snapshot(records)
    row = snapshot.by_id[CATALOG_ID]
    assert row.canonical_name == CATALOG_NAME
    assert row.basename == CATALOG_CORE
    assert [hit.id for hit in snapshot.by_basename[fold_name(CATALOG_CORE)]] == [CATALOG_ID]
    # And the line's own core is genuinely absent from the catalog, rather than
    # merely resolving to something else.
    assert not snapshot.by_name.get(fold_name(LINE_CORE))
    assert not snapshot.by_basename.get(fold_name(LINE_CORE))


def test_the_gap_is_one_condition_token_and_the_override_closes_it() -> None:
    """`冷冻` is the whole difference, and it is a word no stripper can justify
    removing.

    `normalize.py` is numeric-anchored about sizes and closed-list about brands;
    there is no rule that could take a bare condition word without a vocabulary
    list, and inventing one here would be a vocabulary change made to satisfy a
    test. So the line is a miss, the override file names the catalog product, and
    the whole repair is one line of reviewed source.
    """
    assert LINE_CORE == f"{CATALOG_CORE} 冷冻"
    assert normalize_ingredient(LINE_CORE) == LINE_CORE, "nothing else is left to strip"
    assert LINE_OVERRIDES[LINE_CORE] == CATALOG_NAME
    records = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    snapshot = build_snapshot(records)
    # The override's value resolves against the live catalog — at read time, by
    # name, so a renumbered `items.id` cannot break it.
    assert [hit.id for hit in snapshot.by_name[fold_name(LINE_OVERRIDES[LINE_CORE])]] == [
        CATALOG_ID
    ]

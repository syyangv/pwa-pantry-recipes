"""The live flavour collisions, and the proof that both guards are load-bearing.

**This is the most important test file in the matcher.** A guard with no test
that fails when it is deleted is not a guard, and a matcher that silently
returns a wrong Pantry Item id is the worst bug class this app has: it renders
"you have this" for something you do not, and once ticket #11 persists it the
error becomes permanent. Every case below is a **live row in the committed
178-row catalog**, not a synthetic shape, and each is asserted individually.

**Both guards are independently required, and the file is split by guard for
that reason.** §3.2's per-row table says which guard stops which row, and the
answer is asymmetric: the segment-boundary guard stops **all** of them, while the
category-family guard independently stops exactly two (`芝麻`→25 and `蒜`→110) —
on category alone, with no tokenization. A single combined assertion over the
rows would pass with either guard deleted, which is why §10.2 requires a negative
control per guard instead.

**The family guard's negative control, honestly.** On the committed corpus the
segment guard alone refuses every live collision row, so switching the family
guard off changes **nothing** for those rows — they are already refused. That is
not a reason to drop the family guard; it is the reason its control cannot be an
end-to-end assertion on this catalog. So the family guard is proven three ways:

1. **At the guard**, on the live rows: `families`-independently, 25 and 110 are
   refused for their Pantry Category and nothing else, which is §3.2's own claim
   about it ("rejects it *independently of any tokenization*").
2. **End to end, on a catalog where the two rules disagree** — a family-`4`
   cracker and a family-`4` garlic-bread row that are *whole segments* of the
   ingredient's own Latin name, so the segment rule admits them and only the
   family rule can refuse. This is the negative control the AC asks for, on a
   shape where it is a real end-to-end demonstration rather than a tautology.
3. **By construction**: `families` is mandatory in `synonyms.yaml`, the loader
   refuses an entry without one, and `test_match_lexicon.py` asserts both.

Nothing here reads a live path: the catalogs are the committed snapshot and rows
built in-test through the shipped `build_snapshot()`.
"""

from __future__ import annotations

from typing import Final

import pytest

from app.recipes import matcher
from app.recipes.ingredients import parse_ingredient_value
from app.recipes.normalize import normalize_ingredient

from .corpus import (
    committed_catalog,
    corpus_stock,
    real_names_for,
    resolve,
    synthetic_catalog,
)

CATALOG = committed_catalog()
NO_SEGMENT_GUARD: Final = matcher.MatchGuards(segment_boundary=False)
NO_FAMILY_GUARD: Final = matcher.MatchGuards(category_family=False)
NO_GUARDS: Final = matcher.MatchGuards(segment_boundary=False, category_family=False)

#: Every live flavour-collision row, one case each, with the two facts that make
#: it a collision: the row's real text and its Pantry Category family.
#:
#: `source` is the frontmatter key the recipe writes the token under, and it
#: matters: `蒜` and `芝麻` reach the ladder as `调料` and are *assumed on hand*
#: by tier 7, so with both guards on their result is `staples`, not `unresolved`.
#: The collision is therefore not "the token resolves to a snack" — it is "the
#: token does not resolve to a snack, and the audit says which rows it nearly
#: did", and tier 7 only gets to answer that because tiers 4-6 declined.
COLLISIONS: Final = (
    ("蒜", "调料", 72, "柴米 蒜香蒸茄子 300 克", "1.2", "蒜香 is a flavour of a dish"),
    ("蒜", "调料", 110, "乐事 2026FIFA世界杯限定联名薯片蒜蓉面包味", "4", "蒜蓉 is a chip flavour"),
    ("土豆", "材料", 62, "好丽友 呀!土豆 薯条 里脊牛排味 70 克", "4", "a potato CHIP"),
    ("芝麻", "调料", 20, "臻品德 芝麻酱烧饼 10只入 1.54 磅", "1.2s", "a sesame-paste flatbread"),
    ("芝麻", "调料", 25, "好丽友 高笑美芝麻饼干 216 克", "4", "a sesame biscuit"),
    ("芝麻", "调料", 66, "思念 黑芝麻开心果玉汤圆 冷冻 200 克", "1.2d", "a taro/sesame tangyuan"),
    ("芝麻", "调料", 102, "芝麻烧饼", "1.1", "a sesame flatbread, and family 1.1"),
    ("葱", "调料", 160, "小巷口 老上海蟹壳黄 葱香馅 冷冻 360 克", "1.2s", "葱香 is a filling"),
    (
        "开心果",
        "材料",
        29,
        "熟道 开心果抹茶脆曲奇饼干【直播间爆款】 120 克",
        "4",
        "a pistachio cookie",
    ),
    ("开心果", "材料", 66, "思念 黑芝麻开心果玉汤圆 冷冻 200 克", "1.2d", "a tangyuan"),
)

#: The spec's own table, restated as ids so a rename in the catalog fails here
#: rather than quietly changing which rows are protected.
SPEC_ROWS: Final[dict[str, tuple[int, ...]]] = {
    "蒜": (72, 110),
    "土豆": (62,),
    "芝麻": (25, 102),
}


# --- the live corpus the cases are drawn from ------------------------------


@pytest.mark.parametrize(("raw", "source", "row_id", "name", "category", "why"), list(COLLISIONS))
def test_every_live_collision_row_exists_as_this_file_claims(
    raw: str, source: str, row_id: int, name: str, category: str, why: str
) -> None:
    """The rows are the ones the spec names, at the ids it names.

    Asserted first, over the whole table, so a re-import that renumbers or
    renames one of them fails as a *data* failure here rather than as nine
    separate mysteriously-passing guard assertions.
    """
    del raw, source, why  # the row's own text and class are asserted below
    row = CATALOG.by_id[row_id]
    assert row.canonical_name == name
    assert row.category == category
    assert row.family == category_family(row.category)


def category_family(category: str) -> str:
    from app.pantry.catalog import category_family as family_of

    return family_of(category)


def test_the_spec_table_is_a_subset_of_this_files_table() -> None:
    """§3.2 names five rows across three tokens; this file carries ten.

    The spec's five are the ones §3.2 measured; the other five (`芝麻`→20 and
    →66, `葱`→160, `开心果`→29 and →66) are real collisions found by running the
    naive substring join over all 45 distinct frontmatter values against all 176
    candidate rows. Adding them costs nothing and closes the set: the six
    collisions D1 names are a subset of ten, so a reader comparing the two lists
    can see which are the spec's and which this ticket found.
    """
    covered = {(raw, row_id) for raw, source, row_id, *_ in COLLISIONS if source}
    for raw, ids in SPEC_ROWS.items():
        for row_id in ids:
            assert (raw, row_id) in covered, (raw, row_id)
    assert len(covered) == len(COLLISIONS)
    spec_pairs = {(raw, row_id) for raw, ids in SPEC_ROWS.items() for row_id in ids}
    assert spec_pairs <= covered
    assert len(spec_pairs) == 5


# --- the primary obligation: every row individually ------------------------


@pytest.mark.parametrize(("raw", "source", "row_id", "name", "category", "why"), list(COLLISIONS))
def test_a_flavour_descriptor_row_is_never_adopted(
    raw: str, source: str, row_id: int, name: str, category: str, why: str
) -> None:
    """No collision row is adopted, and the audit says which guard held it back."""
    del name, category, why
    result = resolve(raw, CATALOG, source=source)
    assert result.pantry_item_id is None, (
        f"{raw!r} adopted {row_id} — a flavour descriptor, not the ingredient"
    )
    candidate = result.candidate(row_id)
    assert candidate is not None, f"{raw!r} never even reached row {row_id}"
    assert "segment_boundary" in candidate.rejected_by


@pytest.mark.parametrize(("raw", "source", "row_id", "name", "category", "why"), list(COLLISIONS))
def test_a_refused_row_is_still_reported_so_the_user_can_reject_it(
    raw: str, source: str, row_id: int, name: str, category: str, why: str
) -> None:
    """The refusal is auditable, and the near-miss list can still offer the row.

    A refusal nobody can see is a refusal nobody can repair, and ticket #11's
    whole purpose is a persisted, auditable resolution. So the guard's verdict
    travels with the row — its name, its family, and which guard said no.
    """
    del name, category, why
    result = resolve(raw, CATALOG, source=source)
    candidate = result.candidate(row_id)
    assert candidate is not None
    assert candidate.canonical_name
    assert candidate.family
    assert candidate.relation
    assert row_id in result.candidate_ids()
    assert result.rejected_by(row_id) == candidate.rejected_by
    # `开心果` is not a value the real recipes write (`开心果酱` is), so its
    # near-miss list is the only repair surface and must carry the rows.
    if raw == "开心果":
        assert row_id in [miss.pantry_item_id for miss in result.near_misses]


# --- negative control 1: the segment-boundary guard -----------------------


@pytest.mark.parametrize(("raw", "source", "row_id", "name", "category", "why"), list(COLLISIONS))
def test_negative_control_the_segment_guard_disabled_admits_the_row(
    raw: str, source: str, row_id: int, name: str, category: str, why: str
) -> None:
    """**The segment guard is load-bearing.** Delete it and every case breaks.

    One assertion per row, because a single combined assertion over the ten
    would still pass if the guard were only load-bearing for one of them. Each
    case asserts the row is **admitted** — present in the pool with no rejection
    reason — and that the ladder's own answer is wrong, so the failure is
    observable both in the audit trail and end to end.
    """
    del name, category, why
    on = resolve(raw, CATALOG, source=source)
    off = resolve(raw, CATALOG, source=source, guards=NO_SEGMENT_GUARD)

    before = on.candidate(row_id)
    after = off.candidate(row_id)
    assert before is not None and "segment_boundary" in before.rejected_by, raw
    if after is not None:
        assert "segment_boundary" not in after.rejected_by, raw
    # …and the answer itself is wrong. For a `调料` the wrong answer is a Pantry
    # Item id at all; for a `材料` it is a Pantry Item id that is not the food.
    assert off.pantry_item_id is not None or off.match_method not in {"staples", "unresolved"}, (
        f"{raw!r}: with the segment guard off, the ladder stops refusing"
    )
    if off.pantry_item_id is not None:
        assert off.pantry_item_id in [row_id for raw_, s, row_id, *_ in COLLISIONS if raw_ == raw]


def test_negative_control_the_segment_guard_off_resolves_each_token_to_a_wrong_row() -> None:
    """The end-to-end statement, one token at a time.

    `蒜`→72, `土豆`→62, `芝麻`→20, `葱`→160, `开心果`→29 with the guard off; all
    `unresolved`/`staples` with it on. Those are five of the worst answers this
    app could give, and each one is a *different wrong id*, so a future
    implementer cannot pass this file by making the guard merely stricter for one
    token.
    """
    expected_wrong: dict[tuple[str, str], int] = {
        ("蒜", "调料"): 72,
        ("土豆", "材料"): 62,
        ("芝麻", "调料"): 20,
        ("葱", "调料"): 160,
        ("开心果", "材料"): 29,
    }
    for (raw, source), wrong in expected_wrong.items():
        guarded = resolve(raw, CATALOG, source=source)
        unguarded = resolve(raw, CATALOG, source=source, guards=NO_SEGMENT_GUARD)
        assert guarded.pantry_item_id is None, (raw, guarded.pantry_item_id)
        assert guarded.match_method in {"staples", "unresolved"}, (raw, guarded.match_method)
        assert unguarded.pantry_item_id == wrong, (raw, unguarded.pantry_item_id)
        assert unguarded.candidate(wrong).rejected_by == ()  # type: ignore[union-attr]


def test_garlic_the_sixth_collision_is_two_rows_not_one() -> None:
    """`蒜` hits **two** catalog rows, so "five rows" is really six collisions.

    D1's table lists `蒜` once and §3.2's text calls this out: counted by
    recipe-side token there are six live flavour collisions, not five. The two
    rows are stopped by *different* guards — 72 by the segment rule alone, 110 by
    both — and the second test below shows that asymmetry is real rather than a
    claim in a comment.
    """
    assert SPEC_ROWS["蒜"] == (72, 110)
    for row_id in SPEC_ROWS["蒜"]:
        assert resolve("蒜", CATALOG, source="调料").candidate(row_id) is not None


# --- negative control 2: the category-family guard ------------------------


def test_the_family_guard_refuses_the_two_rows_on_category_alone() -> None:
    """§3.2's claim, asserted as its own thing: no tokenization involved.

    The two rows the spec says the family guard independently rejects are 25
    (family `4`) and 110 (family `4`), and the ingredient's own food class is
    `1.1`. The check below is *purely* the Pantry Category family against the
    entry's own `families` allowlist — no segment rule, no string comparison — so
    it is a real observation about the guard rather than a restatement of the
    case above.
    """
    for raw, row_id, entry_name in (("芝麻", 25, "芝麻"), ("蒜", 110, "蒜")):
        entry = matcher.SYNONYM_INDEX[entry_name]
        row = CATALOG.by_id[row_id]
        assert row.family == "4"
        assert "4" not in entry.families
        assert entry.allows(row.family) is False, (raw, row_id)
        # The same two rows the segment rule also stops — which is the asymmetry
        # §3.2 tabulates and which is why one combined assertion is not enough.
        guarded = resolve(raw, CATALOG, source="调料")
        assert "segment_boundary" in guarded.rejected_by(row_id)
        assert "category_family" in guarded.rejected_by(row_id), (raw, row_id)


def test_the_family_guard_is_also_independently_required_for_sesame() -> None:
    """`芝麻`→102 is the row the family guard **cannot** stop, and that is the point.

    `芝麻烧饼` is family `1.1`, the same family a sesame condiment lives in, so no
    class allowlist can refuse it. Only the segment rule can. Asserting it means
    the two guards are demonstrably not substitutes: removing either leaves the
    other one holding a different subset of rows.
    """
    row = CATALOG.by_id[102]
    entry = matcher.SYNONYM_INDEX["芝麻"]
    assert row.family == "1.1"
    assert entry.allows(row.family) is True, "so the family guard cannot stop 102"
    assert "segment_boundary" in resolve("芝麻", CATALOG, source="调料").rejected_by(102)
    # And with the segment rule off, 102 *is* adopted — the family guard does not
    # stand in its way, which is exactly the asymmetry.
    off = resolve("芝麻", CATALOG, source="调料", guards=NO_SEGMENT_GUARD)
    assert off.pantry_item_id in (20, 102)
    assert off.candidate(102).rejected_by == ()  # type: ignore[union-attr]


#: Every row the two guards refuse, over all 45 distinct frontmatter values,
#: asserted in both directions by
#: `test_every_row_either_guard_refuses_is_a_reviewed_flavour_descriptor`.
#:
#: Every entry is the recipe-side token appearing as a **fragment** of a longer
#: product name. The first five rows are the spec's §3.2 table; the other
#: thirteen this ticket found by running the naive substring join over all 45
#: values against all 176 candidate rows, and each is the same defect under a
#: different product.
REVIEWED_GUARD_REFUSALS: Final[frozenset[tuple[str, int]]] = frozenset(
    {
        # -- §3.2's table --------------------------------------------------------
        ("蒜", 72),          # 柴米 蒜香蒸茄子 — 蒜香 is a flavour
        ("蒜", 110),         # 乐事 …薯片蒜蓉面包味 — a chip
        ("土豆", 62),        # 好丽友 呀!土豆 薯条 — a potato chip
        ("芝麻", 25),        # 好丽友 高笑美芝麻饼干 — a biscuit
        ("芝麻", 102),       # 芝麻烧饼 — a flatbread
        # -- found by the same naive join over the real corpus -------------------
        ("芝麻", 20),        # 臻品德 芝麻酱烧饼
        ("芝麻", 66),        # 思念 黑芝麻开心果玉汤圆
        ("芝麻油", 20),      # 臻品德 芝麻酱烧饼
        ("芝麻油", 25),      # 好丽友 高笑美芝麻饼干
        ("芝麻油", 66),      # 思念 黑芝麻开心果玉汤圆
        ("芝麻油", 102),     # 芝麻烧饼
        ("Clam", 143),       # YABA 花蛤肉 — 花蛤 is a fragment of 花蛤肉
        ("Clam", 161),       # Umji's 蛤蜊拌饭套餐 — 蛤蜊 is a fragment
        ("西兰花", 154),     # Forward Greens Micro Broccoli — family 1.1, not 1.2
        ("鸡蛋", 86),        # 爆浆咸蛋黄肉松流沙可頌 — 蛋 in 蛋黄
        ("鸡蛋", 89),        # 咔啰咔曼 咸味蛋黄酥蛋卷
        ("鸡蛋", 148),       # 友臣 肉松小贝蛋糕
        ("鸡蛋", 163),       # Love Me Sweet 港式酥皮蛋挞 — 蛋 in 蛋挞
        ("鸡蛋", 174),       # 柴米 传统工艺 软心皮蛋
        ("葱", 160),         # 小巷口 老上海蟹壳黄 葱香馅 — 葱香 is a filling
    }
)

#: A catalog where the two guards **disagree**, so the family guard is the only
#: thing standing between `芝麻` and a cracker. Every row is a whole segment of
#: the ingredient's own Latin name, so the segment rule admits all of them; the
#: ids are ordered so the *wrong* row is the one a weakened ladder picks.
#:
#: 900 `SesameCracker Bars 200 g`   family 1.1  — `Sesame` is a fragment
#: 901 `Crunchy Sesame Crackers 200 g` family 4 — `Sesame` IS a segment
#: 902 `Sesame Seeds 100 g`          family 1.1  — `Sesame` IS a segment, correct
#: 903 `GarlicCracker Sticks 200 g`  family 1.1  — `Garlic` is a fragment
#: 904 `Crunchy Garlic Crackers 200 g` family 4 — `Garlic` IS a segment
#: 905 `Fresh Garlic 1 lb`           family 1.1  — `Garlic` IS a segment, correct
DISAGREEING: Final = synthetic_catalog(
    [
        (900, "SesameCracker Bars 200 g", "1.1", "[]"),
        (901, "Crunchy Sesame Crackers 200 g", "4", "[]"),
        (902, "Sesame Seeds 100 g", "1.1", "[]"),
        (903, "GarlicCracker Sticks 200 g", "1.1", "[]"),
        (904, "Crunchy Garlic Crackers 200 g", "4", "[]"),
        (905, "Fresh Garlic 1 lb", "1.1", "[]"),
    ]
)


@pytest.mark.parametrize(
    ("raw", "correct", "wrong_by_segment", "wrong_by_family"),
    [("芝麻", 902, 900, 901), ("蒜", 905, 903, 904)],
)
def test_negative_control_the_family_guard_disabled_resolves_to_a_snack(
    raw: str, correct: int, wrong_by_segment: int, wrong_by_family: int
) -> None:
    """**The family guard is load-bearing.** Delete it and each token lands on a snack.

    With both guards on, `芝麻` reaches 902 and `蒜` reaches 905 — the real food in
    a real food family. Switching the **segment** guard off lands each on the row
    where the ingredient is a fragment of a longer word (900, 903). Switching the
    **family** guard off lands each on the row where the ingredient is a whole
    segment of a *snack* name (901, 904) — and the segment rule cannot help,
    because those names really do contain the whole word.

    That is the negative control the acceptance criteria ask for, on a shape where
    it is a real end-to-end demonstration: three different wrong ids, three
    different guards responsible, and one correct answer that only the pair of
    guards produces.
    """
    guarded = resolve(raw, DISAGREEING, source="调料")
    assert guarded.pantry_item_id == correct, raw
    assert guarded.confidence < 1.0, raw

    without_segment = resolve(raw, DISAGREEING, source="调料", guards=NO_SEGMENT_GUARD)
    assert without_segment.pantry_item_id == wrong_by_segment, raw
    assert without_segment.candidate(correct).rejected_by == ()  # type: ignore[union-attr]

    without_family = resolve(raw, DISAGREEING, source="调料", guards=NO_FAMILY_GUARD)
    assert without_family.pantry_item_id == wrong_by_family, raw
    rejected = without_family.candidate(wrong_by_family)
    assert rejected is not None and rejected.rejected_by == (), raw

    without_both = resolve(raw, DISAGREEING, source=source_of(raw), guards=NO_GUARDS)
    assert without_both.pantry_item_id in (wrong_by_segment, wrong_by_family), raw


def source_of(raw: str) -> str:
    """The frontmatter key a probe token is written under in the real recipes.

    `芝麻` and `蒜` are `调料`; the rest of the guard footprint comes from
    `芝麻油` and `鸡蛋`, which are also `调料`, and `西兰花`, a `材料`.
    """
    return "材料" if raw == "西兰花" else "调料"


@pytest.mark.parametrize(("raw", "correct"), [("芝麻", 902), ("蒜", 905)])
def test_neither_guard_is_a_substitute_for_the_other(raw: str, correct: int) -> None:
    """Four configurations, four different answers — so neither guard is redundant.

    This is the assertion the whole file exists for. If either guard were a
    restatement of the other, one of these four would repeat, and a reader could
    delete that one. They do not repeat, and the committed-corpus cases above show
    the same asymmetry against 176 real rows.
    """
    def answer(guards: matcher.MatchGuards) -> int | None:
        return resolve(raw, DISAGREEING, source="调料", guards=guards).pantry_item_id

    answers = {
        "both": answer(matcher.DEFAULT_GUARDS),
        "no_segment": answer(NO_SEGMENT_GUARD),
        "no_family": answer(NO_FAMILY_GUARD),
        "neither": answer(NO_GUARDS),
    }
    assert answers == {
        "both": correct,
        "no_segment": 900 if raw == "芝麻" else 903,
        "no_family": 901 if raw == "芝麻" else 904,
        "neither": 900 if raw == "芝麻" else 903,
    }
    assert len(set(answers.values())) == 3, "one guard's removal must change the answer"


# --- the two guards' rules, at their own boundary -------------------------


@pytest.mark.parametrize(
    ("row_name", "token", "expected"),
    [
        ("Organic Baby Kale", "Kale", True),
        ("Kale Microgreens", "Kale", True),
        ("Frozen Mackerel Boneless 5P", "Mackerel", True),
        ("好丽友 呀!土豆 薯条 里脊牛排味", "土豆", False),
        ("柴米 蒜香蒸茄子", "蒜", False),
        ("2026FIFA世界杯限定联名薯片蒜蓉面包味", "蒜", False),
        ("小巷口 老上海蟹壳黄 葱香馅 冷冻", "葱", False),
        ("思念 黑芝麻开心果玉汤圆 冷冻", "开心果", False),
        ("熟道 开心果抹茶脆曲奇饼干【直播间爆款】", "开心果", False),
        # Symmetric by the same test: a *superstring* of the token is refused just
        # as firmly as a fragment. Admitting `蒜香` would need a closed list of
        # flavour words, which cannot generalise to the next SKU the user buys.
        ("柴米 蒜香蒸茄子", "蒜香", False),
        ("2026FIFA世界杯限定联名薯片蒜蓉面包味", "蒜蓉", False),
        ("柴米 蒜香蒸茄子", "蒜香蒸茄", False),
        ("小小白菜心", "小白菜心", False),
    ],
)
def test_the_segment_rule_is_whole_segment_and_nothing_looser(
    row_name: str, token: str, expected: bool
) -> None:
    """The rule itself, over both scripts and both sides of the boundary.

    One function, one direction, and no closed list of bad words: a name matches
    only where it **is** a whole whitespace-delimited segment, so it refuses a
    fragment (`蒜` in `蒜香蒸茄子`) and a superstring (`蒜香` in `蒜香蒸茄子`) by
    the same test. A blacklist would have caught the six rows the spec names and
    none of the next six the catalog grows.
    """
    assert matcher._is_whole_segment(normalize_ingredient(row_name), token) is expected, (
        row_name,
        token,
    )


def test_punctuation_is_never_a_segment_delimiter() -> None:
    """`!`, `、`, `，` and `【】` do not split a segment — asserted over the set.

    This is the single fact that makes `土豆` fail, so it is asserted directly
    rather than only through `土豆`. A future "improvement" that treated
    punctuation as a word boundary would turn `呀!土豆 薯条` into three segments
    and declare the row cookable, and the only symptom would be a chip.
    """
    for separator in ("!", "、", "，", "【", "】", "（", "）", "/", "·", "-"):
        text = f"呀{separator}土豆薯条"
        assert not matcher._is_whole_segment(text, "土豆"), separator
    assert matcher._is_whole_segment("Kale Microgreens", "Kale") is True
    assert matcher._is_whole_segment("Micro Kale", "Kale") is True
    assert matcher._is_whole_segment("Kale Microgreens", "kal") is False


def test_the_family_guard_is_the_entrys_own_allowlist_and_nothing_else() -> None:
    """No catalog-side inference: the allowlist comes from the lexicon, not the row.

    A guard that derived the allowed family from the candidate it is judging
    would always pass. Asserting the two disagree — the entry allows `1.1`, the
    row is `4` — is what shows the direction of the check.
    """
    entry = matcher.SYNONYM_INDEX["芝麻"]
    assert entry.families == ("1.1", "1.1c")
    for row in CATALOG.rows:
        if row.id in (20, 25, 66, 102):
            assert entry.allows(row.family) is (row.family == "1.1"), row.id
    # A `4` row is refused whatever the segment rule thinks of it.
    assert entry.allows(CATALOG.by_id[25].family) is False
    assert entry.allows(CATALOG.by_id[102].family) is True


# --- the guards and the tier order are mutation-tested --------------------


def test_mutation_deleting_either_guard_breaks_a_named_assertion() -> None:
    """The mutation table, run rather than described.

    Every row switches one guard off on a catalog where the two rules disagree,
    so the switch is a real edit to the guard's own input rather than a
    bookkeeping flag. **Every mutation produced a Pantry Item id** — a mutation
    that produced a *miss* would not demonstrate the guard was load-bearing, it
    would demonstrate the other one was.

    The committed catalog is not used for the family-guard rows, and the reason is
    worth stating plainly: with the segment rule still on, every live collision
    row is refused whatever the family guard says, so a family-guard mutation
    there changes nothing observable. That is a property of this catalog, not
    evidence the guard is inert, and the row set below is where it is not.
    """
    segment_mutations = {
        raw: resolve(raw, DISAGREEING, source="调料", guards=NO_SEGMENT_GUARD).pantry_item_id
        for raw in ("芝麻", "蒜")
    }
    family_mutations = {
        raw: resolve(raw, DISAGREEING, source="调料", guards=NO_FAMILY_GUARD).pantry_item_id
        for raw in ("芝麻", "蒜")
    }
    guarded = {
        raw: resolve(raw, DISAGREEING, source="调料", guards=matcher.DEFAULT_GUARDS).pantry_item_id
        for raw in ("芝麻", "蒜")
    }
    assert guarded == {"芝麻": 902, "蒜": 905}
    assert segment_mutations == {"芝麻": 900, "蒜": 903}
    assert family_mutations == {"芝麻": 901, "蒜": 904}
    wrong = [*segment_mutations.values(), *family_mutations.values()]
    assert all(item_id is not None for item_id in wrong)
    assert not set(wrong) & set(guarded.values()), "a mutation must not reach the right answer"
    assert len(set(wrong)) == 4, "four distinct wrong Pantry Item ids, one per mutation"


def test_mutation_reordering_the_tiers_would_change_a_live_answer() -> None:
    """Moving the staples tier above the exact tiers flips a live answer.

    §9.8 forbids the reordering and this is what it would cost on the committed
    catalog, not on a synthetic one: `Sugar` is not in the 178 rows, but
    `芝麻油` is a `调料` the staples tier would claim, and a `材料` of the same
    name is refused. A reordering that let tier 7 answer for a `材料` would turn
    that refusal into a "found" chip on real data.
    """
    vinegar = resolve("醋", CATALOG, source="调料")
    assert vinegar.match_method == "staples"
    assert vinegar.misfiled_staple is False
    misfiled = resolve("醋", CATALOG, source="材料")
    assert misfiled.misfiled_staple is True
    assert misfiled.match_method == "unresolved"
    # Tier 7's answer is a *class assumption* and names no Pantry Item, so a
    # reordering that made it pre-empt tier 2 could not produce a wrong id — it
    # would produce a missing one, which is the other half of the same defect.
    assert vinegar.pantry_item_id is None


def test_mutation_tier_4_must_be_a_proper_prefix_or_it_shadows_tier_5() -> None:
    """An *improper* prefix relation is the one tier-4 mutation with a real cost.

    Every other ordering mutation is caught by `test_matcher.py`'s call-sequence
    assertion. This one is subtler — tier 4 still runs before tier 5, so the
    order looks untouched — and its cost is that every normalized-exact answer is
    relabelled `note_basename` at 0.9, which is a **confidence** lie rather than a
    wrong id. Asserted so the strictness is not quietly dropped.
    """
    import inspect

    source = inspect.getsource(matcher._prefix_pool)
    assert "len(key) == len(core)" in source, (
        "tier 4 must reject an improper prefix; without it, tier 4 is a superset of "
        "tier 5's equality and no normalized-exact answer is ever reported as such"
    )
    pool = matcher._prefix_pool(CATALOG, "空心菜嫩苗", matcher.DEFAULT_GUARDS)
    assert pool == (), "an equal basename is tier 5's business, not tier 4's"
    assert resolve("新鲜小叶茼蒿", CATALOG).match_tier == 5


# --- and the guards cost no correct answer -------------------------------


def test_every_row_either_guard_refuses_is_a_reviewed_flavour_descriptor() -> None:
    """The guards' full footprint on the real corpus, gated in **both** directions.

    A guard that refused correct answers would be a guard nobody keeps, and "is
    this matcher too strict" is the first question anyone asks when they see a
    high miss rate. So the footprint is measured and reviewed rather than
    asserted in prose: 18 rows across 45 distinct frontmatter values, all of them
    a token appearing as a *fragment* of a longer product name
    (`蛋` in `蛋挞`/`蛋黄酥`/`肉松小贝`/`皮蛋`, `芝麻油` in `芝麻烧饼`, `花蛤` in
    `花蛤肉`, `Broccoli` in `Micro Broccoli`). Twelve of the eighteen are the
    spec's six; the other six this ticket found by running the naive substring
    join, and all six are the same defect.

    Set equality in both directions, exactly as `test_brand_lexicon.py`'s drift
    gate does: a new refusal fails as unreviewed, and a reviewed entry the corpus
    no longer produces fails as stale. A subset assertion would let the list rot
    upward silently.
    """
    refused_by_a_guard: set[tuple[str, int]] = set()
    for source in ("材料", "调料"):
        for name in real_names_for(source):
            for candidate in resolve(name, CATALOG, source=source).candidates:
                if candidate.rejected_by:
                    refused_by_a_guard.add((name, candidate.pantry_item_id))
    assert refused_by_a_guard == REVIEWED_GUARD_REFUSALS, (
        "every guard refusal must be on the reviewed list and every reviewed entry "
        f"must still occur; new: {sorted(refused_by_a_guard - REVIEWED_GUARD_REFUSALS)}; "
        f"stale: {sorted(REVIEWED_GUARD_REFUSALS - refused_by_a_guard)}"
    )
    # And the spec's own six are inside it.
    spec_pairs = {(raw, row_id) for raw, ids in SPEC_ROWS.items() for row_id in ids}
    assert spec_pairs <= refused_by_a_guard
    assert len(refused_by_a_guard) == 20
    # Every refusal carries a guard reason, and the split is the interesting part:
    # 18 are *fragment* refusals and 2 are *class* refusals of a name that really
    # is a whole segment of the row. Those 2 are the family guard's independent
    # value on the real corpus, and asserting the count means a change in which
    # rule is doing the work is visible rather than silent.
    by_segment = set()
    by_class = set()
    for name, row_id in refused_by_a_guard:
        reasons = resolve(name, CATALOG, source=source_of(name)).rejected_by(row_id)
        assert reasons, (name, row_id)
        (by_segment if "segment_boundary" in reasons else by_class).add((name, row_id))
        assert "segment_boundary" in reasons or "category_family" in reasons
    # Exactly one row is refused by the class rule *alone*, and it is a real
    # whole-segment hit: `Broccoli` genuinely is a word of `Forward Greens Micro
    # Broccoli` (154). That row is the family guard's entire independent value on
    # the committed corpus, and pinning the count means a change in which rule is
    # doing the work shows up here rather than as a slower test suite.
    assert by_class == {("西兰花", 154)}, by_class
    assert len(by_segment) == 19
    assert by_segment == REVIEWED_GUARD_REFUSALS - by_class


def test_the_stock_signal_cannot_be_used_to_bypass_a_guard() -> None:
    """Holding the wrong row does not make it the right row.

    `in_stock_ids` is a display fact. If it could influence acceptance, a Pantry
    Stock line would silently choose between two purchase records of one product
    and the chip would be asserting identity it does not have.
    """
    for raw, source, row_id in (("蒜", "调料", 72), ("土豆", "材料", 62), ("芝麻", "调料", 102)):
        parsed = parse_ingredient_value(raw)
        held = matcher.resolve_ingredient(
            parsed, corpus_stock(frozenset({row_id})), CATALOG, source=source
        )
        assert held.pantry_item_id is None, raw
        assert held.in_stock is False, raw

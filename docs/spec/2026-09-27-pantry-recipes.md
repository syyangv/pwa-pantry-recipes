# Pantry Recipes PWA — Feature Specification

- **Spec ID:** `2026-09-27-pantry-recipes`
- **Date:** 2026-09-27
- **Repo:** `syyangv/pwa-pantry-recipes` (public, `main`)
- **State:** Scaffold (infrastructure only). This spec describes the *first*
  feature implementation. Nothing described here exists yet.
- **Pattern reference:** `pwa-template/docs/pwa-template.md` — cited by section
  number throughout. Every non-obvious decision names the pattern that governs
  it.
- **Sibling references:** `pwa-obsidian-daily` (vault reads, daily-note writes,
  auth, ESM, launchd), `pwa-wardrobe` (SQLite migrations), `pwa-deals`
  (`X-Client-Id` mutation ledger), `wholefoods-to-pantry` (the pantry catalog
  producer), `nutrition-intake` (units/nutrients, deferred).
- **Vocabulary:** `CONTEXT.md` is the glossary. This spec introduces no synonym
  for any term in it. New terms are declared in §5.3 with an explicit
  `CONTEXT.md` amendment required in the same commit, as `AGENTS.md` § *Domain
  docs* demands.

> **Deviation from the `to-spec` template, by explicit instruction.** The
> template says "Do NOT include specific file paths or code snippets." The
> requester overrode that: this spec names real files, routes, tables, columns,
> functions, and test filenames so an implementer re-derives nothing. Code is
> limited to the places where it encodes a decision prose cannot — the tier
> ladder (§9.8), the normalization strip order (§9.7), the chip classifier
> (§9.13.2), and the guard order (§9.17) — and each is marked decision-dense
> rather than runnable.
>
> **Second deviation.** The template publishes the spec to the issue tracker
> with a `ready-for-agent` label. The requester asked for a committed file and
> no PR and no issue. `AGENTS.md` records the tracker as GitHub issues on this
> repo via `gh`; creating the tracking issue belongs to the `/to-tickets` phase.

---

## 1. Problem Statement

The household buys groceries through a pipeline that already knows exactly what
was bought — `wholefoods-to-pantry/assets/pantry_items.db`, 178 rows with
canonical names, categories, variants, and prices. The household also keeps 16
cooked recipes as Obsidian notes, each with a `材料` (Ingredients) list, a `调料`
(Seasonings) list, a `烹饪工具` (Cooking Tools) list, and auto-maintained cooking
history frontmatter. Those two bodies of knowledge have never been connected.

The result, in practice: standing in the kitchen with half a bag of greens and
a lemon, there is no way to answer *"what can I actually make right now?"*
without opening three vault folders and eyeballing a pantry note. The
information exists; the join does not.

The second half of the problem is the write. When a cook does happen, the record
of it currently exists only if the user remembers to type `[[盐焗鸡]]` into the
daily note by hand. That manual step is why the cooking frontmatter is already
visibly stale on several notes — `Paradiso三明治` shows `cooking_count: 3` with
`auto_updated: 2026-05-17` while cooks continued through later notes. The
record exists to feed `Helper/utils/recipeTracker.md`, which scans daily notes
for recipe wikilinks and rewrites nine frontmatter fields. Today that pipeline
is fed by hand.

---

## 2. Solution

A local-only, installable PWA that joins the two sources and writes the result
back into the vault.

- **Browse** a list of Recipes from `Hobbies/做饭/Recipes`, each showing an
  honest one-line match summary (`4/6 ingredients found — missing: 香菇, 娃娃菜`)
  plus a colour-coded Ingredient chip row.
- **Inspect** one Recipe: its Ingredients, Seasonings, Cooking Tools, steps, and
  Recipe Cooking History, with a per-Ingredient breakdown of *why* each match
  resolved the way it did.
- **Log a cook**: pick a date (defaulting to today) and the app inserts a
  `[[RecipeName]]` **list item** directly under the daily note's `笔记` heading —
  the exact input `recipeTracker.md` consumes, which then advances the recipe's
  cooking frontmatter. If that day's daily note does not exist, the app **fails
  with a named, actionable error** and never creates the note (F4).
- **Curate** three short PWA-owned Meal Shortlists (breakfast / lunch / dinner)
  to answer "what do I usually eat for lunch" without touching the vault.

The Obsidian vault stays canonical. The PWA *projects* Markdown and performs
exactly one class of write — the Cooking Log. It never rewrites recipe steps,
never edits a recipe note, never creates a Pantry Item, and never writes to
`pantry_items.db`.

---

## 3. Decisions locked by the user

**Every flagged decision is now closed: four product decisions (D1–D4) and
twenty implementation decisions (F1–F20).** Each was flagged for confirmation
when this spec was first written; all of them have since been answered by the
user, and §3 is the single home of every one of them. A future implementer or
reviewer must not re-litigate any of them and, critically, must not treat the
known-bad matching quality in D1 as an undiscovered bug and "fix" it away. The
evidence under each was measured against the live vault and the live catalog and
shown to the user, who chose to proceed on that evidence.

**§3 is the authoritative record of every locked decision.** Each entry states
the decision, the reasoning, and — where the call constrains code — the section
that specifies the implementation, so a ticket implementer reading only its own
section plus §3 never has to guess. Where an entry says "the user reviewed and
accepted this", that is a review, not an assumption.

**The `F` numbers are stable identifiers and are NOT renumbered.** The sequence
F1–F20 is now **fully closed**, and it is **historical**: it is not a sequence to
be compacted, re-sequenced, or renumbered. §3, §4, §6, §9, §10, §13 and §14 all
cross-reference these ids, and so does the research record, so renumbering would
break every one of those references to buy a tidier list. The earlier drafts used
**gaps at 1, 3 and 4** as the signal that those three decisions were resolved;
that convention is now **retired**, because F1–F20 is contiguous precisely
because all twenty are resolved. A newly discovered decision continues at
**F21**. The existing ids never move.

**One decision was overridden after explanation, and the override is the
specification.** **F2** was *not* confirmed as the spec author first proposed it.
The user rejected the proposal as written and chose a hybrid: keep the inverted
unique index, add a **per-slot `IntegrityError` catch** in `resolve_all()`, and
do **not** let the error abort the enclosing `BEGIN IMMEDIATE` transaction. The
wording of §9.10 that the override invalidated has been rewritten so the
transaction's integrity guarantee and the per-slot catch coexist as specified.
F2 below is the authoritative statement; §7.3 and §9.10 implement it.

### 3.1 Index of locked decisions

Twenty-four decisions, all closed. "Specified in" is where a ticket implementer
builds it; `—` means the decision is a policy or a contract that constrains
several sections rather than one.

| Id | Subject | Specified in |
|---|---|---|
| **D1** | Match recipe `材料` against `pantry_items.db`; results materialized | §3.2, §9.6–§9.9 |
| **D2** | The cook wikilink is a bare `[[RecipeName]]` under `笔记` | §3.2, §9.14 |
| **D3** | Three PWA-owned Meal Shortlists; the vault stays meal-free | §3.2, §7.4, §9.12 |
| **D4** | Honest match display + a debug provenance toggle | §3.2, §9.13 |
| **F1** | Pantry Stock state comes from live `Pantry.md`, in addition to the catalog | §3.3, §6, §9.11.3 |
| **F2** | Mapping key keeps the inverted unique index **and** recovers per slot (**user override**) | §3.3, §7.3, §9.10 |
| **F3** | The link is a list item directly under `# 笔记` | §3.3, §9.14 |
| **F4** | A missing daily note is a named error, never an automatic creation | §3.3, §6, §9.15 |
| **F5** | The cook log is online-only and excluded from the outbox | §3.3, §9.18.1 |
| **F6** | `manual`-row immutability via a `BEFORE UPDATE` trigger | §3.3, §7.3, §9.10 |
| **F7** | Provenance is always in the payload; the 调试 toggle is render-only | §3.3, §9.13.4, §9.16 |
| **F8** | `严格模式` is a stateless `?strict=1` query param | §3.3, §9.16 |
| **F9** | `aiosqlite` in `[project].dependencies` | §3.3, §4, §6.1, §7.1 |
| **F10** | `playwright` in an **optional** `[browser]` extra, not in `[test]` | §3.3, §6.1, §10.5 |
| **F11** | Cache TTLs: catalog 300 s / stock 30 s / recipes 60 s | §3.3, §6 |
| **F12** | Exactly three meal slots, closed `CHECK`, no `snack` | §3.3, §7.4, §9.12 |
| **F13** | `cook_log_receipts` ships; `CONTEXT.md`'s Cooking Record is amended | §3.3, §5.3, §7.5 |
| **F14** | The golden test reads a committed snapshot, never a live sibling DB | §3.3, §10.3 |
| **F15** | Emoji runs: hand-built range class over an explicit key set, not `regex` | §3.3, §9.6, §9.7 |
| **F16** | Under `严格模式` a staple-satisfied Seasoning still counts as found | §3.3, §9.13.1 |
| **F17** | Headline is `n/total` from `材料`; no `cookable` boolean is ever published | §3.3, §5.1, §9.13.1 |
| **F18** | Variation matrix: `cache-first` shell, network-only APIs, explicit banner, no write allowlist | §3.3, §9.2, §9.17 |
| **F19** | Pull-to-refresh on the recipe list only | §3.3, §9.4, §15 |
| **F20** | No pagination on the recipe list | §3.3, §13.12 |

### 3.2 Product decisions (D1–D4)

#### D1 — Match recipe `材料` against `pantry_items.db`, as originally asked

**The decision.** Recipe Ingredients are matched against the 178-row pantry
catalog. Match results are materialized into a PWA-owned mapping table.

**The measured ceiling, recorded so it is not rediscovered.**

| Measurement | Value | How it was obtained |
|---|---|---|
| Recipes fully cookable today (every Ingredient resolved) | **3 of 16** | Exact + variants + normalized-exact join over the 16 real recipe notes and the 178-row catalog |
| Distinct `材料` values with no catalog match at all | **58%** | Distinct-value join; includes values that are seasonings-by-another-name and brand-only strings |
| Naive fuzzy (Levenshtein ≤ 2 over the raw string) false-positive rate | **~27%** | Fuzzy join of every distinct `材料` value against all 178 `canonical_name`s |
| Naive substring/prefix matches that are demonstrably wrong | 5 rows / 6 collisions (see below) | `蒜` hits two catalog rows, so six pairwise collisions exist across five table rows |

**The must-not-match cases, all confirmed present in the live catalog.**

| Recipe-side token | Row a naive substring/prefix match would hit | id | Why it is wrong |
|---|---|---|---|
| `蒜` (garlic) | `柴米 蒜香蒸茄子 300 克` | 72 | `蒜香` is a *flavour descriptor* of a dish, not garlic. Category `1.2` (prepared), not `1.1` (produce). |
| `蒜` (garlic) | `乐事 2026FIFA世界杯限定联名薯片蒜蓉面包味` | 110 | `蒜蓉` flavour; category `4` (snack). |
| `土豆` (potato) | `好丽友 呀!土豆 薯条 里脊牛排味 70 克` | 62 | Brand + snack, category `4`. |
| `芝麻` (sesame) | `好丽友 高笑美芝麻饼干 216 克` | 25 | `芝麻饼干` is a sesame *biscuit*, category `4` — while real sesame oil lives in `1.1`. |
| `芝麻` (sesame) | `芝麻烧饼` | 102 | `烧饼` is a flatbread, category `1.1`. |

A live proof that "strip the first token as a brand" is wrong: the catalog row
`Mushroom Dried Morel Mushrooms` has a **food word** (`Mushroom`) in the brand
position, while `柴米 蒜香蒸茄子` has a real brand (`柴米`) in the same position.
Position cannot disambiguate; a closed lexicon can.

**A sixth collision case, and the reason both guards must be tested separately.**
Counted by *recipe-side token*, there are **six** live flavour collisions, not
five: `蒜` hits **two** catalog rows — `柴米 蒜香蒸茄子 300 克` (72) *and*
`乐事 2026FIFA世界杯限定联名薯片蒜蓉面包味` (110, category `4`) — on top of
`土豆`→62, `芝麻`→25 and `芝麻`→102. The extra fact that matters is **which
guard stops each row**:

| Row rejected | Stopped by the segment-boundary guard alone | Stopped by the category-family guard alone |
|---|---|---|
| `蒜` → 72 `柴米 蒜香蒸茄子 300 克` (`1.2`) | **yes** — `蒜香` is a longer CJK word | no — `1.2` is in the same family as produce |
| `蒜` → 110 `乐事 …薯片蒜蓉面包味` (`4`) | **yes** — `蒜蓉` is a longer CJK word | **yes** — family `4` vs real garlic's `1.1` |
| `土豆` → 62 `好丽友 呀!土豆 薯条` (`4`) | **yes** — `土豆` is not a whole segment | **yes** — family `4` vs produce's `1.1` |
| `芝麻` → 25 `好丽友 高笑美芝麻饼干` (`4`) | **yes** — `芝麻饼干` is a longer CJK word | **yes** — family `4` vs sesame oil's `1.1` |
| `芝麻` → 102 `芝麻烧饼` (`1.1`) | **yes** — `芝麻烧饼` is a longer CJK word | no — same family |

So the category-family guard **independently** rejects `芝麻`→25 and `蒜`→110 on
category alone, with no tokenization at all; and the segment-boundary guard
independently rejects **all five rows in the table above, i.e. all six
collisions**. **Both guards are independently required**, and
`tests/recipes/test_must_not_match.py` must assert **each row individually**
*and* include a **negative control per guard**: a test that disables one guard
and asserts the row that only that guard stops now resolves **wrongly** — a
positive control would pass whether or not the guard worked. A guard with no test
that fails when it is deleted is not a guard, and a single combined assertion
over the rows would leave either guard deletable.

**Evidence corrections found while verifying the above.** The direction of every
figure the user reported is right, but **several are stale or mistranscribed**.
Use these instead — they are the numbers the golden test (§10.3) freezes.

- There are **26 distinct `材料` values** across the 16 recipes, in **five**
  shapes, not four (§9.6). The fifth shape the user did not enumerate is
  `X/Y` with **no leading emoji** (`香料/Basil`; `🍞/focaccia` also has a `/`
  but does have an emoji). The parser must handle it.
- The **emoji-only** list spans **both** frontmatter keys, and the transcription
  it was first reported with is wrong. **`🍔` appears in none of the 16 notes**;
  `🥔` (potato, from 烤土豆) is the value that was meant. The corrected list is
  **6 from `材料`** — `🥦 🥚 🍚 🥔 🍠 🍅`, which is 6 of the 26 distinct `材料`
  values, i.e. **23%** — plus **3 from `调料`**: `🧄 🫚 🍋‍🟩`. `🥚` appears in
  **3 of 16** recipes (Easy Fragrant Fried Rice, 番茄炒蛋, 茶碗蒸) — confirmed.
  `🌶️` is *not* in this list: in `材料` it occurs only in the `🌶️/Shishito`
  shape, which carries a recoverable name. Because `🍋‍🟩` is a **three-codepoint
  ZWJ sequence**, the parser needs an **explicit dictionary key set** (§9.6) — a
  Unicode property class cannot express "one key that happens to be a ZWJ run",
  and matching the three codepoints separately produces garbage.
- Exact-name-join fragility is **worse** than stated. `Logistics/库存/Pantry.md`
  has **46** open non-unit task lines today and **2** of them fail an exact join
  against the catalog: `禾苑 蟹粉鱼肉狮子头 冷冻 280 克` (catalog id 108 is
  `禾苑 蟹粉鱼肉狮子头`) and `Sanpellegrino CIAO! Peach Sparkling Water,
  24-Pack`. The user quoted "1 of 23". This is the evidence behind F1's join
  design, and it is *worse* than the recipe→ingredient join — see F1.
- The `variants` column is **effectively empty**: only 3 of 178 rows are
  non-empty, and two of those are just the long product name. The
  "exact `variants` alias" tier will match essentially nothing against the real
  catalog. It is implemented for completeness and is **not** load-bearing; the
  load-bearing tiers are exact `canonical_name`, the brand+size-stripped
  normalized tiers, the synonym set, and the staples allowlist.
  **Consequence for the golden test: tier 3's coverage in
  `golden_match_results.json` is legitimately near-zero. Near-zero tier-3
  coverage is the expected outcome, not a bug** — do not "fix" it by loosening
  the tier, by widening the synonym set to compensate, or by deleting the tier.
  The tier stays because `CONTEXT.md` defines Pantry Item Alias in terms of
  `variants` and a future catalog re-import could populate it.
- **Category `1.1c` (condiments) has exactly 1 row in 178** — id 177,
  `李锦记 蒸鱼豉油 14 盎司`. This single fact is the empirical basis for the
  assumed-on-hand treatment of Seasonings and for the `严格模式（含调料）` toggle
  existing at all.
- **5 catalog rows are the same product under two names** — `优质白桃礼盒`
  104/128, `韩国紫苏叶` 39/105, `POM Wonderful …` 155/167, `台湾旺旺浪味仙 …`
  27/38, `乐事 … 薯片牛肉派味` 111/124. So even a *successful* name join can
  land on either of two ids, which is why the mapping must be materialized and
  repairable rather than recomputed per request.

**Consequence accepted.** The headline answer is frequently "you cannot make
this", and a wrong fuzzy match is permanent once persisted. §9.7, §9.8 and §9.10
specify the guard (ordered tier ladder, closed brand lexicon, single-character
CJK segment-boundary rule, single-food-class rule on the synonym tier) and the
mitigations (re-resolve action, debug provenance toggle, immutable `manual`
overrides).

#### D2 — Logging a cook appends a `[[RecipeName]]` wikilink under the daily note's `笔记` heading

**The decision.** The one vault write this app performs is a bare
`[[RecipeName]]` wikilink — written as a Markdown **list item** — inserted into
`日记/YYYY/YYYY-MM-DD.md` under the `笔记` heading, on a user-chosen date
defaulting to today. The exact byte placement is F3.

**Why the format is not negotiable.** `Helper/utils/recipeTracker.md` computes
`first_cooked`, `last_cooked`, `cooking_count`, `cooking_frequency`,
`cooking_years`, `recent_activity`, `favorite_season`, `cooking_patterns`, and
`auto_updated` from:

```
dv.pages('"日记"').where(p => p.file.outlinks.some(l =>
    l.path.includes(recipe) || l.display === recipe))
```

The wikilink we append **is** the input that plugin consumes. Its text must
equal the recipe note's basename exactly — not a display alias. The bare form
is what the vault already contains (`[[盐焗鸡]]`, `[[花蛤拌饭]]`).

**A subtlety the writing path must know.** `cooking.length` counts *pages*, not
links:

```
cooking.length = dv.pages('"日记"').where(...)
```

Two `[[盐焗鸡]]` lines in one daily note still count as **one** cook. A
duplicate append therefore cannot corrupt `cooking_count`; the dedupe
requirement in §9.14 is for auditability and for a readable daily note, not for
count correctness. Stated here, restated as a numbered invariant in §13, and
restated again in F3 — **three copies on purpose, so that nobody "optimizes"
the dedupe away on the theory that it protects a counter. It does not.**

**A concrete placement conflict, now resolved as F3.** `parse_sections()` from
`app/vault/sections.py` computes the `笔记` region as **just the heading line** —
verified against the live `日记/2026/2026-09-27.md`: byte span `4265..4275`,
region content `b'\n'`, `blank=True` — because the very next line is a
` ````columns ` fence and a region's end is the first following fence or heading
line. Meanwhile the vault's *existing* logged cooks sit in two other places:
`2026-03-10.md` has `[[盐焗鸡]]` immediately after the
`![[dailyModify.base|ordered-list]]` embed, and `2026-09-14.md` has
`[[花蛤拌饭]]` immediately after the `# Event` heading, before that section's
`columns` fence. "Under the `笔记` heading" as literally specified means *above*
the columns fence, matching neither existing example.

**The rule is now fixed: the link is a list item directly under the `笔记`
heading.** F3 below states the exact region-insertion algorithm, including the
`blank=True` empty-region case. The two existing logged cooks remain where they
are; they were typed by hand and are not a precedent the write path follows.

#### D3 — Breakfast/Lunch/Dinner are three PWA-owned shortlists; the vault stays meal-free

**The decision.** No recipe gets a meal classification. No new recipe-note
frontmatter field. Three user-curated, ordered Meal Shortlists live in the PWA's
own SQLite and are **never** written back to the vault.

**Why this constrains the schema.** One Pantry Item must be claimable by many
recipe Ingredients — `空心菜` is a 材料 in 3 of the 16 recipes (`拌空心菜`,
`煮菜菜`, `花蛤拌饭`) and maps to catalog id 83. Therefore the mapping table's
unique key includes the recipe, and the reverse index on `pantry_item_id` is
**non-unique**. See §7.3 and the explicit warning there.

**Consequence accepted.** The Meal Shortlists are PWA-owned state that can drift
from the vault: rename a recipe note in Obsidian and the shortlist row points at
a basename that no longer resolves. Cooking history is **not** grouped by meal —
the shortlists are an input convenience, not a record. An empty shortlist is a
normal empty state, not an error, and renders as an invitation rather than a
failure (template §3d: only show "No items" after a *successful* empty
response).

#### D4 — Honest match display plus a debug provenance toggle

**The decision.** The default UI is one line plus a chip row. **Never** a single
boolean. Recipes are sorted by `found/total` descending with `last_cooked`
(frontmatter) as the tiebreak. **Low-scoring recipes are never auto-hidden** — a
`0/6` recipe is a legitimate shopping-list seed.

Chip colour classes and their exact meaning (§9.13.2):

| Class | Meaning |
|---|---|
| `chip--in-stock` | Resolved to a Pantry Item that is an **open line in `Pantry.md`** — i.e. Pantry Stock, per `CONTEXT.md` |
| `chip--have-been-buying` | Resolved to a Pantry Item in the catalog but **not** currently open stock (bought before, not held now). **Counted as missing in the headline** — see the stock-score amendment in §9.13.2 |
| `chip--in-stock chip--manual` / `chip--have-been-buying chip--manual` | Resolved via `match_method='manual'` — same colour, distinct outline, so a hand fix is visibly different from a machine match |
| `chip--assumed-staple` | Resolved by `staples.yaml`. Assumed on hand. **Excluded from the headline score** in the default view |
| `chip--missing` | Unresolved Ingredient, or (strict view only) an unresolved Seasoning |
| `chip--ignored-seasoning` | Seasoning excluded from scoring in the default view |

The five buckets are exactly the distinction `CONTEXT.md` insists on between
Pantry Item (immutable catalog record of what was *bought*) and Pantry Stock
(what the household *has*). A merely `chip--have-been-buying` chip is the UI's
way of not conflating them.

**Those two stock tiers are only honest if Pantry Stock is read from the live
vault, not inferred from the catalog — which is F1, below.** With the catalog
alone, `红苋菜苗` and `新鲜小叶茼蒿` would render as `chip--in-stock` while being
bought-before-but-not-held, and the distinction D4 is built on would collapse
into a single colour. F1 is not an optimization on top of D4; it is what makes
D4's core distinction true.

The `调试` toggle in Settings reveals, behind every chip: the resolved tier, the
`match_method`, the candidate Pantry Item id(s) considered, and the confidence.
It is a render toggle, not a second data path (§9.13.4, **F7**, locked).

### 3.3 Implementation decisions (F1–F20)

#### F1 — Pantry Stock state comes from the live vault `Pantry.md`, in addition to the catalog

**The decision.** The stock layer reads **two** sources, and both are required.
`pantry_items.db` supplies **identity** — the rename-stable `pantry_item_id`,
the brand, and the `💵` price. `Logistics/库存/Pantry.md` supplies **state** —
`open` / `in_progress` / `done` / `cancelled`, per Pantry Unit. The engine never
infers "I have it" from "I have bought it".

**The measured evidence, and why catalog-only is not merely worse but wrong.**

| Pantry Item | id | In the catalog | Open in `Pantry.md`? | Catalog-only verdict | Truth |
|---|---|---|---|---|---|
| `红苋菜苗` | 139 | yes | **no** | in stock | bought before, not held |
| `新鲜小叶茼蒿` | 70 | yes | **no** | in stock | bought before, not held |
| `空心菜嫩苗` | 83 | yes | **no** — an earlier line was `❌` cancelled 2026-09-22 and the item was re-bought 2 days later | in stock | held again, but only the second purchase is current |

The first two would both render as `chip--in-stock` from the catalog alone. The
third is the subtler failure: `空心菜嫩苗` was cancelled on 2026-09-22 and
re-bought on 2026-09-24, so the catalog's single row cannot distinguish "held"
from "held, then cancelled, then re-held", and a catalog-only answer would have
been right by accident today and wrong the moment the household finished the
second bag. **This is what makes D4's `in-stock` / `have-been-buying` tiers real
rather than decorative.**

**What is reused, unchanged, from
`pwa-obsidian-daily/app/vault/pantry.py`.** The port is a port, not a rewrite;
the parser is already hardened against the exact failure modes the vault has.

| Element | Contract, kept verbatim |
|---|---|
| `parse_pantry(source: bytes, *, max_bytes: int = 2_000_000) -> PantrySnapshot` | Walks numbered H1 sections, H2/H3 subsections, `dataviewjs` / `Tasks` fences, and task lines. `_MAX_ITEMS_TOTAL = 1000` bound kept. Byte-span `start`/`end` discipline kept. Typed `PantryError` failures kept. |
| `_TASK` | `^(?P<indent>[ \t]*)- \[(?P<status>.)\][ \t]*(?P<text>.*)$` — the status character is a **single character**, and it is matched, not assumed. |
| `_MONEY` | `💵\s*\$?([\d,]+(?:\.\d+)?)` — the effective per-unit price (see the invariant below). |
| `_UNIT` | `^\d+\/\d+` — the unit-split marker that identifies a `k/N` **child**. |
| `_ADDED` / `_ENDED` | `➕\s*(\d{4}-\d{2}-\d{2})` and `[✅❌]\s*(\d{4}-\d{2}-\d{2})` — the latter is what makes a cancellation date recoverable. |
| `_TAG` / `_TAG_TOKEN` | `(?:^\|[\s\t])#(?P<tag>[^\s#]+)` and its consuming twin. Pipes are escaped as `\|` for GFM table safety; the regex itself has none. |
| `_OPEN_STATUSES` | `{" ", "/"}` — `[ ]` is open, `[/]` is in-progress. **An unrecognized marker is never open**: `[-]`, `[>]` and friends are dropped. |
| `derived_status(index)` | A parent is `done` iff **all** children are `done`; `in_progress` if **any** child is `done` or `in_progress`; else `open`. Childless rows use `raw_status`. |
| Per-unit parent exclusion | A row matching `_UNIT` is a unit. Its parent is **excluded from every count and every money total** — see the invariant below. |
| `PantryIndex` | TTL-cached read-only snapshot provider: `snapshot()` → `_refresh()` gated on `time.monotonic()`, `PathSafetyError` → `PantryError("pantry_source_unreadable")`, `None` → `PantryError("pantry_source_missing")`. The PWA's `PantryStockIndex` is this class renamed, with `stock_cache_seconds` as its TTL. |

**The CRITICAL invariant — restated here, and it is a test obligation.**
`💵 $X.XX` on a **parent** line is the **effective per-unit price**, not an item
total. Every consumer must therefore **EXCLUDE the unit parent** and count each
**open `k/N` subtask at that price**. A half-used item counted at twice its
value is the failure this prevents, and it is `CONTEXT.md`'s Pantry Unit rule
verbatim.

**Three implementations of this math must stay in parity:**

1. the `existingPantryValue` dataviewjs block in `Logistics/库存/Pantry.md`,
2. `Helper/scripts/pantry_snapshot.py`, and
3. the PWA's own logic in this app.

All three skip `[x]`, `[>]` and `[-]`, all three still count `[/]`
(in-progress), all three skip the parent of a `1/N` subtask while counting those
subtasks at the parent's per-unit price, and all three drop tagged rows.
Divergence between them is invisible until a number is wrong in a place the
user trusts, and two of the three are not in this repo's test suite — so
**parity is asserted here, mechanically**:

- `scripts/snapshot_pantry_catalog.py` **regenerates and diffs** (it already
  exists for the catalog; this is its second job, not a new script). When run
  against the live vault it computes the untagged, open, non-unit-parent total
  **twice** — once with the PWA's rule and once with `pantry_snapshot.py`'s own
  total for the same date — and **refuses to emit a refreshed fixture if the two
  disagree**. A parity break therefore blocks fixture regeneration rather than
  being merged silently.
- `tests/fixtures/pantry_stock_math_parity.json` freezes the agreed total, the
  contributing line count, and the per-unit-excluded parent count.
- `tests/pantry/test_stock_math.py` asserts the PWA's logic reproduces that
  frozen number, and includes the three shape cases that break naive
  implementations: a `3/3` parent that derives to `done` and contributes
  nothing, a `1/2` parent with one open unit contributing **one** unit price, and
  a `[/]` in-progress unit counted as open.
- CI never reads the live vault or the live `Helper/` script, exactly as F14
  requires for the catalog. The live comparison is a **deliberate, local,
  human-run act**, the same discipline as the golden fixtures.

**The join between a catalog `pantry_item_id` and a `Pantry.md` line — and an
honest admission that it is the weakest link in this app.** A `Pantry.md` line
is free text; a catalog row is a normalized name. The join is therefore a
**normalized-name index built on `normalize_ingredient()`** — the *same*
normalization the matcher uses, which is the only reason the two sides can
agree at all — with, in order:

1. an exact hit on the normalized name index (`by_name`);
2. a hit on the tier-4 catalog basename index (`by_basename`), so
   `空心菜嫩苗 0.95-1.05 磅` on the pantry side reaches id 83;
3. an explicit **manual override**.

**The manual override is a committed, PWA-owned YAML file**, not a new table and
not a lexicon: `app/pantry/line_overrides.yaml`, keyed by the *normalized
`Pantry.md` line name* and valued by a `canonical_name` that is looked up in the
**live** catalog at read time. It is deliberately not keyed by id, because §9.8's
"never store a Pantry Item id in a lexicon file" rule exists for exactly this
reason (`items.id` is `AUTOINCREMENT` and a re-import can renumber it) and an
override file is a lexicon in every respect but location.

**This join is measurably less reliable than the recipe→ingredient join, and the
spec says so rather than hiding it.** The measured figure is **2 failures in 46
open lines, not 1 in 23**: the misses are `禾苑 蟹粉鱼肉狮子头 冷冻 280 克` (the
catalog row is `禾苑 蟹粉鱼肉狮子头`, id 108 — a trailing `冷冻` size/condition
token survives) and `Sanpellegrino CIAO! Peach Sparkling Water, 24-Pack` (no
catalog row resolves at all). Add the 5-row duplicate-name set from D1 and a
*successful* join can still land on either of two ids. So:

- The join gets **its own unresolved bucket**, separate from the ingredient
  matcher's, surfaced in the `调试` provenance toggle as
  `stockJoinState ∈ {joined, override, unresolved}` alongside the resolved
  `pantry_item_id`, so a pantry line that no catalog row explains is visible
  rather than silently dropped from `in_stock_names`.
- `/health` gains `stock.unjoinedLineCount`, and `GET /api/recipes` returns
  `stockUnjoinedCount` so a rising number is visible without opening the debug
  view — the same treatment `staleMappingCount` gets in §7.3.
- The escape hatch is therefore **load-bearing, not a nicety**: without it, two
  live pantry lines are permanently unexplainable and the only remedy would be
  editing the user's vault, which this app must never do.

**The extra cost, stated honestly, and the degradation decision.** F1 buys
honest stock at the price of **one more vault file to read** (a second
server-owned relative path, a second TTL, a second `note_revision`) and **one
more failure mode**: if `Pantry.md` is missing, unreadable, or unparseable, the
**entire state layer degrades** — not one recipe, all of them.

**The degradation decision is fail closed, and it is not a close call.**
`PantryStockIndex` raises `PantryError`; `GET /api/recipes` and
`GET /api/recipes/{name}` return **503 `pantry_stock_unreadable`** and the UI
shows an error state naming the pantry note. There is deliberately **no**
fallback:

- not "assume in stock" — that silently inflates every score and is precisely
  the conflation `AGENTS.md` and `CONTEXT.md` forbid;
- not "assume not in stock" — that silently deflates every score to
  `have-been-buying`, which is a plausible-looking wrong answer, the exact thing
  `AGENTS.md` #3 says never to produce;
- not "serve the list with every chip downgraded" — that *looks* like data.

An honest empty answer and a plausible wrong answer are not the same thing, and
this app only ships the first. This is why §9.16 already carries a 503 on
`GET /api/recipes` for an unreadable source — F1 makes that branch real rather
than theoretical, and names it `pantry_stock_unreadable`. The recipe *index* is
unaffected, so `#/recipe/{name}` still shows a note's steps and history; only the
stock-derived chip colours are unavailable, and the provenance view says so
explicitly.

#### F2 — The mapping key keeps the inverted unique index **and** recovers per slot

**This decision is an override, and the override is the specification.** The spec
author's original call was `UNIQUE(recipe_note, ingredient_index)` **plus**
`UNIQUE(recipe_note, pantry_item_id)`, with **no** `REFERENCES` clause, and with
the resulting `sqlite3.IntegrityError` left to abort the enclosing transaction.
The user read that, understood the consequence, and **rejected it as written** in
favour of a hybrid. All three parts below are required; none is optional.

1. **KEEP `ux_ingredient_mappings_recipe_item`** —
   `UNIQUE(recipe_note, pantry_item_id)`, exactly as originally proposed. It is
   the inverted constraint: `pantry_item_id` **alone is not unique**, one Pantry
   Item must be claimable by many recipe Ingredients, and scoping the uniqueness
   to the recipe permits `空心菜 → 83` in three different recipes while still
   catching a real data defect — one recipe listing the same Pantry Item twice.
   It is the constraint; part 2 is what makes it survivable.
2. **ADD a per-slot `IntegrityError` catch in `resolve_all()`.** Catch
   `sqlite3.IntegrityError` **around the offending slot's write only**, then:
   - downgrade **only that slot** to `match_method='unresolved'`,
     `match_tier=0`, `pantry_item_id=NULL`, `confidence=0.0`;
   - record the **conflicting candidate** in that slot's `candidates_json` as
     `[{"pantryItemId": <id>, "canonicalName": <name>, "reason":
     "duplicate_slot_conflict"}]`, so the dropped resolution is auditable rather
     than invisible;
   - **continue** with every remaining slot. The catch is per slot, not per
     batch: a duplicate in slot 7 must not prevent slots 8–31 from resolving.
3. **DO NOT let the error abort the enclosing `BEGIN IMMEDIATE` transaction.**
   The previous §9.10 wording — *"One `BEGIN IMMEDIATE` transaction, so a
   failure leaves the previous state intact rather than a half-resolved
   table"* — was exactly the hazard the user rejected, because under it a single
   duplicate silently reverted **every** row to its pre-pass state. That
   sentence is rewritten; §9.10 now states precisely how the transaction's
   integrity guarantee and the per-slot catch coexist, down to the SQLite
   `ON CONFLICT ABORT` semantics that make coexistence possible at all.

**The failure mode this guards is a *silent* dropped resolution — the worst bug
class in this app.** Two ways it goes wrong without part 2: (a) one recipe lists
the same Pantry Item in two `材料` slots, the second write violates the inverted
index, and the whole resolve pass rolls back, so the app silently serves stale
mappings for **all 16 recipes**; (b) the constraint is dropped instead, and the
app renders two chips claiming the same SKU is in stock. D1's whole thesis is
that a wrong match is permanent once persisted and invisible, so the guard has to
be one that *degrades a single slot visibly* rather than one that *fails
everything invisibly*.

**Do not copy `nutrition-intake`'s bare `UNIQUE` on the FK column.** In SQLite a
`UNIQUE` on a nullable column permits unlimited `NULL` rows but only **one** row
per non-null id. That would make `空心菜 → 83` in `拌空心菜` and `煮菜菜`
**mutually exclusive** and would **silently drop a resolution** — one of the two
recipes would come back with that Ingredient `unresolved` and no error, no log,
and no way for the user to tell. This is a first-class requirement, not a
footnote, precisely because the failure is silent. The composite key is not an
aesthetic choice; it is the only form in which "many recipes, one SKU" and "one
recipe, one SKU" are both expressible.

**No foreign key, and why.** `pantry_items.db` is a different database opened
read-only; SQLite cannot express a cross-database `REFERENCES`. Adding a
same-named table here purely to hang a foreign key off would create a second,
writable copy of catalog truth, which `AGENTS.md` #4 forbids in spirit and D1
forbids in fact. Referential integrity is handled by re-resolution instead: a
mapping whose `pantry_item_id` no longer exists in the live catalog is detected,
surfaced as `staleMappingCount` in `/api/recipes`, and reset to `unresolved`.

**The evidence that no current recipe collides, preserved so a future
implementer knows this constraint is defensive, not load-bearing today.** The
32 `材料` slots across the 16 real recipe notes, verbatim from
`Hobbies/做饭/Recipes`:

| Recipe | `材料` slots (index: raw value) | Two slots on the same Pantry Item? |
|---|---|---|
| `Easy Fragrant Fried Rice` | 0: `🥦` · 1: `🥚` · 2: `包菜` · 3: `🍚` | no |
| `Paradiso三明治` | 0: `Mortadella` · 1: `开心果酱` · 2: `🧀/Stracciatella` · 3: `香料/Basil` · 4: `Arugula` · 5: `🍞/focaccia` | no |
| `凉拌黑木耳` | 0: `🍄/黑木耳` | no |
| `微波菜菜` | 0: `Kale` · 1: `香菇` · 2: `娃娃菜` | no — **closest call** |
| `拌空心菜` | 0: `空心菜` | no |
| `炒红苋菜` | 0: `红苋菜` | no |
| `烤土豆` | 0: `🥔` | no |
| `烤小辣椒` | 0: `🌶️/Shishito` | no |
| `烤红薯` | 0: `🍠` | no |
| `烤鲭鱼` | 0: `"[[Mackerel]]"` | no |
| `烤鸡翅` | 0: `🐔/鸡翅` | no |
| `煮菜菜` | 0: `空心菜` · 1: `茼蒿` | no |
| `番茄炒蛋` | 0: `🥚` · 1: `🍅` | no |
| `盐焗鸡` | 0: `🐔/带皮鸡腿` | no |
| `花蛤拌饭` | 0: `Clam` · 1: `🍄/香菇` · 2: `🌶️/Shishito` · 3: `空心菜` | no — **closest call** |
| `茶碗蒸` | 0: `🥚` · 1: `🍄/香菇` | no |

**`花蛤拌饭` (4 slots) and `微波菜菜` (3 slots) are the two closest calls**, and
they stay distinct. `花蛤拌饭` is the largest `材料` list in the vault and is the
only recipe whose slots span three different Pantry Category families — `Clam`
(1.2m/1.2s, ids 143 and 161: a same-family tie the fuzzy tier's uniqueness
requirement has to break), `空心菜` (1.1, id 83), and `🍄/香菇` /
`🌶️/Shishito` (unresolved). `微波菜菜` is the densest per-slot collision
candidate: `Kale` has three same-family catalog candidates (ids 10, 36, 56 — the
duplicate-name pattern D1 records), and `娃娃菜` sits one segment-boundary step
from `小白菜心` (ids 18, 106). Neither recipe has two slots landing on one
`pantry_item_id`, so neither trips the inverted index.

**Every repeated `材料` value in the vault repeats across *different*
`recipe_note` values, never within one recipe**, and that is exactly what the
composite key permits: `空心菜` in `拌空心菜`, `煮菜菜`, and `花蛤拌饭`; `🥚` in
`Easy Fragrant Fried Rice`, `番茄炒蛋`, and `茶碗蒸`; `香菇` in `微波菜菜`,
`花蛤拌饭`, and `茶碗蒸`; `🌶️/Shishito` in `烤小辣椒` and `花蛤拌饭`. **The
constraint is therefore a defensive data-integrity guard, not a load-bearing
mechanism for any of the 16 current recipes.** That is why part 2's degradation
path is a downgrade plus an audit record rather than a refusal: the day a
user-authored note actually lists the same Pantry Item twice, the correct outcome
is one honest `unresolved` chip with the conflict visible in the `调试` view —
not a 500, and not a silently smaller table.

**What the user reviewed and accepted in full:** the hybrid, the per-slot
downgrade, the transaction-continues wording, and the decision that a recipe
whose two `材料` both resolved to the same Pantry Item **shows the duplicate
rather than hiding it**. The UI and the `调试` toggle surface the conflict for
exactly this reason: `tests/mapping/test_store.py` (§10.2) must build a
two-`材料`-same-SKU fixture, assert the second slot is downgraded to
`unresolved`, assert its `candidates_json` names the conflict, and assert the
other slots of the same recipe still resolved.

#### F3 — The cook wikilink is a list item directly under the `# 笔记` heading

**The decision.** The Cooking Log append is `- [[RecipeName]]` inserted as the
**first list item inside the `笔记` region**, immediately after the `# 笔记`
heading line and before the following ` ````columns ` fence. This is F3; the
preference for inserting after the `![[dailyModify.base|ordered-list]]` embed
is **withdrawn**.

**The evidence that motivated the flag is preserved, because it explains why the
question was ever open.** `parse_sections()` from `app/vault/sections.py`
computes the `笔记` region as **just the heading line** — measured live against
`日记/2026/2026-09-27.md`: byte span **`4265..4275`**, region content
**`b'\n'`**, **`blank=True`** — because the very next line is a
` ````columns ` fence and a region ends at the first following fence or heading
line. The vault's two existing logged cooks are in **different** places:
`2026-03-10.md` has `[[盐焗鸡]]` immediately after the
`![[dailyModify.base|ordered-list]]` embed, and `2026-09-14.md` has
`[[花蛤拌饭]]` immediately after the `# Event` heading, before that section's
`columns` fence. `recipeTracker` is **position-agnostic** — it scans every
outlink on the page — so *every* one of these placements works for the tracker.
The two existing cooks were typed by hand and are ad-hoc, not a convention.

**The exact algorithm, against the `sections.py` heading/region engine.**

1. `store.read_existing_if_exists(relative)`. `None` → the F4 error path
   (§9.15). Never create.
2. `parse_sections(source)` → `require_unique("笔记", level=None,
   code="ambiguous_notes_section")`. More than one `笔记` region **fails closed**
   with 409 `ambiguous_notes_section`.
3. Locate the region's `heading_end` — the byte offset just past the newline that
   terminates the `# 笔记` heading line. This is the single insertion point; the
   region's `end` offset is **not** used, because for the `blank=True` empty
   region `end == heading_end` and for a populated region `end` is the first
   following fence, which would place the link *below* the columns fence and
   outside the section's column layout.
4. **"Creating the region" is a misnomer, and the spec resolves it explicitly:
   the heading already exists — nothing is created.** The `# 笔记` heading comes
   from the vault's own daily-note template and is present in every real daily
   note, so the region engine always finds it. `parse_sections` is a
   *locator*, never a writer: it returns offsets, and this spec adds no code path
   that emits a `# 笔记` heading. The only operation is **"insert the first list
   item under an existing heading"**. A note genuinely lacking a `笔记` heading
   is therefore a `require_unique` failure (409 `ambiguous_notes_section` is
   wrong for that case — the code is `notes_section_missing`, 422), never a
   silent heading insertion.
5. **Blank-line handling for the `blank=True` empty-region case, and the line
   terminator.** The measured live case is exactly this one: region content is
   `b'\n'` and `blank=True` — the heading line's own terminator is the only byte
   in the region. The splice at `heading_end` is:

   ```
   source[:heading_end] + b"- [[" + recipe + b"]]" + terminator + source[heading_end:]
   ```

   **The terminator is read from the source, never assumed to be `b"\n"`.**
   **The live daily note does not justify this rule; the rule stands anyway.**
   Measured, `日记/2026/2026-09-27.md` is **pure LF**: 152 `\n`, **zero**
   `\r\n`, and no file in `日记/2026/` contains a single CRLF. The 10-byte
   region span `4265..4275` is **not** evidence of a two-byte terminator: it is
   a **9-byte heading line** (`# 笔记` is 8 bytes of UTF-8 plus its own one-byte
   `\n`) **plus a 1-byte blank line** — the region's `b'\n'` content — so
   `heading_end` is 4274 and the arithmetic closes exactly with a one-byte
   terminator. An earlier draft of this step read that 10 as 8 + 2 and inferred
   CRLF; the inference was wrong, and the spec now states the measured facts.

   The rule is unchanged and still load-bearing, because the live note is only
   the *easy* case. A splice that hard-codes `\n` on a note that *is* CRLF
   would introduce a **mixed-terminator file** into the user's daily note:
   `git` and Obsidian both surface that as a whole-file diff, and
   `task-date-recorder` re-writes on a debounced `modify` event, so the noise is
   not transient. The same hazard exists in the LF case from the other
   direction — a plugin or an Obsidian sync client that rewrites the note into
   CRLF after we have read it, and a later write that then hard-codes LF. The
   implementation therefore takes the bytes between the heading text and
   `heading_end` from the region engine's own span and reuses them verbatim, and
   `app/vault/sections.py` handles **LF, CRLF, and lone CR** uniformly.
   `tests/cooklog/test_writer.py` asserts the terminator is byte-identical to
   the pre-image's for both a one-byte and a two-byte terminator, and
   `tests/vault/test_sections.py` adds the stronger property: a CRLF note must
   **never gain a lone LF**.

   **Do not "confirm" this rule by re-measuring the live note.** Every note in
   `日记/2026/` is LF today, so a re-measurement will keep saying LF and will
   keep looking like the rule is unnecessary. The rule's justification is the
   CRLF and lone-CR cases the engine must tolerate, not this one file.

   With the terminator handled, the `blank=True` case reduces to: the heading's
   own terminator is preserved in `source[:heading_end]`, the new list item
   brings its own, and the following ` ````columns ` fence still starts on its
   own line. The result is a two-line region (`# 笔记` / `- [[RecipeName]]`) and
   the region becomes `blank=False`. **No additional blank line is inserted and
   no existing blank line is consumed** — inserting one would place the link
   inside the columns fence's visual area on some renderers. When the region is
   already populated, the identical splice at `heading_end` makes the new item
   the **first** item, above existing content.
6. `link_line` is `- [[<recipe_note>]]`: a Markdown list item, no timestamp, no
   emoji, no trailing metadata, no `- [ ]` task box. It must not be a task — a
   task would be checkable and the user could strike it, and `recipeTracker`
   would still read it, so a struck cook would be silently counted.
7. Commit via `AtomicNoteStore.transform_existing`, with CAS and
   `PostWriteVerificationError`, exactly as §9.14 already specifies. The splice
   remains a pure byte operation over offsets computed from the original
   `source`; nothing is re-serialized.

**`recipeTracker` is position-agnostic, and that is why this is safe.** Its
predicate is `p.file.outlinks.some(l => l.path.includes(recipe) ||
l.display === recipe)` over every daily-note page, so the link is found wherever
in the note it lands. The list-item form is still the **bare** wikilink text the
plugin requires: `[[RecipeName]]` verbatim, never a display alias
(`[[盐焗鸡|盐焗鸡]]` would also match via `display`, but the bare form is what the
vault already contains and what §9.14's dedupe regex expects).

**And once more, because it is the finding most likely to be "optimized" away:
`recipeTracker` counts PAGES, not links.** `cooking.length =
dv.pages('"日记"').where(...)` returns one entry per daily-note page, so **a
duplicate append inside one daily note cannot inflate `cooking_count`** — not by
one, not by any amount. §9.14's dedupe and `cook_log_receipts`' unique index
therefore exist for **note readability and the audit trail**, *not* for count
correctness. They are not redundant and must not be removed on the theory that
the counter is safe; a note with four copies of `[[盐焗鸡]]` is a note the user
has to clean up by hand, and the receipt row is what makes "did the app write
this?" answerable.

#### F4 — A missing daily note is a named error, never an automatic creation

**The decision.** If the daily note for the requested date **does not exist, the
PWA does not create it.** The Cooking Log write fails with a clear, actionable
error that names the date and the expected vault path, and the user creates the
note in Obsidian and retries. The user chose this over the subprocess-and-ledger
alternative explicitly.

**The error shape.**

- **Status: `404`.** The addressed resource — `日记/YYYY/YYYY-MM-DD.md` — is
  absent, and 404 is the code that says so without pretending the request was
  malformed (422) or lost a race (409).
- **Envelope.** The scaffold's existing envelope is `{"requestId", "code"}`. This
  error is the **one** code that adds fields, and the extension is additive so
  no existing consumer changes shape:

  ```json
  {
    "requestId": "…",
    "code": "daily_note_missing",
    "message": "找不到 2026-09-27 的日记：日记/2026/2026-09-27.md。请先在 Obsidian 中创建这一天的日记，然后重试。",
    "date": "2026-09-27",
    "relativePath": "日记/2026/2026-09-27.md",
    "retryable": true
  }
  ```

  `message` names **both** the human date and the exact vault-relative path, so
  the fix is one paste into Obsidian's quick switcher. `retryable: true` is
  literal: the same request succeeds unchanged once the note exists. The UI
  renders `message` verbatim and does **not** synthesize its own copy, so there
  is one wording to keep correct.
- **This is a genuine extension to `_api_error`, and it is called out here
  because §9.16 and §9.19 previously said the envelope was unchanged.** The
  change is: the exception detail may carry an optional `message`, `date`,
  `relativePath`, and `retryable`, all of which are optional and all of which
  default to absent. Every pre-existing error code keeps emitting exactly
  `{"requestId", "code"}`. `/health` still leaks no paths, and `relativePath` is
  vault-*relative*, never absolute, so the Server-Owned Root invariant holds.
- **`GET /api/cook-logs?date=` returns the same 404 `daily_note_missing`** for a
  missing date, with the same shape. A read-back that returned an empty
  `entries` array for a date whose note does not exist would be a lie: it would
  say "you cooked nothing that day" when the truth is "there is no record of
  that day at all".

**A creation conflict is not "not found".** The note can exist in the vault
while the app saw it missing — iCloud sync lag, an Obsidian write landing
between our two reads, a network volume briefly stale. Reporting that as
`daily_note_missing` sends the user to create a note that already exists, and
the second attempt then fails differently, which is a confusing two-error
round-trip for what is really a lost race.

The rule follows the **concurrent-external-writer rule** already encoded in
`atomic_write.py`, where the write primitive never assumes it is the only
writer:

1. On `None` from `read_existing_if_exists`, **re-check exactly once** —
   re-resolve the path through `AtomicNoteStore` and re-read, with a bounded
   retry (2 attempts, no sleep loop; the race window is milliseconds).
2. If the second read returns bytes, the note **appeared under us**. This is
   reported as **`409 daily_note_created_concurrently`**, in the same envelope
   extension, with `relativePath`, `currentRevision`, and `retryable: true` —
   and it reuses §9.14's existing 409 conflict UX and resolve panel rather than
   inventing a second one. The user taps retry; the write proceeds normally
   because a note now exists.
3. The same shape covers the mirror case: if `transform_existing` raises
   `ConcurrentFileChange` because the target vanished before replace, that is
   `409 daily_note_changed`, the existing code, not `daily_note_missing`.
4. Only after **two** reads both return `None` is it `404 daily_note_missing`.

The point is that `daily_note_missing` means *the note is absent*, full stop. A
lost race is a `409`, and a `404` is a standing fact the user can act on.

**What is removed, and why this is a net simplification.** The previous draft of
this spec specified an Obsidian CLI + QuickAdd creation path, nine `Settings`
fields, a subprocess sandbox, a Templater settle window, an unusable-note
rollback, and a SQLite idempotency ledger keyed by a caller-supplied UUID. **All
of it is out of scope.** `AGENTS.md` non-negotiable #1 ("never creates a missing
daily note") is therefore **correct as written and needs no amendment** — which
is the single strongest argument for the decision, because the alternative forced
an amendment to a non-negotiable, a `README.md` line, and an `.env.example`
comment. `CONTEXT.md` needs no amendment either.

The cost, stated plainly: back-dating into a day the user never journalled now
requires one action in Obsidian before logging. That is a real cost, paid
deliberately, in exchange for removing a subprocess, a sandbox, a settle window,
a rollback, a ledger table, nine `Settings` fields, and a contradiction with a
non-negotiable — for a case that happens when the user has not written that
day's note at all, which is exactly when they should be writing it.

#### F5 — The cook log is online-only and excluded from the outbox

**The decision.** The Cooking Log does **not** go through the offline outbox. The
button renders **disabled** with `离线：需要连接后记录` when
`navigator.onLine === false`, and on a network-layer failure it surfaces a real
error. It **never** optimistically claims success. The outbox carries Meal
Shortlist add / remove / reorder and the manual Ingredient Mapping, and nothing
else. Fully specified in **§9.18.1**; the disabled-button reason is user story 47.

**Why (all four reasons, because any one alone is arguable).** (1) A Cooking Log
is a CAS-guarded write to a file Obsidian mutates constantly, and the outbox has
**no closed-app guarantee**, so a queued intent replays with a revision that is
stale by construction: it 409s, it parks, and the user is shown a resolve panel
for a meal they already ate. (2) Delivery is at-least-once and the domain
statement is *dated*, so a replay buys nothing a dedupe does not already give.
(3) The honest offline story: logging a cook requires the vault, the vault
requires Obsidian, and **if you are offline you are not cooking.** (4) The
frequency is roughly one write per cook, and the outbox exists to make a
*high-frequency* mutation durable under a *high-frequency* outage; neither
holds.

**F4 reinforces it rather than relaxing it.** An offline cook log would also have
to decide what to do about a date whose daily note does not exist, and
"fail with an actionable error" is not a useful thing to deliver two hours late.

**If this ever changes, the fix is not the outbox.** It is to make the write
primitive revision-tolerant: under `AtomicNoteStore._locked`, re-read and
re-apply the same append with a bounded retry, because a deduped append is
idempotent-by-content. That is a change to the write primitive, not to the
transport. Recorded in §13.7 as a known gap rather than pre-built.

#### F6 — `manual`-row immutability is enforced by a trigger

**The decision.** A `BEFORE UPDATE` trigger on `ingredient_mappings` raises
`ABORT` when `OLD.match_method = 'manual'`. Changing a hand fix is therefore a
**`DELETE` followed by a fresh `INSERT`** of a `manual` row — an auditable
delete + create, never a silent overwrite. `resolve_all()` **skips `manual` rows
entirely**. Specified in **§7.3** and **§9.10**.

**Why the harder mechanism.** The softer rule — no trigger, just "`resolve_all`
skips manual rows" — is simpler, and was the alternative. It is also
insufficient: "skips manual rows" is a property of *one* call site, while the
immutability this decision is about is a property of the *row*. A catalog-change
auto-re-resolve (§9.11.2), a future backfill, a repair script, or a hand-edited
query would all be free to clobber a hand fix. The trigger makes the guarantee
live in the schema, so it holds no matter which code path writes.

#### F7 — Provenance is always in the payload; the 调试 toggle is render-only

**The decision.** Every recipe response carries the full provenance on every
Ingredient slot: tier, `match_method`, `pantry_item_id`, `candidates_json`, and
F1's `stockJoinState`. The `调试` toggle is a **render-only** switch in
`localStorage` — **no separate endpoint, no `?debug=1` route, and no second
response shape.** Specified in **§9.13.4** and **§9.16**.

**Why.** F1's stock join is the app's **weakest link** — 2 known misses in 46
open lines, no tier ladder, no re-resolution pass — and its only defence is that
the misses are *visible*. A toggle that needs a round-trip is a toggle nobody
reaches, and a second response shape is a second thing that can drift from the
first. A 16-recipe list is a few kilobytes, this is a single-user loopback app,
and the fields are not sensitive.

#### F8 — `严格模式` is a stateless query parameter

**The decision.** `严格模式（含调料）` is `?strict=1` on the recipe routes, default
`?strict=0`. **No** persisted setting in SQLite and no `localStorage` server-side
counterpart; the server computes the score, so the parameter is the whole state.
Specified in **§9.16**.

**Why.** A persisted setting would need a round-trip before the first paint
(a visible flash of the wrong score on every cold start) and would create a
second source of truth that the URL and the stored value can disagree about. The
render-cache key already includes `strict` (§9.3), which is what makes the
stateless form cheap.

#### F9 — `aiosqlite` is a new **runtime** dependency

**The decision.** `aiosqlite` is added to `[project].dependencies` in
`pyproject.toml`. It is the app's **only** new runtime dependency. Precedent:
`pwa-wardrobe/app/database.py` — the `apply_pragmas()` block, the
`@asynccontextmanager connect_db()`, and the `_migrate_NNN_*` shape in **§7.1**
are copied from there. Specified in **§6.1**, **§7.1**, and the architecture
note in **§4**.

**Why, stated as a portability argument rather than a performance one.** The
alternative is stdlib `sqlite3` through `asyncio.to_thread`: zero new
dependencies, and fine for a single-user loopback app at roughly one write per
cook. But §7.1's migration idiom is lifted wholesale from wardrobe, and
**re-expressing that idiom over `to_thread` would port the code without porting
the idiom** — a divergence that is invisible in review and permanent in the
file. The async cost is one short-lived connection per request.

#### F10 — `playwright` is an **optional** `[browser]` extra, not in `[test]`

**The decision.** `pyproject.toml` gains

```toml
[project.optional-dependencies]
browser = ["playwright>=1.44"]
```

and **`playwright` is NOT added to `[test]`**, so CI's existing
`pip install ".[test,dev]"` is **unchanged** and the browser suite is opt-in.
Specified in **§6.1** and **§10.5**.

**Why.** Running the two flows in CI needs a browser binary on the ubuntu
runner, which is a real recurring CI cost for two flows. The alternative is
having no browser tests at all, and that cost is *silent*: the §2i back-button
scroll-restore bug and a stale-paint false pass are exactly the two failure
classes the sibling's `test_recipe_browse_flow.py` step 4 was written to catch,
and unit and API tests **structurally cannot** reach either — one is a
navigation-history behaviour, the other is whether pixels actually repaint. The
two flows are deterministic because their input is a `tmp_path` vault and a
`tmp_path` `APP_DATA_DIR` (§10.5), so opt-in costs nothing in flakiness.

#### F11 — Cache TTLs: catalog 300 s, stock 30 s, recipes 60 s

**The decision.** `catalog_cache_seconds = 300.0`, `stock_cache_seconds = 30.0`,
`recipe_cache_seconds = 60.0`, with the validators and bounds in **§6**. All
three are locked as the values above.

**Why each.** Catalog 300 s and recipes 60 s are conventional: both are
effectively immutable for the life of a session, and both are invalidated
explicitly (`catalog_revision` change ⇒ resolve; any write ⇒ `invalidate()`).
Stock 30 s is **load-bearing, not conventional**: it is now *the maximum
staleness a chip colour can have*, because the user toggles tasks in Obsidian
while the PWA is open (§9.11.3, §13.5). Raising it trades chip freshness for
fewer `Pantry.md` parses; 30 s is the point where the chip is still trustworthy
without re-parsing a 2 MB-bounded note constantly.

#### F12 — Exactly three meal slots, `breakfast` / `lunch` / `dinner`

**The decision.** `meal_lists.slot` carries a closed `CHECK` constraint with
exactly three values. **There is no fourth slot and no `snack`.** Specified in
**§7.4** and **§9.12**.

**Why now, and not "add it if asked".** Adding a slot to a SQLite `CHECK`
constraint is a table rebuild, and `meal_lists` is written by the outbox with
replays in flight. It is cheap today while the table is empty and the app is
unimplemented; it becomes an availability-affecting migration the moment
shortlist data exists.

#### F13 — `cook_log_receipts` ships, and `CONTEXT.md`'s Cooking Record is amended

**The decision.** Ship `cook_log_receipts` as a **PWA-owned mirror** of the
daily-note row — a local record of what the app wrote, not a domain artefact —
**and amend `CONTEXT.md` in the same change** so the **Cooking Record** entry no
longer promises a Stock Movement this app does not produce. Specified in
**§7.5** and **§5.3**.

**Why the table ships.** Dropping it loses the `待 Obsidian 同步` badge (§13.5)
and the double-submit dedupe ledger (§7.5), both of which are cheap now and
expensive to add after there is data to migrate. `ux_cook_log_receipts_recipe_date`
is what makes a concurrent double-submit a `200 duplicate` rather than a note
with two copies.

**The interaction with D2's invariant, stated because it is the easy thing to
get backwards.** Because `recipeTracker` counts **pages, not links**, the receipts
table is **not** what keeps `cooking_count` correct — **the daily-note wikilink
does that, and nothing else does.** The receipts table is the **audit trail**
("did the app write this?") and the **double-submit dedupe ledger**. It is not
the correctness mechanism, and it must not be described as one, or a future
reader may "optimize" the wikilink write away on the theory that the receipt
row is load-bearing. It is not. §13.6 states this a third time on purpose.

**The `CONTEXT.md` amendment, in substance.** The Cooking Record entry gains: the
row is a **PWA-owned mirror** providing the audit trail and the double-submit
dedupe; there is **no** resulting Stock Movement, because this app records the
cook and does not move stock; and the daily-note wikilink — not the receipts
table — is what keeps `cooking_count` correct, because `recipeTracker` counts
pages rather than links.

#### F14 — The golden test reads a committed snapshot, never a live sibling DB

**The decision.** The golden matcher test's catalog source is a **committed
`tests/fixtures/pantry_items_snapshot.json`**, regenerated by a **deliberate
`scripts/snapshot_pantry_catalog.py` run in its own commit**, never by CI reading
`wholefoods-to-pantry/assets/pantry_items.db`. The **`💵` parity fixture
(`pantry_stock_math_parity.json`) is frozen for the same reason.** Specified in
**§10.3**.

**Why.** Reading the live sibling database makes CI depend on another repo's
on-disk state and turns every catalog re-import into a CI failure — a failure
that is about the *world*, not about this code, and therefore trains everyone to
ignore CI. F1 extends the rule to the money-parity fixture, which additionally
must not be able to refresh itself: the regeneration script is the deliberate,
human-run act at which the three implementations of the `💵` math are compared.

#### F15 — Emoji runs use a hand-built range class over an explicit key set

**The decision.** The emoji dictionary is an **explicit set of literal keys** and
the leading-emoji-run stripper is a **hand-built character-range class** that
includes `U+FE0F` and `U+200D`, matched **longest-key-first**. The `regex`
module is **not** added. Specified in **§9.6** and **§9.7**.

**Why, and why it got stronger while flagged.** `🍋‍🟩` is
`U+1F34B U+200D U+1F7E9` — a **three-codepoint ZWJ sequence** for lime. A Unicode
*property* class matches the **parts**, not the run, so `\p{Extended_Pictographic}`
finds three unrelated emoji and leaves garbage text behind. That is the real
requirement, and it does not go away: even with `regex` installed, **grapheme
clustering** would be the correct mechanism, and the `regex` module is a
dependency none of the siblings use. An explicit key set is therefore the answer
either way — it is not a workaround for the missing `regex`, it is the only form
that can say "this three-codepoint sequence is one key named 青柠". Python's
stdlib `re` additionally has no `\p{...}` escape at all, so the range class has
to be written out by hand regardless.

#### F16 — Under `严格模式` a staple-satisfied Seasoning still counts as found

**The decision.** In `严格模式（含调料）`, a Seasoning resolved by
`staples.yaml` / `class_suffixes` (`match_method='staples'`) still counts as
**found**. **Only an `unresolved` Seasoning counts as missing.** Specified in
**§9.13.1**, and it is exactly the branch already encoded in
`app/static/js/logic/chip-class.js` (§9.13.2): `staples` returns
`assumed: true, missing: false` before the `isSeasoning && !strict` test is
reached.

**The empirical justification, which is the whole decision.** Pantry Category
`1.1c` (condiments) has **exactly 1 row in 178** (id 177, `李锦记 蒸鱼豉油
14 盎司`). If a staple-satisfied Seasoning counted as missing, then under strict
mode **every** recipe loses its condiments and every score collapses toward
zero — the toggle would show the user sixteen near-zero rows and teach them that
strict mode is broken. Strict mode then means "Seasonings I could not match at
all", which is a meaningful pessimistic view; the alternative means "Seasonings
I assumed on hand are now missing", which is not a view of anything.

#### F17 — The headline is `n/total` from `材料`; no `cookable` boolean is published

**The decision.** The headline string is `n/total` computed from **`材料`
(Ingredients) only** in the default view. **No `cookable: true|false` field is
EVER published** in any API response, in any mode. `CONTEXT.md`'s boolean
**Cookable** stays *defined but unrendered* — the term is kept, the field is not
emitted. Specified in **§5.1** and **§9.13.1**.

**The correction, which the user reviewed and accepted.** The D4 example text
(`4/6 ingredients found — missing: 生抽`) lists `生抽` — a **Seasoning** — in a
**Materials-only** missing list. The rule set as specified **cannot produce that
string**: under the default view `调料` is excluded from both numerator and
denominator, and under `严格模式` it is only *added* to the count. The example was
internally inconsistent with its own rules. The spec therefore uses the
internally consistent form —
`2/5 ingredients found — missing: Clam, 香菇   (严格模式（含调料）: 生抽)` —
which is a Materials-only missing list **plus** a separately-labelled strict-mode
addendum, and both halves are producible. `CONTEXT.md` is deliberately **not**
amended to delete Cookable: the concept is real, the app just refuses to reduce
it to a boolean, and §5.1 records that the boolean is never published so a future
ticket does not add it "for consistency".

#### F18 — Variation-matrix picks: `cache-first` shell, network-only APIs, explicit update banner, **no** read-only write allowlist

**The decision.** One pick per §4 dimension, all locked:

| Dimension | Pick |
|---|---|
| §4a version source | Pattern A (`CACHE_VERSION`) — already wired |
| §4b shell strategy | **`FETCH_STRATEGY = 'cache-first'`** |
| §4c API read strategy | **`CACHE_API_GETS = false`** (network-only) |
| §4d update UX | **Explicit banner**: `WAIT_FOR_MESSAGE = true`, `autoApply: false`, `onStale` shows the banner, Reload → `requestUpdateReload()` |
| §4e busy-guard | `canApplyUpdate: () => !mutationInFlight && !datePickerOpen` |
| read-only mode | **No write allowlist.** `OBSIDIAN_READ_ONLY` rejects **every** `/api/*` mutation |

Specified in **§9.2** and **§9.17**.

**Why each non-obvious pick.** An **auto-takeover** update could fire a forced
reload between the user tapping `做过了` and the request landing, losing the log —
so the banner is explicit and the reload is user-initiated, with a busy-guard so
it never lands while a dialog or a form is open. A `network-only` API is not a
performance loss here: match results change the moment the user toggles a
`Pantry.md` line, so caching them is a **correctness** bug, not a perf trade.
**No read-only write allowlist** is the pick F4 hardened: a read-only Pantry
Recipes that could still log a cook or edit a shortlist would be a confusing
half-mode, and *because F4 removed the creation service*, `read_only` is now the
**only** thing standing between a mutation and the vault. That makes the
allowlist question a security question rather than a UX one, and the answer is
that `read_only` must cover **every** write, with no exception list to get stale.

#### F19 — Pull-to-refresh on the recipe list only

**The decision.** Pattern M (§2h) pull-to-refresh is **enabled on the recipe list
view only** — `/css/pull-refresh.css` and `/js/pwa/pull-refresh.js` are linked
and inert until `initPullToRefresh()` is called, with
`canStart: () => navigator.onLine && !hasPendingEdit()`. Not on the shortlist
view, not on the recipe detail view. Specified in **§9.4**; the exclusion is
restated in **§15**.

**Why.** It is a nice-to-have and the **last** thing to build (it rides on P11's
frontend, and the PWA has no other reason to want a manual refresh before then).
Pulling to refresh a *detail* view invites discarding an in-progress state
(§8's "a forced reload cannot eat a half-typed date"), and pulling to refresh a
shortlist while offline edits are pending is exactly the case
`hasPendingEdit()` exists to block.

#### F20 — No pagination on the recipe list

**The decision.** `GET /api/recipes` returns **every** recipe, unbounded. There is
no `limit` / `offset` parameter and no cursor. Specified in **§9.16**; recorded
as a known limitation in **§13.12**.

**Why, including the cost F1 added.** A limit parameter is cheap to add later and
is premature at 16 notes. F1 makes the omission *slightly* more expensive, in two
ways worth recording: the payload grows because every Ingredient slot now carries
a `stockJoinState`, and the **failure blast radius** grows because the recipe
list is the response that fails closed on an unreadable `Pantry.md` (§13.15) —
one unparseable pantry note takes out the whole list rather than one page of it.
Both are accepted. At 16 notes the list is a few kilobytes, and D4's
"low-scoring recipes are never auto-hidden" requirement is itself an argument
against paging: a `0/6` recipe must be reachable, and a cursor that stops before
it has failed at the requirement.

---

## 4. Architecture

```
FastAPI (loopback :8007)  ──  vanilla ES modules  ──  no build step
  reads:  Obsidian vault   (RECIPES_ROOT, DAILY_NOTES_ROOT, Logistics/库存/Pantry.md)
  reads:  wholefoods-to-pantry/assets/pantry_items.db   (READ-ONLY, mode=ro)
  writes: the dated Obsidian daily note  (Cooking Log — the only vault write)
  writes: APP_DATA_DIR/recipes.sqlite3   (PWA-owned: mappings, shortlists, receipts, ledgers)
  serves: app/static/ straight from disk
```

No ORM, no bundler, no build step, no `node_modules` at runtime — the same
constraint as every sibling. **Exactly one new runtime dependency**
(`aiosqlite`, precedent `pwa-wardrobe/app/database.py`, in
`[project].dependencies`) and **one new optional test extra** (`playwright` in a
`[browser]` extra, deliberately **not** in `[test]`). Both locked: **F9** and
**F10**, recorded in §6.1. **F1 and F4 add neither** — F1 reads a vault note
through the already-ported `pantry.py`, and F4 *removes* a dependency-shaped
surface (a subprocess and its nine config fields) rather than adding one.

**Two ports, not one, and never one end-to-end.** The app process binds loopback
**`:8007`**. Tailscale Serve ingress, once deployment is authorized (§12), takes
**`:8452`** — the 2026-09-27 port audit recorded in `.env.example` assigns 8000
and 8002–8006 to sibling PWAs, 8443 and 8445–8451 as allocated, and 8446 to
wardrobe. `PUBLIC_ORIGIN` is `http://127.0.0.1:8007` in development and
`https://home-macbook-air.tailcd6e49.ts.net:8452` in production, and §9.16's
host guard is written against whichever one `PUBLIC_ORIGIN` names. Any diagram,
smoke check, or Playwright base URL that assumes the app is reachable on a
single port is wrong; §12 owns the ingress plan.

### 4.1 Test seams

Three seams, in descending preference.

1. **The API.** `TestClient(create_app(settings))` against a `tmp_path` vault +
   data dir + a real seeded `pantry_items.db`. This is the primary seam and
   already exists in `tests/conftest.py`. The dependency-ordered fixture keeps
   the catalog, the vault, and `APP_DATA_DIR` isolated per test, and
   `create_app(settings)` never reads the real environment.
2. **The pure vocabulary core.** `app/recipes/ingredients.py`,
   `app/recipes/normalize.py`, and `app/recipes/matcher.py` are pure functions
   over `str` with no I/O. They are unit-testable directly, and the golden
   matcher test (§10.3) runs entirely at this seam — no app, no HTTP, no DB.
3. **The browser.** Two Playwright flows only (§10.5): browse→inspect, and
   log-a-cook.

There is deliberately **no** seam between the API layer and the router layer:
the routers are thin and are tested *through* the API, not around it.
`app/static/js/logic/*.js` mirrors seam 2 on the client (pure, `node --test`).

### 4.2 File layout to create

```
app/
  auth.py                      ported from pwa-obsidian-daily/app/auth.py (trimmed)
  config.py                    EXTEND: new typed, validated Settings fields
  main.py                      EXTEND: routers, lifespan, auth install
  api/
    __init__.py
    session.py                 build_session_router()      -> GET  /api/session
    recipes.py                 build_recipes_router()      -> /api/recipes*
    pantry.py                  build_pantry_router()       -> /api/pantry/items
    shortlists.py              build_shortlists_router()   -> /api/shortlists*
    cooklog.py                 build_cook_log_router()     -> /api/cook-logs
  db/
    __init__.py
    schema.sql                 CREATE TABLE IF NOT EXISTS ... (idempotent)
    database.py                apply_pragmas / connect_db / init_db / _migrate_NNN_*
  vault/
    __init__.py
    atomic_write.py            ported (AtomicNoteStore, trimmed to what we use)
    frontmatter.py             ported (loss-minimizing parse + single-field patch)
    sections.py                ported (heading/region engine)
    daily_paths.py             ported (DailyNotePathPolicy)
    pantry.py                  ported (Pantry.md open-items parser — F1)
  pantry/
    __init__.py
    catalog.py                 PantryCatalog — read-only items access
    stock.py                   PantryStockIndex — Pantry Stock from Pantry.md,
                              the catalog<->line join and its override lookup,
                              the per-unit money math (F1)
    line_overrides.yaml        manual escape hatch for lines the join misses
  recipes/
    __init__.py
    reader.py                  RecipeIndex / RecipeNote / parse_recipe
    ingredients.py             parse_ingredient_value + EMOJI_NAMES
    normalize.py               normalize_ingredient + BRAND_LEXICON + size regex
    matcher.py                 resolve_ingredient (the tier ladder)
    lexicon/
      staples.yaml             ~25-row Seasonings allowlist (in scope)
      synonyms.yaml            ~22 closed CJK<->Latin pairs
  mapping/
    __init__.py
    store.py                   IngredientMappingStore (re-resolve, manual override)
  shortlists/
    __init__.py
    store.py                   MealShortlistStore
  cooklog/
    __init__.py
    writer.py                  CookingLogWriter (CAS + dedupe + region)
  static/
    icons/                     + icon-192 / icon-512 / icon-monochrome-512 / icon-180-apple
    js/
      main.js                  EXTEND
      api.js                   NEW
      router.js                NEW
      views/{home,recipe,shortlists,settings,provenance}.js   NEW
      components/{chips,sheet,stepper}.js                    NEW
      logic/{sort,chip-class,format}.js                      NEW (pure, node --test)
docs/spec/2026-09-27-pantry-recipes.md    this file
scripts/snapshot_pantry_catalog.py        NEW: regenerate the golden catalog
                                         snapshot AND verify the per-unit
                                         money math against
                                         Helper/scripts/pantry_snapshot.py,
                                         refusing to emit on disagreement (F1)
tests/
  conftest.py                  EXTEND: recipe fixtures, seeded catalog, app_data
  fixtures/real_recipes/*.md           NEW: the 16 real recipe notes, frozen
  fixtures/pantry_items_snapshot.json   NEW: frozen 178-row catalog
  fixtures/golden_match_results.json    NEW: the expected per-Ingredient outcome
  fixtures/pantry_stock_math_parity.json  NEW: the frozen per-unit money total
                                         and its contributor counts (F1)
  vault/…  pantry/…  recipes/…  mapping/…  shortlists/…  cooklog/…  db/…
  api/…                     NEW
  js/scaffold.test.mjs         EXTEND
  js/shell_assets.test.mjs     NEW: closes the "missing SHELL_ASSETS entry" hole
  js/logic/*.test.mjs          NEW
  browser/…                    NEW: the two Playwright flows
```

---

## 5. Domain vocabulary

### 5.1 Bound terms (from `CONTEXT.md`, used exactly)

Pantry Item, Pantry Item Alias, Pantry Category, Pantry Stock, Pantry Unit,
Recipe, Ingredient, Seasoning (调料), Cooking Tool, Cookable,
Ingredient Mapping, Cooking Log, Cooking Record, Stock Movement,
Recipe Cooking History, Meal Shortlist, Server-Owned Root, Pantry-Write
Contract, Sync Conflict.

`Cookable` as `CONTEXT.md` defines it is a *boolean*. **This app does not render
it, and no response ever publishes it.** D4 forbids a single boolean, so the app
renders the *evidence* the boolean would have been computed from (the chip row and
the `n/total` line) and never publishes a `cookable: true|false` field in any API
response, in either mode. Locked as **F17**; the user reviewed and accepted this
and explicitly did **not** want `CONTEXT.md`'s Cookable entry deleted, because the
concept is real even though this app refuses to reduce it to a bit. The
consistency check F17 required therefore came back clean: `CONTEXT.md`'s Cookable
entry needs no amendment, and §5.1 is the note that says so.

### 5.2 Vocabulary discipline inside the matcher

- A resolved name is a **Pantry Item Alias** resolution, not "a match". The
  mapping table stores the resolution outcome; `CONTEXT.md` already says alias
  resolution "is a read concern only; it never writes back to the catalog".
- `category` is a **Pantry Category code** (`1.1`, `1.1c`, `4`). The matcher uses
  it only as a *food-class guard* and never renders it as a label — the
  code-to-display-text mapping does not exist and must not be inferred from the
  code.
- `area` (values like `Shampoo`, `Serum`) is a **non-food area** in a separate
  column. Any catalog row with a non-NULL `area` is removed from the candidate
  universe before matching starts.

### 5.3 New terms — requires a `CONTEXT.md` amendment

`AGENTS.md` requires a new domain term to land in `CONTEXT.md`, with its
forbidden synonyms, **in the same commit that introduces the code that
implements it**. Three new terms:

| Term | Definition | Forbidden synonyms | `CONTEXT.md` status |
|---|---|---|---|
| **Ingredient Mapping** | The persisted, auditable resolution of one Ingredient slot to **zero or one** Pantry Item — zero is the `unresolved` state, not a partial one. Carries `raw_value`, `parsed_name`, `parse_method`, `match_method`, `match_tier`, `confidence`, and `candidates_json` (the rejected candidates). Lives only in the PWA's SQLite (`ingredient_mappings`, `APP_DATA_DIR`); the vault has no equivalent. `pantry_item_id` is **not unique** — one SKU is legitimately claimable by many Ingredients across many recipes (`空心菜` → id 83 in three notes) — it carries **no** foreign key because the catalog is a separate read-only database, and a `match_method='manual'` row is **immutable** (a `BEFORE UPDATE` trigger aborts, so a hand fix is delete-then-insert). A persisted wrong mapping is permanent and invisible, which is why the term also carries provenance and a re-resolve action. | match, mapping, link, resolution, join | **landed** — `app/db/schema.sql`, `app/mapping/store.py`; the glossary entry is in `CONTEXT.md` |
| **Meal Shortlist** | One of three PWA-owned, user-ordered lists of Recipe notes for `breakfast` / `lunch` / `dinner`. A planning convenience. **Not** a vault record, not synced back, not a classification of the Recipe, and not a grouping of Cooking Records. The slots are a **closed** set with no `snack`; it is PWA-owned state that may drift from the vault and is never written back; **no** meal frontmatter field exists on a Recipe; an empty slot is a normal empty state, not an error. | meal plan, menu, category, tag, meal type | **landed** — `app/shortlists/store.py`, `app/api/shortlists.py`; the glossary entry is in `CONTEXT.md` |
| **Stock Join** | The resolved correspondence between one task line in `Logistics/库存/Pantry.md` and at most one [Pantry Item](#pantry-item), keyed on a **normalized name** and repaired by a committed manual override. Computed per read; **never stored as a Pantry Item id**. Its own unresolved bucket is surfaced to the user. F1 states the measured miss rate. | stock match, pantry join, line match, availability join, restock key | **specified and recorded** — see below |

**The two required clause amendments, and their exact scope.** Both are locked
(F1, F13) and both are recorded here so an implementer does not re-derive them:

1. **Pantry Stock** gains a clause: state is read from **two** sources —
   `pantry_items.db` for **identity, brand and price**, and
   `Logistics/库存/Pantry.md` for **state**. The existing definition says stock is
   "per-household, mutable, and has no catalog identity", which remains true, but
   a reader would otherwise reasonably assume the *catalog* is the other source,
   and F1's whole point is that it is not sufficient. The `💵` effective-per-unit
   invariant and the per-unit parent exclusion are retained, because the
   implementation now depends on both (F1, §9.11.3).
2. **Cooking Record** is amended so it no longer promises a **Stock Movement**
   this app does not produce. The entry states that `cook_log_receipts` is a
   PWA-owned mirror supplying the audit trail and the double-submit dedupe, and —
   critically — that the **daily-note wikilink, not the receipts table**, is what
   keeps `cooking_count` correct, because `recipeTracker` counts pages rather
   than links (F13, D2).

**`CONTEXT.md` is amended with the spec, not after the code.** `AGENTS.md`'s
same-commit rule is a *code* rule, and this commit is the spec amendment the
decisions themselves required (F1, F13) — the glossary is a contract, and the
`CONTEXT.md` banner already says so ("the terms below are contracts, not
descriptions of working code"). Ingredient Mapping and Meal Shortlist were held
**pending** here until their implementations existed, because nothing about their
wording depended on a product decision and adding them ahead of their code would
have been gratuitous.

**Both have now landed, and this is the reconciliation.** P7 (`61d4d2f`) and P9
(`2fcb7d4`) shipped their code, and both glossary entries are in `CONTEXT.md` as
of this amendment. `AGENTS.md`'s same-commit rule was **not** satisfied for
either: the implementation agents were instructed not to edit `CONTEXT.md`, which
overrode the rule for this one file, so the two entries are recorded here with
their code. That is a process miss, not a design change, and the shipped
behaviour is what the entries above now describe.

**Three places where the shipped code refined the wording proposed above, and the
shipped behaviour won:**

1. **"at most one Pantry Item" → "zero or one".** `set_manual` and the ladder
   both leave `pantry_item_id` `NULL` for an `unresolved` slot. That is a state,
   not a partial one, and calling it "at most one" reads as if a half-bound
   mapping were possible.
2. **The field list names the real columns.** `tier` is `match_tier` and the
   rejected candidates are `candidates_json` (`app/db/schema.sql`).
3. **Three facts are stated that the original proposal omitted**, because the
   implementation made them load-bearing: `pantry_item_id` is **not** unique and
   the mapping is **not** one-to-one (`空心菜` → 83 in three recipes is the
   measured case); there is **no** foreign key, because the catalog is a separate
   read-only database and integrity is handled by `stale_rows()` re-resolution
   instead; and `manual` rows are **immutable** under a `BEFORE UPDATE` trigger,
   so a hand fix is delete-then-insert. The last is also the reason a persisted
   wrong mapping is *permanent and invisible* — nothing re-resolves it — which is
   what makes the provenance fields and the re-resolve action part of the term
   rather than a convenience.

The Meal Shortlist wording needed no such reconciliation: the slots are closed
with no `snack` (a `CHECK` on `meal_lists.slot`), the state is PWA-owned and
never written back, no meal field exists on any of the 16 recipe notes, an empty
slot renders as a normal empty state, and cooking history is not grouped by
meal — all exactly as proposed.

`cook_log_receipts` (§7.5) is a PWA-owned mirror of the daily-note row, not a
new domain concept, so it needs no `CONTEXT.md` **entry** — but it is the reason
amendment (2) above exists, and §13.11 is the limitation it resolves.

---

## 6. Configuration surface

Everything below is **Server-Owned** (`CONTEXT.md`): read from the process
environment at startup by `Settings.from_mapping`, validated there, and
unreachable from any request. There is no request field that reaches `Settings`.

New `Settings` fields, each with a default, a validator, and a `.env.example`
entry — the rule from `CLAUDE.md` § *Traps* ("a new `Settings` field needs a
default, a validator, and a `README`/`.env.example` entry, and it belongs to the
server-owned side. Validate it in `from_mapping` so it fails closed; do not
validate it in a route"):

| Field | Env var | Default | Validation | Why |
|---|---|---|---|---|
| `pantry_note_relative` | `PANTRY_NOTE_RELATIVE` | `Logistics/库存/Pantry.md` | same shape as `_safe_relative_root`, extended to a file path | The Pantry **state** source (F1) |
| `read_only` | `OBSIDIAN_READ_ONLY` | `false` | `_boolean` | Read-only gate in `auth.py` (**F18**: covers **every** mutation — there is no write allowlist) |
| `catalog_cache_seconds` | `CATALOG_CACHE_SECONDS` | `300.0` | `0 < v <= 3600` | TTL for the read-only catalog snapshot |
| `stock_cache_seconds` | `STOCK_CACHE_SECONDS` | `30.0` | `0 < v <= 600` | TTL for the `Pantry.md` Pantry Stock snapshot. Short on purpose: the user toggles stock from Obsidian while the PWA is open, so this is the TTL that decides how stale a chip can be. |
| `recipe_cache_seconds` | `RECIPE_CACHE_SECONDS` | `60.0` | `0 < v <= 3600` | TTL for the recipe index |
| `max_recipe_bytes` | `MAX_RECIPE_BYTES` | `2_000_000` | `1024 <= v <= 20_000_000` | Matches the ported `pantry.py`'s `max_bytes` default, and bounds the `Pantry.md` read the same way |
| `daily_notes_year_policy` | `DAILY_NOTES_YEAR_POLICY` | `None` | `YYYY-YYYY` or `None` | Bounds the year a request may name, so `DailyNotePathPolicy` cannot be steered outside the vault's own layout |

**Seven new `Settings` fields, down from sixteen.** The nine fields the previous
draft added for automatic daily-note creation — `cli_executable`,
`cli_vault_id`, `daily_template_name`, `daily_quickadd_choice`,
`cli_timeout_seconds`, `cli_settle_seconds`, `cli_max_output_bytes`,
`cli_home`, and `daily_required_markers` — are **removed by F4** and must not
appear in `config.py`, `.env.example`, or the test fixtures. There is no
`OBSIDIAN_CLI_EXECUTABLE`, no `OBSIDIAN_CLI_VAULT_ID`, no `DAILY_TEMPLATE_NAME`,
no `DAILY_QUICKADD_CHOICE`, no `OBSIDIAN_CLI_*` timeout/output/home variable,
and no `DAILY_REQUIRED_MARKERS` anywhere in this repository. `config.py` gains
six fields plus the retained year policy; a new-field test asserts the exact
set, so a reintroduced CLI field is a CI failure rather than a silent
regression.

Because `OBSIDIAN_READ_ONLY` is the only gate between a mutation and the vault,
and because F4 removes the only write path that had an availability fallback,
the two interact simply: in read-only mode `POST /api/cook-logs` is 403
`read_only` **before** any note-existence check, so a read-only deployment
cannot leak whether a given daily note exists through a status-code difference.

`.env.example` needs **no** change to its `DAILY_NOTES_ROOT` comment. It
currently states "the PWA does not create missing daily notes", F4 confirms
that as the rule rather than changing it — the flag is what previously proposed
narrowing it to "the only creation path is the configured Obsidian CLI /
QuickAdd invocation", and that proposal is withdrawn. `AGENTS.md`
non-negotiable #1, `README.md`, and `CONTEXT.md`'s Cooking Log definition all
remain accurate as written. The `.env.example` comment that *does* need adding
is the port pair: `8007` is the app bind and `8452` is Tailscale Serve ingress,
and the two are not the same number (§4, §12).

The three cache TTLs above are **locked as the values shown** (F11): catalog
**300 s**, stock **30 s**, recipes **60 s**. Stock's 30 s is the one that is
load-bearing rather than conventional — it is *the maximum staleness a chip
colour can have*, because the user toggles tasks in Obsidian while the PWA is
open (§9.11.3, §13.5). Raising it trades chip freshness for fewer `Pantry.md`
parses; 30 s is the point where the chip is still trustworthy.

### 6.1 Dependency placement (F9, F10)

Both dependency decisions are locked, and **the placement is part of the
decision** — a dependency in the wrong group changes what CI installs.

| Dependency | Group | Locked decision | Why that group |
|---|---|---|---|
| `aiosqlite` | `[project].dependencies` — **runtime** | **F9** | §7.1's `apply_pragmas()` / `connect_db()` / `_migrate_NNN_*` idiom is copied from `pwa-wardrobe/app/database.py`. Re-expressing it over `asyncio.to_thread` would port the code without porting the idiom. |
| `playwright` | `[project.optional-dependencies] browser = [...]` — **optional extra, NOT in `[test]`** | **F10** | CI's `pip install ".[test,dev]"` stays **unchanged** and the two browser flows stay opt-in. They need a browser binary on the runner, which is a recurring CI cost for two flows — and the failure that omitting them would cause (§2i back-button, stale-paint false passes) is **silent**. |

```toml
# pyproject.toml — the exact shape both decisions require
[project]
dependencies = [
    # … the scaffold's existing runtime deps …
    "aiosqlite>=0.20",          # F9: runtime
]

[project.optional-dependencies]
# `test` and `dev` are UNCHANGED by this spec.
browser = ["playwright>=1.44"] # F10: opt-in, deliberately NOT in `test`
```

**Two consequences a reviewer must be able to check.** (1) `playwright` must
**not** appear in `[test]` or in `[dev]`; a `grep playwright pyproject.toml` must
show exactly one occurrence, inside the `browser` extra, or F10 has been undone.
(2) `aiosqlite` must be a **runtime** dependency, not a test one — a deployment
that installs only the wheel would otherwise fail at first request, in
production, on the vault write path. `tests/scaffold/test_app.py` (§10.2) asserts
the dependency set.

**Nothing else is added.** No ORM, no `regex` (F15), no bundler, no
`node_modules` at runtime, and no dependency on `nutrition-intake`. F1 and F4 add
neither — F1 reads a vault note through the already-ported `pantry.py`, and F4
*removes* a dependency-shaped surface (a subprocess and its nine config fields).

---

## 7. Data model — PWA-owned SQLite

`APP_DATA_DIR/recipes.sqlite3`. Not in the vault — the same
`_path_inside_vault` posture already enforced for `PANTRY_ITEMS_DB` applies,
since `APP_DATA_DIR` is required to be outside the vault by
`Settings._required_directory` plus the operator's installer discipline.

### 7.1 Ledger + migrations (from `pwa-wardrobe`)

`app/db/schema.sql` is executed with `executescript` at startup; every statement
is `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`, so the file is
idempotent and safe to re-run. Versioned changes are numbered
`_migrate_NNN_*` coroutines appended to `init_db()` in call order, each guarded
by `SELECT 1 FROM schema_migrations WHERE version = 'NNN_name'` and ending with
`INSERT OR IGNORE INTO schema_migrations (version) VALUES ('NNN_name')` plus
`conn.commit()`. This is the `pwa-wardrobe/app/database.py` shape exactly
(`init_db` → `executescript` → `_migrate_002…` → `_migrate_015…`).

`apply_pragmas()` runs on **every** connection, verbatim from wardrobe:

```
PRAGMA journal_mode=WAL
PRAGMA synchronous=NORMAL
PRAGMA foreign_keys=ON
PRAGMA busy_timeout=5000
PRAGMA cache_size=-64000
PRAGMA temp_store=MEMORY
```

`connect_db()` is an `@asynccontextmanager` yielding an `aiosqlite.Connection`
with `row_factory = aiosqlite.Row`, and closes in a `finally`.

### 7.2 `schema_migrations`

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
```

### 7.3 `ingredient_mappings` — the materialized mapping (D1)

```sql
CREATE TABLE IF NOT EXISTS ingredient_mappings (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_note      TEXT    NOT NULL,   -- vault-relative: 'Hobbies/做饭/Recipes/盐焗鸡.md'
    ingredient_index INTEGER NOT NULL,   -- 0-based index within the 材料 list
    raw_value        TEXT    NOT NULL,   -- the frontmatter string, verbatim
    parsed_name      TEXT,               -- NULL when unparseable
    parse_method     TEXT    NOT NULL,   -- §9.6 enum
    match_method     TEXT    NOT NULL,   -- §9.8 enum
    match_tier       INTEGER NOT NULL,   -- 1..8, or 0 for 'manual'
    pantry_item_id   INTEGER,            -- cross-database; NO foreign key
    confidence       REAL    NOT NULL,   -- 0.0 .. 1.0
    candidates_json  TEXT    NOT NULL DEFAULT '[]',  -- rejected candidates, for audit
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ingredient_mappings_slot
    ON ingredient_mappings(recipe_note, ingredient_index);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ingredient_mappings_recipe_item
    ON ingredient_mappings(recipe_note, pantry_item_id);
CREATE INDEX IF NOT EXISTS ix_ingredient_mappings_item
    ON ingredient_mappings(pantry_item_id);
CREATE INDEX IF NOT EXISTS ix_ingredient_mappings_unresolved
    ON ingredient_mappings(match_method) WHERE match_method = 'unresolved';
```

Two constraints, and the difference between them is the whole point:

- `ux_ingredient_mappings_slot` — the **slot identity**. One row per Ingredient
  position, so re-resolving updates in place and the row is stable across
  resolution passes.
- `ux_ingredient_mappings_recipe_item` — the inverted constraint the user called
  out. `pantry_item_id` **alone is not unique**: one SKU must be claimable by
  many recipe Ingredients, and `空心菜` (id 83) is a 材料 in 3 recipes, so it
  must produce 3 rows across 3 different `recipe_note` values. Scoping the
  uniqueness to `(recipe_note, pantry_item_id)` permits that while still catching
  a real data defect — one recipe listing the same Pantry Item twice.

  **Do not** copy `nutrition-intake`'s pattern of a bare `UNIQUE` on the
  Pantry-Item column. In SQLite a `UNIQUE` on a nullable column permits unlimited
  `NULL` rows but only **one** row per non-null id, which would make
  `空心菜 → 83` in `拌空心菜` and `煮菜菜` mutually exclusive and would silently
  drop a resolution. This is flagged as a first-class requirement, not a
  footnote, because the failure is silent.

- `pantry_item_id` carries **no** `REFERENCES` clause. `pantry_items.db` is a
  different database opened read-only; SQLite cannot express a cross-database
  foreign key, and adding a same-named table here purely to hang a foreign key
  off would create a second, writable copy of catalog truth — which `AGENTS.md`
  #4 forbids in spirit and D1 forbids in fact. Referential integrity is
  instead handled by re-resolution: a mapping whose `pantry_item_id` no longer
  exists in the live catalog is detected, surfaced via `staleMappingCount` in
  `/api/recipes`, and reset to `unresolved`.

**Immutability of `manual`.** A `BEFORE UPDATE` trigger on `ingredient_mappings`
raises `ABORT` when `OLD.match_method = 'manual'`. To change a hand-fixed mapping
the user *deletes* the row and inserts a fresh `manual` one via the re-map
action, so a change is an auditable delete + create rather than a silent
overwrite. `resolve_all()` skips `manual` rows entirely. Locked as **F6**.

**The per-slot `IntegrityError` catch on the inverted index (F2, locked — and
the user overrode the original proposal here).** `ux_ingredient_mappings_recipe_item`
is kept exactly as written above, and `resolve_all()` additionally **catches
`sqlite3.IntegrityError` around each slot's write**:

```python
# Decision-dense: the catch is INSIDE the transaction and scoped to ONE
# statement. It never rolls back the enclosing transaction — that is the whole
# point of the override (F2).
try:
    await conn.execute(UPDATE_SLOT, row)          # one slot
except sqlite3.IntegrityError as exc:
    # ONLY this slot is downgraded. The candidate that collided is recorded
    # so the dropped resolution is auditable, not invisible.
    await conn.execute(
        UPDATE_SLOT_CONFLICT,
        (json.dumps([{"pantryItemId": row["pantry_item_id"],
                      "canonicalName": row["canonical_name"],
                      "reason": "duplicate_slot_conflict"}]),
         "unresolved", 0, None, 0.0, row["recipe_note"], row["ingredient_index"]),
    )
    report.duplicateSlotConflicts += 1            # continue with the next slot
```

The exact semantics, and the exact relationship to the enclosing
`BEGIN IMMEDIATE` transaction, are specified in **§9.10.1**. The three parts a
reviewer must be able to confirm:

- **The index stays.** It is the constraint that catches "one recipe lists the
  same Pantry Item twice".
- **The catch is per slot, not per batch.** A duplicate in slot 7 must not
  prevent slots 8–31 from resolving.
- **The transaction does not abort.** The recovery `UPDATE` runs **inside the
  same still-open transaction** and clears the violation, so it commits with
  everything else.

**The failure mode F2 guards is a *silently* dropped resolution — the worst bug
class in this app** — and it is the same failure the bare-`UNIQUE` warning above
describes. That is why the composite key is a first-class requirement rather
than a footnote: a bare `UNIQUE` would produce exactly the silent drop, and the
per-slot catch produces a *visible* one. `nutrition-intake`'s pattern is still
not to be copied, and the full reasoning is in the `ux_ingredient_mappings_recipe_item`
bullet above.

**The constraint is defensive today, and the evidence is recorded in §3's F2
entry.** The 32 `材料` slots across the 16 real recipe notes are listed there in
full; `花蛤拌饭` (4 slots) and `微波菜菜` (3 slots) are the two closest calls and
both stay distinct. No current recipe has two slots on one `pantry_item_id`, and
every repeated `材料` value in the vault repeats across *different*
`recipe_note` values. A future implementer must therefore read F2 as a
data-integrity guard against a future user-authored note, **not** as a
load-bearing mechanism, and must not "optimize" it away because no current
recipe trips it.

There is deliberately **no** stock column on this table. Stock is volatile — the
user toggles a task in `Pantry.md` while the PWA is open — so persisting it would
make the chip colour wrong. Stock is derived per response (§9.11.3), and the DB
holds no denormalized state that can go stale.

### 7.4 `meal_lists` (D3)

```sql
CREATE TABLE IF NOT EXISTS meal_lists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slot        TEXT    NOT NULL CHECK (slot IN ('breakfast','lunch','dinner')),
    position    INTEGER NOT NULL,
    recipe_note TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_meal_lists_slot_recipe
    ON meal_lists(slot, recipe_note);
CREATE INDEX IF NOT EXISTS ix_meal_lists_slot_position
    ON meal_lists(slot, position);
```

`slot` is a closed enum of **exactly three values** — there is no fourth slot and
no `snack`. Locked as **F12**. `recipe_note` is the **basename** (`盐焗鸡`), not
a path: a request contributes *which recipe*, never a path.

**Why the enum is closed at three and why it is cheap to close it now.** Adding a
slot to a SQLite `CHECK` constraint is a **table rebuild**, and this table is
written by the outbox with replays in flight. It is cheap while the table is
empty and the app is unimplemented; it becomes an availability-affecting
migration the moment shortlist data exists. The three slots also answer the
question the user actually has — "what do I usually eat for lunch" — and a
fourth would only re-open D3's closed question of what a slot *means*.

`UNIQUE (slot, recipe_note)` makes "add" naturally idempotent. Reorder is a
single `BEGIN IMMEDIATE` transaction that rewrites `position` for the affected
slot (bounded to ≤ 50 rows) — SQLite has no deferred unique constraint, so a
`DELETE`-then-`INSERT` shuffle would transiently violate
`ux_meal_lists_slot_recipe` under concurrency, while an in-transaction `UPDATE`
of `position` does not.

### 7.5 `cook_log_receipts` — the local Cooking Record mirror

```sql
CREATE TABLE IF NOT EXISTS cook_log_receipts (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_note              TEXT    NOT NULL,
    log_date                 TEXT    NOT NULL,   -- 'YYYY-MM-DD'
    relative_path            TEXT    NOT NULL,   -- '日记/2026/2026-09-27.md'
    note_revision            TEXT    NOT NULL,   -- 'sha256:…' of the committed note
    recipe_tracker_synced    INTEGER NOT NULL DEFAULT 0,
    written_at               TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cook_log_receipts_recipe_date
    ON cook_log_receipts(recipe_note, log_date);
```

The unique index is the **dedupe ledger** (§9.14): a second log of the same
recipe on the same date is a no-op write, so even a concurrent double-submit
cannot touch the note twice. `recipe_tracker_synced` is the observable for the
known lag recorded in §13.

**This table ships, and it is a PWA-owned mirror — not the correctness mechanism.
Locked as F13.** The single most important thing to get right about it:

> Because `recipeTracker` counts **pages, not links**
> (`cooking.length = dv.pages('"日记"').where(...)`), **the daily-note wikilink
> is what keeps `cooking_count` correct. `cook_log_receipts` does not, and
> cannot.** The receipts table is the **audit trail** — it is what makes "did
> the app write this?" answerable — and the **double-submit dedupe ledger**.
> Neither is redundant, and neither may be described as the correctness
> mechanism, or a future reader may treat the receipt row as load-bearing and
> "simplify" the wikilink write away. Restated as a numbered invariant in §13.6
> and in §3's F3, on purpose.

`CONTEXT.md`'s **Cooking Record** entry is amended in the same change (F13, §5.3)
so it no longer promises a resulting Stock Movement this app does not produce:
the row is a PWA-owned mirror supplying the audit trail and the dedupe, the
consumption itself is not recorded, and the daily note is the record of the cook.

### 7.6 `shortlist_intents` — exactly-once offline replay (D3, Part 6)

```sql
CREATE TABLE IF NOT EXISTS shortlist_intents (
    client_id           TEXT PRIMARY KEY,
    request_fingerprint TEXT NOT NULL,
    response_json       TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
```

The `pwa-deals` `item_status_intents` shape plus a `request_fingerprint`
(§9.18.3). Created by the numbered migration `_migrate_001_shortlist_intents`
*and* lazily inside the mutation handler, exactly as deals does
(`ensure_status_intent_table`), so a direct ASGI test client and a rolling deploy
are safe while the startup migration stays authoritative.

---

## 8. User stories

Numbered and exhaustive. Each names the actor, the capability, and the benefit.

### Browsing and matching

1. As a home cook, I want the app to open to a list of my Recipes sorted by how
   well they match what I have, so that the best option is first with no sorting
   effort from me.
2. As a home cook, I want each Recipe row to show `4/6 ingredients found`, so
   that I judge it by evidence rather than by a green tick I cannot audit.
3. As a home cook, I want the missing Ingredient names spelled out on the row, so
   that I know what to buy without opening the recipe.
4. As a home cook, I want each Ingredient rendered as a coloured chip, so that I
   can scan the state of a recipe at a glance.
5. As a home cook, I want a chip that is *bought-before but not currently in
   stock* to look different from one that *is* in stock, so that the app does
   not lie about what is in my kitchen. The distinction is read from the live
   `Pantry.md`, not inferred from the catalog (F1) — so a cancelled or finished
   item stops counting as held the moment I tick it in Obsidian.
6. As a home cook, I want Seasonings shown as an assumed-on-hand chip row, so
   that owning a teaspoon of soy sauce does not require a grocery trip.
7. As a home cook, I want a `严格模式（含调料）` toggle, so that I can see the
   pessimistic score when I care about every jar.
8. As a home cook, I want a `0/6 ingredients found` Recipe to stay in the list,
   so that it can seed my shopping instead of being hidden from me.
9. As a home cook, I want Recipes tied on score ordered by `last_cooked`, so
   that the dish I made most recently floats up.
10. As a home cook, I want the Recipe's Cooking Tools listed, so that I notice I
    need the air fryer before I start.
11. As a home cook, I want to open a Recipe to read its steps, so that the PWA is
    usable for cooking and not only for deciding.
12. As a home cook, I want the Recipe's Cooking History shown as its frontmatter
    values, so that I can see `cooking_count` and `last_cooked` while deciding.
13. As a home cook, I want a visible "待 Obsidian 同步" badge when the frontmatter
    I am reading is older than a cook I just logged, so that I understand a
    stale number instead of distrusting the app.
14. As a home cook, I want an empty recipe list to render as an invitation, not
    an error, so that a new vault does not look broken.

### Provenance and repair (D4)

15. As a skeptical home cook, I want a `调试` toggle, so that I can see why a
    chip says what it says — including, separately, when an open `Pantry.md` line
    maps to no catalog row at all, so that a pantry item silently missing from
    every score is visible instead of invisible (F1's Stock Join unresolved
    bucket).
16. As a skeptical home cook, I want the resolution tier behind every chip, so
    that I can tell an exact hit from a synonym guess.
17. As a skeptical home cook, I want the candidate Pantry Item id(s) behind every
    chip, so that I can open the catalog row and check it myself.
18. As a skeptical home cook, I want the verbatim `raw_value` behind every chip,
    so that I can see exactly what the vault said.
19. As a home cook whose match is wrong, I want to point a chip at a different
    Pantry Item, so that the app stops being wrong for me.
20. As a home cook who fixed a match by hand, I want that fix to survive every
    later re-resolution and every restart, so that the app does not undo my work.
21. As a home cook who fixed a match by hand, I want the fixed chip to look
    different from a machine match, so that I can still see where my judgement
    ended and the algorithm's began.
22. As a home cook, I want a "re-resolve the unresolved ones" action, so that a
    catalog refresh can fix what was previously unknown without me redoing the
    ones I already fixed.

### Cooking Log (D2)

23. As a home cook, I want a single "做过了" action on a Recipe, so that logging
    a cook is one tap from deciding to cook it.
24. As a home cook, I want the log date to default to today, so that the common
    case needs no interaction.
25. As a home cook, I want to back-date a cook, so that I can log last night's
    dinner this morning.
26. As a home cook, I want my Cooking Log appended to the right daily note, so
    that my vault stays organised by date.
27. As a home cook, I want the append to be a surgical byte patch, so that my
    daily note's frontmatter, embeds, and Tasks-plugin blocks are not rewritten
    by my phone.
28. As a home cook, I want the write to fail loudly rather than silently if my
    daily note changed in Obsidian while I was looking, so that I never lose a
    meal record.
29. As a home cook, I want a second log of the same Recipe on the same day to be
    a no-op, so that a double tap does not litter my note.
30. As a home cook, I want a read-back of what the app wrote, so that I can
    confirm the link landed.
31. As a home cook, I want a clear, actionable error naming the date and the
    exact vault path when that day's daily note does not exist, so that I know
    precisely what to create and do not have to guess which note is missing.
32. As a home cook, I want to retry that same log unchanged once I have created
    the note, so that a missing note is a one-step detour and not lost work.
33. As a home cook, I want the app to never create or write a daily note itself,
    so that Obsidian's template and plugin pipeline stays the only creator and
    my vault's structure stays mine.
34. As a home cook, I want a 409 conflict surfaced with both versions, so that I
    can choose rather than watch a silent overwrite.

### Meal Shortlists (D3)

35. As a home cook, I want a breakfast, a lunch, and a dinner shortlist, so that
    I can answer "what do I usually eat at lunch" in one tap.
36. As a home cook, I want to add a Recipe to a shortlist from the Recipe view,
    so that curating costs one tap.
37. As a home cook, I want to reorder a shortlist, so that my most-used dish is
    first.
38. As a home cook, I want to remove a Recipe from a shortlist, so that a dish I
    no longer eat stops being suggested.
39. As a home cook, I want an empty shortlist to invite me to add something, so
    that it does not look like an error.
40. As a home cook, I want my shortlists to survive a reinstall, so that I do not
    have to rebuild them.
41. As a home cook, I want to know my shortlists are not in the vault, so that I
    do not go looking for them in Obsidian.
42. As a home cook, I want my Cooking History **not** sorted into meals, so that
    "when did I last cook this" stays one question with one answer.

### Platform, offline, and security

43. As a home cook, I want to install the app to my iPhone Home Screen, so that
    it launches full-screen with its own icon.
44. As a home cook, I want the app to work on the train, so that I can browse my
    recipes offline.
45. As a home cook, I want to edit my shortlists offline, so that a tunnel does
    not block me.
46. As a home cook, I want a pending-edit indicator, so that I know the app has
    not yet confirmed my change.
47. As a home cook, I want the cook-log button to say clearly that it needs a
    connection, so that I am never told a cook was logged when it was not.
48. As a home cook, I want the app never to lose my input to a background
    update, so that a forced reload cannot eat a half-typed date.
49. As a home cook, I want a Back button that returns me where I came from, so
    that a deep-linked Recipe does not dump me on the list.
50. As a home cook, I want the app to tell me when the vault path is
    misconfigured, so that an empty screen is never a silent misconfiguration.
51. As a home cook, I want the app to be unusable from another person's device,
    so that my vault is not readable off my tailnet.
52. As a home cook, I want my daily note's Tasks-plugin lines and date markers to
    survive my cook log, so that my task history is not corrupted.
53. As a home cook, I want a slow backend to show "Connecting…" rather than
    looking frozen, so that I know to wait.
54. As a home cook, I want a control that is saving to re-enable itself if the
    request hangs, so that the app never locks up permanently.

---

## 9. Implementation Decisions

### 9.1 PWA shell, install, and icons

**Viewport — template §2a Option A.** Full-bleed interactive PWA: scrollable
list views, content flows past the notch, the app draws edge-to-edge and owns
the safe areas. The scaffold's `index.html` already declares
`width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no,
viewport-fit=cover`. **Keep it.** `viewport-fit=cover` is mandatory — without it
`env(safe-area-inset-*)` silently returns 0 everywhere, which is the trap
obsidian-daily fell into.

**Height.** `app/static/css/styles.css` already has the correct pair,
`min-height: 100vh;` followed by `min-height: 100dvh;` (fallback first, then
`dvh`). Keep it. This app is Option A, not Option B, so `100vh` is **not** an
intentional fixed-short-screen choice and the §2a Option B carve-out does not
apply.

**Overflow baseline — template §2b.** The vendored `css/pwa.css` provides
`html { overflow: visible; }`, `body { overflow-x: hidden; }`, `.scroll-row`
(nowrap + `min-width: 0`), and a `.grid` using `minmax(0, 1fr)`. Note the
vendored rule targets `#app`, while the shell's content root is **`#app-root`** —
so the vendored rule **does not apply to this app's content root at all**, and
`styles.css` must add `#app-root { overflow-x: hidden; }`; it creates a BFC and
contains children, which `overflow-x: clip` does not. The mismatch is a real
scaffold bug, not a style preference: without the compensating rule the chip row
can widen the page, and the symptom (horizontal page scroll) appears only on a
device, never in a desktop browser or an un-emulated Playwright run. The chip row
is the only horizontal scroller in the app and uses the vendored `.scroll-row`
class (§2c: a scrollable row must opt out of intrinsic width with
`min-width: 0`, or it expands the page width).

**Grid columns** (§2d): any multi-column Recipe grid uses `minmax(0, 1fr)`, never
bare `1fr`, plus `.grid > * { min-width: 0; }` and the base rule before any
`@media` override.

**iOS Home Screen.** The three `apple-*` meta tags
(`apple-mobile-web-app-capable`, `mobile-web-app-capable`,
`apple-mobile-web-app-status-bar-style=black-translucent`) and the
`apple-touch-icon` link are already present and correct. **What is missing is the
files.** All four PNGs are **confirmed absent**: `app/static/icons/` holds only a
placeholder README (`.gitkeep` is intentionally absent so the note is visible),
and the four are referenced from **two** places — `app/static/manifest.webmanifest`
references **three** of them (`icon-192`, `icon-512`, `icon-monochrome-512`) and
`index.html`'s `apple-touch-icon` link references the **fourth**
(`icon-180-apple`). iOS falls back to a page screenshot for the Home Screen icon
and Chrome logs a manifest warning. This is a real gap, and this spec closes it:

- `app/static/icons/icon-192.png` — 192×192, `purpose: any`
- `app/static/icons/icon-512.png` — 512×512, `purpose: any maskable`
- `app/static/icons/icon-monochrome-512.png` — 512×512, `purpose: monochrome`
- `app/static/icons/icon-180-apple.png` — 180×180, the real `apple-touch-icon`

Siblings commit their generated binaries to git; do the same, and rewrite
`app/static/icons/README.md` to become the regeneration note rather than the
"missing" table. `pyproject.toml`'s `package-data` already globs
`static/icons/*`, so packaging picks the files up once they exist — but **the CI
wheel-content check does not assert them**, which is why four empty references
have survived this long. Adding the four to the wheel check is part of closing
the gap, not an optional extra: without it, a `.png` that fails to land in the
built wheel produces an installed app with no icon and no test failure.

`display: standalone` is already in the manifest and is asserted by
`tests/scaffold/test_app.py::test_sw_manifest_and_styles_are_served_from_the_same_process`.
Keep it.

**No inline `serviceWorker.register`** — `AGENTS.md` #6 and
`tests/js/scaffold.test.mjs` both enforce it. Registration stays owned by the
vendored `update-manager.js`, imported from `main.js`. An inline registration
would create a second registration the manager cannot control, and a
version-pinned one would reinstall the worker on every load and loop reloads.

**Icons are deliberately NOT version-pinned.** Template §3b: "Icons and the
manifest stay unversioned (immutable content)." §1c step 5 is explicit that iOS
caches manifest metadata longer than page content, so a changed icon may require
remove-and-re-add of the Home Screen app.

**No OS badge.** `app/static/js/pwa/badge.js` is vendored and stays **unused**;
do not import it. Template §3c Pattern J's platform note is explicit that
`navigator.setAppBadge()` is Chromium-only and that iPhone home-screen PWAs
silently no-op. This is an iPhone-first PWA, so any "you have pending writes"
signal is an **in-app pill**, never an OS badge.

### 9.2 Versioning and the converge gate (template Part 3, Part 4a–4e)

**Pattern A (§3a) is already in place and must not change.** `CACHE_VERSION` in
`app/static/sw.js` is the single source; `derive_version()` and
`install_pwa_version()` in the vendored `app/pwa_version.py` regex-parse it;
`/api/version`, `/health`, `FastAPI(version=...)`, and the HTML
`<meta name="app-version">` all derive from it. Do not introduce a `VERSION`
file — that breaks the convergence gate by design (`CLAUDE.md` § *Traps*).

**`CACHE_VERSION` rotation is mandatory on every mutable frontend change.**
This is `AGENTS.md` #8 and template §3b.1 ("enforce version rotation, do not rely
on memory" — a deploy can be internally consistent while reusing the previous SW
cache key). The scaffold's `SHELL_ASSETS` currently precaches only placeholder
files. Every file this spec adds under `app/static/js` and `app/static/css` must
be added to `SHELL_ASSETS` in the same commit, each as
`'/js/views/home.js?v=' + CACHE_VERSION`.

**Gap: nothing catches a *missing* `SHELL_ASSETS` entry — and this spec adds
~15 modules, so the gap must be closed first.**
`tests/js/scaffold.test.mjs` fails on an entry with no file behind it but cannot
detect a file with no entry — the exact failure mode `GEMINI.md` and `README.md`
both call out, and it is a silent offline-shell hole. The direction that is
tested is the harmless one: a `SHELL_ASSETS` entry pointing at a deleted file
fails loudly. The direction that is untested is the damaging one: a new module
that is imported, served, and cached by nothing — it works perfectly online and
is simply **absent from the installed app**, which is a failure only a user on a
subway can diagnose. With ~15 new modules landing under `app/static/js` and
`app/static/css`, the probability of at least one omission is high and the cost
is a broken offline shell. Spec a new `tests/js/shell_assets.test.mjs` that walks
`app/static/js/**` and `app/static/css/**`, excludes the vendored `js/pwa/`
directory and `sw.js` itself, and asserts every remaining file appears in
`SHELL_ASSETS`. **This is a build-order prerequisite, not a nice-to-have: it
ships in P1, before the first new frontend module, so the hole is closed before
anything can fall into it.**

**Variation matrix picks (Part 4), one per dimension. All five are locked — F18:**

| Dimension (§4) | Pick | Reason |
|---|---|---|
| §4a version source | **Pattern A** (`CACHE_VERSION`) | Already wired; the app version *is* the release version |
| §4b shell strategy | **`FETCH_STRATEGY = 'cache-first'`** (current) | The shell changes on release, not on data change. All data is network-only, so a network-first shell (Pattern H) buys nothing. |
| §4c API read strategy | **`CACHE_API_GETS = false`** (current, network-only) | Match results change the moment a user toggles a `Pantry.md` line. Caching them is a correctness bug, not a perf win. §3b rule of thumb: read-mostly → Pattern I. |
| §4d update UX | **`WAIT_FOR_MESSAGE = true`**, `autoApply: false`, `onStale` shows the banner, Reload → `requestUpdateReload()` | **F18.** A forced auto-takeover reload can land between the user tapping "做过了" and the request completing, losing the log. The obsidian-daily explicit-banner variation is the proven fit for a mutation-bearing app. |
| §4e busy-guard | `canApplyUpdate: () => !mutationInFlight && !datePickerOpen` | Required by the 4d choice. Never auto-navigate while a dialog or form is open. |

**The sixth pick, which is not in the §4 matrix: read-only mode has NO write
allowlist. Locked as F18.** `OBSIDIAN_READ_ONLY` rejects **every** `/api/*`
mutation, with no exception list (§9.17). F4 **hardened** this decision rather
than leaving it open: `pwa-obsidian-daily` has an allowlist for its Open Items
feature, and the reasoning there was that some writes are safe. Here there is no
such distinction to draw — with F4 removing the creation service, `read_only` is
the **only** thing standing between a mutation and the vault, so an allowlist
would not be a convenience, it would be a list of holes, and it would be a list
that goes stale silently. A read-only Pantry Recipes that could still log a cook
or edit a shortlist would also be a confusing half-mode.

**Update convergence invariant (§4d).** With `WAIT_FOR_MESSAGE = true` the update
manager must wait for the installing worker to reach `installed` before posting
`SKIP_WAITING`, and must not navigate immediately after `reg.update()`. Guard
against overlapping version checks and overlapping update applications. Register
the worker at the stable `/sw.js` and version module imports and precache entries
separately. A loop of repeated 2xx `GET /`, `/sw.js`, `/api/version` is the
signature of a stale-shell reload loop.

**iOS resume (Pattern F, §3c).** `visibilitychange → visible` and
`pageshow` with `e.persisted` both trigger a re-render of the current view, so a
BFCache restore on iOS does not show stale match data.

**Controllerchange guard (Pattern D, §3c).** Record whether a controller existed
at load and reload only if it did — otherwise first-install adoption fires a
pointless reload. Paired with the skip-waiting flow (Pattern E) and
`WAIT_FOR_MESSAGE = true`.

**Independent panel loading (§3d).** The Recipe detail view loads three things
that can fail independently — the note body, the Ingredient match rows, and the
Recipe Cooking History. Each gets an explicit `loading → ready | error` state via
a `loadPanel(fetch, render)` helper. "No recipes" renders only after a
*successful* empty response, never during loading and never on failure.

### 9.3 Router, view convention, and the back-button rule (template §2i)

**Architecture: full ES modules (§4f).** `<script type="module" src="/js/main.js?v=__APP_VERSION__">`
with `views/`, `components/`, and `logic/` imported from it, each specifier
carrying `?v=__APP_VERSION__` so the `/js/{path}` route's content injection
version-pins the whole graph (§3b). Full ESM over classic+pure-core because the
app has a component graph and needs direct imports of the vendored modules, and
because the pure `logic/` modules are still `node --test`-able without a DOM.
No build step, no bundler.

**Hash router** in `app/static/js/router.js`:

| Hash | View | Notes |
|---|---|---|
| `#/` | `home` | Recipe list, sorted by match |
| `#/recipe/<basename>` | `recipe` | `<basename>` URL-encoded |
| `#/shortlists` | `shortlists` | The three Meal Shortlists |
| `#/settings` | `settings` | Toggles, including 调试 and 严格模式 |
| `#/provenance` | `provenance` | Full debug table over every Ingredient slot |
| unknown | `home` | plus a non-blocking "unknown route" notice |

**View convention.** Every view module exports
`export function mount(root) { ... return function unmount() { ... } }`. `mount`
binds listeners and starts fetches; `unmount` removes every listener, aborts
in-flight `AbortController`s, and clears every `setTimeout` / `setInterval`. The
router calls `unmount()` on the outgoing view *before* `mount()` on the incoming
one. This is the only cross-view contract; there is no global mutable view state,
which is also what keeps the render-cache-key rule (§5c) satisfiable — a cached
render is only legal when its key includes every input the render consumes, and
with no shared state the recipe list's key is just `{catalogRevision,
stockRevision, strict}`.

**Per-view restore state lives in the origin entry's `history.state`**, written
with `replaceState`, holding `{ scrollTop, filters: { strict, search } }`, so
that `history.back()` re-dispatches the origin's hash and the re-render restores
the exact scroll position with zero extra plumbing (the §2i hash-router gotcha).

**The back-button rule is mandatory**, verbatim from §2i:

```js
function goBack() {
  if (window.history.length <= 1) {
    router.navigate('#/');          // direct link / fresh-load fallback
  } else {
    window.history.back();
  }
}
```

Applied to the Recipe view's Back affordance **and** to its loading-error and
not-found states — a failed load still returns to where the user came from.
Hardcoded `router.navigate()` is reserved for post-action navigations (shortlist
add → `#/shortlists`, cook-log success → the recipe view) and for views whose
only entry point is the canonical route (`#/settings`).

**Test.** `tests/browser/test_recipe_browse_flow.py` enters `#/recipe/拌空心菜`
from `#/shortlists` and asserts Back returns to `#/shortlists` with the scroll
offset restored — the exact bug §2i exists to prevent.

### 9.4 CSS, tokens, and touch targets (template Part 2)

- `app/static/css/pwa.css` is **vendored and byte-identical**. Do not re-declare
  its rules (overflow, `.scroll-row`, `.grid`, `.navbar`, `.fab`, `.bottom-bar`,
  `.drawer`, `.toast`, `.sheet`, `.form-sheet`, `.tap`, 44px targets, 16px inputs,
  safe-area utilities). Re-vendor with
  `python3 ~/projects/pwa-template/scripts/vendor.py .`; the pre-commit hook
  (`git config core.hooksPath git-hooks`) and the CI drift step fail otherwise.
- `app/static/css/tokens.css` gains **domain tokens only**: the five chip colour
  tokens in light and dark, a `.chip` geometry token, and a `--tap-gap: 8px`. Its
  own `TODO(implementation)` header is satisfied by this change and the comment
  is updated in the same commit. `styles.css` keeps only the app-shell layout and
  the domain components.
- **44px minimum tap target** on every chip, button, row, and stepper control
  (§2g). Chips are 32px tall visually but sit inside a 44px hit area via
  container padding, so the *tappable* box is ≥ 44px.
- **`font-size: 16px` on every input**, including the date picker (§2g: below
  16px iOS auto-zooms on focus).
- **Safe-area insets** (§2e) on every fixed or floating surface: the bottom nav
  and FAB; the bottom sheet (Pattern C — the sheet pads its own tail with
  `env(safe-area-inset-bottom, 0px)`, because iOS draws the home indicator *over*
  a slide-up sheet's bottom edge); the FAB-anchored toast (Pattern B —
  `bottom: calc(env(safe-area-inset-bottom, 0px) + 60px + 12px)`, the `60px`
  being the FAB height); and any `top: 0` overlay, which must add the top inset
  or content sits under the Dynamic Island — invisible in desktop browsers and in
  Playwright without device emulation.
- **Modal height cap** (Pattern L, §2f): the date picker is
  `max-height: calc(100dvh - 32px)` so Android's address-bar resize cannot clip
  its buttons.
- **Pull-to-refresh** (Pattern M, §2h) is **enabled on the recipe list only**.
  **Locked as F19**; the exclusion (not the shortlist view, not the recipe detail
  view) is restated in §15. Link `/css/pull-refresh.css` and import
  `/js/pwa/pull-refresh.js`; both are inert until `initPullToRefresh()` is
  called. `canStart: () => navigator.onLine && !hasPendingEdit()` — the offline
  gate and the pending-mutation gate live in the consumer callback, per the
  template. Retain the inline SVG's intrinsic `width`/`height` so a mixed-cache
  release cannot expand the glyph to the viewport. It is a nice-to-have and the
  **last** thing to build, which is why it rides on P11 rather than getting its
  own phase.
- **Waking banner** (Pattern G, Part 1): the vendored `waking-banner.js`, already
  imported by `main.js` and asserted by `tests/js/scaffold.test.mjs`, shows a
  one-time "Connecting…" while a cold-starting launchd backend is slow and is
  dismissed on `pwa:awake`. Unchanged — it is already correct.
- **Stale-paint test discipline** (§5d): browser tests wait on *content*, not
  element visibility, because a cached snapshot paints the element with stale
  text first and `wait_for(selector)` passes too early.

### 9.5 Recipe index (vault read)

`app/recipes/reader.py`. `parse_recipe(source: bytes) -> RecipeNote` reads a note
through `parse_frontmatter()` (ported) and extracts:

- `note_name` — the basename (`盐焗鸡`), which is the wikilink text D2 must emit.
- `note_path` — the vault-relative path, stored in the mapping table.
- `ingredients` — the `材料` list as `(index, raw_value)` pairs.
- `seasonings` — the `调料` list.
- `tools` — the `烹饪工具` list. Both shapes occur in the real notes (`炒锅` as a
  scalar, `- 煮锅` as a block list), and both must parse; an empty value
  (`调料:` with nothing) is an empty list, not an error.
- `steps` — the body after the last `# … 步骤` heading, verbatim. Never rewritten.
- `history` — `first_cooked`, `last_cooked`, `cooking_count`,
  `cooking_frequency`, `cooking_years`, `recent_activity`, `favorite_season`,
  `auto_updated`. **Read only.** The PWA never writes them;
  `recipeTracker.md` owns them.

`RecipeIndex` is a TTL snapshot provider in the shape `pantry.py` already
demonstrates: a cached tuple of `RecipeNote`, a `refresh()` gated on
`time.monotonic()`, an `invalidate()` called after any write that could change
the index, and a `close()` that is a documented no-op (no descriptors held).

Index construction reads only `<RECIPES_ROOT>/*.md` via
`AtomicNoteStore.list_direct_markdown()`, bounded at `max_files=500`. Ordering is
vault enumeration order, preserved as `list_direct_markdown` documents for APFS
creation order; the *display* sort is applied client-side per D4.

`Recipes.md` is the vault's table-of-contents page, not a Recipe. It is excluded
by a `_is_recipe_note()` predicate (must have a `材料` or `调料` frontmatter key
and must not be the index page). A note whose frontmatter is malformed is
**skipped and counted**, not fatal — one bad note must not blank the whole list.
The skip count is surfaced in `/health` and in the provenance view, so a silent
drop is impossible.

### 9.6 Ingredient value parser

`app/recipes/ingredients.py`.
`parse_ingredient_value(raw: str) -> ParsedIngredient` returns
`(raw, parsed_name, parse_method)`, where `parsed_name` is `None` only when the
value is genuinely unparseable. **`raw` is always preserved verbatim** — it is the
audit anchor, and `CONTEXT.md`'s loss-minimizing discipline applies to it too.

**Five observed shapes over 26 distinct `材料` values:**

| # | Shape | Examples | `parse_method` | Count |
|---|---|---|---|---|
| 1 | Bare name | `空心菜`, `Kale`, `Clam`, `开心果酱`, `红苋菜`, `茼蒿`, `香菇`, `娃娃菜`, `包菜`, `Arugula`, `Mortadella` | `bare` | 11 |
| 2 | Quoted wikilink | `"[[Mackerel]]"` | `wikilink` | 1 |
| 3 | Emoji + alt | `🌶️/Shishito`, `🐔/鸡翅`, `🐔/带皮鸡腿`, `🍄/香菇`, `🍄/黑木耳`, `🧀/Stracciatella` | `emoji_alt` | 6 |
| 4 | Emoji only, no alt | `🥦`, `🥚`, `🍚`, `🥔`, `🍠`, `🍅` | `emoji_only` | 6 |
| 5 | `X/Y` with **no** leading emoji | `香料/Basil` | `alt_pair` | 2 |

The emoji-only list is **not** confined to `材料`: three more emoji-only values
exist in `调料` (`🧄`, `🫚`, `🍋‍🟩`) and the same parser handles them. The count
in row 4 is 6 of the 26 distinct `材料` values. `🍔` is **not** among them —
`🍔` appears in none of the 16 notes, and `🥔` (potato, from 烤土豆) is the value
the earlier transcription meant.

**The emoji dictionary is hardcoded and required, and it spans BOTH frontmatter
keys.** 6 of the 26 distinct `材料` values (23%) carry no recoverable name, and
`🥚` alone appears in 3 of 16 recipes. Three further emoji-only values live in
`调料` — `🧄`, `🫚`, `🍋‍🟩` — and are parsed by the **same** function, which is
why the dictionary is one set and not a `材料`-only table. `🌶️` is in the
dictionary but is not an emoji-*only* value: in `材料` it appears only in the
`🌶️/Shishito` shape, which carries a recoverable name.

**This is an explicit key set, not a Unicode property class. Locked as F15.**
The distinction is forced by the data, not stylistic: `🍋‍🟩` is `U+1F34B U+200D U+1F7E9`, a
**three-codepoint ZWJ sequence**. `\p{Extended_Pictographic}` matches its parts,
not the run; the `regex` module's grapheme clustering is a new dependency
(F15 rejects it, and **not** because of the dependency — because clustering, not
a property class, is the real requirement, so the `regex` module would not solve
this);
and matching the three codepoints independently yields three unrelated emoji and
garbage text. A *set of literal keys* is the only thing that expresses "this
three-codepoint sequence is one key named 青柠". Minimum required keys:

```python
EMOJI_NAMES = {
    "🥦": "西兰花", "🥚": "鸡蛋", "🍚": "米饭", "🥔": "土豆",
    "🍠": "红薯",  "🍅": "番茄",
    # 调料 — exercised through the same parser, asserted by tests
    "🧄": "蒜",   "🫚": "姜",  "🌶️": "小米椒", "🍋‍🟩": "青柠",
    # shape detection only, no current note needs the name
    "🧀": "奶酪", "🍄": "蘑菇", "🐔": "鸡肉", "🍞": "面包",
}
```

Three Unicode facts that make a naive implementation wrong, and the required
handling:

- `🌶️` is `U+1F336 U+FE0F` — a base plus **variation selector 16**. `NFKC` does
  not remove `U+FE0F`. The dictionary key must include it, and lookups must be
  attempted both with and without the selector.
- `🍋‍🟩` is `U+1F34B U+200D U+1F7E9` — a **ZWJ sequence** spanning three
  code points. It must be a single dictionary key, matched **longest-key-first**,
  and the leading-emoji-run stripper must consume the whole ZWJ run as one unit.
  Splitting it parses as three unrelated emoji and produces garbage.
- A **VS-16-only** or **ZWJ-only** run must be consumable even when it is not a
  dictionary key, which is why the run matcher is a range class rather than a
  dictionary lookup.

**The leading-emoji-run stripper is a hand-built character class, not a Unicode
property escape. F15, locked.** Python's stdlib `re` has no
`\p{Extended_Pictographic}` escape at all, and the `regex` module is rejected as
a new dependency no sibling uses. Note the stronger reason: even with `regex`
installed, matching the *run* rather than its parts requires **grapheme
clustering**, so the explicit key set is the answer either way. So:

```python
# Decision-dense: the emoji run is an explicit range class plus the two
# invisible joiners, consumed greedily from offset 0, longest-key-first.
_EMOJI_RUN = re.compile(
    r"^(?:[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF"
    r"\U0001F1E6-\U0001F1FF\uFE0F\u200D])+"
)
```

**Parse order.** NFKC → strip one layer of matching surrounding quotes
(`"` and `“”‘’`) → if the remainder is `[[…]]`, take the inner text and
`parse_method='wikilink'` → else strip the leading emoji run → if the remainder
starts with `/`, drop the slash and take the rest as the alt (`emoji_alt`) →
else if the whole post-run string is a dictionary key, use its name
(`emoji_only`) → else if a `/` remains it is shape 5 and `parsed_name` is the
**left** side, the CJK primary, with `parse_method='alt_pair'` → else
`parsed_name` is the string and `parse_method='bare'`.

`NFKC` runs first because it folds full-width Latin (`Ｋａｌｅ`) and compatibility
forms before anything else inspects the bytes. It is applied to a working copy;
`raw` is never mutated.

**Why shape 5 takes the left side.** For `香料/Basil` neither side is emoji-only,
and `香料` is the vault's own primary key for the entry while `Basil` is a
supplemental alias. The synonym tier then handles `香料 ↔ Basil`, so either side
resolves; the left side is simply the deterministic default and the full `raw`
stays available for the debug view. The same rule gives `调味酱油` for
`调味酱油/蒸鱼豉油` — a class-word prefix that `staples.yaml`'s `class_suffixes`
then treats as an assumed seasoning (§9.9).

### 9.7 Normalization

`app/recipes/normalize.py`. `normalize_ingredient(value: str) -> str`, applied
to the **parsed name** on the recipe side and to `canonical_name` on the catalog
side, so both sides are comparable before any tier runs.

**The strip order is load-bearing and is exactly this** (F15, locked;
decision-dense):

1. `NFKC`
2. surrounding quotes (`"` `“` `”` `‘` `’`) and leading/trailing whitespace
3. `[[` … `]]` wikilink brackets — a name may arrive already bracketed from a
   manual override
4. **leading emoji run** (the `_EMOJI_RUN` class from §9.6)
5. `#tag` tokens
6. emoji date markers: `💵 ✍️ ➕ 🛫 ✅ ❌ 📅` each followed by
   `\d{4}-\d{2}-\d{2}` — the `_DATE_TOKEN` regex from the ported `pantry.py`
7. parenthesised flavour breakdowns (`（原味*4， 芋头*4， 麻薯*4）`)
8. **size / quantity, THEN brand — in that order**, with the size pass repeated
   to fixpoint
9. collapse leftover whitespace, then trim leading/trailing
   `,，、-` and spaces

The result is the **product core**: the value with every size, brand, and
annotation removed, which is the only form either side of the matcher compares.

```python
# Decision-dense, and verified against all 178 real canonical_name values.
# Applied REPEATEDLY until fixpoint (a doubled size tail needs both passes).
_NUM = r"\d+(?:[.,]\d+)?"
_UNIT = (r"(?:fl\s*oz|fl\s*ounces?|ounces?|oz|pints?|quarts?|gallons?|gal|pts?|qts?"
         r"|千克|毫升|公斤|盎司|磅|升|克|个|枚|片|罐|瓶|袋|盒|根|颗|条|头|把|支|份|只|粒|入"
         r"|kg|mg|ml|lbs|lb|l|g)")
_BOUND = r"(?![A-Za-z\u3000-\u9fff])"   # a unit may not be a PREFIX of a longer word
_SIZE_RE = re.compile(
    rf"(?:{_NUM}\s*(?:[-–~]\s*{_NUM})?\s*(?:{_UNIT})(?:\s*[x×*]\s*{_NUM})?){_BOUND}"
    rf"|(?:{_NUM}\s*[x×*]\s*{_NUM})"        # bare multiplier, e.g. a stray "*5"
    rf"|(?:[x×*]\s*{_NUM}(?=\s|$))",         # a trailing multiplier with no size
    re.IGNORECASE,
)
_PAREN = re.compile(r"[（(][^）)]*[）)]")   # flavour breakdowns, e.g. （原味*4，芋头*4）
```

**Why size before brand, concretely.** Size stripping is anchored to a numeric
pattern and applied to fixpoint, so a doubled size tail collapses. The
`_BOUND` guard is load-bearing in both directions: without it `1 Gallon Jug`
loses the `G` and leaves `allon Jug`, and `6枚装` loses the `枚` and leaves
`装`; with it, both survive intact. Verified outputs for the rows this spec
cites, plus the four shape families the real data contains (plain, range,
multiplier, doubled):

| `canonical_name` | product core |
|---|---|
| `Wang Korea 有机去壳甘栗仁 60g*5 300 克` | `Wang Korea 有机去壳甘栗仁` |
| `八道 高级牛骨汤面 125g*4 500 克` | `八道 高级牛骨汤面` |
| `空心菜嫩苗 0.95-1.05 磅` | `空心菜嫩苗` |
| `红苋菜苗 0.95-1.05 磅` | `红苋菜苗` |
| `新鲜小叶茼蒿 1 磅` | `新鲜小叶茼蒿` |
| `Orri 蜜橘 2.8-3.2 磅` | `Orri 蜜橘` |
| `Surasang 韩国年糕片 1.43 磅` | `Surasang 韩国年糕片` |
| `柴米 传统工艺 软心皮蛋 6枚装 65 克` | `柴米 传统工艺 软心皮蛋 6枚装` |
| `Icelandic Provisions, Extra Creamy Skyr, Cold Brew Coffee, 4.4 Ounce` | `Icelandic Provisions Extra Creamy Skyr Cold Brew Coffee` |
| `365 by Whole Foods Market, Organic Broccoli Florets, 16 oz, (Frozen)` | `365 by Whole Foods Market Organic Broccoli Florets` |
| `Frozen Mackerel Boneless Protion-cut 5P 10.58 盎司` | `Frozen Mackerel Boneless Protion-cut 5P` |
| `Mushroom Dried Morel Mushrooms` | unchanged (no lexicon brand, no size) |
| `柴米 蒜香蒸茄子 300 克` | `柴米 蒜香蒸茄子` → then `蒜香蒸茄子` after the brand step |

142 of 178 rows change under this step; 36 are already bare product cores.

**Accepted imprecision, recorded.** `Chobani® 20g Protein Lowfat Greek Yogurt
Vanilla 6.7oz` reduces to `Chobani® Protein Lowfat Greek Yogurt Vanilla` — the
`20g` is a protein claim, not a size, and is indistinguishable from one. This is
harmless for matching (the word `Protein` is not a food noun and the row is not a
recipe target) and is called out here so a future reader does not "fix" it by
narrowing the pattern and breaking `0.95-1.05 磅`.

**The ordering constraint this imposes.** Size stripping must run **before** brand
stripping, because the size pattern is numeric and anchored while a brand lexicon
entry is a bare token. If brand ran first, a lexicon entry could consume `365` out
of `365 By Whole Foods Market, Organic 1% Milk` as a brand and leave
`By Whole Foods Market, Organic 1% Milk, 32 Fl Oz` for the size pass — and any
digit the brand rule left behind would then be reinterpreted as a size of the
wrong span. The numeric pattern must be consumed before any lexicon lookup sees
the token. Conversely the brand lexicon must not run first *and* be allowed to
match numeric tokens, so `365` is a lexicon entry matched as a whole
space-delimited token, never as a bare digit run.

**The brand lexicon is closed and mandatory — never "strip the first token".**
`BRAND_LEXICON` is a frozen set of leading tokens observed in the 178-row
catalog, seeded from the real data in both scripts:

```
盒马 AeroFarms Chobani Chobani® Icelandic Provisions Lifeway Loacker
POM Wonderful Simple Nutricost 乐事 喜茶 柴米 好丽友 小巷口 禾苑 晨曦
Fusipim JAYONE Koia MOOALA HAITAI ORION CJ Asahi Acure Manukora Matchaful
Driscoll's Beekeeper's BEEKEEPERS Bell Gelatys Dr.Reju-All LESSEREVIL
Forward EVOLUTION NOW Orri "Wang Korea" 思念 臻品德 中华 江船长&Yaba 超禾
味圈 必品阁 饭匹兄弟 "Love Me Sweet" Sanpellegrino "Trader Joe's" 365
"365 By Whole Foods Market" "365 by Whole Foods Market" "Whole Foods Market" YABA
```

A leading token is removed only if it is in `BRAND_LEXICON`, compared
case-insensitively after NFKC, and it is removed together with the single
following space. Nothing else.

Two proofs that position cannot substitute for the lexicon, both live:

- `Mushroom Dried Morel Mushrooms` — the leading token is a **food word**. A
  first-token rule destroys it and matches it to nothing.
- `优质白桃礼盒`, `韩国紫苏叶`, `台湾旺旺浪味仙`, `小白菜心`, `A级波斯黄瓜`,
  `小巷口白糖馅蟹壳黄烧饼` — the leading token is a **product qualifier**, and in
  the last case `小巷口` is a brand *inside* a longer name. Stripping the first
  token would remove a qualifier and leave a fragment.
- The converse: `柴米 蒜香蒸茄子 300 克` — `柴米` **is** a lexicon brand, and
  after stripping it the remaining `蒜香蒸茄子` must still not match a recipe
  Ingredient of `蒜`. That is exactly the collapse D1 asks to be prevented, and
  it is caught by the two guards below rather than by the brand rule.

**The flavour-descriptor guard — two rules, both required.**

1. **Whole-name-segment boundary.** A single-CJK-character token may match only
   when it is the *entire* name segment — followed by end-of-string,
   whitespace, a digit, or a non-CJK character. It may **never** match as a
   prefix of a longer CJK word. This alone rejects `蒜` in `蒜香蒸茄子` (72)
   and in `蒜蓉面包味` (110), `土豆` in `呀!土豆 薯条` (62), and `芝麻` in
   `芝麻饼干` (25) and in `芝麻烧饼` (102). Latin tokens require a `\b` boundary
   for the same reason.
2. **Single food class on the synonym tier.** Every candidate must share the same
   Pantry Category *family* (the leading component: `1.1`/`1.1c`/`1.1d`/`1.1f` →
   family `1.1`; `1.2`/`1.2d`/`1.2m`/`1.2s`/`1.2v` → `1.2`; `2`, `3`, `4`, `5`,
   `6` as themselves), and that family must be in the synonym entry's
   `families` allowlist. Two candidates from different families are rejected.
   This is what makes the real data work: `芝麻` as a condiment is `1.1` (id 102)
   while `芝麻饼干` is `4` (id 25), so the class guard rejects the snack on
   category alone, before the segment guard even runs. The same holds for `蒜`:
   real garlic is `1.1` while `乐事 …薯片蒜蓉面包味` is `4` (id 110), so the
   family guard rejects it **independently of any tokenization**.

**Both guards are checked, not either — and each is independently required.**
The per-row table in D1 shows which guard stops which row, and the result is
asymmetric: the segment-boundary guard independently rejects all **six** live
rows, while the family guard independently rejects **two** of them
(`芝麻`→25 and `蒜`→110). That asymmetry is exactly why a single combined
assertion over the six rows is not sufficient coverage: it would pass with
either guard deleted. §10.2 therefore requires per-row assertions *plus* a
negative control per guard — a test that removes one guard and asserts the row
only that guard stops now resolves to the wrong candidate, which fails if the
guard is deleted and passes if it works.

**Fuzzy tolerance.** The final tier uses Levenshtein distance ≤ 1 on the
**normalized** strings only, and only when both are ≥ 6 characters and
single-script. It never runs on raw text. The live typo `Protion` (for
`Portion`) in catalog id 22 `Frozen Mackerel Boneless Protion-cut 5P 10.58
盎司` is a *catalog-side* typo and is handled by the `contains`-token rule for
`mackerel` (§9.8 tier 5), not by fuzzy; the fuzzy tier exists for the reverse
direction — a recipe-side misspelling of a catalog name — and is the reason for
"edit distance ≥ 1" rather than 0. Fuzzy additionally requires the best candidate
to be **unique** (no tie) and to be in an allowed family. `柴米 蒜香蒸茄子` vs
`蒜` is many edits apart and structurally cannot reach this tier.

### 9.8 The tier ladder (D1's core requirement)

`app/recipes/matcher.py`.
`resolve_ingredient(parsed, stock, catalog) -> MatchResult` walks a **strictly
ordered** ladder and stops at the first tier that produces an accepted result.
Order is not negotiable: a weaker tier must never pre-empt a stronger one.

| Tier | `match_method` | Rule | `confidence` |
|---|---|---|---|
| 1 | `exact_id` | The mapping already carries `pantry_item_id` and that id is still live in the catalog | 1.0 |
| 2 | `exact_name` | `canonical_name == parsed_name`, exact after NFKC + whitespace trim only | 1.0 |
| 3 | `variant_alias` | `parsed_name` equals a `variants[]` entry, case-insensitively, trimmed. **Expected to be near-dead: 3 of 178 rows have a non-empty `variants`, two of which are just the long product name.** Implemented because `CONTEXT.md` defines Pantry Item Alias in terms of `variants`; not load-bearing. | 0.95 |
| 4 | `note_basename` | `parsed_name` relates to a catalog row's effective basename by the prefix + segment-boundary rule | 0.9 |
| 5 | `normalized_exact` | `normalize_ingredient(parsed_name) == normalize_ingredient(canonical_name)` and the normalized form is ≥ 2 characters | 0.85 |
| 6 | `synonym` | `parsed_name` hits a `synonyms.yaml` entry whose `families` allowlist contains **every** candidate's family, and all candidates share one family | 0.7 |
| 7 | `staples` | `parsed_name`, or a `class_suffixes` match such as `X酱油 → 酱油`, is in `staples.yaml` | 0.6 |
| 8 | `unresolved` | Nothing matched | 0.0 |

Tier 2 fires before tier 5 by design, and the golden test asserts the ordering
property explicitly: a value that tier 2 can resolve is never resolved by tier 7.

**Tier 4, "food-note basename", defined precisely.** A catalog row's effective
basename is its `canonical_name` with brand-lexicon and size tokens removed and
the result trimmed — so `空心菜嫩苗 0.95-1.05 磅` → `空心菜嫩苗` and
`红苋菜苗 0.95-1.05 磅` → `红苋菜苗`. Tier 4 accepts a **prefix relation**
between the normalized recipe name and the normalized catalog basename, requiring
≥ 2 matching characters **and** that the unmatched suffix contains no CJK
character. That suffix condition is the same segment-boundary rule as §9.7, and it
is what stops `空心菜` from matching an `小白菜心`-style row and stops `香菇` from
matching a `香菇酱` row. Tier 4 is a separate tier from tier 5 precisely because
tier 5 demands full equality while tier 4 accepts a bounded prefix.

**Tier 6, the synonym set.** `app/recipes/lexicon/synonyms.yaml`, ~22
hand-written pairs, each with an explicit `families` allowlist:

```yaml
# Closed and hand-maintained. Transliteration is NOT used and must never be
# added: pinyin collisions (e.g. 花生 / huasheng) silently merge unrelated foods.
- canonical: 香菇
  match: [Shiitake, "Shiitake Mushroom", 冬菇, 花菇]
  families: ["1.1", "1.2d"]
- canonical: 鲭鱼
  match: [Mackerel, 青花鱼, 鲅鱼]
  families: ["1.1", "1.2", "2"]
```

`match` entries resolve three ways, all of which pass through the candidate's
category-family check and the segment-boundary rule: exact normalized equality,
whole-token `contains`, or whole-name equality. The minimum set the live data
needs:

`Kale↔羽衣甘蓝/芥蓝`, `Arugula↔芝麻菜/沙拉菜`,
`Shiitake/香菇↔冬菇/花菇`, `Mackerel↔鲭鱼/青花鱼`,
`Shishito↔小米椒/朝天椒`, `Basil↔香料/罗勒`,
`focaccia↔佛卡夏`, `Stracciatella↔奶酪`,
`Clam↔花蛤/蛤蜊`, `Broccoli↔西兰花`,
`Potato↔土豆/马铃薯`, `Sweet Potato↔红薯/地瓜`,
`Egg↔鸡蛋/蛋`, `Tomato↔番茄/西红柿`, `Rice↔米饭/白米饭`,
`Chicken Wing↔鸡翅/鸡翅膀`, `Chicken Thigh↔鸡腿/琵琶腿`,
`Cabbage↔包菜/卷心菜/圆白菜`, `Water Spinach↔空心菜/蕹菜`,
`Amaranth↔红苋菜/苋菜`, `Garland Chrysanthemum↔茼蒿`,
`Baby Chinese Cabbage↔娃娃菜`, `Black Fungus↔黑木耳/木耳`,
`Mushroom↔蘑菇`, `Lemon↔柠檬`, `Ginger↔姜/姜片`, `Garlic↔蒜/蒜蓉`.

`Lemon↔柠檬` and `Lime↔青柠` are **separate entries and must never be merged**.
`🍋‍🟩` parses to `青柠`, and a `contains` rule that let `青柠` reach a `柠檬`
catalog row would substitute one fruit for another — precisely the silent
flavour-descriptor error D1 asks us to avoid. `青柠` stays `unresolved` unless a
real catalog row appears. Recorded explicitly because "the pair set is closed and
small" is only true if each entry is individually defensible.

**Never store a Pantry Item id in a lexicon file.** `items.id` is
`INTEGER PRIMARY KEY AUTOINCREMENT`, and a catalog re-import can renumber it.
Lexicon entries are *name predicates* resolved against the live catalog at
request time. Ids are persisted in exactly one place — `ingredient_mappings` —
and re-resolution exists to repair them when the catalog shifts. This is the
second reason the materialized table exists at all, independent of D1's
performance argument.

### 9.9 `staples.yaml` — in scope

`app/recipes/lexicon/staples.yaml`:

```yaml
# Seasonings assumed on hand. These are NOT matched against the catalog and do
# NOT consume the headline score in the default view.
# Empirical justification: Pantry Category 1.1c (condiments) has exactly
# 1 row in 178 (id 177 李锦记 蒸鱼豉油 14 盎司). Matching Seasonings against the
# catalog cannot succeed, so without this file 调料 coverage is ~5%.
# With it, coverage is ~90% for the cost of one file.
- 油
- 盐
- 糖
- 生抽
- 老抽
- 醋
- 芝麻油
- 蒜
- 葱
- 姜
- 香菜
- 鸡精
- 黑胡椒
- 鱼露
- 小米椒
- 料酒
- dashi
- 面粉
- 米
- 淀粉
- 蚝油
- 蒸鱼豉油
- 韩式辣椒粉
- 新奥尔良腌料
- 窑鸡粉
# Class suffixes: any 调料 whose normalized name ends with one of these is
# treated as the class staple (调味酱油 -> 酱油).
class_suffixes: [酱油, 醋, 油, 粉, 酒, 汁, 酱, 盐, 糖]
```

**The staples tier applies only to values that came from the `调料`
frontmatter key.** A `材料` value that hits this list is a data smell, not a
Seasoning — a 材料 of `油` would be a real bulk purchase. The loader records
`match_method='staples'` only for 调料-sourced values; a 材料-sourced value on
the list resolves to `unresolved` and is surfaced in the provenance view as a
probable mis-filed frontmatter entry. This keeps `CONTEXT.md`'s 调料/材料
separation enforced inside the matcher, not only at the display layer, which is
the only place it cannot silently break.

**Where the 5% → 90% number comes from, stated honestly.** It is the ratio of
distinct `调料` values that a catalog-only match resolves, measured with and
without the allowlist. The structural fact behind it is the `1.1c` row count of
1. The percentages are recorded as the research session's measurement and are
re-verified by the golden test (§10.3), which asserts the resolved/unresolved
status of every `调料` value across the 16 real recipes.

### 9.10 The materialized mapping and its repair path

`app/mapping/store.py`, `IngredientMappingStore`:

- `ensure_rows(recipes) -> None` — for each Ingredient slot with no row, insert
  one carrying §9.6's `parse_method`/`parsed_name` and
  `match_method='unresolved'`. Idempotent.
- `resolve_all() -> ResolveReport` — for every non-`manual` row (F6), re-run
  §9.6 → §9.7 → §9.8 and update `match_method`, `match_tier`, `pantry_item_id`,
  `confidence`, `candidates_json`, `updated_at`. One `BEGIN IMMEDIATE`
  transaction for the whole pass, **with a per-slot `sqlite3.IntegrityError`
  catch inside it** (F2, locked). The next table is the specification; the
  earlier wording of this bullet — *"One `BEGIN IMMEDIATE` transaction, so a
  failure leaves the previous state intact rather than a half-resolved table"* —
  was rejected by the user because, without the per-slot catch, a single
  duplicate reverted **every** row.
- `resolve_recipe(recipe_note)` / `resolve_ingredient(recipe_note, index)` — the
  targeted forms.
- `set_manual(recipe_note, index, pantry_item_id) -> row` — validate the id is
  live in the catalog, `DELETE` any existing row for the slot, `INSERT` a fresh
  `manual` row. The trigger from §7.3 makes the delete mandatory, not stylistic.
- `unresolved_rows()` — the input to the "re-resolve the unresolved ones" action.
- `stale_rows()` — rows whose `pantry_item_id` no longer exists in the live
  catalog. Surfaced in `/api/recipes` as `staleMappingCount` so catalog drift is
  visible without opening the debug view.
- `invalidate()` — called after any write that changes stock or recipes.

#### 9.10.1 The transaction's integrity guarantee and the per-slot catch coexist (F2, locked)

They are not in tension because they cover **different failures**, and the
division is the specification:

| Failure | Caught by | Effect on the table |
|---|---|---|
| A slot's `(recipe_note, pantry_item_id)` collides with another slot of the **same** recipe | the **per-slot** `sqlite3.IntegrityError` catch (F2) | **only that slot** is downgraded to `unresolved` with the conflicting candidate written to its `candidates_json`; every other slot commits normally |
| Catalog unreadable, DB locked past `busy_timeout`, disk error, or any unexpected exception | the **transaction** | the whole `BEGIN IMMEDIATE` is rolled back, `resolve_all()` raises, and the previous state is **intact** — not half-resolved |

**The mechanics, precisely, because "a failure leaves the previous state intact"
was exactly the sentence the override invalidated.**

1. The `try` / `except sqlite3.IntegrityError` is **inside** the transaction and
   is scoped to **one `execute()` call** — not to the transaction object, and not
   to the loop. It never sees, and never rolls back, the enclosing transaction.
2. **The SQLite rule that makes this work:** with the default conflict resolution
   `ON CONFLICT ABORT`, a statement-level constraint violation rolls back **only
   that statement**. The transaction stays open and usable. Therefore **neither
   `OR ROLLBACK` nor `OR FAIL` may appear on the slot `UPDATE`** — both would undo
   the whole transaction, which is precisely what the override forbids. A
   static-contract test (§10.2) asserts the absence of both clauses.
3. The recovery write for the offending slot (`UPDATE … SET match_method =
   'unresolved', match_tier = 0, pantry_item_id = NULL, confidence = 0.0,
   candidates_json = '…duplicate_slot_conflict…', updated_at = …`) is a **new
   statement inside the same open transaction**. It clears the unique-index
   violation, so it — and every other slot's write — commits together.
4. `ResolveReport` gains `duplicateSlotConflicts: int` alongside
   `reconsidered` / `resolved` / `stillUnresolved` / `staleReset`, and
   `POST /api/recipes/resolve` returns it (§9.16). A count > 0 is a **data defect
   in a recipe note**, not an engine error, and it is visible without opening the
   `调试` view.
5. `PRAGMA foreign_keys=ON` (§7.1) is unaffected: `pantry_item_id` carries **no
   `REFERENCES` clause** (§7.3), so the only `IntegrityError` this catch can ever
   see is the inverted unique index. The catch is therefore narrow and total —
   it cannot mask an unrelated integrity failure.
6. **`manual` rows are outside the whole mechanism.** `resolve_all()` skips them
   (F6), so a hand fix is never a source of, nor a casualty of, a slot conflict.
7. `resolve_recipe(recipe_note)` and `resolve_ingredient(recipe_note, index)` use
   **the same per-slot catch**, in the same one-transaction form. The override is
   not specific to the full pass.

**What this buys, in the failure terms the user chose it for.** Before: a recipe
listing the same Pantry Item twice meant either (a) the entire resolve pass
rolled back and the app silently served stale mappings for **all 16 recipes**, or
(b) the constraint was dropped and the app rendered two chips claiming one SKU is
in stock. After: **one honest `unresolved` chip**, with the conflict recorded in
`candidates_json`, counted in `duplicateSlotConflicts`, and rendered by the
`调试` toggle — so a recipe whose two `材料` both resolved to the same Pantry Item
**shows the duplicate rather than hiding it**.

**The scope of the defect, recorded so the constraint is not misread as
load-bearing.** No current recipe collides: the 32 `材料` slots across the 16 real
recipe notes are enumerated in §3's F2 entry, `花蛤拌饭` and `微波菜菜` are the two
closest calls, and both stay distinct. The index and the catch are a **defensive
data-integrity guard** against a future user-authored note — which is why the
degradation path is a downgrade plus an audit record rather than a refusal.

**The honesty tradeoff, stated (D1).** A persisted wrong mapping is permanent
and invisible. Three mitigations ship together and all three are required:

1. **Provenance is always stored**, so nothing is ever *un*-debuggable:
   `raw_value` verbatim, `parsed_name`, `parse_method`, `match_method`,
   `match_tier`, `confidence`, and `candidates_json` (the rejected candidates
   with their names and ids).
2. **A re-resolve action** over unresolved *and* stale rows, plus an automatic
   re-resolve when the catalog revision changes (§9.11.2).
3. **The `调试` provenance toggle** (D4), which surfaces 1 to the user.

`match_method='manual'` rows are additionally immutable (§7.3) and render with a
distinct outline. A row the user fixed and a row the algorithm fixed are never
visually or semantically conflated.

### 9.11 Catalog and stock readers

#### 9.11.1 `PantryCatalog` (`app/pantry/catalog.py`)

Read-only. The `items` table is `items(id, canonical_name, category, variants,
first_seen, last_seen, order_count, area, last_price)`, 178 rows.

Opened as `sqlite3.connect("file:<PANTRY_ITEMS_DB>?mode=ro", uri=True)` —
SQLite's own read-only URI, so a bug in our code cannot write. Opened **once** in
the `lifespan` and closed in a `finally`, which is exactly what the scaffold's
own `TODO(implementation)` on line 55 of `app/main.py` asks for ("Close them in a
finally so a descriptor leak cannot outlive shutdown"). Never `CREATE`, `ALTER`,
or `INSERT`. `AGENTS.md` #4: `wholefoods-to-pantry` owns this file.

In-memory indexes rebuilt each `catalog_cache_seconds` refresh:

- `by_id: dict[int, CatalogRow]`
- `by_name: dict[str, CatalogRow]` — NFKC + casefolded `canonical_name`
- `by_variant: dict[str, CatalogRow]` — each `variants[]` entry, NFKC + casefolded
- `by_basename: dict[str, CatalogRow]` — §9.8 tier 4
- `by_family: dict[str, tuple[CatalogRow, ...]]`
- `rows: tuple[CatalogRow, ...]` — the whole universe, for `contains` tiers

**Rows with a non-NULL `area` are excluded from the candidate universe** before
any index is populated. `CONTEXT.md`: `area` holds a non-food area
(`Shampoo`, `Serum`) and "is not a synonym for category and must not be treated as
one".

`CatalogRow` is a frozen dataclass carrying the `items` columns verbatim plus
`family` (the leading component of `category`) and `basename` (tier 4). The
columns `first_seen`, `last_seen`, `order_count`, and `last_price` are **not**
projected into any recipe response — they are Pantry-Write Contract data owned by
`wholefoods-to-pantry`, and nothing in this app needs them. Projecting them would
invite a future reader to treat the catalog as stock.

#### 9.11.2 Catalog-change detection

`catalog_revision` is `"sha256:" + sha256` over the concatenation of
`(id, canonical_name, category, variants, area)` for all rows in `id` order. It
is cheap at 178 rows, stable, and changes on exactly the things that can break a
mapping. It is returned in every `/api/recipes` response. At boot, if it differs
from the revision recorded at the last resolve, `resolve_all()` runs once. This
is what repairs mappings after a catalog re-import renumbers an id — the reason
re-resolution exists alongside the "never store an id in a lexicon file" rule
(§9.8).

#### 9.11.3 `PantryStockIndex` (`app/pantry/stock.py`) — the in-stock signal (F1, locked)

D4 requires an `in-stock` chip colour, which requires Pantry Stock, and F1 fixes
where Pantry Stock comes from: **the live vault note
`Logistics/库存/Pantry.md`, in addition to the catalog.** `CONTEXT.md`: "See the
existing `Logistics/库存/Pantry.md` note, which is the current home of stock
state." `PANTRY_NOTE_RELATIVE` is a Server-Owned Root.

**Two sources, two jobs, and the split is not negotiable.** `pantry_items.db`
answers *"which product is this?"* — `pantry_item_id`, brand, `💵` price. The
vault note answers *"do I have it right now?"* — `open` / `in_progress` /
`done` / `cancelled`, per Pantry Unit. Neither alone answers D4's question:
`红苋菜苗` (139) and `新鲜小叶茼蒿` (70) are in the catalog and are **not** open in
the pantry, and `空心菜嫩苗` (83) was `❌` cancelled on 2026-09-22 and re-bought
two days later, so its catalog row cannot say which purchase is current. The
full measured table is in F1; it is the reason this subsection exists.

**Reuse, not reimplementation.** The read uses the **ported
`app/vault/pantry.py`** with behaviour unchanged. The full element-by-element
contract is the table in F3's sibling decision F1; in summary:

- `parse_pantry(source: bytes, *, max_bytes: int = 2_000_000) -> PantrySnapshot`
  walks numbered H1 sections, H2/H3 subsections, `dataviewjs` / `Tasks` fences,
  and task lines with `💵 $price ✍️/➕/🛫/✅` metadata. Its
  `_MAX_ITEMS_TOTAL = 1000` bound, its byte-span `start`/`end` discipline, and
  its typed `PantryError` failures are kept as-is. F1 carries the
  element-by-element table of the regex vocabulary, the status set, and
  `derived_status`; the points that most often get re-derived wrongly are:
- The regex vocabulary is carried over verbatim: `_TASK` (single-character
  status, matched not assumed), `_MONEY`, `_UNIT` (`^\d+\/\d+`, the unit-split
  marker), `_ADDED`, `_ENDED` (`[✅❌]` plus a date — this is what recovers a
  cancellation date), `_TAG` / `_TAG_TOKEN`.
- `_OPEN_STATUSES = {" ", "/"}`. `[ ]` is open, `[/]` is in-progress, and an
  **unrecognized marker is never open** — `[-]`, `[>]` and anything else is
  dropped. This is load-bearing: it is why the money totals in the three existing
  implementations agree.
- `derived_status(index)`: a parent is `done` iff **all** children are `done`,
  `in_progress` if **any** child is `done` or `in_progress`, else `open`; a
  childless row uses `raw_status`.
- The **per-unit parent exclusion** rule: a `_UNIT` row is a unit; its parent is
  excluded from every count and every money total, because its `💵` is the
  *per-unit* price. The per-unit price rule is `CONTEXT.md`'s Pantry Unit rule
  and is not relaxed anywhere in this port.
- `PantryIndex` is the TTL-cached, read-only snapshot provider this app inherits
  as `PantryStockIndex`: `snapshot()` → `_refresh()` gated on
  `time.monotonic()`, `PathSafetyError` → `PantryError("pantry_source_unreadable")`,
  `None` → `PantryError("pantry_source_missing")`, TTL from
  `stock_cache_seconds`. `invalidate()` and `close()` are wired even though the
  PWA never writes stock, so a future write path does not have to add them.

**The `💵 $X.XX` per-unit invariant, restated because it is the single easiest
thing in this app to get wrong.** `💵 $X.XX` on a **parent** line is the
**effective per-unit price**. Every consumer must **EXCLUDE the unit parent** and
count each **open `k/N` subtask at that price**. Counting the parent *and* its
units double-counts; counting the parent *instead of* its units halves a
half-used item's value. **Three implementations of this math must stay in
parity** — the `existingPantryValue` dataviewjs in `Logistics/库存/Pantry.md`,
`Helper/scripts/pantry_snapshot.py`, and this app's logic — and the parity is a
**test obligation**, not a comment: `scripts/snapshot_pantry_catalog.py` refuses
to refresh `tests/fixtures/pantry_stock_math_parity.json` when the PWA's total
and `pantry_snapshot.py`'s total disagree, and
`tests/pantry/test_stock_math.py` asserts the PWA reproduces the frozen number.
F1 specifies the three shape cases that must be covered.

`PantryStockIndex` exposes:

- `in_stock_names: frozenset[str]` — NFKC + casefolded **product core** of every
  open, non-unit-split row. The product core is `normalize_ingredient()` of the
  row's cleaned text, with the money, date markers, and `#tags` already removed
  by the ported `_clean_text` / `_tags_of`. So `空心菜嫩苗 0.95-1.05 磅` yields
  `空心菜嫩苗`, and the tier-4 basename lookup finds id 83. Using the *same*
  normalization as the matcher is what makes the two agree.
- `in_stock_ids: frozenset[int]` — the joined view: every `in_stock_names` entry
  that the Stock Join resolved to a live catalog row. This, not the name set, is
  what the chip classifier consumes.
- `unjoined: tuple[StockJoinMiss, ...]` — open pantry lines with no catalog
  row, each carrying its raw line text and its normalized product core. Surfaced
  in the `调试` toggle and counted in `/health` as
  `stock.unjoinedLineCount` and in `GET /api/recipes` as `stockUnjoinedCount`.
- `sections: tuple[PantrySection, ...]` — for the counters in `/health` and for a
  future Pantry view (deferred, §15).
- `note_revision` — `"sha256:" + sha256(source)`, the same convention as every
  other note revision in the app, and the value returned as `stockRevision`.
- `invalidate()` and `close()`.

**The Stock Join, and the honesty it requires.** A pantry line is free text; a
catalog row is a normalized name. The join is a normalized-name index built on
`normalize_ingredient()` — the same function the matcher uses, which is the only
reason the two sides can agree — in the order: exact `by_name` hit; tier-4
`by_basename` hit; then a **manual override** from the committed
`app/pantry/line_overrides.yaml`, which is keyed by the *normalized pantry-line
name* and valued by a `canonical_name` looked up in the **live** catalog at read
time. It is deliberately not keyed by id, because §9.8's "never store a Pantry
Item id in a lexicon file" rule applies verbatim: `items.id` is `AUTOINCREMENT`
and a re-import can renumber it.

**This join is measurably weaker than the recipe→ingredient join, and the spec
says so.** The measured miss rate is **2 in 46 open lines, not 1 in 23**: the
misses are `禾苑 蟹粉鱼肉狮子头 冷冻 280 克` (the catalog row is
`禾苑 蟹粉鱼肉狮子头`, id 108 — a trailing condition token survives) and
`Sanpellegrino CIAO! Peach Sparkling Water, 24-Pack` (no catalog row resolves at
all). On top of that, D1's 5-row duplicate-name set means even a *successful*
join can land on either of two ids. The consequences are concrete and all three
are specified in F1: the join gets **its own unresolved bucket**, separate from
the ingredient matcher's; the debug toggle surfaces
`stockJoinState ∈ {joined, override, unresolved}` beside the resolved id; and the
override file is **load-bearing rather than a nicety**, because without it two
live pantry lines are permanently unexplainable and the only remaining remedy
would be editing the user's vault, which this app must never do.

**The extra cost and the degradation decision.** F1 buys honest stock at the price
of **one more vault file to read** (a second Server-Owned relative path, a second
TTL, a second `note_revision`) and **one more whole-layer failure mode**: if
`Pantry.md` is missing, unreadable, or unparseable, *every* recipe loses its
stock colours, not one.

**The decision is to fail closed.** `PantryStockIndex` raises `PantryError`;
`GET /api/recipes` and `GET /api/recipes/{name}` return **503
`pantry_stock_unreadable`**; the UI shows an error state naming the pantry note.
There is no fallback, in either direction and none at all:

- not "assume in stock" — silently inflates every score, and is exactly the
  Pantry Item / Pantry Stock conflation `AGENTS.md` and `CONTEXT.md` forbid;
- not "assume not in stock" — silently deflates every score to
  `have-been-buying`, a plausible-looking wrong answer, the specific thing
  `AGENTS.md` #3 says never to produce;
- not "serve the list with every chip downgraded" — that *looks like data*.

This is why §9.16 already specifies a 503 on `GET /api/recipes` for an
unreadable source — F1 makes that branch real rather than theoretical, and names
it `pantry_stock_unreadable`. The **recipe index is unaffected**, so `#/recipe/{name}` still renders a note's steps
and cooking history; only the stock-derived chip colours are unavailable, and
the provenance view says so explicitly rather than showing an empty chip row that
could be mistaken for "nothing in stock".

**Stock is derived per response, never stored.** The `in_stock` column that an
early draft of §7.3 proposed is deliberately **not** created. Stock is volatile —
the user toggles a task from Obsidian while the PWA is open — so persisting it
would make the chip colour wrong and would give the app denormalized state that
can silently go stale. For the same reason the Stock Join is **not** a table:
it is recomputed on every `stock_cache_seconds` refresh, and the only thing
persisted is the hand-written override file, which is reviewed source.

#### 9.11.4 `CookingLogWriter` (`app/cooklog/writer.py`)

The one vault write. Full contract in §9.14.

### 9.12 Meal Shortlists (D3)

`app/shortlists/store.py`, `MealShortlistStore`:

- `list_all() -> dict[str, tuple[str, ...]]` — `{breakfast: (…), lunch: (…),
  dinner: (…)}`, each ordered by `position`. **All three keys are always
  present**, possibly empty. An empty list is `()` and renders as an invitation,
  never as an error.
- `add(slot, recipe_note)` — validates `slot` against the closed enum and
  `recipe_note` against the live recipe index. `INSERT OR IGNORE` (the unique
  index makes re-add a no-op). Returns the current list.
- `remove(slot, recipe_note)` — `DELETE`, then re-sequences `position` in the same
  transaction so the invariant `position ∈ [0, n)` holds.
- `reorder(slot, order)` — one `BEGIN IMMEDIATE` transaction. Validates that
  `set(order)` equals the current membership **exactly**; a reorder that drops or
  invents a member is a 422, not a silent reconciliation. Rewrites `position` for
  the slot. Bounded at 50 rows.

A shortlist row whose `recipe_note` no longer resolves in the index renders as a
dimmed `⚠ 已重命名` row — D3's drift made visible — and is **not** silently
dropped. Dropping user data on a rename is worse than showing it broken; a
`DELETE` is the only thing that removes it.

Interactions, under the §2 touch rules:

- **Add** — a `+ 早餐 / + 午餐 / + 晚餐` control on the Recipe detail view. One
  tap, no dialog.
- **Remove** — an `×` on the shortlist row (44px target), with a 5s `UNDO` toast
  (FAB-anchored, Pattern B).
- **Reorder** — long-press drag with pointer events, or explicit `▲`/`▼` stepper
  buttons on each row. The stepper is the accessible, testable path and is what
  Playwright drives; drag is progressive enhancement over it. Both call the same
  `PUT /api/shortlists/{slot}/order`.

Nothing about a Meal Shortlist is written to the vault, and no Cooking Record is
grouped by slot.

### 9.13 The honest match display (D4)

#### 9.13.1 The headline line

One line per Recipe, above the chip row. **These four strings are exact and are
frozen by `tests/js/logic/format.test.mjs` (§10.4) and by the browser flow
(§10.5 step 1):**

```
4/6 ingredients found — missing: 香菇, 娃娃菜
6/6 ingredients found
0/3 ingredients found — missing: 空心菜, 香菇, 娃娃菜
2/5 ingredients found — missing: Clam, 香菇   (严格模式（含调料）: 生抽)
```

**The denominator is always the number of names in the list, never a free
literal.** The rules below make the missing list **one entry per missing
`材料` (or, under strict mode, per missing `调料`) slot**, and the denominator
counts exactly the slots the numerator counted, so a `0/6` line must carry six
names and a `0/3` line carries three. The third example above is therefore the
`0/3` form, which is the smallest internally consistent rendering of "nothing
found": three missing Materials over a three-Material recipe. The six-slot
rendering of the same rule —
`0/6 ingredients found — missing: 空心菜, 香菇, 娃娃菜, 茼蒿, 红苋菜, 开心果酱` —
is equally producible, and `tests/js/logic/format.test.mjs` freezes **both**.
An earlier draft of this section showed the three-name list under a `0/6`
denominator; that string is unproducible by the rule set, which is the same
defect class as the D4 `生抽` example that F17 already corrected once, and it is
corrected here for the same reason. **The code in
`app/static/js/logic/format.js` is the authority** and was never changed to
match the bad string; this section was aligned to the code.

Note the shape of the last one, and that it is the F17 correction. The
**Materials-only** missing list and the **strict-mode addendum** are two
separately-labelled clauses; the addendum is where a Seasoning may legitimately
appear, and it never appears in the first clause.

Rules, precisely:

- Numerator and denominator count **`材料` (Ingredients) only** in the default
  view. **Locked as F17.**
- **The numerator counts open Pantry Stock, not resolutions.** A `材料` that
  resolved to a Pantry Item with no open line in `Pantry.md` is **missing**: it
  is in the denominator and named in the missing list, and its chip is
  `chip--have-been-buying` (bought before, not held now). See the stock-score
  amendment in **§9.13.2** — F1 built the two stock tiers, and this is where they
  reach the number. A `manual` slot is exempt: the ladder answers a hand fix
  before it consults stock, so a mapped ingredient counts as found whether or not
  anything is on the shelf.
- In `严格模式（含调料）` both numerator and denominator additionally count
  `调料` (Seasonings). A Seasoning satisfied by `staples.yaml` **still counts
  as found**; only an *unresolved* Seasoning counts as missing. **Locked as
  F16** — and the empirical reason is not a preference: Pantry Category `1.1c`
  has exactly **1 row in 178**, so if a staple-satisfied Seasoning counted as
  missing, strict mode would drive **every** recipe's score to near-zero and the
  toggle would be useless. Strict mode then means "Seasonings I could not match at
  all", which is a meaningful pessimistic view.
- Cooking Tools are displayed but never scored. They are requirements, not stock
  — `CONTEXT.md` is explicit that a tool is not shoppable and not
  inventory-tracked.
- The missing list uses `parsed_name` where available, falling back to
  `raw_value`, joined with `, ` in slot order. It is **not** truncated:
  truncating hides the exact thing the user needs to buy. Long lists wrap.

**No `cookable` boolean is ever published (F17, locked).** Not in the list
response, not in the detail response, not under `?strict=1`. `CONTEXT.md`'s
boolean **Cookable** stays defined-but-unrendered (§5.1) and is deliberately not
amended away. The correction this decision records: the D4 example text
(`4/6 ingredients found — missing: 生抽`) listed `生抽` — a **Seasoning** — in a
**Materials-only** missing list, which **the rule set cannot produce**, because
`调料` is excluded from both numerator and denominator in the default view and is
only *added* under strict mode. The spec uses the internally consistent form
above. **The user reviewed this correction and accepted it.**

#### 9.13.2 Chip rendering

`app/static/js/components/chips.js` renders one chip per Ingredient and per
Seasoning. Chip order is slot order — never sorted, because the recipe's own
order is information. Each chip carries `data-match-method`, `data-tier`, and
`data-item-id` attributes, so the provenance view is a DOM concern rather than a
second data path.

`app/static/js/logic/chip-class.js` is pure and `node --test`ed:

```js
// Decision-dense: the five buckets of D4, in resolution order.
// `missing: !inStock` on the catalog branch and the `outOfStock` flag are the
// **stock-score amendment**; see the note under the code block.
export function chipClass({ matchMethod, pantryItemId, inStock, isSeasoning, strict }) {
  if (matchMethod === 'manual')
    return {
      className: inStock ? 'chip--in-stock chip--manual' : 'chip--have-been-buying chip--manual',
      missing: false, assumed: false, outOfStock: false,
    };
  if (matchMethod === 'staples')
    return { className: 'chip--assumed-staple', missing: false, assumed: true, outOfStock: false };
  if (pantryItemId !== null && pantryItemId !== undefined)
    return {
      className: inStock ? 'chip--in-stock' : 'chip--have-been-buying',
      missing: !inStock, assumed: false, outOfStock: !inStock,
    };
  if (isSeasoning && !strict)
    return { className: 'chip--ignored-seasoning', missing: false, assumed: true, outOfStock: false };
  return { className: 'chip--missing', missing: true, assumed: false, outOfStock: false };
}
```

**The stock-score amendment: a catalog match with no open line is `missing`.**
The block above is the ladder as first specified, and the one line that has since
changed is the catalog branch's `missing`, from `false` to `!inStock`. D4's
`have-been-buying` tier was drawn as a **colour** while the headline counted
**resolutions**, and those are different facts: the Pantry Item catalog is an
immutable record of what was *bought*, so a `茼蒿` row in `pantry_items.db` says
the household once bought it and nothing at all about the shelf. The result was a
list that published `2/2 ingredients found` for `煮菜菜` above a purple `茼蒿`
chip — a recipe the app called cookable while displaying the evidence that it is
not, on the one surface whose whole job is to answer "can I cook this tonight".

This is not a relaxation of F1, it is F1 reaching the score: §5.1 defines
Cookable as matching every Ingredient against **available Pantry Stock**, and
`CONTEXT.md` is explicit that Pantry Item ≠ Pantry Stock. F1 built the two stock
tiers so the distinction would be *real*; this is where the distinction arrives in
the number. Four consequences, all deliberate:

* **`manual` is exempt, and must stay so.** A hand fix answers before the stock
  tiers are consulted, so a mapped-but-unheld ingredient still counts as found. A
  catalog match is evidence; a hand fix is a decision, and the app does not
  overrule the user with a shopping list.
* **`staples` is untouched.** An assumed staple is assumed on hand in both modes,
  which is F16's whole argument for keeping the 严格模式 toggle useful.
* **Under `严格模式` an unheld Seasoning is missing too.** The 调料 rule decides
  *which slots are counted*; it does not make an absent jar present.
* **The server's `_is_missing` moved with it.** `app/api/recipes.py` publishes
  `found` / `total` and the client renders the string from the same slots, so the
  two implementations of this five-branch rule had to change together. The browser
  flow is what proves they did: it compares the painted `data-found` against the
  missing names in the headline, and a server that had been left behind would have
  published `3/4` above `missing: 蒸鱼豉油, 面条`.

`outOfStock` is new and is **not** a sixth bucket. It is the reason, kept beside
the verdict, so a consumer can tell "a Pantry Item exists and nothing is on the
shelf" (`chip--have-been-buying`, `outOfStock: true`) from "nothing was ever
resolved" (`chip--missing`). Both are `missing: true`; only the first carries the
flag, and it rides on the chip as `data-out-of-stock` for §9.13.4's provenance
view. The five colour buckets, the ladder's branch order, and the `manual` /
`staples` precedence are all unchanged.

#### 9.13.3 Sort order

`app/static/js/logic/sort.js`, pure and `node --test`ed:

1. `foundRatio` descending, where `foundRatio = found / max(total, 1)`.
2. `lastCooked` descending (`last_cooked` frontmatter; recipes with no
   `last_cooked` sort last within their ratio bucket).
3. `noteName` ascending, code-point order — a total order, so the list is
   deterministic and Playwright can assert on it.

No score threshold, no collapse, no "show more". A `0/6` recipe is a first-class
row.

#### 9.13.4 The debug provenance toggle

`设置 → 调试` in `app/static/js/views/settings.js`, persisted in `localStorage`
under `pantry-recipes:debug` — not in SQLite, because it is a pure render concern
and must not require a round-trip.

When on, every chip renders a secondary line with
`tier · match_method · #id · confidence`, and `#/provenance` shows a full table:
one row per Ingredient slot across all recipes with `raw_value`, `parsed_name`,
`parse_method`, `match_tier`, `match_method`, `pantry_item_id`, `confidence`, and
the rejected `candidates_json`. That view also hosts the two repair actions
(§9.10) — "重新解析未匹配项" and the per-row manual re-map picker, which searches
`GET /api/pantry/items?q=` and writes through the manual endpoint.

**The view also hosts F1's Stock Join table**, and this is not optional — F1's
join is measurably the weakest link in the app (2 misses in 46 open lines), so
the only thing standing between it and silent wrongness is that the misses are
visible. It lists every open `Pantry.md` line with its raw text, its normalized
product core, and its `stockJoinState ∈ {joined, override, unresolved}` plus
`pantryItemIds` when joined. The `unresolved` rows are the actionable ones, and
**what an unresolved row names is the `overrideKey` to add to
`app/pantry/line_overrides.yaml` — the folded key, and only the key. The
`canonical_name` beside it is the operator's to supply.** The row is still enough
to act on: the key is the string a paste must reproduce, because
`load_line_overrides` refuses to boot on any key that is not already
`fold_name`-normalized, so a raw `Pantry.md` line pasted as a key produces an
entry that can never fire. Repairing a miss is therefore a one-line reviewed
source edit rather than a database write or — far worse — an edit to the user's
vault.

**The server does not publish a guessed `canonical_name`, and that is the
contract rather than a gap.** The join is exactly three tiers (exact name →
basename → `line_overrides.yaml`) with no fourth and no re-resolution pass, so
for a line no tier explained there is no candidate value the server could name
without inventing it. A miss is *by definition* a row the ladder could not
resolve, so naming a value for it would be a fourth tier wearing a disguise — and
a confidently wrong `canonical_name` is strictly worse than an honest miss,
because it converts a visible gap into an invisible bad chip. `overrideName` is
therefore `null` on a plain miss, and `repairHint` says so in as many words
(`服务器不替你猜这个值` — the server will not guess this value for you; there is no
fourth rung on the ladder to guess it from).

**Where the server does have a name, it reports that name verbatim.** An override
that *fired* and still did not resolve is a different failure from a line no tier
touched: the operator already wrote an entry and it is stale — mistyped, or the
product was renamed by a re-ingest. `StockJoinMiss.override` carries that value,
`overrideName` publishes it, and `repairHint` is the other sentence: change the
value of key `{key}` to a `canonical_name` that really exists in the live
catalog, or delete the line. That is why the field is nullable rather than
absent: `null` means "no name was tried, and none should be", while a string
means "this is the name that was tried, and it is wrong". On a **resolved** row
`overrideName` is `null` on every hit — a hit's repair value is a fact about a
file the client has no business reading, and a tier-3 hit already says `override`
in its `stockJoinState` and names its own `overrideKey`.

`joined` rows resolved to a **duplicate** catalog name (D1's 5-row set) are
shown in `pantryItemIds` with **both** candidate ids, so the ambiguity is
visible before it becomes a wrong chip.

**F7, locked:** the API **always** returns the provenance fields on every recipe
response (a 16-recipe list is a few kilobytes), so toggling 调试 is instant and
cannot race. Single-user, private, loopback — the fields are not sensitive. There
is **no** `?debug=1` route, **no** reduced response shape, and **no** second
response to drift: the toggle is a render switch over `localStorage`, full stop.
The reason is F1's own acknowledged weakness — the stock join is the app's
weakest link and its only defence is that the misses are visible, and a toggle
that needs a round-trip is a toggle the user will not reach.

**F2, locked:** the Stock Join table is also where a **duplicate-slot conflict**
is surfaced. A recipe whose two `材料` both resolved to the same Pantry Item has
its second slot downgraded to `unresolved` with the conflicting candidate in its
`candidates_json` (§9.10.1), and this table shows it as a normal `unresolved` row
**with the conflict reason attached** — the duplicate is *shown*, never hidden.

### 9.14 The Cooking Log write (D2)

`app/cooklog/writer.py`.
`CookingLogWriter.append(recipe_note, log_date, base_revision=None, client_id=None)
-> CookLogResult`.

**Path policy — server-owned root; the browser supplies only a date.** Mirror
`pwa-obsidian-daily/app/vault/daily_paths.py`:

```python
class DailyNotePathPolicy:
    def resolve(self, requested_date: str) -> str:
        # date.fromisoformat; reject unless parsed.isoformat() == requested_date
        return f"{self.root}/{parsed.year:04d}/{requested_date}.md"
```

The browser sends `{"recipeNote": "盐焗鸡", "date": "2026-09-27"}` and the server
derives `日记/2026/2026-09-27.md` from `DAILY_NOTES_ROOT`. **No request field
reaches a path.** `daily_notes_year_policy` bounds the year; a date outside it is
422 `date_outside_configured_scope`.

**The read-modify-write, using the ported hardened primitives only:**

1. `store.read_existing_if_exists(relative)` through `AtomicNoteStore` — a
   descriptor-pinned, no-follow read that normalizes transient descriptor races
   to a typed `PathSafetyError`. `None` means absent → step 1a.
   1a. **The note is absent: the F4 error path, never a creation.** Re-check
   **once** (F4 step 1); if the note is now present, return 409
   `daily_note_created_concurrently`; if it is still absent after two reads,
   return 404 `daily_note_missing` with `message`, `date`, `relativePath`, and
   `retryable: true`. Full contract in §9.15. The PWA does not create the note
   and does not invoke any external process to create it.
2. `parse_sections(source)` → `require_unique("笔记", level=None,
   code="ambiguous_notes_section")`. More than one `笔记` region **fails closed**
   with 409 `ambiguous_notes_section` — the vault's own `sectionUpsert` is
   first-match-wins substring matching (`lines[i].includes(keyword)`) and the PWA
   must not replicate that, per `sections.py`'s own docstring. A note with **no**
   `笔记` heading at all is 422 `notes_section_missing`, never a silent heading
   insertion.
3. **Dedupe.** Scan the **entire** note source for an existing wikilink to this
   recipe. The check is a whole-note scan, not a region check, because the vault
   already places logged cooks in two other regions than the one this spec writes
   to (§3, F3: `2026-03-10` and `2026-09-14`). The predicate mirrors
   `recipeTracker.md`'s own match — an outlink whose `path.includes(recipe)` or
   `display === recipe` — implemented as a regex over the source:
   `\[\[[^\]]*\|?\s*<escaped-recipe>\s*\]\]`. A hit returns
   `CookLogResult(status="duplicate", wrote=False)` with the current
   `noteRevision`, HTTP 200, and **no write**.
4. **CAS.** If `base_revision` was supplied and
   `"sha256:" + sha256(source) != base_revision`, return 409
   `daily_note_changed` with `{requestId, code, currentRevision}`. The client
   refetches and re-offers. This is Part 5a **set semantics**: a Cooking Log is a
   statement about a date, not a delta, so a blind retry would be wrong.
5. **The patch.** A pure function
   `append_cook_link(source: bytes, region: Region, link_line: bytes) -> bytes`
   that splices `source[:insert_at] + link_line + terminator + source[insert_at:]`
   and touches **nothing else**, where `terminator` is the line terminator read
   from `source` at the insertion point — **not** a hard-coded `b"\n"`, for the
   measured reason in F3 step 5. This is the same span discipline as
   `frontmatter.py`'s `render()` and the ported `pantry.py`'s `_set_status` — byte
   spans into the original `source`, never a re-serialization. `link_line` is
   `- [[<recipe_note>]]`: a Markdown **list item**, no timestamp, no emoji, no
   task box, no trailing metadata. The exact `insert_at`, the `blank=True`
   empty-region handling, the terminator rule, and the "the heading already
   exists, nothing is created" clarification are all specified in **F3**; this
   step references F3 rather than restating it. `recipeTracker` counts *pages*,
   so decoration would only pollute the user's daily note.
6. **Commit** via `AtomicNoteStore.transform_existing(relative, transform)`,
   which already provides: a per-relative `flock` plus an in-process lock; a
   `_same_file` identity check (dev/ino/size/mtime_ns) **before** the replace; a
   timestamped backup into the recovery root with `backup_count=10`; an `O_EXCL`
   temp file in the target directory at the original mode; `os.replace`; a
   directory `fsync`; a **post-write read-back verification** that raises
   `PostWriteVerificationError` on mismatch; and temp cleanup in a `finally`.
   `ConcurrentFileChange` maps to 409 `daily_note_changed`.

   **This is why the write primitive is not re-invented.** Descriptor-pinned
   traversal means a symlink swapped in mid-write cannot redirect the write out of
   the vault. The identity check means a daily note that `task-date-recorder`
   rewrote between our read and our replace is caught. The backup means a
   post-write verification failure is recoverable. The read-back means a silent
   partial write is impossible.
7. **Receipt.** Insert into `cook_log_receipts`.
   `ux_cook_log_receipts_recipe_date` makes a second log of the same recipe on
   the same date a constraint violation, which is caught and returned as
   `status="duplicate"` — so even a concurrent double-submit cannot write the
   note twice.
8. `recipe_tracker_synced` **is not written by this path and no longer drives any
   surface.** It was specified here to be "flipped by a **read-path** comparison"
   — when a recipe is loaded and `frontmatter.last_cooked >=
   max(receipt.log_date)`, update the row — and that flip was never implemented,
   so the column sat at its `DEFAULT 0` forever and the `待 Obsidian 同步` badge
   was permanently on for every recipe ever logged through the PWA.

   The comparison is now made where it is actually needed and it is made
   **without a write**: `GET /api/recipes/{note_name}` publishes
   **`pendingCookDates`**, the logged dates strictly after `last_cooked`
   (`app/api/recipes.py::_pending_cook_dates`). It is the exact complement of the
   rule written above, so the two agree by construction, and it publishes the
   **dates** rather than a boolean, so "how far behind" is answerable and not
   only "is it behind". Two details are load-bearing: a `NULL` `last_cooked`
   makes *every* logged date pending (the tracker has counted nothing, and an
   empty list there would assert a sync that never happened), and the comparison
   is `>` not `>=`, because a cook on the same day as `last_cooked` is already
   counted and `>=` would report a permanently pending cook on the recipe most
   likely to be current.

   The column is **kept and left at 0** rather than dropped: it is a live table on
   a deployed service and removing it is a SQLite table rebuild, which is a
   separate, separately-authorised change. It is inert — no route reads it and no
   code writes it — and the badge reads `pendingCookDates` instead. Nothing may
   reintroduce a read of it, because the value it holds is not a fact about the
   tracker; it is the absence of an implementation.

**Surgical-patch discipline is a hard requirement, not a preference.** The
`task-date-recorder` plugin runs `app.fileManager.processFrontMatter()` and
`app.vault.modify()` on daily notes on a debounced `modify` event, and
`recipeTracker` runs `processFrontMatter` on recipe notes. A full re-dump of a
daily note would rewrite `modified_at`, disturb the `INPUT[toggle(...)]` inline
fields, and potentially reorder frontmatter keys. Step 5 is a byte splice;
nothing else is permitted.

**Placement is F3, and it is locked.** The `笔记` region computed by
`parse_sections()` in the live `日记/2026/2026-09-27.md` is only the heading line
— byte span `4265..4275`, content `b'\n'`, `blank=True` — because the next line
is a ` ````columns ` fence. The insertion point is the region's **`heading_end`**
offset, not its `end` offset, so the link lands **between `# 笔记` and the columns
fence**. That is valid Markdown, `recipeTracker` finds it (it scans every outlink
on the page, so position is irrelevant to it), and the previous draft's
"insert after the `![[dailyModify.base|ordered-list]]` embed" preference is
**withdrawn**. The full algorithm, the blank-line handling, the confirmation that
the heading already exists so nothing is created, and the withdrawn-preference
rationale are all in **F3**. `tests/cooklog/test_writer.py` and
`tests/vault/test_sections.py` assert the F3 byte layout directly.

**Conflict UX (§5a, set semantics).** A 409 shows a resolve panel with the
PWA's intent and the current server state side by side, and any follow-up write
carries the fresh revision. It never retries the same stale revision — that is the
"silent 409 loop" the pattern exists to prevent.

**Watchdog (§5b).** The log button is disabled in flight, and that disable is
paired with the vendored `unstickOnTimeout` helper
(`/js/pwa/unstick-on-timeout.js?v=__APP_VERSION__`) whose `clear()` is called in a
`finally`. A static-contract test asserts every `.disabled = true` site in
`app/static/js/**` is paired with the helper — the same coverage
`pwa-obsidian-daily` has.

### 9.15 A missing daily note is an error, not a creation (F4, locked)

This section replaces the Obsidian CLI / QuickAdd creation design entirely. See
**F4** in §3 for the decision, its rationale, and what was removed.

**There is no creation code.** No `app/vault/obsidian_cli.py`, no subprocess,
no `OBSIDIAN_CLI_EXECUTABLE`, no `TV_SYNC_COMMAND_ID`, no QuickAdd choice, no
Templater settle window, no `CreationIdempotencyStore`, and no idempotency
ledger table. `AtomicNoteStore.create_new` and `create_directory` are **not**
ported for this purpose and the daily-note path never calls them. The only
primitive the Cooking Log uses is `transform_existing`, on a file already
present.

**The error contract.** `read_existing_if_exists` returning `None` is the only
trigger.

| Step | Action | Result |
|---|---|---|
| 1 | `store.read_existing_if_exists(relative)` | `bytes` → proceed with the write |
| 2 | On `None`: re-resolve the path and re-read, **once**, bounded at 2 attempts, no sleep loop | `bytes` → the note appeared under us |
| 3 | `bytes` on the second read | **409 `daily_note_created_concurrently`** with `relativePath`, `currentRevision`, `retryable: true` |
| 4 | `None` on both reads | **404 `daily_note_missing`** with `message`, `date`, `relativePath`, `retryable: true` |
| 5 | `transform_existing` raises `ConcurrentFileChange` because the target vanished before replace | **409 `daily_note_changed`** — the existing code, *not* `daily_note_missing` |

Steps 2–4 are the **concurrent-external-writer rule** from `atomic_write.py`,
where the write primitive never assumes it is the only writer. The consequence
is the important part: **`daily_note_missing` means the note is absent, full
stop.** A note that exists in the vault but that the app saw missing — iCloud
sync lag, an Obsidian write landing between reads, a briefly stale network
volume — is a `409`, never a `404`. Reporting a lost race as "not found" would
send the user to create a note that already exists, and the second attempt would
then fail differently, which is a confusing two-error round trip for what is
really a millisecond race.

The `409 daily_note_created_concurrently` **reuses §9.14's existing 409 resolve
panel** rather than introducing a second conflict UI. The user taps retry; the
write then proceeds normally because a note exists.

**The envelope extension.** The scaffold's error envelope is
`{"requestId", "code"}`. Two codes add optional fields, and the change is
additive so no existing response changes shape:

```json
{
  "requestId": "…",
  "code": "daily_note_missing",
  "message": "找不到 2026-09-27 的日记：日记/2026/2026-09-27.md。请先在 Obsidian 中创建这一天的日记，然后重试。",
  "date": "2026-09-27",
  "relativePath": "日记/2026/2026-09-27.md",
  "retryable": true
}
```

The `message` is in Chinese because that is the app's user-facing language
throughout (`笔记`, `严格模式（含调料）`, `待 Obsidian 同步`, `⚠ 已重命名`). Gloss
for a reader of this spec: *"Cannot find the daily note for 2026-09-27:
日记/2026/2026-09-27.md. Create that day's note in Obsidian first, then retry."*

- `message` names **both** the human date and the exact vault-relative path, so
  the fix is one paste into Obsidian's quick switcher. The UI renders `message`
  **verbatim** and synthesizes no copy of its own, so there is exactly one
  wording to keep correct. A localization change is a one-line change.
- `relativePath` is **vault-relative**, never absolute, so the Server-Owned Root
  invariant survives and `/health` still leaks no path.
- `retryable: true` is literal: the identical request succeeds once the note
  exists. The client shows a retry affordance rather than a dead end.
- Every pre-existing error code keeps emitting exactly `{"requestId", "code"}`.
  `message`, `date`, `relativePath`, and `retryable` default to absent.

**`GET /api/cook-logs?date=` returns the same 404** with the same shape for a
missing date. A read-back that returned `{"entries": []}` for a date whose note
does not exist would assert "you cooked nothing that day", which is a different
claim from "there is no record of that day" and is not one this app can support.

**Read-only mode ordering.** `OBSIDIAN_READ_ONLY=true` yields 403 `read_only`
**before** any note-existence check (§9.16's guard order), so a read-only
deployment cannot be used to probe which daily notes exist by comparing 403
against 404.

**What is deliberately gone, so a future reader does not "restore" it.** The
removed design had a direct argument in its favour — `pwa-obsidian-daily` already
implements it, and back-dating into an un-journalled day is a real use case. It
is still the wrong call here, for reasons that are specific to *this* repo:

1. It contradicted `AGENTS.md` non-negotiable #1 and required amending that
   file, plus `README.md` and `.env.example`, to narrow the claim. F4 keeps the
   non-negotiable **correct as written**, which is worth more than the feature.
2. It added a subprocess sandbox, a Templater settle window, a
   unusable-note rollback, and a UUID idempotency ledger — roughly ten times the
   surface area — to handle the case where the user has not written that day's
   note, which is exactly when they should be writing it.
3. Its fatal edge case was a half-expanded frontmatter left in a daily note,
   which poisons that date and makes every later read 409 until repaired by hand.
   The error path has no such state.

The cost that remains: back-dating into an un-journalled day needs one action in
Obsidian first. Accepted deliberately, and stated again in §15.

### 9.16 API contract

All `/api/*` responses are `Cache-Control: no-store` (the scaffold's
`cache_policy` middleware already does this, correctly exempting `/api/version`,
whose stronger headers the vendored `install_pwa_version` owns). All errors use
the scaffold's existing envelope: `{"requestId": ..., "code": ...}` plus
`X-Request-ID`. **Two** error codes add optional `message` / `date` /
`relativePath` / `retryable` fields — `daily_note_missing` and
`daily_note_created_concurrently` (§9.15) — and the extension is additive: every
other code still emits exactly `{"requestId", "code"}`.

| Method + path | Purpose | Success | Errors |
|---|---|---|---|
| `GET /api/version` | Existing convergence gate | `{"version": "v…"}` | — |
| `GET /api/session` | Identity, CSRF token, capability flags | `{"identity","csrfToken","version","readOnly","appTimezone"}` | 401 `identity_*` |
| `GET /api/pantry/items?q=&limit=` | Catalog search for the manual picker | `{"items":[{id,canonicalName,category,area,variants}],"catalogRevision"}` — **no `lastSeen`**, see the picker row's note below | 422 bad query |
| `GET /api/recipes?strict=0` | The list, with per-Ingredient provenance | `{"recipes":[…],"catalogRevision","stockRevision","strict","staleMappingCount","stockUnjoinedCount","skipped":N,"stockJoin":{…}}` | 503 `pantry_stock_unreadable` / `pantry_db_unreadable` |
| `GET /api/recipes/{note_name}` | One Recipe, full | `{"recipe":{…,"ingredients":[{index,rawValue,parsedName,parseMethod,matchMethod,matchTier,pantryItemId,confidence,candidatesJson,inStock,stockJoinState,isSeasoning}],"history":{…}}` — the ingredient list is **exact**, and carries no `chipClass`; see below | 404 `recipe_not_found`; 503 `pantry_stock_unreadable` |
| `POST /api/recipes/resolve` | Re-resolve unresolved and stale rows | `{"reconsidered":N,"resolved":N,"stillUnresolved":N,"staleReset":N,"duplicateSlotConflicts":N,"conflicts":[{recipeNote,ingredientIndex,rawValue,pantryItemId,canonicalName,reason}]}` | 409 while a resolve is in flight |
| `PUT /api/recipes/{note_name}/ingredients/{index}/mapping` | Manual re-map | `{"mapping":{…}}` | 404 / 422 / 403 `read_only` |
| `DELETE /api/recipes/{note_name}/ingredients/{index}/mapping` | Clear a manual row | `{"mapping":null}` | 404 |
| `GET /api/shortlists` | All three slots | `{"breakfast":[…],"lunch":[…],"dinner":[…]}` | — |
| `POST /api/shortlists/{slot}` | Add | `{"slot","items":[…]}` | 422 bad slot / unknown recipe |
| `DELETE /api/shortlists/{slot}/{note_name}` | Remove | `{"slot","items":[…]}` | 404 |
| `PUT /api/shortlists/{slot}/order` | Reorder | `{"slot","items":[…]}` | 422 membership mismatch |
| `GET /api/cook-logs?date=` | Read-back for a date | `{"date","relativePath","noteRevision","entries":[{recipeNote,writtenAt,trackerSynced}]}` | 422 bad date; **404 `daily_note_missing`** (same envelope extension, §9.15) |
| `POST /api/cook-logs` | The Cooking Log write | `201 {"status":"logged","relativePath","noteRevision"}` or `200 {"status":"duplicate",…}` | 404 `daily_note_missing`; 409 `daily_note_changed` / `daily_note_created_concurrently` / `ambiguous_notes_section`; 422 `notes_section_missing`; 413 body cap |

**F1's two new response fields.** `stockUnjoinedCount` on `GET /api/recipes` is
the count of open `Pantry.md` lines the Stock Join could not resolve to a
catalog row (F1). `stockJoinState` on each Ingredient is
`joined | override | unresolved` — the Stock Join's own state, distinct from
`matchMethod`, which describes the recipe→catalog resolution. Both exist so that
F1's acknowledged weak join is **visible** rather than silent, and both are what
the `调试` toggle renders.

**`stockJoin` and `skipped` — added after this table was locked, and the reason
the widening is safe.** Both arrived in later tickets, both were **additive**, and
every key that existed before them kept its exact shape: the list response is
still `recipes` + the six scalars, and the two tickets touched no existing row.
`tests/api/test_recipes_api.py::LIST_KEYS` pins the eight-key set exactly, so a
ninth key is a deliberate edit rather than a surprise.

- **`skipped: N`** — the number of recipe notes dropped for being unreadable
  (`RecipeSnapshot.skipped`). A bad note is **skipped and counted, never fatal**:
  one unreadable file must not blank the whole list. It is the same number
  `/health` publishes as `recipes.skipped`, and it is on the response rather than
  only in the debug view for the same reason `staleMappingCount` is (§7.3) — a
  note that silently stopped being indexed is a wrong answer, not a missing one.
- **`stockJoin: {…}`** — F1's **whole** per-line join table, materialized in the
  recipe response rather than fetched by a second route. It is a projection of
  the *same* `StockJoin` object that `stockUnjoinedCount` and every slot's
  `stockJoinState` were computed from, so the table cannot disagree with the
  count beside it: `{"lineCount","unjoinedCount","tierCounts":{"exact","basename","override"},"guidance","lines":[…]}`. `lines` is
  every open `Pantry.md` line in `line_index` order — the note's own order, so a
  row's position on screen is its position in the file — and a line is
  `{lineIndex,section,text,core,stockJoinState,tier,pantryItemIds,overrideKey,overrideName,repairHint}`
  whether it hit or missed, so the two row shapes differ only where the join's
  answer differs. `pantryItemIds` is a **list**, both candidates on a duplicate.
  `overrideName` is `null` on a plain miss and carries the stale value when an
  override fired and did not resolve; `repairHint` is a server-composed sentence
  and never `null` on a miss. See §9.13.4 for why the server names the key and
  refuses to name the value. The alternative — a `GET /api/stock-join` route —
  was rejected on the §9.13.4 ratio argument: the table is 45 rows against a
  16-recipe list, and a second request is a second thing that can drift from the
  first, which is the failure **F7** exists to prevent.

**Two rows above were corrected against the routes as they respond, and one row
was corrected twice.** The table is the first thing an implementer reads, so a
key that is named here and absent from the response is worse than a missing key:

- **`lastSeen` is not published on the picker row.** It was named here and is
  deliberately withheld: `last_price` / `first_seen` / `last_seen` /
  `order_count` are Pantry-Write Contract data owned by `wholefoods-to-pantry`,
  and this app is a read-only consumer of a sibling project's committed asset
  (`AGENTS.md` #4). `CatalogRow` does not even project them, so publishing one
  would need a second, wider reader over `items` — and would invite a caller to
  read purchase history as current stock, which is the Pantry Item / Pantry Stock
  conflation `CONTEXT.md` forbids. The ticket's non-goal ("emit `last_price`,
  `first_seen`, `last_seen`, or `order_count` in any response") is the tighter of
  the two constraints and wins. `area` **is** published, and is always `null`:
  `PantryCatalog` drops every row whose `area` is not NULL before building a
  `CatalogRow`, so publishing the key states a fact rather than hiding one.
  Recorded in `app/api/pantry.py` and pinned by
  `tests/api/test_pantry_api.py::ITEM_KEYS`.
- **An Ingredient carries no `chipClass`, and this is the design, not an
  omission.** `chipClass` is a **client-side** function
  (`app/static/js/logic/chip-class.js`), fed the raw facts — `matchMethod`,
  `pantryItemId`, `inStock`, `isSeasoning`, `strict` — all of which *are*
  published, and it decides a CSS class name. A server-rendered `chipClass`
  string would be a sixth place to change the answer on top of the ladder the
  server already duplicates in `_is_missing`, and it would have Python owning a
  CSS class name. So the slot publishes `inStock` — the raw fact the classifier
  consumes — and the browser derives the class. `candidatesJson` was also missing
  from this row even though the **F7** paragraph below requires it on every
  Ingredient of every recipe response; the row now matches that paragraph.
- **`conflicts` travels with `duplicateSlotConflicts`.** The resolve row listed
  the count without the entries, while **F2** below and
  `tests/api/test_recipes_api.py::REPORT_KEYS` both require the conflict to be
  *shown* and never merely counted.

**F2's `duplicateSlotConflicts` field.** `POST /api/recipes/resolve` returns the
count of slots downgraded by the per-slot `IntegrityError` catch (§9.10.1), and
the `conflicts` entries themselves travel beside it — a count without the rows
would satisfy the word "countable" and not the intent, which is that the
downgrade is *shown*. Each entry is
`{recipeNote, ingredientIndex, rawValue, pantryItemId, canonicalName, reason}`.
The count is the F2 degradation made countable, and a value > 0 is a data defect
in a user-authored recipe note, not an engine error. It is reported here so a
reviewer can see the downgrade is observable and not a silent drop.

**F7: provenance is unconditional — there is no second shape.** Every Ingredient
on every recipe response carries `matchTier`, `matchMethod`, `pantryItemId`,
`candidatesJson`, and `stockJoinState`. **There is no `?debug=1` route and no
reduced response.** The `调试` toggle is a render switch over `localStorage`
(§9.13.4). The reason is F1's own weakness: the stock join is the app's weakest
link and its only defence is visibility, and a toggle that needs a round-trip is
a toggle the user will not reach — while a second response shape is a second thing
that can drift from the first.

**F4's 503 is gone.** There is no `daily_note_creation_unavailable`, because
there is no creation service. A missing daily note is a **404**, not a 503: the
request is well-formed and the resource is absent, and 503 would wrongly imply
the server is temporarily unhealthy and invite a blind retry. The remaining 503s
on the read paths are F1's **fail-closed** `pantry_stock_unreadable` and the
catalog's `pantry_db_unreadable` — both genuinely "a source this app depends on
is unreadable", and both permanent until the user fixes the vault, which is why
the UI must not present them as retry-soon.

`{note_name}` is the **URL-encoded basename**, resolved against the in-memory
index. It is never joined to a path by the client and never trusted as one: the
router looks it up in the index and 404s if absent. This preserves the
Server-Owned Root invariant while still letting a request select *which recipe*.

`strict` is a query parameter, `0`/`1`, default `0`. **Locked as F8.** It is
stateless because the server computes the score; a persisted setting would need a
round-trip before the first paint and would create a second source of truth. The
render-cache key already includes `strict` (§9.3), which is what makes the
stateless form cheap rather than merely tidy.

`GET /api/recipes` takes **no** `limit`, `offset`, or cursor parameter and
returns every recipe. **Locked as F20** — correct at 16 notes; §13.12 records
the two ways F1 makes that more expensive than it looks, and why it is still the
right call.

`POST /api/cook-logs` accepts an optional `X-Client-Id` header, validated exactly
as `pwa-deals` does (trim, non-empty, bounded by `MAX_CLIENT_ID_LENGTH`) and
ignored when absent. Cook logs are **not** sent through the outbox (§9.18.1), so
the header is accepted only as defence-in-depth against a user-driven double
submit; the real dedupe guarantee is `cook_log_receipts` (§7.5).

### 9.17 Auth — from the scaffold's `TODO(implementation)` into a real implementation

Port `pwa-obsidian-daily/app/auth.py` into `app/auth.py`, keeping the **guard
order** verbatim, and adapt it to this app's route set:

```
host          → 400 invalid_host
identity      → 401 identity_missing | identity_invalid | identity_denied | identity_spoof
read_only     → 403 read_only                              [mutations only]
body size     → 413 request_too_large                      [mutations only; 1 MiB cap,
                                                            chunked rejected]
origin        → 403 origin_not_allowed                    [mutations, and a
                                                            foreign-origin OPTIONS]
content-type  → 415 unsupported_media_type                [mutations with a body]
CSRF          → 403 csrf_required | csrf_invalid          [mutations]
```

- **Identity.** Trusted-header mode (`TRUST_TAILSCALE_HEADERS=true`): the exact
  normalized `Tailscale-User-Login` compared byte-for-byte (after OWS trimming)
  against `TAILSCALE_OWNER_LOGIN` via `hmac.compare_digest`; missing → 401
  `identity_missing`; multiple, empty-after-trim, > 254 chars, or
  control-character values → 401 `identity_invalid`; well-formed but mismatched
  (including case differences) → 401 `identity_denied`. Development mode: **any**
  `Tailscale-User-Login` or `Tailscale-User-Name` header is rejected with 401
  `identity_spoof` regardless of value, and the effective identity is
  `DEV_IDENTITY` (falling back to `TAILSCALE_OWNER_LOGIN`).
- **Read-only mode.** `OBSIDIAN_READ_ONLY=true` rejects every `/api/*` mutation
  with 403 `read_only`, **after** identity and **before** every other mutation
  check, and never inspects a body. It cannot run before identity: a blocked actor
  must not be able to distinguish the read-only flag from the identity checks.
  Unlike `pwa-obsidian-daily` there is **no** write allowlist in read-only mode
  (**F18**, locked) — a read-only Pantry Recipes PWA that could still log a cook
  or edit a shortlist would be a confusing half-mode, and because F4 removed the
  creation service this gate is the **only** thing between a mutation and the
  vault, so it covers every write and the allowlist does not exist to go stale. `read_only` is checked
  **before** any note-existence check, so a read-only deployment cannot be used
  to probe which daily notes exist by comparing 403 against 404 (§9.15).
- **CSRF.** `secrets.token_urlsafe(32)` (43 characters), 3600 s TTL, a rolling
  window of 16 tokens so concurrent tabs do not race, bounded at 128 characters on
  input, cleared on process restart, issued by `GET /api/session`.
- **Body cap.** 1 MiB (1048576) on `/api/*`; `Transfer-Encoding: chunked` is
  rejected with 413. There is no upload route in this app, so there is no
  multipart exception.
- **Host.** `Host` must be the `PUBLIC_ORIGIN` host (with matching port) or a
  loopback / `localhost` host (any port); anything else is 400 `invalid_host`.
- **CSP and security headers** — copied verbatim from `pwa-obsidian-daily`'s
  `SECURITY_HEADERS`, unchanged:

  ```
  default-src 'self'; script-src 'self'; style-src 'self';
  img-src 'self' data:; font-src 'self'; connect-src 'self';
  manifest-src 'self'; worker-src 'self'; base-uri 'none';
  form-action 'none'; frame-ancestors 'none'; object-src 'none'
  ```

  plus `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, and
  `Referrer-Policy: no-referrer`.

  This is why `index.html` must stay inline-script-free: the version arrives via
  the `<meta name="app-version">` tag, not an inline
  `window.__APP_VERSION__ = …`. `tests/js/scaffold.test.mjs` already asserts
  exactly one `<script>` tag, with `type="module"` and
  `src="/js/main.js?v=__APP_VERSION__"`. The observidian-daily embed carve-out
  for Monthly's dashboard views is **not** ported; `frame-ancestors 'none'`
  stands.

- **Registration order.** `install_security_middleware()` is registered **after**
  the `cache_policy` middleware so that security runs **outermost**: a hostile
  `Host` must be rejected before any other middleware parses `request.url`
  (Starlette builds the URL from the `Host` header), and every rejection still
  carries `X-Request-ID` and `Cache-Control: no-store`. The scaffold's
  `TODO(implementation): install_security_middleware(application, runtime)` on
  line 62 of `app/main.py` becomes this call, placed after the `cache_policy`
  decorator on line 82.
- `main.js` sends `Origin` implicitly (the browser does it), sends the CSRF
  header on every mutation using the token fetched at boot, and refreshes that
  token once on a 401 `csrf_invalid`.
- `/health` stays **unauthenticated** and therefore must keep leaking no
  server-owned paths — the existing test asserts the three paths are absent from
  the body. New `/health` counters are counts, not paths.

### 9.18 Offline writes (template Part 6)

#### 9.18.1 Which mutations go through the outbox — and which do not

**This split is locked: F5.** The two lists below are exhaustive and together
they account for every mutation in the API table in §9.16.

**Through the outbox** (vendored `/js/pwa/outbox.js`, write-through, page-owned
replay, per §6b and §6c):

- Meal Shortlist add / remove / reorder. Cheap, PWA-owned, set-semantics mutations
  whose loss is annoying rather than harmful, and a kitchen is a plausible
  offline place. They get the `X-Client-Id` ledger (§9.18.3) so a replay is
  exactly-once.
- Manual Ingredient Mapping. Same reasoning, and it is an authoring action
  plausibly done offline while standing in front of the pantry.

**NOT through the outbox — the Cooking Log write.** This is a judgment call and
the reasoning is the point:

1. A Cooking Log is a CAS-guarded write to a file that **Obsidian mutates
   constantly**. `task-date-recorder` runs `processFrontMatter` +
   `vault.modify` on daily notes on a debounced `modify` event. An intent queued
   at 19:05 and replayed at 21:40 — the normal outbox shape, since the outbox has
   **no closed-app guarantee** (§6c) — carries a revision that is now stale by
   construction. It 409s, it parks, and the user is shown a resolve panel for a
   meal they already ate. That is a strictly worse outcome than a clear failure.
2. Delivery is at-least-once and the domain statement is *dated*. A replay could
   carry the frozen date and stay truthful, but combined with (1) it buys nothing.
3. The honest offline story: logging a cook requires the vault; the vault
   requires Obsidian; **if you are offline you are not cooking.** A shortlist edit
   works offline because the shortlist is PWA state with no external
   preconditions. A Cooking Log has a hard external precondition.
4. Frequency is roughly one write per cook. The outbox exists to make a
   *high-frequency* mutation durable under a *high-frequency* outage. Neither
   holds here.

So the cook-log button is **online-only and says so**. **Locked as F5.** It
renders disabled with `离线：需要连接后记录` when `navigator.onLine === false`,
and on a network-layer failure it surfaces a real error — it **never**
optimistically claims success. The watchdog (§9.14) covers a hang. User story 47
is the statement of this from the user's side; `tests/js/outbox_contract.test.mjs`
(§10.4) is the structural assertion that **no** cook-log intent is ever enqueued.

*If a future requirement demands offline cook logging*, the correct fix is **not**
the outbox. It is to make the write revision-tolerant: under
`AtomicNoteStore._locked`, re-read and **re-apply the same append** (Part 5a
delta semantics) with a bounded retry, because a deduped append is
idempotent-by-content. That is a change to the write primitive, not to the
transport. Recorded in §13.7 as a known gap rather than pre-built.

#### 9.18.2 Wiring

`main.js` creates one `createBrowserOutbox` with
`replay: (intent) => replayDomainIntent(intent)` (§6b). The app owns replay
because it knows the endpoint, the CSRF token, the revision, and the UI
semantics. `replayDomainIntent` switches on `intent.type`: `shortlist.add`,
`shortlist.remove`, `shortlist.reorder`, `mapping.set`, `mapping.clear`. The
outbox adapter already supplies the `online` / `focus` / `visibilitychange` /
interval / `{type:'flush-outbox'}` triggers and a serialized flush, and `sw.js`
already registers the `outbox` sync tag — **no `sw.js` change is needed for the
outbox**, only the `SHELL_ASSETS` additions for the new app modules.

Payloads stay small and JSON-serializable per §6a. There is no IndexedDB
migration need: the largest payload is a ≤ 50-element `order` array of basenames.

#### 9.18.3 `X-Client-Id` exactly-once server ledger

`pwa-deals`' `item_status_intents` shape, in `shortlist_intents` (§7.6). For a
mutation carrying `X-Client-Id`:

1. `BEGIN IMMEDIATE`.
2. `SELECT … FROM shortlist_intents WHERE client_id = ?`.
   - Hit with a matching `request_fingerprint` → `COMMIT` and return the stored
     `response_json` verbatim. **Exactly-once.**
   - Hit with a **different** fingerprint → `ROLLBACK` and 409
     `client_id_reused`. A key is bound to one intent; a client that reuses it for
     something else has a bug, and silently accepting would make the replay
     history unauditable.
3. Perform the mutation.
4. `INSERT` the intent row with
   `json.dumps(response, separators=(',',':'), sort_keys=True)`.
5. `COMMIT`, return the response.

`request_fingerprint` is a `sha256` over a canonical `json.dumps` of
`{"method", "slot", "target": recipe_note | "order", "order"}`. Deals compares the
aliased row fields instead; a fingerprint is simpler here and matches
`request_fingerprint()` in `pwa-obsidian-daily/app/creation/obsidian_cli.py`.

`shortlist_intents` is also created lazily by `ensure_shortlist_intents_table(conn)`
inside the mutation handler, exactly as deals does, so a direct ASGI test client
and a rolling deploy stay safe while the startup migration remains authoritative.

### 9.19 `main.py` wiring

- The `lifespan` opens `PantryCatalog` (read-only), `RecipeIndex`,
  `PantryStockIndex`, the `AtomicNoteStore`, `IngredientMappingStore`, and
  `MealShortlistStore`, and runs `init_db()`.
  Every one is closed in a `finally` — a descriptor leak must not outlive
  shutdown. The LaunchAgent's `SoftResourceLimits NumberOfFiles 8192` exists
  because launchd's default 256 is not the shell's `ulimit -n`, and a descriptor
  leak otherwise only appears in production. **There is no creation service in
  the lifespan** (F4): the previous draft listed `DailyNoteCreationService`
  here, and it does not exist.
- Route registration order is load-bearing and unchanged in shape
  (`app/main.py` docstring + `CLAUDE.md` § *Traps*):
  `install_pwa_version` → exception handlers → `cache_policy` middleware →
  `install_security_middleware` (**outermost**) → `/health` → `/` → `/sw.js` →
  `/manifest.webmanifest` → `/js/{path}` → **domain routers** →
  `/api/{unmatched_path:path}` 404 envelope → **static mount last**. A catch-all
  mount shadows anything registered after it.
- The `/api/{unmatched_path:path}` catch-all keeps its
  `{"requestId","code":"not_found"}` envelope, so an unknown `/api/*` never falls
  through to the static mount's HTML 404.
- `_api_error` is unchanged, **except** for the additive envelope extension in
  §9.15: an exception detail may carry optional `message`, `date`,
  `relativePath`, and `retryable`, all defaulting to absent, so every
  pre-existing code still emits exactly `{"requestId", "code"}`. `relativePath`
  is always vault-relative, so `/health` and the envelope both leak no absolute
  path. All other new error codes are raised by
  `HTTPException(status, detail=...)` from a route, which the existing
  `StarletteHTTPException` handler already maps to the envelope for `/api/*`.
- `/health` gains `pantry_db.rowCount`, `recipes.count`, `recipes.skipped`,
  `stock.openCount`, `stock.unjoinedLineCount`, and
  `mappings.unresolvedCount`, and continues to leak **no** paths.

### 9.20 Deferred integrations — named so they are not invented twice

- **`nutrition-intake/src/nutrition_intake/units.py` and `nutrients.py`** are
  zero-dependency, pure, and already tested. They are the right home for quantity
  parsing and unit conversion when quantities arrive. This spec does **not** use
  them and adds no dependency on that project. Named here so a future
  quantity ticket reaches for them instead of writing a new converter.
- **`wholefoods-to-pantry/references/pantry-write.md`** is the Pantry-Write
  Contract. This app is a *consumer* of stock that already satisfies it and must
  not re-implement the purchase-side writer. The only rule it must honour directly
  is the Pantry Unit per-unit-price rule, which it honours by **not** touching
  price at all: no recipe response carries `last_price`.

---

## 10. Testing Decisions

**Every testing decision that was flagged is now locked, and each one changes what
CI installs or what CI asserts:** F10 (opt-in `[browser]` extra), F14 (a frozen
committed snapshot, never a live sibling DB), F16 and F17 (exact expected
strings), and F2 (the duplicate-conflict test case). They are called out at the
point of specification below, not only here, so no test author has to read §3 to
know what to assert.

### 10.1 What makes a good test here

Test **external behavior**: the bytes written to a daily note, the JSON a route
returns, the pixels a browser paints. Do not test that a private helper was
called, do not test SQLite statement strings, and do not drive the tier ladder
through the API when the ladder is a pure function.

Three rules carried from the siblings:

- **A test that pins an expected value pins the decision, not the
  implementation.** The golden test (§10.3) is the anchor for the matcher.
- **Failing closed is a behavior worth asserting.** Every typed error
  (`PantryError`, `SectionError`, `PathSafetyError`, `ConcurrentFileChange`,
  `PostWriteVerificationError`, `ConfigurationError`, `DailyNotePathError`) needs
  a test that the API maps it to the right status, not merely that the function
  raises.
- **Races need hooks, not luck.** `AtomicNoteStore` takes a
  `race_hook: Callable[[str], None]`. Tests use it at `after_resolution`,
  `before_backup`, `after_temp`, and `before_replace` to inject a concurrent
  change and assert `ConcurrentFileChange`. Port the sibling's
  `test_atomic_write.py` approach rather than inventing a new one.

### 10.2 Python suite

`pyproject.toml` already sets `timeout = 120` and `timeout_method = "signal"`.
Keep both — 120 s is a hang detector, not a budget, and the signal method raises
*inside* the test so every `finally` runs, whereas the thread method hard-exits
the process and skips exactly that cleanup. CI runs `ruff check app tests scripts` →
`mypy app` → `pytest` → `npm test` → `npm run check` → wheel build. Add the four
icon files to the wheel-content check.

**CI's install line is unchanged: `pip install ".[test,dev]"`. F10, locked.**
`playwright` is **not** in `[test]` or `[dev]`, so the browser suite is opt-in and
needs `pip install ".[browser]" && playwright install chromium` (§10.5). A
`tests/` file that imports `playwright` is therefore **not** collected by the
default run and must not be imported at module scope from a conftest — it lives
under `tests/browser/` and skips cleanly when the extra is absent.
`tests/scaffold/test_app.py` asserts the dependency placement itself: `aiosqlite`
in `[project].dependencies`, exactly one `playwright` occurrence and it is inside
the `browser` extra (§6.1).

| File | Covers |
|---|---|
| `tests/scaffold/test_app.py` (extend) | The converge gate, `/js/{path}` injection, traversal rejection, the JSON 404 envelope, fail-closed config. Add: `/api/session` shape, the new `/health` counters, the security headers on every response, and **an assertion of the exact new-`Settings`-field set**, so that any field F4 removed is a test failure rather than a silent regression. The field names are enumerated once, in §6; this test asserts the set, it does not restate it. |
| `tests/scaffold/test_routes.py` (new) | Every route in §9.16 responds; `/api/does-not-exist` returns the envelope; `/js/../config.py` still 404s; `POST /api/cook-logs` with no CSRF token is 403. |
| `tests/db/test_migrations.py` (new) | `init_db()` is idempotent; `schema_migrations` records every version; a re-run is a no-op; the six pragmas are applied on every connection. |
| `tests/vault/test_atomic_write.py` (new) | Descriptor pinning, symlink refusal, CAS via `race_hook`, backup-then-replace, post-write verification failure and its `finally` cleanup, `ConcurrentFileExists`. |
| `tests/vault/test_frontmatter.py` (new) | `render()` leaves unrelated bytes byte-identical; a block list round-trips; a duplicate key fails closed. |
| `tests/vault/test_sections.py` (new) | The `笔记` region is the heading line in a real daily note (the `2026-09-27.md` shape, span `4265..4275`, content `b'\n'`, `blank=True` — a 9-byte heading line plus a 1-byte blank line, so the note is **pure LF**, not CRLF); `require_unique` raises on two; a note with no `笔记` heading raises the missing-section error rather than inserting one. Plus the terminator widths: a CRLF note is spliced with CRLF and **never gains a lone LF**, and a lone-CR note parses as its LF equivalent. |
| `tests/vault/test_daily_paths.py` (new) | `resolve("2026-09-27") == "日记/2026/2026-09-27.md"`; rejects `2026-9-27`, `../x`, an absolute path, and a year outside the policy. |
| `tests/vault/test_pantry_stock.py` (new) | Open vs done markers, from the ported parser: `[ ]` and `[/]` are open, `[x]`/`[X]` are done, `[-]`/`[>]`/unknown are **never** open; `derived_status` is `done` iff all children are done, `in_progress` if any child is done or in-progress, else `open`; `_ADDED`/`_ENDED` recover the `➕`/`✅`/`❌` dates; `禾苑 蟹粉鱼肉狮子头 冷冻 280 克` yields a product core that tier 4 finds at id 108; the `_MAX_ITEMS_TOTAL` and `max_bytes` bounds hold. |
| `tests/pantry/test_stock_join.py` (new) | **F1's join.** The exact-name tier, the tier-4 basename tier, and the `line_overrides.yaml` override tier, in order; the override is keyed by normalized line name and resolves a `canonical_name` to an id **at read time**, so a renumbered `items.id` does not break it; a duplicate catalog name surfaces both candidates; an unjoinable line lands in `unjoined` and **not** in `in_stock_ids`. |
| `tests/pantry/test_stock_math.py` (new) | **F1's per-unit price parity obligation.** The three shape cases: a `3/3` parent derives `done` and contributes nothing; a `1/2` parent with one open unit contributes exactly **one** unit price and the parent contributes zero; a `[/]` in-progress unit counts as open. Reproduces the frozen total in `tests/fixtures/pantry_stock_math_parity.json` exactly, including the contributor and excluded-parent counts. |
| `tests/recipes/test_ingredients.py` (new) | All five shapes; `🌶️` with and without `U+FE0F`; `🍋‍🟩` as **one** three-codepoint ZWJ run and **not** three separate emoji; `"[[Mackerel]]"`; bare `空心菜`; the 6 `材料` emoji-only values and the 3 `调料` ones; `raw` preserved byte-for-byte. |
| `tests/recipes/test_normalize.py` (new) | The strip order; `Wang Korea 有机去壳甘栗仁 60g*5 300 克` → `有机去壳甘栗仁`; `Mushroom Dried Morel Mushrooms` keeps `Mushroom` (not a lexicon brand); `柴米 蒜香蒸茄子 300 克` keeps `蒜香蒸茄子`; `#tag` and emoji date markers stripped. |
| `tests/recipes/test_matcher.py` (new) | Each tier in isolation, plus the ordering property: a value tier 2 can resolve is never resolved by tier 7. |
| `tests/recipes/test_must_not_match.py` (new) | **The six live rows from D1**, each asserted **individually**: `蒜`→72, `蒜`→110, `土豆`→62, `芝麻`→25, `芝麻`→102 all resolve to `unresolved`. **Plus a negative control per guard**, because the per-row table in D1 shows the guards are asymmetrically load-bearing: with the category-family guard disabled, `芝麻`→25 and `蒜`→110 are asserted to now resolve wrongly; with the segment-boundary guard disabled, `蒜`→72, `土豆`→62, and `芝麻`→102 are asserted to now resolve wrongly. A guard with no test that fails when it is deleted is not a guard. |
| `tests/recipes/test_golden_real_recipes.py` (new) | §10.3. |
| `tests/mapping/test_store.py` (new) | The inverted unique constraint and **F2's per-slot recovery**. Positive half: three recipes may map to id 83; one recipe may not map two slots to id 83. `manual` is immutable (F6); `set_manual` deletes + inserts; `resolve_all` skips `manual`; a stale id is detected and reset. **F2's duplicate-conflict case, which is the half that only exists because of the override:** build a fixture recipe whose two `材料` both resolve to the same Pantry Item, then assert (1) the **first** slot commits normally, (2) the **second** slot is downgraded to `match_method='unresolved'`, `match_tier=0`, `pantry_item_id=NULL`, `confidence=0.0`, (3) its `candidates_json` records the conflicting candidate with `reason: "duplicate_slot_conflict"`, (4) `ResolveReport.duplicateSlotConflicts == 1` and `POST /api/recipes/resolve` reports it, (5) **every other slot of that same recipe still resolved** and the whole table is committed — the transaction did not abort, (6) a second `resolve_all()` is idempotent on the downgraded slot. A **static-contract assertion** that the slot `UPDATE` carries neither `OR ROLLBACK` nor `OR FAIL`, because either would undo the transaction and silently reinstate the failure F2 exists to prevent. |
| `tests/shortlists/test_store.py` (new) | Add is idempotent; remove re-sequences; reorder rejects a membership mismatch; all three slots always present; an unknown slot is a 422; a renamed recipe renders as broken, not dropped. |
| `tests/cooklog/test_writer.py` (new) | The exact bytes appended, matching **F3**'s layout: `- [[<recipe>]]` as the first list item under `# 笔记`, with the region's `blank=True` empty-region case producing `# 笔记\n- [[…]]\n` and no extra blank line; the `noteRevision` CAS 409; a duplicate is a 200 with no write; two `笔记` regions fail closed; a note with **no** `笔记` heading is 422 and writes nothing; the append is **byte-identical everywhere else** (assert the pre-image minus the splice). |
| `tests/cooklog/test_missing_note.py` (new) | **F4.** 404 `daily_note_missing` with `message` naming both `2026-09-27` and `日记/2026/2026-09-27.md`, plus `date`, `relativePath`, `retryable: true`; a note that appears between the first and second read yields 409 `daily_note_created_concurrently`, **not** 404; the identical request succeeds once the note exists (the retryability claim is asserted, not assumed); `GET /api/cook-logs?date=` returns the same 404 for a missing date rather than an empty `entries`; `read_only` yields 403 **before** any existence check; every other error code still emits exactly `{"requestId","code"}`; and **no file is created** in any case. |
| `tests/api/test_auth.py` (new) | The guard **order** as a table: for each pair of failing guards, the earlier one wins. Both identity modes. `read_only` rejects before Origin and before any note-existence check. The 1 MiB cap. Chunked rejection. The CSRF lifecycle. Host rules. The CSP on every response. |
| `tests/api/test_recipes_api.py`, `test_shortlists_api.py`, `test_cooklog_api.py` (new) | Route contracts, status codes, and envelope shapes. |

### 10.3 The golden matcher test — the CI anchor for D1

`tests/recipes/test_golden_real_recipes.py` plus two fixtures.

**`tests/fixtures/real_recipes/*.md`** — the 16 real recipe notes, copied verbatim
at the time this spec was written. They are frozen data, not a dependency: CI must
never reach into the live vault.

**`tests/fixtures/pantry_items_snapshot.json`** — a frozen dump of all 178
`items` rows (`id`, `canonical_name`, `category`, `variants`, `area`) generated
once by `scripts/snapshot_pantry_catalog.py`. **Locked as F14: CI does not read
the live `wholefoods-to-pantry` database, ever.** The snapshot is regenerated
**deliberately, in its own commit**, when the catalog changes. The reason is
specific, not stylistic: reading the live sibling DB makes CI depend on another
repo's on-disk state and turns **every catalog re-import into a CI failure** — a
failure that is about the world rather than about this code, and which therefore
teaches everyone to ignore CI. F1 extends the same rule to the `💵` parity
fixture `tests/fixtures/pantry_stock_math_parity.json`, which is **frozen rather
than live-read for the same reason**, and which the regeneration script refuses to
refresh when the PWA's per-unit total and `Helper/scripts/pantry_snapshot.py`'s
total disagree (§10.3's closing note).

**`tests/fixtures/golden_match_results.json`** — the expected outcome for every
Ingredient and Seasoning slot of every recipe:

```json
{
  "generatedAt": "2026-09-27",
  "recipes": {
    "拌空心菜": {
      "ingredients": [
        {"index": 0, "rawValue": "空心菜", "parseMethod": "bare",
         "parsedName": "空心菜", "matchMethod": "normalized_exact",
         "pantryItemId": 83}
      ]
    },
    "Easy Fragrant Fried Rice": {
      "ingredients": [
        {"index": 0, "rawValue": "🥦", "parseMethod": "emoji_only",
         "parsedName": "西兰花", "matchMethod": "synonym", "pantryItemId": 10},
        {"index": 1, "rawValue": "🥚", "parseMethod": "emoji_only",
         "parsedName": "鸡蛋", "matchMethod": "unresolved", "pantryItemId": null}
      ]
    }
  }
}
```

The test runs `parse_recipe` + `resolve_ingredient` over the frozen fixtures and
asserts an exact per-slot match on `parseMethod`, `parsedName`, `matchMethod`,
and `pantryItemId`. **A change to the matcher that silently breaks
`空心菜 → 83` fails CI** — that is the entire purpose of this test, and the
`空心菜 → 83` assertion is called out by name in the ticket so a reviewer cannot
miss it.

`scripts/snapshot_pantry_catalog.py` regenerates both fixtures from the live vault
and catalog and prints the diff. Regeneration is a **deliberate act**: the diff is
reviewed, and a change in the `unresolved` count is called out in the commit
message. A silent regeneration is exactly how a golden test stops meaning
anything.

Secondary golden assertions, computed from the same fixtures:

- `unresolved` share of distinct `材料` values — currently **58%**.
- `staples`-satisfied share of distinct `调料` values — currently **~90% with**
  `staples.yaml` and **~5% without**.
- Exactly **3 of 16** recipes have every Ingredient resolved.
- `variant_alias` (tier 3) resolves **nothing** — the expected result, because 3
  of 178 catalog rows have a non-empty `variants` and two of those are just the
  long product name. **A near-zero tier-3 count is the assertion, not a
  regression.** Recorded so a future reader who sees 0/0 tier-3 coverage in the
  fixture does not "fix" it by loosening the tier, by widening the synonym set
  to compensate, or by deleting the tier. The tier exists because
  `CONTEXT.md` defines Pantry Item Alias in terms of `variants`.

These are the D1 evidence, frozen. An implementation that cannot reach them is
either wrong or requires a deliberate re-measurement of the evidence.

**The fixture is also where F2's constraint is proven *not* load-bearing.** The
golden fixture must record, per recipe, that **no recipe has two `材料` slots on
the same `pantry_item_id`** — the assertion behind §3's F2 evidence table. If a
regeneration ever produces a recipe where it *is* true, that is a new fact about
the vault, it must be called out in the regeneration commit message by name, and
F2's per-slot catch is what keeps the app honest about it rather than failing.

**What this golden test does not cover, and must not be read as covering.** It
exercises the **recipe→catalog** join only. It says nothing about F1's
**Stock Join** (pantry line → catalog row), which is a different join, has a
different and worse measured miss rate (2 in 46, not 1 in 23), and is asserted
separately in `tests/pantry/test_stock_join.py` and
`tests/pantry/test_stock_math.py`. Its fixture,
`tests/fixtures/pantry_stock_math_parity.json`, is regenerated by
`scripts/snapshot_pantry_catalog.py`, which additionally **refuses to emit it**
when the PWA's per-unit money total and `Helper/scripts/pantry_snapshot.py`'s
total for the same date disagree. That refusal is the mechanism that keeps the
three implementations of the `💵` per-unit math in parity, and it is the reason
F1's invariant is a test obligation rather than a comment.

### 10.4 JS suite — `node --test`, no dependencies

`npm test` runs `tests/js/*.test.mjs`; `npm run check` runs `node --check` on the
module-graph entry and the worker. Both stay dependency-free — no `npm install` is
required, which is the point.

| File | Covers |
|---|---|
| `tests/js/scaffold.test.mjs` (extend) | The existing boot contract, plus: every mutable asset URL version-pinned, every new ESM import carrying `?v=__APP_VERSION__`, no `serviceWorker` string in `main.js`. |
| `tests/js/shell_assets.test.mjs` (new) | **Closes the missing-entry hole**: walk `app/static/js/**` and `app/static/css/**`, exclude `js/pwa/`, assert every file is in `SHELL_ASSETS`. Also assert no `SHELL_ASSETS` entry lacks a file (the existing direction), that `CACHE_VERSION` matches `/^v\d+\.\d+\.\d+$/`, and that `NETWORK_ONLY_PREFIXES` still contains `'/api/'` and `NETWORK_ONLY_EXACT` still contains `'/health'`. |
| `tests/js/logic/sort.test.mjs` (new) | The three-level sort, determinism, and that a `0/6` recipe is never filtered out. |
| `tests/js/logic/chip-class.test.mjs` (new) | All five buckets, the `manual` outline, the strict/non-strict Seasoning split. |
| `tests/js/logic/format.test.mjs` (new) | **F16 and F17's exact expected strings, frozen.** The four headline forms in §9.13.1 **verbatim**, including the `6/6` case with no missing list, the `0/3` case (`0/3 ingredients found — missing: 空心菜, 香菇, 娃娃菜`) **and** the six-slot `0/6` case with the full untruncated list — the same rule at two slot counts, which is what proves the denominator tracks the slot count rather than being a literal — and the strict-mode form `2/5 ingredients found — missing: Clam, 香菇   (严格模式（含调料）: 生抽)` — asserted as an **exact string equality**, not a regex. Two F16-specific assertions: a `staples`-satisfied Seasoning (`生抽`) still counts as **found** under `strict: true`, and an `unresolved` Seasoning counts as missing under `strict: true` and is **excluded entirely** under `strict: false`. One F17-specific assertion: the Materials-only clause **never** contains a `调料` name under either mode — the exact correction the user accepted. |
| `tests/js/outbox_contract.test.mjs` (new) | The app's `replayDomainIntent` covers every enqueued `type`, sets `X-Client-Id` from `intent.clientId`, and **never** enqueues a cook-log intent. |

### 10.5 Browser suite — Playwright, two flows, **opt-in** (F10, locked)

**`playwright` lives in the optional `[browser]` extra and is deliberately NOT in
`[test]`** (§6.1), so CI's `pip install ".[test,dev]"` is unchanged and this suite
is **opt-in**: `pip install ".[browser]" && playwright install chromium`. A
default `pytest` run does not collect these files, and they must not be reachable
from a module-scope import in a conftest.

**Why opt-in rather than in CI, restated because the cost of being wrong here is
silent.** The two flows need a browser binary on the ubuntu runner, a recurring
CI cost, for two flows. But the failure that omitting them would cause is one
that **no unit or API test can produce**: step 4 below is a
`history.back()` + scroll-restore behaviour, and step 2 is whether pixels
actually repaint. A stale-paint false pass — a cached snapshot filling the
element with old text so `wait_for(selector)` returns too early — is precisely
what a JS-level test cannot see and precisely what the sibling's
`test_recipe_browse_flow.py` step 4 was written to catch. **Their input is a
`tmp_path` vault and a `tmp_path` `APP_DATA_DIR` (§10.5), so they are
deterministic**; opt-in costs nothing in flakiness, only in frequency.

Two flows only — the seam above them is covered, and a third flow is a
maintenance liability. Both run against a `tmp_path` vault and `tmp_path`
`APP_DATA_DIR`, never the real ones.

**`tests/browser/test_recipe_browse_flow.py`** — the D4 display contract:

1. Boot, then wait for **content**, not element visibility (§5d), and assert the
   first row's headline line **exactly** equals one of the four frozen forms in
   §9.13.1 (F17 — the `n/total` shape from `材料`, and never a `cookable`
   boolean anywhere in the payload).
2. Assert the chip row is present and that every chip carries a class from the
   five-bucket vocabulary.
3. Assert the sequence of `found/total` values is non-increasing, and that a
   `0/6` row is present and visible.
4. Enter `#/recipe/拌空心菜` from the list, press Back, and assert the URL is `#/`
   **and** the scroll offset is restored (§2i). **This step is the reason the
   suite exists** — it is structurally unreachable from any other seam.
5. Toggle `调试`, assert the tier text appears, reload, and assert the toggle
   persisted in `localStorage` — and assert **no additional request was made**,
   which is F7's render-only claim observed rather than assumed.

**`tests/browser/test_cook_log_flow.py`** — the D2 write contract:

1. Log a cook on today's date; assert the 201 `status: "logged"` and that the
   returned `relativePath` equals `日记/<year>/<today>.md`.
2. **Read the file from disk** and assert the exact line `- [[<recipe>]]` is
   present as the **first list item under the `# 笔记` heading** (F3), that the
   note's `modified_at` frontmatter and the
   `![[dailyModify.base|ordered-list]]` embed are byte-identical to the pre-image,
   and that the `INPUT[toggle(...)]` lines are untouched.
3. Log the same recipe again; assert `200 {"status":"duplicate"}` and that the
   file's `sha256` is **unchanged**.
4. Write a competing change into the note out of band, then log with a stale
   `baseRevision`; assert 409 `daily_note_changed` and that the file is unchanged.
5. Log into a date whose daily note does not exist; assert **404
   `daily_note_missing`**, that the response's `message` names both the date and
   `日记/<year>/<date>.md`, that `retryable` is `true`, and that **no file was
   created** — under F4 this is now unconditional rather than contingent on an
   unconfigured creation adapter, so it is the assertion that carries the whole
   decision. Then create the note in the fixture and assert the identical
   request now succeeds, which is what makes `retryable: true` a tested claim.
6. Exercise the race: have the fixture create the note between the server's
   first and second read; assert **409 `daily_note_created_concurrently`** and
   **not** `daily_note_missing`.

### 10.6 Type and lint scope

`mypy --strict` on `app`, and nothing else. CI enforces `mypy app` and
`pyproject.toml`'s `files` is `["app"]`, so the config, the documented commands
and the gate name one scope: a bare `mypy` in the repo root is the same run.
`tests/` and `scripts/` are outside it — `mypy app tests` is 119 pre-existing
errors in 21 files unrelated to this work, and `mypy scripts` is 2 errors in
`scripts/snapshot_pantry_catalog.py`; both were measured and both were left out
rather than adopted, because a config that advertises a scope nothing enforces
is the defect, not the errors. `ruff` is a separate decision and **is** wider
than `mypy`: `ruff check app tests scripts`. Every new module is fully annotated
— the vault primitives are `frozen=True` dataclasses with typed byte spans and
the new code matches. `ruff` with the existing
`select = ["E","F","I","UP","B"]` and `line-length = 100`.

---

## 11. Phased build order

One `/implement` ticket per phase. Each is sized for a fresh implementation
context given only the ticket, this spec, `AGENTS.md`, and `CONTEXT.md` — never
the design conversation (`idea-to-ship` §4).

```
P1  Config + auth middleware + GET /api/session
    + tests/js/shell_assets.test.mjs                    deps: —
P2  SQLite schema.sql + database.py + init_db            deps: —
P3  Vault read primitives: atomic_write, frontmatter,
    sections, daily_paths, pantry                        deps: —
P4  PantryCatalog + PantryStockIndex + the Stock Join
    + line_overrides + the per-unit money parity test     deps: P2, P3
P5  Ingredient parser + emoji dictionary + normalize      deps: —
P6  Tier ladder + staples.yaml + synonyms.yaml            deps: P4, P5
P7  IngredientMappingStore + re-resolve + manual
    override + the golden matcher test                   deps: P2, P6
    (F2's per-slot IntegrityError catch ships here)
P8  RecipeIndex + GET /api/recipes + /api/recipes/{name}
    + GET /api/pantry/items                              deps: P4, P7
P9  MealShortlistStore + /api/shortlists                 deps: P2, P8
P10 CookingLogWriter: path policy, CAS, F3 region splice,
    dedupe, the F4 missing-note error, POST+GET
    /api/cook-logs                                       deps: P3, P7
P11 Frontend: router, views, components, tokens, styles,
    the honest display, provenance toggle, the F1
    provenance Stock Join table                          deps: P8, P9
P12 Icons + install polish + SHELL_ASSETS + drift gate   deps: P11
P13 Offline outbox wiring + X-Client-Id ledger            deps: P9, P10
P14 Playwright browser tests (opt-in [browser] extra,
    F10 — NOT part of the default CI run)                 deps: P11, P10
P15 Deploy: launchd bootstrap, Tailscale Serve, converge deps: all
```

**F4 removed a phase and the list is renormalized.** The previous draft's `P11
Daily-note creation: obsidian_cli + idempotency` existed only to build the
Obsidian CLI / QuickAdd port, its nine `Settings` fields, the Templater settle
window, the rollback, and the idempotency ledger — all of which F4 removes. It
is gone, and every later phase shifts down by one: the old P12–P16 are now
**P11–P15**. There is no gap and no duplicate number.

**Two phases changed content, not just number.** `P1` gains
`tests/js/shell_assets.test.mjs`, because the missing-`SHELL_ASSETS`-entry hole
must be closed **before** the ~15 new frontend modules land in P11 — shipping the
test in the same phase as the modules it protects would already be too late if
any module were forgotten. `P4` gains the Stock Join, `line_overrides.yaml`, and
`tests/pantry/test_stock_math.py`, because F1's state layer, its join, and its
three-way money parity are one unit of work against one vault file; splitting
them would put a chip class in the frontend with nothing behind it.

**The dependency edges were re-checked after renormalization and remain coherent.**
`P4 → P6` still means "the tier ladder needs both the catalog universe and the
stock set". `P10` depends on `P3` (the write primitives) and `P7` (the mapping
store, for the receipt insert) and on neither `P9` nor `P11`. `P14`'s Playwright
flows need the frontend (`P11`) and the cook-log write (`P10`) and nothing else.
`P13`'s outbox needs the shortlists (`P9`) and the cook-log endpoint (`P10`, to
assert it is never enqueued) — that edge survives, and it is now **backed by a
locked decision rather than by an open flag**: **F5** is the decision that the
cook log is online-only and excluded from the outbox (§9.18.1), and this edge is
its test. F4 made the cook-log error path richer, which *reinforces* F5 rather
than relaxing it — an offline cook log would also have to decide what to do about
a date whose note does not exist, and "fail with an actionable error" is not a
useful thing to deliver two hours late. The edge is kept exactly as it was: it
exists so `tests/js/outbox_contract.test.mjs` can assert the negative.

Critical path: **P5 → P6 → P7 → P8 → P11 → P14**. P1, P2, P3 and P5 are
independent and can run in any order or in parallel given isolated worktrees.
`idea-to-ship` forbids shared-workspace parallel subagents, so the default is
sequential execution recomputed after each ticket; the ordering above is the
*ready* order, not a license to parallelize.

**Verification after every phase**, not just at the end:
`pytest` → `ruff check app tests scripts` → `mypy app` → `npm test` →
`npm run check` → `vendor.py --check .`. A phase that cannot pass its own suite
does not proceed. The `ruff` scope includes `scripts/` and the `mypy` scope does
not; that asymmetry is deliberate and is §10.6's subject, so do not "make them
match" by adding `scripts/` to `mypy` or by dropping it from `ruff`.

---

## 12. Deployment

`AGENTS.md` records deployment as **not yet authorized**, and
`scripts/pwa-pantry-recipes.example.plist` as a template only. This section is the
plan; installing the LaunchAgent still requires explicit authorization, and the
whole point of that note is that installing it today would start a service whose
domain routes do not exist.

Distribution path is Part 1a: private, single-user, Tailscale. **Process manager
and port.** launchd, label `com.syang.pwa-pantry-recipes`,
loopback bind `127.0.0.1:8007`. 8007 is the audited app port
(8000/8002–8006 are allocated to sibling PWAs), and the plist template already
carries it, along with `SoftResourceLimits NumberOfFiles 8192`.

**Tailscale Serve ingress takes `:8452`** — 8443 and 8445–8451 are allocated, and
8446 belongs to wardrobe. **No step in this section serves the app on a single
port end-to-end**: the app listens on 8007, the phone reaches 8452, and
Tailscale Serve proxies between them. The 2026-09-27 port audit in `.env.example`
records exactly this pair, and §4's architecture diagram names 8007 for the bind
only. Any diagram, smoke check, or Playwright `base_url` that assumes one port
for both ends is wrong — Playwright drives the **loopback** 8007 directly
(`create_app` + `TestClient`-style local server, never through Serve), while
production validation goes through 8452 from a participant identity. The rule
from template §1a: never run a bare `tailscale serve <target>`; state port + path
+ backend URL explicitly, and run `port-manager audit --json` **before and after**
the change. Validate from a participant identity (a phone off the host's
network): the PWA port succeeds and unrelated HTTPS ports and SSH still fail.
Only after that is
`PUBLIC_ORIGIN=https://home-macbook-air.tailcd6e49.ts.net:8452` and
`TRUST_TAILSCALE_HEADERS=true` enabled — the `.env.example` already carries that
production origin commented out with the same warning.

**The converge gate (template §1c and §3e), run in full before calling a release
done:**

1. Source version (`CACHE_VERSION` in `sw.js`) == local `GET /api/version`.
2. Local `GET /api/version` == deployed-origin `GET /api/version`.
3. Local and deployed `X-PWA-Backend-Started-At` match, **and** that timestamp is
   newer than every changed startup-loaded file — `app/config.py`, `app/schema.sql`,
   and the recipe/pantry index inputs.
4. A release-specific live API smoke check proves the new backend behavior. A
   version match alone cannot detect a new static frontend talking to an old
   in-memory schema — so the smoke check is a field the release added, e.g.
   asserting a new key appears in `GET /api/recipes`.
5. The HTML shell references the same versioned assets as the SW cache name
   (`tests/js/shell_assets.test.mjs` is the local half of this).
6. Returning from background triggers a version check.
7. An open confirmation flow postpones the reload (the §4e busy-guard).
8. **Only then** reinstall / replace the Home Screen PWA, because iOS caches
   manifest metadata longer than page content and an icon change may need
   remove-and-re-add.

The displayed PWA version proves frontend/static freshness only. It is never
evidence that an already-running backend reloaded configuration.

**Release sequence**, verbatim from `AGENTS.md` § *Deploy*:
bump `CACHE_VERSION` → add new static files to `SHELL_ASSETS` → commit → push →
`launchctl kickstart -k gui/$(id -u)/com.syang.pwa-pantry-recipes` → verify
`/api/version` matches `CACHE_VERSION` and `X-PWA-Backend-Started-At` is newer
than every changed startup-loaded file → only then reinstall the Home Screen PWA.
`kickstart -k` is right for code changes; a plist edit needs
`bootout` + `bootstrap` instead. After any restart, prove the listener with
`port_manager.py inspect 8007` — `launchctl print` showing `state = running`
proves only that a process is alive, not that the socket is listening.

**`portfolio.toml`.** The `[[apps]]` entry already exists at
`pwa-template/portfolio.toml` with `name = "pwa-pantry-recipes"`,
`test = [".venv/bin/python", "-m", "pytest", "tests/scaffold/test_app.py", "-q"]`,
`deploy = "launchd"`, `label = "com.syang.pwa-pantry-recipes"`. Two edits when
the features land: widen `test` to the whole `pytest` suite (it currently pins the
scaffold test only, which is correct today and wrong afterwards), and update the
trailing comment to say the LaunchAgent is installed rather than a template.
`CLAUDE.md` states the one cross-repo edit this repo owns is exactly this block.

---

## 13. Known limitations, accepted

**Reconciled against the closed decision set (2026-09-27).** Every flagged
decision F1–F20 is now locked, so this list has been re-read against the final
§3. Three items changed, and the reason each changed is stated inline: item 11
was **only** there because a decision was open and is now **resolved**; items 7
and 10 were **hardened** by their confirmations; and a new item 19 records F2's
one accepted cost. Nothing was left in this list that a later reader could
mistake for an open question.

1. **D1's ceiling.** Only 3 of 16 recipes are fully cookable from the catalog
   alone, and 58% of distinct `材料` values have no match. This is a property of
   the data, not of the implementation. The app is honest about it rather than
   hiding it: no low-scoring recipe is auto-hidden, and a `0/6` recipe is a
   legitimate shopping-list seed.
2. **A persisted wrong mapping is permanent and invisible.** Mitigations shipped
   together: full provenance on every row, a "re-resolve the unresolved and stale
   ones" action, an automatic re-resolve on catalog-revision change, and the `调试`
   toggle. None of them is a guarantee — a confidently wrong `normalized_exact`
   match will sit there until a human notices it.
3. **A mapping is only as durable as the catalog's ids.** `items.id` is
   `AUTOINCREMENT`; a re-import can renumber. `catalog_revision` + automatic
   re-resolve repairs it, and a `manual` row survives because it is immutable.
4. **Meal Shortlist drift.** The shortlists are PWA-owned state and are never
   synced back. Renaming a recipe note in Obsidian leaves a row that renders as
   `⚠ 已重命名` until the user removes it. This is the accepted cost of D3's "no
   new frontmatter field, vault stays meal-free".
5. **`recipeTracker` lag.** The PWA's view of `cooking_count` / `last_cooked`
   comes from frontmatter that the Obsidian-side `recipeTracker` dataviewjs
   rewrites **only when that recipe note is rendered**. So a Cook logged at 19:00
   may not appear in the recipe's history until the note is opened in Obsidian.
   The app surfaces this as a `待 Obsidian 同步` badge driven by
   `cook_log_receipts.recipe_tracker_synced`, rather than showing a number it
   knows is stale.
6. **`recipeTracker` counts pages, not links.** `cooking.length =
   dv.pages('"日记"').where(...)` returns one entry per daily-note page, so a
   duplicate append inside one daily note **cannot** inflate `cooking_count` —
   not by one, not by any amount. §9.14's dedupe and `cook_log_receipts`' unique
   index therefore exist for **note readability and the audit trail**, *not* for
   count correctness. They are not redundant; removing them on the theory that
   the counter is safe leaves the user a note with four copies of the same
   wikilink and removes the record of which copy the app wrote.
7. **Cooking Log is online-only. Confirmed and hardened as F5.** No outbox, no
   offline replay (§9.18.1). The button is disabled offline with the stated
   reason `离线：需要连接后记录` and never optimistically claims success. The
   alternative is a 409 resolve panel for an already-eaten meal. **The
   confirmation hardened this** rather than merely closing it: F4's richer
   missing-note error adds a second reason against offline logging (what would an
   offline cook log do about a date whose note does not exist?), and the "if this
   ever changes" remedy is now recorded precisely — a revision-tolerant write
   primitive, **not** the outbox — so a future ticket cannot reach for the wrong
   fix.
8. **The `variants` tier is nearly dead.** Only 3 of 178 catalog rows have a
   non-empty `variants` column, and two of those are just the long product name.
   It is implemented because `CONTEXT.md` defines Pantry Item Alias in terms of
   `variants`, but it will not fire in practice. **This is why tier-3 coverage in
   the golden fixture is near-zero, and that near-zero is the expected outcome**
   — a future reader seeing an empty tier 3 must not loosen the tier, widen the
   synonym set to compensate, or delete it (§10.3).
9. **`调` coverage is an assumption, not a measurement.** `staples.yaml` marks a
   Seasoning "assumed on hand" because category `1.1c` has 1 row in 178. If the
   household is actually out of 生抽, the app cannot know.
10. **Strict mode is coarse, and F16 made its coarseness deliberate.** `严格模式（含调料）`
    treats an **unresolved** Seasoning as missing but has no notion of "you have
    half a bottle". It is a pessimistic switch, not a stock model. **F16
    confirmed the sharper version of this and hardened it:** a
    *staple-satisfied* Seasoning still counts as found, because Pantry Category
    `1.1c` has 1 catalog row in 178 and the alternative drove every score to
    near-zero, making the toggle useless. The limitation is therefore not "strict
    mode is imprecise" but the precise one: strict mode answers "which Seasonings
    could I not match at all", and it is silent about quantities — a limitation
    that is only visible because F16 fixed the worse one next to it.
11. **No quantity model, and no Stock Movement. F13 resolved the second half of
    this; the first half stands.** An Ingredient says *what*, not *how much*, and
    the Cooking Log does not record Pantry Unit quantities — that remains a real
    gap and is the substance of the *Quantities* deferral in §15. What F13
    **resolved** is the second half: `CONTEXT.md`'s Cooking Record entry no longer
    promises a Stock Movement this app does not produce (§5.3). So the limitation
    is now stated accurately rather than as a pending amendment: *the app records
    the cook and does not move stock.* The `cook_log_receipts` table ships as a
    PWA-owned mirror supplying the audit trail and the double-submit dedupe, and
    — per item 6 — it is **not** what keeps `cooking_count` correct; the
    daily-note wikilink is. There is no longer a documentation/app mismatch
    outstanding here.
12. **16 recipes, one household. Confirmed as F20.** There is no pagination, and
    the recipe list response is unbounded. At 16 notes this is correct; past a
    few hundred it would need a limit parameter. F1 makes the omission slightly
    more expensive in two recorded ways — the payload grows with per-Ingredient
    `stockJoinState`, and the recipe list is the response that fails closed on an
    unreadable `Pantry.md`, so the failure blast radius grows with it. Both are
    accepted; D4's "never auto-hide a low-scoring recipe" is itself an argument
    against a cursor that might stop before the `0/6` row.
13. **The `contains` tiers are O(catalog).** 178 rows makes every matcher pass
    trivially cheap. A catalog an order of magnitude larger would need an
    inverted index; the tier structure would not change.
14. **F1's Stock Join is the weakest link in the app, and it is measurably weaker
    than the recipe→ingredient join.** A `Pantry.md` line is free text; a
    catalog row is a normalized name. The measured miss rate is **2 of 46 open
    lines** — `禾苑 蟹粉鱼肉狮子头 冷冻 280 克` and `Sanpellegrino CIAO! Peach
    Sparkling Water, 24-Pack` — and D1's 5-row duplicate-name set means even a
    *successful* join can land on either of two ids. Unlike the recipe→ingredient
    join, this one has **no tier ladder**: it is exact-normalized-name, then the
    tier-4 basename, then the committed override. There is no synonym tier, no
    fuzzy tier, and no re-resolution pass, because there is no materialized table
    to re-resolve — so a line the three tiers miss is simply absent from
    `in_stock_ids`, and any recipe depending on it renders
    `chip--have-been-buying` when the item is in fact on the shelf. Mitigations:
    a dedicated `unjoined` bucket surfaced in the `调试` toggle, a
    `stockUnjoinedCount` on `GET /api/recipes`, a `stock.unjoinedLineCount` in
    `/health`, and `app/pantry/line_overrides.yaml` as the repair path. The
    override is **load-bearing**, not a nicety: without it, two live pantry lines
    are permanently unexplainable and the only remaining remedy would be editing
    the user's vault, which this app must never do.
15. **F1 makes the whole recipe list depend on a second vault file.** If
    `Logistics/库存/Pantry.md` is missing, unreadable, or unparseable, **every**
    recipe loses its stock colours — not one. The decision is to fail closed with
    503 `pantry_stock_unreadable` rather than guess, because both guesses are
    plausible-looking wrong answers: assuming in stock silently inflates every
    score, and assuming not-in-stock silently deflates every score to
    `have-been-buying`. The recipe index, the note bodies, and the cooking
    history remain readable, so the app is not wholly down — but the headline
    feature is. This is a real, accepted availability cost bought in exchange for
    D4's in-stock / have-been-buying distinction being true rather than
    decorative.
16. **The `💵` per-unit math is implemented in three places and only one is in
    this repository.** The `existingPantryValue` dataviewjs in
    `Logistics/库存/Pantry.md` and `Helper/scripts/pantry_snapshot.py` are
    outside this repo's test suite, so a change to either cannot fail *our* CI.
    The mitigation is procedural and is stated so it is not mistaken for a
    guarantee: `scripts/snapshot_pantry_catalog.py` computes the total both ways
    and **refuses to refresh the parity fixture** when they disagree, so a
    divergence is caught at fixture-regeneration time by a human, deliberately —
    the same discipline as regenerating the golden catalog snapshot. It is not
    caught automatically, and it will not be.
17. **F4's cost is a manual step before back-dating.** Logging a cook into a day
    with no daily note now requires creating that note in Obsidian first. The
    alternative — automatic creation — was rejected deliberately: it would have
    contradicted `AGENTS.md` non-negotiable #1, added a subprocess sandbox, a
    Templater settle window, a rollback path, a UUID idempotency ledger, and nine
    `Settings` fields, and its fatal edge case was a half-expanded frontmatter
    left in a daily note that poisons that date until repaired by hand. The
    error names the date and the exact path and is retryable, so the cost is one
    action with a clear instruction, not a wall.
18. **The cook-log link lands above the daily note's columns fence, unlike the
    two existing logged cooks.** `日记/2026-09-14.md` has `[[花蛤拌饭]]` after a
    `# Event` heading and `日记/2026/2026-03-10.md` has `[[盐焗鸡]]` after the
    `![[dailyModify.base|ordered-list]]` embed, so a note will accumulate logged
    cooks in two different places. `recipeTracker` is position-agnostic so the
    counts are correct either way, and the whole-note dedupe scan (§9.14 step 3)
    covers both regions — but a human reading the note will see cooks in two
    places. F3 chose the literal reading of D2 over consistency with two
    hand-typed lines.
19. **F2's cost: a duplicated Pantry Item inside one recipe loses that one slot's
    resolution, and nothing recovers it automatically.** The moment a
    user-authored note lists the same Pantry Item in two `材料` slots, the
    inverted unique index fires and the second slot is downgraded to
    `unresolved` with the conflict recorded in `candidates_json` (§9.10.1). The
    user sees one honest `chip--missing` where they expected a match, plus the
    conflict reason in the `调试` view — the duplicate is **shown, never hidden**
    (R18). There is no auto-recovery, because re-running the matcher would
    deterministically re-select the same item and re-collide; recovery is a
    re-map, which is a user action by design. **No current recipe is affected**
    (0 of 16; the 32-slot evidence is in §3's F2 entry), so this is a
    conservative cost accepted for a constraint that is defensive today.

---

## 14. Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | A matcher change silently breaks a correct resolution (e.g. `空心菜 → 83`) | high | high | The golden test over the 16 real recipes with a frozen catalog snapshot (§10.3) makes it a CI failure, not a silent regression. `空心菜 → 83` is named in the ticket. |
| R2 | A flavour-descriptor false positive ships (garlic in `蒜香蒸茄子` or in `乐事 …薯片蒜蓉面包味`, potato in `呀!土豆 薯条`, sesame in `芝麻饼干`) | medium | high — it makes the app confidently wrong about what I have | Two independent guards (§9.7): the whole-name-segment boundary and the single-food-class rule. `test_must_not_match.py` asserts each of the **six** live rows individually **and** carries a negative control per guard, because the per-row table in D1 shows the guards are asymmetrically load-bearing — the family guard alone stops `芝麻`→25 and `蒜`→110, and a single combined assertion would leave either guard deletable. |
| R3 | The daily-note write corrupts a note the user cares about | low | severe | Descriptor-pinned `AtomicNoteStore` + identity CAS + backup-then-replace + post-write read-back. The write is a byte splice, never a re-dump, so `task-date-recorder`'s `INPUT[toggle]` fields and Tasks-plugin date markers survive. Asserted byte-for-byte in `test_cook_log_flow.py` step 2. |
| R4 | A concurrent Obsidian write is silently clobbered | low | severe | 409 `daily_note_changed` on a client-supplied revision, plus the store's own `_same_file` check immediately before `os.replace`. Part 5a set semantics: the user resolves, the app never retries a stale revision forever. |
| R5 | The app is exposed off loopback and the vault becomes readable | low | severe | Ported auth middleware (host → identity → read_only → origin → content-type → CSRF) + strict CSP + the loopback-only bind invariant, which `validate_bind_invariant` already enforces. The scaffold's `TODO` becomes a real implementation in P1. |
| R6 | The stale-shell loop from Part 4d | low | medium | `WAIT_FOR_MESSAGE = true` with the §4d convergence invariant (wait for `installed` before `SKIP_WAITING`, no immediate navigation), Pattern D's first-install guard, and `shell_assets.test.mjs` catching an out-of-alignment. |
| R7 | A new JS/CSS file is added and forgotten in `SHELL_ASSETS`, so the offline shell is incomplete and silent | **high without the fix** | medium | `tests/js/shell_assets.test.mjs` walks the tree and asserts completeness, closing the hole `GEMINI.md` and `README.md` both call out but neither currently detects. It ships in **P1**, before the ~15 new modules land in P11, because a test that arrives with the modules it protects has already missed its window. |
| R8 | **`Pantry.md` is missing, renamed, or mid-edit in Obsidian, and the entire recipe list loses its stock colours** (F1) | **medium — this is the highest-likelihood new risk F1 introduces** | medium | Fail closed, deliberately: 503 `pantry_stock_unreadable`, no fallback in either direction, because both fallbacks are plausible-looking wrong answers (§13.15). `stock_cache_seconds` is 30 s so recovery is fast once the note parses. The recipe index, note bodies, and cooking history stay readable, so the app degrades to "cannot answer what can I cook" rather than "down". `/health` exposes `stock.openCount` and `stock.unjoinedLineCount` so the state is diagnosable without the UI. |
| R9 | **F1's Stock Join silently drops a pantry line, so a held item renders `have-been-buying`** | **high — 2 of 46 lines are already known to miss** | medium | The join has no tier ladder (exact → basename → override) and no re-resolution pass, so it is strictly weaker than the recipe→ingredient join and the spec says so (§13.14). Mitigations: a dedicated `unjoined` bucket in the `调试` toggle, `stockUnjoinedCount` on `GET /api/recipes`, `stock.unjoinedLineCount` in `/health`, and `app/pantry/line_overrides.yaml` as a reviewed one-line repair per miss. The override is load-bearing: without it the only remaining fix is editing the user's vault. |
| R10 | **The `💵` per-unit math drifts between the two vault-side implementations and this app** | medium | low — a money display, not a score | Parity is a test obligation (F1): `scripts/snapshot_pantry_catalog.py` computes the total both ways and refuses to emit `pantry_stock_math_parity.json` on disagreement, and `tests/pantry/test_stock_math.py` pins the three breaking shapes. Residual risk stated plainly: only one of the three implementations is in this repo, so a change to the other two is caught at fixture-regeneration time by a human, not automatically (§13.16). |
| R11 | A user replays a shortlist intent for the wrong mutation | low | medium | `request_fingerprint` binding (§6d): a reused `X-Client-Id` with a different payload is a bounded 409, not a silent overwrite (§9.18.3). |
| R12 | `recipeTracker` never fires, so the cooking frontmatter stays permanently stale | medium | low | The `待 Obsidian 同步` badge makes the lag *visible* rather than confusing, and `cook_log_receipts` records what was written independently of whether Obsidian has caught up. |
| R13 | Shortlist drift is discovered as data loss | low | medium | A renamed recipe renders as a dimmed `⚠ 已重命名` row, never silently dropped. Nothing is auto-pruned. |
| R14 | Scope creep into quantities / nutrition / write-back | medium | medium | §15 states the deferrals with reasons, and `nutrition-intake`'s units and nutrients are named as the future home so a quantity ticket reuses them instead of forking a converter. |
| R15 | A dependency is added that a sibling does not use | low | low | **Exactly two, both locked and both with a named sibling precedent: `aiosqlite` (pwa-wardrobe) in `[project].dependencies` (F9) and `playwright` in an optional `[browser]` extra, deliberately **NOT** in `[test]` (F10).** The *placement* is part of each decision and is itself asserted (§6.1): one `grep playwright pyproject.toml` must show exactly one occurrence, inside the `browser` extra, and `aiosqlite` must be a **runtime** dependency rather than a test one — a deployment that installs only the wheel would otherwise fail at first request, in production, on the vault write path. No ORM, no `regex` (F15), no bundler, no build step, no `node_modules` at runtime. **F1 and F4 add neither** — F1 reads a vault note through the already-ported `pantry.py`, and F4 *removes* a dependency-shaped surface (a subprocess and its nine config fields) rather than adding one. |
| R16 | **F4's manual step is read as a bug** ("the app can't log a cook") | medium | low | The 404 is the most specific error the API emits: it names the date, the exact vault-relative path, and `retryable: true`, and the UI renders the server's `message` verbatim rather than synthesizing a vaguer one. §9.15 records the removed design and the three reasons it was the wrong call, so a future reader sees a decision rather than an oversight. |
| R17 | **The append introduces a mixed line terminator into the user's daily note** (surfaced by F3) | **medium — the risk is real, but not on the live note; it is the CRLF and lone-CR cases the engine must tolerate** | low for content, **medium for trust** | **Corrected evidence.** An earlier version of this row claimed the live note is CRLF: it read `# 笔记` as 8 bytes and the measured region span `4265..4275` as 10, and inferred a two-byte terminator. **That inference was wrong.** Measured, `日记/2026/2026-09-27.md` is pure LF — 152 `\n`, **zero** `\r\n` — and no file in `日记/2026/` contains a CRLF at all. The 10-byte span is a **9-byte heading line** (`# 笔记`, 8 bytes of UTF-8, plus its own one-byte `\n`) **plus a 1-byte blank line** (the region's `b'\n'` content), so `heading_end` is 4274 and the arithmetic closes with a one-byte terminator. **The rule F3 mandates is unchanged and still load-bearing**, because the live note is only the easy case: a splice that hard-codes `b"\n"` on a CRLF note leaves one lone LF in an otherwise CRLF file, and `git` renders that as a whole-file diff, Obsidian shows a mixed-ending file, and `task-date-recorder` re-writes on a debounced `modify` event so the noise is not transient. The same hazard appears in the LF case from the other direction — a plugin or Obsidian sync client rewriting the note to CRLF after we read it, then a later write hard-coding LF. F3 step 5 requires the terminator to be read from the source span and reused verbatim, and `app/vault/sections.py` now handles LF, CRLF and lone CR uniformly; `test_writer.py` asserts it is byte-identical to the pre-image for both a one-byte and a two-byte terminator, and `tests/vault/test_sections.py` adds the stronger property that a CRLF note never gains a lone LF. The `test_cook_log_flow.py` step-2 "byte-identical everywhere else" assertion is the second line of defence. **The risk is now justified by the other notes and by post-Obsidian-rewrite states, not by this file** — a reader who re-measures `2026-09-27.md` will find pure LF and must not conclude the rule is unnecessary. |
| R18 | **A recipe lists the same Pantry Item twice, and that slot's resolution is silently dropped — or the whole resolve pass is silently reverted** (F2) | low today — **0 of the 16 current recipes collides**, and the full 32-slot evidence is in §3's F2 entry | medium — a *silently* dropped resolution is the worst bug class in this app | **F2's override is the whole mitigation, and it hardens what R1 did not cover.** `UNIQUE(recipe_note, pantry_item_id)` catches the defect; the **per-slot `IntegrityError` catch** (§9.10.1) confines the consequence to **one slot**, which is downgraded to `unresolved` with the conflicting candidate in its `candidates_json`, counted in `duplicateSlotConflicts`, and rendered by the `调试` toggle — while every other slot commits and the enclosing `BEGIN IMMEDIATE` transaction stays open. `tests/mapping/test_store.py` asserts all six of those properties plus a static-contract check that the slot `UPDATE` carries neither `OR ROLLBACK` nor `OR FAIL`. The residual is accepted and stated plainly: the user sees one honest `unresolved` chip where they expected a match, and nothing recovers it automatically — which is the correct trade against a whole-table silent revert. |

---

## 15. Out of Scope

Each item names the reason, so a future ticket does not re-derive it.

**Reconciled against the closed decision set (2026-09-27).** Every flagged
decision F1–F20 is locked, so this list was re-read for the two failure modes a
closing pass actually has: something that is now **in** scope but still listed
here, and something that is **in** scope but missing. Nothing is in scope and
still listed — the two entries that named a flag (F13's Stock Movement, F4's
daily-note creation) name **locked** decisions and are correctly exclusions. The
three in-scope items that had no entry here are now stated explicitly at the
bottom: the `[browser]` extra, pull-to-refresh on the recipe list (F19), and the
per-slot `IntegrityError` catch (F2). "Out of scope" below now means *decided
against*, never *not yet decided*.

- **The ingredient-registry vault migration.** Moving recipe Ingredients to
  canonical vault links (or a registry note) would remove the parsing problem
  entirely. It is out of scope because it rewrites 16 user-authored notes and
  changes the vault's own data model — a separate decision with its own
  approval, and one that D1's evidence does not require.
- **Quantities.** An Ingredient says *what*, not *how much*. No quantity is
  parsed, stored, displayed, or decremented, and the Cooking Log records no
  Pantry Unit quantities. When it arrives, the home is
  `nutrition-intake/src/nutrition_intake/units.py` — zero-dependency, pure, and
  already tested — not a new converter here. A deferred integration (§9.20),
  deliberately not built. **F13's amendment does not put this back in scope**:
  it removes the *promise* from the glossary, it does not add the capability.
- **Nutrition integration.** Same reason, same modules (`nutrients.py`). No
  nutrient data is read, computed, or displayed.
- **Barcode / receipt re-import.** That is `wholefoods-to-pantry`'s job. This app
  opens `pantry_items.db` read-only and never ingests a source document.
- **Writing back to `pantry_items.db`.** `AGENTS.md` #4: `wholefoods-to-pantry`
  owns that file. This app creates no Pantry Item, migrates no schema, and opens
  the database through SQLite's read-only URI. A future stock decrement would be
  a PWA-side projection, never a catalog write.
- **Multi-user.** `TAILSCALE_OWNER_LOGIN` is a single exact identity compared
  byte-for-byte. There is no per-user state, no sharing, no permissions beyond
  owner-or-nobody, and no notion of "whose shortlist is this".
- **A Pantry view.** The app reads `Pantry.md` for the Pantry Stock state and
  does not render a browsable stock list. The vault's DataviewJS overview remains
  better at that job, and duplicating it would create a second, worse surface.
  What the app does surface is the *join health* — the Stock Join table in the
  `调试` view (F1) — which is diagnostic, not a browsing surface.
- **A Shopping List.** Reserved in `CONTEXT.md` as a derived, read-only reorder
  suggestion. Out of scope; the `0/6` recipe rows are the seed of it without
  committing to the feature.
- **A Meal Plan.** Reserved in `CONTEXT.md` as a device-local date arrangement.
  Distinct from a Meal Shortlist (D3), which is not date-based. Not built.
- **Stock Movement / consumption accounting. Locked as F13.** `CONTEXT.md`
  defined a Stock Movement as a Pantry Stock change attributed to a Cooking
  Record or a purchase, and this app **records the cook without moving stock**.
  F13's resolution is two-part: the `cook_log_receipts` table ships as a
  PWA-owned mirror (audit trail + double-submit dedupe), and `CONTEXT.md`'s
  Cooking Record entry is **amended** so it no longer promises a Stock Movement
  this app does not produce (§5.3). The capability stays out of scope; the
  documentation mismatch is now closed rather than pending.
- **Automatically creating a missing daily note (F4).** The user chose the error
  path deliberately. Logging a cook into a date with no daily note returns 404
  `daily_note_missing`, naming the date and the exact vault-relative path, and
  is retryable once the user creates the note in Obsidian. The alternative — an
  Obsidian CLI / QuickAdd creation path — is out of scope because it would
  contradict `AGENTS.md` non-negotiable #1, and because its cost is a subprocess
  sandbox, a Templater settle window, an unusable-note rollback, a UUID
  idempotency ledger, and nine `Settings` fields, bought for the case where the
  user has not written that day's note — which is exactly when they should be
  writing it. Its fatal edge case was a half-expanded frontmatter left in a daily
  note, which poisons that date and makes every later read 409 until repaired by
  hand. `AGENTS.md`, `README.md`, `.env.example`, and `CONTEXT.md` all remain
  accurate as written and need no amendment. §9.15 keeps the removed design's
  reasoning so it is not re-proposed without its costs.
- **Pull-to-refresh on the shortlist or recipe detail views. Locked as F19.**
  Enabled on the recipe list only (Pattern M, §2h) — the shortlist and recipe
  detail views are excluded deliberately, because pulling to refresh a detail
  view invites discarding an in-progress state (a half-typed date) and pulling to
  refresh a shortlist while offline edits are pending is exactly the case
  `hasPendingEdit()` exists to block.
- **An OS badge.** Chromium-only and a silent no-op on the iPhone home screen
  (Pattern J, §3c). Any pending-write signal is an in-app pill.
- **Running the Playwright flows in CI. Locked as F10.** `playwright` is in an
  optional `[browser]` extra, so `pip install ".[test,dev]"` is unchanged and the
  two browser flows are **opt-in** and local. This is a *placement* exclusion,
  not a testing exclusion: the two flows are specified, they ship, and they are
  the only coverage of the §2i back-button scroll restore and of stale-paint false
  passes (§10.5). What is out of scope is a browser binary on the CI runner.

**In scope, and stated here because it had no entry above.** A closing pass over
a spec with 20 flags is exactly when something in scope quietly loses its home:

- **The per-slot `IntegrityError` catch and `duplicateSlotConflicts` (F2).**
  In scope. §7.3 specifies the catch, §9.10.1 specifies how it coexists with the
  single `BEGIN IMMEDIATE` transaction, §9.16 publishes the counter, §10.2 asserts
  it, and it rides with P7.
- **Pull-to-refresh on the recipe list (F19).** In scope, last thing to build,
  riding with P11. Specified in §9.4.
- **The optional `[browser]` extra (F10).** In scope. `pyproject.toml` gains
  `browser = ["playwright>=1.44"]`; §6.1 states the placement and §10.5 the
  opt-in invocation. `playwright` must appear **nowhere else** in
  `pyproject.toml`.
- **The `CONTEXT.md` amendments (F1, F13).** Already landed with this spec (§5.3,
  and `CONTEXT.md` itself). They are domain-doc work, not a feature, and
  Ingredient Mapping / Meal Shortlist remain pending their implementation
  commits per `AGENTS.md` § *Domain docs*.

---

## 16. Further Notes

**A note on the `笔记` region and F3 — now resolved, and the resolution is the
counter-intuitive one.** The most easily missed fact in this spec is that
`parse_sections()` gives a `笔记` region containing only its heading line, so
"insert under the `笔记` heading" and "insert at the end of the `笔记` region" are
not the same operation. This was the single decision most likely to need the
user's eye; it has now been answered, and the answer is the **first** of those
two: the link goes at the region's `heading_end`, directly under `# 笔记` and
above the columns fence. The alternative the spec previously recommended —
inserting after the `![[dailyModify.base|ordered-list]]` embed, matching the
`2026-03-10` precedent — is **withdrawn**. The reason it is worth stating is
that neither placement is more *correct*: `recipeTracker` scans every outlink on
the page and is position-agnostic, so both work. The tie was broken on
simplicity and on the literal reading of D2, and the two existing logged cooks
are hand-typed lines rather than a convention. F3 carries the byte-level
algorithm, the `blank=True` empty-region handling, and the confirmation that the
heading already exists so nothing is created.

**A note on what was verified versus what was measured.** §3's numbers were
re-derived from the live vault and the live catalog while writing this spec, and
several of them did not match the figures first reported. The corrected values
are the ones the golden test freezes, and they are listed here together so a
reader does not have to hunt across sections to find which numbers to trust:

| Figure | First reported | Corrected | Where it lives |
|---|---|---|---|
| `材料` value shapes | 4 | **5** — the unlisted one is `X/Y` with no leading emoji (`香料/Basil`) | D1, §9.6 |
| Emoji-only values | `🍔` and 20% | **`🥔`**, and 6 of 26 (23%) from `材料` plus 3 from `调料` | D1, §9.6 |
| `Pantry.md` name-join misses | 1 of 23 | **2 of 46** — the second miss is `Sanpellegrino CIAO! Peach Sparkling Water, 24-Pack` | D1, F1, §9.11.3 |
| Flavour collisions | 5 rows | **6 rows** — `蒜` hits two catalog rows (72 and 110), and the family guard independently stops 110 | D1, §9.7 |
| Daily-note write port | 503 on a missing note | **404** — the note is absent, not the server unhealthy | F4, §9.15, §9.16 |
| Exposed port | 8007 end-to-end | **8007 loopback bind, 8452 Tailscale Serve ingress** | §4, §12 |
| F2's mapping key | `UNIQUE(recipe_note, ingredient_index)` + `UNIQUE(recipe_note, pantry_item_id)`, no per-slot recovery | **KEEP both indexes, ADD a per-slot `IntegrityError` catch; the transaction does not abort** — the user's override after explanation | §3.3 F2, §7.3, §9.10.1 |
| Recipe-list provenance | a `?debug=1` endpoint returning a reduced shape | **Provenance always in the normal payload; the 调试 toggle is render-only** (F7) | §3.3 F7, §9.13.4, §9.16 |
| Browser tests | `playwright` in `[test]`, run in CI | **Optional `[browser]` extra, not in `[test]`, opt-in** (F10) | §6.1, §10.5 |

The direction of every figure is unchanged. The `~27%` naive-fuzzy false-positive
rate and the `5% → 90%` 调料 coverage figures are carried through from the
research session and are re-verified by the golden test rather than re-derived
here, because both depend on the exact fuzzy threshold and allowlist that only
the implementation will pin down.

**A note on the one decision that was overridden, and why it is called out here
separately.** F2 is the only decision in this spec that the user **rejected after
the author had already made the call and defended it**, and it is the one most
likely to be "corrected" back by a later reader who has not read the reasoning.
The original proposal was internally consistent: the inverted unique index catches
a real defect, and a single `BEGIN IMMEDIATE` transaction means a failure leaves
the previous state intact. The user rejected it because *that* guarantee is the
problem — one duplicated Pantry Item in one note would have silently reverted
**every** mapping in the table, and a silently reverted table is a silently wrong
app for all 16 recipes. The hybrid keeps the index and narrows the blast radius to
one slot, downgraded visibly and recorded in `candidates_json` (§9.10.1). Both
halves are in §3.3, §7.3, and §9.10.1; they are not in tension, and the only
sentence that made them look like it was — *"so a failure leaves the previous
state intact rather than a half-resolved table"* — has been rewritten, because it
was the hazard and not the guarantee.

**A note on test seams.** There are three (§4.1), and the one that matters is the
second. The matcher is a pure function over `str` with no I/O, so the highest
possible seam is available for it and the golden test lives there — no app, no
HTTP, no database, no vault. Building the matcher behind a service boundary to
"tidy it up" would push that test down a seam and make it slower and flakier for
no benefit.

**A note on the double-writing guard.** The Cooking Log is the only vault write,
and it goes through exactly one function, `AtomicNoteStore.transform_existing`.
Any future feature that writes to the vault must route through the same primitive
with the same CAS and backup discipline. A second writer with its own file I/O
would defeat every guarantee in §9.14 at once, and nothing in the type system
would catch it — which is why `tests/cooklog/test_writer.py` asserts the
byte-identical-everywhere-else property directly against the file on disk.

---

## 17. Decisions to confirm — **the list is empty; it is closed**

**There are no open decisions. Every flag has been answered by the user and
recorded in §3.** This section is now a status marker, not a work list: nothing
here is waiting on the user, and no implementation ticket should be blocked on it.

| # | Decision awaiting confirmation | Status |
|---|---|---|
| — | **none** | The list is **empty**. All 24 decisions — four product (D1–D4) and twenty implementation (F1–F20) — are locked in **§3**, and §3 is their single home. |

**The history, so the shape of the list is not mistaken for a gap.** This list
began at **20** items. F1, F3 and F4 were answered first and moved into §3,
leaving 17; then the user answered the remaining 17 — **F2 and F5–F20** — and the
list is now **0**. The three removals are the only editorial change this section
has ever needed, and each is recorded in the commit that made it.

**The `F` numbering is unchanged and is now historical and stable.** F1–F20 is a
**contiguous, fully closed** sequence, and it is **not** to be renumbered,
compacted, or re-sequenced: §3, §4, §6, §9, §10, §13, §14 and §15 all
cross-reference these ids, so renumbering would break every one of those
references to buy a tidier list. The **gap convention is retired** — earlier
drafts left gaps at 1, 3 and 4 as the visible signal that those decisions were
resolved, and that signal no longer exists because all twenty are resolved. **A
newly discovered decision continues at F21**; the existing ids never move.

**Where to look instead of here.** The decision index is **§3.1** (one row per
decision, with the section that specifies it); the decisions themselves are
**§3.2** (D1–D4) and **§3.3** (F1–F20, in numeric order). §3.2 and §3.3 are
written for a ticket implementer: each entry states the decision, the reasoning,
and the sections that specify the code, so nothing needs to be re-derived.

**One decision was an override rather than a confirmation, and it is F2.** The
user read the author's proposal, understood the consequence, and rejected the
per-batch abort in favour of a per-slot `IntegrityError` catch. F2 is the
authoritative statement; §7.3 and §9.10.1 implement it, and §13.19 and R18 record
its one accepted cost. A later reader must not "correct" it back to the original
proposal.

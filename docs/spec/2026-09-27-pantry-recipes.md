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
- **Log a cook**: pick a date (defaulting to today) and the app appends a
  `[[RecipeName]]` wikilink under the daily note's `笔记` heading — the exact
  input `recipeTracker.md` consumes, which then advances the recipe's cooking
  frontmatter.
- **Curate** three short PWA-owned Meal Shortlists (breakfast / lunch / dinner)
  to answer "what do I usually eat for lunch" without touching the vault.

The Obsidian vault stays canonical. The PWA *projects* Markdown and performs
exactly one class of write — the Cooking Log. It never rewrites recipe steps,
never edits a recipe note, never creates a Pantry Item, and never writes to
`pantry_items.db`.

---

## 3. Decisions locked by the user

These four product decisions are **closed**. A future implementer or reviewer
must not re-litigate them and, critically, must not treat the known-bad matching
quality in D1 as an undiscovered bug and "fix" it away. The evidence was
measured during the grilling session and shown to the user, who chose to proceed
anyway.

### D1 — Match recipe `材料` against `pantry_items.db`, as originally asked

**The decision.** Recipe Ingredients are matched against the 178-row pantry
catalog. Match results are materialized into a PWA-owned mapping table.

**The measured ceiling, recorded so it is not rediscovered.**

| Measurement | Value | How it was obtained |
|---|---|---|
| Recipes fully cookable today (every Ingredient resolved) | **3 of 16** | Exact + variants + normalized-exact join over the 16 real recipe notes and the 178-row catalog |
| Distinct `材料` values with no catalog match at all | **58%** | Distinct-value join; includes values that are seasonings-by-another-name and brand-only strings |
| Naive fuzzy (Levenshtein ≤ 2 over the raw string) false-positive rate | **~27%** | Fuzzy join of every distinct `材料` value against all 178 `canonical_name`s |
| Naive substring/prefix matches that are demonstrably wrong | 5 named live rows | See below |

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

**Evidence corrections found while verifying the above.** The user's numbers are
directionally right but two are stale. Use these instead — they are the numbers
the golden test (§10.3) freezes.

- There are **26 distinct `材料` values** across the 16 recipes, in **five**
  shapes, not four (§9.6). The fifth shape the user did not enumerate is
  `X/Y` with **no leading emoji** (`香料/Basil`; `🍞/focaccia` also has a `/`
  but does have an emoji). The parser must handle it.
- **6 of 26** distinct `材料` values are emoji-only with no recoverable name
  (`🥦 🥚 🍚 🥔 🍠 🍅`) — 23%, not 20%. A further three emoji-only values
  (`🧄 🫚 🍋‍🟩`) exist but live in `调料`, not `材料`. `🍔` appears nowhere in the
  16 notes; `🥔` (potato) is the value that was meant. `🥚` appears in **3 of
  16** recipes (Easy Fragrant Fried Rice, 番茄炒蛋, 茶碗蒸) — confirmed.
- Exact-name-join fragility is **worse** than stated. `Logistics/库存/Pantry.md`
  has **46** open non-unit task lines today and **2** of them fail an exact join
  against the catalog: `禾苑 蟹粉鱼肉狮子头 冷冻 280 克` (catalog id 108 is
  `禾苑 蟹粉鱼肉狮子头`) and `Sanpellegrino CIAO! Peach Sparkling Water,
  24-Pack`. The user quoted "1 of 23".
- The `variants` column is **effectively empty**: only 3 of 178 rows are
  non-empty, and two of those are just the long product name. The
  "exact `variants` alias" tier will match essentially nothing against the real
  catalog. It is implemented for completeness and is **not** load-bearing; the
  load-bearing tiers are exact `canonical_name`, the brand+size-stripped
  normalized tiers, the synonym set, and the staples allowlist.
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

### D2 — Logging a cook appends a `[[RecipeName]]` wikilink under the daily note's `笔记` heading

**The decision.** The one vault write this app performs is a bare line
`[[RecipeName]]` inserted into `日记/YYYY/YYYY-MM-DD.md` under the `笔记`
heading, on a user-chosen date defaulting to today.

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
links. Two `[[盐焗鸡]]` lines in one daily note still count as **one** cook. A
duplicate append therefore cannot corrupt `cooking_count`; the dedupe
requirement in §9.14 is for auditability and for a readable daily note, not for
count correctness. Stated explicitly so no one "optimizes" it away.

**A concrete placement conflict, resolved with a flag (F3).**
`parse_sections()` from `app/vault/sections.py` computes the `笔记` region as
**just the heading line** — verified against the live
`日记/2026/2026-09-27.md`: byte span `4265..4275`, region content `b'\n'`,
`blank=True` — because the very next line is a ` ````columns ` fence and a
region's end is the first following fence or heading line. Meanwhile the vault's
*existing* logged cooks sit in two other places: `2026-03-10.md` has
`[[盐焗鸡]]` immediately after the `![[dailyModify.base|ordered-list]]` embed,
and `2026-09-14.md` has `[[花蛤拌饭]]` immediately after the `# Event` heading,
before that section's `columns` fence. "Under the `笔记` heading" as literally
specified means *above* the columns fence, matching neither existing example.

### D3 — Breakfast/Lunch/Dinner are three PWA-owned shortlists; the vault stays meal-free

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

### D4 — Honest match display plus a debug provenance toggle

**The decision.** The default UI is one line plus a chip row. **Never** a single
boolean. Recipes are sorted by `found/total` descending with `last_cooked`
(frontmatter) as the tiebreak. **Low-scoring recipes are never auto-hidden** — a
`0/6` recipe is a legitimate shopping-list seed.

Chip colour classes and their exact meaning (§9.13.2):

| Class | Meaning |
|---|---|
| `chip--in-stock` | Resolved to a Pantry Item that is an **open line in `Pantry.md`** — i.e. Pantry Stock, per `CONTEXT.md` |
| `chip--have-been-buying` | Resolved to a Pantry Item in the catalog but **not** currently open stock (bought before, not held now) |
| `chip--in-stock chip--manual` / `chip--have-been-buying chip--manual` | Resolved via `match_method='manual'` — same colour, distinct outline, so a hand fix is visibly different from a machine match |
| `chip--assumed-staple` | Resolved by `staples.yaml`. Assumed on hand. **Excluded from the headline score** in the default view |
| `chip--missing` | Unresolved Ingredient, or (strict view only) an unresolved Seasoning |
| `chip--ignored-seasoning` | Seasoning excluded from scoring in the default view |

The five buckets are exactly the distinction `CONTEXT.md` insists on between
Pantry Item (immutable catalog record of what was *bought*) and Pantry Stock
(what the household *has*). A merely `chip--have-been-buying` chip is the UI's
way of not conflating them.

The `调试` toggle in Settings reveals, behind every chip: the resolved tier, the
`match_method`, the candidate Pantry Item id(s) considered, and the confidence.
It is a render toggle, not a second data path (§9.13.4, flag F7).

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
constraint as every sibling. New runtime dependencies: **one** (`aiosqlite`,
precedent `pwa-wardrobe/app/database.py`); flag **F9**.

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
    pantry.py                  ported (Pantry.md open-items parser)
    obsidian_cli.py            ported (DailyNoteCreationService)
  pantry/
    __init__.py
    catalog.py                 PantryCatalog — read-only items access
    stock.py                   PantryStockIndex — in-stock set from Pantry.md
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
scripts/snapshot_pantry_catalog.py        NEW: regenerate the golden catalog snapshot
tests/
  conftest.py                  EXTEND: recipe fixtures, seeded catalog, app_data
  fixtures/real_recipes/*.md           NEW: the 16 real recipe notes, frozen
  fixtures/pantry_items_snapshot.json   NEW: frozen 178-row catalog
  fixtures/golden_match_results.json    NEW: the expected per-Ingredient outcome
  vault/…  recipes/…  mapping/…  shortlists/…  cooklog/…  db/…  api/…   NEW
  js/scaffold.test.mjs         EXTEND
  js/shell_assets.test.mjs     NEW: closes the "missing SHELL_ASSETS entry" hole
  js/logic/*.test.mjs          NEW
  browser/…                    NEW: the two Playwright flows
```

---

## 5. Domain vocabulary

### 5.1 Bound terms (from `CONTEXT.md`, used exactly)

Pantry Item, Pantry Item Alias, Pantry Category, Pantry Stock, Pantry Unit,
Recipe, Ingredient, Seasoning (调料), Cooking Tool, Cookable, Cooking Log,
Cooking Record, Stock Movement, Recipe Cooking History, Server-Owned Root,
Pantry-Write Contract, Sync Conflict.

`Cookable` as `CONTEXT.md` defines it is a *boolean*. **This app does not render
it.** D4 forbids a single boolean, so the app renders the *evidence* the boolean
would have been computed from (the chip row and the `n/total` line) and never
publishes a `cookable: true|false` field in any API response. Flag **F17**.

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
implements it**. Two new terms:

| Term | Definition | Forbidden synonyms |
|---|---|---|
| **Ingredient Mapping** | The persisted, auditable resolution of one Ingredient slot to at most one Pantry Item. Carries `raw_value`, `parsed_name`, `parse_method`, `match_method`, tier, confidence, and the rejected candidates. Lives only in the PWA's SQLite; the vault has no equivalent. | match, mapping, link, resolution, join |
| **Meal Shortlist** | One of three PWA-owned, user-ordered lists of Recipe notes for `breakfast` / `lunch` / `dinner`. A planning convenience. **Not** a vault record, not synced back, not a classification of the Recipe, and not a grouping of Cooking Records. | meal plan, menu, category, tag, meal type |

`cook_log_receipts` (§7.5) is a PWA-owned mirror of the daily-note row, not a
new domain concept, so it needs no `CONTEXT.md` entry — but §13 notes that
`CONTEXT.md`'s **Cooking Record** definition currently promises a Stock
Movement this app does not perform. Flag **F13**.

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
| `pantry_note_relative` | `PANTRY_NOTE_RELATIVE` | `Logistics/库存/Pantry.md` | same shape as `_safe_relative_root`, extended to a file path | The Pantry Stock source (flag **F1**) |
| `read_only` | `OBSIDIAN_READ_ONLY` | `false` | `_boolean` | Read-only gate in `auth.py` (flag **F18**) |
| `catalog_cache_seconds` | `CATALOG_CACHE_SECONDS` | `300.0` | `0 < v <= 3600` | TTL for the read-only catalog snapshot |
| `stock_cache_seconds` | `STOCK_CACHE_SECONDS` | `30.0` | `0 < v <= 600` | TTL for the `Pantry.md` open-items snapshot. Short on purpose: the user toggles stock from Obsidian while the PWA is open. |
| `recipe_cache_seconds` | `RECIPE_CACHE_SECONDS` | `60.0` | `0 < v <= 3600` | TTL for the recipe index |
| `max_recipe_bytes` | `MAX_RECIPE_BYTES` | `2_000_000` | `1024 <= v <= 20_000_000` | Matches `pantry.py`'s `max_bytes` default |
| `cli_executable` | `OBSIDIAN_CLI_EXECUTABLE` | `None` | absolute path, non-symlink, or `None` | Daily-note creation (flag **F4**) |
| `cli_vault_id` | `OBSIDIAN_CLI_VAULT_ID` | `None` | `_valid_cli_text(max=128)` or `None` | " |
| `daily_template_name` | `DAILY_TEMPLATE_NAME` | `None` | `_valid_cli_text(max=128)` or `None` | " |
| `daily_quickadd_choice` | `DAILY_QUICKADD_CHOICE` | `None` | `_valid_cli_text(max=128)` or `None` | Selects `quickadd:run` instead of `create template=` |
| `cli_timeout_seconds` | `OBSIDIAN_CLI_TIMEOUT_SECONDS` | `20.0` | `1 <= v <= 120` | " |
| `cli_settle_seconds` | `OBSIDIAN_CLI_SETTLE_SECONDS` | `5.0` | `0 <= v <= 30` | Templater rewrites the note asynchronously |
| `cli_max_output_bytes` | `OBSIDIAN_CLI_MAX_OUTPUT_BYTES` | `65536` | `1024 <= v <= 1048576` | " |
| `cli_home` | `OBSIDIAN_CLI_HOME` | `None` | absolute directory or `None` | Fixed subprocess `HOME` |
| `daily_required_markers` | `DAILY_REQUIRED_MARKERS` | `()` | tuple of short printable strings | Postcondition that a created note is a real daily note |
| `daily_notes_year_policy` | `DAILY_NOTES_YEAR_POLICY` | `None` | `YYYY-YYYY` or `None` | Bounds the year a request may name |

`cli_*` fields default to `None`, so an unconfigured checkout has
`settings.creation.available == False`, and the Cooking Log write returns a typed
`daily_note_creation_unavailable` **only when the note is actually missing**.
Logging into an existing note keeps working. This mirrors
`pwa-obsidian-daily`, where an unusable creation adapter must not take down the
existing-note service.

`.env.example` also needs its `DAILY_NOTES_ROOT` comment narrowed. It currently
states "the PWA does not create missing daily notes", which flag **F4** changes
to "the PWA never creates a daily note by writing the file; the only creation
path is the configured Obsidian CLI / QuickAdd invocation".

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
overwrite. `resolve_all()` skips `manual` rows entirely. Flag **F6**.

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

`slot` is a closed enum of exactly three values — there is no fourth slot and no
`snack` (flag **F12**). `recipe_note` is the **basename** (`盐焗鸡`), not a path:
a request contributes *which recipe*, never a path.

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
   not lie about what is in my kitchen.
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
    chip says what it says.
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
31. As a home cook, I want the app to create a missing daily note for me, so
    that back-dating into a day I never journalled works.
32. As a home cook, I want the created daily note to be a real templated daily
    note, so that it looks like every other note in my vault.
33. As a home cook, I want the app to never write the daily-note file directly,
    so that Obsidian's template and plugin pipeline stays the only creator.
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
vendored rule targets `#app`, while the shell's content root is `#app-root`, so
`styles.css` must add `#app-root { overflow-x: hidden; }` — it creates a BFC and
contains children, which `overflow-x: clip` does not. The chip row is the only
horizontal scroller in the app and uses the vendored `.scroll-row` class (§2c: a
scrollable row must opt out of intrinsic width with `min-width: 0`, or it expands
the page width).

**Grid columns** (§2d): any multi-column Recipe grid uses `minmax(0, 1fr)`, never
bare `1fr`, plus `.grid > * { min-width: 0; }` and the base rule before any
`@media` override.

**iOS Home Screen.** The three `apple-*` meta tags
(`apple-mobile-web-app-capable`, `mobile-web-app-capable`,
`apple-mobile-web-app-status-bar-style=black-translucent`) and the
`apple-touch-icon` link are already present and correct. **What is missing is the
file.** All four PNGs referenced by `app/static/manifest.webmanifest` and by the
`apple-touch-icon` link are absent — `app/static/icons/` holds only a placeholder
README (`.gitkeep` is intentionally absent so the note is visible). iOS falls
back to a page screenshot for the Home Screen icon and Chrome logs a manifest
warning. This is a real gap, and this spec closes it:

- `app/static/icons/icon-192.png` — 192×192, `purpose: any`
- `app/static/icons/icon-512.png` — 512×512, `purpose: any maskable`
- `app/static/icons/icon-monochrome-512.png` — 512×512, `purpose: monochrome`
- `app/static/icons/icon-180-apple.png` — 180×180, the real `apple-touch-icon`

Siblings commit their generated binaries to git; do the same, and rewrite
`app/static/icons/README.md` to become the regeneration note rather than the
"missing" table. `pyproject.toml`'s `package-data` already globs
`static/icons/*`, and the CI wheel-content check does **not** assert icons — add
all four to it.

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

**Gap: nothing catches a *missing* `SHELL_ASSETS` entry.**
`tests/js/scaffold.test.mjs` fails on an entry with no file behind it but cannot
detect a file with no entry — the exact failure mode `GEMINI.md` and `README.md`
both call out, and it is a silent offline-shell hole. Spec a new
`tests/js/shell_assets.test.mjs` that walks `app/static/js/**` and
`app/static/css/**`, excludes the vendored `js/pwa/` directory and `sw.js`
itself, and asserts every remaining file appears in `SHELL_ASSETS`. That closes
the hole before this spec adds ~15 modules.

**Variation matrix picks (Part 4), one per dimension:**

| Dimension (§4) | Pick | Reason |
|---|---|---|
| §4a version source | **Pattern A** (`CACHE_VERSION`) | Already wired; the app version *is* the release version |
| §4b shell strategy | **`FETCH_STRATEGY = 'cache-first'`** (current) | The shell changes on release, not on data change. All data is network-only, so a network-first shell (Pattern H) buys nothing. |
| §4c API read strategy | **`CACHE_API_GETS = false`** (current, network-only) | Match results change the moment a user toggles a `Pantry.md` line. Caching them is a correctness bug, not a perf win. §3b rule of thumb: read-mostly → Pattern I. |
| §4d update UX | **`WAIT_FOR_MESSAGE = true`**, `autoApply: false`, `onStale` shows the banner, Reload → `requestUpdateReload()` | **Flag F18.** A forced auto-takeover reload can land between the user tapping "做过了" and the request completing, losing the log. The obsidian-daily explicit-banner variation is the proven fit for a mutation-bearing app. |
| §4e busy-guard | `canApplyUpdate: () => !mutationInFlight && !datePickerOpen` | Required by the 4d choice. Never auto-navigate while a dialog or form is open. |

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
  Link `/css/pull-refresh.css` and import `/js/pwa/pull-refresh.js`; both are
  inert until `initPullToRefresh()` is called. `canStart: () => navigator.onLine
  && !hasPendingEdit()` — the offline gate and the pending-mutation gate live in
  the consumer callback, per the template. Retain the inline SVG's intrinsic
  `width`/`height` so a mixed-cache release cannot expand the glyph to the
  viewport.
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

**The emoji dictionary is hardcoded and required.** 6 of 26 distinct `材料`
values (23%) carry no recoverable name, and `🥚` alone appears in 3 of 16
recipes. Minimum required keys:

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
property escape.** Python's stdlib `re` has no `\p{Extended_Pictographic}`, and
the `regex` module would be a new dependency (flag **F15**). So:

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

**The strip order is load-bearing and is exactly this** (flag F15,
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
   category alone, before the segment guard even runs.

Both guards are checked, not either. A test that only checks one is an incomplete
implementation, and §10.2 asserts each of the five live rows individually.

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
| 3 | `variant_alias` | `parsed_name` equals a `variants[]` entry, case-insensitively, trimmed | 0.95 |
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
- `resolve_all() -> ResolveReport` — for every non-`manual` row, re-run
  §9.6 → §9.7 → §9.8 and update `match_method`, `match_tier`, `pantry_item_id`,
  `confidence`, `candidates_json`, `updated_at`. One `BEGIN IMMEDIATE`
  transaction, so a failure leaves the previous state intact rather than a
  half-resolved table.
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

#### 9.11.3 `PantryStockIndex` (`app/pantry/stock.py`) — the in-stock signal (flag F1)

D4 requires an `in-stock` chip colour, which requires Pantry Stock. Stock lives in
`Logistics/库存/Pantry.md` — `CONTEXT.md`: "See the existing
`Logistics/库存/Pantry.md` note, which is the current home of stock state."
`PANTRY_NOTE_RELATIVE` is a Server-Owned Root.

The read reuses the **ported `app/vault/pantry.py` hardened parser** with its
behaviour unchanged: `parse_pantry()` walks numbered H1 sections, H2/H3
subsections, `dataviewjs` / `Tasks` fences, and task lines with
`💵 $price ✍️/➕/🛫/✅` metadata; a unit-split child (`1/2`) counts per unit and the
split parent is excluded from counts because its `💵` is the *per-unit* price;
open is `[ ]` or `[/]`, and unrecognized markers are never open. Its
`_MAX_ITEMS_TOTAL = 1000` bound, its byte-span `start`/`end` discipline, and its
typed `PantryError` failures are kept as-is. The per-unit price rule is
`CONTEXT.md`'s Pantry Unit rule and is not relaxed anywhere in this port.

`PantryStockIndex` exposes:

- `in_stock_names: frozenset[str]` — NFKC + casefolded **product core** of every
  open, non-unit-split row. The product core is `normalize_ingredient()` of the
  row's cleaned text, with the money, date markers, and `#tags` already removed
  by the ported `_clean_text` / `_tags_of`. So `空心菜嫩苗 0.95-1.05 磅` yields
  `空心菜嫩苗`, and the tier-4 basename lookup finds id 83. Using the *same*
  normalization as the matcher is what makes the two agree.
- `sections: tuple[PantrySection, ...]` — for the counters in `/health` and for a
  future Pantry view (deferred, §15).
- `note_revision` — `"sha256:" + sha256(source)`, the same convention as every
  other note revision in the app.
- `invalidate()` and `close()` — wired even though the PWA never writes stock, so
  a future write path does not have to add them.

**The join is on the normalized name, never on a raw string.** That is the
justification for the whole tier ladder: today **2 of 46** open `Pantry.md` lines
fail an exact raw join against the catalog (`禾苑 蟹粉鱼肉狮子头 冷冻 280 克` vs
id 108, and `Sanpellegrino CIAO! Peach Sparkling Water, 24-Pack`), and a 5-row
duplicate-name set in the catalog (`优质白桃礼盒` 104/128, `韩国紫苏叶` 39/105,
`POM Wonderful …` 155/167, `台湾旺旺浪味仙 …` 27/38, `乐事 … 薯片牛肉派味`
111/124) means even a *successful* name join can land on either of two ids. The
tier ladder tolerates both; a materialized mapping plus a repair path is the only
thing that can be made stable when it is not.

**Stock is derived per response, never stored.** The `in_stock` column that an
early draft of §7.3 proposed is deliberately **not** created. Stock is volatile —
the user toggles a task from Obsidian while the PWA is open — so persisting it
would make the chip colour wrong and would give the app denormalized state that
can silently go stale.

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

One line per Recipe, above the chip row:

```
4/6 ingredients found — missing: 香菇, 娃娃菜
6/6 ingredients found
0/6 ingredients found — missing: 空心菜, 香菇, 娃娃菜
2/5 ingredients found — missing: Clam, 香菇   (严格模式（含调料）: 生抽)
```

Rules, precisely:

- Numerator and denominator count **`材料` (Ingredients) only** in the default
  view.
- In `严格模式（含调料）` both numerator and denominator additionally count
  `调料` (Seasonings). A Seasoning satisfied by `staples.yaml` still counts as
  **found**; only an *unresolved* Seasoning counts as missing. Flag **F16**.
- Cooking Tools are displayed but never scored. They are requirements, not stock
  — `CONTEXT.md` is explicit that a tool is not shoppable and not
  inventory-tracked.
- The missing list uses `parsed_name` where available, falling back to
  `raw_value`, joined with `, ` in slot order. It is **not** truncated:
  truncating hides the exact thing the user needs to buy. Long lists wrap.

Flag **F17** records that the D4 example text (`4/6 ingredients found — missing:
生抽`) lists a Seasoning in a Materials-only missing list, which these rules
cannot produce; the spec uses the internally consistent form.

#### 9.13.2 Chip rendering

`app/static/js/components/chips.js` renders one chip per Ingredient and per
Seasoning. Chip order is slot order — never sorted, because the recipe's own
order is information. Each chip carries `data-match-method`, `data-tier`, and
`data-item-id` attributes, so the provenance view is a DOM concern rather than a
second data path.

`app/static/js/logic/chip-class.js` is pure and `node --test`ed:

```js
// Decision-dense: the five buckets of D4, in resolution order.
export function chipClass({ matchMethod, pantryItemId, inStock, isSeasoning, strict }) {
  if (matchMethod === 'manual')
    return {
      className: inStock ? 'chip--in-stock chip--manual' : 'chip--have-been-buying chip--manual',
      missing: false, assumed: false,
    };
  if (matchMethod === 'staples')
    return { className: 'chip--assumed-staple', missing: false, assumed: true };
  if (pantryItemId !== null && pantryItemId !== undefined)
    return {
      className: inStock ? 'chip--in-stock' : 'chip--have-been-buying',
      missing: false, assumed: false,
    };
  if (isSeasoning && !strict)
    return { className: 'chip--ignored-seasoning', missing: false, assumed: true };
  return { className: 'chip--missing', missing: true, assumed: false };
}
```

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

Flag **F7**: the API always returns the provenance fields on every recipe response
(a 16-recipe list is a few kilobytes) so toggling 调试 is instant and cannot race.
Single-user, private, loopback — the fields are not sensitive.

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
   1a. **Create via the Obsidian CLI + QuickAdd** (§9.15), then re-read.
2. `parse_sections(source)` → `require_unique("笔记", level=None,
   code="ambiguous_notes_section")`. More than one `笔记` region **fails closed**
   with 409 `ambiguous_notes_section` — the vault's own `sectionUpsert` is
   first-match-wins substring matching (`lines[i].includes(keyword)`) and the PWA
   must not replicate that, per `sections.py`'s own docstring.
3. **Dedupe.** Scan the **entire** note source for an existing wikilink to this
   recipe. The check is a whole-note scan, not a region check, because the vault
   already places logged cooks in two different regions (§3, D2). The predicate
   mirrors `recipeTracker.md`'s own match — an outlink whose `path.includes(recipe)`
   or `display === recipe` — implemented as a regex over the source:
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
   that splices `source[:insert_at] + link_line + newline + source[insert_at:]`
   and touches **nothing else**. This is the same span discipline as
   `frontmatter.py`'s `render()` and `pantry.py`'s `_set_status` — byte spans
   into the original `source`, never a re-serialization. `link_line` is
   `[[<recipe_note>]]`: no timestamp, no emoji, no bullet, no trailing metadata.
   `recipeTracker` counts *pages*, so decoration would only pollute the user's
   daily note.
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
8. `recipe_tracker_synced` stays 0. It is flipped by a **read-path** comparison,
   not by a write (§13): when a recipe is loaded and
   `frontmatter.last_cooked >= max(receipt.log_date)`, the row is updated and the
   history is shown fresh.

**Surgical-patch discipline is a hard requirement, not a preference.** The
`task-date-recorder` plugin runs `app.fileManager.processFrontMatter()` and
`app.vault.modify()` on daily notes on a debounced `modify` event, and
`recipeTracker` runs `processFrontMatter` on recipe notes. A full re-dump of a
daily note would rewrite `modified_at`, disturb the `INPUT[toggle(...)]` inline
fields, and potentially reorder frontmatter keys. Step 5 is a byte splice;
nothing else is permitted.

**Flag F3 — the placement ambiguity.** `parse_sections()` computes the `笔记`
region as only the heading line — verified live against
`日记/2026/2026-09-27.md`, byte span `4265..4275`, content `b'\n'`,
`blank=True` — because the next line is a ` ````columns ` fence and a region ends
at the first following fence or heading line. "Insert at the end of the `笔记`
region" therefore places the link **between `# 笔记` and the columns fence**:
valid Markdown, and `recipeTracker` will still find it (it scans every outlink on
the page, so position is irrelevant to it), but it matches neither of the vault's
two existing logged cooks.

**Recommended resolution, to confirm as F3:** insert immediately after the last
line of the `![[dailyModify.base|ordered-list]]` embed when that embed is present
in the note; otherwise fall back to the end of the `笔记` region. This matches the
`2026-03-10` precedent, keeps the link inside 笔记's column layout, and is still a
pure byte splice. The alternative — directly under the `# 笔记` heading — is
simpler, is the literal reading of D2, and is equally correct for `recipeTracker`.

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

### 9.15 Creating a missing daily note (flag F4)

**Decision to confirm.** D2 asks for creating an absent daily note "via the same
hardened path as the sibling app", which points at `pwa-obsidian-daily`'s
`DailyNoteCreationService`. This spec adopts that: an `app/vault/obsidian_cli.py`
port that shells out to the **official Obsidian CLI** and, when
`daily_quickadd_choice` is configured, to `quickadd:run` so the vault's own macro
creates through the Templater API. The PWA **never writes the daily-note file
itself** — `AtomicNoteStore.create_new` is explicitly *not* used for daily notes,
and `tests/browser/test_cook_log_flow.py` step 5 asserts that no file is created.

This contradicts `AGENTS.md` non-negotiable #1 ("never creates a missing daily
note") and the matching lines in `README.md` and `.env.example`. **Both files
must be amended in the same commit that adds `app/vault/obsidian_cli.py`**,
narrowing the claim to "never creates a daily note by writing the file; the only
creation path is the configured Obsidian CLI / QuickAdd invocation". Flagged
rather than silently reinterpreting a non-negotiable.

Ported behaviour, kept intact:

- Direct argument vector, never a shell, never `overwrite`:
  `<cli> vault=<id> create path=<derived> template=<template>`, or
  `<cli> vault=<id> quickadd:run choice=<choice> vars={"date":"<validated>"}`.
  A request contributes only the already-validated date, so it can never select
  the binary, the vault, the target path, the template, the choice, or the
  environment.
- Fixed subprocess environment allowlist: `HOME` (configured `cli_home` or
  inherited), minimal `PATH=/usr/bin:/bin:/usr/sbin:/sbin`, `LANG`/`LC_ALL` =
  `C.UTF-8`, inherited `TMPDIR`. No request data can set an environment variable.
- Bounded pipes with a `selectors` loop, a `cli_max_output_bytes` cap, a
  `cli_timeout_seconds` deadline, `start_new_session=True`, and a process-group
  `SIGKILL` teardown.
- `VaultUnavailable` vs `ObsidianUnavailable` classification from a stderr tail
  bounded to 2048 bytes and never logged verbatim.
- **Settle window**: Templater rewrites the note asynchronously, so the target can
  appear byte-stable with unresolved `<% %>` tokens. Poll until
  `validate_created_content(source, daily_required_markers)` passes or the window
  expires.
- **Rollback of an unusable created note**: if the settle window expires, the
  bytes are provably ours (absent before, never validated) and are discarded. A
  half-expanded frontmatter poisons that date and would make every later read 409
  until someone repaired it by hand.
- **Idempotency ledger**: `CreationIdempotencyStore` in the PWA's SQLite, keyed by
  a caller-supplied UUID `idempotency_key`, with a `request_fingerprint` over
  `{relative, vault_id, template, quickadd_choice, root, executable, markers}`. A
  reused key with a different fingerprint is 409 `idempotency_key_reused`; a
  reused key with the same fingerprint replays the stored result. An
  `intent`/`complete` pair means a crash mid-create is recovered by re-checking
  whether the target now exists.

**Degradation is creation-only.** If the CLI is unconfigured or Obsidian is
unavailable, the service reports `creation_unavailable` and the Cooking Log write
returns 503 `daily_note_creation_unavailable` **only when the note is actually
missing**. Logging into an existing note keeps working. This mirrors
`pwa-obsidian-daily`, where an unusable creation adapter must not take down the
existing-note service.

**Folder creation.** The ported `AtomicNoteStore.create_directory` is used for the
`日记/{year}/` directory only — descriptor-pinned, idempotent, no-follow. It is
never called with a request-derived path, and never for a nested arbitrary path.

### 9.16 API contract

All `/api/*` responses are `Cache-Control: no-store` (the scaffold's
`cache_policy` middleware already does this, correctly exempting `/api/version`,
whose stronger headers the vendored `install_pwa_version` owns). All errors use
the scaffold's existing envelope: `{"requestId": ..., "code": ...}` plus
`X-Request-ID`.

| Method + path | Purpose | Success | Errors |
|---|---|---|---|
| `GET /api/version` | Existing convergence gate | `{"version": "v…"}` | — |
| `GET /api/session` | Identity, CSRF token, capability flags | `{"identity","csrfToken","version","readOnly","appTimezone"}` | 401 `identity_*` |
| `GET /api/pantry/items?q=&limit=` | Catalog search for the manual picker | `{"items":[{id,canonicalName,category,area,variants,lastSeen}],"catalogRevision"}` | 422 bad query |
| `GET /api/recipes?strict=0` | The list, with per-Ingredient provenance | `{"recipes":[…],"catalogRevision","stockRevision","staleMappingCount","strict"}` | 503 pantry/stock unreadable |
| `GET /api/recipes/{note_name}` | One Recipe, full | `{"recipe":{…,"ingredients":[{index,rawValue,parsedName,parseMethod,matchMethod,matchTier,pantryItemId,confidence,chipClass,isSeasoning}],"history":{…}}}` | 404 `recipe_not_found` |
| `POST /api/recipes/resolve` | Re-resolve unresolved and stale rows | `{"reconsidered":N,"resolved":N,"stillUnresolved":N,"staleReset":N}` | 409 while a resolve is in flight |
| `PUT /api/recipes/{note_name}/ingredients/{index}/mapping` | Manual re-map | `{"mapping":{…}}` | 404 / 422 / 403 `read_only` |
| `DELETE /api/recipes/{note_name}/ingredients/{index}/mapping` | Clear a manual row | `{"mapping":null}` | 404 |
| `GET /api/shortlists` | All three slots | `{"breakfast":[…],"lunch":[…],"dinner":[…]}` | — |
| `POST /api/shortlists/{slot}` | Add | `{"slot","items":[…]}` | 422 bad slot / unknown recipe |
| `DELETE /api/shortlists/{slot}/{note_name}` | Remove | `{"slot","items":[…]}` | 404 |
| `PUT /api/shortlists/{slot}/order` | Reorder | `{"slot","items":[…]}` | 422 membership mismatch |
| `GET /api/cook-logs?date=` | Read-back for a date | `{"date","relativePath","noteRevision","entries":[{recipeNote,writtenAt,trackerSynced}]}` | 422 bad date |
| `POST /api/cook-logs` | The Cooking Log write | `201 {"status":"logged","relativePath","noteRevision"}` or `200 {"status":"duplicate",…}` | 409 `daily_note_changed` / `ambiguous_notes_section`; 503 `daily_note_creation_unavailable`; 413 body cap |

`{note_name}` is the **URL-encoded basename**, resolved against the in-memory
index. It is never joined to a path by the client and never trusted as one: the
router looks it up in the index and 404s if absent. This preserves the
Server-Owned Root invariant while still letting a request select *which recipe*.

`strict` is a query parameter, `0`/`1`, default `0` (flag **F8**). It is
stateless because the server computes the score; a persisted setting would need a
round-trip before the first paint and would create a second source of truth.

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
  (flag **F18**) — a read-only Pantry Recipes PWA that could still log a cook or
  edit a shortlist would be a confusing half-mode.
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

So the cook-log button is **online-only and says so**. It renders disabled with
`离线：需要连接后记录` when `navigator.onLine === false`, and on a network-layer
failure it surfaces a real error — it **never** optimistically claims success.
The watchdog (§9.14) covers a hang.

*If a future requirement demands offline cook logging*, the correct fix is **not**
the outbox. It is to make the write revision-tolerant: under
`AtomicNoteStore._locked`, re-read and **re-apply the same append** (Part 5a
delta semantics) with a bounded retry, because a deduped append is
idempotent-by-content. That is a change to the write primitive, not to the
transport. Recorded in §13 as a known gap rather than pre-built.

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
  `PantryStockIndex`, the `AtomicNoteStore`, `IngredientMappingStore`,
  `MealShortlistStore`, and `DailyNoteCreationService`, and runs `init_db()`.
  Every one is closed in a `finally` — a descriptor leak must not outlive
  shutdown. The LaunchAgent's `SoftResourceLimits NumberOfFiles 8192` exists
  because launchd's default 256 is not the shell's `ulimit -n`, and a descriptor
  leak otherwise only appears in production.
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
- `_api_error` is unchanged. New error codes are raised by
  `HTTPException(status, detail=...)` from a route, which the existing
  `StarletteHTTPException` handler already maps to the envelope for `/api/*`.
- `/health` gains `pantry_db.rowCount`, `recipes.count`, `recipes.skipped`,
  `stock.openCount`, and `mappings.unresolvedCount`, and continues to leak **no**
  paths.

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
the process and skips exactly that cleanup. CI runs `ruff check app tests` →
`mypy app` → `pytest` → `npm test` → `npm run check` → wheel build. Add the four
icon files to the wheel-content check.

| File | Covers |
|---|---|
| `tests/scaffold/test_app.py` (extend) | The converge gate, `/js/{path}` injection, traversal rejection, the JSON 404 envelope, fail-closed config. Add: `/api/session` shape, the new `/health` counters, and the security headers on every response. |
| `tests/scaffold/test_routes.py` (new) | Every route in §9.16 responds; `/api/does-not-exist` returns the envelope; `/js/../config.py` still 404s; `POST /api/cook-logs` with no CSRF token is 403. |
| `tests/db/test_migrations.py` (new) | `init_db()` is idempotent; `schema_migrations` records every version; a re-run is a no-op; the six pragmas are applied on every connection. |
| `tests/vault/test_atomic_write.py` (new) | Descriptor pinning, symlink refusal, CAS via `race_hook`, backup-then-replace, post-write verification failure and its `finally` cleanup, `ConcurrentFileExists`. |
| `tests/vault/test_frontmatter.py` (new) | `render()` leaves unrelated bytes byte-identical; a block list round-trips; a duplicate key fails closed. |
| `tests/vault/test_sections.py` (new) | The `笔记` region is the heading line in a real daily note (the `2026-09-27.md` shape, span `4265..4275`); `require_unique` raises on two. |
| `tests/vault/test_daily_paths.py` (new) | `resolve("2026-09-27") == "日记/2026/2026-09-27.md"`; rejects `2026-9-27`, `../x`, an absolute path, and a year outside the policy. |
| `tests/vault/test_pantry_stock.py` (new) | Open vs done markers; `禾苑 蟹粉鱼肉狮子头 冷冻 280 克` yields a product core that tier 4 finds at id 108; a unit-split parent is excluded and its units are not; the `[1 MiB]` / count bounds hold. |
| `tests/recipes/test_ingredients.py` (new) | All five shapes; `🌶️` with and without `U+FE0F`; `🍋‍🟩` as **one** ZWJ run; `"[[Mackerel]]"`; bare `空心菜`; `raw` preserved byte-for-byte. |
| `tests/recipes/test_normalize.py` (new) | The strip order; `Wang Korea 有机去壳甘栗仁 60g*5 300 克` → `有机去壳甘栗仁`; `Mushroom Dried Morel Mushrooms` keeps `Mushroom` (not a lexicon brand); `柴米 蒜香蒸茄子 300 克` keeps `蒜香蒸茄子`; `#tag` and emoji date markers stripped. |
| `tests/recipes/test_matcher.py` (new) | Each tier in isolation, plus the ordering property: a value tier 2 can resolve is never resolved by tier 7. |
| `tests/recipes/test_must_not_match.py` (new) | **The five rows from §3**: `蒜`→72, `蒜`→110, `土豆`→62, `芝麻`→25, `芝麻`→102 all resolve to `unresolved`. And the class guard alone: `芝麻` never resolves to a category-`4` row. |
| `tests/recipes/test_golden_real_recipes.py` (new) | §10.3. |
| `tests/mapping/test_store.py` (new) | The inverted unique constraint: three recipes may map to id 83; one recipe may not map two slots to id 83; `manual` is immutable; `set_manual` deletes + inserts; `resolve_all` skips `manual`; a stale id is detected and reset. |
| `tests/shortlists/test_store.py` (new) | Add is idempotent; remove re-sequences; reorder rejects a membership mismatch; all three slots always present; an unknown slot is a 422; a renamed recipe renders as broken, not dropped. |
| `tests/cooklog/test_writer.py` (new) | The exact bytes appended; the `noteRevision` CAS 409; a duplicate is a 200 with no write; two `笔记` regions fail closed; the append is **byte-identical everywhere else** (assert the pre-image minus the splice). |
| `tests/cooklog/test_creation.py` (new) | The fake-CLI contract fixture (ported from the sibling's `tests/creation/fixtures/fake_obsidian_cli.py`): create / already-exists / replayed; the settle window; the unusable-note rollback; `idempotency_key_reused`; the timeout; `vault_unavailable` classification; and that a hostile date never reaches the argv. |
| `tests/api/test_auth.py` (new) | The guard **order** as a table: for each pair of failing guards, the earlier one wins. Both identity modes. `read_only` rejects before Origin. The 1 MiB cap. Chunked rejection. The CSRF lifecycle. Host rules. The CSP on every response. |
| `tests/api/test_recipes_api.py`, `test_shortlists_api.py`, `test_cooklog_api.py` (new) | Route contracts, status codes, and envelope shapes. |

### 10.3 The golden matcher test — the CI anchor for D1

`tests/recipes/test_golden_real_recipes.py` plus two fixtures.

**`tests/fixtures/real_recipes/*.md`** — the 16 real recipe notes, copied verbatim
at the time this spec was written. They are frozen data, not a dependency: CI must
never reach into the live vault.

**`tests/fixtures/pantry_items_snapshot.json`** — a frozen dump of all 178
`items` rows (`id`, `canonical_name`, `category`, `variants`, `area`) generated
once by `scripts/snapshot_pantry_catalog.py`. CI does **not** read the live
`wholefoods-to-pantry` database (flag **F14**); the snapshot is regenerated
deliberately, in its own commit, when the catalog changes.

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

These three are the D1 evidence, frozen. An implementation that cannot reach them
is either wrong or requires a deliberate re-measurement of the evidence.

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
| `tests/js/logic/format.test.mjs` (new) | The headline line, including the `6/6` case with no missing list and the `0/6` case with the full list. |
| `tests/js/outbox_contract.test.mjs` (new) | The app's `replayDomainIntent` covers every enqueued `type`, sets `X-Client-Id` from `intent.clientId`, and **never** enqueues a cook-log intent. |

### 10.5 Browser suite — Playwright, two flows

New `[browser]` extra adding `playwright` (flag **F10**). Two flows only — the seam
above them is covered, and a third flow is a maintenance liability. Both run
against a `tmp_path` vault and `tmp_path` `APP_DATA_DIR`, never the real ones.

**`tests/browser/test_recipe_browse_flow.py`** — the D4 display contract:

1. Boot, then wait for **content**, not element visibility (§5d), and assert the
   first row's headline line matches the `n/total` form.
2. Assert the chip row is present and that every chip carries a class from the
   five-bucket vocabulary.
3. Assert the sequence of `found/total` values is non-increasing, and that a
   `0/6` row is present and visible.
4. Enter `#/recipe/拌空心菜` from the list, press Back, and assert the URL is `#/`
   **and** the scroll offset is restored (§2i).
5. Toggle `调试`, assert the tier text appears, reload, and assert the toggle
   persisted in `localStorage`.

**`tests/browser/test_cook_log_flow.py`** — the D2 write contract:

1. Log a cook on today's date; assert the 201 `status: "logged"` and that the
   returned `relativePath` equals `日记/<year>/<today>.md`.
2. **Read the file from disk** and assert the exact line `[[<recipe>]]` is
   present, that the note's `modified_at` frontmatter and the
   `![[dailyModify.base|ordered-list]]` embed are byte-identical to the pre-image,
   and that the `INPUT[toggle(...)]` lines are untouched.
3. Log the same recipe again; assert `200 {"status":"duplicate"}` and that the
   file's `sha256` is **unchanged**.
4. Write a competing change into the note out of band, then log with a stale
   `baseRevision`; assert 409 `daily_note_changed` and that the file is unchanged.
5. Log into a date whose daily note does not exist; assert 503
   `daily_note_creation_unavailable` (creation unconfigured in the test env) and
   that **no file was created** — this is the assertion that proves the PWA never
   writes the daily-note file itself.

### 10.6 Type and lint scope

`mypy --strict` on `app` (CI enforces `mypy app`; `pyproject.toml` lists
`files = ["app", "tests"]` with relaxed overrides for `tests.*`). Every new
module is fully annotated — the vault primitives are `frozen=True` dataclasses
with typed byte spans and the new code matches. `ruff` with the existing
`select = ["E","F","I","UP","B"]` and `line-length = 100`.

---

## 11. Phased build order

One `/implement` ticket per phase. Each is sized for a fresh implementation
context given only the ticket, this spec, `AGENTS.md`, and `CONTEXT.md` — never
the design conversation (`idea-to-ship` §4).

```
P1  Config + auth middleware + GET /api/session          deps: —
P2  SQLite schema.sql + database.py + init_db            deps: —
P3  Vault read primitives: atomic_write, frontmatter,
    sections, daily_paths, pantry                        deps: —
P4  PantryCatalog + PantryStockIndex                     deps: P2, P3
P5  Ingredient parser + emoji dictionary + normalize      deps: —
P6  Tier ladder + staples.yaml + synonyms.yaml            deps: P4, P5
P7  IngredientMappingStore + re-resolve + manual
    override + the golden matcher test                   deps: P2, P6
P8  RecipeIndex + GET /api/recipes + /api/recipes/{name}
    + GET /api/pantry/items                              deps: P4, P7
P9  MealShortlistStore + /api/shortlists                 deps: P2, P8
P10 CookingLogWriter: path policy, CAS, region, dedupe,
    POST+GET /api/cook-logs                              deps: P3, P7
P11 Daily-note creation: obsidian_cli + idempotency      deps: P10
P12 Frontend: router, views, components, tokens, styles,
    the honest display, provenance toggle                deps: P8, P9
P13 Icons + install polish + SHELL_ASSETS + drift gate   deps: P12
P14 Offline outbox wiring + X-Client-Id ledger            deps: P9, P10
P15 Playwright browser tests                             deps: P12, P10
P16 Deploy: launchd bootstrap, Tailscale Serve, converge deps: all
```

Critical path: **P5 → P6 → P7 → P8 → P12 → P15**. P1, P2, P3 and P5 are
independent and can run in any order or in parallel given isolated worktrees.
`idea-to-ship` forbids shared-workspace parallel subagents, so the default is
sequential execution recomputed after each ticket; the ordering above is the
*ready* order, not a license to parallelize.

**Verification after every phase**, not just at the end:
`pytest` → `ruff check app tests` → `mypy app` → `npm test` → `npm run check` →
`vendor.py --check .`. A phase that cannot pass its own suite does not proceed.

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
8446 belongs to wardrobe. The rule from template §1a: never run a bare
`tailscale serve <target>`; state port + path + backend URL explicitly, and run
`port-manager audit --json` **before and after** the change. Validate from a
participant identity (a phone off the host's network): the PWA port succeeds and
unrelated HTTPS ports and SSH still fail. Only after that is
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
6. **`recipeTracker` counts pages, not links.** A duplicate append in one daily
   note does not inflate `cooking_count`. Dedupe therefore protects the note's
   readability and the audit trail, not the counter.
7. **Cooking Log is online-only.** No outbox, no offline replay (§9.18.1). The
   button is disabled offline and says why. The alternative is a 409 resolve
   panel for an already-eaten meal.
8. **The `variants` tier is nearly dead.** Only 3 of 178 catalog rows have a
   non-empty `variants` column. It is implemented because `CONTEXT.md` defines
   Pantry Item Alias in terms of `variants`, but it will not fire in practice.
9. **`调` coverage is an assumption, not a measurement.** `staples.yaml` marks a
   Seasoning "assumed on hand" because category `1.1c` has 1 row in 178. If the
   household is actually out of 生抽, the app cannot know.
10. **Strict mode is coarse.** `严格模式（含调料）` treats an unresolved Seasoning
    as missing, but has no notion of "you have half a bottle". It is a
    pessimistic switch, not a stock model.
11. **No quantity model.** An Ingredient says *what*, not *how much*. The
    Cooking Log does not record Pantry Unit quantities, and `CONTEXT.md`'s Cooking
    Record definition promises both quantities and a Stock Movement — neither is
    implemented here. See flag F13.
12. **16 recipes, one household.** There is no pagination, and the recipe list
    response is unbounded. At 16 notes this is correct; past a few hundred it
    would need a limit parameter. Flag F20.
13. **The `contains` tiers are O(catalog).** 178 rows makes every matcher pass
    trivially cheap. A catalog an order of magnitude larger would need an
    inverted index; the tier structure would not change.

---

## 14. Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | A matcher change silently breaks a correct resolution (e.g. `空心菜 → 83`) | high | high | The golden test over the 16 real recipes with a frozen catalog snapshot (§10.3) makes it a CI failure, not a silent regression. `空心菜 → 83` is named in the ticket. |
| R2 | A flavour-descriptor false positive ships (garlic in `蒜香蒸茄子`, potato in `呀!土豆 薯条`, sesame in `芝麻饼干`) | medium | high — it makes the app confidently wrong about what I have | Two independent guards (§9.7): the whole-name-segment boundary and the single-food-class rule. Both are unit-tested against all five live rows, individually, in `test_must_not_match.py`. |
| R3 | The daily-note write corrupts a note the user cares about | low | severe | Descriptor-pinned `AtomicNoteStore` + identity CAS + backup-then-replace + post-write read-back. The write is a byte splice, never a re-dump, so `task-date-recorder`'s `INPUT[toggle]` fields and Tasks-plugin date markers survive. Asserted byte-for-byte in `test_cook_log_flow.py` step 2. |
| R4 | A concurrent Obsidian write is silently clobbered | low | severe | 409 `daily_note_changed` on a client-supplied revision, plus the store's own `_same_file` check immediately before `os.replace`. Part 5a set semantics: the user resolves, the app never retries a stale revision forever. |
| R5 | The app is exposed off loopback and the vault becomes readable | low | severe | Ported auth middleware (host → identity → read_only → origin → content-type → CSRF) + strict CSP + the loopback-only bind invariant, which `validate_bind_invariant` already enforces. The scaffold's `TODO` becomes a real implementation in P1. |
| R6 | The stale-shell loop from Part 4d | low | medium | `WAIT_FOR_MESSAGE = true` with the §4d convergence invariant (wait for `installed` before `SKIP_WAITING`, no immediate navigation), Pattern D's first-install guard, and `shell_assets.test.mjs` catching an out-of-alignment. |
| R7 | A new JS/CSS file is added and forgotten in `SHELL_ASSETS`, so the offline shell is incomplete and silent | **high without the fix** | medium | `tests/js/shell_assets.test.mjs` walks the tree and asserts completeness, closing the hole `GEMINI.md` and `README.md` both call out but neither currently detects. |
| R8 | The Obsidian CLI path breaks (app not running, vault not open, QuickAdd macro renamed) | medium | low — only affects logging into a day with no note | Typed `VaultUnavailable` / `ObsidianUnavailable` / `CreationMisconfigured` classes; creation degrades in isolation and the existing-note path keeps working. The fatal case is a half-expanded note, which the settle window + rollback prevents. |
| R9 | A user replays a shortlist intent for the wrong mutation | low | medium | `request_fingerprint` binding (§6d): a reused `X-Client-Id` with a different payload is a bounded 409, not a silent overwrite (§9.18.3). |
| R10 | `recipeTracker` never fires, so the cooking frontmatter stays permanently stale | medium | low | The `待 Obsidian 同步` badge makes the lag *visible* rather than confusing, and `cook_log_receipts` records what was written independently of whether Obsidian has caught up. |
| R11 | Shortlist drift is discovered as data loss | low | medium | A renamed recipe renders as a dimmed `⚠ 已重命名` row, never silently dropped. Nothing is auto-pruned. |
| R12 | Scope creep into quantities / nutrition / write-back | medium | medium | §15 states the deferrals with reasons, and `nutrition-intake`'s units and nutrients are named as the future home so a quantity ticket reuses them instead of forking a converter. |
| R13 | A dependency is added that a sibling does not use | low | low | Exactly two are proposed, each with a named sibling precedent: `aiosqlite` (pwa-wardrobe) and `playwright` (browser testing). Both are flagged (F9, F10). No ORM, no bundler, no build step. |

---

## 15. Out of Scope

Each item names the reason, so a future ticket does not re-derive it.

- **The ingredient-registry vault migration.** Moving recipe Ingredients to
  canonical vault links (or a registry note) would remove the parsing problem
  entirely. It is out of scope because it rewrites 16 user-authored notes and
  changes the vault's own data model — a separate decision with its own
  approval, and one that D1's evidence does not require.
- **Quantities.** An Ingredient says *what*, not *how much*. No quantity is
  parsed, stored, displayed, or decremented. When it arrives, the home is
  `nutrition-intake/src/nutrition_intake/units.py` — zero-dependency, pure, and
  already tested — not a new converter here. Flagged as a deferred integration
  (§9.20), deliberately not built.
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
- **A Pantry view.** The app reads `Pantry.md` for the in-stock signal and does
  not render a browsable stock list. The vault's DataviewJS overview remains
  better at that job, and duplicating it would create a second, worse surface.
- **A Shopping List.** Reserved in `CONTEXT.md` as a derived, read-only reorder
  suggestion. Out of scope; the `0/6` recipe rows are the seed of it without
  committing to the feature.
- **A Meal Plan.** Reserved in `CONTEXT.md` as a device-local date arrangement.
  Distinct from a Meal Shortlist (D3), which is not date-based. Not built.
- **Stock Movement / consumption accounting.** `CONTEXT.md` defines a Stock
  Movement as a Pantry Stock change attributed to a Cooking Record or a purchase.
  This app records the cook and does not move stock. See flag F13.
- **Pull-to-refresh on the shortlist or recipe detail views.** Enabled on the
  recipe list only (Pattern M, §2h).
- **An OS badge.** Chromium-only and a silent no-op on the iPhone home screen
  (Pattern J, §3c). Any pending-write signal is an in-app pill.

---

## 16. Further Notes

**A note on the `笔记` region and D2.** The most easily missed fact in this spec is
that `parse_sections()` gives a `笔记` region containing only its heading line, so
"insert under the `笔记` heading" and "insert at the end of the `笔记` region" are
not the same operation. Both are legitimate; they produce visually different
results. See F3 — this is the single decision most likely to need the user's eye.

**A note on what was verified versus what was measured.** §3's numbers were
re-derived from the live vault and the live catalog while writing this spec, and
three of them did not match the figures the user was shown (the 26-vs-4 shapes,
the 6-of-26 emoji-only count with `🥔` rather than `🍔`, and the 2-of-46
Pantry.md join misses rather than 1-of-23). The direction of every figure is
unchanged; the corrected values are the ones the golden test freezes. The
`~27%` naive-fuzzy false-positive rate and the `5% → 90%` 调料 coverage figures
are carried through from the research session and are re-verified by the golden
test rather than re-derived here, because both depend on the exact fuzzy
threshold and allowlist that only the implementation will pin down.

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

## 17. Decisions to confirm

Every item here is a place where I made a call the user has not explicitly
confirmed. They are listed so they can be batched and answered in one pass
(`idea-to-ship` §4: parked questions are presented as a batch, and each answer is
recorded durably before re-dispatching the affected ticket).

| # | Decision I made | My call | What changes if you disagree |
|---|---|---|---|
| **F1** | Where Pantry Stock comes from | `Logistics/库存/Pantry.md` open items, via the ported hardened `pantry.py`, cached 30 s, with `PANTRY_NOTE_RELATIVE` as a new Server-Owned Root | The `in-stock` / `have-been-buying` split (D4's core distinction) has no source. Without this, D4 collapses to a single colour and violates its own "Pantry Item ≠ Pantry Stock" rule. |
| **F2** | Mapping-table key design | Slot identity `UNIQUE(recipe_note, ingredient_index)` **plus** the inverted `UNIQUE(recipe_note, pantry_item_id)`, and **no** FK on `pantry_item_id` | The user's instruction said `UNIQUE(recipe_id, pantry_item_id)`; I read `recipe_id` as the recipe (so one recipe cannot list the same SKU twice) while `空心菜 → 83` stays legal across 3 recipes. If "recipe_id" was meant as the *slot*, the composite key should be `(recipe_note, ingredient_index, pantry_item_id)` and the "one recipe, one SKU" check is lost. |
| **F3** | Where the cook wikilink goes | Prefer immediately after the `![[dailyModify.base\|ordered-list]]` embed (matching the `2026-03-10` precedent); fall back to the end of the `笔记` region | Insert directly under the `# 笔记` heading instead. Simpler, the literal reading of D2, equally correct for `recipeTracker`, and visually above the columns fence rather than inside them. **The one decision most worth your eye.** |
| **F4** | Creating a missing daily note | Shell out to the official Obsidian CLI + QuickAdd, mirroring `pwa-obsidian-daily`, adding 10 new `Settings` fields | This directly contradicts `AGENTS.md` non-negotiable #1. Either amend that file in the same commit (my recommendation, and the spec says so), or return 409 `daily_note_missing` and leave creation to Obsidian. The alternative is a much smaller config surface. |
| **F5** | The cook log is offline-only-excluded from the outbox | Online-only; the button is disabled offline and says why. Reasoning in §9.18.1 | Offline cook logging needs a revision-tolerant write primitive (re-read + bounded re-apply under the lock), not the outbox. I did not pre-build it. |
| **F6** | `manual`-row immutability mechanism | A `BEFORE UPDATE` trigger that aborts, forcing delete + re-insert to change a hand fix | A softer rule — no trigger, just "re-resolve skips manual rows". Simpler, but a hand fix could then be clobbered by any other code path that writes a mapping. |
| **F7** | Provenance always in the payload | Every recipe response carries tier, method, `pantry_item_id`, and candidates; the 调试 toggle is render-only | A separate `?debug=1` endpoint. Halves the normal payload but makes the toggle a round-trip and introduces a way for the two shapes to drift. |
| **F8** | `严格模式` is a query parameter | `?strict=1` (or `?strict=0`, the default), stateless | A persisted setting in SQLite, so the choice survives a restart. Costs a round-trip before first paint and a second source of truth. |
| **F9** | `aiosqlite` as a new runtime dependency | Added to `[project].dependencies`, precedent `pwa-wardrobe/app/database.py` (the pragmas block is copied from there) | Use stdlib `sqlite3` through `asyncio.to_thread`. Zero new dependencies and fine for a single-user loopback app at ~1 write per cook, but it diverges from the sibling whose migration pattern we are copying. |
| **F10** | `playwright` as a new `[browser]` test extra | Optional extra, not in `[test]`, so CI's `pip install ".[test,dev]"` is unchanged and the browser suite is opt-in | Running the two flows in CI. They need a browser binary in the ubuntu runner, which is a real CI cost. |
| **F11** | Cache TTLs | catalog 300 s, stock 30 s, recipes 60 s | All three. Stock is short because you toggle tasks from Obsidian while the PWA is open; the other two are conventional. |
| **F12** | Exactly three meal slots | `breakfast` / `lunch` / `dinner`, a closed `CHECK` constraint, no `snack` | Adding a slot is a SQLite table rebuild, so it is cheap now and expensive later. |
| **F13** | `cook_log_receipts` and the `CONTEXT.md` Cooking Record mismatch | Ship the receipts table as a PWA-owned mirror; amend `CONTEXT.md` so Cooking Record no longer promises a Stock Movement this app does not produce | Dropping the table loses the `待 Obsidian 同步` badge and the double-submit dedupe ledger, both of which are cheap here and expensive to add after users have data. |
| **F14** | The golden test's catalog source | A committed `pantry_items_snapshot.json` plus a deliberate `scripts/snapshot_pantry_catalog.py` regeneration | Reading the live `wholefoods-to-pantry` DB. That makes CI depend on a sibling repo's on-disk state and turns every catalog re-import into a CI failure. |
| **F15** | Emoji-run stripping without a Unicode property escape | A hand-built range class plus `U+FE0F` / `U+200D`, longest-key-first | The `regex` module, which gives `\p{Extended_Pictographic}` but adds a dependency none of the siblings use. |
| **F16** | Strict-mode semantics for staple-satisfied 调料 | They still count as **found**; only an *unresolved* Seasoning counts as missing | Every 调料 becomes missing under strict mode, which drives every score to near-zero given `1.1c` has 1 catalog row — the toggle would be useless. |
| **F17** | The headline string and the absence of a `cookable` boolean | `n/total` from 材料 only by default; no boolean field is ever published | The D4 example text (`missing: 生抽`) lists a 调料 in a Materials-only missing list, which the rule set cannot produce. The spec uses the internally consistent form, and `CONTEXT.md`'s boolean Cookable stays defined-but-unrendered. |
| **F18** | Variation-matrix picks not covered by D1–D4 | `cache-first` shell, `network-only` APIs, **explicit update banner** with a `canApplyUpdate` busy-guard, and **no** read-only write allowlist | Auto-takeover would risk a forced reload between tapping "做过了" and the request landing. A read-only allowlist is what obsidian-daily has for Open Items; a read-only Pantry Recipes that could still log a cook seemed like a confusing half-mode. |
| **F19** | Pull-to-refresh | Enabled on the recipe list only (Pattern M, §2h) | Template §2h calls it optional. It is a nice-to-have and the last thing to build. |
| **F20** | No pagination on the recipe list | Unbounded, correct at 16 recipes | A limit/offset parameter. Cheap to add later; premature now. |

# Domain Glossary

> **State (2026-09-28): the terms below are contracts, and the features that
> implement them have shipped** — the recipe index, the Stock Join, the eight-tier
> matcher, the materialized Ingredient Mapping, the Meal Shortlists, the Cooking
> Log write, and the provenance view. An entry is still a *contract* first: it
> states what a term means and what may not be called it, which is a stronger
> claim than "here is what the code currently does". Where an entry says a thing
> is deliberately **not** published or **not** stored, that is the design, not a
> gap — see `README.md` § *What does not work yet* for what genuinely is one.
> Deployment is still unauthorized and has not happened; no term here depends on
> it.

The Obsidian vault is canonical. The PWA **projects** existing Markdown and
performs only narrowly defined, revision-checked transformations. Every term
below that says "vault" means the note text is owned by Obsidian, not by this
app.

## Pantry Item
A purchasable grocery product tracked in the `wholefoods-to-pantry` SQLite
catalog (`assets/pantry_items.db`, table `items`, keyed by `canonical_name`).
It is a **catalog record, not inventory**: the PWA reads the catalog and never
writes it, and the PWA never *creates* a Pantry Item. Distinguish from
[Pantry Stock](#pantry-stock), which is per-household and mutable.

## Pantry Item Alias
A variant spelling of a Pantry Item (`variants`, plus any user-added synonym)
matched case-insensitively and ignoring surrounding whitespace. Recipes
reference ingredients by alias, so the same physical product can satisfy several
spellings. Alias resolution is a read concern only; it never writes back to the
catalog.

## Pantry Category
The catalog's `category` code (e.g. `1.1`, `1.1d`) for a Pantry Item. A code, not
a human label — the mapping to display text is presentation, and must not be
inferred from the code. Note that `area` is a *separate* column holding a
non-food area (`Shampoo`, `Serum`); it is not a synonym for category and must not
be treated as one.

## Pantry Stock
What the household currently holds, as opposed to what has ever been bought.
Deliberately **not** a Pantry Item: stock is per-household, mutable, and has no
catalog identity.

State comes from **`Logistics/库存/Pantry.md`** — *in addition to* the catalog,
not instead of it, and the two are not interchangeable:

- `pantry_items.db` (the catalog) supplies **identity, brand and price**: the
  rename-stable `pantry_item_id`, the brand, and the `💵` amount.
- `Logistics/库存/Pantry.md` supplies **state**: `open` / `in_progress` / `done` /
  `cancelled`, per [Pantry Unit](#pantry-unit).

The engine never infers "I have it" from "I have bought it". A [Pantry
Item](#pantry-item) that is in the catalog but has no open line in the pantry note
is **bought before, not held now**, and the two must never be rendered as the
same thing — conflating them makes a re-buy look like a duplicate and makes a
finished bag look like a full one.

The `💵` amount on a **parent** line is the **effective per-unit price**, and the
**unit parent is excluded from every count and every money total**; the per-unit
Pantry Units are counted at that price. Counting the parent *and* its units
double-counts; counting the parent *instead of* its units halves a half-used
item's value. Three implementations of this math must stay in parity: the
`existingPantryValue` DataviewJS in `Pantry.md`, `Helper/scripts/pantry_snapshot.py`,
and the PWA's own logic.

## Recipe
One Markdown note under `RECIPES_ROOT` (`Hobbies/做饭/Recipes`) describing a
dish. A Recipe is a **read** projection: the PWA renders it but never rewrites
its steps, and its cooking-history frontmatter is advanced only by the
[Cooking Log](#cooking-log) write path below.

## Ingredient
A single required input of a Recipe, listed in the recipe note's `材料`
frontmatter. An Ingredient names a [Pantry Item Alias](#pantry-item-alias);
it carries no quantity — a Recipe says *what*. A [Cooking
Log](#cooking-log) says *which* Recipe was made on *which* date; it does **not**
say *how much* was used, and must not be described as if it did (see
[Cooking Record](#cooking-record)).

## Seasoning (调料)
A non-primary input of a Recipe, listed in the recipe note's `调料` frontmatter.
Distinct from an [Ingredient](#ingredient): a Seasoning is present in small
quantities and is **never** counted against [Pantry Stock](#pantry-stock) when
deciding what can be cooked. A recipe's 调料 list must not be flattened into its
材料 list — doing so silently inflates the "you can cook this" answer.

## Cooking Tool (烹饪工具)
Equipment a Recipe requires, from the recipe note's `烹饪工具` frontmatter. Not
stocked and not matched against the catalog. See
[Cookable](#cookable) for how tools affect eligibility.

## Cookable
The boolean answer to "can I make this Recipe right now?", computed by matching
every [Ingredient](#ingredient) of a Recipe against available
[Pantry Stock](#pantry-stock), and requiring the Recipe's
[Cooking Tool](#cooking-tool). A Seasoning never blocks Cookable. Cookable is a
**derived, read-only** property: it is recomputed from stock and never stored on
the Recipe.

**Defined but not rendered, and deliberately so.** The PWA publishes **no**
`cookable: true|false` field in any response. It renders the *evidence* the
boolean would have been computed from — a `found/total` headline counted from
`材料` (Ingredients) only, plus a per-Ingredient chip row whose classes say
*why* — because a single green tick cannot be audited and a user cannot tell which
jar the app thinks is missing. The term is kept because the concept is real and
useful; the boolean is not emitted because reducing it to a bit would hide the
evidence. Consumers must read the score and the chips, never a boolean.

## Pantry Unit
The smallest separately-countable unit of a Pantry Item — a can, a bottle, a
bag, a single piece. A multi-package purchase splits into N Pantry Units, and
the unit carries the per-unit price; the parent line's amount is a *unit* price,
not an item total. Every count and money total must iterate units and skip the
parent, or a half-used item is counted at twice its value. (This is the same
rule the existing `Logistics/库存/Pantry.md` DataviewJS and the Obsidian Daily
PWA follow; see `wholefoods-to-pantry/references/pantry-write.md`.)

## Ingredient Mapping
A materialized row binding **one** Recipe `材料` slot to **one**
`pantry_item_id` — the persisted, auditable outcome of resolving an
[Ingredient](#ingredient) against the catalog. It lives only in the PWA's own
SQLite (`ingredient_mappings`, `APP_DATA_DIR`); the vault has no equivalent, and
nothing here is a projection of a recipe note. It carries `raw_value`,
`parsed_name`, `parse_method`, `match_method`, `match_tier`, `confidence`, and
the rejected `candidates_json`, so a wrong answer can be *shown* rather than
merely replaced.

- **`pantry_item_id` is NOT unique, and the mapping is NOT one-to-one.** One SKU
  is legitimately claimable by many Ingredients across many Recipes: `空心菜`
  resolves to id 83 in three different recipes. "One item, many uses" is the
  normal case, so the join is many-to-few. The uniqueness the schema does enforce
  is *narrower and different*: one recipe may not list the same `pantry_item_id`
  twice, and a violation is a reported `duplicate_slot_conflict` on that one
  slot, never a silent pick of a winner.
- **It carries NO foreign key, and that is deliberate.** `pantry_items.db` is a
  separate, **read-only** database owned by `wholefoods-to-pantry`; there is no
  enforceable reference across that boundary. Referential integrity is handled
  instead by re-resolution — a row whose id no longer exists in the live catalog
  is found by `stale_rows()`, surfaced as `staleMappingCount`, and reset to
  `unresolved`.
- **`match_method='manual'` rows are immutable.** A `BEFORE UPDATE` trigger
  aborts, so changing a hand fix is a `DELETE` plus a fresh `INSERT`, never an
  overwrite. The guarantee is a property of the *row*, so no call site — a
  re-resolve sweep, a backfill, a hand-edited query — can clobber a hand fix.
- **A persisted wrong mapping is permanent and invisible.** Nothing re-resolves a
  `manual` row, so a wrong hand fix renders as confidently as a right one, and
  the symptom is a chip that says the wrong thing about the user's own kitchen.
  That is the whole reason the mapping is materialized *and* carries provenance:
  the `tier · match_method · #id · confidence` line, the rejected candidates, and
  the **re-resolve** and **re-map** repair actions are what make a wrong row
  findable at all.

## Stock Join
The act of relating a `pantry_item_id` from `pantry_items.db` to a **live line in
`Logistics/库存/Pantry.md`**, so that a [Pantry Item](#pantry-item) can be shown
as [Pantry Stock](#pantry-stock) rather than as merely bought-before. It is a
read-time operation: the result is **not** stored, and no `pantry_item_id` is
persisted for a pantry line.

- **`pantry_item_id` is NOT unique, and the mapping is NOT one-to-one.** One SKU
  can satisfy several recipe Ingredients and several pantry lines; a pantry line
  can also match no row at all. "One item, many uses" is the normal case, so the
  join must be many-to-few and must tolerate a miss without guessing.
- **It is a weaker link than the Recipe→Ingredient mapping, and the reason is
  structural.** A `Pantry.md` line is free text; a catalog row is a normalized
  name. The join is therefore exact normalized-name, then a product-basename
  match, then a committed manual override — and there is **no tier ladder, no
  fuzzy tier, and no re-resolution pass**, because there is **no materialized
  table to re-resolve against**. That is precisely why
  `app/pantry/line_overrides.yaml` is **load-bearing rather than a nicety**:
  without it, a line no tier explains is unexplainable forever, and the only
  remaining remedy would be editing the user's vault.
- **A line all tiers miss is simply absent from the in-stock set.** It is not
  silently treated as in stock and not silently treated as out of stock; it is
  *unjoined*, counted, and surfaced in the debug provenance view. Absence from
  the in-stock set is not evidence the household does not have the item.
- **A successful join can still land on either of two ids**, because the catalog
  contains duplicate product names under two rows.

## Cooking Log
The record that a Recipe was actually made on a specific local date. It is
written **into the daily note** for that date under `DAILY_NOTES_ROOT`
(`日记/{year}/{year}-{mm}-{dd}.md`), not into the Recipe note, and it is the only
write this app performs. See [Cooking Record](#cooking-record).

## Cooking Record
The row a [Cooking Log](#cooking-log) appends to a daily note: which Recipe, on
which date. **This app does not record quantities and does not produce a
[Stock Movement](#stock-movement)** — it records the cook and does not move
stock. Quantities and consumption accounting are out of scope: the PWA never
decrements a [Pantry Unit](#pantry-unit) and never records a consumed amount. A
stock change in this household is attributable to a purchase restock through the
[Pantry-Write Contract](#pantry-write-contract) — **not** to a Cooking Record.

The app keeps a **PWA-owned mirror** of each row in `cook_log_receipts`
(`APP_DATA_DIR`, not the vault). That table is the **audit trail** — it is what
makes "did the app write this?" answerable — and it is the **double-submit
dedupe ledger**: a second log of the same Recipe on the same date is a no-op.

**The daily-note wikilink, not the receipts table, is what keeps `cooking_count`
correct.** `Helper/utils/recipeTracker.md` computes
`cooking.length = dv.pages('"日记"').where(...)`, which counts **pages, not
links** — two `[[盐焗鸡]]` lines in one daily note still count as one cook. The
receipts table therefore guards readability and auditability, not the counter, and
neither it nor the dedupe may be removed on the theory that the counter needs
protecting. It does not.

## Stock Movement
A change to [Pantry Stock](#pantry-stock) with a stated cause: a
[Cooking Record](#cooking-record) (consumption) or a purchase restock. A stock
projection that cannot attribute every movement to one of these two causes is
incomplete and must fail closed rather than show a plausible total.

**Only the restock cause occurs in this app.** A [Cooking
Record](#cooking-record) records that a meal was made; it is not a consumption
measurement and must not be presented as one. So any Stock Movement a reader sees
in the vault originated from a purchase through the
[Pantry-Write Contract](#pantry-write-contract), and the PWA is a consumer of that
stock, never an author of a movement.

## Recipe Cooking History
The aggregate frontmatter a Recipe note carries about past cooks:
`first_cooked`, `last_cooked`, `cooking_count`, `cooking_frequency`,
`cooking_years`, `recent_activity`, `favorite_season`. It is **derived** from
[Cooking Records](#cooking-record) across daily notes and is recomputed, never
hand-edited and never incremented independently — otherwise it drifts from the
daily notes it summarizes.

## Meal Shortlist
One of three PWA-owned, user-ordered lists of [Recipe](#recipe) notes — the
Breakfast, Lunch, and Dinner lists. A planning convenience, and the whole of it
is `meal_lists` in the PWA's own SQLite.

- **The slots are a closed set: `breakfast`, `lunch`, `dinner`.** There is no
  fourth slot and **no `snack`**, enforced by a `CHECK` constraint on the column.
  Closing the set now is what keeps adding one later a deliberate table rebuild
  rather than a data migration.
- **It is PWA-owned state that can drift from the vault, and is never synced
  back.** The lists name Recipe notes; they do not own them, they do not create
  them, and a reorder writes nothing outside `APP_DATA_DIR`. A Recipe deleted or
  renamed in Obsidian leaves its Shortlist entry behind, and the entry renders
  **broken, not dropped** — the user's ordering is theirs, and silently pruning
  it would be the app editing the user's list.
- **A Recipe carries no meal classification, and no frontmatter field is added
  for one.** A Recipe's membership in `breakfast` says nothing about the Recipe:
  the same note may sit on two shortlists, or none. Meal-ness is a property of
  the *user's* arrangement, never of the dish.
- **An empty slot is a normal empty state, not an error.** All three keys are
  always present and possibly empty; "nothing planned for lunch" is an answer.
- **Cooking history is NOT grouped by meal.** The [Recipe Cooking
  History](#recipe-cooking-history) frontmatter and the daily-note rows stay
  meal-agnostic; a shortlist has no effect on what a [Cooking
  Record](#cooking-record) records, and grouping history by slot would
  contradict `recipeTracker`'s page-counting rule.

## Server-Owned Root
A path or folder that comes from the process environment only
(`PANTRY_ITEMS_DB`, `RECIPES_ROOT`, `DAILY_NOTES_ROOT`, `OBSIDIAN_VAULT_PATH`,
`APP_DATA_DIR`). A request may contribute *which recipe* and *which date* — never
*which paths*. (*Which quantities* is reserved and **not implemented**; no
request field carries one today, and none may until
[Cooking Record](#cooking-record) records quantities.) See the terminology note
below.

## Pantry-Write Contract
The `wholefoods-to-pantry` project's documented rule set for how a grocery order
becomes pantry stock (`references/pantry-write.md`), including the
[Pantry Unit](#pantry-unit) per-unit price rule. This app is a **consumer** of
stock that already satisfies that contract; it must not re-implement the
purchase-side writer.

## Sync Conflict
A state in which the daily note changed in Obsidian after the PWA loaded it and
the PWA holds a different uncommitted value. Both versions stay visible until
the user resolves them. The PWA never silently wins, because a silently
overwritten cooking log loses a real meal.

---

## Terminology: use these, not the synonyms

| Use | Never use | Why |
|---|---|---|
| Pantry Item | catalog entry, grocery item, product record, SKU | "Item" alone collides with UI list items and with "Pantry Stock". |
| Pantry Stock | inventory, pantry items, supplies, on-hand items | "Pantry Item" is the immutable catalog record; stock is what the household has. Conflating them makes a re-buy look like a duplicate. |
| Pantry Category | aisle, section, department, type | The catalog column is a numeric code, not a human grouping; "section" is already taken by the `Pantry.md` note's `##` headings. |
| Pantry Unit | serving, portion, pack, piece, count | "Portion"/"serving" are consumption amounts, not the physical countable unit; "pack" hides the N-units case that causes double counting. |
| Stock Join | stock match, pantry join, line match, availability join, restock key, join key | "Stock match" reads as the Recipe→Ingredient resolution, which is a different and stronger link. "Availability join" implies availability comes from the join; it comes from the pantry note's status. "Restock key" implies it keys a restock write, which it never does. |
| Ingredient | 材料 item, ingredient line, shopping item | "Shopping item" implies the app builds a shopping list, which it does not. |
| Ingredient Mapping | match, mapping, link, resolution, join | "Match"/"resolution" name the *act* of resolving, which happens on a sweep; the Mapping is the row that survives it. "Join" is reserved for the [Stock Join](#stock-join), a different and weaker link. |
| Seasoning (调料) | ingredient, spice, condiment, flavoring | 调料 is a *separate* frontmatter list from 材料. Calling them ingredients collapses two lists into one and breaks Cookable. |
| Cooking Tool | equipment, utensil, appliance, pan | "Appliance"/"equipment" imply shoppable, inventory-tracked things; tools are requirements, not stock. |
| Cookable | available, can-make, makeable, ready, possible | "Available" collides with [Pantry Stock](#pantry-stock) availability and with API availability. "Makeable" is not a word. Use the `n/total` score and the chip row, not a bare boolean — the boolean is never published. |
| Cooking Log | log, entry, record, activity, meal log | "Entry"/"record" are used by other surfaces; the log is specifically the daily-note write. |
| Cooking Record | log entry, row, meal | A row inside a daily note, not the write operation. |
| Stock Movement | delta, change, diff, transaction | "Transaction"/"delta" imply accounting; a movement is attributed to a cause and is not a ledger entry. |
| Recipe | dish, meal, menu item, cookbook entry | "Meal" is the occasion, not the note; "dish" is a synonym with no separate definition and invites confusion with the daily note's meal sections. |
| Recipe Cooking History | stats, counters, metadata | "Stats" is read as derived display; the frontmatter is the durable aggregate that must match the daily notes. |
| Meal Shortlist | meal plan, menu, category, tag, meal type | "Meal plan" is a [reserved](#reserved-for-later-named-now-so-the-naming-is-not-invented-twice) term for a dated arrangement, which a Shortlist is not. "Category"/"tag"/"meal type" each imply a classification *of the Recipe*; a Shortlist is the user's arrangement, and the Recipe carries no such field. |
| Server-Owned Root | config path, setting, root, base dir | "Config"/"setting" understates that no request can influence them; that is the security property. |

**A note on Chinese/English mixing.** The vault's own vocabulary is Chinese
(材料, 调料, 做法, 日记). The code vocabulary is English (`ingredient`,
`seasoning`). Keep the mapping one-to-one and never introduce a third spelling
for either list — a third name for 调料 is how it ends up merged into 材料.

---

## Reserved for later, named now so the naming is not invented twice

- **Shopping List** — a derived, read-only reorder suggestion computed from
  [Pantry Stock](#pantry-stock) and [Recipe Cooking History](#recipe-cooking-history).
  Not implemented, and it must never write to the `wholefoods-to-pantry`
  catalog.
- **Meal Plan** — a device-local arrangement of [Recipe](#recipe) entries for a
  date, mirroring the Daily Meal Plan in `pwa-obsidian-daily`. A planning aid,
  not a vault record.

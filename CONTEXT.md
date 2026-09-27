# Domain Glossary

> **Scaffold state (2026-09-27):** this glossary defines the vocabulary the
> feature implementation will use. None of the domain surfaces exist yet — the
> app serves the shell, `/health`, and `/api/version` only. The terms below are
> contracts, not descriptions of working code.

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
catalog identity. See the existing `Logistics/库存/Pantry.md` note, which is the
current home of stock state.

## Recipe
One Markdown note under `RECIPES_ROOT` (`Hobbies/做饭/Recipes`) describing a
dish. A Recipe is a **read** projection: the PWA renders it but never rewrites
its steps, and its cooking-history frontmatter is advanced only by the
[Cooking Log](#cooking-log) write path below.

## Ingredient
A single required input of a Recipe, listed in the recipe note's `材料`
frontmatter. An Ingredient names a [Pantry Item Alias](#pantry-item-alias);
it carries no quantity — a Recipe says *what*, a [Cooking Log](#cooking-log)
says *how much*.

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

## Pantry Unit
The smallest separately-countable unit of a Pantry Item — a can, a bottle, a
bag, a single piece. A multi-package purchase splits into N Pantry Units, and
the unit carries the per-unit price; the parent line's amount is a *unit* price,
not an item total. Every count and money total must iterate units and skip the
parent, or a half-used item is counted at twice its value. (This is the same
rule the existing `Logistics/库存/Pantry.md` DataviewJS and the Obsidian Daily
PWA follow; see `wholefoods-to-pantry/references/pantry-write.md`.)

## Cooking Log
The record that a Recipe was actually made on a specific local date. It is
written **into the daily note** for that date under `DAILY_NOTES_ROOT`
(`日记/{year}/{year}-{mm}-{dd}.md`), not into the Recipe note, and it is the only
write this app performs. See [Cooking Record](#cooking-record).

## Cooking Record
The row a [Cooking Log](#cooking-log) appends to a daily note: which Recipe, on
which date, at what [Pantry Unit](#pantry-unit) quantities, with the resulting
[Stock Movement](#stock-movement). It is the audit trail a
[Stock Movement](#stock-movement) must be reconcilable against — a stock change
with no Cooking Record is a mystery the app must be able to show.

## Stock Movement
A change to [Pantry Stock](#pantry-stock) with a stated cause: a
[Cooking Record](#cooking-record) (consumption) or a purchase restock. A stock
projection that cannot attribute every movement to one of these two causes is
incomplete and must fail closed rather than show a plausible total.

## Recipe Cooking History
The aggregate frontmatter a Recipe note carries about past cooks:
`first_cooked`, `last_cooked`, `cooking_count`, `cooking_frequency`,
`cooking_years`, `recent_activity`, `favorite_season`. It is **derived** from
[Cooking Records](#cooking-record) across daily notes and is recomputed, never
hand-edited and never incremented independently — otherwise it drifts from the
daily notes it summarizes.

## Server-Owned Root
A path or folder that comes from the process environment only
(`PANTRY_ITEMS_DB`, `RECIPES_ROOT`, `DAILY_NOTES_ROOT`, `OBSIDIAN_VAULT_PATH`,
`APP_DATA_DIR`). A request may contribute *which recipe*, *which date*, and
*which quantities* — never *which paths*. See the terminology note below.

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
| Ingredient | 材料 item, ingredient line, shopping item | "Shopping item" implies the app builds a shopping list, which it does not. |
| Seasoning (调料) | ingredient, spice, condiment, flavoring | 调料 is a *separate* frontmatter list from 材料. Calling them ingredients collapses two lists into one and breaks Cookable. |
| Cooking Tool | equipment, utensil, appliance, pan | "Appliance"/"equipment" imply shoppable, inventory-tracked things; tools are requirements, not stock. |
| Cookable | available, can-make, makeable, ready, possible | "Available" collides with [Pantry Stock](#pantry-stock) availability and with API availability. "Makeable" is not a word. |
| Cooking Log | log, entry, record, activity, meal log | "Entry"/"record" are used by other surfaces; the log is specifically the daily-note write. |
| Cooking Record | log entry, row, meal | A row inside a daily note, not the write operation. |
| Stock Movement | delta, change, diff, transaction | "Transaction"/"delta" imply accounting; a movement is attributed to a cause and is not a ledger entry. |
| Recipe | dish, meal, menu item, cookbook entry | "Meal" is the occasion, not the note; "dish" is a synonym with no separate definition and invites confusion with the daily note's meal sections. |
| Recipe Cooking History | stats, counters, metadata | "Stats" is read as derived display; the frontmatter is the durable aggregate that must match the daily notes. |
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

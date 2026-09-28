"""The materialized Ingredient-to-Pantry-Item resolution (D1).

`store` is the **write** side of the vocabulary core. `app.recipes.matcher`
decides *which* Pantry Item one `材料` value is; this package decides that the
answer is *remembered*, and it is the only place in the app where a Pantry
Item id is ever persisted. Nothing else writes `ingredient_mappings`, so a
`manual` row is a genuine statement by the user and a non-`manual` row is a
genuine statement by the ladder — and the two are never conflated.

**A persisted wrong mapping is permanent and invisible, and that is the whole
problem this package exists to make survivable.** Three mitigations ship
together, because none is sufficient alone:

1. **Provenance is always stored** — `raw_value` verbatim, `parsed_name`,
   `parse_method`, `match_method`, `match_tier`, `confidence`, and
   `candidates_json` with every row the ladder looked at and why it refused it.
   Nothing is ever *un*-debuggable.
2. **A re-resolve path exists** — `resolve_all()`, `resolve_recipe()`, and
   `resolve_ingredient()` over unresolved *and* stale rows, plus an automatic
   re-resolve when `catalog_revision` changes (§9.11.2).
3. **The `调试` provenance toggle** surfaces (1) to the user. That is the
   render layer's job (`app.api.recipes`); this package supplies the data.

**Two constraints and one catch (F2), and the catch is the reason the
constraints are tolerable.** `ux_ingredient_mappings_slot` gives slot identity
so a re-resolution updates in place; `ux_ingredient_mappings_recipe_item` is
scoped to the recipe, so one Pantry Item may satisfy Ingredients in many
recipes (`空心菜` → 83 in three of them) while a recipe listing the same Pantry
Item twice is still caught. That catch fires as a per-slot
`sqlite3.IntegrityError` **inside** the still-open transaction, downgrades
**only that slot** to `unresolved` with the conflict recorded in its
`candidates_json`, and lets every remaining slot commit. A whole-transaction
abort would silently revert all 16 recipes' mappings over one duplicated
`材料`; dropping the constraint would render two chips claiming one SKU is in
stock. The chosen failure is one honest unresolved chip with the conflict
visible. No current recipe collides, so the constraint is defensive, not
load-bearing — but it is not to be "optimized" away.

**`manual` rows are immutable, and the guarantee is in the schema (F6).** A
`BEFORE UPDATE` trigger raises `ABORT` when the old row is a hand fix, so
changing one is a `DELETE` plus a fresh `INSERT` — an auditable pair, never a
silent overwrite. `resolve_all()` skips them, which makes a hand fix a
property of the row rather than of any one call site: an auto-re-resolve, a
future backfill, a repair script and a hand-edited query all respect it
identically, because none of them is the thing enforcing it.
"""

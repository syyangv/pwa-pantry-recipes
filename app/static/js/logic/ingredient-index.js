/* The 食材 tab's index: group every Ingredient slot and every Pantry.md line by
 * the Pantry Item they landed on.
 *
 * **PURE, AND IN `logic/` FOR THE REASON `sort.js` AND `chip-class.js` ARE.**
 * No DOM, no fetch, no storage, no clock. The grouping is the part with a
 * non-obvious answer, so it is separated from the view that paints it and can be
 * asserted on its own — `tests/js/logic/ingredient-index.test.mjs` drives this
 * module with the app's own frozen payload and the fake DOM is not involved at
 * all.
 *
 * **THE INPUT IS THE ONE `GET /api/recipes` RESPONSE, AND IT IS THE ONLY ONE.**
 * A second route serving the same Stock Join was the option weighed and
 * rejected, and `app/api/recipes.py`'s module docstring is where that argument
 * is made in full: this response already publishes two *derived* views of the
 * same `StockJoin` object (`stockUnjoinedCount` and every slot's
 * `stockJoinState`), so a second read of `Pantry.md` through a second TTL
 * window could contradict the number printed beside it in the first. A 食材 tab
 * that disagreed with the 菜谱 tab's chip colours on the same response would be
 * worse than no tab. So this is a PROJECTION of the response the recipe list
 * already reads, and it takes no input the list does not already have.
 *
 * **A ROW IS A PANTRY ITEM, NOT A RECIPE SLOT — WHICH IS WHY THE TWO SOURCES
 * HAVE TO BE MERGED.** `Pantry.md` is the user's own free text and a recipe's
 * `材料` is a list of names; the join between them is three tiers deep and can
 * miss (F1's weakest link). Grouping only the slots would produce an inventory
 * that omits everything in the house no recipe happens to mention — the
 * shopping-relevant half of the question — and grouping only the lines would
 * produce an inventory that cannot answer "what does this make?". So both are
 * read, and both are keyed by the id the join produced.
 *
 * **A ROW'S NAME IS THE USER'S OWN `Pantry.md` TEXT WHEN THERE IS ONE, AND THE
 * VIEW SAYS WHICH SOURCE IT CAME FROM.** There is no `canonical_name` in this
 * payload, and inventing one client-side would be a guess about a table the
 * client cannot read. So the name is the line the user actually wrote when a
 * line exists (`nameFrom: 'stock'`), and the recipe's own parsed name when only
 * a recipe mentions it (`nameFrom: 'recipe'`). `otherNames` carries the other
 * source's names so a disagreement is *shown* rather than resolved silently —
 * `蒜` on a recipe and `蒜苗` on a pantry line is a real difference between two
 * files, and picking one would be the confident-wrong-answer class `AGENTS.md` #3
 * rules out.
 *
 * **IN STOCK IS THE SERVER'S FACT, TAKEN FROM EITHER PUBLICATION OF IT.** A
 * Stock Join hit's `pantryItemIds` are all in `in_stock_ids` (that is what
 * `app/pantry/stock.py`'s `StockJoinHit` documents), and a slot's `inStock` is
 * the same set tested per slot. The two are unioned rather than one preferred,
 * and a line whose `stockJoinState` is `unresolved` never sets the flag even if
 * it carries ids — that combination is contradictory, and resolving it towards
 * "in stock" is the direction that makes a wrong answer look right.
 *
 * **NOTHING IS FILTERED OUT, IN STOCK OR NOT.** D4's rule is that a `0/6` recipe
 * is a first-class row, and the same reasoning applies here from the other
 * side: an item a recipe needs and the house does not have is precisely the row
 * a user opens this tab to find. In-stock rows sort first; nothing is hidden,
 * collapsed, or thresholded. `counts.inStockWithoutRecipe` is what makes the
 * reverse case (in the house, in no recipe) countable rather than a surprise.
 *
 * **NO RECIPE SCORE IS PRINTED ON A ROW.** Each nested recipe carries the
 * server's `found`/`total`, but that is the score of the WHOLE recipe, not of
 * this ingredient's contribution to it, and putting it beside an ingredient
 * would read as "this item is 3/6 of your dinner". The 菜谱 tab is where a
 * score belongs. The numbers are carried so a caller that wants them has them,
 * and this view does not want them.
 *
 * **THE ORDER IS A TOTAL ORDER, DETERMINED WITHOUT A CLOCK OR A LOCALE.** In
 * stock first, then name by code point, then id ascending — the same
 * code-point rule `sort.js` uses and the same reason for it (`localeCompare`
 * would make the rendered order depend on the device's locale). `id` is the
 * final tiebreak so two Pantry Items sharing a display name cannot swap places
 * between renders.
 */

/** Where a row's display name came from. Shown, never resolved silently. */
export const NAME_FROM_STOCK = 'stock';
export const NAME_FROM_RECIPE = 'recipe';
export const NAME_FROM_ID = 'id';

/** In stock / not, as the two facts they are. Not a third state. */
export const IN_STOCK = 'in-stock';
export const OUT_OF_STOCK = 'out-of-stock';

function slotName(slot) {
  const parsed = slot && slot.parsedName;
  if (parsed !== null && parsed !== undefined && parsed !== '') return String(parsed);
  const raw = slot && slot.rawValue;
  return raw === null || raw === undefined || raw === '' ? '' : String(raw);
}

function byCodePoint(a, b) {
  if (a < b) return -1;
  if (a > b) return 1;
  return 0;
}

function touch(byId, id) {
  let item = byId.get(id);
  if (item) return item;
  item = {
    id,
    name: null,
    nameFrom: null,
    otherNames: [],
    state: OUT_OF_STOCK,
    stockLines: [],
    /* `noteName -> entry`, NOT a concatenated string key. A `\`${id} ${noteName}\``
     * key looks fine and is a collision waiting to happen (id 1 + "2 3" and id
     * 12 + "3" are the same string), and the separator I first reached for was a
     * literal NUL byte — which works and makes the file BINARY to git, so it
     * becomes invisible to grep and to every diff. Two levels of Map have no
     * separator to get wrong. */
    byRecipe: new Map(),
    /* `null` until a slot says otherwise: an item only the Stock Join knows has
     * no 格 at all, and "only 调料" is a claim about slots it does not have. */
    seasoningOnly: null,
    recipeNames: [],
  };
  byId.set(id, item);
  return item;
}

/** A slot's contribution to one row: which recipe, which 格, and which kind. */
function slotRef(recipe, slot) {
  return {
    noteName: recipe.noteName,
    index: slot.index,
    isSeasoning: Boolean(slot.isSeasoning),
    matchMethod: slot.matchMethod,
    inStock: Boolean(slot.inStock),
  };
}

/**
 * The whole index. `payload` is a `GET /api/recipes` body; a missing or
 * malformed one yields empty collections and zeroed counts rather than throwing,
 * because the caller renders an empty state and a thrown TypeError would take
 * the tab down instead of answering "there is nothing here".
 */
export function buildIngredientIndex(payload) {
  const body = payload && typeof payload === 'object' ? payload : {};
  const recipes = Array.isArray(body.recipes) ? body.recipes : [];
  const join = body.stockJoin && typeof body.stockJoin === 'object' ? body.stockJoin : {};
  const lines = Array.isArray(join.lines) ? join.lines : [];

  const byId = new Map();
  /* `item.byRecipe` keeps ONE entry per (item, recipe), so a recipe that uses the
   * same Pantry Item in two 格 is ONE row with two slot references rather than
   * two rows. F2 makes a recipe listing the same Pantry Item twice a conflict the
   * user resolves by editing the note, so it is not impossible on screen and must
   * not be rendered as two recipes. */
  const unresolvedLines = [];
  let slotCount = 0;

  for (const recipe of recipes) {
    const slots = Array.isArray(recipe && recipe.ingredients) ? recipe.ingredients : [];
    for (const slot of slots) {
      const id = slot && slot.pantryItemId;
      if (id === null || id === undefined) continue;
      slotCount += 1;
      const item = touch(byId, id);
      if (slot.inStock) {
        item.state = IN_STOCK;
        }
      const name = slotName(slot);
      if (name && !item.recipeNames.includes(name)) item.recipeNames.push(name);
      const isSeasoning = Boolean(slot.isSeasoning);
      item.seasoningOnly =
        item.seasoningOnly === null ? isSeasoning : item.seasoningOnly && isSeasoning;
      // Insertion order, so the recipes on a row follow the response's own recipe
      // order: deterministic for a given payload, with no sort of its own that
      // could disagree with the list tab about the same recipe.
      let entry = item.byRecipe.get(recipe.noteName);
      if (!entry) {
        entry = { noteName: recipe.noteName, found: recipe.found, total: recipe.total, slots: [] };
        item.byRecipe.set(recipe.noteName, entry);
      }
      entry.slots.push(slotRef(recipe, slot));
    }
  }

  for (const line of lines) {
    const ids = Array.isArray(line && line.pantryItemIds) ? line.pantryItemIds : [];
    const unresolved = (line && line.stockJoinState) === 'unresolved';
    if (unresolved) {
      // Kept, and never merged into an item. A miss is a pantry line the join
      // could not explain; it is not a Pantry Item, and inventing one for it
      // would put a plausible-looking row on a screen whose whole job is to be
      // believed. The view prints a pointer to 溯源 instead of a fake row.
      unresolvedLines.push({
        lineIndex: line.lineIndex,
        section: line.section,
        text: line.text,
        core: line.core,
        overrideKey: line.overrideKey,
        overrideName: line.overrideName,
      });
      continue;
    }
    for (const id of ids) {
      if (id === null || id === undefined) continue;
      const item = touch(byId, id);
      // A hit's ids are all in `in_stock_ids` — see the header note.
      item.state = IN_STOCK;
      item.stockLines.push({
        lineIndex: line.lineIndex,
        section: line.section,
        text: line.text,
        core: line.core,
        state: line.stockJoinState,
        tier: line.tier,
      });
    }
  }

  const items = [...byId.values()].map((item) => {
    // The stock line the user wrote, earliest line first — the note's own order
    // is the order they are looking for a line in.
    const linesByPosition = [...item.stockLines].sort(
      (a, b) => Number(a.lineIndex) - Number(b.lineIndex),
    );
    const stockName = linesByPosition.length ? String(linesByPosition[0].text || '') : '';
    const recipeName = item.recipeNames.length ? item.recipeNames[0] : '';
    let name = stockName || recipeName;
    let nameFrom = stockName ? NAME_FROM_STOCK : NAME_FROM_RECIPE;
    if (!name) {
      // No name from either source. The id is a real fact and the least
      // misleading label available — it is not a name, and it is not a claim to
      // have found one.
      name = `Pantry Item #${item.id}`;
      nameFrom = NAME_FROM_ID;
    }
    const otherNames = nameFrom === NAME_FROM_STOCK ? item.recipeNames : [stockName].filter(Boolean);
    const recipes = [...item.byRecipe.values()];
    // `slotCount` is ALSO the outer count of slots that resolved to an item, so
    // this one is named for what it counts rather than shadowing it.
    const itemSlots = recipes.reduce((total, entry) => total + entry.slots.length, 0);
    return {
      id: item.id,
      name,
      nameFrom,
      otherNames,
      state: item.state,
      seasoningOnly: item.seasoningOnly,
      slotCount: itemSlots,
      stockLines: linesByPosition,
      recipes,
    };
  });

  items.sort((a, b) => {
    if (a.state !== b.state) return a.state === IN_STOCK ? -1 : 1;
    const byName = byCodePoint(a.name, b.name);
    if (byName !== 0) return byName;
    return a.id - b.id;
  });

  return {
    items,
    unresolvedLines,
    counts: {
      items: items.length,
      inStock: items.filter((item) => item.state === IN_STOCK).length,
      inStockWithoutRecipe: items.filter(
        (item) => item.state === IN_STOCK && item.recipes.length === 0,
      ).length,
      slots: slotCount,
      stockLines: lines.length,
      unresolvedLines: unresolvedLines.length,
    },
  };
}

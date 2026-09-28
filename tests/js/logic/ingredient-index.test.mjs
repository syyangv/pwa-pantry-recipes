/* `logic/ingredient-index.js` — the 食材 tab's grouping, asserted on its own.
 *
 * This is the module with the non-obvious answer, which is why it lives in
 * `logic/` beside `sort.js` and `chip-class.js`: no DOM, no fetch, no storage, no
 * clock, so every rule below is testable without a fake document and a stubbed
 * transport. What the VIEW does with the result is asserted separately in
 * `tests/js/pantry-view.test.mjs`; this file never touches a view.
 *
 * **THE PAYLOAD IS THE APP'S OWN FROZEN DATA.** The Stock Join rows are copied
 * out of `tests/js/provenance.test.mjs`'s `FROZEN_JOIN`, which copied them out of
 * `tests/pantry/test_stock_join.py` and the frozen `Pantry.md` — the real
 * basename hit (`空心菜嫩苗 0.95-1.05 磅` → 83, which is not the name on the line),
 * the real duplicate-name collision (`organic 1% milk` → [90, 116]), the real
 * tier-3 override hits, and a real miss. A fixture invented for this test would
 * not catch a wiring mistake, and the whole claim of this module is that it is
 * wired to what the server publishes.
 */

import assert from 'node:assert/strict';
import { register } from 'node:module';
import test from 'node:test';

register('../app-version-hook.mjs', import.meta.url);

const {
  buildIngredientIndex,
  IN_STOCK,
  OUT_OF_STOCK,
  NAME_FROM_ID,
  NAME_FROM_RECIPE,
  NAME_FROM_STOCK,
} = await import('../../../app/static/js/logic/ingredient-index.js');

/* One slot in the wire shape `app/api/recipes.py`'s `_slot_body` publishes. */
function slot(overrides = {}) {
  return {
    index: 0,
    rawValue: '空心菜',
    parsedName: '空心菜',
    parseMethod: 'name_only',
    matchMethod: 'note_basename',
    matchTier: 4,
    pantryItemId: 83,
    confidence: 0.95,
    candidatesJson: [],
    inStock: true,
    stockJoinState: 'joined',
    isSeasoning: false,
    ...overrides,
  };
}

function recipe(noteName, ingredients, overrides = {}) {
  return {
    noteName,
    notePath: `Hobbies/做饭/Recipes/${noteName}.md`,
    found: 1,
    total: 1,
    lastCooked: null,
    ingredients,
    tools: [],
    ...overrides,
  };
}

/* The real `stockJoin` rows, trimmed to the four shapes that matter here. */
const STOCK_LINES = [
  {
    lineIndex: 360,
    section: '1',
    text: '空心菜嫩苗 0.95-1.05 磅',
    core: '空心菜嫩苗 0.95-1.05 磅',
    stockJoinState: 'joined',
    tier: 2,
    pantryItemIds: [83],
    overrideKey: '空心菜嫩苗 0.95-1.05 磅',
    overrideName: null,
    repairHint: null,
  },
  {
    lineIndex: 372,
    section: '1',
    text: '365 By Whole Foods Market, Organic 1% Milk, 32 Fl Oz',
    core: 'Organic 1% Milk',
    stockJoinState: 'joined',
    tier: 2,
    // The real collision: two catalog rows, one product. §9.13.4 requires BOTH
    // ids, and this module must not collapse them.
    pantryItemIds: [90, 116],
    overrideKey: 'organic 1% milk',
    overrideName: null,
    repairHint: null,
  },
  {
    lineIndex: 511,
    section: '5',
    text: 'Manukora Manuka Honey MGO 50+',
    core: 'Manukora Manuka Honey MGO 50+',
    stockJoinState: 'unresolved',
    tier: 0,
    pantryItemIds: [],
    overrideKey: 'manukora manuka honey mgo 50+',
    overrideName: null,
    repairHint: '在 app/pantry/line_overrides.yaml 的 overrides 里加一行：键写 manukora manuka honey mgo 50+。',
  },
];

const REVISIONS = {
  catalogRevision: 'sha256:1da7ba7e0a28a2f207519e37e218f4c1477e8199669e9ce69f1d5ec88bd8b6a9',
  stockRevision: 'sha256:6a231469ab208483b5dfef0b6720ef46e205b23e21b1d2a50352ddc77dc20aea',
  strict: 0,
  staleMappingCount: 0,
  stockUnjoinedCount: 1,
  skipped: 0,
  recipes: [],
  stockJoin: { lineCount: 3, unjoinedCount: 1, tierCounts: {}, guidance: '', lines: STOCK_LINES },
};

function payload(recipes, overrides = {}) {
  return { ...REVISIONS, recipes, ...overrides };
}

const byId = (index, id) => index.items.find((item) => item.id === id);

/* --- the rules ---------------------------------------------------------- */

test('a Pantry Item known to both sides is named by the user Pantry.md line', () => {
  const index = buildIngredientIndex(
    payload([recipe('拌空心菜', [slot({ parsedName: '空心菜' })])]),
  );
  const item = byId(index, 83);
  // The line says `空心菜嫩苗 0.95-1.05 磅` and the recipe says `空心菜`. The
  // row leads with what the user wrote and SHOWS the recipe's word; merging the
  // two would be the confident-wrong-answer class AGENTS.md #3 rules out.
  assert.equal(item.name, '空心菜嫩苗 0.95-1.05 磅');
  assert.equal(item.nameFrom, NAME_FROM_STOCK);
  assert.deepEqual(item.otherNames, ['空心菜']);
  assert.equal(item.state, IN_STOCK);
  assert.equal(item.stockLines.length, 1);
  assert.equal(item.stockLines[0].tier, 2);
});

test('an item only a recipe mentions is named by the recipe and is NOT in stock', () => {
  const index = buildIngredientIndex(
    payload([recipe('煮菜菜', [slot({ pantryItemId: 999, parsedName: 'Arugula', inStock: false })])]),
  );
  const item = byId(index, 999);
  assert.equal(item.name, 'Arugula');
  assert.equal(item.nameFrom, NAME_FROM_RECIPE);
  assert.equal(item.state, OUT_OF_STOCK);
  assert.deepEqual(item.otherNames, []);
  assert.deepEqual(item.stockLines, []);
});

test('an item with no name from either source is labelled by its id, not invented', () => {
  const index = buildIngredientIndex(
    payload([recipe('无名', [slot({ pantryItemId: 7, parsedName: null, rawValue: '' })])]),
  );
  const item = byId(index, 7);
  assert.equal(item.name, 'Pantry Item #7');
  assert.equal(item.nameFrom, NAME_FROM_ID);
});

test('a stock line the join could not explain becomes a MISS, never a row', () => {
  const index = buildIngredientIndex(payload([]));
  // It is reported, so the view can print a pointer to 溯源…
  assert.equal(index.unresolvedLines.length, 1);
  assert.equal(index.unresolvedLines[0].text, 'Manukora Manuka Honey MGO 50+');
  assert.equal(index.unresolvedLines[0].overrideKey, 'manukora manuka honey mgo 50+');
  // …and it is NOT an item. A miss is a pantry line the join could not explain;
  // turning it into a Pantry Item would put a plausible-looking row on a screen
  // whose whole job is to be believed.
  assert.equal(byId(index, 999), undefined);
  for (const item of index.items) {
    assert.ok(!item.name.includes('Manukora'), 'a miss leaked into an item row');
    assert.ok(
      !item.stockLines.some((line) => line.lineIndex === 511),
      'the miss line was merged into an item',
    );
  }
});

test('a miss that somehow carries ids still does not set in-stock', () => {
  // Contradictory by construction — `_miss_line` always publishes an empty
  // `pantryItemIds` — and resolved towards "in stock" it would be the direction
  // that makes a wrong answer look right.
  const index = buildIngredientIndex(
    payload([], {
      stockJoin: {
        lineCount: 1,
        unjoinedCount: 1,
        tierCounts: {},
        guidance: '',
        lines: [
          {
            ...STOCK_LINES[2],
            pantryItemIds: [4242],
          },
        ],
      },
    }),
  );
  assert.equal(byId(index, 4242), undefined);
  assert.equal(index.unresolvedLines.length, 1);
});

test('the duplicate-name join publishes BOTH items, and neither wins', () => {
  const index = buildIngredientIndex(payload([]));
  const first = byId(index, 90);
  const second = byId(index, 116);
  assert.ok(first && second, 'a duplicate-name join lost one of its two ids');
  // Same line on both, because the line really does name both. A winner picked by
  // insertion order is the one failure a name lookup must never have.
  assert.equal(first.stockLines[0].lineIndex, 372);
  assert.equal(second.stockLines[0].lineIndex, 372);
  assert.equal(first.name, second.name);
  assert.equal(first.state, IN_STOCK);
  assert.equal(second.state, IN_STOCK);
});

test('an in-stock item no recipe uses is KEPT and counted', () => {
  const index = buildIngredientIndex(payload([]));
  // 90 and 116 are on the shelf and in no recipe. This is the half of the
  // inventory a recipe-derived view can never produce, and it is the reason this
  // tab is not just the recipe list transposed.
  assert.equal(index.counts.inStock, 3); // 83, 90, 116
  assert.equal(index.counts.inStockWithoutRecipe, 3);
  assert.ok(byId(index, 90).recipes.length === 0);
});

test('nothing is filtered: an item you do NOT have is still a row', () => {
  const index = buildIngredientIndex(
    payload([recipe('煮菜菜', [slot({ pantryItemId: 999, inStock: false })])]),
  );
  // D4 makes a 0/6 recipe a first-class row; the same rule from the other side.
  // An item a recipe needs and the house lacks is the row someone opens this tab
  // to find, so there is no "in stock only" filter anywhere in this module — and
  // the three stock-only rows are here too, because the fixture still has its
  // Pantry.md lines.
  assert.equal(index.items.length, 4);
  assert.equal(byId(index, 999).state, OUT_OF_STOCK);
  assert.deepEqual(byId(index, 999).recipes.map((entry) => entry.noteName), ['煮菜菜']);
});

test('one Pantry Item in two slots of one recipe is ONE recipe entry, two slots', () => {
  const index = buildIngredientIndex(
    payload([
      recipe('炒菜', [
        slot({ index: 0, pantryItemId: 83 }),
        slot({ index: 1, pantryItemId: 83, parsedName: '空心菜' }),
      ]),
    ]),
  );
  const item = byId(index, 83);
  // F2 makes a recipe listing the same Pantry Item twice a conflict the user
  // resolves by editing the note, so it is not impossible on screen — and it
  // must not render as two recipes.
  assert.equal(item.recipes.length, 1);
  assert.equal(item.recipes[0].noteName, '炒菜');
  assert.deepEqual(item.recipes[0].slots.map((entry) => entry.index), [0, 1]);
  assert.equal(item.slotCount, 2);
});

test('the order is in-stock first, then code point, then id', () => {
  const index = buildIngredientIndex(
    payload([
      recipe('z', [
        slot({ pantryItemId: 500, parsedName: 'Zucchini', inStock: false }),
        slot({ index: 1, pantryItemId: 501, parsedName: 'apples', inStock: false }),
        slot({ index: 2, pantryItemId: 502, parsedName: 'Abalone', inStock: false }),
      ]),
    ]),
  );
  // 83/90/116 are in stock from the lines; the three recipe-only items are not.
  // Inside the in-stock group the order is by NAME, and `365 By Whole Foods…`
  // (leading '3', code point 51) sorts before `空心菜嫩苗` — code-point order, not
  // a locale's, so the rendered order cannot depend on the device.
  assert.deepEqual(
    index.items.map((item) => item.id),
    [90, 116, 83, 502, 500, 501],
  );
  // Code point, not locale: `localeCompare` would make the rendered order
  // depend on the device's locale, which is the reason `logic/sort.js` avoids it.
  // So 'A' (65) < 'Z' (90) < 'a' (97) — a locale would interleave the case
  // variants, and three rows whose names differ only in case would reorder
  // themselves when the phone's language changed.
  assert.deepEqual(index.items.slice(3).map((item) => item.name), ['Abalone', 'Zucchini', 'apples']);
});

test('seasoningOnly is a claim about slots, and is null when there are none', () => {
  const index = buildIngredientIndex(
    payload([
      recipe('料', [
        slot({ index: 0, pantryItemId: 1, isSeasoning: true, matchMethod: 'staples' }),
        slot({ index: 1, pantryItemId: 2, isSeasoning: false }),
        slot({ index: 2, pantryItemId: 2, isSeasoning: true }),
      ]),
    ]),
  );
  assert.equal(byId(index, 1).seasoningOnly, true, 'a 调料-only item must read as such');
  assert.equal(byId(index, 2).seasoningOnly, false, 'one 材料 格 is enough to clear it');
  // Known only to the Stock Join: no 格 at all, so "only 调料" is a claim about
  // slots the row does not have.
  assert.equal(byId(index, 83).seasoningOnly, null);
});

test('counts are the numbers the header line prints', () => {
  const index = buildIngredientIndex(
    payload([recipe('拌空心菜', [slot(), slot({ index: 1, pantryItemId: null })])]),
  );
  // The unresolved slot is not counted as a slot of any item — a slot with no
  // Pantry Item is a slot no ingredient row can be built from.
  assert.equal(index.counts.slots, 1);
  assert.equal(index.counts.items, 3); // 83, 90, 116
  assert.equal(index.counts.inStock, 3);
  assert.equal(index.counts.inStockWithoutRecipe, 2); // 90 and 116
  assert.equal(index.counts.stockLines, 3);
  assert.equal(index.counts.unresolvedLines, 1);
});

test('a malformed payload answers empty instead of throwing', () => {
  for (const bad of [undefined, null, {}, { recipes: null }, { recipes: [], stockJoin: 7 }]) {
    const index = buildIngredientIndex(bad);
    assert.deepEqual(index.items, []);
    assert.deepEqual(index.unresolvedLines, []);
    assert.equal(index.counts.items, 0);
  }
});

test('the function is pure: the payload it is given is not mutated', () => {
  const input = payload([recipe('拌空心菜', [slot()])]);
  const before = JSON.stringify(input);
  buildIngredientIndex(input);
  assert.equal(JSON.stringify(input), before);
});

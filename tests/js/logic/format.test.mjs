/* F16 and F17's headline strings, frozen (node --test, no dependencies).
 *
 * Every expectation below is an exact string equality, not a regex: these
 * strings are a contract with the browser flow (§10.5) and with the render
 * ticket, so a drift in the em dash, the separator, or the width of the gap
 * before the strict addendum has to be a failure here.
 *
 * The slots are built by running the real classifier (chip-class.js) and
 * spreading its verdict onto the slot, which is what the render layer does.
 * Composing the two modules is deliberate: an F16 assertion is only meaningful
 * if the "staples still counts as found" half comes from the same code the
 * browser runs.
 */

import assert from 'node:assert/strict';
import test from 'node:test';

import { chipClass } from '../../../app/static/js/logic/chip-class.js';
import { headline } from '../../../app/static/js/logic/format.js';

const inStock = (name, overrides = {}) => ({
  rawValue: name,
  parsedName: name,
  isSeasoning: false,
  matchMethod: 'normalized_exact',
  pantryItemId: 1,
  inStock: true,
  ...overrides,
});

const absent = (name, overrides = {}) => ({
  rawValue: name,
  parsedName: name,
  isSeasoning: false,
  matchMethod: 'unresolved',
  pantryItemId: null,
  inStock: false,
  ...overrides,
});

const seasoning = (name, overrides = {}) => ({
  rawValue: name,
  parsedName: name,
  isSeasoning: true,
  matchMethod: 'unresolved',
  pantryItemId: null,
  inStock: false,
  ...overrides,
});

const staplesSeasoning = (name) =>
  seasoning(name, { matchMethod: 'staples', pantryItemId: null, inStock: false });

const line = (slots, strict = false) =>
  headline({
    strict,
    ingredients: slots.map((slot) => ({ ...slot, ...chipClass({ ...slot, strict }) })),
  });

/* The two Recipes the frozen strings are about: a 6-材料 note and the
 * 花蛤拌饭-shaped note whose 4 Ingredients plus 1 调料 make the strict form. */
const SIX = [
  inStock('Clam'),
  absent('香菇'),
  inStock('茼蒿'),
  absent('娃娃菜'),
  inStock('红苋菜'),
  inStock('开心果酱'),
];

const FOUR_PLUS_SEASONING = [
  inStock('茼蒿'),
  absent('Clam'),
  inStock('红苋菜'),
  absent('香菇'),
  seasoning('生抽'),
];

test('a partly-found Recipe reads n/total with the Materials-only missing list', () => {
  assert.equal(line(SIX), '4/6 ingredients found — missing: 香菇, 娃娃菜');
});

test('a fully-found Recipe reads n/total and nothing else', () => {
  const all = SIX.map((slot) => inStock(slot.parsedName));
  assert.equal(line(all), '6/6 ingredients found');
});

test('a 0/6 Recipe lists every missing Material, untruncated', () => {
  const none = [
    absent('空心菜'),
    absent('香菇'),
    absent('娃娃菜'),
    absent('茼蒿'),
    absent('红苋菜'),
    absent('开心果酱'),
  ];
  assert.equal(
    line(none),
    '0/6 ingredients found — missing: 空心菜, 香菇, 娃娃菜, 茼蒿, 红苋菜, 开心果酱',
  );
});

test('a Recipe with nothing found lists exactly its missing Materials', () => {
  assert.equal(line([absent('空心菜'), absent('香菇'), absent('娃娃菜')]), '0/3 ingredients found — missing: 空心菜, 香菇, 娃娃菜');
});

test('严格模式 adds the Seasoning clause, separated by exactly three spaces', () => {
  assert.equal(
    line(FOUR_PLUS_SEASONING, true),
    '2/5 ingredients found — missing: Clam, 香菇   (严格模式（含调料）: 生抽)',
  );
});

test('F16 — a staples-satisfied Seasoning counts as found under 严格模式', () => {
  const slots = [...FOUR_PLUS_SEASONING.slice(0, 4), staplesSeasoning('生抽')];
  assert.equal(line(slots, true), '3/5 ingredients found — missing: Clam, 香菇');
});

test('F16 — an unresolved Seasoning is excluded entirely outside 严格模式', () => {
  assert.equal(line(FOUR_PLUS_SEASONING), '2/4 ingredients found — missing: Clam, 香菇');
});

test('F16 — the same Seasoning moves the strict numerator and the addendum together', () => {
  const strictWithMissing = line(FOUR_PLUS_SEASONING, true);
  const strictWithFound = line([...FOUR_PLUS_SEASONING.slice(0, 4), staplesSeasoning('生抽')], true);
  assert.equal(strictWithFound.startsWith('3/5'), true);
  assert.equal(strictWithMissing.startsWith('2/5'), true);
  assert.equal(strictWithFound.includes('生抽'), false, 'a found Seasoning is not listed at all');
  assert.equal(strictWithMissing.includes('生抽'), true, 'an unresolved Seasoning is named');
});

test('F17 — the Materials-only clause never names a 调料, in either mode', () => {
  const strict = line(FOUR_PLUS_SEASONING, true);
  const [materials, addendum] = strict.split('   (严格模式（含调料）: ');
  assert.equal(materials.includes('生抽'), false);
  assert.equal(addendum, '生抽)');
  assert.equal(line(FOUR_PLUS_SEASONING).includes('生抽'), false);
});

test('the addendum carries no clause of its own when every Seasoning resolved', () => {
  const slots = [...FOUR_PLUS_SEASONING.slice(0, 4), staplesSeasoning('生抽')];
  assert.equal(line(slots, true).endsWith('ingredients found — missing: Clam, 香菇'), true);
});

test('the addendum still stands alone when every Material is found', () => {
  const slots = SIX.map((slot) => inStock(slot.parsedName)).concat(seasoning('生抽'));
  assert.equal(line(slots, true), '6/7 ingredients found   (严格模式（含调料）: 生抽)');
});

test('the missing list keeps slot order and is never sorted or deduplicated', () => {
  const slots = [absent('香菇'), inStock('Clam'), absent('娃娃菜'), absent('空心菜')];
  assert.equal(line(slots), '1/4 ingredients found — missing: 香菇, 娃娃菜, 空心菜');
});

test('a long missing list is not truncated', () => {
  const names = Array.from({ length: 24 }, (_, index) => `缺料${index}`);
  const rendered = line(names.map((name) => absent(name)));
  assert.equal(rendered.split(' — missing: ')[1], names.join(', '));
  assert.equal(rendered.split(', ').length, 24);
});

test('the missing list prefers parsedName and falls back to rawValue', () => {
  const slots = [
    absent('香菇', { rawValue: '🍄/香菇', parsedName: '香菇' }),
    absent('空心菜', { rawValue: '空心菜', parsedName: null }),
  ];
  assert.equal(line(slots), '0/2 ingredients found — missing: 香菇, 空心菜');
});

test('a missing slot with no name at all is rendered as empty, never as undefined', () => {
  const rendered = line([absent('香菇'), { isSeasoning: false, missing: true }]);
  assert.equal(rendered, '0/2 ingredients found — missing: 香菇, ');
  assert.equal(rendered.includes('undefined'), false);
});

test('a staples-satisfied 材料 counts as found in the default view', () => {
  // §9.13.1 counts 材料 in the default view and nothing but an unresolved slot
  // is missing. F16 is the Seasoning case: a staples Seasoning is excluded by
  // the 调料 rule, and 严格模式 is what re-admits it.
  const slots = [inStock('Clam'), absent('香菇', { matchMethod: 'staples', pantryItemId: null, inStock: false })];
  assert.equal(line(slots), '2/2 ingredients found');
});

test('the headline is deterministic and leaves its input alone', () => {
  const snapshot = structuredClone(FOUR_PLUS_SEASONING);
  const first = line(FOUR_PLUS_SEASONING, true);
  const second = line(FOUR_PLUS_SEASONING, true);
  assert.equal(first, second);
  assert.deepEqual(FOUR_PLUS_SEASONING, snapshot);
});

test('the punctuation is fixed code points, not locale-formatted', () => {
  const rendered = line(FOUR_PLUS_SEASONING, true);
  assert.equal(rendered.includes('—'), true);
  assert.equal(rendered.includes('-'), false, 'ASCII hyphen must not appear in place of the em dash');
  assert.equal(rendered.includes('found — missing: Clam, 香菇   (严格模式（含调料）: 生抽)'), true);
  assert.equal(/[^ ]( {3})\(严格模式/.exec(rendered)[1].length, 3);
});

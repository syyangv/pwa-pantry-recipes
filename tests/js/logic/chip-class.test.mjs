/* D4's five chip buckets, in resolution order (node --test, no dependencies).
 *
 * The order is the contract, so each test pins a bucket together with the
 * inputs that must NOT be able to reach it — a `staples` Seasoning stays found
 * under 严格模式 (F16), and a manual fix keeps its outline whatever the stock
 * tier says.
 */

import assert from 'node:assert/strict';
import test from 'node:test';

import { chipClass } from '../../../app/static/js/logic/chip-class.js';

const MANUAL = { matchMethod: 'manual', pantryItemId: 12, inStock: true, isSeasoning: false, strict: false };

test('a manual fix that resolves to open stock is in-stock with the manual outline', () => {
  assert.deepEqual(chipClass(MANUAL), {
    className: 'chip--in-stock chip--manual',
    missing: false,
    assumed: false,
  });
});

test('a manual fix that resolves to a closed line keeps the outline and says bought-before', () => {
  assert.deepEqual(chipClass({ ...MANUAL, inStock: false }), {
    className: 'chip--have-been-buying chip--manual',
    missing: false,
    assumed: false,
  });
});

test('the manual outline wins over the stock tiers it is combined with', () => {
  const inStock = chipClass({ ...MANUAL, inStock: true, pantryItemId: 7 });
  const notHeld = chipClass({ ...MANUAL, inStock: false, pantryItemId: 7 });
  assert.match(inStock.className, /chip--manual$/);
  assert.match(notHeld.className, /chip--manual$/);
});

test('a resolved Pantry Item is in-stock when the line is open', () => {
  assert.deepEqual(
    chipClass({ matchMethod: 'exact', pantryItemId: 34, inStock: true, isSeasoning: false, strict: false }),
    { className: 'chip--in-stock', missing: false, assumed: false },
  );
});

test('a resolved Pantry Item whose line is not open is have-been-buying, not in-stock', () => {
  assert.deepEqual(
    chipClass({ matchMethod: 'alias', pantryItemId: 34, inStock: false, isSeasoning: false, strict: false }),
    { className: 'chip--have-been-buying', missing: false, assumed: false },
  );
});

test('a staples match is assumed on hand and never missing, even for a Seasoning under 严格模式', () => {
  // F16: this return happens before the isSeasoning && !strict test, so a
  // staple-satisfied Seasoning is found in both modes.
  for (const strict of [false, true]) {
    assert.deepEqual(
      chipClass({ matchMethod: 'staples', pantryItemId: null, inStock: false, isSeasoning: true, strict }),
      { className: 'chip--assumed-staple', missing: false, assumed: true },
    );
  }
});

test('an unresolved Seasoning is ignored in the default view and missing under 严格模式', () => {
  const unresolved = { matchMethod: 'unresolved', pantryItemId: null, inStock: false, isSeasoning: true };
  assert.deepEqual(chipClass({ ...unresolved, strict: false }), {
    className: 'chip--ignored-seasoning',
    missing: false,
    assumed: true,
  });
  assert.deepEqual(chipClass({ ...unresolved, strict: true }), {
    className: 'chip--missing',
    missing: true,
    assumed: false,
  });
});

test('an unresolved Ingredient is missing in both modes', () => {
  for (const strict of [false, true]) {
    assert.deepEqual(
      chipClass({ matchMethod: 'unresolved', pantryItemId: null, inStock: false, isSeasoning: false, strict }),
      { className: 'chip--missing', missing: true, assumed: false },
    );
  }
});

test('both a null and an absent Pantry Item id fall through to the unresolved branch', () => {
  const base = { matchMethod: 'unresolved', inStock: false, isSeasoning: false, strict: false };
  assert.equal(chipClass({ ...base, pantryItemId: null }).className, 'chip--missing');
  assert.equal(chipClass({ ...base, pantryItemId: undefined }).className, 'chip--missing');
  assert.equal(chipClass(base).className, 'chip--missing');
});

test('a non-null Pantry Item id outranks the Seasoning test whatever the method', () => {
  assert.equal(
    chipClass({ matchMethod: 'unresolved', pantryItemId: 3, inStock: true, isSeasoning: true, strict: false })
      .className,
    'chip--in-stock',
  );
});

test('the five buckets and no others are reachable', () => {
  const seen = new Set();
  const inputs = [
    { matchMethod: 'manual', pantryItemId: 1, inStock: true, isSeasoning: false, strict: false },
    { matchMethod: 'manual', pantryItemId: 1, inStock: false, isSeasoning: false, strict: false },
    { matchMethod: 'exact', pantryItemId: 1, inStock: true, isSeasoning: false, strict: false },
    { matchMethod: 'exact', pantryItemId: 1, inStock: false, isSeasoning: false, strict: false },
    { matchMethod: 'staples', pantryItemId: null, inStock: false, isSeasoning: true, strict: true },
    { matchMethod: 'unresolved', pantryItemId: null, inStock: false, isSeasoning: false, strict: false },
    { matchMethod: 'unresolved', pantryItemId: null, inStock: false, isSeasoning: true, strict: false },
  ];
  for (const input of inputs) seen.add(chipClass(input).className);
  assert.deepEqual(
    [...seen].sort(),
    [
      'chip--assumed-staple',
      'chip--have-been-buying',
      'chip--have-been-buying chip--manual',
      'chip--ignored-seasoning',
      'chip--in-stock',
      'chip--in-stock chip--manual',
      'chip--missing',
    ],
  );
});

test('classification is pure: the input is not touched and repeat calls agree', () => {
  const input = { matchMethod: 'exact', pantryItemId: 9, inStock: false, isSeasoning: true, strict: true };
  const snapshot = structuredClone(input);
  const first = chipClass(input);
  const second = chipClass(input);
  assert.deepEqual(input, snapshot);
  assert.deepEqual(first, second);
  assert.notEqual(first, second, 'each call must return its own object, not a shared one');
});

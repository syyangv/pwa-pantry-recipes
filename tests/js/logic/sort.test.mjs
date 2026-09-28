/* D4's three-level display sort (node --test, no dependencies).
 *
 * The point of the third level is that the order is a total order, so the list
 * is deterministic and the browser flow can assert on it. The point of the
 * "nothing is filtered" tests is D4 itself: a 0/6 Recipe is a legitimate
 * shopping-list seed, not a row to hide.
 *
 * The last two tests are not about the sort: they are the collection gate for
 * `npm test`, kept here on purpose so a narrowed glob cannot drop the file that
 * would notice. See the section comment above them.
 */

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

import { EXPECTED_TESTS, collectedTestFiles, isProbeRun, testScript } from '../collection-probe.mjs';
import { sortRecipes } from '../../../app/static/js/logic/sort.js';

const recipe = (noteName, found, total, lastCooked = null) => ({ noteName, found, total, lastCooked });

test('a higher foundRatio comes first', () => {
  const sorted = sortRecipes([
    recipe('low', 2, 6, '2026-01-01'),
    recipe('high', 5, 6, '2020-01-01'),
    recipe('middle', 3, 6, '2019-01-01'),
  ]);
  assert.deepEqual(sorted.map((row) => row.noteName), ['high', 'middle', 'low']);
});

test('a 0/6 Recipe is a first-class row, sorted last, never filtered out', () => {
  const rows = [recipe('nothing', 0, 6), recipe('something', 1, 6), recipe('all', 6, 6)];
  const sorted = sortRecipes(rows);
  assert.equal(sorted.length, 3);
  assert.deepEqual(sorted.map((row) => row.noteName), ['all', 'something', 'nothing']);
});

test('a zero-total Recipe scores 0 rather than dividing by zero', () => {
  const sorted = sortRecipes([recipe('empty', 0, 0), recipe('one', 1, 1), recipe('none-found', 0, 4)]);
  assert.deepEqual(sorted.map((row) => row.noteName), ['one', 'empty', 'none-found']);
});

test('ratios compare across differing totals, not raw found counts', () => {
  const sorted = sortRecipes([recipe('small', 1, 1), recipe('large', 5, 6)]);
  assert.deepEqual(sorted.map((row) => row.noteName), ['small', 'large']);
});

test('within a ratio bucket the more recently cooked Recipe comes first', () => {
  const sorted = sortRecipes([
    recipe('older', 3, 6, '2026-03-01'),
    recipe('newer', 3, 6, '2026-09-27'),
    recipe('oldest', 3, 6, '2025-12-31'),
  ]);
  assert.deepEqual(sorted.map((row) => row.noteName), ['newer', 'older', 'oldest']);
});

test('a Recipe with no last_cooked sorts last inside its own ratio bucket only', () => {
  const sorted = sortRecipes([
    recipe('never-3of6', 3, 6, null),
    recipe('cooked-3of6', 3, 6, '2020-01-01'),
    recipe('never-6of6', 6, 6, null),
  ]);
  assert.deepEqual(sorted.map((row) => row.noteName), ['never-6of6', 'cooked-3of6', 'never-3of6']);
});

test('an empty-string last_cooked is treated as never cooked', () => {
  const sorted = sortRecipes([recipe('blank', 1, 1, ''), recipe('cooked', 1, 1, '2026-01-01')]);
  assert.deepEqual(sorted.map((row) => row.noteName), ['cooked', 'blank']);
});

test('noteName breaks a tie in code-point order, not locale order', () => {
  // 'Z' (U+005A) sorts before 'a' (U+0061) by code point; a locale-sensitive
  // comparison would order them the other way on several locales.
  const sorted = sortRecipes([recipe('a', 1, 1), recipe('Z', 1, 1), recipe('A', 1, 1)]);
  assert.deepEqual(sorted.map((row) => row.noteName), ['A', 'Z', 'a']);
});

test('Chinese note names break a tie by code point', () => {
  const sorted = sortRecipes([recipe('香菇', 1, 1), recipe('娃娃菜', 1, 1), recipe('拌空心菜', 1, 1)]);
  assert.deepEqual(sorted.map((row) => row.noteName), ['娃娃菜', '拌空心菜', '香菇']);
});

test('the order does not depend on the input order', () => {
  const rows = [
    recipe('香菇', 4, 6, '2026-02-02'),
    recipe('娃娃菜', 4, 6, '2026-02-02'),
    recipe('茼蒿', 4, 6, null),
    recipe('红苋菜', 6, 6, '2025-01-01'),
    recipe('开心果酱', 0, 6, '2026-09-01'),
  ];
  const expected = ['红苋菜', '娃娃菜', '香菇', '茼蒿', '开心果酱'];
  assert.deepEqual(sortRecipes(rows).map((row) => row.noteName), expected);
  assert.deepEqual(sortRecipes([...rows].reverse()).map((row) => row.noteName), expected);
  assert.deepEqual(sortRecipes(sortRecipes(rows)).map((row) => row.noteName), expected);
});

test('the caller array is not mutated and the same rows come back', () => {
  const rows = [recipe('low', 1, 6), recipe('high', 6, 6)];
  const sorted = sortRecipes(rows);
  assert.deepEqual(rows.map((row) => row.noteName), ['low', 'high']);
  assert.deepEqual(sorted.map((row) => row.noteName), ['high', 'low']);
  assert.notEqual(sorted, rows);
});

test('a single row and an empty list are both fine', () => {
  const one = [recipe('only', 2, 3)];
  assert.deepEqual(sortRecipes(one), one);
  assert.deepEqual(sortRecipes([]), []);
});

/* --- what `npm test` actually collects -------------------------------------
 *
 * These two live under tests/js/logic/ deliberately. The regression they exist
 * to catch is a narrowed glob in package.json: unquoted, `npm` hands the
 * pattern to /bin/sh, /bin/sh has no globstar, and it expands one level down —
 * so every top-level test file, the SHELL_ASSETS gate among them, is dropped
 * while the suite still reports 0 failures. A collection gate in a top-level
 * file would be dropped by that same regression, so this one sits where the
 * broken spelling is still collected. scaffold.test.mjs holds the mirror gate
 * for the narrowing that drops this directory instead.
 */

test('the test script reaches Node as one quoted pattern, not a shell expansion', () => {
  // Single quotes are what keep the literal `**` intact for Node's own glob
  // engine. A second working spelling would be worse than one wrong one:
  // nothing compares the two, and only one of them survives a third directory.
  assert.equal(testScript(), "node --test 'tests/js/**/*.test.mjs'");
  const expansion = spawnSync('/bin/sh', ['-c', `echo ${testScript()}`], { encoding: 'utf8' });
  assert.deepEqual(expansion.stdout.trim().split(' '), ['node', '--test', 'tests/js/**/*.test.mjs']);
});

test(
  'npm test collects the top-level test files as well as this one',
  { skip: isProbeRun() ? 'this is the probe run' : false },
  () => {
    const collected = collectedTestFiles();
    assert.deepEqual(collected, EXPECTED_TESTS, `collected: ${collected.join(', ')}`);
    assert.equal(collected.length, EXPECTED_TESTS.length);
  },
);

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
import { copyFileSync, mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

import { EXPECTED_TESTS, collectedTestFiles, isProbeRun, testScript } from '../collection-probe.mjs';
import { collectTestFiles } from '../../../scripts/run-js-tests.mjs';
import { sortRecipes } from '../../../app/static/js/logic/sort.js';

const REPO_ROOT_FOR_TESTS = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');

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

test('the test script hands Node a file list, so no shell can narrow it', () => {
  /* This replaced a test that asserted the exact old command string, a
   * `node --test` invocation carrying a recursive `**` glob, and verified the
   * single quotes survived /bin/sh. That property is GONE and the guard is now
   * stronger: there is no glob in the command at all, so there is nothing for a
   * shell to expand, mis-expand, or word-split differently under zsh than under
   * sh.
   *
   * The old form failed in two ways, both silent:
   *   - unquoted under /bin/sh (what npm uses): `**` narrows to one level, whole
   *     test files are missing, and the suite reports 0 failures;
   *   - `node --test $FILES` under zsh: no word splitting, so Node is handed ONE
   *     argument and reports `Could not find 'a b c'`.
   * A command with no `*` in it cannot be subject to either.
   */
  assert.equal(testScript(), 'node scripts/run-js-tests.mjs');
  assert.ok(
    !testScript().includes('*'),
    `the test script must carry no glob for a shell to mangle: ${testScript()}`,
  );
  // One spelling only. A second working spelling would be worse than one wrong
  // one: nothing compares the two, and only one of them survives a third
  // directory.
  const echoed = spawnSync('/bin/sh', ['-c', `echo ${testScript()}`], { encoding: 'utf8' });
  assert.deepEqual(echoed.stdout.trim().split(' '), ['node', 'scripts/run-js-tests.mjs']);
});

test('the runner discovers exactly the files the probe expects', () => {
  /* Ties the guard to the runner directly, so the two cannot drift: the probe
   * re-runs the real command and records what Node actually started, while this
   * asks the runner's own discovery what it intends to start. If a new test file
   * is added and not listed in EXPECTED_TESTS, both halves move together and the
   * comparison below fails — the §7.1 failure mode in reverse, where a gate
   * exists and is not running. */
  const discovered = collectTestFiles().map((file) =>
    relative(REPO_ROOT_FOR_TESTS, file).split(sep).join('/'),
  );
  assert.deepEqual(discovered, EXPECTED_TESTS, `discovered: ${discovered.join(', ')}`);
  // And a nested one is included, which is the whole reason the old `**` existed.
  assert.ok(
    discovered.includes('tests/js/logic/sort.test.mjs'),
    'the nested logic gate is not discovered',
  );
});

test('an empty test tree exits non-zero instead of reporting a vacuous pass', () => {
  /* The reason this runner exists at all. `node --test` handed an empty file
   * list exits 0, so a discovery bug that collects nothing produces a GREEN
   * suite that ran no tests — and both historical failures of the globbed
   * command were exactly that, with no exit code to catch them. */
  const workdir = mkdtempSync(join(tmpdir(), 'empty-js-tests-'));
  try {
    mkdirSync(join(workdir, 'scripts'), { recursive: true });
    mkdirSync(join(workdir, 'tests', 'js'), { recursive: true });
    copyFileSync(
      join(REPO_ROOT_FOR_TESTS, 'scripts', 'run-js-tests.mjs'),
      join(workdir, 'scripts', 'run-js-tests.mjs'),
    );
    const run = spawnSync(process.execPath, ['scripts/run-js-tests.mjs'], {
      cwd: workdir,
      encoding: 'utf8',
    });
    assert.equal(run.status, 2, `expected a non-zero exit, got ${run.status}`);
    assert.match(run.stderr, /refusing to report a pass/);
  } finally {
    rmSync(workdir, { recursive: true, force: true });
  }
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

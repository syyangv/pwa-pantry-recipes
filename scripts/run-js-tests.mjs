#!/usr/bin/env node
/* Run every test file under tests/js, discovered IN NODE rather than by a shell.
 *
 * **This exists because the previous form was shell-dependent in two different
 * ways, and both fail silently rather than loudly.**
 *
 * `npm test` was `node --test` with a `**`-glob passed through a shell. The pattern is quoted on
 * purpose: `npm` runs scripts through `/bin/sh`, which has no `globstar`, so an
 * unquoted `**` narrows to one level, Node is handed a list with whole test
 * files missing, and the suite reports 0 failures having never started them. But
 * the safety depended on the quoting surviving, and nothing verified that it did:
 *
 *   - under `/bin/sh` (what npm uses) an unquoted `**` silently collects a
 *     subset and passes;
 *   - under zsh, `node --test $FILES` passes every path as ONE argument, and
 *     Node reports `Could not find 'a b c'` and runs 0 tests.
 *
 * Both are "the tests did not run" wearing "the tests passed". The reporter on
 * Node 22 names no file it ran, so nothing downstream can tell.
 *
 * So: the glob is gone. This script walks `tests/js` itself, sorts the result
 * (a stable order makes a failure reproducible), refuses to run an empty set
 * rather than reporting a vacuous pass, and spawns `node --test` with an argv
 * array — which no shell word-splits. `tests/js/collection-probe.mjs` re-runs
 * this same module and asserts the collected set, so the guard and the runner
 * cannot drift apart.
 *
 * Exit code is Node's own, passed through unchanged: a failing test must fail the
 * command, and a crash in this script must not be reported as a green suite.
 */

import { spawnSync } from 'node:child_process';
import { readdirSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const TEST_ROOT = join(ROOT, 'tests', 'js');
const SUFFIX = '.test.mjs';

/**
 * Every `*.test.mjs` under `dir`, recursively, sorted.
 *
 * Symlinked directories are NOT followed, and that is load-bearing rather than
 * incidental: `readdirSync(withFileTypes)` reports `isDirectory() === false` for
 * a symlink, so the recursion cannot cycle. A test tree is checked-in source, but
 * a symlink loop here would hang the suite instead of failing it, which is the
 * one outcome a test runner must never produce.
 */
export function collectTestFiles(dir = TEST_ROOT) {
  const found = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      found.push(...collectTestFiles(full));
    } else if (entry.isFile() && entry.name.endsWith(SUFFIX)) {
      found.push(full);
    }
  }
  return found.sort();
}

function main() {
  let files;
  try {
    files = collectTestFiles();
  } catch (error) {
    process.stderr.write(`run-js-tests: cannot read ${TEST_ROOT}: ${error.message}\n`);
    return 2;
  }
  if (files.length === 0) {
    // The whole point of this file. `node --test` on an empty set exits 0, which
    // is a green suite that ran nothing.
    process.stderr.write(
      `run-js-tests: no *${SUFFIX} under ${relative(ROOT, TEST_ROOT)} — refusing to report a pass\n`,
    );
    return 2;
  }
  const relativeFiles = files.map((file) => relative(ROOT, file));
  process.stderr.write(`run-js-tests: ${relativeFiles.length} test files\n`);
  const result = spawnSync(process.execPath, ['--test', ...relativeFiles], {
    cwd: ROOT,
    stdio: 'inherit',
  });
  if (result.error) {
    process.stderr.write(`run-js-tests: ${result.error.message}\n`);
    return 2;
  }
  // `status` is null when the child died on a signal; that is not a pass either.
  return typeof result.status === 'number' ? result.status : 2;
}

if (process.argv[1] && import.meta.url === `file://${process.argv[1]}`) {
  process.exit(main());
}

export { TEST_ROOT, SUFFIX };

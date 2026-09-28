/* What `npm test` actually collects, for the two gates that assert it.
 *
 * `npm` runs scripts through /bin/sh, which has no globstar. A recursive
 * pattern that reaches that shell unquoted is expanded one level down, and the
 * runner is handed a list with whole test files missing from it — the suite
 * then reports 0 failures having never started them. No exit code catches that,
 * and no reporter on Node 22 names the files it ran (TAP, spec and junit all
 * name tests), so this module re-runs package.json's own script through the
 * same shell and records the set from the inside:
 *
 *   - loaded with --import (through NODE_OPTIONS) into every process the runner
 *     spawns, each started file appends its own entry script;
 *   - imported by scaffold.test.mjs and tests/js/logic/sort.test.mjs, which
 *     compare the recorded set against EXPECTED_TESTS.
 *
 * Both callers are needed. A gate in tests/js/scaffold.test.mjs is dropped by
 * the unquoted-`**` narrowing, and a gate in tests/js/logic/ is dropped by the
 * one-level `tests/js/*.test.mjs` narrowing — each survives the regression the
 * other cannot see.
 */

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { appendFileSync, existsSync, mkdtempSync, readFileSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

export const EXPECTED_TESTS = [
  'tests/js/logic/chip-class.test.mjs',
  'tests/js/logic/format.test.mjs',
  'tests/js/logic/sort.test.mjs',
  'tests/js/scaffold.test.mjs',
  'tests/js/shell_assets.test.mjs',
];

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const SIDECAR = fileURLToPath(import.meta.url);

export const testScript = () =>
  JSON.parse(readFileSync(join(REPO_ROOT, 'package.json'), 'utf8')).scripts.test;

export const isProbeRun = () => process.env.PANTRY_COLLECTOR_NESTED === '1';

export function collectedTestFiles() {
  const workdir = mkdtempSync(join(tmpdir(), 'collected-'));
  const record = join(workdir, 'collected.txt');
  // NODE_TEST_CONTEXT is how a spawned test file knows that it is one, and the
  // runner refuses to start a nested run while it is set.
  const { NODE_TEST_CONTEXT, ...outerEnv } = process.env;
  try {
    const run = spawnSync('/bin/sh', ['-c', testScript()], {
      cwd: REPO_ROOT,
      encoding: 'utf8',
      env: {
        ...outerEnv,
        NODE_OPTIONS: `--import ${SIDECAR}`,
        PANTRY_COLLECTOR_NESTED: '1',
        PANTRY_COLLECTED_FILES: record,
      },
    });
    assert.equal(run.error, undefined, `could not re-run the test script: ${run.error}`);
    assert.ok(existsSync(record), `the collector sidecar never ran: ${run.stderr}`);
    const root = realpathSync(REPO_ROOT);
    return readFileSync(record, 'utf8')
      .split('\n')
      .filter((line) => line !== '')
      .map((line) => relative(root, realpathSync(line)).split(sep).join('/'))
      .sort();
  } finally {
    rmSync(workdir, { recursive: true, force: true });
  }
}

/* The sidecar half. The runner's own parent process has no entry script, so
 * only a collected test file writes here; the guard keeps an ordinary
 * `npm test` from paying for this module at all. */
const target = process.env.PANTRY_COLLECTED_FILES;
const entry = process.argv[1];
if (target && typeof entry === 'string' && entry.endsWith('.test.mjs'))
  appendFileSync(target, `${entry}\n`);

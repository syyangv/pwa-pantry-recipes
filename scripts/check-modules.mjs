/* `npm run check` — the module-graph syntax gate.
 *
 * `node --check FILE` checks ONE file and does not follow import specifiers
 * (verified: `node --check ok.js bad.js` exits 0 having checked only ok.js).
 * So the scaffold's script, `node --check app/static/js/main.js`, was green
 * for a graph in which any submodule could contain a syntax error — and this
 * app has fifteen modules. The spec's stated intent is "node --check on the
 * module-graph entry and the worker"; making that true for a graph is this
 * script's whole job.
 *
 * It also resolves every import specifier, which no syntax check can do: a
 * typo in a module path is a runtime 404 and, for a precached module, a hole
 * in the offline shell. `tests/js/scaffold.test.mjs` asserts the same thing
 * from the other side; this one runs before any test does, so a broken graph
 * fails in a second rather than after a full suite.
 *
 * No dependencies, no build step: `node scripts/check-modules.mjs`.
 */

import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join, posix, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const STATIC_DIR = join(REPO_ROOT, 'app', 'static');

const stripJsComments = (js) => js.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '');

function walk(dir) {
  const files = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) files.push(...walk(full));
    else if (entry.name.endsWith('.js')) files.push(full);
  }
  return files.sort();
}

const modules = walk(join(STATIC_DIR, 'js'));
const worker = join(STATIC_DIR, 'sw.js');
const targets = [...modules, worker];

const failures = [];
for (const file of targets) {
  const result = spawnSync(process.execPath, ['--check', file], { encoding: 'utf8' });
  if (result.status !== 0) {
    failures.push(`${relative(REPO_ROOT, file)}\n${(result.stderr || '').trim()}`);
  }
}

for (const file of modules) {
  const source = stripJsComments(readFileSync(file, 'utf8'));
  for (const [, specifier] of source.matchAll(/\bfrom\s+'([^']+)'/g)) {
    const target = specifier.split('?')[0];
    const resolved = target.startsWith('/js/')
      ? join(STATIC_DIR, 'js', target.slice('/js/'.length))
      : join(dirname(file), target);
    if (!existsSync(resolved)) {
      failures.push(
        `${relative(REPO_ROOT, file)} imports ${specifier}, which does not exist`,
      );
    }
    if (!specifier.includes('?v=__APP_VERSION__')) {
      failures.push(`${relative(REPO_ROOT, file)} imports ${specifier} without the version token`);
    }
  }
}

if (failures.length > 0) {
  for (const failure of failures) process.stderr.write(`${failure}\n`);
  process.stderr.write(`\ncheck-modules: ${failures.length} problem(s)\n`);
  process.exit(1);
}

process.stdout.write(
  `check-modules: ${modules.length} modules + sw.js parse, and every import resolves\n`,
);

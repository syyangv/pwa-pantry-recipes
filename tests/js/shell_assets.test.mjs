/* Completeness gate for the service worker's precache (node --test, no deps).
 *
 * scaffold.test.mjs already fails when a SHELL_ASSETS entry has no file behind
 * it. That is the harmless direction. This file covers the damaging one: a
 * module or stylesheet under app/static/js or app/static/css that NOTHING
 * precaches. It is served, imported, and cached by nothing — it works
 * perfectly online and is simply absent from an installed app, which is
 * diagnosable only by a user on a subway. Both directions live here so they
 * are read together.
 *
 * The vendored js/pwa/ modules are excluded: the boot graph pulls them in as
 * static imports and the precache list names the ones main.js imports, not the
 * whole vendor directory.
 */

import assert from 'node:assert/strict';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join, posix, relative, sep } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const STATIC_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'app', 'static');
const sw = readFileSync(join(STATIC_DIR, 'sw.js'), 'utf8');

const VENDORED = 'js/pwa/';

function walkFiles(dir) {
  const files = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) files.push(...walkFiles(full));
    else if (entry.isFile()) files.push(posix.join(...relative(STATIC_DIR, full).split(sep)));
  }
  return files;
}

const frontendFiles = [...walkFiles(join(STATIC_DIR, 'js')), ...walkFiles(join(STATIC_DIR, 'css'))]
  .filter((file) => !file.startsWith(VENDORED))
  .sort();

/* Entries are literals or `'/path?v=' + CACHE_VERSION` concatenations, so the
 * quoted literal is the stable part and the query is dropped. */
const shellAssetsBlock = sw.match(/const SHELL_ASSETS = \[([\s\S]*?)\n\];/);
assert.ok(shellAssetsBlock, 'SHELL_ASSETS not found in sw.js');
const precached = new Set(
  [...shellAssetsBlock[1].matchAll(/'([^']*)'/g)]
    .map((match) => match[1].split('?')[0])
    // '/' is the HTML shell, served by a route rather than a static file.
    .filter((pathname) => pathname !== '/')
    .map((pathname) => pathname.replace(/^\//, ''))
);

test('every frontend module and stylesheet is precached', () => {
  const missing = frontendFiles.filter((file) => !precached.has(file));
  assert.deepEqual(
    missing,
    [],
    `missing from SHELL_ASSETS (absent from the offline shell): ${missing.join(', ')}`,
  );
});

test('every precached path has a file behind it', () => {
  const dangling = [...precached].filter((file) => !existsSync(join(STATIC_DIR, file)));
  assert.deepEqual(dangling, [], `SHELL_ASSETS references a missing file: ${dangling.join(', ')}`);
});

test('CACHE_VERSION is the Pattern A single source in vX.Y.Z form', () => {
  const version = sw.match(/const CACHE_VERSION = '([^']*)'/);
  assert.ok(version, 'CACHE_VERSION not found in sw.js');
  assert.match(version[1], /^v\d+\.\d+\.\d+$/);
});

test('the API and health routes stay network-only', () => {
  const prefixes = sw.match(/const NETWORK_ONLY_PREFIXES = \[([\s\S]*?)\];/);
  assert.ok(prefixes, 'NETWORK_ONLY_PREFIXES not found in sw.js');
  assert.match(prefixes[1], /'\/api\/'/);
  assert.match(sw, /const NETWORK_ONLY_EXACT = \[[^\]]*'\/health'/);
});

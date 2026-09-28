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
 * The vendored js/pwa/ modules are excluded from the DIRECTORY walk: the boot
 * graph pulls them in as static imports and the precache list names the ones
 * the app reaches, not the whole vendor directory — js/pwa/badge.js is
 * opt-in infrastructure that nothing imports, and demanding it be precached
 * would be a false alarm, while excluding the whole directory is a hole big
 * enough to ship a cold-offline-start break in. So the vendored dir gets its
 * own gate instead: the boot-graph walk at the bottom of this file, which
 * starts at js/main.js and follows static imports THROUGH js/pwa/, and asserts
 * that everything it reaches is precached. Both directions live here so they
 * are read together.
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
 * quoted literal is the stable part and the query is dropped.
 *
 * Comments are stripped before the quoted-literal regex runs, for the same
 * reason scaffold.test.mjs does it: an apostrophe inside a comment opens a
 * phantom "entry" that runs to the next quote, and a phantom entry is either a
 * false alarm on a correct commit or — worse — an excuse to delete the comment
 * instead of the real gap. See the comment in scaffold.test.mjs. */
const stripJsComments = (js) => js.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '');

const shellAssetsBlock = sw.match(/const SHELL_ASSETS = \[([\s\S]*?)\n\];/);
assert.ok(shellAssetsBlock, 'SHELL_ASSETS not found in sw.js');
const precached = new Set(
  [...stripJsComments(shellAssetsBlock[1]).matchAll(/'([^']*)'/g)]
    .map((match) => match[1].split('?')[0])
    // '/' is the HTML shell, served by a route rather than a static file.
    .filter((pathname) => pathname !== '/')
    .map((pathname) => pathname.replace(/^\//, ''))
);

/* THE BOOT GRAPH. The directory walk above cannot see js/pwa/, which is where
 * the real cold-offline-start break lived: outbox.js (reached through
 * js/domain-intents.js), pull-refresh.js and unstick-on-timeout.js (reached
 * through the views) were all imported, served, and precached by nothing, so
 * the app worked perfectly online and could not boot at all on a first offline
 * launch. Naming three more modules by hand would have fixed this instance and
 * left the next one unguarded, so the gate is derived instead: walk the static
 * import graph from js/main.js and require every module it reaches to be
 * precached. It covers the vendored dir and any future one for free, and it
 * does not over-reach — a module nothing imports (js/pwa/badge.js, opt-in
 * infrastructure documented by a usage comment at the top of the file) is not
 * reachable and is not required.
 *
 * Why static and not a browser: there is no offline harness in this repo, and
 * `npm run check` resolves specifiers against the FILESYSTEM, so it cannot
 * distinguish "on disk" from "in the precache" — the exact distinction at issue.
 * Reachability is the property that makes the cache requirement true, so the
 * gate asserts reachability. */
const BOOT_ENTRY = 'js/main.js';

/* `from '…'`, side-effect `import '…'`, and dynamic `import('…')` in one pass;
 * a module reached only dynamically is just as unbootable offline. Comments are
 * stripped first, for the reason above: the vendored modules document their own
 * usage with `import { initBadge } from '/js/pwa/badge.js';` in a header
 * comment, and an unstripped walk would read that as a real edge. */
const IMPORT_RE = /(?:\bfrom\s*|\bimport\s*|\bimport\s*\(\s*)'([^']+)'/g;

function localSpecifiers(importer) {
  const source = readFileSync(join(STATIC_DIR, importer), 'utf8');
  return [...stripJsComments(source).matchAll(IMPORT_RE)].map((match) => {
    const target = match[1].split('?')[0];
    // A root-absolute specifier is what a browser resolves against the origin;
    // a relative one against the importing module. Anything else is not a file
    // in this app (no bare specifiers, no URLs) and contributes no edge.
    if (!target.startsWith('/') && !target.startsWith('.')) return null;
    if (!target.endsWith('.js')) return null;
    return target.startsWith('/')
      ? target.slice(1)
      : posix.normalize(posix.join(posix.dirname(importer), target));
  }).filter((file) => file !== null && existsSync(join(STATIC_DIR, file)));
}

/* Depth-first from the entry, so `seen` is the full transitive closure. */
function bootGraph(entry = BOOT_ENTRY) {
  const seen = new Set();
  const queue = [entry];
  while (queue.length > 0) {
    const file = queue.pop();
    if (seen.has(file) || !existsSync(join(STATIC_DIR, file))) continue;
    seen.add(file);
    for (const dependency of localSpecifiers(file)) {
      if (!seen.has(dependency)) queue.push(dependency);
    }
  }
  return seen;
}

const reachable = bootGraph();

test('every SHELL_ASSETS entry is a path, not comment debris', () => {
  const entries = [...stripJsComments(shellAssetsBlock[1]).matchAll(/'([^']*)'/g)].map(
    (match) => match[1],
  );
  assert.ok(entries.length > 0, 'SHELL_ASSETS is empty');
  for (const entry of entries) {
    // '/' is the HTML shell itself, served by a route rather than a file.
    assert.match(entry, /^\/[\w./-]*(\?|$)/, `not a shell asset path: ${entry}`);
  }
});

test('every frontend module and stylesheet is precached', () => {
  const missing = frontendFiles.filter((file) => !precached.has(file));
  assert.deepEqual(
    missing,
    [],
    `missing from SHELL_ASSETS (absent from the offline shell): ${missing.join(', ')}`,
  );
});

/* NON-VACUITY, for the gate directly above. A gate that compares two sets
 * derived from the same source can be green because both sides are empty, or
 * because a name was spelled differently on the two sides and the comparison
 * silently found nothing to complain about. This one proves the comparison
 * still bites: it removes ONE real, precached file from the precached set and
 * asserts the same expression then reports exactly that file. If a future
 * refactor of `precached` or `frontendFiles` breaks the comparison, this fails
 * first and says which half went wrong. */
test('the precache completeness gate still catches an unprecached file', () => {
  const probe = 'js/logic/sort.js';
  assert.ok(precached.has(probe), `the probe file itself is not precached: ${probe}`);
  assert.ok(frontendFiles.includes(probe), 'the probe file is not in the walked set');
  const withoutProbe = new Set([...precached].filter((file) => file !== probe));
  const caught = frontendFiles.filter((file) => !withoutProbe.has(file));
  assert.deepEqual(caught, [probe], 'dropping one precached file was not detected');
  // And the negative control: the real set is complete, so the gate's own
  // answer is an empty list for a reason rather than by construction.
  assert.deepEqual(frontendFiles.filter((file) => !precached.has(file)), []);
  // The set is not empty either. A comparison between two empty sets passes
  // forever and means nothing.
  assert.ok(precached.size >= frontendFiles.length, 'the precached set is smaller than the walked set');
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

test('every module reachable in the boot graph is precached', () => {
  // A cold offline start resolves the import graph out of the shell cache with
  // no network to fall back on, so a module that is reachable but unprecached
  // is a hard boot failure on the one launch path this app exists for.
  const missing = [...reachable].filter((file) => !precached.has(file)).sort();
  assert.deepEqual(
    missing,
    [],
    `reachable from ${BOOT_ENTRY} but absent from SHELL_ASSETS (cold offline start fails): ${missing.join(', ')}`,
  );
});

test('the boot graph really is walked, and through the vendored directory', () => {
  // Non-vacuity, part 1: the walk found something. A parser change that broke
  // the regex would leave `reachable` holding only the entry module, and a
  // one-element set that happens to be precached passes the gate above forever.
  assert.ok(reachable.size >= 20, `the boot graph resolved to only ${reachable.size} module(s)`);
  assert.ok(reachable.has(BOOT_ENTRY), `the boot graph does not contain its own entry: ${BOOT_ENTRY}`);
  // Non-vacuity, part 2: it followed transitive edges rather than only reading
  // main.js's own import statements — pull-refresh.js is named by no first-level
  // import, only by js/views/home.js's.
  assert.ok(reachable.has('js/views/home.js'), 'the walk did not reach the views');
  assert.ok(reachable.has('js/pwa/pull-refresh.js'), 'the walk did not reach the vendored modules');
  // Non-vacuity, part 3: the vendored dir is genuinely in scope, which is the
  // whole reason this gate exists. js/pwa/badge.js is NOT asserted either way —
  // nothing imports it, so demanding it would be the false alarm the directory
  // filter was avoiding.
  const vendored = [...reachable].filter((file) => file.startsWith(VENDORED)).sort();
  assert.deepEqual(
    vendored,
    [
      'js/pwa/outbox.js',
      'js/pwa/pull-refresh.js',
      'js/pwa/unstick-on-timeout.js',
      'js/pwa/update-manager.js',
      'js/pwa/waking-banner.js',
    ],
    'the set of vendored modules the boot graph reaches changed; reconcile the names above',
  );
});

test('the boot-graph gate still catches an unprecached reachable module', () => {
  // NON-VACUITY, part 4: the gate as it would have run against the shipped
  // defect. Remove ONE real entry from the precached set and assert the gate's
  // own expression then reports exactly that module. The probe is
  // js/pwa/outbox.js — the module whose missing entry broke a cold offline
  // start — chosen deliberately over a first-party module, because a probe
  // outside the directory-filtered vendor dir is the one that proves the
  // filtering gap is genuinely closed.
  const probe = 'js/pwa/outbox.js';
  assert.ok(reachable.has(probe), `the probe is not in the boot graph: ${probe}`);
  assert.ok(precached.has(probe), `the probe is not precached: ${probe}`);
  const withoutProbe = new Set([...precached].filter((file) => file !== probe));
  const caught = [...reachable].filter((file) => !withoutProbe.has(file)).sort();
  assert.deepEqual(caught, [probe], 'dropping one precached reachable module was not detected');
  // And the negative control: against the real set the gate's answer is empty
  // for a reason, not by construction.
  assert.deepEqual([...reachable].filter((file) => !precached.has(file)), []);
});

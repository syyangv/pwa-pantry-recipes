/* Structural gate for the boot contract (node --test, no dependencies).
 *
 * These assertions are cheap and catch the exact regressions the pwa-infra
 * docs call out, so they belong in CI rather than in a code review checklist:
 *
 *  - No inline `serviceWorker.register` in index.html. update-manager owns
 *    registration; a second registration (especially with a version-pinned
 *    URL) reinstalls the worker on every load and can loop reloads
 *    (docs/pwa-template.md Part 3c).
 *  - Every mutable asset URL in the shell is version-pinned with the
 *    __APP_VERSION__ token, not a hand-typed literal that drifts on the next
 *    bump (Part 3b).
 *  - ES-module imports carry the same token, so the /js/{path} route's
 *    content injection version-pins the whole graph.
 *  - sw.js SHELL_ASSETS stays aligned with the files that actually exist.
 *  - `npm test` still collects the nested test files. A test file the runner
 *    never starts is not coverage, it is a file that reads like coverage.
 */

import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join, posix, relative, sep } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { EXPECTED_TESTS, collectedTestFiles, isProbeRun } from './collection-probe.mjs';

const STATIC_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'app', 'static');
const read = (relative) => readFileSync(join(STATIC_DIR, relative), 'utf8');

/* Recursive, so a module added under a new subdirectory is covered without
 * editing this file. `/bin/sh` has no globstar, which is the whole reason
 * `npm test`'s pattern is quoted — see collection-probe.mjs. */
function walkJs(dir = join(STATIC_DIR, 'js')) {
  const files = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) files.push(...walkJs(full));
    else if (entry.name.endsWith('.js')) files.push(posix.join(...relative(STATIC_DIR, full).split(sep)));
  }
  return files.sort();
}

/* Comments legitimately *mention* `serviceWorker.register` while forbidding it,
 * so the checks below run against comment-stripped source. Stripping is
 * deliberately naive (no string-literal awareness): it can only ever remove
 * text, so a real registration call can never be hidden by the stripper. */
const stripHtmlComments = (html) => html.replace(/<!--[\s\S]*?-->/g, '');
const stripJsComments = (js) => js.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '');

const index = read('index.html');
const main = read('js/main.js');
const sw = read('sw.js');

const indexCode = stripHtmlComments(index);
const mainCode = stripJsComments(main);

test('the HTML shell never registers the service worker inline', () => {
  assert.equal(indexCode.includes('serviceWorker'), false);
  assert.equal(/<script\b(?![^>]*\bsrc=)/i.test(indexCode), false);
});

test('the HTML shell has no inline script at all (CSP-clean version pin)', () => {
  // Only the external module entry point is allowed. An inline
  // `window.__APP_VERSION__ = ...` would reintroduce the inline-script
  // pattern; the value comes from the meta tag instead.
  const scripts = indexCode.match(/<script\b[^>]*>/g) ?? [];
  assert.equal(scripts.length, 1, `expected exactly one script tag, got: ${scripts}`);
  assert.match(scripts[0], /type="module"/);
  assert.match(scripts[0], /src="\/js\/main\.js\?v=__APP_VERSION__"/);
});

test('the shell embeds the serve-injected version meta tag', () => {
  assert.match(index, /<meta name="app-version" content="__APP_VERSION__">/);
  // The token may only appear inside a served URL or that one meta tag —
  // a bare literal in text content would be a second, driftable source.
  const outsideUrls = indexCode.replace(/(?:href|src|content)="[^"]*"/g, '');
  assert.equal(outsideUrls.includes('__APP_VERSION__'), false);
});

test('every mutable stylesheet link is version-pinned', () => {
  for (const match of indexCode.matchAll(/<link[^>]+rel="stylesheet"[^>]*>/g)) {
    assert.match(match[0], /\?v=__APP_VERSION__"/, `unpinned stylesheet: ${match[0]}`);
  }
});

test('main.js imports the vendored pwa-infra modules with the version token', () => {
  assert.match(
    mainCode,
    /import \{ initUpdateManager[^}]*\} from '\/js\/pwa\/update-manager\.js\?v=__APP_VERSION__'/,
  );
  assert.match(
    mainCode,
    /import \{ initWakingBanner[^}]*\} from '\/js\/pwa\/waking-banner\.js\?v=__APP_VERSION__'/,
  );
  // Registration is the manager's job, so main.js must not call it directly.
  assert.equal(mainCode.includes('serviceWorker'), false);
  assert.match(mainCode, /initWakingBanner\(/);
  assert.match(mainCode, /initUpdateManager\(/);
});

/* F18 (locked) §4d, on both sides at once. The two knobs are a PAIR: a
 * WAIT_FOR_MESSAGE = false worker skipWaiting()s on install, so `autoApply:
 * false` alone would mean the banner never appears and the new worker takes
 * over on its own — which is the auto-takeover the decision rejects. */
test('the update UX is the F18 explicit banner, not auto-takeover', () => {
  assert.match(sw, /const WAIT_FOR_MESSAGE = true;/);
  assert.match(mainCode, /autoApply: false/);
  assert.equal(
    /autoApply:\s*true/.test(mainCode),
    false,
    'main.js still auto-applies updates, which F18 rejects',
  );
  // The banner's Reload is what tells a waiting worker to take over.
  assert.match(mainCode, /requestUpdateReload\(\)/);
  // §4e busy-guard: never apply an update during a write or a modal.
  assert.match(mainCode, /canApplyUpdate/);
  assert.match(main, /mutationInFlight\(\)/);
  assert.match(main, /dialog\[open\]/);
});

test('Pattern F re-renders the current view on both resume surfaces', () => {
  // iOS resumes a suspended Home Screen snapshot without re-running startup
  // code, and a BFCache restore can fire `pageshow` with persisted=true and
  // NO visibilitychange. Missing either leaves match data minutes stale.
  assert.match(mainCode, /addEventListener\('visibilitychange'/);
  assert.match(mainCode, /document\.visibilityState === 'visible'\) reloadCurrentView\(\)/);
  assert.match(mainCode, /addEventListener\('pageshow'/);
  assert.match(mainCode, /event\.persisted/);
});

test('every ESM import in the app graph carries the version token', () => {
  // `?v=` on the ENTRY url does not propagate to nested imports, so the pin has
  // to be on every specifier; the /js/{path} route's content injection is what
  // makes a single CACHE_VERSION bump cover the whole graph.
  const files = walkJs();
  assert.ok(files.length >= 8, `expected the full module graph, found ${files.length} files`);
  for (const file of files) {
    const source = stripJsComments(read(file));
    for (const [, specifier] of source.matchAll(/\bfrom\s+'([^']+)'/g)) {
      assert.match(
        specifier,
        /\?v=__APP_VERSION__$/,
        `${file} imports ${specifier} without the version token`,
      );
    }
  }
});

test('no module registers a service worker outside update-manager', () => {
  // AGENTS.md #6. Registration is owned by the vendored update-manager; a
  // second registration — especially a version-pinned one — reinstalls the
  // worker on every load and can loop reloads.
  for (const file of walkJs()) {
    // The vendored pwa-infra modules legitimately know about registration;
    // update-manager is the one that OWNS it.
    if (file.startsWith('js/pwa/')) continue;
    assert.equal(
      stripJsComments(read(file)).includes('serviceWorker'),
      false,
      `${file} touches the service worker directly`,
    );
  }
});

test('every module under app/static/js resolves to a real file', () => {
  // `node --check` does not follow import specifiers, so a typo in a path is
  // invisible to `npm run check` and to a syntax check: it is a 404 at runtime
  // and, for a precached module, a hole in the offline shell.
  for (const file of walkJs()) {
    const source = stripJsComments(read(file));
    for (const [, specifier] of source.matchAll(/\bfrom\s+'([^']+)'/g)) {
      const target = specifier.split('?')[0];
      const resolved = target.startsWith('/js/')
        ? join(STATIC_DIR, 'js', target.slice('/js/'.length))
        : join(dirname(join(STATIC_DIR, file)), target);
      assert.ok(existsSync(resolved), `${file} imports ${specifier}, which does not exist`);
    }
  }
});

test('app/static/css adds only domain tokens, never a fork of the baseline', () => {
  const styles = read('css/styles.css');
  const tokens = read('css/tokens.css');
  // The screen-fixed baseline belongs to the vendored pwa.css. Re-declaring
  // it in the app layer is how a vendored fix silently stops applying — and it
  // is how the iOS left-gap trap comes back, because a `overflow-x` on <html>
  // makes iOS its own scroll container.
  for (const rule of [
    'touch-action',
    'font-size: 16px',
    'min-height: 44px',
    'min-width: 44px',
    '-webkit-overflow-scrolling',
    'env(safe-area-inset-top);',
  ]) {
    assert.equal(styles.includes(rule), false, `styles.css re-declares "${rule}"`);
  }
  assert.equal(/html\s*\{[\s\S]{0,200}?overflow/.test(styles), false, 'overflow on <html>');
  assert.match(styles, /#app-root\s*{[^}]*overflow-x: hidden/);
  // 2d: minmax(0, 1fr), never bare 1fr, and the base rule before the @media.
  // The first `@media` in the file is the override, so a comment mentioning
  // @media earlier in the file must not be what this compares against.
  const grid = styles.indexOf('.recipe-grid {');
  const media = styles.indexOf('@media (min-width');
  assert.ok(grid !== -1 && media > grid, 'the grid base rule must precede the @media overrides');
  assert.equal(/grid-template-columns:\s*repeat\(\d+, 1fr\)/.test(styles), false);
  assert.match(styles, /grid-template-columns: minmax\(0, 1fr\)/);
  assert.match(styles, /\.recipe-grid > \*\s*{\s*min-width: 0;/);
  // F11's 30 s stock TTL is a data fact, but the surface that must not lie
  // about stock is the chip row: 44px tall, horizontally scrollable, and the
  // only scroller in the app.
  assert.match(styles, /\.chip-row \{[^}]*padding-top: var\(--chip-hit-pad\)/);
  assert.match(tokens, /--tap-gap: 8px/);
  assert.match(tokens, /--chip-height: 32px/);
  for (const bucket of [
    'found',
    'manual',
    'assumed',
    'ignored',
    'missing',
  ]) {
    assert.match(tokens, new RegExp(`--chip-${bucket}-bg:`), `${bucket} has no light token`);
    assert.match(tokens, new RegExp(`--chip-${bucket}-fg:`), `${bucket} has no light token`);
  }
});

test('the shell declares the two hosts main.js writes into', () => {
  // A silent no-op in a view is the failure mode: main.js guards every lookup
  // with `if (!host) return`, so a renamed id is invisible until someone reads
  // the code. The ids are part of the shell contract, so they are asserted.
  assert.match(index, /id="app-root"/);
  assert.match(index, /id="update-banner"/);
  assert.match(index, /id="route-notice"/);
  assert.match(index, /id="version-badge"/);
  assert.match(mainCode, /getElementById\('route-notice'\)/);
});

test('main.js publishes the version global the update manager reads', () => {
  // update-manager reads window['__APP' + '_VERSION__'] so a naive
  // server-side token replacement cannot rewrite the property access.
  assert.match(main, /__APP' \+ '_VERSION__/);
  assert.match(main, /meta\[name="app-version"\]|querySelector\('meta\[name="app-version"\]'\)/);
});

test('main.js signals pwa:awake so the waking banner is dismissed', () => {
  assert.match(main, /dispatchEvent\(new Event\('pwa:awake'\)\)/);
});

test('sw.js keeps a CACHE_VERSION constant (Pattern A single source)', () => {
  assert.match(sw, /const CACHE_VERSION = 'v\d+\.\d+\.\d+';/);
  assert.match(sw, /pwa-infra service worker/);
});

test('sw.js SHELL_ASSETS only references files that exist', () => {
  const block = sw.match(/const SHELL_ASSETS = \[([\s\S]*?)\n\];/);
  assert.ok(block, 'SHELL_ASSETS not found in sw.js');
  // Comments are stripped first, and this is not cosmetic: the entry parser
  // below is a quoted-literal regex, so a single apostrophe inside a comment
  // ("the router's five routes") opens a phantom string that runs to the next
  // quote and is then reported as a missing file. A comment is not a precache
  // entry, and treating one as one makes this gate fail a correct commit.
  const entries = [...stripJsComments(block[1]).matchAll(/'([^']+)'/g)].map((match) => match[1]);
  assert.ok(entries.length > 0, 'SHELL_ASSETS is empty — the offline shell would be bare');
  for (const entry of entries) {
    assert.ok(entry.startsWith('/'), `SHELL_ASSETS entry is not a path: ${entry}`);
    const [pathname] = entry.split('?');
    if (pathname === '/') continue; // the HTML shell is served by a route
    const relative = pathname.replace(/^\//, '');
    assert.ok(
      existsSync(join(STATIC_DIR, relative)),
      `SHELL_ASSETS references a missing file: ${pathname}`,
    );
  }
});

test('sw.js never caches the API or health routes', () => {
  const block = sw.match(/const NETWORK_ONLY_PREFIXES = \[([\s\S]*?)\];/);
  assert.ok(block, 'NETWORK_ONLY_PREFIXES not found in sw.js');
  assert.match(block[1], /'\/api\/'/);
  assert.match(sw, /const NETWORK_ONLY_EXACT = \[[^\]]*'\/health'/);
});

/* The mirror of the gate in tests/js/logic/sort.test.mjs, and it lives here for
 * the same reason inverted: a one-level `tests/js/*.test.mjs` glob collects this
 * file and drops the whole logic/ directory, so only a gate up here can see that
 * narrowing. Neither gate can see the other's, which is why there are two. */
test(
  'npm test collects the nested test files as well as this one',
  { skip: isProbeRun() ? 'this is the probe run' : false },
  () => {
    const collected = collectedTestFiles();
    assert.deepEqual(collected, EXPECTED_TESTS, `collected: ${collected.join(', ')}`);
    assert.equal(collected.length, EXPECTED_TESTS.length);
  },
);

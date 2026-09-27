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
 */

import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const STATIC_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'app', 'static');
const read = (relative) => readFileSync(join(STATIC_DIR, relative), 'utf8');

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
  const entries = [...block[1].matchAll(/'([^']+)'/g)].map((match) => match[1]);
  assert.ok(entries.length > 0, 'SHELL_ASSETS is empty — the offline shell would be bare');
  for (const entry of entries) {
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

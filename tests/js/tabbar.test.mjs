/* The tab bar: the contract between `index.html`'s static markup and
 * `js/tabbar.js`, and the two pieces of behaviour the markup cannot carry.
 *
 * **WHY THE MARKUP IS STATIC AND STILL NEEDS A GATE.** The shell is precached,
 * so the bar is on screen at first paint offline — which a bar built by
 * JavaScript is not. The cost of static markup is that the two halves can drift:
 * a tab added to `TABS` and forgotten in `index.html` is a route the bar cannot
 * reach, and a `data-tab` left in the markup with no `TABS` entry is a control
 * whose current-route mark nothing ever sets. The first test below reads the
 * shell and compares it to the module, so neither can be changed alone.
 *
 * **THE THREE DISMISSALS ARE ASSERTED BECAUSE THE FOURTH IS ABSENT ON PURPOSE.**
 * `tabbar.js` closes 更多 on the toggle, on Escape and on any navigation. It does
 * NOT close on a tap on the page behind it, and that is a decision rather than an
 * oversight: the menu has no scrim, so there is no obvious owner for such a tap,
 * and closing on one would make a flick that registers as a tap dismiss the menu
 * while not closing would strand a user expecting iOS. Asserting the three that
 * exist keeps the gap from quietly becoming a bug.
 *
 * NO LIVE PATH, no server, no network: `index.html` is read as text and the DOM
 * is the repository's own `fake-dom.mjs`.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { register } from 'node:module';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { installFakeDom } from './fake-dom.mjs';

register('./app-version-hook.mjs', import.meta.url);

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const SHELL = readFileSync(join(REPO_ROOT, 'app', 'static', 'index.html'), 'utf8');
const STYLES = readFileSync(join(REPO_ROOT, 'app', 'static', 'css', 'styles.css'), 'utf8');
const TOKENS = readFileSync(join(REPO_ROOT, 'app', 'static', 'css', 'tokens.css'), 'utf8');

const { ALL_TAB_KEYS, MORE_KEY, TABS, MORE_LINKS, initTabbar, isMoreView, tabKeyForView } =
  await import('../../app/static/js/tabbar.js');

/* --- the shell <-> module contract ---------------------------------------- */

/** Every `data-tab` + href/label pair the SHELL actually carries. */
function shellTabs() {
  return [...SHELL.matchAll(/data-tab="([a-z]+)"[^>]*>(?:<span[^>]*>)?([^<]*)</g)].map((match) => ({
    key: match[1],
    label: match[2].trim(),
  }));
}

test('every tab in tabbar.js exists in the shell, with the same label', () => {
  const shell = new Map(shellTabs().map((entry) => [entry.key, entry.label]));
  for (const tab of TABS) {
    assert.ok(shell.has(tab.key), `index.html has no control for the ${tab.key} tab`);
    assert.equal(shell.get(tab.key), tab.label, `the ${tab.key} tab's label drifted`);
  }
});

test('every 更多 row in tabbar.js exists in the shell, with the same label', () => {
  const shell = new Map(shellTabs().map((entry) => [entry.key, entry.label]));
  for (const link of MORE_LINKS) {
    assert.ok(shell.has(link.key), `index.html has no 更多 row for ${link.pattern}`);
    assert.equal(shell.get(link.key), link.label);
  }
  // The menu button is a control too, and it is not a route.
  assert.ok(shell.has(MORE_KEY));
});

test('the shell carries no data-tab the module does not know about', () => {
  // The other direction: an orphan control in the markup is a button whose
  // current-route mark nothing will ever set, and nobody would notice.
  const known = new Set(ALL_TAB_KEYS);
  for (const entry of shellTabs()) {
    assert.ok(known.has(entry.key), `index.html carries an unknown data-tab: ${entry.key}`);
  }
});

test('the tab hrefs are the routes the router table actually has', async () => {
  const { ROUTES } = await import('../../app/static/js/router.js');
  const patterns = new Set(ROUTES.map((route) => route.pattern));
  for (const entry of [...TABS, ...MORE_LINKS]) {
    assert.ok(patterns.has(entry.pattern), `${entry.pattern} is not a route`);
  }
  // …and the two tabs are the two the hash lands on, in the bar's order. A tab
  // bar that pointed at a route nothing dispatches would be a dead control.
  assert.deepEqual(
    TABS.map((tab) => tab.pattern),
    ['#/', '#/pantry'],
  );
});

test('the tab bar is OUTSIDE #app-root, which the router clears on navigation', () => {
  const rootIndex = SHELL.indexOf('id="app-root"');
  const navIndex = SHELL.indexOf('id="tabbar"');
  assert.ok(rootIndex > -1 && navIndex > -1, 'the shell is missing one of the two');
  // A bar inside the content root is wiped by `render()`'s `host.textContent =
  // ''` on the first navigation, which is the bug this placement exists to
  // avoid — so this is asserted on the markup rather than left to review.
  assert.ok(navIndex > rootIndex, 'the tab bar must come after #app-root, not inside it');
  // The closing tag has to come after the opening one, or `navIndex` is inside a
  // subtree the router owns and the assertion above proves nothing.
  assert.ok(SHELL.indexOf('</main>') < navIndex, 'the tab bar is not after </main>');
});

test('the 更多 sheet keeps the vendored .form-sheet class the F18 busy-guard looks for', () => {
  // `main.js`'s `isModalOpen()` matches `.form-sheet:not([hidden])`, so an open
  // menu blocks a forced Service Worker reload. Renaming the class would opt the
  // menu out of that guard silently.
  assert.match(SHELL, /id="more-sheet"[^>]*class="form-sheet more-sheet"/);
  assert.match(SHELL, /id="more-sheet"[^>]*\bhidden\b/);
});

test('the tab bar clears the home indicator, and the content clears the bar', () => {
  // A fixed bar does not displace content, so this sum is what keeps the last
  // row of a list reachable. Asserted on the CSS because a bar that renders
  // correctly and covers the last row still fails the user.
  assert.match(TOKENS, /--tabbar-h: \d+px;/);
  assert.match(STYLES, /\.tabbar \{[^}]*position: fixed;/);
  assert.match(STYLES, /\.tabbar \{[^}]*padding: [^;]*env\(safe-area-inset-bottom/);
  assert.match(
    STYLES,
    /\.content \{[^}]*padding-bottom: calc\(env\(safe-area-inset-bottom, 0px\) \+ var\(--tabbar-h\)/,
  );
});

/* --- behaviour ------------------------------------------------------------ */

function mountShell() {
  // `installFakeDom` returns an environment whose `restore()` puts the globals
  // back — it is a method on the returned object, not the return value itself.
  const env = installFakeDom();
  const restore = () => env.restore();
  const doc = globalThis.document;
  const make = (tag, key, label, href) => {
    const node = doc.createElement(tag);
    node.setAttribute('class', 'tabbar__tab');
    node.setAttribute('data-tab', key);
    if (href) node.setAttribute('href', href);
    if (label) {
      const span = doc.createElement('span');
      span.textContent = label;
      node.appendChild(span);
    }
    doc.body.appendChild(node);
    return node;
  };
  make('a', 'home', '菜谱', '#/');
  make('a', 'pantry', '食材', '#/pantry');
  const more = make('button', MORE_KEY, '更多');
  more.setAttribute('aria-expanded', 'false');
  const sheet = doc.createElement('div');
  sheet.setAttribute('id', 'more-sheet');
  sheet.setAttribute('class', 'form-sheet more-sheet');
  sheet.setAttribute('hidden', '');
  doc.body.appendChild(sheet);
  for (const link of MORE_LINKS) make('a', link.key, link.label, link.pattern);
  return { restore, bar: initTabbar(), doc, more, sheet };
}

test('markActive marks the tab the route belongs to, and only that one', () => {
  const { restore, bar, doc, more } = mountShell();
  try {
    bar.markActive('pantry');
    assert.equal(doc.querySelector('[data-tab="pantry"]').getAttribute('aria-current'), 'page');
    assert.equal(doc.querySelector('[data-tab="home"]').getAttribute('aria-current'), null);
    assert.equal(more.getAttribute('aria-current'), null);
    bar.markActive('home');
    assert.equal(doc.querySelector('[data-tab="home"]').getAttribute('aria-current'), 'page');
    assert.equal(doc.querySelector('[data-tab="pantry"]').getAttribute('aria-current'), null);
  } finally {
    bar.destroy();
    restore();
  }
});

test('a 更多 destination marks 更多, not 菜谱', () => {
  const { restore, bar, doc, more } = mountShell();
  try {
    // A user who tapped 更多 → 溯源 is on 溯源. A bar showing neither tab nor 更多
    // as current is the one place this design could have lied.
    for (const view of ['shortlists', 'provenance', 'settings']) {
      bar.markActive(view);
      assert.ok(isMoreView(view));
      assert.equal(more.getAttribute('aria-current'), 'page', `${view} did not mark 更多`);
      assert.equal(doc.querySelector('[data-tab="home"]').getAttribute('aria-current'), null);
    }
  } finally {
    bar.destroy();
    restore();
  }
});

test('a view no tab owns marks NOTHING', () => {
  const { restore, bar, doc } = mountShell();
  try {
    // The recipe detail is a destination reached from a list, not a section, so
    // `tabKeyForView` returns null rather than falling back to 菜谱 — which would
    // tell the user they are on the list when they are reading a recipe.
    assert.equal(tabKeyForView('recipe'), null);
    bar.markActive('recipe');
    assert.equal(doc.querySelector('[data-tab="home"]').getAttribute('aria-current'), null);
    assert.equal(doc.querySelector('[data-tab="pantry"]').getAttribute('aria-current'), null);
  } finally {
    bar.destroy();
    restore();
  }
});

test('更多 toggles, and reflects its state in the DOM rather than a variable', () => {
  const { restore, bar, more, sheet } = mountShell();
  try {
    assert.equal(bar.isMenuOpen(), false);
    more.dispatchEvent({ type: 'click' });
    assert.equal(bar.isMenuOpen(), true);
    assert.equal(sheet.getAttribute('hidden'), null, 'an open menu must not be [hidden]');
    assert.equal(more.getAttribute('aria-expanded'), 'true');
    more.dispatchEvent({ type: 'click' });
    assert.equal(bar.isMenuOpen(), false);
    assert.notEqual(sheet.getAttribute('hidden'), null);
    assert.equal(more.getAttribute('aria-expanded'), 'false');
  } finally {
    bar.destroy();
    restore();
  }
});

test('navigating closes 更多, and so does Escape', () => {
  const { restore, bar, doc, more, sheet } = mountShell();
  try {
    more.dispatchEvent({ type: 'click' });
    assert.equal(bar.isMenuOpen(), true);
    // A menu row is a real hash link, so the ROUTER navigates and hashchange is
    // what reports it. Closing on the event rather than the click is also what
    // covers Back: a traversal re-enters #/provenance with no click at all.
    globalThis.dispatchEvent({ type: 'hashchange' });
    assert.equal(bar.isMenuOpen(), false);

    more.dispatchEvent({ type: 'click' });
    doc.dispatchEvent({ type: 'keydown', key: 'Escape' });
    assert.equal(bar.isMenuOpen(), false);
    assert.equal(sheet.getAttribute('hidden'), '');

    // Escape with the menu shut is a no-op, not a throw.
    doc.dispatchEvent({ type: 'keydown', key: 'Escape' });
    assert.equal(bar.isMenuOpen(), false);
  } finally {
    bar.destroy();
    restore();
  }
});

test('leaving a 更多 route closes the menu even if no hashchange was seen', () => {
  const { restore, bar, more } = mountShell();
  try {
    more.dispatchEvent({ type: 'click' });
    bar.markActive('provenance');
    assert.equal(bar.isMenuOpen(), true, 'a menu open on its own destination stays open');
    bar.markActive('home');
    assert.equal(bar.isMenuOpen(), false);
  } finally {
    bar.destroy();
    restore();
  }
});

test('destroy() detaches every listener it added', () => {
  const { restore, bar, more } = mountShell();
  more.dispatchEvent({ type: 'click' });
  assert.equal(bar.isMenuOpen(), true);
  bar.destroy();
  // Nothing left to react, so the DOM must not move. A leaked listener on a bar
  // the router never tears down is a control that keeps answering after the view
  // is gone — the exact class of leak `unmount()` ordering exists to prevent.
  more.dispatchEvent({ type: 'click' });
  assert.equal(bar.isMenuOpen(), true, 'the menu should be frozen after destroy()');
  restore();
});

test('main.js creates the bar BEFORE the router, and feeds it onNavigate', () => {
  const main = readFileSync(join(REPO_ROOT, 'app', 'static', 'js', 'main.js'), 'utf8');
  // A bar created after `start()` would leave the very first screen with no
  // current tab marked — a bar that cannot say where you are on the screen you
  // arrive at.
  assert.ok(
    main.indexOf('const tabbar = initTabbar()') < main.indexOf('initRouter({'),
    'initTabbar() must run before initRouter()',
  );
  assert.match(main, /onNavigate: \(route\) => tabbar\.markActive\(route && route\.view\)/);
  // …and the six views, with the new one registered.
  assert.match(main, /pantry: \{ mount: mountPantry \}/);
});

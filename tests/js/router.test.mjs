/* Router, view-convention, and back-button gates (node --test, no deps).
 *
 * What only this file can see:
 *   - hash parsing and the `notFound` path, including that an unknown hash
 *     renders home AND raises a notice without pushing a history entry;
 *   - percent-decoding of `#/recipe/<basename>`, and that a decoded `/` inside
 *     a basename cannot invent a path segment (the regex matches the ENCODED
 *     form, decoding happens per parameter);
 *   - the mount/unmount ORDER, which the browser makes unobservable: by the
 *     time a user sees the wrong view, both have run;
 *   - the §2i back-button rule, including its `history.length <= 1` branch,
 *     which a desktop test never reaches because the length is always > 1.
 *
 * The scroll RESTORE of a Back traversal is Playwright's P14a job — a real
 * browser's scroll position and BFCache cannot be faked here — so what is
 * asserted below is the mechanism the restore depends on: the state is written
 * with replaceState (never pushState), it lives on the entry the user is
 * leaving, and a traversal re-dispatches the origin's hash.
 *
 * `router.js` imports nothing and touches no global at module scope precisely
 * so this file can import it with no DOM and no `?v=` resolution.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  DEFAULT_HASH,
  ROUTES,
  createRouter,
  goBack,
  matchRoute,
  mergeViewState,
  normalizeHash,
  parseHash,
} from '../../app/static/js/router.js';

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');

/* A window stand-in: only the four surfaces the router touches. Assigning
 * `location.hash` fires hashchange, and `history.back()` walks the stack and
 * fires popstate then hashchange, which is exactly the ordering the restore
 * depends on. */
function fakeWindow({ hash = '', historyLength = 3, scrollY = 0 } = {}) {
  const listeners = new Map();
  const stack = [{ hash, state: null }];
  let index = 0;

  const emit = (type) => {
    for (const handler of listeners.get(type) || []) handler({ type });
  };

  const location = {
    get hash() {
      return stack[index].hash;
    },
    set hash(next) {
      if (next === stack[index].hash) return;
      stack.splice(index + 1);
      // A new entry's state is null, exactly as in a browser. Leaving the
      // previous entry's state in place would hide the whole class of bug
      // this fake exists to catch: reading an offset after the destination has
      // been pushed, and finding the ORIGIN's.
      stack.push({ hash: next, state: null });
      index = stack.length - 1;
      history.state = null;
      emit('hashchange');
    },
  };

  const history = {
    state: null,
    replaceState(state) {
      stack[index].state = state;
      history.state = state;
    },
    pushState(state, _title, url) {
      stack.splice(index + 1);
      stack.push({ hash: url || stack[index].hash, state: state ?? null });
      index = stack.length - 1;
      history.state = stack[index].state;
    },
    back() {
      if (index === 0) return;
      index -= 1;
      history.state = stack[index].state;
      emit('popstate');
      emit('hashchange');
    },
  };

  const win = {
    location,
    history: new Proxy(history, {
      get(target, prop) {
        if (prop === 'length') return historyLength;
        return target[prop];
      },
    }),
    scrollY,
    scrollTo(x, y) {
      win.scrollY = y;
      win.scrollCalls.push([x, y]);
    },
    scrollCalls: [],
    addEventListener(type, handler) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(handler);
    },
    removeEventListener(type, handler) {
      const list = listeners.get(type) || [];
      const at = list.indexOf(handler);
      if (at !== -1) list.splice(at, 1);
    },
    console: { error() {} },
  };
  win.listenerCount = (type) => (listeners.get(type) || []).length;
  /* The two events a traversal delivers, in the order Chromium delivers them
   * (measured: `popstate` -> the engine's reset scroll -> `hashchange`). Exposed
   * so a test can open the window BETWEEN them, which `history.back()` cannot:
   * it fires both synchronously, and the defect lives in that gap. */
  win.dispatchEventForTest = (type) => emit(type);
  return win;
}

function fakeRoot() {
  return { textContent: 'stale', children: 0 };
}

function spyView(log, name) {
  return {
    mount(root, params) {
      log.push(`mount:${name}`);
      root.children += 1;
      const onClick = () => log.push(`click:${name}`);
      root.addEventListener = root.addEventListener || (() => {});
      root.onClick = onClick;
      return () => {
        log.push(`unmount:${name}`);
        root.children -= 1;
        root.onClick = null;
      };
    },
  };
}

test('the route table is exactly the six routes in the spec', () => {
  assert.deepEqual(
    ROUTES.map((route) => [route.pattern, route.view]),
    [
      ['#/', 'home'],
      ['#/pantry', 'pantry'],
      ['#/recipe/:basename', 'recipe'],
      ['#/shortlists', 'shortlists'],
      ['#/settings', 'settings'],
      ['#/provenance', 'provenance'],
    ],
  );
  assert.equal(DEFAULT_HASH, '#/');
});

/* `#/pantry` is the second tab, and the only route that had to be inserted
 * rather than appended — it sits NEXT TO `#/` in the table because the two are
 * the two tabs, and a reader comparing this table with the tab bar in
 * index.html should find them in the same order. The two are kept honest from
 * both sides: `tabbar.test.mjs` reads the shell markup and compares it to
 * `tabbar.js`'s TABS, and this test fixes the route table. */
test('#/pantry is the second route, adjacent to #/', () => {
  assert.deepEqual(
    ROUTES.slice(0, 2).map((route) => route.view),
    ['home', 'pantry'],
  );
  const matched = matchRoute('#/pantry');
  assert.equal(matched.view, 'pantry');
  assert.deepEqual(matched.params, {});
  // A single segment, so a recipe basename can never be read as this route and
  // this route can never swallow a detail URL.
  assert.equal(matchRoute('#/pantry/anything'), null);
  assert.equal(matchRoute('#/pantryextra'), null);
});

test('normalizeHash defaults an empty or bare hash to the home route', () => {
  assert.equal(normalizeHash(''), '#/');
  assert.equal(normalizeHash('#'), '#/');
  assert.equal(normalizeHash(undefined), '#/');
  assert.equal(normalizeHash('  '), '#/');
  assert.equal(normalizeHash('/settings'), '#/settings');
  assert.equal(normalizeHash('#/settings'), '#/settings');
});

test('parseHash splits path and query without decoding', () => {
  assert.deepEqual(parseHash('#/recipe/%E6%8B%8C%E7%A9%BA%E5%BF%83%E8%8F%9C'), {
    hash: '#/recipe/%E6%8B%8C%E7%A9%BA%E5%BF%83%E8%8F%9C',
    path: '/recipe/%E6%8B%8C%E7%A9%BA%E5%BF%83%E8%8F%9C',
    query: '',
    segments: ['recipe', '%E6%8B%8C%E7%A9%BA%E5%BF%83%E8%8F%9C'],
  });
  // The strict flag is a query parameter (F8), so a hash may carry one.
  assert.equal(parseHash('#/?strict=1').query, 'strict=1');
  assert.equal(parseHash('#/?strict=1').path, '/');
});

test('matchRoute decodes the recipe basename parameter', () => {
  const found = matchRoute('#/recipe/%E6%8B%8C%E7%A9%BA%E5%BF%83%E8%8F%9C');
  assert.equal(found.view, 'recipe');
  assert.equal(found.params.basename, '拌空心菜');
});

test('an encoded slash inside a basename does not become a path segment', () => {
  // %2F stays inside the single `([^/]+)` capture: the regex matches the
  // ENCODED hash, so a decoded '/' can never invent a segment.
  const found = matchRoute('#/recipe/a%2Fb');
  assert.equal(found.view, 'recipe');
  assert.equal(found.params.basename, 'a/b');
  assert.equal(matchRoute('#/recipe/a/b'), null);
});

test('a malformed percent escape degrades instead of throwing', () => {
  const found = matchRoute('#/recipe/100%');
  assert.equal(found.view, 'recipe');
  assert.equal(found.params.basename, '100%');
});

test('the four remaining routes match their own hash and nothing else', () => {
  for (const [hash, view] of [
    ['#/', 'home'],
    ['#/shortlists', 'shortlists'],
    ['#/settings', 'settings'],
    ['#/provenance', 'provenance'],
  ]) {
    assert.equal(matchRoute(hash).view, view, hash);
  }
  assert.equal(matchRoute('#/nope'), null);
  assert.equal(matchRoute('#/recipes'), null);
  assert.equal(matchRoute('#/settings/extra'), null);
});

test('mergeViewState merges filters one level deep and replaces the rest', () => {
  assert.deepEqual(
    mergeViewState({ scrollTop: 120, filters: { strict: false, search: '豆腐' } }, { filters: { strict: true } }),
    { scrollTop: 120, filters: { strict: true, search: '豆腐' } },
  );
  assert.deepEqual(mergeViewState(null, { scrollTop: 0 }), { scrollTop: 0 });
});

test('the router unmounts the outgoing view BEFORE mounting the incoming one', () => {
  const log = [];
  const win = fakeWindow({ hash: '#/' });
  const root = fakeRoot();
  const router = createRouter({
    window: win,
    root,
    views: { home: spyView(log, 'home'), settings: spyView(log, 'settings') },
  }).start();

  assert.deepEqual(log, ['mount:home']);
  win.location.hash = '#/settings';
  assert.deepEqual(log, ['mount:home', 'unmount:home', 'mount:settings']);
  router.stop();
  assert.deepEqual(log, ['mount:home', 'unmount:home', 'mount:settings', 'unmount:settings']);
});

test("an outgoing view's listener is gone once the next view is mounted", () => {
  // The leak the ordering exists to prevent: a view still holding a document
  // listener keeps writing into a root the next view already owns. The
  // browser makes this unobservable by eye, so it is counted.
  const win = fakeWindow({ hash: '#/' });
  const root = fakeRoot();
  // Each view keeps the handler it bound, because removeEventListener only
  // matches the same function reference.
  const views = {
    home: viewWithListener(win),
    settings: viewWithListener(win),
  };
  const router = createRouter({ window: win, root, views }).start();
  assert.equal(win.listenerCount('app:ping'), 1);
  win.location.hash = '#/settings';
  // Two mounted, one torn down: no accumulation across a view switch.
  assert.equal(win.listenerCount('app:ping'), 1);
  router.stop();
  assert.equal(win.listenerCount('app:ping'), 0);
});

function viewWithListener(win) {
  return {
    mount() {
      const handler = () => {};
      win.addEventListener('app:ping', handler);
      return () => win.removeEventListener('app:ping', handler);
    },
  };
}

test('a missing view module is reported, not thrown', () => {
  const win = fakeWindow({ hash: '#/provenance' });
  const root = fakeRoot();
  const router = createRouter({ window: win, root, views: { home: spyView([], 'home') } }).start();
  assert.equal(router.current().missing, true);
  assert.equal(router.current().view, 'provenance');
  router.stop();
});

test('an unknown hash renders home AND raises a notice', () => {
  const log = [];
  const notices = [];
  const win = fakeWindow({ hash: '#/not-a-route' });
  const root = fakeRoot();
  const router = createRouter({
    window: win,
    root,
    views: { home: spyView(log, 'home') },
    onUnknownRoute: (hash) => notices.push(hash),
  }).start();

  assert.deepEqual(notices, ['#/not-a-route']);
  assert.deepEqual(log, ['mount:home']);
  assert.equal(router.current().unknown, '#/not-a-route');
  // The hash is left alone: rewriting it would push a history entry for a typo
  // and make Back re-enter the broken route.
  assert.equal(win.location.hash, '#/not-a-route');
  router.stop();
});

test('a forward navigation starts at the top; a Back traversal restores', () => {
  const log = [];
  const win = fakeWindow({ hash: '#/shortlists' });
  const root = fakeRoot();
  const router = createRouter({
    window: win,
    root,
    views: { home: spyView(log, 'home'), shortlists: spyView(log, 'shortlists') },
  }).start();

  // The origin records where the user is, with replaceState.
  router.saveViewState({ scrollTop: 900, filters: { strict: false, search: null } });
  win.location.hash = '#/recipe/x';
  assert.deepEqual(win.scrollCalls.at(-1), [0, 0], 'a forward navigation starts at the top');

  win.history.back();
  assert.deepEqual(win.scrollCalls.at(-1), [0, 900], 'Back restores the origin offset');
  assert.equal(win.location.hash, '#/shortlists');
  router.stop();
});

test('a teardown that records state cannot overwrite the entry being entered', () => {
  // The real trap, and it is invisible until a browser runs it: by the time a
  // hashchange handler runs, the browser has already pushed the DESTINATION
  // entry, so a `replaceState` from the outgoing view's teardown lands in the
  // entry being navigated TO. The origin's own offset is then replaced by the
  // outgoing view's, and Back restores the wrong number. The router reads the
  // offset before it unmounts anything for exactly this reason.
  const log = [];
  const win = fakeWindow({ hash: '#/shortlists' });
  const root = fakeRoot();
  let routerRef = null;
  const views = {
    shortlists: {
      mount() {
        return () => {
          // A careless view would save here; do it anyway and prove it is safe.
          log.push('unmount:shortlists');
          routerRef.saveViewState({ scrollTop: 999 });
        };
      },
    },
    recipe: {
      mount() {
        log.push('mount:recipe');
        return () => log.push('unmount:recipe');
      },
    },
  };
  routerRef = createRouter({ window: win, root, views }).start();
  // The user scrolls the origin; its own listener records the offset.
  routerRef.saveViewState({ scrollTop: 640 });

  win.location.hash = '#/recipe/x';
  assert.deepEqual(log, ['unmount:shortlists', 'mount:recipe']);
  // A forward navigation starts at the top, not at the outgoing view's offset.
  assert.deepEqual(win.scrollCalls.at(-1), [0, 0]);

  win.history.back();
  assert.deepEqual(win.scrollCalls.at(-1), [0, 640], 'Back restores the ORIGIN offset');
  routerRef.stop();
});

test('goBack calls history.back() when there is history to go back to', () => {
  const log = [];
  const win = fakeWindow({ hash: '#/recipe/x', historyLength: 4 });
  const root = fakeRoot();
  const router = createRouter({
    window: win,
    root,
    views: { home: spyView(log, 'home'), recipe: spyView(log, 'recipe') },
  }).start();

  win.history.pushState(null, '', '#/recipe/y');
  router.goBack();
  assert.equal(win.location.hash, '#/recipe/x');
  router.stop();
});

test('a tracked-scroll save between popstate and the dispatch is dropped, not written', () => {
  /* The scroll-restore race, at the one seam that can see the WINDOW even though
   * it cannot see the scroll event. Measured in Chromium, the order during a Back
   * traversal is
   *
   *     popstate -> scroll (the engine resetting) -> hashchange
   *
   * and the rAF-throttled save that scroll event triggers is what corrupts the
   * entry being arrived at: `popstate y=0 hist=640` / `scroll y=244 hist=640` /
   * `hashchange y=244 hist=244`. The event order is Chromium's and Playwright's
   * to measure; what the fake window can prove is the RULE the fix encodes, and
   * that the rule releases the gate again — otherwise every later write in the
   * session would be silently discarded and the view would stop recording where
   * the user is at all. */
  const log = [];
  const win = fakeWindow({ hash: '#/shortlists' });
  const root = fakeRoot();
  const router = createRouter({
    window: win,
    root,
    views: { home: spyView(log, 'home'), shortlists: spyView(log, 'shortlists') },
  }).start();

  router.saveViewState({ scrollTop: 640 });
  assert.equal(win.history.state.scrollTop, 640);

  // The traversal, dispatched the way the browser dispatches it. `popstate`
  // raises the latch; nothing between it and the `hashchange` may write.
  const emit = (type) => win.dispatchEventForTest(type);
  emit('popstate');
  const refused = router.saveViewState({ scrollTop: 244 });
  assert.equal(win.history.state.scrollTop, 640, 'a traversal-window save reached the entry');
  // The refusal returns the state as it IS, never a state that was not written.
  assert.deepEqual(refused, { scrollTop: 640 });

  // And the dispatch that follows releases it, so the incoming view can record.
  // The dispatch itself restores the offset it read while the gate was up —
  // 640, the correct number, which is the whole claim: the 244 never landed, so
  // nothing was restored from it.
  emit('hashchange');
  assert.deepEqual(win.scrollCalls.at(-1), [0, 640], 'the restore used the corrupted offset');
  router.saveViewState({ scrollTop: 57 });
  assert.equal(win.history.state.scrollTop, 57, 'the gate never came down');
  router.stop();
});

test('the entry epoch moves on a traversal and on a push, and never on a read', () => {
  /* The view-side half of the same contract, and the reason the fix is an epoch
   * rather than a `popstate` detach: `trackScroll` defers its save to the next
   * animation frame, which is after every task the traversal queues, so a view
   * that COMPARES the epoch performs its decision after `popstate` under either
   * delivery order — while a view that detaches on `popstate` is defeated by a
   * reset scroll that arrives first, and a traversal that never changes the hash
   * would leave a detached view with no tracking for the rest of its life. */
  const log = [];
  const win = fakeWindow({ hash: '#/' });
  const root = fakeRoot();
  const router = createRouter({
    window: win,
    root,
    views: { home: spyView(log, 'home'), recipe: spyView(log, 'recipe') },
  }).start();

  const atBoot = router.currentEntryEpoch();
  assert.equal(typeof atBoot, 'number');
  // A read is not a move: the offset the view re-applies must not invalidate
  // the view that captured the epoch.
  router.readViewState();
  assert.equal(router.currentEntryEpoch(), atBoot);

  router.navigate('#/recipe/x');
  assert.equal(router.currentEntryEpoch(), atBoot + 1, 'a push did not move the epoch');

  const afterPush = router.currentEntryEpoch();
  win.dispatchEventForTest('popstate');
  assert.equal(router.currentEntryEpoch(), afterPush + 1, 'a traversal did not move the epoch');
  router.stop();
});

test('goBack falls back to the canonical route at history.length <= 1', () => {
  // The fresh-load / direct-link branch: `history.length` is 1, so there is
  // nothing to go back to and the user must land somewhere real.
  const log = [];
  const win = fakeWindow({ hash: '#/recipe/x', historyLength: 1 });
  const root = fakeRoot();
  const router = createRouter({
    window: win,
    root,
    views: { home: spyView(log, 'home'), recipe: spyView(log, 'recipe') },
  }).start();
  assert.deepEqual(log, ['mount:recipe']);

  router.goBack();
  assert.equal(win.location.hash, '#/');
  assert.deepEqual(log, ['mount:recipe', 'unmount:recipe', 'mount:home']);
  router.stop();
});

test('the module-level helpers refuse to guess before initRouter runs', () => {
  // A view importing `goBack` must not silently navigate against a router that
  // was never built against a window.
  assert.throws(() => goBack(), /initRouter/);
});

test('every view module exports mount(root) and returns an unmount', () => {
  const files = ['home', 'recipe', 'shortlists', 'settings', 'provenance'];
  for (const name of files) {
    const source = readFileSync(
      join(REPO_ROOT, 'app', 'static', 'js', 'views', `${name}.js`),
      'utf8',
    );
    assert.match(source, /export function mount\(/, `${name}.js has no exported mount`);
    // The convention is `return function unmount()`, not an arrow assigned
    // elsewhere and not a bare `return;`.
    assert.match(source, /return function unmount\(\)/, `${name}.js returns no unmount`);
  }
});

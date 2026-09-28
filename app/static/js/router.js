/* The hash router and the view convention. docs/pwa-template.md 2i / spec 9.3.
 *
 * Five routes, no more (spec 9.3 table):
 *
 *   #/                    -> home          the recipe list
 *   #/recipe/<basename>   -> recipe        the detail, basename URL-encoded
 *   #/shortlists          -> shortlists    the three meal shortlists (D3)
 *   #/settings            -> settings      调试 + 严格模式 (F7, F8)
 *   #/provenance          -> provenance    the full per-Ingredient table
 *   anything else         -> home, plus a non-blocking "unknown route" notice
 *
 * WHY A HASH ROUTER: the SW precaches `/` and every module is served from
 * `/js/...`; a path router would ask the network for `/recipe/拌空心菜` on a
 * cold offline start, where no route but `/` exists in the precache. The hash
 * is not sent to the server, so the precached shell always satisfies it.
 *
 * THE VIEW CONVENTION (spec 9.3): every view module exports
 *
 *   export function mount(root) { ... return function unmount() { ... } }
 *
 * and the router calls the outgoing view's `unmount()` BEFORE the incoming
 * view's `mount()`. That ordering is load-bearing: a view that leaves a
 * listener or an in-flight AbortController behind keeps writing into a `root`
 * the next view already owns. It is the only cross-view contract — there is no
 * global mutable view state, which is also what keeps the render-cache-key
 * rule satisfiable (a cached render is legal only when its key includes every
 * input the render consumes).
 *
 * PER-VIEW RESTORE STATE (spec 9.3, template 2i "hash-router gotcha"): the
 * scroll position and filters of the view being LEFT are written into that
 * view's own `history` entry with `replaceState`. `history.back()` therefore
 * restores the origin's `history.state` together with the origin's hash, the
 * `hashchange` listener re-dispatches it, and the re-render reads its scroll
 * offset back with zero extra plumbing. Playwright P14a asserts exactly this
 * (enter #/recipe/… from #/shortlists, Back, assert the offset) — it is the one
 * behaviour unit and API tests structurally cannot see.
 *
 * The consequence every view has to respect: a view records its state from its
 * SCROLL LISTENER, never from `mount()` or `unmount()`. By the time a
 * `hashchange` handler runs, the browser has already pushed the destination
 * entry, so a `replaceState` from the outgoing view's teardown lands in the
 * entry being navigated to — silently overwriting the origin's own offset with
 * the outgoing view's. The router reads the offset before it touches anything
 * for the same reason.
 *
 * ## THE SCROLL-RESTORE RACE, AND WHY THE SCROLL LISTENER IS THE LIABLE PART
 *
 * Reading the offset first is NOT sufficient, and the reason is a measurement
 * rather than an argument. In Chromium, a same-document Back traversal dispatches
 *
 *     popstate  ->  scroll (the engine resetting/restoring)  ->  hashchange
 *
 * — measured, not assumed, and the middle event is the whole bug. The outgoing
 * detail view's `trackScroll` handler is still attached when that reset fires,
 * so its rAF-throttled save writes the RESET value into the entry being arrived
 * at, and `render()` then reads the origin's own offset back already corrupted.
 * Observed end to end: `popstate y=0 hist=640` / `scroll y=244 hist=640` /
 * `hashchange y=244 hist=244`, settling at 244 instead of 640 — and because
 * `history.state` holds the wrong number, a second Back stays wrong.
 *
 * So the router owns the other half of the same contract, in two pieces:
 *
 * * **A traversal latch.** `popstate` raises it, the `render()` that follows
 *   lowers it, and `saveViewState` is a no-op while it is up. Between those two
 *   moments *no view owns the current history entry* — the browser has already
 *   committed the arrival — so no view may write it. This is deliberately at the
 *   router rather than in each view: `home.js`, `shortlists.js`, `provenance.js`
 *   and `settings.js` all keep the same tracked-scroll listener, and a rule that
 *   only one view honours is a rule the fourth view silently breaks.
 * * **An entry epoch**, `currentEntryEpoch()`, for a view that wants the
 *   decision at its own end. The latch alone protects the pre-`render()` window;
 *   the epoch additionally tells a view "the entry you were mounted for is no
 *   longer the current one", which is the property a view can check without
 *   knowing anything about `popstate`. `views/recipe.js` consults it, and it is
 *   exposed as a plain integer so the decision is testable with the fake window
 *   above, which never moves `scrollY` and therefore never fires a scroll.
 *
 * WHY AN EPOCH GUARD RATHER THAN #24's "detach the listener on `popstate`".
 * The detach is right about the ordering — the reset really does come after
 * `popstate` — but it is *strictly weaker*. `trackScroll` is rAF-throttled, so
 * the save it performs is always deferred to the next animation frame, which is
 * after every task the traversal queues, `popstate` among them. A view that
 * checks the epoch performs its comparison at that deferred moment and so is
 * correct under BOTH orderings; a view that detaches on `popstate` is defeated
 * outright by any reset scroll dispatched *before* `popstate`, which is a
 * rendering detail this app does not control. The epoch is also self-healing: a
 * traversal that never changes the hash leaves a detached view with no scroll
 * tracking for the rest of its life, and leaves the epoch guard working.
 *
 * THE BACK-BUTTON RULE (template 2i), verbatim and non-negotiable:
 *
 *   function goBack() {
 *     if (window.history.length <= 1) router.navigate('#/');   // fresh load
 *     else window.history.back();
 *   }
 *
 * A detail view is reachable from the list, from a shortlist, and from search,
 * so a hardcoded `navigate('#/')` on Back dumps the user into the canonical
 * list and loses the origin. `goBack()` is applied to the error and not-found
 * states too — a failed load still returns to where the user came from.
 * Hardcoded navigation is reserved for post-action moves (shortlist add ->
 * #/shortlists) and for a view whose only entry point IS the canonical route.
 *
 * NO DOM AT MODULE SCOPE. The whole module is importable by `node --test`
 * with no `window`/`document` stub, which is why the window is a constructor
 * argument rather than the global: tests drive `createRouter({ window: fake })`.
 * For the same reason this module imports nothing — an import specifier
 * carrying `?v=__APP_VERSION__` is correct in the browser and unresolvable in
 * Node, so a module under test stays import-free.
 */

export const DEFAULT_HASH = '#/';

/** The five routes, in match order. Frozen: the table is the contract. */
export const ROUTES = Object.freeze([
  Object.freeze({ pattern: '#/', view: 'home' }),
  Object.freeze({ pattern: '#/recipe/:basename', view: 'recipe' }),
  Object.freeze({ pattern: '#/shortlists', view: 'shortlists' }),
  Object.freeze({ pattern: '#/settings', view: 'settings' }),
  Object.freeze({ pattern: '#/provenance', view: 'provenance' }),
]);

/** The route an unknown hash falls back to — home, plus a notice. */
export const FALLBACK_PATTERN = ROUTES[0].pattern;

export function normalizeHash(hash) {
  if (typeof hash !== 'string') return DEFAULT_HASH;
  const trimmed = hash.trim();
  if (trimmed === '' || trimmed === '#') return DEFAULT_HASH;
  return trimmed.startsWith('#') ? trimmed : `#${trimmed}`;
}

/**
 * Split a hash into its parts without resolving it. `segments` are still
 * percent-encoded — decoding happens per-parameter in `matchRoute`, so a
 * decoded `/` inside a recipe basename cannot invent an extra path segment.
 */
export function parseHash(hash) {
  const normalized = normalizeHash(hash);
  const raw = normalized.slice(1); // '/recipe/%E6%8B%8C…'
  const questionMark = raw.indexOf('?');
  const path = questionMark === -1 ? raw : raw.slice(0, questionMark);
  const query = questionMark === -1 ? '' : raw.slice(questionMark + 1);
  return {
    hash: normalized,
    path,
    query,
    segments: path.split('/').filter((segment) => segment !== ''),
  };
}

function escapeForRegExp(literal) {
  return literal.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function compileRoute(pattern) {
  const normalized = normalizeHash(pattern);
  const names = [];
  const source = escapeForRegExp(normalized).replace(/:([A-Za-z0-9_]+)/g, (_, name) => {
    names.push(name);
    return '([^/]+)';
  });
  return { pattern: normalized, names, regex: new RegExp(`^${source}$`) };
}

const COMPILED = new Map();

function compiledRoutes(routes) {
  return routes.map((route) => {
    const cached = COMPILED.get(route.pattern);
    if (cached) return { ...route, ...cached };
    const compiled = compileRoute(route.pattern);
    COMPILED.set(route.pattern, compiled);
    return { ...route, ...compiled };
  });
}

/** A malformed percent escape must not take the router down. */
function decodeParam(value) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

export function matchRoute(hash, routes = ROUTES) {
  const normalized = normalizeHash(hash);
  for (const route of compiledRoutes(routes)) {
    const found = normalized.match(route.regex);
    if (!found) continue;
    const params = {};
    route.names.forEach((name, index) => {
      params[name] = decodeParam(found[index + 1]);
    });
    return { pattern: route.pattern, view: route.view, params, hash: normalized };
  }
  return null;
}

/** Merge into the current history entry, one level deep for `filters`. */
export function mergeViewState(previous, patch) {
  const base = previous && typeof previous === 'object' ? previous : {};
  const next = { ...base };
  for (const [key, value] of Object.entries(patch || {})) {
    if (key === 'filters') {
      next.filters = { ...(base.filters || {}), ...(value || {}) };
    } else {
      next[key] = value;
    }
  }
  return next;
}

export function createRouter({
  window: win = globalThis,
  views = {},
  root = null,
  routes = ROUTES,
  fallbackPattern = FALLBACK_PATTERN,
  onUnknownRoute = null,
  onNavigate = null,
} = {}) {
  if (!win) throw new TypeError('createRouter requires a window');
  // Resolved through matchRoute, not compileRoute: the fallback needs the
  // ROUTE (view + params), and compileRoute only returns the pattern's regex.
  const fallback = matchRoute(fallbackPattern, routes) || { view: null, params: {} };
  let unmountActive = null;
  let active = null;
  let started = false;
  let onHashChange = null;
  let onPopState = null;
  let stopping = false;
  /* A history traversal (Back/Forward) fires popstate BEFORE hashchange, so
   * the flag is what tells the next dispatch "restore this entry's scroll"
   * rather than "start the incoming view at the top". Without it, Back
   * re-dispatches the origin's hash (template 2i) and the origin re-renders
   * at the top — the exact bug P14a asserts against. */
  let traversed = false;
  /* The scroll-restore race's other half. Raised by `popstate` — the instant
   * the browser commits the arrival and starts resetting the scroll offset —
   * and lowered by the `render()` that follows it. While it is up the current
   * history entry belongs to nobody: the outgoing view's own tracked-scroll
   * save would land in the entry being arrived at. */
  let traversalPending = false;
  /* Bumped whenever `history`'s current entry stops being the one the active
   * view was mounted for: a traversal (popstate) or a push (`navigate`). A view
   * captures it at mount and refuses to write once it has moved, which is the
   * same decision as the latch, expressed where the view can make it. */
  let entryEpoch = 0;

  function resolve(hash) {
    const match = matchRoute(hash, routes);
    if (match) return { match, unknown: null };
    // Unknown: non-blocking notice, then home. The hash is NOT rewritten —
    // pushing a history entry for a typo would make Back re-enter it.
    return { match: null, unknown: normalizeHash(hash) };
  }

  function fallbackTarget(hash) {
    return {
      pattern: fallbackPattern,
      view: fallback.view,
      params: fallback.params,
      hash: normalizeHash(hash),
    };
  }

  function resolveRoot() {
    if (root) return root;
    const doc = win.document;
    return doc ? doc.getElementById('app-root') : null;
  }

  function render(hash) {
    const { match, unknown } = resolve(hash);
    if (unknown !== null && onUnknownRoute) onUnknownRoute(unknown);
    const target = match || fallbackTarget(hash);
    const host = resolveRoot();
    // A same-route dispatch (Pattern F's iOS resume re-render) and a history
    // traversal (Back) both restore the entry's own scroll offset; a forward
    // navigation starts at the top. Scrolling to 0 for a NEW view is what stops
    // a list from inheriting the detail's offset.
    const restoring = traversed || normalizeHash(win.location.hash) === target.hash;
    traversed = false;
    /* Read the offset FIRST, before anything can write to history.state. By
     * the time a hashchange handler runs, the browser has already pushed the
     * DESTINATION entry, so a `replaceState` from the outgoing view's teardown
     * lands in the entry being navigated TO — the exact inversion that makes
     * Back restore the wrong offset and makes a fresh view start halfway down
     * the previous one. */
    const restoreTop = restoring ? router.readViewState().scrollTop : undefined;
    const previous = unmountActive;
    unmountActive = null;
    active = null;
    // unmount BEFORE mount (spec 9.3): a view that is still writing into `root`
    // while the next view owns it is the leak this ordering exists to prevent.
    if (typeof previous === 'function') {
      try {
        previous();
      } catch (error) {
        if (win.console && win.console.error) win.console.error('unmount failed', error);
      }
    }
    if (!host) return null;
    host.textContent = '';
    const view = views[target.view];
    if (!view || typeof view.mount !== 'function') {
      active = { view: target.view, params: target.params, hash: target.hash, missing: true };
      if (onNavigate) onNavigate(active);
      return active;
    }
    const teardown = view.mount(host, target.params);
    unmountActive = typeof teardown === 'function' ? teardown : null;
    active = {
      view: target.view,
      params: target.params,
      hash: target.hash,
      unknown,
      missing: false,
    };
    if (onNavigate) onNavigate(active);
    if (typeof win.scrollTo === 'function') {
      win.scrollTo(0, typeof restoreTop === 'number' ? restoreTop : 0);
    }
    /* The traversal is now fully dispatched: the incoming view owns the entry
     * and has been told where to restore, so the latch comes down. Lowering it
     * HERE and not at the top of `render()` is deliberate — everything between
     * those two points, including the outgoing view's teardown, is a moment in
     * which the current entry is not the outgoing view's to write. */
    traversalPending = false;
    return active;
  }

  function dispatch() {
    if (stopping) return null;
    return render(win.location.hash);
  }

  const router = {
    routes,

    start() {
      if (started) {
        dispatch();
        return router;
      }
      started = true;
      onHashChange = () => dispatch();
      onPopState = () => {
        traversed = true;
        /* The instant the browser commits the arrival. Two things happen here and
         * both are load-bearing: the entry epoch moves, so a view can tell that
         * the entry it was mounted for is no longer the current one; and the
         * latch goes up, so a tracked-scroll save that arrives in the window
         * between this and the `hashchange` dispatch is dropped rather than
         * written into the entry being arrived at. */
        traversalPending = true;
        entryEpoch += 1;
      };
      win.addEventListener('hashchange', onHashChange);
      win.addEventListener('popstate', onPopState);
      const doc = win.document;
      if (doc && doc.readyState === 'loading') {
        doc.addEventListener('DOMContentLoaded', () => dispatch(), { once: true });
      } else {
        dispatch();
      }
      return router;
    },

    stop() {
      stopping = true;
      if (started && onHashChange) win.removeEventListener('hashchange', onHashChange);
      if (started && onPopState) win.removeEventListener('popstate', onPopState);
      if (typeof unmountActive === 'function') unmountActive();
      unmountActive = null;
      active = null;
      started = false;
      onHashChange = null;
      onPopState = null;
      /* The latch is dropped with the listeners that raise it. A stopped router
       * has no `popstate` handler left to lower it, and a `saveViewState` that
       * stayed latched for the rest of the session would silently discard every
       * offset the app recorded. */
      traversalPending = false;
      return router;
    },

    navigate(hash) {
      const normalized = normalizeHash(hash);
      if (win.location.hash === normalized) return dispatch();
      /* A push, not a traversal: `popstate` does not fire, so the epoch moves
       * here instead. Assigning `location.hash` tears the current view's
       * document down, the document collapses, and the collapse is an ordinary
       * scroll event the outgoing view's listener is still attached to — the
       * mirror image of the traversal race, and it lands the outgoing view's
       * offset in the DESTINATION entry. A forward navigation ignores the
       * destination's saved offset anyway, so dropping that write costs
       * nothing. */
      entryEpoch += 1;
      // Assigning `location.hash` pushes a history entry, which is what makes
      // the origin entry (and its saved state) reachable by Back.
      win.location.hash = normalized;
      return null;
    },

    reload() {
      return dispatch();
    },

    /* template 2i, verbatim. Applied to the Back affordance AND to the
     * loading-error and not-found states. */
    goBack(fallback = fallbackPattern) {
      if (win.history.length <= 1) return router.navigate(fallback);
      return win.history.back();
    },

    readViewState() {
      const state = win.history.state;
      return state && typeof state === 'object' ? state : {};
    },

    currentEntryEpoch() {
      return entryEpoch;
    },

    /* replaceState, never pushState: this records where the user is, it does
     * not create a step they could Back into.
     *
     * REFUSED while a traversal is being committed. `popstate` has fired and
     * the `hashchange` dispatch has not run yet, so the browser has already
     * made the destination entry current and no view owns it: a write here
     * would be the outgoing view's offset, or the engine's mid-restore value,
     * landing in the entry being arrived at. The refusal returns the state as
     * it actually is, so a caller that reads the result is never told about a
     * write that did not happen. */
    saveViewState(patch) {
      const current = router.readViewState();
      if (traversalPending) return current;
      const next = mergeViewState(current, patch);
      win.history.replaceState(next, '');
      return next;
    },

    current() {
      return active;
    },
  };

  return router;
}

/* The app's singleton. Views import these helpers rather than a router
 * object, so a view module has no construction step and `main.js` stays the
 * single place that knows which window the app runs in. */
let instance = null;

export function initRouter(options) {
  instance = createRouter(options);
  return instance;
}

function require_() {
  if (!instance) throw new Error('router: initRouter() has not run yet');
  return instance;
}

export function navigate(hash) {
  return require_().navigate(hash);
}

export function reload() {
  return require_().reload();
}

export function goBack(fallback) {
  return require_().goBack(fallback);
}

export function saveViewState(patch) {
  return require_().saveViewState(patch);
}

/**
 * This view's own `history` entry state — the same value the router reads when
 * it restores the offset.
 *
 * Exported for one specific reason, and it is not convenience. The router calls
 * `scrollTo` immediately after `mount()` returns, but a view that fetches is
 * still showing its loading state at that moment, so a long list is a few pixels
 * tall and the restore silently CLAMPS (measured in Chromium: an offset of 640
 * lands on 16). A view whose first paint is asynchronous therefore has to
 * re-apply the target once its content is in, and it must read the SAME entry
 * the router read rather than keeping its own copy of the number — two copies
 * of a restore target is exactly how they come to disagree.
 */
export function readViewState() {
  return require_().readViewState();
}

/**
 * The identity of the `history` entry this view was mounted for. It moves on a
 * traversal and on a push, so `epoch === currentEntryEpoch()` is exactly the
 * statement "the entry I am recording into is still mine".
 *
 * A view that keeps a tracked-scroll save should capture this at `mount()` and
 * drop the save when it no longer holds. The reason it is a number rather than
 * a boolean is that the decision belongs at the moment of the SAVE, which
 * `trackScroll` defers to the next animation frame — and a frame is always
 * after the traversal's own event dispatch, whichever order the engine chooses
 * to deliver the events in.
 */
export function currentEntryEpoch() {
  return require_().currentEntryEpoch();
}

export function currentRoute() {
  return require_().current();
}

/* The tab bar: 菜谱 / 食材, and the 更多 menu that holds the other three routes.
 *
 * **TWO TABS PLUS A MENU, NOT FIVE TABS.** The two the user asked for are the
 * two the app is actually about — what can I cook, and what I have — and they
 * are the two a thumb reaches. `#/shortlists`, `#/settings` and `#/provenance`
 * keep their routes, their views and their history entries; they are simply
 * reached through one menu instead of being typed as a hash, which is the only
 * way any of them was reachable before this file existed.
 *
 * **THE TABS ARE `<a href="#/…">`, NOT BUTTONS WITH A CLICK HANDLER.** The app
 * has a hash router, and a hash link is a navigation the router's own
 * `hashchange` listener performs: it pushes a history entry, it works with
 * middle-click and long-press "open in new tab", it survives JS being slow, and
 * it needs no code here at all. A button that called `navigate()` would have to
 * re-implement every one of those, and would get the middle-click one wrong.
 *
 * **WHAT IS ACTUALLY IN THIS FILE, THEN:** 更多's open/close, and the
 * current-route mark. Both need behaviour, and both need a single owner — a
 * second place that decided "which tab is active" is a second place to forget.
 *
 * **`TABS` IS THE CONTRACT WITH `index.html`, AND A TEST ENFORCES IT.** The
 * markup is static (the shell is precached, so the bar is on screen at first
 * paint offline, which a JS-built bar is not), so the two could drift.
 * `tests/js/tabbar.test.mjs` reads `index.html` and asserts every `data-tab`
 * href here and there are the same set. That is the only reason `TABS` exists
 * as data rather than as comments.
 *
 * **`markActive` MARKS BY VIEW NAME, NOT BY PATTERN.** The router hands
 * `onNavigate` the matched route's `view`, which is already resolved — no
 * second `matchRoute` call, and a tab can never disagree with the route table
 * about what `#/pantry` is.
 *
 * **THE 更多 BUTTON IS MARKED CURRENT FOR ALL THREE OF ITS ROUTES.** A user who
 * taps 更多 → 溯源 is on 溯源, and a bar showing neither tab nor 更多 as current
 * is the one place this design could have lied. `MORE_VIEWS` is what makes that
 * statement true, and it is data rather than a rule inferred from the markup.
 *
 * **NO OUTSIDE-TAP DISMISS, DELIBERATELY.** The menu has no scrim (a menu of
 * three routes does not need one), and a scrimless menu has no obvious owner
 * for a tap on the page behind it — closing on it would make a scroll that
 * starts as a flick and registers as a tap close the menu, and not closing on
 * it would leave a user who expects iOS behaviour stuck. Escape, the toggle
 * itself, and any navigation all close it, which are the three dismissals with
 * an unambiguous owner.
 *
 * **THE SHEET KEEPS THE VENDORED `.form-sheet` CLASS.** `main.js`'s F18 §4e
 * busy-guard looks for `.form-sheet:not([hidden])` to decide that a modal holds
 * input a forced Service Worker reload would discard; an open menu is that
 * state. The class is in `index.html` rather than here for the same reason the
 * markup is — see the comment there.
 *
 * NO DOM AT MODULE SCOPE, and the window is an argument: this module is
 * importable by `node --test` with no global stub, which is the same
 * arrangement `router.js` uses and for the same reason.
 */

/**
 * The routes the bar reaches, in the order they appear. `key` is the `data-tab`
 * attribute on the markup; `label` is asserted against `index.html`'s text so a
 * renamed tab cannot pass.
 */
export const TABS = Object.freeze([
  Object.freeze({ key: 'home', pattern: '#/', label: '菜谱' }),
  Object.freeze({ key: 'pantry', pattern: '#/pantry', label: '食材' }),
]);

/**
 * The three routes behind 更多, and the label the menu row carries. `key` IS the
 * router's view name for that route, not a tab-local alias — which is what lets
 * `markActive` take the `view` `onNavigate` hands it and match without a second
 * route table.
 */
export const MORE_LINKS = Object.freeze([
  Object.freeze({ key: 'shortlists', pattern: '#/shortlists', label: '清单' }),
  Object.freeze({ key: 'provenance', pattern: '#/provenance', label: '溯源' }),
  Object.freeze({ key: 'settings', pattern: '#/settings', label: '调试与严格模式' }),
]);

/** The button that opens the menu. It is not a route and never becomes one. */
export const MORE_KEY = 'more';

/**
 * The view names that count as "更多 is where you are". Deliberately a separate
 * list from `MORE_LINKS` rather than derived from it: a future fourth menu row
 * that is a SHEET (no route of its own) would otherwise silently become a
 * current-route marker for a view that is not one of its destinations.
 */
export const MORE_VIEWS = Object.freeze(MORE_LINKS.map((link) => link.key));

/** Every `data-tab` the shell markup is expected to carry, bar and menu alike. */
export const ALL_TAB_KEYS = Object.freeze([
  ...TABS.map((tab) => tab.key),
  MORE_KEY,
  ...MORE_LINKS.map((link) => link.key),
]);

export function isMoreView(view) {
  return MORE_VIEWS.includes(view);
}

/**
 * The `data-tab` that owns a route, or `null` for a view no tab owns — the
 * recipe detail, which is reached from a list and is a destination rather than
 * a section. Returning `null` rather than falling back to 菜谱 is deliberate: a
 * detail view is emphatically not the recipe list, and marking 菜谱 there would
 * tell the user they are somewhere they are not.
 */
export function tabKeyForView(view) {
  const tab = TABS.find((candidate) => candidate.key === view);
  if (tab) return tab.key;
  return isMoreView(view) ? MORE_KEY : null;
}

function byKey(win, key) {
  const doc = win.document;
  return doc ? doc.querySelector(`[data-tab="${key}"]`) : null;
}

function isOpen(sheet) {
  return Boolean(sheet) && !sheet.hasAttribute('hidden');
}

/**
 * Wire the bar. Returns `{ markActive, isMenuOpen, destroy }`.
 *
 * `markActive(view)` is called by `main.js` from the router's `onNavigate`, so
 * the bar follows navigation without this module importing the router — the
 * dependency runs one way, and the router never learns that a tab bar exists.
 */
export function initTabbar({ window: win = globalThis } = {}) {
  if (!win) throw new TypeError('initTabbar requires a window');
  const doc = win.document;
  const button = doc ? doc.querySelector(`[data-tab="${MORE_KEY}"]`) : null;
  const sheet = doc ? doc.getElementById('more-sheet') : null;
  const listeners = [];
  let destroyed = false;

  function listen(target, type, handler) {
    if (!target) return;
    target.addEventListener(type, handler);
    listeners.push(() => target.removeEventListener(type, handler));
  }

  /* Reflecting the menu's state in the DOM rather than in a variable is the
   * whole trick: `hidden` and `aria-expanded` are what `main.js`'s busy-guard
   * and a screen reader read, so a second copy of "is it open" in JS could only
   * ever disagree with the thing that matters. */
  function setOpen(open) {
    if (!sheet || !button) return;
    if (open) sheet.removeAttribute('hidden');
    else sheet.setAttribute('hidden', '');
    button.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  function toggle() {
    setOpen(!isOpen(sheet));
  }

  function markActive(view) {
    const active = tabKeyForView(view);
    for (const key of ALL_TAB_KEYS) {
      const node = byKey(win, key);
      if (!node) continue;
      // `setAttribute`/`removeAttribute` rather than a class, because
      // `aria-current` is the accessible state and the CSS keys off it: one
      // attribute, one source of truth, and a screen reader gets the same
      // answer the paint does.
      if (key === active) node.setAttribute('aria-current', 'page');
      else node.removeAttribute('aria-current');
    }
    // Navigating away from a 更多 destination closes the menu, whatever route
    // it navigated to — a menu left open over a screen it does not belong to is
    // a control the user cannot account for.
    if (active !== MORE_KEY) setOpen(false);
  }

  listen(button, 'click', toggle);
  /* A menu row is a real hash link, so the router navigates and `hashchange`
   * is what tells us the navigation is done. Closing on the EVENT rather than
   * on the click is what also covers Back/Forward: a traversal re-enters
   * `#/provenance` with no click at all. */
  listen(win, 'hashchange', () => setOpen(false));
  listen(doc, 'keydown', (event) => {
    if (event && event.key === 'Escape' && isOpen(sheet)) setOpen(false);
  });

  return {
    markActive,

    isMenuOpen() {
      return isOpen(sheet);
    },

    destroy() {
      if (destroyed) return;
      destroyed = true;
      for (const off of listeners.splice(0)) off();
    },
  };
}

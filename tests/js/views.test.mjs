/* The two D4 views, rendered. node --test, no dependencies.
 *
 * These are the assertions a screenshot cannot make. They drive the REAL view
 * modules through the REAL `dom.js` `el()` against the fake DOM in
 * `fake-dom.mjs`, with `api.js`'s transport stubbed — so a failure means the
 * view is wrong, not that a hand-written stub drifted.
 *
 * What is asserted, and why each one is load-bearing:
 *
 *   1. **The four frozen headline strings, byte-exact.** `format.test.mjs`
 *      freezes `headline()`'s OUTPUT. This freezes that the VIEW PLACES that
 *      output unmodified — same bytes, no trimming, no appended text, no
 *      re-cased punctuation. A view that composes its own sentence passes the
 *      first test and fails this one, which is the whole point of splitting it.
 *   2. **Every chip tier renders with the class `chip-class.js` chose**, and
 *      with the three provenance attributes, in SLOT ORDER.
 *   3. **The 503 fail-closed state is non-empty, names the pantry note, and
 *      never claims the pantry is empty.** The 503 body is `{requestId, code}`
 *      with no `recipes` key, so a view that assumed a list would paint an
 *      empty screen — and an empty chip row reads as "nothing in your pantry",
 *      which is the single worst sentence this app can produce.
 *   4. **The empty state is an invitation, not an error** — and is reachable
 *      only from a SUCCESSFUL empty response, never during loading and never
 *      after a failure.
 *   5. **Offline, the cook-log control is disabled, says why, and queues
 *      nothing** (F5), and a hung request re-enables it (§8 story 54).
 *   6. **A 409 renders the server's `message` verbatim** through `error.message`
 *      — never `data.message` — and the follow-up write carries the fresh
 *      revision.
 *   7. **`mutationInFlight()` blocks the F18 §4e busy-guard while the cook log
 *      is in flight**, so a forced reload cannot land between the tap and the
 *      write.
 *   8. **Low-scoring recipes are never hidden**, and the rendered order is the
 *      one `logic/sort.js` specifies.
 */

import assert from 'node:assert/strict';
import { register } from 'node:module';
import test from 'node:test';

import { installFakeDom, textOf, walk } from './fake-dom.mjs';

register('./app-version-hook.mjs', import.meta.url);

/* The `?v=__APP_VERSION__` token is a serve-time substitution, so the modules
 * below can only be imported AFTER the resolve hook is registered — hence
 * dynamic import, and hence this file is the one place that knows the trick. */
const { el } = await import('../../app/static/js/dom.js');
const { chipRow, chip, classifySlot, scoredSlots, provenanceText, CHIP_CLASSES } = await import(
  '../../app/static/js/chips.js'
);
const { headline } = await import('../../app/static/js/logic/format.js');
const { sortRecipes } = await import('../../app/static/js/logic/sort.js');
const panels = await import('../../app/static/js/panels.js');
const { hasPendingEdit, markPendingEdits, pendingEdits, resetPendingEdits } = await import(
  '../../app/static/js/pending-edits.js'
);
const { initRouter } = await import('../../app/static/js/router.js');
const intents = await import('../../app/static/js/domain-intents.js');
const api = await import('../../app/static/js/api.js');
const { mount: mountHome } = await import('../../app/static/js/views/home.js');
const { mount: mountRecipe, MEALS, OFFLINE_REASON, TRACKER_BADGE, todayIn, ageInDays, ageLabel } =
  await import('../../app/static/js/views/recipe.js');

const { emptyState, errorState, loadPanel, sourceUnavailableState, isSourceUnavailable } = panels;

/* --- fixtures ------------------------------------------------------------ */

const sessionBody = (overrides = {}) => ({
  identity: 'you@example.com',
  csrfToken: 'token-a',
  version: 'v0.4.0',
  readOnly: false,
  appTimezone: 'America/New_York',
  ...overrides,
});

/** One slot, with the wire shape §9.16 publishes and nothing invented. */
function slot(index, overrides = {}) {
  return {
    index,
    rawValue: `原料${index}`,
    parsedName: `原料${index}`,
    parseMethod: 'bare',
    matchMethod: 'unresolved',
    matchTier: 8,
    pantryItemId: null,
    confidence: 0,
    candidatesJson: [],
    inStock: false,
    stockJoinState: 'unresolved',
    isSeasoning: false,
    ...overrides,
  };
}

function recipe(noteName, ingredients, overrides = {}) {
  return {
    noteName,
    notePath: `Hobbies/做饭/Recipes/${noteName}.md`,
    found: ingredients.filter((item) => !item.isSeasoning).length,
    total: ingredients.filter((item) => !item.isSeasoning).length,
    lastCooked: null,
    ingredients,
    tools: ['炒锅'],
    ...overrides,
  };
}

function listPayload(recipes, overrides = {}) {
  return {
    recipes,
    catalogRevision: 'sha256:1da7ba7e0a28a2f207519e37e218f4c1477e8199669e9ce69f1d5ec88bd8b6a9',
    stockRevision: 'sha256:fcdc163b762fcbbc71114b9e73803e976cbdbf085daecd6896585e921c6016db',
    strict: 0,
    staleMappingCount: 0,
    stockUnjoinedCount: 0,
    skipped: 0,
    ...overrides,
  };
}

/* The four recipes whose headlines ARE the four frozen strings of §9.13.1.
 * Each one is built so the count and the missing list are internally
 * consistent, which is the F17 correction: the Materials-only clause never
 * names a 调料, and the strict addendum is a SEPARATE, separately-labelled one.
 */
const SIX_OF_SIX = recipe('全都有', [
  slot(0, { parsedName: '香菇', matchMethod: 'normalized_exact', matchTier: 1, pantryItemId: 12, inStock: true, stockJoinState: 'joined' }),
  slot(1, { parsedName: '娃娃菜', matchMethod: 'synonym', matchTier: 6, pantryItemId: 13, inStock: true, stockJoinState: 'joined' }),
  slot(2, { parsedName: '西兰花', matchMethod: 'synonym', matchTier: 6, pantryItemId: 14, inStock: true, stockJoinState: 'joined' }),
  slot(3, { parsedName: '包菜', matchMethod: 'synonym', matchTier: 6, pantryItemId: 15, inStock: true, stockJoinState: 'joined' }),
  slot(4, { parsedName: '空心菜', matchMethod: 'synonym', matchTier: 6, pantryItemId: 83, inStock: true, stockJoinState: 'joined' }),
  slot(5, { parsedName: '米', matchMethod: 'normalized_exact', matchTier: 1, pantryItemId: 21, inStock: true, stockJoinState: 'joined' }),
  slot(0, { parsedName: '生抽', isSeasoning: true, matchMethod: 'staples', matchTier: 7, confidence: 0.6 }),
  slot(1, { parsedName: '蒜', isSeasoning: true, matchMethod: 'staples', matchTier: 7, confidence: 0.6 }),
  slot(2, { parsedName: '葱', isSeasoning: true, matchMethod: 'staples', matchTier: 7, confidence: 0.6 }),
  slot(3, { parsedName: '盐', isSeasoning: true, matchMethod: 'staples', matchTier: 7, confidence: 0.6 }),
], { found: 6, total: 6 });

const FOUR_OF_SIX = recipe('缺两样', [
  slot(0, { parsedName: '香菇', matchMethod: 'normalized_exact', matchTier: 1, pantryItemId: 12, inStock: true, stockJoinState: 'joined' }),
  slot(1, { parsedName: '娃娃菜', matchMethod: 'normalized_exact', matchTier: 1, pantryItemId: 13, inStock: true, stockJoinState: 'joined' }),
  slot(2, { parsedName: '西兰花', matchMethod: 'synonym', matchTier: 6, pantryItemId: 14, inStock: true, stockJoinState: 'joined' }),
  slot(3, { parsedName: '包菜', matchMethod: 'synonym', matchTier: 6, pantryItemId: 15, inStock: true, stockJoinState: 'joined' }),
  slot(4, { parsedName: '香菇' }),
  slot(5, { parsedName: '娃娃菜' }),
  slot(0, { parsedName: '生抽', isSeasoning: true, matchMethod: 'staples', matchTier: 7, confidence: 0.6 }),
  slot(1, { parsedName: '蒜', isSeasoning: true, matchMethod: 'staples', matchTier: 7, confidence: 0.6 }),
], { found: 4, total: 6 });

/* The strict-mode form: four 材料 (two of them missing) plus one UNRESOLVED
 * 调料. Under 严格模式 the denominator is 5, the numerator 2, the Materials-only
 * clause names `Clam, 香菇`, and `生抽` appears only in the separately-labelled
 * addendum. This is the exact slot list tests/js/logic/format.test.mjs freezes,
 * so the view is asserted against the same shape the logic gate is. */
const TWO_OF_FIVE = recipe('花蛤拌饭', [
  slot(0, { parsedName: '茼蒿', matchMethod: 'normalized_exact', matchTier: 1, pantryItemId: 30, inStock: true, stockJoinState: 'joined' }),
  slot(1, { rawValue: 'Clam', parsedName: 'Clam' }),
  slot(2, { parsedName: '红苋菜', matchMethod: 'normalized_exact', matchTier: 1, pantryItemId: 31, inStock: true, stockJoinState: 'joined' }),
  slot(3, { parsedName: '香菇' }),
  slot(0, { parsedName: '生抽', isSeasoning: true }),
], { found: 2, total: 4 });

const ZERO_OF_THREE = recipe('炒空心菜', [
  slot(0, { parsedName: '空心菜' }),
  slot(1, { parsedName: '香菇' }),
  slot(2, { parsedName: '娃娃菜' }),
], { found: 0, total: 3 });

/* --- harness -------------------------------------------------------------- */

const fetches = [];
let responder = () => json(listPayload([]), 200);

function json(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => null },
    async json() {
      return body;
    },
  };
}

/**
 * The fake DOM, the router, and **one app outbox**.
 *
 * The outbox is part of the boot contract now (#23): `main.js` creates exactly
 * one, and `views/recipe.js` reaches it through `writeIntent`. A view test that
 * mounted the recipe view without one would be testing a state the app can never
 * be in, and the failure it would produce is a rejected promise rather than a
 * missing button — so the harness builds the real thing over the fake DOM's
 * storage instead of stubbing the module.
 */
function install(options = {}) {
  const dom = installFakeDom(options);
  initRouter({ window: dom.window, views: {} });
  dom.outbox = intents.initOutbox({
    storage: dom.localStorage,
    eventTarget: dom.window,
    document: dom.document,
    navigator: dom.navigator,
    serviceWorker: null,
    // The vendored adapter's 30 s interval must not hold the runner's event
    // loop open, and no view test depends on real elapsed time.
    timers: { setInterval: () => 0, clearInterval: () => {} },
    events: { addEventListener() {}, removeEventListener() {}, dispatchEvent: () => true },
    autoStart: false,
  });
  // The vendored adapter registers its `online` / `focus` / `visibilitychange`
  // listeners on the window and only removes them in `close()`. Restoring the
  // DOM without closing the outbox would leave one behind, and the listener
  // ledger test below is exactly the assertion that would catch it — so the two
  // are tied together here rather than in each test.
  const restoreDom = dom.restore;
  dom.restore = () => {
    dom.outbox.close();
    restoreDom();
  };
  return dom;
}

function setResponder(fn) {
  responder = fn;
}

globalThis.fetch = async (url, init = {}) => {
  fetches.push({ url: String(url), init });
  return responder(String(url), init, fetches.length);
};

/**
 * Find a BUTTON by its label.
 *
 * `walk` returns ancestors too, and a wrapper div holding exactly one labelled
 * button has the same `textContent` — so a bare text match silently taps a div
 * and the assertion then fails for a reason that has nothing to do with the view.
 */
function button(root, label) {
  return walk(root).find((node) => node.tagName === 'BUTTON' && node.textContent === label);
}

/** Let every queued microtask and promise chain settle. */
const settle = async (turns = 6) => {
  for (let i = 0; i < turns; i += 1) await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
};

test.beforeEach(() => {
  fetches.length = 0;
  api.resetApi();
  api.recipeListCache.clear();
  resetPendingEdits();
  // The outbox is a module singleton, so its queue has to start empty for each
  // test or one test's queued intent replays inside the next one.
  intents.resetOutbox();
  setResponder(() => json(sessionBody(), 200));
});

test.afterEach(() => {
  api.resetApi();
  intents.resetOutbox();
  resetPendingEdits();
});

function dom_(role) {
  return null;
}
void dom_;

/* ==========================================================================
   1. The four frozen strings
   ========================================================================== */

test('the four frozen headline strings are produced by headline() unchanged', () => {
  assert.equal(headline({ ingredients: scoredSlots(SIX_OF_SIX.ingredients, false) }), '6/6 ingredients found');
  assert.equal(
    headline({ ingredients: scoredSlots(FOUR_OF_SIX.ingredients, false) }),
    '4/6 ingredients found — missing: 香菇, 娃娃菜',
  );
  assert.equal(
    headline({ ingredients: scoredSlots(ZERO_OF_THREE.ingredients, false) }),
    '0/3 ingredients found — missing: 空心菜, 香菇, 娃娃菜',
  );
  // Three ASCII spaces before the addendum, and 生抽 appears ONLY there.
  assert.equal(
    headline({ ingredients: scoredSlots(TWO_OF_FIVE.ingredients, true), strict: true }),
    '2/5 ingredients found — missing: Clam, 香菇   (严格模式（含调料）: 生抽)',
  );
});

test('the home view renders the non-strict frozen strings byte-exact, in sorted order', async () => {
  const dom = install();
  setResponder((url) => {
    if (url.includes('/api/session')) return json(sessionBody());
    return json(listPayload([ZERO_OF_THREE, FOUR_OF_SIX, SIX_OF_SIX]));
  });
  const unmount = mountHome(dom.root);
  await settle();

  const rendered = dom.byData('headline').map((node) => node.textContent);
  assert.deepEqual(rendered, [
    '6/6 ingredients found',
    '4/6 ingredients found — missing: 香菇, 娃娃菜',
    '0/3 ingredients found — missing: 空心菜, 香菇, 娃娃菜',
  ]);
  unmount();
  dom.restore();
});

test('under ?strict=1 the same list renders the strict frozen string, three spaces and all', async () => {
  const dom = install({ search: '?strict=1' });
  setResponder((url) => {
    if (url.includes('/api/session')) return json(sessionBody());
    return json(listPayload([TWO_OF_FIVE, ZERO_OF_THREE], { strict: 1 }));
  });
  const unmount = mountHome(dom.root);
  await settle();

  const rendered = dom.byData('headline').map((node) => node.textContent);
  assert.deepEqual(rendered, [
    '2/5 ingredients found — missing: Clam, 香菇   (严格模式（含调料）: 生抽)',
    '0/3 ingredients found — missing: 空心菜, 香菇, 娃娃菜',
  ]);
  // Byte-exact means byte-exact: exactly three ASCII spaces before the addendum,
  // and `生抽` in the addendum ONLY.
  assert.equal(/[^ ]( {3})\(严格模式/.exec(rendered[0])[1].length, 3);
  const [materials, addendum] = rendered[0].split('   (严格模式（含调料）: ');
  assert.equal(materials.includes('生抽'), false, 'a 调料 leaked into the Materials-only clause');
  assert.equal(addendum, '生抽)');
  unmount();
  dom.restore();
});

test('no headline is ever decorated — no suffix, no prefix, no trimming', async () => {
  const dom = install();
  setResponder((url) => (url.includes('/api/session') ? json(sessionBody()) : json(listPayload([SIX_OF_SIX]))));
  const unmount = mountHome(dom.root);
  await settle();
  const node = dom.byData('headline')[0];
  assert.equal(node.textContent, '6/6 ingredients found');
  assert.equal(node.textContent.trim(), node.textContent);
  assert.equal(node.textContent.includes('\n'), false);
  unmount();
  dom.restore();
});

/* ==========================================================================
   2. The chip tiers
   ========================================================================== */

test('chipClass chooses the class and the view paints exactly that class', () => {
  const dom = install();
  const cases = [
    [slot(0, { matchMethod: 'normalized_exact', pantryItemId: 1, inStock: true }), 'chip--in-stock'],
    [slot(0, { matchMethod: 'synonym', pantryItemId: 2, inStock: false }), 'chip--have-been-buying'],
    [slot(0, { matchMethod: 'staples', isSeasoning: true }), 'chip--assumed-staple'],
    [slot(0, {}), 'chip--missing'],
    [slot(0, { isSeasoning: true }), 'chip--ignored-seasoning'],
  ];
  for (const [one, expected] of cases) {
    const rendered = chip(one);
    assert.ok(
      rendered.className.includes(expected),
      `${one.matchMethod}/${one.isSeasoning} rendered ${rendered.className}, wanted ${expected}`,
    );
  }
  // A hand fix is visibly different: the same colour, plus the outline.
  const manualInStock = chip(slot(0, { matchMethod: 'manual', pantryItemId: 3, inStock: true }));
  assert.ok(manualInStock.className.includes('chip--in-stock'));
  assert.ok(manualInStock.className.includes('chip--manual'));
  const manualNotHeld = chip(slot(0, { matchMethod: 'manual', pantryItemId: 3, inStock: false }));
  assert.ok(manualNotHeld.className.includes('chip--have-been-buying'));
  assert.ok(manualNotHeld.className.includes('chip--manual'));

  // Every class the two views can emit is one of the five, plus the manual
  // outline. A sixth bucket is a bug in a view, not a new tier.
  const emitted = new Set(
    walk(dom.root).flatMap((node) => String(node.className || '').split(/\s+/).filter(Boolean)),
  );
  for (const name of emitted) {
    if (name === 'chip') continue;
    assert.ok(
      CHIP_CLASSES.includes(name) || name === 'chip--manual' || name === 'chip--tool',
      `unexpected chip class: ${name}`,
    );
  }
  dom.restore();
});

test('F16: a staples-satisfied Seasoning is found, an unresolved one is missing', () => {
  const staple = slot(0, { parsedName: '生抽', isSeasoning: true, matchMethod: 'staples' });
  const unresolved = slot(1, { parsedName: '蚝油', isSeasoning: true });
  assert.equal(classifySlot(staple, true).missing, false);
  assert.equal(classifySlot(unresolved, true).missing, true);
  // Outside strict mode a Seasoning is IGNORED, which is what keeps the
  // Materials-only missing list Materials-only.
  assert.equal(classifySlot(unresolved, false).className, 'chip--ignored-seasoning');
  assert.equal(classifySlot(unresolved, false).missing, false);
  assert.equal(
    headline({ ingredients: scoredSlots([unresolved], false) }),
    '0/0 ingredients found',
    'a Seasoning must not enter the default view at all',
  );
});

test('the chip row is in SLOT ORDER, never sorted, and carries the three provenance attributes', () => {
  const dom = install();
  const ingredients = [
    slot(0, { parsedName: '香菇', matchMethod: 'normalized_exact', pantryItemId: 12, inStock: true, matchTier: 1, stockJoinState: 'joined' }),
    slot(1, { parsedName: '空心菜' }),
    slot(2, { parsedName: '娃娃菜' }),
    slot(0, { parsedName: '生抽', isSeasoning: true, matchMethod: 'staples' }),
  ];
  const row = chipRow({ ingredients, strict: false });
  dom.root.appendChild(row);
  const chips = walk(row).filter((node) => node.classList.contains('chip'));
  assert.deepEqual(
    chips.map((node) => node.firstChild.textContent),
    ['香菇', '空心菜', '娃娃菜', '生抽'],
    'slot order is information and must not be sorted',
  );
  assert.equal(chips[0].dataset.matchMethod, 'normalized_exact');
  assert.equal(chips[0].dataset.tier, '1');
  assert.equal(chips[0].dataset.itemId, '12');
  assert.equal(chips[1].dataset.itemId, '', 'an unresolved slot has no id, not the string "null"');
  assert.equal(chips[3].dataset.seasoning, 'true');
  assert.equal(chips[0].dataset.joinState, 'joined');
  dom.restore();
});

test('the 调试 toggle adds a provenance line to every chip and no chip is lost', () => {
  const dom = install();
  const ingredients = [
    slot(0, { parsedName: '香菇', matchMethod: 'normalized_exact', pantryItemId: 12, inStock: true, matchTier: 1 }),
    slot(1, { parsedName: '空心菜' }),
  ];
  const plain = chipRow({ ingredients });
  dom.root.appendChild(plain);
  const plainLines = walk(plain).filter((node) => node.dataset.role === 'provenance');
  assert.equal(plainLines.length, 0);
  plain.remove();

  const debug = chipRow({ ingredients, debug: true });
  dom.root.appendChild(debug);
  const lines = walk(debug).filter((node) => node.dataset.role === 'provenance');
  assert.equal(lines.length, 2);
  assert.equal(lines[0].textContent, provenanceText(ingredients[0]));
  assert.ok(lines[0].textContent.includes('tier 1'));
  assert.ok(lines[0].textContent.includes('#12'));
  assert.ok(lines[1].textContent.includes('—'), 'an unresolved slot shows a dash, not "#null"');
  dom.restore();
});

/* ==========================================================================
   3. The 503 fail-closed state
   ========================================================================== */

test('a 503 renders a real, named, actionable state — and no chip row at all', async () => {
  const dom = install();
  setResponder((url) => {
    if (url.includes('/api/session')) return json(sessionBody());
    return json({ requestId: 'req-503', code: 'pantry_stock_unreadable' }, 503);
  });
  const unmount = mountHome(dom.root);
  await settle();

  const panel = dom.byData('source-unavailable')[0];
  assert.ok(panel, 'the 503 painted no unavailable state');
  const text = textOf(panel);
  assert.ok(text.length > 0);
  assert.ok(text.includes('Pantry.md'), `the state does not name the note: ${text}`);
  assert.ok(text.includes('Logistics/库存/Pantry.md'));
  assert.ok(text.includes('pantry_stock_unreadable'), 'the server code is not shown');
  assert.ok(text.includes('req-503'), 'the requestId is not shown');

  // The dangerous assertions. A chip row here would read as "your pantry is
  // empty", and any empty-state wording would say the same thing.
  assert.equal(dom.byData('chips').length, 0, 'a 503 rendered a chip row');
  assert.equal(dom.byData('recipe').length, 0, 'a 503 rendered recipe rows');
  assert.equal(dom.byData('empty-recipes').length, 0, 'a 503 rendered the empty-invitation state');
  const whole = textOf(dom.root);
  assert.equal(whole.includes('还没有菜谱'), false);
  assert.equal(whole.includes('没有可以显示'), false);
  // F1's own rule: this failure is permanent until the user fixes the vault, so
  // the state must not invite a retry that cannot help.
  assert.equal(whole.includes('重试'), false);
  assert.equal(whole.includes('稍后'), false);
  assert.ok(text.includes('不代表你的厨房里什么都没有'));

  unmount();
  dom.restore();
});

test('the recipe detail 503 names the note, and the Back affordance is still there', async () => {
  const dom = install();
  setResponder((url) => {
    if (url.includes('/api/session')) return json(sessionBody());
    return json({ requestId: 'req-503b', code: 'pantry_stock_unreadable' }, 503);
  });
  const unmount = mountRecipe(dom.root, { basename: '盐焗鸡' });
  await settle();

  const panel = dom.byData('source-unavailable')[0];
  assert.ok(panel, 'the detail 503 painted no unavailable state');
  assert.ok(textOf(panel).includes('Logistics/库存/Pantry.md'));
  assert.equal(dom.byData('chips').length, 0);
  // §2i: a failed load still returns to where the user came from, so the Back
  // affordance is part of the error state, not only of the happy path.
  const back = button(dom.root, '返回');
  assert.ok(back, 'the 503 state has no Back affordance');
  unmount();
  dom.restore();
});

test('isSourceUnavailable keys on the 503 status, whatever the code is', () => {
  assert.equal(isSourceUnavailable({ status: 503, code: 'pantry_stock_unreadable' }), true);
  assert.equal(isSourceUnavailable({ status: 503, code: 'missing_recipes_root' }), true);
  assert.equal(isSourceUnavailable({ status: 404, code: 'recipe_not_found' }), false);
  assert.equal(isSourceUnavailable({ status: 0, code: 'offline' }), false);
  assert.equal(isSourceUnavailable(null), false);
});

test('the unavailable state never claims the pantry is empty, whatever it says', () => {
  const dom = install();
  const state = sourceUnavailableState({ error: { status: 503, code: 'pantry_db_unreadable', requestId: 'r' } });
  dom.root.appendChild(state);
  const text = textOf(state);
  assert.ok(text.includes('pantry_items.db'), 'the catalog branch must name the catalog');
  assert.equal(state.dataset.panelState, 'unavailable');
  assert.ok(dom.byData('unavailable-note').length === 1);
  assert.ok(text.includes('不代表你的厨房里什么都没有'));
  dom.restore();
});

/* ==========================================================================
   4. Empty states
   ========================================================================== */

test('an empty list is an invitation, and only a SUCCESSFUL empty response reaches it', async () => {
  const dom = install();
  setResponder((url) => (url.includes('/api/session') ? json(sessionBody()) : json(listPayload([]))));
  const unmount = mountHome(dom.root);
  await settle();
  const empty = dom.byData('empty-recipes')[0];
  assert.ok(empty, 'a successful empty list rendered no empty state');
  assert.equal(empty.dataset.panelState, 'empty');
  const text = textOf(empty);
  assert.ok(text.includes('还没有菜谱'));
  assert.ok(text.includes('Obsidian'));
  assert.equal(text.includes('error'), false, 'the empty state is styled as an error');
  assert.equal(empty.className.includes('error'), false);
  unmount();
  dom.restore();
});

test('a failed list load renders an error state, never the empty invitation', async () => {
  const dom = install();
  setResponder((url) => {
    if (url.includes('/api/session')) return json(sessionBody());
    return json({ requestId: 'req-500', code: 'boom' }, 500);
  });
  const unmount = mountHome(dom.root);
  await settle();
  assert.equal(dom.byData('empty-recipes').length, 0, 'a 500 rendered the empty invitation');
  const failure = dom.byData('list-error')[0];
  assert.ok(failure, 'a 500 rendered no error state');
  assert.equal(failure.dataset.panelState, 'error');
  unmount();
  dom.restore();
});

test('an empty meal shortlist is a normal empty state, not an error', () => {
  const dom = install();
  // D3: all three slots are always present, possibly empty, and an empty list
  // renders as an invitation. This is the ONE empty-state primitive the
  // shortlists view is required to use, asserted here so the wording and the
  // absence of any error affordance are a tested property rather than a habit.
  for (const meal of MEALS) {
    const state = emptyState({
      title: `${meal.label}清单还是空的。`,
      body: '在菜谱上加一个「+ ' + meal.label + '」，它就会出现在这里。',
      dataset: { role: 'empty-shortlist', slot: meal.slot },
    });
    assert.equal(state.dataset.panelState, 'empty');
    assert.equal(state.dataset.role, 'empty-shortlist');
    assert.equal(state.dataset.slot, meal.slot);
    assert.ok(textOf(state).includes(meal.label));
    assert.equal(state.className.includes('error'), false, `${meal.slot} is styled as an error`);
    assert.equal(walk(state).some((node) => String(node.className || '').includes('error')), false);
    assert.equal(walk(state).some((node) => node.tagName === 'BUTTON'), false, 'an empty state offers nothing to retry');
  }
  // And the two control labels the empty state invites the user to press exist.
  assert.deepEqual(MEALS.map((meal) => meal.label), ['早餐', '午餐', '晚餐']);
  dom.restore();
});

/* ==========================================================================
   5. Sorting, and the "never auto-hidden" rule
   ========================================================================== */

test('the rendered order is logic/sort.js order, computed by logic/sort.js itself', async () => {
  const dom = install();
  const a = recipe('A', [slot(0, { matchMethod: 'synonym', pantryItemId: 1, inStock: true })], { found: 2, total: 2, lastCooked: '2025-01-01' });
  const b = recipe('B', [slot(0, { matchMethod: 'synonym', pantryItemId: 1, inStock: true })], { found: 2, total: 2, lastCooked: '2026-01-01' });
  const c = recipe('C', [slot(0, { matchMethod: 'synonym', pantryItemId: 1, inStock: true })], { found: 2, total: 2, lastCooked: null });
  const zero = recipe('零', [slot(0), slot(1), slot(2), slot(3), slot(4), slot(5)], { found: 0, total: 6 });
  setResponder((url) =>
    url.includes('/api/session') ? json(sessionBody()) : json(listPayload([zero, c, a, b])),
  );
  const unmount = mountHome(dom.root);
  await settle();

  const rendered = dom.byData('recipe').map((node) => node.dataset.note);
  // lastCooked descending, so B (2026) then A (2025) then C (no last_cooked) —
  // and the 0/6 row LAST, not hidden.
  assert.deepEqual(rendered, ['B', 'A', 'C', '零']);
  assert.equal(dom.byData('rows')[0].querySelectorAll('.recipe-row').length, 4);

  // The comparator is the shipped one, and it agrees with what was painted.
  assert.deepEqual(
    sortRecipes([zero, c, a, b]).map((item) => item.noteName),
    rendered,
  );
  unmount();
  dom.restore();
});

test('a 0/6 recipe is a first-class row with a full six-name missing list', async () => {
  const dom = install();
  const six = recipe('全缺', [
    slot(0, { parsedName: '空心菜' }),
    slot(1, { parsedName: '香菇' }),
    slot(2, { parsedName: '娃娃菜' }),
    slot(3, { parsedName: '茼蒿' }),
    slot(4, { parsedName: '红苋菜' }),
    slot(5, { parsedName: '开心果酱' }),
  ], { found: 0, total: 6 });
  setResponder((url) => (url.includes('/api/session') ? json(sessionBody()) : json(listPayload([six]))));
  const unmount = mountHome(dom.root);
  await settle();
  const rows = dom.byData('recipe');
  assert.equal(rows.length, 1, 'a 0/6 recipe was hidden');
  assert.equal(
    dom.byData('headline')[0].textContent,
    '0/6 ingredients found — missing: 空心菜, 香菇, 娃娃菜, 茼蒿, 红苋菜, 开心果酱',
  );
  assert.equal(dom.byData('chips')[0].querySelectorAll('.chip--missing').length, 6);
  unmount();
  dom.restore();
});

/* ==========================================================================
   6. Strict mode is the query parameter, and the debug toggle makes no request
   ========================================================================== */

test('严格模式 is read from the URL and sent as ?strict=1 — never persisted', async () => {
  const dom = install({ search: '?strict=1' });
  setResponder((url) => (url.includes('/api/session') ? json(sessionBody()) : json(listPayload([SIX_OF_SIX]))));
  const unmount = mountHome(dom.root);
  await settle();
  const listCall = fetches.find((entry) => entry.url.includes('/api/recipes'));
  assert.ok(listCall, 'no list request was made');
  assert.equal(listCall.url, '/api/recipes?strict=1');
  assert.equal(dom.localStorage.getItem('pantry-recipes:strict'), null);
  unmount();
  dom.restore();
});

test('the 调试 toggle repaints from the payload already in hand and issues NO request', async () => {
  const dom = install();
  setResponder((url) => (url.includes('/api/session') ? json(sessionBody()) : json(listPayload([FOUR_OF_SIX]))));
  const unmount = mountHome(dom.root);
  await settle();
  const before = fetches.length;
  assert.equal(dom.byData('provenance').length, 0, 'provenance lines before the toggle');

  const { setDebugEnabled } = await import('../../app/static/js/prefs.js');
  setDebugEnabled(true);
  await settle();

  assert.equal(fetches.length, before, 'toggling 调试 made a request (F7 says it is render-only)');
  const lines = dom.byData('provenance');
  assert.equal(lines.length, FOUR_OF_SIX.ingredients.length);
  assert.ok(lines[0].textContent.includes('tier'));
  assert.equal(dom.byData('headline')[0].textContent, '4/6 ingredients found — missing: 香菇, 娃娃菜');
  unmount();
  dom.restore();
});

/* ==========================================================================
   7. The recipe detail
   ========================================================================== */

function detailBody(overrides = {}) {
  return {
    recipe: {
      noteName: '盐焗鸡',
      notePath: 'Hobbies/做饭/Recipes/盐焗鸡.md',
      found: 2,
      total: 3,
      lastCooked: '2026-03-10',
      ingredients: FOUR_OF_SIX.ingredients,
      tools: ['烤箱', '炒锅'],
      steps: '\n- 腌制\n- 烤 40 分钟',
      history: {
        firstCooked: '2025-11-02',
        lastCooked: '2026-03-10',
        cookingCount: 4,
        cookingFrequency: 12,
        cookingYears: ['2025', '2026'],
        recentActivity: 3,
        favoriteSeason: '冬季',
        cookingPatterns: ['常做菜品'],
        autoUpdated: '2026-09-14 22:02',
      },
      ...overrides,
    },
  };
}

function respondRecipe(
  extra = () =>
    json({ date: '2026-09-28', relativePath: '日记/2026/2026-09-28.md', noteRevision: 'sha256:note', entries: [] }),
) {
  setResponder((url, init) => {
    if (url.includes('/api/session')) return json(sessionBody());
    if (url.includes('/api/recipes')) return json(detailBody());
    return extra(url, init);
  });
}

test('the detail renders headline, chips, tools, verbatim steps, and the history', async () => {
  const dom = install();
  respondRecipe();
  const unmount = mountRecipe(dom.root, { basename: '盐焗鸡' });
  await settle();

  assert.equal(dom.byData('headline')[0].textContent, '4/6 ingredients found — missing: 香菇, 娃娃菜');
  assert.equal(dom.byData('chips').length, 1);
  // Tools are displayed and never scored: they carry `chip--tool`, and no tool
  // name appears in the headline.
  const tools = dom.byData('tools')[0];
  assert.deepEqual(tools.querySelectorAll('.chip').map((node) => node.textContent), ['烤箱', '炒锅']);
  for (const tool of tools.querySelectorAll('.chip')) {
    assert.ok(tool.className.includes('chip--tool'));
    for (const bucket of CHIP_CLASSES) {
      assert.equal(tool.className.includes(bucket), false, `a tool wears ${bucket}`);
    }
  }
  // The note body VERBATIM, including the leading newline the reader emits.
  assert.equal(dom.byData('steps')[0].textContent, '\n- 腌制\n- 烤 40 分钟');
  const history = textOf(dom.byData('history')[0]);
  assert.ok(history.includes('做过次数'));
  assert.ok(history.includes('4'));
  assert.ok(history.includes('冬季'));
  assert.ok(history.includes('2026-09-14 22:02'));
  unmount();
  dom.restore();
});

/* --------------------------------------------------------------------------
   7b. Tracker staleness: the nine fields are recomputed ONLY when the note is
   opened in Obsidian, so after a cook logged here they are correct-looking and
   out of date at the same time. `pendingCookDates` is the server's comparison
   and the panel's job is to say so, name the dates, and name the remedy.
   -------------------------------------------------------------------------- */

/* `ageInDays` / `ageLabel` are pure and take `today` as an argument precisely so
 * these are testable without a clock. A view test that asserted "14 天前" would
 * pass today and rot the day after; asserting the bucket against a FIXED today
 * cannot rot at all. */
test('ageInDays reads the tracker stamp by hand and refuses what it cannot parse', () => {
  // The space-separated `YYYY-MM-DD HH:mm` is the whole reason: `Date.parse`
  // wants a `T`, and this is a string the tracker wrote.
  assert.equal(ageInDays('2026-09-14 22:02', '2026-09-28'), 14);
  assert.equal(ageInDays('2026-09-27', '2026-09-28'), 1);
  assert.equal(ageInDays('2026-09-28', '2026-09-28'), 0);
  // A month boundary is date-index arithmetic, so it cannot slip a day.
  assert.equal(ageInDays('2026-08-28', '2026-09-28'), 31);
  assert.equal(ageInDays('2025-02-28', '2025-03-01'), 1);
  // 2024 is a leap year, and a hardcoded 365 would be wrong here.
  assert.equal(ageInDays('2024-02-29', '2024-03-01'), 1);
  // Every "cannot read it" answer is `null`, never NaN and never a number. A
  // cosmetic field must not put `NaN 天前` on the screen.
  assert.equal(ageInDays('', '2026-09-28'), null);
  assert.equal(ageInDays('yesterday', '2026-09-28'), null);
  assert.equal(ageInDays('2026-09-14 22:02', 'not-a-date'), null);
  assert.equal(ageInDays(null, '2026-09-28'), null);
  // A stamp from the FUTURE is not a small age, it is an unreadable one: a
  // `-1 天前` on screen would be a clock-skew bug rendered as a fact.
  assert.equal(ageInDays('2026-10-01', '2026-09-28'), null);
});

test('ageLabel buckets coarsely, so the label stays true as it ages', () => {
  assert.equal(ageLabel(0), '今天');
  assert.equal(ageLabel(1), '昨天');
  assert.equal(ageLabel(14), '14 天前');
  assert.equal(ageLabel(29), '29 天前');
  // 30 days is a month, not "30 天前": the buckets change unit on purpose.
  assert.equal(ageLabel(30), '1 个月前');
  assert.equal(ageLabel(200), '6 个月前');
  assert.equal(ageLabel(364), '12 个月前');
  assert.equal(ageLabel(365), '1 年前');
  assert.equal(ageLabel(400), '1 年 1 个月前');
  assert.equal(ageLabel(730), '2 年前');
  // Unparseable in, nothing out. A blank suffix beats a wrong number.
  assert.equal(ageLabel(null), '');
});

test('a pending cook renders the warning, the dates, and the remedy', async () => {
  const dom = install();
  setResponder((url) => {
    if (url.includes('/api/session')) return json(sessionBody());
    if (url.includes('/api/cook-logs')) {
      return json({ date: '2026-09-28', relativePath: '日记/2026/2026-09-28.md', noteRevision: 'sha256:note', entries: [] });
    }
    return json(detailBody({ pendingCookDates: ['2026-09-25', '2026-10-02'] }));
  });
  const unmount = mountRecipe(dom.root, { basename: '盐焗鸡' });
  await settle();

  const stale = dom.byData('history-stale');
  assert.equal(stale.length, 1, 'the warning must render when something is pending');
  const text = textOf(stale[0]);
  // The COUNT is stated, because "some of it is behind" is not actionable…
  assert.ok(text.includes('2'), `the pending count must be visible: ${text}`);
  // …and the DATES are stated, because the count alone is not evidence.
  assert.ok(text.includes('2026-09-25'), text);
  assert.ok(text.includes('2026-10-02'), text);
  // The remedy is named, because a warning without one is just anxiety: the
  // number only moves when the note is opened in Obsidian.
  assert.ok(text.includes('Obsidian'), text);
  assert.ok(text.includes('打开'), text);

  // The history fields are still there underneath — the warning ADDS to the
  // panel, it does not replace it.
  assert.equal(dom.byData('history').length, 1);
  assert.ok(textOf(dom.byData('history')[0]).includes('做过次数'));

  unmount();
  dom.restore();
});

test('nothing pending renders no warning at all', async () => {
  const dom = install();
  respondRecipe();
  const unmount = mountRecipe(dom.root, { basename: '盐焗鸡' });
  await settle();

  assert.equal(dom.byData('history-stale').length, 0, 'no pending, no warning');
  assert.equal(dom.byData('history').length, 1, 'and the history is untouched');

  unmount();
  dom.restore();
});

test('a pending cook on a never-cooked recipe does not claim it was never cooked', async () => {
  const dom = install();
  setResponder((url) => {
    if (url.includes('/api/session')) return json(sessionBody());
    if (url.includes('/api/cook-logs')) {
      return json({ date: '2026-09-28', relativePath: '日记/2026/2026-09-28.md', noteRevision: 'sha256:note', entries: [] });
    }
    return json(
      detailBody({
        history: {
          firstCooked: null,
          lastCooked: null,
          cookingCount: null,
          cookingFrequency: null,
          cookingYears: [],
          recentActivity: null,
          favoriteSeason: null,
          cookingPatterns: [],
          autoUpdated: null,
        },
        pendingCookDates: ['2026-09-27'],
      }),
    );
  });
  const unmount = mountRecipe(dom.root, { basename: '盐焗鸡' });
  await settle();

  // "还没有做过这道菜" is now a FALSE claim — there is a receipt saying
  // otherwise. The empty state is the one thing in this panel that can be
  // contradicted outright, so it yields to the pending warning.
  assert.equal(dom.byData('empty-history').length, 0);
  assert.equal(dom.byData('history-stale').length, 1, 'the pending cook is still reported');
  assert.equal(dom.byData('history').length, 0, 'no field has a value to show');

  unmount();
  dom.restore();
});

test('an empty pattern list renders no row, rather than a label with no value', async () => {
  const dom = install();
  setResponder((url) => {
    if (url.includes('/api/session')) return json(sessionBody());
    if (url.includes('/api/cook-logs')) {
      return json({ date: '2026-09-28', relativePath: '日记/2026/2026-09-28.md', noteRevision: 'sha256:note', entries: [] });
    }
    return json(detailBody({ history: { ...detailBody().recipe.history, cookingPatterns: [] } }));
  });
  const unmount = mountRecipe(dom.root, { basename: '盐焗鸡' });
  await settle();

  const labels = [...dom.byData('history')[0].querySelectorAll('.field__label')].map(
    (node) => node.textContent,
  );
  // `[]` is a real payload — the key is there and nothing qualified — and a row
  // reading `做法模式` with an empty value looks like data loss.
  assert.ok(!labels.includes('做法模式'), `an empty list must render no row: ${labels}`);
  // The rows that DO have values are unaffected.
  assert.ok(labels.includes('第一次做'), labels.join('|'));

  unmount();
  dom.restore();
});

/* --------------------------------------------------------------------------
   7c. `来源` / `时长（分钟）` and the `频率` zero.
   -------------------------------------------------------------------------- */

test('配方信息 renders 来源 and 时长, and hides its heading when there is neither', async () => {
  const dom = install();
  const withMeta = async (overrides) => {
    setResponder((url) => {
      if (url.includes('/api/session')) return json(sessionBody());
      if (url.includes('/api/cook-logs')) {
        return json({ date: '2026-09-28', relativePath: '日记/2026/2026-09-28.md', noteRevision: 'sha256:note', entries: [] });
      }
      return json(detailBody(overrides));
    });
    const next = install();
    const stop = mountRecipe(next.root, { basename: '盐焗鸡' });
    await settle();
    return next;
  };

  // The two real shapes meet here: `来源` is a LIST in 13 of 16 vault notes and a
  // bare string in 2, and the server has already reconciled them.
  let next = await withMeta({ source: ['小红书', '下厨房'], durationMinutes: 40 });
  const meta = next.byData('recipe-meta');
  assert.equal(meta.length, 1, 'the block did not render');
  const text = textOf(meta[0]);
  assert.ok(text.includes('来源'), text);
  assert.ok(text.includes('小红书'), text);
  assert.ok(text.includes('下厨房'), text);
  assert.ok(text.includes('时长（分钟）'), text);
  assert.ok(text.includes('40'), text);
  next.restore();

  // Both blank — `烤土豆` and `花蛤拌饭` in the real vault — and a heading above
  // nothing is worse than no heading.
  next = await withMeta({ source: [], durationMinutes: null });
  assert.equal(next.byData('recipe-meta').length, 0, 'an empty block rendered');
  next.restore();

  // A duration of 0 is a real (if useless) value and must NOT read as absent.
  next = await withMeta({ source: [], durationMinutes: 0 });
  assert.equal(next.byData('recipe-meta').length, 1, 'a 0-minute duration was dropped as blank');
  assert.ok(textOf(next.byData('recipe-meta')[0]).includes('0'));
  next.restore();

  dom.restore();
});

test('频率 0 is not rendered: the tracker means "not computed" there', async () => {
  const dom = install();
  setResponder((url) => {
    if (url.includes('/api/session')) return json(sessionBody());
    if (url.includes('/api/cook-logs')) {
      return json({ date: '2026-09-28', relativePath: '日记/2026/2026-09-28.md', noteRevision: 'sha256:note', entries: [] });
    }
    // `recipeTracker` writes frequency 0 whenever cooking_count is 1, which is
    // 7 of the 16 real notes. `茶碗蒸` is one of them.
    return json(
      detailBody({
        history: { ...detailBody().recipe.history, cookingCount: 1, cookingFrequency: 0 },
      }),
    );
  });
  const unmount = mountRecipe(dom.root, { basename: '盐焗鸡' });
  await settle();

  const labels = [...dom.byData('history')[0].querySelectorAll('.field__label')].map(
    (node) => node.textContent,
  );
  assert.ok(!labels.includes('频率'), `a computed-nothing 0 rendered as a number: ${labels}`);
  // The count that produced the 0 is still shown, and it is the real fact.
  assert.ok(labels.includes('做过次数'), labels.join('|'));

  // And a real frequency is unaffected.
  unmount();
  dom.restore();

  const dom2 = install();
  respondRecipe();
  const unmount2 = mountRecipe(dom2.root, { basename: '盐焗鸡' });
  await settle();
  assert.ok(
    [...dom2.byData('history')[0].querySelectorAll('.field__label')]
      .map((node) => node.textContent)
      .includes('频率'),
    'a real frequency stopped rendering',
  );
  unmount2();
  dom2.restore();
});

test('a 404 renders the Back affordance and the server code, and does not invent steps', async () => {
  const dom = install();
  setResponder((url) => {
    if (url.includes('/api/session')) return json(sessionBody());
    return json({ requestId: 'req-404', code: 'recipe_not_found' }, 404);
  });
  const unmount = mountRecipe(dom.root, { basename: '不存在的菜' });
  await settle();
  const failure = dom.byData('recipe-not-found')[0];
  assert.ok(failure);
  assert.equal(failure.dataset.panelState, 'error');
  assert.ok(textOf(failure).includes('recipe_not_found'));
  assert.equal(dom.byData('steps').length, 0);
  assert.ok(button(dom.root, '返回'));
  unmount();
  dom.restore();
});

test('the day with no daily note is this recipe own error state, not a blank recipe', async () => {
  const dom = install();
  respondRecipe(() =>
    json({ requestId: 'req-404b', code: 'daily_note_missing', message: '这一天还没有日记。', date: '2026-09-28', relativePath: '日记/2026/2026-09-28.md', retryable: true }, 404),
  );
  const unmount = mountRecipe(dom.root, { basename: '盐焗鸡' });
  await settle();
  const tracker = dom.byData('tracker-unavailable')[0];
  assert.ok(tracker, 'a missing daily note blanked the tracker panel');
  assert.ok(tracker.textContent.includes('这一天还没有日记。'), 'the server message is not shown');
  // ... and the recipe itself is untouched.
  assert.equal(dom.byData('headline')[0].textContent, '4/6 ingredients found — missing: 香菇, 娃娃菜');
  assert.equal(dom.byData('steps')[0].textContent, '\n- 腌制\n- 烤 40 分钟');
  unmount();
  dom.restore();
});

test('the 待 Obsidian 同步 badge shows when the tracker has not caught up', async () => {
  const dom = install();
  setResponder((url) => {
    if (url.includes('/api/session')) return json(sessionBody());
    if (url.includes('/api/cook-logs')) {
      return json({ date: '2026-09-28', relativePath: '日记/2026/2026-09-28.md', noteRevision: 'sha256:note', entries: [] });
    }
    return json(detailBody({ pendingCookDates: ['2026-09-25'] }));
  });
  const unmount = mountRecipe(dom.root, { basename: '盐焗鸡' });
  await settle();
  const badge = dom.byData('tracker-badge')[0];
  assert.ok(badge, 'the badge did not render');
  assert.equal(badge.textContent, TRACKER_BADGE);
  assert.ok(textOf(dom.byData('tracker-note')[0]).includes('recipeTracker'));
  unmount();
  dom.restore();
});

test('the badge IGNORES trackerSynced: false, because that column is never flipped', async () => {
  /* The regression this pins, and it is why the field is off the wire entirely.
   * `cook_log_receipts.recipe_tracker_synced` is inserted as 0 and nothing in
   * the repo ever writes 1 — the read-path comparison spec §13 step 8 described
   * was never implemented. The badge used to read it, so it was on screen
   * forever for every recipe ever logged here. A receipt for TODAY with
   * `trackerSynced: false` and an empty `pendingCookDates` is exactly that state,
   * and the answer must be no badge. The key is still in this payload on purpose:
   * the API no longer publishes it, so this also pins that a stale client
   * response cannot resurrect the old behaviour. */
  const dom = install();
  respondRecipe(() =>
    json({
      date: '2026-09-28',
      relativePath: '日记/2026/2026-09-28.md',
      noteRevision: 'sha256:note',
      entries: [{ recipeNote: '盐焗鸡', writtenAt: '2026-09-28T02:00:00Z', trackerSynced: false }],
    }),
  );
  const unmount = mountRecipe(dom.root, { basename: '盐焗鸡' });
  await settle();

  assert.equal(dom.byData('tracker-badge').length, 0, 'the dead flag showed the badge');
  assert.equal(dom.byData('tracker-clear').length, 1, 'and the panel claims it is in sync');
  unmount();
  dom.restore();
});

test('the badge stays hidden when nothing is pending, whatever a receipt claims', async () => {
  /* Renamed from "when the tracker has synced". That framing was the old model:
   * the assertion used to be about `trackerSynced: true`, a field the badge no
   * longer reads and the API no longer publishes. The claim it made — that the
   * badge is not on — is unchanged; what it is no longer *about* is a boolean
   * that could only ever be false. `trackerSynced: true` is kept in the payload
   * on purpose: an unrecognised key must be inert, not merely absent. */
  const dom = install();
  respondRecipe(() =>
    json({
      date: '2026-09-28',
      relativePath: 'x',
      noteRevision: 'sha256:note',
      entries: [{ recipeNote: '盐焗鸡', writtenAt: 'x', trackerSynced: true }],
    }),
  );
  const unmount = mountRecipe(dom.root, { basename: '盐焗鸡' });
  await settle();
  assert.equal(dom.byData('tracker-badge').length, 0);
  assert.equal(dom.byData('tracker-clear').length, 1);
  unmount();
  dom.restore();
});

/* ==========================================================================
   8. The Cooking Log: offline, in-flight, 404, 409
   ========================================================================== */

async function mountLoggedRecipe(dom, options = {}) {
  respondRecipe(options.respond);
  await api.initApi();
  const unmount = mountRecipe(dom.root, { basename: '盐焗鸡' });
  await settle();
  const open = dom.byData('log-button')[0];
  open.dispatchEvent({ type: 'click' });
  return {
    unmount,
    open,
    picker: dom.byData('date-picker')[0],
    dateInput: dom.byData('log-date')[0],
  };
}

test('the date picker opens on today, and the input is the 16px one pwa.css owns', async () => {
  const dom = install();
  const { unmount, picker, dateInput } = await mountLoggedRecipe(dom);
  assert.equal(picker.getAttribute('hidden'), null, 'the picker did not open');
  assert.equal(dateInput.value, todayIn('America/New_York'), 'the picker did not default to today');
  assert.match(dateInput.value, /^\d{4}-\d{2}-\d{2}$/);
  // `.form-sheet` is the vendored height cap, `.date-picker` is what
  // main.js's isModalOpen() looks for alongside it.
  assert.ok(picker.className.includes('form-sheet'));
  assert.ok(picker.className.includes('date-picker'));
  unmount();
  dom.restore();
});

test('offline: the cook-log action is disabled, says exactly why, and queues nothing', async () => {
  const dom = install({ online: false });
  const { unmount, open } = await mountLoggedRecipe(dom);
  assert.equal(open.disabled, true, 'the log button is enabled while offline');
  assert.equal(dom.byData('log-reason')[0].textContent, OFFLINE_REASON);
  assert.equal(OFFLINE_REASON, '离线：需要连接后记录');

  // A tap on a disabled control changes nothing and enqueues nothing: F5 says
  // the cook log is online-only and §9.18.1 says it is not on the outbox.
  const before = fetches.length;
  open.dispatchEvent({ type: 'click' });
  await settle();
  assert.equal(fetches.length, before, 'an offline tap issued a request');
  // A disabled control opens nothing either: there is no half-open sheet waiting
  // on a write that cannot happen.
  assert.equal(dom.byData('date-picker')[0].getAttribute('hidden'), '');

  // Coming back online re-enables it: the offline disable is a STATE, undone
  // by the `online` event rather than by a watchdog.
  dom.setOnline(true);
  assert.equal(open.disabled, false);
  assert.equal(dom.byData('log-reason')[0].textContent, '');
  unmount();
  dom.restore();
});

test('a read-only install disables the write too, and says why', async () => {
  const dom = install();
  setResponder((url) => {
    if (url.includes('/api/session')) return json(sessionBody({ readOnly: true }));
    if (url.includes('/api/recipes')) return json(detailBody());
    return json({ date: 'x', noteRevision: 'sha256:note', entries: [] });
  });
  await api.initApi();
  const unmount = mountRecipe(dom.root, { basename: '盐焗鸡' });
  await settle();
  assert.ok(dom.byData('log-reason')[0].textContent.includes('只读'));
  unmount();
  dom.restore();
});

test('a successful write says so from the response, and re-reads the tracker', async () => {
  const dom = install();
  let logged = 0;
  const { unmount, picker, dateInput } = await mountLoggedRecipe(dom, {
    respond: (url) => {
      if (url.includes('/api/cook-logs?date=')) {
        return json({
          date: '2026-09-28',
          relativePath: '日记/2026/2026-09-28.md',
          noteRevision: 'sha256:note',
          entries: [{ recipeNote: '盐焗鸡', writtenAt: 'x', trackerSynced: false }],
        });
      }
      logged += 1;
      return json({ status: 'logged', relativePath: '日记/2026/2026-09-28.md', noteRevision: 'sha256:fresh' }, 201);
    },
  });
  const submit = button(picker, '记录');
  submit.dispatchEvent({ type: 'click' });
  await settle();

  const post = fetches.find((entry) => entry.init.method === 'POST');
  assert.equal(post.url, '/api/cook-logs');
  assert.deepEqual(JSON.parse(post.init.body), {
    recipeNote: '盐焗鸡',
    date: dateInput.value,
    // §9.3 / F4: the write is a compare-and-swap, and the revision it swaps
    // against is the one the mount-time read-back returned. Sending `null`
    // here would be an unconditional overwrite in everything but the name.
    baseRevision: 'sha256:note',
  });
  assert.equal(logged, 1);
  assert.ok(dom.byData('log-status')[0].textContent.includes('日记/2026/2026-09-28.md'));
  assert.equal(picker.getAttribute('hidden'), '', 'the picker stayed open');
  assert.ok(dom.byData('tracker-badge')[0], 'the badge did not appear after a write');
  unmount();
  dom.restore();
});

test('a 404 daily_note_missing renders the server message verbatim, with a retry', async () => {
  const dom = install();
  const message = '2026-09-28 的日记还不存在（应该在 日记/2026/2026-09-28.md）。在 Obsidian 里建好它，然后重试。';
  let posts = 0;
  const { unmount, picker } = await mountLoggedRecipe(dom, {
    respond: (url) => {
      if (url.includes('/api/cook-logs?date=')) return json({ date: 'x', noteRevision: 'sha256:note', entries: [] });
      posts += 1;
      return json(
        { requestId: 'req-missing', code: 'daily_note_missing', message, date: '2026-09-28', relativePath: '日记/2026/2026-09-28.md', retryable: true },
        404,
      );
    },
  });
  button(picker, '记录').dispatchEvent({ type: 'click' });
  await settle();

  const state = dom.byData('log-missing-note')[0];
  assert.ok(state, 'no missing-note state rendered');
  assert.equal(state.dataset.panelState, 'error');
  assert.ok(
    textOf(state).includes(message),
    'the server message was not rendered verbatim',
  );
  assert.ok(textOf(state).includes('日记/2026/2026-09-28.md'));
  assert.equal(dom.byData('log-status')[0].textContent, '', 'a failure claimed success');

  // F4: retrying the IDENTICAL request is the remedy, so it must be offered.
  const again = button(state, '再试一次');
  assert.ok(again, 'no retry affordance on a retryable 404');
  again.dispatchEvent({ type: 'click' });
  await settle();
  assert.equal(posts, 2, 'the retry did not re-issue the identical request');
  unmount();
  dom.restore();
});

test('the 再试一次 button on a missing note carries the MISSING date`s revision, not another`s', async () => {
  /* The dead end #25 found, and it was a symptom of the same scalar-revision bug
   * rather than a second defect. `daily_note_missing` publishes no
   * `currentRevision` — F4's envelope for that code is `{requestId, code,
   * message, date, relativePath, retryable}` and nothing else — so the button
   * used to fall back to "whatever revision the view last read". For a note that
   * was MISSING, that is some other date's revision, and the retry the panel
   * offers as a one-step detour became a `409 daily_note_changed` instead: the
   * user fixed the thing the message asked them to fix and still could not log
   * the cook.
   *
   * The fix is to ask the map about the date the SERVER named. For a note that
   * was missing the view has never read it, so that is `null` — the request F4
   * says is retryable, and which #25 verified the server honours (201 once the
   * note exists). It is not a weakened compare-and-swap: there is no base to
   * compare against, and the gate below shows a date the view DID read keeping
   * its own revision through the same button. */
  const dom = install();
  const bodies = [];
  let missing = true;
  const { unmount, open, picker, dateInput } = await mountLoggedRecipe(dom, {
    respond: (url, init) => {
      const asked = new URL(url, 'http://x').searchParams.get('date');
      if (url.includes('/api/cook-logs?date=')) {
        if (asked === '2026-03-12') {
          return json({ requestId: 'r', code: 'daily_note_missing', message: '这一天还没有日记。', date: '2026-03-12', relativePath: '日记/2026/2026-03-12.md', retryable: true }, 404);
        }
        return json({ date: asked, noteRevision: 'sha256:today', entries: [] });
      }
      bodies.push(JSON.parse(init.body));
      if (missing) {
        return json({ requestId: 'r', code: 'daily_note_missing', message: '这一天还没有日记。', date: '2026-03-12', relativePath: '日记/2026/2026-03-12.md', retryable: true }, 404);
      }
      return json({ status: 'logged', relativePath: '日记/2026/2026-03-12.md', noteRevision: 'sha256:twelve' }, 201);
    },
  });

  // First the date the view HAS read, so the map holds that date's revision.
  const today = todayIn('America/New_York');
  button(picker, '记录').dispatchEvent({ type: 'click' });
  await settle();
  assert.equal(bodies[0].baseRevision, 'sha256:today', bodies[0].baseRevision);

  // Now the missing date. The button is the only way out of the panel, so its
  // body is the whole claim.
  open.dispatchEvent({ type: 'click' });
  await settle();
  dateInput.value = '2026-03-12';
  button(picker, '记录').dispatchEvent({ type: 'click' });
  await settle();
  const state = dom.byData('log-missing-note')[0];
  assert.ok(state, 'the missing-note panel did not render');
  const again = button(state, '再试一次');
  again.dispatchEvent({ type: 'click' });
  await settle();
  assert.equal(bodies[2].baseRevision, null, `the retry replayed a foreign revision: ${JSON.stringify(bodies[2])} — this is the dead end`);
  assert.equal(bodies[2].date, '2026-03-12');

  // The user creates the note and taps 再试一次 again: one tap, and it lands.
  missing = false;
  again.dispatchEvent({ type: 'click' });
  await settle();
  assert.ok(
    textOf(dom.byData('log-status')[0]).includes('日记/2026/2026-03-12.md'),
    `the retry did not recover: ${textOf(dom.byData('log-status')[0])}`,
  );
  unmount();
  dom.restore();
});

test('a 409 shows both versions, the server message verbatim, and the retry carries the fresh revision', async () => {
  const dom = install();
  const message = '日记在这次读取之后被创建了，所以这次记录没有写入。';
  const bodies = [];
  const { unmount, picker } = await mountLoggedRecipe(dom, {
    respond: (url) => {
      if (url.includes('/api/cook-logs?date=')) return json({ date: 'x', noteRevision: 'sha256:note', entries: [] });
      bodies.push(JSON.parse(JSON.stringify(globalThis.__lastBody || null)) || null);
      return json(
        { requestId: 'req-409', code: 'daily_note_created_concurrently', message, date: '2026-09-28', relativePath: '日记/2026/2026-09-28.md', currentRevision: 'sha256:fresh' },
        409,
      );
    },
  });
  button(picker, '记录').dispatchEvent({ type: 'click' });
  await settle();

  const conflict = dom.byData('conflict')[0];
  assert.ok(conflict, 'no resolve panel rendered for a 409');
  assert.equal(conflict.dataset.panelState, 'conflict');
  const text = textOf(conflict);
  assert.ok(text.includes('盐焗鸡 @ '), 'the PWA intent is not shown');
  assert.ok(text.includes('sha256:fresh'), 'the server revision is not shown');
  assert.equal(
    textOf(dom.byData('server-message')[0]),
    message,
    'the 409 message must come from error.message, verbatim',
  );

  // The follow-up write carries the FRESH revision — §9.3 / F4.
  const again = button(conflict, '按服务器现状再记一次');
  assert.ok(again);
  again.dispatchEvent({ type: 'click' });
  await settle();
  const posts = fetches.filter((entry) => entry.init.method === 'POST');
  assert.equal(posts.length, 2);
  assert.equal(JSON.parse(posts[1].init.body).baseRevision, 'sha256:fresh');
  unmount();
  dom.restore();
});

test('a second cook on another date carries no revision, and the first date still does', async () => {
  /* The per-date revision map, and the reason it is a Map. A Cooking Log write
   * is a statement about a DATE, and a note revision is `sha256` of one date's
   * bytes, so "the revision this view knows" is one value per date rather than a
   * scalar. Held as a scalar it produced the bug the browser flow pinned: log
   * 2026-03-10, change the date, log again, and the second write carried
   * 2026-03-10's revision — a `409 daily_note_changed` naming a conflict that
   * never happened, with the second cook silently lost. */
  const dom = install();
  const bodies = [];
  /* The read-back tracks the write, the way the server's does: a badge read
   * before the cook sees the pre-write bytes and one after sees the new ones.
   * A responder that answered every read with the same string would make the
   * third assertion below fail for a fixture reason rather than an app one. */
  let todayRevision = 'sha256:today';
  const { unmount, open, picker, dateInput } = await mountLoggedRecipe(dom, {
    respond: (url, init) => {
      if (url.includes('/api/cook-logs?date=')) {
        return json({ date: 'x', noteRevision: todayRevision, entries: [] });
      }
      bodies.push(JSON.parse(init.body));
      todayRevision = 'sha256:written';
      return json(
        { status: 'logged', relativePath: '日记/2026/x.md', noteRevision: todayRevision },
        201,
      );
    },
  });
  const first = todayIn('America/New_York');
  assert.equal(dateInput.value, first);
  button(picker, '记录').dispatchEvent({ type: 'click' });
  await settle();

  // The first write is a real compare-and-swap: the badge read on mount saw this
  // date's note, so the base is this date's own revision — NOT `null`, which is
  // what §10.5 step 4's `409` needs and what a "just send null" fix would throw
  // away along with the bug.
  assert.deepEqual(bodies[0], {
    recipeNote: '盐焗鸡',
    date: first,
    baseRevision: 'sha256:today',
  });

  // A second cook, on a DIFFERENT date, through the same UI the user has: tap
  // 做过了 again, change the date, tap 记录.
  open.dispatchEvent({ type: 'click' });
  await settle();
  dateInput.value = '2026-03-12';
  button(picker, '记录').dispatchEvent({ type: 'click' });
  await settle();

  assert.equal(bodies.length, 2);
  assert.deepEqual(bodies[1], {
    recipeNote: '盐焗鸡',
    date: '2026-03-12',
    // Never read, so there is no base to compare against, and saying so with
    // `null` is the honest request. A revision belonging to another date would
    // be a compare against bytes this write is not replacing.
    baseRevision: null,
  });

  // And the first date has NOT been forgotten: going back to it still
  // compare-and-swaps against the revision that date's own write returned.
  open.dispatchEvent({ type: 'click' });
  await settle();
  dateInput.value = first;
  button(picker, '记录').dispatchEvent({ type: 'click' });
  await settle();
  assert.equal(bodies[2].baseRevision, 'sha256:written', bodies[2].baseRevision);
  assert.equal(bodies[2].date, first);
  unmount();
  dom.restore();
});

test('a 409 with no message in the envelope falls back to the code, not to invented copy', async () => {
  const dom = install();
  const { unmount, picker } = await mountLoggedRecipe(dom, {
    respond: (url) => {
      if (url.includes('/api/cook-logs?date=')) return json({ date: 'x', noteRevision: 'n', entries: [] });
      // `daily_note_changed` is a CONFLICT code that is NOT a PRESENCE code, so
      // the envelope carries `currentRevision` and no `message` at all.
      return json({ requestId: 'req-409b', code: 'daily_note_changed', currentRevision: 'sha256:other' }, 409);
    },
  });
  button(picker, '记录').dispatchEvent({ type: 'click' });
  await settle();
  const conflict = dom.byData('conflict')[0];
  assert.ok(conflict);
  // `ApiError`'s constructor already substitutes the CODE for a missing
  // `message`, so the node renders `daily_note_changed` — the server's own
  // token, not a sentence this app composed. The point of the assertion is that
  // nothing is INVENTED: the same `error.message` the view renders is exactly
  // what the envelope produced.
  assert.deepEqual(dom.byData('server-message').map((node) => node.textContent), [
    'daily_note_changed',
  ]);
  assert.equal(textOf(conflict).includes('重试'), false, 'a retry sentence was invented');
  assert.ok(textOf(conflict).includes('sha256:other'));
  unmount();
  dom.restore();
});

/* ==========================================================================
   9. The F18 busy-guard and the unstick watchdog
   ========================================================================== */

test('mutationInFlight() is true for the whole cook-log write, so canApplyUpdate() is false', async () => {
  const dom = install();
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const { unmount, picker } = await mountLoggedRecipe(dom, {
    respond: async (url) => {
      if (url.includes('/api/cook-logs?date=')) return json({ date: 'x', noteRevision: 'n', entries: [] });
      await gate;
      return json({ status: 'logged', relativePath: 'r', noteRevision: 'sha256:fresh' }, 201);
    },
  });

  const submit = button(picker, '记录');
  assert.equal(api.mutationInFlight(), false, 'nothing is in flight before the tap');
  submit.dispatchEvent({ type: 'click' });
  await settle(2);

  assert.equal(api.mutationInFlight(), true, 'the POST is not counted as an in-flight mutation');
  // main.js owns the real `canApplyUpdate`; this harness composes the SAME two
  // terms F18 §4e names, from the SAME two predicates, so the behaviour is
  // observable here instead of only asserted about in source.
  const isModalOpen = () => Boolean(document.querySelector('dialog[open], .form-sheet:not([hidden]), .date-picker:not([hidden])'));
  const canApplyUpdate = () => !api.mutationInFlight() && !isModalOpen();
  assert.equal(canApplyUpdate(), false, 'an update could land while the cook log is in flight');
  // The open picker is the OTHER independent reason, which is the half of §4e
  // that is about a half-typed date rather than a write.
  release();
  await settle();
  assert.equal(api.mutationInFlight(), false, 'the mutation was never released');
  unmount();
  dom.restore();
});

test('an open date picker alone blocks the update, so a half-typed date survives', async () => {
  const dom = install();
  const { unmount, open } = await mountLoggedRecipe(dom);
  const isModalOpen = () => Boolean(document.querySelector('dialog[open], .form-sheet:not([hidden]), .date-picker:not([hidden])'));
  assert.equal(!api.mutationInFlight() && !isModalOpen(), false);
  // Closing it re-opens the gate: the picker is a MODAL, not a write.
  dom.byData('date-picker')[0].setAttribute('hidden', '');
  assert.equal(!api.mutationInFlight() && !isModalOpen(), true);
  open.dispatchEvent({ type: 'click' });
  unmount();
  dom.restore();
});

test('a hung cook-log request re-enables the control instead of locking the app up', async () => {
  const dom = install();
  const { unmount, picker } = await mountLoggedRecipe(dom, {
    // A request that never settles. The watchdog, not the request, is what
    // gets the control back — that is §8 story 54.
    respond: () => new Promise(() => {}),
  });
  const submit = button(picker, '记录');
  const logButton = dom.byData('log-button')[0];
  assert.equal(logButton.disabled, false);
  submit.dispatchEvent({ type: 'click' });
  await settle(2);
  assert.equal(logButton.disabled, true, 'the control was not disabled in flight');

  dom.runTimers();
  assert.equal(logButton.disabled, false, 'the watchdog never re-enabled the control');
  assert.ok(dom.byData('log-failed')[0], 'a stalled request reported nothing');
  unmount();
  dom.restore();
});

/* ==========================================================================
   10. F19: pull-to-refresh, on the list only
   ========================================================================== */

test('the home view pulls to refresh, and refuses to start offline or with a pending edit', async () => {
  const dom = install();
  setResponder((url) => (url.includes('/api/session') ? json(sessionBody()) : json(listPayload([SIX_OF_SIX]))));
  const unmount = mountHome(dom.root);
  await settle();

  const indicator = dom.byData('pull-indicator')[0];
  assert.ok(indicator, 'the list has no pull-to-refresh indicator');
  // F19 / §9.4: the inline SVG keeps its intrinsic size so a mixed-cache
  // release cannot expand the glyph to the viewport.
  const svg = walk(indicator).find((node) => node.tagName === 'SVG');
  assert.ok(svg, 'no inline SVG');
  assert.equal(svg.getAttribute('width'), '22');
  assert.equal(svg.getAttribute('height'), '22');

  const touch = (type, y) =>
    document.dispatchEvent({ type, touches: type === 'touchend' ? [] : [{ clientY: y }] });

  const listCalls = () => fetches.filter((entry) => entry.url.includes('/api/recipes')).length;
  const before = listCalls();
  touch('touchstart', 10);
  touch('touchmove', 140); // past the 72px threshold
  touch('touchend');
  await settle();
  assert.equal(listCalls(), before + 1, 'a full pull did not refresh the list');

  // Offline: the gesture does not start. Nothing is queued, nothing is claimed.
  dom.setOnline(false);
  await settle();
  const offlineBefore = listCalls();
  touch('touchstart', 10);
  touch('touchmove', 140);
  touch('touchend');
  await settle();
  assert.equal(listCalls(), offlineBefore, 'a pull refreshed while offline');

  // A pending edit blocks it too, which is the case F19 names.
  dom.setOnline(true);
  await settle();
  markPendingEdits(1);
  assert.equal(hasPendingEdit(), true);
  const pendingBefore = listCalls();
  touch('touchstart', 10);
  touch('touchmove', 140);
  touch('touchend');
  await settle();
  assert.equal(listCalls(), pendingBefore, 'a pull discarded a pending edit');
  markPendingEdits(-1);
  assert.equal(pendingEdits(), 0);

  unmount();
  // unmount must detach the gesture listeners too, or a torn-down view keeps
  // pulling.
  const afterUnmount = listCalls();
  touch('touchstart', 10);
  touch('touchmove', 140);
  touch('touchend');
  await settle();
  assert.equal(listCalls(), afterUnmount, 'the gesture outlived unmount()');
  dom.restore();
});

test('pull-to-refresh is enabled on the LIST only, never on the detail', async () => {
  const dom = install();
  respondRecipe();
  const unmount = mountRecipe(dom.root, { basename: '盐焗鸡' });
  await settle();
  assert.equal(dom.byData('pull-indicator').length, 0, 'the detail view got a pull indicator');

  const listCalls = () => fetches.filter((entry) => entry.url.includes('/api/recipes')).length;
  const before = listCalls();
  for (const [type, y] of [['touchstart', 10], ['touchmove', 140], ['touchend']]) {
    document.dispatchEvent({ type, touches: type === 'touchend' ? [] : [{ clientY: y }] });
  }
  await settle();
  assert.equal(listCalls(), before, 'the detail view refreshed on a pull');
  unmount();
  dom.restore();
});

test('a restored scroll offset is re-applied after the first paint, not before it', async () => {
  // The router restores with `scrollTo` immediately after `mount()` returns, and
  // at that moment the list is the one-line loading state — a page about 16px
  // tall, so `scrollTo(0, 640)` CLAMPS. Measured in Chromium before the fix:
  // `640 -> 16`. The view therefore re-applies the router's own target once its
  // rows exist, reading the SAME history entry the router read.
  //
  // Driven through the REAL router rather than by calling `mount` directly, so
  // the early restore the browser performs is in the sequence and the clamp is
  // modelled rather than assumed away.
  const dom = install();
  setResponder((url) => (url.includes('/api/session') ? json(sessionBody()) : json(listPayload([SIX_OF_SIX]))));
  dom.history.replaceState({ scrollTop: 640, filters: { strict: false, search: null } });

  const positions = [];
  const asked = [];
  // Model the real clamp: the browser can only scroll as far as the CONTENT is
  // tall, and the list is one loading line until the rows are painted. That is
  // the whole mechanism of the bug — the restore target was fine, the page was
  // 16px of scrollable content at the moment it was applied.
  const scrollable = () => (dom.byData('recipe').length > 0 ? 4000 : 16);
  dom.window.scrollTo = (x, y) => {
    asked.push(y);
    dom.window.scrollY = Math.min(y, scrollable());
    positions.push(dom.window.scrollY);
  };
  dom.window.scrollY = 0;

  const router = initRouter({ window: dom.window, views: { home: { mount: mountHome } } });
  router.reload();
  await settle();

  assert.deepEqual(asked, [640, 640], 'the restore was not attempted twice');
  assert.equal(positions[0], 16, 'the fake never clamped, so this proves nothing');
  assert.equal(positions[positions.length - 1], 640, 'the restore target was not re-applied');
  assert.equal(dom.window.scrollY, 640);
  router.stop();
  dom.restore();
});

test('a fresh forward navigation into the list does NOT inherit an old offset', async () => {
  const dom = install();
  setResponder((url) => (url.includes('/api/session') ? json(sessionBody()) : json(listPayload([SIX_OF_SIX]))));
  // A freshly pushed entry has NO state, which is how a forward navigation is
  // distinguishable from a Back traversal.
  assert.equal(dom.history.state, null);
  const scrolled = [];
  dom.window.scrollTo = (x, y) => scrolled.push(y);
  const router = initRouter({ window: dom.window, views: { home: { mount: mountHome } } });
  router.reload();
  await settle();
  assert.ok(
    scrolled.every((y) => y === 0),
    `a fresh navigation tried to restore an offset: ${scrolled}`,
  );
  router.stop();
  dom.restore();
});

test('the stock revision is surfaced, and a long-open tab re-reads at the 30s TTL', async () => {
  const dom = install();
  setResponder((url) => {
    if (url.includes('/api/session')) return json(sessionBody());
    return json(listPayload([SIX_OF_SIX], { stockRevision: 'sha256:fcdc163b762fcbbc' }));
  });
  const unmount = mountHome(dom.root);
  await settle();
  // F11: the user toggles tasks in Obsidian while this tab is open, so the
  // screen states its own age rather than implying it is current.
  assert.ok(dom.byData('meta')[0].textContent.includes('fcdc163b762f'));
  assert.ok(dom.byData('meta')[0].textContent.includes('30 秒'));

  const first = fetches.filter((entry) => entry.url.includes('/api/recipes')).length;
  setResponder((url) => {
    if (url.includes('/api/session')) return json(sessionBody());
    return json(listPayload([SIX_OF_SIX], { stockRevision: 'sha256:aaaaaaaabbbb' }));
  });
  dom.runTimers();
  await settle();
  const second = fetches.filter((entry) => entry.url.includes('/api/recipes')).length;
  assert.equal(second, first + 1, 'the TTL poll never fired');
  // The revisions differ, so the render DID change: a long-open tab notices.
  assert.ok(dom.byData('meta')[0].textContent.includes('aaaaaaaabbbb'));
  unmount();
  dom.restore();
});

test('unmount clears the TTL poll, so a torn-down view stops asking the server', async () => {
  const dom = install();
  setResponder((url) => (url.includes('/api/session') ? json(sessionBody()) : json(listPayload([]))));
  const unmount = mountHome(dom.root);
  await settle();
  unmount();
  const after = fetches.length;
  dom.runTimers();
  await settle();
  assert.equal(fetches.length, after, 'the poll outlived unmount()');
  dom.restore();
});

/* ==========================================================================
   11. loadPanel's three states, and the teardown contract
   ========================================================================== */

test('loadPanel is loading -> ready | error, and abort() silences a late settle', async () => {
  const seen = [];
  const ok = loadPanel({
    load: () => Promise.resolve('value'),
    onLoading: () => seen.push('loading'),
    onReady: (value) => seen.push(`ready:${value}`),
    onError: () => seen.push('error'),
  });
  assert.equal(ok.status, 'loading');
  await settle();
  assert.deepEqual(seen, ['loading', 'ready:value']);
  assert.equal(ok.status, 'ready');

  const failed = [];
  loadPanel({
    load: () => Promise.reject(new Error('nope')),
    onLoading: () => failed.push('loading'),
    onReady: () => failed.push('ready'),
    onError: (error) => failed.push(`error:${error.message}`),
  });
  await settle();
  assert.deepEqual(failed, ['loading', 'error:nope']);

  const late = [];
  const panel = loadPanel({
    load: () => Promise.resolve('late'),
    onLoading: () => late.push('loading'),
    onReady: () => late.push('ready'),
    onError: () => late.push('error'),
  });
  panel.abort();
  await settle();
  assert.deepEqual(late, ['loading'], 'an aborted panel still painted');
});

test('a panel that settles after unmount writes nothing into the next view root', async () => {
  const dom = install();
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  setResponder((url) => {
    if (url.includes('/api/session')) return json(sessionBody());
    return gate.then(() => json(listPayload([SIX_OF_SIX])));
  });
  const unmount = mountHome(dom.root);
  await settle(2);
  unmount();
  assert.equal(dom.root.textContent, '', 'unmount left the tree behind');
  release();
  await settle();
  assert.equal(dom.root.textContent, '', 'a settled-after-unmount panel repainted the root');
  dom.restore();
});

test('the detail view registers every listener it adds and drops them all', async () => {
  const dom = install();
  respondRecipe();
  /* The baseline is captured AFTER `install()`, because the app outbox keeps its
   * own `online` listener for the life of the page — that is the vendored
   * adapter doing its job, not a leak, and asserting an absolute zero would be
   * asserting that the outbox does not exist. What is under test is the DELTA
   * the view adds and the view removes. */
  const baseline = (type) => (dom.window.listeners.get(type) || new Set()).size;
  const before = { online: baseline('online'), offline: baseline('offline') };
  assert.ok(before.online > 0, 'the harness registered no outbox listener to begin with');

  const unmount = mountRecipe(dom.root, { basename: '盐焗鸡' });
  await settle();
  const live = dom.window.listeners.get('online');
  assert.ok(live && live.size > before.online, 'the view never listened for connectivity');
  unmount();
  assert.equal(baseline('online'), before.online, 'a listener outlived unmount()');
  assert.equal(baseline('offline'), before.offline);
  assert.equal(dom.root.textContent, '');
  dom.restore();
});

/* ==========================================================================
   12. No `cookable`, anywhere
   ========================================================================== */

test('the rendered payload and the DOM never contain a cookable boolean', async () => {
  const dom = install();
  const seen = [];
  setResponder((url) => {
    seen.push(url);
    if (url.includes('/api/session')) return json(sessionBody());
    return json(listPayload([FOUR_OF_SIX]));
  });
  const unmount = mountHome(dom.root);
  await settle();
  const rendered = textOf(dom.root);
  assert.equal(/cookable/.test(rendered), false);
  for (const url of seen) {
    assert.equal(/cookable/.test(url), false);
  }
  // And nothing in the row's dataset is a single boolean verdict either.
  for (const row of dom.byData('recipe')) {
    for (const [key, value] of Object.entries(row.dataset)) {
      assert.notEqual(value, 'true', `recipe row publishes a bare boolean: ${key}`);
      assert.notEqual(value, 'false', `recipe row publishes a bare boolean: ${key}`);
    }
  }
  unmount();
  dom.restore();
});

test('el() is textContent-only: a hostile recipe name cannot become markup', async () => {
  const dom = install();
  const hostile = recipe('<img src=x onerror=alert(1)>', [slot(0, { parsedName: '香菇', matchMethod: 'synonym', pantryItemId: 1, inStock: true })], {
    found: 1,
    total: 1,
  });
  setResponder((url) => (url.includes('/api/session') ? json(sessionBody()) : json(listPayload([hostile]))));
  const unmount = mountHome(dom.root);
  await settle();
  const link = dom.byData('recipe-link')[0];
  assert.equal(link.querySelectorAll('img').length, 0, 'a recipe name became an element');
  const nameNode = walk(link).find((node) => node.className.includes('recipe-row__name'));
  assert.equal(nameNode.textContent, '<img src=x onerror=alert(1)>');
  unmount();
  dom.restore();
});

/* ==========================================================================
   13. The shortlist controls
   ========================================================================== */

test('the three meal controls are one tap, no dialog, and a failure claims nothing', async () => {
  const dom = install();
  let added = null;
  respondRecipe((url, init) => {
    if (url.includes('/api/cook-logs?date=')) return json({ date: 'x', noteRevision: 'n', entries: [] });
    if (url.includes('/api/shortlists/')) {
      added = JSON.parse(init.body);
      return json({ slot: 'breakfast', items: ['盐焗鸡'] });
    }
    return json({});
  });
  const unmount = mountRecipe(dom.root, { basename: '盐焗鸡' });
  await settle();

  const buttons = dom.byData('meal-add');
  assert.deepEqual(buttons.map((node) => node.dataset.slot), ['breakfast', 'lunch', 'dinner']);
  assert.deepEqual(buttons.map((node) => node.textContent), ['+ 早餐', '+ 午餐', '+ 晚餐']);
  // One tap, and no dialog: the picker for the cook log is the only sheet, and
  // it is still closed.
  buttons[0].dispatchEvent({ type: 'click' });
  await settle();
  assert.deepEqual(added, { recipeNote: '盐焗鸡' });
  assert.equal(dom.byData('date-picker')[0].getAttribute('hidden'), '');
  assert.equal(buttons[0].textContent, '已加入早餐');
  assert.equal(pendingEdits(), 0, 'the pending-edit counter leaked');

  // A failure is shown as a failure and must not read as a success. The
  // `?slot` route's own refusals (422 bad slot, unknown recipe) are the real
  // cases this branch exists for.
  setResponder((url, init) => {
    if (url.includes('/api/session')) return json(sessionBody());
    if (url.includes('/api/recipes')) return json(detailBody());
    if (url.includes('/api/cook-logs?date=')) return json({ date: 'x', noteRevision: 'n', entries: [] });
    void init;
    return json({ requestId: 'r', code: 'not_found' }, 404);
  });
  buttons[1].dispatchEvent({ type: 'click' });
  await settle();
  assert.equal(buttons[1].textContent, '+ 午餐', 'a failed add left a success label behind');
  assert.ok(dom.byData('log-status')[0].textContent.includes('午餐清单'));
  assert.equal(dom.byData('log-status')[0].textContent.includes('已加入'), false);
  unmount();
  dom.restore();
});

/* ==========================================================================
   14. The shared state helpers
   ========================================================================== */

test('errorState quotes the server code and requestId, and composes no replacement sentence', () => {
  const dom = install();
  const state = errorState({ title: '读不到。', detail: '', code: 'recipe_not_found', requestId: 'req-7' });
  dom.root.appendChild(state);
  const trail = dom.byData('error-trail')[0];
  assert.equal(trail.textContent, 'recipe_not_found · requestId req-7');
  assert.equal(state.dataset.panelState, 'error');
  assert.equal(errorState({ title: 'x', code: 'only_code' }).dataset.panelState, 'error');
  dom.restore();
});

test('el() and the helpers never touch innerHTML', () => {
  const dom = install();
  const node = el('p', { text: '<b>x</b>' });
  assert.equal(node.childNodes.length, 1);
  assert.equal(node.childNodes[0].nodeType, 3, 'text did not go in as a text node');
  assert.equal(node.querySelectorAll('b').length, 0);
  dom.restore();
});

/* ==========================================================================
   9. Retracting a Cooking Record
   ========================================================================== */

const RETRACTABLE = {
  date: '2026-09-28',
  writtenAt: '2026-09-28T17:41:47.082Z',
  retractableUntil: '2026-10-01T17:41:47.082Z',
};

function respondRetract(detail, retract = () => json({ status: 'retracted', relativePath: '日记/2026/2026-09-28.md', noteRevision: 'sha256:r', retractedAt: '2026-09-29T01:00:00.000Z' })) {
  setResponder((url, init) => {
    if (url.includes('/api/session')) return json(sessionBody());
    if (init.method === 'DELETE') return retract(url, init);
    if (url.includes('/api/cook-logs')) {
      return json({ date: '2026-09-28', relativePath: '日记/2026/2026-09-28.md', noteRevision: 'sha256:note', entries: [] });
    }
    return json(detailBody(detail));
  });
}

async function mountRetractable(dom, detail = {}, retract) {
  respondRetract({ retractableCooks: [RETRACTABLE], pendingCookDates: ['2026-09-28'], ...detail }, retract);
  await api.initApi();
  const unmount = mountRecipe(dom.root, { basename: '盐焗鸡' });
  await settle();
  return unmount;
}

const retractButton = (dom) => dom.byData('retract-button')[0];
const deletes = () => fetches.filter((entry) => entry.init.method === 'DELETE');

test('a retractable cook renders its date and a 撤销 control; none renders nothing', async () => {
  const dom = install();
  const unmount = await mountRetractable(dom);
  assert.equal(dom.byData('retract-row').length, 1);
  const row = textOf(dom.byData('retract-row')[0]);
  assert.ok(row.includes('2026-09-28'), row);
  assert.equal(retractButton(dom).textContent, '撤销');
  unmount();
  dom.restore();

  const empty = install();
  const unmountEmpty = await mountRetractable(empty, { retractableCooks: [] });
  assert.equal(empty.byData('retract-row').length, 0, 'no retractable cook, no control');
  unmountEmpty();
  empty.restore();
});

test('the first tap only asks; the second tap sends the DELETE', async () => {
  const dom = install();
  const unmount = await mountRetractable(dom);
  fetches.length = 0;

  retractButton(dom).dispatchEvent({ type: 'click' });
  await settle();
  assert.equal(deletes().length, 0, 'the first tap wrote to the vault');
  const confirmButton = dom.byData('retract-confirm')[0];
  assert.ok(confirmButton, 'no confirmation step');

  confirmButton.dispatchEvent({ type: 'click' });
  await settle();
  assert.equal(deletes().length, 1);
  assert.ok(
    deletes()[0].url.endsWith(`/api/cook-logs/2026-09-28/${encodeURIComponent('盐焗鸡')}`),
    deletes()[0].url,
  );
  unmount();
  dom.restore();
});

test('cancelling the confirmation sends nothing and restores the control', async () => {
  const dom = install();
  const unmount = await mountRetractable(dom);
  fetches.length = 0;

  retractButton(dom).dispatchEvent({ type: 'click' });
  dom.byData('retract-cancel')[0].dispatchEvent({ type: 'click' });
  await settle();

  assert.equal(deletes().length, 0);
  assert.ok(retractButton(dom), 'the 撤销 control did not come back');
  unmount();
  dom.restore();
});

test('a retraction removes the row, says so, and stops reporting that cook as pending', async () => {
  const dom = install();
  const unmount = await mountRetractable(dom);
  assert.equal(dom.byData('history-stale').length, 1, 'precondition: the cook is pending');

  retractButton(dom).dispatchEvent({ type: 'click' });
  dom.byData('retract-confirm')[0].dispatchEvent({ type: 'click' });
  await settle();

  assert.equal(dom.byData('retract-row').length, 0);
  assert.ok(textOf(dom.byData('retract-status')[0]).includes('已撤销'));
  assert.equal(dom.byData('history-stale').length, 0, 'a retracted cook is not pending');
  unmount();
  dom.restore();
});

test('retracting the counted date shows the retraction as waiting on Obsidian', async () => {
  const dom = install();
  // `detailBody`'s history has lastCooked 2026-03-10: retract THAT date.
  const unmount = await mountRetractable(dom, {
    retractableCooks: [{ ...RETRACTABLE, date: '2026-03-10' }],
    pendingCookDates: [],
  });

  retractButton(dom).dispatchEvent({ type: 'click' });
  dom.byData('retract-confirm')[0].dispatchEvent({ type: 'click' });
  await settle();

  const stale = dom.byData('history-retracted');
  assert.equal(stale.length, 1, 'the count still includes the retracted date, and nothing said so');
  const text = textOf(stale[0]);
  assert.ok(text.includes('2026-03-10'), text);
  assert.ok(text.includes('Obsidian'), text);
  assert.equal(dom.byData('tracker-badge').length, 1, 'the badge must show for a pending retraction');
  unmount();
  dom.restore();
});

test('the server-reported pending retraction renders the panel and the badge on load', async () => {
  const dom = install();
  const unmount = await mountRetractable(dom, {
    retractableCooks: [],
    pendingCookDates: [],
    pendingRetractionDates: ['2026-03-10'],
  });
  assert.equal(dom.byData('history-retracted').length, 1);
  assert.equal(dom.byData('tracker-badge').length, 1);
  unmount();
  dom.restore();
});

for (const [code, status, needle] of [
  ['cook_record_not_removable', 409, '不会替你删'],
  ['retraction_window_closed', 409, '超过'],
  ['cook_record_not_found', 404, '没有记过'],
]) {
  test(`a ${code} refusal is explained in words and leaves the row in place`, async () => {
    const dom = install();
    const unmount = await mountRetractable(dom, {}, () => json({ requestId: 'r', code }, status));

    retractButton(dom).dispatchEvent({ type: 'click' });
    dom.byData('retract-confirm')[0].dispatchEvent({ type: 'click' });
    await settle();

    assert.ok(textOf(dom.byData('retract-status')[0]).includes(needle), code);
    assert.equal(dom.byData('retract-row').length, 1, 'a refused retraction removed the row');
    unmount();
    dom.restore();
  });
}

test('offline: the 撤销 control is disabled and sends nothing', async () => {
  const dom = install({ online: false });
  const unmount = await mountRetractable(dom);
  assert.equal(retractButton(dom).disabled, true);
  fetches.length = 0;
  retractButton(dom).dispatchEvent({ type: 'click' });
  await settle();
  assert.equal(fetches.length, 0);
  unmount();
  dom.restore();
});

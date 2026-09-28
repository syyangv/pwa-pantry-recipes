/* #/pantry — the 食材 tab, rendered. node --test, no dependencies.
 *
 * These are the assertions a screenshot cannot make. They drive the REAL view
 * module through the REAL `dom.js` `el()` against the fake DOM in
 * `fake-dom.mjs`, with `api.js`'s transport stubbed — so a failure means the view
 * is wrong, not that a hand-written stub drifted.
 *
 * The grouping itself is asserted in `tests/js/logic/ingredient-index.test.mjs`,
 * against the app's own frozen `Pantry.md` rows. This file is the other half: the
 * rows the index produces are on screen, the states are worded as states, the
 * links go where a router will recognise them, and the failure paths fail
 * closed.
 *
 * What is asserted, and why each one is load-bearing:
 *
 *   1. **One request, and it is the recipe list.** This tab reads the same
 *      `GET /api/recipes` the 菜谱 tab reads. The fetch ledger is asserted
 *      exactly, because the failure this design exists to prevent is a second
 *      route re-reading `Pantry.md` through a second TTL window and disagreeing
 *      with the chip colours on the other tab — and a second request is the
 *      only way that can happen.
 *   2. **A row carries the recipe links, and no recipe SCORE.** `found/total`
 *      beside an ingredient would read as "this jar is 3/6 of your dinner".
 *   3. **The in-the-house-but-in-no-recipe row is a row**, with a sentence
 *      instead of links. That case is the half of the inventory a recipe-derived
 *      view cannot produce, and it is the reason this tab is not the recipe list
 *      transposed.
 *   4. **A Stock Join miss is a POINTER and never a row.** A miss turned into a
 *      Pantry Item would be a plausible-looking row on a screen whose whole job
 *      is to be believed.
 *   5. **The 503 reuses #21's panel and paints no inventory at all.** A 503 body
 *      is `{requestId, code}`; a view that assumed an inventory would paint an
 *      empty screen, and an empty ingredient list reads as "your pantry is
 *      empty" — the single worst sentence this app can produce.
 *   6. **The empty state is an invitation and is reachable only from a
 *      successful response** — never during loading, never after a failure.
 *   7. **No pull-to-refresh.** F19 scopes `initPullToRefresh` to `views/home.js`
 *      and excludes the other views by name; a second pull target is a second
 *      thing to get wrong.
 *   8. **unmount() really unmounts**: the poll timer and the `online` listener
 *      are both gone, so a tab the user left cannot re-read behind them.
 *
 * **NO TEST HERE READS A LIVE PATH.** Every input is a literal in this file, the
 * DOM is fake, and nothing starts the app or touches the vault — which is why a
 * run on another machine produces the same result.
 */

import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { register } from 'node:module';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { installFakeDom, textOf, walk } from './fake-dom.mjs';

register('./app-version-hook.mjs', import.meta.url);

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const VIEW_SOURCE = readFileSync(join(REPO_ROOT, 'app', 'static', 'js', 'views', 'pantry.js'), 'utf8');
const stripJsComments = (js) => js.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

const api = await import('../../app/static/js/api.js');
const { initRouter } = await import('../../app/static/js/router.js');
const { mount: mountPantry, STATE_LABELS, NAME_SOURCE_LABELS, UNRESOLVED_POINTER } = await import(
  '../../app/static/js/views/pantry.js'
);

/* --- fixtures ------------------------------------------------------------- */

const sessionBody = {
  identity: 'you@example.com',
  csrfToken: 'token-a',
  version: 'v0.7.2',
  readOnly: false,
  appTimezone: 'America/New_York',
};

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
    found: 1,
    total: 2,
    lastCooked: null,
    ingredients,
    tools: ['炒锅'],
    ...overrides,
  };
}

/* The real `stockJoin` rows, copied out of `tests/js/provenance.test.mjs`'s
 * FROZEN_JOIN, which copied them out of `tests/pantry/test_stock_join.py` and
 * the frozen `Pantry.md`: the real basename hit, the real duplicate-name
 * collision, and a real miss. */
function line(overrides) {
  return {
    lineIndex: 360,
    section: '1',
    text: '空心菜嫩苗 0.95-1.05 磅',
    core: '空心菜嫩苗 0.95-1.05 磅',
    stockJoinState: 'joined',
    tier: 2,
    pantryItemIds: [83],
    overrideKey: '空心菜嫩苗 0.95-1.05 磅',
    overrideName: null,
    repairHint: null,
    ...overrides,
  };
}

const LINES = [
  line({}),
  line({
    lineIndex: 372,
    section: '1',
    text: '365 By Whole Foods Market, Organic 1% Milk, 32 Fl Oz',
    core: 'Organic 1% Milk',
    pantryItemIds: [90, 116],
    overrideKey: 'organic 1% milk',
  }),
  line({
    lineIndex: 511,
    section: '5',
    text: 'Manukora Manuka Honey MGO 50+',
    core: 'Manukora Manuka Honey MGO 50+',
    stockJoinState: 'unresolved',
    tier: 0,
    pantryItemIds: [],
    overrideKey: 'manukora manuka honey mgo 50+',
    repairHint: '在 app/pantry/line_overrides.yaml 的 overrides 里加一行：键写 manukora manuka honey mgo 50+。',
  }),
];

function listPayload(recipes, overrides = {}) {
  return {
    recipes,
    catalogRevision: 'sha256:1da7ba7e0a28a2f207519e37e218f4c1477e8199669e9ce69f1d5ec88bd8b6a9',
    stockRevision: 'sha256:fcdc163b762fcbbc71114b9e73803e976cbdbf085daecd6896585e921c6016db',
    strict: 0,
    staleMappingCount: 0,
    stockUnjoinedCount: 1,
    skipped: 0,
    stockJoin: { lineCount: 3, unjoinedCount: 1, tierCounts: {}, guidance: '', lines: LINES },
    ...overrides,
  };
}

/* The fixture that exercises every row shape at once. */
const MIXED = [
  recipe('拌空心菜', [
    slot(0, {
      parsedName: '空心菜',
      matchMethod: 'note_basename',
      matchTier: 4,
      pantryItemId: 83,
      inStock: true,
      stockJoinState: 'joined',
    }),
  ]),
  recipe('煮菜菜', [
    slot(0, {
      parsedName: 'Arugula',
      matchMethod: 'synonym',
      matchTier: 6,
      pantryItemId: 999,
      confidence: 0.7,
    }),
  ]),
];

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

function install(options = {}) {
  const dom = installFakeDom(options);
  // `views/pantry.js` reaches `readViewState` / `saveViewState` through the
  // router singleton, so a router has to exist before the view mounts — the same
  // arrangement `main.js` builds.
  initRouter({ window: dom.window, views: {} });
  return dom;
}

function setResponder(fn) {
  responder = fn;
}

globalThis.fetch = async (url, init = {}) => {
  fetches.push({ url: String(url), init });
  return responder(String(url), init, fetches.length);
};

const settle = async (turns = 8) => {
  for (let i = 0; i < turns; i += 1) await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
};

test.beforeEach(() => {
  fetches.length = 0;
  api.resetApi();
  api.recipeListCache.clear();
  setResponder((url) =>
    url.includes('/api/session') ? json(sessionBody(), 200) : json(listPayload([]), 200),
  );
});

test.afterEach(() => {
  api.resetApi();
  api.recipeListCache.clear();
});

/** Mount, let the first load land, and hand back the rendered rows. */
async function render(options = {}) {
  const dom = install(options);
  setResponder((url) => {
    if (url.includes('/api/session')) return json(sessionBody(), 200);
    return json(listPayload(options.recipes || [], options.payloadOverrides || {}), 200);
  });
  const unmount = mountPantry(dom.root);
  await settle();
  return { dom, unmount, rows: dom.root.querySelectorAll('[data-role="ingredient"]') };
}

const recipeUrls = () => fetches.map((entry) => entry.url);

/* --- 1. one request, and it is the recipe list ---------------------------- */

test('this tab reads the SAME endpoint the 菜谱 tab reads, and nothing else', async () => {
  const { dom, unmount } = await render({ recipes: MIXED });
  try {
    const data = recipeUrls().filter((url) => !url.includes('/api/session'));
    assert.equal(data.length, 1, `expected exactly one data read, got ${JSON.stringify(data)}`);
    // The point of the whole design: one payload, so the two tabs cannot
    // disagree about the same jar. A second route here is the regression.
    assert.match(data[0], /^\/api\/recipes\?/);
    assert.equal(
      recipeUrls().some((url) => url.includes('/api/pantry')),
      false,
      'the 食材 tab must not read the pantry through a second route',
    );
  } finally {
    unmount();
    dom.restore();
  }
});

/* --- 2 & 3. the rows ------------------------------------------------------ */

test('a row shows the Pantry.md name, the in-stock state, and the recipe links', async () => {
  const { dom, unmount, rows } = await render({ recipes: MIXED });
  try {
    // 83 (both sides), 90 and 116 (stock only), 999 (recipe only).
    assert.equal(rows.length, 4);
    const greens = rows.find((node) => node.dataset.id === '83');
    assert.ok(greens, 'the 空心菜 row is missing');
    // The user's own line leads, because there is no canonical_name in this
    // payload and inventing one would be a guess about a table the client cannot
    // read.
    assert.equal(greens.querySelector('[data-role="item-name"]').textContent, '空心菜嫩苗 0.95-1.05 磅');
    assert.equal(greens.querySelector('[data-role="item-state"]').textContent, STATE_LABELS['in-stock']);
    assert.equal(greens.querySelector('[data-role="item-name-source"]').textContent, NAME_SOURCE_LABELS.stock);
    // …and the recipe's own word is shown, not merged.
    assert.equal(
      greens.querySelector('[data-role="item-other-names"]').dataset.names,
      '空心菜',
      'the recipe side of the disagreement is not on the row',
    );
    const link = greens.querySelector('[data-role="recipe-link"]');
    assert.equal(link.getAttribute('href'), '#/recipe/%E6%8B%8C%E7%A9%BA%E5%BF%83%E8%8F%9C');
    assert.equal(link.textContent, '拌空心菜');
  } finally {
    unmount();
    dom.restore();
  }
});

test('no row prints a recipe SCORE, because that number is the recipe’s', async () => {
  const { dom, unmount, rows } = await render({ recipes: MIXED });
  try {
    for (const row of rows) {
      const text = textOf(row);
      // `found/total` beside an ingredient reads as "this jar is 1/2 of your
      // dinner", and it is not: it scores the whole recipe.
      assert.ok(!/\d+\/\d+ ingredients found/.test(text), `a row printed a headline: ${text}`);
      assert.ok(!text.includes('严格模式'), 'a row printed a strict-mode clause');
    }
  } finally {
    unmount();
    dom.restore();
  }
});

test('an item in the house that no recipe uses is a row with a sentence, not links', async () => {
  const { dom, unmount, rows } = await render({ recipes: MIXED });
  try {
    const milk = rows.find((node) => node.dataset.id === '90');
    assert.ok(milk, 'the stock-only row is missing — this is the case that tab exists for');
    assert.equal(milk.dataset.state, 'in-stock');
    assert.equal(milk.dataset.recipes, '0');
    assert.equal(milk.querySelectorAll('[data-role="recipe-link"]').length, 0);
    assert.match(textOf(milk.querySelector('[data-role="item-no-recipes"]')), /没有一道用它/);
  } finally {
    unmount();
    dom.restore();
  }
});

test('an item the house does NOT have is still a row', async () => {
  const { dom, unmount, rows } = await render({ recipes: MIXED });
  try {
    // D4's rule from the other side: a 0/6 recipe is a first-class row, and so is
    // the ingredient a recipe needs and the house lacks. No "in stock only"
    // filter anywhere.
    const arugula = rows.find((node) => node.dataset.id === '999');
    assert.ok(arugula, 'an out-of-stock item was filtered out');
    assert.equal(arugula.dataset.state, 'out-of-stock');
    assert.equal(arugula.querySelector('[data-role="item-state"]').textContent, STATE_LABELS['out-of-stock']);
    assert.equal(arugula.querySelector('[data-role="item-name"]').textContent, 'Arugula');
    // …and it is named by the RECIPE, because no Pantry.md line reached this
    // Pantry Item. The row says which, rather than presenting one file's word as
    // if both files had agreed.
    assert.equal(arugula.querySelector('[data-role="item-name-source"]').textContent, NAME_SOURCE_LABELS.recipe);
    assert.equal(arugula.querySelector('[data-role="item-other-names"]'), null);
  } finally {
    unmount();
    dom.restore();
  }
});

test('the duplicate-name join shows BOTH ids as two rows, not one winner', async () => {
  const { dom, unmount, rows } = await render({ recipes: MIXED });
  try {
    // §9.13.4: a name lookup that picked a winner by insertion order is the one
    // failure `app/pantry/stock.py` forbids.
    const both = rows.filter((node) => node.dataset.id === '90' || node.dataset.id === '116');
    assert.equal(both.length, 2);
    assert.deepEqual(
      both.map((node) => node.dataset.id),
      ['90', '116'],
    );
  } finally {
    unmount();
    dom.restore();
  }
});

/* --- 4. a miss is a pointer ---------------------------------------------- */

test('a Stock Join miss is a pointer to 溯源 and never a row', async () => {
  const { dom, unmount, rows } = await render({ recipes: MIXED });
  try {
    const pointer = dom.root.querySelector('[data-role="unjoined-pointer"]');
    assert.ok(pointer, 'the miss was not reported at all — silence reads as "nothing is wrong"');
    assert.equal(pointer.dataset.count, '1');
    // The count is interpolated, and the sentence is the one the module exports,
    // so there is a single wording to keep.
    assert.equal(textOf(pointer), UNRESOLVED_POINTER.replace('{count}', '1'));
    assert.match(textOf(pointer), /溯源/);
    // And it is not a row: a miss turned into a Pantry Item would be a
    // plausible-looking row on a screen whose whole job is to be believed.
    for (const row of rows) {
      assert.ok(!textOf(row).includes('Manukora'), 'the miss line was rendered as an item');
    }
  } finally {
    unmount();
    dom.restore();
  }
});

test('with no misses, there is no pointer', async () => {
  const { dom, unmount } = await render({
    recipes: MIXED,
    payloadOverrides: {
      stockUnjoinedCount: 0,
      stockJoin: { lineCount: 2, unjoinedCount: 0, tierCounts: {}, guidance: '', lines: LINES.slice(0, 2) },
    },
  });
  try {
    assert.equal(dom.root.querySelector('[data-role="unjoined-pointer"]'), null);
    // …and the header line drops the zero counter rather than printing "0",
    // which is the same rule home.js follows: a "0 unresolved" badge on every
    // screen trains the eye to skip the number that matters.
    assert.ok(!dom.root.querySelector('[data-role="meta"]').textContent.includes('没对上的库存行'));
  } finally {
    unmount();
    dom.restore();
  }
});

/* --- 5 & 6. the failure paths -------------------------------------------- */

test('a 503 paints the shared unavailable panel and NO inventory at all', async () => {
  const dom = install();
  setResponder((url) => {
    if (url.includes('/api/session')) return json(sessionBody(), 200);
    return json({ requestId: 'req-503', code: 'pantry_stock_unreadable' }, 503);
  });
  const unmount = mountPantry(dom.root);
  await settle();
  try {
    const panel = dom.root.querySelector('[data-panel-state="unavailable"]');
    assert.ok(panel, 'the 503 painted no unavailable state');
    assert.ok(textOf(panel).includes('req-503'), 'the requestId is not shown');
    // A 503 body is `{requestId, code}` — no `recipes`, no `stockJoin`. A view
    // that assumed an inventory would paint an empty screen, and an empty
    // ingredient list reads as "your pantry is empty".
    assert.equal(dom.root.querySelectorAll('[data-role="ingredient"]').length, 0);
    assert.equal(dom.root.querySelectorAll('[data-role="empty-ingredients"]').length, 0);
    // No retry control: F1's failure is permanent until the user fixes the vault.
    assert.equal(walk(dom.root).filter((node) => node.tagName === 'BUTTON').length, 0);
  } finally {
    unmount();
    dom.restore();
  }
});

test('an empty SUCCESSFUL response is an invitation, never an error', async () => {
  // Empty means empty: no recipe slot AND no Pantry.md line. Leaving the frozen
  // lines in place would produce three in-stock items, and the assertion below
  // would be testing a different screen.
  const { dom, unmount } = await render({
    recipes: [],
    payloadOverrides: {
      stockUnjoinedCount: 0,
      stockJoin: { lineCount: 0, unjoinedCount: 0, tierCounts: {}, guidance: '', lines: [] },
    },
  });
  try {
    const empty = dom.root.querySelector('[data-panel-state="empty"]');
    assert.ok(empty, 'a successful empty response painted no invitation');
    assert.equal(empty.querySelector('[data-role="empty-ingredients"]') !== null, true);
    assert.equal(dom.root.querySelectorAll('[data-role="ingredient"]').length, 0);
    // An empty list is a normal state, not a failure.
    assert.equal(empty.className.includes('banner-error'), false);
  } finally {
    unmount();
    dom.restore();
  }
});

test('the empty state is unreachable during loading and after a failure', async () => {
  const dom = install();
  setResponder((url) => {
    if (url.includes('/api/session')) return json(sessionBody(), 200);
    return json({ requestId: 'r', code: 'boom' }, 500);
  });
  const unmount = mountPantry(dom.root);
  // The loading state is on screen before anything resolves…
  assert.equal(
    dom.root.querySelector('[data-panel-state="loading"]') !== null,
    true,
    'nothing is on screen while the first load is in flight',
  );
  await settle();
  try {
    const failure = dom.root.querySelector('[data-panel-state="error"]');
    assert.ok(failure, 'a 500 painted no error state');
    assert.equal(dom.root.querySelectorAll('[data-role="empty-ingredients"]').length, 0);
    // …and the failure offers a retry, because this one CAN be transient.
    assert.ok(
      walk(dom.root).some((node) => node.tagName === 'BUTTON' && node.textContent === '重新检查'),
      'a 500 offers no retry',
    );
  } finally {
    unmount();
    dom.restore();
  }
});

test('offline says so, and a failed poll keeps the last good paint', async () => {
  const { dom, unmount, rows } = await render({ recipes: MIXED, online: false });
  try {
    assert.ok(
      dom.root.querySelector('[data-role="offline"]'),
      'the offline banner is missing',
    );
    assert.equal(rows.length, 4, 'the offline paint lost the rows it already had');
  } finally {
    unmount();
    dom.restore();
  }
});

/* --- 7 & 8. the two lifecycle rules -------------------------------------- */

/* Comments are stripped before these SOURCE gates run, and that is not
 * cosmetic: this file's own header explains at length why `initPullToRefresh`
 * must not be called here, so a naive substring scan reads the rule and fails the
 * file that obeys it. `tests/js/provenance.test.mjs` does the same for the same
 * reason. */
const VIEW_CODE = VIEW_SOURCE
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '');

test('this view has NO pull-to-refresh, because F19 scopes it to home.js', () => {
  // F19 excludes the other views by name and for reasons that are not stylistic;
  // a second pull target is a second thing to get wrong. Asserted on the SOURCE
  // because the symptom — a missing gesture — is invisible to a DOM test.
  assert.equal(VIEW_CODE.includes('initPullToRefresh'), false);
  assert.equal(VIEW_CODE.includes('pull-refresh'), false);
  assert.equal(VIEW_CODE.includes('ptr-indicator'), false);
  // …and nothing else in the app does either: F19 says ONE view, and a second
  // caller anywhere would break the rule this file is asserting.
  const callers = readdirSync(join(REPO_ROOT, 'app', 'static', 'js', 'views')).filter((name) =>
    stripJsComments(
      readFileSync(join(REPO_ROOT, 'app', 'static', 'js', 'views', name), 'utf8'),
    ).includes('initPullToRefresh'),
  );
  assert.deepEqual(callers, ['home.js'], 'pull-to-refresh grew a second home');
});

test('the view composes no cookable boolean and displays no path', () => {
  // F17 and the two server-owned-root rules, asserted on the source because both
  // are bans rather than renders: a `cookable` class or a `notePath` in a
  // `data-` attribute would be a claim this payload cannot support.
  assert.equal(/cookable/i.test(VIEW_CODE), false);
  assert.equal(VIEW_CODE.includes('notePath'), false);
  assert.equal(VIEW_CODE.includes('relativePath'), false);
  assert.equal(VIEW_CODE.includes('innerHTML'), false);
  // The two states are the two states: no third "unknown" bucket.
  assert.deepEqual(Object.keys(STATE_LABELS), ['in-stock', 'out-of-stock']);
});

test('unmount() stops the poll and the online listener', async () => {
  const { dom, unmount } = await render({ recipes: MIXED });
  const before = fetches.length;
  unmount();
  // Both of these would add a fetch if the teardown had missed one. A view that
  // re-reads behind the user is the class of leak `unmount()` ordering exists to
  // prevent — the router clears `#app-root` immediately after, so the read would
  // land in a subtree the next view already owns.
  dom.window.dispatchEvent({ type: 'online' });
  await settle();
  dom.runTimers();
  await settle();
  assert.equal(fetches.length, before, 'the unmounted view kept reading');
  dom.restore();
});

test('the panel says the two tabs read one endpoint, and the meta line shows the age', async () => {
  const { dom, unmount } = await render({ recipes: MIXED });
  try {
    assert.match(textOf(dom.root.querySelector('[data-role="view-note"]')), /同一个接口/);
    const meta = textOf(dom.root.querySelector('[data-role="meta"]'));
    // F11's 30 s TTL, printed: the user edits tasks in Obsidian while this tab is
    // open, and two screens printing different ages for one pantry would be a lie
    // about one of them.
    assert.match(meta, /库存最长 30 秒未更新/);
    assert.match(meta, /4 行食材，其中在货 3/);
    assert.match(meta, /在货但没有菜谱用它 2/);
  } finally {
    unmount();
    dom.restore();
  }
});

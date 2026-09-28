/* #/provenance — the provenance table, and F1's Stock Join inspector.
 *
 * These are the assertions a screenshot cannot make. They drive the REAL view
 * module through the REAL `dom.js` `el()` against the fake DOM in
 * `fake-dom.mjs`, with `api.js`'s transport stubbed — so a failure means the
 * view is wrong, not that a hand-written stub drifted.
 *
 * The fixtures are the app's OWN frozen data, copied out of
 * `tests/fixtures/golden_match_results.json` and the frozen `Pantry.md`: the
 * real two-guard refusal (`好丽友 高笑美芝麻饼干 216 克`, id 25, refused by BOTH
 * `segment_boundary` and `category_family`), the real tiers the vault actually
 * produces (4 `note_basename`, 5 `normalized_exact`, 6 `synonym`, 7 `staples`,
 * 8 the ladder's own miss), and the real join states. A fixture invented for a
 * test would not catch a wiring mistake, and the whole point of this view is
 * that it is wired to what the server publishes.
 *
 * What is asserted, and why each one is load-bearing:
 *
 *   1. **Every tier renders, and `tier 0` renders as "没有层级命中".** The
 *      ladder's miss (`tier 8 · unresolved`) and "no mapping row exists yet"
 *      (`tier 0`) are different facts about different states, and printing both
 *      as a number is how "we don't know" reads as "we looked, and it is not
 *      there".
 *   2. **The `unjoined` bucket and `stockUnjoinedCount` are both on screen**,
 *      with the sentence that keeps "not in the in-stock set" from being read
 *      as "not in the house".
 *   3. **A two-guard refusal shows BOTH reasons.** The audit deliberately keeps
 *      every objection and `reason` carries only the first; recording one
 *      understates what is holding the row back.
 *   4. **F7, the render-only property: toggling 调试 repaints the payload already
 *      in hand and issues ZERO requests.** This is the test the view exists to
 *      be able to pass.
 *   5. **The 503 reuses #21's panel**, and a source-level gate proves no second
 *      implementation of the fail-closed state crept in.
 *   6. **No `cookable` anywhere, and no path** — neither absolute nor
 *      vault-relative — is ever displayed.
 *   7. **The two repair actions**, their 409s, and their non-vacuity.
 *
 * **NO TEST HERE READS A LIVE PATH.** Every input is a literal in this file or
 * a file in the repository, and the DOM is fake. Nothing here starts the app,
 * touches the vault, or depends on a fixture's absolute location — which is why
 * a run on another machine produces the same result.
 */

import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { register } from 'node:module';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { installFakeDom, textOf, walk } from './fake-dom.mjs';

register('./app-version-hook.mjs', import.meta.url);

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const PROVENANCE_SOURCE = join(REPO_ROOT, 'app', 'static', 'js', 'views', 'provenance.js');
const PANELS_SOURCE = join(REPO_ROOT, 'app', 'static', 'js', 'panels.js');

/* The `?v=__APP_VERSION__` token is a serve-time substitution, so the modules
 * below can only be imported AFTER the resolve hook is registered — hence
 * dynamic import, and hence this file is the one place that knows the trick. */
const api = await import('../../app/static/js/api.js');
const { initRouter } = await import('../../app/static/js/router.js');
const { setDebugEnabled } = await import('../../app/static/js/prefs.js');
const {
  mount: mountProvenance,
  tierText,
  joinCounts,
  joinTierText,
  JOIN_STATE_LABELS,
  JOIN_STATE_SENTENCES,
  RESOLVE_LABEL,
  REMAP_LABEL,
  OFFLINE_WRITE_REASON,
} = await import('../../app/static/js/views/provenance.js');

/* --- fixtures ------------------------------------------------------------ */

const sessionBody = (overrides = {}) => ({
  identity: 'you@example.com',
  csrfToken: 'token-a',
  version: 'v0.4.0',
  readOnly: false,
  appTimezone: 'America/New_York',
  ...overrides,
});

/** One slot, with the exact wire shape §9.16 publishes and nothing invented. */
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

/** One audit entry, in `candidate_payload`'s seven keys. */
function candidate(id, name, rejectedBy, overrides = {}) {
  return {
    pantryItemId: id,
    canonicalName: name,
    pantryCategory: '4',
    family: '4',
    relation: 'token_contains',
    reason: rejectedBy[0] || 'matched',
    rejectedBy,
    ...overrides,
  };
}

function recipe(noteName, ingredients, overrides = {}) {
  return {
    noteName,
    notePath: `Hobbies/做饭/Recipes/${noteName}.md`,
    found: ingredients.filter((item) => !item.isSeasoning && item.pantryItemId !== null).length,
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
    stockRevision: 'sha256:6a231469ab208483b5dfef0b6720ef46e205b23e21b1d2a50352ddc77dc20aea',
    strict: 0,
    staleMappingCount: 0,
    stockUnjoinedCount: 0,
    skipped: 0,
    ...overrides,
  };
}

/**
 * The frozen vault, one recipe per real tier, plus the two states the app
 * reaches only on demand:
 *
 *   - `拌空心菜` slot 0 is tier 6 / `synonym` / id 83 / `joined` / in stock — the
 *     pantry line and the catalog agree;
 *   - `拌空心菜` slot 1 is 芝麻 with the REAL two-guard refusal of id 25
 *     (`segment_boundary` AND `category_family`) and two more candidates;
 *   - `烤鲭鱼` slot 0 is tier 5 / `normalized_exact` / id 22 / `override` — a
 *     product only `line_overrides.yaml` can explain, and it is not on the
 *     shelf, which is the two-facts-one-row case F1 warns about;
 *   - `微波菜菜` slot 0 is tier 4 / `note_basename` / id 56;
 *   - `茶碗蒸` slot 0 is tier 7 / `staples` on a 调料, so it has no mapping row
 *     and therefore no manual control at all;
 *   - `花蛤拌饭` slot 0 is `tier 0` — `app/api/recipes.py`'s "no mapping row
 *     yet" state, which is NOT the ladder's own miss;
 *   - `Easy Fragrant Fried Rice` slot 0 is a hand fix (`manual`), the one
 *     `matchMethod` the app never derives.
 */
/**
 * §9.13.4's `stockJoin` table, as the server publishes it.
 *
 * These are the app's OWN frozen numbers, read out of `tests/pantry/test_stock_join.py`
 * and the frozen `Pantry.md`: 45 product lines, 3 on the exact tier, 40 on the
 * basename tier, 2 through `line_overrides.yaml`, 0 unresolved. A subset is
 * reproduced here because 45 cards in a unit test buys nothing — but every row
 * is a real row, including the three shapes the table has to get right:
 *
 *   - a **basename hit** — `空心菜嫩苗 0.95-1.05 磅` reaching id 83, which is not
 *     the name on the line;
 *   - a **duplicate-name join** — the real `organic 1% milk` collision, [90, 116],
 *     which must publish BOTH ids or the loser vanishes with no diagnostic;
 *   - a **tier-3 override hit** — `Sanpellegrino CIAO! Peach Sparkling Water,
 *     24-Pack`, the line no catalog row explains without
 *     `app/pantry/line_overrides.yaml`;
 *   - and a **miss**, driven by the test below rather than by the shipped file:
 *     the frozen note has zero of them, and a table that only ever renders
 *     `joined` rows is exactly the table that would read as healthy while the
 *     join was broken.
 */
const FROZEN_JOIN = {
  lineCount: 7,
  unjoinedCount: 2,
  tierCounts: { exact: 1, basename: 2, override: 2 },
  guidance:
    '这个 join 只有三层（精确名 → basename → line_overrides.yaml），没有第四层，也没有第二次重扫：' +
    '它没有物化的表可以重扫。三层都没命中的那一行只是不在在货名单里 —— ' +
    '这不是“家里没有”的证据，但也不会自己变好。' +
    'app/pantry/line_overrides.yaml 是承重的、不是装饰：没有它，一个 miss 唯一的修法就是改你自己的库。',
  lines: [
    {
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
    },
    {
      lineIndex: 372,
      section: '1',
      text: '365 By Whole Foods Market, Organic 1% Milk, 32 Fl Oz',
      core: 'Organic 1% Milk',
      stockJoinState: 'joined',
      tier: 2,
      // The real collision from `COLLISIONS`: two catalog rows, one product.
      pantryItemIds: [90, 116],
      overrideKey: 'organic 1% milk',
      overrideName: null,
      repairHint: null,
    },
    {
      lineIndex: 480,
      section: '2',
      text: 'Sanpellegrino CIAO! Peach Sparkling Water, 24-Pack',
      core: 'CIAO! Peach Sparkling Water 24-Pack',
      stockJoinState: 'override',
      tier: 3,
      pantryItemIds: [48],
      overrideKey: 'ciao! peach sparkling water 24-pack',
      overrideName: null,
      repairHint: null,
    },
    {
      lineIndex: 500,
      section: '2',
      text: '禾苑 蟹粉鱼肉狮子头 冷冻 280 克',
      core: '蟹粉鱼肉狮子头 冷冻',
      stockJoinState: 'override',
      tier: 3,
      pantryItemIds: [108],
      overrideKey: '蟹粉鱼肉狮子头 冷冻',
      overrideName: null,
      repairHint: null,
    },
    {
      lineIndex: 511,
      section: '2',
      text: 'Manukora Manuka Honey MGO 50+',
      core: 'Manukora Manuka Honey MGO 50+',
      stockJoinState: 'unresolved',
      tier: 0,
      pantryItemIds: [],
      overrideKey: 'manukora manuka honey mgo 50+',
      overrideName: null,
      // SERVER TEXT. The client prints this and composes none of it; a test
      // below asserts the rendered row carries this string verbatim.
      repairHint:
        '在 app/pantry/line_overrides.yaml 的 overrides 里加一行：' +
        '键写 manukora manuka honey mgo 50+，值写这一行真正指向的那个 Pantry Item 的 canonical_name。' +
        '服务器不替你猜这个值 —— 三层阶梯之外没有第四层可以猜，' +
        '猜出来的名字只会把一个诚实的 miss 变成一个错答案。',
    },
    {
      lineIndex: 540,
      section: '5',
      text: 'POM Wonderful 100% Pomegranate Juice, 48 Fl Oz',
      core: 'POM Wonderful 100% Pomegranate Juice',
      stockJoinState: 'joined',
      tier: 1,
      pantryItemIds: [157],
      overrideKey: 'pom wonderful 100% pomegranate juice',
      overrideName: null,
      repairHint: null,
    },
    {
      // The second miss shape: the override tier FIRED and did not resolve, so
      // the server has a real `canonical_name` to report — the stale one the
      // reviewed file names. The remedy differs (fix or delete the entry, do not
      // add a second), so the hint differs, and a table that rendered both misses
      // with one sentence would be wrong about half of them.
      lineIndex: 561,
      section: '6',
      text: 'Trader Joe’s Dark Chocolate Peanut Butter Cup 6 盎司',
      core: 'Trader Joe’s Dark Chocolate Peanut Butter Cup',
      stockJoinState: 'unresolved',
      tier: 0,
      pantryItemIds: [],
      overrideKey: 'trader joe’s dark chocolate peanut butter cup',
      overrideName: 'Trader Joes Dark Chocolate Peanut Butter Cups',
      repairHint:
        '这一行已经有一条覆盖了，但它的值 Trader Joes Dark Chocolate Peanut Butter Cups ' +
        '在现在的 live 目录里查不到（打错了，或者产品被重新导入改了名）。' +
        '把 app/pantry/line_overrides.yaml 里键 trader joe’s dark chocolate peanut butter cup ' +
        '的值改成一个目录里真实存在的 canonical_name，或者删掉这一行。',
    },
  ],
};

const FROZEN = listPayload(
  [
    recipe('拌空心菜', [
      slot(0, {
        rawValue: '🥬 空心菜',
        parsedName: '空心菜',
        matchMethod: 'synonym',
        matchTier: 6,
        pantryItemId: 83,
        confidence: 0.7,
        inStock: true,
        stockJoinState: 'joined',
        candidatesJson: [
          candidate(83, '空心菜', [], { relation: 'normalized_exact', pantryCategory: '1.2', family: '1.2' }),
        ],
      }),
      slot(1, {
        rawValue: '芝麻',
        parsedName: '芝麻',
        isSeasoning: true,
        candidatesJson: [
          candidate(20, '臻品德 芝麻酱烧饼 10只入 1.54 磅', ['segment_boundary', 'category_family'], {
            relation: 'basename_prefix',
            pantryCategory: '1.2',
            family: '1.2',
          }),
          candidate(25, '好丽友 高笑美芝麻饼干 216 克', ['segment_boundary', 'category_family']),
          candidate(66, '思念 黑芝麻开心果玉汤圆 冷冻 200 克', ['segment_boundary', 'category_family']),
        ],
      }),
    ]),
    recipe('烤鲭鱼', [
      slot(0, {
        rawValue: 'Mackerel',
        parsedName: 'Mackerel',
        matchMethod: 'normalized_exact',
        matchTier: 5,
        pantryItemId: 22,
        confidence: 0.85,
        inStock: false,
        stockJoinState: 'override',
      }),
    ]),
    recipe('微波菜菜', [
      slot(0, {
        rawValue: 'Kale',
        parsedName: 'Kale',
        matchMethod: 'note_basename',
        matchTier: 4,
        pantryItemId: 56,
        confidence: 0.9,
        inStock: true,
        stockJoinState: 'joined',
      }),
    ]),
    recipe('茶碗蒸', [
      slot(0, { rawValue: '生抽', parsedName: '生抽', matchMethod: 'staples', matchTier: 7, confidence: 0.6, isSeasoning: true }),
    ]),
    recipe('花蛤拌饭', [slot(0, { parsedName: '茼蒿', matchTier: 0, confidence: 0 })]),
    recipe('Easy Fragrant Fried Rice', [
      slot(0, {
        parsedName: '西兰花',
        matchMethod: 'manual',
        matchTier: 0,
        pantryItemId: 14,
        confidence: 1,
        inStock: true,
        stockJoinState: 'joined',
      }),
      slot(1, { rawValue: '🥚', parsedName: '鸡蛋' }),
    ]),
  ],
  { stockUnjoinedCount: 2, staleMappingCount: 1, skipped: 1, stockJoin: FROZEN_JOIN },
);

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
  initRouter({ window: dom.window, views: {} });
  return dom;
}

/**
 * One responder for the whole surface, so no test has to remember which URL
 * comes back from where. The list route is matched on `/api/recipes` followed
 * by `?` or the end, so `/api/recipes/resolve` and the per-slot mapping routes
 * fall through to `other`. `list: null` sends the LIST route to `other` too,
 * which is how the fail-closed 503 and the ordinary 500 are driven.
 */
function setResponder({ list = FROZEN, session = {}, other = null } = {}) {
  responder = (url, init) => {
    if (url.includes('/api/session')) return json(sessionBody(session));
    if (/\/api\/recipes(\?|$)/.test(url) && (!init.method || init.method === 'GET')) {
      return list === null ? (other ? other(url, init) : json({})) : json(list);
    }
    if (other) return other(url, init);
    return json({}, 200);
  };
}

globalThis.fetch = async (url, init = {}) => {
  fetches.push({ url: String(url), init });
  return responder(String(url), init, fetches.length);
};

const settle = async (turns = 6) => {
  for (let i = 0; i < turns; i += 1) await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
};

function button(root, label) {
  return walk(root).find((node) => node.tagName === 'BUTTON' && node.textContent === label);
}

const fieldsOf = (card) =>
  walk(card)
    .filter((node) => node.className === 'field')
    .map((node) => [textOf(node.childNodes[0]), textOf(node.childNodes[1])]);

const fieldPairs = (card) => new Map(fieldsOf(card));

const fieldValue = (card, label) => {
  const found = fieldPairs(card).get(label);
  return found === undefined ? null : found;
};

/**
 * Mount with the frozen payload. `options` is merged into the responder, so a
 * test that needs a different answer for a second route passes it here rather
 * than installing a responder and having this overwrite it.
 */
async function mountFrozen(dom, list = FROZEN, options = {}) {
  setResponder({ list, ...options });
  const unmount = mountProvenance(dom.root);
  await settle();
  return unmount;
}

test.beforeEach(() => {
  fetches.length = 0;
  api.resetApi();
  api.recipeListCache.clear();
  // The 调试 flag lives in `localStorage`, and `prefs.js` falls back to an
  // in-memory map when there is none — so it is reset here rather than trusted
  // not to leak from the previous test.
  setDebugEnabled(false);
  setResponder({ list: FROZEN });
});

test.afterEach(() => {
  api.resetApi();
  setDebugEnabled(false);
});

/* ==========================================================================
   1. The tiers, including "no tier fired"
   ========================================================================== */

test('tierText names every state the server can send, and never prints tier 0 as a tier', () => {
  assert.equal(tierText({ matchTier: 4, matchMethod: 'note_basename' }), 'tier 4 · note_basename');
  assert.equal(tierText({ matchTier: 5, matchMethod: 'normalized_exact' }), 'tier 5 · normalized_exact');
  assert.equal(tierText({ matchTier: 6, matchMethod: 'synonym' }), 'tier 6 · synonym');
  assert.equal(tierText({ matchTier: 7, matchMethod: 'staples' }), 'tier 7 · staples');
  // The ladder's own miss: it RAN, and adopted nothing.
  assert.ok(tierText({ matchTier: 8, matchMethod: 'unresolved' }).startsWith('tier 8 · unresolved'));
  // And the state that is NOT a miss at all: no mapping row exists yet, so
  // nothing has even looked. `0` must never read as a tier that fired.
  const none = tierText({ matchTier: 0, matchMethod: 'unresolved' });
  assert.ok(none.includes('没有层级命中'), none);
  assert.ok(none.includes('tier 0'), 'the raw value is still shown, just not as a tier');
  assert.equal(/tier 0 ·/.test(none), false, 'tier 0 was rendered as if a tier had fired');
  // A missing or nonsensical value is treated as "no tier", never as a number.
  assert.ok(tierText({}).includes('没有层级命中'));
  assert.ok(tierText(null).includes('没有层级命中'));
});

test('every tier in the frozen payload is rendered, and the "no tier" card says so', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  const cards = dom.byData('slot');
  assert.equal(cards.length, 8, 'one card per slot, in payload order');

  assert.deepEqual(
    cards.map((card) => card.dataset.tier),
    ['6', '8', '5', '4', '7', '0', '0', '8'],
  );
  const byNote = (note, index) =>
    cards.find((card) => card.dataset.note === note && card.dataset.slot === index);

  assert.equal(fieldValue(byNote('微波菜菜', '0'), '匹配层级'), 'tier 4 · note_basename');
  assert.equal(fieldValue(byNote('烤鲭鱼', '0'), '匹配层级'), 'tier 5 · normalized_exact');
  assert.equal(fieldValue(byNote('拌空心菜', '0'), '匹配层级'), 'tier 6 · synonym');
  assert.equal(fieldValue(byNote('茶碗蒸', '0'), '匹配层级'), 'tier 7 · staples');
  assert.ok(fieldValue(byNote('拌空心菜', '1'), '匹配层级').startsWith('tier 8 · unresolved'));
  // The tier-0 card is the "nothing has looked yet" state, said in words.
  const noTier = fieldValue(byNote('花蛤拌饭', '0'), '匹配层级');
  assert.ok(noTier.includes('没有层级命中'), noTier);
  assert.equal(byNote('Easy Fragrant Fried Rice', '0').dataset.method, 'manual');
  unmount();
  dom.restore();
});

test('the rest of the published row is on the card: raw, parsed, method, id, confidence', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  const card = dom.byData('slot').find((node) => node.dataset.note === '烤鲭鱼');
  assert.equal(fieldValue(card, '原文 raw_value'), 'Mackerel');
  assert.equal(fieldValue(card, '解析名 parsed_name'), 'Mackerel');
  assert.equal(fieldValue(card, '解析方式 parse_method'), 'bare');
  assert.equal(fieldValue(card, '匹配方式 match_method'), 'normalized_exact');
  assert.equal(fieldValue(card, 'Pantry Item'), '#22');
  assert.equal(fieldValue(card, '置信度 confidence'), '0.85');
  assert.equal(fieldValue(card, '在货 inStock'), '否');
  // The join state and the shelf are DIFFERENT facts and are two different
  // lines: this product is explained only by the override file, and it is not on
  // the shelf. Merging them is the bug F1 exists to prevent.
  assert.ok(fieldValue(card, 'stockJoinState').startsWith('override'));
  assert.equal(fieldValue(card, '在货 inStock'), '否');
  unmount();
  dom.restore();
});

test('the chip in each card is chips.js\'s own verdict, not a class this view chose', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  const cards = dom.byData('slot');
  const chipIn = (card) => walk(card).find((node) => node.classList.contains('chip'));

  const joined = cards.find((card) => card.dataset.note === '拌空心菜' && card.dataset.slot === '0');
  const joinedChip = chipIn(joined);
  assert.ok(joinedChip.className.includes('chip--in-stock'));
  assert.equal(joinedChip.dataset.matchMethod, 'synonym');
  assert.equal(joinedChip.dataset.tier, '6');
  assert.equal(joinedChip.dataset.itemId, '83');
  assert.equal(joinedChip.dataset.joinState, 'joined');

  // Resolved but not held is NOT in stock, and the colour says so.
  const overrideChip = chipIn(cards.find((card) => card.dataset.note === '烤鲭鱼'));
  assert.ok(overrideChip.className.includes('chip--have-been-buying'));
  // A hand fix is visibly a hand fix: same colour, plus the outline.
  const manualChip = chipIn(
    cards.find((card) => card.dataset.note === 'Easy Fragrant Fried Rice' && card.dataset.slot === '0'),
  );
  assert.ok(manualChip.className.includes('chip--manual'));
  // A 调料 outside strict mode is ignored, which is F17's Materials-only rule.
  const seasoningChip = chipIn(cards.find((card) => card.dataset.kind === 'seasoning'));
  assert.ok(seasoningChip.className.includes('chip--ignored-seasoning'));
  unmount();
  dom.restore();
});

/* ==========================================================================
   2. The Stock Join: the weakest link, made visible
   ========================================================================== */

test('the join summary shows stockUnjoinedCount, the stale count, skipped, and the state split', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  const summary = dom.byData('join-summary')[0];
  assert.ok(summary, 'no join summary rendered');
  const pairs = fieldPairs(summary);
  assert.equal(pairs.get('Pantry.md 里没对上的行（服务器计数）'), '2');
  assert.equal(pairs.get('过期映射'), '1');
  assert.equal(pairs.get('跳过的笔记'), '1', 'a note skipped for malformed frontmatter must not vanish');
  assert.equal(pairs.get('这一屏里的格子总数'), '8');
  assert.equal(pairs.get('join 说 joined'), '3');
  assert.equal(pairs.get('join 说 override'), '1');
  assert.equal(pairs.get('join 说 unresolved'), '4');
  // The header carries both revisions and F11's TTL, so a stale screen says so
  // instead of implying it is current.
  const meta = dom.byData('meta')[0].textContent;
  assert.ok(meta.includes('1da7ba7e0a28'), meta);
  assert.ok(meta.includes('6a231469ab20'), meta);
  assert.ok(meta.includes('30 秒'), meta);
  unmount();
  dom.restore();
});

test('the unjoined bucket names every slot the join could not explain, and refuses to say "you do not have it"', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  const bucket = dom.byData('unjoined')[0];
  assert.ok(bucket, 'no unjoined bucket rendered');
  assert.equal(bucket.dataset.count, '4');
  // The recipe-side face of the same weakness, named slot by slot. The pantry
  // side is the count above; neither is derivable from the other.
  assert.deepEqual(
    dom.byData('unjoined-slot').map((node) => node.textContent),
    [
      '拌空心菜 第 1 格：芝麻（—）',
      '茶碗蒸 第 0 格：生抽（—）',
      '花蛤拌饭 第 0 格：茼蒿（—）',
      'Easy Fragrant Fried Rice 第 1 格：鸡蛋（—）',
    ],
  );
  const note = textOf(dom.byData('unjoined-note')[0]);
  assert.ok(note.includes('2 行'), 'the server count is not restated beside the bucket');
  assert.ok(note.includes('不等于“你没有”'), note);
  // The repair path is named where a miss is repaired (§9.13.4), and the
  // missing re-resolution pass is stated rather than glossed.
  const caveat = textOf(dom.byData('join-caveat')[0]);
  assert.ok(caveat.includes('app/pantry/line_overrides.yaml'), caveat);
  assert.ok(caveat.includes('没有第二次重扫'), 'the absent re-resolution pass is not stated');
  unmount();
  dom.restore();
});

test('the three join states are explained once in the legend, and the warning rides the unresolved row', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  // The legend says all three ONCE, above ~70 rows that each name their own —
  // a paragraph repeated 70 times is a paragraph nobody reads.
  const legend = textOf(dom.byData('join-legend')[0]);
  for (const sentence of Object.values(JOIN_STATE_SENTENCES)) {
    assert.ok(legend.includes(sentence), `the legend is missing: ${sentence}`);
  }
  // ... and the sentence that actually matters is repeated on the rows where it
  // bites, because that is the row a user is staring at.
  const cards = dom.byData('slot');
  const unresolvedCards = cards.filter((card) => card.dataset.joinState === 'unresolved');
  const joinedCards = cards.filter((card) => card.dataset.joinState !== 'unresolved');
  assert.equal(unresolvedCards.length, 4);
  for (const card of unresolvedCards) {
    const warning = walk(card).find((node) => node.dataset.role === 'join-warning');
    assert.ok(warning, `no warning on an unresolved card: ${card.dataset.note}`);
    assert.ok(warning.textContent.includes('你家里没有'), warning.textContent);
    assert.ok(warning.textContent.includes('line_overrides.yaml'), 'the repair path is not named on the row');
  }
  for (const card of joinedCards) {
    assert.equal(
      walk(card).some((node) => node.dataset.role === 'join-warning'),
      false,
      'the warning is repeated on a row the join explained',
    );
  }
  // And a card's own value is the SHORT label, which is what makes 70 of them
  // readable; the sentence is the legend's job.
  assert.ok(fieldValue(joinedCards[0], 'stockJoinState').startsWith('joined'));
  assert.ok(fieldValue(unresolvedCards[0], 'stockJoinState').startsWith('unresolved'));
  assert.ok(fieldValue(unresolvedCards[0], 'stockJoinState').length < 40, 'the card repeats the whole sentence');
  unmount();
  dom.restore();
});

test('joinCounts separates the three shipped states and counts anything else apart', () => {
  const counts = joinCounts([
    { stockJoinState: 'joined' },
    { stockJoinState: 'joined' },
    { stockJoinState: 'override' },
    { stockJoinState: 'unresolved' },
    { stockJoinState: 'something_new' },
    {},
  ]);
  assert.deepEqual(counts, { joined: 2, override: 1, unresolved: 1, other: 2, total: 6 });
  // NON-VACUITY: these three are the values `app/pantry/stock.py` actually
  // ships, so a rename there fails this rather than silently landing in
  // `other` and reading as zero.
  assert.deepEqual(Object.keys(JOIN_STATE_LABELS).sort(), ['joined', 'override', 'unresolved']);
  assert.equal(joinCounts([]).total, 0);
});

test('an unrecognised join state is named as unrecognised, never shown as a real one', async () => {
  const dom = install();
  const list = listPayload([recipe('怪状态', [slot(0, { stockJoinState: 'half_joined' })])]);
  const unmount = await mountFrozen(dom, list);
  const label = fieldValue(dom.byData('slot')[0], 'stockJoinState');
  assert.ok(label.includes('没见过的 join 状态'), label);
  assert.ok(label.includes('half_joined'), 'the server value is not quoted back');
  // And it is counted apart from the three real ones rather than inflating one.
  assert.equal(fieldValue(dom.byData('join-summary')[0], 'join 说了这一屏没见过的值'), '1');
  assert.equal(fieldValue(dom.byData('join-summary')[0], 'join 说 unresolved'), '0');
  unmount();
  dom.restore();
});

test('a zero unjoined count is still stated, because "0" is the number that trains the eye to skip it', async () => {
  const dom = install();
  const list = listPayload([
    recipe('都在货', [slot(0, { matchMethod: 'synonym', pantryItemId: 83, inStock: true, stockJoinState: 'joined' })]),
  ]);
  const unmount = await mountFrozen(dom, list);
  const summary = dom.byData('join-summary')[0];
  assert.equal(fieldValue(summary, 'Pantry.md 里没对上的行（服务器计数）'), '0');
  assert.ok(textOf(dom.byData('unjoined-note')[0]).includes('0 行'));
  assert.equal(dom.byData('unjoined').length, 0);
  assert.ok(dom.byData('unjoined-empty')[0], 'a clean join says so rather than rendering nothing');
  unmount();
  dom.restore();
});

/* ==========================================================================
   3. The complete rejectedBy list
   ========================================================================== */

test('a candidate refused by TWO guards shows both, and prints the count', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  const rows = dom.byData('candidate');
  assert.equal(rows.length, 4, 'every candidate the ladder saw is listed, rejected ones included');

  const two = rows.find((node) => node.dataset.itemId === '25');
  assert.ok(two, 'the two-guard candidate is not listed');
  // `reason` alone carries only the FIRST guard. The full list is a separate,
  // separately-labelled thing — and it is what is printed.
  assert.equal(two.dataset.reason, 'segment_boundary');
  assert.equal(two.dataset.rejectedBy, 'segment_boundary|category_family');
  const text = textOf(two);
  assert.ok(text.includes('共 2 条'), `the count is not printed: ${text}`);
  assert.ok(text.includes('segment_boundary'), text);
  assert.ok(text.includes('category_family'), text);
  assert.ok(text.includes('好丽友 高笑美芝麻饼干 216 克'), text);
  assert.ok(text.includes('#25'), text);
  // NON-VACUITY for the assertion above: three of the four rows really are
  // two-guard refusals, and the fourth is an adopted row, so "shows both" is
  // not passing because every row happens to have one reason.
  assert.equal(rows.filter((node) => node.dataset.rejectedBy.split('|').length === 2).length, 3);
  assert.equal(rows.filter((node) => node.dataset.rejectedBy === '').length, 1);
  unmount();
  dom.restore();
});

test('an adopted candidate says it has no rejection reason, rather than printing an empty list', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  const adopted = dom.byData('candidate').find((node) => node.dataset.itemId === '83');
  assert.equal(adopted.dataset.rejectedBy, '');
  const line = dom.byData('rejected-by').find((node) => node.parentNode === adopted);
  assert.ok(textOf(line).includes('没有'), textOf(line));
  unmount();
  dom.restore();
});

test('a slot with no candidate rows says so, instead of rendering an empty box', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  // Two of the eight cards carry a candidate list; the other six say so.
  assert.equal(dom.byData('candidates').length, 2, 'only the slots that have rows get a candidate list');
  assert.equal(dom.byData('no-candidates').length, 6);
  assert.ok(dom.byData('no-candidates')[0].textContent.includes('没有留下任何候选行'));
  unmount();
  dom.restore();
});

/* ==========================================================================
   4. F7: the toggle is render-only. This is the test the view exists for.
   ========================================================================== */

test('F7: toggling 调试 repaints from the payload in hand and issues ZERO requests', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);

  // The mount itself made exactly ONE request: the normal list read. No session
  // call (a GET needs no CSRF token) and no second provenance read.
  assert.deepEqual(
    fetches.map((entry) => entry.url),
    ['/api/recipes?strict=0'],
    'the view opened more than the one normal read',
  );
  assert.equal(dom.byData('provenance').length, 0, 'provenance lines before the toggle');

  const before = fetches.length;
  setDebugEnabled(true);
  await settle();

  assert.equal(fetches.length, before, 'toggling 调试 made a request (F7: it is render-only)');
  // It visibly repainted: every chip grew its secondary line, and NOTHING was
  // lost — the repaint consumes the same payload, not a new one.
  const lines = dom.byData('provenance');
  assert.equal(lines.length, 8, 'one provenance line per slot card');
  assert.ok(lines[0].textContent.includes('tier 6 · synonym · #83 · 0.7'), lines[0].textContent);
  assert.equal(dom.byData('slot').length, 8, 'the repaint lost rows');
  assert.equal(dom.byData('unjoined').length, 1, 'the repaint lost the unjoined bucket');
  assert.equal(dom.byData('join-summary').length, 1, 'the repaint lost the join summary');

  // And back off again: still zero requests.
  const afterOff = fetches.length;
  setDebugEnabled(false);
  await settle();
  assert.equal(fetches.length, afterOff, 'turning 调试 off made a request');
  assert.equal(dom.byData('provenance').length, 0);
  unmount();
  dom.restore();
});

test('the mount read carries the strict parameter and nothing else (F8, and no ?debug=1)', async () => {
  const dom = install({ search: '?strict=1' });
  // The payload's own `strict` is what the header prints — the server's answer,
  // not the URL's, because F8 makes the server the scorer.
  const unmount = await mountFrozen(dom, listPayload(FROZEN.recipes, { ...FROZEN, strict: 1 }));
  const call = fetches.find((entry) => entry.url.includes('/api/recipes'));
  assert.equal(call.url, '/api/recipes?strict=1');
  assert.equal(/debug/i.test(call.url), false, 'the toggle leaked into the URL');
  assert.ok(dom.byData('meta')[0].textContent.includes('严格模式 开'));
  // And with the parameter off, the header says so rather than assuming.
  const offDom = install();
  const unmountOff = await mountFrozen(offDom);
  assert.ok(offDom.byData('meta')[0].textContent.includes('严格模式 关'));
  unmountOff();
  offDom.restore();
  unmount();
  dom.restore();
});

test('the 30s TTL re-reads a long-open tab, and an unchanged payload does not repaint it', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  const reads = () => fetches.filter((entry) => entry.url.includes('/api/recipes')).length;
  const before = reads();
  const cardCount = dom.byData('slot').length;

  // Same revisions: the render consumes only these three inputs, so a quiet poll
  // that finds no change leaves the DOM — and the scroll offset — alone.
  dom.runTimers();
  await settle();
  assert.equal(reads(), before + 1, 'the TTL poll never fired');
  assert.equal(dom.byData('slot').length, cardCount);

  setResponder({ list: listPayload(FROZEN.recipes, { ...FROZEN, stockRevision: 'sha256:aaaaaaaabbbb' }) });
  dom.runTimers();
  await settle();
  assert.equal(reads(), before + 2, 'the second poll never fired');
  assert.ok(dom.byData('meta')[0].textContent.includes('aaaaaaaabbbb'), 'the new revision is not shown');
  unmount();
  dom.restore();
});

/* ==========================================================================
   5. F1's 503: #21's panel, and no second implementation
   ========================================================================== */

test('a 503 paints #21\'s unavailable panel, and never a table', async () => {
  const dom = install();
  // `list: null` sends the LIST route itself to the failure: F1's 503 is a
  // refusal of `GET /api/recipes`, not a side channel.
  const unmount = await mountFrozen(dom, null, {
    other: () => json({ requestId: 'req-503-prov', code: 'pantry_stock_unreadable' }, 503),
  });

  const panel = dom.byData('source-unavailable')[0];
  assert.ok(panel, 'the 503 painted no unavailable state');
  assert.equal(panel.dataset.panelState, 'unavailable');
  const text = textOf(panel);
  assert.ok(text.includes('Logistics/库存/Pantry.md'), text);
  assert.ok(text.includes('pantry_stock_unreadable'), text);
  assert.ok(text.includes('req-503-prov'), 'the requestId is not quoted');
  // The dangerous assertions: a 503 body is `{requestId, code}` with no
  // `recipes` key, so ANY table here would read as "nothing matched and nothing
  // is in stock" — the single worst thing this app can say.
  assert.equal(dom.byData('slot').length, 0, 'a 503 rendered slot cards');
  assert.equal(dom.byData('unjoined').length, 0, 'a 503 rendered an unjoined count');
  assert.equal(dom.byData('join-summary').length, 0, 'a 503 rendered a zero join summary');
  assert.equal(dom.byData('empty-recipes').length, 0, 'a 503 rendered the empty invitation');
  assert.equal(textOf(dom.root).includes('还没有菜谱'), false);
  assert.equal(textOf(dom.root).includes('重试'), false, 'F1\'s failure is permanent; no retry is offered');
  // §2i: a failed load still returns to where the user came from.
  assert.ok(button(dom.root, '返回'), 'the 503 state has no Back affordance');
  unmount();
  dom.restore();
});

test('NO SECOND fail-closed implementation exists: only panels.js says the unavailable sentence', () => {
  const panels = readFileSync(PANELS_SOURCE, 'utf8');
  const source = readFileSync(PROVENANCE_SOURCE, 'utf8');
  const marker = '不代表你的厨房里什么都没有';
  // NON-VACUITY first: if NEITHER file carried the sentence, "only one file has
  // it" would be true for the wrong reason and the gates below would be green
  // while the feature was gone.
  assert.ok(panels.includes(marker), 'panels.js no longer carries the fail-closed sentence');
  assert.equal(source.includes(marker), false, 'provenance.js re-implemented the fail-closed sentence');
  assert.ok(panels.includes("panelState: 'unavailable'"));
  assert.equal(source.includes("panelState: 'unavailable'"), false);
  assert.ok(panels.includes("role: 'source-unavailable'"));
  assert.equal(source.includes("'source-unavailable'"), false);
  // And the view really does import and call the shared one.
  assert.match(
    source,
    /import \{[\s\S]*?sourceUnavailableState[\s\S]*?\} from '\.\.\/panels\.js\?v=__APP_VERSION__'/,
  );
  assert.ok(source.includes('sourceUnavailableState({ error })'));
});

test('the unavailable state is implemented EXACTLY ONCE in the whole app, not just in this view', () => {
  /* The test above compares two files, which is the question *this view* raises.
   * This one is the app-wide claim F1 actually needs: a 503 has one sentence,
   * one `panelState`, and one `role`, and no second copy can appear in any module
   * — including a module this ticket did not write. A new view that wanted to
   * say "your pantry is unreadable" in its own words would be the failure, and a
   * two-file comparison could not see it.

   * The walk is over `app/static/js`, `js/pwa/` included, because the vendored
   * pwa-infra modules are the least likely place to author one and the most
   * embarrassing. */
  const jsDir = join(REPO_ROOT, 'app', 'static', 'js');
  const files = [];
  const walkDir = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) walkDir(full);
      else if (entry.name.endsWith('.js')) files.push(full);
    }
  };
  walkDir(jsDir);
  assert.ok(files.length > 10, `the walk found only ${files.length} modules`);

  // Three separate markers, because a copy could re-implement the state while
  // borrowing one of the three. All three must live in exactly one file.
  const markers = [
    '不代表你的厨房里什么都没有',
    "panelState: 'unavailable'",
    "role: 'source-unavailable'",
  ];
  for (const marker of markers) {
    const holders = files.filter((file) => readFileSync(file, 'utf8').includes(marker));
    assert.deepEqual(
      holders.map((file) => file.slice(REPO_ROOT.length + 1)),
      ['app/static/js/panels.js'],
      `${marker} is implemented in ${holders.length} files`,
    );
  }

  // ... and the one implementation is a named export, so a caller has to import
  // it rather than copy it.
  assert.match(
    readFileSync(PANELS_SOURCE, 'utf8'),
    /export function sourceUnavailableState\(/,
    'the shared implementation is no longer exported',
  );
});

test('an ordinary failure offers a retry, and an empty list is the SHARED empty state', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom, null, {
    other: () => json({ requestId: 'req-500', code: 'boom' }, 500),
  });
  const failure = dom.byData('provenance-error')[0];
  assert.ok(failure, 'a 500 rendered no error state');
  assert.equal(failure.dataset.panelState, 'error');
  assert.ok(textOf(failure).includes('boom'), 'the server code is not shown');
  assert.equal(dom.byData('empty-recipes').length, 0, 'a failure rendered the empty invitation');
  assert.ok(button(dom.root, '重新检查'), 'no retry affordance');
  unmount();
  dom.restore();

  const emptyDom = install();
  const unmountEmpty = await mountFrozen(emptyDom, listPayload([]));
  const empty = emptyDom.byData('empty-recipes')[0];
  assert.ok(empty, 'a successful empty list rendered no empty state');
  assert.equal(empty.dataset.panelState, 'empty');
  assert.equal(empty.className.includes('error'), false, 'the empty state is styled as an error');
  unmountEmpty();
  emptyDom.restore();
});

/* ==========================================================================
   6. The two repair actions
   ========================================================================== */

test('重新解析未匹配项 posts once, prints the five numbers and F2\'s conflict, then re-reads', async () => {
  const dom = install();
  const posts = [];
  const unmount = await mountFrozen(dom, FROZEN, {
    other: (url, init) => {
      if (init.method === 'POST') {
        posts.push({ url, method: init.method });
        return json({
          reconsidered: 32,
          resolved: 9,
          stillUnresolved: 23,
          staleReset: 0,
          duplicateSlotConflicts: 1,
          conflicts: [
            {
              recipeNote: '拌空心菜',
              ingredientIndex: 4,
              rawValue: '香菇',
              pantryItemId: 12,
              canonicalName: '香菇',
              reason: 'duplicate_slot_conflict',
            },
          ],
        });
      }
      return json({}, 200);
    },
  });
  const resolve = dom.byData('resolve')[0];
  assert.equal(resolve.textContent, RESOLVE_LABEL);
  assert.equal(resolve.disabled, false);

  const before = fetches.length;
  resolve.dispatchEvent({ type: 'click' });
  await settle();

  assert.equal(posts.length, 1, 'the re-resolve did not post exactly once');
  assert.equal(posts[0].url, '/api/recipes/resolve');
  const report = dom.byData('resolve-report')[0];
  assert.ok(report, 'the resolve report was not rendered');
  const pairs = fieldPairs(report);
  assert.equal(pairs.get('重新考虑'), '32');
  assert.equal(pairs.get('解析成功'), '9');
  assert.equal(pairs.get('仍然没匹配上'), '23');
  assert.equal(pairs.get('清掉的过期映射'), '0');
  assert.equal(pairs.get('同一道菜两格撞同一个 Pantry Item（F2）'), '1');
  // F2: the conflict is SHOWN, with its own reason attached, never folded away
  // into the count.
  const conflict = dom.byData('resolve-conflict')[0];
  assert.ok(conflict, 'F2\'s conflict was not shown');
  assert.equal(conflict.dataset.reason, 'duplicate_slot_conflict');
  assert.ok(textOf(conflict).includes('拌空心菜'), textOf(conflict));
  assert.ok(textOf(conflict).includes('#12'), textOf(conflict));
  // A pass changed the mapping table, so the screen re-reads — deliberately,
  // and only because the user tapped something. The other non-POST request is
  // `api.js`'s CSRF token fetch, which every mutation needs and which the
  // fixture answers; the assertion is written to count LIST reads, not to hide
  // that one.
  const after = fetches.slice(before);
  assert.equal(after.filter((entry) => entry.init.method === 'POST').length, 1);
  assert.equal(after.filter((entry) => entry.url.includes('/api/recipes?')).length, 1, 'not exactly one re-read');
  assert.equal(after.filter((entry) => entry.url.includes('/api/session')).length, 1, 'the CSRF token fetch');
  unmount();
  dom.restore();
});

test('a clean pass says so, rather than rendering an empty conflict box', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom, FROZEN, {
    other: () =>
      json({ reconsidered: 32, resolved: 9, stillUnresolved: 23, staleReset: 0, duplicateSlotConflicts: 0, conflicts: [] }),
  });
  dom.byData('resolve')[0].dispatchEvent({ type: 'click' });
  await settle();
  assert.equal(dom.byData('resolve-conflicts').length, 0);
  assert.ok(textOf(dom.byData('resolve-no-conflicts')[0]).includes('没有出现重复占用的冲突'));
  unmount();
  dom.restore();
});

test('a 409 resolve_in_flight is shown AS that, and invites no second pass', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom, FROZEN, {
    other: () => json({ requestId: 'req-409', code: 'resolve_in_flight' }, 409),
  });
  dom.byData('resolve')[0].dispatchEvent({ type: 'click' });
  await settle();

  const state = dom.byData('resolve-in-flight')[0];
  assert.ok(state, 'the 409 was not shown as an in-flight resolve');
  assert.equal(state.dataset.panelState, 'error');
  const text = textOf(state);
  assert.ok(text.includes('resolve_in_flight'), text);
  assert.ok(text.includes('req-409'), text);
  assert.ok(text.includes('已经有一遍'), text);
  assert.equal(/重试|再试一次/.test(text), false, 'a second pass was invited while one is running');
  assert.equal(dom.byData('resolve-report').length, 0, 'a failure claimed a report');
  // ... and the control is usable again.
  assert.equal(dom.byData('resolve')[0].disabled, false);
  unmount();
  dom.restore();
});

test('offline, both write controls are disabled, say why, and write nothing (F5)', async () => {
  const dom = install({ online: false });
  const unmount = await mountFrozen(dom);
  const resolve = dom.byData('resolve')[0];
  assert.equal(resolve.disabled, true, 'the re-resolve control is enabled while offline');
  assert.equal(dom.byData('write-reason')[0].textContent, OFFLINE_WRITE_REASON);
  assert.ok(dom.byData('offline')[0], 'the offline banner is missing');

  const before = fetches.length;
  resolve.dispatchEvent({ type: 'click' });
  await settle();
  assert.equal(fetches.length, before, 'an offline tap issued a request');
  // Every material slot's control is disabled too, not just the re-resolve one.
  const opens = dom.byData('remap-open');
  assert.ok(opens.length > 0, 'no material card offered a manual control at all');
  assert.equal(
    opens.every((node) => node.disabled),
    true,
    'a manual control is live while offline',
  );
  // A 调料 card has no control at all, and says why.
  assert.equal(opens.filter((node) => node.dataset.note === '茶碗蒸').length, 0);
  assert.ok(dom.byData('remap-unavailable').length > 0, 'a 调料 card does not say why it has no control');

  dom.setOnline(true);
  await settle();
  assert.equal(resolve.disabled, false, 'coming back online did not re-enable the control');
  unmount();
  dom.restore();
});

test('a read-only install disables the writes and names the reason', async () => {
  const dom = install();
  setResponder({ session: { readOnly: true } });
  await api.initApi();
  const unmount = mountProvenance(dom.root);
  await settle();
  assert.ok(dom.byData('write-reason')[0].textContent.includes('只读'));
  assert.equal(dom.byData('resolve')[0].disabled, true);
  unmount();
  dom.restore();
});

test('the manual re-map searches the catalog, writes through PUT, and says it is a delete + insert', async () => {
  const dom = install();
  const writes = [];
  const unmount = await mountFrozen(dom, FROZEN, {
    other: (url, init) => {
      // A read carries an explicit `GET`; only a real mutation is recorded.
      if (init.method && init.method !== 'GET') writes.push({ url, method: init.method, body: init.body });
      if (url.includes('/api/pantry/items')) {
        return json({
          catalogRevision: 'sha256:1da7ba7e0a28',
          items: [
            { id: 14, canonicalName: 'Cascadian Farm Broccoli Florets Organic, 10 Oz', category: '1.2', area: null, variants: [] },
            { id: 118, canonicalName: '365 by Whole Foods Market, Organic Broccoli Florets', category: '1.2v', area: null, variants: [] },
          ],
        });
      }
      return json({ mapping: { recipeNote: '花蛤拌饭', ingredientIndex: 0, pantryItemId: 30 } });
    },
  });

  dom.byData('remap-open').find((node) => node.dataset.note === '花蛤拌饭').dispatchEvent({ type: 'click' });
  const picker = dom.byData('remap-picker')[0];
  assert.equal(picker.getAttribute('hidden'), null, 'the picker did not open');
  assert.ok(picker.className.includes('form-sheet'), 'the vendored height cap is gone');
  assert.ok(textOf(dom.byData('remap-target')[0]).includes('花蛤拌饭 第 0 格'));
  // F6: the delete + insert is stated BEFORE the user writes anything.
  assert.ok(textOf(picker).includes('F6'), 'the pick does not say it is a delete + insert');
  // A slot with no hand fix offers nothing to clear.
  assert.equal(dom.byData('remap-clear')[0].getAttribute('hidden'), '');

  const query = dom.byData('remap-query')[0];
  assert.equal(query.value, '茼蒿', 'the query is not prefilled with the slot name');
  query.value = '西兰花';
  const before = fetches.length;
  dom.byData('remap-search')[0].dispatchEvent({ type: 'click' });
  await settle();

  const search = fetches.slice(before).find((entry) => entry.url.includes('/api/pantry/items'));
  assert.equal(search.url, '/api/pantry/items?q=%E8%A5%BF%E5%85%B0%E8%8A%B1&limit=20');
  const items = dom.byData('remap-item');
  assert.equal(items.length, 2);
  assert.equal(items[0].dataset.itemId, '14');
  assert.equal(items[0].textContent, 'Cascadian Farm Broccoli Florets Organic, 10 Oz · #14 · 类别 1.2');

  items[0].dispatchEvent({ type: 'click' });
  await settle();
  const put = writes.find((entry) => entry.method === 'PUT');
  assert.ok(put, 'no PUT was issued');
  // The basename travels as ONE path segment, percent-encoded — never joined to
  // a path here (D2): the server resolves it against its own index.
  assert.equal(put.url, `/api/recipes/${encodeURIComponent('花蛤拌饭')}/ingredients/0/mapping`);
  assert.equal(put.url.split("/").length, 7, 'the basename leaked a path separator');
  assert.deepEqual(JSON.parse(put.body), { pantryItemId: 14 });
  assert.ok(textOf(dom.byData('remap-status')[0]).includes('已手工指定'));
  unmount();
  dom.restore();
});

test('opening the picker scrolls it into view, because the sheet is ~70 cards below the control', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  // The fake DOM has no `scrollIntoView` — a layout call this app has never
  // needed — so the stub is what makes the assertion observable rather than
  // vacuous. If the view stopped calling it, the count would stay 0.
  const picker = dom.byData('remap-picker')[0];
  let scrolled = 0;
  picker.scrollIntoView = () => {
    scrolled += 1;
  };
  dom.byData('remap-open').find((node) => node.dataset.note === '花蛤拌饭').dispatchEvent({ type: 'click' });
  assert.equal(scrolled, 1, 'the picker opened without being brought into view');
  // The guard is load-bearing too: with the method absent the view must not
  // throw, and the picker must still be open.
  delete picker.scrollIntoView;
  dom.byData('remap-open').find((node) => node.dataset.note === '烤鲭鱼').dispatchEvent({ type: 'click' });
  assert.equal(picker.getAttribute('hidden'), null);
  unmount();
  dom.restore();
});

test('the picker is dismissed by 关闭 without a request, and a hung search re-enables its control', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  dom.byData('remap-open').find((node) => node.dataset.note === '花蛤拌饭').dispatchEvent({ type: 'click' });
  const before = fetches.length;
  button(dom.byData('remap-picker')[0], '关闭').dispatchEvent({ type: 'click' });
  assert.equal(dom.byData('remap-picker')[0].getAttribute('hidden'), '', 'the picker stayed open');
  assert.equal(fetches.length, before, 'dismissing the picker issued a request');
  unmount();
  dom.restore();

  const stalled = install();
  const unmountStalled = await mountFrozen(stalled, FROZEN, { other: () => new Promise(() => {}) });
  stalled.byData('remap-open').find((node) => node.dataset.note === '花蛤拌饭').dispatchEvent({ type: 'click' });
  const search = stalled.byData('remap-search')[0];
  search.dispatchEvent({ type: 'click' });
  await settle(2);
  assert.equal(search.disabled, true, 'the search control was not disabled in flight');
  stalled.runTimers();
  assert.equal(search.disabled, false, 'the watchdog never re-enabled the search control');
  assert.ok(textOf(stalled.byData('remap-status')[0]).includes('没有回来'));
  unmountStalled();
  stalled.restore();
});

test('the manual re-map reports F2\'s duplicate-slot conflict and claims no write', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom, FROZEN, {
    // `init.method` is 'GET' on a read, not absent: `api.js` always sends an
    // explicit method, so a bare truthiness test here would answer a search
    // with the write's refusal.
    other: (url, init) =>
      init.method && init.method !== 'GET'
        ? json({ requestId: 'req-dup', code: 'duplicate_slot_conflict' }, 409)
        : url.includes('/api/pantry/items')
          ? json({ items: [{ id: 30, canonicalName: '茼蒿', category: '1.2', area: null, variants: [] }] })
          : json({}, 200),
  });
  dom.byData('remap-open').find((node) => node.dataset.note === '花蛤拌饭').dispatchEvent({ type: 'click' });
  dom.byData('remap-search')[0].dispatchEvent({ type: 'click' });
  await settle();
  dom.byData('remap-item')[0].dispatchEvent({ type: 'click' });
  await settle();

  const conflict = dom.byData('remap-conflict')[0];
  assert.ok(conflict, 'F2\'s 409 was not shown as a conflict');
  const text = textOf(conflict);
  assert.ok(text.includes('duplicate_slot_conflict'), text);
  assert.ok(text.includes('F2'), text);
  assert.ok(text.includes('没有写入'), 'the failure did not say nothing was written');
  assert.equal(dom.byData('remap-status')[0].textContent, '', 'a failure claimed success');
  unmount();
  dom.restore();
});

test('an empty catalog search is a real answer, in the SHARED empty state', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom, FROZEN, {
    other: (url) => (url.includes('/api/pantry/items') ? json({ catalogRevision: 'x', items: [] }) : json({})),
  });
  dom.byData('remap-open').find((node) => node.dataset.note === '花蛤拌饭').dispatchEvent({ type: 'click' });
  dom.byData('remap-search')[0].dispatchEvent({ type: 'click' });
  await settle();
  const empty = dom.byData('remap-empty')[0];
  assert.ok(empty, 'an empty search rendered no state');
  assert.equal(empty.dataset.panelState, 'empty');
  assert.equal(empty.className.includes('error'), false, 'an empty result is styled as a failure');
  unmount();
  dom.restore();
});

test('a blank query is refused client-side, and never becomes a request', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  dom.byData('remap-open').find((node) => node.dataset.note === '花蛤拌饭').dispatchEvent({ type: 'click' });
  dom.byData('remap-query')[0].value = '   ';
  const before = fetches.length;
  dom.byData('remap-search')[0].dispatchEvent({ type: 'click' });
  await settle();
  assert.equal(fetches.length, before, 'a blank query was sent to the server');
  assert.ok(dom.byData('remap-status')[0].textContent.includes('先写一个'));
  unmount();
  dom.restore();
});

test('a hand-fixed slot offers the clear, and clearing is a DELETE to the same slot', async () => {
  const dom = install();
  const writes = [];
  const unmount = await mountFrozen(dom, FROZEN, {
    other: (url, init) => {
      if (!init.method || init.method === 'GET') return json({});
      writes.push({ url, method: init.method });
      return json({ mapping: null });
    },
  });
  dom
    .byData('remap-open')
    .find((node) => node.dataset.note === 'Easy Fragrant Fried Rice' && node.dataset.slot === '0')
    .dispatchEvent({ type: 'click' });
  assert.equal(dom.byData('remap-clear')[0].getAttribute('hidden'), null, 'a hand fix offers no way to clear it');
  dom.byData('remap-clear')[0].dispatchEvent({ type: 'click' });
  await settle();
  const del = writes.find((entry) => entry.method === 'DELETE');
  assert.ok(del, 'no DELETE was issued');
  assert.ok(del.url.endsWith('/ingredients/0/mapping'), del.url);
  assert.ok(textOf(dom.byData('remap-status')[0]).includes('已清除'));
  unmount();
  dom.restore();
});

test('a hung re-resolve re-enables its control instead of freezing the view (§8 story 54)', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom, FROZEN, { other: () => new Promise(() => {}) });
  const resolve = dom.byData('resolve')[0];
  resolve.dispatchEvent({ type: 'click' });
  await settle(2);
  assert.equal(resolve.disabled, true, 'the control was not disabled in flight');
  dom.runTimers();
  assert.equal(resolve.disabled, false, 'the watchdog never re-enabled the control');
  assert.ok(dom.byData('resolve-stalled')[0], 'a stalled request reported nothing');
  unmount();
  dom.restore();
});

/* ==========================================================================
   8. §9.13.4's per-line Stock Join table
   ========================================================================== */

test('the table renders every line, in note order, with the tier counts in numbers', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  const table = dom.byData('join-table')[0];
  assert.ok(table, 'no Stock Join table rendered');

  // The counts are the join's own, and they are three NAMED numbers rather than
  // a total: "3 of 45" is a health signal and "45" alone is not.
  const pairs = fieldPairs(table);
  assert.equal(pairs.get('join 看到的行数'), '7');
  assert.equal(pairs.get('其中没对上的（服务器计数）'), '2');
  assert.equal(pairs.get('第一层 精确名 命中'), '1');
  assert.equal(pairs.get('第二层 basename 命中'), '2');
  assert.equal(pairs.get('第三层 line_overrides.yaml 命中'), '2');

  // One card per line, in `lineIndex` order — a row's position on screen is its
  // position in the note, which is where a user looks for it.
  const rows = dom.byData('join-line');
  assert.equal(rows.length, 7);
  assert.deepEqual(rows.map((node) => Number(node.dataset.line)), [360, 372, 480, 500, 511, 540, 561]);
  assert.equal(dom.byData('join-lines')[0].dataset.count, '7');
  unmount();
  dom.restore();
});

test('a row shows the raw line, the product core, the state, and the tier that fired', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  const basename = dom.byData('join-line').find((node) => node.dataset.line === '360');

  // The line as written and the core the index was asked with are DIFFERENT
  // strings, and the difference is the whole reason the basename tier exists.
  assert.equal(fieldValue(basename, '原文 text'), '空心菜嫩苗 0.95-1.05 磅');
  assert.equal(fieldValue(basename, '规范化 product core'), '空心菜嫩苗 0.95-1.05 磅');
  assert.equal(fieldValue(basename, 'stockJoinState'), JOIN_STATE_LABELS.joined);
  assert.equal(basename.dataset.state, 'joined');
  assert.equal(fieldValue(basename, '哪一层命中的'), 'tier 2 · basename');
  assert.equal(fieldValue(basename, 'Pantry Item'), '#83');
  // `overrideKey` rides on EVERY row, not only a miss: it is the string a paste
  // has to reproduce, and a reader comparing it with the file needs it on a hit.
  assert.equal(fieldValue(basename, '覆盖表里的键 overrideKey'), '空心菜嫩苗 0.95-1.05 磅');
  assert.equal(basename.dataset.overrideKey, '空心菜嫩苗 0.95-1.05 磅');

  // The override-tier hit says so in words, not only in a number.
  const override = dom.byData('join-line').find((node) => node.dataset.line === '480');
  assert.equal(fieldValue(override, '哪一层命中的'), 'tier 3 · line_overrides.yaml，靠 line_overrides.yaml');
  assert.equal(override.dataset.state, 'override');
  assert.equal(fieldValue(override, 'Pantry Item'), '#48');
  unmount();
  dom.restore();
});

test('a duplicate catalog name shows BOTH candidate ids, never one', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  const duplicate = dom.byData('join-line').find((node) => node.dataset.line === '372');

  // The real `organic 1% milk` collision. One id here would have been a winner
  // picked by insertion order, and the loser would have vanished with no
  // diagnostic — the one failure a name lookup must never have.
  assert.deepEqual(duplicate.dataset.itemIds.split(','), ['90', '116']);
  assert.equal(fieldValue(duplicate, 'Pantry Item'), '#90、#116');
  unmount();
  dom.restore();
});

test('a miss names the key to paste, and the guidance is the SERVER\'s sentence verbatim', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  const miss = dom.byData('join-line').find((node) => node.dataset.line === '511');

  // The key is the normalized core, and it is the exact string the override file
  // is keyed by — a raw line pasted as a key never fires and the loader refuses
  // to start on one.
  assert.equal(fieldValue(miss, '覆盖表里的键 overrideKey'), 'manukora manuka honey mgo 50+');
  assert.equal(miss.dataset.overrideKey, 'manukora manuka honey mgo 50+');
  // The server names the KEY and refuses to invent the value. `overrideName` is
  // null here, so the row shows no value at all rather than a plausible one.
  assert.equal(miss.dataset.overrideName, '');
  assert.equal(fieldValue(miss, '覆盖表里的值 overrideName'), null);
  assert.equal(miss.dataset.tier, '0');
  assert.equal(fieldValue(miss, 'Pantry Item'), null, 'a miss published an id');
  // TIER 0 IS NOT A TIER. All three ran and adopted nothing, which is a
  // different fact from a slot nothing has looked at yet.
  assert.ok(fieldValue(miss, '哪一层命中的').includes('没有层级命中'));
  assert.equal(/tier 0 ·/.test(fieldValue(miss, '哪一层命中的')), false);

  // **THE GUIDANCE IS PRINTED, NOT COMPOSED.** The rendered text is the payload's
  // string, character for character. A client that built its own sentence from
  // `overrideKey` plus a template would differ here, which is the assertion.
  const repair = dom.byData('join-repair').find((node) => node.parentNode === miss);
  assert.ok(repair, 'the miss row carries no repair guidance');
  assert.equal(repair.textContent, FROZEN_JOIN.lines.find((l) => l.lineIndex === 511).repairHint);
  assert.ok(repair.textContent.includes('manukora manuka honey mgo 50+'), 'the key is not named');
  assert.ok(repair.textContent.includes('app/pantry/line_overrides.yaml'), 'the file is not named');
  // The refusal, stated by the server and not softened here.
  assert.ok(repair.textContent.includes('不替你猜'), repair.textContent);

  // NON-VACUITY: a resolved row has nothing to repair and says nothing. A hint on
  // a joined row would invite a repair of a line the join already explained.
  const resolved = dom.byData('join-line').filter((node) => node.dataset.state !== 'unresolved');
  assert.equal(resolved.length, 5);
  assert.equal(
    dom.byData('join-repair').length,
    2,
    'exactly the two misses carry guidance',
  );
  unmount();
  dom.restore();
});

test('a stale override names the name it tried, and the remedy differs from a plain miss', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  const stale = dom.byData('join-line').find((node) => node.dataset.line === '561');

  assert.equal(stale.dataset.overrideName, 'Trader Joes Dark Chocolate Peanut Butter Cups');
  assert.equal(
    fieldValue(stale, '覆盖表里的值 overrideName'),
    'Trader Joes Dark Chocolate Peanut Butter Cups',
  );
  const repair = dom.byData('join-repair').find((node) => node.parentNode === stale);
  // A stale entry is a DIFFERENT repair from a missing one: fix or delete the
  // existing entry. One sentence for both misses would be wrong about one of them.
  assert.ok(repair.textContent.includes('查不到'), repair.textContent);
  assert.equal(
    repair.textContent,
    FROZEN_JOIN.lines.find((l) => l.lineIndex === 561).repairHint,
    'the stale-override guidance was composed rather than sent',
  );
  assert.equal(/不替你猜/.test(repair.textContent), false, 'a stale name IS known; do not refuse to name it');
  unmount();
  dom.restore();
});

test('the table states the join\'s weakness in the server\'s words, and never invents a name', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  const guidance = textOf(dom.byData('join-guidance')[0]);

  // Printed verbatim. A client-composed caveat would differ, and the difference
  // is the assertion — the sentence is about a file format only the server reads.
  assert.equal(guidance, FROZEN_JOIN.guidance);
  // Three tiers, and NO RE-RESOLUTION PASS: there is no materialized stock-join
  // table to re-sweep, so a miss does not repair itself. That is the fact a user
  // needs before deciding a `have-been-buying` chip is wrong.
  assert.ok(guidance.includes('没有第二次重扫'), guidance);
  assert.ok(guidance.includes('app/pantry/line_overrides.yaml'), guidance);
  assert.ok(guidance.includes('家里没有'), 'the table does not keep a miss from reading as a verdict');
  // The section's own static caveat stays: it is the design statement, and a
  // pre-existing test pins its wording.
  assert.ok(textOf(dom.byData('join-caveat')[0]).includes('没有第二次重扫'));
  unmount();
  dom.restore();
});

test('joinTierText names all three tiers and refuses to print tier 0 as one', () => {
  assert.equal(joinTierText(1, 'joined'), 'tier 1 · 精确名 exact');
  assert.equal(joinTierText(2, 'joined'), 'tier 2 · basename');
  assert.equal(joinTierText(3, 'override'), 'tier 3 · line_overrides.yaml，靠 line_overrides.yaml');
  // No tier fired. All three ran and adopted nothing — NOT "tier 0 resolved".
  const none = joinTierText(0, 'unresolved');
  assert.ok(none.includes('没有层级命中'), none);
  assert.equal(/tier 0 ·/.test(none), false);
  // A value the server did not send is named as unrecognised, never coerced.
  assert.ok(joinTierText(9, 'joined').includes('没见过的 join 层级'), joinTierText(9, 'joined'));
  assert.ok(joinTierText(undefined, 'joined').includes('没有层级命中'));
  assert.ok(joinTierText('nonsense', 'joined').includes('没有层级命中'));
});

test('F7 survives the table: toggling 调试 repaints it from the payload with ZERO requests', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  assert.equal(dom.byData('join-line').length, 7);

  const before = fetches.length;
  setDebugEnabled(true);
  await settle();
  // The table is a field on the response this view already read, so the toggle
  // repaints it like everything else here and asks for nothing.
  assert.equal(fetches.length, before, 'toggling 调试 fetched the join table (F7: render-only)');
  assert.equal(dom.byData('join-line').length, 7, 'the repaint lost rows');
  assert.equal(dom.byData('join-table').length, 1, 'the repaint lost the table');
  assert.equal(dom.byData('join-repair').length, 2, 'the repaint lost the miss guidance');
  // And the repaint is a repaint, not a no-op: the provenance line is what the
  // toggle is FOR, and the table is repainted in the same pass.
  assert.equal(dom.byData('provenance').length, 8, 'the toggle did not repaint the chips');
  // Every fetches entry is the one list read the mount made. A second route for
  // the table would show up here as a URL the view should not have needed.
  assert.deepEqual(
    [...new Set(fetches.map((entry) => entry.url))],
    ['/api/recipes?strict=0'],
    'the view opened a data path the payload already carried',
  );

  setDebugEnabled(false);
  await settle();
  assert.equal(fetches.length, before, 'turning 调试 off made a request');
  assert.equal(dom.byData('join-line').length, 7);
  unmount();
  dom.restore();
});

test('a 503 paints the ONE unavailable panel and NO table: there is nothing to render', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom, null, {
    other: () => json({ requestId: 'req-join-503', code: 'pantry_stock_unreadable' }, 503),
  });

  // A 503 body is exactly `{requestId, code}`. There is no `stockJoin` in it, and
  // a table left over from a previous good paint would be a STALE table beside a
  // refusal to read the source it came from — the worse of the two failures.
  const panel = dom.byData('source-unavailable')[0];
  assert.ok(panel, 'the 503 painted no unavailable state');
  assert.equal(panel.dataset.panelState, 'unavailable');
  assert.ok(textOf(panel).includes('pantry_stock_unreadable'));
  assert.equal(dom.byData('join-table').length, 0, 'a 503 rendered a join table');
  assert.equal(dom.byData('join-line').length, 0, 'a 503 rendered join rows');
  assert.equal(dom.byData('join-guidance').length, 0, 'a 503 rendered the guidance');
  assert.equal(dom.byData('join-table-absent').length, 0, 'a 503 rendered the absent-field note');
  assert.equal(dom.byData('slot').length, 0, 'a 503 rendered slot cards');
  assert.equal(textOf(dom.root).includes('重试'), false, 'F1\'s failure is permanent; no retry is offered');

  // ... and a 503 that arrives AFTER a good paint still clears the table.
  const dom2 = install();
  const unmount2 = await mountFrozen(dom2);
  assert.equal(dom2.byData('join-line').length, 7);
  setResponder({ list: null, other: () => json({ requestId: 'r2', code: 'pantry_stock_unreadable' }, 503) });
  dom2.window.dispatchEvent({ type: 'online' });
  await settle();
  assert.equal(dom2.byData('join-line').length, 0, 'a stale table survived a 503');
  assert.equal(dom2.byData('source-unavailable').length, 1);
  unmount2();
  dom2.restore();
  unmount();
  dom.restore();
});

test('a response with no stockJoin says the field is absent, not that the join saw nothing', async () => {
  const dom = install();
  // A server that predates this view. The honest rendering is a named absence:
  // an empty table here would read as "the join saw no lines", which is the one
  // blank this table exists to prevent.
  const { stockJoin, ...withoutTable } = FROZEN;
  const unmount = await mountFrozen(dom, withoutTable);
  assert.equal(dom.byData('join-table').length, 0, 'a table was invented from an absent field');
  const note = dom.byData('join-table-absent')[0];
  assert.ok(note, 'an absent field rendered nothing at all');
  assert.ok(note.textContent.includes('没有 stockJoin'), note.textContent);
  assert.equal(/0 行|没有任何产品行/.test(textOf(note)), false, 'an absence was rendered as a count');
  // And the rest of the screen is unaffected: the slots still render.
  assert.equal(dom.byData('slot').length, 8);
  void stockJoin;
  unmount();
  dom.restore();
});

test('a clean join with zero misses still renders the table and says the count is 0', async () => {
  const dom = install();
  const clean = {
    ...FROZEN_JOIN,
    lineCount: 5,
    unjoinedCount: 0,
    tierCounts: { exact: 1, basename: 2, override: 2 },
    lines: FROZEN_JOIN.lines.filter((line) => line.stockJoinState !== 'unresolved'),
  };
  const unmount = await mountFrozen(dom, listPayload(FROZEN.recipes, { ...FROZEN, stockJoin: clean }));
  const pairs = fieldPairs(dom.byData('join-table')[0]);
  // "0" is the number that trains the eye to skip the field, so it is printed.
  assert.equal(pairs.get('其中没对上的（服务器计数）'), '0');
  assert.equal(dom.byData('join-line').length, 5);
  assert.equal(dom.byData('join-repair').length, 0);
  // No misses and no empty state: the table is populated, so nothing is missing.
  assert.equal(dom.byData('join-lines-empty').length, 0);
  unmount();
  dom.restore();
});

test('an empty Pantry.md renders a named empty table rather than nothing', async () => {
  const dom = install();
  const empty = { lineCount: 0, unjoinedCount: 0, tierCounts: { exact: 0, basename: 0, override: 0 }, guidance: FROZEN_JOIN.guidance, lines: [] };
  const unmount = await mountFrozen(dom, listPayload(FROZEN.recipes, { ...FROZEN, stockJoin: empty }));
  const emptyState = dom.byData('join-lines-empty')[0];
  assert.ok(emptyState, 'an empty join rendered nothing at all');
  // The distinction this table exists to keep: no product lines is a real
  // answer, and it is NOT the same as "the source is unreadable" (which is a 503
  // panel) nor as "the field is absent" (which is a different note above).
  assert.ok(emptyState.textContent.includes('没有看到任何产品行'), emptyState.textContent);
  assert.equal(dom.byData('join-line').length, 0);
  unmount();
  dom.restore();
});

test('the table renders vault- and catalog-sourced text as textContent, never as markup', async () => {
  const dom = install();
  // A line whose text is markup. `el()` is textContent-only, so this must land
  // on screen as the literal characters and must not create an element.
  const hostile = {
    ...FROZEN_JOIN,
    lines: [
      {
        ...FROZEN_JOIN.lines[0],
        lineIndex: 999,
        text: '<img src=x onerror="globalThis.__pwned=1"><b>bold</b>',
        core: '<script>globalThis.__pwned=2</script>',
        stockJoinState: 'unresolved',
        tier: 0,
        pantryItemIds: [],
        overrideKey: '<img src=x>',
        overrideName: null,
        repairHint: '把 <b>键</b> 写进 overrides',
      },
    ],
    lineCount: 1,
    unjoinedCount: 1,
  };
  const unmount = await mountFrozen(dom, listPayload(FROZEN.recipes, { ...FROZEN, stockJoin: hostile }));
  const row = dom.byData('join-line')[0];
  assert.equal(row.dataset.line, '999');
  assert.equal(fieldValue(row, '原文 text'), '<img src=x onerror="globalThis.__pwned=1"><b>bold</b>');
  // No element was created from any of it.
  assert.equal(dom.root.querySelectorAll('img').length, 0);
  assert.equal(dom.root.querySelectorAll('b').length, 0);
  assert.equal(dom.root.querySelectorAll('script').length, 0);
  assert.equal(globalThis.__pwned, undefined);
  // The guidance is text too, for the same reason.
  assert.equal(dom.byData('join-repair')[0].textContent, '把 <b>键</b> 写进 overrides');
  unmount();
  dom.restore();
});

test('F17 and the path rule hold on the table: no cookable, and no path anywhere', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  const text = textOf(dom.root);

  assert.equal(/cookable/.test(text), false, 'the table names a boolean');
  for (const node of walk(dom.root)) {
    for (const [key, value] of Object.entries(node.dataset)) {
      assert.equal(/cookable/i.test(`${key}=${value}`), false, `${key}=${value}`);
    }
  }
  // `section` is the note's own heading NUMBER, published on purpose — it is how
  // a reader finds the line. The directory the note lives in is server-owned
  // layout and is never on the wire or on screen.
  for (const row of dom.byData('join-line')) {
    assert.match(row.dataset.section, /^\d*$/, row.dataset.section);
  }
  assert.equal(/\/(Users|home|var|opt|private)\//.test(text), false, 'an absolute path is on screen');
  assert.equal(/[\w/一-鿿]+\/Pantry\.md/.test(text), false, 'the note was named with its directory');
  unmount();
  dom.restore();
});

/* ==========================================================================
   7. F17, paths, §2i, and teardown
   ========================================================================== */

test('F17: no cookable substring in the rendered text, the request URLs, or any dataset', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  assert.equal(/cookable/.test(textOf(dom.root)), false, 'the rendered text contains `cookable`');
  for (const entry of fetches) assert.equal(/cookable/.test(entry.url), false, entry.url);
  for (const node of walk(dom.root)) {
    for (const [key, value] of Object.entries(node.dataset)) {
      assert.equal(/cookable/.test(key), false, `a dataset key names it: ${key}`);
      assert.equal(/cookable/.test(String(value)), false, `a dataset value names it: ${key}=${value}`);
    }
    assert.equal(/cookable/.test(String(node.className || '')), false, 'a class name says it');
  }
  unmount();
  dom.restore();
});

test('no path is displayed: not the vault root, and not the server-relative one either', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  const text = textOf(dom.root);
  // `notePath` is published and this view deliberately does not print it; the
  // recipe NAME is the identity here. Nothing filesystem-shaped may appear.
  assert.equal(text.includes('Hobbies/'), false, 'a vault-relative path is on screen');
  assert.equal(/\/(Users|home|var|tmp|opt|private)\//.test(text), false, 'an absolute path is on screen');
  // NON-VACUITY: the fixture really does carry a notePath, so "no path rendered"
  // is a decision rather than an accident of the fixture.
  assert.ok(FROZEN.recipes[0].notePath.includes('Hobbies/'), 'the fixture has no path to leak');
  // The pantry note may be NAMED — `panels.js` names it, and this view's join
  // section is about that file — but never with the directory in front of it,
  // which is the half that leaks server layout.
  assert.ok(text.includes('Pantry.md'), 'the join section should name the file it is about');
  assert.equal(/[\w/一-鿿]+\/Pantry\.md/.test(text), false, 'the note was named with its directory');
  unmount();
  dom.restore();
});

test('the Back affordance follows §2i on the happy path and after a failure', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  const back = button(dom.root, '返回');
  assert.ok(back, 'no Back affordance');
  let wentBack = 0;
  dom.history.back = () => {
    wentBack += 1;
  };
  back.dispatchEvent({ type: 'click' });
  assert.equal(wentBack, 1, 'Back did not go back rather than hardcoding #/');
  unmount();
  dom.restore();

  const failed = install();
  setResponder({ other: () => json({ requestId: 'r', code: 'boom' }, 500) });
  const unmountFailed = mountProvenance(failed.root);
  await settle();
  assert.ok(button(failed.root, '返回'), 'a failed load has no Back affordance');
  unmountFailed();
  failed.restore();
});

test('a restored scroll offset is re-applied once the rows exist, not before', async () => {
  const dom = install();
  setResponder({ list: FROZEN });
  dom.history.replaceState({ scrollTop: 640 });
  const asked = [];
  // Model the real clamp: the page is one loading line tall until the rows are
  // painted, so a restore applied at mount lands on 16. That is the whole
  // mechanism the re-application exists for.
  const scrollable = () => (dom.byData('slot').length > 0 ? 4000 : 16);
  dom.window.scrollTo = (x, y) => {
    asked.push(y);
    dom.window.scrollY = Math.min(y, scrollable());
  };
  dom.window.scrollY = 0;
  const router = initRouter({ window: dom.window, views: { provenance: { mount: mountProvenance } } });
  dom.setHash('#/provenance');
  router.reload();
  await settle();

  assert.ok(asked.includes(640), 'the restore target was never attempted');
  assert.equal(dom.window.scrollY, 640, 'the offset was clamped by the still-loading page');
  router.stop();
  dom.restore();
});

test('unmount drops every listener, the TTL poll, and the tree', async () => {
  const dom = install();
  const unmount = await mountFrozen(dom);
  unmount();
  assert.equal(dom.root.textContent, '', 'unmount left the tree behind');
  assert.equal(dom.window.listeners.get('online').size, 0, 'a connectivity listener outlived unmount()');
  assert.equal(dom.window.listeners.get('offline').size, 0);
  assert.equal(
    dom.window.listeners.get('pantry:debug-change').size,
    0,
    'the DEBUG_EVENT listener outlived unmount()',
  );
  assert.equal(dom.pendingTimers(), 0, 'the TTL poll outlived unmount()');
  const after = fetches.length;
  dom.runTimers();
  await settle();
  assert.equal(fetches.length, after, 'a torn-down view still asked the server');
  // And a debug event after unmount paints nothing rather than resurrecting it.
  setDebugEnabled(true);
  await settle();
  assert.equal(dom.root.textContent, '');
  assert.equal(fetches.length, after);
  dom.restore();
});

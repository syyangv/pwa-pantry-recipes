/* #/provenance — 溯源: every Ingredient slot's provenance, and F1's Stock Join.
 *
 * **F7: this screen is a VIEW, not a data path.** It reads the one
 * `GET /api/recipes` the home view already reads, and the 调试 toggle repaints
 * the payload already in hand — no request, no `?debug=1`, no reduced shape,
 * no second response to drift from the first. `prefs.js`'s `DEBUG_EVENT` is the
 * only trigger, exactly as in `home.js`: the listener below re-reads `payload`
 * and calls `paint()` again, and `chips.js` adds its secondary provenance line
 * to the chip in that repaint. So the toggle is observable HERE too, and the
 * zero-request property is assertable on this view rather than only on the list.
 *
 * **The Stock Join is the weakest link in this app, and this is its inspector.**
 * The recipe→ingredient join persists its result in `ingredient_mappings` and
 * can be re-resolved against it; the join between a free-text `Pantry.md` line
 * and a normalized catalog row cannot — there is no materialized stock-join
 * table, so there is nothing to re-resolve against, and a line all three tiers
 * miss is simply ABSENT from `in_stock_ids`. Its absence is not evidence the
 * household does not have the item; the honest consequence is a chip reading
 * `have-been-buying` for something on the shelf. So the weakness is shown, not
 * hidden: the join's own state per slot (`stockJoinState`), the server's own
 * `stockUnjoinedCount`, and a paragraph that names where a miss is repaired.
 *
 * **`stockJoinState` is NOT "do I have it", and this screen never conflates the
 * two.** `matchMethod` describes the recipe→catalog resolution,
 * `stockJoinState` describes whether the *other* join could explain the product
 * this slot landed on, and `inStock` is the shelf. A slot can be `joined` and
 * not in stock (the line exists, the product is in the catalog, the task is
 * done) and a slot can be `unresolved` and in stock, and both are real answers
 * about different facts. §9.13.4 asks "why does this say I have it / why does
 * this say I don't", and merging the three is how a diagnostic becomes a lie.
 *
 * **`tier 0` is rendered as "没有层级命中" and never as a tier number.** The
 * ladder's own miss is `tier 8 · unresolved` — the sweep ran and adopted
 * nothing — which is a DIFFERENT state from a `材料` slot that has no mapping
 * row at all (`app/api/recipes.py`'s `_slot` fallback publishes `matchTier: 0`),
 * where nothing has even looked yet. Printing both as a number is how "we don't
 * know" gets read as "we looked, and it is not there".
 *
 * **THE COMPLETE `rejectedBy` LIST IS RENDERED, NOT `reason` ALONE.** The
 * matcher's audit deliberately keeps every objection
 * (`MatchCandidate.rejected_by`): `好丽友 高笑美芝麻饼干 216 克` (25) is refused by
 * `segment_boundary` AND by `category_family`, and `reason` carries only the
 * first. Recording one would understate what is holding the row back and would
 * make a repair look pointless when the second guard would have refused it
 * anyway. `reason` and the full list are therefore two separately-labelled
 * things, and the count is printed so a one-guard and a two-guard refusal are
 * visibly different.
 *
 * **NO ABSOLUTE PATH, AND NO SERVER-OWNED ONE, IS EVER DISPLAYED.** `notePath`
 * and `relativePath` are vault-relative on the wire and this view prints
 * neither; the recipe's own NAME is the identity here, and the pantry note is
 * named by `panels.js`'s `PANTRY_NOTE_LABEL` (a display label, never a
 * configured path). `el()` is textContent-only, so a Pantry Item name from the
 * catalog and a slot's `rawValue` from the vault can never become markup.
 *
 * **§9.13.4's PER-PANTRY-LINE STOCK JOIN TABLE IS HERE, AND IT IS SERVER TEXT.**
 * `payload.stockJoin` carries every open product line with its raw text, its
 * normalized product core, its `stockJoinState`, the tier that fired, and — for
 * a duplicate catalog name — **both** candidate ids, because §9.13.4 wants the
 * ambiguity visible before it becomes a wrong chip. It is a field on the one
 * response this view already reads, not a second route: the same `StockJoin`
 * object produced `stockUnjoinedCount` and every slot's `stockJoinState`, and a
 * table from a second read could contradict the count printed beside it. So the
 * 调试 toggle repaints it like everything else here, with **zero** requests.
 *
 * **THE SENTENCES IN THAT TABLE ARE SENT, NOT WRITTEN.** The `guidance`
 * paragraph and each miss's `repairHint` arrive as finished text and are
 * printed verbatim; this file composes no sentence about `line_overrides.yaml`.
 * That file is reviewed source whose key must already be NFKC + trim +
 * casefolded or the app refuses to boot, so the rules about it belong to the
 * server that reads it. A miss's `repairHint` therefore also says the honest
 * thing: the server names the *key* to paste and refuses to invent the
 * `canonical_name` beside it, because the join has no tier that could know one
 * and a guess would be the fourth tier in disguise.
 *
 * **F1's 503 IS #21's PANEL, VERBATIM, AND NOT A SECOND COPY.** A 503 body is
 * exactly `{requestId, code}` — there is no `recipes` key — so a view that
 * assumed a list would paint an empty screen, and an empty provenance table
 * next to an empty chip row reads as "nothing matched and nothing is in stock",
 * which is the worst thing this app can say. `sourceUnavailableState()` is the
 * one implementation, imported from `panels.js`; this file composes no
 * unavailable wording of its own, and `tests/js/provenance.test.mjs` asserts
 * both the reuse and the absence of a second one.
 *
 * **F17: no `cookable` boolean, in text, in a dataset, or in a URL.** Not as a
 * class, not as a payload key, not as a query parameter. What is here is the
 * evidence and the server's own `n/total`.
 *
 * **THE TWO REPAIR ACTIONS ARE ACTIONS, NOT RENDERS, AND THAT IS THE POINT.**
 * `重新解析未匹配项` (`POST /api/recipes/resolve`) and the per-row manual re-map
 * (`GET /api/pantry/items?q=` → `PUT`/`DELETE`) are the only requests this view
 * makes beyond its own read, and each one is a deliberate tap by a user who has
 * decided something. Neither is a background refresh and neither happens
 * because a flag changed. Both fail loudly: a 409 "a resolve is already
 * running" is shown AS that, a 409 `duplicate_slot_conflict` is shown with F2's
 * own reason attached (F2: the duplicate is shown, never hidden), and a hand
 * fix is described as the delete + insert it is rather than an overwrite,
 * because F6's trigger makes the row immutable.
 *
 * **A CARD PER SLOT, NOT A `<table>`.** Ten columns do not fit a phone, and
 * §9.4 allows exactly ONE horizontal scroller — the chip row. Each card is one
 * row of the table in every other respect: it carries the recipe, the slot
 * index, and every field as its own `data-*` attribute, so the table is
 * programmatically queryable without a second rendering of it.
 */

import {
  apiFetch,
  isReadOnly,
  parseStrict,
  recipeListCache,
  recipeListCacheKey,
  withStrict,
} from '../api.js?v=__APP_VERSION__';
import { DEBUG_EVENT, isDebugEnabled } from '../prefs.js?v=__APP_VERSION__';
import { goBack, readViewState, saveViewState } from '../router.js?v=__APP_VERSION__';
import { el, trackScroll } from '../dom.js?v=__APP_VERSION__';
import { chip } from '../chips.js?v=__APP_VERSION__';
import { unstickOnTimeout } from '../pwa/unstick-on-timeout.js?v=__APP_VERSION__';
import {
  emptyState,
  errorState,
  isSourceUnavailable,
  loadingState,
  loadPanel,
  sourceUnavailableState,
} from '../panels.js?v=__APP_VERSION__';

/** §9.13.4's re-resolve control. Named so there is one wording to keep. */
export const RESOLVE_LABEL = '重新解析未匹配项';

/** The per-row manual fix. A `材料` slot only — see `app/api/recipes.py`. */
export const REMAP_LABEL = '手工指定';
export const CLEAR_MANUAL_LABEL = '清除手工指定';

/** F5-style offline sentence, for both write paths in this view. */
export const OFFLINE_WRITE_REASON = '离线：这一屏的写入需要连接后进行。';

/** The picker's own note: a hand fix is a delete + insert, never an overwrite. */
export const MANUAL_IS_DELETE_PLUS_INSERT =
  '改一个手工指定不是覆盖：F6 的触发器让这一行不可变，所以服务器是删掉旧的那条、再插一条新的，' +
  '两步各自留一条审计记录。这一屏只做前一步能做的事，不替你改笔记。';

/** §9.13.4's pickers must survive a hung request rather than freeze. */
export const UNSTICK_DELAY_MS = 15000;

/** F11's TTL, restated because the poll below is derived from it. */
export const STOCK_TTL_SECONDS = 30;

const RECIPES_PATH = '/api/recipes';
const RESOLVE_PATH = '/api/recipes/resolve';
const ITEMS_PATH = '/api/pantry/items';
/** §9.16's picker's page size, restated so the client never invents one. */
const PICKER_LIMIT = 20;

/**
 * `stockJoinState`, the three values `app/pantry/stock.py` ships.
 *
 * `JOIN_STATE_LABELS` is what a CARD shows — short, because it is printed on
 * every one of the ~70 slots and a paragraph 70 times is a paragraph nobody
 * reads. `JOIN_STATE_SENTENCES` is the same three facts said once, in the
 * summary above the table, and the "it does not mean you don't have it"
 * sentence is repeated on the cards where it actually bites (an `unresolved`
 * row), because that is the row a user is staring at when they ask why.
 */
export const JOIN_STATE_LABELS = Object.freeze({
  joined: 'joined（库存里有一行对上了）',
  override: 'override（靠 line_overrides.yaml 对上的）',
  unresolved: 'unresolved（没有任何一行库存能解释它）',
});

export const JOIN_STATE_SENTENCES = Object.freeze({
  joined: 'joined：库存里有一行对上了这个 Pantry Item（精确名，或者 basename）。',
  override: 'override：这一行是靠 line_overrides.yaml 对上的，前两层阶梯都没命中。',
  unresolved:
    'unresolved：没有任何一行库存能解释这个 Pantry Item。它因此不在在货名单里，' +
    '但这不等于家里没有 —— 只是这一行没人能对上。',
});

/**
 * What the card shows. A value the server did not send, or sent as something
 * else, is named as such rather than folded into a real state.
 */
export function joinStateLabel(state) {
  return (
    JOIN_STATE_LABELS[state] ||
    `unresolved（服务器给了一个没见过的 join 状态：${String(state)}，所以算进 unresolved）`
  );
}

/**
 * The tier sentence for one slot. `tier 0` is NOT a tier: it is
 * `app/api/recipes.py`'s "this 材料 slot has no mapping row yet" state, and it
 * is the one case where nothing has looked at the slot at all.
 */
export function tierText(slot) {
  const tier = Number(slot && slot.matchTier);
  if (!Number.isFinite(tier) || tier <= 0) {
    return '没有层级命中（tier 0）：这一格还没有映射记录，没有任何阶梯跑过它。';
  }
  const method = String((slot && slot.matchMethod) || '');
  if (method === 'unresolved') {
    return `tier ${tier} · unresolved：阶梯跑完了，什么都没有采用。`;
  }
  return `tier ${tier} · ${method}`;
}

/**
 * Which of the join's three tiers fired, for one Stock Join line.
 *
 * **`tier 0` is NOT a tier**, and it is a *different* fact from the slot-level
 * `tier 0` in `tierText()` above: a `材料` slot with no mapping row is "nothing
 * has looked at this slot yet", while a pantry line at tier 0 is "all three
 * tiers looked and adopted nothing". Both print as a non-tier for the same
 * reason — a bare `0` reads as a tier that fired and resolved to nothing.
 *
 * A value the server did not send, or sent as something else, is named as
 * unrecognised rather than folded into a real tier, for the same reason
 * `joinStateLabel` does it.
 */
export function joinTierText(tier, state) {
  const number = Number(tier);
  if (!Number.isFinite(number) || number <= 0) {
    return '没有层级命中（tier 0）：三层（精确名 → basename → 覆盖表）都跑过了，什么都没有采用。';
  }
  const names = { 1: '精确名 exact', 2: 'basename', 3: 'line_overrides.yaml' };
  const name = names[number];
  if (!name) {
    return `服务器给了一个没见过的 join 层级：${String(tier)}，所以按“没法判断”显示。`;
  }
  const via = state === 'override' ? '，靠 line_overrides.yaml' : '';
  return `tier ${number} · ${name}${via}`;
}

/** `sha256:…` -> a 12-char prefix. A revision, not a path and not a URL. */
function shortRevision(revision) {
  const text = String(revision || '');
  const hex = text.includes(':') ? text.slice(text.indexOf(':') + 1) : text;
  return hex ? hex.slice(0, 12) : '—';
}

/** One `label: value` line, skipped when the server sent nothing to say. */
function field(label, value) {
  if (value === null || value === undefined || value === '') return null;
  return el('div', { class: 'field' }, [
    el('span', { class: 'field__label', text: label }),
    el('span', { class: 'field__value', text: String(value) }),
  ]);
}

function confidenceText(value) {
  const number = Number(value);
  return Number.isFinite(number) ? String(number) : '—';
}

function itemText(id) {
  return id === null || id === undefined || id === '' ? '—' : `#${id}`;
}

/** The one sentence that keeps an `unresolved` row from reading as a verdict. */
export const UNRESOLVED_IS_NOT_ABSENT =
  '这不是“你家里没有”的意思 —— 只是 Pantry.md 里没有哪一行能对上这个 Pantry Item。' +
  '要修它，在 app/pantry/line_overrides.yaml 里加一行被评审过的名字；不要去改你的库。';

/**
 * The audit cell. `reason` and the COMPLETE `rejectedBy` list are two different
 * facts and are rendered as two different facts — see the header note. A
 * matched candidate has an empty `rejectedBy`, and the row says so rather than
 * printing an empty list. The three runs are separated in the TEXT rather than
 * by CSS: `styles.css` is the one stylesheet this app owns and a new rule in it
 * is a rule a `chip--` sibling can regress, so the separator is a character.
 */
function candidateNode(candidate) {
  const rejected = Array.isArray(candidate.rejectedBy) ? candidate.rejectedBy : [];
  return el('p', {
    class: 'muted',
    dataset: {
      role: 'candidate',
      itemId: String(candidate.pantryItemId),
      reason: String(candidate.reason || ''),
      rejectedBy: rejected.join('|'),
    },
  }, [
    el('span', {
      dataset: { role: 'candidate-name' },
      text: `${candidate.canonicalName} · ${itemText(candidate.pantryItemId)} · `,
    }),
    el('span', {
      dataset: { role: 'candidate-meta' },
      text: `类别 ${candidate.pantryCategory} / family ${candidate.family} · 关系 ${candidate.relation} · reason ${candidate.reason} — `,
    }),
    el('span', {
      dataset: { role: 'rejected-by' },
      text: rejected.length
        ? `拒绝原因（共 ${rejected.length} 条，全部列在这里）：${rejected.join('、')}`
        : '拒绝原因：没有（这一行被采用了，或者阶梯没反对它）。',
    }),
  ]);
}

/** The header line: every number the response published, verbatim. */
function metaLine(payload) {
  return [
    `目录版本 ${shortRevision(payload.catalogRevision)}`,
    `库存版本 ${shortRevision(payload.stockRevision)}`,
    `库存最长 ${STOCK_TTL_SECONDS} 秒未更新（F11：你会在 Obsidian 里勾掉一项，最长这么久才反映到 chip 上）`,
    `过期映射 ${Number(payload.staleMappingCount) || 0}`,
    `跳过的笔记 ${Number(payload.skipped) || 0}`,
    `严格模式 ${Number(payload.strict) === 1 ? '开（含调料）' : '关（只算材料）'}`,
  ].join(' · ');
}

/**
 * F1's join, counted from the slots in hand. The server's counter is printed
 * separately because the two are about DIFFERENT things and neither is
 * derivable from the other: `stockUnjoinedCount` counts `Pantry.md` lines no
 * tier explained, while a slot's `stockJoinState` says whether the product that
 * slot landed on was one of them.
 */
export function joinCounts(slots = []) {
  const counts = { joined: 0, override: 0, unresolved: 0, other: 0, total: slots.length };
  for (const slot of slots) {
    const state = slot && slot.stockJoinState;
    if (state === 'joined' || state === 'override' || state === 'unresolved') counts[state] += 1;
    else counts.other += 1;
  }
  return counts;
}

export function mount(root) {
  const listeners = [];
  const remapButtons = [];
  const strict = parseStrict(window.location.search);
  // `let`, not `const`: the 调试 toggle is a RENDER flag (F7) and the settings
  // screen changes it in `localStorage` while this view stays mounted.
  let debug = isDebugEnabled();
  let payload = null;
  let activePanel = null;
  let controller = null;
  let pollTimer = null;
  let torndown = false;
  let writeInFlight = 0;
  let remapTarget = null;

  function listen(target, type, handler) {
    target.addEventListener(type, handler);
    listeners.push(() => target.removeEventListener(type, handler));
  }

  // Scroll only: see the header note in router.js. A save from mount() or
  // unmount() would land in the wrong history entry, because the browser has
  // already pushed the destination by the time a hashchange handler runs.
  const detachScroll = trackScroll(() => saveViewState({ scrollTop: window.scrollY }));

  /* The restore target, read ONCE and before anything can write to
   * `history.state`. Same reason as home.js and recipe.js: the router calls
   * `scrollTo` right after `mount()` returns, and at that instant this view is
   * a one-line loading state about 16px tall, so a long Back offset CLAMPS
   * (measured in Chromium: 640 -> 16). Re-applied below, once the rows exist. */
  const resumeTop = readViewState().scrollTop;

  const back = el('button', { type: 'button', class: 'button', text: '返回' });
  const resolveButton = el('button', {
    type: 'button',
    class: 'button',
    dataset: { role: 'resolve' },
    text: RESOLVE_LABEL,
  });
  const writeReason = el('p', { class: 'muted', dataset: { role: 'write-reason' } });
  const actions = el('div', { class: 'settings-actions', dataset: { role: 'actions' } }, [resolveButton, writeReason]);

  const meta = el('p', { class: 'muted', dataset: { role: 'meta' } });
  const banner = el('div', { dataset: { role: 'banner' } });
  const joinSlot = el('div', { dataset: { role: 'join-slot' } });
  const joinTableSlot = el('div', { dataset: { role: 'join-table-slot' } });
  const resolveSlot = el('div', { dataset: { role: 'resolve-slot' } });
  const body = el('div', { dataset: { role: 'body' } }, [loadingState('正在读菜谱和库存的溯源信息…')]);

  const query = el('input', { type: 'text', class: 'log-date', dataset: { role: 'remap-query' } });
  const searchButton = el('button', {
    type: 'button',
    class: 'button',
    dataset: { role: 'remap-search' },
    text: '搜索',
  });
  const closeButton = el('button', { type: 'button', class: 'button', text: '关闭' });
  const clearButton = el('button', {
    type: 'button',
    class: 'button',
    dataset: { role: 'remap-clear' },
    text: CLEAR_MANUAL_LABEL,
  });
  const remapTargetLine = el('p', { class: 'muted', dataset: { role: 'remap-target' } });
  const remapStatus = el('p', { class: 'muted', dataset: { role: 'remap-status' } });
  const remapError = el('div', { dataset: { role: 'remap-error' } });
  const remapResults = el('div', { dataset: { role: 'remap-results' } });
  // `.form-sheet` carries the vendored Pattern L cap, and `input` is already
  // `font-size: 16px` in the vendored baseline, so iOS does not zoom the query
  // box. `hidden` is exactly what main.js's `isModalOpen()` looks for, which is
  // what stops an update from reloading the page under an open picker (F18 §4e).
  const picker = el('div', { class: 'form-sheet date-picker', dataset: { role: 'remap-picker' } }, [
    remapTargetLine,
    el('p', { class: 'muted', text: '按名字搜 Pantry Item（GET /api/pantry/items?q=…）。只读目录，不新建 Pantry Item。' }),
    query,
    el('div', { class: 'settings-actions' }, [searchButton]),
    remapStatus,
    remapError,
    remapResults,
    el('p', { class: 'muted', text: MANUAL_IS_DELETE_PLUS_INSERT }),
    el('div', { class: 'settings-actions' }, [clearButton, closeButton]),
  ]);
  picker.setAttribute('hidden', '');

  root.appendChild(
    el('section', { class: 'panel', dataset: { view: 'provenance' } }, [
      el('div', { class: 'settings-actions' }, [back]),
      el('h2', { text: '溯源' }),
      el('p', {
        class: 'muted',
        dataset: { role: 'view-note' },
        text:
          '这一屏只画已经在正常响应里的东西：没有第二个接口，也没有 ?debug=1。' +
          '打开或关掉“调试”只是重画一次，不会请求服务器。',
      }),
      meta,
      banner,
      el('h3', { class: 'settings-subhead', text: 'Stock Join（F1：整个 app 最弱的一环）' }),
      el('p', {
        class: 'muted',
        dataset: { role: 'join-caveat' },
        text:
          '库存行是自由文本，目录行是规范化名字；这个 join 只有三步（精确名 → basename → 覆盖表），' +
          '没有更多层级，也没有第二次重扫 —— 它没有物化的表可以重扫。三步都没命中的那一行，' +
          '只是不在在货名单里。这不是“家里没有”的证据，但也不会自己变好：' +
          '唯一的修法是在 app/pantry/line_overrides.yaml 里加一行被评审过的名字，而不是去改你的库。',
      }),
      joinSlot,
      joinTableSlot,
      actions,
      resolveSlot,
      el('h3', { class: 'settings-subhead', text: '逐格溯源' }),
      body,
      picker,
    ]),
  );

  /* --- writability: a STATE, not a flight -------------------------------- */

  function writeBlocker() {
    // F5: online-only, and nothing is queued — this file imports no outbox.
    if (navigator.onLine === false) return OFFLINE_WRITE_REASON;
    // F18: a read-only install refuses EVERY mutation; saying so beats letting
    // the user discover it on a failed tap.
    if (isReadOnly()) return '只读模式：这一台不会写入映射表（服务器拒绝所有写入，没有例外）。';
    return '';
  }

  function applyWritability() {
    const blocked = writeBlocker() !== '';
    writeReason.textContent = writeBlocker();
    resolveButton.disabled = blocked || writeInFlight > 0 || torndown;
    // `关闭` is deliberately NOT in this list: dismissing a sheet is not a write
    // and must stay possible offline.
    searchButton.disabled = blocked || !remapTarget;
    clearButton.disabled = blocked || !remapTarget;
    for (const button of remapButtons) button.disabled = blocked || button.dataset.locked === 'true';
  }

  /* --- action 1: 重新解析未匹配项 ---------------------------------------- */

  async function runResolve() {
    if (resolveButton.disabled) return;
    resolveSlot.textContent = '';
    writeInFlight += 1;
    applyWritability();
    const clearWatchdog = unstickOnTimeout(resolveButton, {
      delay: UNSTICK_DELAY_MS,
      onStall: () => {
        resolveSlot.appendChild(
          errorState({
            title: '重新解析没有回来。',
            detail: '请求一直没有完成，可以再点一次。',
            code: 'request_stalled',
            dataset: { role: 'resolve-stalled' },
          }),
        );
      },
    });
    try {
      const report = await apiFetch(RESOLVE_PATH, { method: 'POST' });
      resolveSlot.appendChild(resolveReport(report));
      // A deliberate, user-initiated read afterwards: the pass just changed the
      // mapping table, and a diagnostic that keeps showing the pre-pass state
      // is worse than useless. This is NOT the toggle's second data path — the
      // toggle itself still makes no request at all.
      startLoad();
    } catch (error) {
      resolveSlot.appendChild(resolveFailure(error));
    } finally {
      clearWatchdog();
      writeInFlight -= 1;
      applyWritability();
    }
  }

  /** §9.10.1's five numbers, and F2's conflicts, shown with the count. */
  function resolveReport(report) {
    const body = report || {};
    const conflicts = Array.isArray(body.conflicts) ? body.conflicts : [];
    return el('div', { dataset: { role: 'resolve-report' } }, [
      el('p', { class: 'muted', text: '重新解析跑完了，下面是这一遍的五个数字：' }),
      el('div', { class: 'fields' }, [
        field('重新考虑', body.reconsidered),
        field('解析成功', body.resolved),
        field('仍然没匹配上', body.stillUnresolved),
        field('清掉的过期映射', body.staleReset),
        field('同一道菜两格撞同一个 Pantry Item（F2）', body.duplicateSlotConflicts),
      ].filter(Boolean)),
      Number(body.duplicateSlotConflicts) > 0
        ? el('div', { dataset: { role: 'resolve-conflicts' } }, [
            el('p', {
              class: 'muted',
              text: '下面这些格子被拒绝了，理由是同一道菜的另一格已经占了那个 Pantry Item（F2）。这里只显示，不隐藏：',
            }),
            ...conflicts.map((conflict) =>
              el('p', {
                class: 'muted',
                dataset: { role: 'resolve-conflict', reason: String(conflict.reason || '') },
                text:
                  `${conflict.recipeNote} 第 ${conflict.ingredientIndex} 格（${conflict.rawValue}）→ ` +
                  `${itemText(conflict.pantryItemId)} ${conflict.canonicalName}：${conflict.reason}`,
              }),
            ),
          ])
        : el('p', {
            class: 'muted',
            dataset: { role: 'resolve-no-conflicts' },
            text: '这一遍没有出现重复占用的冲突。',
          }),
    ]);
  }

  function resolveFailure(error) {
    if (error && error.status === 409) {
      // `resolve_in_flight`: a pass is already running. Shown AS that — a second
      // tap is not a second pass, and a "try again" sentence would invite
      // exactly the racing double press the 409 exists to refuse.
      return errorState({
        title: '已经有一遍重新解析在跑了。',
        detail: '这一次没有排队，也没有第二遍同时开。等它跑完再点一次。',
        code: error.code,
        requestId: error.requestId,
        dataset: { role: 'resolve-in-flight' },
      });
    }
    return errorState({
      title: '重新解析没有完成。',
      detail: (error && error.message) || '',
      code: error && error.code,
      requestId: error && error.requestId,
      dataset: { role: 'resolve-failed' },
    });
  }

  /* --- action 2: the per-row manual re-map -------------------------------- */

  function openRemap(target) {
    remapTarget = target;
    remapError.textContent = '';
    remapStatus.textContent = '';
    remapResults.textContent = '';
    query.value = target.parsedName || target.rawValue || '';
    remapTargetLine.textContent = `${target.noteName} 第 ${target.index} 格：${target.rawValue}`;
    // A hand fix can only be cleared if there is one; saying so beats a button
    // that 404s.
    if (target.matchMethod === 'manual') clearButton.removeAttribute('hidden');
    else clearButton.setAttribute('hidden', '');
    picker.removeAttribute('hidden');
    // **The sheet is at the END of this view, and this view is ~70 cards tall.**
    // `recipe.js` can get away with the sheet sitting below its control because
    // the control and the sheet are in the same screenful; here they are tens
    // of thousands of pixels apart, so opening it and leaving the scroll where
    // it was means tapping a control and seeing nothing happen. Guarded, because
    // `scrollIntoView` is a layout call this app has never needed and the tests
    // assert behaviour, not layout.
    if (typeof picker.scrollIntoView === 'function') picker.scrollIntoView({ block: 'nearest' });
    applyWritability();
  }

  function closeRemap() {
    remapTarget = null;
    picker.setAttribute('hidden', '');
    applyWritability();
  }

  function remapFailure(error) {
    if (error && error.status === 409 && error.code === 'duplicate_slot_conflict') {
      // F2: the duplicate is SHOWN. A hand fix is not a way around "one recipe
      // lists the same Pantry Item twice"; `set_manual`'s transaction rolled
      // back whole, so the previous row is intact and nothing was corrupted.
      return errorState({
        title: '同一道菜的另一格已经用了这个 Pantry Item（F2）。',
        detail: '服务器没有写入任何东西，之前的记录原样还在。要这道菜列出两次同一个 Pantry Item，得改笔记本身。',
        code: error.code,
        requestId: error.requestId,
        dataset: { role: 'remap-conflict' },
      });
    }
    if (error && error.status === 409) {
      return errorState({
        title: '服务器那边有一遍解析正在跑，这次没有写入。',
        detail: '等它跑完再试一次。',
        code: error.code,
        requestId: error.requestId,
        dataset: { role: 'remap-in-flight' },
      });
    }
    if (error && error.status === 422) {
      return errorState({
        title: '这个 Pantry Item 不在目录里。',
        detail: '重新搜一次，挑一个结果里真实存在的编号。',
        code: error.code,
        requestId: error.requestId,
        dataset: { role: 'remap-unknown-item' },
      });
    }
    return errorState({
      title: '没有写进去。',
      detail: (error && error.message) || '',
      code: error && error.code,
      requestId: error && error.requestId,
      dataset: { role: 'remap-failed' },
    });
  }

  async function searchItems() {
    if (searchButton.disabled || !remapTarget) return;
    const target = remapTarget;
    const term = String(query.value || '').trim();
    remapError.textContent = '';
    if (!term) {
      // A blank `q` is a 422 server-side, so asking for it would be a client bug
      // dressed as a search. Refused here with the same rule.
      remapStatus.textContent = '先写一个 Pantry Item 的名字再搜。';
      return;
    }
    remapStatus.textContent = '正在搜目录…';
    searchButton.disabled = true;
    const clearWatchdog = unstickOnTimeout(searchButton, {
      delay: UNSTICK_DELAY_MS,
      onStall: () => {
        remapStatus.textContent = '搜索没有回来，可以再搜一次。';
      },
    });
    try {
      const answer = await apiFetch(`${ITEMS_PATH}?q=${encodeURIComponent(term)}&limit=${PICKER_LIMIT}`);
      if (torndown || remapTarget !== target) return;
      remapStatus.textContent = '';
      remapResults.textContent = '';
      const items = (answer && answer.items) || [];
      if (items.length === 0) {
        // An empty result is a REAL answer — "there is no Pantry Item by that
        // name" — and is not the same as "your query was not a query", which is
        // refused above. The shared empty state, so there is one invitation.
        remapResults.appendChild(
          emptyState({
            title: '目录里没有这个名字。',
            body: '换一个 Pantry Item 的名字再搜一次；这一屏不新建 Pantry Item。',
            dataset: { role: 'remap-empty' },
          }),
        );
        return;
      }
      for (const item of items) remapResults.appendChild(itemButton(item, target));
    } catch (error) {
      if (torndown || remapTarget !== target) return;
      remapError.appendChild(
        errorState({
          title: '搜不了目录。',
          detail: (error && error.message) || '',
          code: error && error.code,
          requestId: error && error.requestId,
          dataset: { role: 'remap-search-failed' },
        }),
      );
    } finally {
      clearWatchdog();
      applyWritability();
    }
  }

  function itemButton(item, target) {
    const button = el('button', {
      type: 'button',
      class: 'button',
      dataset: { role: 'remap-item', itemId: String(item.id) },
      text: `${item.canonicalName} · #${item.id} · 类别 ${item.category}`,
    });
    button.addEventListener('click', () => void setManual(item, target));
    return button;
  }

  function mappingPath(target) {
    // The basename is sent back as a path SEGMENT the server resolves against
    // its own index — never joined to a path here (D2).
    return `/api/recipes/${encodeURIComponent(target.noteName)}/ingredients/${target.index}/mapping`;
  }

  async function setManual(item, target) {
    if (writeInFlight > 0 || remapTarget !== target) return;
    writeInFlight += 1;
    applyWritability();
    try {
      await apiFetch(mappingPath(target), { method: 'PUT', body: { pantryItemId: item.id } });
      remapError.textContent = '';
      remapStatus.textContent = `已手工指定：${item.canonicalName}（#${item.id}）。`;
      remapResults.textContent = '';
      startLoad();
    } catch (error) {
      remapStatus.textContent = '';
      remapError.appendChild(remapFailure(error));
    } finally {
      writeInFlight -= 1;
      applyWritability();
    }
  }

  async function clearManual() {
    const target = remapTarget;
    if (!target || writeInFlight > 0) return;
    writeInFlight += 1;
    applyWritability();
    try {
      await apiFetch(mappingPath(target), { method: 'DELETE' });
      remapError.textContent = '';
      remapStatus.textContent = '手工指定已清除，这一格回到自动解析。';
      remapResults.textContent = '';
      startLoad();
    } catch (error) {
      remapStatus.textContent = '';
      remapError.appendChild(remapFailure(error));
    } finally {
      writeInFlight -= 1;
      applyWritability();
    }
  }

  /* --- the rows ---------------------------------------------------------- */

  function slotCard(recipe, slot) {
    const target = {
      noteName: recipe.noteName,
      index: slot.index,
      rawValue: slot.rawValue,
      parsedName: slot.parsedName,
      matchMethod: slot.matchMethod,
    };
    // `ingredient_mappings.ingredient_index` is documented in `schema.sql` as a
    // `材料` index, so a 调料 slot has no row to write and inventing one would
    // put a frontmatter string into an audit anchor nothing reads. A 调料 card
    // therefore says WHY it has no control, rather than offering a write the
    // server would refuse (§9.16: the manual routes are `ingredients/{index}`
    // and reach 材料 slots only).
    const actions = slot.isSeasoning
      ? el('p', {
          class: 'muted',
          dataset: { role: 'remap-unavailable' },
          text: '调料格没有映射行，所以没有“手工指定”这一步；它的答案每次响应都是现算的。',
        })
      : (() => {
          const openButton = el('button', {
            type: 'button',
            class: 'button',
            dataset: { role: 'remap-open', note: recipe.noteName, slot: String(slot.index) },
            text: REMAP_LABEL,
          });
          openButton.addEventListener('click', () => openRemap(target));
          remapButtons.push(openButton);
          return el('div', { class: 'settings-actions' }, [openButton]);
        })();

    const candidates = Array.isArray(slot.candidatesJson) ? slot.candidatesJson : [];
    return el(
      'div',
      {
        class: 'recipe-row',
        dataset: {
          role: 'slot',
          note: recipe.noteName,
          slot: String(slot.index),
          kind: slot.isSeasoning ? 'seasoning' : 'material',
          tier: String(slot.matchTier),
          method: String(slot.matchMethod),
          itemId: slot.pantryItemId === null || slot.pantryItemId === undefined ? '' : String(slot.pantryItemId),
          joinState: String(slot.stockJoinState),
        },
      },
      [
        el('p', { class: 'recipe-row__name', dataset: { role: 'slot-note' }, text: recipe.noteName }),
        // The chip comes from `chips.js`, so the CLASS is `chip-class.js`'s
        // answer and this view decides no colour of its own. It carries the
        // `data-*` provenance attributes too, which is what makes the 调试
        // repaint observable here.
        chip(slot, { strict, debug }),
        el('div', { class: 'fields' }, [
          field('原文 raw_value', slot.rawValue),
          field('解析名 parsed_name', slot.parsedName),
          field('解析方式 parse_method', slot.parseMethod),
          field('匹配层级', tierText(slot)),
          field('匹配方式 match_method', slot.matchMethod),
          field('Pantry Item', itemText(slot.pantryItemId)),
          field('置信度 confidence', confidenceText(slot.confidence)),
          field('stockJoinState', joinStateLabel(slot.stockJoinState)),
          field('在货 inStock', slot.inStock ? '是' : '否'),
          field(
            '格子类型',
            slot.isSeasoning ? '调料（在货时算作有；只有没匹配上的调料才算缺）' : '材料',
          ),
        ]),
        // The "it does not mean you don't have it" sentence goes on the row
        // where it bites. A user asking "why does this say I don't?" is looking
        // at an `unresolved` row, and that is where the answer has to be.
        slot.stockJoinState === 'unresolved'
          ? el('p', { class: 'muted', dataset: { role: 'join-warning' }, text: UNRESOLVED_IS_NOT_ABSENT })
          : null,
        candidates.length
          ? el('div', { dataset: { role: 'candidates', count: String(candidates.length) } }, [
              el('p', { class: 'muted', text: `阶梯看过的 ${candidates.length} 行（被拒绝的也在内）：` }),
              ...candidates.map(candidateNode),
            ])
          : el('p', { class: 'muted', dataset: { role: 'no-candidates' }, text: '阶梯没有留下任何候选行。' }),
        actions,
      ],
    );
  }

  function joinSummary(next) {
    const slots = next.recipes.flatMap((recipe) =>
      (recipe.ingredients || []).map((slot) => ({ ...slot, noteName: recipe.noteName })),
    );
    const counts = joinCounts(slots);
    const unjoinedSlots = slots.filter((slot) => slot.stockJoinState === 'unresolved');
    return el('div', { dataset: { role: 'join-summary' } }, [
      el('div', { class: 'fields' }, [
        field('Pantry.md 里没对上的行（服务器计数）', Number(next.stockUnjoinedCount) || 0),
        field('过期映射', Number(next.staleMappingCount) || 0),
        field('跳过的笔记', Number(next.skipped) || 0),
        field('这一屏里的格子总数', counts.total),
        field('join 说 joined', counts.joined),
        field('join 说 override', counts.override),
        field('join 说 unresolved', counts.unresolved),
        counts.other ?         field('join 说了这一屏没见过的值', counts.other) : null,
      ].filter(Boolean)),
      // The three states, said ONCE, above ~70 rows that each name their own.
      el('div', { dataset: { role: 'join-legend' } },
        Object.values(JOIN_STATE_SENTENCES).map((sentence) => el('p', { class: 'muted', text: sentence }))),
      el('p', {
        class: 'muted',
        dataset: { role: 'unjoined-note' },
        text:
          `“没对上的行”是 Pantry.md 那边数出来的：${Number(next.stockUnjoinedCount) || 0} 行。` +
          '下面这些格子是同一件事在菜谱这边的另一面 —— join 说 unresolved，' +
          '说明这个格子落到的那个 Pantry Item 没有任何一行库存能解释。它不等于“你没有”，也不等于“你有”。',
      }),
      unjoinedSlots.length
        ? el('div', { dataset: { role: 'unjoined', count: String(unjoinedSlots.length) } }, [
            el('p', { class: 'muted', text: `join 说 unresolved 的格子（${unjoinedSlots.length} 个）：` }),
            ...unjoinedSlots.map((slot) =>
              el('p', {
                class: 'muted',
                dataset: { role: 'unjoined-slot', note: slot.noteName, slot: String(slot.index) },
                text: `${slot.noteName} 第 ${slot.index} 格：${slot.parsedName || slot.rawValue}（${itemText(slot.pantryItemId)}）`,
              }),
            ),
          ])
        : el('p', { class: 'muted', dataset: { role: 'unjoined-empty' }, text: '这一屏里没有 join 说 unresolved 的格子。' }),
      el('p', {
        class: 'muted',
        dataset: { role: 'join-override-note' },
        text:
          'override 那一档是靠 app/pantry/line_overrides.yaml 里的名字对上的：' +
          '它只在一行既不是目录里的精确名、也不是 basename 时才被查，而且它存的是名字不是编号' +
          '（编号会被重新导入改掉），所以改这个被评审的文件是唯一不用动你库的办法。',
      }),
    ]);
  }

  /**
   * §9.13.4's per-line Stock Join table.
   *
   * **A CARD PER LINE, not a `<table>`, for the reason the slot cards give**:
   * §9.4 allows exactly ONE horizontal scroller in this app and the chip row
   * spends it. Six fields per row is what fits a card; a `<table>` would need a
   * second scroller or a squeezed column.
   *
   * **THE TEXT IS SERVER-SUPPLIED AND THIS FUNCTION COMPOSES NONE OF IT.** The
   * `guidance` paragraph and each miss's `repairHint` arrive as finished
   * sentences from `GET /api/recipes`, and they are printed verbatim. They are
   * server text because they are statements about `line_overrides.yaml` — a
   * reviewed source file whose key must already be NFKC + trim + casefolded or
   * the app refuses to boot. A client composing that sentence would own a fact
   * about a file it cannot read, and would compose it wrong the first time the
   * format changed. Everything below that is a *label* or a *projection* of a
   * field is this view's; every claim is the server's.
   *
   * **`el()` is textContent-only, so a `Pantry.md` line and a catalog name can
   * never become markup.** Both are user- and catalog-sourced free text and the
   * whole table is built through `el()`; there is no `innerHTML` anywhere below.
   */
  function joinLineCard(line) {
    const ids = Array.isArray(line.pantryItemIds) ? line.pantryItemIds : [];
    const fields = [
      field('原文 text', line.text),
      field('规范化 product core', line.core),
      field('stockJoinState', joinStateLabel(line.stockJoinState)),
      field('哪一层命中的', joinTierText(line.tier, line.stockJoinState)),
      // BOTH ids on a duplicate name, joined by `、` — §9.13.4 wants the
      // ambiguity visible here, before it becomes a wrong chip downstream. One
      // id would have been a winner picked by insertion order.
      ids.length ? field('Pantry Item', ids.map((id) => `#${id}`).join('、')) : null,
      field('区（笔记里的小节号，不是路径）', line.section),
      // `overrideKey` is the string a paste has to reproduce, so it is shown on
      // EVERY row and not only on a miss: it is the same `fold_name` the override
      // file is keyed by, and a reader comparing it against the file needs to
      // see it on a hit too.
      field('覆盖表里的键 overrideKey', line.overrideKey),
      line.overrideName ? field('覆盖表里的值 overrideName', line.overrideName) : null,
    ].filter(Boolean);
    return el(
      'div',
      {
        class: 'recipe-row',
        dataset: {
          role: 'join-line',
          line: String(line.lineIndex),
          section: String(line.section === null || line.section === undefined ? '' : line.section),
          state: String(line.stockJoinState),
          tier: String(line.tier),
          itemIds: ids.join(','),
          overrideKey: String(line.overrideKey === null || line.overrideKey === undefined ? '' : line.overrideKey),
          overrideName: String(line.overrideName === null || line.overrideName === undefined ? '' : line.overrideName),
        },
      },
      [
        el('p', { class: 'recipe-row__name', dataset: { role: 'join-line-head' }, text: line.text }),
        el('div', { class: 'fields' }, fields),
        // The miss guidance, verbatim. Rendered ONLY when the server sent one,
        // so a client-composed replacement cannot exist here to be asserted
        // against: the sentence is a payload field or it is nothing.
        line.repairHint
          ? el('p', { class: 'muted', dataset: { role: 'join-repair' }, text: line.repairHint })
          : null,
      ],
    );
  }

  function joinTable(join) {
    const lines = Array.isArray(join && join.lines) ? join.lines : [];
    const tiers = (join && join.tierCounts) || {};
    return el('div', { dataset: { role: 'join-table' } }, [
      el('div', { class: 'fields' }, [
        field('join 看到的行数', Number(join && join.lineCount) || 0),
        field('其中没对上的（服务器计数）', Number(join && join.unjoinedCount) || 0),
        field('第一层 精确名 命中', Number(tiers.exact) || 0),
        field('第二层 basename 命中', Number(tiers.basename) || 0),
        field('第三层 line_overrides.yaml 命中', Number(tiers.override) || 0),
      ].filter(Boolean)),
      // The weakness, in the server's words. `tier 0` and "没有层级命中" are
      // different facts and this row is where a miss says so in numbers.
      el('p', {
        class: 'muted',
        dataset: { role: 'join-guidance' },
        text: String((join && join.guidance) || ''),
      }),
      lines.length
        ? el('div', { dataset: { role: 'join-lines', count: String(lines.length) } }, [
            el('p', { class: 'muted', text: `下面 ${lines.length} 行是 Pantry.md 里的产品行，按笔记里的行序排列。` }),
            ...lines.map(joinLineCard),
          ])
        : el('p', { class: 'muted', dataset: { role: 'join-lines-empty' }, text: 'join 没有看到任何产品行。' }),
    ]);
  }

  function paint(next) {
    payload = next;
    meta.textContent = metaLine(next);
    banner.textContent = '';
    if (navigator.onLine === false) {
      banner.appendChild(
        el('p', {
          class: 'banner banner-notice',
          dataset: { role: 'offline' },
          text: '离线：这一屏是上一次读到的数据，写入类操作现在点不动。',
        }),
      );
    }
    joinSlot.textContent = '';
    joinSlot.appendChild(joinSummary(next));
    joinTableSlot.textContent = '';
    // `stockJoin` is unconditional on the list (F7), so a successful response
    // always carries it. A response without it is a *server* that predates this
    // view, and the honest thing is to say so rather than render an empty table
    // that reads as "the join saw nothing" — the exact blank this table exists
    // to avoid.
    if (next.stockJoin) {
      joinTableSlot.appendChild(joinTable(next.stockJoin));
    } else {
      joinTableSlot.appendChild(
        el('p', {
          class: 'muted',
          dataset: { role: 'join-table-absent' },
          text: '这次响应里没有 stockJoin 字段，所以没有表可以画。这不是“零行”，是服务器没有发这一段。',
        }),
      );
    }
    body.textContent = '';
    if (!next.recipes.length) {
      // The SHARED empty state, and reachable only from a SUCCESSFUL empty
      // response: `loadPanel` hands `onReady` a successful body and nothing
      // else, so this cannot be reached during loading or after a failure.
      body.appendChild(
        emptyState({
          title: '还没有菜谱，所以没有溯源信息。',
          body: '在 Obsidian 的菜谱文件夹里放一个笔记，这一屏就会出现。',
          dataset: { role: 'empty-recipes' },
        }),
      );
    } else {
      for (const recipe of next.recipes) {
        const slots = recipe.ingredients || [];
        body.appendChild(
          el('div', { dataset: { role: 'recipe-block', note: recipe.noteName } }, [
            el(
              'h3',
              { class: 'settings-subhead', dataset: { role: 'recipe-head', note: recipe.noteName } },
              [`${recipe.noteName} · ${recipe.found}/${recipe.total}`],
            ),
            ...slots.map((slot) => slotCard(recipe, slot)),
          ]),
        );
      }
    }
    // Now the page is tall enough for the offset to mean something.
    if (typeof resumeTop === 'number' && resumeTop > 0 && window.scrollY !== resumeTop) {
      window.scrollTo(0, resumeTop);
    }
    // The rows carry their own write controls, so writability is re-applied
    // after every paint. Without this a view MOUNTED while offline — or one
    // whose rows were painted before the offline event — would render live
    // controls.
    applyWritability();
  }

  function paintFailure(error) {
    body.textContent = '';
    joinSlot.textContent = '';
    // Cleared on EVERY failure, and the 503 case below matters most: a 503 body
    // is exactly `{requestId, code}`, so there is no `stockJoin` to render and a
    // table left over from the last good paint would be a *stale* table beside
    // a refusal to read the source it came from. The unavailable panel replaces
    // it. There is deliberately no second unavailable state here — see the
    // header note and `sourceUnavailableState`'s own definition.
    joinTableSlot.textContent = '';
    if (isSourceUnavailable(error)) {
      // #21's panel, verbatim. A 503 body is exactly `{requestId, code}`: there
      // is no `recipes` key to render, and an empty table here would read as
      // "nothing matched and nothing is in stock" — the worst sentence this app
      // can produce. There is deliberately no retry control: F1's failure is
      // permanent until the vault file is fixed.
      joinSlot.appendChild(sourceUnavailableState({ error }));
      meta.textContent = '';
      return;
    }
    const retry = el('button', { type: 'button', class: 'button', text: '重新检查' });
    retry.addEventListener('click', () => startLoad());
    joinSlot.appendChild(
      errorState({
        title: '读不到溯源信息。',
        detail: (error && error.message) || '这一屏没有可以显示的数据。',
        code: error && error.code,
        requestId: error && error.requestId,
        actions: [retry],
        dataset: { role: 'provenance-error' },
      }),
    );
  }

  function schedulePoll() {
    if (torndown) return;
    if (pollTimer !== null) window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(() => {
      pollTimer = null;
      startLoad({ quiet: true });
    }, STOCK_TTL_SECONDS * 1000);
  }

  function startLoad({ quiet = false } = {}) {
    if (torndown) return;
    if (quiet && navigator.onLine === false) {
      schedulePoll();
      return;
    }
    if (activePanel) activePanel.abort();
    controller = new AbortController();
    const signal = controller.signal;
    activePanel = loadPanel({
      load: async () => {
        const next = await apiFetch(withStrict(RECIPES_PATH, strict), { signal });
        // The cache is keyed on every input the render below consumes and on
        // nothing else, so a hit means the painted DOM would be identical — the
        // same key and the same singleton `home.js` uses.
        const key = recipeListCacheKey({
          catalogRevision: next.catalogRevision,
          stockRevision: next.stockRevision,
          strict,
        });
        const cached = recipeListCache.get(key);
        recipeListCache.set(key, next);
        return { next, unchanged: Boolean(cached) && quiet };
      },
      onLoading: () => {
        if (quiet) return;
        body.textContent = '';
        body.appendChild(loadingState('正在读菜谱和库存的溯源信息…'));
      },
      onReady: ({ next, unchanged }) => {
        if (unchanged) return;
        paint(next);
      },
      onError: (error) => {
        if (quiet && payload) return; // keep the last good paint on a failed poll
        paintFailure(error);
      },
    });
    schedulePoll();
  }

  listen(back, 'click', () => goBack());
  listen(resolveButton, 'click', () => void runResolve());
  listen(searchButton, 'click', () => void searchItems());
  listen(closeButton, 'click', () => closeRemap());
  listen(clearButton, 'click', () => void clearManual());
  listen(query, 'keydown', (event) => {
    if (event && event.key === 'Enter') void searchItems();
  });
  listen(window, 'online', () => {
    applyWritability();
    startLoad();
  });
  listen(window, 'offline', applyWritability);
  // F7: the flag changed on the settings screen. Repaint the payload already in
  // hand — no request, because provenance was in it all along.
  listen(window, DEBUG_EVENT, () => {
    if (torndown || !payload) return;
    debug = isDebugEnabled();
    paint(payload);
  });

  startLoad();
  // Synchronous, not deferred: the disabled state is part of the first paint the
  // user sees, and a `setTimeout(0)` would make it depend on a macrotask.
  applyWritability();

  return function unmount() {
    torndown = true;
    if (pollTimer !== null) window.clearTimeout(pollTimer);
    pollTimer = null;
    if (activePanel) activePanel.abort();
    activePanel = null;
    if (controller) controller.abort();
    controller = null;
    remapTarget = null;
    detachScroll();
    for (const off of listeners.splice(0)) off();
    resolveButton.disabled = false;
    searchButton.disabled = false;
    clearButton.disabled = false;
    closeButton.disabled = false;
    for (const button of remapButtons.splice(0)) button.disabled = false;
    root.textContent = '';
  };
}

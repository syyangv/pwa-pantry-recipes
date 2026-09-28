/* #/ — the recipe list. D4's honest display, the screen the whole app feeds.
 *
 * A user opens this on a phone and asks one question: *what can I cook right
 * now?* The answer is one line per recipe plus a chip row. There is no boolean
 * anywhere on this screen, no green tick, and no threshold:
 *
 *   - **The headline is `logic/format.js`'s output, verbatim.** Not composed
 *     here, not trimmed, not re-cased. F17: it is `n/total` over `材料` only
 *     unless 严格模式 is on, and a 调料 name can therefore never appear in the
 *     Materials-only clause.
 *   - **The chip row is `logic/chip-class.js`'s output, verbatim.** `chips.js`
 *     calls the classifier; this view never decides a colour.
 *   - **The order is `logic/sort.js`'s order.** foundRatio desc, then
 *     lastCooked desc, then noteName by code point. No `localeCompare`, no
 *     `Date`, no `Intl` anywhere in this file: determinism is a contract and
 *     #24's browser flow asserts on this exact sequence.
 *   - **A 0/6 recipe is a first-class row.** Nothing is filtered, collapsed, or
 *     thresholded here. A shopping-list seed the list hides is the most
 *     user-visible possible misreading of D4 (F20, §13.1).
 *
 * **F11's 30 s stock TTL is load-bearing, so the screen shows its own age.** The
 * user toggles tasks in Obsidian while this tab is open, so a chip colour can be
 * at most 30 s stale. `stockRevision` is therefore printed in the header, and a
 * `setTimeout` chain re-reads the list every TTL: a long-open tab notices the
 * stock moved instead of showing yesterday's colours until it is touched. When
 * the two revisions are unchanged the DOM is left alone — `api.js`'s
 * `recipeListCache` is keyed on exactly `{catalogRevision, stockRevision,
 * strict}`, which is what makes "the render consumes only these three inputs" a
 * checkable claim rather than a comment.
 *
 * **F19: pull-to-refresh lives HERE and nowhere else.** `initPullToRefresh` is
 * called from this module, in this view's mount, and from no other view. The
 * shortlist view and the recipe detail view are excluded by the spec, and the
 * reasons are not stylistic: pulling to refresh a detail view invites discarding
 * an in-progress date, and pulling to refresh a shortlist while an offline edit
 * is pending is the exact case `hasPendingEdit()` exists to block.
 */

import {
  apiFetch,
  parseStrict,
  recipeListCache,
  recipeListCacheKey,
  withStrict,
} from '../api.js?v=__APP_VERSION__';
import { DEBUG_EVENT, isDebugEnabled } from '../prefs.js?v=__APP_VERSION__';
import { readViewState, saveViewState } from '../router.js?v=__APP_VERSION__';
import { el, trackScroll } from '../dom.js?v=__APP_VERSION__';
import { chipRow, scoredSlots } from '../chips.js?v=__APP_VERSION__';
import { headline } from '../logic/format.js?v=__APP_VERSION__';
import { sortRecipes } from '../logic/sort.js?v=__APP_VERSION__';
import { hasPendingEdit } from '../pending-edits.js?v=__APP_VERSION__';
import { initPullToRefresh } from '../pwa/pull-refresh.js?v=__APP_VERSION__';
import {
  emptyState,
  errorState,
  isSourceUnavailable,
  loadingState,
  loadPanel,
  sourceUnavailableState,
} from '../panels.js?v=__APP_VERSION__';

/** F11's stock TTL, restated because the poll below is derived from it. */
export const STOCK_TTL_SECONDS = 30;

const RECIPES_PATH = '/api/recipes';
const SVG_NS = 'http://www.w3.org/2000/svg';

/**
 * F19's indicator. The inline SVG keeps its intrinsic `width`/`height` so a
 * mixed-cache release cannot expand the glyph to the viewport (§9.4).
 */
function pullIndicator() {
  const host = el('div', { class: 'ptr-indicator', dataset: { role: 'pull-indicator' } });
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('class', 'ptr-spinner');
  svg.setAttribute('width', '22');
  svg.setAttribute('height', '22');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  const track = document.createElementNS(SVG_NS, 'circle');
  track.setAttribute('cx', '12');
  track.setAttribute('cy', '12');
  track.setAttribute('r', '9');
  track.setAttribute('fill', 'none');
  track.setAttribute('stroke', 'currentColor');
  track.setAttribute('stroke-width', '2');
  svg.appendChild(track);
  host.appendChild(svg);
  return host;
}

/** `sha256:…` -> a 12-char prefix. A revision, not a path and not a URL. */
function shortRevision(revision) {
  const text = String(revision || '');
  const hex = text.includes(':') ? text.slice(text.indexOf(':') + 1) : text;
  return hex ? hex.slice(0, 12) : '—';
}

function metaLine(payload) {
  const parts = [
    `库存版本 ${shortRevision(payload.stockRevision)}`,
    `目录版本 ${shortRevision(payload.catalogRevision)}`,
    `库存最长 ${STOCK_TTL_SECONDS} 秒未更新`,
  ];
  // Only the non-zero counters. A "0 unresolved" badge on every screen trains
  // the eye to skip the number that matters.
  if (payload.staleMappingCount > 0) parts.push(`过期映射 ${payload.staleMappingCount}`);
  if (payload.stockUnjoinedCount > 0) parts.push(`库存行没对上 ${payload.stockUnjoinedCount}`);
  if (payload.skipped > 0) parts.push(`跳过的笔记 ${payload.skipped}`);
  return parts.join(' · ');
}

/**
 * One recipe: the headline line above the chip row. The link wraps the NAME and
 * the HEADLINE but not the chip row — a horizontal scroller inside an anchor
 * turns a swipe into a navigation on a touch device, and the row is 44px tall
 * either way.
 */
function recipeRow(recipe, { strict, debug }) {
  const slots = scoredSlots(recipe.ingredients || [], strict);
  return el('article', {
    class: 'recipe-row',
    dataset: {
      role: 'recipe',
      note: recipe.noteName,
      found: String(recipe.found),
      total: String(recipe.total),
    },
  }, [
    el('a', {
      class: 'recipe-row__link',
      href: `#/recipe/${encodeURIComponent(recipe.noteName)}`,
      dataset: { role: 'recipe-link', note: recipe.noteName },
    }, [
      el('span', { class: 'recipe-row__name', text: recipe.noteName }),
      // The headline gets its own node, byte-exact. Nothing above or below this
      // line may wrap it, trim it, or append to it: the four frozen strings of
      // §9.13.1 are what both `format.test.mjs` and #24's browser flow compare.
      el('p', { class: 'recipe-row__headline', dataset: { role: 'headline' }, text: headline({ ingredients: slots, strict }) }),
    ]),
    chipRow({ ingredients: slots, strict, debug }),
  ]);
}

function listBody(recipes, options) {
  // §9.13.3's comparator, imported. `sortRecipes` returns a new array in a new
  // order and filters nothing; there is no threshold below this line.
  return el('div', { class: 'recipe-grid', dataset: { role: 'rows' } },
    sortRecipes(recipes).map((recipe) => recipeRow(recipe, options)));
}

function offlineNotice() {
  return el('p', {
    class: 'banner banner-notice',
    dataset: { role: 'offline' },
    text: '离线：这一屏是上一次读到的数据，下拉刷新需要联网。',
  });
}

export function mount(root) {
  const strict = parseStrict(window.location.search);
  // `let`, not `const`: the 调试 toggle is a RENDER flag (F7) and the settings
  // screen changes it in `localStorage` while this view stays mounted. A `const`
  // captured here would make the event listener below a no-op that looks
  // correct.
  let debug = isDebugEnabled();
  let payload = null;
  let activePanel = null;
  let controller = null;
  let pollTimer = null;
  let pullDestroy = null;
  let torndown = false;

  // Scroll only: see the header note in router.js. A save from mount() or
  // unmount() would land in the wrong history entry, because the browser has
  // already pushed the destination by the time a hashchange handler runs.
  const detachScroll = trackScroll(() =>
    saveViewState({ scrollTop: window.scrollY, filters: { strict, search: null } }),
  );

  /* The offset this entry is to be restored to, read ONCE and before anything
   * can write to `history.state`. A fresh forward navigation lands on an entry
   * with no state at all, so `resumeTop` is `undefined` there and the
   * re-application below is a no-op; a Back traversal lands on the entry this
   * very view saved 640 into, and the router's own restore is re-applied once
   * the rows exist.
   *
   * WHY IT IS NEEDED, MEASURED: the router calls `scrollTo` right after
   * `mount()` returns, and at that instant the list is the one-line loading
   * state — a page about 16px tall. `scrollTo(0, 640)` clamps to 16, the scroll
   * event then saves 16, and Back lands 16 pixels from the top. The state the
   * router read was 640 the whole time; only the DOM was too short. */
  const resumeTop = readViewState().scrollTop;

  const meta = el('p', { class: 'muted', dataset: { role: 'meta' } });
  const banner = el('div', { dataset: { role: 'banner' } });
  const body = el('div', { dataset: { role: 'body' } }, [loadingState('正在读菜谱和库存…')]);
  const indicator = pullIndicator();
  const panel = el('section', { class: 'panel', dataset: { view: 'home' } }, [
    indicator,
    el('h2', { text: '今天能做什么' }),
    banner,
    meta,
    body,
  ]);
  root.appendChild(panel);

  function schedulePoll() {
    if (torndown) return;
    if (pollTimer !== null) window.clearTimeout(pollTimer);
    pollTimer = window.setTimeout(() => {
      pollTimer = null;
      startLoad({ quiet: true });
    }, STOCK_TTL_SECONDS * 1000);
  }

  function paint(next) {
    payload = next;
    meta.textContent = metaLine(next);
    banner.textContent = '';
    if (navigator.onLine === false) banner.appendChild(offlineNotice());
    body.textContent = '';
    if (!next.recipes.length) {
      // §8 story 14: an invitation, and reachable ONLY from a successful empty
      // response. `loadPanel` gives `onReady` a successful body and nothing
      // else, so this line can never be reached during loading or after a
      // failure — which is the whole rule.
      body.appendChild(
        emptyState({
          title: '还没有菜谱。',
          body: '在 Obsidian 的菜谱文件夹里放一个笔记，这一屏就会出现。',
          dataset: { role: 'empty-recipes' },
        }),
      );
      return;
    }
    body.appendChild(listBody(next.recipes, { strict, debug }));
    // Now the page is tall enough for the offset to mean something.
    if (typeof resumeTop === 'number' && resumeTop > 0 && window.scrollY !== resumeTop) {
      window.scrollTo(0, resumeTop);
    }
  }

  function paintFailure(error) {
    body.textContent = '';
    if (isSourceUnavailable(error)) {
      // F1's fail-closed state. A 503 body is exactly `{requestId, code}`:
      // there is no `recipes` key to render, and deliberately no retry control,
      // because the failure is permanent until the user fixes the vault.
      body.appendChild(sourceUnavailableState({ error }));
      return;
    }
    if (navigator.onLine === false) {
      body.appendChild(
        errorState({
          title: '读不到菜谱。',
          detail: '当前离线，这一屏没有可以显示的数据。',
          code: error && error.code,
          requestId: error && error.requestId,
          dataset: { role: 'list-offline' },
        }),
      );
      return;
    }
    const retry = el('button', { type: 'button', class: 'button', text: '重新检查' });
    retry.addEventListener('click', () => startLoad());
    body.appendChild(
      errorState({
        title: '读不到菜谱。',
        detail: (error && error.message) || '这一屏没有可以显示的数据。',
        code: error && error.code,
        requestId: error && error.requestId,
        actions: [retry],
        dataset: { role: 'list-error' },
      }),
    );
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
    const own = controller;
    activePanel = loadPanel({
      load: async () => {
        const next = await apiFetch(withStrict(RECIPES_PATH, strict), { signal });
        // The cache is keyed on every input the render below consumes and on
        // nothing else, so a hit means the painted DOM would be byte-identical.
        // A quiet poll that finds no change therefore leaves the list — and
        // the user's scroll offset — exactly where they were.
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
        body.appendChild(loadingState('正在读菜谱和库存…'));
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

  startLoad();

  /* F19, enabled here and nowhere else. `canStart` is the spec's callback
   * verbatim: offline cannot refresh, and a pull must not discard an
   * unconfirmed write. `onRefresh` re-reads the list and enqueues nothing — this
   * is a read, and `startLoad` owns its own error state, so a failed pull
   * paints the failure rather than leaving a stale list looking current. */
  pullDestroy = initPullToRefresh({
    indicator,
    canStart: () => navigator.onLine && !hasPendingEdit(),
    onRefresh: () => startLoad(),
    onError: () => {},
  });

  // F7: the flag changed on the settings screen. Repaint the payload already in
  // hand — no request, because provenance was in it all along.
  const onDebug = () => {
    if (torndown || !payload) return;
    debug = isDebugEnabled();
    paint(payload);
  };
  const onConnectivity = () => {
    if (torndown) return;
    if (navigator.onLine === false) {
      banner.textContent = '';
      banner.appendChild(offlineNotice());
      return;
    }
    startLoad();
  };
  window.addEventListener(DEBUG_EVENT, onDebug);
  window.addEventListener('online', onConnectivity);

  return function unmount() {
    torndown = true;
    if (pollTimer !== null) window.clearTimeout(pollTimer);
    pollTimer = null;
    if (activePanel) activePanel.abort();
    activePanel = null;
    if (controller) controller.abort();
    controller = null;
    if (pullDestroy) pullDestroy();
    pullDestroy = null;
    detachScroll();
    window.removeEventListener(DEBUG_EVENT, onDebug);
    window.removeEventListener('online', onConnectivity);
    root.textContent = '';
  };
}

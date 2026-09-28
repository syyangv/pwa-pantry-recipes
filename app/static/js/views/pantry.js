/* #/pantry — 食材: what the house has, and what each thing makes. The second tab.
 *
 * **ONE TAB, TWO QUESTIONS, AND THE ORDER THEY ARE ASKED IN.** A user opens this
 * on a phone holding a jar and asks "what can I do with this?" — which is a
 * question about an INGREDIENT, not about a recipe. `#/` answers the mirror of
 * it ("what can I cook?"), and neither answer is derivable from the other: a
 * recipe list ordered by match ratio cannot tell you that the soy sauce in the
 * back of the fridge feeds six recipes and that none of them are on this week's
 * list, and an ingredient list cannot tell you which dinner to make tonight.
 * Hence two tabs rather than one screen with a toggle — a toggle makes the user
 * hold the question in their head and pick a mode, which is the work the split
 * was supposed to do.
 *
 * **THE DATA IS THE ONE `GET /api/recipes` RESPONSE, AND THAT IS A RULE, NOT A
 * SHORTCUT.** The obvious build is a second route serving the pantry stock, and
 * `app/api/recipes.py`'s module docstring is where that option is weighed and
 * rejected: this response already publishes two derived views of the same
 * `StockJoin` object (`stockUnjoinedCount` and every slot's `stockJoinState`),
 * so a second read of `Pantry.md` through a second TTL window could contradict
 * the number printed beside it in the first. Two tabs reading two endpoints
 * would be two tabs that can disagree about the same jar. So this view reads
 * what the recipe list reads, and `logic/ingredient-index.js` does the grouping.
 *
 * **THE GROUPING IS NOT IN THIS FILE.** It is a pure module under `logic/`,
 * beside `sort.js` and `chip-class.js`, because it is the part with a
 * non-obvious answer and `tests/js/logic/ingredient-index.test.mjs` can assert it
 * without a DOM. This file paints rows and owns nothing else.
 *
 * **NO RECIPE SCORE ON A ROW.** Each row links to the recipes that use the item,
 * but it does not print that recipe's `found/total`: that number scores the
 * WHOLE recipe, and beside an ingredient it would read as "this jar is 3/6 of
 * your dinner". `#/` is where a score belongs.
 *
 * **A ROW NAMES ITS SOURCE, AND SHOWS THE OTHER ONE.** There is no
 * `canonical_name` in this payload, so a row's name is the user's own
 * `Pantry.md` text when a Stock Join line exists and the recipe's parsed name
 * when only a recipe mentions it — and the row says which, because `蒜` on a
 * recipe and `蒜苗` on a pantry line is a real difference between two files.
 * Silently picking one would be the confident-wrong-answer class `AGENTS.md` #3
 * rules out, so the disagreement is a second line, not a merge.
 *
 * **NOTHING IS FILTERED.** D4 makes a `0/6` recipe a first-class row, and the
 * same rule from the other side: an item a recipe needs and the house does not
 * have is exactly the row someone opens this tab to find. `logic/` sorts in
 * stock first and drops nothing — no "show only in stock" filter, no collapse,
 * no threshold. `counts.inStockWithoutRecipe` is what makes the reverse case
 * (in the house, in no recipe) countable instead of a surprise.
 *
 * **THE 30 s POLL AND THE HEADER AGE ARE F11's, AND THEY ARE COPIED FROM
 * `home.js` DELIBERATELY.** The user ticks items off in Obsidian while this tab
 * is open, so a stock answer here is at most as stale as one there, and the two
 * screens printing different ages for the same pantry would be a lie about one
 * of them. `api.js`'s `recipeListCache` is keyed on exactly
 * `{catalogRevision, stockRevision, strict}` and this render consumes exactly
 * those three inputs, so a quiet poll that finds no change leaves the list — and
 * the user's scroll offset — alone. That cache has a limit of ONE entry and is
 * shared with `home.js` and `provenance.js`; a tab switch therefore costs one
 * re-read, which is the same cost two views sharing it already had.
 *
 * **NO PULL-TO-REFRESH HERE, AND THAT IS F19's RULE, NOT AN OMISSION.** F19
 * scopes `initPullToRefresh` to `views/home.js` and excludes the other views by
 * name, and the reasons are not stylistic: pulling to refresh a detail view
 * invites discarding an in-progress date, and pulling while an offline edit is
 * pending is the exact case `hasPendingEdit()` exists to block. A second view
 * with a second pull target would be a second thing to get wrong, so this one
 * has none; the stock still refreshes on its own every TTL, which is the refresh
 * that actually matters on a pantry screen.
 *
 * **NO 调试 TOGGLE HERE, BECAUSE THERE IS NOTHING FOR IT TO RE-RENDER.** F7's
 * 调试 switch repaints provenance onto chips, and this view draws no chips. A
 * listener that repainted an identical DOM would be a request-free no-op wearing
 * the costume of F7's zero-request property, and the property is worth more
 * where it is real (`home.js` and `provenance.js` both assert it) than
 * everywhere.
 *
 * **A 503 IS #21's PANEL, IMPORTED, NEVER A SECOND COPY.** A 503 body is
 * exactly `{requestId, code}` — there is no `recipes` key and no `stockJoin` — so
 * a view that assumed an inventory would paint an empty screen, and an empty
 * ingredient list reads as "your pantry is empty", which is the worst sentence
 * this app can produce. `sourceUnavailableState()` is the one implementation,
 * from `panels.js`.
 */

import {
  apiFetch,
  parseStrict,
  recipeListCache,
  recipeListCacheKey,
  withStrict,
} from '../api.js?v=__APP_VERSION__';
import { readViewState, saveViewState } from '../router.js?v=__APP_VERSION__';
import { el, trackScroll } from '../dom.js?v=__APP_VERSION__';
import {
  buildIngredientIndex,
  IN_STOCK,
  NAME_FROM_RECIPE,
  NAME_FROM_STOCK,
} from '../logic/ingredient-index.js?v=__APP_VERSION__';
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

/** The two in-stock facts, as words. Never a third "unknown" state. */
export const STATE_LABELS = Object.freeze({
  [IN_STOCK]: '在货',
  'out-of-stock': '不在在货名单里',
});

/** Where the row's name came from, said on the row. See the header note. */
export const NAME_SOURCE_LABELS = Object.freeze({
  [NAME_FROM_STOCK]: '名字来自 Pantry.md 里那一行',
  [NAME_FROM_RECIPE]: '名字来自菜谱里那一格（Pantry.md 里没有对上的行）',
  id: '两个来源都没有给出名字',
});

/** A pantry line no tier could explain, as ONE pointer — not as an invented row. */
export const UNRESOLVED_POINTER =
  '另外有 {count} 行库存是三层阶梯都没对上的，所以这一屏没有把它们算成任何一行食材。' +
  '这不是“家里没有”的证据。要看每一行、去修它，去「更多 → 溯源」。';

/** `sha256:…` -> a 12-char prefix. A revision, not a path and not a URL. */
function shortRevision(revision) {
  const text = String(revision || '');
  const hex = text.includes(':') ? text.slice(text.indexOf(':') + 1) : text;
  return hex ? hex.slice(0, 12) : '—';
}

function metaLine(payload, counts) {
  const parts = [
    `${counts.items} 行食材，其中在货 ${counts.inStock}`,
    `在货但没有菜谱用它 ${counts.inStockWithoutRecipe}`,
    `库存版本 ${shortRevision(payload.stockRevision)}`,
    `目录版本 ${shortRevision(payload.catalogRevision)}`,
    `库存最长 ${STOCK_TTL_SECONDS} 秒未更新`,
  ];
  // Only the non-zero counters, for the same reason home.js does it: a "0
  // unresolved" badge on every screen trains the eye to skip the number that
  // matters.
  if (counts.unresolvedLines > 0) parts.push(`没对上的库存行 ${counts.unresolvedLines}`);
  if (payload.staleMappingCount > 0) parts.push(`过期映射 ${payload.staleMappingCount}`);
  if (payload.skipped > 0) parts.push(`跳过的笔记 ${payload.skipped}`);
  return parts.join(' · ');
}

function offlineNotice() {
  return el('p', {
    class: 'banner banner-notice',
    dataset: { role: 'offline' },
    text: '离线：这一屏是上一次读到的数据。',
  });
}

/** The recipes that use this item, each a link into the detail view. */
function recipeLinks(item) {
  return el(
    'div',
    { class: 'ing-recipes', dataset: { role: 'item-recipes', count: String(item.recipes.length) } },
    item.recipes.map((entry) =>
      el('a', {
        class: 'recipe-row__link ing-recipe',
        href: `#/recipe/${encodeURIComponent(entry.noteName)}`,
        dataset: { role: 'recipe-link', note: entry.noteName },
        text: entry.noteName,
      }),
    ),
  );
}

/**
 * One Pantry Item. The card is `recipe-row` because that is this app's ONE card
 * chrome and a second one would be a second set of border/radius/padding rules
 * to keep in step; the `ing-` classes below are the ingredient-specific parts.
 *
 * `data-*` carries the whole row as queryable state — id, stock state, name
 * source, recipe count — so the table this screen draws programmatically has a
 * second rendering of it that costs no bytes on the wire.
 */
function itemRow(item) {
  return el(
    'article',
    {
      class: 'recipe-row ing-row',
      dataset: {
        role: 'ingredient',
        id: String(item.id),
        state: item.state,
        nameFrom: item.nameFrom,
        recipes: String(item.recipes.length),
        slots: String(item.slotCount),
        seasoning: String(item.seasoningOnly),
      },
    },
    [
      el('p', { class: 'recipe-row__name', dataset: { role: 'item-name' }, text: item.name }),
      el('p', { class: 'muted', dataset: { role: 'item-state' }, text: STATE_LABELS[item.state] }),
      el('p', {
        class: 'muted ing-source',
        dataset: { role: 'item-name-source' },
        text: NAME_SOURCE_LABELS[item.nameFrom] || NAME_SOURCE_LABELS.id,
      }),
      // The other source's name, shown and not merged. Empty when both sides
      // agree or when there is no second name to show.
      item.otherNames.length
        ? el('p', {
            class: 'muted ing-alias',
            dataset: { role: 'item-other-names', names: item.otherNames.join('|') },
            text:
              item.nameFrom === NAME_FROM_STOCK
                ? `菜谱里写作：${item.otherNames.join('、')}`
                : `Pantry.md 里写作：${item.otherNames.join('、')}`,
          })
        : null,
      item.seasoningOnly === true
        ? el('p', {
            class: 'muted',
            dataset: { role: 'item-seasoning' },
            text: '只有菜谱里的调料格在用它。',
          })
        : null,
      // The in-the-house-but-in-no-recipe case, which is the half of the
      // inventory the recipe-derived side can never produce and the reason this
      // tab exists at all.
      item.recipes.length === 0
        ? el('p', {
            class: 'muted',
            dataset: { role: 'item-no-recipes' },
            text: '在货，但现在的菜谱里没有一道用它。',
          })
        : recipeLinks(item),
    ],
  );
}

function listBody(index) {
  return el(
    'div',
    { class: 'recipe-grid', dataset: { role: 'rows' } },
    index.items.map(itemRow),
  );
}

export function mount(root) {
  const strict = parseStrict(window.location.search);
  let payload = null;
  let activePanel = null;
  let controller = null;
  let pollTimer = null;
  let torndown = false;

  // Scroll only: see the header note in router.js. A save from mount() or
  // unmount() would land in the wrong history entry, because the browser has
  // already pushed the destination by the time a hashchange handler runs.
  const detachScroll = trackScroll(() =>
    saveViewState({ scrollTop: window.scrollY, filters: { strict, search: null } }),
  );

  /* The restore target, read ONCE and before anything can write to
   * `history.state`. Same measured reason as home.js: the router calls
   * `scrollTo` right after `mount()` returns, and at that instant this view is a
   * one-line loading state about 16px tall, so a 640px Back offset CLAMPS. */
  const resumeTop = readViewState().scrollTop;

  const meta = el('p', { class: 'muted', dataset: { role: 'meta' } });
  const banner = el('div', { dataset: { role: 'banner' } });
  const body = el('div', { dataset: { role: 'body' } }, [loadingState('正在读库存和菜谱…')]);
  const panel = el('section', { class: 'panel', dataset: { view: 'pantry' } }, [
    el('h2', { text: '家里有什么' }),
    el('p', {
      class: 'muted',
      dataset: { role: 'view-note' },
      text:
        '这一屏只画已经在正常响应里的东西：和「菜谱」读的是同一个接口，' +
        '没有第二个接口，也没有 ?debug=1。两个页签不会对同一瓶酱说出不同的话。',
    }),
    meta,
    banner,
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
    const index = buildIngredientIndex(next);
    meta.textContent = metaLine(next, index.counts);
    banner.textContent = '';
    if (navigator.onLine === false) banner.appendChild(offlineNotice());
    body.textContent = '';
    if (!index.items.length) {
      // The SHARED empty state, and reachable only from a SUCCESSFUL response:
      // `loadPanel` hands `onReady` a successful body and nothing else, so this
      // line cannot be reached while loading or after a failure. It is an
      // invitation, not a failure — "no recipes yet" is a normal state, and a
      // view that styled it as an error would teach the user their pantry is
      // broken.
      body.appendChild(
        emptyState({
          title: '还没有可以对上的食材。',
          body: '在 Obsidian 的 Pantry.md 里写一行，或者放一个菜谱笔记，这一屏就会出现。',
          dataset: { role: 'empty-ingredients' },
        }),
      );
      return;
    }
    body.appendChild(listBody(index));
    // The misses, as ONE pointer and never as invented rows — see the header
    // note and `logic/ingredient-index.js`'s. The count is in the pointer and in
    // the header line, so a reader who wants the rows themselves is one tap from
    // the screen that has them.
    if (index.unresolvedLines.length) {
      body.appendChild(
        el('p', {
          class: 'muted',
          dataset: { role: 'unjoined-pointer', count: String(index.unresolvedLines.length) },
          text: UNRESOLVED_POINTER.replace('{count}', String(index.unresolvedLines.length)),
        }),
      );
    }
    // Now the page is tall enough for the offset to mean something.
    if (typeof resumeTop === 'number' && resumeTop > 0 && window.scrollY !== resumeTop) {
      window.scrollTo(0, resumeTop);
    }
  }

  function paintFailure(error) {
    body.textContent = '';
    if (isSourceUnavailable(error)) {
      // F1's fail-closed state, #21's panel, imported. A 503 body is exactly
      // `{requestId, code}`: there is no `recipes` key to group, so an empty
      // list here would read as "your pantry is empty" — the worst sentence this
      // app can produce. There is deliberately no retry control: the failure is
      // permanent until the user fixes the vault.
      body.appendChild(sourceUnavailableState({ error }));
      return;
    }
    if (navigator.onLine === false) {
      body.appendChild(
        errorState({
          title: '读不到库存。',
          detail: '当前离线，这一屏没有可以显示的数据。',
          code: error && error.code,
          requestId: error && error.requestId,
          dataset: { role: 'pantry-offline' },
        }),
      );
      return;
    }
    const retry = el('button', { type: 'button', class: 'button', text: '重新检查' });
    retry.addEventListener('click', () => startLoad());
    body.appendChild(
      errorState({
        title: '读不到库存。',
        detail: (error && error.message) || '这一屏没有可以显示的数据。',
        code: error && error.code,
        requestId: error && error.requestId,
        actions: [retry],
        dataset: { role: 'pantry-error' },
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
    activePanel = loadPanel({
      load: async () => {
        const next = await apiFetch(withStrict(RECIPES_PATH, strict), { signal });
        // Keyed on every input the render below consumes and on nothing else,
        // so a hit means the painted DOM would be byte-identical and a quiet
        // poll that finds no change leaves the list — and the scroll offset —
        // exactly where they were.
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
        body.appendChild(loadingState('正在读库存和菜谱…'));
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

  const onConnectivity = () => {
    if (torndown) return;
    if (navigator.onLine === false) {
      banner.textContent = '';
      banner.appendChild(offlineNotice());
      return;
    }
    startLoad();
  };
  window.addEventListener('online', onConnectivity);

  startLoad();

  return function unmount() {
    torndown = true;
    if (pollTimer !== null) window.clearTimeout(pollTimer);
    pollTimer = null;
    if (activePanel) activePanel.abort();
    activePanel = null;
    if (controller) controller.abort();
    controller = null;
    detachScroll();
    window.removeEventListener('online', onConnectivity);
    root.textContent = '';
  };
}

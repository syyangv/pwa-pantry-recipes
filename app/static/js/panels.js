/* The three shared state renderers every view composes, and `loadPanel`.
 *
 * ONE module owns "loading / empty / error" so that a view cannot invent a
 * fourth wording, and so the F1 fail-closed state is written down once. §9.2's
 * independent-panel-loading rule is `loadPanel` below: each panel it drives has
 * its own explicit `loading -> ready | error` state, so a panel that failed
 * cannot blank a panel that succeeded.
 *
 * WHY THE 503 IS NOT AN ERROR STATE WITH A RETRY BUTTON. F1 is fail-closed and
 * its blast radius is the WHOLE recipe list: an unreadable `Pantry.md` means
 * every recipe's stock-derived chip colour is unavailable, and both rejected
 * fallbacks ("assume in stock", "assume not in stock") are worse than a refusal
 * — the second one produces a plausible-looking wrong answer. The server's 503
 * body is exactly `{requestId, code}`: there is no `recipes` key to render, and
 * no `message`, so anything this module says is its own copy. That is acceptable
 * here precisely because a 503 is PERMANENT until the user fixes the vault: the
 * user is told which file to fix, and no button invites a retry that cannot
 * help. A view that assumed a list existed would paint an empty screen, and an
 * empty screen next to an empty chip row reads as "nothing in your pantry" —
 * the single worst thing this app can say.
 *
 * `PANTRY_NOTE_LABEL` is a DISPLAY LABEL, not a configured path. The
 * authoritative value is the server's `PANTRY_NOTE_RELATIVE` (default
 * `Logistics/库存/Pantry.md`), which no response publishes — /health deliberately
 * leaks no server-owned path — so a client that must name the file names the
 * default. An operator who changes the setting gets a stale label, which is a
 * known and reported gap rather than a second source of truth.
 *
 * NO DOM AT MODULE SCOPE, and one import: `el()` from dom.js. Importable under
 * `node --test` through tests/js/app-version-hook.mjs.
 */

import { el } from './dom.js?v=__APP_VERSION__';

export const PANTRY_NOTE_LABEL = 'Logistics/库存/Pantry.md';

/** The two §9.16 fail-closed codes, plus the config-derived ones, by status. */
export function isSourceUnavailable(error) {
  return Boolean(error) && error.status === 503;
}

export function isStockUnavailable(error) {
  return isSourceUnavailable(error) && error.code === 'pantry_stock_unreadable';
}

export function panelState(node) {
  return node && node.dataset ? node.dataset.panelState : undefined;
}

/** `加载中…` — never an empty container, and never the string "no recipes". */
export function loadingState(label = '加载中…') {
  return el('p', { class: 'muted', dataset: { panelState: 'loading' }, text: label });
}

/**
 * The D3 empty state, shared by the recipe list (§8 story 14) and by every
 * shortlist (§8 story 39). It carries NO error affordance and no `banner-error`
 * class: an empty list is a normal state with nothing to retry, and a view that
 * styled it as a failure would teach the user that "no recipes" is a problem.
 */
export function emptyState({ title, body, dataset = {} }) {
  return el('div', { class: 'empty-state', dataset: { panelState: 'empty', ...dataset } }, [
    el('p', { class: 'empty-state__title', text: title }),
    body ? el('p', { class: 'muted', text: body }) : null,
  ]);
}

/**
 * A failure the user can act on: what happened, and what to do. `code` and
 * `requestId` are the server's own, printed verbatim so a report quotes the
 * server rather than this app.
 */
export function errorState({ title, detail, code, requestId, actions = [], dataset = {} }) {
  const trail = [code, requestId ? `requestId ${requestId}` : ''].filter(Boolean).join(' · ');
  return el('div', { class: 'error-state', dataset: { panelState: 'error', ...dataset } }, [
    el('p', { class: 'error-state__title', text: title }),
    detail ? el('p', { class: 'muted', text: detail }) : null,
    trail ? el('p', { class: 'muted', dataset: { role: 'error-trail' }, text: trail }) : null,
    actions.length ? el('div', { class: 'settings-actions' }, actions) : null,
  ]);
}

/**
 * F1's fail-closed state. Two shapes, because the two failures are different
 * repairs: an unreadable `Pantry.md` costs the chip COLOURS, an unreadable
 * catalog costs the matches as well.
 *
 * Deliberately no retry control, and deliberately explicit that nothing is
 * shown: the sentence that forbids "nothing in stock" from being inferred is
 * the reason this state is a named function rather than a string.
 */
export function sourceUnavailableState({ error, actions = [] }) {
  const stock = isStockUnavailable(error);
  const title = stock ? '读不到库存清单' : '读不到 Pantry Items 目录';
  const detail = stock
    ? `Pantry.md（${PANTRY_NOTE_LABEL}）现在读不出来，所以这一屏里所有食材的“在不在货”都算不出来。` +
      '这不是可以等一等的错误：把那份笔记修好、或者让它可读，然后回到这一屏。'
    : 'pantry_items.db 现在打不开，所以菜谱里的食材还没有被解析成任何 Pantry Item。' +
      '这不是可以等一等的错误：把目录文件修好，然后回到这一屏。';
  return el('div', { class: 'error-state', dataset: { panelState: 'unavailable', role: 'source-unavailable' } }, [
    el('p', { class: 'error-state__title', text: title }),
    el('p', { class: 'muted', text: detail }),
    // The one sentence that keeps an empty screen from reading as an empty
    // pantry. It is a separate node so a test can assert on it directly.
    el('p', {
      class: 'muted',
      dataset: { role: 'unavailable-note' },
      text: '这里没有显示任何食材 chip —— 这不代表你的厨房里什么都没有，只是 app 读不到库存。',
    }),
    el('p', {
      class: 'muted',
      dataset: { role: 'error-trail' },
      text: [error && error.code, error && error.requestId ? `requestId ${error.requestId}` : '']
        .filter(Boolean)
        .join(' · '),
    }),
    actions.length ? el('div', { class: 'settings-actions' }, actions) : null,
  ]);
}

/**
 * §9.2's `loadPanel(fetch, render)`, named.
 *
 * A panel has exactly three states and no fourth: `loading` is painted before
 * the fetch is even scheduled, so an empty container is never a possible
 * intermediate, and "no recipes" can therefore only ever be rendered by a
 * SUCCESSFUL empty response. `abort()` is what the view calls from `unmount()`;
 * a panel that settles after its view was torn down writes nothing.
 *
 * @returns {{status: string, aborted: boolean, abort: () => void}}
 */
export function loadPanel({ load, onLoading, onReady, onError }) {
  const panel = {
    status: 'loading',
    aborted: false,
    abort() {
      panel.aborted = true;
    },
  };
  onLoading();
  Promise.resolve()
    .then(load)
    .then((value) => {
      if (panel.aborted) return;
      panel.status = 'ready';
      onReady(value);
    })
    .catch((error) => {
      if (panel.aborted) return;
      panel.status = 'error';
      onError(error);
    });
  return panel;
}

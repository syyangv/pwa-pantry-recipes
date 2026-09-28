/* 设置 — the two toggles of F7 and F8, and nothing else.
 *
 * 调试 (F7): render-only, persisted in `localStorage` under
 * `pantry-recipes:debug` via prefs.js. No round-trip, no second endpoint, no
 * `?debug=1` — provenance is already in the payload.
 *
 * 严格模式（含调料） (F8): a STATELESS `?strict=1` query parameter. The control
 * rewrites `location.search` with `replaceState` and asks the router to
 * re-render, so the URL stays the single source of truth for the flag and no
 * server-side setting exists to drift from it. It is deliberately NOT saved
 * here: a persisted strict flag would need a round-trip before the first paint
 * and would be a second source of truth.
 *
 * Back is `goBack()` (template 2i) rather than a hardcoded `#/`, so a settings
 * page reached from a recipe detail returns there.
 *
 * `mount(root)` returns `unmount()`; every listener and timer it creates is
 * torn down there. That is the whole cross-view contract (spec 9.3).
 */

import { getSession, parseStrict } from '../api.js?v=__APP_VERSION__';
import { goBack, reload, saveViewState } from '../router.js?v=__APP_VERSION__';
import { isDebugEnabled, setDebugEnabled } from '../prefs.js?v=__APP_VERSION__';
import { el, row } from '../dom.js?v=__APP_VERSION__';

const DEBUG_NOTE = '渲染已有数据里的溯源信息，不请求服务器。';
const STRICT_NOTE = '把调料（Seasoning）也计入分数，状态只保留在网址里。';

function toggleButton(label, pressed) {
  const button = el('button', {
    type: 'button',
    class: 'toggle',
    'aria-pressed': pressed ? 'true' : 'false',
    text: label,
  });
  return button;
}

function sessionPanel() {
  const session = getSession();
  if (!session) {
    return el('p', { class: 'muted', text: '连接中…' });
  }
  const lines = [`身份：${session.identity || '—'}`];
  if (session.readOnly) {
    // F18: read-only has NO write allowlist. A mutation is refused outright, so
    // saying so up front beats letting the user discover it on a failed tap.
    lines.push('只读模式：所有写入都会被拒绝（没有例外名单）。');
  }
  if (session.appTimezone) lines.push(`应用时区：${session.appTimezone}`);
  return el('div', { class: 'muted' }, lines.map((line) => el('p', { text: line })));
}

export function mount(root) {
  const listeners = [];

  function listen(target, type, handler) {
    target.addEventListener(type, handler);
    listeners.push(() => target.removeEventListener(type, handler));
  }

  function render() {
    root.textContent = '';
    const debug = isDebugEnabled();
    const strict = parseStrict(window.location.search);

    const debugButton = toggleButton(debug ? '调试：开' : '调试：关', debug);
    listen(debugButton, 'click', () => {
      // A render-only switch: no filter changed, so nothing is written to the
      // view's history entry and Back still returns to the same scroll.
      setDebugEnabled(!isDebugEnabled());
      render();
    });

    const strictButton = toggleButton(
      strict ? '严格模式：开' : '严格模式：关',
      strict,
    );
    listen(strictButton, 'click', () => {
      const next = new URL(window.location.href);
      // F8: the flag IS the query parameter. replaceState, not an assignment —
      // a navigation would throw away the view stack the back button needs.
      next.searchParams.set('strict', strict ? '0' : '1');
      window.history.replaceState(window.history.state, '', next.toString());
      saveViewState({ filters: { strict: !strict } });
      render();
      reload();
    });

    const back = el('button', { type: 'button', class: 'button', text: '返回' });
    listen(back, 'click', () => goBack());

    root.appendChild(
      el('section', { class: 'panel', dataset: { view: 'settings' } }, [
        el('h2', { text: '设置' }),
        row('调试', debugButton, DEBUG_NOTE),
        row('严格模式（含调料）', strictButton, STRICT_NOTE),
        el('div', { class: 'settings-actions' }, [back]),
        el('h3', { class: 'settings-subhead', text: '连接' }),
        sessionPanel(),
      ]),
    );
  }

  render();
  // The boot session resolves after the first paint, so the connection block
  // re-renders itself rather than waiting for a route change.
  listen(window, 'pwa:awake', render);

  return function unmount() {
    for (const off of listeners.splice(0)) off();
    root.textContent = '';
  };
}

/* #/ — the recipe list. Route and view contract only.
 *
 * The list body itself (the D4 headline line, the chip row, the `minmax(0, 1fr)`
 * grid) is the next ticket's work; this module exists so the route resolves,
 * so the router's mount/unmount contract is exercised by a real view, and so
 * the page has an honest empty state instead of a blank screen.
 *
 * The two rules that are expensive to retrofit are already wired: `mount`
 * returns an `unmount` that drops every listener, and the scroll offset is
 * written into THIS view's own `history` entry with `replaceState` while the
 * user is still on it, so `history.back()` restores it (template 2i —
 * Playwright P14a asserts the restore end to end, which no unit test can).
 */

import { el, trackScroll } from '../dom.js?v=__APP_VERSION__';
import { saveViewState } from '../router.js?v=__APP_VERSION__';

export function mount(root) {
  // Scroll only: see the header note in router.js. A save from mount() or
  // unmount() would land in the wrong history entry, because the browser has
  // already pushed the destination by the time a hashchange handler runs.
  const save = () => saveViewState({ scrollTop: window.scrollY, filters: { strict: null, search: null } });
  const detachScroll = trackScroll(save);

  root.appendChild(
    el('section', { class: 'panel', dataset: { view: 'home' } }, [
      el('h2', { text: '今天能做什么' }),
      el('p', {
        class: 'muted',
        text: '菜谱列表还没有接上 API；这一屏的路由、外壳和返回行为已经就位。',
      }),
    ]),
  );

  return function unmount() {
    detachScroll();
    root.textContent = '';
  };
}

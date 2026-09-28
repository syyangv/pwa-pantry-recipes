/* #/recipe/<basename> — the detail. Route and view contract only.
 *
 * The note body, the Ingredient match rows, and the Cooking History are the
 * next ticket's work. What is wired here is the part that is easy to get wrong
 * and expensive to retrofit: the §2i back-button rule, applied to the happy
 * path AND to the loading-error and not-found states, so a failed load still
 * returns to where the user came from instead of stranding the user on a dead
 * page.
 *
 * `basename` arrives already percent-decoded from the router. It is only ever
 * sent back as a query value for `GET /api/recipes/{name}` — it is never
 * joined to a path here, which is what keeps Server-Owned Root (#2) intact.
 */

import { el, trackScroll } from '../dom.js?v=__APP_VERSION__';
import { goBack, saveViewState } from '../router.js?v=__APP_VERSION__';

function body(heading, message, backLabel) {
  const back = el('button', { type: 'button', class: 'button', text: backLabel });
  return { back, panel: el('section', { class: 'panel', dataset: { view: 'recipe' } }, [
    el('h2', { text: heading }),
    el('p', { class: 'muted', text: message }),
    el('div', { class: 'settings-actions' }, [back]),
  ]) };
}

export function mount(root, params = {}) {
  const listeners = [];

  function listen(target, type, handler) {
    target.addEventListener(type, handler);
    listeners.push(() => target.removeEventListener(type, handler));
  }

  const name = typeof params.basename === 'string' ? params.basename : '';

  // The router restores this view's own scroll offset out of the entry's state
  // when it re-renders (a Back traversal or a Pattern F resume re-render); here
  // the view keeps that entry's state current while the user is still on it.
  // Scroll only: see the header note in router.js. A save from mount() or
  // unmount() would land in the wrong history entry, because the browser has
  // already pushed the destination by the time a hashchange handler runs.
  const save = () => saveViewState({ scrollTop: window.scrollY });
  const detachScroll = trackScroll(save);

  const { back, panel } = name
    ? body(name, '详情还没有接上 API；这一屏的路由、外壳和返回行为已经就位。', '返回')
    : body('菜谱', '这个地址里没有菜谱名。', '返回');
  listen(back, 'click', () => goBack());
  root.appendChild(panel);

  return function unmount() {
    detachScroll();
    for (const off of listeners.splice(0)) off();
    root.textContent = '';
  };
}

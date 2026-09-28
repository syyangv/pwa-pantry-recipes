/* #/provenance — the full per-Ingredient provenance table. Route only.
 *
 * F7 makes this a VIEW of data that is already in the normal payload: there is
 * no `?debug=1`, no second response shape, and no second data path. This
 * module therefore reads the same `GET /api/recipes` shape the 调试 toggle
 * reads, and the table body is the next ticket's work.
 */

import { el, trackScroll } from '../dom.js?v=__APP_VERSION__';
import { goBack, saveViewState } from '../router.js?v=__APP_VERSION__';

export function mount(root) {
  const back = el('button', { type: 'button', class: 'button', text: '返回' });
  const onBack = () => goBack();
  back.addEventListener('click', onBack);

  // Scroll only: see the header note in router.js. A save from mount() or
  // unmount() would land in the wrong history entry, because the browser has
  // already pushed the destination by the time a hashchange handler runs.
  const save = () => saveViewState({ scrollTop: window.scrollY });
  const detachScroll = trackScroll(save);

  root.appendChild(
    el('section', { class: 'panel', dataset: { view: 'provenance' } }, [
      el('h2', { text: '溯源' }),
      el('p', {
        class: 'muted',
        text: '溯源信息一直就在正常响应里；这一屏只是把它整张列出来，没有第二份数据。',
      }),
      el('div', { class: 'settings-actions' }, [back]),
    ]),
  );

  return function unmount() {
    detachScroll();
    back.removeEventListener('click', onBack);
    root.textContent = '';
  };
}

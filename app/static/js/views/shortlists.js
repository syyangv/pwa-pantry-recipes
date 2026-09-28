/* #/shortlists — the three meal shortlists. Route and view contract only.
 *
 * D3 is the decision this view will render: Breakfast / Lunch / Dinner are
 * three user-curated shortlists in the PWA's own SQLite, the vault recipe
 * notes stay meal-free, and no recipe ever gains a meal classification. An
 * empty shortlist is a normal empty state here, not an error.
 *
 * Shortlist add is a post-action navigation (`#/shortlists` is the
 * destination), which is one of the two places a hardcoded `navigate()` is
 * correct — the other being a view whose only entry point is its own route.
 *
 * The scroll capture is not optional here: `#/shortlists` -> `#/recipe/<name>`
 * -> Back is the exact flow Playwright P14a drives, and it asserts the scroll
 * offset is restored. A view that does not record its own offset is a view the
 * assertion fails on.
 */

import { el, trackScroll } from '../dom.js?v=__APP_VERSION__';
import { saveViewState } from '../router.js?v=__APP_VERSION__';

export function mount(root) {
  // Scroll only: see the header note in router.js. A save from mount() or
  // unmount() would land in the wrong history entry, because the browser has
  // already pushed the destination by the time a hashchange handler runs.
  const save = () => saveViewState({ scrollTop: window.scrollY });
  const detachScroll = trackScroll(save);

  root.appendChild(
    el('section', { class: 'panel', dataset: { view: 'shortlists' } }, [
      el('h2', { text: '早 / 午 / 晚' }),
      el('p', {
        class: 'muted',
        text: '三个清单存在 PWA 自己的库里；库里的菜谱笔记不带餐次，一个空清单是正常的空状态。',
      }),
    ]),
  );

  return function unmount() {
    detachScroll();
    root.textContent = '';
  };
}

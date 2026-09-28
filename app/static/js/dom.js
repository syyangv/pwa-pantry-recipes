/* The smallest DOM helper the views share. No framework, no build step.
 *
 * `el` sets text through `textContent` and never through `innerHTML`, so a
 * recipe basename, a Pantry Item name, or a 调试 provenance string can never
 * become markup. CSP is `script-src 'self'` with no `unsafe-inline`, and the
 * one gap it cannot close is a string that reaches `innerHTML`.
 *
 * Importable under `node --test` (it touches no global at module scope), which
 * is why it is a separate module from the views that use it.
 */

export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = String(value);
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key === 'style') Object.assign(node.style, value);
    else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) node.setAttribute(key, '');
    else node.setAttribute(key, String(value));
  }
  for (const child of [].concat(children)) {
    if (child === undefined || child === null || child === false) continue;
    node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
  }
  return node;
}

export function clear(node) {
  if (node) node.textContent = '';
  return node;
}

/** A labelled control row; the row itself is the 44px tap target. */
export function row(label, control, note) {
  return el('div', { class: 'setting-row' }, [
    el('div', { class: 'setting-row__text' }, [
      el('span', { class: 'setting-row__label', text: label }),
      note ? el('span', { class: 'setting-row__note muted', text: note }) : null,
    ]),
    control,
  ]);
}

/**
 * rAF-throttled scroll capture, so a view can keep its position in its OWN
 * `history` entry with `replaceState` (template 2i). It has to be throttled:
 * an unthrottled `replaceState` on every scroll event is a structured-clone
 * and a history write per frame.
 *
 * WHY THE VIEW OWNS THIS: the router cannot do it. By the time a `hashchange`
 * dispatch runs, the browser has already pushed the destination entry, so a
 * `replaceState` there would write the outgoing view's scroll offset into the
 * INCOMING entry. The outgoing entry has to be updated while the user is still
 * on it, which means the view.
 *
 * @returns {() => void} detach
 */
export function trackScroll(save) {
  let frame = null;
  const onScroll = () => {
    if (frame !== null) return;
    const raf = globalThis.requestAnimationFrame || ((fn) => globalThis.setTimeout(fn, 16));
    frame = raf(() => {
      frame = null;
      save();
    });
  };
  globalThis.addEventListener('scroll', onScroll, { passive: true });
  return () => {
    if (frame !== null && globalThis.cancelAnimationFrame) globalThis.cancelAnimationFrame(frame);
    frame = null;
    globalThis.removeEventListener('scroll', onScroll);
  };
}


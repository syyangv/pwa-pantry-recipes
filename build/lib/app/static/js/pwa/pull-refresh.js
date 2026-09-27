/**
 * Optional wardrobe-proven pull-to-refresh interaction for no-build PWAs.
 *
 * The shared module owns gesture mechanics only. Apps retain refresh meaning
 * through canStart() and onRefresh() callbacks.
 */
export function initPullToRefresh(options = {}) {
  const indicator = options.indicator;
  const onRefresh = options.onRefresh;
  if (!indicator || typeof onRefresh !== 'function') return () => {};

  const eventTarget = options.eventTarget || document;
  const threshold = Number.isFinite(options.threshold) ? options.threshold : 72;
  const maxPull = threshold * 1.5;
  const revealRatio = 0.6;
  const canStart = typeof options.canStart === 'function' ? options.canStart : () => true;
  const getScrollTop = typeof options.getScrollTop === 'function'
    ? options.getScrollTop
    : () => Math.max(window.scrollY || 0, document.documentElement.scrollTop || 0);
  const onError = typeof options.onError === 'function' ? options.onError : () => {};

  let startY = 0;
  let deltaY = 0;
  let dragging = false;
  let refreshing = false;
  let resetTimer = null;

  function setProgress(dy) {
    const clamped = Math.min(dy, maxPull);
    const progress = Math.min(dy / threshold, 1);
    indicator.style.height = `${clamped * revealRatio}px`;
    indicator.style.opacity = String(progress);
    indicator.dataset.ready = progress >= 1 ? 'true' : 'false';
  }

  function reset() {
    indicator.style.transition = 'height 220ms ease, opacity 220ms ease';
    indicator.style.height = '0';
    indicator.style.opacity = '0';
    indicator.dataset.ready = 'false';
    if (resetTimer !== null) clearTimeout(resetTimer);
    resetTimer = setTimeout(() => {
      indicator.style.transition = '';
      resetTimer = null;
    }, 240);
    dragging = false;
    deltaY = 0;
  }

  function touchStart(event) {
    if (refreshing || event.touches.length !== 1 || getScrollTop() > 4 || !canStart()) return;
    startY = event.touches[0].clientY;
    deltaY = 0;
    dragging = true;
  }

  function touchMove(event) {
    if (!dragging || refreshing || event.touches.length !== 1) return;
    const dy = event.touches[0].clientY - startY;
    if (dy <= 0) {
      reset();
      return;
    }
    deltaY = dy;
    setProgress(dy);
  }

  function touchEnd() {
    if (!dragging) return;
    const triggered = deltaY >= threshold;
    reset();
    if (!triggered || refreshing) return;
    refreshing = true;
    Promise.resolve()
      .then(onRefresh)
      .catch(onError)
      .finally(() => { refreshing = false; });
  }

  function touchCancel() {
    if (dragging) reset();
  }

  eventTarget.addEventListener('touchstart', touchStart, { passive: true });
  eventTarget.addEventListener('touchmove', touchMove, { passive: true });
  eventTarget.addEventListener('touchend', touchEnd, { passive: true });
  eventTarget.addEventListener('touchcancel', touchCancel, { passive: true });

  return function destroyPullToRefresh() {
    eventTarget.removeEventListener('touchstart', touchStart);
    eventTarget.removeEventListener('touchmove', touchMove);
    eventTarget.removeEventListener('touchend', touchEnd);
    eventTarget.removeEventListener('touchcancel', touchCancel);
    if (resetTimer !== null) clearTimeout(resetTimer);
    indicator.style.transition = '';
    indicator.style.height = '0';
    indicator.style.opacity = '0';
    indicator.dataset.ready = 'false';
  };
}

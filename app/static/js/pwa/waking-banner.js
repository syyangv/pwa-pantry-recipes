/* ==========================================================================
 * pwa-infra waking-banner — covers a slow backend cold start (Pattern G).
 *
 * A launchd/Docker backend can take seconds to wake; on first load the app
 * looks frozen. Show a one-time "Connecting…" status while the first request
 * is slow and remove it when the app signals it is awake. { once: true } =
 * the banner appears only on the initial wake, never on transient reconnects.
 *
 * The app dispatches the awake event when its first real fetch succeeds:
 *   window.dispatchEvent(new Event('pwa:awake'));
 *
 * Usage (ES module):
 *   import { initWakingBanner } from '/js/pwa/waking-banner.js';
 *   initWakingBanner();
 * ========================================================================== */

export function initWakingBanner({
  text = 'Connecting…',
  className = 'waking-banner',
  events = { waking: 'pwa:waking', awake: 'pwa:awake' },
} = {}) {
  let banner = null;

  window.addEventListener(
    events.waking,
    () => {
      if (banner) return;
      banner = document.createElement('div');
      banner.className = className;
      banner.textContent = text;
      banner.setAttribute('role', 'status');
      document.body.appendChild(banner);
    },
    { once: true }
  );

  window.addEventListener(
    events.awake,
    () => {
      if (banner) {
        banner.remove();
        banner = null;
      }
    },
    { once: true }
  );
}

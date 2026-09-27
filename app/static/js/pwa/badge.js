/* ==========================================================================
 * pwa-infra badge — OS unread-count badge on the app icon (Pattern J).
 *
 * ⚠️ PLATFORM CONSTRAINTS (read before using):
 *   - navigator.setAppBadge()/clearAppBadge() are CHROMIUM-ONLY: Chrome/Edge
 *     on macOS, Windows, Android. iOS Safari / iPhone home-screen PWAs do NOT
 *     support the Badging API at all (WebKit, open since 2021) — the call
 *     silently no-ops. Do not rely on this for an iOS-targeted PWA.
 *   - The badge renders only on an INSTALLED app icon (Dock icon on macOS,
 *     taskbar on Windows, launcher on Android) — never on a browser tab.
 *   - iOS fallback: use an in-app banner/counter (e.g. "47 new today" pill)
 *     instead — iOS web apps cannot show icon badges.
 *
 * Usage (ES module):
 *   import { initBadge } from '/js/pwa/badge.js';
 *   initBadge({ statsEndpoint: '/api/stats', field: 'new_today' });
 * ========================================================================== */

export function initBadge({
  statsEndpoint = '/api/stats',
  field = 'new_today',
  onVisible = null, // called after clearing the badge (e.g. localStorage side-effects)
  onHidden = null,  // called before fetching/setting the badge
} = {}) {
  document.addEventListener('visibilitychange', async () => {
    if (document.visibilityState === 'visible') {
      try {
        if ('clearAppBadge' in navigator) await navigator.clearAppBadge();
      } catch {
        /* unsupported */
      }
      if (onVisible) onVisible();
    } else if (document.visibilityState === 'hidden') {
      if (onHidden) onHidden();
      try {
        if ('setAppBadge' in navigator) {
          const resp = await fetch(statsEndpoint);
          if (resp.ok) {
            const count = (await resp.json())[field] ?? 0;
            if (count > 0) await navigator.setAppBadge(count); // never show a zero
          }
        }
      } catch {
        /* unreachable or unsupported */
      }
    }
  });
}

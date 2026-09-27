/* ==========================================================================
 * pwa-infra update-manager — keeps an installed PWA off stale code.
 *
 * Implements docs/pwa-template.md Part 3:
 *   3c  update-check loop: pageshow, focus, visibilitychange, registration,
 *       and a heartbeat interval while active.
 *   D   controllerchange guard — no pointless reload on first install.
 *   E   skip-waiting reload flow — a banner's Reload button activates the
 *       waiting SW; Pattern D then performs the reload safely.
 *   F   iOS resume — visibilitychange + BFCache pageshow (e.persisted).
 *
 * Requirements:
 *   - The HTML shell sets window.__APP_VERSION__ to the same value the
 *     backend reports, e.g. <script>window.__APP_VERSION__='v0.1.0';</script>
 *   - The backend exposes GET /api/version (see python/pwa_version.py).
 *   - This module owns SW registration (swUrl option) — never register from
 *     index.html too (a second registration with a different URL, e.g. a
 *     version-pinned /sw.js?v=X, reinstalls the worker every load and can
 *     loop reloading).
 *
 * Explicit-update UX (banner + Reload button): pair sw.js WAIT_FOR_MESSAGE
 * with { autoApply: false, onStale: () => showBanner() } and wire the button
 * to requestUpdateReload().
 *
 * Usage (ES module):
 *   import { initUpdateManager, requestUpdateReload } from '/js/pwa/update-manager.js';
 *   initUpdateManager({ onStale: (v) => showUpdateBanner(v) });
 * ========================================================================== */

const DEFAULT_INTERVAL_MS = 5 * 60 * 1000;

let registrationPromise = null;

function registerServiceWorker(swUrl, swScope) {
  if (!('serviceWorker' in navigator)) return Promise.resolve(null);
  // Register the SW HERE — this is the ONE place. Registering from anywhere
  // else (especially with a different URL, e.g. a version-pinned /sw.js?v=X)
  // makes the browser reinstall the worker on every load: skipWaiting + claim
  // fire controllerchange, the page reloads, and two registrations can
  // alternate in an infinite reload loop. updateViaCache:'none' already makes
  // the browser byte-check /sw.js on every navigation, so no version pin is
  // ever needed for deploys to propagate.
  registrationPromise = navigator.serviceWorker.register(swUrl, {
    updateViaCache: 'none',
    ...(swScope ? { scope: swScope } : {}),
  });
  registrationPromise.catch(() => {}); // registration failure is non-fatal
  return registrationPromise;
}

function getRegistration() {
  return registrationPromise || Promise.resolve(null);
}

export function initUpdateManager({
  swUrl = '/sw.js',
  swScope = undefined,
  versionEndpoint = '/api/version',
  checkIntervalMs = DEFAULT_INTERVAL_MS,
  autoApply = true,
  canApplyUpdate = null,
  onStale = null,
  onFresh = null,
} = {}) {
  const hadController = Boolean(navigator.serviceWorker && navigator.serviceWorker.controller);
  let refreshing = false;
  let applyingUpdate = false;
  let versionCheckPromise = null;

  // Register the SW once, here. Do NOT register it anywhere else (see
  // registerServiceWorker above).
  registerServiceWorker(swUrl, swScope);

  /* Pattern D — controllerchange fires on first install too (page adopts a
   * controller it didn't have). Only reload on a real takeover. */
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (refreshing || !hadController) return;
      refreshing = true;
      window.location.reload();
    });
  }

  function navigateToVersionedRoot(version) {
    const url = new URL(window.location.href);
    url.searchParams.set('v', version); // force the HTML shell to re-fetch
    window.location.assign(url.toString());
  }

  function waitForInstallingWorker(registration) {
    const installing = registration && registration.installing;
    if (!installing) return Promise.resolve();
    return new Promise((resolve) => {
      let settled = false;
      let timeoutId = null;
      const finish = () => {
        if (settled) return;
        settled = true;
        installing.removeEventListener('statechange', onStateChange);
        if (timeoutId !== null) window.clearTimeout(timeoutId);
        resolve();
      };
      const onStateChange = () => {
        if (installing.state === 'installed' || installing.state === 'redundant') finish();
      };
      timeoutId = window.setTimeout(finish, 10000);
      installing.addEventListener('statechange', onStateChange);
      onStateChange();
    });
  }

  async function activateWaitingWorker(registration) {
    if (!registration.waiting && registration.installing) {
      await waitForInstallingWorker(registration);
    }
    if (!registration.waiting) return false;
    registration.waiting.postMessage({ type: 'SKIP_WAITING' });
    return true;
  }

  async function applyUpdate(serverVersion) {
    if (applyingUpdate) return;
    applyingUpdate = true;
    try {
      const reg = await getRegistration().catch(() => null);
      if (!reg) {
        // Service workers are unavailable; a versioned navigation is the only
        // cache-busting fallback left.
        navigateToVersionedRoot(serverVersion);
        return;
      }
      /* Wait for installation before reloading. Navigating immediately after
       * reg.update() races the install and can reload the old controller in a
       * loop. */
      if (await activateWaitingWorker(reg)) return;
      await reg.update().catch(() => {});
      if (await activateWaitingWorker(reg)) return;
      // Leave the page intact if no waiting worker appeared; the next guarded
      // version check can retry without starting another reload loop.
    } finally {
      applyingUpdate = false;
    }
  }

  async function checkVersionOnce() {
    if (document.visibilityState !== 'visible') return;
    let data;
    try {
      const resp = await fetch(versionEndpoint, { cache: 'no-store' });
      if (!resp.ok) return;
      data = await resp.json();
    } catch {
      return; // offline — ignore
    }
    if (!data || typeof data.version !== 'string') return;

    // Keep the server's response-token replacement from rewriting this
    // property access into invalid JavaScript such as window.v0.19.62.
    const pinned = window['__APP' + '_VERSION__'];
    if (pinned && pinned !== data.version) {
      if (onStale) onStale(data.version);
      /* Gate auto-apply: a busy page (open dialog/form, unsaved input) must
       * not be force-navigated. When blocked, this cycle is skipped and the
       * next pageshow/focus/interval tick retries. (template Part 3c: delay
       * the reload while a confirmation flow is in progress). */
      if (autoApply && (!canApplyUpdate || canApplyUpdate())) {
        await applyUpdate(data.version);
      }
    } else if (onFresh) {
      onFresh(data.version);
    }
  }

  async function checkVersion() {
    if (versionCheckPromise) return versionCheckPromise;
    const run = checkVersionOnce();
    versionCheckPromise = run;
    try {
      return await run;
    } finally {
      if (versionCheckPromise === run) versionCheckPromise = null;
    }
  }

  /* 3c — check on every surface trigger, plus a heartbeat while active. */
  window.addEventListener('pageshow', () => {
    if (document.visibilityState === 'visible') checkVersion();
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') checkVersion();
  });
  window.addEventListener('focus', checkVersion);
  setInterval(checkVersion, checkIntervalMs);

  /* Boot: refresh the SW itself, then a first version check once it settles. */
  getRegistration().then((reg) => {
    if (reg) reg.update().catch(() => {});
    setTimeout(checkVersion, 1500);
  });

  return { checkVersion, applyUpdate };
}

/* Pattern E — wire an update banner's Reload button to this. Activates a
 * waiting SW (Pattern D reloads on takeover); hard-reloads otherwise. */
export async function requestUpdateReload() {
  const reg = await getRegistration();
  if (reg && reg.waiting) {
    reg.waiting.postMessage({ type: 'SKIP_WAITING' });
    return;
  }
  window.location.reload();
}

/* Pantry Recipes PWA — module entry point.
 *
 * Boot contract for the scaffold:
 *   1. Publish window.__APP_VERSION__ from the serve-injected
 *      <meta name="app-version"> tag. The vendored pwa-infra update-manager
 *      reads that global (window['__APP' + '_VERSION__']) to decide whether the
 *      server is ahead of the running shell. Reading it from the meta tag
 *      instead of an inline <script> keeps index.html CSP-clean.
 *   2. initWakingBanner() BEFORE the first network call, so a slow launchd
 *      cold start shows "Connecting…" rather than a frozen screen.
 *   3. initUpdateManager() — this is the ONE place the service worker is
 *      registered. index.html must never call navigator.serviceWorker.register.
 *   4. dispatch 'pwa:awake' once the first real fetch resolves, which removes
 *      the waking banner.
 *
 * Every import uses ?v=__APP_VERSION__. The /js/{path} route in app/main.py
 * injects the token into module *content* as well, so nested ES-module imports
 * (which drop the query string) are version-pinned by the same single
 * CACHE_VERSION bump. Never hand-pin a literal version here.
 */

import { initUpdateManager, requestUpdateReload } from '/js/pwa/update-manager.js?v=__APP_VERSION__';
import { initWakingBanner } from '/js/pwa/waking-banner.js?v=__APP_VERSION__';

const meta = document.querySelector('meta[name="app-version"]');
const pinned = meta ? meta.getAttribute('content') : '';
if (pinned) window['__APP' + '_VERSION__'] = pinned;

// Wake the screen before any fetch can stall, then hand the service-worker
// lifecycle to update-manager (docs/pwa-template.md Pattern G + Part 3c).
initWakingBanner({ text: 'Connecting to pantry…' });
initUpdateManager({
  // auto-takeover (docs/pwa-template.md 4d) + Pattern 4e busy-guard: apply the
  // update without a forced reload, but never while a dialog/sheet is open.
  // Switch to { autoApply: false } + WAIT_FOR_MESSAGE=true in sw.js when the
  // app grows forms whose input an auto-reload could discard.
  autoApply: true,
  canApplyUpdate: () => !document.querySelector('dialog[open], .overlay:not([hidden])'),
  onStale: (version) => showUpdateBanner(version),
  onFresh: (version) => showVersionBadge(version),
});

function showUpdateBanner(version) {
  const host = document.getElementById('update-banner');
  if (!host || host.dataset.version === version) return;
  host.dataset.version = version;
  host.className = 'banner banner-update';
  host.textContent = `A new version (${version}) is available.`;
  const button = document.createElement('button');
  button.className = 'button';
  button.type = 'button';
  button.textContent = 'Reload';
  button.addEventListener('click', () => {
    button.textContent = 'Updating…';
    void requestUpdateReload();
    // Fallback: the browser may delay or drop controllerchange. Reload anyway.
    setTimeout(() => window.location.reload(), 1500);
  });
  host.appendChild(button);
}

function showVersionBadge(version) {
  const badge = document.getElementById('version-badge');
  if (badge) {
    badge.textContent = version;
    badge.hidden = false;
  }
  const scaffold = document.getElementById('scaffold-version');
  if (scaffold) scaffold.textContent = `Backend version ${version}`;
}

// TODO(implementation): replace this probe with the first real domain call
// (pantry catalog / recipe index) and dispatch 'pwa:awake' from its success
// path. The /health probe only proves the process is up.
fetch('/health', { cache: 'no-store' })
  .then((response) => (response.ok ? response.json() : null))
  .then((payload) => {
    if (payload && payload.version) showVersionBadge(payload.version);
  })
  .catch(() => {})
  .finally(() => window.dispatchEvent(new Event('pwa:awake')));

/* Pantry Recipes PWA — module entry point.
 *
 * Boot contract for the app shell:
 *   1. Publish window.__APP_VERSION__ from the serve-injected
 *      <meta name="app-version"> tag. The vendored pwa-infra update-manager
 *      reads that global (window['__APP' + '_VERSION__']) to decide whether the
 *      server is ahead of the running shell. Reading it from the meta tag
 *      instead of an inline <script> keeps index.html CSP-clean.
 *   2. initWakingBanner() BEFORE the first network call, so a slow launchd
 *      cold start shows "Connecting…" rather than a frozen screen.
 *   3. initUpdateManager() — this is the ONE place the service worker is
 *      registered. index.html must never call navigator.serviceWorker.register.
 *   4. initApi() — one GET /api/session for the CSRF token and the capability
 *      flags, shared by every later request. Its settle path dispatches
 *      'pwa:awake', which removes the waking banner.
 *   5. initRouter() + start() — mounts the first view.
 *
 * Every import uses ?v=__APP_VERSION__. The /js/{path} route in app/main.py
 * injects the token into module *content* as well, so nested ES-module imports
 * (which drop the query string) are version-pinned by the same single
 * CACHE_VERSION bump. Never hand-pin a literal version here.
 */

import { initUpdateManager, requestUpdateReload } from '/js/pwa/update-manager.js?v=__APP_VERSION__';
import { initWakingBanner } from '/js/pwa/waking-banner.js?v=__APP_VERSION__';
import { initApi, mutationInFlight } from '/js/api.js?v=__APP_VERSION__';
import { initOutbox } from '/js/domain-intents.js?v=__APP_VERSION__';
import { initRouter, reload as reloadCurrentView } from '/js/router.js?v=__APP_VERSION__';
import { mount as mountHome } from '/js/views/home.js?v=__APP_VERSION__';
import { mount as mountRecipe } from '/js/views/recipe.js?v=__APP_VERSION__';
import { mount as mountShortlists } from '/js/views/shortlists.js?v=__APP_VERSION__';
import { mount as mountSettings } from '/js/views/settings.js?v=__APP_VERSION__';
import { mount as mountProvenance } from '/js/views/provenance.js?v=__APP_VERSION__';

const meta = document.querySelector('meta[name="app-version"]');
const pinned = meta ? meta.getAttribute('content') : '';
if (pinned) window['__APP' + '_VERSION__'] = pinned;

/* F18 §4e busy-guard: never apply an update while a write is in flight or a
 * modal is open. The first term is not theoretical — a forced reload can land
 * between the user tapping 做过了 and the request completing, and the log is
 * lost. The second is template 4e: a date picker or dialog holds input a
 * reload would discard. */
function isModalOpen() {
  return Boolean(
    document.querySelector('dialog[open], .form-sheet:not([hidden]), .date-picker:not([hidden])'),
  );
}

function canApplyUpdate() {
  return !mutationInFlight() && !isModalOpen();
}

// Wake the screen before any fetch can stall, then hand the service-worker
// lifecycle to update-manager (docs/pwa-template.md Pattern G + Part 3c).
initWakingBanner({ text: 'Connecting to pantry…' });
initUpdateManager({
  /* F18 §4d, EXPLICIT BANNER — and this is the reconciliation with the
   * `autoApply: true` the scaffold shipped. It is not a default to keep: a
   * mutation-bearing app must not have a reload forced on it. sw.js sets
   * WAIT_FOR_MESSAGE = true to match, so a new worker installs and WAITS; the
   * Reload button below is what tells it to take over. Auto-takeover would be
   * a `SKIP_WAITING` on install, and the one moment it fires is whatever
   * moment the deploy lands — possibly between a cook-log tap and its
   * request. */
  autoApply: false,
  canApplyUpdate,
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
    // One click, one application: disable before the await so a double tap
    // cannot post two SKIP_WAITING messages (template 4d convergence guard).
    if (button.disabled) return;
    button.disabled = true;
    button.textContent = 'Updating…';
    // Pattern E: wake the waiting worker; the manager's controllerchange guard
    // performs the reload. The timeout is only the fallback for a browser that
    // drops controllerchange entirely.
    void requestUpdateReload();
    window.setTimeout(() => window.location.reload(), 1500);
  });
  host.appendChild(button);
}

function showVersionBadge(version) {
  const badge = document.getElementById('version-badge');
  if (badge) {
    badge.textContent = version;
    badge.hidden = false;
  }
}

function showRouteNotice(hash) {
  const host = document.getElementById('route-notice');
  if (!host) return;
  // Non-blocking: the home view is already mounted behind it. The hash is left
  // alone on purpose — rewriting it would push a history entry for a typo.
  host.textContent = `Unknown route: ${hash}`;
  host.hidden = false;
}

initRouter({
  views: {
    home: { mount: mountHome },
    recipe: { mount: mountRecipe },
    shortlists: { mount: mountShortlists },
    settings: { mount: mountSettings },
    provenance: { mount: mountProvenance },
  },
  onUnknownRoute: showRouteNotice,
}).start();

/* ONE outbox, created here and read by every view (§9.18.2, Part 6b).
 *
 * It is created *after* the router so the first view is already mounted and a
 * view that wants to write on mount finds a live outbox, and *before* the
 * `online` / `focus` / `visibilitychange` / interval triggers can fire — the
 * vendored adapter registers them in its constructor, so an outbox created later
 * would have missed a connectivity event that arrived in between.
 *
 * `replay` is the app's, not the adapter's, and that is Part 6b's whole point:
 * the app knows the endpoint, the CSRF token, the revision, and the UI semantics;
 * the adapter only knows about localStorage and flush ordering. `F18`'s pair is
 * untouched by this — the outbox registers a Background Sync tag the vendored
 * worker already handles, and the `autoApply: false` / `WAIT_FOR_MESSAGE: true`
 * pairing that keeps an update from being forced underneath a replay is set in
 * `main.js` and `sw.js` respectively and neither moved.
 */
initOutbox();

/* Pattern F — iOS resume. A BFCache restore can fire `pageshow` with
 * e.persisted WITHOUT a visibilitychange, and a Home Screen PWA resumes a
 * suspended snapshot without re-running startup code, so match data can be
 * minutes stale. Re-render the current view on both surfaces. */
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') reloadCurrentView();
});
window.addEventListener('pageshow', (event) => {
  if (event.persisted) reloadCurrentView();
});

/* The boot session is the first real domain call, so its settle path is what
 * dismisses the waking banner. `.finally`, not `.then`: a backend that is
 * refusing connections must still clear the banner or the app looks frozen
 * with nothing to retry. */
initApi()
  .then((session) => {
    if (session && session.version) showVersionBadge(session.version);
  })
  .catch(() => {})
  .finally(() => window.dispatchEvent(new Event('pwa:awake')));

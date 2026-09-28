/* ==========================================================================
 * pwa-infra service worker — the standardized app-shell worker.
 *
 * Implements the proven patterns from docs/pwa-template.md (Part 3):
 *   A  CACHE_VERSION is the single source of truth — the backend
 *      (python/pwa_version.py) regex-parses THIS file to serve /api/version.
 *   H  Configurable cache-first/network-first strategy for static assets.
 *   I  Network-only skip-list: APIs, uploads, health are NEVER cached
 *      (unless CACHE_API_GETS, deals-style, which wipes on write).
 *   6  Optional Background Sync wake-up: ask running clients to flush their
 *      page-owned outbox when the browser provides SyncManager.
 *   Wardrobe-proven defaults: cache-first shell + network-first navigation
 *      (so a stale index.html never pins old versioned asset URLs).
 *
 * CONFIG — edit only the block below. Bump CACHE_VERSION on EVERY deploy;
 * that single bump evicts stale shells and advances /api/version.
 * ========================================================================== */

/* ===== CONFIG ===== */
/* Pattern A single source of truth: /api/version, the FastAPI app version, and
 * the version injected into the HTML shell all derive from this constant, so
 * the served cache name and every reported version cannot drift. Bump on every
 * deploy. */
const CACHE_VERSION = 'v0.4.1';
const CACHE_NAME = `pwa-shell-${CACHE_VERSION}`;
const API_CACHE_NAME = `pwa-api-${CACHE_VERSION}`;
const OUTBOX_SYNC_TAG = 'outbox';
const NAVIGATION_FALLBACK_MS = 1500;

/* URLs to precache at install. Leave [] to runtime-cache only.
 * These are typically the HTML shell + manifest + every module/asset the
 * app needs offline. Update on every release that adds/removes files.
 *
 * THE LIST IS GATED, NOT MEMORISED. `tests/js/shell_assets.test.mjs` walks
 * app/static/js and app/static/css and fails when a file here is missing, in
 * the direction that matters: a module that is imported, served, and precached
 * by nothing works perfectly online and is simply absent from an installed
 * app — diagnosable only by a user on a subway. Add the file HERE in the same
 * commit that creates it (AGENTS.md #8). */
const SHELL_ASSETS = [
  // Version-pin mutable JS/CSS with CACHE_VERSION so a deploy (bump) always
  // fetches fresh copies — unversioned assets can be served stale by the
  // browser HTTP cache (304) + network-first SW (docs/pwa-template.md 3b).
  '/',
  '/manifest.webmanifest',
  '/css/pwa.css?v=' + CACHE_VERSION,
  '/css/tokens.css?v=' + CACHE_VERSION,
  '/css/styles.css?v=' + CACHE_VERSION,
  '/css/pull-refresh.css?v=' + CACHE_VERSION,
  '/js/main.js?v=' + CACHE_VERSION,
  // The shell graph: the hash router, the API client (CSRF lifecycle), the
  // DOM helper, and the render-only preference store.
  '/js/router.js?v=' + CACHE_VERSION,
  '/js/api.js?v=' + CACHE_VERSION,
  '/js/dom.js?v=' + CACHE_VERSION,
  '/js/prefs.js?v=' + CACHE_VERSION,
  // The five views behind the five routes. main.js imports all five, so the
  // boot graph already reaches them — but `shell_assets.test.mjs` requires every
  // module to be NAMED here, and rightly: a view added later and reached only
  // by a hash the user has never visited is not in any import graph yet, and a
  // precache that trusts the graph alone is how a route 404s offline.
  '/js/views/home.js?v=' + CACHE_VERSION,
  '/js/views/recipe.js?v=' + CACHE_VERSION,
  '/js/views/shortlists.js?v=' + CACHE_VERSION,
  '/js/views/settings.js?v=' + CACHE_VERSION,
  '/js/views/provenance.js?v=' + CACHE_VERSION,
  // The D4 display primitives, shared by the home and recipe views. They live
  // at the top of js/ rather than in a js/components/ directory on purpose: a
  // new subdirectory under static/js/ needs its own [tool.setuptools]
  // package-data glob, and pyproject.toml is owned by another agent this wave.
  // `static/js/*` already covers them, so a wheel ships them with no
  // registration step at all.
  '/js/chips.js?v=' + CACHE_VERSION,
  '/js/panels.js?v=' + CACHE_VERSION,
  '/js/pending-edits.js?v=' + CACHE_VERSION,
  // Pure client-side logic modules. Not imported by main.js itself, so they are
  // named here rather than pulled in by the boot graph; without the entries a
  // cold offline start would resolve them from the network and fail.
  '/js/logic/chip-class.js?v=' + CACHE_VERSION,
  '/js/logic/format.js?v=' + CACHE_VERSION,
  '/js/logic/sort.js?v=' + CACHE_VERSION,
  // Vendored pwa-infra modules imported by /js/main.js. They are part of the
  // boot graph, so a cold offline start needs them in the shell cache.
  '/js/pwa/update-manager.js?v=' + CACHE_VERSION,
  '/js/pwa/waking-banner.js?v=' + CACHE_VERSION,
];

/* Pattern I — never cache these. Entries ending in '/' are prefix matches
 * ('/api/' skips /api/*, /api/v1/*, ...); others are exact path matches. */
const NETWORK_ONLY_PREFIXES = ['/api/', '/images/'];
const NETWORK_ONLY_EXACT = ['/upload', '/health'];

/* Static-asset strategy:
 *   'cache-first'   — wardrobe-proven: precached shell served instantly,
 *                     network fallback on miss.
 *   'network-first' — Pattern H (face-world-cup): fresh when online,
 *                     offline fallback to cache. */
const FETCH_STRATEGY = 'cache-first';

/* Deals-style API caching: cache API GET responses and wipe the API cache on
 * any write. false = read-mostly apps keep APIs fully network-only (simplest). */
const CACHE_API_GETS = false;

/* API read strategy when CACHE_API_GETS is true:
 *   'network-first' — always fresh online, API-cache fallback offline (default)
 *   'swr'           — stale-while-revalidate (deals, proven): serve the cached
 *                     copy immediately and refresh in the background; when an
 *                     offline fallback serves stale data, notify every client
 *                     with { type: 'stale-data' } (the app's offline banner
 *                     listens). Applies to API_SWR_PREFIXES only. */
const API_STRATEGY = 'network-first';
const API_SWR_PREFIXES = ['/api/items', '/api/stats'];

/* Explicit-update mode (obsidian-daily style): when true, a NEW worker does
 * NOT skipWaiting on install — it installs and waits, and activates only when
 * the page posts { type: 'SKIP_WAITING' } (update-manager's
 * requestUpdateReload / applyUpdate). Pair with update-manager options
 * { autoApply: false, onStale: showBanner }. First install still activates
 * immediately (no active worker exists yet).
 *
 * F18 (locked), §4d: TRUE. A forced auto-takeover reload can land between the
 * user tapping 做过了 and the cook-log request completing, and the log is
 * lost. The banner in main.js is what tells a waiting worker to take over.
 * The scaffold shipped `false` + `autoApply: true`, which is the auto-takeover
 * variation; both sides move together or the banner never appears. */
const WAIT_FOR_MESSAGE = true;

/* Cache-name prefixes to evict on activate. Keep your app's HISTORICAL
 * prefixes here during/after a migration to this worker so old caches are
 * cleaned up instead of lingering (e.g. wardrobe-shell-, deals-shell-,
 * deals-api-, face-cup-, obsidian-daily-shell-, pwa-outlook-cal-sync-). */
const STALE_CACHE_PREFIXES = ['pwa-shell-', 'pwa-api-'];
/* ===== END CONFIG ===== */

/* Part 6 bonus: Background Sync is optional and the outbox remains
 * page-owned.  Feature-detect registration.sync rather than assuming that
 * SyncManager exists; unsupported browsers take the same path as before. */
function registerOutboxSync() {
  const syncManager = self.registration && self.registration.sync;
  if (!syncManager || typeof syncManager.register !== 'function') return Promise.resolve();
  try {
    return Promise.resolve(syncManager.register(OUTBOX_SYNC_TAG)).catch(() => {});
  } catch (_) {
    return Promise.resolve();
  }
}

/* ===== install: precache shell, take over immediately (or wait) ===== */
self.addEventListener('install', (event) => {
  if (!WAIT_FOR_MESSAGE) self.skipWaiting();
  if (SHELL_ASSETS.length === 0) return;
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      // cache:no-store guarantees the fresh shell is fetched from the network
      // rather than a stale HTTP-cached copy (the HTML shell is served without
      // explicit no-store on some backends).
      cache.addAll(SHELL_ASSETS.map((url) => new Request(url, { cache: 'no-store' })))
    )
  );
});

/* Explicit-update mode: the page posts this when the user confirms reload. */
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') self.skipWaiting();
});

/* ===== activate: drop stale caches, claim clients ===== */
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter(
              (key) =>
                STALE_CACHE_PREFIXES.some((prefix) => key.startsWith(prefix)) &&
                key !== CACHE_NAME &&
                key !== API_CACHE_NAME
            )
            .map((key) => caches.delete(key))
        )
      )
      .then(() => registerOutboxSync())
      .then(() => self.clients.claim())
  );
});

/* Optional Background Sync bonus: wake running, controlled clients and let
 * the page-owned outbox decide how to replay its durable intents. */
self.addEventListener('sync', (event) => {
  if (event.tag !== OUTBOX_SYNC_TAG) return;
  event.waitUntil(
    self.clients
      .matchAll({ type: 'window', includeUncontrolled: true })
      .then((clients) => {
        clients.forEach((client) => client.postMessage({ type: 'flush-outbox' }));
      })
      .catch(() => {})
  );
});

/* ===== fetch ===== */
function isNetworkOnly(url) {
  if (NETWORK_ONLY_PREFIXES.some((p) => url.pathname.startsWith(p))) return true;
  return NETWORK_ONLY_EXACT.includes(url.pathname);
}

function fetchAndCache(request, cacheName) {
  return fetch(request).then((response) => {
    if (response && response.ok) {
      const copy = response.clone(); // response is one-shot: clone to cache AND serve
      caches.open(cacheName).then((cache) => cache.put(request, copy)).catch(() => {});
    }
    return response;
  });
}

/* Network-first navigations need a small paint budget for installed PWAs.
 * A slow network must not leave the launch screen blank when an older shell
 * is already available, but a first visit with no shell must still wait for
 * the network instead of resolving with an empty response. */
function fetchNavigation(request) {
  // Bypass the browser HTTP cache so a stale HTML document cannot disagree
  // with a newer worker/API version. Scope fallbacks to this release's shell
  // cache so an unrelated runtime cache cannot win the navigation.
  const networkRequest = new Request(request, { cache: 'no-store' });
  const network = fetch(networkRequest).catch((error) =>
    caches.match('/', { cacheName: CACHE_NAME }).then((cached) => {
      if (cached) return cached;
      throw error;
    })
  );
  let timer;
  const cachedAfterBudget = new Promise((resolve) => {
    timer = setTimeout(resolve, NAVIGATION_FALLBACK_MS);
  })
    .then(() => caches.match('/', { cacheName: CACHE_NAME }))
    .then((cached) => cached || network);

  return Promise.race([network, cachedAfterBudget]).finally(() => {
    if (timer !== undefined) clearTimeout(timer);
  });
}

/* Stale-while-revalidate (deals, proven): serve the cached copy immediately,
 * refresh it in the background, and tell the page when offline data is stale.
 * A request with `Cache-Control: no-cache` bypasses the cached copy. */
function staleWhileRevalidate(request) {
  return caches.open(API_CACHE_NAME).then((cache) =>
    cache.match(request).then((cached) => {
      const skipCache = request.headers.get('Cache-Control') === 'no-cache';
      const networkFetch = fetch(request)
        .then((response) => {
          if (response && response.ok) {
            cache.put(request, response.clone());
          }
          return response;
        })
        .catch(() => {
          if (cached) {
            self.clients
              .matchAll()
              .then((clients) => clients.forEach((c) => c.postMessage({ type: 'stale-data' })));
          }
          return cached;
        });
      return cached && !skipCache ? cached : networkFetch;
    })
  );
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  /* Non-GET: never cache. Deals-style API cache is invalidated on write so the
   * next open is fresh — the "hands-off" half every strategy relies on. */
  if (request.method !== 'GET') {
    if (CACHE_API_GETS && url.pathname.startsWith('/api/')) {
      event.respondWith(
        caches
          .open(API_CACHE_NAME)
          .then((cache) =>
            cache.keys().then((keys) => Promise.all(keys.map((k) => cache.delete(k))))
          )
          .then(() => fetch(request))
      );
      return;
    }
    return; // browser handles the write directly
  }

  /* Pattern I skip-list — APIs, uploads, health probes must be real. */
  if (isNetworkOnly(url)) {
    if (CACHE_API_GETS) {
      if (API_STRATEGY === 'swr' && API_SWR_PREFIXES.some((p) => url.pathname.startsWith(p))) {
        event.respondWith(staleWhileRevalidate(request));
      } else {
        event.respondWith(
          fetchAndCache(request, API_CACHE_NAME).catch(() =>
            caches.match(request, { cacheName: API_CACHE_NAME })
          )
        );
      }
    }
    // else: straight to network, never cached.
    return;
  }

  /* Navigations: always try the network so version labels and module import
   * URLs advance; after a short paint budget, fall back to an existing shell
   * so an installed PWA does not show a blank launch screen. */
  if (request.mode === 'navigate') {
    event.respondWith(fetchNavigation(request));
    return;
  }

  /* Static assets: chosen strategy. */
  if (FETCH_STRATEGY === 'network-first') {
    event.respondWith(
      fetchAndCache(request, CACHE_NAME).catch(() =>
        caches.match(request, { cacheName: CACHE_NAME })
      )
    );
  } else {
    event.respondWith(
      caches
        .match(request, { cacheName: CACHE_NAME })
        .then((cached) => cached || fetchAndCache(request, CACHE_NAME))
    );
  }
});

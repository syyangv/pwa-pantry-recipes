/* The API client: the CSRF header lifecycle and the render-cache key.
 * docs/pwa-template.md Part 3, spec 9.16 / 9.17.
 *
 * Origin is never set by this module — the browser sends `Origin` on every
 * same-origin mutation and app/auth.py compares it against PUBLIC_ORIGIN. A
 * hand-written `Origin` header is both forbidden by fetch and a lie.
 *
 * THE CSRF LIFECYCLE: `GET /api/session` issues the token (43 chars, 3600 s
 * TTL, a rolling window of 16 so two tabs do not race). The token is fetched
 * ONCE at boot and echoed as `X-CSRF-Token` on every POST/PUT/PATCH/DELETE.
 * app/auth.py answers a stale or unknown token with 403 `csrf_invalid`, and
 * this module refreshes exactly once per call and replays it — a second
 * failure is the caller's problem, because retrying forever against a
 * `read_only` or `origin_not_allowed` rejection just hides it.
 *
 * (The spec and ticket say "401 csrf_invalid"; the shipped guard is 403 — the
 * status is 403 for every CSRF rejection. The refresh is keyed on the
 * `csrf_invalid` CODE, not the status, so it survives either.)
 *
 * F4: an error carries the server's `message` VERBATIM in `error.message`, so a
 * view renders what the server said rather than composing a vaguer sentence of
 * its own — that is what keeps there being exactly one wording to keep right.
 * `error.data` still holds the whole body, `date` / `relativePath` /
 * `retryable` / `currentRevision` included.
 *
 * F7: provenance is unconditional. There is no `?debug=1`, no second
 * response shape, and no reduced payload — the 调试 toggle is a render
 * switch in prefs.js, over the same JSON.
 *
 * F8: `严格模式` is a stateless `?strict=1` query parameter and nothing else.
 * No persisted server-side setting, no extra endpoint.
 *
 * NO DOM AT MODULE SCOPE and no imports, for the same reason router.js has
 * none: `node --test` can import this file directly. `globalThis.fetch` is
 * read at call time, so a test can stub it.
 */

const MUTATION_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
const CSRF_HEADER = 'X-CSRF-Token';
const CSRF_INVALID = 'csrf_invalid';

export class ApiError extends Error {
  constructor({ status = 0, code = 'request_failed', message = '', requestId = null, data = null } = {}) {
    super(message || code);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.data = data;
  }

  /** 401/403 identity or CSRF rejections mean "who are you", not "try later". */
  get isAuth() {
    return this.status === 401 || this.code.startsWith('identity_') || this.code === CSRF_INVALID;
  }
}

let sessionValue = null;
let sessionPromise = null;
let inflightMutations = 0;

/** True while any mutation is in flight. F18's busy-guard reads this. */
export function mutationInFlight() {
  return inflightMutations > 0;
}

export function getSession() {
  return sessionValue;
}

export function isReadOnly() {
  return Boolean(sessionValue && sessionValue.readOnly);
}

export function apiUrl(path) {
  if (typeof path !== 'string' || path === '') throw new TypeError('apiUrl requires a path');
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(path)) {
    // A cross-origin URL here would be both a CSP violation and a way to send
    // the CSRF token somewhere it does not belong.
    throw new ApiError({ code: 'external_url_rejected', message: 'external URLs are not allowed' });
  }
  return path.startsWith('/') ? path : `/api/${path}`;
}

async function readBody(response) {
  if (response.status === 204) return null;
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function toApiError(response, body) {
  const code = (body && body.code) || (response.status === 404 ? 'not_found' : 'http_error');
  return new ApiError({
    status: response.status,
    code,
    requestId: (body && body.requestId) || response.headers.get('X-Request-ID'),
    // F4 (spec 9.15): the server's `message` is the SINGLE source of wording —
    // the UI renders `error.message` verbatim and composes no copy of its own,
    // which is what keeps there being exactly one sentence to keep correct. Only
    // two codes send one; every other code still sends `{requestId, code}`, and
    // the constructor's `code` fallback then reads exactly as it did before.
    message: (body && body.message) || '',
    data: body,
  });
}

/** One shared boot request: concurrent callers await the same promise. */
export function ensureSession() {
  if (sessionValue) return Promise.resolve(sessionValue);
  if (!sessionPromise) {
    sessionPromise = fetch(apiUrl('/api/session'), {
      cache: 'no-store',
      credentials: 'same-origin',
    })
      .then(async (response) => {
        const body = await readBody(response);
        if (!response.ok) throw toApiError(response, body);
        sessionValue = body;
        return body;
      })
      .finally(() => {
        sessionPromise = null;
      });
  }
  return sessionPromise;
}

/** Re-read the session to pick up a rotated CSRF token. */
export function refreshSession() {
  sessionValue = null;
  return ensureSession();
}

export function initApi() {
  return ensureSession();
}

export function resetApi() {
  sessionValue = null;
  sessionPromise = null;
  inflightMutations = 0;
}

/**
 * @param {object} options
 * @param {string} [options.method]
 * @param {unknown} [options.body]        serialized as JSON when present
 * @param {AbortSignal} [options.signal]   the view's own controller
 * @param {Record<string,string>} [options.headers]
 */
export async function apiFetch(path, options = {}) {
  const url = apiUrl(path);
  const method = String(options.method || 'GET').toUpperCase();
  const mutation = MUTATION_METHODS.has(method);
  const headers = { ...(options.headers || {}) };
  if (options.body !== undefined && options.body !== null) {
    headers['Content-Type'] = 'application/json';
  }

  if (mutation) {
    // The token is fetched at boot; this await is free after that and is what
    // keeps a mutation issued during a slow cold start from going out bare.
    const session = await ensureSession();
    if (session && session.csrfToken) headers[CSRF_HEADER] = session.csrfToken;
  }

  inflightMutations += mutation ? 1 : 0;
  try {
    return await send(url, method, headers, options);
  } finally {
    inflightMutations -= mutation ? 1 : 0;
  }
}

async function send(url, method, headers, options) {
  const init = {
    method,
    headers,
    // Every /api/* response is Cache-Control: no-store server-side (spec
    // 9.16); the client asks for the same so the HTTP cache cannot disagree.
    cache: 'no-store',
    credentials: 'same-origin',
    ...(options.signal ? { signal: options.signal } : {}),
  };
  if (options.body !== undefined && options.body !== null) init.body = JSON.stringify(options.body);

  const response = await globalThis.fetch(url, init);
  if (response.ok) return readBody(response);

  const body = await readBody(response);
  const error = toApiError(response, body);
  if (error.code !== CSRF_INVALID) throw error;

  // Exactly one refresh, exactly one replay. A second `csrf_invalid` is a real
  // rejection (read_only, origin, or a token store that was cleared) and is
  // surfaced instead of retried.
  await refreshSession();
  const retryHeaders = { ...headers };
  const token = sessionValue && sessionValue.csrfToken;
  if (token) retryHeaders[CSRF_HEADER] = token;
  const retried = await globalThis.fetch(url, { ...init, headers: retryHeaders });
  if (retried.ok) return readBody(retried);
  throw toApiError(retried, await readBody(retried));
}

/* --- F8: 严格模式 is a query parameter, nothing else ---------------------- */

export function strictQuery(strict) {
  return strict ? 'strict=1' : 'strict=0';
}

export function withStrict(path, strict) {
  const separator = path.includes('?') ? '&' : '?';
  return `${path}${separator}${strictQuery(strict)}`;
}

export function parseStrict(search) {
  return /(^|[?&])strict=1(&|$)/.test(String(search || ''));
}

/* --- The render cache (template 5c) ---------------------------------------
 *
 * A cached render is legal only when its key includes EVERY input the render
 * consumes. The recipe list's inputs are exactly the two source revisions and
 * the strict flag, so the key is exactly `{catalogRevision, stockRevision,
 * strict}` and nothing else. The 30 s stock TTL (F11) is the maximum staleness
 * a chip colour can have, because the user edits tasks in Obsidian while this
 * PWA is open — the key has to move when the stock does, or the chip lies.
 *
 * What is cached is the FETCH RESULT, not painted HTML. That is why the
 * render-only 调试 flag is deliberately not in the key: it is applied to the
 * same payload after the cache read (F7), so including it would make the key
 * describe a rendering concern instead of a data one.
 */
export function recipeListCacheKey({ catalogRevision, stockRevision, strict } = {}) {
  return JSON.stringify([
    catalogRevision === undefined ? null : catalogRevision,
    stockRevision === undefined ? null : stockRevision,
    strict ? 1 : 0,
  ]);
}

export class RenderCache {
  constructor(limit = 1) {
    this.limit = limit;
    this.entries = new Map();
  }

  get(key) {
    if (!this.entries.has(key)) return undefined;
    // Re-insert: Map preserves insertion order, so this makes the read key the
    // most recent one and lets the limit evict the genuinely oldest.
    const value = this.entries.get(key);
    this.entries.delete(key);
    this.entries.set(key, value);
    return value;
  }

  set(key, value) {
    if (this.entries.has(key)) this.entries.delete(key);
    this.entries.set(key, value);
    while (this.entries.size > this.limit) {
      this.entries.delete(this.entries.keys().next().value);
    }
    return value;
  }

  clear() {
    this.entries.clear();
  }
}

export const recipeListCache = new RenderCache(1);

/* Render-only preferences. F7.
 *
 * `调试` lives in `localStorage` under `pantry-recipes:debug` and NOWHERE else.
 * It is not in SQLite, not in a query parameter, and not a second endpoint:
 * provenance is already in every `GET /api/recipes` payload, so the toggle
 * costs a repaint and nothing else. A toggle that needs a round-trip is a
 * toggle the user will not reach, and a second response shape is a second thing
 * that can drift from the first (spec 9.16, F7).
 *
 * `严格模式` is deliberately NOT here. F8 makes it a stateless `?strict=1`
 * query parameter: the server computes the score, so a persisted setting would
 * need a round-trip before the first paint and would be a second source of
 * truth for a value the URL already carries.
 *
 * The store degrades to an in-memory map when localStorage is unavailable
 * (private browsing, a storage-policy iframe), so a view never has to guard.
 */

export const DEBUG_KEY = 'pantry-recipes:debug';
export const DEBUG_EVENT = 'pantry:debug-change';

const memory = new Map();

function storage() {
  try {
    const candidate = globalThis.localStorage;
    if (!candidate || typeof candidate.getItem !== 'function') return null;
    return candidate;
  } catch {
    return null;
  }
}

export function isDebugEnabled() {
  const store = storage();
  const raw = store ? store.getItem(DEBUG_KEY) : memory.get(DEBUG_KEY);
  if (raw === null || raw === undefined) return false;
  return raw === '1' || raw === 'true';
}

export function setDebugEnabled(enabled) {
  const value = enabled ? '1' : '0';
  const store = storage();
  if (store) store.setItem(DEBUG_KEY, value);
  else memory.set(DEBUG_KEY, value);
  // Views re-read the flag rather than being told which DOM to rebuild, so a
  // change made on the settings view reaches an open list without a round-trip.
  if (globalThis.dispatchEvent) {
    globalThis.dispatchEvent(new CustomEvent(DEBUG_EVENT, { detail: { enabled: Boolean(enabled) } }));
  }
  return Boolean(enabled);
}

export function toggleDebug() {
  return setDebugEnabled(!isDebugEnabled());
}

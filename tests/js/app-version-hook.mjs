/* A Node ESM resolve hook that neutralises the serve-time version token.
 *
 * `app/static/js/**` carries `?v=__APP_VERSION__` on every import, and it must:
 * `scripts/check-modules.mjs` and `tests/js/scaffold.test.mjs` both fail a
 * specifier without it, because the `/js/{path}` route injects the real
 * `CACHE_VERSION` into module CONTENT and a literal version would be a second,
 * driftable source.
 *
 * The consequence for testing is that the browser modules are not importable
 * from `node --test` as written — `?v=__APP_VERSION__` is not a path Node can
 * resolve. This hook is the whole answer to that, and it is deliberately as
 * narrow as possible: it rewrites exactly one literal suffix and delegates
 * every other resolution decision to Node, so a genuinely bad import still
 * fails here exactly as it would in `npm run check`.
 *
 * It is registered with `module.register()` from a test file BEFORE a dynamic
 * `import()` of the module under test, which is why those imports are dynamic.
 * `app/static/js/logic/*.js` need none of this — they import nothing at all,
 * which is what keeps them testable without a DOM.
 */

const TOKEN = '?v=__APP_VERSION__';

export function resolve(specifier, context, nextResolve) {
  if (specifier.endsWith(TOKEN)) {
    return nextResolve(specifier.slice(0, -TOKEN.length), context);
  }
  return nextResolve(specifier, context);
}

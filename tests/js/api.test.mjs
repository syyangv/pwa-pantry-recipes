/* API client gates: the CSRF header lifecycle and the render-cache key
 * (node --test, no deps).
 *
 * `api.js` reads `globalThis.fetch` at call time and imports nothing, so this
 * file drives the real client against a stub transport and asserts the two
 * things a browser would otherwise have to be trusted for:
 *
 *   - every mutation carries the `X-CSRF-Token` from the boot session, and
 *     exactly ONE refresh-and-replay happens on `csrf_invalid` (a second one
 *     is a real rejection — `read_only`, a foreign Origin — and retrying it
 *     forever hides the reason the user is actually stuck);
 *   - the recipe list's render-cache key is exactly {catalogRevision,
 *     stockRevision, strict}, so no render can be served from a cache that
 *     was built from inputs it did not consume.
 *
 * The status the shipped guard uses is asserted too: app/auth.py answers
 * `csrf_invalid` with 403, not the 401 the spec text says. The refresh is
 * keyed on the CODE precisely so neither number is load-bearing.
 */

import assert from 'node:assert/strict';
import test from 'node:test';

import {
  ApiError,
  RenderCache,
  apiFetch,
  apiUrl,
  initApi,
  mutationInFlight,
  parseStrict,
  recipeListCacheKey,
  refreshSession,
  resetApi,
  strictQuery,
  withStrict,
} from '../../app/static/js/api.js';

const TOKEN_A = 'token-a';
const TOKEN_B = 'token-b';

function sessionBody(token, overrides = {}) {
  return {
    identity: 'syang@example.com',
    csrfToken: token,
    version: 'v0.3.0',
    readOnly: false,
    appTimezone: 'America/Los_Angeles',
    ...overrides,
  };
}

/** A stub transport that records every call and replays queued responses. */
function stubFetch(responder) {
  const calls = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url, init });
    return responder(url, init, calls.length);
  };
  return {
    calls,
    restore() {
      globalThis.fetch = original;
    },
  };
}

function json(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => null },
    async json() {
      return body;
    },
  };
}

test.afterEach(() => {
  resetApi();
});

test('apiUrl rejects a cross-origin URL outright', () => {
  assert.equal(apiUrl('/api/recipes'), '/api/recipes');
  assert.equal(apiUrl('recipes'), '/api/recipes');
  // A hand-written Origin is forbidden by fetch, and a cross-origin URL here
  // would be a way to send the CSRF token somewhere it does not belong.
  assert.throws(() => apiUrl('https://example.com/api/recipes'), ApiError);
  assert.throws(() => apiUrl(''), TypeError);
});

test('a GET is not sent with a CSRF header and issues no session call', async () => {
  const stub = stubFetch(() => json({ recipes: [] }));
  try {
    const payload = await apiFetch('/api/recipes');
    assert.deepEqual(payload, { recipes: [] });
    assert.equal(stub.calls.length, 1);
    assert.equal(stub.calls[0].init.headers['X-CSRF-Token'], undefined);
  } finally {
    stub.restore();
  }
});

test('a mutation carries the token from the boot session', async () => {
  const stub = stubFetch((url) =>
    url === '/api/session' ? json(sessionBody(TOKEN_A)) : json({ status: 'logged' }, 201),
  );
  try {
    await initApi();
    assert.equal(stub.calls[0].url, '/api/session');
    assert.equal(stub.calls[0].init.headers, undefined, 'the session call carries no token');
    await apiFetch('/api/cook-logs', { method: 'POST', body: { recipeNote: '拌空心菜' } });
    const mutation = stub.calls.at(-1);
    assert.equal(mutation.init.method, 'POST');
    assert.equal(mutation.init.headers['X-CSRF-Token'], TOKEN_A);
    assert.equal(mutation.init.headers['Content-Type'], 'application/json');
    assert.equal(mutation.init.body, JSON.stringify({ recipeNote: '拌空心菜' }));
  } finally {
    stub.restore();
  }
});

function getTokenOf(stub, url) {
  return stub.calls.find((call) => call.url === url)?.init.headers?.['X-CSRF-Token'];
}

test('the boot session is fetched once and shared by concurrent callers', async () => {
  let sessions = 0;
  const stub = stubFetch((url) => {
    if (url === '/api/session') {
      sessions += 1;
      return json(sessionBody(TOKEN_A));
    }
    return json({ ok: true });
  });
  try {
    await Promise.all([apiFetch('/api/shortlists', { method: 'POST', body: {} }), initApi()]);
    assert.equal(sessions, 1, 'two callers, one session request');
    assert.equal(getTokenOf(stub, '/api/shortlists'), TOKEN_A);
  } finally {
    stub.restore();
  }
});

test('a stale token is refreshed exactly once and the call is replayed', async () => {
  let sessions = 0;
  const stub = stubFetch((url, init) => {
    if (url === '/api/session') {
      sessions += 1;
      return json(sessionBody(sessions === 1 ? TOKEN_A : TOKEN_B));
    }
    if (init.headers['X-CSRF-Token'] === TOKEN_A) {
      return json({ requestId: 'r1', code: 'csrf_invalid' }, 403);
    }
    return json({ status: 'logged' }, 201);
  });
  try {
    await initApi();
    const payload = await apiFetch('/api/cook-logs', { method: 'POST', body: { date: '2026-09-27' } });
    assert.deepEqual(payload, { status: 'logged' });
    assert.equal(sessions, 2, 'one refresh, not a retry loop');
    // The replay carries the NEW token, and the old one is not resent.
    const replay = stub.calls.at(-1);
    assert.equal(replay.init.headers['X-CSRF-Token'], TOKEN_B);
    const attempts = stub.calls.filter((call) => call.url === '/api/cook-logs');
    assert.equal(attempts.length, 2);
    assert.equal(attempts[0].init.headers['X-CSRF-Token'], TOKEN_A);
    assert.equal(attempts[1].init.headers['X-CSRF-Token'], TOKEN_B);
  } finally {
    stub.restore();
  }
});

test('a second csrf_invalid surfaces as an error instead of retrying', async () => {
  let sessions = 0;
  const stub = stubFetch((url) => {
    if (url === '/api/session') {
      sessions += 1;
      return json(sessionBody(`token-${sessions}`));
    }
    return json({ requestId: 'r1', code: 'csrf_invalid' }, 403);
  });
  try {
    await initApi();
    await assert.rejects(
      () => apiFetch('/api/cook-logs', { method: 'POST', body: {} }),
      (error) => {
        assert.ok(error instanceof ApiError);
        // The status the SHIPPED guard uses is 403; the spec text says 401.
        // Keying the refresh on the code is what makes that irrelevant.
        assert.equal(error.status, 403);
        assert.equal(error.code, 'csrf_invalid');
        return true;
      },
    );
    assert.equal(sessions, 2, 'exactly one refresh, then stop');
    assert.equal(
      stub.calls.filter((call) => call.url === '/api/cook-logs').length,
      2,
      'no third attempt',
    );
  } finally {
    stub.restore();
  }
});

test('read_only is surfaced as an ApiError, not a silent empty result', async () => {
  const stub = stubFetch((url) =>
    url === '/api/session'
      ? json(sessionBody(TOKEN_A, { readOnly: true }))
      : json({ requestId: 'r2', code: 'read_only' }, 403),
  );
  try {
    await initApi();
    await assert.rejects(() => apiFetch('/api/shortlists/breakfast', { method: 'POST' }), {
      code: 'read_only',
    });
  } finally {
    stub.restore();
  }
});

test('the in-flight mutation counter brackets exactly the mutation', async () => {
  let seen = null;
  const stub = stubFetch(async () => {
    seen = mutationInFlight();
    return json({ ok: true });
  });
  try {
    await initApi();
    assert.equal(mutationInFlight(), false);
    await apiFetch('/api/cook-logs', { method: 'POST', body: {} });
    assert.equal(seen, true, 'a reload guard sees the write in flight');
    assert.equal(mutationInFlight(), false, 'and stops seeing it afterwards');
  } finally {
    stub.restore();
  }
});

test('a GET does not move the in-flight counter', async () => {
  const stub = stubFetch(() => json({ recipes: [] }));
  try {
    await apiFetch('/api/recipes');
    assert.equal(mutationInFlight(), false);
  } finally {
    stub.restore();
  }
});

test('refreshSession re-reads the token on demand', async () => {
  let sessions = 0;
  const stub = stubFetch((url) => {
    if (url === '/api/session') {
      sessions += 1;
      return json(sessionBody(`token-${sessions}`));
    }
    return json({});
  });
  try {
    await initApi();
    await refreshSession();
    assert.equal(sessions, 2);
    await apiFetch('/api/cook-logs', { method: 'POST', body: {} });
    assert.equal(getTokenOf(stub, '/api/cook-logs'), 'token-2');
  } finally {
    stub.restore();
  }
});

/* --- F4: the server's wording is the only wording ---------------------- */

test("the error message is the server's, verbatim", async () => {
  // Exactly the body `app/api/cooklog.py` emits for a missing daily note, so
  // this gate is the client's half of "one source of wording": the view renders
  // `error.message` and the sentence in the vault's language is the one the
  // server wrote. A client that composed its own copy here would be a second
  // wording to keep correct — and a UI that showed `daily_note_missing` where
  // the server said 找不到 … would be showing a code as an explanation.
  const message =
    '找不到 2026-09-27 的日记：日记/2026/2026-09-27.md。请先在 Obsidian 中创建这一天的日记，然后重试。';
  const stub = stubFetch((url) =>
    url === '/api/session'
      ? json(sessionBody(TOKEN_A))
      : json(
          {
            requestId: 'r9',
            code: 'daily_note_missing',
            message,
            date: '2026-09-27',
            relativePath: '日记/2026/2026-09-27.md',
            retryable: true,
          },
          404,
        ),
  );
  try {
    await initApi();
    const error = await apiFetch('/api/cook-logs', { method: 'POST', body: {} }).then(
      () => null,
      (rejection) => rejection,
    );
    assert.ok(error instanceof ApiError);
    assert.equal(error.message, message, 'rendered verbatim, not summarised');
    assert.equal(error.code, 'daily_note_missing');
    assert.equal(error.status, 404);
    // The extension stays readable for a view that needs the path or the retry
    // affordance, without the message having to be parsed back out of a string.
    assert.equal(error.data.date, '2026-09-27');
    assert.equal(error.data.relativePath, '日记/2026/2026-09-27.md');
    assert.equal(error.data.retryable, true);
  } finally {
    stub.restore();
  }
});

test('a two-key envelope still reads as its code, with no message invented', async () => {
  // The extension is additive: every pre-existing code sends `{requestId, code}`
  // and nothing else, so the fallback has to be the code and never a string
  // this module made up.
  const stub = stubFetch((url) =>
    url === '/api/session' ? json(sessionBody(TOKEN_A)) : json({ requestId: 'r1', code: 'invalid_recipe_note' }, 422),
  );
  try {
    await initApi();
    const error = await apiFetch('/api/cook-logs', { method: 'POST', body: {} }).then(
      () => null,
      (rejection) => rejection,
    );
    assert.equal(error.message, 'invalid_recipe_note');
  } finally {
    stub.restore();
  }
});

/* --- F8: the strict flag is a query parameter --------------------------- */

test('strict is a query parameter and nothing else', () => {
  assert.equal(strictQuery(true), 'strict=1');
  assert.equal(strictQuery(false), 'strict=0');
  assert.equal(withStrict('/api/recipes', true), '/api/recipes?strict=1');
  assert.equal(withStrict('/api/recipes', false), '/api/recipes?strict=0');
  assert.equal(withStrict('/api/recipes?x=1', true), '/api/recipes?x=1&strict=1');
  assert.equal(parseStrict('?strict=1'), true);
  assert.equal(parseStrict('?strict=0'), false);
  assert.equal(parseStrict(''), false);
  // `strict=1` anywhere in the query counts; `xstrict=1` is a different param.
  assert.equal(parseStrict('?a=1&strict=1&b=2'), true);
  assert.equal(parseStrict('?xstrict=1'), false);
});

/* --- template 5c: the render cache key ---------------------------------- */

test('the recipe list cache key is exactly the three inputs it consumes', () => {
  const base = { catalogRevision: 7, stockRevision: 3, strict: false };
  const key = recipeListCacheKey(base);
  // Any of the three moving must miss: the stock revision is the one that
  // matters most (F11's 30 s TTL) because the user edits Pantry.md in
  // Obsidian while this PWA is open, and a chip colour that does not move with
  // it is a lie.
  assert.notEqual(key, recipeListCacheKey({ ...base, catalogRevision: 8 }));
  assert.notEqual(key, recipeListCacheKey({ ...base, stockRevision: 4 }));
  assert.notEqual(key, recipeListCacheKey({ ...base, strict: true }));
  // …and nothing else may change it. The 调试 flag (F7) is applied to the same
  // payload AFTER the read, so including it would describe a rendering concern
  // as a data input.
  assert.equal(key, recipeListCacheKey({ ...base, debug: true }));
  assert.equal(key, recipeListCacheKey({ ...base, recipes: [{ name: 'x' }] }));
  assert.match(key, /^\[7,3,0\]$/);
});

test('an absent revision and a null revision are the same cache key', () => {
  // "The server did not report a revision" and "the revision is null" are the
  // same input, and collapsing them is safe: the payload they key is the same
  // payload. What must NOT happen is `undefined` stringifying to something
  // that differs from a reported value, which would silently never hit.
  assert.equal(recipeListCacheKey({}), recipeListCacheKey({ strict: false }));
  assert.equal(
    recipeListCacheKey({ catalogRevision: null, stockRevision: null, strict: false }),
    recipeListCacheKey({}),
  );
  assert.match(recipeListCacheKey({}), /^\[null,null,0\]$/);
  assert.notEqual(recipeListCacheKey({ strict: true }), recipeListCacheKey({}));
});

test('RenderCache evicts the genuinely oldest entry, not the current one', () => {
  const cache = new RenderCache(2);
  cache.set('a', 1);
  cache.set('b', 2);
  assert.equal(cache.get('a'), 1);
  cache.set('c', 3);
  // 'a' was just read, so 'b' is the oldest and is the one that goes.
  assert.equal(cache.get('a'), 1);
  assert.equal(cache.get('b'), undefined);
  assert.equal(cache.get('c'), 3);
});

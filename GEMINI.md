# Session Learnings

## Scaffold: what is real vs. what is a placeholder
- The repo is infrastructure-only. `/api/*` has exactly one route
  (`/api/version`); everything else is the static shell, `/health`, `/sw.js`,
  and the manifest. A claim that "the app lists recipes" is false.
- `app/static/icons/` is an empty directory with a README. The manifest and the
  `apple-touch-icon` link point at four PNGs that do not exist, so iOS uses a
  page screenshot for the Home Screen icon. This is a known gap — do not
  "fix" it by deleting the manifest entries.
- There is no `.env` parser. `Settings.from_environment()` reads
  `os.environ`; `cp .env.example .env` documents intent but nothing loads it.
  Use `env $(cat .env | xargs)`, a launchd `EnvironmentVariables` dict, or
  direnv.

## Vendored-infra contract
- `app/static/js/pwa/*`, `app/static/css/pwa.css`, `app/static/css/pull-refresh.css`,
  and `app/pwa_version.py` are byte-identical copies from
  `~/projects/pwa-template`. The drift gate is
  `python3 ~/projects/pwa-template/scripts/vendor.py --check .`, wired into
  `git-hooks/pre-commit` (via `core.hooksPath git-hooks`) and into CI.
- `app/static/sw.js` is the exception: it is *configured* per app, so
  `vendor.py` only copies it when absent. `--force-sw` overwrites it on purpose
  — do not pass it casually, it destroys `CACHE_VERSION` and `SHELL_ASSETS`.
- `SHELL_ASSETS` currently lists the scaffold's files. A file added under
  `app/static/js` or `app/static/css` and forgotten there is simply missing
  from the offline shell. `tests/js/scaffold.test.mjs` catches the reverse (an
  entry with no file) but cannot catch a missing entry.

## Versioning
- Pattern A: `CACHE_VERSION` in `app/static/sw.js` is the only version. There is
  no `VERSION` file. `derive_version()` and `install_pwa_version()` in the
  vendored `app/pwa_version.py` both regex-parse it, and
  `tests/scaffold/test_app.py` asserts they agree with `/health` and the HTML
  shell. Adding a `VERSION` file breaks the convergence gate by design.
- The `/js/{path}` route replaces `__APP_VERSION__` in module *content*. This
  is load-bearing: a `?v=` pin on `/js/main.js?v=X` does **not** propagate to
  `import './views/list.js?v=X'`, which resolves to `/js/views/list.js` with the
  query dropped. Without content injection every submodule is stale-304-able
  after a deploy while the shell advertises the new version.

## Route ordering in `create_app()`
- `/js/{path}` must be registered **before** the static mount, or the mount wins
  and the token is never injected.
- The static mount must be registered **last**; it is a catch-all and shadows
  anything added after it.
- The `/api/{path}` catch-all must precede the mount, or an unknown API path
  returns StaticFiles' HTML 404 instead of the `{requestId, code}` envelope.
- `/health` and `/api/*` get `Cache-Control: no-store` from the `cache_policy`
  middleware. `/api/version` additionally carries `X-PWA-Backend-Started-At`,
  which proves the *backend* restarted — a matching version proves only that
  static assets are fresh.

## Service-worker registration
- `update-manager.js` is the single registration site. An inline
  `navigator.serviceWorker.register` in `index.html`, especially with a
  version-pinned `/sw.js?v=X` URL, reinstalls the worker on every load:
  `skipWaiting` + `clients.claim` fire `controllerchange`, the page reloads,
  and two registrations can alternate in an infinite reload loop.
- The manager reads the pinned version from
  `window['__APP' + '_VERSION__']` (built by concatenation on purpose, so a
  server-side token replacement cannot rewrite the property access into
  `window.v0.1.0`). `main.js` populates it from the serve-injected
  `<meta name="app-version">` tag, which keeps `index.html` free of inline
  script.

## Ports
- Loopback app ports in use: 8000 (wardrobe prod), 8002 (deals), 8003 (wardrobe
  dev), 8004 (obsidian-daily), 8005 (obsidian-monthly), 8006 (obsidian-editor).
  **8007** was allocated to this app on 2026-09-27.
- Tailscale Serve ingress in use: 8443, 8445, 8446, 8447, 8448, 8449, 8450,
  8451. **8452** is the next free one and has **not** been configured for this
  app. Run `tailscale serve status` and audit before and after any change.

## Verification commands that actually gate
- `pytest tests/scaffold/test_app.py -q` is the portfolio-pinned test, so it
  must stay fast and must not touch the real vault or the real
  `pantry_items.db`. The `tests/conftest.py` fixtures seed a throwaway vault
  tree and a real SQLite file with the producer's `items` schema.
- `mypy app` only. `pyproject.toml` says `files = ["app", "tests"]` but CI and
  the documented command pass `app` — `tests/` is not type-checked.
- `node --check app/static/js/main.js` needs `"type": "module"` in
  `package.json`; without it Node parses `.js` as CommonJS and rejects the
  `import` statements. `sw.js` has no module syntax, so it passes either way.

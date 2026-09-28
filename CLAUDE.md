# CLAUDE.md — pwa-pantry-recipes

**Read `AGENTS.md` first.** It is the authority for the non-negotiables, the
layout, the commands, and the domain vocabulary. This file records only the
things a Claude Code session gets wrong without being told.

## Read before writing code

- `AGENTS.md` — non-negotiables, layout, commands, deploy.
- `CONTEXT.md` — the domain glossary and the forbidden-synonym table. Do not
  invent a name for a concept; look it up, and if it is missing, add it there in
  the same commit.
- `README.md` § *What does not work yet* — the honest gap list. This repo is a
  scaffold, so "the app already does X" is usually false.

## Ground truth before assumption

- `~/projects/pwa-template/docs/pwa-template.md` is the pattern reference. The
  SW, the version pin, the module-graph injection, and the update lifecycle all
  have a documented reason; read the part before changing one.
- `~/projects/pwa-obsidian-daily` is the closest sibling (vault reads,
  daily-note writes, ESM, launchd, fail-closed config). Copy its shape rather
  than inventing a new one.
- `~/projects/wholefoods-to-pantry/references/pantry-write.md` is the producer's
  contract. `PantryItem` unit/price semantics live there, not in this repo.
- `app/pwa_version.py` and `app/static/js/pwa/*` are **vendored**. Read them to
  know the API, never to improve them. Changes belong in `pwa-template`, then
  re-vendor.

## Traps in this repo

- **`__APP_VERSION__` is a serve-time token, not a constant.** The `"/"` route
  and the `/js/{path}` route replace it. In `app/static/**` it must appear only
  inside a served URL or the `app-version` meta tag. `tests/js/scaffold.test.mjs`
  enforces this. Never hand-type a version literal.
- **Route order in `create_app()` is a contract, not style.** `/js/{path}` must
  precede the static mount (it injects the version into module content); the
  static mount must be last (a catch-all mount shadows anything after it); the
  `/api/{path}` catch-all must precede it so unknown API paths return the JSON
  envelope rather than an HTML 404.
- **`install_pwa_version` + `derive_version` both read `sw.js`.** They must keep
  reading the same file; that is what makes the convergence gate pass. If you
  introduce a `VERSION` file you break the single-source invariant.
- **Configuration is read at startup, not per request.** A new `Settings` field
  needs a default, a validator, and a `README`/`.env.example` entry, and it
  belongs to the *server-owned* side. Validate it in `from_mapping` so it fails
  closed; do not validate it in a route.
- **`PANTRY_ITEMS_DB` outside the vault is enforced.** A derived data file
  inside the vault would be indexed and synced by Obsidian.
- **`app/static/icons/` is empty on purpose.** The manifest and the
  `apple-touch-icon` link reference four PNGs that do not exist. That is a known
  gap, not a bug to "fix" by deleting the references.
- **CI checks `mypy app`, not `mypy app tests`.** `pyproject.toml` lists both
  under `files`; only `app` is enforced. Do not assume `tests/` is type-clean.

## Before you commit

```bash
.venv/bin/python -m pytest && .venv/bin/python -m ruff check app tests scripts \
  && .venv/bin/python -m mypy app && npm test && npm run check \
  && python3 ~/projects/pwa-template/scripts/vendor.py --check .
```

The pre-commit hook runs the drift gate (and gitleaks when installed), so a
vendored-file hand-edit is rejected before it lands.

## Out of scope

Do not touch other projects to make this one work — in particular, do not
modify `pwa-template`'s vendored files in place, do not write to
`wholefoods-to-pantry`'s database, and do not edit the production vault. The one
cross-repo edit this repo's scaffold owns is its own `[[apps]]` block in
`pwa-template/portfolio.toml`.

# app/static/icons/ — the four PWA icons

All four files are committed and generated, not hand-drawn. They are referenced
from two places, and both are covered by `tests/scaffold/test_icons.py`:

| File | Size | Referenced by |
|---|---|---|
| `icon-192.png` | 192×192 | `manifest.webmanifest` `icons[]`, `purpose: any` |
| `icon-512.png` | 512×512 | `manifest.webmanifest` `icons[]`, `purpose: any maskable` |
| `icon-monochrome-512.png` | 512×512 | `manifest.webmanifest` `icons[]`, `purpose: monochrome` |
| `icon-180-apple.png` | 180×180 | `<link rel="apple-touch-icon">` in `index.html` |

A missing icon is not fatal — the app boots, installs, and caches — but iOS
falls back to a **page screenshot** for the Home Screen icon and Chrome logs a
manifest warning. `.github/workflows/ci.yml`'s wheel-content check asserts all
four land in the built wheel, so a `.png` that fails to package can no longer
produce an installed app with no icon and no test failure.

## Regenerating

```bash
.venv/bin/python scripts/generate_icons.py           # rewrite the four PNGs
.venv/bin/python scripts/generate_icons.py --check   # fail if they drifted
```

The generator is stdlib-only and deterministic — no timestamps, no randomness —
so an unchanged tree reproduces the committed bytes exactly. That is what
`--check` asserts, and `tests/scaffold/test_icons.py` runs it, so a hand-edited
or truncated binary fails the suite. Edit the mark or the palette in
`scripts/generate_icons.py` and rerun; never patch a PNG by hand.

The mark is a pantry jar with a lid and a fill line, drawn in the app's own
`tokens.css` colors. The variants differ only in their background, which is what
each reference actually requires:

- `purpose: any` gets a rounded square, so a launcher that applies no mask still
  gets a rounded icon instead of a hard square.
- `purpose: any maskable` is full bleed with the mark inside the central 80%
  safe circle, so a circular or squircle mask never clips it.
- `apple-touch-icon` is full bleed and fully opaque: iOS rounds the corners
  itself and composites alpha over black, so transparency would install a black
  icon.
- `purpose: monochrome` is transparent with one opaque silhouette, because
  Chromium tints a themed icon from its alpha channel.

## Unversioned on purpose

Icons and the manifest stay **unversioned** (docs/pwa-template.md §3b:
immutable content). There is no `?v=` token on any icon URL, and they are not in
`sw.js`'s `SHELL_ASSETS`. The cost is a caching one: iOS caches manifest
metadata longer than page content, so **changing an icon requires removing and
re-adding the Home Screen app** to see the new one.

# app/static/icons/ — placeholder

This directory is **empty on purpose**. The icon files referenced by
`app/static/manifest.webmanifest` and by the `apple-touch-icon` link in
`app/static/index.html` are not committed:

| Path | Referenced by | Status |
|---|---|---|
| `icon-192.png` | manifest `icons[]` | **missing** |
| `icon-512.png` | manifest `icons[]` (any maskable) | **missing** |
| `icon-monochrome-512.png` | manifest `icons[]` (monochrome) | **missing** |
| `icon-180-apple.png` | `<link rel="apple-touch-icon">` in index.html | **missing** |

A missing icon is not fatal — the app boots, installs, and caches — but iOS will
fall back to a screenshot for the Home Screen icon, and Chrome logs a manifest
warning. **Generate all four before installing the PWA on a device.**

Sibling PWAs keep their generated binaries in git, so once they exist they are
committed like any other asset. Follow the pwa-deals / pwa-wardrobe convention
(180x180 apple, 192 and 512 including a maskable variant, plus a monochrome
variant for the Chromium themed-icon badge).

`.gitkeep` is intentionally absent so this note is visible in an otherwise
empty directory; see `README.md` for the generate step.

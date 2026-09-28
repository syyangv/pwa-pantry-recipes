"""Gates for the four PWA icons the install path depends on.

Four empty references lived in this scaffold for a long time: the manifest
pointed at three PNGs and `index.html` at a fourth, none of which existed.
Nothing failed, because nothing checked that a reference resolves. iOS falls
back to a screenshot for the Home Screen icon and Chrome logs a manifest
warning, so the failure is invisible in the app and only visible after install.

These tests close that from both ends:

  1. Every reference resolves — a fifth `src` or `href` added later cannot be
     forgotten, because the loop reads the manifest and the shell rather than
     a hardcoded list.
  2. What it resolves to is a real PNG of the size the manifest declares, with
     visible content. A 0-byte file, a renamed text file, or a solid-color
     placeholder fails here.
  3. The `apple-touch-icon` is opaque and full-bleed, because iOS composites
     alpha over black and rounds the corners itself.
  4. The references are not version-pinned, and the committed bytes still match
     `scripts/generate_icons.py`.

The icons are intentionally **not** in `sw.js`'s `SHELL_ASSETS`: they are
unversioned immutable content, and `tests/js/shell_assets.test.mjs` walks only
`app/static/js` and `app/static/css`. The wheel-content check in
`.github/workflows/ci.yml` is the gate that they ship in the installed app.
"""

from __future__ import annotations

import json
import re
import struct
import subprocess
import sys
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = REPO_ROOT / "app" / "static"
ICONS_DIR = STATIC_ROOT / "icons"
MANIFEST_PATH = STATIC_ROOT / "manifest.webmanifest"
INDEX_PATH = STATIC_ROOT / "index.html"
GENERATOR = REPO_ROOT / "scripts" / "generate_icons.py"

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_APPLE_TOUCH_ICON_RE = re.compile(r"<link[^>]*rel=\"apple-touch-icon\"[^>]*>")
_HREF_RE = re.compile(r"href=\"([^\"]+)\"")
_SIZES_RE = re.compile(r"sizes=\"([^\"]+)\"")


def _square(reference: str, sizes: str) -> int:
    match = re.fullmatch(r"(\d+)x(\d+)", sizes)
    assert match is not None, f"{reference} declares a non-square size: {sizes}"
    assert match.group(1) == match.group(2), f"{reference} is not square: {sizes}"
    return int(match.group(1))


def _apple_touch_icon_tag() -> str:
    shell = INDEX_PATH.read_text(encoding="utf-8")
    tag = _APPLE_TOUCH_ICON_RE.search(shell)
    assert tag is not None, "index.html declares no <link rel=\"apple-touch-icon\">"
    return tag.group(0)


def _apple_touch_icon_href() -> str:
    href = _HREF_RE.search(_apple_touch_icon_tag())
    assert href is not None, _apple_touch_icon_tag()
    return href.group(1)


def _icon_references() -> dict[str, int]:
    """Every icon URL the install path uses, mapped to its declared edge length.

    Read from the manifest and from the shell's `apple-touch-icon` link, so a
    new reference is covered without touching this file.
    """
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    references = {icon["src"]: _square(icon["src"], icon["sizes"]) for icon in manifest["icons"]}

    href = _apple_touch_icon_href()
    sizes = _SIZES_RE.search(_apple_touch_icon_tag())
    assert sizes is not None, _apple_touch_icon_tag()
    references[href] = _square(href, sizes.group(1))
    return references


def _paeth(left: int, up: int, up_left: int) -> int:
    estimate = left + up - up_left
    distances = (abs(estimate - left), abs(estimate - up), abs(estimate - up_left))
    return (left, up, up_left)[distances.index(min(distances))]


def _decode_rgba(path: Path) -> tuple[int, int, list[bytes]]:
    """Width, height, and unfiltered RGBA scanlines.

    RGBA 8-bit non-interlaced only, which is what the generator emits. The alpha
    channel is load-bearing twice over — Chromium tints the monochrome icon from
    it and iOS composites against black — so an icon that drops it is wrong, not
    merely a different encoding.
    """
    data = path.read_bytes()
    assert data.startswith(PNG_SIGNATURE), f"{path.name} is not a PNG"

    chunks: list[tuple[bytes, bytes]] = []
    offset = len(PNG_SIGNATURE)
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        tag = data[offset + 4 : offset + 8]
        chunks.append((tag, data[offset + 8 : offset + 8 + length]))
        offset += 12 + length

    header = next(payload for tag, payload in chunks if tag == b"IHDR")
    width, height, depth, color_type, _compression, _filter, interlace = struct.unpack(
        ">IIBBBBB", header
    )
    assert (depth, color_type, interlace) == (8, 6, 0), (
        f"{path.name} is not 8-bit non-interlaced RGBA: "
        f"depth={depth} color_type={color_type} interlace={interlace}"
    )

    raw = zlib.decompress(b"".join(payload for tag, payload in chunks if tag == b"IDAT"))
    stride = width * 4
    assert len(raw) == height * (stride + 1), f"{path.name} has a truncated image stream"

    rows: list[bytes] = []
    previous = bytes(stride)
    for index in range(height):
        start = index * (stride + 1)
        filter_type = raw[start]
        assert filter_type <= 4, f"{path.name} uses an unknown PNG filter {filter_type}"
        line = bytearray(raw[start + 1 : start + 1 + stride])
        for position in range(stride):
            left = line[position - 4] if position >= 4 else 0
            up = previous[position]
            up_left = previous[position - 4] if position >= 4 else 0
            predictor = (0, left, up, (left + up) // 2, _paeth(left, up, up_left))[filter_type]
            line[position] = (line[position] + predictor) & 0xFF
        rows.append(bytes(line))
        previous = bytes(line)
    return width, height, rows


def _pixel(rows: list[bytes], x: int, y: int) -> tuple[int, int, int, int]:
    offset = x * 4
    row = rows[y]
    return (row[offset], row[offset + 1], row[offset + 2], row[offset + 3])


def _path_for(reference: str) -> Path:
    return STATIC_ROOT / reference.lstrip("/")


def test_every_referenced_icon_resolves_to_a_file() -> None:
    references = _icon_references()
    assert len(references) == 4, f"expected four icon references, found {sorted(references)}"
    missing = sorted(reference for reference in references if not _path_for(reference).is_file())
    assert not missing, f"referenced icons that do not exist under {ICONS_DIR}: {missing}"


def test_referenced_icons_are_pngs_of_their_declared_size() -> None:
    for reference, size in _icon_references().items():
        path = _path_for(reference)
        assert path.stat().st_size > 0, f"{path.name} is empty"
        width, height, _rows = _decode_rgba(path)
        assert (width, height) == (size, size), (
            f"{path.name} is {width}x{height} but the reference declares {size}x{size}"
        )


def test_referenced_icons_have_visible_content() -> None:
    for reference in _icon_references():
        path = _path_for(reference)
        width, height, rows = _decode_rgba(path)
        colors = {row[offset : offset + 4] for row in rows for offset in range(0, len(row), 4)}
        assert len(colors) > 1, f"{path.name} is a single flat color: a placeholder, not an icon"
        corner = _pixel(rows, 0, 0)
        center = _pixel(rows, width // 2, height // 2)
        assert center != corner, f"{path.name} has no mark distinguishable from its background"


def test_the_apple_touch_icon_is_opaque_and_full_bleed() -> None:
    href = _apple_touch_icon_href()
    assert _icon_references()[href] == 180
    width, height, rows = _decode_rgba(_path_for(href))
    assert (width, height) == (180, 180), f"{href} is {width}x{height}"
    # iOS composites alpha over black and applies its own corner mask, so a
    # transparent or pre-rounded file installs as a black or clipped icon.
    translucent = [
        (x, y) for y in range(height) for x in range(width) if _pixel(rows, x, y)[3] != 255
    ]
    assert not translucent, f"{href} has {len(translucent)} non-opaque pixels"
    corners = {
        _pixel(rows, x, y)
        for x, y in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))
    }
    assert len(corners) == 1, f"{href} is pre-rounded; iOS masks the corners itself"


def test_icon_references_are_not_version_pinned() -> None:
    # docs/pwa-template.md 3b: icons and the manifest are immutable content and
    # stay unversioned. A `?v=` token here is the documented wrong move, because
    # iOS caches manifest metadata for longer than page content.
    for reference in _icon_references():
        assert "?" not in reference, f"{reference} is version-pinned"


def test_committed_icons_match_the_generator() -> None:
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        "committed icons differ from scripts/generate_icons.py — rerun it\n"
        f"{result.stdout}{result.stderr}"
    )

#!/usr/bin/env python3
"""Regenerate the four PWA icons under `app/static/icons/`.

The icons are committed binaries, so this script is the source of truth for
their pixels. Run it after changing the mark or the palette:

    .venv/bin/python scripts/generate_icons.py           # rewrite the PNGs
    .venv/bin/python scripts/generate_icons.py --check   # fail if they drifted

Deterministic by construction: no timestamps, no randomness, and no dependency
outside the standard library, so a rerun on an unchanged tree reproduces the
committed bytes exactly. `--check` is what proves that. It compares the encoded
bytes, so a zlib whose deflate output differs would surface as drift; the fix is
to rerun without `--check` and commit the result.

The mark is a pantry jar with a lid and a fill line, drawn in the app's own
tokens (`tokens.css` dark surface, text color, and accent). Each variant differs
only in how the background is handled, which is what the reference demands:

* `icon-192.png` — manifest, `purpose: any`: a rounded square, so a launcher
  that does no masking still gets a rounded icon.
* `icon-512.png` — manifest, `purpose: any maskable`: full bleed, with the mark
  inside the central 80% safe circle.
* `icon-180-apple.png` — the shell's `apple-touch-icon`: full bleed and fully
  opaque, because iOS masks the corners itself and composites alpha over black.
* `icon-monochrome-512.png` — manifest, `purpose: monochrome`: transparent, with
  one opaque silhouette, because Chromium tints an icon from its alpha channel.

Icons stay **unversioned** (docs/pwa-template.md 3b: immutable content) and are
not precached in `SHELL_ASSETS`, so no `CACHE_VERSION` bump follows a change
here. iOS caches manifest metadata longer than page content, so a changed icon
needs the Home Screen app removed and re-added.
"""

from __future__ import annotations

import argparse
import struct
import sys
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

ICONS_DIR = Path(__file__).resolve().parents[1] / "app" / "static" / "icons"

# app/static/css/tokens.css — the dark surface, the light text color, the accent.
BACKGROUND = (0x13, 0x13, 0x18)
JAR = (0xF4, 0xF4, 0xF6)
ACCENT = (0xB8, 0x54, 0x1F)

# 4x4 supersampling: 17 coverage levels per edge, which is enough for a mark
# made of rounded rectangles. Regenerating all four takes about ten seconds.
SUPERSAMPLE = 4

# Normalized geometry as fractions of the canvas. The lid+body union spans
# v 0.235..0.765 so the mark is optically centered; the furthest mark point from
# the center is the body's bottom corner at (0.73, 0.765) — 0.351 of the canvas,
# inside the 0.4 maskable safe radius.
LID = (0.5, 0.30, 0.40, 0.13, 0.03)
BODY = (0.5, 0.565, 0.46, 0.40, 0.09)
FILL_TOP = 0.545
ANY_RADIUS = 0.22

Paint = Callable[[float, float], tuple[int, int, int, int]]


class Variant(NamedTuple):
    name: str
    size: int
    background: str


VARIANTS: tuple[Variant, ...] = (
    Variant("icon-192.png", 192, "rounded"),
    Variant("icon-512.png", 512, "bleed"),
    Variant("icon-180-apple.png", 180, "bleed"),
    Variant("icon-monochrome-512.png", 512, "transparent"),
)


def _rounded_rect(u: float, v: float, cx: float, cy: float, w: float, h: float, r: float) -> bool:
    dx = abs(u - cx) - (w / 2 - r)
    dy = abs(v - cy) - (h / 2 - r)
    if dx <= 0.0 and dy <= 0.0:
        return True
    dx = max(dx, 0.0)
    dy = max(dy, 0.0)
    return dx * dx + dy * dy <= r * r


def _color_paint(background_radius: float | None) -> Paint:
    """`background_radius=None` is a full-bleed square; a number rounds the corners.

    A rounded-rect predicate cannot express a full square: at r = w/2 = h/2 it
    degenerates into a circle, whose corners stay transparent.
    """
    opaque = (BACKGROUND[0], BACKGROUND[1], BACKGROUND[2], 255)

    def paint(u: float, v: float) -> tuple[int, int, int, int]:
        if background_radius is None:
            back = True
        else:
            back = _rounded_rect(u, v, 0.5, 0.5, 1.0, 1.0, background_radius)
        layers = (
            (opaque, back),
            (ACCENT, _rounded_rect(u, v, *LID)),
            (JAR, _rounded_rect(u, v, *BODY)),
            (ACCENT, _rounded_rect(u, v, *BODY) and v >= FILL_TOP),
        )
        painted = None
        for color, inside in layers:
            if inside:
                painted = color
        if painted is None:
            return (0, 0, 0, 0)
        return (painted[0], painted[1], painted[2], 255)

    return paint


def _monochrome_paint(u: float, v: float) -> tuple[int, int, int, int]:
    if _rounded_rect(u, v, *LID) or _rounded_rect(u, v, *BODY):
        return (0, 0, 0, 255)
    return (0, 0, 0, 0)


def _render(size: int, paint: Paint) -> list[bytearray]:
    step = 1.0 / SUPERSAMPLE
    samples = SUPERSAMPLE * SUPERSAMPLE
    rows: list[bytearray] = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            alpha_sum = 0
            channels = [0, 0, 0]
            for sy in range(SUPERSAMPLE):
                v = (y + (sy + 0.5) * step) / size
                for sx in range(SUPERSAMPLE):
                    u = (x + (sx + 0.5) * step) / size
                    r, g, b, a = paint(u, v)
                    alpha_sum += a
                    channels[0] += r * a
                    channels[1] += g * a
                    channels[2] += b * a
            if alpha_sum == 0:
                row += b"\x00\x00\x00\x00"
                continue
            row += bytes(
                (
                    round(channels[0] / alpha_sum),
                    round(channels[1] / alpha_sum),
                    round(channels[2] / alpha_sum),
                    round(alpha_sum / samples),
                )
            )
        rows.append(row)
    return rows


def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))


def _png(size: int, rows: list[bytearray]) -> bytes:
    raw = bytearray()
    for row in rows:
        raw.append(0)
        raw += row
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)),
            _chunk(b"IDAT", zlib.compress(bytes(raw), 9)),
            _chunk(b"IEND", b""),
        )
    )


def render(variant: Variant) -> bytes:
    if variant.background == "transparent":
        return _png(variant.size, _render(variant.size, _monochrome_paint))
    radius = ANY_RADIUS if variant.background == "rounded" else None
    return _png(variant.size, _render(variant.size, _color_paint(radius)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate the four PWA icons.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit non-zero if a committed icon differs from the generator",
    )
    args = parser.parse_args(argv)

    drifted: list[str] = []
    for variant in VARIANTS:
        path = ICONS_DIR / variant.name
        data = render(variant)
        if args.check:
            differs = not path.is_file() or path.read_bytes() != data
            if differs:
                drifted.append(variant.name)
            state = "drift" if differs else "ok"
            print(f"{state:>5}  {variant.name}  {variant.size}x{variant.size}  {len(data)} bytes")
        else:
            path.write_bytes(data)
            print(f"wrote  {path}  {variant.size}x{variant.size}  {len(data)} bytes")
    if drifted:
        print(f"committed icons differ from the generator: {drifted}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""pwa-infra version helper — single source of truth for the app version.

Implements docs/pwa-template.md Part 3a (Pattern A): the Service Worker's
``CACHE_VERSION`` constant is the single source. This module regex-parses it
out of ``sw.js`` at startup and serves it via ``GET /api/version`` with
no-store headers, so the SW cache name, the API version, and the client's
pinned version (``window.__APP_VERSION__``) can never drift.

Usage (FastAPI)::

    from pwa_version import install_pwa_version
    install_pwa_version(app, static_dir=Path(__file__).parent / "static")

or compose manually::

    from pwa_version import build_version_router, derive_version
    app.include_router(build_version_router(derive_version(static_dir)))
"""

# NOTE: no ``from __future__ import annotations`` here on purpose. The
# ``response: Response`` parameter in ``build_version_router`` must resolve
# eagerly: FastAPI analyzes the endpoint signature and would otherwise see an
# unresolved ForwardRef (the ``Response`` import lives inside the function
# body), mis-declare ``response`` as a query parameter, and return 422 for
# every GET /api/version. PEP 604 unions (``str | Path``) need Python 3.10+.

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Matches both `const CACHE_VERSION = 'v1'` (wardrobe style) and
# `CACHE_VERSION: 'v1'` (object-literal style).
_CACHE_VERSION_RE = re.compile(r"CACHE_VERSION\s*[:=]\s*['\"]([^'\"]+)['\"]")
BACKEND_STARTED_AT = datetime.now(UTC).isoformat().replace("+00:00", "Z")


def version_headers() -> dict[str, str]:
    """No-cache headers plus a stable fingerprint of this backend process."""
    return {
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "X-PWA-Backend-Started-At": BACKEND_STARTED_AT,
    }


def derive_version(
    static_dir: str | Path | None = None,
    sw_rel: str = "sw.js",
    version_file: str | Path | None = None,
) -> str:
    """Resolve the app version from a single source.

    Two modes:

    * **Pattern A (default)** — parse ``CACHE_VERSION`` out of the Service
      Worker file: the SW cache name, the API version, and the client's
      pinned version (``window.__APP_VERSION__``) can never drift.
    * **VERSION-file mode** — read a standalone version file (deals-style,
      where the SW cache version is deliberately decoupled from the app
      version). Pass ``version_file`` and omit ``static_dir``.

    Args:
        static_dir: directory that serves ``sw.js`` (Pattern A).
        sw_rel: Service Worker filename relative to ``static_dir``.
        version_file: path to a version file whose trimmed contents are the
            version (mutually exclusive with Pattern A).

    Raises:
        ValueError: if neither ``static_dir`` nor ``version_file`` is given,
            or no ``CACHE_VERSION`` constant is found in Pattern A mode.
        FileNotFoundError: if the sw.js / version file is missing.
    """
    if version_file is not None:
        return Path(version_file).read_text(encoding="utf-8").strip()
    if static_dir is None:
        raise ValueError("provide either static_dir (Pattern A) or version_file")
    sw_path = Path(static_dir) / sw_rel
    sw_text = sw_path.read_text(encoding="utf-8")
    match = _CACHE_VERSION_RE.search(sw_text)
    if match is None:
        raise ValueError(f"no CACHE_VERSION found in {sw_path}")
    return match.group(1)


def build_version_router(version: str) -> Any:
    """FastAPI router exposing ``GET /api/version`` (no-store)."""
    from fastapi import APIRouter, Response

    router = APIRouter()

    @router.get("/api/version")
    async def version_endpoint(response: Response) -> dict[str, str]:
        # Never let the browser cache this: the update loop (js/update-manager.js)
        # polls it to detect staleness. The start timestamp distinguishes fresh
        # static assets from a backend that still holds an old schema/config in
        # memory; it deliberately stays in a header to preserve the JSON contract.
        response.headers.update(version_headers())
        return {"version": version}

    return router


def install_pwa_version(
    app: Any,
    static_dir: str | Path | None = None,
    sw_rel: str = "sw.js",
    version_file: str | Path | None = None,
) -> str:
    """Resolve the version and mount ``GET /api/version`` on ``app``.

    Returns the derived version (handy for logging or embedding).
    """
    version = derive_version(static_dir, sw_rel, version_file)
    app.include_router(build_version_router(version))
    return version

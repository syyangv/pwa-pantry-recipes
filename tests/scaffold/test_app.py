"""Scaffold gates for the shell contract.

Run alone:  .venv/bin/python -m pytest tests/scaffold/test_app.py -q

These tests lock in the infrastructure decisions that are expensive to debug
later, and that a later feature PR could silently undo:

  1. The convergence gate — /api/version, /health, the FastAPI app version, and
     the HTML shell's injected pin must all equal the sw.js CACHE_VERSION the
     service worker actually caches under (pwa-infra Pattern A, docs/
     pwa-template.md 3a/3e). A release that bumps sw.js but not the shell, or
     a shell that pins a hand-typed literal, fails here.
  2. The module-version injection route — nested ES-module imports drop the
     `?v=` query string, so without /js/{path} replacing __APP_VERSION__ inside
     module *content*, a CACHE_VERSION bump leaves every submodule stale-304-
     able (Part 3b).
  3. Fail-closed configuration — a missing vault, a missing pantry catalog, a
     non-loopback bind, or a relative/injected server-owned root must refuse to
     start rather than degrade to a silently empty surface.
  4. The exact `Settings` surface and the placement of the two dependencies
     the spec added. Both are decisions about what is NOT there as much as
     about what is: a reintroduced CLI field, or `playwright` in `[test]`,
     silently undoes a locked decision.
  5. The packaging registration. Every package the build registers must exist
     on disk, and every `package-data` glob must match a real file. For
     *packages* that is one-directional on purpose: it deliberately does NOT
     claim the converse — that every directory under `app/` is registered —
     because a ticket only owns its own diff, so a completeness scan turns red
     on directories another ticket has not created yet and on `__pycache__`
     appearing. For the `app` key's `static/` globs it IS both directions:
     every file under `app/static/` must be matched by some glob, because a
     static file that no glob ships is precached by `sw.js` and 404s in an
     installed app, and nothing under `static/` is legitimately unshipped.
"""

from __future__ import annotations

import glob
import re
import tomllib
from dataclasses import fields
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import ConfigurationError, Settings
from app.main import APP_VERSION, STATIC_ROOT, create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
SW_PATH = STATIC_ROOT / "sw.js"
TEST_ORIGIN = "https://recipes.test.invalid"
_CACH_VERSION_RE = re.compile(r"CACHE_VERSION\s*=\s*'([^']+)'")


def sw_cache_version() -> str:
    """CACHE_VERSION from the service worker — the single source of truth."""
    match = _CACH_VERSION_RE.search(SW_PATH.read_text(encoding="utf-8"))
    assert match is not None, "CACHE_VERSION missing from sw.js"
    return match.group(1)


# --- 1. Convergence gate ------------------------------------------------


def test_api_version_equals_sw_cache_version(client: TestClient) -> None:
    expected = sw_cache_version()
    response = client.get("/api/version")
    assert response.status_code == 200
    assert response.json() == {"version": expected}
    # No-store, or the update check can read a cached version and never notice
    # a deploy.
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate"
    # Proves this backend process booted after the release, not that static
    # assets are fresh.
    assert response.headers["x-pwa-backend-started-at"]


def test_module_level_app_version_derives_from_the_same_source() -> None:
    assert APP_VERSION == sw_cache_version()


def test_html_shell_embeds_the_derived_version(client: TestClient) -> None:
    expected = sw_cache_version()
    response = client.get("/")
    assert response.status_code == 200
    assert f'<meta name="app-version" content="{expected}">' in response.text
    # Every token was replaced; a leftover means an un-injected asset URL.
    assert "__APP_VERSION__" not in response.text
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate"


def test_health_reports_the_same_version_and_leaks_no_paths(
    client: TestClient, settings: Settings
) -> None:
    expected = sw_cache_version()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["version"] == expected
    assert body["status"] == "ok"
    assert body["vault"] == {"readable": True}
    assert body["app_data"] == {"operational": True}
    # §9.19's five count groups, each with exactly the names the spec names and
    # not one more. `pantry_db.rowCount` is the `items` TABLE's row count, which
    # is why it is 1 here: the fixture catalog is seeded with one row.
    assert body["pantry_db"] == {"readable": True, "rowCount": 1}
    assert set(body["recipes"]) == {"count", "skipped"}
    assert set(body["stock"]) == {"openCount", "unjoinedLineCount"}
    assert set(body["mappings"]) == {"unresolvedCount"}
    # The seeded catalog has one row, the fixture vault has no `Pantry.md` at
    # all, and its Recipes folder is empty. So the catalog count is a real
    # number, and stock is `None` — "not available" — rather than 0, which would
    # claim the pantry was read and held nothing.
    assert body["recipes"] == {"count": 0, "skipped": 0}
    assert body["stock"] == {"openCount": None, "unjoinedLineCount": None}
    assert body["mappings"]["unresolvedCount"] == 0
    # Health is unauthenticated: it must not disclose the server-owned paths.
    assert str(settings.vault_path) not in response.text
    assert str(settings.app_data_dir) not in response.text
    assert str(settings.pantry_items_db) not in response.text
    # F1: the failure names the vault-relative note and no absolute path, and it
    # reaches `/health` as a null counter rather than as a 500 — the probe a user
    # reads to find out *why* the app is answering 503 must keep answering.
    assert "Pantry.md" not in response.text


# --- 2. Module-version injection + static shell ------------------------


def test_js_route_injects_the_version_into_module_content(client: TestClient) -> None:
    expected = sw_cache_version()
    response = client.get("/js/main.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    # The nested imports are pinned by content injection, not by a literal.
    assert "__APP_VERSION__" not in response.text
    assert f"/js/pwa/update-manager.js?v={expected}" in response.text
    assert f"/js/pwa/waking-banner.js?v={expected}" in response.text


def test_js_route_rejects_path_traversal(client: TestClient) -> None:
    for path in ("/js/../config.py", "/js/../../etc/passwd", "/js/nope.js"):
        response = client.get(path)
        assert response.status_code in {307, 404}, path
        assert "../config.py" not in response.text


def test_sw_manifest_and_styles_are_served_from_the_same_process(
    client: TestClient,
) -> None:
    worker = client.get("/sw.js")
    assert worker.status_code == 200
    assert worker.headers["service-worker-allowed"] == "/"
    assert sw_cache_version() in worker.text

    manifest = client.get("/manifest.webmanifest")
    assert manifest.status_code == 200
    assert manifest.json()["display"] == "standalone"
    assert {icon["sizes"] for icon in manifest.json()["icons"]} >= {"192x192", "512x512"}

    for path in ("/css/pwa.css", "/css/tokens.css", "/css/styles.css"):
        assert client.get(path).status_code == 200, path


def test_unknown_api_path_returns_the_json_envelope_not_html(client: TestClient) -> None:
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["code"] == "not_found"
    assert body["requestId"] == response.headers["x-request-id"]


# --- 3. Fail-closed configuration --------------------------------------


def _base(root: Path) -> dict[str, str]:
    return {
        "OBSIDIAN_VAULT_PATH": str(root / "vault"),
        "APP_DATA_DIR": str(root / "data"),
        "PANTRY_ITEMS_DB": str(root / "pantry_items.db"),
        "PUBLIC_ORIGIN": TEST_ORIGIN,
        "TAILSCALE_OWNER_LOGIN": "owner@test.invalid",
    }


def test_valid_settings_capture_the_server_owned_roots(settings: Settings) -> None:
    assert settings.recipes_root == "Hobbies/做饭/Recipes"
    assert settings.daily_notes_root == "日记"
    assert settings.bind_host == "127.0.0.1"
    assert settings.trust_tailscale_headers is False
    # Dev identity falls back to the owner login.
    assert settings.dev_identity == "owner@test.invalid"


@pytest.mark.parametrize(
    ("key", "value", "error"),
    [
        ("OBSIDIAN_VAULT_PATH", "relative/vault", "vault_path_must_be_absolute"),
        ("APP_DATA_DIR", "", "missing_app_data_dir"),
        (
            "PANTRY_ITEMS_DB",
            "/nonexistent/pantry_items.db",
            "unsafe_or_missing_pantry_items_db_path",
        ),
        ("PUBLIC_ORIGIN", "http://recipes.example.com", "invalid_public_origin"),
        ("TAILSCALE_OWNER_LOGIN", "  ", "missing_tailscale_owner_login"),
        ("BIND_HOST", "0.0.0.0", "bind_host_must_be_loopback"),
        ("RECIPES_ROOT", "../escape", "invalid_recipes_root"),
        ("RECIPES_ROOT", "/absolute", "invalid_recipes_root"),
        ("RECIPES_ROOT", ".hidden", "invalid_recipes_root"),
        ("DAILY_NOTES_ROOT", "a/../../b", "invalid_daily_notes_root"),
        ("TRUST_TAILSCALE_HEADERS", "yes", "invalid_trust_tailscale_headers"),
        ("APP_TIMEZONE", "Mars/Olympus", "invalid_app_timezone"),
    ],
)
def test_invalid_settings_refuse_to_start(
    runtime_root: Path, key: str, value: str, error: str
) -> None:
    values = {**_base(runtime_root), key: value}
    with pytest.raises(ConfigurationError) as raised:
        Settings.from_mapping(values)
    assert raised.value.args[0] == error


def test_trusted_headers_require_a_loopback_bind(runtime_root: Path) -> None:
    values = {
        **_base(runtime_root),
        "TRUST_TAILSCALE_HEADERS": "true",
        "BIND_HOST": "192.168.1.10",
    }
    with pytest.raises(ConfigurationError) as raised:
        Settings.from_mapping(values)
    assert raised.value.args[0] == "trusted_headers_require_loopback_bind"


def test_pantry_catalog_inside_the_vault_is_rejected(runtime_root: Path) -> None:
    # A derived data file inside the vault would be indexed and synced by
    # Obsidian, so it is a configuration error, not a supported layout.
    inside = runtime_root / "vault" / "pantry_items.db"
    inside.write_bytes(b"")
    values = {**_base(runtime_root), "PANTRY_ITEMS_DB": str(inside)}
    with pytest.raises(ConfigurationError) as raised:
        Settings.from_mapping(values)
    assert raised.value.args[0] == "pantry_items_db_must_be_outside_vault"


def test_create_app_does_not_read_the_environment_when_settings_are_injected(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Import-time and factory-time environment reads must be bypassed, or a
    # developer's real vault would be touched by the test suite.
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
    monkeypatch.setenv("PANTRY_ITEMS_DB", "/nonexistent/should-not-be-read.db")
    assert create_app(settings).state.settings is settings


# --- 4. Configuration surface + dependency placement ---------------------


def test_settings_exposes_exactly_the_locked_field_set() -> None:
    # The set is the contract. A field a locked decision removed, and a field
    # nobody asked for, are both failures here rather than a silent drift.
    assert {field.name for field in fields(Settings)} == {
        "vault_path",
        "app_data_dir",
        "public_origin",
        "tailscale_owner_login",
        "dev_identity",
        "bind_host",
        "app_timezone",
        "trust_tailscale_headers",
        "pantry_items_db",
        "pantry_note_relative",
        "recipes_root",
        "daily_notes_root",
        "daily_notes_year_policy",
        "read_only",
        "catalog_cache_seconds",
        "stock_cache_seconds",
        "recipe_cache_seconds",
        "max_recipe_bytes",
    }


def test_new_settings_capture_their_documented_defaults(settings: Settings) -> None:
    assert settings.pantry_note_relative == "Logistics/库存/Pantry.md"
    assert settings.daily_notes_year_policy is None
    assert settings.read_only is False
    assert settings.catalog_cache_seconds == 300.0
    assert settings.stock_cache_seconds == 30.0
    assert settings.recipe_cache_seconds == 60.0
    assert settings.max_recipe_bytes == 2_000_000


def test_overridden_settings_capture_the_operator_values(runtime_root: Path) -> None:
    values = {
        **_base(runtime_root),
        "PANTRY_NOTE_RELATIVE": "库存/Kitchen/Pantry note.md",
        "DAILY_NOTES_YEAR_POLICY": "2024-2027",
        "OBSIDIAN_READ_ONLY": "true",
        "CATALOG_CACHE_SECONDS": "120.5",
        "STOCK_CACHE_SECONDS": "5",
        "RECIPE_CACHE_SECONDS": "3600",
        "MAX_RECIPE_BYTES": "4096",
    }
    settings = Settings.from_mapping(values)
    assert settings.pantry_note_relative == "库存/Kitchen/Pantry note.md"
    assert settings.daily_notes_year_policy == "2024-2027"
    assert settings.read_only is True
    assert settings.catalog_cache_seconds == 120.5
    assert settings.stock_cache_seconds == 5.0
    assert settings.recipe_cache_seconds == 3600.0
    assert settings.max_recipe_bytes == 4096


@pytest.mark.parametrize(
    ("key", "value", "error"),
    [
        ("PANTRY_NOTE_RELATIVE", "../escape/Pantry.md", "invalid_pantry_note_relative"),
        ("PANTRY_NOTE_RELATIVE", "/absolute/Pantry.md", "invalid_pantry_note_relative"),
        ("PANTRY_NOTE_RELATIVE", "库存/Pantry", "invalid_pantry_note_relative"),
        ("PANTRY_NOTE_RELATIVE", "库存/Pantry.md/", "invalid_pantry_note_relative"),
        ("PANTRY_NOTE_RELATIVE", "  ", "invalid_pantry_note_relative"),
        ("OBSIDIAN_READ_ONLY", "yes", "invalid_read_only"),
        ("OBSIDIAN_READ_ONLY", "1", "invalid_read_only"),
        ("OBSIDIAN_READ_ONLY", "", "invalid_read_only"),
        ("CATALOG_CACHE_SECONDS", "0", "invalid_catalog_cache_seconds"),
        ("CATALOG_CACHE_SECONDS", "3600.1", "invalid_catalog_cache_seconds"),
        ("CATALOG_CACHE_SECONDS", "-1", "invalid_catalog_cache_seconds"),
        ("CATALOG_CACHE_SECONDS", "nan", "invalid_catalog_cache_seconds"),
        ("CATALOG_CACHE_SECONDS", "inf", "invalid_catalog_cache_seconds"),
        ("CATALOG_CACHE_SECONDS", "soon", "invalid_catalog_cache_seconds"),
        ("CATALOG_CACHE_SECONDS", "", "invalid_catalog_cache_seconds"),
        ("STOCK_CACHE_SECONDS", "0", "invalid_stock_cache_seconds"),
        ("STOCK_CACHE_SECONDS", "600.5", "invalid_stock_cache_seconds"),
        ("RECIPE_CACHE_SECONDS", "0", "invalid_recipe_cache_seconds"),
        ("RECIPE_CACHE_SECONDS", "3601", "invalid_recipe_cache_seconds"),
        ("MAX_RECIPE_BYTES", "1023", "invalid_max_recipe_bytes"),
        ("MAX_RECIPE_BYTES", "20000001", "invalid_max_recipe_bytes"),
        ("MAX_RECIPE_BYTES", "2.5", "invalid_max_recipe_bytes"),
        ("DAILY_NOTES_YEAR_POLICY", "2027", "invalid_daily_notes_year_policy"),
        ("DAILY_NOTES_YEAR_POLICY", "27-2027", "invalid_daily_notes_year_policy"),
        ("DAILY_NOTES_YEAR_POLICY", "2027-27", "invalid_daily_notes_year_policy"),
    ],
)
def test_new_settings_refuse_to_start(
    runtime_root: Path, key: str, value: str, error: str
) -> None:
    values = {**_base(runtime_root), key: value}
    with pytest.raises(ConfigurationError) as raised:
        Settings.from_mapping(values)
    assert raised.value.args[0] == error


def test_aiosqlite_is_a_runtime_dependency_and_playwright_is_an_opt_in_extra() -> None:
    text = PYPROJECT_PATH.read_text(encoding="utf-8")
    project = tomllib.loads(text)["project"]
    assert "aiosqlite>=0.20" in project["dependencies"]

    extras = project["optional-dependencies"]
    assert extras["browser"] == ["playwright>=1.44"]
    # One occurrence in the whole file, and it is the `browser` extra: a second
    # one in `[test]` would make the browser suite non-optional in CI.
    assert text.count("playwright") == 1


# --- 5. Packaging registration -------------------------------------------


def _pyproject() -> dict[str, Any]:
    return tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))


def _discovered_packages(config: dict[str, Any]) -> set[str]:
    """What `packages.find` with `namespaces = false` will select.

    Reproduced rather than imported: `find_packages` is a build-time dependency,
    `types-setuptools` is not, and importing it would make the `mypy` config's
    `files = ["app", "tests"]` unsatisfiable without a new dependency this
    ticket may not add. For a flat layout the two definitions coincide —
    directories carrying an `__init__.py`, filtered by the `include` globs.
    """

    finder = config["tool"]["setuptools"]["packages"]["find"]
    if finder["namespaces"] is not False:
        return _namespace_packages()
    includes = finder["include"]
    found = set()
    for init in sorted((REPO_ROOT / "app").rglob("__init__.py")):
        dotted = ".".join(init.parent.relative_to(REPO_ROOT).parts)
        if any(fnmatch(dotted, pattern) for pattern in includes):
            found.add(dotted)
    return found


def _namespace_packages() -> set[str]:
    """Every directory under `app/`, which is what `namespaces = true` selects.

    Only reached to produce a failure message that names the offending entries.
    """

    return {
        ".".join(path.relative_to(REPO_ROOT).parts)
        for path in (REPO_ROOT / "app").rglob("*")
        if path.is_dir()
    }


def test_every_registered_package_exists_on_disk() -> None:
    # One direction only: a stale registration. A package listed after its
    # directory was removed builds a wheel that silently lacks code.
    packages = _discovered_packages(_pyproject())
    assert packages, "auto-discovery found nothing under app/"
    for package in sorted(packages):
        directory = REPO_ROOT / package.replace(".", "/")
        assert directory.is_dir(), f"{package} is registered but {directory} does not exist"
        # A data directory is not a package. `app/static` is served from disk
        # and shipped via `package-data`, so promoting it to a package would be
        # a claim the tree does not make.
        assert package != "app.static"
        assert "__pycache__" not in package.split(".")


def test_discovery_does_not_pick_up_data_directories() -> None:
    # `namespaces = false` is what does this. The setuptools default is `true`,
    # and with it `app.static`, `app.static.js`, `app.static.js.pwa`,
    # `app.static.css`, `app.static.icons` and `app.recipes.lexicon` all become
    # "packages" — which is a wrong package set, not a harmless one.
    finder = _pyproject()["tool"]["setuptools"]["packages"]["find"]
    assert finder["namespaces"] is False
    packages = _discovered_packages(_pyproject())
    assert not any(part == "static" for package in packages for part in package.split("."))
    assert "app.recipes.lexicon" not in packages


def _globbed_files(package: str, pattern: str) -> set[Path]:
    """The files `pattern` really matches for `package`, on the backend's terms.

    `glob.glob` rather than `fnmatch`, and that distinction is the whole point:
    `fnmatch`'s `*` crosses a `/`, so `fnmatch("static/js/logic/sort.js",
    "static/js/*")` is true, while `glob.glob("static/js/*")` from the package
    directory yields `main.js` plus the `logic/` and `pwa/` *directories* and
    none of their contents. setuptools expands these patterns with `glob`, so
    only `glob` says what the wheel will contain. A non-empty check written
    with `fnmatch` therefore reports `static/js/*` as covering the logic
    modules when the build drops all three.
    """

    package_dir = REPO_ROOT / package.replace(".", "/")
    matched = {package_dir / name for name in glob.glob(pattern, root_dir=package_dir)}
    return {path for path in matched if path.is_file()}


def test_every_package_data_key_resolves_to_a_package_and_a_real_file() -> None:
    # A key for a package that is not on disk ships nothing; a glob that matches
    # nothing ships nothing. Both are the same silent-green hole the wheel
    # check cannot see, because it only lists files it already knows about.
    config = _pyproject()
    packages = _discovered_packages(config)
    package_data: dict[str, list[str]] = config["tool"]["setuptools"]["package-data"]

    assert set(package_data).issubset(packages)
    for package, patterns in package_data.items():
        for pattern in patterns:
            matched = _globbed_files(package, pattern)
            assert matched, f"{package!r} pattern {pattern!r} matches no file on disk"


def test_every_static_file_is_covered_by_a_package_data_glob() -> None:
    """The completeness half of the claim above, for the `app` key.

    Non-empty-per-glob is satisfied by one lucky file: `static/js/*` matched
    `main.js` while shipping none of `static/js/logic/`, and all three logic
    modules are in `sw.js`'s `SHELL_ASSETS` — so an installed app precached
    three paths it does not contain and 404'd them offline. This asserts the
    other direction: everything on disk under `app/static/` is matched by some
    `app` glob, so adding a static file without registering it fails here
    instead of in a user's installed app.

    Scoped to `app`/`static` deliberately. The same hole exists under
    `app.recipes` and `app.db`, but a data file there that is *meant* to stay
    out of the wheel is plausible, and a blanket claim over every
    `package-data` key would be a maintenance burden rather than a gate. Every
    file under `static/` is served or precached, so none of them can be
    legitimately unshipped.

    The dotfile exposure is the one `tests/js/shell_assets.test.mjs` already
    carries: it walks `static/js` and `static/css` with no dotfile filter, so a
    stray `.DS_Store` is a pre-existing condition of this tree, not one added
    here.
    """

    patterns = _pyproject()["tool"]["setuptools"]["package-data"]["app"]
    covered: set[Path] = set()
    for pattern in patterns:
        covered |= _globbed_files("app", pattern)
    static_dir = REPO_ROOT / "app" / "static"
    on_disk = {path for path in static_dir.rglob("*") if path.is_file()}

    assert not on_disk - covered, (
        "static files no package-data glob ships (absent from every wheel): "
        + ", ".join(
            sorted(path.relative_to(REPO_ROOT).as_posix() for path in on_disk - covered)
        )
    )


def test_dotted_package_data_keys_are_quoted() -> None:
    # An unquoted `app.db = [...]` in a file that already defines `app` is a
    # hard parse error, not a warning: TOML reads it as adding a sub-key to an
    # immutable table, and the whole build fails with
    # "Cannot mutate immutable namespace".
    for line in PYPROJECT_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if "=" not in stripped or stripped.startswith(("#", "[")):
            continue
        key = stripped.split("=", 1)[0].strip()
        if "." in key:
            assert key.startswith('"') and key.endswith('"'), line

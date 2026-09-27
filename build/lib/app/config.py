"""Typed, fail-closed runtime configuration for the Pantry Recipes PWA.

Every value is read once at startup from the process environment. Nothing here
is ever taken from a request: `PANTRY_ITEMS_DB`, `RECIPES_ROOT`, and
`DAILY_NOTES_ROOT` are server-owned, so a browser can never redirect a read or
a write to a path the operator did not authorize. Invalid values refuse to
start rather than degrading silently.
"""

from __future__ import annotations

import ipaddress
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


class ConfigurationError(ValueError):
    """A core configuration error that must prevent application startup."""


@dataclass(frozen=True)
class Settings:
    """Immutable, validated runtime configuration."""

    # --- Core, required -------------------------------------------------
    vault_path: Path
    app_data_dir: Path
    public_origin: str
    tailscale_owner_login: str
    dev_identity: str
    bind_host: str
    app_timezone: str
    trust_tailscale_headers: bool

    # --- Server-owned domain roots (requests cannot select these) -------
    pantry_items_db: Path
    recipes_root: str
    daily_notes_root: str

    @classmethod
    def from_environment(cls) -> Settings:
        return cls.from_mapping(os.environ)

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> Settings:
        vault = _required_directory(values, "OBSIDIAN_VAULT_PATH", "vault")
        data = _required_directory(values, "APP_DATA_DIR", "app_data")

        origin = values.get("PUBLIC_ORIGIN", "")
        if not _valid_origin(origin):
            raise ConfigurationError("invalid_public_origin")

        owner = _required_text(values, "TAILSCALE_OWNER_LOGIN")
        _validate_identity(owner, "invalid_tailscale_owner_login")
        dev_identity = values.get("DEV_IDENTITY", "").strip() or owner
        _validate_identity(dev_identity, "invalid_dev_identity")

        bind_host = values.get("BIND_HOST", "127.0.0.1")
        if not _valid_bind_host(bind_host):
            raise ConfigurationError("invalid_bind_host")
        trust_headers = _boolean(
            values.get("TRUST_TAILSCALE_HEADERS", "false"), "trust_tailscale_headers"
        )
        if not is_loopback_bind_host(bind_host):
            raise ConfigurationError(
                "trusted_headers_require_loopback_bind"
                if trust_headers
                else "bind_host_must_be_loopback"
            )

        timezone = values.get("APP_TIMEZONE", "America/New_York")
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ConfigurationError("invalid_app_timezone") from exc

        # --- Server-owned domain roots ---------------------------------
        # PANTRY_ITEMS_DB is the wholefoods-to-pantry producer's SQLite
        # catalog (table `items`). It is a sibling project's asset, NOT inside
        # the vault: the PWA reads it and never writes it. A missing file is a
        # startup error so the pantry surface can never report a silent
        # "empty pantry" that is actually a misconfigured path.
        pantry_db = _required_file(values, "PANTRY_ITEMS_DB", "pantry_items_db")
        if _path_inside_vault(pantry_db, vault):
            raise ConfigurationError("pantry_items_db_must_be_outside_vault")

        recipes_root = values.get("RECIPES_ROOT", "Hobbies/做饭/Recipes")
        if not _safe_relative_root(recipes_root):
            raise ConfigurationError("invalid_recipes_root")

        daily_notes_root = values.get("DAILY_NOTES_ROOT", "日记")
        if not _safe_relative_root(daily_notes_root):
            raise ConfigurationError("invalid_daily_notes_root")

        return cls(
            vault_path=vault,
            app_data_dir=data,
            public_origin=origin,
            tailscale_owner_login=owner,
            dev_identity=dev_identity,
            bind_host=bind_host,
            app_timezone=timezone,
            trust_tailscale_headers=trust_headers,
            pantry_items_db=pantry_db,
            recipes_root=recipes_root,
            daily_notes_root=daily_notes_root,
        )


def _required_text(values: Mapping[str, str], key: str) -> str:
    value = values.get(key, "").strip()
    if not value:
        raise ConfigurationError(f"missing_{key.lower()}")
    return value


def _validate_identity(value: str, error: str) -> None:
    if len(value) > 254 or any(c.isspace() or ord(c) < 32 for c in value):
        raise ConfigurationError(error)


def _valid_origin(value: str) -> bool:
    """Syntax check plus a scheme rule: HTTPS for anything non-loopback."""
    if not value or value != value.strip() or any(c.isspace() for c in value):
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port  # Access performs numeric and range validation.
    except ValueError:
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65_535)
    ):
        return False
    address: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
    try:
        address = ipaddress.ip_address(hostname)
        valid_hostname = "%" not in hostname
    except ValueError:
        valid_hostname = hostname == "localhost" or (
            len(hostname) <= 253
            and all(_DNS_LABEL.fullmatch(label) for label in hostname.split("."))
        )
    if not valid_hostname:
        return False
    return parsed.scheme == "https" or hostname == "localhost" or bool(
        address and address.is_loopback
    )


def _required_directory(values: Mapping[str, str], key: str, label: str) -> Path:
    raw = _required_text(values, key)
    path = Path(raw)
    if not path.is_absolute():
        raise ConfigurationError(f"{label}_path_must_be_absolute")
    if not _valid_directory(path):
        raise ConfigurationError(f"unsafe_or_missing_{label}_path")
    return Path(os.path.realpath(path))


def _required_file(values: Mapping[str, str], key: str, label: str) -> Path:
    raw = _required_text(values, key)
    path = Path(raw)
    if not path.is_absolute():
        raise ConfigurationError(f"{label}_path_must_be_absolute")
    if not _valid_regular_file(path):
        raise ConfigurationError(f"unsafe_or_missing_{label}_path")
    return Path(os.path.realpath(path))


def _valid_directory(path: Path) -> bool:
    try:
        return not stat.S_ISLNK(path.lstat().st_mode) and path.is_dir()
    except OSError:
        return False


def _valid_regular_file(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
        return stat.S_ISREG(mode) and not stat.S_ISLNK(mode)
    except OSError:
        return False


def _safe_relative_root(value: str) -> bool:
    """A vault-relative folder: relative, POSIX, no dot segments, no control chars."""
    path = PurePosixPath(value)
    return (
        bool(path.parts)
        and not path.is_absolute()
        and value == path.as_posix()
        and all(ord(c) >= 32 and c != "\x7f" for c in value)
        and all(
            part not in {"", ".", ".."} and not part.startswith(".") for part in path.parts
        )
    )


def _path_inside_vault(path: Path, vault: Path) -> bool:
    """True when `path` is the vault itself or nested inside it.

    Read-only derived data must stay outside the vault: a file inside would be
    indexed and synced by Obsidian. Comparison is on the absolute,
    non-symlink-resolved path; a read-time O_NOFOLLOW traversal stays
    authoritative for symlinks.
    """
    return path == vault or vault in path.parents


def _boolean(raw: str, label: str) -> bool:
    normalized = raw.strip().lower()
    if normalized not in {"true", "false"}:
        raise ConfigurationError(f"invalid_{label}")
    return normalized == "true"


def is_loopback_bind_host(bind_host: str) -> bool:
    """True for `localhost` or any loopback IP address literal."""
    if bind_host.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(bind_host)
    except ValueError:
        return False
    return address.is_loopback


def _valid_bind_host(value: str) -> bool:
    """Syntax-only check: `localhost` or an IP literal, no port/whitespace."""
    if not value or value != value.strip() or any(c.isspace() for c in value):
        return False
    if value.lower() == "localhost":
        return True
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def validate_bind_invariant(settings: Settings) -> None:
    """Loopback bind is mandatory in every mode; trusted-header (production)
    mode rejects a non-loopback bind with a dedicated error before serving.
    Defensive re-check for directly constructed `Settings` instances."""
    if not is_loopback_bind_host(settings.bind_host):
        raise ConfigurationError(
            "trusted_headers_require_loopback_bind"
            if settings.trust_tailscale_headers
            else "bind_host_must_be_loopback"
        )

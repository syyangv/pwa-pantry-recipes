"""Device grants require an explicit ID/tag/IP tuple and a server-owned daemon socket."""

import socket
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app.config import ConfigurationError, Settings
from tests.api.conftest import make_settings

GRANT = {
    "TRUST_TAILSCALE_HEADERS": "true",
    "TAILSCALE_DEVICE_ID": "nTestMac123456",
    "TAILSCALE_DEVICE_TAG": "tag:face-up",
    "TAILSCALE_DEVICE_IPS": "100.87.56.102",
}


@pytest.fixture
def short_socket_path():
    # macOS Unix socket names are limited to 104 bytes; pytest paths exceed that.
    with TemporaryDirectory(prefix="ts-", dir="/tmp") as directory:
        yield Path(directory) / "s"


@pytest.mark.parametrize(
    "key,value",
    [
        ("TAILSCALE_DEVICE_ID", ""),
        ("TAILSCALE_DEVICE_ID", "name\nheader"),
        ("TAILSCALE_DEVICE_TAG", ""),
        ("TAILSCALE_DEVICE_TAG", "face-up"),
        ("TAILSCALE_DEVICE_IPS", ""),
        ("TAILSCALE_DEVICE_IPS", "127.0.0.1"),
        ("TAILSCALE_DEVICE_IPS", "8.8.8.8"),
        ("TAILSCALE_DEVICE_IPS", "100.87.56.102, 100.64.1.2"),
        ("TAILSCALE_DEVICE_IPS", "fd7a:115c:a1e0::1%en0"),
        ("TAILSCALE_SOCKET_PATH", "relative.sock"),
        ("TAILSCALE_SOCKET_PATH", "/tmp/../socket"),
        ("TRUST_TAILSCALE_HEADERS", "false"),
    ],
)
def test_incomplete_or_unsafe_grant_refuses_startup(
    runtime_root: Path, key: str, value: str
) -> None:
    with pytest.raises(ConfigurationError):
        make_settings(runtime_root, **{**GRANT, key: value})


def test_missing_or_regular_file_is_not_a_daemon_socket(runtime_root: Path) -> None:
    p = runtime_root / "not-a-socket"
    p.write_text("")
    for path in (p, runtime_root / "absent"):
        with pytest.raises(ConfigurationError, match="unsafe_or_missing_tailscale_socket"):
            make_settings(runtime_root, **GRANT, TAILSCALE_SOCKET_PATH=str(path))


def test_valid_grant_uses_verified_unix_socket(runtime_root: Path, short_socket_path: Path) -> None:
    p = short_socket_path
    with socket.socket(socket.AF_UNIX) as s:
        s.bind(str(p))
        settings = make_settings(runtime_root, **GRANT, TAILSCALE_SOCKET_PATH=str(p))
    assert settings.tailscale_device_id == GRANT["TAILSCALE_DEVICE_ID"]
    assert settings.tailscale_device_tag == GRANT["TAILSCALE_DEVICE_TAG"]
    assert settings.tailscale_device_ips == ("100.87.56.102",)


def test_devices_default_disabled(prod_settings: Settings) -> None:
    assert prod_settings.tailscale_device_id is None
    assert prod_settings.tailscale_device_ips == ()
    assert prod_settings.tailscale_device_tag is None

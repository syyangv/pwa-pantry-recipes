"""Bounded daemon transport and fail-closed handling of control-plane answers."""

import io
import json
import socket
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest

from app import tailscale
from tests.api.test_device_auth import IP, NODE, TAG, node


@pytest.mark.parametrize(
    "raw,status", [(b"{}", 503), (b"not-json", 200), (b"x" * (tailscale.MAX_WHOIS_BYTES + 1), 200)]
)
def test_bad_daemon_responses_raise(
    monkeypatch: pytest.MonkeyPatch, raw: bytes, status: int
) -> None:
    class Connection:
        def __init__(self, *_: Any) -> None:
            pass

        def request(self, *_: Any) -> None:
            pass

        def getresponse(self) -> Any:
            response = io.BytesIO(raw)
            response.status = status
            return response

        def close(self) -> None:
            pass

    monkeypatch.setattr(tailscale, "_UnixConnection", Connection)
    with pytest.raises((OSError, ValueError)):
        tailscale.lookup_node(Path("/unused.sock"), IP)


@pytest.fixture
def short_socket_path():
    # macOS Unix socket names are limited to 104 bytes; pytest paths exceed that.
    with TemporaryDirectory(prefix="ts-", dir="/tmp") as directory:
        yield Path(directory) / "s"


def test_real_localapi_http_over_unix_socket(short_socket_path: Path) -> None:
    path = short_socket_path
    requests: list[bytes] = []
    body = json.dumps(node()).encode()
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(path))
        server.listen(1)
        server.settimeout(3)

        def serve() -> None:
            connection, _ = server.accept()
            with connection:
                connection.settimeout(3)
                requests.append(connection.recv(8192))
                connection.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\n\r\n"
                    + body
                )

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        data = tailscale.lookup_node(path, IP)
        thread.join(timeout=3)
    assert not thread.is_alive()
    assert b"GET /localapi/v0/whois?addr=100.87.56.102 HTTP/1.1" in requests[0]
    assert tailscale.matches_node(data, NODE, TAG, IP)


def test_disabled_key_expiry_is_an_explicit_valid_zero_time() -> None:
    data = node()
    data["Node"]["KeyExpiry"] = "0001-01-01T00:00:00Z"
    assert tailscale.matches_node(data, NODE, TAG, IP)

"""Bounded read-only Tailscale LocalAPI identity lookup; never an IP-only grant."""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

LOOKUP_TIMEOUT = 0.5
MAX_WHOIS_BYTES = 262_144


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: Path) -> None:
        super().__init__("local-tailscaled.sock", timeout=LOOKUP_TIMEOUT)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(LOOKUP_TIMEOUT)
            sock.connect(str(self._path))
        except OSError:
            sock.close()
            raise
        self.sock = sock


def lookup_node(path: Path, ip: str) -> Any:
    """Read WhoIs via a server-owned Unix socket, without subprocesses or credentials."""
    connection = _UnixConnection(path)
    try:
        connection.request("GET", "/localapi/v0/whois?" + urlencode({"addr": ip}))
        response = connection.getresponse()
        if response.status != 200:
            raise OSError("device_lookup_unavailable")
        raw = response.read(MAX_WHOIS_BYTES + 1)
        if len(raw) > MAX_WHOIS_BYTES:
            raise OSError("device_lookup_oversized")
        return json.loads(raw)
    finally:
        connection.close()


def matches_node(data: Any, node_id: str, tag: str, ip: str) -> bool:
    """Match the daemon's node ID, required tag, address, and current key validity."""
    if not isinstance(data, dict) or not isinstance(data.get("Node"), dict):
        return False
    node = data["Node"]
    if node.get("StableID") != node_id or node.get("Expired") is True:
        return False
    tags = node.get("Tags")
    if not isinstance(tags, list) or tag not in tags:
        return False
    addresses = node.get("Addresses")
    if not isinstance(addresses, list):
        return False
    try:
        if not any(
            ipaddress.ip_interface(address).ip == ipaddress.ip_address(ip) for address in addresses
        ):
            return False
        expiry = node.get("KeyExpiry")
        if not isinstance(expiry, str):
            return False
        # Tailscale's zero time explicitly means key expiry is disabled.
        if expiry != "0001-01-01T00:00:00Z":
            expires_at = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if expires_at.tzinfo is None or expires_at <= datetime.now(UTC):
                return False
    except (ValueError, TypeError):
        return False
    return True

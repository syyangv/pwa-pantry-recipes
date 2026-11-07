"""Tagged-device authentication is explicit, verified, and never a header-only bypass."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.api.conftest import ORIGIN, OWNER

IP = "100.87.56.102"
IP6 = "fd7a:115c:a1e0::4b34:3866"
NODE = "nTestMac123456"
TAG = "tag:face-up"


def node() -> dict[str, Any]:
    return {
        "Node": {
            "StableID": NODE,
            "Tags": [TAG],
            "Addresses": [IP + "/32", IP6 + "/128"],
            "KeyExpiry": "2099-01-01T00:00:00Z",
        }
    }


@pytest.fixture
def device_settings(prod_settings: Settings) -> Settings:
    return replace(
        prod_settings,
        tailscale_device_id=NODE,
        tailscale_device_ips=(IP, IP6),
        tailscale_device_tag=TAG,
    )


@pytest.fixture
def lookup(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def fake(path: Path, ip: str) -> dict[str, Any]:
        calls.append(ip)
        return node()

    monkeypatch.setattr("app.tailscale.lookup_node", fake)
    return calls


def client(settings: Settings, host: str = "127.0.0.1") -> TestClient:
    return TestClient(create_app(settings), base_url=ORIGIN, client=(host, 50000))


@pytest.mark.parametrize("ip", [IP, IP6])
def test_verified_device_can_read_session(
    device_settings: Settings, lookup: list[str], ip: str
) -> None:
    with client(device_settings) as c:
        r = c.get("/api/session", headers={"X-Forwarded-For": ip})
        assert r.status_code == 200
        assert r.json()["identity"] == f"tailscale-device:{NODE}"
        assert lookup == [ip]


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Forwarded-For": "127.0.0.1"},
        {"X-Forwarded-For": IP + ", 100.64.0.1"},
        {"X-Forwarded-For": IP + ":123"},
        {"X-Forwarded-For": "not-an-ip"},
        {"X-Forwarded-For": IP, "%": "x", "Tailscale-Funnel-Request": "?1"},
        [("X-Forwarded-For", IP), ("X-Forwarded-For", IP)],
    ],
)
def test_invalid_forwarding_never_calls_whois(
    device_settings: Settings, lookup: list[str], headers: Any
) -> None:
    with client(device_settings) as c:
        assert c.get("/api/version", headers=headers).status_code == 401
        assert lookup == []


def test_non_loopback_peer_cannot_spoof_forwarding(
    device_settings: Settings, lookup: list[str]
) -> None:
    with client(device_settings, "100.64.99.1") as c:
        assert c.get("/api/version", headers={"X-Forwarded-For": IP}).status_code == 401
        assert lookup == []


@pytest.mark.parametrize("login", [OWNER, "attacker@example.invalid", ""])
def test_present_login_never_falls_back(
    device_settings: Settings, lookup: list[str], login: str
) -> None:
    with client(device_settings) as c:
        r = c.get("/api/session", headers={"X-Forwarded-For": IP, "Tailscale-User-Login": login})
        assert r.status_code == (200 if login == OWNER else 401)
        assert lookup == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("StableID", "nDifferentMac"),
        ("Tags", ["tag:other"]),
        ("Addresses", ["100.64.99.1/32"]),
        ("KeyExpiry", "2020-01-01T00:00:00Z"),
        ("KeyExpiry", "bad-date"),
        ("KeyExpiry", None),
        ("Expired", True),
    ],
)
def test_wrong_or_expired_node_is_rejected(
    device_settings: Settings, monkeypatch: pytest.MonkeyPatch, field: str, value: Any
) -> None:
    data = node()
    data["Node"][field] = value
    monkeypatch.setattr("app.tailscale.lookup_node", lambda *_: data)
    with client(device_settings) as c:
        assert c.get("/api/version", headers={"X-Forwarded-For": IP}).status_code == 401


@pytest.mark.parametrize("data", [None, {}, {"Node": None}, {"Node": []}])
def test_malformed_whois_fails_closed(
    device_settings: Settings, monkeypatch: pytest.MonkeyPatch, data: Any
) -> None:
    monkeypatch.setattr("app.tailscale.lookup_node", lambda *_: data)
    with client(device_settings) as c:
        assert c.get("/api/version", headers={"X-Forwarded-For": IP}).status_code == 401


def test_whois_failure_fails_closed(
    device_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_: Any) -> Any:
        raise OSError("internal socket details")

    monkeypatch.setattr("app.tailscale.lookup_node", fail)
    with client(device_settings) as c:
        r = c.get("/api/version", headers={"X-Forwarded-For": IP})
        assert r.status_code == 401
        assert "internal socket" not in r.text


def test_device_writes_still_need_origin_and_csrf(
    device_settings: Settings, lookup: list[str]
) -> None:
    headers = {"X-Forwarded-For": IP}
    with client(device_settings) as c:
        r = c.post("/api/does-not-exist", headers=headers)
        assert r.status_code == 403 and r.json()["code"] == "origin_not_allowed"
        r = c.post("/api/does-not-exist", headers={**headers, "Origin": ORIGIN})
        assert r.status_code == 403 and r.json()["code"] == "csrf_required"
        csrf = c.get("/api/session", headers=headers).json()["csrfToken"]
        r = c.post(
            "/api/does-not-exist", headers={**headers, "Origin": ORIGIN, "X-CSRF-Token": csrf}
        )
        assert r.status_code == 404
    with client(replace(device_settings, read_only=True)) as c:
        r = c.post("/api/does-not-exist", headers=headers)
        assert r.status_code == 403 and r.json()["code"] == "read_only"


def test_disabled_device_path_preserves_missing_identity(
    prod_settings: Settings, lookup: list[str]
) -> None:
    with client(prod_settings) as c:
        r = c.get("/api/version", headers={"X-Forwarded-For": IP})
        assert r.status_code == 401 and r.json()["code"] == "identity_missing"
        assert lookup == []


def test_actual_lookup_timeout_denies(
    device_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    def slow(*_: Any) -> Any:
        time.sleep(0.05)
        return node()

    monkeypatch.setattr("app.tailscale.lookup_node", slow)
    monkeypatch.setattr("app.auth.DEVICE_LOOKUP_TIMEOUT", 0.001)
    with client(device_settings) as c:
        assert c.get("/api/version", headers={"X-Forwarded-For": IP}).status_code == 401

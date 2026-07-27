"""Device + IP sliding-window rate limits for public endpoints."""
from __future__ import annotations

import os
import sys

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("ENABLE_RATE_LIMIT", "true")

    import config
    import rate_limit

    monkeypatch.setattr(config, "ENABLE_RATE_LIMIT", True)
    monkeypatch.setattr(config, "RATE_LIMIT_CHAT_DEVICE", 3)
    monkeypatch.setattr(config, "RATE_LIMIT_CHAT_IP", 5)
    monkeypatch.setattr(config, "RATE_LIMIT_CHAT_WINDOW_S", 60)
    monkeypatch.setattr(config, "RATE_LIMIT_WRITE_DEVICE", 2)
    monkeypatch.setattr(config, "RATE_LIMIT_WRITE_IP", 4)
    monkeypatch.setattr(config, "RATE_LIMIT_WRITE_WINDOW_S", 60)

    rate_limit.get_limiter().reset()

    app = FastAPI()

    @app.post("/chat")
    def chat(_: None = Depends(rate_limit.enforce_rate_limit("chat"))):
        return {"ok": True}

    @app.post("/reports")
    def reports(_: None = Depends(rate_limit.enforce_rate_limit("public_write"))):
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return TestClient(app)


def test_sliding_window_allows_then_blocks():
    import rate_limit

    limiter = rate_limit.SlidingWindowLimiter()
    assert limiter.check("k", 2, 60)[0] is True
    assert limiter.check("k", 2, 60)[0] is True
    ok, retry = limiter.check("k", 2, 60)
    assert ok is False
    assert retry >= 1


def test_sliding_window_resets_after_window(monkeypatch):
    import rate_limit

    limiter = rate_limit.SlidingWindowLimiter()
    times = iter([100.0, 100.5, 161.0])
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: next(times))
    assert limiter.check("k", 1, 60)[0] is True
    assert limiter.check("k", 1, 60)[0] is False
    assert limiter.check("k", 1, 60)[0] is True


def test_device_id_validation():
    from unittest.mock import MagicMock

    import rate_limit

    req = MagicMock()
    req.headers = {"x-device-id": "s_abc12345"}
    assert rate_limit.device_id(req) == "s_abc12345"

    req.headers = {"x-device-id": "bad"}
    assert rate_limit.device_id(req) is None

    req.headers = {"x-device-id": "no spaces allowed!!"}
    assert rate_limit.device_id(req) is None


def test_chat_device_limit_returns_429(client):
    headers = {"X-Device-Id": "device-test-001"}
    for _ in range(3):
        res = client.post("/chat", headers=headers)
        assert res.status_code == 200, res.text

    blocked = client.post("/chat", headers=headers)
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
    assert "Rate limit exceeded" in blocked.json()["detail"]


def test_chat_ip_ceiling_across_devices(client):
    # IP limit is 5; use distinct devices so device buckets never trip first.
    for i in range(5):
        res = client.post("/chat", headers={"X-Device-Id": f"device-ip-ceiling-{i:02d}"})
        assert res.status_code == 200, res.text

    blocked = client.post("/chat", headers={"X-Device-Id": "device-ip-ceiling-99"})
    assert blocked.status_code == 429


def test_missing_device_still_ip_limited(client):
    for _ in range(5):
        assert client.post("/chat").status_code == 200

    blocked = client.post("/chat")
    assert blocked.status_code == 429


def test_health_unaffected(client):
    for _ in range(20):
        assert client.get("/health").status_code == 200


def test_reports_write_limit(client):
    headers = {"X-Device-Id": "device-write-001"}
    assert client.post("/reports", headers=headers).status_code == 200
    assert client.post("/reports", headers=headers).status_code == 200
    blocked = client.post("/reports", headers=headers)
    assert blocked.status_code == 429


def test_app_wires_rate_limit_dependency():
    from pathlib import Path

    text = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
    assert 'Depends(enforce_rate_limit("chat"))' in text
    assert 'Depends(enforce_rate_limit("public_write"))' in text
    assert "from rate_limit import enforce_rate_limit" in text

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from mozaiksai.hosts import runtime


class _FakeAdmin:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[tuple, dict]] = []

    async def command(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return {"ok": 1}


class _FakeMongoClient:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.admin = _FakeAdmin(error=error)


@pytest.mark.asyncio
async def test_health_ping_command_does_not_include_server_selection_timeout() -> None:
    client = _FakeMongoClient()

    await runtime._ping_mongo(client)

    assert client.admin.calls == [(("ping",), {})]


@pytest.mark.asyncio
async def test_health_endpoint_succeeds_with_fake_mongo_ping(monkeypatch) -> None:
    client = _FakeMongoClient()
    monkeypatch.setattr(runtime, "mongo_client", client)
    monkeypatch.setattr(runtime, "simple_transport", object())
    monkeypatch.setattr(runtime, "workflow_status_summary", lambda: {"running": 0})

    result = await runtime.health_check()

    assert result["status"] == "healthy"
    assert result["workflows"] == {"running": 0}
    assert "transport" in result
    assert client.admin.calls == [(("ping",), {})]


@pytest.mark.asyncio
async def test_health_endpoint_returns_clear_failure_when_ping_raises(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "mongo_client", _FakeMongoClient(error=RuntimeError("network down")))

    with pytest.raises(HTTPException) as exc:
        await runtime.health_check()

    assert exc.value.status_code == 503
    assert "MongoDB unreachable" in exc.value.detail
    # Exception details must not be forwarded to callers (security hardening)
    assert "network down" not in exc.value.detail


@pytest.mark.asyncio
async def test_health_response_does_not_expose_secrets(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "mongo_client", _FakeMongoClient())
    monkeypatch.setattr(runtime, "simple_transport", object())
    monkeypatch.setattr(runtime, "workflow_status_summary", lambda: {"status": "ok"})

    result = await runtime.health_check()
    rendered = repr(result).lower()

    for forbidden in ["password", "secret", "token", "authorization", "mongodb+srv://"]:
        assert forbidden not in rendered


# ---------------------------------------------------------------------------
# /api/health/ready — readiness probe
# ---------------------------------------------------------------------------


def _ready_response(monkeypatch, *, startup_degraded=False, startup_degraded_reason=None, failed_module_names=None):
    """Helper: call /api/health/ready via TestClient with faked runtime state."""
    monkeypatch.setattr(runtime, "mongo_client", _FakeMongoClient())
    monkeypatch.setattr(runtime, "simple_transport", object())
    runtime.app.state.startup_degraded = startup_degraded
    runtime.app.state.startup_degraded_reason = startup_degraded_reason
    runtime.app.state.failed_module_names = failed_module_names or []
    client = TestClient(runtime.app, raise_server_exceptions=False)
    return client.get("/api/health/ready")


def test_readiness_returns_200_when_healthy(monkeypatch) -> None:
    resp = _ready_response(monkeypatch)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"]["mongodb"] == "ok"
    assert body["checks"]["transport"] == "ok"
    assert body["checks"]["app_startup"] == "ok"
    assert "failed_modules" not in body


def test_readiness_returns_503_when_startup_degraded(monkeypatch) -> None:
    resp = _ready_response(
        monkeypatch,
        startup_degraded=True,
        startup_degraded_reason="MODULE_LOAD_PARTIAL: 1 module(s) failed to load: bad_module",
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert "degraded" in body["checks"]["app_startup"]
    assert "MODULE_LOAD_PARTIAL" in body["checks"]["app_startup"]


def test_readiness_includes_failed_modules_when_set(monkeypatch) -> None:
    resp = _ready_response(
        monkeypatch,
        startup_degraded=True,
        startup_degraded_reason="MODULE_LOAD_PARTIAL: 2 module(s) failed: alpha, beta",
        failed_module_names=["alpha", "beta"],
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["failed_modules"] == ["alpha", "beta"]


def test_readiness_no_failed_modules_key_when_all_loaded(monkeypatch) -> None:
    resp = _ready_response(monkeypatch, failed_module_names=[])
    assert resp.status_code == 200
    assert "failed_modules" not in resp.json()


def test_readiness_returns_503_when_mongo_not_initialized(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "mongo_client", None)
    monkeypatch.setattr(runtime, "simple_transport", object())
    runtime.app.state.startup_degraded = False
    runtime.app.state.failed_module_names = []
    client = TestClient(runtime.app, raise_server_exceptions=False)
    resp = client.get("/api/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["mongodb"] == "not_initialized"


def test_readiness_returns_503_when_transport_not_initialized(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "mongo_client", _FakeMongoClient())
    monkeypatch.setattr(runtime, "simple_transport", None)
    runtime.app.state.startup_degraded = False
    runtime.app.state.failed_module_names = []
    client = TestClient(runtime.app, raise_server_exceptions=False)
    resp = client.get("/api/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["transport"] == "not_initialized"


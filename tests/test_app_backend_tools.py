"""Tests: app_backend_tools AG2 tool functions are error-safe.

AG2 tool functions must never raise unhandled exceptions — any backend
error must be returned as a JSON error string so the agent can continue.
"""
from __future__ import annotations

import json

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FailingBackend:
    """Backend stub that raises on every call."""

    async def request(self, *args, **kwargs):
        raise ConnectionError("backend unreachable")

    async def emit(self, *args, **kwargs):
        raise RuntimeError("dispatcher down")

    async def health(self, *args, **kwargs):
        raise TimeoutError("health check timed out")


class _HealthyBackend:
    """Backend stub that returns success."""

    from mozaiksai.core.ports.app_backend import BackendHealth, BackendResponse

    async def request(self, method, path, *, json_body=None, headers=None, user_token=None):
        from mozaiksai.core.ports.app_backend import BackendResponse
        return BackendResponse(success=True, status_code=200, data={"ok": True})

    async def emit(self, event_type, data):
        return True

    async def health(self):
        from mozaiksai.core.ports.app_backend import BackendHealth
        return BackendHealth(healthy=True, version="1.0.0")


# ---------------------------------------------------------------------------
# backend_request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backend_request_returns_json_on_backend_exception(monkeypatch):
    """backend_request must return JSON error string, never raise."""
    from mozaiksai.core.workflow import app_backend_tools

    monkeypatch.setattr(
        app_backend_tools,
        "_get_backend_for_test",
        lambda: _FailingBackend(),
        raising=False,
    )
    # Patch get_app_backend inside the module.
    import mozaiksai.core.adapters.http_app_backend as _http_mod
    monkeypatch.setattr(_http_mod, "get_app_backend", lambda: _FailingBackend())

    result = await app_backend_tools.backend_request("GET", "/api/test")
    parsed = json.loads(result)

    assert parsed["status"] == "error"
    assert "error" in parsed
    assert "backend unreachable" in parsed["error"]


@pytest.mark.asyncio
async def test_backend_request_success_path(monkeypatch):
    import mozaiksai.core.adapters.http_app_backend as _http_mod
    from mozaiksai.core.workflow import app_backend_tools

    monkeypatch.setattr(_http_mod, "get_app_backend", lambda: _HealthyBackend())

    result = await app_backend_tools.backend_request("POST", "/api/items", payload={"name": "x"})
    parsed = json.loads(result)

    assert parsed["status"] == "success"
    assert parsed["data"]["ok"] is True


# ---------------------------------------------------------------------------
# emit_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_event_returns_json_on_exception(monkeypatch):
    """emit_event must return JSON failed status, never raise."""
    import mozaiksai.core.adapters.http_app_backend as _http_mod
    from mozaiksai.core.workflow import app_backend_tools

    monkeypatch.setattr(_http_mod, "get_app_backend", lambda: _FailingBackend())

    result = await app_backend_tools.emit_event("order.placed", {"order_id": "x"})
    parsed = json.loads(result)

    assert parsed["status"] == "failed"
    assert parsed["event_type"] == "order.placed"
    assert "error" in parsed


@pytest.mark.asyncio
async def test_emit_event_success_path(monkeypatch):
    import mozaiksai.core.adapters.http_app_backend as _http_mod
    from mozaiksai.core.workflow import app_backend_tools

    monkeypatch.setattr(_http_mod, "get_app_backend", lambda: _HealthyBackend())

    result = await app_backend_tools.emit_event("listing.saved", {"id": "abc"})
    parsed = json.loads(result)

    assert parsed["status"] == "emitted"
    assert parsed["event_type"] == "listing.saved"


# ---------------------------------------------------------------------------
# check_backend_health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_backend_health_returns_json_on_exception(monkeypatch):
    """check_backend_health must return JSON unhealthy, never raise."""
    import mozaiksai.core.adapters.http_app_backend as _http_mod
    from mozaiksai.core.workflow import app_backend_tools

    monkeypatch.setattr(_http_mod, "get_app_backend", lambda: _FailingBackend())

    result = await app_backend_tools.check_backend_health()
    parsed = json.loads(result)

    assert parsed["healthy"] is False
    assert "error" in parsed


@pytest.mark.asyncio
async def test_check_backend_health_success_path(monkeypatch):
    import mozaiksai.core.adapters.http_app_backend as _http_mod
    from mozaiksai.core.workflow import app_backend_tools

    monkeypatch.setattr(_http_mod, "get_app_backend", lambda: _HealthyBackend())

    result = await app_backend_tools.check_backend_health()
    parsed = json.loads(result)

    assert parsed["healthy"] is True
    assert parsed["version"] == "1.0.0"

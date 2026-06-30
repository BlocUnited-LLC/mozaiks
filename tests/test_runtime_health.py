from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

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


class _FakeAppState:
    """Minimal app state carrier used to construct mock Request objects."""

    def __init__(
        self,
        *,
        startup_degraded: bool = False,
        startup_degraded_reason: str | None = None,
        failed_module_names: list[str] | None = None,
    ) -> None:
        self.startup_degraded = startup_degraded
        self.startup_degraded_reason = startup_degraded_reason or ""
        self.failed_module_names = failed_module_names or []


class _FakeApp:
    def __init__(self, state: _FakeAppState) -> None:
        self.state = state


def _make_request(state: _FakeAppState) -> Request:
    """Build a minimal Starlette Request carrying the given app state.

    Using a direct function call (not TestClient) avoids building the
    FastAPI middleware stack, which locks the app and breaks tests that
    later import platform.py (which adds module-level middleware).
    """
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/health/ready",
        "headers": [],
        "query_string": b"",
        "app": _FakeApp(state),
    }
    return Request(scope)


async def _call_readiness(
    monkeypatch,
    *,
    startup_degraded: bool = False,
    startup_degraded_reason: str | None = None,
    failed_module_names: list[str] | None = None,
    mongo_error: Exception | None = None,
    transport: object | None = ...,  # type: ignore[assignment]
) -> tuple[int, dict]:
    """Call health_readiness directly and return (status_code, body)."""
    monkeypatch.setattr(runtime, "mongo_client", _FakeMongoClient(error=mongo_error))
    monkeypatch.setattr(
        runtime,
        "simple_transport",
        object() if transport is ... else transport,
    )
    state = _FakeAppState(
        startup_degraded=startup_degraded,
        startup_degraded_reason=startup_degraded_reason,
        failed_module_names=failed_module_names,
    )
    request = _make_request(state)
    try:
        result = await runtime.health_readiness(request)
        # health_readiness returns a JSONResponse (200 or 503); parse both correctly
        if hasattr(result, "body"):
            import json
            return result.status_code, json.loads(result.body)
        return 200, result
    except HTTPException as exc:
        body = exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}
        return exc.status_code, body


@pytest.mark.asyncio
async def test_readiness_returns_200_when_healthy(monkeypatch) -> None:
    status, body = await _call_readiness(monkeypatch)
    assert status == 200
    assert body["status"] == "ready"
    assert body["checks"]["mongodb"] == "ok"
    assert body["checks"]["transport"] == "ok"
    assert body["checks"]["app_startup"] == "ok"
    assert "failed_modules" not in body
    assert "failed_module_count" not in body


@pytest.mark.asyncio
async def test_readiness_returns_503_when_startup_degraded(monkeypatch) -> None:
    status, body = await _call_readiness(
        monkeypatch,
        startup_degraded=True,
        startup_degraded_reason="MODULE_LOAD_PARTIAL: 1 module(s) failed to load: bad_module",
    )
    assert status == 503
    assert body["status"] == "degraded"
    assert "degraded" in body["checks"]["app_startup"]
    assert "MODULE_LOAD_PARTIAL" in body["checks"]["app_startup"]


@pytest.mark.asyncio
async def test_readiness_includes_failed_module_count_when_set(monkeypatch) -> None:
    status, body = await _call_readiness(
        monkeypatch,
        startup_degraded=True,
        startup_degraded_reason="MODULE_LOAD_PARTIAL: 2 module(s) failed to load",
        failed_module_names=["alpha", "beta"],
    )
    assert status == 503
    # Module names must not be exposed on unauthenticated readiness probe
    assert "failed_modules" not in body
    assert body["failed_module_count"] == 2


@pytest.mark.asyncio
async def test_readiness_no_failed_modules_key_when_all_loaded(monkeypatch) -> None:
    status, body = await _call_readiness(monkeypatch, failed_module_names=[])
    assert status == 200
    assert "failed_modules" not in body


@pytest.mark.asyncio
async def test_readiness_returns_503_when_mongo_not_initialized(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "mongo_client", None)
    monkeypatch.setattr(runtime, "simple_transport", object())
    state = _FakeAppState()
    request = _make_request(state)
    import json
    result = await runtime.health_readiness(request)
    body = json.loads(result.body)
    assert result.status_code == 503
    assert body["status"] == "degraded"
    assert body["checks"]["mongodb"] == "not_initialized"


@pytest.mark.asyncio
async def test_readiness_returns_503_when_transport_not_initialized(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "mongo_client", _FakeMongoClient())
    monkeypatch.setattr(runtime, "simple_transport", None)
    state = _FakeAppState()
    request = _make_request(state)
    import json
    result = await runtime.health_readiness(request)
    body = json.loads(result.body)
    assert result.status_code == 503
    assert body["status"] == "degraded"
    assert body["checks"]["transport"] == "not_initialized"

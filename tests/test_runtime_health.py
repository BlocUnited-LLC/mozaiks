from __future__ import annotations

import pytest
from fastapi import HTTPException

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
    assert "network down" in exc.value.detail


@pytest.mark.asyncio
async def test_health_response_does_not_expose_secrets(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "mongo_client", _FakeMongoClient())
    monkeypatch.setattr(runtime, "simple_transport", object())
    monkeypatch.setattr(runtime, "workflow_status_summary", lambda: {"status": "ok"})

    result = await runtime.health_check()
    rendered = repr(result).lower()

    for forbidden in ["password", "secret", "token", "authorization", "mongodb+srv://"]:
        assert forbidden not in rendered

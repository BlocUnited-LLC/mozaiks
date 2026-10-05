"""Cancellation is real; child execution and Mongo are isolated test boundaries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import anyio
import pytest
from motor import motor_asyncio

from factory_app.workflows.AppGenerator.tools import app_runtime_smoke


@pytest.mark.asyncio
@pytest.mark.parametrize("cancellation", ["anyio", "asyncio"])
@pytest.mark.parametrize("drop_fails", [False, True])
async def test_cancelled_smoke_finishes_child_and_database_cleanup(
    monkeypatch, tmp_path, cancellation, drop_fails,
):
    (tmp_path / "app.json").write_text("{}", encoding="utf-8")
    events = []
    original_cancellation = []
    request = {}

    class Child:
        def run(self, app_root, smoke_request, timeout_seconds):
            request.update(smoke_request)
            events.append("child_started")

        def kill(self):
            events.append("child_stopped")

    async def thread_boundary(function, *args):
        if function.__name__ == "run":
            function(*args)
            if cancellation == "anyio":
                scope.cancel()
            else:
                asyncio.current_task().cancel()
            try:
                await anyio.lowlevel.checkpoint()
            except asyncio.CancelledError as exc:
                original_cancellation.append(exc)
                raise
            pytest.fail("Smoke did not receive cancellation")
        await anyio.lowlevel.checkpoint()
        return function(*args)

    async def drop_database(name):
        await anyio.lowlevel.checkpoint()
        assert name == request["database_name"]
        assert name.startswith("mozaiks_runtime_smoke_")
        events.append("database_cleanup_attempted")
        if drop_fails:
            raise ConnectionError("Database unavailable")

    client = SimpleNamespace(
        admin=SimpleNamespace(command=AsyncMock()),
        drop_database=AsyncMock(side_effect=drop_database),
        close=Mock(side_effect=lambda: events.append("client_closed")),
    )
    monkeypatch.setattr(motor_asyncio, "AsyncIOMotorClient", lambda *args, **kwargs: client)
    monkeypatch.setattr(app_runtime_smoke, "_ChildProcess", Child)
    # Keep the module's actual wait_for, while giving both thread awaits a
    # deterministic cancellation checkpoint without creating a child process.
    monkeypatch.setattr(app_runtime_smoke, "asyncio", SimpleNamespace(
        wait_for=asyncio.wait_for, to_thread=thread_boundary,
    ))
    with anyio.CancelScope() as scope:
        with pytest.raises(asyncio.CancelledError) as raised:
            await app_runtime_smoke.run_app_runtime_smoke(
                tmp_path, mongo_uri="mongodb://isolated-test.invalid", timeout_seconds=5,
            )

    assert events == ["child_started", "child_stopped", "database_cleanup_attempted", "client_closed"]
    assert raised.value is original_cancellation[0]
    client.drop_database.assert_awaited_once_with(request["database_name"])
    client.close.assert_called_once_with()

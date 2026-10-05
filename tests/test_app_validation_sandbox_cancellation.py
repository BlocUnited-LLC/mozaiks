"""Provider teardown must finish before a cancelled validation request unwinds."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import pytest

from factory_app.workflows.AppGenerator.tools import app_validation
from mozaiksai.core import adapters


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["docker", "e2b"])
@pytest.mark.parametrize("cancellation", ["anyio", "asyncio"])
async def test_cancelled_validation_finishes_teardown_and_propagates(monkeypatch, provider, cancellation):
    terminated = []
    cancellation_seen = []

    async def run(**kwargs):
        if cancellation == "anyio":
            scope.cancel()
        else:
            asyncio.current_task().cancel()
        try:
            await anyio.lowlevel.checkpoint()
        except asyncio.CancelledError as exc:
            cancellation_seen.append(exc)
            raise
        pytest.fail("Validation did not receive cancellation")

    async def terminate(*, session_id):
        # A real provider suspends while confirming that the sandbox is gone.
        await anyio.lowlevel.checkpoint()
        terminated.append(session_id)
        return True

    adapter = SimpleNamespace(
        create_session=AsyncMock(return_value=SimpleNamespace(session_id="cancelled-build", provider=provider)),
        write_files=AsyncMock(), run_command=AsyncMock(side_effect=run),
        terminate_session=AsyncMock(side_effect=terminate),
    )
    monkeypatch.setattr(adapters, "get_sandbox_adapter", lambda _: adapter)
    with anyio.CancelScope() as scope:
        with pytest.raises(asyncio.CancelledError) as raised:
            await app_validation._run_sandbox_validation(
                strategy=provider, resolved_files={"app.json": "{}"}, commands=[],
                start_dev_server=False, timeout_seconds=120,
            )

    assert raised.value is cancellation_seen[0]
    assert terminated == ["cancelled-build"]
    adapter.terminate_session.assert_awaited_once_with(session_id="cancelled-build")
    assert adapter.run_command.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["docker", "e2b"])
@pytest.mark.parametrize("cleanup_error", [False, True])
async def test_unconfirmed_sandbox_cleanup_keeps_failed_status(monkeypatch, provider, cleanup_error):
    async def terminate(**kwargs):
        await anyio.lowlevel.checkpoint()
        if cleanup_error:
            raise ConnectionError("Provider unreachable")
        return False

    adapter = SimpleNamespace(
        create_session=AsyncMock(return_value=SimpleNamespace(session_id="unconfirmed-build", provider=provider)),
        write_files=AsyncMock(),
        run_command=AsyncMock(return_value=SimpleNamespace(success=True, stdout="compiled", stderr="")),
        terminate_session=AsyncMock(side_effect=terminate),
    )
    monkeypatch.setattr(adapters, "get_sandbox_adapter", lambda _: adapter)
    result = await app_validation._run_sandbox_validation(
        strategy=provider, resolved_files={"app.json": "{}"}, commands=[],
        start_dev_server=False, timeout_seconds=120,
    )

    assert result["success"] is False
    assert result["validation_status"] == "failed"
    assert result["sandbox_session_id"] == "unconfirmed-build"
    assert result["sandbox_provider"] == provider
    assert result["sandbox_terminated"] is False
    assert result["infrastructure_failure"] is True
    assert result["preview_url"] is None
    assert any("Sandbox cleanup could not be confirmed" in error for error in result["errors"])

"""Teardown with real SDK exceptions and mocked provider calls only.

The adapter kills by session ID without reconnecting, because reconnect can
resume a paused sandbox before teardown.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from mozaiksai.core.adapters import e2b_sandbox
from mozaiksai.core.sandbox import preview_sessions
from tests.test_artifact_preview_sessions import _manager

pytest.importorskip("e2b_code_interpreter")
sdk_errors = pytest.importorskip("e2b.exceptions")


@pytest.fixture
def sdk(monkeypatch):
    sandbox = SimpleNamespace(sandbox_id="provider-session")
    factory = SimpleNamespace(
        create=Mock(return_value=sandbox), connect=Mock(return_value=sandbox), kill=Mock(return_value=True),
        get_info=Mock(return_value=SimpleNamespace(end_at=datetime.now(UTC) + timedelta(seconds=60))),
    )
    monkeypatch.setattr(e2b_sandbox, "Sandbox", factory)
    return factory, sandbox


@pytest.mark.asyncio
async def test_confirmed_not_found_is_successful_teardown(sdk):
    factory, _ = sdk
    factory.kill.side_effect = sdk_errors.NotFoundException("Sandbox expired")

    assert await e2b_sandbox.E2BSandboxAdapter().terminate_session(session_id="provider-session") is True
    factory.kill.assert_called_once_with("provider-session")
    factory.connect.assert_not_called()


@pytest.mark.parametrize("kill_result", [True, False])
@pytest.mark.asyncio
async def test_sdk_kill_success_or_404_both_confirm_absence(sdk, kill_result):
    factory, _ = sdk
    factory.kill.return_value = kill_result

    assert await e2b_sandbox.E2BSandboxAdapter().terminate_session(session_id="provider-session") is True
    factory.kill.assert_called_once_with("provider-session")
    factory.connect.assert_not_called()


@pytest.mark.parametrize("error_type", [
    sdk_errors.AuthenticationException,
    sdk_errors.TimeoutException,
    sdk_errors.SandboxException,
    ConnectionError,
    LookupError,
])
@pytest.mark.asyncio
async def test_unconfirmed_absence_errors_propagate_unchanged(sdk, error_type):
    factory, _ = sdk
    error = error_type("not found in an unrelated error message")
    factory.kill.side_effect = error

    with pytest.raises(error_type) as raised:
        await e2b_sandbox.E2BSandboxAdapter().terminate_session(session_id="provider-session")

    assert raised.value is error
    factory.kill.assert_called_once_with("provider-session")
    factory.connect.assert_not_called()


async def _expired_preview(monkeypatch):
    adapter = e2b_sandbox.E2BSandboxAdapter()
    manager = _manager(adapter, provider="e2b")
    manager._queue_seconds = 5
    identity = dict(app_id="factory", user_id="owner", target_app_id="target", build_registry_id="registry")
    state = await manager.create_or_reuse("artifact", **identity)
    after_deadline = state.expires_at + timedelta(seconds=1)
    monkeypatch.setattr(preview_sessions, "_utcnow", lambda: after_deadline)
    socket = AsyncMock()
    manager._ws_clients[state.sandbox_id] = {socket}
    return manager, state, socket, identity


@pytest.mark.parametrize("operation", ["recreate", "status"])
@pytest.mark.asyncio
async def test_expired_provider_session_does_not_block_manager_recovery(sdk, operation, monkeypatch):
    factory, _ = sdk
    manager, state, socket, identity = await _expired_preview(monkeypatch)
    factory.kill.side_effect = sdk_errors.NotFoundException("Sandbox already expired")

    if operation == "status":
        with pytest.raises(KeyError, match="expired"):
            await manager.status(state.sandbox_id)
    replacement = await manager.create_or_reuse("artifact", **identity)

    assert replacement.sandbox_id != state.sandbox_id
    assert await manager._store.get(state.sandbox_id) is None
    assert state.sandbox_id not in manager._ws_clients
    assert factory.create.call_count == 2
    socket.close.assert_awaited_once()


@pytest.mark.parametrize("error_type", [sdk_errors.AuthenticationException, sdk_errors.TimeoutException, ConnectionError])
@pytest.mark.asyncio
async def test_provider_outage_keeps_manager_cleanup_state(sdk, error_type, monkeypatch):
    factory, _ = sdk
    manager, state, socket, identity = await _expired_preview(monkeypatch)
    error = error_type("Provider is unavailable")
    factory.kill.side_effect = error

    with pytest.raises(error_type) as raised:
        await manager.create_or_reuse("artifact", **identity)

    assert raised.value is error
    current = await manager._store.get(state.sandbox_id)
    assert current["session_id"] == state.session_id
    assert (current["app_id"], current["user_id"], current["artifact_id"]) == (state.app_id, state.user_id, state.artifact_id)
    assert socket in manager._ws_clients[state.sandbox_id]
    socket.close.assert_not_awaited()
    assert factory.create.call_count == 1


@pytest.mark.asyncio
async def test_missing_sdk_still_fails_explicitly(monkeypatch):
    monkeypatch.setattr(e2b_sandbox, "Sandbox", None)

    with pytest.raises(RuntimeError, match="not installed"):
        await e2b_sandbox.E2BSandboxAdapter().terminate_session(session_id="provider-session")

"""Teardown with real SDK exceptions and mocked provider calls only.

Verified against e2b 2.14.0 / e2b-code-interpreter 2.4.1:
SandboxApi._cls_connect raises NotFoundException on HTTP 404;
SandboxApi._cls_kill returns False on HTTP 404 and raises for other errors.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from mozaiksai.core.adapters import e2b_sandbox
from mozaiksai.core.sandbox.preview_sessions import ArtifactPreviewSessionManager

pytest.importorskip("e2b_code_interpreter")
sdk_errors = pytest.importorskip("e2b.exceptions")


@pytest.fixture
def sdk(monkeypatch):
    sandbox = SimpleNamespace(sandbox_id="provider-session", kill=Mock(return_value=True))
    factory = SimpleNamespace(create=Mock(return_value=sandbox), connect=Mock(return_value=sandbox))
    monkeypatch.setattr(e2b_sandbox, "Sandbox", factory)
    return factory, sandbox


@pytest.mark.parametrize("stage", ["connect", "kill"])
@pytest.mark.asyncio
async def test_confirmed_not_found_is_successful_teardown(sdk, stage):
    factory, sandbox = sdk
    operation = factory.connect if stage == "connect" else sandbox.kill
    operation.side_effect = sdk_errors.NotFoundException("Sandbox expired")

    assert await e2b_sandbox.E2BSandboxAdapter().terminate_session(session_id="provider-session") is True
    if stage == "connect":
        sandbox.kill.assert_not_called()


@pytest.mark.parametrize("kill_result", [True, False])
@pytest.mark.asyncio
async def test_sdk_kill_success_or_404_both_confirm_absence(sdk, kill_result):
    _, sandbox = sdk
    sandbox.kill.return_value = kill_result

    assert await e2b_sandbox.E2BSandboxAdapter().terminate_session(session_id="provider-session") is True
    sandbox.kill.assert_called_once_with()


@pytest.mark.parametrize("stage", ["connect", "kill"])
@pytest.mark.parametrize("error_type", [
    sdk_errors.AuthenticationException,
    sdk_errors.TimeoutException,
    sdk_errors.SandboxException,
    ConnectionError,
    LookupError,
])
@pytest.mark.asyncio
async def test_unconfirmed_absence_errors_propagate_unchanged(sdk, stage, error_type):
    factory, sandbox = sdk
    error = error_type("not found in an unrelated error message")
    operation = factory.connect if stage == "connect" else sandbox.kill
    operation.side_effect = error

    with pytest.raises(error_type) as raised:
        await e2b_sandbox.E2BSandboxAdapter().terminate_session(session_id="provider-session")

    assert raised.value is error


async def _expired_preview():
    adapter = e2b_sandbox.E2BSandboxAdapter()
    manager = ArtifactPreviewSessionManager(provider_resolver=lambda: ("e2b", adapter))
    identity = dict(app_id="factory", user_id="owner", target_app_id="target", build_registry_id="registry")
    state = await manager.create_or_reuse("artifact", **identity)
    state.created_at -= timedelta(minutes=manager._ttl_minutes + 1)
    socket = AsyncMock()
    manager._ws_clients[state.sandbox_id] = {socket}
    return manager, state, socket, identity


@pytest.mark.parametrize("operation", ["recreate", "status"])
@pytest.mark.asyncio
async def test_expired_provider_session_does_not_block_manager_recovery(sdk, operation):
    factory, _ = sdk
    manager, state, socket, identity = await _expired_preview()
    factory.connect.side_effect = sdk_errors.NotFoundException("Sandbox already expired")

    if operation == "status":
        with pytest.raises(KeyError, match="expired"):
            await manager.status(state.sandbox_id)
    replacement = await manager.create_or_reuse("artifact", **identity)

    assert replacement.sandbox_id != state.sandbox_id
    assert state.sandbox_id not in manager._sessions
    assert state.sandbox_id not in manager._ws_clients
    assert factory.create.call_count == 2
    socket.close.assert_awaited_once()


@pytest.mark.parametrize("error_type", [sdk_errors.AuthenticationException, sdk_errors.TimeoutException, ConnectionError])
@pytest.mark.asyncio
async def test_provider_outage_keeps_manager_cleanup_state(sdk, error_type):
    factory, _ = sdk
    manager, state, socket, identity = await _expired_preview()
    error = error_type("Provider is unavailable")
    factory.connect.side_effect = error

    with pytest.raises(error_type) as raised:
        await manager.create_or_reuse("artifact", **identity)

    assert raised.value is error
    assert manager._sessions[state.sandbox_id] is state
    assert manager._artifact_to_sandbox[(state.app_id, state.user_id, state.artifact_id)] == state.sandbox_id
    assert socket in manager._ws_clients[state.sandbox_id]
    socket.close.assert_not_awaited()
    assert factory.create.call_count == 1


@pytest.mark.asyncio
async def test_missing_sdk_still_fails_explicitly(monkeypatch):
    monkeypatch.setattr(e2b_sandbox, "Sandbox", None)

    with pytest.raises(RuntimeError, match="not installed"):
        await e2b_sandbox.E2BSandboxAdapter().terminate_session(session_id="provider-session")

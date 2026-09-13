from unittest.mock import AsyncMock

import pytest

from mozaiksai.core.adapters.docker_sandbox import DockerSandboxAdapter
from mozaiksai.core.ports.sandbox import SandboxRunResult
from mozaiksai.core.sandbox.preview_sessions import _safe_relpath
from tests.test_artifact_preview_sessions import FakeSandboxAdapter, _create, _manager


def test_preview_rejects_absolute_paths():
    assert _safe_relpath("/etc/passwd") is None
    assert _safe_relpath("C:/outside.txt") is None


@pytest.mark.asyncio
async def test_dead_server_never_becomes_running():
    adapter = FakeSandboxAdapter()
    adapter.install_result = SandboxRunResult(success=False, exit_code=1, stderr="connection refused")
    manager = _manager(adapter)
    state = await _create(manager)
    result = await manager._finish_start(state, 3000)
    assert result.status == "error"
    assert result.preview_url is None


@pytest.mark.asyncio
async def test_docker_background_failure_is_not_success(monkeypatch):
    process = AsyncMock()
    process.wait.return_value = 125
    monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(return_value=process))
    result = await DockerSandboxAdapter().run_command(
        session_id="missing-container", command="false", background=True,
    )
    assert not result.success
    assert result.exit_code == 125


@pytest.mark.asyncio
async def test_canonical_bundle_has_a_platform_launch_path():
    adapter = FakeSandboxAdapter()
    manager = _manager(adapter)
    state = await _create(manager)
    await manager.sync(state.sandbox_id, files=[
        {"path": "app.json", "content": '{"appId":"preview-app","appName":"Preview","authRequired":false}'},
        {"path": "requirements.txt", "content": ""},
    ], deleted=[])
    result = await manager.start(state.sandbox_id)
    assert result.status == "running"
    commands = [kwargs["command"] for name, kwargs in adapter.calls if name == "run_command"]
    assert any("mozaiks" in command for command in commands)
    assert not any("main:app" in command for command in commands)

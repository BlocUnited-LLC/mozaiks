import asyncio
import io
import tarfile
from unittest.mock import AsyncMock

import pytest

from mozaiksai.core.adapters.docker_sandbox import DockerSandboxAdapter
from mozaiksai.core.ports.sandbox import SandboxRunResult
from mozaiksai.core.sandbox.preview_sessions import _safe_relpath
from tests.test_artifact_preview_sessions import (
    MANIFEST,
    FakeSandboxAdapter,
    _create,
    _manager,
    _metadata,
    _read,
    _sync_manifest,
)


def test_preview_rejects_absolute_paths():
    assert _safe_relpath("/etc/passwd") is None
    assert _safe_relpath("C:/outside.txt") is None


@pytest.mark.asyncio
async def test_dead_server_never_becomes_running():
    adapter = FakeSandboxAdapter()
    adapter.install_result = SandboxRunResult(success=False, exit_code=1, stderr="connection refused")
    manager = _manager(adapter)
    state = await _create(manager)
    await _sync_manifest(manager, state)
    result = await manager.start(state.sandbox_id)
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


@pytest.mark.parametrize("alias", ["app/app.json", "app\\app.json", "app.json ", " app.json"])
@pytest.mark.asyncio
async def test_manifest_alias_rejected_before_initial_provider_write(alias):
    adapter = FakeSandboxAdapter()
    manager = _manager(adapter)
    state = await _create(manager)
    before = list(adapter.calls)

    with pytest.raises(ValueError):
        await manager.sync(state.sandbox_id, [
            {"path": "app.json", "content": MANIFEST},
            {"path": alias, "content": '{"appId":"foreign-app"}'},
        ], [])

    assert adapter.calls == before
    assert _metadata(await _read(manager, state)) == (None, [], False)


@pytest.mark.asyncio
async def test_binary_file_path_rejected_before_provider_write():
    adapter = FakeSandboxAdapter()
    manager = _manager(adapter)
    state = await _create(manager)
    before = list(adapter.calls)

    with pytest.raises(ValueError, match="Invalid preview file path"):
        await manager.sync(state.sandbox_id, [{"path": b"app.json", "content": MANIFEST}], [])

    assert adapter.calls == before
    assert _metadata(await _read(manager, state)) == (None, [], False)


@pytest.mark.parametrize("path", ["app.json", "ui/pages/home.yaml"])
@pytest.mark.parametrize("operation", ["write", "delete"])
@pytest.mark.asyncio
async def test_workspace_alias_cannot_modify_an_existing_snapshot(path, operation):
    adapter = FakeSandboxAdapter()
    manager = _manager(adapter)
    state = await _create(manager)
    await _sync_manifest(manager, state, **{"ui/pages/home.yaml": "original"})
    snapshot = _metadata(await _read(manager, state))
    before = list(adapter.calls)
    alias = f"app/{path}"

    with pytest.raises(ValueError):
        await manager.sync(
            state.sandbox_id,
            [{"path": alias, "content": "replacement"}] if operation == "write" else [],
            [alias] if operation == "delete" else [],
        )

    assert _metadata(await _read(manager, state)) == snapshot
    assert adapter.calls == before


@pytest.mark.parametrize("failure", ["write", "delete", "cancelled_write"])
@pytest.mark.asyncio
async def test_failed_sync_cannot_restart_or_resync_a_partially_written_sandbox(monkeypatch, failure):
    adapter = FakeSandboxAdapter()
    manager = _manager(adapter)
    state = await _create(manager)
    await _sync_manifest(manager, state, **{
        "ui/pages/home.yaml": "original", "ui/pages/obsolete.yaml": "old page",
    })
    await manager.start(state.sandbox_id)
    snapshot = _metadata(await _read(manager, state))
    provider_files = dict(next(kwargs["files"] for name, kwargs in adapter.calls if name == "write_files"))

    async def partial_write(**kwargs):
        path, content = next(iter(kwargs["files"].items()))
        provider_files[path] = content
        if failure == "cancelled_write":
            raise asyncio.CancelledError()
        if failure == "write":
            raise RuntimeError("Provider failed after writing a file")
        return {"written": list(kwargs["files"]), "count": len(kwargs["files"])}

    writer = AsyncMock(side_effect=partial_write)
    monkeypatch.setattr(adapter, "write_files", writer)
    if failure == "delete":
        adapter.command_results["rm -f"] = SandboxRunResult(success=False, exit_code=1)
    expected_error = asyncio.CancelledError if failure == "cancelled_write" else RuntimeError
    with pytest.raises(expected_error):
        await manager.sync(
            state.sandbox_id,
            [{"path": "ui/pages/home.yaml", "content": "partially updated"}],
            ["ui/pages/obsolete.yaml"],
        )

    assert provider_files["app/ui/pages/home.yaml"] == "partially updated"
    assert provider_files["app/ui/pages/obsolete.yaml"] == "old page"
    current = await _read(manager, state)
    assert _metadata(current) == snapshot
    assert current.status == "error" and current.preview_url is None
    before_retry = list(adapter.calls)
    error = current.last_error

    restarted = await manager.start(state.sandbox_id)
    assert restarted.status == "error" and restarted.preview_url is None
    assert restarted.last_error == error
    with pytest.raises(ValueError, match="recreate"):
        await manager.sync(state.sandbox_id, [{"path": "app.json", "content": MANIFEST}], [])
    assert adapter.calls == before_retry
    assert writer.await_count == 1

    replacement = await _create(manager)
    assert replacement.sandbox_id != state.sandbox_id
    with pytest.raises(KeyError):
        await _read(manager, state)
    assert any(kind == "terminate_session" for kind, _ in adapter.calls)


@pytest.mark.asyncio
async def test_docker_copies_binary_files_as_the_container_user(monkeypatch):
    adapter = DockerSandboxAdapter()
    run = AsyncMock(return_value=(0, "", ""))
    monkeypatch.setattr(adapter, "_run", run)
    files = {"app/ui/pages/home.yaml": "title: Home\n", "app/brand/icon.png": b"\x89PNG\x00\xff"}

    result = await adapter.write_files(session_id="review-container", files=files)

    assert result == {"written": list(files), "count": 2}
    copy = run.await_args_list[-1]
    command = copy.args[0]
    assert command[:3] == ["docker", "exec", "-i"]
    assert "tar" in command and "--no-same-owner" in command
    assert "--user" not in command
    with tarfile.open(fileobj=io.BytesIO(copy.kwargs["input_data"])) as archive:
        assert archive.getnames() == list(files)
        assert archive.extractfile("app/brand/icon.png").read() == files["app/brand/icon.png"]
        assert archive.extractfile("app/ui/pages/home.yaml").read() == files["app/ui/pages/home.yaml"].encode()


@pytest.mark.parametrize("path", ["app/app.json ", "../escape", "C:/escape", "."])
@pytest.mark.asyncio
async def test_docker_rejects_invalid_paths_before_any_provider_command(monkeypatch, tmp_path, path):
    if path == "C:/escape":
        path = str(tmp_path / "outside.txt")
    adapter = DockerSandboxAdapter()
    run = AsyncMock(return_value=(0, "", ""))
    monkeypatch.setattr(adapter, "_run", run)

    with pytest.raises(ValueError):
        await adapter.write_files(session_id="review-container", files={path: "invalid"})

    run.assert_not_awaited()


@pytest.mark.parametrize("confirmation,terminated", [
    ((0, "", ""), True),
    ((0, "review-container\n", ""), False),
    ((1, "", "daemon unavailable"), False),
])
@pytest.mark.asyncio
async def test_docker_stop_requires_confirmed_absence_after_failure(monkeypatch, confirmation, terminated):
    adapter = DockerSandboxAdapter()
    run = AsyncMock(side_effect=[(1, "", "stop failed"), confirmation])
    monkeypatch.setattr(adapter, "_run", run)

    assert await adapter.terminate_session(session_id="review-container") is terminated
    assert run.await_count == 2
    assert "id=review-container" in run.await_args.args[0]


@pytest.mark.asyncio
async def test_failed_stop_keeps_the_session_available_for_cleanup():
    adapter = FakeSandboxAdapter()
    adapter.terminate_session = AsyncMock(return_value=False)
    manager = _manager(adapter)
    state = await _create(manager)

    with pytest.raises(RuntimeError, match="stop"):
        await manager.stop(state.sandbox_id)

    current = await _read(manager, state)
    assert current.sandbox_id == state.sandbox_id
    assert current.session_id == state.session_id
    assert (await _create(manager)).sandbox_id == state.sandbox_id


@pytest.mark.asyncio
async def test_confirmed_provider_absence_allows_failed_session_recreation(monkeypatch):
    docker = DockerSandboxAdapter()
    monkeypatch.setattr(docker, "_run", AsyncMock(side_effect=[(1, "", "already removed"), (0, "", "")]))
    adapter = FakeSandboxAdapter()
    adapter.terminate_session = docker.terminate_session
    manager = _manager(adapter)
    state = await _create(manager)
    token = await manager._store.claim_operation(state.sandbox_id, kind="status", lease_seconds=60)
    await manager._store.save(state.sandbox_id, {"status": "error"}, operation_token=token)
    await manager._store.release_operation(state.sandbox_id, token)

    replacement = await _create(manager)

    assert replacement.sandbox_id != state.sandbox_id
    with pytest.raises(KeyError):
        await _read(manager, state)

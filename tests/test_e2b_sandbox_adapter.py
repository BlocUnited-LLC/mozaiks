from __future__ import annotations

import pytest

from tests.import_utils import import_module_directly

_sandbox_mod = import_module_directly("mozaiksai.core.adapters.e2b_sandbox")

E2BSandboxAdapter = _sandbox_mod.E2BSandboxAdapter


class _FakeFiles:
    def __init__(self) -> None:
        self.writes = []

    def write(self, path, data):  # noqa: ANN001
        self.writes.append((path, data))
        return {"path": path}

    def read(self, path, file_format="text"):  # noqa: ANN001
        if file_format == "bytes":
            return bytearray(b"hello")
        return f"contents:{path}"


class _FakeCommands:
    def run(self, *, cmd, background=None, envs=None, cwd=None, timeout=None):  # noqa: ANN001
        if background:
            class _Handle:
                pid = 321

            return _Handle()

        class _Result:
            exit_code = 0
            stdout = f"ran:{cmd}"
            stderr = ""
            error = None

        return _Result()


class _FakeSandbox:
    def __init__(self, sandbox_id="sbx_123"):
        self.sandbox_id = sandbox_id
        self.sandbox_domain = "sandbox.example"
        self.files = _FakeFiles()
        self.commands = _FakeCommands()
        self.connection_config = type("_Cfg", (), {"debug": False})()
        self.timeout = None
        self.killed = False

    def get_host(self, port):  # noqa: ANN001
        return f"preview-{port}.example"

    def set_timeout(self, timeout):  # noqa: ANN001
        self.timeout = timeout

    def kill(self):
        self.killed = True


@pytest.mark.asyncio
async def test_e2b_adapter_uses_real_sdk_shape(monkeypatch) -> None:
    created = _FakeSandbox()
    connected = _FakeSandbox()

    class _SandboxFactory:
        @staticmethod
        def create(template=None, timeout=None, metadata=None, envs=None):  # noqa: ANN001
            created.template = template
            created.timeout = timeout
            created.metadata = metadata
            created.envs = envs
            return created

        @staticmethod
        def connect(session_id, timeout=None):  # noqa: ANN001
            connected.connected_session_id = session_id
            connected.timeout = timeout
            return connected

    monkeypatch.setattr(_sandbox_mod, "Sandbox", _SandboxFactory)

    adapter = E2BSandboxAdapter(default_template="mozaiks-runtime-v1", default_timeout_seconds=120)

    session = await adapter.create_session(metadata={"app_id": "app-1"}, envs={"A": "1"})
    assert session.session_id == "sbx_123"
    assert session.provider == "e2b"

    write_result = await adapter.write_files(
        session_id="sbx_123",
        files={"src/App.tsx": "console.log('hi')"},
        cwd="/workspace",
    )
    assert write_result["count"] == 1
    assert created.files.writes == [("/workspace/src/App.tsx", "console.log('hi')")]

    read_result = await adapter.read_file(session_id="sbx_123", path="/workspace/src/App.tsx")
    assert read_result == "contents:/workspace/src/App.tsx"

    run_result = await adapter.run_command(
        session_id="sbx_123",
        command="npm test",
        cwd="/workspace",
    )
    assert run_result.success is True
    assert run_result.stdout == "ran:npm test"

    background_result = await adapter.run_command(
        session_id="sbx_123",
        command="npm run dev",
        background=True,
    )
    assert background_result.success is True
    assert background_result.process_id == 321

    preview_url = await adapter.get_preview_url(session_id="sbx_123", port=3000)
    assert preview_url == "https://preview-3000.example"

    extended = await adapter.extend_session(session_id="sbx_123", timeout_seconds=900)
    assert extended.session_id == "sbx_123"
    assert created.timeout == 900

    terminated = await adapter.terminate_session(session_id="sbx_123")
    assert terminated is True
    assert created.killed is True


@pytest.mark.asyncio
async def test_ordinary_io_does_not_renew_the_provider_timeout(monkeypatch):
    from unittest.mock import Mock
    factory = Mock()
    factory.create.return_value = _FakeSandbox()
    monkeypatch.setattr(_sandbox_mod, "Sandbox", factory)
    adapter = E2BSandboxAdapter(default_timeout_seconds=60)
    session = await adapter.create_session()
    for _ in range(3):
        await adapter.run_command(session_id=session.session_id, command="true")
        await adapter.get_preview_url(session_id=session.session_id, port=3000)
    factory.connect.assert_not_called()
    assert factory.create.call_args.kwargs["timeout"] == 60


@pytest.mark.asyncio
async def test_reconnect_preserves_the_remaining_provider_deadline(monkeypatch):
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from unittest.mock import Mock

    factory = Mock()
    factory.get_info.return_value = SimpleNamespace(end_at=datetime.now(UTC) + timedelta(seconds=45))
    factory.connect.return_value = _FakeSandbox()
    monkeypatch.setattr(_sandbox_mod, "Sandbox", factory)
    adapter = E2BSandboxAdapter()
    await adapter.read_file(session_id="sbx_123", path="/tmp/file")
    assert 1 <= factory.connect.call_args.kwargs["timeout"] <= 45
    await adapter.read_file(session_id="sbx_123", path="/tmp/file")
    factory.connect.assert_called_once()


@pytest.mark.asyncio
async def test_cancelled_allocation_waits_for_creation_and_kills_the_sandbox(monkeypatch):
    import asyncio
    import threading
    from unittest.mock import Mock

    started, release = threading.Event(), threading.Event()
    sandbox = _FakeSandbox()

    def create(**kwargs):
        started.set()
        assert release.wait(timeout=5)
        return sandbox

    factory = Mock()
    factory.create.side_effect = create
    monkeypatch.setattr(_sandbox_mod, "Sandbox", factory)
    adapter = E2BSandboxAdapter()
    task = asyncio.create_task(adapter.create_session())
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sandbox.killed
    assert not adapter._sessions

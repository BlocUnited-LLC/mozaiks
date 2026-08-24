"""Artifact preview sessions: manager behavior + mounted Studio API routes.

The preview session manager (`mozaiksai.core.sandbox.preview_sessions`) backs
the AppWorkbench live-preview loop: create/reuse a sandbox per artifact, sync
files, start the dev server, stream status. These tests drive it through a
fake SandboxPort adapter and exercise the HTTP router with auth overridden.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mozaiksai.core.ports.sandbox import SandboxRunResult, SandboxSessionInfo
from mozaiksai.core.sandbox.preview_sessions import (
    ArtifactPreviewSessionManager,
    _safe_relpath,
    is_valid_artifact_id,
    is_valid_sandbox_id,
)


class FakeSandboxAdapter:
    """Minimal SandboxPort implementation recording every call."""

    def __init__(self, *, preview_url: str | None = "https://preview.example") -> None:
        self.calls: list[tuple[str, dict]] = []
        self.preview_url = preview_url
        self.install_result = SandboxRunResult(success=True, exit_code=0)

    async def create_session(self, **kwargs):
        self.calls.append(("create_session", kwargs))
        return SandboxSessionInfo(session_id="sess-1", provider="docker")

    async def connect(self, **kwargs):
        self.calls.append(("connect", kwargs))
        return SandboxSessionInfo(session_id="sess-1", provider="docker")

    async def write_files(self, **kwargs):
        self.calls.append(("write_files", kwargs))
        return {"written": list(kwargs.get("files", {})), "count": len(kwargs.get("files", {}))}

    async def read_file(self, **kwargs):
        self.calls.append(("read_file", kwargs))
        return ""

    async def run_command(self, **kwargs):
        self.calls.append(("run_command", kwargs))
        if kwargs.get("background"):
            return SandboxRunResult(success=True, process_id=42)
        return self.install_result

    async def get_preview_url(self, **kwargs):
        self.calls.append(("get_preview_url", kwargs))
        return self.preview_url

    async def extend_session(self, **kwargs):
        self.calls.append(("extend_session", kwargs))
        return SandboxSessionInfo(session_id="sess-1", provider="docker")

    async def terminate_session(self, **kwargs):
        self.calls.append(("terminate_session", kwargs))
        return True

    def capabilities(self):
        return {"provider": "docker", "supports_preview": True}


def _manager(adapter: FakeSandboxAdapter) -> ArtifactPreviewSessionManager:
    mgr = ArtifactPreviewSessionManager(provider_resolver=lambda: ("docker", adapter))
    mgr._broadcast = AsyncMock()
    return mgr


# ---------------------------------------------------------------------------
# Validators / path safety
# ---------------------------------------------------------------------------


def test_id_validators():
    assert is_valid_artifact_id("app_1-abc")
    assert not is_valid_artifact_id("bad id!")
    assert not is_valid_artifact_id("")
    assert is_valid_sandbox_id("a" * 128)
    assert not is_valid_sandbox_id("a" * 129)


def test_safe_relpath_rejects_traversal_and_absolute():
    assert _safe_relpath("src/App.jsx") == "src/App.jsx"
    assert _safe_relpath("/etc/passwd") == "etc/passwd"
    assert _safe_relpath("../outside") is None
    assert _safe_relpath("a/../../outside") is None
    assert _safe_relpath("") is None


# ---------------------------------------------------------------------------
# Manager lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_then_reuse_same_artifact(monkeypatch):
    monkeypatch.setenv("SANDBOX_TTL_MINUTES", "30")
    adapter = FakeSandboxAdapter()
    mgr = _manager(adapter)

    first = await mgr.create_or_reuse("artifact-a")
    second = await mgr.create_or_reuse("artifact-a")

    assert first.sandbox_id == second.sandbox_id
    creates = [c for c in adapter.calls if c[0] == "create_session"]
    assert len(creates) == 1


@pytest.mark.asyncio
async def test_sync_writes_files_and_removes_deleted(monkeypatch):
    monkeypatch.setenv("SANDBOX_TTL_MINUTES", "30")
    adapter = FakeSandboxAdapter()
    mgr = _manager(adapter)
    st = await mgr.create_or_reuse("artifact-a")

    await mgr.sync(
        st.sandbox_id,
        files=[
            {"path": "package.json", "content": "{}"},
            {"path": "../escape.js", "content": "nope"},
        ],
        deleted=["old.js"],
    )

    writes = [c for c in adapter.calls if c[0] == "write_files"]
    assert len(writes) == 1
    written = writes[0][1]["files"]
    assert "package.json" in written
    assert all("escape" not in p for p in written)
    removes = [c for c in adapter.calls if c[0] == "run_command" and "rm -f" in c[1]["command"]]
    assert len(removes) == 1
    assert st.last_files == {"package.json": "{}"}


@pytest.mark.asyncio
async def test_start_node_runs_install_and_dev_server(monkeypatch):
    monkeypatch.setenv("SANDBOX_TTL_MINUTES", "30")
    adapter = FakeSandboxAdapter()
    mgr = _manager(adapter)
    st = await mgr.create_or_reuse("artifact-a")
    await mgr.sync(
        st.sandbox_id,
        files=[{"path": "package.json", "content": '{"scripts": {"dev": "vite"}}'}],
        deleted=[],
    )

    result = await mgr.start(st.sandbox_id)

    assert result.status == "running"
    assert result.preview_url == "https://preview.example"
    commands = [c[1]["command"] for c in adapter.calls if c[0] == "run_command"]
    assert any("npm install" in c for c in commands)
    assert any("npm run dev" in c for c in commands)


@pytest.mark.asyncio
async def test_start_without_runtime_reports_error(monkeypatch):
    monkeypatch.setenv("SANDBOX_TTL_MINUTES", "30")
    adapter = FakeSandboxAdapter()
    mgr = _manager(adapter)
    st = await mgr.create_or_reuse("artifact-a")

    result = await mgr.start(st.sandbox_id)

    assert result.status == "error"
    assert "No runtime detected" in (result.last_error or "")


@pytest.mark.asyncio
async def test_failed_install_surfaces_stderr(monkeypatch):
    monkeypatch.setenv("SANDBOX_TTL_MINUTES", "30")
    adapter = FakeSandboxAdapter()
    adapter.install_result = SandboxRunResult(
        success=False, exit_code=1, stderr="npm ERR! boom"
    )
    mgr = _manager(adapter)
    st = await mgr.create_or_reuse("artifact-a")
    await mgr.sync(
        st.sandbox_id,
        files=[{"path": "package.json", "content": '{"scripts": {"dev": "vite"}}'}],
        deleted=[],
    )

    result = await mgr.start(st.sandbox_id)

    assert result.status == "error"
    assert "npm ERR! boom" in (result.last_error or "")


@pytest.mark.asyncio
async def test_stop_terminates_provider_session_and_clears_state(monkeypatch):
    monkeypatch.setenv("SANDBOX_TTL_MINUTES", "30")
    adapter = FakeSandboxAdapter()
    mgr = _manager(adapter)
    st = await mgr.create_or_reuse("artifact-a")

    await mgr.stop(st.sandbox_id)

    assert any(c[0] == "terminate_session" for c in adapter.calls)
    with pytest.raises(KeyError):
        await mgr.status(st.sandbox_id)
    # A new create after stop provisions a fresh provider session
    await mgr.create_or_reuse("artifact-a")
    creates = [c for c in adapter.calls if c[0] == "create_session"]
    assert len(creates) == 2


@pytest.mark.asyncio
async def test_no_provider_available_raises_clear_error():
    def _no_provider():
        raise RuntimeError("No preview sandbox provider available.")

    mgr = ArtifactPreviewSessionManager(provider_resolver=_no_provider)
    mgr._broadcast = AsyncMock()
    with pytest.raises(RuntimeError, match="No preview sandbox provider"):
        await mgr.create_or_reuse("artifact-a")


# ---------------------------------------------------------------------------
# Mounted API routes (auth overridden)
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client(monkeypatch):
    import mozaiksai.core.sandbox.preview_sessions as ps
    from mozaiksai.core.auth import require_user_scope
    from mozaiksai.hosts.routers.sandbox import router

    monkeypatch.setenv("SANDBOX_TTL_MINUTES", "30")
    adapter = FakeSandboxAdapter()
    mgr = _manager(adapter)
    monkeypatch.setattr(ps, "_manager", mgr)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_user_scope] = lambda: {"user_id": "tester"}
    with TestClient(app) as client:
        yield client, adapter


def test_router_create_sync_start_status_stop(api_client):
    client, adapter = api_client

    created = client.post("/api/artifacts/artifact-a/sandbox")
    assert created.status_code == 200
    sid = created.json()["sandboxId"]

    synced = client.post(
        f"/api/sandbox/{sid}/sync",
        json={"files": [{"path": "package.json", "content": '{"scripts": {"dev": "vite"}}'}], "deleted": []},
    )
    assert synced.status_code == 200

    started = client.post(f"/api/sandbox/{sid}/start")
    assert started.status_code == 200
    body = started.json()
    assert body["status"] == "running"
    assert body["previewUrl"] == "https://preview.example"

    status = client.get(f"/api/sandbox/{sid}/status")
    assert status.status_code == 200
    assert status.json()["previewUrl"] == "https://preview.example"

    stopped = client.post(f"/api/sandbox/{sid}/stop")
    assert stopped.status_code == 200
    assert client.get(f"/api/sandbox/{sid}/status").status_code == 404


def test_router_rejects_invalid_ids(api_client):
    client, _adapter = api_client
    assert client.post("/api/artifacts/bad%20id!/sandbox").status_code == 400
    assert client.get(f"/api/sandbox/{'a' * 129}/status").status_code == 400


def test_router_unknown_sandbox_404(api_client):
    client, _adapter = api_client
    assert client.get("/api/sandbox/unknown123/status").status_code == 404


def test_router_no_provider_returns_503(monkeypatch):
    import mozaiksai.core.sandbox.preview_sessions as ps
    from mozaiksai.core.auth import require_user_scope
    from mozaiksai.hosts.routers.sandbox import router

    def _no_provider():
        raise RuntimeError("No preview sandbox provider available. Set E2B_API_KEY or Docker.")

    mgr = ArtifactPreviewSessionManager(provider_resolver=_no_provider)
    mgr._broadcast = AsyncMock()
    monkeypatch.setattr(ps, "_manager", mgr)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_user_scope] = lambda: {"user_id": "tester"}
    with TestClient(app) as client:
        response = client.post("/api/artifacts/artifact-a/sandbox")
    assert response.status_code == 503
    assert "provider" in response.json()["detail"]

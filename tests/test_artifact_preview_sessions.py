"""Canonical artifact preview lifecycle and HTTP/WebSocket owner boundaries."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mozaiksai.core.auth import UserPrincipal, require_user_scope
from mozaiksai.core.ports.sandbox import SandboxRunResult, SandboxSessionInfo
from mozaiksai.core.sandbox.preview_sessions import (
    ArtifactPreviewSessionManager,
    _safe_relpath,
    is_valid_artifact_id,
    is_valid_sandbox_id,
)
from mozaiksai.hosts.routers.sandbox import create_sandbox_router

IDENTITY = dict(app_id="factory", user_id="tester", target_app_id="preview-app", build_registry_id="appreg-a")
MANIFEST = '{"appId":"preview-app","appName":"Preview","authRequired":false}'


class FakeSandboxAdapter:
    def __init__(self, *, preview_url="https://preview.example"):
        self.calls = []
        self.preview_url = preview_url
        self.install_result = SandboxRunResult(success=True, exit_code=0)
        self.background_result = SandboxRunResult(success=True, exit_code=0)
        self.command_results = {}

    async def create_session(self, **kwargs):
        self.calls.append(("create_session", kwargs))
        return SandboxSessionInfo(session_id=f"sess-{len(self.calls)}", provider="docker")

    async def write_files(self, **kwargs):
        self.calls.append(("write_files", kwargs))
        return {"written": list(kwargs["files"]), "count": len(kwargs["files"])}

    async def run_command(self, **kwargs):
        self.calls.append(("run_command", kwargs))
        for fragment, result in self.command_results.items():
            if fragment in kwargs["command"]:
                return result
        return self.background_result if kwargs.get("background") else self.install_result

    async def get_preview_url(self, **kwargs):
        self.calls.append(("get_preview_url", kwargs))
        return self.preview_url

    async def terminate_session(self, **kwargs):
        self.calls.append(("terminate_session", kwargs))
        return True


def _manager(adapter):
    manager = ArtifactPreviewSessionManager(provider_resolver=lambda: ("docker", adapter), startup_timeout_seconds=0)
    manager._broadcast = AsyncMock()
    return manager


async def _create(manager, artifact_id="artifact-a", **identity):
    return await manager.create_or_reuse(artifact_id, **{**IDENTITY, **identity})


async def _sync_manifest(manager, state, **files):
    await manager.sync(state.sandbox_id, [{"path": key, "content": value} for key, value in {"app.json": MANIFEST, **files}.items()], [])


def test_id_validators():
    assert is_valid_artifact_id("app_1-abc")
    assert not is_valid_artifact_id("bad id!")
    assert not is_valid_artifact_id("")
    assert is_valid_sandbox_id("a" * 128)
    assert not is_valid_sandbox_id("a" * 129)


@pytest.mark.parametrize("path", ["/etc/passwd", "C:/outside", "../outside", "a/../../outside", "", ".", "a\x00b"])
def test_safe_relpath_rejects_unsafe_paths(path):
    assert _safe_relpath(path) is None


def test_safe_relpath_normalizes_relative_separator():
    assert _safe_relpath("src\\App.jsx") == "src/App.jsx"


@pytest.mark.asyncio
async def test_reuse_is_per_owner_host_and_artifact():
    adapter = FakeSandboxAdapter()
    manager = _manager(adapter)
    first = await _create(manager)
    assert (await _create(manager)).sandbox_id == first.sandbox_id
    assert (await _create(manager, user_id="someone-else")).sandbox_id != first.sandbox_id
    assert (await _create(manager, app_id="another-host")).sandbox_id != first.sandbox_id
    assert (await _create(manager, "artifact-b")).sandbox_id != first.sandbox_id
    assert len([call for call in adapter.calls if call[0] == "create_session"]) == 4


@pytest.mark.asyncio
async def test_only_explicit_preview_environment_is_forwarded(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "host-secret")
    monkeypatch.setenv("MONGO_URI", "host-database")
    monkeypatch.setenv("MOZAIKS_PREVIEW_ENV_VITE_OIDC_AUTHORITY", "http://local-idp")
    adapter = FakeSandboxAdapter()
    await _create(_manager(adapter))
    env = adapter.calls[0][1]["envs"]
    assert env["VITE_OIDC_AUTHORITY"] == "http://local-idp"
    assert "OPENAI_API_KEY" not in env
    assert "MONGO_URI" not in env


@pytest.mark.asyncio
async def test_sync_rejects_invalid_paths_before_any_write():
    adapter = FakeSandboxAdapter()
    manager = _manager(adapter)
    state = await _create(manager)
    with pytest.raises(ValueError, match="Invalid preview"):
        await _sync_manifest(manager, state, **{"../escape.js": "bad"})
    assert not any(call[0] == "write_files" for call in adapter.calls)


@pytest.mark.asyncio
async def test_sync_checks_build_target_before_any_write():
    adapter = FakeSandboxAdapter()
    manager = _manager(adapter)
    state = await _create(manager)
    with pytest.raises(ValueError, match="appId"):
        await _sync_manifest(manager, state, **{"app.json": '{"appId":"foreign-app"}'})
    assert not any(call[0] == "write_files" for call in adapter.calls)


@pytest.mark.asyncio
async def test_sync_uses_the_same_workspace_layout_as_promotion():
    adapter = FakeSandboxAdapter()
    manager = _manager(adapter)
    state = await _create(manager)
    await _sync_manifest(manager, state, **{
        "workflows/Inbox/orchestrator.yaml": "name: Inbox", "requirements.txt": "httpx",
        "brand/icon.png": b"\x89PNG\x00",
    })
    written = next(data for kind, data in adapter.calls if kind == "write_files")
    assert written["cwd"] == "/workspace"
    assert written["files"]["app/app.json"] == MANIFEST
    assert written["files"]["app/brand/icon.png"] == b"\x89PNG\x00"
    assert written["files"]["workflows/Inbox/orchestrator.yaml"] == "name: Inbox"
    assert written["files"]["requirements.txt"] == "httpx"


@pytest.mark.asyncio
async def test_sync_quotes_deleted_paths_and_updates_snapshot():
    adapter = FakeSandboxAdapter()
    manager = _manager(adapter)
    state = await _create(manager)
    await _sync_manifest(manager, state, **{"$(touch stolen).js": "old"})
    await manager.sync(state.sandbox_id, [], ["$(touch stolen).js"])
    commands = [data["command"] for kind, data in adapter.calls if kind == "run_command"]
    assert commands == ["rm -f -- 'app/$(touch stolen).js'"]
    assert state.last_files == {"app.json": MANIFEST}


@pytest.mark.asyncio
async def test_sync_failure_clears_live_url_and_recreates_session():
    adapter = FakeSandboxAdapter()
    manager = _manager(adapter)
    state = await _create(manager)
    await _sync_manifest(manager, state)
    await manager.start(state.sandbox_id)
    adapter.write_files = AsyncMock(side_effect=RuntimeError("disk error"))
    with pytest.raises(RuntimeError):
        await _sync_manifest(manager, state)
    assert state.status == "error" and state.preview_url is None
    assert (await _create(manager)).sandbox_id != state.sandbox_id


@pytest.mark.asyncio
async def test_start_uses_canonical_runtime_and_real_health_check():
    adapter = FakeSandboxAdapter()
    manager = _manager(adapter)
    state = await _create(manager)
    await _sync_manifest(manager, state, **{"requirements.txt": "httpx"})
    result = await manager.start(state.sandbox_id)
    assert result.status == "running"
    assert result.preview_url == "https://preview.example"
    commands = [data["command"] for kind, data in adapter.calls if kind == "run_command"]
    assert commands[0].endswith("preview_runtime stop")
    assert "preview-constraints.txt" in commands[1]
    assert "preview_runtime start --app-root /workspace/app" in commands[2]
    assert commands[3].endswith("preview_runtime check --port 3000 --app-root /workspace/app")


@pytest.mark.parametrize("fragment,message", [("pip install", "dependency"), ("preview_runtime start", "process"), ("preview_runtime check", "healthy")])
@pytest.mark.asyncio
async def test_failed_runtime_stage_never_reports_a_preview(fragment, message):
    adapter = FakeSandboxAdapter()
    adapter.command_results[fragment] = SandboxRunResult(success=False, exit_code=1, stderr="secret-must-not-be-returned")
    manager = _manager(adapter)
    state = await _create(manager)
    await _sync_manifest(manager, state, **{"requirements.txt": "httpx"})
    result = await manager.start(state.sandbox_id)
    assert result.status == "error" and result.preview_url is None
    assert message in result.last_error
    assert "secret" not in result.last_error


@pytest.mark.asyncio
async def test_missing_manifest_fails_start():
    manager = _manager(FakeSandboxAdapter())
    state = await _create(manager)
    assert (await manager.start(state.sandbox_id)).status == "error"
    assert "app.json" in state.last_error


@pytest.mark.asyncio
async def test_dead_runtime_clears_an_existing_url():
    adapter = FakeSandboxAdapter()
    manager = _manager(adapter)
    state = await _create(manager)
    await _sync_manifest(manager, state)
    await manager.start(state.sandbox_id)
    adapter.install_result = SandboxRunResult(success=False, exit_code=1)
    assert (await manager.status(state.sandbox_id)).status == "error"
    assert state.preview_url is None


@pytest.mark.asyncio
async def test_expiry_is_absolute_even_after_status_polling():
    manager = _manager(FakeSandboxAdapter())
    state = await _create(manager)
    await manager.status(state.sandbox_id)
    state.created_at -= timedelta(minutes=31)
    with pytest.raises(KeyError, match="expired"):
        await manager.status(state.sandbox_id)
    assert state.sandbox_id not in manager._sessions


@pytest.mark.asyncio
async def test_unknown_owner_cannot_even_expire_someone_elses_container():
    adapter = FakeSandboxAdapter()
    manager = _manager(adapter)
    state = await _create(manager)
    state.created_at -= timedelta(minutes=31)
    with pytest.raises(KeyError):
        await manager.require_owner(state.sandbox_id, app_id="factory", user_id="outsider")
    assert not any(kind == "terminate_session" for kind, _ in adapter.calls)


@pytest.mark.asyncio
async def test_failed_create_does_not_poison_retry():
    adapter = FakeSandboxAdapter()
    original = adapter.create_session
    adapter.create_session = AsyncMock(side_effect=RuntimeError("unavailable"))
    manager = _manager(adapter)
    with pytest.raises(RuntimeError):
        await _create(manager)
    assert not manager._sessions and not manager._artifact_to_sandbox
    adapter.create_session = original
    assert (await _create(manager)).session_id


@pytest.mark.asyncio
async def test_stop_cleans_up_even_when_websocket_is_already_closed():
    adapter = FakeSandboxAdapter()
    manager = _manager(adapter)
    state = await _create(manager)
    socket = AsyncMock()
    socket.close.side_effect = RuntimeError("closed")
    manager._ws_clients[state.sandbox_id] = {socket}
    await manager.stop(state.sandbox_id)
    assert not manager._sessions and not manager._artifact_to_sandbox
    assert any(kind == "terminate_session" for kind, _ in adapter.calls)


@pytest.fixture
def api_client(monkeypatch):
    import mozaiksai.core.sandbox.preview_sessions as sessions
    from mozaiksai.core.auth.websocket_auth import WebSocketUser

    adapter = FakeSandboxAdapter()
    manager = _manager(adapter)
    monkeypatch.setattr(sessions, "_manager", manager)
    principal = UserPrincipal(user_id="tester", app_id="factory", email=None, name=None, roles=[], scopes=[], raw_claims={})

    async def resolve_artifact(user, artifact_id, registry_id):
        if (user.app_id, user.user_id, artifact_id, registry_id) != ("factory", "tester", "artifact-a", "appreg-a"):
            raise HTTPException(status_code=404, detail="Artifact not found")
        return "preview-app", {"app.json": MANIFEST}

    async def websocket_auth(_socket):
        return WebSocketUser(user_id=principal.user_id, app_id=principal.app_id, email=None, name=None, roles=[], scopes=[], raw_claims={}, provider="test")

    monkeypatch.setattr("mozaiksai.hosts.routers.sandbox.authenticate_websocket", websocket_auth)
    app = FastAPI()
    app.include_router(create_sandbox_router(resolve_scope=lambda user: (user.app_id, user.user_id), resolve_artifact=resolve_artifact))
    app.dependency_overrides[require_user_scope] = lambda: principal
    with TestClient(app) as client:
        yield client, adapter, principal


CREATE_URL = "/api/artifacts/artifact-a/sandbox?build_registry_id=appreg-a"


def test_router_create_sync_start_status_stop(api_client):
    client, adapter, _ = api_client
    created = client.post(CREATE_URL)
    assert created.status_code == 200, created.text
    sid = created.json()["sandboxId"]
    assert any(kind == "write_files" for kind, _ in adapter.calls)
    assert client.post(f"/api/sandbox/{sid}/sync", json={"files": [], "deleted": []}).status_code == 200
    assert client.post(f"/api/sandbox/{sid}/start").json()["status"] == "running"
    assert client.get(f"/api/sandbox/{sid}/status").json()["previewUrl"] == "https://preview.example"
    assert client.post(f"/api/sandbox/{sid}/stop").status_code == 200
    assert client.get(f"/api/sandbox/{sid}/status").status_code == 404


def test_router_requires_persisted_owned_artifact_before_allocating(api_client):
    client, adapter, _ = api_client
    assert client.post("/api/artifacts/artifact-a/sandbox").status_code == 422
    assert client.post("/api/artifacts/invented/sandbox?build_registry_id=appreg-a").status_code == 404
    assert client.post("/api/artifacts/artifact-a/sandbox?build_registry_id=foreign").status_code == 404
    assert not adapter.calls


@pytest.mark.parametrize("field,value", [("user_id", "outsider"), ("app_id", "foreign-host")])
def test_all_http_operations_enforce_owner(api_client, field, value):
    client, adapter, principal = api_client
    sid = client.post(CREATE_URL).json()["sandboxId"]
    setattr(principal, field, value)
    count = len(adapter.calls)
    for path in ("start", "stop", "sync"):
        assert client.post(f"/api/sandbox/{sid}/{path}", json={"files": [], "deleted": []}).status_code == 404
    assert client.get(f"/api/sandbox/{sid}/status").status_code == 404
    assert len(adapter.calls) == count


def test_websocket_owner_receives_status_foreign_owner_is_rejected(api_client):
    client, _, principal = api_client
    sid = client.post(CREATE_URL).json()["sandboxId"]
    with client.websocket_connect(f"/ws/sandbox/{sid}") as socket:
        assert socket.receive_json()["status"] == "starting"
    principal.user_id = "outsider"
    with pytest.raises(WebSocketDisconnect) as raised, client.websocket_connect(f"/ws/sandbox/{sid}"):
        pass
    assert raised.value.code == 1008


def test_router_invalid_paths_and_identifiers(api_client):
    client, _, _ = api_client
    assert client.post("/api/artifacts/bad%20id!/sandbox?build_registry_id=appreg-a").status_code == 400
    assert client.get(f"/api/sandbox/{'a' * 129}/status").status_code == 400
    sid = client.post(CREATE_URL).json()["sandboxId"]
    assert client.post(f"/api/sandbox/{sid}/sync", json={"deleted": ["../outside"]}).status_code == 422


def test_provider_failure_is_503_without_raw_error(api_client):
    client, adapter, _ = api_client
    adapter.create_session = AsyncMock(side_effect=RuntimeError("secret-value"))
    response = client.post(CREATE_URL)
    assert response.status_code == 503
    assert "secret-value" not in response.text

"""Canonical artifact preview lifecycle and HTTP/WebSocket owner boundaries."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mozaiksai.core.auth import UserPrincipal, require_user_scope
from mozaiksai.core.ports.sandbox import SandboxRunResult, SandboxSessionInfo
from mozaiksai.core.sandbox import preview_sessions
from mozaiksai.core.sandbox.preview_sessions import (
    ArtifactPreviewSessionManager,
    PreviewCapacityError,
    _safe_relpath,
    is_valid_artifact_id,
    is_valid_sandbox_id,
    preview_sessions_lifespan,
    resolve_preview_provider,
)
from mozaiksai.core.sandbox.preview_store import (
    MongoPreviewStore,
    PreviewLeaseLostError,
    PreviewOperationBusy,
    PreviewRecoveryRequired,
)
from mozaiksai.hosts.routers.sandbox import create_sandbox_router
from tests.helpers.preview_mongo import FakePreviewDatabase

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


def _store(db=None):
    return MongoPreviewStore(db if db is not None else FakePreviewDatabase(), now=lambda: preview_sessions._utcnow())


def _manager(adapter, *, store=None, provider="docker"):
    manager = ArtifactPreviewSessionManager(
        provider_resolver=lambda: (provider, adapter), startup_timeout_seconds=0,
        store=store if store is not None else _store(),
    )
    manager._queue_seconds = 0.05
    manager._poll_seconds = 0.001
    manager._broadcast = AsyncMock()
    return manager


async def _create(manager, artifact_id="artifact-a", **identity):
    return await manager.create_or_reuse(artifact_id, **{**IDENTITY, **identity})


async def _sync_manifest(manager, state, **files):
    await manager.sync(state.sandbox_id, [{"path": key, "content": value} for key, value in {"app.json": MANIFEST, **files}.items()], [])


async def _read(manager, state):
    return await manager.require_owner(state.sandbox_id, app_id=state.app_id, user_id=state.user_id)


def _metadata(state):
    return state.manifest, sorted(state.paths), state.has_requirements


def test_id_validators():
    assert is_valid_artifact_id("app_1-abc")
    assert not is_valid_artifact_id("bad id!")
    assert not is_valid_artifact_id("")
    assert is_valid_sandbox_id("a" * 128)
    assert not is_valid_sandbox_id("a" * 129)


def test_preview_provider_defaults_to_docker_even_when_e2b_key_exists(monkeypatch):
    import mozaiksai.core.adapters.docker_sandbox as docker_sandbox

    adapter = object()
    monkeypatch.setattr(docker_sandbox, "docker_available", lambda: True)
    monkeypatch.setattr(docker_sandbox, "get_docker_sandbox", lambda: adapter)

    provider, resolved = resolve_preview_provider({"E2B_API_KEY": "configured"})

    assert provider == "docker"
    assert resolved is adapter


def test_preview_provider_requires_explicit_e2b_selection(monkeypatch):
    import mozaiksai.core.adapters.e2b_sandbox as e2b_sandbox

    adapter = object()
    monkeypatch.setattr(e2b_sandbox, "get_e2b_sandbox", lambda: adapter)

    provider, resolved = resolve_preview_provider(
        {"E2B_API_KEY": "configured", "MOZAIKS_PREVIEW_PROVIDER": "e2b"}
    )

    assert provider == "e2b"
    assert resolved is adapter


def test_explicit_e2b_selection_requires_api_key():
    with pytest.raises(RuntimeError, match="E2B_API_KEY"):
        resolve_preview_provider({"MOZAIKS_PREVIEW_PROVIDER": "e2b"})


def test_preview_provider_rejects_unknown_explicit_value():
    with pytest.raises(ValueError, match="Unsupported preview provider"):
        resolve_preview_provider({"MOZAIKS_PREVIEW_PROVIDER": "modal"})


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
async def test_two_workers_reuse_the_same_durable_preview():
    adapter = FakeSandboxAdapter()
    database = FakePreviewDatabase()
    first = _manager(adapter, store=_store(database))
    second = _manager(adapter, store=_store(database))

    states = await asyncio.gather(_create(first), _create(second))

    assert states[0].sandbox_id == states[1].sandbox_id
    assert states[0].session_id == states[1].session_id
    assert len([call for call in adapter.calls if call[0] == "create_session"]) == 1
    await _sync_manifest(first, states[0])
    running = await second.start(states[1].sandbox_id)
    assert running.status == "running"
    assert (await first.status(running.sandbox_id)).preview_url == running.preview_url


@pytest.mark.asyncio
async def test_workers_share_one_health_check_per_interval(monkeypatch):
    adapter = FakeSandboxAdapter()
    database = FakePreviewDatabase()
    workers = [_manager(adapter, store=_store(database)) for _ in range(4)]
    state = await _create(workers[0])
    await _sync_manifest(workers[0], state)
    await workers[0].start(state.sandbox_id)
    before = len(adapter.calls)
    await asyncio.gather(*(worker.status(state.sandbox_id) for worker in workers))
    assert len(adapter.calls) == before

    later = preview_sessions._utcnow() + timedelta(seconds=11)
    monkeypatch.setattr(preview_sessions, "_utcnow", lambda: later)
    results = await asyncio.gather(*(worker.status(state.sandbox_id) for worker in workers))

    assert all(result.status == "running" for result in results)
    checks = [kwargs for kind, kwargs in adapter.calls[before:] if kind == "run_command"]
    assert len(checks) == 1
    assert "preview_runtime check" in checks[0]["command"]


@pytest.mark.asyncio
async def test_websocket_observes_other_worker_status_and_stop_without_provider_polling():
    adapter = FakeSandboxAdapter()
    database = FakePreviewDatabase()
    observer = _manager(adapter, store=_store(database))
    worker = _manager(adapter, store=_store(database))
    # Use the production broadcaster for the worker holding the connection.
    del observer._broadcast
    state = await _create(worker)
    socket = AsyncMock()
    await observer.register_ws(state.sandbox_id, socket)
    assert socket.send_json.await_args.args[0]["status"] == "starting"
    await _sync_manifest(worker, state)
    running = await worker.start(state.sandbox_id)
    before = list(adapter.calls)

    await observer.refresh_websockets()

    assert socket.send_json.await_args.args[0] == {
        "type": "status", "status": "running", "previewUrl": running.preview_url, "lastError": None,
    }
    assert adapter.calls == before
    await worker.stop(state.sandbox_id)
    before = list(adapter.calls)
    await observer.refresh_websockets()
    assert socket.send_json.await_args.args[0]["status"] == "error"
    assert socket.send_json.await_args.args[0]["previewUrl"] is None
    socket.close.assert_awaited_once()
    assert state.sandbox_id not in observer._ws_clients
    assert adapter.calls == before


@pytest.mark.asyncio
async def test_returned_snapshots_do_not_mutate_or_override_persisted_metadata():
    adapter = FakeSandboxAdapter()
    manager = _manager(adapter)
    initial = await _create(manager)
    await _sync_manifest(manager, initial, **{"brand/logo.png": b"\x89PNG\x00"})
    running = await manager.start(initial.sandbox_id)

    assert initial.status == "starting" and initial.preview_url is None
    assert _metadata(initial) == (None, [], False)
    assert running.status == "running"
    assert _metadata(running) == (MANIFEST, ["app.json", "brand/logo.png"], False)
    stored = await manager._store.get(initial.sandbox_id)
    assert "last_files" not in stored
    running.paths.append("unpersisted.txt")
    running.status = "error"
    current = await _read(manager, initial)
    assert current.status == "running"
    assert "unpersisted.txt" not in current.paths


@pytest.mark.asyncio
async def test_only_explicit_preview_environment_is_forwarded(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "host-secret")
    monkeypatch.setenv("MONGO_URI", "host-database")
    monkeypatch.setenv("MOZAIKS_PREVIEW_ENV_VITE_OIDC_AUTHORITY", "http://local-idp")
    monkeypatch.setenv("MOZAIKS_PREVIEW_ENV_VITE_MOZAIKS_PREVIEW", "false")
    adapter = FakeSandboxAdapter()
    await _create(_manager(adapter))
    env = adapter.calls[0][1]["envs"]
    assert env["MOZAIKS_WEB_SHELL_PATH"] == "/opt/mozaiks/web_shell"
    assert env["MOZAIKS_CHAT_UI_PATH"] == "/opt/mozaiks/chat-ui"
    assert env["MOZAIKS_FACTORY_APP_PATH"] == "/opt/mozaiks/factory_app"
    assert env["VITE_OIDC_AUTHORITY"] == "http://local-idp"
    assert env["VITE_MOZAIKS_PREVIEW"] == "true"
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
    assert _metadata(await _read(manager, state)) == (MANIFEST, ["app.json"], False)


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
    current = await _read(manager, state)
    assert current.status == "error" and current.preview_url is None
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
    launch = next(data for kind, data in adapter.calls if kind == "run_command" and "preview_runtime start" in data["command"])
    assert launch["envs"] == {"__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS": "preview.example"}
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
    result = await manager.start(state.sandbox_id)
    assert result.status == "error"
    assert "app.json" in result.last_error


@pytest.mark.asyncio
async def test_dead_runtime_clears_an_existing_url(monkeypatch):
    adapter = FakeSandboxAdapter()
    manager = _manager(adapter)
    state = await _create(manager)
    await _sync_manifest(manager, state)
    await manager.start(state.sandbox_id)
    adapter.install_result = SandboxRunResult(success=False, exit_code=1)
    later = preview_sessions._utcnow() + timedelta(seconds=11)
    monkeypatch.setattr(preview_sessions, "_utcnow", lambda: later)
    current = await manager.status(state.sandbox_id)
    assert current.status == "error"
    assert current.preview_url is None


@pytest.mark.asyncio
async def test_expiry_is_absolute_even_after_status_polling(monkeypatch):
    manager = _manager(FakeSandboxAdapter())
    state = await _create(manager)
    before_deadline = state.expires_at - timedelta(seconds=1)
    monkeypatch.setattr(preview_sessions, "_utcnow", lambda: before_deadline)
    current = await manager.status(state.sandbox_id)
    assert current.expires_at == state.expires_at
    after_deadline = state.expires_at + timedelta(seconds=1)
    monkeypatch.setattr(preview_sessions, "_utcnow", lambda: after_deadline)
    with pytest.raises(KeyError, match="expired"):
        await manager.status(state.sandbox_id)
    assert await manager._store.get(state.sandbox_id) is None


@pytest.mark.asyncio
async def test_unknown_owner_cannot_even_expire_someone_elses_container(monkeypatch):
    adapter = FakeSandboxAdapter()
    manager = _manager(adapter)
    state = await _create(manager)
    after_deadline = state.expires_at + timedelta(seconds=1)
    monkeypatch.setattr(preview_sessions, "_utcnow", lambda: after_deadline)
    with pytest.raises(KeyError):
        await manager.require_owner(state.sandbox_id, app_id="factory", user_id="outsider")
    assert not any(kind == "terminate_session" for kind, _ in adapter.calls)
    assert await manager._store.get(state.sandbox_id) is not None


@pytest.mark.asyncio
async def test_unknown_create_outcome_holds_capacity_until_provider_deadline(monkeypatch):
    monkeypatch.setenv("SANDBOX_MAX_SESSIONS", "1")
    adapter = FakeSandboxAdapter()
    original = adapter.create_session
    adapter.create_session = AsyncMock(side_effect=RuntimeError("unavailable"))
    manager = _manager(adapter)
    with pytest.raises(RuntimeError):
        await _create(manager)
    with pytest.raises(PreviewCapacityError):
        await _create(manager, user_id="other-owner")
    pending = await manager._store.list()
    assert len(pending) == 1
    assert pending[0]["phase"] == "provisioning"
    assert pending[0]["session_id"] is None
    with pytest.raises(RuntimeError):
        await _create(manager)
    adapter.create_session.assert_awaited_once()
    adapter.create_session = original
    after_deadline = pending[0]["expires_at"] + timedelta(seconds=1)
    monkeypatch.setattr(preview_sessions, "_utcnow", lambda: after_deadline)
    assert (await _create(manager)).session_id
    assert await manager._store.get(pending[0]["sandbox_id"]) is None


@pytest.mark.asyncio
async def test_stop_cleans_up_even_when_websocket_is_already_closed():
    adapter = FakeSandboxAdapter()
    manager = _manager(adapter)
    state = await _create(manager)
    socket = AsyncMock()
    socket.close.side_effect = RuntimeError("closed")
    manager._ws_clients[state.sandbox_id] = {socket}
    await manager.stop(state.sandbox_id)
    with pytest.raises(KeyError):
        await _read(manager, state)
    assert state.sandbox_id not in manager._ws_clients
    assert any(kind == "terminate_session" for kind, _ in adapter.calls)


@pytest.mark.asyncio
async def test_owner_and_host_limits_allow_reuse_and_release_capacity(monkeypatch):
    monkeypatch.setenv("SANDBOX_MAX_SESSIONS", "2")
    monkeypatch.setenv("SANDBOX_MAX_OWNER_SESSIONS", "1")
    adapter = FakeSandboxAdapter()
    manager = _manager(adapter)
    state = await _create(manager)
    assert (await _create(manager)).sandbox_id == state.sandbox_id
    before = len(adapter.calls)
    with pytest.raises(PreviewCapacityError):
        await _create(manager, "other-artifact")
    assert len(adapter.calls) == before
    second = await _create(manager, user_id="second")
    with pytest.raises(PreviewCapacityError):
        await _create(manager, user_id="third")
    await manager.stop(second.sandbox_id)
    assert (await _create(manager, user_id="third")).session_id


@pytest.mark.asyncio
async def test_concurrent_creations_never_exceed_configured_capacity(monkeypatch):
    monkeypatch.setenv("SANDBOX_MAX_SESSIONS", "1")
    adapter = FakeSandboxAdapter()
    database = FakePreviewDatabase()
    workers = [_manager(adapter, store=_store(database)) for _ in range(4)]
    results = await asyncio.gather(*(_create(manager, user_id=str(i)) for i, manager in enumerate(workers)), return_exceptions=True)
    assert sum(isinstance(item, PreviewCapacityError) for item in results) == 3
    assert len([call for call in adapter.calls if call[0] == "create_session"]) == 1


@pytest.mark.asyncio
async def test_shared_capacity_waits_for_another_worker_to_release_a_session(monkeypatch):
    monkeypatch.setenv("SANDBOX_MAX_SESSIONS", "1")
    adapter = FakeSandboxAdapter()
    database = FakePreviewDatabase()
    first = _manager(adapter, store=_store(database))
    second = _manager(adapter, store=_store(database))
    second._queue_seconds = 1
    active = await _create(first)
    waiting = asyncio.create_task(_create(second, user_id="another-user"))
    try:
        async with asyncio.timeout(1):
            while len(await first._store.list()) < 2:
                await asyncio.sleep(0)
        assert not waiting.done()
        assert len([call for call in adapter.calls if call[0] == "create_session"]) == 1
        await first.stop(active.sandbox_id)
        admitted = await waiting
        assert admitted.session_id and admitted.sandbox_id != active.sandbox_id
        assert len([call for call in adapter.calls if call[0] == "create_session"]) == 2
    finally:
        if not waiting.done():
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting


@pytest.mark.asyncio
async def test_cleanup_expires_previews_but_shutdown_preserves_live_session(monkeypatch):
    adapter = FakeSandboxAdapter()
    store = _store()
    manager = _manager(adapter, store=store)
    monkeypatch.setattr(preview_sessions, "_manager", manager)
    async with preview_sessions_lifespan(None):
        expired = await _create(manager)
        after_deadline = expired.expires_at + timedelta(seconds=1)
        monkeypatch.setattr(preview_sessions, "_utcnow", lambda: after_deadline)
        await manager.cleanup()
        assert await store.get(expired.sandbox_id) is None
        live = await _create(manager)
        await _sync_manifest(manager, live)
        await manager.start(live.sandbox_id)
    restarted = _manager(adapter, store=_store(store._db))
    assert (await _create(restarted)).sandbox_id == live.sandbox_id
    assert (await restarted.status(live.sandbox_id)).status == "running"
    terminated = [kwargs["session_id"] for kind, kwargs in adapter.calls if kind == "terminate_session"]
    assert terminated == [expired.session_id]


@pytest.mark.asyncio
async def test_failure_terminates_sandbox_without_hiding_cleanup_outages():
    adapter = FakeSandboxAdapter()
    manager = _manager(adapter)
    state = await _create(manager)
    result = await manager.start(state.sandbox_id)
    assert result.session_id is None
    assert any(kind == "terminate_session" for kind, _ in adapter.calls)
    other = await _create(manager)
    adapter.terminate_session = AsyncMock(side_effect=ConnectionError())
    result = await manager.start(other.sandbox_id)
    assert result.session_id is not None
    assert (await _read(manager, other)).session_id == other.session_id


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


def test_capacity_exhaustion_returns_429_without_allocating(api_client):
    import mozaiksai.core.sandbox.preview_sessions as sessions

    client, adapter, _ = api_client
    sessions._manager.create_or_reuse = AsyncMock(side_effect=PreviewCapacityError("Preview capacity reached"))
    response = client.post(CREATE_URL)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "15"
    assert not adapter.calls


@pytest.mark.parametrize("operation", ["create", "sync", "start", "status", "stop"])
@pytest.mark.parametrize("failure", [PreviewOperationBusy, PreviewLeaseLostError, PreviewRecoveryRequired])
def test_coordination_conflicts_are_retryable_http_409(api_client, monkeypatch, operation, failure):
    client, adapter, _ = api_client
    sid = client.post(CREATE_URL).json()["sandboxId"]
    before = list(adapter.calls)
    name = "create_or_reuse" if operation == "create" else operation
    monkeypatch.setattr(preview_sessions._manager, name, AsyncMock(side_effect=failure("Preview operation unavailable")))

    if operation == "create":
        response = client.post(CREATE_URL)
    elif operation == "status":
        response = client.get(f"/api/sandbox/{sid}/status")
    else:
        response = client.post(f"/api/sandbox/{sid}/{operation}", json={"files": [], "deleted": []})

    assert response.status_code == 409, response.text
    assert response.headers["Retry-After"] == "2"
    assert adapter.calls == before


@pytest.mark.parametrize("operation", ["create", "owner", "sync", "start", "status", "stop"])
def test_database_failures_are_sanitized_http_503(api_client, monkeypatch, operation):
    from pymongo.errors import ServerSelectionTimeoutError

    client, adapter, _ = api_client
    sid = client.post(CREATE_URL).json()["sandboxId"]
    before = list(adapter.calls)
    name = {"create": "create_or_reuse", "owner": "require_owner"}.get(operation, operation)
    monkeypatch.setattr(preview_sessions._manager, name, AsyncMock(
        side_effect=ServerSelectionTimeoutError("database-credential-must-stay-private"),
    ))

    if operation == "create":
        response = client.post(CREATE_URL)
    elif operation in {"owner", "status"}:
        response = client.get(f"/api/sandbox/{sid}/status")
    else:
        response = client.post(f"/api/sandbox/{sid}/{operation}", json={"files": [], "deleted": []})

    assert response.status_code == 503, response.text
    assert "credential" not in response.text
    assert adapter.calls == before

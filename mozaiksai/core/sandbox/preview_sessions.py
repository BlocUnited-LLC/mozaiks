"""Owner-bound, disposable canonical-app previews over SandboxPort."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from logs.logging_config import get_core_logger
from mozaiksai.core.ports.sandbox import SandboxPort
from mozaiksai.core.runtime.app.paths import app_bundle_workspace_path
from mozaiksai.core.sandbox.preview_store import (
    MongoPreviewStore,
    PreviewCapacityError,
    PreviewLeaseLostError,
    PreviewOperationBusy,
)

logger = get_core_logger("artifact_preview_sessions")
_ARTIFACT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
_SANDBOX_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
_RUNTIME = "python -m mozaiksai.core.sandbox.preview_runtime"
_PREVIEW_PORT = 3000
_ENV_PREFIX = "MOZAIKS_PREVIEW_ENV_"


def is_valid_artifact_id(value: str) -> bool:
    return isinstance(value, str) and bool(_ARTIFACT_ID_RE.fullmatch(value))


def is_valid_sandbox_id(value: str) -> bool:
    return isinstance(value, str) and bool(_SANDBOX_ID_RE.fullmatch(value))


def _utcnow() -> datetime:
    return datetime.now(UTC)


def sandbox_workspace_root(provider: str) -> str:
    root = "/workspace" if provider == "docker" else (os.getenv("SANDBOX_WORKDIR") or "/home/user/app")
    path = PurePosixPath(root)
    if not path.is_absolute() or str(path) == "/" or ".." in path.parts or "\x00" in root:
        raise ValueError("SANDBOX_WORKDIR must be an absolute sandbox path")
    return str(path).rstrip("/")


def sandbox_resource_environment() -> dict[str, str]:
    # Dockerfile ENV is build-time only in E2B; credentials require explicit opt-in.
    return {
        "MOZAIKS_WEB_SHELL_PATH": "/opt/mozaiks/web_shell",
        "MOZAIKS_CHAT_UI_PATH": "/opt/mozaiks/chat-ui",
        "MOZAIKS_FACTORY_APP_PATH": "/opt/mozaiks/factory_app",
        **{name[len(_ENV_PREFIX):]: value for name, value in os.environ.items() if name.startswith(_ENV_PREFIX)},
        "VITE_MOZAIKS_PREVIEW": "true",
    }


def _safe_relpath(raw: str) -> str | None:
    value = str(raw or "").replace("\\", "/")
    path = PurePosixPath(value)
    if not value or value != value.strip() or value.startswith("/") or ":" in value or "\x00" in value:
        return None
    if ".." in path.parts or str(path) == ".":
        return None
    return str(path)


def resolve_preview_provider(env: dict[str, str] | None = None) -> tuple[str, SandboxPort]:
    env_map = os.environ if env is None else env
    requested = str(env_map.get("MOZAIKS_PREVIEW_PROVIDER", "")).strip().lower()
    if requested == "e2b":
        if not str(env_map.get("E2B_API_KEY", "")).strip():
            raise RuntimeError(
                "E2B_API_KEY is required when MOZAIKS_PREVIEW_PROVIDER=e2b"
            )
        from mozaiksai.core.adapters.e2b_sandbox import get_e2b_sandbox

        return "e2b", get_e2b_sandbox()
    if requested not in {"", "docker"}:
        raise ValueError(
            f"Unsupported preview provider {requested!r}; expected 'docker' or 'e2b'"
        )
    from mozaiksai.core.adapters.docker_sandbox import docker_available, get_docker_sandbox

    if docker_available():
        return "docker", get_docker_sandbox()
    raise RuntimeError("No preview sandbox available. Start the configured local Docker sandbox provider.")


@dataclass
class PreviewSessionState:
    sandbox_id: str
    artifact_id: str
    app_id: str
    user_id: str
    target_app_id: str
    build_registry_id: str
    provider: str
    created_at: datetime
    expires_at: datetime
    phase: str = "active"
    session_id: str | None = None
    status: str = "starting"
    preview_url: str | None = None
    last_error: str | None = None
    last_access_at: datetime = field(default_factory=_utcnow)
    manifest: str | None = None
    paths: list[str] = field(default_factory=list)
    has_requirements: bool = False
    health_checked_at: datetime | None = None

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> PreviewSessionState:
        return cls(**{name: record[name] for name in cls.__dataclass_fields__ if name in record})

    def payload(self) -> dict[str, Any]:
        snapshot = asdict(self)
        return {name: snapshot[name] for name in (
            "session_id", "status", "preview_url", "last_error", "last_access_at",
            "manifest", "paths", "has_requirements", "health_checked_at",
        )}


class ArtifactPreviewSessionManager:
    """Studio preview lifecycle; Mongo owns identity, admission and operation leases."""

    def __init__(
        self, *, provider_resolver: Any | None = None,
        startup_timeout_seconds: float = 120, store: MongoPreviewStore | None = None,
    ) -> None:
        self._store = store if store is not None else MongoPreviewStore()
        self._ws_clients: dict[str, set[Any]] = {}
        self._ws_status: dict[str, dict[str, Any]] = {}
        self._provider_resolver = provider_resolver or resolve_preview_provider
        self._ttl_minutes = self._positive_setting("SANDBOX_TTL_MINUTES", 30)
        self._max_sessions = self._positive_setting("SANDBOX_MAX_SESSIONS", 20)
        self._max_owner_sessions = self._positive_setting("SANDBOX_MAX_OWNER_SESSIONS", 2)
        self._max_pending = self._positive_setting("SANDBOX_MAX_PENDING", 20)
        self._max_parallel_creates = self._positive_setting("SANDBOX_MAX_PARALLEL_CREATES", 4)
        self._queue_seconds = self._positive_setting("SANDBOX_QUEUE_TIMEOUT_SECONDS", 15)
        self._lease_seconds = self._positive_setting("SANDBOX_OPERATION_LEASE_SECONDS", 60)
        self._template = os.getenv("SANDBOX_TEMPLATE") or None
        self._startup_timeout_seconds = startup_timeout_seconds
        self._allocation_timeout_seconds = 60
        self._health_interval_seconds = 10
        self._poll_seconds = 0.1

    @staticmethod
    def _positive_setting(name: str, default: int) -> int:
        value = int(os.getenv(name) or str(default))
        if value <= 0:
            raise ValueError(f"{name} must be positive")
        return value

    def _workdir(self, provider: str) -> str:
        return sandbox_workspace_root(provider)

    def _adapter(self, provider: str) -> SandboxPort:
        resolved_provider, adapter = self._provider_resolver()
        if resolved_provider != provider:
            raise RuntimeError("Preview provider changed; restore the provider to stop its existing previews")
        return adapter

    @staticmethod
    def _is_expired(state: PreviewSessionState) -> bool:
        return _utcnow() >= state.expires_at

    @staticmethod
    def _message(state: PreviewSessionState) -> dict[str, Any]:
        return {
            "type": "status", "status": state.status,
            "previewUrl": state.preview_url, "lastError": state.last_error,
        }

    async def _broadcast(self, sandbox_id: str, message: dict[str, Any]) -> None:
        self._ws_status[sandbox_id] = message
        for websocket in list(self._ws_clients.get(sandbox_id, set())):
            try:
                await asyncio.wait_for(websocket.send_json(message), timeout=2)
            except Exception:
                self._ws_clients.get(sandbox_id, set()).discard(websocket)
        if not self._ws_clients.get(sandbox_id):
            self._ws_status.pop(sandbox_id, None)

    async def register_ws(self, sandbox_id: str, websocket: Any) -> None:
        state = await self._ensure_alive(sandbox_id)
        self._ws_clients.setdefault(sandbox_id, set()).add(websocket)
        message = self._message(state)
        self._ws_status[sandbox_id] = message
        await websocket.send_json(message)

    async def unregister_ws(self, sandbox_id: str, websocket: Any) -> None:
        self._ws_clients.get(sandbox_id, set()).discard(websocket)
        if not self._ws_clients.get(sandbox_id):
            self._ws_clients.pop(sandbox_id, None)
            self._ws_status.pop(sandbox_id, None)

    async def _close_websockets(self, sandbox_id: str) -> None:
        for websocket in list(self._ws_clients.pop(sandbox_id, set())):
            try:
                await asyncio.wait_for(websocket.close(), timeout=2)
            except Exception:
                pass
        self._ws_status.pop(sandbox_id, None)

    async def refresh_websockets(self) -> None:
        # Each worker observes durable status for only its locally connected clients.
        # No provider command is issued per client or per WebSocket poll.
        for sandbox_id in list(self._ws_clients):
            record = await self._store.get(sandbox_id)
            if record is None:
                await self._broadcast(sandbox_id, {
                    "type": "status", "status": "error", "previewUrl": None,
                    "lastError": "Preview stopped",
                })
                await self._close_websockets(sandbox_id)
                continue
            message = self._message(PreviewSessionState.from_record(record))
            if self._ws_status.get(sandbox_id) != message:
                await self._broadcast(sandbox_id, message)

    async def create_or_reuse(
        self, artifact_id: str, *, app_id: str, user_id: str, target_app_id: str, build_registry_id: str,
    ) -> PreviewSessionState:
        if not all(is_valid_artifact_id(value) for value in (artifact_id, app_id, target_app_id, build_registry_id)) or not user_id:
            raise ValueError("Invalid preview identity")
        provider, adapter = self._provider_resolver()
        identity = dict(
            artifact_id=artifact_id, app_id=app_id, user_id=user_id,
            target_app_id=target_app_id, build_registry_id=build_registry_id, provider=provider,
        )
        await self.cleanup()
        reservation = await self._store.reserve(
            identity, max_sessions=self._max_sessions, max_owner_sessions=self._max_owner_sessions,
            max_pending=self._max_pending, queue_seconds=self._queue_seconds,
            ttl_seconds=self._ttl_minutes * 60,
        )
        sandbox_id = reservation["sandbox_id"]
        wait_deadline = time.monotonic() + self._queue_seconds
        try:
            while True:
                record = await self._store.get(sandbox_id)
                if record is None:
                    raise PreviewCapacityError("Preview queue expired; try again")
                if record["phase"] == "active":
                    state = PreviewSessionState.from_record(record)
                    if state.status == "error" or self._is_expired(state):
                        await self.stop(sandbox_id)
                        return await self.create_or_reuse(artifact_id, **{k: v for k, v in identity.items() if k not in {"artifact_id", "provider"}})
                    return state
                allocation = None
                if record["phase"] == "queued":
                    try:
                        allocation = await self._store.try_allocate(
                            sandbox_id, max_parallel_creates=self._max_parallel_creates,
                            provider_deadline=_utcnow() + timedelta(seconds=self._ttl_minutes * 60 + self._allocation_timeout_seconds),
                        )
                    except KeyError as exc:
                        await self._store.abandon_queued(sandbox_id)
                        raise PreviewCapacityError("Preview queue expired; try again") from exc
                if allocation is not None:
                    return await self._allocate(allocation, adapter)
                if time.monotonic() >= wait_deadline:
                    await self._store.abandon_queued(sandbox_id)
                    raise PreviewCapacityError("Preview capacity is busy; stop an existing preview or try again shortly")
                await asyncio.sleep(self._poll_seconds)
        except asyncio.CancelledError:
            # A disconnected caller cannot cancel another worker's paid allocation.
            await self._store.abandon_queued(sandbox_id)
            raise

    async def _allocate(self, allocation: dict[str, Any], adapter: SandboxPort) -> PreviewSessionState:
        sandbox_id = allocation["sandbox_id"]
        token = allocation["allocation_token"]
        started = time.monotonic()
        try:
            async with asyncio.timeout(self._allocation_timeout_seconds):
                info = await adapter.create_session(
                    template=self._template, timeout_seconds=self._ttl_minutes * 60,
                    envs=sandbox_resource_environment(),
                    metadata={
                        "purpose": "artifact_preview", "manager_sandbox_id": sandbox_id,
                        **{name: allocation[name] for name in ("artifact_id", "app_id", "user_id", "target_app_id", "build_registry_id")},
                    },
                )
        except (Exception, asyncio.CancelledError):
            # A timeout can follow successful remote allocation. The durable claim
            # continues counting against capacity until its conservative hard deadline.
            logger.warning("preview_allocation_unconfirmed sandbox=%s", sandbox_id)
            raise
        state = PreviewSessionState.from_record({
            **allocation, "phase": "active", "session_id": info.session_id,
            "expires_at": _utcnow() + timedelta(seconds=self._ttl_minutes * 60 - (time.monotonic() - started)),
        })
        try:
            saved = await self._store.attach_session(sandbox_id, token, state.payload(), expires_at=state.expires_at)
        except (Exception, asyncio.CancelledError):
            try:
                if await adapter.terminate_session(session_id=info.session_id):
                    # Attachment can succeed before admission bookkeeping fails.
                    record = await self._store.get(sandbox_id)
                    if record and record.get("session_id"):
                        await self.stop(sandbox_id)
                    else:
                        await self._store.release(sandbox_id, allocation_token=token, provider_absent=True)
            except Exception as exc:
                logger.warning("preview_cleanup_failed sandbox=%s exception=%s", sandbox_id, type(exc).__name__)
            raise
        logger.info("preview_allocated sandbox=%s duration_seconds=%.3f", sandbox_id, time.monotonic() - started)
        return PreviewSessionState.from_record(saved)

    async def _ensure_alive(self, sandbox_id: str) -> PreviewSessionState:
        if not is_valid_sandbox_id(sandbox_id):
            raise ValueError("Invalid sandboxId")
        record = await self._store.get(sandbox_id)
        if record is None:
            raise KeyError("Sandbox not found")
        state = PreviewSessionState.from_record(record)
        if self._is_expired(state):
            await self.stop(sandbox_id)
            raise KeyError("Sandbox expired")
        return state

    async def require_owner(self, sandbox_id: str, *, app_id: str, user_id: str) -> PreviewSessionState:
        record = await self._store.get(sandbox_id)
        if record is None or (record["app_id"], record["user_id"]) != (app_id, user_id):
            raise KeyError("Sandbox not found")
        return await self._ensure_alive(sandbox_id)

    @asynccontextmanager
    async def _operation(self, sandbox_id: str, kind: str) -> AsyncIterator[tuple[PreviewSessionState, str]]:
        token = await self._store.claim_operation(sandbox_id, kind=kind, lease_seconds=self._lease_seconds)
        owner = asyncio.current_task()
        lost = False
        completed = False

        async def heartbeat():
            nonlocal lost
            while True:
                await asyncio.sleep(self._lease_seconds / 3)
                try:
                    await self._store.renew_operation(sandbox_id, token, self._lease_seconds)
                except Exception:
                    lost = True
                    if owner is not None:
                        owner.cancel()
                    return

        renewal = asyncio.create_task(heartbeat())
        try:
            record = await self._store.get(sandbox_id)
            if record is None:
                raise KeyError("Sandbox stopped")
            yield PreviewSessionState.from_record(record), token
            if lost:
                raise PreviewLeaseLostError("Preview operation lost its database lease; recreate the preview")
            completed = True
        except asyncio.CancelledError as exc:
            if lost:
                raise PreviewLeaseLostError("Preview operation lost its database lease; recreate the preview") from exc
            raise
        finally:
            renewal.cancel()
            try:
                await renewal
            except asyncio.CancelledError:
                pass
            if not lost:
                if not completed:
                    # If a remote mutation may have completed but its durable
                    # result is unknown, retain the lease for recovery teardown.
                    try:
                        current = await self._store.get(sandbox_id)
                        completed = current is not None and current.get("status") == "error"
                    except Exception:
                        completed = False
                if completed:
                    await self._store.release_operation(sandbox_id, token)

    async def _save(self, state: PreviewSessionState, token: str) -> PreviewSessionState:
        state.last_access_at = _utcnow()
        saved = await self._store.save(state.sandbox_id, state.payload(), operation_token=token)
        await self._broadcast(state.sandbox_id, self._message(state))
        return PreviewSessionState.from_record(saved)

    def _prepare_sync(
        self, state: PreviewSessionState, files: list[dict[str, str | bytes]], deleted: list[str],
    ) -> tuple[dict[str, str | bytes], list[str], list[str], str | None, bool]:
        next_files: dict[str, str | bytes] = {}
        for entry in files:
            raw_path = entry.get("path", "")
            path = _safe_relpath(raw_path) if isinstance(raw_path, str) else None
            if path is None or not isinstance(entry.get("content"), (str, bytes)):
                raise ValueError("Invalid preview file path or content")
            if path in next_files:
                raise ValueError("Duplicate preview file path")
            next_files[path] = entry["content"]
        deleted_paths: list[str] = []
        for raw_path in deleted:
            path = _safe_relpath(raw_path)
            if path is None:
                raise ValueError("Invalid deleted preview file path")
            deleted_paths.append(path)
        destinations: dict[str, str] = {}
        for path in (*state.paths, *next_files, *deleted_paths):
            destination = app_bundle_workspace_path(path)
            if destinations.setdefault(destination, path) != path:
                raise ValueError("Conflicting preview file destinations")
        paths = sorted((set(state.paths) | set(next_files)) - set(deleted_paths))
        manifest = next_files.get("app.json", state.manifest)
        if "app.json" in deleted_paths:
            manifest = None
        if isinstance(manifest, bytes):
            manifest = manifest.decode("utf-8")
        self._validate_identity(state, manifest)
        has_requirements = state.has_requirements
        if "requirements.txt" in next_files:
            has_requirements = bool(next_files["requirements.txt"].strip())
        if "requirements.txt" in deleted_paths:
            has_requirements = False
        self._store.validate_state({**state.payload(), "manifest": manifest, "paths": paths, "has_requirements": has_requirements})
        return next_files, deleted_paths, paths, manifest, has_requirements

    async def sync(self, sandbox_id: str, files: list[dict[str, str | bytes]], deleted: list[str]) -> None:
        await self._ensure_alive(sandbox_id)
        async with self._operation(sandbox_id, "sync") as (state, token):
            if state.status == "error":
                raise ValueError("Preview failed; recreate the preview before syncing files")
            try:
                next_files, deleted_paths, paths, manifest, has_requirements = self._prepare_sync(state, files, deleted)
            except ValueError:
                # Validation completed before any provider mutation.
                await self._store.release_operation(sandbox_id, token)
                raise
            adapter = self._adapter(state.provider)
            try:
                if next_files:
                    await adapter.write_files(
                        session_id=self._session_id(state),
                        files={app_bundle_workspace_path(path): content for path, content in next_files.items()},
                        cwd=self._workdir(state.provider),
                    )
                for path in deleted_paths:
                    result = await adapter.run_command(
                        session_id=self._session_id(state), command=f"rm -f -- {shlex.quote(app_bundle_workspace_path(path))}",
                        cwd=self._workdir(state.provider), timeout_seconds=15,
                    )
                    if not result.success:
                        raise RuntimeError("Failed to remove a preview file")
                state.paths, state.manifest, state.has_requirements = paths, manifest, has_requirements
                state.health_checked_at = None
                await self._save(state, token)
            except (Exception, asyncio.CancelledError):
                await self._fail(state, "Preview file sync failed; recreate the preview", token)
                raise

    @staticmethod
    def _validate_identity(state: PreviewSessionState, raw_manifest: str | None) -> None:
        try:
            manifest = json.loads(raw_manifest) if raw_manifest is not None else None
        except (ValueError, TypeError) as exc:
            raise ValueError("Preview requires a canonical app.json") from exc
        if manifest is None:
            raise ValueError("Preview requires a canonical app.json")
        if not isinstance(manifest, dict) or manifest.get("appId") != state.target_app_id:
            raise ValueError("Preview appId does not match the owned build target")

    @staticmethod
    def _session_id(state: PreviewSessionState) -> str:
        if not state.session_id:
            raise RuntimeError("Sandbox provider session missing")
        return state.session_id

    async def _fail(self, state: PreviewSessionState, message: str, token: str) -> PreviewSessionState:
        state.status, state.last_error, state.preview_url = "error", message, None
        # Publish failure before remote teardown; an outage must retain cleanup debt.
        await self._save(state, token)
        if state.session_id:
            try:
                if await self._adapter(state.provider).terminate_session(session_id=state.session_id):
                    state.session_id = None
            except Exception as exc:
                logger.warning("preview_cleanup_failed sandbox=%s exception=%s", state.sandbox_id, type(exc).__name__)
        return await self._save(state, token)

    async def start(self, sandbox_id: str) -> PreviewSessionState:
        await self._ensure_alive(sandbox_id)
        async with self._operation(sandbox_id, "start") as (state, token):
            if state.status == "error":
                return state
            state.status, state.preview_url, state.last_error = "starting", None, None
            await self._save(state, token)
            try:
                self._validate_identity(state, state.manifest)
                adapter = self._adapter(state.provider)
                session_id = self._session_id(state)
                stopped = await adapter.run_command(session_id=session_id, command=f"{_RUNTIME} stop", timeout_seconds=30)
                if not stopped.success:
                    return await self._fail(state, "Preview image cannot stop the canonical runtime; rebuild the configured sandbox image", token)
                if state.has_requirements:
                    install = await adapter.run_command(
                        session_id=session_id, command="python -m pip install --user -c /opt/mozaiks/preview-constraints.txt -r requirements.txt",
                        cwd=self._workdir(state.provider), timeout_seconds=300,
                    )
                    if not install.success:
                        return await self._fail(state, "Preview dependency installation failed", token)
                url = await adapter.get_preview_url(session_id=session_id, port=_PREVIEW_PORT)
                if not url:
                    return await self._fail(state, "Preview provider did not publish the frontend port", token)
                command = (
                    f"{_RUNTIME} start --app-root {shlex.quote(self._workdir(state.provider) + '/app')} "
                    f"--preview-url {shlex.quote(url)} > /tmp/mozaiks-preview-start.log 2>&1"
                )
                result = await adapter.run_command(
                    session_id=session_id, command=command, background=True,
                    envs={"__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS": urlsplit(url).hostname or ""},
                )
                if not result.success:
                    return await self._fail(state, "Preview runtime process failed to start", token)
                return await self._finish_start(state, _PREVIEW_PORT, token)
            except PreviewLeaseLostError:
                raise
            except asyncio.CancelledError:
                await self._fail(state, "Preview startup cancelled", token)
                raise
            except ValueError as exc:
                return await self._fail(state, str(exc), token)
            except Exception as exc:
                logger.warning("preview_start_failed sandbox=%s exception=%s", sandbox_id, type(exc).__name__)
                return await self._fail(state, "Preview startup failed; inspect the sandbox runtime logs", token)

    async def _finish_start(self, state: PreviewSessionState, port: int, token: str) -> PreviewSessionState:
        adapter = self._adapter(state.provider)
        deadline = time.monotonic() + self._startup_timeout_seconds
        while True:
            result = await adapter.run_command(
                session_id=self._session_id(state),
                command=f"{_RUNTIME} check --port {port} --app-root {shlex.quote(self._workdir(state.provider) + '/app')}",
                timeout_seconds=10,
            )
            if result.success:
                break
            if time.monotonic() >= deadline:
                return await self._fail(state, "Preview backend or frontend did not become healthy", token)
            await asyncio.sleep(1)
        url = await adapter.get_preview_url(session_id=self._session_id(state), port=port)
        if not url:
            return await self._fail(state, "Preview provider did not publish the frontend port", token)
        state.preview_url, state.status, state.last_error = url, "running", None
        state.health_checked_at = _utcnow()
        return await self._save(state, token)

    async def status(self, sandbox_id: str) -> PreviewSessionState:
        state = await self._ensure_alive(sandbox_id)
        if state.status != "running" or (
            state.health_checked_at is not None
            and (_utcnow() - state.health_checked_at).total_seconds() < self._health_interval_seconds
        ):
            return state
        try:
            async with self._operation(sandbox_id, "status") as (state, token):
                if state.status != "running" or (
                    state.health_checked_at is not None
                    and (_utcnow() - state.health_checked_at).total_seconds() < self._health_interval_seconds
                ):
                    return state
                try:
                    result = await self._adapter(state.provider).run_command(
                        session_id=self._session_id(state),
                        command=f"{_RUNTIME} check --app-root {shlex.quote(self._workdir(state.provider) + '/app')}",
                        timeout_seconds=10,
                    )
                except Exception:
                    return await self._fail(state, "Preview runtime is unavailable", token)
                if not result.success:
                    return await self._fail(state, "Preview runtime is no longer healthy", token)
                state.health_checked_at = _utcnow()
                return await self._save(state, token)
        except PreviewOperationBusy:
            return await self._ensure_alive(sandbox_id)

    async def stop(self, sandbox_id: str) -> None:
        record = await self._store.get(sandbox_id)
        if record is None:
            return
        if record["phase"] == "queued":
            await self._store.abandon_queued(sandbox_id)
            return
        if record["phase"] == "provisioning":
            if _utcnow() < record["expires_at"]:
                raise PreviewOperationBusy("Preview allocation is in progress; retry shortly")
            await self._store.release(sandbox_id, allocation_token=record["allocation_token"])
        else:
            async with self._operation(sandbox_id, "stop") as (state, token):
                state.status, state.preview_url, state.last_error = "error", None, "Preview cleanup pending"
                await self._save(state, token)
                if state.session_id:
                    stopped = await self._adapter(state.provider).terminate_session(session_id=state.session_id)
                    if not stopped:
                        raise RuntimeError("Preview provider could not confirm the sandbox stopped")
                await self._store.release(sandbox_id, operation_token=token, provider_absent=True)
        await self._broadcast(sandbox_id, {
            "type": "status", "status": "error", "lastError": "Preview stopped", "previewUrl": None,
        })
        await self._close_websockets(sandbox_id)

    async def cleanup(self, *, expired_only: bool = True) -> None:
        for record in await self._store.list():
            if record["phase"] == "queued":
                if not expired_only or record["queue_deadline"] <= _utcnow():
                    await self._store.abandon_queued(record["sandbox_id"])
                continue
            operation = record.get("operation") or {}
            interrupted = operation.get("expires_at") is not None and operation["expires_at"] <= _utcnow()
            if expired_only and record.get("status") != "error" and record["expires_at"] > _utcnow() and not interrupted:
                continue
            try:
                await self.stop(record["sandbox_id"])
            except PreviewOperationBusy:
                continue
            except Exception as exc:
                logger.warning("preview_cleanup_failed sandbox=%s exception=%s", record["sandbox_id"], type(exc).__name__)

    async def _maintenance_loop(self) -> None:
        cleanup_at = 0.0
        while True:
            try:
                await self.refresh_websockets()
                if time.monotonic() >= cleanup_at:
                    await self.cleanup()
                    cleanup_at = time.monotonic() + 15
            except Exception as exc:
                logger.warning("preview_maintenance_failed exception=%s", type(exc).__name__)
            await asyncio.sleep(1)

    async def close(self) -> None:
        for sandbox_id in list(self._ws_clients):
            await self._close_websockets(sandbox_id)


@asynccontextmanager
async def preview_sessions_lifespan(_app):
    manager = get_artifact_preview_sessions()
    task = asyncio.create_task(manager._maintenance_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # Running previews belong to durable records, not this worker's lifetime.
        await manager.close()


_manager: ArtifactPreviewSessionManager | None = None


def get_artifact_preview_sessions() -> ArtifactPreviewSessionManager:
    global _manager
    if _manager is None:
        _manager = ArtifactPreviewSessionManager()
    return _manager


def reset_artifact_preview_sessions() -> None:
    global _manager
    _manager = None


__all__ = [
    "ArtifactPreviewSessionManager", "PreviewSessionState", "get_artifact_preview_sessions",
    "is_valid_artifact_id", "is_valid_sandbox_id", "reset_artifact_preview_sessions", "resolve_preview_provider",
    "PreviewCapacityError", "preview_sessions_lifespan",
]

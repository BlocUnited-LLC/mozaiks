"""Owner-bound, disposable canonical-app previews over SandboxPort."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from logs.logging_config import get_core_logger
from mozaiksai.core.ports.sandbox import SandboxPort
from mozaiksai.core.runtime.app.paths import app_bundle_workspace_path

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
    session_id: str | None = None
    status: str = "starting"
    preview_url: str | None = None
    last_error: str | None = None
    last_access_at: datetime = field(default_factory=_utcnow)
    last_files: dict[str, str | bytes] = field(default_factory=dict)
    operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


class ArtifactPreviewSessionManager:
    def __init__(self, *, provider_resolver: Any | None = None, startup_timeout_seconds: float = 120) -> None:
        self._lock = asyncio.Lock()
        self._artifact_to_sandbox: dict[tuple[str, str, str], str] = {}
        self._sessions: dict[str, PreviewSessionState] = {}
        self._ws_clients: dict[str, set[Any]] = {}
        self._provider_resolver = provider_resolver or resolve_preview_provider
        self._ttl_minutes = int(os.getenv("SANDBOX_TTL_MINUTES", "30"))
        if self._ttl_minutes <= 0:
            raise ValueError("SANDBOX_TTL_MINUTES must be positive")
        self._template = os.getenv("SANDBOX_TEMPLATE") or None
        self._startup_timeout_seconds = startup_timeout_seconds

    def _workdir(self, provider: str) -> str:
        root = "/workspace" if provider == "docker" else os.getenv("SANDBOX_WORKDIR", "/home/user/app")
        return root.rstrip("/")

    def _adapter(self, provider: str) -> SandboxPort:
        resolved_provider, adapter = self._provider_resolver()
        if resolved_provider != provider:
            raise RuntimeError("Preview provider changed; restart the preview session")
        return adapter

    def _is_expired(self, state: PreviewSessionState) -> bool:
        # Provider deadlines are absolute. Status polling must not extend them.
        return _utcnow() - state.created_at >= timedelta(minutes=self._ttl_minutes)

    async def _broadcast(self, sandbox_id: str, message: dict[str, Any]) -> None:
        for websocket in list(self._ws_clients.get(sandbox_id, set())):
            try:
                await websocket.send_json(message)
            except Exception:
                self._ws_clients.get(sandbox_id, set()).discard(websocket)

    async def register_ws(self, sandbox_id: str, websocket: Any) -> None:
        state = await self._ensure_alive(sandbox_id)
        self._ws_clients.setdefault(sandbox_id, set()).add(websocket)
        await websocket.send_json({
            "type": "status", "status": state.status,
            "previewUrl": state.preview_url, "lastError": state.last_error,
        })

    async def unregister_ws(self, sandbox_id: str, websocket: Any) -> None:
        self._ws_clients.get(sandbox_id, set()).discard(websocket)

    async def create_or_reuse(
        self, artifact_id: str, *, app_id: str, user_id: str, target_app_id: str, build_registry_id: str,
    ) -> PreviewSessionState:
        if not all(is_valid_artifact_id(value) for value in (artifact_id, app_id, target_app_id, build_registry_id)) or not user_id:
            raise ValueError("Invalid preview identity")
        provider, adapter = self._provider_resolver()
        key = (app_id, user_id, artifact_id)
        async with self._lock:
            existing = self._sessions.get(self._artifact_to_sandbox.get(key, ""))
            if existing:
                if (existing.target_app_id, existing.build_registry_id) != (target_app_id, build_registry_id):
                    raise ValueError("Preview artifact identity changed")
                if existing.provider == provider and existing.status != "error" and not self._is_expired(existing):
                    return existing
                await self.stop(existing.sandbox_id)
            state = PreviewSessionState(
                sandbox_id=uuid4().hex, artifact_id=artifact_id, app_id=app_id, user_id=user_id,
                target_app_id=target_app_id, build_registry_id=build_registry_id,
                provider=provider, created_at=_utcnow(),
            )
            # The host never forwards its own environment or credentials implicitly.
            envs = {name[len(_ENV_PREFIX):]: value for name, value in os.environ.items() if name.startswith(_ENV_PREFIX)}
            info = await adapter.create_session(
                template=self._template, timeout_seconds=self._ttl_minutes * 60,
                envs=envs,
                metadata={
                    "purpose": "artifact_preview", "artifact_id": artifact_id,
                    "manager_sandbox_id": state.sandbox_id, "app_id": app_id,
                    "user_id": user_id, "target_app_id": target_app_id,
                    "build_registry_id": build_registry_id,
                },
            )
            state.session_id = info.session_id
            self._sessions[state.sandbox_id] = state
            self._artifact_to_sandbox[key] = state.sandbox_id
            return state

    async def _ensure_alive(self, sandbox_id: str) -> PreviewSessionState:
        if not is_valid_sandbox_id(sandbox_id):
            raise ValueError("Invalid sandboxId")
        state = self._sessions.get(sandbox_id)
        if state is None:
            raise KeyError("Sandbox not found")
        if self._is_expired(state):
            await self.stop(sandbox_id)
            raise KeyError("Sandbox expired")
        state.last_access_at = _utcnow()
        return state

    async def require_owner(self, sandbox_id: str, *, app_id: str, user_id: str) -> PreviewSessionState:
        state = self._sessions.get(sandbox_id)
        if state is None or (state.app_id, state.user_id) != (app_id, user_id):
            raise KeyError("Sandbox not found")
        return await self._ensure_alive(sandbox_id)

    async def sync(self, sandbox_id: str, files: list[dict[str, str | bytes]], deleted: list[str]) -> None:
        state = await self._ensure_alive(sandbox_id)
        async with state.operation_lock:
            if self._sessions.get(sandbox_id) is not state:
                raise KeyError("Sandbox stopped")
            if state.status == "error":
                raise ValueError("Preview failed; recreate the preview before syncing files")
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
            for path in (*state.last_files, *next_files, *deleted_paths):
                destination = app_bundle_workspace_path(path)
                if destinations.setdefault(destination, path) != path:
                    raise ValueError("Conflicting preview file destinations")
            merged = {**state.last_files, **next_files}
            for path in deleted_paths:
                merged.pop(path, None)
            self._validate_identity(state, merged)
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
            except (Exception, asyncio.CancelledError):
                await self._fail(state, "Preview file sync failed; recreate the preview")
                raise
            state.last_files = merged

    @staticmethod
    def _validate_identity(state: PreviewSessionState, files: dict[str, str | bytes]) -> None:
        try:
            manifest = json.loads(files["app.json"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError("Preview requires a canonical app.json") from exc
        if not isinstance(manifest, dict) or manifest.get("appId") != state.target_app_id:
            raise ValueError("Preview appId does not match the owned build target")

    @staticmethod
    def _session_id(state: PreviewSessionState) -> str:
        if not state.session_id:
            raise RuntimeError("Sandbox provider session missing")
        return state.session_id

    async def _fail(self, state: PreviewSessionState, message: str) -> PreviewSessionState:
        state.status = "error"
        state.last_error = message
        state.preview_url = None
        await self._broadcast(state.sandbox_id, {"type": "status", "status": "error", "error": message, "previewUrl": None})
        return state

    async def start(self, sandbox_id: str) -> PreviewSessionState:
        state = await self._ensure_alive(sandbox_id)
        async with state.operation_lock:
            if self._sessions.get(sandbox_id) is not state:
                raise KeyError("Sandbox stopped")
            if state.status == "error":
                return state
            state.status, state.preview_url, state.last_error = "starting", None, None
            await self._broadcast(sandbox_id, {"type": "status", "status": "starting", "previewUrl": None})
            try:
                self._validate_identity(state, state.last_files)
                adapter = self._adapter(state.provider)
                session_id = self._session_id(state)
                stopped = await adapter.run_command(session_id=session_id, command=f"{_RUNTIME} stop", timeout_seconds=30)
                if not stopped.success:
                    return await self._fail(state, "Preview image cannot stop the canonical runtime; rebuild the configured sandbox image")
                if state.last_files.get("requirements.txt", "").strip():
                    install = await adapter.run_command(
                        session_id=session_id, command="python -m pip install --user -c /opt/mozaiks/preview-constraints.txt -r requirements.txt",
                        cwd=self._workdir(state.provider), timeout_seconds=300,
                    )
                    if not install.success:
                        return await self._fail(state, "Preview dependency installation failed")
                url = await adapter.get_preview_url(session_id=session_id, port=_PREVIEW_PORT)
                if not url:
                    return await self._fail(state, "Preview provider did not publish the frontend port")
                command = (
                    f"{_RUNTIME} start --app-root {shlex.quote(self._workdir(state.provider) + '/app')} "
                    f"--preview-url {shlex.quote(url)} > /tmp/mozaiks-preview-start.log 2>&1"
                )
                result = await adapter.run_command(session_id=session_id, command=command, background=True)
                if not result.success:
                    return await self._fail(state, "Preview runtime process failed to start")
                return await self._finish_start(state, _PREVIEW_PORT)
            except ValueError as exc:
                return await self._fail(state, str(exc))
            except Exception as exc:
                logger.warning("preview_start_failed sandbox=%s exception=%s", sandbox_id, type(exc).__name__)
                return await self._fail(state, "Preview startup failed; inspect the sandbox runtime logs")

    async def _finish_start(self, state: PreviewSessionState, port: int) -> PreviewSessionState:
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
                return await self._fail(state, "Preview backend or frontend did not become healthy")
            await asyncio.sleep(1)
        url = await adapter.get_preview_url(session_id=self._session_id(state), port=port)
        if not url:
            return await self._fail(state, "Preview provider did not publish the frontend port")
        state.preview_url, state.status, state.last_error = url, "running", None
        await self._broadcast(state.sandbox_id, {"type": "status", "status": "running", "previewUrl": url})
        return state

    async def status(self, sandbox_id: str) -> PreviewSessionState:
        state = await self._ensure_alive(sandbox_id)
        async with state.operation_lock:
            if self._sessions.get(sandbox_id) is not state:
                raise KeyError("Sandbox stopped")
            if state.status == "running":
                try:
                    result = await self._adapter(state.provider).run_command(
                        session_id=self._session_id(state),
                        command=f"{_RUNTIME} check --app-root {shlex.quote(self._workdir(state.provider) + '/app')}",
                        timeout_seconds=10,
                    )
                except Exception:
                    return await self._fail(state, "Preview runtime is unavailable")
                if not result.success:
                    return await self._fail(state, "Preview runtime is no longer healthy")
        return state

    async def stop(self, sandbox_id: str) -> None:
        state = self._sessions.get(sandbox_id)
        if state is None:
            return
        async with state.operation_lock:
            if state.session_id:
                stopped = await self._adapter(state.provider).terminate_session(session_id=state.session_id)
                if not stopped:
                    raise RuntimeError("Preview provider could not confirm the sandbox stopped")
            state.status, state.preview_url, state.last_error = "error", None, "Preview stopped"
            await self._broadcast(sandbox_id, {"type": "status", "status": "error", "error": "Preview stopped", "previewUrl": None})
            self._sessions.pop(sandbox_id, None)
            self._artifact_to_sandbox.pop((state.app_id, state.user_id, state.artifact_id), None)
            for websocket in list(self._ws_clients.pop(sandbox_id, set())):
                try:
                    await websocket.close()
                except Exception:
                    pass


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
]

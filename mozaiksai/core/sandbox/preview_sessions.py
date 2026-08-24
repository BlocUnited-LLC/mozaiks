"""Artifact preview sessions over the SandboxPort seam.

Manages ephemeral sandbox sessions that boot a generated app bundle so the
Studio AppWorkbench can render (and refresh) a live preview iframe. Sessions
are disposable workspaces, never truth stores: state lives in memory, every
provider session carries a kill deadline and identity metadata, and outcomes
that matter persist elsewhere (build records).

Provider resolution mirrors the app-validation ladder for the preview-capable
strategies: e2b when `E2B_API_KEY` is set, otherwise local Docker when a
daemon is reachable. `local`/`skip` have no preview surface, so there is no
further fallback — creation fails with a clear message instead.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import posixpath
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from logs.logging_config import get_core_logger
from mozaiksai.core.ports.sandbox import SandboxPort

logger = get_core_logger("artifact_preview_sessions")

_ARTIFACT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_SANDBOX_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")

_NODE_PREVIEW_PORT = 3000
_PYTHON_PREVIEW_PORT = 8000
_INSTALL_TIMEOUT_SECONDS = 300.0


def is_valid_artifact_id(value: str) -> bool:
    return bool(value and isinstance(value, str) and _ARTIFACT_ID_RE.match(value))


def is_valid_sandbox_id(value: str) -> bool:
    return bool(value and isinstance(value, str) and _SANDBOX_ID_RE.match(value))


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _safe_relpath(raw: str) -> str | None:
    """Normalize a sandbox-relative file path; reject traversal and absolutes."""
    value = str(raw or "").strip().replace("\\", "/").lstrip("/")
    if not value:
        return None
    normalized = posixpath.normpath(value)
    if normalized.startswith("..") or "/../" in f"/{normalized}/":
        return None
    return normalized


def resolve_preview_provider(env: dict[str, str] | None = None) -> tuple[str, SandboxPort]:
    """Resolve the preview-capable sandbox adapter, e2b first then docker."""
    env_map = env or os.environ
    if str(env_map.get("E2B_API_KEY", "")).strip():
        from mozaiksai.core.adapters.e2b_sandbox import get_e2b_sandbox

        return "e2b", get_e2b_sandbox()
    from mozaiksai.core.adapters.docker_sandbox import docker_available, get_docker_sandbox

    if docker_available():
        return "docker", get_docker_sandbox()
    raise RuntimeError(
        "No preview sandbox provider available. Set E2B_API_KEY for hosted "
        "sandboxes or start a local Docker daemon."
    )


@dataclass
class PreviewSessionState:
    sandbox_id: str
    artifact_id: str
    provider: str
    created_at: datetime
    session_id: str | None = None
    status: str = "starting"  # starting|running|error
    preview_url: str | None = None
    last_error: str | None = None
    last_access_at: datetime = field(default_factory=_utcnow)
    last_files: dict[str, str] = field(default_factory=dict)


class ArtifactPreviewSessionManager:
    """In-memory registry of artifact preview sessions over SandboxPort.

    Contract:
    - artifact_id -> sandbox_id reuse (one active preview per artifact)
    - sandbox_id -> state; provider session ids never leave the server
    - provider sessions carry a kill deadline (TTL) and identity metadata so
      orphans are attributable and self-terminating even if this process dies
    """

    def __init__(self, *, provider_resolver: Any | None = None) -> None:
        self._lock = asyncio.Lock()
        self._artifact_to_sandbox: dict[str, str] = {}
        self._sessions: dict[str, PreviewSessionState] = {}
        self._ws_clients: dict[str, set[Any]] = {}
        self._provider_resolver = provider_resolver or resolve_preview_provider

        try:
            self._ttl_minutes = int(os.getenv("SANDBOX_TTL_MINUTES", "30"))
        except Exception:
            self._ttl_minutes = 30
        self._template = os.getenv("SANDBOX_TEMPLATE") or None

    def _workdir(self, provider: str) -> str:
        if provider == "docker":
            return "/workspace"
        return os.getenv("SANDBOX_WORKDIR", "/home/user/app").rstrip("/")

    def _adapter(self, provider: str) -> SandboxPort:
        resolved_provider, adapter = self._provider_resolver()
        if resolved_provider != provider:
            raise RuntimeError(
                f"Preview provider changed from '{provider}' to '{resolved_provider}'; "
                "restart the preview session."
            )
        return adapter

    def _is_expired(self, st: PreviewSessionState) -> bool:
        if self._ttl_minutes <= 0:
            return False
        return _utcnow() - st.last_access_at > timedelta(minutes=self._ttl_minutes)

    async def _broadcast(self, sandbox_id: str, message: dict[str, Any]) -> None:
        clients = list(self._ws_clients.get(sandbox_id, set()))
        for ws in clients:
            try:
                await ws.send_json(message)
            except Exception:
                self._ws_clients.get(sandbox_id, set()).discard(ws)

    async def register_ws(self, sandbox_id: str, websocket: Any) -> None:
        async with self._lock:
            self._ws_clients.setdefault(sandbox_id, set()).add(websocket)
            st = self._sessions.get(sandbox_id)
        if st:
            await self._broadcast(
                sandbox_id,
                {
                    "type": "status",
                    "status": st.status,
                    "previewUrl": st.preview_url,
                    "lastError": st.last_error,
                },
            )

    async def unregister_ws(self, sandbox_id: str, websocket: Any) -> None:
        async with self._lock:
            self._ws_clients.get(sandbox_id, set()).discard(websocket)

    async def create_or_reuse(self, artifact_id: str) -> PreviewSessionState:
        if not is_valid_artifact_id(artifact_id):
            raise ValueError("Invalid artifactId")

        provider, adapter = self._provider_resolver()

        async with self._lock:
            existing_id = self._artifact_to_sandbox.get(artifact_id)
            if existing_id:
                st = self._sessions.get(existing_id)
                if st and st.provider == provider and not self._is_expired(st):
                    st.last_access_at = _utcnow()
                    return st

            sandbox_id = hashlib.sha1(
                f"{artifact_id}:{_utcnow().isoformat()}".encode()
            ).hexdigest()[:18]
            st = PreviewSessionState(
                sandbox_id=sandbox_id,
                artifact_id=artifact_id,
                provider=provider,
                created_at=_utcnow(),
                status="starting",
            )
            self._artifact_to_sandbox[artifact_id] = sandbox_id
            self._sessions[sandbox_id] = st

        logger.info(
            "PREVIEW_SESSION_CREATE artifact=%s sandbox=%s provider=%s",
            artifact_id,
            sandbox_id,
            provider,
        )
        try:
            info = await adapter.create_session(
                template=self._template,
                timeout_seconds=(self._ttl_minutes * 60) if self._ttl_minutes > 0 else None,
                metadata={
                    "purpose": "artifact_preview",
                    "artifact_id": str(artifact_id),
                    "manager_sandbox_id": sandbox_id,
                },
            )
            st.session_id = info.session_id
            st.status = "starting"
            st.last_error = None
            await self._broadcast(sandbox_id, {"type": "status", "status": "starting"})
            return st
        except Exception as exc:
            st.status = "error"
            st.last_error = f"Sandbox create failed: {exc}"
            await self._broadcast(
                sandbox_id, {"type": "status", "status": "error", "error": st.last_error}
            )
            raise

    async def _ensure_alive(self, sandbox_id: str) -> PreviewSessionState:
        if not is_valid_sandbox_id(sandbox_id):
            raise ValueError("Invalid sandboxId")
        async with self._lock:
            st = self._sessions.get(sandbox_id)
        if not st:
            raise KeyError("Sandbox not found")
        if self._is_expired(st):
            await self.stop(sandbox_id)
            raise KeyError("Sandbox expired")
        st.last_access_at = _utcnow()
        return st

    async def sync(
        self, sandbox_id: str, files: list[dict[str, str]], deleted: list[str]
    ) -> None:
        st = await self._ensure_alive(sandbox_id)
        if not st.session_id:
            raise RuntimeError("Sandbox provider session missing")
        adapter = self._adapter(st.provider)
        workdir = self._workdir(st.provider)

        next_files: dict[str, str] = {}
        for entry in files or []:
            if not isinstance(entry, dict):
                continue
            path = _safe_relpath(entry.get("path", ""))
            content = entry.get("content")
            if path and isinstance(content, str):
                next_files[path] = content

        deleted_paths = [p for p in (_safe_relpath(d) for d in (deleted or [])) if p]

        logger.info(
            "PREVIEW_SESSION_SYNC sandbox=%s files=%d deleted=%d",
            sandbox_id,
            len(next_files),
            len(deleted_paths),
        )

        if next_files:
            await adapter.write_files(
                session_id=self._session_id(st),
                files=dict(next_files),
                cwd=workdir,
            )
        for rel_path in deleted_paths:
            await adapter.run_command(
                session_id=self._session_id(st),
                command=f"rm -f {json.dumps(rel_path)}",
                cwd=workdir,
                timeout_seconds=15.0,
            )

        st.last_files.update(next_files)
        for rel_path in deleted_paths:
            st.last_files.pop(rel_path, None)

    async def start(self, sandbox_id: str) -> PreviewSessionState:
        st = await self._ensure_alive(sandbox_id)
        if not st.session_id:
            raise RuntimeError("Sandbox provider session missing")

        st.last_error = None
        st.status = "starting"
        st.preview_url = None
        await self._broadcast(sandbox_id, {"type": "status", "status": "starting"})

        files = st.last_files or {}
        if "package.json" in files:
            return await self._start_node(st)
        if "requirements.txt" in files:
            return await self._start_python(st)

        st.status = "error"
        st.last_error = "No runtime detected: include package.json or requirements.txt"
        await self._broadcast(
            sandbox_id, {"type": "status", "status": "error", "error": st.last_error}
        )
        return st

    @staticmethod
    def _session_id(st: PreviewSessionState) -> str:
        if not st.session_id:
            raise RuntimeError("Sandbox provider session missing")
        return st.session_id

    async def _fail(self, st: PreviewSessionState, message: str) -> PreviewSessionState:
        st.status = "error"
        st.last_error = message
        await self._broadcast(
            st.sandbox_id, {"type": "status", "status": "error", "error": message}
        )
        return st

    async def _finish_start(self, st: PreviewSessionState, port: int) -> PreviewSessionState:
        adapter = self._adapter(st.provider)
        await asyncio.sleep(2)
        try:
            preview_url = await adapter.get_preview_url(session_id=self._session_id(st), port=port)
        except Exception as exc:
            return await self._fail(st, f"Failed to get preview URL: {exc}")
        if not preview_url:
            return await self._fail(st, "Failed to get preview URL: provider returned none")
        st.preview_url = preview_url
        st.status = "running"
        await self._broadcast(
            st.sandbox_id,
            {"type": "status", "status": "running", "previewUrl": st.preview_url},
        )
        return st

    async def _start_node(self, st: PreviewSessionState) -> PreviewSessionState:
        adapter = self._adapter(st.provider)
        workdir = self._workdir(st.provider)

        await self._broadcast(
            st.sandbox_id, {"type": "log", "stream": "stdout", "line": "Installing deps..."}
        )
        install = await adapter.run_command(
            session_id=self._session_id(st),
            command="npm install",
            cwd=workdir,
            timeout_seconds=_INSTALL_TIMEOUT_SECONDS,
        )
        if not install.success:
            message = (install.stderr or install.stdout or install.error or "npm install failed").strip()
            await self._broadcast(
                st.sandbox_id, {"type": "log", "stream": "stderr", "line": message}
            )
            return await self._fail(st, message)

        try:
            pkg = json.loads(st.last_files.get("package.json") or "{}")
        except Exception:
            pkg = {}
        scripts = pkg.get("scripts") if isinstance(pkg, dict) else None
        scripts = scripts if isinstance(scripts, dict) else {}

        port = _NODE_PREVIEW_PORT
        if "dev" in scripts:
            cmd = f"npm run dev -- --host 0.0.0.0 --port {port}"
        elif "start" in scripts:
            cmd = f"HOST=0.0.0.0 PORT={port} npm start"
        else:
            return await self._fail(st, "package.json missing scripts.dev or scripts.start")

        await self._broadcast(
            st.sandbox_id, {"type": "log", "stream": "stdout", "line": "Starting dev server..."}
        )
        await adapter.run_command(
            session_id=self._session_id(st),
            command=cmd,
            cwd=workdir,
            background=True,
        )
        return await self._finish_start(st, port)

    async def _start_python(self, st: PreviewSessionState) -> PreviewSessionState:
        adapter = self._adapter(st.provider)
        workdir = self._workdir(st.provider)

        await self._broadcast(
            st.sandbox_id, {"type": "log", "stream": "stdout", "line": "Installing deps..."}
        )
        install = await adapter.run_command(
            session_id=self._session_id(st),
            command="python -m pip install -r requirements.txt",
            cwd=workdir,
            timeout_seconds=_INSTALL_TIMEOUT_SECONDS,
        )
        if not install.success:
            message = (install.stderr or install.stdout or install.error or "pip install failed").strip()
            await self._broadcast(
                st.sandbox_id, {"type": "log", "stream": "stderr", "line": message}
            )
            return await self._fail(st, message)

        main_py = st.last_files.get("main.py")
        if not main_py or "FastAPI" not in main_py:
            return await self._fail(
                st, "Expected main.py with a FastAPI app (e.g., app = FastAPI())"
            )

        port = _PYTHON_PREVIEW_PORT
        await self._broadcast(
            st.sandbox_id, {"type": "log", "stream": "stdout", "line": "Starting server..."}
        )
        await adapter.run_command(
            session_id=self._session_id(st),
            command=f"uvicorn main:app --host 0.0.0.0 --port {port}",
            cwd=workdir,
            background=True,
        )
        return await self._finish_start(st, port)

    async def status(self, sandbox_id: str) -> PreviewSessionState:
        return await self._ensure_alive(sandbox_id)

    async def stop(self, sandbox_id: str) -> None:
        async with self._lock:
            st = self._sessions.get(sandbox_id)
        if not st:
            return

        logger.info(
            "PREVIEW_SESSION_STOP sandbox=%s artifact=%s", sandbox_id, st.artifact_id
        )
        try:
            if st.session_id:
                try:
                    adapter = self._adapter(st.provider)
                    await adapter.terminate_session(session_id=self._session_id(st))
                except Exception:
                    pass
        finally:
            async with self._lock:
                self._sessions.pop(sandbox_id, None)
                self._ws_clients.pop(sandbox_id, None)
                if self._artifact_to_sandbox.get(st.artifact_id) == sandbox_id:
                    self._artifact_to_sandbox.pop(st.artifact_id, None)
            await self._broadcast(
                sandbox_id, {"type": "status", "status": "error", "error": "stopped"}
            )


_manager: ArtifactPreviewSessionManager | None = None


def get_artifact_preview_sessions() -> ArtifactPreviewSessionManager:
    global _manager
    if _manager is None:
        _manager = ArtifactPreviewSessionManager()
    return _manager


def reset_artifact_preview_sessions() -> None:
    """Test hook: drop the process-wide manager singleton."""
    global _manager
    _manager = None


__all__ = [
    "ArtifactPreviewSessionManager",
    "PreviewSessionState",
    "get_artifact_preview_sessions",
    "is_valid_artifact_id",
    "is_valid_sandbox_id",
    "reset_artifact_preview_sessions",
    "resolve_preview_provider",
]


import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from logs.logging_config import get_workflow_logger

try:
    from e2b_code_interpreter import Sandbox  # type: ignore
except Exception:  # pragma: no cover
    Sandbox = None  # type: ignore


_ARTIFACT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_SANDBOX_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


def is_valid_artifact_id(value: str) -> bool:
    return bool(value and isinstance(value, str) and _ARTIFACT_ID_RE.match(value))


def is_valid_sandbox_id(value: str) -> bool:
    return bool(value and isinstance(value, str) and _SANDBOX_ID_RE.match(value))


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _compute_sync_hash(files: dict[str, str], deleted: list[str]) -> str:
    payload = {
        "files": [{"path": p, "sha": _sha256_text(c)} for p, c in sorted(files.items())],
        "deleted": sorted([str(p) for p in (deleted or []) if p]),
    }
    return _sha256_text(json.dumps(payload, sort_keys=True))


@dataclass
class SandboxState:
    sandbox_id: str
    artifact_id: str
    created_at: datetime
    status: str = "starting"  # starting|running|error
    preview_url: str | None = None
    last_error: str | None = None
    last_sync_hash: str | None = None
    last_access_at: datetime = field(default_factory=_utcnow)

    # Provider handle + last synced file snapshot (used for runtime detection)
    sandbox: Any = None
    last_files: dict[str, str] = field(default_factory=dict)


class ArtifactSandboxManager:
    """In-memory sandbox manager for Artifact previews.

    AppGenerator-specific: this is tightly coupled to app generation / preview workflows.

    Contract:
    - artifact_id -> sandbox_id reuse
    - sandbox_id -> state
    - E2B provider key stays server-side only
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._artifact_to_sandbox: dict[str, str] = {}
        self._sandboxes: dict[str, SandboxState] = {}
        self._ws_clients: dict[str, set[Any]] = {}
        self._logger = get_workflow_logger("artifact_sandbox")

        try:
            self._ttl_minutes = int(os.getenv("SANDBOX_TTL_MINUTES", "30"))
        except Exception:
            self._ttl_minutes = 30

        self._template = os.getenv("SANDBOX_TEMPLATE") or None
        self._workdir = os.getenv("SANDBOX_WORKDIR", "/home/user/app").rstrip("/")

    def _is_expired(self, st: SandboxState) -> bool:
        if self._ttl_minutes <= 0:
            return False
        return _utcnow() - st.last_access_at > timedelta(minutes=self._ttl_minutes)

    async def _broadcast(self, sandbox_id: str, message: dict[str, Any]) -> None:
        clients = list(self._ws_clients.get(sandbox_id, set()))
        if not clients:
            return
        dead: list[Any] = []
        for ws in clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        if dead:
            for ws in dead:
                try:
                    self._ws_clients.get(sandbox_id, set()).discard(ws)
                except Exception:
                    pass

    async def register_ws(self, sandbox_id: str, websocket: Any) -> None:
        async with self._lock:
            self._ws_clients.setdefault(sandbox_id, set()).add(websocket)
            st = self._sandboxes.get(sandbox_id)
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

    async def create_or_reuse(self, artifact_id: str) -> SandboxState:
        if not is_valid_artifact_id(artifact_id):
            raise ValueError("Invalid artifactId")
        if Sandbox is None:
            raise RuntimeError("E2B SDK not installed (missing e2b-code-interpreter)")

        async with self._lock:
            existing_id = self._artifact_to_sandbox.get(artifact_id)
            if existing_id:
                st = self._sandboxes.get(existing_id)
                if st and not self._is_expired(st):
                    st.last_access_at = _utcnow()
                    return st

            # Create new sandbox
            sandbox_id = hashlib.sha1(f"{artifact_id}:{_utcnow().isoformat()}".encode()).hexdigest()[:18]
            st = SandboxState(
                sandbox_id=sandbox_id,
                artifact_id=artifact_id,
                created_at=_utcnow(),
                status="starting",
            )
            self._artifact_to_sandbox[artifact_id] = sandbox_id
            self._sandboxes[sandbox_id] = st

        # Create provider sandbox outside lock
        self._logger.info("SANDBOX_CREATE", extra={"artifactId": artifact_id, "sandboxId": sandbox_id})
        try:
            st.sandbox = Sandbox.create(template=self._template, timeout=None)
            st.status = "starting"
            st.last_error = None
            await self._broadcast(sandbox_id, {"type": "status", "status": "starting"})
            return st
        except Exception as exc:
            st.status = "error"
            st.last_error = f"Sandbox create failed: {exc}"
            await self._broadcast(sandbox_id, {"type": "status", "status": "error", "error": st.last_error})
            raise

    async def _ensure_alive(self, sandbox_id: str) -> SandboxState:
        if not is_valid_sandbox_id(sandbox_id):
            raise ValueError("Invalid sandboxId")
        async with self._lock:
            st = self._sandboxes.get(sandbox_id)
        if not st:
            raise KeyError("Sandbox not found")
        if self._is_expired(st):
            await self.stop(sandbox_id)
            raise KeyError("Sandbox expired")
        st.last_access_at = _utcnow()
        return st

    async def sync(self, sandbox_id: str, files: list[dict[str, str]], deleted: list[str]) -> None:
        st = await self._ensure_alive(sandbox_id)
        if not st.sandbox:
            raise RuntimeError("Sandbox provider handle missing")

        self._logger.info(
            "SANDBOX_SYNC",
            extra={"sandboxId": sandbox_id, "files": len(files or []), "deleted": len(deleted or [])},
        )

        # Normalize payload
        next_files: dict[str, str] = {}
        for entry in files or []:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            content = entry.get("content")
            if isinstance(path, str) and path.strip() and isinstance(content, str):
                next_files[path.strip().lstrip("/")] = content

        deleted_paths = [str(p).strip().lstrip("/") for p in (deleted or []) if str(p).strip()]

        fs = st.sandbox.files

        # Write files
        for rel_path, content in next_files.items():
            abs_path = f"{self._workdir}/{rel_path}" if self._workdir else rel_path
            dir_path = os.path.dirname(abs_path)
            if dir_path and dir_path != "/":
                try:
                    fs.make_dir(dir_path)
                except Exception:
                    pass
            fs.write(abs_path, content)

        # Remove deleted
        for rel_path in deleted_paths:
            abs_path = f"{self._workdir}/{rel_path}" if self._workdir else rel_path
            try:
                fs.remove(abs_path)
            except Exception:
                pass

        # Update state snapshot
        st.last_files.update(next_files)
        for rel_path in deleted_paths:
            st.last_files.pop(rel_path, None)

        st.last_sync_hash = _compute_sync_hash(st.last_files, [])

    async def start(self, sandbox_id: str) -> SandboxState:
        st = await self._ensure_alive(sandbox_id)
        if not st.sandbox:
            raise RuntimeError("Sandbox provider handle missing")

        # Clear previous error
        st.last_error = None
        st.status = "starting"
        st.preview_url = None
        await self._broadcast(sandbox_id, {"type": "status", "status": "starting"})

        files = st.last_files or {}
        has_pkg = "package.json" in files
        has_req = "requirements.txt" in files

        if not has_pkg and not has_req:
            st.status = "error"
            st.last_error = "No runtime detected: include package.json or requirements.txt"
            await self._broadcast(sandbox_id, {"type": "status", "status": "error", "error": st.last_error})
            return st

        if has_pkg:
            return await self._start_node(st)
        return await self._start_python(st)

    async def _start_node(self, st: SandboxState) -> SandboxState:
        sandbox_id = st.sandbox_id
        port = 3000

        await self._broadcast(sandbox_id, {"type": "log", "stream": "stdout", "line": "Installing deps..."})
        install = st.sandbox.commands.run(f"cd {self._workdir} && npm install")
        if int(getattr(install, "exit_code", 1)) != 0:
            st.status = "error"
            st.last_error = (install.stderr or install.stdout or "npm install failed").strip()
            await self._broadcast(sandbox_id, {"type": "log", "stream": "stderr", "line": st.last_error})
            await self._broadcast(sandbox_id, {"type": "status", "status": "error", "error": st.last_error})
            return st

        pkg_text = st.last_files.get("package.json") or "{}"
        try:
            pkg = json.loads(pkg_text)
        except Exception:
            pkg = {}
        scripts = pkg.get("scripts") if isinstance(pkg, dict) else None
        scripts = scripts if isinstance(scripts, dict) else {}

        if "dev" in scripts:
            cmd = f"cd {self._workdir} && npm run dev -- --host 0.0.0.0 --port {port}"
        elif "start" in scripts:
            cmd = f"cd {self._workdir} && HOST=0.0.0.0 PORT={port} npm start"
        else:
            st.status = "error"
            st.last_error = "package.json missing scripts.dev or scripts.start"
            await self._broadcast(sandbox_id, {"type": "status", "status": "error", "error": st.last_error})
            return st

        await self._broadcast(sandbox_id, {"type": "log", "stream": "stdout", "line": "Starting dev server..."})
        try:
            st.sandbox.commands.run(cmd, background=True)
        except TypeError:
            # Older SDKs may not support background flag.
            st.sandbox.commands.run(cmd)

        # Best-effort small delay, then expose preview
        await asyncio.sleep(2)
        try:
            host = st.sandbox.get_host(port)
            st.preview_url = host if str(host).startswith("http") else f"https://{host}"
        except Exception as exc:
            st.status = "error"
            st.last_error = f"Failed to get preview URL: {exc}"
            await self._broadcast(sandbox_id, {"type": "status", "status": "error", "error": st.last_error})
            return st

        st.status = "running"
        await self._broadcast(sandbox_id, {"type": "status", "status": "running", "previewUrl": st.preview_url})
        return st

    async def _start_python(self, st: SandboxState) -> SandboxState:
        sandbox_id = st.sandbox_id
        port = 8000

        await self._broadcast(sandbox_id, {"type": "log", "stream": "stdout", "line": "Installing deps..."})
        install = st.sandbox.commands.run(f"cd {self._workdir} && python -m pip install -r requirements.txt")
        if int(getattr(install, "exit_code", 1)) != 0:
            st.status = "error"
            st.last_error = (install.stderr or install.stdout or "pip install failed").strip()
            await self._broadcast(sandbox_id, {"type": "log", "stream": "stderr", "line": st.last_error})
            await self._broadcast(sandbox_id, {"type": "status", "status": "error", "error": st.last_error})
            return st

        main_py = st.last_files.get("main.py")
        if not main_py or "FastAPI" not in main_py:
            st.status = "error"
            st.last_error = "Expected main.py with a FastAPI app (e.g., app = FastAPI())"
            await self._broadcast(sandbox_id, {"type": "status", "status": "error", "error": st.last_error})
            return st

        await self._broadcast(sandbox_id, {"type": "log", "stream": "stdout", "line": "Starting server..."})
        cmd = f"cd {self._workdir} && uvicorn main:app --host 0.0.0.0 --port {port}"
        try:
            st.sandbox.commands.run(cmd, background=True)
        except TypeError:
            st.sandbox.commands.run(cmd)

        await asyncio.sleep(2)
        try:
            host = st.sandbox.get_host(port)
            st.preview_url = host if str(host).startswith("http") else f"https://{host}"
        except Exception as exc:
            st.status = "error"
            st.last_error = f"Failed to get preview URL: {exc}"
            await self._broadcast(sandbox_id, {"type": "status", "status": "error", "error": st.last_error})
            return st

        st.status = "running"
        await self._broadcast(sandbox_id, {"type": "status", "status": "running", "previewUrl": st.preview_url})
        return st

    async def status(self, sandbox_id: str) -> SandboxState:
        st = await self._ensure_alive(sandbox_id)
        return st

    async def stop(self, sandbox_id: str) -> None:
        async with self._lock:
            st = self._sandboxes.get(sandbox_id)

        if not st:
            return

        self._logger.info("SANDBOX_STOP", extra={"sandboxId": sandbox_id, "artifactId": st.artifact_id})
        try:
            if st.sandbox is not None:
                try:
                    st.sandbox.kill()
                except Exception:
                    try:
                        st.sandbox.close()
                    except Exception:
                        pass
        finally:
            async with self._lock:
                self._sandboxes.pop(sandbox_id, None)
                self._ws_clients.pop(sandbox_id, None)
                if self._artifact_to_sandbox.get(st.artifact_id) == sandbox_id:
                    self._artifact_to_sandbox.pop(st.artifact_id, None)

            await self._broadcast(sandbox_id, {"type": "status", "status": "error", "error": "stopped"})


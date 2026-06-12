from __future__ import annotations

import asyncio
import os
import posixpath
from typing import Any

from logs.logging_config import get_core_logger
from mozaiksai.core.ports.sandbox import SandboxRunResult, SandboxSessionInfo

logger = get_core_logger("e2b_sandbox")

try:
    from e2b_code_interpreter import Sandbox
except Exception:  # pragma: no cover
    Sandbox = None  # type: ignore[assignment]


class E2BSandboxAdapter:
    """Async adapter over the synchronous E2B Python SDK."""

    def __init__(
        self,
        *,
        default_template: str | None = None,
        default_timeout_seconds: int | None = None,
    ) -> None:
        self._default_template = default_template or os.getenv("E2B_TEMPLATE") or None
        raw_timeout = default_timeout_seconds if default_timeout_seconds is not None else os.getenv("E2B_TIMEOUT")
        self._default_timeout_seconds = int(raw_timeout) if raw_timeout else 300

    def _require_sdk(self):
        if Sandbox is None:
            raise RuntimeError("e2b_code_interpreter is not installed; install mozaiks[e2b] to enable sandbox operations")
        return Sandbox

    def _session_info(
        self,
        sandbox: Any,
        *,
        preview_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SandboxSessionInfo:
        info = dict(metadata or {})
        host = getattr(sandbox, "sandbox_domain", None)
        if host:
            info.setdefault("sandbox_domain", host)
        return SandboxSessionInfo(
            session_id=str(getattr(sandbox, "sandbox_id", "") or ""),
            provider="e2b",
            preview_url=preview_url,
            metadata=info,
        )

    async def _connect_sandbox(self, session_id: str, timeout_seconds: int | None = None):
        sandbox_cls = self._require_sdk()
        timeout = timeout_seconds if timeout_seconds is not None else self._default_timeout_seconds
        return await asyncio.to_thread(sandbox_cls.connect, session_id, timeout=timeout)

    @staticmethod
    def _resolve_path(path: str, cwd: str | None) -> str:
        if not cwd:
            return path
        if path.startswith("/"):
            return path
        return posixpath.join(cwd, path)

    async def create_session(
        self,
        *,
        template: str | None = None,
        timeout_seconds: int | None = None,
        metadata: dict[str, str] | None = None,
        envs: dict[str, str] | None = None,
    ) -> SandboxSessionInfo:
        sandbox_cls = self._require_sdk()
        timeout = timeout_seconds if timeout_seconds is not None else self._default_timeout_seconds
        sandbox = await asyncio.to_thread(
            sandbox_cls.create,
            template=template or self._default_template,
            timeout=timeout,
            metadata=metadata,
            envs=envs,
        )
        return self._session_info(sandbox, metadata={"template": template or self._default_template or "default"})

    async def connect(
        self,
        *,
        session_id: str,
        timeout_seconds: int | None = None,
    ) -> SandboxSessionInfo:
        sandbox = await self._connect_sandbox(session_id, timeout_seconds=timeout_seconds)
        return self._session_info(sandbox)

    async def write_files(
        self,
        *,
        session_id: str,
        files: dict[str, str | bytes],
        cwd: str | None = None,
    ) -> dict[str, Any]:
        sandbox = await self._connect_sandbox(session_id)
        written = []
        for path, content in files.items():
            resolved = self._resolve_path(path, cwd)
            await asyncio.to_thread(sandbox.files.write, resolved, content)
            written.append(resolved)
        return {"written": written, "count": len(written)}

    async def read_file(
        self,
        *,
        session_id: str,
        path: str,
        as_bytes: bool = False,
    ) -> str | bytes:
        sandbox = await self._connect_sandbox(session_id)
        file_format = "bytes" if as_bytes else "text"
        result = await asyncio.to_thread(sandbox.files.read, path, file_format)
        return bytes(result) if as_bytes else str(result)

    async def run_command(
        self,
        *,
        session_id: str,
        command: str,
        cwd: str | None = None,
        envs: dict[str, str] | None = None,
        background: bool = False,
        timeout_seconds: float | None = 60.0,
    ) -> SandboxRunResult:
        sandbox = await self._connect_sandbox(session_id)
        try:
            result = await asyncio.to_thread(
                sandbox.commands.run,
                cmd=command,
                background=background,
                envs=envs,
                cwd=cwd,
                timeout=timeout_seconds,
            )
            if background:
                return SandboxRunResult(success=True, process_id=getattr(result, "pid", None))
            exit_code = getattr(result, "exit_code", 0)
            exit_code = int(0 if exit_code is None else exit_code)
            return SandboxRunResult(
                success=exit_code == 0 and not getattr(result, "error", None),
                exit_code=exit_code,
                stdout=str(getattr(result, "stdout", "") or ""),
                stderr=str(getattr(result, "stderr", "") or ""),
                error=getattr(result, "error", None),
            )
        except Exception as exc:
            exit_code = int(getattr(exc, "exit_code", 1) or 1)
            return SandboxRunResult(
                success=False,
                exit_code=exit_code,
                stdout=str(getattr(exc, "stdout", "") or ""),
                stderr=str(getattr(exc, "stderr", "") or ""),
                error=str(getattr(exc, "error", None) or exc),
            )

    async def get_preview_url(
        self,
        *,
        session_id: str,
        port: int,
    ) -> str | None:
        sandbox = await self._connect_sandbox(session_id)
        host = await asyncio.to_thread(sandbox.get_host, port)
        if not host:
            return None
        scheme = "http" if bool(getattr(sandbox.connection_config, "debug", False)) else "https"
        return f"{scheme}://{host}"

    async def extend_session(
        self,
        *,
        session_id: str,
        timeout_seconds: int,
    ) -> SandboxSessionInfo:
        sandbox = await self._connect_sandbox(session_id)
        await asyncio.to_thread(sandbox.set_timeout, timeout_seconds)
        return self._session_info(sandbox)

    async def terminate_session(self, *, session_id: str) -> bool:
        sandbox = await self._connect_sandbox(session_id)
        await asyncio.to_thread(sandbox.kill)
        return True

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": "e2b",
            "supports_preview": True,
            "supports_file_io": True,
            "supports_command_execution": True,
            "supports_timeout_extension": True,
        }


_sandbox_adapter: E2BSandboxAdapter | None = None


def get_e2b_sandbox() -> E2BSandboxAdapter:
    global _sandbox_adapter
    if _sandbox_adapter is None:
        _sandbox_adapter = E2BSandboxAdapter()
    return _sandbox_adapter


__all__ = ["E2BSandboxAdapter", "get_e2b_sandbox"]
from __future__ import annotations

import asyncio
import os
import posixpath
import re
from datetime import UTC, datetime
from typing import Any

from logs.logging_config import get_core_logger
from mozaiksai.core.ports.sandbox import SandboxRunResult, SandboxSessionInfo

logger = get_core_logger("e2b_sandbox")
_SEALED_PURPOSE = "sealed_candidate_preview"
_ORDINARY_PURPOSES = frozenset({"artifact_preview", "app_validation"})
_PINNED_BUILD_REF = re.compile(
    r"^(?:[A-Za-z0-9][A-Za-z0-9._-]*/)?[A-Za-z0-9][A-Za-z0-9._-]*:"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _public_traffic_allowed(network: Any) -> bool | None:
    if isinstance(network, dict):
        return network.get("allow_public_traffic")
    return None


def _require_running_sealed_session(details: Any) -> None:
    metadata = getattr(details, "metadata", None)
    if isinstance(metadata, dict) and metadata.get("purpose") in _ORDINARY_PURPOSES:
        return
    if getattr(details, "state", None) != "running":
        raise RuntimeError("Sealed E2B session is not running; refusing to resume it")
    if (
        not isinstance(metadata, dict)
        or metadata.get("purpose") != _SEALED_PURPOSE
        or getattr(details, "allow_internet_access", None) is not False
        or _public_traffic_allowed(getattr(details, "network", None)) is not False
        or not isinstance(getattr(details, "lifecycle", None), dict)
        or details.lifecycle.get("on_timeout") != "kill"
        or details.lifecycle.get("auto_resume") is not False
    ):
        raise RuntimeError("E2B did not confirm sealed preview isolation")


try:
    from e2b.exceptions import NotFoundException
    from e2b_code_interpreter import Sandbox
except Exception:  # pragma: no cover
    Sandbox = None  # type: ignore[assignment]
    _NOT_FOUND_ERRORS: tuple[type[Exception], ...] = ()
else:
    _NOT_FOUND_ERRORS = (NotFoundException,)


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
        self._sessions: dict[str, Any] = {}

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
        if host and info.get("purpose") != _SEALED_PURPOSE:
            info.setdefault("sandbox_domain", host)
        return SandboxSessionInfo(
            session_id=str(getattr(sandbox, "sandbox_id", "") or ""),
            provider="e2b",
            preview_url=preview_url,
            metadata=info,
        )

    async def _connected_session_info(self, sandbox: Any) -> SandboxSessionInfo:
        details = await asyncio.to_thread(sandbox.get_info)
        metadata = getattr(details, "metadata", None)
        # Unknown provider metadata must not reveal a sealed session host.
        purpose = metadata.get("purpose") if isinstance(metadata, dict) else None
        return self._session_info(
            sandbox, metadata=None if purpose in _ORDINARY_PURPOSES else {"purpose": _SEALED_PURPOSE},
        )

    async def _connect_sandbox(self, session_id: str, timeout_seconds: int | None = None):
        if session_id in self._sessions:
            sandbox = self._sessions[session_id]
            if timeout_seconds is not None:
                await asyncio.to_thread(sandbox.set_timeout, timeout_seconds)
            return sandbox
        sandbox_cls = self._require_sdk()
        info = await asyncio.to_thread(sandbox_cls.get_info, session_id)
        _require_running_sealed_session(info)
        if timeout_seconds is None:
            # SDK connect renews the lifetime; preserve the provider's deadline.
            timeout_seconds = max(1, int((info.end_at - datetime.now(UTC)).total_seconds()))
        sandbox = await asyncio.to_thread(sandbox_cls.connect, session_id, timeout=timeout_seconds)
        metadata = getattr(info, "metadata", None)
        if not isinstance(metadata, dict) or metadata.get("purpose") not in _ORDINARY_PURPOSES:
            _require_running_sealed_session(await asyncio.to_thread(sandbox.get_info))
        self._sessions[session_id] = sandbox
        return sandbox

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
        request_metadata = dict(metadata or {})
        purpose = request_metadata.setdefault("purpose", "artifact_preview")
        if purpose not in _ORDINARY_PURPOSES and purpose != _SEALED_PURPOSE:
            raise ValueError("Unsupported E2B sandbox purpose")
        sealed = purpose == _SEALED_PURPOSE
        if sealed:
            if not template or not _PINNED_BUILD_REF.fullmatch(template):
                raise ValueError("Sealed E2B preview requires an exact template build reference")
            if envs:
                raise ValueError("Sealed E2B preview cannot receive environment values")
        isolation = (
            {
                "allow_internet_access": False,
                "network": {"allow_public_traffic": False},
                "lifecycle": {"on_timeout": "kill", "auto_resume": False},
            }
            if sealed else {}
        )
        creation = asyncio.create_task(asyncio.to_thread(
            sandbox_cls.create,
            template=template or self._default_template,
            timeout=timeout,
            metadata=request_metadata,
            envs=envs,
            **isolation,
        ))
        try:
            sandbox = await asyncio.shield(creation)
        except asyncio.CancelledError:
            try:
                sandbox = await creation
                await asyncio.to_thread(sandbox.kill)
            except Exception as exc:
                logger.error("cancelled_sandbox_cleanup_failed exception=%s", type(exc).__name__)
            raise
        if sealed:
            try:
                details = await asyncio.to_thread(sandbox.get_info)
                network = getattr(details, "network", None)
                lifecycle = getattr(details, "lifecycle", None)
                if (
                    getattr(details, "allow_internet_access", None) is not False
                    or _public_traffic_allowed(network) is not False
                    or not isinstance(lifecycle, dict)
                    or lifecycle.get("on_timeout") != "kill"
                    or lifecycle.get("auto_resume") is not False
                    or getattr(details, "metadata", {}).get("purpose") != _SEALED_PURPOSE
                    or not getattr(details, "template_id", None)
                ):
                    raise RuntimeError("E2B did not confirm sealed preview isolation")
            except (Exception, asyncio.CancelledError):
                await asyncio.to_thread(sandbox.kill)
                raise
            session_metadata = {"purpose": _SEALED_PURPOSE, "template_id": details.template_id}
        else:
            session_metadata = {}
        self._sessions[sandbox.sandbox_id] = sandbox
        return self._session_info(sandbox, metadata={
            "template": template or self._default_template or "default",
            **session_metadata,
        })

    async def connect(
        self,
        *,
        session_id: str,
        timeout_seconds: int | None = None,
    ) -> SandboxSessionInfo:
        sandbox = await self._connect_sandbox(session_id, timeout_seconds=timeout_seconds)
        return await self._connected_session_info(sandbox)

    async def write_files(
        self,
        *,
        session_id: str,
        files: dict[str, str | bytes],
        cwd: str | None = None,
    ) -> dict[str, Any]:
        sandbox = await self._connect_sandbox(session_id)
        metadata = getattr(await asyncio.to_thread(sandbox.get_info), "metadata", None)
        if not isinstance(metadata, dict) or metadata.get("purpose") not in _ORDINARY_PURPOSES:
            raise ValueError("Sealed E2B preview does not accept mutable file writes")
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
        if envs:
            details = await asyncio.to_thread(sandbox.get_info)
            metadata = getattr(details, "metadata", None)
            if not isinstance(metadata, dict) or metadata.get("purpose") not in _ORDINARY_PURPOSES:
                raise ValueError("Sealed E2B preview cannot receive command environment values")
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
        details = await asyncio.to_thread(sandbox.get_info)
        metadata = getattr(details, "metadata", None)
        network = getattr(details, "network", None)
        if not isinstance(metadata, dict) or metadata.get("purpose") not in _ORDINARY_PURPOSES or (
            network is not None and _public_traffic_allowed(network) is False
        ):
            return None
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
        return await self._connected_session_info(sandbox)

    async def terminate_session(self, *, session_id: str) -> bool:
        try:
            # Reconnecting can resume a paused sandbox; teardown must kill by ID.
            await asyncio.to_thread(self._require_sdk().kill, session_id)
        except _NOT_FOUND_ERRORS:
            pass
        self._sessions.pop(session_id, None)
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

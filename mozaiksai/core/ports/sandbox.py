from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class SandboxSessionInfo:
    session_id: str
    provider: str
    preview_url: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SandboxRunResult:
    success: bool
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None
    process_id: Optional[int] = None


@runtime_checkable
class SandboxPort(Protocol):
    async def create_session(
        self,
        *,
        template: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        metadata: Optional[Dict[str, str]] = None,
        envs: Optional[Dict[str, str]] = None,
    ) -> SandboxSessionInfo:
        ...

    async def connect(
        self,
        *,
        session_id: str,
        timeout_seconds: Optional[int] = None,
    ) -> SandboxSessionInfo:
        ...

    async def write_files(
        self,
        *,
        session_id: str,
        files: Dict[str, str | bytes],
        cwd: Optional[str] = None,
    ) -> Dict[str, Any]:
        ...

    async def read_file(
        self,
        *,
        session_id: str,
        path: str,
        as_bytes: bool = False,
    ) -> str | bytes:
        ...

    async def run_command(
        self,
        *,
        session_id: str,
        command: str,
        cwd: Optional[str] = None,
        envs: Optional[Dict[str, str]] = None,
        background: bool = False,
        timeout_seconds: Optional[float] = 60.0,
    ) -> SandboxRunResult:
        ...

    async def get_preview_url(
        self,
        *,
        session_id: str,
        port: int,
    ) -> Optional[str]:
        ...

    async def extend_session(
        self,
        *,
        session_id: str,
        timeout_seconds: int,
    ) -> SandboxSessionInfo:
        ...

    async def terminate_session(self, *, session_id: str) -> bool:
        ...

    def capabilities(self) -> Dict[str, Any]:
        ...


__all__ = ["SandboxPort", "SandboxRunResult", "SandboxSessionInfo"]
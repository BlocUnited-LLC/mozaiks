from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class SandboxSessionInfo:
    session_id: str
    provider: str
    preview_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SandboxRunResult:
    success: bool
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    process_id: int | None = None


@runtime_checkable
class SandboxPort(Protocol):
    async def create_session(
        self,
        *,
        template: str | None = None,
        timeout_seconds: int | None = None,
        metadata: dict[str, str] | None = None,
        envs: dict[str, str] | None = None,
    ) -> SandboxSessionInfo:
        ...

    async def connect(
        self,
        *,
        session_id: str,
        timeout_seconds: int | None = None,
    ) -> SandboxSessionInfo:
        ...

    async def write_files(
        self,
        *,
        session_id: str,
        files: dict[str, str | bytes],
        cwd: str | None = None,
    ) -> dict[str, Any]:
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
        cwd: str | None = None,
        envs: dict[str, str] | None = None,
        background: bool = False,
        timeout_seconds: float | None = 60.0,
    ) -> SandboxRunResult:
        ...

    async def get_preview_url(
        self,
        *,
        session_id: str,
        port: int,
    ) -> str | None:
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

    def capabilities(self) -> dict[str, Any]:
        ...


__all__ = ["SandboxPort", "SandboxRunResult", "SandboxSessionInfo"]
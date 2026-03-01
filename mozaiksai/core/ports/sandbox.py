"""Sandbox execution port protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from mozaiksai.core.contracts import SandboxExecutionResult


@runtime_checkable
class SandboxPort(Protocol):
    """Execute untrusted workload code in an isolated sandbox."""

    async def execute_python(
        self,
        *,
        run_id: str,
        task_id: str,
        tool_id: str,
        source_files: dict[str, str],
        requirements: list[str],
        input_json: dict[str, object],
        env: dict[str, str],
        timeout_seconds: int = 60,
    ) -> SandboxExecutionResult:
        ...

    async def execute_node(
        self,
        *,
        run_id: str,
        task_id: str,
        tool_id: str,
        source_files: dict[str, str],
        npm_packages: list[str],
        input_json: dict[str, object],
        env: dict[str, str],
        timeout_seconds: int = 60,
    ) -> SandboxExecutionResult:
        ...


__all__ = ["SandboxPort"]

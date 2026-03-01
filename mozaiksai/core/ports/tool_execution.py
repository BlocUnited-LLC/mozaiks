"""Tool execution port protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from mozaiksai.core.contracts.tools import ToolExecutionRequest, ToolExecutionResult


@runtime_checkable
class ToolExecutionPort(Protocol):
    """Execute runtime tools deterministically by contract."""

    async def execute_tool(
        self,
        request: ToolExecutionRequest,
        *,
        timeout_seconds: int = 60,
    ) -> ToolExecutionResult:
        ...


__all__ = ["ToolExecutionPort"]

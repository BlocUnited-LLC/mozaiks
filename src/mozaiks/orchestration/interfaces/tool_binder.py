"""Tool binding and invocation contract."""

from __future__ import annotations

from typing import Protocol, Sequence

from ..domain.tool_spec import ToolCall, ToolResult, ToolSpec
from ..domain.runtime_context import RuntimeContext


class ToolBinder(Protocol):
    """Binds tool specs and executes calls."""

    async def bind(self, tools: Sequence[ToolSpec]) -> None:
        """Bind allowed tools for the current execution scope."""
        ...

    async def invoke(
        self,
        call: ToolCall,
        *,
        run_id: str,
        runtime_context: RuntimeContext | None = None,
    ) -> ToolResult:
        """Execute a tool call and return normalized result."""
        ...

from __future__ import annotations

from typing import Any, Protocol

from .contracts import ControlPlaneToolCall, ControlPlaneToolResult


class ChangeClassifierPort(Protocol):
    async def classify(self, **kwargs: Any) -> Any: ...


class RoutingPolicyPort(Protocol):
    async def route(self, request: Any) -> Any: ...


class CodingWorkerPort(Protocol):
    async def execute(self, request: Any) -> Any: ...


class ControlPlaneToolExecutorPort(Protocol):
    async def execute_tool(
        self,
        call: ControlPlaneToolCall,
        *,
        context: dict[str, Any] | None = None,
    ) -> ControlPlaneToolResult: ...

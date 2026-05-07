from __future__ import annotations

from typing import Any, Protocol

from .contracts import (
    CodingWorkerRequest,
    CodingWorkerResult,
    ControlPlaneToolCall,
    ControlPlaneToolContext,
    ControlPlaneToolResult,
    HarnessDecision,
    ScopeProposal,
)


class ChangeClassifierPort(Protocol):
    async def classify(self, **kwargs: Any) -> Any: ...


class RoutingPolicyPort(Protocol):
    async def route(self, request: Any) -> Any: ...


class CodingWorkerPort(Protocol):
    async def execute(self, request: CodingWorkerRequest) -> CodingWorkerResult: ...


class ScopeProposalPort(Protocol):
    async def propose(self, **kwargs: Any) -> ScopeProposal: ...


class HarnessDecisionPolicyPort(Protocol):
    def decide(self, **kwargs: Any) -> HarnessDecision: ...


class ControlPlaneToolExecutorPort(Protocol):
    async def execute_tool(
        self,
        call: ControlPlaneToolCall,
        *,
        context: ControlPlaneToolContext | dict[str, Any] | None = None,
    ) -> ControlPlaneToolResult: ...

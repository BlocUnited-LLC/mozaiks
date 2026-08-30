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
    StagedPatchProposal,
)


class ChangeClassifierPort(Protocol):
    async def classify(self, **kwargs: Any) -> Any: ...


class RoutingPolicyPort(Protocol):
    async def route(self, request: Any) -> Any: ...


class CodingWorkerPort(Protocol):
    async def execute(self, request: CodingWorkerRequest) -> CodingWorkerResult: ...


class CodingExecutionProvider(Protocol):
    """Produces a scoped patch proposal for one refinement coding request.

    Sits one level below :class:`CodingWorkerPort`: the coding worker owns
    eligibility, validation, artifact persistence, and the checkpoint result
    shape, while a provider owns only the production of staged file changes
    within the explicitly scoped inputs. Providers never widen scope, never
    write live app source, and never touch the artifact store — their sole
    output is the provider-neutral :class:`StagedPatchProposal`.
    """

    @property
    def provider_id(self) -> str: ...

    async def execute(self, request: CodingWorkerRequest) -> StagedPatchProposal: ...


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

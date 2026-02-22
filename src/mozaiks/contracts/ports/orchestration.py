"""Runtime orchestration port protocol."""

from __future__ import annotations

from typing import Any, AsyncIterator, Protocol, runtime_checkable

from mozaiks.contracts import DomainEvent, ResumeRequest, RunRequest


@runtime_checkable
class OrchestrationPort(Protocol):
    """Versioned orchestration contract for runtime engines."""

    async def run(self, request: RunRequest) -> AsyncIterator[DomainEvent]:
        ...

    async def resume(self, request: ResumeRequest) -> AsyncIterator[DomainEvent]:
        ...

    async def cancel(self, run_id: str) -> None:
        ...

    def capabilities(self) -> dict[str, Any]:
        ...


__all__ = ["OrchestrationPort"]

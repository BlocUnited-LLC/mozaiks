"""AI workflow runner contract."""

from __future__ import annotations

from typing import AsyncIterator, Protocol

from ..domain.events import CanonicalEvent
from ..domain.requests import ResumeRequest, RunRequest


class AIWorkflowRunner(Protocol):
    """Public engine API consumed by runtime hosts."""

    async def run(self, request: RunRequest) -> AsyncIterator[CanonicalEvent]:
        """Start a new run and stream canonical events."""
        ...

    async def resume(self, request: ResumeRequest) -> AsyncIterator[CanonicalEvent]:
        """Resume a run from a previously persisted checkpoint."""
        ...

    async def cancel(self, run_id: str) -> None:
        """Cancel an active run."""
        ...

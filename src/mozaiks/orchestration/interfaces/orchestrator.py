"""Orchestrator contract."""

from __future__ import annotations

from typing import AsyncIterator, Protocol

from ..domain.dag import TaskDAG
from ..domain.events import CanonicalEvent
from ..domain.runtime_context import RuntimeContext
from ..domain.state import RunCheckpoint


class Orchestrator(Protocol):
    """Vendor-neutral orchestration surface."""

    async def start(
        self,
        plan: TaskDAG,
        run_id: str,
        initial_input: dict[str, object] | None = None,
        metadata: dict[str, object] | None = None,
        runtime_context: RuntimeContext | None = None,
    ) -> AsyncIterator[CanonicalEvent]:
        """Execute a new run from task graph roots."""
        ...

    async def resume(
        self,
        plan: TaskDAG,
        checkpoint: RunCheckpoint,
        runtime_context: RuntimeContext | None = None,
    ) -> AsyncIterator[CanonicalEvent]:
        """Resume run execution from saved checkpoint state."""
        ...

"""Persistence and sink ports."""

from __future__ import annotations

from typing import Protocol

from ..domain.events import CanonicalEvent
from ..domain.state import RunCheckpoint


class CheckpointStorePort(Protocol):
    """Persists and retrieves run checkpoints."""

    async def save(self, checkpoint: RunCheckpoint) -> None:
        ...

    async def load(self, run_id: str) -> RunCheckpoint | None:
        ...


class EventSinkPort(Protocol):
    """Stores canonical events."""

    async def append(self, event: CanonicalEvent) -> None:
        ...

    async def list(self, run_id: str) -> list[CanonicalEvent]:
        ...

"""In-memory port implementations and stream framing."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from ..domain.events import CanonicalEvent
from ..domain.state import RunCheckpoint
from ..interfaces.ports import CheckpointStorePort, EventSinkPort
from ..interfaces.stream_adapter import StreamAdapter


class InMemoryCheckpointStore(CheckpointStorePort):
    """Simple checkpoint store for local runs and tests."""

    def __init__(self) -> None:
        self._checkpoints: dict[str, RunCheckpoint] = {}

    async def save(self, checkpoint: RunCheckpoint) -> None:
        self._checkpoints[checkpoint.run_id] = deepcopy(checkpoint)

    async def load(self, run_id: str) -> RunCheckpoint | None:
        checkpoint = self._checkpoints.get(run_id)
        if checkpoint is None:
            return None
        return deepcopy(checkpoint)


class InMemoryEventSink(EventSinkPort):
    """Simple event sink for tests and local usage."""

    def __init__(self) -> None:
        self._events: dict[str, list[CanonicalEvent]] = {}

    async def append(self, event: CanonicalEvent) -> None:
        self._events.setdefault(event.run_id, []).append(event)

    async def list(self, run_id: str) -> list[CanonicalEvent]:
        return list(self._events.get(run_id, []))


@dataclass(slots=True)
class JsonStreamAdapter(StreamAdapter):
    """Default stream adapter that exposes event dicts."""

    def to_frame(self, event: CanonicalEvent) -> dict[str, object]:
        return event.to_dict()

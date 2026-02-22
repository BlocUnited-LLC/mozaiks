from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from mozaiks.contracts import EventEnvelope


@dataclass(frozen=True)
class RunRecordView:
    run_id: str
    created_at: datetime
    status: str
    workflow_name: str
    workflow_version: str
    metadata: dict[str, Any]
    last_seq: int = 0


@dataclass(frozen=True)
class PersistedEvent:
    seq: int
    event: EventEnvelope


@dataclass(frozen=True)
class ArtifactRecordView:
    artifact_id: str
    run_id: str
    seq: int | None
    artifact_type: str
    uri: str
    checksum: str
    version: str
    media_type: str | None
    content_base64: str | None
    metadata: dict[str, Any]
    created_at: datetime


class EventStorePort(Protocol):
    async def create_run(
        self,
        *,
        run_id: str,
        workflow_name: str,
        workflow_version: str,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> RunRecordView: ...

    async def get_run(self, run_id: str) -> RunRecordView | None: ...

    async def update_run_status(self, *, run_id: str, status: str) -> RunRecordView: ...

    async def append_event(self, *, run_id: str, event: EventEnvelope) -> PersistedEvent: ...

    async def list_events(self, *, run_id: str, after_seq: int = 0, limit: int = 100) -> list[PersistedEvent]: ...

    async def get_latest_event(self, *, run_id: str) -> PersistedEvent | None: ...

    async def get_artifact(
        self,
        *,
        artifact_id: str | None = None,
        uri: str | None = None,
        checksum: str | None = None,
    ) -> ArtifactRecordView | None: ...


class EventSinkPort(Protocol):
    async def emit(self, event: EventEnvelope | dict[str, Any]) -> PersistedEvent: ...


class CheckpointStorePort(Protocol):
    async def save_checkpoint(self, *, run_id: str, checkpoint_key: str, payload: dict[str, Any]) -> None: ...

    async def load_checkpoint(self, *, run_id: str, checkpoint_key: str) -> dict[str, Any] | None: ...

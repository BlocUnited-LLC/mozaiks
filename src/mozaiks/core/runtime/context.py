from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Mapping, Sequence

from mozaiks.core.persistence import EventStorePort
from mozaiks.core.context import RuntimeContext
from mozaiks.contracts import EventEnvelope
from mozaiks.contracts.ports import (
    ArtifactPort,
    ClockPort,
    ControlPlanePort,
    LedgerPort,
    LoggerPort,
    OrchestrationPort,
    SandboxPort,
    SecretsPort,
    ToolExecutionPort,
)


class EventStoreLedger(LedgerPort):
    """Ledger adapter backed by the core event store.
    
    Implements kernel's LedgerPort for event storage (append/read EventEnvelope).
    """

    def __init__(self, store: EventStorePort) -> None:
        self._store = store

    async def append(self, envelope: EventEnvelope) -> str:
        persisted = await self._store.append_event(run_id=envelope.run_id, event=envelope)
        return str(persisted.seq)

    async def read(
        self,
        *,
        correlation_id: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> Sequence[EventEnvelope]:
        target_run_id = run_id or correlation_id
        if target_run_id is None:
            return []
        rows = await self._store.list_events(run_id=target_run_id, after_seq=0, limit=max(1, limit))
        return [item.event for item in rows]


class EventStoreControlPlane(ControlPlanePort):
    """Control-plane adapter backed by run records in the core event store."""

    def __init__(self, store: EventStorePort) -> None:
        self._store = store

    async def get_run_state(self, run_id: str) -> dict[str, object] | None:
        run = await self._store.get_run(run_id)
        if run is None:
            return None
        return {"run_id": run.run_id, "status": run.status, "metadata": dict(run.metadata)}

    async def notify_run_state(
        self,
        run_id: str,
        state: str,
        *,
        metadata: dict[str, object] | None = None,
    ) -> None:
        await self._store.update_run_status(run_id=run_id, status=state)


class SystemClock(ClockPort):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class StandardLogger(LoggerPort):
    def __init__(self, name: str) -> None:
        self._logger = logging.getLogger(name)

    def debug(self, message: str, **context: object) -> None:
        self._logger.debug(message, extra={"context": context})

    def info(self, message: str, **context: object) -> None:
        self._logger.info(message, extra={"context": context})

    def warning(self, message: str, **context: object) -> None:
        self._logger.warning(message, extra={"context": context})

    def error(self, message: str, **context: object) -> None:
        self._logger.error(message, extra={"context": context})


@dataclass(slots=True)
class CoreRuntimeContext(RuntimeContext):
    run_id: str
    workflow_name: str
    workflow_version: str
    ledger: LedgerPort
    tenant_id: str | None = None
    control_plane: ControlPlanePort | None = None
    artifacts: ArtifactPort | None = None
    clock: ClockPort | None = None
    logger: LoggerPort | None = None
    orchestrator: OrchestrationPort | None = None
    tool_executor: ToolExecutionPort | None = None
    sandbox: SandboxPort | None = None
    secrets: SecretsPort | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

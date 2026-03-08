"""RunSupervisor — owns the run lifecycle.

The RunSupervisor is the central coordinator for workflow execution.
It manages the full run lifecycle (start, pause, resume, cancel) and
routes requests to workers via the WorkerRegistry.

Design rules
------------
* Single entry point for all run operations
* Delegates execution to capability-specific workers
* Collects DomainEvents and forwards to EventBus
* Tracks active runs for health monitoring
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator

from mozaiksai.contracts import (
    DomainEvent,
    EVENT_SCHEMA_VERSION,
    ResumeRequest,
    RunRequest,
)
from mozaiksai.runtime.execution.event_bus import EventBus, get_event_bus
from mozaiksai.runtime.execution.capability_registry import (
    CapabilityRegistry,
    get_capability_registry,
)
from mozaiksai.workers.registry import WorkerRegistry, get_worker_registry

logger = logging.getLogger(__name__)


class RunState(str, Enum):
    """Possible states for a run."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class RunContext:
    """Tracks state for an active run."""

    run_id: str
    workflow_name: str
    capability: str
    state: RunState
    started_at: datetime
    completed_at: datetime | None = None
    last_seq: int = 0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class RunSupervisor:
    """Coordinates workflow execution across workers.

    The RunSupervisor:
    1. Receives RunRequest from transport layer
    2. Determines required capability from workflow metadata
    3. Routes to appropriate worker via WorkerRegistry
    4. Collects DomainEvents and publishes to EventBus
    5. Tracks run state for lifecycle management

    Usage::

        supervisor = RunSupervisor()
        async for event in supervisor.start_run(request):
            await transport.send(event)

        # Or cancel an active run
        await supervisor.cancel_run(run_id)
    """

    def __init__(
        self,
        worker_registry: WorkerRegistry | None = None,
        capability_registry: CapabilityRegistry | None = None,
        event_bus: EventBus | None = None,
    ):
        """Initialize the RunSupervisor.

        Parameters
        ----------
        worker_registry : WorkerRegistry, optional
            Registry for looking up workers. Uses global if not provided.
        capability_registry : CapabilityRegistry, optional
            Registry for capability metadata. Uses global if not provided.
        event_bus : EventBus, optional
            Event bus for publishing events. Uses global if not provided.
        """
        self._worker_registry = worker_registry or get_worker_registry()
        self._capability_registry = capability_registry or get_capability_registry()
        self._event_bus = event_bus or get_event_bus()
        self._active_runs: dict[str, RunContext] = {}
        self._run_tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    async def start_run(self, request: RunRequest) -> AsyncIterator[DomainEvent]:
        """Start a new workflow run.

        Parameters
        ----------
        request : RunRequest
            The run request.

        Yields
        ------
        DomainEvent
            Events from the workflow execution.

        Raises
        ------
        ValueError
            If no worker is available for the required capability.
        """
        run_id = request.run_id
        workflow_name = request.workflow_name

        # Dispatch: capability → CapabilityRegistry → worker_type → WorkerRegistry
        capability = request.capability

        meta = self._capability_registry.get_metadata(capability)
        if not meta:
            raise ValueError(
                f"Capability not registered: '{capability}'. "
                f"Available: {self._capability_registry.list_capabilities()}"
            )
        worker = self._worker_registry.get_worker(meta.worker_type)
        if not worker:
            raise ValueError(
                f"No worker available for worker_type '{meta.worker_type}' "
                f"(capability='{capability}')."
            )

        # Create run context
        ctx = RunContext(
            run_id=run_id,
            workflow_name=workflow_name,
            capability=capability,
            state=RunState.RUNNING,
            started_at=datetime.now(timezone.utc),
            metadata={
                "app_id": request.app_id,
                "user_id": request.user_id,
                "chat_id": request.chat_id,
            },
        )
        self._active_runs[run_id] = ctx

        logger.info(
            f"[RUN_SUPERVISOR] Starting run: run_id={run_id} "
            f"workflow={workflow_name} capability={capability}"
        )

        try:
            # Execute via worker and yield events
            async for event in worker.execute(request):
                ctx.last_seq = event.seq
                # Publish to event bus (non-blocking)
                asyncio.create_task(self._event_bus.publish(event))
                yield event

            # Mark completed
            ctx.state = RunState.COMPLETED
            ctx.completed_at = datetime.now(timezone.utc)

            logger.info(
                f"[RUN_SUPERVISOR] Run completed: run_id={run_id} "
                f"total_events={ctx.last_seq}"
            )

        except asyncio.CancelledError:
            ctx.state = RunState.CANCELLED
            ctx.completed_at = datetime.now(timezone.utc)
            logger.info(f"[RUN_SUPERVISOR] Run cancelled: run_id={run_id}")
            raise

        except Exception as e:
            ctx.state = RunState.FAILED
            ctx.error = str(e)
            ctx.completed_at = datetime.now(timezone.utc)
            logger.error(f"[RUN_SUPERVISOR] Run failed: run_id={run_id} error={e}")

            # Emit failure event
            yield DomainEvent(
                event_type="run.failed",
                seq=ctx.last_seq + 1,
                occurred_at=datetime.now(timezone.utc),
                run_id=run_id,
                schema_version=EVENT_SCHEMA_VERSION,
                data={"error": str(e), "error_type": type(e).__name__},
                metadata=None,
            )
            raise

        finally:
            # Keep run context for inspection, but could clean up after timeout
            pass

    async def resume_run(self, request: ResumeRequest) -> AsyncIterator[DomainEvent]:
        """Resume a paused or checkpointed run.

        Parameters
        ----------
        request : ResumeRequest
            The resume request.

        Yields
        ------
        DomainEvent
            Events from the resumed execution.
        """
        run_id = request.run_id
        capability = request.capability

        meta = self._capability_registry.get_metadata(capability)
        if not meta:
            raise ValueError(f"Capability not registered: '{capability}'")
        worker = self._worker_registry.get_worker(meta.worker_type)
        if not worker:
            raise ValueError(
                f"No worker available for worker_type '{meta.worker_type}' "
                f"(capability='{capability}')."
            )

        # Check for existing run context
        ctx = self._active_runs.get(run_id)
        if ctx:
            ctx.state = RunState.RUNNING
        else:
            ctx = RunContext(
                run_id=run_id,
                workflow_name=request.workflow_name,
                capability=capability,
                state=RunState.RUNNING,
                started_at=datetime.now(timezone.utc),
                last_seq=request.last_seq,
            )
            self._active_runs[run_id] = ctx

        logger.info(
            f"[RUN_SUPERVISOR] Resuming run: run_id={run_id} from_seq={request.last_seq}"
        )

        # The adapter handles resume internally - convert to RunRequest
        run_request = RunRequest(
            run_id=run_id,
            capability=request.capability,
            workflow_name=request.workflow_name,
            app_id=request.app_id,
            user_id=request.user_id,
            chat_id=request.chat_id,
            metadata=request.metadata,
        )

        try:
            async for event in worker.execute(run_request):
                ctx.last_seq = event.seq
                asyncio.create_task(self._event_bus.publish(event))
                yield event

            ctx.state = RunState.COMPLETED
            ctx.completed_at = datetime.now(timezone.utc)

        except Exception as e:
            ctx.state = RunState.FAILED
            ctx.error = str(e)
            ctx.completed_at = datetime.now(timezone.utc)
            raise

    async def cancel_run(self, run_id: str) -> bool:
        """Cancel an active run.

        Parameters
        ----------
        run_id : str
            The run to cancel.

        Returns
        -------
        bool
            True if the run was cancelled, False if not found.
        """
        ctx = self._active_runs.get(run_id)
        if not ctx:
            logger.warning(f"[RUN_SUPERVISOR] Cancel: run not found: {run_id}")
            return False

        if ctx.state != RunState.RUNNING:
            logger.warning(
                f"[RUN_SUPERVISOR] Cancel: run not running: {run_id} state={ctx.state}"
            )
            return False

        # Cancel via worker
        _cancel_meta = self._capability_registry.get_metadata(ctx.capability)
        if _cancel_meta:
            worker = self._worker_registry.get_worker(_cancel_meta.worker_type)
            if worker:
                await worker.cancel(run_id)

        ctx.state = RunState.CANCELLED
        ctx.completed_at = datetime.now(timezone.utc)

        logger.info(f"[RUN_SUPERVISOR] Run cancelled: {run_id}")
        return True

    async def pause_run(self, run_id: str) -> bool:
        """Pause an active run.

        Parameters
        ----------
        run_id : str
            The run to pause.

        Returns
        -------
        bool
            True if paused, False if not found or not running.
        """
        ctx = self._active_runs.get(run_id)
        if not ctx or ctx.state != RunState.RUNNING:
            return False

        ctx.state = RunState.PAUSED
        logger.info(f"[RUN_SUPERVISOR] Run paused: {run_id}")
        return True

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def get_run_context(self, run_id: str) -> RunContext | None:
        """Get the context for a run.

        Parameters
        ----------
        run_id : str
            The run ID.

        Returns
        -------
        RunContext | None
            The run context, or None if not found.
        """
        return self._active_runs.get(run_id)

    def get_active_runs(self) -> list[RunContext]:
        """Get all active (running or paused) runs.

        Returns
        -------
        list[RunContext]
            Active run contexts.
        """
        return [
            ctx
            for ctx in self._active_runs.values()
            if ctx.state in (RunState.RUNNING, RunState.PAUSED)
        ]

    def status(self) -> dict[str, Any]:
        """Return supervisor status for health checks.

        Returns
        -------
        dict
            Status information.
        """
        active = self.get_active_runs()
        return {
            "total_runs": len(self._active_runs),
            "active_runs": len(active),
            "runs_by_state": {
                state.value: len([c for c in self._active_runs.values() if c.state == state])
                for state in RunState
            },
            "worker_capabilities": self._worker_registry.list_capabilities(),
        }


# Global supervisor instance
_global_supervisor: RunSupervisor | None = None


def get_run_supervisor() -> RunSupervisor:
    """Get the global RunSupervisor instance.

    Returns
    -------
    RunSupervisor
        The global supervisor.
    """
    global _global_supervisor
    if _global_supervisor is None:
        _global_supervisor = RunSupervisor()
    return _global_supervisor


def reset_run_supervisor() -> None:
    """Reset the global supervisor (for testing)."""
    global _global_supervisor
    _global_supervisor = None


__all__ = [
    "RunContext",
    "RunState",
    "RunSupervisor",
    "get_run_supervisor",
    "reset_run_supervisor",
]

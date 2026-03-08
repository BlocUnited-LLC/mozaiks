"""AgentWorker — capability worker for multi-agent orchestration.

This worker handles the 'agent' capability type and delegates execution
to the AG2EngineAdapter. It implements the WorkerPort protocol and
streams DomainEvent objects back to the RunSupervisor.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, AsyncIterator

from mozaiksai.contracts import DomainEvent, RunRequest
from mozaiksai.ports.worker import WorkerPort
from mozaiksai.adapters.ag2.adapter import AG2EngineAdapter

logger = logging.getLogger(__name__)


class AgentWorker:
    """Worker that handles multi-agent workflow execution.

    The AgentWorker:
    1. Receives RunRequest from the RunSupervisor
    2. Delegates to AG2EngineAdapter for execution
    3. Streams DomainEvent objects back to the supervisor
    4. Tracks active runs for health monitoring

    Usage::

        worker = AgentWorker()
        async for event in worker.execute(request):
            await transport.send(event)
    """

    def __init__(
        self,
        worker_id: str | None = None,
        adapter: AG2EngineAdapter | None = None,
    ):
        """Initialize the AgentWorker.

        Parameters
        ----------
        worker_id : str, optional
            Unique identifier for this worker. Auto-generated if not provided.
        adapter : AG2EngineAdapter, optional
            The engine adapter to use. Creates a new one if not provided.
        """
        self._worker_id = worker_id or f"agent-worker-{uuid.uuid4().hex[:8]}"
        self._adapter = adapter or AG2EngineAdapter()
        self._active_runs: set[str] = set()
        self._total_runs: int = 0
        self._failed_runs: int = 0

    # ------------------------------------------------------------------
    # WorkerPort implementation
    # ------------------------------------------------------------------

    @property
    def capability(self) -> str:
        """Return the capability type this worker handles."""
        return "agent"

    @property
    def worker_id(self) -> str:
        """Return the unique identifier for this worker instance."""
        return self._worker_id

    async def execute(self, request: RunRequest) -> AsyncIterator[DomainEvent]:
        """Execute a run and yield DomainEvent objects.

        Parameters
        ----------
        request : RunRequest
            The run request to execute.

        Yields
        ------
        DomainEvent
            Canonical domain events from the execution.
        """
        run_id = request.run_id
        self._active_runs.add(run_id)
        self._total_runs += 1

        logger.info(
            f"[AGENT_WORKER] Starting run: worker_id={self._worker_id} "
            f"run_id={run_id} workflow={request.workflow_name}"
        )

        try:
            async for event in self._adapter.run(request):
                yield event

        except Exception as e:
            self._failed_runs += 1
            logger.error(
                f"[AGENT_WORKER] Run failed: worker_id={self._worker_id} "
                f"run_id={run_id} error={e}"
            )
            raise

        finally:
            self._active_runs.discard(run_id)
            logger.info(
                f"[AGENT_WORKER] Run completed: worker_id={self._worker_id} run_id={run_id}"
            )

    async def cancel(self, run_id: str) -> None:
        """Cancel an active run.

        Parameters
        ----------
        run_id : str
            The ID of the run to cancel.
        """
        if run_id not in self._active_runs:
            logger.warning(
                f"[AGENT_WORKER] Cancel requested for unknown run: "
                f"worker_id={self._worker_id} run_id={run_id}"
            )
            return

        logger.info(
            f"[AGENT_WORKER] Cancelling run: worker_id={self._worker_id} run_id={run_id}"
        )

        await self._adapter.cancel(run_id)
        self._active_runs.discard(run_id)

    def status(self) -> dict[str, Any]:
        """Return worker status for health checks.

        Returns
        -------
        dict
            Status information including active runs, health, etc.
        """
        return {
            "worker_id": self._worker_id,
            "capability": self.capability,
            "healthy": True,
            "active_runs": len(self._active_runs),
            "active_run_ids": list(self._active_runs),
            "total_runs": self._total_runs,
            "failed_runs": self._failed_runs,
            "adapter_capabilities": self._adapter.capabilities(),
        }

    # ------------------------------------------------------------------
    # Additional methods
    # ------------------------------------------------------------------

    def is_run_active(self, run_id: str) -> bool:
        """Check if a run is currently active.

        Parameters
        ----------
        run_id : str
            The run ID to check.

        Returns
        -------
        bool
            True if the run is active, False otherwise.
        """
        return run_id in self._active_runs


__all__ = ["AgentWorker"]

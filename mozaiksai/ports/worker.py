"""Worker port protocol for capability-oriented execution."""

from __future__ import annotations

from typing import Any, AsyncIterator, Protocol, runtime_checkable

from mozaiksai.contracts import DomainEvent, RunRequest


WORKER_PROTOCOL_VERSION = "1.0.0"


@runtime_checkable
class WorkerPort(Protocol):
    """Protocol for workers that execute capability-specific runs.

    Workers receive run requests from the RunSupervisor and delegate
    to engine-specific adapters (e.g., AG2EngineAdapter). They stream
    DomainEvent objects back to the supervisor.

    Workers are capability-scoped — an AgentWorker handles agent-based
    workflows while a PipelineWorker might handle DAG-based pipelines.
    """

    @property
    def capability(self) -> str:
        """Return the capability type this worker handles (e.g., 'agent')."""
        ...

    @property
    def worker_id(self) -> str:
        """Return a unique identifier for this worker instance."""
        ...

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
        ...

    async def cancel(self, run_id: str) -> None:
        """Cancel an active run.

        Parameters
        ----------
        run_id : str
            The ID of the run to cancel.
        """
        ...

    def status(self) -> dict[str, Any]:
        """Return worker status for health checks.

        Returns
        -------
        dict
            Status information including active runs, health, etc.
        """
        ...


@runtime_checkable
class WorkerRegistryPort(Protocol):
    """Protocol for worker registries that manage worker pools."""

    def register(self, worker: WorkerPort) -> None:
        """Register a worker instance."""
        ...

    def unregister(self, worker_id: str) -> None:
        """Unregister a worker by ID."""
        ...

    def get_worker(self, capability: str) -> WorkerPort | None:
        """Get a worker that handles the given capability."""
        ...

    def list_workers(self) -> list[WorkerPort]:
        """List all registered workers."""
        ...

    def list_capabilities(self) -> list[str]:
        """List all available capabilities."""
        ...


__all__ = [
    "WORKER_PROTOCOL_VERSION",
    "WorkerPort",
    "WorkerRegistryPort",
]

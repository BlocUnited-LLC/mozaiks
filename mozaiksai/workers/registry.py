"""WorkerRegistry — manages worker pools for capability-based execution.

The registry maintains a collection of workers, each handling a specific
capability type. The RunSupervisor uses the registry to dispatch runs
to the appropriate worker.
"""

from __future__ import annotations

import logging
from typing import Any

from mozaiksai.ports.worker import WorkerPort, WorkerRegistryPort

logger = logging.getLogger(__name__)


class WorkerRegistry:
    """In-memory registry for managing worker pools.

    The registry:
    - Tracks workers by their worker_id
    - Indexes workers by capability for fast lookup
    - Supports multiple workers per capability for load balancing

    Usage::

        registry = WorkerRegistry()
        registry.register(AgentWorker())
        worker = registry.get_worker("agent")
        async for event in worker.execute(request):
            ...
    """

    def __init__(self):
        """Initialize the worker registry."""
        self._workers: dict[str, WorkerPort] = {}
        self._by_capability: dict[str, list[str]] = {}

    def register(self, worker: WorkerPort) -> None:
        """Register a worker instance.

        Parameters
        ----------
        worker : WorkerPort
            The worker to register.

        Raises
        ------
        ValueError
            If a worker with the same ID is already registered.
        """
        worker_id = worker.worker_id
        capability = worker.capability

        if worker_id in self._workers:
            raise ValueError(f"Worker already registered: {worker_id}")

        self._workers[worker_id] = worker

        if capability not in self._by_capability:
            self._by_capability[capability] = []
        self._by_capability[capability].append(worker_id)

        logger.info(
            f"[WORKER_REGISTRY] Registered worker: id={worker_id} capability={capability}"
        )

    def unregister(self, worker_id: str) -> None:
        """Unregister a worker by ID.

        Parameters
        ----------
        worker_id : str
            The ID of the worker to unregister.
        """
        worker = self._workers.pop(worker_id, None)
        if not worker:
            logger.warning(f"[WORKER_REGISTRY] Worker not found: {worker_id}")
            return

        capability = worker.capability
        if capability in self._by_capability:
            try:
                self._by_capability[capability].remove(worker_id)
                if not self._by_capability[capability]:
                    del self._by_capability[capability]
            except ValueError:
                pass

        logger.info(f"[WORKER_REGISTRY] Unregistered worker: {worker_id}")

    def get_worker(self, capability: str) -> WorkerPort | None:
        """Get a worker that handles the given capability.

        Currently returns the first available worker. Future implementations
        may add load balancing or affinity-based selection.

        Parameters
        ----------
        capability : str
            The capability type (e.g., 'agent').

        Returns
        -------
        WorkerPort | None
            A worker for the capability, or None if none available.
        """
        worker_ids = self._by_capability.get(capability, [])
        if not worker_ids:
            return None

        # Simple round-robin: take first worker
        # Future: implement proper load balancing
        worker_id = worker_ids[0]
        return self._workers.get(worker_id)

    def get_worker_by_id(self, worker_id: str) -> WorkerPort | None:
        """Get a specific worker by ID.

        Parameters
        ----------
        worker_id : str
            The worker ID.

        Returns
        -------
        WorkerPort | None
            The worker, or None if not found.
        """
        return self._workers.get(worker_id)

    def list_workers(self) -> list[WorkerPort]:
        """List all registered workers.

        Returns
        -------
        list[WorkerPort]
            All registered workers.
        """
        return list(self._workers.values())

    def list_capabilities(self) -> list[str]:
        """List all available capabilities.

        Returns
        -------
        list[str]
            All capabilities with at least one registered worker.
        """
        return list(self._by_capability.keys())

    def status(self) -> dict[str, Any]:
        """Return registry status for health checks.

        Returns
        -------
        dict
            Status information about the registry.
        """
        worker_statuses = []
        for worker in self._workers.values():
            try:
                worker_statuses.append(worker.status())
            except Exception as e:
                worker_statuses.append({
                    "worker_id": worker.worker_id,
                    "error": str(e),
                })

        return {
            "total_workers": len(self._workers),
            "capabilities": self.list_capabilities(),
            "workers": worker_statuses,
        }


# Global registry instance
_global_registry: WorkerRegistry | None = None


def get_worker_registry() -> WorkerRegistry:
    """Get the global worker registry instance.

    Returns
    -------
    WorkerRegistry
        The global registry instance.
    """
    global _global_registry
    if _global_registry is None:
        _global_registry = WorkerRegistry()
    return _global_registry


def reset_worker_registry() -> None:
    """Reset the global worker registry (for testing)."""
    global _global_registry
    _global_registry = None


__all__ = [
    "WorkerRegistry",
    "get_worker_registry",
    "reset_worker_registry",
]

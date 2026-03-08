"""runtime/worker_registry.py — Runtime-layer interface to the WorkerRegistry.

The WorkerRegistry lives in mozaiksai/workers/registry.py; this module
re-exports it from the runtime namespace so that the rest of the runtime
(RunSupervisor, factory bootstrap) can reference it through a stable
runtime-layer import path.

Architecture position
---------------------
    Transport
    → RunSupervisor          (runtime/execution/run_supervisor.py)
    → WorkerRegistry  ←──── THIS MODULE
    → AgentWorker            (workers/agent_worker.py)
    → AG2EngineAdapter       (adapters/ag2/adapter.py)
"""
from __future__ import annotations

from mozaiksai.workers.registry import (
    WorkerRegistry,
    get_worker_registry,
    reset_worker_registry,
)

__all__ = [
    "WorkerRegistry",
    "get_worker_registry",
    "reset_worker_registry",
]

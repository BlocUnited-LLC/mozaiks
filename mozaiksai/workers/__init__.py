"""Workers layer — capability-specific execution handlers.

Workers receive run requests from the RunSupervisor and delegate to
engine-specific adapters. Each worker type handles a specific capability:

- AgentWorker: Multi-agent orchestration via AG2EngineAdapter
- (Future) PipelineWorker: DAG-based pipeline execution
- (Future) ToolWorker: Direct tool invocation

Workers are engine-agnostic — they interact with adapters through
the OrchestrationPort protocol and yield DomainEvent objects.
"""

from __future__ import annotations

__all__ = [
    "AgentWorker",
    "WorkerRegistry",
]


def __getattr__(name: str) -> object:
    """Lazy import to avoid AG2 dependency chain on package init."""
    if name == "AgentWorker":
        from mozaiksai.workers.agent_worker import AgentWorker
        return AgentWorker
    if name == "WorkerRegistry":
        from mozaiksai.workers.registry import WorkerRegistry
        return WorkerRegistry
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

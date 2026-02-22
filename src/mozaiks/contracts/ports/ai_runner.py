"""AI runner port protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from mozaiks.contracts.ports.orchestration import OrchestrationPort

AI_RUNNER_PROTOCOL_VERSION = "1.0.0"


@runtime_checkable
class AIWorkflowRunnerPort(OrchestrationPort, Protocol):
    """Versioned contract for AI workflow execution engines."""
    pass


__all__ = ["AI_RUNNER_PROTOCOL_VERSION", "AIWorkflowRunnerPort"]

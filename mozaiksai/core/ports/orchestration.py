
# FILE: mozaiksai/core/ports/orchestration.py
# DESCRIPTION: OrchestrationPort — the engine-agnostic contract surface for all
#              workflow execution in mozaiksai.
#
# This is the single boundary between the runtime layer and the execution engine.
# Everything above this contract (transport, API routes, websocket handlers) is
# engine-agnostic.  Everything below it (AG2 GroupChat, a_run_group_chat, events)
# is engine-specific and isolated in adapters/.
#
# When AG2 ships bidirectional streaming, ONLY the adapter changes.
# When a second engine is added, a NEW adapter is written.
# This file never changes for engine reasons.
# ==============================================================================
"""OrchestrationPort — runtime↔engine contract.

The port defines three verbs:
    run      — start a new workflow execution
    resume   — continue a paused workflow (handoff-to-user, fan-in resume, etc.)
    cancel   — abort a running workflow

And one query:
    capabilities — what the adapter supports (pause, fan-out, streaming, etc.)

Data flows through typed request/result objects and a DomainEvent envelope so the
runtime never imports AG2 types directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


class RunStatus(Enum):
    """Outcome of a single run/resume invocation."""

    COMPLETED = "completed"
    PAUSED = "paused"          # explicit pause (fan-out, budget, etc.)
    FAILED = "failed"
    CANCELLED = "cancelled"
    IN_PROGRESS = "in_progress"


@dataclass(frozen=True)
class RunRequest:
    """Everything the runtime needs to start a new orchestration run."""

    workflow_name: str
    app_id: str
    chat_id: str
    user_id: str
    initial_message: Optional[str] = None
    initial_agent_name_override: Optional[str] = None
    # Arbitrary engine-specific overrides (llm_config, max_rounds, etc.)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResumeRequest:
    """Everything the runtime needs to resume a paused orchestration."""

    workflow_name: str
    app_id: str
    chat_id: str
    user_id: str
    resume_agent: Optional[str] = None
    injected_context: Optional[Dict[str, Any]] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DomainEvent:
    """Engine-agnostic event envelope streamed back to the runtime.

    The runtime (transport, websocket handler, event dispatcher) consumes
    DomainEvents without importing any AG2 type.

    Fields:
        kind       — namespaced event type  (e.g. "chat.text", "chat.tool_call")
        payload    — serializable event data (keys depend on kind)
        chat_id    — the owning chat session
        timestamp  — UTC ISO-8601
        source     — which adapter produced this event (e.g. "ag2")
    """

    kind: str
    payload: Dict[str, Any]
    chat_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str = "ag2"


@dataclass
class RunResult:
    """Summary returned after a run or resume completes (or is paused/cancelled)."""

    status: RunStatus
    chat_id: str
    workflow_name: str
    # Merged child outputs (populated after fan-in).
    merged_context: Optional[Dict[str, Any]] = None
    # Token / cost accounting.
    usage: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Port protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class OrchestrationPort(Protocol):
    """Engine-agnostic contract for workflow execution.

    Implementors:
        AG2OrchestrationAdapter  — wraps AG2 a_run_group_chat + resume + cancel
        (future)                 — wraps a second engine

    Consumers:
        SimpleTransport          — calls run/resume/cancel from websocket/REST handlers
        WorkflowPackCoordinator  — calls run/resume for child and parent GroupChats
        JourneyOrchestrator      — calls run for next-step workflows
    """

    async def run(self, request: RunRequest) -> RunResult:
        """Execute a workflow from scratch (or from persisted history).

        The adapter is responsible for:
        - loading workflow config
        - building the AG2 pattern (agents, handoffs, context variables)
        - running the GroupChat
        - streaming events to the transport via DomainEvent
        - returning a RunResult when the GroupChat finishes (or pauses)
        """
        ...

    async def resume(self, request: ResumeRequest) -> RunResult:
        """Resume a paused workflow.

        Typical triggers:
        - Fan-in complete — parent resumes with merged child context
        - User clicked "continue" after a checkpoint
        """
        ...

    async def cancel(self, run_id: str) -> None:
        """Abort a running workflow.

        `run_id` is typically the `chat_id`.
        """
        ...

    def capabilities(self) -> Dict[str, Any]:
        """Advertise what this adapter supports.

        Example return:
            {
                "engine": "ag2",
                "version": "0.11.2",
                "supports_pause": True,
                "supports_fan_out": True,
                "supports_bidirectional_stream": False,
                "supports_cancel": True,
            }
        """
        ...


__all__ = [
    "OrchestrationPort",
    "RunRequest",
    "ResumeRequest",
    "RunResult",
    "RunStatus",
    "DomainEvent",
]

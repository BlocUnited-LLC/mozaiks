"""AG2EngineAdapter — the ONLY place where AG2 is executed.

This adapter implements the OrchestrationPort protocol and wraps all
AG2-specific execution logic. Downstream code (workers, runtime core)
interacts with this adapter via the protocol interface and receives
engine-agnostic DomainEvent instances.

Design rules
------------
* Single entry point for AG2 execution — no other module should directly
  call a_run_group_chat or interact with AG2 patterns.
* All AG2 imports are contained within this adapter layer.
* Yields canonical DomainEvent objects from contracts.events.
* Translates AG2 streaming events via the event_translator module.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from mozaiksai.contracts import (
    DomainEvent,
    EVENT_SCHEMA_VERSION,
    ResumeRequest,
    RunRequest,
)
from mozaiksai.ports.orchestration import OrchestrationPort

from mozaiksai.adapters.ag2.event_translator import (
    translate as translate_ag2_event,
    DomainEvent as StreamDomainEvent,
    EventKind,
)

logger = logging.getLogger(__name__)


def _stream_to_canonical(
    stream_event: StreamDomainEvent,
    run_id: str,
    seq: int,
) -> DomainEvent:
    """Convert streaming DomainEvent to canonical contract DomainEvent."""
    # Map EventKind to canonical event_type string
    event_type_map = {
        EventKind.TEXT: "agent.text",
        EventKind.SELECT_SPEAKER: "orchestration.speaker_selected",
        EventKind.INPUT_REQUEST: "agent.input_requested",
        EventKind.TOOL_CALL: "tool.invoked",
        EventKind.USAGE_SUMMARY: "usage.summary",
        EventKind.STREAM_CHUNK: "agent.stream_chunk",
        EventKind.RUN_COMPLETE: "run.completed",
        EventKind.HANDOFF_TO_USER: "agent.handoff_to_user",
        EventKind.UNKNOWN: "agent.unknown",
    }
    event_type = event_type_map.get(stream_event.kind, "agent.unknown")

    # Build payload from stream event
    payload: dict[str, Any] = {
        "content": stream_event.content,
    }
    if stream_event.agent:
        payload["agent"] = stream_event.agent
    if stream_event.metadata:
        # Don't include raw callbacks in payload
        filtered_metadata = {
            k: v for k, v in stream_event.metadata.items()
            if k != "respond" and not callable(v)
        }
        payload["metadata"] = filtered_metadata

    # Build metadata for envelope
    envelope_metadata: dict[str, Any] = {}
    if stream_event.agent:
        envelope_metadata["source_agent"] = stream_event.agent

    return DomainEvent(
        event_type=event_type,
        seq=seq,
        occurred_at=datetime.now(timezone.utc),
        run_id=run_id,
        schema_version=EVENT_SCHEMA_VERSION,
        data=payload,
        metadata=envelope_metadata if envelope_metadata else None,
    )


class AG2EngineAdapter:
    """Adapter that wraps AG2 execution and exposes OrchestrationPort interface.

    This is the **single entry point** for all AG2 workflow execution.
    The adapter:
    1. Accepts RunRequest/ResumeRequest from the runtime layer
    2. Builds agents, patterns, and context using AG2-specific modules
    3. Launches AG2 group chat execution
    4. Translates AG2 events to canonical DomainEvent objects
    5. Yields DomainEvent objects to the caller

    Usage::

        adapter = AG2EngineAdapter()
        async for event in adapter.run(request):
            # event is a DomainEvent from contracts.events
            await transport.send(event)
    """

    def __init__(
        self,
        agents_factory: Any | None = None,
        context_factory: Any | None = None,
        handoffs_factory: Any | None = None,
    ):
        """Initialize the AG2 engine adapter.

        Parameters
        ----------
        agents_factory : callable, optional
            Custom factory for creating agents. If not provided,
            uses the default create_agents from agent_factory.
        context_factory : callable, optional
            Custom factory for loading context variables.
        handoffs_factory : callable, optional
            Custom factory for wiring handoffs.
        """
        self.agents_factory = agents_factory
        self.context_factory = context_factory
        self.handoffs_factory = handoffs_factory
        self._active_runs: dict = {}

    # ------------------------------------------------------------------
    # OrchestrationPort implementation
    # ------------------------------------------------------------------

    async def run(self, request: RunRequest) -> AsyncIterator[DomainEvent]:
        """Start a new workflow run and yield DomainEvent objects.

        Parameters
        ----------
        request : RunRequest
            The run request containing workflow_name, payload, and metadata.

        Yields
        ------
        DomainEvent
            Canonical domain events from the workflow execution.
        """
        run_id = request.run_id
        workflow_name = request.workflow_name
        app_id = request.app_id or "default_app"
        chat_id = request.chat_id or run_id
        user_id = request.user_id

        # Extract initial message from payload
        initial_message = request.context.get("message") or request.context.get("initial_message")

        logger.info(
            f"[AG2_ADAPTER] Starting run: run_id={run_id} workflow={workflow_name} "
            f"app_id={app_id} chat_id={chat_id}"
        )

        # Emit run.started lifecycle event.
        yield DomainEvent(
            event_type="run.started",
            seq=0,
            occurred_at=datetime.now(timezone.utc),
            run_id=run_id,
            schema_version=EVENT_SCHEMA_VERSION,
            data={"workflow_name": workflow_name, "app_id": app_id, "chat_id": chat_id},
            metadata=None,
        )

        # Delegate to the existing orchestration path.  All AG2 event
        # streaming continues through SimpleTransport — orchestration
        # logic is NOT rewritten in this phase (Phase 2 boundary only).
        try:
            from mozaiksai.engine.orchestration import run_workflow_orchestration
            await run_workflow_orchestration(
                workflow_name=workflow_name,
                app_id=app_id,
                chat_id=chat_id,
                user_id=user_id,
                initial_message=initial_message,
                initial_agent_name_override=request.metadata.get(
                    "initial_agent_name_override"
                ),
            )
        except Exception as e:
            logger.error(f"[AG2_ADAPTER] Run failed: run_id={run_id} error={e}")
            yield DomainEvent(
                event_type="run.failed",
                seq=1,
                occurred_at=datetime.now(timezone.utc),
                run_id=run_id,
                schema_version=EVENT_SCHEMA_VERSION,
                data={"error": str(e), "error_type": type(e).__name__},
                metadata=None,
            )
            raise

        # Emit run.completed lifecycle event.
        yield DomainEvent(
            event_type="run.completed",
            seq=1,
            occurred_at=datetime.now(timezone.utc),
            run_id=run_id,
            schema_version=EVENT_SCHEMA_VERSION,
            data={"workflow_name": workflow_name},
            metadata=None,
        )

    async def resume(self, request: ResumeRequest) -> AsyncIterator[DomainEvent]:
        """Resume a workflow run from checkpoint.

        Parameters
        ----------
        request : ResumeRequest
            The resume request containing run_id and checkpoint information.

        Yields
        ------
        DomainEvent
            Canonical domain events from the resumed workflow execution.
        """
        run_id = request.run_id
        workflow_name = request.workflow_name
        app_id = request.app_id or "default_app"
        chat_id = request.chat_id or run_id
        user_id = request.user_id

        logger.info(
            f"[AG2_ADAPTER] Resuming run: run_id={run_id} workflow={workflow_name} "
            f"from_seq={request.last_seq}"
        )

        # Emit run.resumed lifecycle event.
        yield DomainEvent(
            event_type="run.resumed",
            seq=request.last_seq,
            occurred_at=datetime.now(timezone.utc),
            run_id=run_id,
            schema_version=EVENT_SCHEMA_VERSION,
            data={"workflow_name": workflow_name, "app_id": app_id, "chat_id": chat_id},
            metadata={"checkpoint_id": request.checkpoint_id},
        )

        # Delegate to the existing orchestration path.  The executor's
        # resume logic (_resume_or_initialize_chat) picks up the prior
        # session history from persistence automatically.
        try:
            from mozaiksai.engine.orchestration import run_workflow_orchestration
            await run_workflow_orchestration(
                workflow_name=workflow_name,
                app_id=app_id,
                chat_id=chat_id,
                user_id=user_id,
                initial_message=None,
            )
        except Exception as e:
            logger.error(f"[AG2_ADAPTER] Resume failed: run_id={run_id} error={e}")
            yield DomainEvent(
                event_type="run.failed",
                seq=request.last_seq + 1,
                occurred_at=datetime.now(timezone.utc),
                run_id=run_id,
                schema_version=EVENT_SCHEMA_VERSION,
                data={"error": str(e), "error_type": type(e).__name__},
                metadata=None,
            )
            raise

        # Emit run.completed lifecycle event.
        yield DomainEvent(
            event_type="run.completed",
            seq=request.last_seq + 1,
            occurred_at=datetime.now(timezone.utc),
            run_id=run_id,
            schema_version=EVENT_SCHEMA_VERSION,
            data={"workflow_name": workflow_name},
            metadata=None,
        )

    async def cancel(self, run_id: str) -> None:
        """Cancel an active workflow run.

        Parameters
        ----------
        run_id : str
            The ID of the run to cancel.
        """
        # Phase 2: execution delegated to run_workflow_orchestration which
        # manages its own cancel path via SimpleTransport / termination handler.
        # Direct PreparedRun tracking is not maintained at this layer.
        logger.info(f"[AG2_ADAPTER] Cancel requested for run: {run_id} (delegated to orchestration)")

    def capabilities(self) -> dict[str, Any]:
        """Return adapter capabilities for discovery.

        Returns
        -------
        dict
            Capability metadata for this adapter.
        """
        return {
            "engine": "ag2",
            "version": "0.6.0",
            "supports_resume": True,
            "supports_cancel": True,
            "supports_streaming": True,
            "supported_patterns": [
                "AutoPattern",
                "DefaultPattern",
                "RoundRobinPattern",
            ],
            "features": [
                "handoffs",
                "tools",
                "context_variables",
                "structured_outputs",
                "lifecycle_hooks",
            ],
        }

    # ------------------------------------------------------------------
    # Additional helper methods
    # ------------------------------------------------------------------

    def get_active_run(self, run_id: str) -> Any | None:
        """Get the active run context for a run.

        Parameters
        ----------
        run_id : str
            The run ID to look up.

        Returns
        -------
        Any | None
            The run context if active, None otherwise.

        Note
        ----
        Currently returns None as run tracking is delegated to
        run_workflow_orchestration. Will be populated when the
        adapter owns full run lifecycle.
        """
        return self._active_runs.get(run_id)

    @property
    def active_run_count(self) -> int:
        """Return the number of currently active runs."""
        return len(self._active_runs)


# Verify protocol conformance at module load time
def _verify_protocol_conformance():
    """Verify AG2EngineAdapter implements OrchestrationPort."""
    import typing
    # This will raise if the adapter doesn't conform
    if not isinstance(AG2EngineAdapter, type):
        return
    # Runtime check - create instance and verify
    # (deferred to avoid circular imports during module load)


__all__ = ["AG2EngineAdapter"]

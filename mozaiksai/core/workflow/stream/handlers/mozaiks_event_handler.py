# ==============================================================================
# FILE: mozaiksai/core/workflow/stream/handlers/mozaiks_event_handler.py
# DESCRIPTION: Handler for mozaiksai custom AG2 events
# ==============================================================================

"""
Mozaiksai Custom Event Handler

Handles AG2-native custom events defined in mozaiksai/core/events/ag2_events.py.
Routes events to UnifiedEventDispatcher for consistent cross-system handling.

This handler is the bridge between AG2's native event stream and mozaiksai's
event system (UnifiedEventDispatcher).

Forward Compatibility (AG2 Beta):
    When migrating to beta streams, this handler becomes a subscriber:

    # Current (groupchat)
    registry.register(MozaiksaiEventHandler())

    # Future (beta)
    stream.subscribe(handler.handle, condition=is_mozaiksai_event)

    The handler logic stays the same.
"""

import logging
from typing import Any, Dict, Optional, Set, Type

from .base import BaseEventHandler

logger = logging.getLogger(__name__)

from mozaiksai.core.events.ag2_events import (
    AgentThinkingEvent,
    ALL_MOZAIKSAI_EVENTS,
    ArtifactReadyEvent,
    ArtifactUpdatedEvent,
    ContextUpdatedEvent,
    DecompositionPlannedEvent,
    HandoffRequestedEvent,
    JourneyCompletedEvent,
    JourneyStartedEvent,
    PlanCreatedEvent,
    PrerequisitesRequiredEvent,
    StructuredOutputEvent,
    ToolCallRequestedEvent,
    WorkflowTriggeredEvent,
)
from mozaiksai.core.events.ag2_event_bridge import AG2EventBridge


class MozaiksaiEventHandler(BaseEventHandler):
    """
    Handler for mozaiksai custom AG2 events.

    Routes events from AG2's event stream to UnifiedEventDispatcher,
    ensuring consistent handling across the mozaiksai event system.

    Events handled:
        - WorkflowTriggeredEvent
        - HandoffRequestedEvent
        - PlanCreatedEvent
        - PrerequisitesRequiredEvent
        - AgentThinkingEvent
        - StructuredOutputEvent
        - ArtifactUpdatedEvent
        - ToolCallRequestedEvent
        - ContextUpdatedEvent
        - JourneyStartedEvent
        - JourneyCompletedEvent

    Usage:
        registry = EventHandlerRegistry()
        registry.register(MozaiksaiEventHandler())
    """

    def __init__(self):
        self._bridge = AG2EventBridge()
        self._handled_count = 0

    def event_types(self) -> Set[Type]:
        """Return the custom event types handled by this bridge."""
        return set(ALL_MOZAIKSAI_EVENTS)

    async def handle(
        self,
        event: Any,
        ctx: "StreamContext",  # type: ignore
        state: "StreamState",  # type: ignore
    ) -> Optional[Dict[str, Any]]:
        """
        Handle mozaiksai custom event by routing to UnifiedEventDispatcher.

        Args:
            event: Mozaiksai custom AG2 event
            ctx: Stream context
            state: Stream state

        Returns:
            UI payload dict for transport, or None if event should not be sent to UI
        """
        if not self._bridge.can_handle(event):
            return None

        event_name = event.__class__.__name__
        ctx.wf_logger.info(
            f" [{ctx.workflow_name_upper}] Processing custom event: {event_name}"
        )

        # Route to UnifiedEventDispatcher
        success = await self._bridge.handle(event, ctx, state)

        if success:
            self._handled_count += 1
            logger.debug(
                f"[MOZAIKS_HANDLER] Successfully routed {event_name} to dispatcher"
            )
        else:
            logger.warning(
                f"[MOZAIKS_HANDLER] Failed to route {event_name} to dispatcher"
            )

        # Build UI payload for specific event types
        payload = self._build_ui_payload(event, ctx)

        return payload

    def _build_ui_payload(
        self,
        event: Any,
        ctx: "StreamContext",  # type: ignore
    ) -> Optional[Dict[str, Any]]:
        """
        Build UI payload for events that should be sent to frontend.

        Some events (like AgentThinkingEvent) should update the UI.
        Others (like internal control events) should not.
        """
        def _content_attr(name: str, default=None):
            if hasattr(event, "content"):
                return getattr(event.content, name, default)
            return getattr(event, name, default)

        def _build_activity(activity_type: str, message: str, *, status: str = "working", agent: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
            return {
                "kind": "activity",
                "activity_type": activity_type,
                "status": status,
                "agent": agent,
                "message": message,
                "chat_id": ctx.chat_id,
                "workflow_name": ctx.workflow_name,
                "metadata": metadata or {},
            }

        if isinstance(event, AgentThinkingEvent):
            return {
                "kind": "agent_thinking",
                "agent": getattr(event.content, "agent_name", None) if hasattr(event, "content") else getattr(event, "agent_name", None),
                "thinking_type": getattr(event.content, "thinking_type", "default") if hasattr(event, "content") else getattr(event, "thinking_type", "default"),
                "chat_id": ctx.chat_id,
            }

        if isinstance(event, StructuredOutputEvent):
            agent_name = _content_attr("agent_name")
            output_type = _content_attr("output_type")
            validation_passed = bool(_content_attr("validation_passed", True))
            status = "validated" if validation_passed else "invalid"
            return _build_activity(
                "structured_output",
                f"{agent_name or 'Agent'} produced {status} {output_type or 'structured output'}.",
                status=status,
                agent=agent_name,
                metadata={
                    "output_type": output_type,
                    "validation_passed": validation_passed,
                },
            )

        if isinstance(event, ToolCallRequestedEvent):
            return {
                "kind": "tool_call",
                "tool_name": getattr(event.content, "tool_name", None) if hasattr(event, "content") else getattr(event, "tool_name", None),
                "agent": getattr(event.content, "agent_name", None) if hasattr(event, "content") else getattr(event, "agent_name", None),
                "display": getattr(event.content, "display_mode", "inline") if hasattr(event, "content") else getattr(event, "display_mode", "inline"),
                "payload": getattr(event.content, "payload", {}) if hasattr(event, "content") else getattr(event, "payload", {}),
                "chat_id": ctx.chat_id,
            }

        if isinstance(event, HandoffRequestedEvent):
            from_agent = _content_attr("from_agent")
            to_agent = _content_attr("to_agent")
            reason = _content_attr("reason", "")
            return _build_activity(
                "handoff_requested",
                f"Handoff: {from_agent or 'Agent'} to {to_agent or 'next agent'}.",
                status="handoff",
                agent=from_agent,
                metadata={"from_agent": from_agent, "to_agent": to_agent, "reason": reason},
            )

        if isinstance(event, DecompositionPlannedEvent):
            structured_data = _content_attr("structured_data", {}) or {}
            workflow_count = len(structured_data.get("workflows", []) or []) if isinstance(structured_data, dict) else 0
            agent_name = _content_attr("agent_name")
            return _build_activity(
                "decomposition_planned",
                f"{agent_name or 'Agent'} planned {workflow_count} child workflow{'' if workflow_count == 1 else 's'}.",
                status="planned",
                agent=agent_name,
                metadata={"workflow_count": workflow_count},
            )

        if isinstance(event, (ArtifactUpdatedEvent, ArtifactReadyEvent)):
            artifact_type = _content_attr("artifact_type", "artifact")
            artifact_id = _content_attr("artifact_id")
            action = "ready" if isinstance(event, ArtifactReadyEvent) else str(_content_attr("action", "updated") or "updated").strip().lower()
            return _build_activity(
                f"artifact_{action}",
                f"{artifact_type.replace('_', ' ').title()} {action}.",
                status=action,
                metadata={
                    "artifact_type": artifact_type,
                    "artifact_id": artifact_id,
                    "artifact_version_id": _content_attr("artifact_version_id"),
                },
            )

        if isinstance(event, PlanCreatedEvent):
            steps = _content_attr("steps", []) or []
            return _build_activity(
                "plan_created",
                f"Execution plan created with {len(steps)} step{'' if len(steps) == 1 else 's'}.",
                status="planned",
                metadata={
                    "plan_id": _content_attr("plan_id"),
                    "step_count": len(steps),
                    "estimated_turns": _content_attr("estimated_turns", 0),
                },
            )

        # Other events are internal and don't need UI representation
        return None

    def should_break(self, event: Any, state: "StreamState") -> bool:  # type: ignore
        """Custom events don't terminate the stream."""
        return False

    def get_metrics(self) -> Dict[str, Any]:
        """Get handler metrics."""
        return {
            "handled_count": self._handled_count,
            "registered_event_types": len(ALL_MOZAIKSAI_EVENTS),
        }

# ==============================================================================
# FILE: mozaiksai/core/events/ag2_event_bridge.py
# DESCRIPTION: Bridge AG2 custom events to UnifiedEventDispatcher
# ==============================================================================

"""
AG2 Event Bridge

This module bridges AG2-native custom events to mozaiksai's UnifiedEventDispatcher.
It provides:

1. Handler registration for custom events in stream processing
2. Conversion of AG2 event objects to dispatcher-compatible dicts
3. Forward-compatible patterns for AG2 beta migration

Usage in _stream_events():
    from mozaiksai.core.events.ag2_event_bridge import AG2EventBridge

    bridge = AG2EventBridge(dispatcher)

    for event in a_run_group_chat_iter(..., yield_on=ALL_MOZAIKSAI_EVENTS + [TextEvent]):
        if bridge.can_handle(event):
            await bridge.handle(event, ctx)
        elif isinstance(event, TextEvent):
            # Normal text handling
            ...

Forward Compatibility (AG2 Beta):
    When migrating to beta streams, this bridge becomes a subscriber:

    # Beta pattern
    stream.subscribe(bridge.handle, condition=is_mozaiksai_event)

    The event conversion logic stays the same.
"""

import logging
from typing import Any, Dict, Optional, Type
from datetime import datetime, UTC

from autogen.events.agent_events import (
    InputRequestEvent,
    RunCompletionEvent,
    SelectSpeakerEvent,
    TextEvent,
    ToolCallEvent,
)

from .ag2_events import (
    # Control events
    WorkflowTriggeredEvent,
    HandoffRequestedEvent,
    PlanCreatedEvent,
    PrerequisitesRequiredEvent,
    # Runtime events
    AgentThinkingEvent,
    StructuredOutputEvent,
    DecompositionPlannedEvent,
    ArtifactUpdatedEvent,
    ArtifactReadyEvent,
    UIToolRequestedEvent,
    ContextUpdatedEvent,
    # Journey events
    JourneyStartedEvent,
    JourneyCompletedEvent,
    ALL_MOZAIKSAI_EVENTS,
)
from .runtime_events import (
    RUNTIME_AGENT_OUTPUT_VALIDATED,
    RUNTIME_DECOMPOSITION_PLANNED,
    ARTIFACT_EVENT_CREATED,
    ARTIFACT_EVENT_UPDATED,
    ARTIFACT_EVENT_READY,
    ARTIFACT_EVENT_DELETED,
    build_artifact_lifecycle_event,
    build_runtime_agent_output_validated_event,
    build_runtime_context_payload,
    build_runtime_decomposition_planned_event,
    build_turn_idempotency_key,
)

logger = logging.getLogger(__name__)


STANDARD_AG2_EVENTS = [
    TextEvent,
    InputRequestEvent,
    SelectSpeakerEvent,
    RunCompletionEvent,
    ToolCallEvent,
]


class AG2EventBridge:
    """
    Bridge AG2 custom events to UnifiedEventDispatcher.

    Handles conversion and routing of AG2-native events to the
    mozaiksai event system.
    """

    # Map AG2 event classes to dispatcher event types
    EVENT_TYPE_MAP: Dict[Type, str] = {
        # Control events
        WorkflowTriggeredEvent: "control.workflow_triggered",
        HandoffRequestedEvent: "control.handoff_requested",
        PlanCreatedEvent: "control.plan_created",
        PrerequisitesRequiredEvent: "control.prerequisites_required",
        # Runtime events
        AgentThinkingEvent: "runtime.agent_thinking",
        StructuredOutputEvent: RUNTIME_AGENT_OUTPUT_VALIDATED,
        DecompositionPlannedEvent: RUNTIME_DECOMPOSITION_PLANNED,
        ArtifactUpdatedEvent: ARTIFACT_EVENT_UPDATED,
        ArtifactReadyEvent: ARTIFACT_EVENT_READY,
        UIToolRequestedEvent: "runtime.ui_tool_requested",
        ContextUpdatedEvent: "runtime.context_updated",
        # Journey events
        JourneyStartedEvent: "mfj.journey_started",
        JourneyCompletedEvent: "mfj.journey_completed",
    }

    def __init__(self, dispatcher=None):
        """
        Initialize bridge with optional dispatcher.

        If dispatcher is None, uses get_event_dispatcher() on first handle.
        """
        self._dispatcher = dispatcher
        self._handled_count = 0

    @property
    def dispatcher(self):
        if self._dispatcher is None:
            from .unified_event_dispatcher import get_event_dispatcher
            self._dispatcher = get_event_dispatcher()
        return self._dispatcher

    def can_handle(self, event: Any) -> bool:
        """Check if this bridge can handle the given event."""
        return type(event) in self.EVENT_TYPE_MAP

    def _event_to_dict(self, event: Any) -> Dict[str, Any]:
        """
        Convert AG2 event object to dictionary for dispatcher.

        AG2's @wrap_event adds a 'content' wrapper. We extract
        the actual fields for cleaner downstream handling.
        """
        event_type = type(event)
        result = {
            "kind": self.EVENT_TYPE_MAP.get(event_type, "unknown"),
            "timestamp": datetime.now(UTC).isoformat(),
            "_ag2_event_type": event_type.__name__,
        }

        # Extract content from wrapped event or direct attributes
        if hasattr(event, 'content') and hasattr(event.content, '__dict__'):
            # @wrap_event wraps fields in .content
            for key, value in event.content.__dict__.items():
                if not key.startswith('_'):
                    result[key] = value
        else:
            # Direct attribute access
            for key in dir(event):
                if not key.startswith('_') and not callable(getattr(event, key)):
                    try:
                        result[key] = getattr(event, key)
                    except Exception:
                        pass

        return result

    def _build_structured_output_payload(
        self,
        event: Any,
        ctx: Optional[Any] = None,
        state: Optional[Any] = None,
    ) -> Dict[str, Any]:
        content = getattr(event, "content", event)
        agent_name = str(
            getattr(content, "agent_name", None)
            or getattr(event, "agent_name", None)
            or ""
        ).strip()
        model_name = str(
            getattr(content, "output_type", None)
            or getattr(event, "output_type", None)
            or "structured_output"
        ).strip()
        output_data = getattr(content, "output_data", None)
        validation_passed = getattr(content, "validation_passed", True)

        context_payload: Dict[str, Any] = {}
        turn_key: Optional[str] = None
        pattern_context_ref = None
        if ctx is not None:
            context_payload = build_runtime_context_payload(
                chat_id=str(getattr(ctx, "chat_id", "") or ""),
                app_id=str(getattr(ctx, "app_id", "") or ""),
                workflow_name=str(getattr(ctx, "workflow_name", "") or ""),
                user_id=getattr(ctx, "user_id", None),
                turn_sequence=getattr(state, "sequence_counter", None),
                context_variables=getattr(ctx, "context_variables", None),
            )
            if agent_name:
                context_payload["agent_name"] = agent_name
            pattern_context_ref = getattr(ctx, "context_variables", None)
        if ctx is not None and state is not None:
            chat_id = str(getattr(ctx, "chat_id", "") or "")
            turn_sequence = getattr(state, "sequence_counter", None)
            if chat_id and isinstance(turn_sequence, int):
                turn_key = build_turn_idempotency_key(chat_id, turn_sequence)

        return build_runtime_agent_output_validated_event(
            agent=agent_name or "unknown_agent",
            model_name=model_name,
            structured_data=output_data if isinstance(output_data, dict) else {"value": output_data},
            auto_tool_mode=False,
            context=context_payload,
            source="ag2_custom_event",
            turn_idempotency_key=turn_key,
            pattern_context_ref=pattern_context_ref,
            validation_passed=bool(validation_passed),
        )

    def _build_decomposition_payload(self, event: Any) -> Dict[str, Any]:
        content = getattr(event, "content", event)
        agent_name = str(
            getattr(content, "agent_name", None)
            or getattr(event, "agent_name", None)
            or "unknown_agent"
        ).strip()
        model_name = str(
            getattr(content, "model_name", None)
            or getattr(event, "model_name", None)
            or "decomposition"
        ).strip()
        structured_data = getattr(content, "structured_data", None) or {}
        context = getattr(content, "context", None) or {}
        return build_runtime_decomposition_planned_event(
            agent=agent_name,
            model_name=model_name,
            structured_data=structured_data,
            context=context,
            source="ag2_custom_event",
        )

    def _build_artifact_payload(self, event: Any, ctx: Optional[Any] = None) -> Dict[str, Any]:
        content = getattr(event, "content", event)
        action = str(getattr(content, "action", None) or "updated").strip().lower()
        if isinstance(event, ArtifactReadyEvent):
            event_type = ARTIFACT_EVENT_READY
        else:
            event_type = {
                "created": ARTIFACT_EVENT_CREATED,
                "updated": ARTIFACT_EVENT_UPDATED,
                "deleted": ARTIFACT_EVENT_DELETED,
            }.get(action, ARTIFACT_EVENT_UPDATED)
        return build_artifact_lifecycle_event(
            event_type=event_type,
            artifact_id=str(getattr(content, "artifact_id", "") or ""),
            artifact_kind=str(getattr(content, "artifact_type", "") or "artifact"),
            chat_id=str(getattr(content, "chat_id", "") or ""),
            workflow_name=str(getattr(content, "workflow_name", "") or ""),
            app_id=str(getattr(ctx, "app_id", "") or "") if ctx is not None else None,
            artifact_version_id=(
                str(getattr(content, "artifact_version_id", "") or "") or None
            ),
            metadata={"action": action} if not isinstance(event, ArtifactReadyEvent) else None,
            source="ag2_custom_event",
        )

    async def handle(self, event: Any, ctx: Optional[Any] = None, state: Optional[Any] = None) -> bool:
        """
        Handle AG2 custom event by routing to UnifiedEventDispatcher.

        Args:
            event: AG2 custom event (one of ALL_MOZAIKSAI_EVENTS)
            ctx: Optional StreamContext for additional routing info

        Returns:
            True if event was handled, False otherwise
        """
        if not self.can_handle(event):
            return False

        event_type = type(event)
        if isinstance(event, StructuredOutputEvent):
            event_dict = self._build_structured_output_payload(event, ctx, state)
        elif isinstance(event, DecompositionPlannedEvent):
            event_dict = self._build_decomposition_payload(event)
        elif isinstance(event, (ArtifactUpdatedEvent, ArtifactReadyEvent)):
            event_dict = self._build_artifact_payload(event, ctx)
        else:
            event_dict = self._event_to_dict(event)

        # Add context info if available
        if ctx is not None:
            if hasattr(ctx, 'chat_id'):
                event_dict.setdefault('chat_id', ctx.chat_id)
            if hasattr(ctx, 'app_id'):
                event_dict.setdefault('app_id', ctx.app_id)
            if hasattr(ctx, 'workflow_name'):
                event_dict.setdefault('workflow_name', ctx.workflow_name)

        # Get dispatcher event type
        dispatcher_event_type = str(event_dict.get("kind") or self.EVENT_TYPE_MAP.get(event_type, "unknown"))

        logger.info(
            "[AG2_BRIDGE] Routing %s -> %s",
            event_type.__name__,
            dispatcher_event_type,
        )

        # Emit to dispatcher
        try:
            await self.dispatcher.emit(dispatcher_event_type, event_dict)
            self._handled_count += 1
            return True
        except Exception as exc:
            logger.error(
                "[AG2_BRIDGE] Failed to emit %s: %s",
                dispatcher_event_type,
                exc,
            )
            return False

    def get_metrics(self) -> Dict[str, Any]:
        """Get bridge metrics."""
        return {
            "handled_count": self._handled_count,
            "supported_events": list(self.EVENT_TYPE_MAP.keys()),
        }


# =============================================================================
# INTEGRATION HELPERS
# =============================================================================

def get_yield_on_events() -> list:
    """
    Get the list of event types to yield on in a_run_group_chat_iter.

    Combines mozaiksai custom events with standard AG2 events.

    Usage:
        from mozaiksai.core.events.ag2_event_bridge import get_yield_on_events

        for event in a_run_group_chat_iter(
            pattern=pattern,
            messages=messages,
            yield_on=get_yield_on_events(),
        ):
            ...
    """
    return list(STANDARD_AG2_EVENTS) + list(ALL_MOZAIKSAI_EVENTS)


def create_stream_handler(dispatcher=None):
    """
    Factory function to create a stream event handler.

    Returns a handler function suitable for use in stream processing loops.

    Usage:
        handler = create_stream_handler()

        for event in response:
            if handler.can_handle(event):
                await handler.handle(event, ctx)
    """
    return AG2EventBridge(dispatcher)

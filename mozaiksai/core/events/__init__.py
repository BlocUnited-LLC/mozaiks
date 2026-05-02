# ==============================================================================
# FILE: mozaiksai/core/events/__init__.py
# DESCRIPTION: Events package initialization - unified event system exports
# ==============================================================================

"""
mozaiksai Unified Event System - Three Distinct Event Types

This package handles THREE separate event systems, each with different purposes:

1. BUSINESS EVENTS (System Monitoring & Logging)
   - Field: log_event_type  
   - Purpose: Application lifecycle, performance, monitoring
   - Usage: emit_business_event("SERVER_STARTUP_COMPLETED", "Server ready")

2. TOOL CALL REQUEST EVENTS (Agent-to-UI Communication)
   - Field: tool_name
   - Purpose: Interactive components, user input requests, dynamic UI
    - Usage: emit_tool_call_request("agent_api_key_input", {...}, workflow_name="SomeWorkflow")

3. AG2 RUNTIME EVENTS (AutoGen Workflow Events)
   - Field: kind (internal) -> type (WebSocket)  
   - Purpose: AG2 agent messages, state changes, workflow execution
   - Processed via: event_serialization.py -> WebSocket transport

Usage Examples:

    # Business events (monitoring/logging)
    from mozaiksai.core.events import emit_business_event
    await emit_business_event("WORKFLOW_STARTED", "Workflow initialized")

    # Tool call request events (agent-UI interaction)
    from mozaiksai.core.events import emit_tool_call_request
    await emit_tool_call_request("api_key_input", {"service": "openai"}, "SomeWorkflow")

    # AG2 runtime events are handled automatically by the orchestration layer
    # via event_serialization.py - no direct API needed

    # Direct dispatcher access (advanced / internal)
    from mozaiksai.core.events import get_event_dispatcher
    dispatcher = get_event_dispatcher()
    metrics = dispatcher.get_metrics()
"""

from .unified_event_dispatcher import (
    # Core classes
    UnifiedEventDispatcher,
    EventCategory,
    EventType,
    BusinessLogEvent,
    ToolCallRequestEvent,
    
    # Event handlers
    EventHandler,
    BusinessLogHandler,
    ToolCallRequestHandler,
    
    # Main functions
    get_event_dispatcher,
    emit_business_event,
    emit_tool_call_request
)

from .handoff_events import emit_handoff_event, HANDOFF_EVENT_TYPE

# AG2-native custom events (forward-compatible with AG2 beta streams)
from .ag2_events import (
    # Control events
    WorkflowTriggeredEvent,
    HandoffRequestedEvent,
    PlanCreatedEvent,
    PrerequisitesRequiredEvent,
    # Runtime events
    AgentThinkingEvent,
    StructuredOutputEvent,
    ArtifactUpdatedEvent,
    ArtifactReadyEvent,
    ToolCallRequestedEvent,
    ContextUpdatedEvent,
    # Journey events
    JourneyStartedEvent,
    JourneyCompletedEvent,
    # Helpers
    emit_ag2_event,
    emit_handoff_requested,
    emit_structured_output,
    emit_artifact_ready,
    emit_artifact_updated,
    emit_tool_call_requested,
    # Event registries
    MOZAIKSAI_CONTROL_EVENTS,
    MOZAIKSAI_RUNTIME_EVENTS,
    MOZAIKSAI_JOURNEY_EVENTS,
    ALL_MOZAIKSAI_EVENTS,
)

__all__ = [
    # Core dispatcher
    "UnifiedEventDispatcher",
    "get_event_dispatcher",
    
    # Event categories and types
    "EventCategory", 
    "EventType",
    "BusinessLogEvent",
    "ToolCallRequestEvent",
    
    # Handlers
    "EventHandler",
    "BusinessLogHandler", 
    "ToolCallRequestHandler",
    
    # Convenience functions
    "emit_business_event",
    "emit_tool_call_request",
    "emit_handoff_event",
    "HANDOFF_EVENT_TYPE",

    # AG2-native custom events
    "WorkflowTriggeredEvent",
    "HandoffRequestedEvent",
    "PlanCreatedEvent",
    "PrerequisitesRequiredEvent",
    "AgentThinkingEvent",
    "StructuredOutputEvent",
    "ArtifactUpdatedEvent",
    "ArtifactReadyEvent",
    "ToolCallRequestedEvent",
    "ContextUpdatedEvent",
    "JourneyStartedEvent",
    "JourneyCompletedEvent",

    # AG2 event helpers
    "emit_ag2_event",
    "emit_handoff_requested",
    "emit_structured_output",
    "emit_artifact_ready",
    "emit_artifact_updated",
    "emit_tool_call_requested",

    # Event registries (for yield_on)
    "MOZAIKSAI_CONTROL_EVENTS",
    "MOZAIKSAI_RUNTIME_EVENTS",
    "MOZAIKSAI_JOURNEY_EVENTS",
    "ALL_MOZAIKSAI_EVENTS",
]


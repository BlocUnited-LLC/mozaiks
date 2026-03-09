# ==============================================================================
# FILE: core/events/__init__.py
# DESCRIPTION: Events package initialization - unified event system exports
# ==============================================================================

"""
MozaiksAI Unified Event System - Three Distinct Event Types

This package handles THREE separate event systems, each with different purposes:

1. BUSINESS EVENTS (System Monitoring & Logging)
   - Field: log_event_type  
   - Purpose: Application lifecycle, performance, monitoring
   - Usage: emit_business_event("SERVER_STARTUP_COMPLETED", "Server ready")

2. UI TOOL EVENTS (Agent-to-UI Communication) 
   - Field: ui_tool_id
   - Purpose: Interactive components, user input requests, dynamic UI
    - Usage: emit_ui_tool_event("agent_api_key_input", {...}, workflow_name="SomeWorkflow")

3. AG2 RUNTIME EVENTS (AutoGen Workflow Events)
   - Field: kind (internal) -> type (WebSocket)  
   - Purpose: AG2 agent messages, state changes, workflow execution
   - Processed via: event_serialization.py -> WebSocket transport

Usage Examples:

    # Business events (monitoring/logging)
    from mozaiksai.core.events import emit_business_event
    await emit_business_event("WORKFLOW_STARTED", "Workflow initialized")

    # UI tool events (agent-UI interaction)
    from mozaiksai.core.events import emit_ui_tool_event  
    await emit_ui_tool_event("api_key_input", {"service": "openai"}, "SomeWorkflow")

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
    UIToolEvent,
    SessionPausedEvent,
    
    # Event handlers
    EventHandler,
    BusinessLogHandler,
    UIToolHandler,
    
    # Main functions
    get_event_dispatcher,
    emit_business_event,
    emit_ui_tool_event
)

from .handoff_events import emit_handoff_event, HANDOFF_EVENT_TYPE

__all__ = [
    # Core dispatcher
    "UnifiedEventDispatcher",
    "get_event_dispatcher",
    
    # Event categories and types
    "EventCategory", 
    "EventType",
    "BusinessLogEvent",
    "UIToolEvent",
    "SessionPausedEvent",
    
    # Handlers
    "EventHandler",
    "BusinessLogHandler", 
    "UIToolHandler",
    
    # Convenience functions
    "emit_business_event",
    "emit_ui_tool_event",
    "emit_handoff_event",
    "HANDOFF_EVENT_TYPE",
]


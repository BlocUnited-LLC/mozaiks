# ==============================================================================
# FILE: mozaiksai/core/events/__init__.py
# DESCRIPTION: Events package initialization - unified event system exports
# ==============================================================================

"""
mozaiksai Unified Event System

This package handles two event categories:

1. BUSINESS EVENTS (System Monitoring & Logging)
   - Purpose: Application lifecycle, performance, monitoring
   - Usage: emit_business_event("SERVER_STARTUP_COMPLETED", "Server ready")

2. TOOL CALL REQUEST EVENTS (Agent-to-UI Communication)
   - Purpose: Interactive components, user input requests, dynamic UI
   - Usage: emit_tool_call_request("agent_api_key_input", {...}, workflow_name="SomeWorkflow")

AG2 runtime events (agent messages, state changes, workflow execution) are
handled directly by the orchestration layer via event_serialization.py and
the AG2 beta MemoryStream subscription — no separate event wrapper needed.

Usage Examples:

    from mozaiksai.core.events import emit_business_event
    await emit_business_event("WORKFLOW_STARTED", "Workflow initialized")

    from mozaiksai.core.events import emit_tool_call_request
    await emit_tool_call_request("api_key_input", {"service": "openai"}, "SomeWorkflow")

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
]


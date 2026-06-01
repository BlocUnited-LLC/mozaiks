# ==============================================================================
# FILE: mozaiksai/core/workflow/stream/handlers/__init__.py
# DESCRIPTION: Event handler implementations for AG2 events
# ==============================================================================

"""
AG2 Event Handlers

Each handler processes a specific set of AG2 beta event types emitted by the
agent.ask() / MemoryStream loop:
    - TextEventHandler: TextEvent
    - InputRequestHandler: InputRequestEvent
    - SelectSpeakerHandler: SelectSpeakerEvent
    - ToolCallHandler: ToolCallsEvent, ToolCallEvent
    - ToolResponseHandler: ToolResultsEvent
    - CompletionHandler: RunCompletionEvent
    - UsageSummaryHandler: UsageSummaryEvent
    - MozaiksaiEventHandler: custom mozaiksai AG2 events
    - ErrorHandler: ErrorEvent
    - StreamingEventHandler: StreamEvent (token streaming)
"""

from .base import BaseEventHandler, DefaultEventHandler
from .text_handler import TextEventHandler
from .input_handler import InputRequestHandler
from .speaker_handler import SelectSpeakerHandler
from .tool_handler import ToolCallHandler, ToolResponseHandler
from .completion_handler import CompletionHandler, UsageSummaryHandler
from .mozaiks_event_handler import MozaiksaiEventHandler
from .error_handler import ErrorHandler
from .streaming_handler import StreamingEventHandler

__all__ = [
    # Base classes
    "BaseEventHandler",
    "DefaultEventHandler",
    # Message handlers
    "TextEventHandler",
    # Streaming handlers
    "StreamingEventHandler",
    # Input handlers
    "InputRequestHandler",
    # Speaker handlers
    "SelectSpeakerHandler",
    # Tool handlers
    "ToolCallHandler",
    "ToolResponseHandler",
    # Completion handlers
    "CompletionHandler",
    "UsageSummaryHandler",
    # Mozaiks event handler
    "MozaiksaiEventHandler",
    # Error handlers
    "ErrorHandler",
]

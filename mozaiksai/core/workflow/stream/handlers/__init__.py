# ==============================================================================
# FILE: mozaiksai/core/workflow/stream/handlers/__init__.py
# DESCRIPTION: Event handler implementations for AG2 events
# ==============================================================================

"""
AG2 Event Handlers

Each handler processes a specific set of AG2 event types:
    - TextEventHandler: TextEvent, PrintEvent
    - InputRequestHandler: InputRequestEvent
    - SelectSpeakerHandler: SelectSpeakerEvent
    - ToolCallHandler: ToolCallEvent, FunctionCallEvent
    - ToolResponseHandler: ToolResponseEvent, FunctionResponseEvent
    - CompletionHandler: RunCompletionEvent
    - UsageSummaryHandler: UsageSummaryEvent
    - TransitionHandler: AfterWorksTransitionEvent
    - MozaiksaiEventHandler: custom mozaiksai AG2 events
    - GroupChatRunHandler: GroupChatRunChatEvent
    - GroupChatResumeHandler: GroupChatResumeEvent
    - ErrorHandler: ErrorEvent
    - StreamingEventHandler: StreamEvent (token streaming)
"""

from .base import BaseEventHandler, DefaultEventHandler
from .text_handler import TextEventHandler
from .input_handler import InputRequestHandler
from .speaker_handler import SelectSpeakerHandler
from .tool_handler import ToolCallHandler, ToolResponseHandler
from .completion_handler import CompletionHandler, UsageSummaryHandler
from .transition_handler import TransitionHandler
from .mozaiks_event_handler import MozaiksaiEventHandler
from .group_chat_handler import GroupChatRunHandler, GroupChatResumeHandler
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
    # Transition handlers
    "TransitionHandler",
    "MozaiksaiEventHandler",
    # Group chat handlers
    "GroupChatRunHandler",
    "GroupChatResumeHandler",
    # Error handlers
    "ErrorHandler",
]

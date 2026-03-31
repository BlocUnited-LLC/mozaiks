# ==============================================================================
# FILE: mozaiksai/core/workflow/stream/__init__.py
# DESCRIPTION: Event stream processing module for AG2 orchestration
# ==============================================================================

"""
AG2 Event Stream Processing Module

This module provides a handler-based architecture for processing AG2 events
during workflow orchestration, replacing the monolithic if/elif event loop.

Public API:
    - EventStreamProcessor: Main entry point for event stream processing
    - StreamContext: Immutable context passed to handlers
    - StreamState: Mutable state tracked across events
    - EventHandlerRegistry: Handler dispatch registry

Usage:
    from mozaiksai.core.workflow.stream import (
        EventStreamProcessor,
        StreamContext,
        StreamState,
    )

    processor = EventStreamProcessor()
    ctx = StreamContext(chat_id=chat_id, app_id=app_id, ...)
    state = StreamState()
    result = await processor.process_stream(response, ctx, state)
"""

from .context import StreamContext, StreamState
from .registry import EventHandlerRegistry
from .processor import EventStreamProcessor

__all__ = [
    "EventStreamProcessor",
    "StreamContext",
    "StreamState",
    "EventHandlerRegistry",
]

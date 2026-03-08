"""Engine streaming support — AG2 IOStream bridge and token-level streaming.

This package provides the ``MozaiksIOStream`` implementation that bridges
AG2's internal event bus (IOStream) to the Mozaiks transport layer
(SimpleTransport / WebSocket).

Key classes:
    MozaiksIOStream — Custom IOStream that forwards AG2 StreamEvent/TextEvent/etc.
                      to SimpleTransport for real-time token-level streaming to
                      the frontend.

Usage:
    from mozaiksai.engine.streaming import MozaiksIOStream, create_iostream_bridge

    bridge = create_iostream_bridge(chat_id=chat_id, transport=transport)
    with IOStream.set_default(bridge):
        response = await a_run_group_chat(...)
"""

from mozaiksai.engine.streaming.iostream_bridge import (
    MozaiksIOStream,
    create_iostream_bridge,
)
from mozaiksai.engine.streaming.domain_event import DomainEvent, EventKind
from mozaiksai.engine.streaming.ag2_event_adapter import translate as translate_ag2_event

__all__ = [
    "MozaiksIOStream",
    "create_iostream_bridge",
    "DomainEvent",
    "EventKind",
    "translate_ag2_event",
]

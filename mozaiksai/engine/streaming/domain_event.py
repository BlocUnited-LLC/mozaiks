"""Normalized domain events for the Mozaiks pipeline.

AG2 event objects are vendor-specific and leak AG2 internals across layers.
This module defines a thin, engine-agnostic event envelope that the adapter
produces and all downstream middleware / transport code consumes.

Keeping ``raw`` on every ``DomainEvent`` means middleware that truly needs
AG2-specific fields (e.g. ``InputRequestEvent.respond``) can access them
without the rest of the pipeline importing AG2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class EventKind(str, Enum):
    """Canonical event kinds produced by the AG2 adapter."""

    TEXT = "text"
    SELECT_SPEAKER = "select_speaker"
    INPUT_REQUEST = "input_request"
    TOOL_CALL = "tool_call"
    USAGE_SUMMARY = "usage_summary"
    STREAM_CHUNK = "stream_chunk"
    RUN_COMPLETE = "run_complete"
    HANDOFF_TO_USER = "handoff_to_user"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class DomainEvent:
    """Engine-agnostic event envelope.

    Attributes
    ----------
    kind : EventKind
        Canonical event category.
    agent : str | None
        Agent that produced the event (source / sender).
    content : Any
        Primary payload — the text, token chunk, usage dict, etc.
    metadata : dict
        Additional fields extracted from the AG2 event that don't fit
        neatly into *content* (e.g. ``request_id``, ``respond`` callback,
        candidate agent list, tool name, cost breakdown).
    raw : Any
        The original AG2 event object, preserved so downstream layers
        can access vendor-specific fields without importing AG2.
    sequence : int
        Monotonically increasing counter assigned by the event loop.
    """

    kind: EventKind
    agent: Optional[str] = None
    content: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Any = None
    sequence: int = 0

"""Compatibility shim — implementation moved to ``mozaiksai.adapters.ag2.event_builders``.

This file re-exports everything from the adapter layer so that existing
code importing from ``mozaiksai.engine.events.serialization`` continues
to work unchanged.  New code should import directly from the adapter layer.
"""

from __future__ import annotations  # noqa: F401

# Re-export the full public API from the canonical adapter module
from mozaiksai.adapters.ag2.event_builders import (  # noqa: F401
    EventBuildContext,
    normalize_text_content,
    serialize_event_content,
    extract_agent_name,
    build_ui_event_payload,
    build_structured_output_ready_event,
)

__all__ = [
    "EventBuildContext",
    "normalize_text_content",
    "serialize_event_content",
    "extract_agent_name",
    "build_ui_event_payload",
    "build_structured_output_ready_event",
]

# ---------------------------------------------------------------------------
# STOP — do NOT add new code here.  Add it to
# mozaiksai/adapters/ag2/event_builders.py instead.
# ---------------------------------------------------------------------------
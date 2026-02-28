"""
Message handling utilities.

Provides message normalization, text extraction, and serialization.
"""

from .utils import (
    normalize_to_strict_ag2,
    normalize_text_content,
    serialize_event_content,
    extract_agent_name,
    safe_context_snapshot,
)

__all__ = [
    'normalize_to_strict_ag2',
    'normalize_text_content',
    'serialize_event_content',
    'extract_agent_name',
    'safe_context_snapshot',
]


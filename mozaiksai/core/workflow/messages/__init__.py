"""
Message handling utilities.

Provides message normalization, text extraction, and serialization.
"""

from .utils import (
    extract_agent_name,
    normalize_text_content,
    normalize_to_strict_ag2,
    serialize_event_content,
)

__all__ = [
    'normalize_to_strict_ag2',
    'normalize_text_content',
    'serialize_event_content',
    'extract_agent_name',
]


"""Compatibility shim — implementation moved to ``mozaiksai.adapters.ag2.messages``.

This file re-exports everything from the adapter layer so that existing
code importing from ``mozaiksai.engine.*`` continues to work unchanged.
New code should import directly from the adapter layer.
"""

from __future__ import annotations

from mozaiksai.adapters.ag2.messages import *  # noqa: F401, F403

__all__ = ['normalize_to_strict_ag2', 'normalize_text_content', 'serialize_event_content', 'extract_agent_name', 'safe_context_snapshot', 'extract_images_from_conversation']

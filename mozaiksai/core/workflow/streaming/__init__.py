# ==============================================================================
# FILE: core/workflow/streaming/__init__.py
# DESCRIPTION: Streaming utilities for real-time token emission
# ==============================================================================

"""
Streaming Module

Provides utilities for real-time token streaming from LLM responses
to WebSocket clients, enabling typewriter effect in the frontend.

Components:
    - install_streaming_wrapper: Patches OpenAI client for streaming
    - uninstall_streaming_wrapper: Restores original behavior
    - update_streaming_agent: Updates current agent for chunk attribution
"""

from .openai_streaming_wrapper import (
    install_streaming_wrapper,
    uninstall_streaming_wrapper,
    update_streaming_agent,
    set_streaming_context,
    clear_streaming_context,
)

__all__ = [
    "install_streaming_wrapper",
    "uninstall_streaming_wrapper",
    "update_streaming_agent",
    "set_streaming_context",
    "clear_streaming_context",
]

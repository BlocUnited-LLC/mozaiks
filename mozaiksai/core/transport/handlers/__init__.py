# ==============================================================================
# FILE: mozaiksai/core/transport/handlers/__init__.py
# DESCRIPTION: WebSocket message handlers for SimpleTransport
# ==============================================================================
"""
WebSocket message handlers extracted from SimpleTransport.

This module provides focused handler functions for each WebSocket message type,
improving code organization and maintainability.

Usage:
    from mozaiksai.core.transport.handlers import MESSAGE_HANDLERS

    handler = MESSAGE_HANDLERS.get(message_type)
    if handler:
        await handler(transport, data, chat_id, websocket)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Coroutine, Dict

from fastapi import WebSocket

from .base import ERROR_CODES, utc_timestamp
from .input_handlers import handle_tool_call_response, handle_user_input_submit
from .mode_handlers import handle_enter_general_mode, handle_start_general_chat
from .session_handlers import handle_artifact_action, handle_client_resume
from .workflow_handlers import (
    handle_start_workflow,
    handle_start_workflow_batch,
    handle_switch_workflow,
)

if TYPE_CHECKING:
    from mozaiksai.core.transport.simple_transport import SimpleTransport

# Type alias for handler functions
HandlerFunc = Callable[
    ["SimpleTransport", Dict[str, Any], str, WebSocket],
    Coroutine[Any, Any, None]
]

# Message type to handler mapping
MESSAGE_HANDLERS: Dict[str, HandlerFunc] = {
    "user.input.submit": handle_user_input_submit,
    "user_input_submit": handle_user_input_submit,
    "tool_call_response": handle_tool_call_response,
    "chat.artifact_action": handle_artifact_action,
    "chat.switch_workflow": handle_switch_workflow,
    "chat.enter_general_mode": handle_enter_general_mode,
    "chat.start_general_chat": handle_start_general_chat,
    "chat.start_workflow": handle_start_workflow,
    "chat.start_workflow_batch": handle_start_workflow_batch,
    "client.resume": handle_client_resume,
}

__all__ = [
    # Handler registry
    "MESSAGE_HANDLERS",
    "ERROR_CODES",
    # Utilities
    "utc_timestamp",
    # Individual handlers (for direct import if needed)
    "handle_user_input_submit",
    "handle_tool_call_response",
    "handle_switch_workflow",
    "handle_enter_general_mode",
    "handle_start_general_chat",
    "handle_start_workflow",
    "handle_start_workflow_batch",
    "handle_client_resume",
    "handle_artifact_action",
]

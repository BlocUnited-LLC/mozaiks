# ==============================================================================
# FILE: core/transport/handlers/mode_handlers.py
# DESCRIPTION: Handlers for general mode and mode switching operations
# ==============================================================================
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from fastapi import WebSocket

from mozaiksai.core.transport.session_registry import session_registry

from .base import logger, utc_timestamp

if TYPE_CHECKING:
    from mozaiksai.core.transport.simple_transport import SimpleTransport


async def handle_enter_general_mode(
    transport: "SimpleTransport",
    data: Dict[str, Any],
    chat_id: str,
    websocket: WebSocket,
) -> None:
    """Handle chat.enter_general_mode message type."""
    ws_id = transport._get_conn_meta(chat_id).get("ws_id")
    if not ws_id:
        raise ValueError("WebSocket ID not found in connection metadata")

    session_registry.enter_general_mode(ws_id)
    general_ctx = await transport._ensure_general_chat_context(chat_id=chat_id)
    general_chat_id = general_ctx.get("chat_id")

    logger.info(f"Entered general mode (ws_id={ws_id}, general_chat={general_chat_id})")

    await websocket.send_json({
        "type": "chat.mode_changed",
        "data": {
            "mode": "general",
            "general_chat_id": general_ctx.get("chat_id"),
            "general_chat_label": general_ctx.get("label"),
            "general_chat_sequence": general_ctx.get("sequence"),
        },
        "timestamp": utc_timestamp()
    })


async def handle_start_general_chat(
    transport: "SimpleTransport",
    data: Dict[str, Any],
    chat_id: str,
    websocket: WebSocket,
) -> None:
    """Handle chat.start_general_chat message type."""
    ws_id = transport._get_conn_meta(chat_id).get("ws_id")
    if not ws_id:
        raise ValueError("WebSocket ID not found in connection metadata")

    session_registry.enter_general_mode(ws_id)
    general_ctx = await transport._ensure_general_chat_context(chat_id=chat_id, force_new=True)

    await websocket.send_json({
        "type": "chat.general_session_created",
        "data": {
            "general_chat_id": general_ctx.get("chat_id"),
            "general_chat_label": general_ctx.get("label"),
            "general_chat_sequence": general_ctx.get("sequence"),
        },
        "timestamp": utc_timestamp()
    })
    logger.info(f"Started new general chat session {general_ctx.get('chat_id')} (ws_id={ws_id})")

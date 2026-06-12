# ==============================================================================
# FILE: mozaiksai/core/transport/handlers/session_handlers.py
# DESCRIPTION: Handlers for session management (resume, artifact actions)
# ==============================================================================
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import WebSocket

if TYPE_CHECKING:
    from mozaiksai.core.transport.simple_transport import SimpleTransport


async def handle_client_resume(
    transport: SimpleTransport,
    data: dict[str, Any],
    chat_id: str,
    websocket: WebSocket,
) -> None:
    """Handle client.resume message type."""
    last_client_index = data.get("lastClientIndex")
    if not isinstance(last_client_index, int):
        raise ValueError("lastClientIndex must be int")
    await transport._handle_resume_request(chat_id, last_client_index, websocket)


async def handle_artifact_action(
    transport: SimpleTransport,
    data: dict[str, Any],
    chat_id: str,
    websocket: WebSocket,
) -> None:
    """Handle chat.artifact_action message type.

    This is a thin wrapper that delegates to transport._handle_artifact_action
    which contains the full implementation.
    """
    await transport._handle_artifact_action(data, chat_id, websocket)

# ==============================================================================
# FILE: core/transport/handlers/base.py
# DESCRIPTION: Base protocol and utilities for WebSocket message handlers
# ==============================================================================
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Dict, Protocol

from fastapi import WebSocket

if TYPE_CHECKING:
    from mozaiksai.core.transport.simple_transport import SimpleTransport

logger = logging.getLogger("simple_transport.handlers")


def utc_timestamp() -> str:
    """Return current UTC timestamp in ISO format for WebSocket messages."""
    return datetime.now(timezone.utc).isoformat()


class TransportProtocol(Protocol):
    """Protocol defining the transport interface handlers can use."""

    connections: Dict[str, Dict[str, Any]]
    _background_tasks: Dict[str, Any]

    def _get_conn_meta(self, chat_id: str) -> Dict[str, Any]: ...
    async def _send_ws_error(self, websocket: WebSocket, message: str, error_code: str) -> None: ...
    async def submit_user_input(self, input_request_id: str, user_input: str) -> bool: ...
    async def submit_ui_tool_response(self, event_id: str, response_data: Dict[str, Any]) -> bool: ...
    async def process_incoming_user_message(self, *, chat_id: str, user_id: str, content: str, source: str) -> None: ...
    async def _handle_general_agent_exchange(self, *, chat_id: str, ws_id: Any, user_message: str, ui_context: Dict[str, Any]) -> None: ...
    async def _ensure_general_chat_context(self, *, chat_id: str, force_new: bool = False) -> Dict[str, Any]: ...
    def _get_or_create_persistence_manager(self) -> Any: ...
    async def _run_workflow_background(self, *, chat_id: str, workflow_name: str, app_id: str, user_id: str, ws_id: Any, initial_message: str | None, initial_agent_name_override: str | None) -> None: ...
    async def _handle_resume_request(self, chat_id: str, last_client_index: int, websocket: WebSocket) -> None: ...
    async def _handle_artifact_action(self, event: Dict[str, Any], chat_id: str, websocket: WebSocket) -> None: ...


# Type alias for handler functions
HandlerFunc = Callable[
    ["SimpleTransport", Dict[str, Any], str, WebSocket],
    Coroutine[Any, Any, None]
]


# Error codes for each message type
ERROR_CODES: Dict[str, str] = {
    "user.input.submit": "USER_INPUT_FAILED",
    "user_input_submit": "USER_INPUT_FAILED",
    "ui_tool_response": "UI_TOOL_RESPONSE_FAILED",
    "chat.artifact_action": "ARTIFACT_ACTION_FAILED",
    "chat.switch_workflow": "SWITCH_WORKFLOW_FAILED",
    "chat.enter_general_mode": "GENERAL_MODE_FAILED",
    "chat.start_general_chat": "GENERAL_CHAT_CREATE_FAILED",
    "chat.start_workflow": "START_WORKFLOW_FAILED",
    "chat.start_workflow_batch": "START_WORKFLOW_BATCH_FAILED",
    "client.resume": "RESUME_FAILED",
}

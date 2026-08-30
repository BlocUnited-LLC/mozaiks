# ==============================================================================
# FILE: mozaiksai/core/transport/handlers/input_handlers.py
# DESCRIPTION: Handlers for user input and UI tool response messages
# ==============================================================================
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import WebSocket

from mozaiksai.core.transport.event_contract import send_event_envelope
from mozaiksai.core.transport.session_registry import session_registry

from .base import logger, utc_timestamp

if TYPE_CHECKING:
    from mozaiksai.core.transport.simple_transport import SimpleTransport


async def handle_user_input_submit(
    transport: SimpleTransport,
    data: dict[str, Any],
    chat_id: str,
    websocket: WebSocket,
) -> None:
    """Handle user.input.submit / user_input_submit message type."""
    req_id = data.get('input_request_id') or data.get('request_id')
    text = (data.get('text') or data.get('user_input') or "").strip()
    conn = transport._get_conn_meta(chat_id)
    ws_id = conn.get("ws_id")
    ui_context_payload = data.get("context") or data.get("ui_context") or {}
    if not isinstance(ui_context_payload, dict):
        ui_context_payload = {}
    requested_general_chat_id = (
        ui_context_payload.get("general_chat_id")
        or ui_context_payload.get("requested_general_chat_id")
        or data.get("general_chat_id")
    )
    if requested_general_chat_id:
        ui_context_payload["requested_general_chat_id"] = requested_general_chat_id

    logger.debug("INPUT_SUBMIT chat=%s req_id=%s text_len=%d ws_id=%s", chat_id, req_id, len(text), ws_id)

    is_general_mode = bool(ws_id and session_registry.is_in_general_mode(ws_id))
    logger.debug("INPUT_MODE_CHECK is_general=%s has_req_id=%s", is_general_mode, bool(req_id))

    # General mode without request_id
    if not req_id and is_general_mode:
        if not text:
            await transport._send_ws_error(websocket, "Message cannot be empty in general mode", "GENERAL_MODE_EMPTY_MESSAGE")
            return
        try:
            await transport._handle_general_agent_exchange(
                chat_id=chat_id,
                ws_id=ws_id,
                user_message=text,
                ui_context=ui_context_payload,
            )
            await send_event_envelope(websocket, {
                "schema_version": "mozaiks.ui.event.v1",
                "type": "chat.input_ack",
                "data": {"chat_id": chat_id, "status": "accepted"},
                "timestamp": utc_timestamp()
            })
        except Exception as general_err:
            logger.error("Failed to process general-mode message for %s: %s", chat_id, general_err, exc_info=True)
            await transport._send_ws_error(websocket, "General mode is unavailable right now. Please try again.", "GENERAL_MODE_FAILED")
        return

    if req_id:
        logger.warning("[INPUT] request_id=%s is no longer accepted on user.input.submit; use tool_call_response instead", req_id)
        await transport._send_ws_error(
            websocket,
            "Response-required interactions must be answered with tool_call_response",
            "INPUT_REQUEST_SUBMIT_REMOVED",
        )
        return

    # Free-form user message (no pending request)
    try:
        target_chat_id = chat_id
        target_workflow_name = conn.get("workflow_name")
        target_app_id = conn.get("app_id")
        target_user_id = conn.get("user_id")
        try:
            if ws_id:
                active_ctx = session_registry.get_active_workflow(ws_id)
                if active_ctx and getattr(active_ctx, "chat_id", None):
                    target_chat_id = str(active_ctx.chat_id)
                    target_workflow_name = getattr(active_ctx, "workflow_name", None) or target_workflow_name
                    target_app_id = getattr(active_ctx, "app_id", None) or target_app_id
                    target_user_id = getattr(active_ctx, "user_id", None) or target_user_id
        except Exception:
            target_chat_id = chat_id

        target_conn = transport._get_conn_meta(target_chat_id)
        target_workflow_name = target_conn.get("workflow_name") or target_workflow_name
        target_app_id = target_conn.get("app_id") or target_app_id
        target_user_id = target_conn.get("user_id") or target_user_id

        has_workflow_context = bool(
            isinstance(target_workflow_name, str)
            and target_workflow_name.strip()
            and target_app_id
        )

        # Workflow-mode composer input must always route back through the
        # orchestration bridge when workflow context exists. Startup mode only
        # decides who speaks first at session launch; it must not block later
        # human turns after a pause or review checkpoint.
        if has_workflow_context:
            if not target_app_id or not target_workflow_name:
                await transport._send_ws_error(
                    websocket,
                    "Missing workflow context for workflow message",
                    "WORKFLOW_CONTEXT_MISSING",
                )
                return
            await transport.handle_user_input_from_api(
                chat_id=target_chat_id,
                user_id=str(target_user_id or conn.get("user_id") or ""),
                workflow_name=str(target_workflow_name),
                message=text,
                app_id=str(target_app_id),
            )
        else:
            await transport.process_incoming_user_message(
                chat_id=target_chat_id,
                user_id=target_user_id or conn.get('user_id'),
                content=text,
                source='ws'
            )

        await send_event_envelope(websocket, {
            "schema_version": "mozaiks.ui.event.v1",
            "type": "chat.input_ack",
            "data": {"chat_id": target_chat_id, "status": "accepted"},
            "timestamp": utc_timestamp()
        })
    except Exception as e:
        logger.error("Failed to process free-form user message for %s: %s", chat_id, e, exc_info=True)
        await transport._send_ws_error(websocket, "User message failed", "USER_MESSAGE_FAILED")


async def handle_tool_call_response(
    transport: SimpleTransport,
    data: dict[str, Any],
    chat_id: str,
    websocket: WebSocket,
) -> None:
    """Handle tool_call_response message type."""
    event_id = data.get('tool_call_id')
    response_data = data.get('response', {})
    if not event_id:
        return
    try:
        ok = await transport.submit_tool_call_response(event_id, response_data)
        logger.debug("TOOL_CALL_RESPONSE_RECEIVED event=%s accepted=%s", event_id, ok)
        await send_event_envelope(websocket, {
            "schema_version": "mozaiks.ui.event.v1",
            "type": "ack.tool_call_response",
            "data": {"tool_call_id": event_id, "status": "accepted" if ok else "rejected"},
            "timestamp": utc_timestamp()
        })
    except Exception as uie:
        logger.error("Failed to process tool call response %s: %s", event_id, uie, exc_info=True)
        await transport._send_ws_error(websocket, "Tool call response failed", "TOOL_CALL_RESPONSE_FAILED")

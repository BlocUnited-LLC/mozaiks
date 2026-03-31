# ==============================================================================
# FILE: mozaiksai/core/transport/handlers/input_handlers.py
# DESCRIPTION: Handlers for user input and UI tool response messages
# ==============================================================================
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from fastapi import WebSocket

from mozaiksai.core.transport.session_registry import session_registry

from .base import logger, utc_timestamp

if TYPE_CHECKING:
    from mozaiksai.core.transport.simple_transport import SimpleTransport


async def handle_user_input_submit(
    transport: "SimpleTransport",
    data: Dict[str, Any],
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

    logger.info(f"[INPUT] Received user.input.submit: chat={chat_id}, req_id={req_id}, text_len={len(text)}, ws_id={ws_id}")

    is_general_mode = bool(ws_id and session_registry.is_in_general_mode(ws_id))
    logger.info(f"[INPUT] Mode check: is_general={is_general_mode}, has_req_id={bool(req_id)}")

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
            await websocket.send_json({
                "type": "chat.input_ack",
                "data": {"chat_id": chat_id, "status": "accepted"},
                "timestamp": utc_timestamp()
            })
        except Exception as general_err:
            logger.error(f"Failed to process general-mode message for {chat_id}: {general_err}")
            await transport._send_ws_error(websocket, "General mode is unavailable right now. Please try again.", "GENERAL_MODE_FAILED")
        return

    # AG2 InputRequestEvent response
    if req_id:
        logger.info(f"[INPUT] Routing to submit_user_input for AG2 InputRequestEvent: req_id={req_id}")
        try:
            ok = await transport.submit_user_input(req_id, text)
            logger.info(f"[INPUT] submit_user_input returned: {ok} for req_id={req_id}")
            await websocket.send_json({
                "type": "ack.input",
                "data": {"input_request_id": req_id, "status": "accepted" if ok else "rejected"},
                "timestamp": utc_timestamp()
            })
        except Exception as ie:
            logger.error(f"Failed to process inbound user input {req_id}: {ie}", exc_info=True)
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

        workflow_startup_mode = "AgentDriven"
        if isinstance(target_workflow_name, str) and target_workflow_name.strip():
            try:
                from mozaiksai.core.workflow.workflow_manager import workflow_manager

                cfg = workflow_manager.get_config(str(target_workflow_name))
                workflow_startup_mode = str((cfg or {}).get("workflow_startup_mode", "AgentDriven"))
            except Exception:
                workflow_startup_mode = "AgentDriven"

        # For UserDriven workflows, the first free-form user message should
        # start (or continue) orchestration through the same smart router used
        # by HTTP input. For other modes, persist the message without routing it.
        if workflow_startup_mode == "UserDriven":
            if not target_app_id or not target_workflow_name:
                await transport._send_ws_error(
                    websocket,
                    "Missing workflow context for UserDriven message",
                    "USER_DRIVEN_CONTEXT_MISSING",
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

        await websocket.send_json({
            "type": "chat.input_ack",
            "data": {"chat_id": target_chat_id, "status": "accepted"},
            "timestamp": utc_timestamp()
        })
    except Exception as e:
        logger.error(f"Failed to process free-form user message for {chat_id}: {e}")
        await transport._send_ws_error(websocket, "User message failed", "USER_MESSAGE_FAILED")


async def handle_ui_tool_response(
    transport: "SimpleTransport",
    data: Dict[str, Any],
    chat_id: str,
    websocket: WebSocket,
) -> None:
    """Handle ui_tool_response message type."""
    event_id = data.get('eventId') or data.get('ui_tool_id')
    response_data = data.get('response', {})
    if not event_id:
        return
    try:
        ok = await transport.submit_ui_tool_response(event_id, response_data)
        logger.info(f"UI tool response received for event {event_id}: {ok}")
        await websocket.send_json({
            "type": "ack.ui_tool_response",
            "data": {"eventId": event_id, "status": "accepted" if ok else "rejected"},
            "timestamp": utc_timestamp()
        })
    except Exception as uie:
        logger.error(f"Failed to process UI tool response {event_id}: {uie}")
        await transport._send_ws_error(websocket, "UI tool response failed", "UI_TOOL_RESPONSE_FAILED")

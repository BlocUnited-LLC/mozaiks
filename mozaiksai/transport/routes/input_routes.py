"""User input, component action, and UI-tool submission routes."""
from __future__ import annotations

import json
import logging
from datetime import datetime, UTC

from fastapi import APIRouter, Depends, HTTPException, Request

from mozaiksai.runtime.auth import UserPrincipal, require_user_scope
from mozaiksai.runtime.auth.dependencies import (
    validate_user_id_against_principal as _validate_user_id_against_principal,
)
from mozaiksai.runtime.multitenant import build_app_scope_filter
from logs.logging_config import get_workflow_logger

logger = logging.getLogger(__name__)
wf_logger = get_workflow_logger("input_routes")

router = APIRouter(tags=["input"])


# ---------------------------------------------------------------------------
# User message input
# ---------------------------------------------------------------------------


@router.post("/chat/{app_id}/{chat_id}/{user_id}/input")
async def handle_user_input(
    request: Request,
    app_id: str,
    chat_id: str,
    user_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Receive user input and trigger the workflow."""
    user_id = _validate_user_id_against_principal(principal, path_user_id=user_id)

    simple_transport = request.app.state.simple_transport
    persistence_manager = request.app.state.persistence_manager

    if not simple_transport:
        raise HTTPException(status_code=503, detail="Transport service is not available.")

    try:
        # Ownership check
        try:
            coll = await persistence_manager._coll()
            owned = await coll.find_one(
                {"_id": chat_id, "user_id": user_id, **build_app_scope_filter(app_id)},
                {"_id": 1},
            )
            if not owned:
                raise HTTPException(status_code=404, detail="Chat not found")
        except HTTPException:
            raise
        except Exception as owner_err:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to validate chat ownership: {owner_err}",
            )

        data = await request.json()
        message = data.get("message")
        workflow_name = data.get("workflow_name")

        wf_logger.info(
            "USER_INPUT_ENDPOINT_CALLED: User input endpoint called",
            app_id=app_id,
            chat_id=chat_id,
            user_id=user_id,
            workflow_name=workflow_name,
            message_length=(len(message) if message else 0),
        )

        if not message:
            raise HTTPException(status_code=400, detail="Message cannot be empty.")

        result = await simple_transport.handle_user_input_from_api(
            chat_id=chat_id,
            user_id=user_id,
            workflow_name=workflow_name,
            message=message,
            app_id=app_id,
        )

        wf_logger.info(
            "USER_INPUT_PROCESSED: User input processed successfully",
            chat_id=chat_id,
            transport=result.get("transport"),
        )

        return {
            "status": "Message received and is being processed.",
            "transport": result.get("transport"),
        }
    except HTTPException:
        raise
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")
    except Exception as e:
        logger.error(f"Error handling user input for chat {chat_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to process input: {e}")


# ---------------------------------------------------------------------------
# Submit user-input response (from AG2 input_request)
# ---------------------------------------------------------------------------


@router.post("/api/user-input/submit")
async def submit_user_input_response(
    request: Request,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Submit a user input response triggered by an AG2 agent input request."""
    simple_transport = request.app.state.simple_transport

    if not simple_transport:
        raise HTTPException(status_code=503, detail="Transport service is not available.")

    try:
        data = await request.json()
        input_request_id = data.get("input_request_id")
        user_input = data.get("user_input")

        if not input_request_id:
            raise HTTPException(
                status_code=400, detail="'input_request_id' field is required."
            )
        if not user_input:
            raise HTTPException(
                status_code=400, detail="'user_input' field is required."
            )

        success = await simple_transport.submit_user_input(input_request_id, user_input)

        if success:
            wf_logger.info(
                "USER_INPUT_RESPONSE_SUBMITTED: User input response submitted",
                input_request_id=input_request_id,
                input_length=len(user_input),
            )
            return {
                "status": "success",
                "message": "User input submitted successfully",
            }
        else:
            raise HTTPException(
                status_code=404,
                detail="Input request not found or already completed",
            )
    except HTTPException:
        raise
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")
    except Exception as e:
        logger.error(f"Error submitting user input response: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to submit user input: {e}"
        )


# ---------------------------------------------------------------------------
# Component action (ContextVariables)
# ---------------------------------------------------------------------------


@router.post("/chat/{app_id}/{chat_id}/component_action")
async def handle_component_action(
    request: Request,
    app_id: str,
    chat_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Receive component actions for AG2 ContextVariables (WebSocket support)."""
    simple_transport = request.app.state.simple_transport
    persistence_manager = request.app.state.persistence_manager

    if not simple_transport:
        raise HTTPException(status_code=503, detail="Transport service is not available.")

    try:
        # Ownership check
        if principal.user_id != "anonymous":
            try:
                coll = await persistence_manager._coll()
                owned = await coll.find_one(
                    {
                        "_id": chat_id,
                        "user_id": principal.user_id,
                        **build_app_scope_filter(app_id),
                    },
                    {"_id": 1},
                )
                if not owned:
                    raise HTTPException(status_code=404, detail="Chat not found")
            except HTTPException:
                raise
            except Exception as owner_err:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to validate chat ownership: {owner_err}",
                )

        data = await request.json()
        component_id = data.get("component_id")
        action_type = data.get("action_type")
        action_data = data.get("action_data", {})

        wf_logger.info(
            "COMPONENT_ACTION_ENDPOINT_CALLED: Component action endpoint called",
            app_id=app_id,
            chat_id=chat_id,
            component_id=component_id,
            action_type=action_type,
        )

        if not component_id or not action_type:
            raise HTTPException(
                status_code=400,
                detail="'component_id' and 'action_type' fields are required.",
            )

        logger.info(f"Component action via HTTP: {component_id} -> {action_type}")

        try:
            result = await simple_transport.process_component_action(
                chat_id=chat_id,
                app_id=app_id,
                component_id=component_id,
                action_type=action_type,
                action_data=action_data or {},
            )
            wf_logger.info(
                "COMPONENT_ACTION_PROCESSED: Component action processed successfully",
                chat_id=chat_id,
                component_id=component_id,
                action_type=action_type,
                applied_keys=list((result.get("applied") or {}).keys()),
            )
            return {
                "status": "success",
                "message": "Component action applied",
                "applied": result.get("applied"),
                "timestamp": datetime.now(UTC).isoformat(),
            }
        except Exception as action_error:
            logger.error(f"Component action failed: {action_error}")
            raise HTTPException(
                status_code=500,
                detail=f"Component action failed: {action_error}",
            )
    except HTTPException:
        raise
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")
    except Exception as e:
        logger.error(f"Error handling component action for chat {chat_id}: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to process component action: {e}"
        )


# ---------------------------------------------------------------------------
# UI tool response
# ---------------------------------------------------------------------------


@router.post("/api/ui-tool/submit")
async def submit_ui_tool_response(
    request: Request,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Submit UI tool responses (e.g. AgentAPIKeyInput, FileDownloadCenter)."""
    simple_transport = request.app.state.simple_transport

    if not simple_transport:
        raise HTTPException(status_code=503, detail="Transport service is not available.")

    try:
        data = await request.json()
        event_id = data.get("event_id")
        response_data = data.get("response_data")

        if not event_id:
            raise HTTPException(
                status_code=400, detail="'event_id' field is required."
            )
        if not response_data:
            raise HTTPException(
                status_code=400, detail="'response_data' field is required."
            )

        success = await simple_transport.submit_ui_tool_response(event_id, response_data)

        if success:
            wf_logger.info(
                "UI_TOOL_RESPONSE_SUBMITTED: UI tool response submitted",
                event_id=event_id,
                response_status=response_data.get("status", "unknown"),
                ui_tool_id=response_data.get("data", {}).get("ui_tool_id", "unknown"),
            )
            return {
                "status": "success",
                "message": "UI tool response submitted successfully",
            }
        else:
            raise HTTPException(
                status_code=404,
                detail="UI tool event not found or already completed",
            )
    except HTTPException:
        raise
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")
    except Exception as e:
        logger.error(f"Error submitting UI tool response: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to submit UI tool response: {e}"
        )

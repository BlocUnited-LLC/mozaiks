# ==============================================================================
# FILE: core/transport/handlers/workflow_handlers.py
# DESCRIPTION: Handlers for workflow start, switch, and batch operations
# ==============================================================================
from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any, Dict, List

from fastapi import WebSocket

from mozaiksai.core.transport.session_registry import session_registry

from .base import logger, utc_timestamp

if TYPE_CHECKING:
    from mozaiksai.core.transport.simple_transport import SimpleTransport


async def handle_switch_workflow(
    transport: "SimpleTransport",
    data: Dict[str, Any],
    chat_id: str,
    websocket: WebSocket,
) -> None:
    """Handle chat.switch_workflow message type."""
    target_chat_id = data.get("chat_id")
    frontend_context = data.get("frontend_context")

    if not target_chat_id:
        raise ValueError("chat_id required for workflow switch")

    ws_id = transport._get_conn_meta(chat_id).get("ws_id")
    if not ws_id:
        raise ValueError("WebSocket ID not found in connection metadata")

    # Store frontend context in connection metadata
    if frontend_context and isinstance(frontend_context, dict):
        if target_chat_id not in transport.connections:
            transport.connections[target_chat_id] = {}
        transport.connections[target_chat_id]["frontend_context"] = frontend_context
        logger.info(f"Stored frontend context for {target_chat_id}: {list(frontend_context.keys())}")

    # Switch workflow context
    active_context = session_registry.switch_workflow(ws_id, target_chat_id)
    if not active_context:
        raise ValueError(f"Workflow {target_chat_id} not found or already completed")

    logger.info(f"Switched from {chat_id} to {target_chat_id} (ws_id={ws_id})")

    await websocket.send_json({
        "type": "chat.context_switched",
        "data": {
            "from_chat_id": chat_id,
            "to_chat_id": target_chat_id,
            "workflow_name": active_context.workflow_name,
            "artifact_id": active_context.artifact_id,
            "app_id": active_context.app_id
        },
        "timestamp": utc_timestamp()
    })


async def handle_start_workflow(
    transport: "SimpleTransport",
    data: Dict[str, Any],
    chat_id: str,
    websocket: WebSocket,
) -> None:
    """Handle chat.start_workflow message type."""
    target_workflow = data.get("workflow_name")
    initial_message = data.get("initial_message") or data.get("message")
    auto_run = bool(data.get("auto_run", True))
    initial_agent_name_override = data.get("initial_agent") or data.get("initial_agent_name")
    frontend_context = data.get("frontend_context")

    if not target_workflow:
        raise ValueError("workflow_name required")

    conn = transport._get_conn_meta(chat_id)
    ws_id = conn.get("ws_id")
    ent_id = conn.get("app_id")
    usr_id = conn.get("user_id")

    if not ws_id or not ent_id or not usr_id:
        raise ValueError("Missing connection metadata")

    # Validate pack prerequisites
    from mozaiksai.core.workflow.pack.gating import validate_pack_prereqs
    pm = transport._get_or_create_persistence_manager()
    ok, prereq_error = await validate_pack_prereqs(
        app_id=str(ent_id),
        user_id=str(usr_id),
        workflow_name=str(target_workflow),
        persistence=pm,
    )
    if not ok:
        await websocket.send_json({
            "type": "chat.prereq_blocked",
            "data": {
                "workflow_name": str(target_workflow),
                "message": prereq_error or "Prerequisites not met",
                "error_code": "WORKFLOW_PREREQS_NOT_MET",
            },
            "chat_id": chat_id,
            "timestamp": utc_timestamp(),
        })
        return

    # Create new chat session
    new_chat_id = f"chat_{target_workflow}_{uuid.uuid4().hex[:8]}"
    await pm.create_chat_session(
        chat_id=new_chat_id,
        app_id=str(ent_id),
        workflow_name=str(target_workflow),
        user_id=str(usr_id),
    )

    # Store frontend context
    if frontend_context and isinstance(frontend_context, dict):
        if new_chat_id not in transport.connections:
            transport.connections[new_chat_id] = {}
        transport.connections[new_chat_id]["frontend_context"] = frontend_context
        logger.info(f"Stored frontend context for new workflow {new_chat_id}: {list(frontend_context.keys())}")

    # Register in session registry
    session_registry.add_workflow(
        ws_id=ws_id,
        chat_id=new_chat_id,
        workflow_name=target_workflow,
        app_id=ent_id,
        user_id=usr_id,
        auto_activate=True
    )

    logger.info(f"Started new workflow {target_workflow} (chat_id={new_chat_id}, ws_id={ws_id})")

    await websocket.send_json({
        "type": "chat.workflow_started",
        "data": {
            "chat_id": new_chat_id,
            "workflow_name": target_workflow,
            "app_id": ent_id,
            "user_id": usr_id
        },
        "timestamp": utc_timestamp()
    })

    # Auto-run if requested
    if auto_run:
        transport._background_tasks[new_chat_id] = asyncio.create_task(
            transport._run_workflow_background(
                chat_id=new_chat_id,
                workflow_name=str(target_workflow),
                app_id=str(ent_id),
                user_id=str(usr_id),
                ws_id=ws_id,
                initial_message=str(initial_message) if isinstance(initial_message, str) and initial_message.strip() else None,
                initial_agent_name_override=str(initial_agent_name_override) if isinstance(initial_agent_name_override, str) and initial_agent_name_override.strip() else None,
            )
        )


async def handle_start_workflow_batch(
    transport: "SimpleTransport",
    data: Dict[str, Any],
    chat_id: str,
    websocket: WebSocket,
) -> None:
    """Handle chat.start_workflow_batch message type."""
    runs = data.get("runs")
    activate_first = bool(data.get("activate_first", False))
    auto_run = bool(data.get("auto_run", True))

    conn = transport._get_conn_meta(chat_id)
    ws_id = conn.get("ws_id")
    ent_id = conn.get("app_id")
    usr_id = conn.get("user_id")
    if not ws_id or not ent_id or not usr_id:
        raise ValueError("Missing connection metadata")

    if not isinstance(runs, list) or not runs:
        raise ValueError("runs must be a non-empty list")

    pm = transport._get_or_create_persistence_manager()
    from mozaiksai.core.workflow.pack.gating import validate_pack_prereqs

    started: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []

    for i, run in enumerate(runs):
        if not isinstance(run, dict):
            raise ValueError("Each run must be an object")
        target_workflow = run.get("workflow_name")
        if not target_workflow:
            raise ValueError("Each run requires workflow_name")

        initial_message = run.get("initial_message") or run.get("message") or run.get("prompt")
        initial_agent_name_override = run.get("initial_agent") or run.get("initial_agent_name")
        label = run.get("label")

        ok, prereq_error = await validate_pack_prereqs(
            app_id=str(ent_id),
            user_id=str(usr_id),
            workflow_name=str(target_workflow),
            persistence=pm,
        )
        if not ok:
            blocked.append({
                "workflow_name": str(target_workflow),
                "reason": prereq_error or "Prerequisites not met",
            })
            await websocket.send_json({
                "type": "chat.prereq_blocked",
                "data": {
                    "workflow_name": str(target_workflow),
                    "message": prereq_error or "Prerequisites not met",
                    "error_code": "WORKFLOW_PREREQS_NOT_MET",
                },
                "chat_id": chat_id,
                "timestamp": utc_timestamp(),
            })
            continue

        new_chat_id = f"chat_{target_workflow}_{uuid.uuid4().hex[:8]}"
        await pm.create_chat_session(
            chat_id=new_chat_id,
            app_id=str(ent_id),
            workflow_name=str(target_workflow),
            user_id=str(usr_id),
        )

        session_registry.add_workflow(
            ws_id=ws_id,
            chat_id=new_chat_id,
            workflow_name=str(target_workflow),
            app_id=str(ent_id),
            user_id=str(usr_id),
            auto_activate=bool(activate_first and i == 0),
        )

        started.append({
            "chat_id": new_chat_id,
            "workflow_name": str(target_workflow),
            "app_id": str(ent_id),
            "user_id": str(usr_id),
            "label": str(label) if label else None,
        })

        await websocket.send_json({
            "type": "chat.workflow_started",
            "data": {
                "chat_id": new_chat_id,
                "workflow_name": str(target_workflow),
                "app_id": str(ent_id),
                "user_id": str(usr_id),
                "label": str(label) if label else None,
            },
            "timestamp": utc_timestamp(),
        })

        if auto_run:
            transport._background_tasks[new_chat_id] = asyncio.create_task(
                transport._run_workflow_background(
                    chat_id=new_chat_id,
                    workflow_name=str(target_workflow),
                    app_id=str(ent_id),
                    user_id=str(usr_id),
                    ws_id=ws_id,
                    initial_message=str(initial_message) if isinstance(initial_message, str) and initial_message.strip() else None,
                    initial_agent_name_override=str(initial_agent_name_override) if isinstance(initial_agent_name_override, str) and initial_agent_name_override.strip() else None,
                )
            )

    # Summary ack
    await websocket.send_json({
        "type": "chat.workflow_batch_started",
        "data": {
            "count": len(started),
            "workflows": started,
            "blocked": blocked,
        },
        "timestamp": utc_timestamp(),
    })

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
    replay_on_switch = bool(data.get("replay_on_switch", True))

    if not target_chat_id:
        raise ValueError("chat_id required for workflow switch")

    conn = transport._get_conn_meta(chat_id)
    ws_id = conn.get("ws_id")
    if not ws_id:
        # Recover gracefully when metadata was partially initialized.
        ws_id = id(websocket)
        if chat_id not in transport.connections:
            transport.connections[chat_id] = {"websocket": websocket}
        transport.connections[chat_id]["ws_id"] = ws_id
        logger.warning(f"Recovered missing ws_id for chat {chat_id} using websocket identity")

    # Store frontend context in connection metadata
    if frontend_context and isinstance(frontend_context, dict):
        if target_chat_id not in transport.connections:
            transport.connections[target_chat_id] = {}
        transport.connections[target_chat_id]["frontend_context"] = frontend_context
        logger.info(f"Stored frontend context for {target_chat_id}: {list(frontend_context.keys())}")

    # Switch workflow context
    active_context = session_registry.switch_workflow(ws_id, target_chat_id)
    if not active_context:
        # Attempt on-demand session hydration from persistence when registry drifted.
        try:
            pm = transport._get_or_create_persistence_manager()
            coll = await pm._coll()
            doc = await coll.find_one(
                {"_id": target_chat_id},
                {"workflow_name": 1, "app_id": 1, "user_id": 1, "artifact_instance_id": 1},
            )
            if doc and doc.get("workflow_name") and doc.get("app_id") and doc.get("user_id"):
                active_context = session_registry.add_workflow(
                    ws_id=ws_id,
                    chat_id=target_chat_id,
                    workflow_name=str(doc.get("workflow_name")),
                    app_id=str(doc.get("app_id")),
                    user_id=str(doc.get("user_id")),
                    artifact_id=str(doc.get("artifact_instance_id")) if doc.get("artifact_instance_id") else None,
                    auto_activate=True,
                )
        except Exception as hydrate_err:
            logger.warning(f"Failed to hydrate workflow context for {target_chat_id}: {hydrate_err}")

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

    # Replay persisted transcript when switching back into workflow mode so the
    # UI can reliably reconstruct workflow messages after Ask-mode transitions.
    if replay_on_switch:
        try:
            from mozaiksai.core.transport.resume_groupchat import GroupChatResumer

            resumer = GroupChatResumer()

            async def send_event_wrapper(event_dict: Dict[str, Any], _target_chat_id: str | None) -> None:
                if not isinstance(event_dict, dict):
                    return

                kind = event_dict.get("kind")
                if kind == "text":
                    await websocket.send_json({
                        "type": "chat.text",
                        "data": {
                            "index": event_dict.get("index", 0),
                            "content": event_dict.get("content", ""),
                            "role": event_dict.get("role", "user"),
                            "agent": event_dict.get("agent", "user"),
                            "sender": event_dict.get("agent", "user"),
                            "replay": event_dict.get("replay", True),
                            "timestamp": event_dict.get("timestamp"),
                            "metadata": event_dict.get("metadata"),
                            "uiToolEvent": event_dict.get("uiToolEvent"),
                            "ui_tool_completed": event_dict.get("ui_tool_completed"),
                            "ui_tool_status": event_dict.get("ui_tool_status"),
                        },
                        "timestamp": utc_timestamp(),
                    })
                elif kind == "resume_boundary":
                    boundary = {k: v for k, v in event_dict.items() if k != "kind"}
                    await websocket.send_json({
                        "type": "chat.resume_boundary",
                        "data": boundary,
                        "timestamp": utc_timestamp(),
                    })

            await resumer.handle_resume_request(
                chat_id=str(target_chat_id),
                app_id=str(active_context.app_id),
                last_client_index=-1,
                send_event=send_event_wrapper,
            )
        except Exception as replay_err:
            logger.warning(
                "Workflow switch replay failed for chat %s (ws_id=%s): %s",
                target_chat_id,
                ws_id,
                replay_err,
            )

    # UserDriven UX: when switching from Ask -> Workflow, emit pre-run bootstrap
    # if this workflow chat has no transcript yet. We bypass only the persisted
    # "sent" guard here and use per-session dedupe to avoid duplicate emits on
    # repeated toggles within the same websocket session.
    await transport._emit_userdriven_bootstrap_if_needed(
        chat_id=target_chat_id,
        user_id=active_context.user_id,
        workflow_name=active_context.workflow_name,
        app_id=active_context.app_id,
        ignore_sent_guard=True,
        session_dedupe_token=f"{ws_id}:{target_chat_id}",
    )


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

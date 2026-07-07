# ==============================================================================
# FILE: mozaiksai/core/transport/handlers/workflow_handlers.py
# DESCRIPTION: Handlers for workflow start, switch, and batch operations
# ==============================================================================
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket

from mozaiksai.core.transport.session_registry import session_registry

from .base import logger, utc_timestamp

if TYPE_CHECKING:
    from mozaiksai.core.transport.simple_transport import SimpleTransport


def _background_task_failure_callback(
    workflow_name: str,
    chat_id: str,
) -> Callable[[asyncio.Task[Any]], None]:
    def _callback(task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "WORKFLOW_BACKGROUND_TASK_FAILED workflow=%s chat=%s: %s",
                workflow_name,
                chat_id,
                exc,
            )

    return _callback


async def handle_switch_workflow(
    transport: SimpleTransport,
    data: dict[str, Any],
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
        logger.warning("WS_ID_RECOVERED chat=%s using websocket identity", chat_id)

    # Store frontend context in connection metadata
    if frontend_context and isinstance(frontend_context, dict):
        if target_chat_id not in transport.connections:
            transport.connections[target_chat_id] = {}
        transport.connections[target_chat_id]["frontend_context"] = frontend_context
        logger.debug("FRONTEND_CONTEXT_STORED chat=%s keys=%s", target_chat_id, list(frontend_context.keys()))

    # Switch workflow context
    active_context = session_registry.switch_workflow(ws_id, target_chat_id)  # type: ignore[arg-type]
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
                    ws_id=ws_id,  # type: ignore[arg-type]
                    chat_id=target_chat_id,
                    workflow_name=str(doc.get("workflow_name")),
                    app_id=str(doc.get("app_id")),
                    user_id=str(doc.get("user_id")),
                    artifact_id=str(doc.get("artifact_instance_id")) if doc.get("artifact_instance_id") else None,
                    auto_activate=True,
                )
        except Exception as hydrate_err:
            logger.warning("WORKFLOW_HYDRATE_FAILED chat=%s: %s", target_chat_id, hydrate_err)

    if not active_context:
        raise ValueError(f"Workflow {target_chat_id} not found or already completed")

    logger.debug("WORKFLOW_CONTEXT_SWITCHED from=%s to=%s ws_id=%s", chat_id, target_chat_id, ws_id)

    try:
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
    except Exception as send_err:
        logger.debug(
            "WORKFLOW_CONTEXT_SWITCH_ACK_SKIPPED from=%s to=%s ws_id=%s: %s",
            chat_id,
            target_chat_id,
            ws_id,
            send_err,
        )

    # UserDriven auto-start: start the AG2 run immediately so the initial
    # agent can produce the first visible message from its prompt. The
    # orchestration layer supplies a hidden seed when no explicit seed exists.
    try:
        from mozaiksai.core.workflow.workflow_manager import workflow_manager

        target_chat_id_str = str(target_chat_id)
        try:
            workflow_manager.reload_workflow(str(active_context.workflow_name))
        except Exception:
            pass
        cfg = workflow_manager.get_config(str(active_context.workflow_name)) or {}
        workflow_startup_mode = str(cfg.get("workflow_startup_mode", "AgentDriven")).strip().lower()
        existing_task = transport._background_tasks.get(target_chat_id_str)
        if workflow_startup_mode == "userdriven":
            if not (existing_task and not existing_task.done()):
                pm = transport._get_or_create_persistence_manager()
                coll = await pm._coll()
                doc = await coll.find_one(
                    {"_id": target_chat_id_str},
                    {"status": 1},
                )
                status = int(doc.get("status", -1)) if doc else -1
                run_history = await pm.load_run_history(
                    chat_id=target_chat_id_str,
                    app_id=str(active_context.app_id),
                )

                if status == 0 and not run_history:
                    _t = asyncio.create_task(
                        transport._run_workflow_background(
                            chat_id=target_chat_id_str,
                            workflow_name=str(active_context.workflow_name),
                            app_id=str(active_context.app_id),
                            user_id=str(active_context.user_id),
                            ws_id=ws_id,
                            initial_message=None,
                        ),
                        name=f"workflow:{active_context.workflow_name}:{target_chat_id_str}",
                    )
                    _t.add_done_callback(
                        _background_task_failure_callback(
                            str(active_context.workflow_name),
                            target_chat_id_str,
                        )
                    )
                    transport._background_tasks[target_chat_id_str] = _t
    except Exception as native_start_err:
        logger.warning(
            "UserDriven auto-start failed for chat %s (ws_id=%s): %s",
            target_chat_id,
            ws_id,
            native_start_err,
        )

    # Replay persisted AG2 run history when switching back into workflow mode so the
    # UI can reliably reconstruct workflow messages after Ask-mode transitions.
    if replay_on_switch:
        try:
            from mozaiksai.core.transport.run_replay import WorkflowRunReplayer

            replayer = WorkflowRunReplayer()

            async def send_event_wrapper(event_dict: dict[str, Any], _target_chat_id: str | None) -> None:
                if not isinstance(event_dict, dict):
                    return

                kind = event_dict.get("kind")
                if kind == "text":
                    text_payload = {k: v for k, v in event_dict.items() if k != "kind"}
                    text_payload.setdefault("index", 0)
                    text_payload.setdefault("content", "")
                    text_payload.setdefault("role", "user")
                    text_payload.setdefault("agent", event_dict.get("sender", "user"))
                    text_payload.setdefault("sender", text_payload.get("agent", "user"))
                    text_payload.setdefault("replay", True)
                    envelope = {
                        "type": "chat.text",
                        "data": text_payload,
                        "timestamp": utc_timestamp(),
                    }
                    await websocket.send_json(transport._serialize_ag2_events(envelope))
                elif kind == "resume_boundary":
                    boundary = {k: v for k, v in event_dict.items() if k != "kind"}
                    envelope = {
                        "type": "chat.resume_boundary",
                        "data": boundary,
                        "timestamp": utc_timestamp(),
                    }
                    await websocket.send_json(transport._serialize_ag2_events(envelope))
                elif kind == "awaiting_reply":
                    awaiting = {k: v for k, v in event_dict.items() if k != "kind"}
                    envelope = {
                        "type": "chat.awaiting_reply",
                        "data": awaiting,
                        "timestamp": utc_timestamp(),
                    }
                    await websocket.send_json(transport._serialize_ag2_events(envelope))

            await replayer.handle_resume_request(
                chat_id=str(target_chat_id),
                app_id=str(active_context.app_id),
                last_client_index=-1,
                send_event=send_event_wrapper,
                workflow_startup_mode=workflow_startup_mode,
            )
        except Exception as replay_err:
            logger.warning(
                "Workflow switch replay failed for chat %s (ws_id=%s): %s",
                target_chat_id,
                ws_id,
                replay_err,
            )


async def handle_start_workflow(
    transport: SimpleTransport,
    data: dict[str, Any],
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

    from mozaiksai.core.session import TriggerInput, get_session_router

    pm = transport._get_or_create_persistence_manager()
    session_router = get_session_router()
    route_decision = await session_router.route_trigger(
        TriggerInput(
            app_id=str(ent_id),
            user_id=str(usr_id),
            trigger_source="chat",
            workflow_id=str(target_workflow),
        )
    )
    resolved_workflow = route_decision.workflow_id
    if route_decision.rerouted_by_dependency and route_decision.unmet_dependency is not None:
        await websocket.send_json({
            "type": "chat.workflow_rerouted",
            "data": {
                "requested_workflow_name": str(target_workflow),
                "resolved_workflow_name": resolved_workflow,
                "reason": route_decision.unmet_dependency.reason,
                "blocked_workflow_name": route_decision.unmet_dependency.blocked_workflow_id,
            },
            "chat_id": chat_id,
            "timestamp": utc_timestamp(),
        })

    # Create new chat session
    new_chat_id = f"chat_{resolved_workflow}_{uuid.uuid4().hex[:8]}"
    await pm.create_chat_session(
        chat_id=new_chat_id,
        app_id=str(ent_id),
        workflow_name=str(resolved_workflow),
        user_id=str(usr_id),
        extra_fields={
            "trigger_meta": {
                "trigger_source": "chat",
                "requested_workflow_id": str(target_workflow),
                "resolved_workflow_id": str(resolved_workflow),
                "rerouted_by_dependency": bool(route_decision.rerouted_by_dependency),
            }
        },
    )
    await session_router.bind_workflow_session(
        app_id=str(ent_id),
        user_id=str(usr_id),
        workflow_id=str(resolved_workflow),
        chat_id=new_chat_id,
    )

    # Store frontend context
    if frontend_context and isinstance(frontend_context, dict):
        if new_chat_id not in transport.connections:
            transport.connections[new_chat_id] = {}
        transport.connections[new_chat_id]["frontend_context"] = frontend_context
        logger.debug("FRONTEND_CONTEXT_STORED chat=%s keys=%s", new_chat_id, list(frontend_context.keys()))

    # Register in session registry
    session_registry.add_workflow(
        ws_id=ws_id,
        chat_id=new_chat_id,
        workflow_name=resolved_workflow,
        app_id=ent_id,
        user_id=usr_id,
        auto_activate=True
    )

    logger.debug("WORKFLOW_STARTED workflow=%s chat=%s ws_id=%s", resolved_workflow, new_chat_id, ws_id)

    await websocket.send_json({
        "type": "chat.workflow_started",
        "data": {
            "chat_id": new_chat_id,
            "workflow_name": resolved_workflow,
            "requested_workflow_name": str(target_workflow),
            "app_id": ent_id,
            "user_id": usr_id
        },
        "timestamp": utc_timestamp()
    })

    # Auto-run if requested
    if auto_run:
        _wf_task = asyncio.create_task(
            transport._run_workflow_background(
                chat_id=new_chat_id,
                workflow_name=str(resolved_workflow),
                app_id=str(ent_id),
                user_id=str(usr_id),
                ws_id=ws_id,
                initial_message=str(initial_message) if isinstance(initial_message, str) and initial_message.strip() else None,
                initial_agent_name_override=str(initial_agent_name_override) if isinstance(initial_agent_name_override, str) and initial_agent_name_override.strip() else None,
            ),
            name=f"workflow:{resolved_workflow}:{new_chat_id}",
        )
        _wf_task.add_done_callback(
            _background_task_failure_callback(str(resolved_workflow), new_chat_id)
        )
        transport._background_tasks[new_chat_id] = _wf_task


async def handle_start_workflow_batch(
    transport: SimpleTransport,
    data: dict[str, Any],
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
    from mozaiksai.core.session import TriggerInput, get_session_router
    session_router = get_session_router()

    started: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    for i, run in enumerate(runs):
        if not isinstance(run, dict):
            raise ValueError("Each run must be an object")
        target_workflow = run.get("workflow_name")
        if not target_workflow:
            raise ValueError("Each run requires workflow_name")

        initial_message = run.get("initial_message") or run.get("message") or run.get("prompt")
        initial_agent_name_override = run.get("initial_agent") or run.get("initial_agent_name")
        label = run.get("label")

        route_decision = await session_router.route_trigger(
            TriggerInput(
                app_id=str(ent_id),
                user_id=str(usr_id),
                trigger_source="chat",
                workflow_id=str(target_workflow),
            )
        )
        resolved_workflow = route_decision.workflow_id

        if route_decision.rerouted_by_dependency and route_decision.unmet_dependency is not None:
            blocked.append({
                "workflow_name": str(target_workflow),
                "reason": route_decision.unmet_dependency.reason,
                "rerouted_to": resolved_workflow,
            })

        new_chat_id = f"chat_{resolved_workflow}_{uuid.uuid4().hex[:8]}"
        await pm.create_chat_session(
            chat_id=new_chat_id,
            app_id=str(ent_id),
            workflow_name=str(resolved_workflow),
            user_id=str(usr_id),
            extra_fields={
                "trigger_meta": {
                    "trigger_source": "chat",
                    "requested_workflow_id": str(target_workflow),
                    "resolved_workflow_id": str(resolved_workflow),
                    "rerouted_by_dependency": bool(route_decision.rerouted_by_dependency),
                }
            },
        )
        await session_router.bind_workflow_session(
            app_id=str(ent_id),
            user_id=str(usr_id),
            workflow_id=str(resolved_workflow),
            chat_id=new_chat_id,
        )

        session_registry.add_workflow(
            ws_id=ws_id,
            chat_id=new_chat_id,
            workflow_name=str(resolved_workflow),
            app_id=str(ent_id),
            user_id=str(usr_id),
            auto_activate=bool(activate_first and i == 0),
        )

        started.append({
            "chat_id": new_chat_id,
            "workflow_name": str(resolved_workflow),
            "requested_workflow_name": str(target_workflow),
            "app_id": str(ent_id),
            "user_id": str(usr_id),
            "label": str(label) if label else None,
        })

        if route_decision.rerouted_by_dependency and route_decision.unmet_dependency is not None:
            await websocket.send_json({
                "type": "chat.workflow_rerouted",
                "data": {
                    "requested_workflow_name": str(target_workflow),
                    "resolved_workflow_name": str(resolved_workflow),
                    "reason": route_decision.unmet_dependency.reason,
                    "blocked_workflow_name": route_decision.unmet_dependency.blocked_workflow_id,
                },
                "chat_id": chat_id,
                "timestamp": utc_timestamp(),
            })

        await websocket.send_json({
            "type": "chat.workflow_started",
            "data": {
                "chat_id": new_chat_id,
                "workflow_name": str(resolved_workflow),
                "requested_workflow_name": str(target_workflow),
                "app_id": str(ent_id),
                "user_id": str(usr_id),
                "label": str(label) if label else None,
            },
            "timestamp": utc_timestamp(),
        })

        if auto_run:
            _batch_task = asyncio.create_task(
                transport._run_workflow_background(
                    chat_id=new_chat_id,
                    workflow_name=str(resolved_workflow),
                    app_id=str(ent_id),
                    user_id=str(usr_id),
                    ws_id=ws_id,
                    initial_message=str(initial_message) if isinstance(initial_message, str) and initial_message.strip() else None,
                    initial_agent_name_override=str(initial_agent_name_override) if isinstance(initial_agent_name_override, str) and initial_agent_name_override.strip() else None,
                ),
                name=f"workflow:{resolved_workflow}:{new_chat_id}",
            )
            _batch_task.add_done_callback(
                _background_task_failure_callback(str(resolved_workflow), new_chat_id)
            )
            transport._background_tasks[new_chat_id] = _batch_task

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

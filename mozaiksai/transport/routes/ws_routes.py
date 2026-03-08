"""WebSocket endpoint for real-time agent communication."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, UTC

from fastapi import APIRouter, WebSocket

from mozaiksai.runtime.auth import (
    authenticate_websocket_with_path_binding,
    WS_CLOSE_POLICY_VIOLATION,
)
from mozaiksai.runtime.multitenant import build_app_scope_filter
from mozaiksai.runtime.extensions.platform_hooks import get_platform_hooks
from mozaiksai.transport.websocket.registry import session_registry
from logs.logging_config import get_workflow_logger

logger = logging.getLogger(__name__)
wf_logger = get_workflow_logger("ws_routes")

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    workflow_name: str,
    app_id: str,
    chat_id: str,
    user_id: str,
):
    """WebSocket endpoint for real-time agent communication with multi-workflow session support."""
    simple_transport = websocket.app.state.simple_transport
    persistence_manager = websocket.app.state.persistence_manager

    if not simple_transport:
        await websocket.close(code=1000, reason="Transport service not available")
        return

    # Authenticate and validate path bindings
    ws_user = await authenticate_websocket_with_path_binding(
        websocket,
        path_user_id=user_id,
        path_app_id=app_id,
        path_chat_id=chat_id,
    )
    if ws_user is None:
        return  # Connection already closed with 1008

    user_id = ws_user.user_id

    # Ownership check — ensure existing chat belongs to this principal
    try:
        coll = await persistence_manager._coll()
        existing = await coll.find_one(
            {"_id": chat_id, **build_app_scope_filter(app_id)},
            {"_id": 1, "user_id": 1, "workflow_name": 1},
        )
        if existing:
            owner = existing.get("user_id")
            wf = existing.get("workflow_name")
            if not owner or str(owner).strip() != str(user_id).strip():
                await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason="Chat not found")
                return
            if wf and str(wf).strip() != str(workflow_name).strip():
                await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason="Chat not found")
                return
    except Exception as ownership_err:
        wf_logger.debug(f"WS_CHAT_OWNERSHIP_CHECK_SKIPPED: {ownership_err}")

    ws_id = id(websocket)

    # Validate workflow prerequisites (fail-closed)
    try:
        is_valid, error_msg = await get_platform_hooks().call_chat_prereqs(
            app_id=app_id,
            user_id=user_id,
            workflow_name=workflow_name,
            persistence=persistence_manager,
        )
        if not is_valid:
            wf_logger.warning(
                "WS_PREREQS_NOT_MET",
                extra={
                    "workflow_name": workflow_name,
                    "app_id": app_id,
                    "user_id": user_id,
                    "error": error_msg,
                    "chat_id": chat_id,
                },
            )
            try:
                await websocket.accept()
                await websocket.send_json(
                    {
                        "type": "chat.error",
                        "data": {
                            "message": error_msg,
                            "error_code": "WORKFLOW_PREREQS_NOT_MET",
                            "workflow_name": workflow_name,
                            "chat_id": chat_id,
                        },
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )
            except Exception:
                pass
            await websocket.close(code=1008, reason="Prerequisites not met")
            return
    except Exception as dep_err:
        wf_logger.error(f"WS_PREREQ_VALIDATION_FAILED: {dep_err}", exc_info=True)
        try:
            await websocket.accept()
            await websocket.send_json(
                {
                    "type": "chat.error",
                    "data": {
                        "message": "Failed to validate workflow prerequisites. Please try again.",
                        "error_code": "PREREQ_VALIDATION_ERROR",
                        "workflow_name": workflow_name,
                        "chat_id": chat_id,
                    },
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
        except Exception:
            pass
        await websocket.close(code=1011, reason="Prerequisite validation failed")
        return

    wf_logger.info(
        f"New WebSocket connection for workflow '{workflow_name}' "
        f"(incoming chat_id={chat_id}, ws_id={ws_id})"
    )

    # Auto resume vs new session selection
    active_chat_id = chat_id
    try:
        coll = await persistence_manager._coll()
        latest = (
            await coll.find(
                {
                    "workflow_name": workflow_name,
                    "user_id": user_id,
                    **build_app_scope_filter(app_id),
                }
            )
            .sort("created_at", -1)
            .limit(1)
            .to_list(length=1)
        )
        if latest:
            latest_doc = latest[0]
            latest_status = int(latest_doc.get("status", -1))
            latest_id = latest_doc.get("_id")
            if latest_status == 0:
                active_chat_id = latest_id
                wf_logger.info(
                    "WS_AUTO_RESUME",
                    extra={"chat_id": active_chat_id, "incoming_chat_id": chat_id},
                )
            else:
                if not await coll.find_one(
                    {"_id": chat_id, "user_id": user_id, **build_app_scope_filter(app_id)}
                ):
                    await persistence_manager.create_chat_session(
                        chat_id, app_id, workflow_name, user_id
                    )
                    wf_logger.info("WS_NEW_SESSION_CREATED", extra={"chat_id": chat_id})
        else:
            if not await coll.find_one(
                {"_id": chat_id, "user_id": user_id, **build_app_scope_filter(app_id)}
            ):
                await persistence_manager.create_chat_session(
                    chat_id, app_id, workflow_name, user_id
                )
                wf_logger.info("WS_FIRST_SESSION_CREATED", extra={"chat_id": chat_id})
    except Exception as pre_err:
        wf_logger.error(f"WS_SESSION_DETERMINATION_FAILED: {pre_err}")

    # Auto-start AgentDriven workflows once the socket is accepted/registered
    async def _auto_start_if_needed():
        try:
            from mozaiksai.kernel.workflow_manager import workflow_manager

            cfg = workflow_manager.get_config(workflow_name)
            if cfg.get("startup_mode", "AgentDriven") == "AgentDriven":
                local_transport = simple_transport
                if not local_transport:
                    return
                for _ in range(20):
                    conn = local_transport.connections.get(active_chat_id)
                    if conn and conn.websocket is not None:
                        if conn.autostarted:
                            return
                        conn.autostarted = True
                        break
                    await asyncio.sleep(0.1)

                await local_transport.handle_user_input_from_api(
                    chat_id=active_chat_id,
                    user_id=user_id,
                    workflow_name=workflow_name,
                    message=None,
                    app_id=app_id,
                )
        except Exception as e:
            logger.error(
                f"Auto-start failed for {workflow_name}/{active_chat_id}: {e}"
            )

    asyncio.create_task(_auto_start_if_needed())

    # Emit initial chat_meta event
    await _emit_chat_meta(
        persistence_manager=persistence_manager,
        simple_transport=simple_transport,
        active_chat_id=active_chat_id,
        workflow_name=workflow_name,
        app_id=app_id,
        user_id=user_id,
    )

    # Register in session registry
    session_registry.add_workflow(
        ws_id=ws_id,
        chat_id=active_chat_id,
        workflow_name=workflow_name,
        app_id=app_id,
        user_id=user_id,
        auto_activate=True,
    )

    try:
        await simple_transport.handle_websocket(
            websocket=websocket,
            chat_id=active_chat_id,
            user_id=user_id,
            workflow_name=workflow_name,
            app_id=app_id,
            ws_id=ws_id,
        )
    finally:
        session_registry.remove_session(ws_id)
        wf_logger.info(f"Cleaned up session registry for ws_id={ws_id}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _emit_chat_meta(
    *,
    persistence_manager,
    simple_transport,
    active_chat_id: str,
    workflow_name: str,
    app_id: str,
    user_id: str,
) -> None:
    """Send the initial ``chat_meta`` event on the websocket so the frontend
    can align caches and restore artifact UI state immediately."""
    try:
        has_children = False
        try:
            from mozaiksai.kernel.pack.config import workflow_has_journeys

            has_children = workflow_has_journeys(workflow_name)
        except Exception:
            has_children = False

        chat_exists = False
        coll = None
        try:
            coll = await persistence_manager._coll()
            existing_doc = await coll.find_one(
                {"_id": active_chat_id, "user_id": user_id, **build_app_scope_filter(app_id)},
                {"_id": 1},
            )
            chat_exists = existing_doc is not None
        except Exception as ce:
            wf_logger.debug(f"chat existence check failed for {active_chat_id}: {ce}")

        if not chat_exists:
            try:
                await persistence_manager.create_chat_session(
                    active_chat_id, app_id, workflow_name, user_id
                )
                chat_exists = True
                wf_logger.info(
                    "WS_BACKFILL_SESSION_CREATED", extra={"chat_id": active_chat_id}
                )
            except Exception as ce:
                wf_logger.debug(
                    f"Failed to backfill chat session for {active_chat_id}: {ce}"
                )

        try:
            cache_seed = await persistence_manager.get_or_assign_cache_seed(
                active_chat_id, app_id
            )
        except Exception as ce:
            cache_seed = None
            wf_logger.debug(
                f"cache_seed retrieval failed for WS {active_chat_id}: {ce}"
            )

        if simple_transport:
            last_artifact = None
            created_at_iso = None
            doc = None
            try:
                if coll is not None:
                    doc = await coll.find_one(
                        {
                            "_id": active_chat_id,
                            "user_id": user_id,
                            **build_app_scope_filter(app_id),
                        },
                        {
                            "last_artifact": 1,
                            "created_at": 1,
                            "status": 1,
                            "last_sequence": 1,
                        },
                    )
                    if doc:
                        last_artifact = doc.get("last_artifact")
                        ca = doc.get("created_at")
                        if ca:
                            try:
                                created_at_iso = ca.isoformat()
                            except Exception:
                                created_at_iso = str(ca)
            except Exception as la_err:
                wf_logger.debug(
                    f"last_artifact fetch failed for chat_meta {active_chat_id}: {la_err}"
                )

            await simple_transport.send_event_to_ui(
                {
                    "kind": "chat_meta",
                    "chat_id": active_chat_id,
                    "workflow_name": workflow_name,
                    "app_id": app_id,
                    "user_id": user_id,
                    "has_children": has_children,
                    "cache_seed": cache_seed,
                    "chat_exists": chat_exists,
                    "last_artifact": last_artifact,
                    "status": doc.get("status") if doc else None,
                    "last_sequence": doc.get("last_sequence") if doc else None,
                    "created_at": created_at_iso,
                },
                active_chat_id,
            )
            wf_logger.info(
                "CHAT_META_EMITTED",
                extra={
                    "chat_id": active_chat_id,
                    "workflow_name": workflow_name,
                    "app_id": app_id,
                    "cache_seed": cache_seed,
                    "chat_exists": chat_exists,
                    "has_last_artifact": bool(last_artifact),
                    "created_at": created_at_iso,
                },
            )
    except Exception as meta_e:
        wf_logger.debug(
            f"Failed to emit chat_meta for {active_chat_id}: {meta_e}"
        )

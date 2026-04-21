from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, Tuple

from logs.logging_config import get_core_logger

from mozaiksai.core.multitenant import build_app_scope_filter
from mozaiksai.core.session.model import TriggerInput
from mozaiksai.core.session.persistence import SessionStateStore
from mozaiksai.core.session.router import get_session_router
from mozaiksai.core.transport.session_registry import session_registry

logger = get_core_logger("journey_orchestrator")


def _is_completed_status(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value) == 1
    if isinstance(value, str):
        return value.strip().lower() in {"completed", "complete", "success", "succeeded", "ok", "done"}
    return True


class JourneyOrchestrator:
    """Auto-advance orchestrator for global pack journeys."""

    def __init__(self) -> None:
        self._inflight: Dict[str, asyncio.Lock] = {}

    async def handle_run_complete(self, payload: Dict[str, Any]) -> None:
        chat_id = str(payload.get("chat_id") or "").strip()
        if not chat_id:
            return
        if not _is_completed_status(payload.get("status")):
            return

        lock = self._inflight.setdefault(chat_id, asyncio.Lock())
        async with lock:
            try:
                await self._handle_run_complete_inner(payload, chat_id)
            except Exception as exc:  # pragma: no cover
                logger.error("[JOURNEY] handle_run_complete failed: %s", exc, exc_info=True)

    async def _handle_run_complete_inner(self, payload: Dict[str, Any], chat_id: str) -> None:
        workflow_name = str(payload.get("workflow_name") or payload.get("workflow") or "").strip()
        app_id = str(payload.get("app_id") or payload.get("app") or "").strip()
        user_id = str(payload.get("user_id") or payload.get("user") or "").strip()

        # Best-effort: infer missing context via transport metadata.
        conn, transport = await self._get_transport_conn(chat_id)
        if conn:
            workflow_name = workflow_name or str(conn.get("workflow_name") or "").strip()
            app_id = app_id or str(conn.get("app_id") or "").strip()
            user_id = user_id or str(conn.get("user_id") or "").strip()

        if not workflow_name or not app_id or not user_id:
            return
        if not transport or not conn:
            return

        websocket = conn.get("websocket")
        ws_id = conn.get("ws_id")
        if websocket is None or ws_id is None:
            return

        pm = transport._get_or_create_persistence_manager()
        coll = await pm._coll()
        session_router = get_session_router()
        advance = await session_router.advance_journey_after_run_complete(
            app_id=app_id,
            user_id=user_id,
            workflow_id=workflow_name,
            chat_id=chat_id,
        )
        if advance is None or advance.completed:
            return

        if advance.next_transition_id:
            await transport.send_event_to_ui(
                {
                    "type": "chat.transition_requested",
                    "data": {
                        "transition_id": advance.next_transition_id,
                        "from_chat_id": chat_id,
                        "app_id": app_id,
                        "journey_id": advance.journey_instance_id,
                        "journey_key": advance.journey_key,
                        "journey_position": advance.next_group_index,
                    },
                    "timestamp": datetime.now(UTC).isoformat(),
                },
                chat_id,
            )
            return

        if not advance.next_workflows:
            return

        try:
            session_registry.complete_workflow(int(ws_id), chat_id)
        except Exception:
            pass

        spawned: List[Tuple[str, str, bool]] = []  # (workflow_name, chat_id, created_new)
        session_scope_id = SessionStateStore.session_id_for_scope(app_id, user_id)
        next_group_index = int(advance.next_group_index or 0)
        for wf in advance.next_workflows:
            route_decision = await session_router.route_trigger(
                TriggerInput(
                    app_id=app_id,
                    user_id=user_id,
                    trigger_source="run_complete",
                    workflow_id=wf,
                )
            )
            if route_decision.workflow_id != wf:
                reason = (
                    route_decision.unmet_dependency.reason
                    if route_decision.unmet_dependency is not None
                    else f"Journey step '{wf}' cannot start because prerequisites are not met."
                )
                await transport.send_event_to_ui(
                    {
                        "type": "chat.error",
                        "data": {
                            "message": reason,
                            "error_code": "WORKFLOW_PREREQS_NOT_MET",
                            "workflow_name": wf,
                            "chat_id": chat_id,
                        },
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                    chat_id,
                )
                return

            existing_next = await coll.find_one(
                {
                    "session_router_session_id": session_scope_id,
                    "journey_instance_id": advance.journey_instance_id,
                    "journey_position": next_group_index,
                    "workflow_name": wf,
                    **build_app_scope_filter(app_id),
                },
                projection={"_id": 1},
                sort=[("created_at", -1)],
            )
            next_chat_id = (
                str(existing_next.get("_id"))
                if isinstance(existing_next, dict) and existing_next.get("_id")
                else ""
            )
            created_new = False
            if not next_chat_id:
                next_chat_id = str(uuid.uuid4())
                await pm.create_chat_session(
                    chat_id=next_chat_id,
                    app_id=app_id,
                    workflow_name=wf,
                    user_id=user_id,
                    extra_fields={
                        "session_router_session_id": session_scope_id,
                        "journey_instance_id": advance.journey_instance_id,
                        "journey_key": advance.journey_key,
                        "journey_position": next_group_index,
                        "journey_total_steps": int(advance.journey_total_steps),
                    },
                )
                created_new = True
            await session_router.annotate_workflow_chat(
                app_id=app_id,
                user_id=user_id,
                workflow_id=wf,
                chat_id=next_chat_id,
                journey_id=advance.journey_key,
                journey_position=next_group_index,
            )
            spawned.append((wf, next_chat_id, created_new))

            self._ensure_connection_alias(
                transport=transport,
                source_conn=conn,
                target_chat_id=next_chat_id,
                workflow_name=wf,
                app_id=app_id,
                user_id=user_id,
            )
            await self._flush_pre_connection_buffers(transport=transport, chat_id=next_chat_id)

        primary_workflow, primary_chat_id, _ = spawned[-1]
        await session_router.bind_workflow_session(
            app_id=app_id,
            user_id=user_id,
            workflow_id=primary_workflow,
            chat_id=primary_chat_id,
            journey_id=advance.journey_key,
            journey_position=next_group_index,
        )
        session_registry.add_workflow(
            ws_id=int(ws_id),
            chat_id=primary_chat_id,
            workflow_name=primary_workflow,
            app_id=app_id,
            user_id=user_id,
            auto_activate=True,
        )
        for wf, cid, _created in spawned:
            if cid == primary_chat_id:
                continue
            session_registry.add_workflow(
                ws_id=int(ws_id),
                chat_id=cid,
                workflow_name=wf,
                app_id=app_id,
                user_id=user_id,
                auto_activate=False,
            )

        await transport.send_event_to_ui(
            {
                "type": "chat.context_switched",
                "data": {
                    "from_chat_id": chat_id,
                    "to_chat_id": primary_chat_id,
                    "workflow_name": primary_workflow,
                    "app_id": app_id,
                    "journey_id": advance.journey_instance_id,
                    "journey_key": advance.journey_key,
                },
                "timestamp": datetime.now(UTC).isoformat(),
            },
            chat_id,
        )

        for wf, cid, _created in spawned:
            transport._background_tasks[cid] = asyncio.create_task(
                transport._run_workflow_background(
                    chat_id=cid,
                    workflow_name=wf,
                    app_id=app_id,
                    user_id=user_id,
                    ws_id=int(ws_id),
                    initial_message=None,
                    initial_agent_name_override=None,
                )
            )

    async def _get_transport_conn(self, chat_id: str) -> Tuple[Optional[Dict[str, Any]], Any]:
        try:
            from mozaiksai.core.transport.simple_transport import SimpleTransport

            transport = await SimpleTransport.get_instance()
            if transport is None:
                return None, None
            conn = transport.connections.get(chat_id) or {}
            return (conn if isinstance(conn, dict) and conn else None), transport
        except Exception:
            return None, None

    def _ensure_connection_alias(
        self,
        *,
        transport: Any,
        source_conn: Dict[str, Any],
        target_chat_id: str,
        workflow_name: str,
        app_id: str,
        user_id: str,
    ) -> None:
        if not target_chat_id:
            return
        websocket = source_conn.get("websocket")
        ws_id = source_conn.get("ws_id")
        if websocket is None or ws_id is None:
            return

        existing = transport.connections.get(target_chat_id)
        if not isinstance(existing, dict):
            existing = {}

        frontend_context = existing.get("frontend_context") or source_conn.get("frontend_context")
        transport.connections[target_chat_id] = {
            **existing,
            "websocket": websocket,
            "user_id": user_id,
            "workflow_name": workflow_name,
            "app_id": app_id,
            "active": True,
            "ws_id": ws_id,
        }
        if frontend_context and isinstance(frontend_context, dict):
            transport.connections[target_chat_id]["frontend_context"] = frontend_context

    async def _flush_pre_connection_buffers(self, *, transport: Any, chat_id: str) -> None:
        try:
            buffers = getattr(transport, "_pre_connection_buffers", None)
            if not isinstance(buffers, dict):
                return
            buffered = buffers.pop(chat_id, None)
            if not buffered or not isinstance(buffered, list):
                return
            for msg in buffered:
                try:
                    await transport._queue_message_with_backpressure(chat_id, msg)  # noqa: SLF001
                except Exception:
                    continue
            try:
                await transport._flush_message_queue(chat_id)  # noqa: SLF001
            except Exception:
                return
        except Exception:
            return


__all__ = ["JourneyOrchestrator"]

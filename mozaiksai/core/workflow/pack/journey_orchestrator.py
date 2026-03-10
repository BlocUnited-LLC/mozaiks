from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, Tuple

from logs.logging_config import get_core_logger

from mozaiksai.core.data.models import WorkflowStatus
from mozaiksai.core.multitenant import build_app_scope_filter
from mozaiksai.core.transport.session_registry import session_registry
from mozaiksai.core.workflow.pack.config import (
    get_journey,
    infer_auto_journey_for_start,
    load_global_pack_graph,
)
from mozaiksai.core.workflow.pack.gating import validate_pack_prereqs
from mozaiksai.core.workflow.pack.schema import GlobalJourney, GlobalPackGraph, normalize_step_groups

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
        pack = load_global_pack_graph()
        if pack is None:
            return

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
        doc = await coll.find_one(
            {"_id": chat_id, **build_app_scope_filter(app_id)},
            projection={
                "_id": 1,
                "workflow_name": 1,
                "status": 1,
                "journey_id": 1,
                "journey_key": 1,
                "journey_step_index": 1,
            },
        )

        journey_key = str((doc or {}).get("journey_key") or "").strip()
        journey_id = str((doc or {}).get("journey_id") or "").strip()

        journey = get_journey(pack, journey_key) if journey_key else None
        if journey is None and not journey_key:
            journey = self._infer_auto_journey(pack, workflow_name)
            if journey is not None:
                journey_key = journey.id
        if journey is None:
            return

        groups = normalize_step_groups(journey.steps)
        if not groups:
            return

        current_group_index: Optional[int] = None
        for idx, group in enumerate(groups):
            if workflow_name in group:
                current_group_index = idx
                break
        if current_group_index is None:
            return
        if current_group_index >= len(groups) - 1:
            return

        if not journey_id:
            journey_id = str(uuid.uuid4())
            await coll.update_one(
                {"_id": chat_id, **build_app_scope_filter(app_id)},
                {
                    "$set": {
                        "journey_id": journey_id,
                        "journey_key": journey_key or journey.id,
                        "journey_step_index": int(current_group_index),
                        "journey_total_steps": len(groups),
                    }
                },
            )

        # For parallel groups, advance only when all siblings completed.
        current_group = groups[current_group_index]
        for wf in current_group:
            doc_done = await coll.find_one(
                {
                    "journey_id": journey_id,
                    "journey_step_index": int(current_group_index),
                    "workflow_name": wf,
                    "status": int(WorkflowStatus.COMPLETED),
                    **build_app_scope_filter(app_id),
                },
                projection={"_id": 1},
                sort=[("completed_at", -1), ("created_at", -1)],
            )
            if not doc_done:
                return

        next_group_index = current_group_index + 1
        next_group = groups[next_group_index]
        if not next_group:
            return

        try:
            session_registry.complete_workflow(int(ws_id), chat_id)
        except Exception:
            pass

        spawned: List[Tuple[str, str, bool]] = []  # (workflow_name, chat_id, created_new)
        for wf in next_group:
            ok, prereq_error = await validate_pack_prereqs(
                app_id=app_id,
                user_id=user_id,
                workflow_name=wf,
                persistence=pm,
            )
            if not ok:
                await transport.send_event_to_ui(
                    {
                        "type": "chat.error",
                        "data": {
                            "message": prereq_error or "Prerequisites not met",
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
                    "journey_id": journey_id,
                    "journey_step_index": int(next_group_index),
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
                        "journey_id": journey_id,
                        "journey_key": journey_key or journey.id,
                        "journey_step_index": int(next_group_index),
                        "journey_total_steps": len(groups),
                    },
                )
                created_new = True
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
                    "journey_id": journey_id,
                    "journey_key": journey_key,
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

    @staticmethod
    def _infer_auto_journey(pack: GlobalPackGraph, workflow_name: str) -> Optional[GlobalJourney]:
        return infer_auto_journey_for_start(pack, workflow_name)

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


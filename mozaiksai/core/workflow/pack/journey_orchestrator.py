from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from logs.logging_config import get_core_logger
from mozaiksai.core.data.persistence.persistence_manager import SERVER_OWNED_SESSION_FIELDS
from mozaiksai.core.multitenant import build_app_scope_filter
from mozaiksai.core.session.build_binding import RunBuildBinding
from mozaiksai.core.session.model import TriggerInput
from mozaiksai.core.session.persistence import SessionStateStore
from mozaiksai.core.session.router import get_session_router_for_chat
from mozaiksai.core.transport.session_registry import session_registry
from mozaiksai.core.workflow.context.authority import (
    TRANSITION_ROUTER_WRITER,
    build_context_authority_policy,
)
from mozaiksai.core.workflow.pack.config import get_transition, load_global_pack_graph

logger = get_core_logger("journey_orchestrator")


def _spawned_workflow_failure_callback(
    workflow_name: str,
    chat_id: str,
) -> Callable[[asyncio.Task[Any]], None]:
    def _callback(task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "JOURNEY_SPAWNED_WORKFLOW_FAILED workflow=%s chat=%s: %s",
                workflow_name,
                chat_id,
                exc,
            )

    return _callback

def _is_completed_status(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value) == 1
    if isinstance(value, str):
        return value.strip().lower() in {"completed", "complete", "success", "succeeded", "ok", "done"}
    return False


def _is_successful_completion(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("failed") or payload.get("awaiting_user_input") or payload.get("error"):
        return False
    if "run_completed" in payload and not bool(payload.get("run_completed")):
        return False
    return _is_completed_status(payload.get("status"))


def _project_launch_context(chat_doc: Any, workflow_name: str) -> dict[str, Any]:
    """Carry declared launch inputs, not another workflow's execution state."""
    if not isinstance(chat_doc, dict):
        return {}
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    config = workflow_manager.get_config(workflow_name)
    if not config:
        raise ValueError(f"Journey target workflow is not loaded: {workflow_name}")
    definitions = (config.get("context_variables") or {}).get("definitions", {})
    policy = build_context_authority_policy(
        workflow_name=workflow_name, definitions=definitions,
        transition_rules=(config.get("transition_graph") or {}).get("transition_rules", []),
    )
    return {
        key: value for key, value in chat_doc.items()
        if key in definitions and key not in SERVER_OWNED_SESSION_FIELDS
        and (definitions[key].get("source") or {}).get("type") == "state"
        and policy.can_write(key, writer_id=TRANSITION_ROUTER_WRITER)
    }


class JourneyOrchestrator:
    """Auto-advance orchestrator for global pack journeys."""

    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Lock] = {}

    async def handle_run_complete(self, payload: dict[str, Any]) -> None:
        chat_id = str(payload.get("chat_id") or "").strip()
        if not chat_id:
            return
        if not _is_successful_completion(payload):
            return

        lock = self._inflight.setdefault(chat_id, asyncio.Lock())
        async with lock:
            try:
                await self._handle_run_complete_inner(payload, chat_id)
            except Exception as exc:
                logger.error("[JOURNEY] handle_run_complete failed: %s", exc, exc_info=True)
                try:
                    conn, transport = await self._get_transport_conn(chat_id)
                    if conn and transport:
                        await transport.send_event_to_ui(
                            {
                                "schema_version": "mozaiks.ui.event.v1",
                                "type": "chat.error",
                                "data": {
                                    "message": "This workflow finished, but the next journey step could not start. The build is not complete.",
                                    "error_code": "JOURNEY_ADVANCE_FAILED",
                                    "chat_id": chat_id,
                                },
                                "timestamp": datetime.now(UTC).isoformat(),
                            },
                            chat_id,
                        )
                except Exception:
                    logger.exception("[JOURNEY] Could not deliver handoff failure for chat=%s", chat_id)

    async def _handle_run_complete_inner(self, payload: dict[str, Any], chat_id: str) -> None:
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
            logger.warning(
                "[JOURNEY] Run-complete handoff skipped chat=%s missing identity "
                "workflow_name=%r app_id=%r user_id=%r",
                chat_id, workflow_name, app_id, user_id,
            )
            return
        if not transport or not conn:
            logger.warning(
                "[JOURNEY] Run-complete handoff skipped chat=%s missing transport "
                "or connection transport_present=%s connection_present=%s",
                chat_id, transport is not None, conn is not None,
            )
            return

        websocket = conn.get("websocket")
        ws_id = conn.get("ws_id")
        if websocket is None or ws_id is None:
            logger.warning(
                "[JOURNEY] Run-complete handoff skipped chat=%s missing websocket "
                "connection fields websocket_present=%s ws_id_present=%s",
                chat_id, websocket is not None, ws_id is not None,
            )
            return

        pm = transport._get_or_create_persistence_manager()
        coll = await pm._coll()
        source_chat_doc = await coll.find_one(
            {"_id": chat_id, "user_id": user_id, **build_app_scope_filter(app_id)}
        )
        session_router = await get_session_router_for_chat(app_id=app_id, user_id=user_id, chat_id=chat_id)
        raw_binding = source_chat_doc.get("run_build_binding") if source_chat_doc else None
        binding = RunBuildBinding.model_validate(raw_binding) if raw_binding is not None else None
        advance = await session_router.advance_journey_after_run_complete(
            app_id=app_id,
            user_id=user_id,
            workflow_id=workflow_name,
            chat_id=chat_id,
        )
        if advance is None or advance.completed:
            return

        if advance.next_transition_id:
            pack = load_global_pack_graph()
            transition = get_transition(pack, advance.next_transition_id) if pack is not None else None
            if transition is None:
                raise ValueError(f"Journey transition is not declared: {advance.next_transition_id}")
            transition_context = {}
            if transition.transition_type == "chat_session":
                if not transition.route_to:
                    raise ValueError("Chat-session transition has no target workflow")
                transition_context = _project_launch_context(source_chat_doc, transition.route_to)
            await transport.send_event_to_ui(
                {
                    "schema_version": "mozaiks.ui.event.v1",
                    "type": "chat.transition_requested",
                    "data": {
                        "transition_id": advance.next_transition_id,
                        "from_chat_id": chat_id,
                        "app_id": app_id,
                        "journey_id": advance.journey_instance_id,
                        "journey_key": advance.journey_key,
                        "journey_position": advance.next_group_index,
                        "context_variables": transition_context,
                    },
                    "timestamp": datetime.now(UTC).isoformat(),
                },
                chat_id,
            )
            return

        if not advance.next_workflows:
            return

        try:
            session_registry.complete_workflow(int(ws_id), chat_id)  # type: ignore[arg-type]
        except Exception:
            pass

        spawned: list[tuple[str, str, bool]] = []  # (workflow_name, chat_id, created_new)
        session_scope_id = SessionStateStore.session_id_for_scope(app_id, user_id, binding.target_app_id if binding else None)
        next_group_index = int(advance.next_group_index or 0)
        for wf in advance.next_workflows:
            trigger_payload = {
                "source_chat_id": chat_id,
                "source_workflow_id": workflow_name,
                "journey_instance_id": advance.journey_instance_id,
                "journey_key": advance.journey_key,
                "journey_position": next_group_index,
            }
            route_context = {**_project_launch_context(source_chat_doc, wf), **dict(advance.context_seed or {})}
            route_decision = await session_router.route_trigger(
                TriggerInput(
                    app_id=app_id,
                    user_id=user_id,
                    trigger_source="run_complete",
                    workflow_id=wf,
                    journey_id=advance.journey_key,
                    context_variables=route_context,
                    trigger_payload=trigger_payload,
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
                        "schema_version": "mozaiks.ui.event.v1",
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

            from mozaiksai.core.session.launcher import (
                admit_launch_context,
                apply_launch_context_provider,
                create_routed_chat_session,
            )

            merged_context = {**dict(route_decision.context_seed), **route_context}
            provided_context = await apply_launch_context_provider(
                workflow_id=wf,
                requested_workflow_id=route_decision.requested_workflow_id,
                context_variables=merged_context,
                app_id=app_id,
                user_id=user_id,
                journey_id=advance.journey_key,
                trigger_source="run_complete",
                trigger_payload=trigger_payload,
            )
            validated_context = admit_launch_context(wf, provided_context)

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
                extra_fields = {
                    **validated_context,
                    "session_router_session_id": session_scope_id,
                    "journey_instance_id": advance.journey_instance_id,
                    "journey_key": advance.journey_key,
                    "journey_position": next_group_index,
                    "journey_total_steps": int(advance.journey_total_steps),
                }
                await create_routed_chat_session(
                    persistence_manager=pm,
                    chat_id=next_chat_id,
                    app_id=app_id,
                    workflow_id=wf,
                    user_id=user_id,
                    context_variables=extra_fields,
                    trigger_meta={"trigger_source": "run_complete"},
                    source_chat_id=chat_id,
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
                source_chat_id=chat_id,
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
            ws_id=int(ws_id),  # type: ignore[arg-type]
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
                ws_id=int(ws_id),  # type: ignore[arg-type]
                chat_id=cid,
                workflow_name=wf,
                app_id=app_id,
                user_id=user_id,
                auto_activate=False,
            )

        await transport.send_event_to_ui(
            {
                "schema_version": "mozaiks.ui.event.v1",
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
            _spawned_task = asyncio.create_task(
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
            _spawned_task.add_done_callback(_spawned_workflow_failure_callback(wf, cid))
            transport._background_tasks[cid] = _spawned_task

    async def _get_transport_conn(self, chat_id: str) -> tuple[dict[str, Any] | None, Any]:
        try:
            from mozaiksai.core.transport.simple_transport import SimpleTransport

            transport = await SimpleTransport.get_instance()
            if transport is None:
                return None, None
            conn = transport.connections.get(chat_id) or {}
            return (conn if isinstance(conn, dict) and conn else None), transport
        except Exception:
            logger.exception("[JOURNEY] Could not resolve transport connection chat=%s", chat_id)
            return None, None

    def _ensure_connection_alias(
        self,
        *,
        transport: Any,
        source_conn: dict[str, Any],
        source_chat_id: str,
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
            "aliased_from_chat_id": source_chat_id,
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

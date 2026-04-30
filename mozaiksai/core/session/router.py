from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Optional, Set

from logs.logging_config import get_core_logger

from mozaiksai.core.data.models import WorkflowStatus
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.multitenant import build_app_scope_filter
from mozaiksai.core.workflow.pack.config import (
    compute_required_dependencies,
    get_workflow_sequence,
    get_transition,
    get_workflow_entry,
    infer_auto_workflow_sequence_for_start,
    list_workflow_ids,
    load_global_pack_graph,
    normalize_step_groups,
)

from .model import (
    JourneyAdvanceDecision,
    RoutingDecision,
    SessionLifecycle,
    SessionState,
    TransitionResolution,
    TriggerInput,
    UnmetDependency,
)
from .persistence import SessionStateStore
from .trigger_routing import NullTriggerRouteResolver, TriggerRouteResolver

logger = get_core_logger("session_router")


class SessionRouter:
    """Unified session-level router for workflow starts and host-supplied trigger routing."""

    def __init__(
        self,
        *,
        persistence: Optional[AG2PersistenceManager] = None,
        trigger_route_resolver: Optional[TriggerRouteResolver] = None,
        store: Optional[SessionStateStore] = None,
    ) -> None:
        self._persistence = persistence or AG2PersistenceManager()
        self._trigger_route_resolver = trigger_route_resolver or NullTriggerRouteResolver()
        self._store = store or SessionStateStore(self._persistence)

    async def route_trigger(self, trigger: TriggerInput) -> RoutingDecision:
        app_id = str(trigger.app_id or "").strip()
        user_id = str(trigger.user_id or "").strip()
        trigger_source = str(trigger.trigger_source or "").strip().lower() or "chat"
        if not app_id or not user_id:
            raise ValueError("app_id and user_id are required")

        requested_workflow_id = str(trigger.workflow_id or "").strip() or None
        context_seed = {}
        explanation = ""
        is_full_restart = False
        lifecycle_state = SessionLifecycle.ACTIVE

        route_contribution = self._trigger_route_resolver.resolve(trigger)
        if route_contribution is not None:
            context_seed.update(route_contribution.context_seed)
            explanation = route_contribution.explanation
            is_full_restart = bool(route_contribution.is_full_restart)
            requested_workflow_id = requested_workflow_id or route_contribution.workflow_id
            lifecycle_state = route_contribution.lifecycle_state

        if not requested_workflow_id:
            raise ValueError("workflow_id is required unless refinement routing resolves one")

        pack = load_global_pack_graph()
        if pack is not None and requested_workflow_id not in set(list_workflow_ids(pack)):
            raise ValueError(f"Unknown workflow_id '{requested_workflow_id}'")

        resolved_workflow_id = requested_workflow_id
        unmet_dependency: Optional[UnmetDependency] = None
        rerouted_by_dependency = False

        if pack is not None:
            coll = await self._persistence._coll()
            unmet_dependency = await self._find_first_unmet_dependency(
                workflow_id=requested_workflow_id,
                app_id=app_id,
                user_id=user_id,
                coll=coll,
                visited=set(),
            )
            if unmet_dependency is not None:
                resolved_workflow_id = unmet_dependency.workflow_id
                rerouted_by_dependency = True
                if not explanation:
                    explanation = (
                        f"Rerouted to unmet dependency '{unmet_dependency.workflow_id}' "
                        f"before starting '{unmet_dependency.blocked_workflow_id}'."
                    )
                context_seed.setdefault("dependency_redirect", True)
                context_seed.setdefault("requested_workflow_id", requested_workflow_id)
                context_seed.setdefault("rerouted_workflow_id", resolved_workflow_id)
                context_seed.setdefault("dependency_reason", unmet_dependency.reason)
                lifecycle_state = SessionLifecycle.ACTIVE

        decision = RoutingDecision(
            workflow_id=resolved_workflow_id,
            requested_workflow_id=requested_workflow_id,
            context_seed=context_seed,
            explanation=explanation,
            is_full_restart=is_full_restart,
            rerouted_by_dependency=rerouted_by_dependency,
            unmet_dependency=unmet_dependency,
            lifecycle_state=lifecycle_state,
        )
        await self._persist_state(trigger=trigger, decision=decision)
        return decision

    async def resolve_transition(
        self,
        *,
        app_id: str,
        user_id: str,
        transition_id: str,
        option_id: Optional[str] = None,
        context_seed: Optional[dict[str, Any]] = None,
    ) -> TransitionResolution:
        app = str(app_id or "").strip()
        user = str(user_id or "").strip()
        current_transition_id = str(transition_id or "").strip()
        selected_option_id = str(option_id or "").strip()
        resolved_context = dict(context_seed or {})

        if not app or not user:
            raise ValueError("app_id and user_id are required")
        if not current_transition_id:
            raise ValueError("transition_id is required")

        pack = load_global_pack_graph()
        if pack is None:
            raise ValueError("Global pack graph is not available")

        transition = get_transition(pack, current_transition_id)
        if transition is None:
            raise ValueError(f"Unknown transition_id '{current_transition_id}'")

        target, resolved_context = self._resolve_transition_target(
            transition=transition,
            option_id=selected_option_id,
            context_seed=resolved_context,
        )

        route_type = "transition" if get_transition(pack, target) is not None else "workflow"

        if route_type == "transition":
            await self._persist_transition_state(
                app_id=app,
                user_id=user,
                pending_transition_id=target,
            )
            return TransitionResolution(
                resolution_type="transition",
                transition_id=current_transition_id,
                target_id=target,
                route_type=route_type,
                context_seed=resolved_context,
                option_id=selected_option_id or None,
            )

        routing_decision = await self.route_trigger(
            TriggerInput(
                app_id=app,
                user_id=user,
                trigger_source="transition",
                workflow_id=target,
                context_variables=resolved_context,
            )
        )
        return TransitionResolution(
            resolution_type="workflow",
            transition_id=current_transition_id,
            target_id=routing_decision.workflow_id,
            route_type=route_type,
            context_seed=resolved_context,
            option_id=selected_option_id or None,
            routing_decision=routing_decision,
        )

    async def bind_workflow_session(
        self,
        *,
        app_id: str,
        user_id: str,
        workflow_id: str,
        chat_id: str,
        journey_id: Optional[str] = None,
        journey_position: Optional[int] = None,
    ) -> None:
        app = str(app_id or "").strip()
        user = str(user_id or "").strip()
        workflow = str(workflow_id or "").strip()
        chat = str(chat_id or "").strip()

        if not app or not user or not workflow or not chat:
            raise ValueError("app_id, user_id, workflow_id, and chat_id are required")

        state = await self._load_or_create_state(app_id=app, user_id=user)
        self._ensure_journey_state_for_workflow(
            state,
            workflow_id=workflow,
            journey_id=journey_id,
            journey_position=journey_position,
        )
        state.lifecycle_state = SessionLifecycle.ACTIVE
        state.current_workflow_id = workflow
        state.current_chat_id = chat
        state.pending_transition_id = None
        state.updated_at = datetime.now(UTC)
        await self._store.upsert(state)
        await self._persist_chat_journey_metadata(
            app_id=app,
            chat_id=chat,
            state=state,
            workflow_id=workflow,
        )

    async def annotate_workflow_chat(
        self,
        *,
        app_id: str,
        user_id: str,
        workflow_id: str,
        chat_id: str,
        journey_id: Optional[str] = None,
        journey_position: Optional[int] = None,
    ) -> None:
        app = str(app_id or "").strip()
        user = str(user_id or "").strip()
        workflow = str(workflow_id or "").strip()
        chat = str(chat_id or "").strip()
        if not app or not user or not workflow or not chat:
            raise ValueError("app_id, user_id, workflow_id, and chat_id are required")

        state = await self._load_or_create_state(app_id=app, user_id=user)
        self._ensure_journey_state_for_workflow(
            state,
            workflow_id=workflow,
            journey_id=journey_id,
            journey_position=journey_position,
        )
        state.updated_at = datetime.now(UTC)
        await self._store.upsert(state)
        await self._persist_chat_journey_metadata(
            app_id=app,
            chat_id=chat,
            state=state,
            workflow_id=workflow,
        )

    async def advance_journey_after_run_complete(
        self,
        *,
        app_id: str,
        user_id: str,
        workflow_id: str,
        chat_id: str,
    ) -> Optional[JourneyAdvanceDecision]:
        app = str(app_id or "").strip()
        user = str(user_id or "").strip()
        workflow = str(workflow_id or "").strip()
        chat = str(chat_id or "").strip()
        if not app or not user or not workflow or not chat:
            raise ValueError("app_id, user_id, workflow_id, and chat_id are required")

        pack = load_global_pack_graph()
        if pack is None:
            return None

        state = await self._load_or_create_state(app_id=app, user_id=user)
        journey = self._resolve_active_journey_for_workflow(pack, state, workflow)
        if journey is None:
            return None

        groups = normalize_step_groups(journey.steps)
        if not groups:
            return None

        current_group_index = self._resolve_group_index_for_workflow(
            groups=groups,
            workflow_id=workflow,
            preferred_index=state.journey_position,
        )
        if current_group_index is None:
            return None

        if not state.journey_instance_id:
            state.journey_instance_id = str(uuid.uuid4())
        self._apply_journey_metadata_to_state(
            state,
            journey.id,
            journey_position=current_group_index,
        )
        state.current_workflow_id = workflow
        state.current_chat_id = chat
        state.updated_at = datetime.now(UTC)
        await self._store.upsert(state)
        await self._persist_chat_journey_metadata(
            app_id=app,
            chat_id=chat,
            state=state,
            workflow_id=workflow,
        )

        current_group = groups[current_group_index]
        if not await self._is_journey_group_completed(
            app_id=app,
            session_id=state.session_id,
            journey_instance_id=state.journey_instance_id,
            journey_position=current_group_index,
            workflows=current_group,
        ):
            return None

        if current_group_index >= len(groups) - 1:
            state.lifecycle_state = SessionLifecycle.COMPLETED
            state.current_workflow_id = workflow
            state.current_chat_id = chat
            state.pending_transition_id = None
            state.updated_at = datetime.now(UTC)
            await self._store.upsert(state)
            return JourneyAdvanceDecision(
                journey_instance_id=state.journey_instance_id,
                journey_key=journey.id,
                current_group_index=current_group_index,
                journey_total_steps=len(groups),
                completed=True,
            )

        next_group_index = current_group_index + 1
        next_step = journey.steps[next_group_index]
        next_transition_id = str(getattr(next_step, "transition", "") or "").strip() or None
        next_group = list(groups[next_group_index])
        if next_transition_id:
            state.lifecycle_state = SessionLifecycle.AWAITING_TRANSITION
            state.current_workflow_id = None
            state.current_chat_id = None
            state.pending_transition_id = next_transition_id
            self._apply_journey_metadata_to_state(
                state,
                journey.id,
                journey_position=next_group_index,
            )
            state.updated_at = datetime.now(UTC)
            await self._store.upsert(state)
            return JourneyAdvanceDecision(
                journey_instance_id=state.journey_instance_id,
                journey_key=journey.id,
                current_group_index=current_group_index,
                journey_total_steps=len(groups),
                next_group_index=next_group_index,
                next_transition_id=next_transition_id,
                completed=False,
            )

        state.lifecycle_state = SessionLifecycle.ACTIVE
        state.current_workflow_id = None
        state.current_chat_id = None
        state.pending_transition_id = None
        self._apply_journey_metadata_to_state(
            state,
            journey.id,
            journey_position=next_group_index,
        )
        state.updated_at = datetime.now(UTC)
        await self._store.upsert(state)
        return JourneyAdvanceDecision(
            journey_instance_id=state.journey_instance_id,
            journey_key=journey.id,
            current_group_index=current_group_index,
            journey_total_steps=len(groups),
            next_group_index=next_group_index,
            next_workflows=next_group,
            completed=False,
        )

    async def resolve_resume(
        self,
        *,
        app_id: str,
        user_id: str,
        requested_workflow_id: Optional[str] = None,
        requested_chat_id: Optional[str] = None,
    ) -> dict[str, Any]:
        app = str(app_id or "").strip()
        user = str(user_id or "").strip()
        requested_workflow = str(requested_workflow_id or "").strip() or None
        requested_chat = str(requested_chat_id or "").strip() or None
        if not app or not user:
            raise ValueError("app_id and user_id are required")

        state = await self._load_or_create_state(app_id=app, user_id=user)
        coll = await self._persistence._coll()

        requested_doc = await self._find_chat_doc(
            coll=coll,
            app_id=app,
            user_id=user,
            chat_id=requested_chat,
            workflow_id=requested_workflow,
        )
        if requested_doc is not None:
            self._apply_chat_doc_to_state(state, requested_doc, requested_workflow=requested_workflow)
            await self._store.upsert(state)
            return {
                "chat_id": str(requested_doc.get("_id")),
                "workflow_id": str(requested_doc.get("workflow_name") or requested_workflow or ""),
                "found": True,
                "resolved_from": "requested_chat",
                "session_state": self._serialize_state(state),
            }

        state_doc = await self._find_chat_doc(
            coll=coll,
            app_id=app,
            user_id=user,
            chat_id=state.current_chat_id,
            workflow_id=requested_workflow or state.current_workflow_id,
        )
        if state_doc is not None:
            self._apply_chat_doc_to_state(state, state_doc, requested_workflow=requested_workflow)
            await self._store.upsert(state)
            return {
                "chat_id": str(state_doc.get("_id")),
                "workflow_id": str(state_doc.get("workflow_name") or requested_workflow or ""),
                "found": True,
                "resolved_from": "session_state",
                "session_state": self._serialize_state(state),
            }

        in_progress_doc = await self._find_latest_chat_doc(
            coll=coll,
            app_id=app,
            user_id=user,
            workflow_id=requested_workflow or state.current_workflow_id,
            in_progress_only=True,
        )
        if in_progress_doc is None and requested_workflow is not None:
            in_progress_doc = await self._find_latest_chat_doc(
                coll=coll,
                app_id=app,
                user_id=user,
                workflow_id=requested_workflow,
                in_progress_only=True,
            )
        if in_progress_doc is None:
            in_progress_doc = await self._find_latest_chat_doc(
                coll=coll,
                app_id=app,
                user_id=user,
                workflow_id=requested_workflow,
                in_progress_only=False,
            )

        if in_progress_doc is not None:
            self._apply_chat_doc_to_state(state, in_progress_doc, requested_workflow=requested_workflow)
            await self._store.upsert(state)
            return {
                "chat_id": str(in_progress_doc.get("_id")),
                "workflow_id": str(in_progress_doc.get("workflow_name") or requested_workflow or ""),
                "found": True,
                "resolved_from": "latest_chat",
                "session_state": self._serialize_state(state),
            }

        if requested_workflow:
            state.current_workflow_id = requested_workflow
        state.current_chat_id = requested_chat
        state.updated_at = datetime.now(UTC)
        await self._store.upsert(state)
        return {
            "chat_id": requested_chat,
            "workflow_id": requested_workflow or state.current_workflow_id,
            "found": False,
            "resolved_from": "none",
            "session_state": self._serialize_state(state),
        }

    async def get_session_snapshot(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        app = str(app_id or "").strip()
        user = str(user_id or "").strip()
        if not app or not user:
            raise ValueError("app_id and user_id are required")
        state = await self._load_or_create_state(app_id=app, user_id=user)
        return self._serialize_state(state)

    async def mark_awaiting_approval(
        self,
        *,
        app_id: str,
        user_id: str,
        approval_id: str,
        workflow_id: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> dict[str, Any]:
        app = str(app_id or "").strip()
        user = str(user_id or "").strip()
        approval = str(approval_id or "").strip()
        workflow = str(workflow_id or "").strip() or None
        chat = str(chat_id or "").strip() or None
        if not app or not user or not approval:
            raise ValueError("app_id, user_id, and approval_id are required")

        state = await self._load_or_create_state(app_id=app, user_id=user)
        state.lifecycle_state = SessionLifecycle.AWAITING_APPROVAL
        state.pending_approval_id = approval
        if workflow:
            state.current_workflow_id = workflow
        if chat:
            state.current_chat_id = chat
        state.updated_at = datetime.now(UTC)
        await self._store.upsert(state)
        return self._serialize_state(state)

    async def resolve_approval(
        self,
        *,
        app_id: str,
        user_id: str,
        approval_id: str,
        approved: bool = True,
    ) -> dict[str, Any]:
        app = str(app_id or "").strip()
        user = str(user_id or "").strip()
        approval = str(approval_id or "").strip()
        if not app or not user or not approval:
            raise ValueError("app_id, user_id, and approval_id are required")

        state = await self._load_or_create_state(app_id=app, user_id=user)
        pending = str(state.pending_approval_id or "").strip()
        if pending != approval:
            raise ValueError(f"approval_id '{approval}' does not match pending approval")

        state.pending_approval_id = None
        state.lifecycle_state = SessionLifecycle.ACTIVE
        state.last_route_explanation = (
            f"Approval '{approval}' approved."
            if approved
            else f"Approval '{approval}' rejected."
        )
        state.updated_at = datetime.now(UTC)
        await self._store.upsert(state)
        payload = self._serialize_state(state)
        payload["approved"] = bool(approved)
        return payload

    async def _persist_state(self, *, trigger: TriggerInput, decision: RoutingDecision) -> None:
        app_id = str(trigger.app_id)
        user_id = str(trigger.user_id)
        now = datetime.now(UTC)

        state = await self._load_or_create_state(app_id=app_id, user_id=user_id)

        state.lifecycle_state = decision.lifecycle_state
        state.current_workflow_id = decision.workflow_id
        state.last_trigger_source = str(trigger.trigger_source or "chat")
        state.last_requested_workflow_id = decision.requested_workflow_id
        state.last_route_explanation = decision.explanation
        state.pending_transition_id = None
        state.updated_at = now
        await self._store.upsert(state)

    async def _persist_transition_state(
        self,
        *,
        app_id: str,
        user_id: str,
        pending_transition_id: str,
    ) -> None:
        state = await self._load_or_create_state(app_id=app_id, user_id=user_id)
        state.lifecycle_state = SessionLifecycle.AWAITING_TRANSITION
        state.pending_transition_id = str(pending_transition_id)
        state.current_workflow_id = None
        state.current_chat_id = None
        state.updated_at = datetime.now(UTC)
        await self._store.upsert(state)

    async def _load_or_create_state(self, *, app_id: str, user_id: str) -> SessionState:
        now = datetime.now(UTC)
        state = await self._store.load(app_id=app_id, user_id=user_id)
        if state is not None:
            return state
        return SessionState(
            session_id=SessionStateStore.session_id_for_scope(app_id, user_id),
            app_id=app_id,
            user_id=user_id,
            created_at=now,
            updated_at=now,
        )

    def _apply_journey_metadata_to_state(
        self,
        state: SessionState,
        journey_id: str,
        *,
        journey_position: Optional[int] = None,
        reset_instance: bool = False,
    ) -> None:
        pack = load_global_pack_graph()
        journey = get_workflow_sequence(pack, journey_id) if pack is not None else None
        if journey is None:
            state.journey_key = str(journey_id)
            if journey_position is not None:
                state.journey_position = int(journey_position)
            return
        if reset_instance or state.journey_key != journey.id or not state.journey_instance_id:
            state.journey_instance_id = str(uuid.uuid4())
        state.journey_key = journey.id
        groups = normalize_step_groups(journey.steps)
        state.journey_total_steps = len(groups)
        if journey_position is not None:
            state.journey_position = int(journey_position)

    def _ensure_journey_state_for_workflow(
        self,
        state: SessionState,
        *,
        workflow_id: str,
        journey_id: Optional[str],
        journey_position: Optional[int],
    ) -> None:
        pack = load_global_pack_graph()
        if pack is None:
            return

        resolved_journey = None
        explicit_journey_id = str(journey_id or "").strip()
        if explicit_journey_id:
            resolved_journey = get_workflow_sequence(pack, explicit_journey_id)
        elif state.journey_key:
            active_journey = get_workflow_sequence(pack, state.journey_key)
            if active_journey is not None:
                groups = normalize_step_groups(active_journey.steps)
                resolved_index = self._resolve_group_index_for_workflow(
                    groups=groups,
                    workflow_id=workflow_id,
                    preferred_index=journey_position,
                )
                if resolved_index is not None:
                    resolved_journey = active_journey
                    if journey_position is None:
                        journey_position = resolved_index
        if resolved_journey is None:
            resolved_journey = infer_auto_workflow_sequence_for_start(pack, workflow_id)
            if resolved_journey is None:
                return
            if journey_position is None:
                groups = normalize_step_groups(resolved_journey.steps)
                resolved_index = self._resolve_group_index_for_workflow(
                    groups=groups,
                    workflow_id=workflow_id,
                )
                journey_position = resolved_index if resolved_index is not None else 0

        reset_instance = resolved_journey.id != state.journey_key and journey_position == 0
        self._apply_journey_metadata_to_state(
            state,
            resolved_journey.id,
            journey_position=journey_position,
            reset_instance=reset_instance,
        )

    @staticmethod
    def _resolve_group_index_for_workflow(
        *,
        groups: list[list[str]],
        workflow_id: str,
        preferred_index: Optional[int] = None,
    ) -> Optional[int]:
        if preferred_index is not None:
            try:
                index = int(preferred_index)
            except Exception:
                index = None
            else:
                if 0 <= index < len(groups) and workflow_id in groups[index]:
                    return index
        for index, group in enumerate(groups):
            if workflow_id in group:
                return index
        return None

    @staticmethod
    def _resolve_active_journey_for_workflow(pack, state: SessionState, workflow_id: str):
        if state.journey_key:
            active_journey = get_workflow_sequence(pack, state.journey_key)
            if active_journey is not None:
                groups = normalize_step_groups(active_journey.steps)
                if any(workflow_id in group for group in groups):
                    return active_journey
        return infer_auto_workflow_sequence_for_start(pack, workflow_id)

    async def _persist_chat_journey_metadata(
        self,
        *,
        app_id: str,
        chat_id: str,
        state: SessionState,
        workflow_id: str,
    ) -> None:
        coll = await self._persistence._coll()
        update = {
            "session_router_session_id": state.session_id,
            "journey_instance_id": state.journey_instance_id,
            "journey_key": state.journey_key,
            "journey_position": int(state.journey_position),
            "journey_total_steps": int(state.journey_total_steps),
            "workflow_name": workflow_id,
        }
        await coll.update_one(
            {"_id": chat_id, **build_app_scope_filter(app_id)},
            {"$set": update},
        )

    @staticmethod
    def _serialize_state(state: SessionState) -> dict[str, Any]:
        return {
            "session_id": state.session_id,
            "app_id": state.app_id,
            "user_id": state.user_id,
            "lifecycle_state": state.lifecycle_state.value,
            "current_workflow_id": state.current_workflow_id,
            "current_chat_id": state.current_chat_id,
            "journey_instance_id": state.journey_instance_id,
            "journey_key": state.journey_key,
            "journey_position": int(state.journey_position),
            "journey_total_steps": int(state.journey_total_steps),
            "pending_transition_id": state.pending_transition_id,
            "pending_approval_id": state.pending_approval_id,
            "last_trigger_source": state.last_trigger_source,
            "last_requested_workflow_id": state.last_requested_workflow_id,
            "last_route_explanation": state.last_route_explanation,
            "created_at": state.created_at.isoformat(),
            "updated_at": state.updated_at.isoformat(),
        }

    async def _find_chat_doc(
        self,
        *,
        coll,
        app_id: str,
        user_id: str,
        chat_id: Optional[str],
        workflow_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        target_chat_id = str(chat_id or "").strip()
        if not target_chat_id:
            return None
        query = {
            "_id": target_chat_id,
            "user_id": str(user_id),
            **build_app_scope_filter(app_id),
        }
        if workflow_id:
            query["workflow_name"] = str(workflow_id)
        doc = await coll.find_one(query)
        return dict(doc) if isinstance(doc, dict) else None

    async def _find_latest_chat_doc(
        self,
        *,
        coll,
        app_id: str,
        user_id: str,
        workflow_id: Optional[str] = None,
        in_progress_only: bool,
    ) -> Optional[dict[str, Any]]:
        query = {
            "user_id": str(user_id),
            **build_app_scope_filter(app_id),
        }
        if workflow_id:
            query["workflow_name"] = str(workflow_id)
        if in_progress_only:
            query["status"] = int(WorkflowStatus.IN_PROGRESS)
        doc = await coll.find_one(
            query,
            sort=[("last_updated_at", -1), ("created_at", -1)],
        )
        return dict(doc) if isinstance(doc, dict) else None

    def _apply_chat_doc_to_state(
        self,
        state: SessionState,
        chat_doc: dict[str, Any],
        *,
        requested_workflow: Optional[str] = None,
    ) -> None:
        workflow_id = str(chat_doc.get("workflow_name") or requested_workflow or "").strip() or None
        if workflow_id:
            state.current_workflow_id = workflow_id
        state.current_chat_id = str(chat_doc.get("_id") or state.current_chat_id or "").strip() or None

        journey_key = str(chat_doc.get("journey_key") or "").strip()
        if journey_key:
            state.journey_key = journey_key
        journey_instance_id = str(chat_doc.get("journey_instance_id") or "").strip()
        if journey_instance_id:
            state.journey_instance_id = journey_instance_id
        if chat_doc.get("journey_position") is not None:
            try:
                state.journey_position = int(chat_doc.get("journey_position"))
            except Exception:
                pass
        if chat_doc.get("journey_total_steps") is not None:
            try:
                state.journey_total_steps = int(chat_doc.get("journey_total_steps"))
            except Exception:
                pass

        if state.pending_approval_id:
            state.lifecycle_state = SessionLifecycle.AWAITING_APPROVAL
        elif state.pending_transition_id:
            state.lifecycle_state = SessionLifecycle.AWAITING_TRANSITION
        elif self._is_doc_in_progress(chat_doc):
            state.lifecycle_state = SessionLifecycle.ACTIVE
        else:
            state.lifecycle_state = SessionLifecycle.COMPLETED
        state.updated_at = datetime.now(UTC)

    @staticmethod
    def _is_doc_in_progress(chat_doc: dict[str, Any]) -> bool:
        status = chat_doc.get("status")
        if isinstance(status, int):
            return status == int(WorkflowStatus.IN_PROGRESS)
        if isinstance(status, str):
            normalized = status.strip().lower()
            return normalized in {"0", "in_progress", "active"}
        return False

    async def _is_journey_group_completed(
        self,
        *,
        app_id: str,
        session_id: str,
        journey_instance_id: str,
        journey_position: int,
        workflows: list[str],
    ) -> bool:
        coll = await self._persistence._coll()
        for workflow in workflows:
            doc = await coll.find_one(
                {
                    "session_router_session_id": session_id,
                    "journey_instance_id": journey_instance_id,
                    "journey_position": int(journey_position),
                    "workflow_name": str(workflow),
                    "status": int(WorkflowStatus.COMPLETED),
                    **build_app_scope_filter(app_id),
                },
                projection={"_id": 1},
                sort=[("completed_at", -1), ("created_at", -1)],
            )
            if not doc:
                return False
        return True

    def _resolve_transition_target(
        self,
        *,
        transition,
        option_id: str,
        context_seed: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        selected_option_id = str(option_id or "").strip()
        if selected_option_id:
            return self._resolve_selected_option(
                transition=transition,
                option_id=selected_option_id,
                context_seed=context_seed,
            )

        transition_type = str(getattr(transition, "transition_type", "") or "").strip()
        if transition_type in {"silent", "progress_view", "prerequisite_redirect"}:
            target = str(getattr(transition, "route_to", "") or "").strip()
            if not target:
                raise ValueError(f"Transition '{transition.id}' has no route_to")
            return target, dict(context_seed)

        if transition_type == "condition":
            return self._resolve_condition_transition(transition=transition, context_seed=context_seed)

        raise ValueError(f"option_id is required for transition '{transition.id}'")

    @staticmethod
    def _resolve_selected_option(
        *,
        transition,
        option_id: str,
        context_seed: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        selected = str(option_id or "").strip()
        for option in getattr(transition, "options", []) or []:
            if str(getattr(option, "id", "") or "").strip() != selected:
                continue
            resolved_context = dict(context_seed)
            option_context = getattr(option, "context_variables", None)
            if isinstance(option_context, dict):
                resolved_context.update(option_context)
            target = str(getattr(option, "route_to", "") or "").strip()
            if not target:
                raise ValueError(
                    f"option_id '{selected}' for transition '{transition.id}' has no route_to"
                )
            return target, resolved_context

        if selected == "confirm":
            target = str(getattr(transition, "confirm_route", "") or "").strip()
            if target:
                return target, dict(context_seed)
        if selected == "cancel":
            target = str(getattr(transition, "cancel_route", "") or "").strip()
            if target:
                return target, dict(context_seed)

        raise ValueError(
            f"option_id '{selected}' is not valid for transition '{transition.id}'"
        )

    @staticmethod
    def _resolve_condition_transition(transition, context_seed: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        context_key = str(getattr(transition, "context_key", "") or "").strip()
        if not context_key:
            raise ValueError(f"Condition transition '{transition.id}' is missing context_key")

        context_value = context_seed.get(context_key)
        for route in getattr(transition, "routes", []) or []:
            if getattr(route, "match", None) != context_value:
                continue
            target = str(getattr(route, "route_to", "") or "").strip()
            if not target:
                continue
            return target, dict(context_seed)

        default_route = str(getattr(transition, "default_route", "") or "").strip()
        if default_route:
            return default_route, dict(context_seed)

        raise ValueError(
            f"Condition transition '{transition.id}' had no matching route for context '{context_key}'"
        )

    async def _find_first_unmet_dependency(
        self,
        *,
        workflow_id: str,
        app_id: str,
        user_id: str,
        coll,
        visited: Set[str],
    ) -> Optional[UnmetDependency]:
        wf = str(workflow_id or "").strip()
        if not wf or wf in visited:
            return None
        visited.add(wf)

        pack = load_global_pack_graph()
        if pack is None or get_workflow_entry(pack, wf) is None:
            return None

        required_dependencies = compute_required_dependencies(pack, wf)
        for dependency in required_dependencies:
            parent = str(dependency.get("from") or "").strip()
            if not parent:
                continue
            scope = str(dependency.get("scope") or "app").strip().lower() or "app"
            reason = str(dependency.get("reason") or "").strip() or (
                f"{wf} requires {parent} to be completed first."
            )

            nested_unmet = await self._find_first_unmet_dependency(
                workflow_id=parent,
                app_id=app_id,
                user_id=user_id,
                coll=coll,
                visited=visited,
            )
            if nested_unmet is not None:
                return nested_unmet

            if not await self._is_workflow_completed(
                coll=coll,
                app_id=app_id,
                user_id=user_id,
                workflow_id=parent,
                scope=scope,
            ):
                return UnmetDependency(
                    workflow_id=parent,
                    blocked_workflow_id=wf,
                    reason=reason,
                    scope=scope,
                )
        return None

    async def _is_workflow_completed(
        self,
        *,
        coll,
        app_id: str,
        user_id: str,
        workflow_id: str,
        scope: str,
    ) -> bool:
        query = {
            "workflow_name": str(workflow_id),
            "status": int(WorkflowStatus.COMPLETED),
            **build_app_scope_filter(app_id),
        }
        if scope == "user":
            query["user_id"] = str(user_id)
        doc = await coll.find_one(
            query,
            projection={"_id": 1},
            sort=[("completed_at", -1), ("created_at", -1)],
        )
        return bool(doc)


_router: Optional[SessionRouter] = None
_router_trigger_route_resolver: Optional[TriggerRouteResolver] = None


def configure_session_router(
    *,
    trigger_route_resolver: Optional[TriggerRouteResolver] = None,
) -> SessionRouter:
    global _router, _router_trigger_route_resolver
    _router_trigger_route_resolver = trigger_route_resolver
    _router = SessionRouter(trigger_route_resolver=_router_trigger_route_resolver)
    return _router


def get_session_router() -> SessionRouter:
    global _router
    if _router is None:
        _router = SessionRouter(trigger_route_resolver=_router_trigger_route_resolver)
    return _router

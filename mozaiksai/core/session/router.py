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
    PendingHarnessDecision,
    RevisionEntry,
    RoutingDecision,
    SequenceStatus,
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
        explicit_journey_id = str(trigger.journey_id or "").strip() or None
        if not app_id or not user_id:
            raise ValueError("app_id and user_id are required")

        requested_workflow_id = str(trigger.workflow_id or "").strip() or None
        context_seed = {}
        explanation = ""
        is_full_restart = False
        lifecycle_state = SessionLifecycle.ACTIVE

        route_contribution = await self._trigger_route_resolver.resolve(trigger)
        if route_contribution is not None:
            context_seed.update(route_contribution.context_seed)
            explanation = route_contribution.explanation
            is_full_restart = bool(route_contribution.is_full_restart)
            requested_workflow_id = requested_workflow_id or route_contribution.workflow_id
            explicit_journey_id = explicit_journey_id or route_contribution.journey_id
            lifecycle_state = route_contribution.lifecycle_state

        if not requested_workflow_id:
            raise ValueError("workflow_id is required unless refinement routing resolves one")

        pack = load_global_pack_graph()
        if pack is not None and requested_workflow_id not in set(list_workflow_ids(pack)):
            raise ValueError(f"Unknown workflow_id '{requested_workflow_id}'")
        if pack is not None and explicit_journey_id and get_workflow_sequence(pack, explicit_journey_id) is None:
            raise ValueError(f"Unknown journey_id '{explicit_journey_id}'")

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
                if explicit_journey_id and not self._journey_contains_workflow(
                    pack,
                    journey_id=explicit_journey_id,
                    workflow_id=resolved_workflow_id,
                ):
                    explicit_journey_id = None
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
            journey_id=explicit_journey_id,
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
        journey_id: Optional[str] = None,
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

        target, resolved_context, resolved_journey_id = self._resolve_transition_target(
            transition=transition,
            option_id=selected_option_id,
            journey_id=journey_id,
            context_seed=resolved_context,
        )

        route_type = "transition" if get_transition(pack, target) is not None else "workflow"

        if route_type == "transition":
            await self._persist_transition_state(
                app_id=app,
                user_id=user,
                pending_transition_id=target,
                journey_id=resolved_journey_id,
            )
            return TransitionResolution(
                resolution_type="transition",
                transition_id=current_transition_id,
                target_id=target,
                route_type=route_type,
                journey_id=resolved_journey_id,
                context_seed=resolved_context,
                option_id=selected_option_id or None,
            )

        routing_decision = await self.route_trigger(
            TriggerInput(
                app_id=app,
                user_id=user,
                trigger_source="transition",
                workflow_id=target,
                journey_id=resolved_journey_id,
                context_variables=resolved_context,
            )
        )
        return TransitionResolution(
            resolution_type="workflow",
            transition_id=current_transition_id,
            target_id=routing_decision.workflow_id,
            route_type=route_type,
            journey_id=routing_decision.journey_id,
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
        if state.active_revision_id:
            state.sequence_status = SequenceStatus.REVISING
        elif state.sequence_status == SequenceStatus.STALE:
            state.sequence_status = SequenceStatus.IN_PROGRESS
        state.current_workflow_id = workflow
        state.current_chat_id = chat
        state.pending_harness_decision = None
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

        # Sync artifact_version_refs from ArtifactStore so that
        # ArtifactInvalidationService has accurate version IDs for future
        # refinement requests — even after a normal (non-revision) workflow run.
        try:
            from mozaiksai.core.artifacts.store import get_artifact_store
            current_refs = await get_artifact_store().get_current_artifact_version_refs(app_id=app)
            if current_refs:
                state.artifact_version_refs.update(current_refs)
        except Exception as exc:
            logger.debug("[JOURNEY] artifact_version_refs sync skipped: %s", exc)

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
            now = datetime.now(UTC)
            state.lifecycle_state = SessionLifecycle.COMPLETED
            state.sequence_status = SequenceStatus.COMPLETED
            state.sequence_completed_at = now
            self._complete_active_revision(state)
            state.current_workflow_id = workflow
            state.current_chat_id = chat
            state.pending_transition_id = None
            state.updated_at = now
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
        next_sequence_status = (
            SequenceStatus.REVISING if state.active_revision_id else SequenceStatus.IN_PROGRESS
        )
        if next_transition_id:
            state.lifecycle_state = SessionLifecycle.AWAITING_TRANSITION
            state.sequence_status = next_sequence_status
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
        state.sequence_status = next_sequence_status
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

    async def mark_pending_harness_decision(
        self,
        *,
        app_id: str,
        user_id: str,
        pending_decision: PendingHarnessDecision,
        workflow_id: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> dict[str, Any]:
        app = str(app_id or "").strip()
        user = str(user_id or "").strip()
        workflow = str(workflow_id or "").strip() or None
        chat = str(chat_id or "").strip() or None
        if not app or not user:
            raise ValueError("app_id and user_id are required")
        decision_id = str(pending_decision.decision_id or "").strip()
        if not decision_id:
            raise ValueError("pending_decision.decision_id is required")

        state = await self._load_or_create_state(app_id=app, user_id=user)
        state.lifecycle_state = SessionLifecycle.AWAITING_DECISION
        state.pending_harness_decision = pending_decision
        if workflow:
            state.current_workflow_id = workflow
        if chat:
            state.current_chat_id = chat
        state.updated_at = datetime.now(UTC)
        await self._store.upsert(state)
        return self._serialize_state(state)

    async def resolve_pending_harness_decision(
        self,
        *,
        app_id: str,
        user_id: str,
        decision_id: str,
        action_id: Optional[str] = None,
        accepted: bool = True,
    ) -> dict[str, Any]:
        app = str(app_id or "").strip()
        user = str(user_id or "").strip()
        decision = str(decision_id or "").strip()
        resolved_action_id = str(action_id or "").strip() or None
        if not app or not user or not decision:
            raise ValueError("app_id, user_id, and decision_id are required")

        state = await self._load_or_create_state(app_id=app, user_id=user)
        pending = state.pending_harness_decision
        if pending is None:
            raise ValueError("No pending harness decision is recorded for this session")
        if str(pending.decision_id or "").strip() != decision:
            raise ValueError(f"decision_id '{decision}' does not match pending harness decision")

        state.pending_harness_decision = None
        state.lifecycle_state = SessionLifecycle.ACTIVE
        state.last_route_explanation = (
            f"Pending decision '{decision}' resolved with action '{resolved_action_id or 'none'}'."
            if accepted
            else f"Pending decision '{decision}' dismissed."
        )
        state.updated_at = datetime.now(UTC)
        await self._store.upsert(state)
        payload = self._serialize_state(state)
        payload["accepted"] = bool(accepted)
        payload["action_id"] = resolved_action_id
        return payload

    async def persist_revision_intent(
        self,
        *,
        trigger: TriggerInput,
        decision: RoutingDecision,
        pending_harness_decision: Optional[PendingHarnessDecision] = None,
    ) -> dict[str, Any]:
        """Persist revision lineage for a harness decision before launch."""

        app_id = str(trigger.app_id or "").strip()
        user_id = str(trigger.user_id or "").strip()
        if not app_id or not user_id:
            raise ValueError("app_id and user_id are required")

        now = datetime.now(UTC)
        state = await self._load_or_create_state(app_id=app_id, user_id=user_id)
        prior_sequence_status = state.sequence_status
        prior_artifact_refs = dict(state.artifact_version_refs)
        self._apply_revision_state(
            state=state,
            trigger=trigger,
            decision=decision,
            prior_sequence_status=prior_sequence_status,
            prior_artifact_refs=prior_artifact_refs,
            now=now,
        )

        if not state.current_workflow_id:
            state.current_workflow_id = decision.workflow_id
        state.lifecycle_state = (
            SessionLifecycle.AWAITING_DECISION
            if pending_harness_decision is not None
            else decision.lifecycle_state
        )
        state.pending_harness_decision = pending_harness_decision
        state.pending_transition_id = None
        state.last_trigger_source = str(trigger.trigger_source or "chat")
        state.last_requested_workflow_id = decision.requested_workflow_id
        state.last_route_explanation = decision.explanation
        state.updated_at = now
        await self._store.upsert(state)
        return self._serialize_state(state)

    async def _persist_state(self, *, trigger: TriggerInput, decision: RoutingDecision) -> None:
        app_id = str(trigger.app_id)
        user_id = str(trigger.user_id)
        now = datetime.now(UTC)

        state = await self._load_or_create_state(app_id=app_id, user_id=user_id)
        prior_sequence_status = state.sequence_status
        prior_artifact_refs = dict(state.artifact_version_refs)
        self._ensure_journey_state_for_workflow(
            state,
            workflow_id=decision.workflow_id,
            journey_id=decision.journey_id,
            journey_position=None,
        )
        self._apply_revision_state(
            state=state,
            trigger=trigger,
            decision=decision,
            prior_sequence_status=prior_sequence_status,
            prior_artifact_refs=prior_artifact_refs,
            now=now,
        )

        state.lifecycle_state = decision.lifecycle_state
        state.current_workflow_id = decision.workflow_id
        state.pending_harness_decision = None
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
        journey_id: Optional[str] = None,
    ) -> None:
        state = await self._load_or_create_state(app_id=app_id, user_id=user_id)
        self._ensure_journey_state_for_transition(
            state,
            transition_id=str(pending_transition_id),
            journey_id=journey_id,
        )
        state.lifecycle_state = SessionLifecycle.AWAITING_TRANSITION
        state.pending_transition_id = str(pending_transition_id)
        state.pending_harness_decision = None
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
            if resolved_journey is None:
                raise ValueError(f"Unknown journey_id '{explicit_journey_id}'")
            groups = normalize_step_groups(resolved_journey.steps)
            resolved_index = self._resolve_group_index_for_workflow(
                groups=groups,
                workflow_id=workflow_id,
                preferred_index=journey_position,
            )
            if resolved_index is None:
                raise ValueError(
                    f"Workflow '{workflow_id}' is not part of journey '{explicit_journey_id}'"
                )
            if journey_position is None:
                journey_position = resolved_index
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

    def _ensure_journey_state_for_transition(
        self,
        state: SessionState,
        *,
        transition_id: str,
        journey_id: Optional[str],
    ) -> None:
        pack = load_global_pack_graph()
        if pack is None:
            return

        resolved_journey = None
        explicit_journey_id = str(journey_id or "").strip()
        if explicit_journey_id:
            resolved_journey = get_workflow_sequence(pack, explicit_journey_id)
            if resolved_journey is None:
                raise ValueError(f"Unknown journey_id '{explicit_journey_id}'")
        elif state.journey_key:
            resolved_journey = get_workflow_sequence(pack, state.journey_key)

        if resolved_journey is None:
            return

        resolved_index = self._resolve_step_index_for_transition(
            steps=resolved_journey.steps,
            transition_id=transition_id,
        )
        if resolved_index is None:
            if explicit_journey_id:
                raise ValueError(
                    f"Transition '{transition_id}' is not part of journey '{explicit_journey_id}'"
                )
            return

        reset_instance = resolved_journey.id != state.journey_key and resolved_index == 0
        self._apply_journey_metadata_to_state(
            state,
            resolved_journey.id,
            journey_position=resolved_index,
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
    def _journey_contains_workflow(pack: Any, *, journey_id: str, workflow_id: str) -> bool:
        journey = get_workflow_sequence(pack, journey_id)
        if journey is None:
            return False
        for group in normalize_step_groups(journey.steps):
            if workflow_id in group:
                return True
        return False

    @staticmethod
    def _resolve_step_index_for_transition(
        *,
        steps: list[Any],
        transition_id: str,
        preferred_index: Optional[int] = None,
    ) -> Optional[int]:
        if preferred_index is not None:
            try:
                index = int(preferred_index)
            except Exception:
                index = None
            else:
                if 0 <= index < len(steps):
                    step_transition = str(getattr(steps[index], "transition", "") or "").strip()
                    if step_transition == transition_id:
                        return index
        for index, step in enumerate(steps):
            step_transition = str(getattr(step, "transition", "") or "").strip()
            if step_transition == transition_id:
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
            "sequence_status": state.sequence_status.value,
            "sequence_completed_at": (
                state.sequence_completed_at.isoformat()
                if state.sequence_completed_at is not None
                else None
            ),
            "active_revision_id": state.active_revision_id,
            "active_change_request_id": state.active_change_request_id,
            "current_revision_scope": state.current_revision_scope,
            "revision_origin_workflow": state.revision_origin_workflow,
            "restart_from_workflow": state.restart_from_workflow,
            "lifecycle_state": state.lifecycle_state.value,
            "current_workflow_id": state.current_workflow_id,
            "current_chat_id": state.current_chat_id,
            "journey_instance_id": state.journey_instance_id,
            "journey_key": state.journey_key,
            "journey_position": int(state.journey_position),
            "journey_total_steps": int(state.journey_total_steps),
            "pending_transition_id": state.pending_transition_id,
            "pending_harness_decision": (
                {
                    "decision_id": state.pending_harness_decision.decision_id,
                    "decision_type": state.pending_harness_decision.decision_type,
                    "message": state.pending_harness_decision.message,
                    "rationale": state.pending_harness_decision.rationale,
                    "confidence": float(state.pending_harness_decision.confidence),
                    "recommended_workflow_id": state.pending_harness_decision.recommended_workflow_id,
                    "selected_paths": list(state.pending_harness_decision.selected_paths),
                    "clarification_question": state.pending_harness_decision.clarification_question,
                    "change_request_id": state.pending_harness_decision.change_request_id,
                    "revision_id": state.pending_harness_decision.revision_id,
                    "requires_confirmation": bool(state.pending_harness_decision.requires_confirmation),
                    "trigger_source": state.pending_harness_decision.trigger_source,
                    "requested_workflow_id": state.pending_harness_decision.requested_workflow_id,
                    "journey_id": state.pending_harness_decision.journey_id,
                    "context_variables": dict(state.pending_harness_decision.context_variables),
                    "trigger_payload": dict(state.pending_harness_decision.trigger_payload),
                    "actions": [
                        {
                            "action_id": action.action_id,
                            "label": action.label,
                            "action_type": action.action_type,
                            "workflow_id": action.workflow_id,
                            "metadata": dict(action.metadata),
                        }
                        for action in state.pending_harness_decision.actions
                    ],
                    "metadata": dict(state.pending_harness_decision.metadata),
                    "created_at": state.pending_harness_decision.created_at.isoformat(),
                }
                if state.pending_harness_decision is not None
                else None
            ),
            "last_trigger_source": state.last_trigger_source,
            "last_requested_workflow_id": state.last_requested_workflow_id,
            "last_route_explanation": state.last_route_explanation,
            "artifact_version_refs": dict(state.artifact_version_refs),
            "stale_layers": dict(state.stale_layers),
            "revision_history": [
                {
                    "revision_id": entry.revision_id,
                    "change_request_id": entry.change_request_id,
                    "scope": entry.scope,
                    "origin_workflow": entry.origin_workflow,
                    "target_workflow": entry.target_workflow,
                    "from_version_refs": dict(entry.from_version_refs),
                    "to_version_refs": dict(entry.to_version_refs),
                    "timestamp": entry.timestamp.isoformat(),
                }
                for entry in state.revision_history
            ],
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

        if state.pending_harness_decision is not None:
            state.lifecycle_state = SessionLifecycle.AWAITING_DECISION
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
        journey_id: Optional[str],
        context_seed: dict[str, Any],
    ) -> tuple[str, dict[str, Any], Optional[str]]:
        selected_option_id = str(option_id or "").strip()
        if selected_option_id:
            return self._resolve_selected_option(
                transition=transition,
                option_id=selected_option_id,
                journey_id=journey_id,
                context_seed=context_seed,
            )

        transition_type = str(getattr(transition, "transition_type", "") or "").strip()
        if transition_type in {"silent", "progress_view", "prerequisite_redirect", "chat_session"}:
            target = str(getattr(transition, "route_to", "") or "").strip()
            if not target:
                raise ValueError(f"Transition '{transition.id}' has no route_to")
            transition_journey_id = str(getattr(transition, "sequence", "") or "").strip() or None
            return target, dict(context_seed), transition_journey_id or (str(journey_id or "").strip() or None)

        if transition_type == "condition":
            return self._resolve_condition_transition(
                transition=transition,
                journey_id=journey_id,
                context_seed=context_seed,
            )

        raise ValueError(f"option_id is required for transition '{transition.id}'")

    @staticmethod
    def _resolve_selected_option(
        *,
        transition,
        option_id: str,
        journey_id: Optional[str],
        context_seed: dict[str, Any],
    ) -> tuple[str, dict[str, Any], Optional[str]]:
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
            option_journey_id = str(getattr(option, "sequence", "") or "").strip() or None
            transition_journey_id = str(getattr(transition, "sequence", "") or "").strip() or None
            return target, resolved_context, option_journey_id or transition_journey_id or (str(journey_id or "").strip() or None)

        if selected == "confirm":
            target = str(getattr(transition, "confirm_route", "") or "").strip()
            if target:
                transition_journey_id = str(getattr(transition, "sequence", "") or "").strip() or None
                return target, dict(context_seed), transition_journey_id or (str(journey_id or "").strip() or None)
        if selected == "cancel":
            target = str(getattr(transition, "cancel_route", "") or "").strip()
            if target:
                transition_journey_id = str(getattr(transition, "sequence", "") or "").strip() or None
                return target, dict(context_seed), transition_journey_id or (str(journey_id or "").strip() or None)

        raise ValueError(
            f"option_id '{selected}' is not valid for transition '{transition.id}'"
        )

    @staticmethod
    def _resolve_condition_transition(
        transition,
        journey_id: Optional[str],
        context_seed: dict[str, Any],
    ) -> tuple[str, dict[str, Any], Optional[str]]:
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
            route_journey_id = str(getattr(route, "sequence", "") or "").strip() or None
            transition_journey_id = str(getattr(transition, "sequence", "") or "").strip() or None
            return target, dict(context_seed), route_journey_id or transition_journey_id or (str(journey_id or "").strip() or None)

        default_route = str(getattr(transition, "default_route", "") or "").strip()
        if default_route:
            transition_journey_id = str(getattr(transition, "sequence", "") or "").strip() or None
            return default_route, dict(context_seed), transition_journey_id or (str(journey_id or "").strip() or None)

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

    def _apply_revision_state(
        self,
        *,
        state: SessionState,
        trigger: TriggerInput,
        decision: RoutingDecision,
        prior_sequence_status: SequenceStatus,
        prior_artifact_refs: dict[str, str],
        now: datetime,
    ) -> None:
        context_seed = decision.context_seed
        build_mode = str(context_seed.get("build_mode") or "").strip().lower()
        if build_mode != "revision":
            if prior_sequence_status == SequenceStatus.STALE:
                state.sequence_status = SequenceStatus.IN_PROGRESS
            elif prior_sequence_status == SequenceStatus.REVISING and not state.active_revision_id:
                state.sequence_status = SequenceStatus.IN_PROGRESS
            return

        trigger_payload = dict(trigger.trigger_payload or {})
        change_request_id = str(
            context_seed.get("change_request_id")
            or trigger_payload.get("change_request_id")
            or ""
        ).strip() or None
        revision_scope = str(context_seed.get("revision_scope") or "").strip() or None
        revision_origin_workflow = str(
            context_seed.get("revision_origin_workflow")
            or trigger.workflow_id
            or state.current_workflow_id
            or ""
        ).strip() or None
        revision_id = str(context_seed.get("revision_id") or "").strip() or str(uuid.uuid4())
        restart_from_workflow = str(
            context_seed.get("impact_set", {}).get("restart_from")
            if isinstance(context_seed.get("impact_set"), dict)
            else ""
        ).strip() or decision.workflow_id
        artifact_kind = str(context_seed.get("artifact_kind") or "").strip() or None
        artifact_version_id = str(context_seed.get("artifact_version_id") or "").strip() or None
        impact_set = context_seed.get("impact_set")
        affected_layers = []
        if isinstance(impact_set, dict):
            affected_layers = [
                str(layer).strip()
                for layer in (impact_set.get("affected_declarative_families") or [])
                if str(layer).strip()
            ]

        context_seed["revision_id"] = revision_id
        context_seed["build_mode"] = "revision"
        context_seed["sequence_status"] = prior_sequence_status.value
        if change_request_id:
            context_seed["change_request_id"] = change_request_id
        if revision_origin_workflow:
            context_seed["revision_origin_workflow"] = revision_origin_workflow

        state.active_revision_id = revision_id
        state.active_change_request_id = change_request_id
        state.current_revision_scope = revision_scope
        state.revision_origin_workflow = revision_origin_workflow
        state.restart_from_workflow = restart_from_workflow

        if artifact_kind and artifact_version_id:
            state.artifact_version_refs[artifact_kind] = artifact_version_id

        stale_reason = change_request_id or revision_id
        if decision.is_full_restart:
            layers = affected_layers or ([artifact_kind] if artifact_kind else [])
            for layer in layers:
                state.stale_layers[layer] = stale_reason
            state.sequence_status = SequenceStatus.STALE
        else:
            state.sequence_status = SequenceStatus.REVISING

        last_entry = state.revision_history[-1] if state.revision_history else None
        if last_entry is None or last_entry.revision_id != revision_id:
            state.revision_history.append(
                RevisionEntry(
                    revision_id=revision_id,
                    change_request_id=change_request_id,
                    scope=revision_scope,
                    origin_workflow=revision_origin_workflow,
                    target_workflow=decision.workflow_id,
                    from_version_refs=prior_artifact_refs,
                    timestamp=now,
                )
            )

    @staticmethod
    def _complete_active_revision(state: SessionState) -> None:
        if state.active_revision_id and state.revision_history:
            latest_entry = state.revision_history[-1]
            if latest_entry.revision_id == state.active_revision_id:
                latest_entry.to_version_refs = dict(state.artifact_version_refs)
        state.active_revision_id = None
        state.active_change_request_id = None
        state.current_revision_scope = None
        state.revision_origin_workflow = None
        state.restart_from_workflow = None
        state.stale_layers = {}

    async def fail_active_revision(
        self,
        *,
        app_id: str,
        user_id: str,
        workflow_id: str,
    ) -> None:
        """Clear stuck revision state after a workflow failure.

        Called when a workflow errors during a revision so the session does not
        remain stuck in REVISING state indefinitely. Sets sequence_status to
        STALE so the next refinement request can re-classify and route correctly.

        No-op when no revision is active.
        """
        app = str(app_id or "").strip()
        user = str(user_id or "").strip()
        if not app or not user:
            return

        state = await self._load_or_create_state(app_id=app, user_id=user)
        if not state.active_revision_id:
            return

        self._complete_active_revision(state)
        state.sequence_status = SequenceStatus.STALE
        state.updated_at = datetime.now(UTC)
        await self._store.upsert(state)
        logger.info(
            "[JOURNEY] Cleared stuck revision state for app %s after workflow %s failure",
            app,
            str(workflow_id or "").strip(),
        )


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

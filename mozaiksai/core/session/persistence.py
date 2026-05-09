from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Dict, Optional

from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager

from .model import (
    PendingDecisionAction,
    PendingHarnessDecision,
    RevisionEntry,
    SequenceStatus,
    SessionLifecycle,
    SessionState,
)


_LEGACY_LIFECYCLE_ALIASES = {
    "refining": SessionLifecycle.ACTIVE,
    "awaiting_approval": SessionLifecycle.AWAITING_DECISION,
}


def _coerce_datetime(value: Any, *, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed
        except Exception:
            return fallback
    return fallback


def _coerce_string_map(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {}
    normalized: Dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key or "").strip()
        val = str(raw_value or "").strip()
        if key and val:
            normalized[key] = val
    return normalized


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            normalized.append(text)
    return normalized


def _derive_legacy_sequence_status(lifecycle_state: SessionLifecycle) -> SequenceStatus:
    if lifecycle_state == SessionLifecycle.COMPLETED:
        return SequenceStatus.COMPLETED
    if lifecycle_state == SessionLifecycle.STALE:
        return SequenceStatus.STALE
    return SequenceStatus.IN_PROGRESS


def _coerce_revision_history(value: Any, *, fallback: datetime) -> list[RevisionEntry]:
    if not isinstance(value, list):
        return []
    entries: list[RevisionEntry] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        revision_id = str(item.get("revision_id") or "").strip()
        if not revision_id:
            continue
        entries.append(
            RevisionEntry(
                revision_id=revision_id,
                change_request_id=str(item.get("change_request_id") or "").strip() or None,
                scope=str(item.get("scope") or "").strip() or None,
                origin_workflow=str(item.get("origin_workflow") or "").strip() or None,
                target_workflow=str(item.get("target_workflow") or "").strip() or None,
                from_version_refs=_coerce_string_map(item.get("from_version_refs")),
                to_version_refs=_coerce_string_map(item.get("to_version_refs")),
                timestamp=_coerce_datetime(item.get("timestamp"), fallback=fallback),
            )
        )
    return entries


def _coerce_pending_actions(value: Any) -> list[PendingDecisionAction]:
    if not isinstance(value, list):
        return []
    actions: list[PendingDecisionAction] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        action_id = str(item.get("action_id") or "").strip()
        label = str(item.get("label") or "").strip()
        if not action_id or not label:
            continue
        actions.append(
            PendingDecisionAction(
                action_id=action_id,
                label=label,
                action_type=str(item.get("action_type") or "run_workflow").strip() or "run_workflow",
                workflow_id=str(item.get("workflow_id") or "").strip() or None,
                metadata=_coerce_dict(item.get("metadata")),
            )
        )
    return actions


def _coerce_pending_harness_decision(
    value: Any,
    *,
    fallback: datetime,
) -> Optional[PendingHarnessDecision]:
    if not isinstance(value, dict):
        return None
    decision_id = str(value.get("decision_id") or "").strip()
    decision_type = str(value.get("decision_type") or "").strip()
    message = str(value.get("message") or "").strip()
    rationale = str(value.get("rationale") or "").strip()
    if not decision_id or not decision_type or not message or not rationale:
        return None
    return PendingHarnessDecision(
        decision_id=decision_id,
        decision_type=decision_type,
        message=message,
        rationale=rationale,
        confidence=float(value.get("confidence") or 0.0),
        recommended_workflow_id=str(value.get("recommended_workflow_id") or "").strip() or None,
        selected_paths=_coerce_string_list(value.get("selected_paths")),
        clarification_question=str(value.get("clarification_question") or "").strip() or None,
        change_request_id=str(value.get("change_request_id") or "").strip() or None,
        revision_id=str(value.get("revision_id") or "").strip() or None,
        requires_confirmation=bool(value.get("requires_confirmation", False)),
        trigger_source=str(value.get("trigger_source") or "refinement").strip() or "refinement",
        requested_workflow_id=str(value.get("requested_workflow_id") or "").strip() or None,
        journey_id=str(value.get("journey_id") or "").strip() or None,
        context_variables=_coerce_dict(value.get("context_variables")),
        trigger_payload=_coerce_dict(value.get("trigger_payload")),
        actions=_coerce_pending_actions(value.get("actions")),
        metadata=_coerce_dict(value.get("metadata")),
        created_at=_coerce_datetime(value.get("created_at"), fallback=fallback),
    )


class SessionStateStore:
    """Persistence layer for SessionRouter state."""

    def __init__(self, persistence: Optional[AG2PersistenceManager] = None) -> None:
        self._persistence = persistence or AG2PersistenceManager()

    @staticmethod
    def session_id_for_scope(app_id: str, user_id: str) -> str:
        app = str(app_id or "").strip()
        user = str(user_id or "").strip()
        return f"session_router::{app}::{user}"

    async def _coll(self):
        coll_getter = getattr(self._persistence, "_coll", None)
        if callable(coll_getter):
            try:
                return await coll_getter("SessionRouterState")
            except TypeError:
                pass

        persistence_root = getattr(self._persistence, "persistence", None)
        if persistence_root is None:
            raise TypeError("Persistence backend does not expose SessionRouterState collection access")

        await persistence_root._ensure_client()
        assert persistence_root.client is not None, "Mongo client not initialized"
        return persistence_root.client["mozaiksai"]["SessionRouterState"]

    async def load(self, *, app_id: str, user_id: str) -> Optional[SessionState]:
        coll = await self._coll()
        session_id = self.session_id_for_scope(app_id, user_id)
        doc = await coll.find_one({"_id": session_id})
        if not isinstance(doc, dict):
            return None

        now = datetime.now(UTC)
        raw_lifecycle = str(doc.get("lifecycle_state") or SessionLifecycle.INITIAL.value)
        try:
            lifecycle_state = SessionLifecycle(raw_lifecycle)
        except Exception:
            lifecycle_state = _LEGACY_LIFECYCLE_ALIASES.get(raw_lifecycle, SessionLifecycle.INITIAL)
        raw_sequence_status = str(doc.get("sequence_status") or "").strip()
        try:
            sequence_status = (
                SequenceStatus(raw_sequence_status)
                if raw_sequence_status
                else _derive_legacy_sequence_status(lifecycle_state)
            )
        except Exception:
            sequence_status = _derive_legacy_sequence_status(lifecycle_state)

        return SessionState(
            session_id=session_id,
            app_id=str(doc.get("app_id") or app_id),
            user_id=str(doc.get("user_id") or user_id),
            sequence_status=sequence_status,
            sequence_completed_at=(
                _coerce_datetime(doc.get("sequence_completed_at"), fallback=now)
                if doc.get("sequence_completed_at") is not None
                else None
            ),
            active_revision_id=str(doc.get("active_revision_id") or "").strip() or None,
            active_change_request_id=str(doc.get("active_change_request_id") or "").strip() or None,
            current_revision_scope=str(doc.get("current_revision_scope") or "").strip() or None,
            revision_origin_workflow=str(doc.get("revision_origin_workflow") or "").strip() or None,
            restart_from_workflow=str(doc.get("restart_from_workflow") or "").strip() or None,
            lifecycle_state=lifecycle_state,
            current_workflow_id=doc.get("current_workflow_id"),
            current_chat_id=doc.get("current_chat_id"),
            journey_instance_id=doc.get("journey_instance_id"),
            journey_key=doc.get("journey_key"),
            journey_position=int(doc.get("journey_position") or 0),
            journey_total_steps=int(doc.get("journey_total_steps") or 0),
            pending_transition_id=doc.get("pending_transition_id"),
            pending_harness_decision=_coerce_pending_harness_decision(
                doc.get("pending_harness_decision"),
                fallback=now,
            ),
            last_trigger_source=doc.get("last_trigger_source"),
            last_requested_workflow_id=doc.get("last_requested_workflow_id"),
            last_route_explanation=doc.get("last_route_explanation"),
            artifact_version_refs=_coerce_string_map(doc.get("artifact_version_refs")),
            stale_layers=_coerce_string_map(doc.get("stale_layers")),
            revision_history=_coerce_revision_history(doc.get("revision_history"), fallback=now),
            created_at=_coerce_datetime(doc.get("created_at"), fallback=now),
            updated_at=_coerce_datetime(doc.get("updated_at"), fallback=now),
        )

    async def upsert(self, state: SessionState) -> None:
        coll = await self._coll()
        payload: Dict[str, Any] = asdict(state)
        payload["_id"] = state.session_id
        payload["lifecycle_state"] = state.lifecycle_state.value
        payload["sequence_status"] = state.sequence_status.value
        await coll.update_one({"_id": state.session_id}, {"$set": payload}, upsert=True)

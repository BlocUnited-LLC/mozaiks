from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Dict, Optional

from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager

from .model import SessionLifecycle, SessionState


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
            lifecycle_state = SessionLifecycle.INITIAL

        return SessionState(
            session_id=session_id,
            app_id=str(doc.get("app_id") or app_id),
            user_id=str(doc.get("user_id") or user_id),
            lifecycle_state=lifecycle_state,
            current_workflow_id=doc.get("current_workflow_id"),
            current_chat_id=doc.get("current_chat_id"),
            journey_instance_id=doc.get("journey_instance_id"),
            journey_key=doc.get("journey_key"),
            journey_position=int(doc.get("journey_position") or 0),
            journey_total_steps=int(doc.get("journey_total_steps") or 0),
            pending_transition_id=doc.get("pending_transition_id"),
            pending_approval_id=doc.get("pending_approval_id"),
            last_trigger_source=doc.get("last_trigger_source"),
            last_requested_workflow_id=doc.get("last_requested_workflow_id"),
            last_route_explanation=doc.get("last_route_explanation"),
            created_at=_coerce_datetime(doc.get("created_at"), fallback=now),
            updated_at=_coerce_datetime(doc.get("updated_at"), fallback=now),
        )

    async def upsert(self, state: SessionState) -> None:
        coll = await self._coll()
        payload: Dict[str, Any] = asdict(state)
        payload["_id"] = state.session_id
        payload["lifecycle_state"] = state.lifecycle_state.value
        await coll.update_one({"_id": state.session_id}, {"$set": payload}, upsert=True)

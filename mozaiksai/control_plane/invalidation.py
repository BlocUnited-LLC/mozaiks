from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from logs.logging_config import get_core_logger
from mozaiksai.core.artifacts import ArtifactStore, get_artifact_store
from mozaiksai.core.session.persistence import SessionStateStore

if TYPE_CHECKING:
    from .implementations.refinement_router import RefinementRequest, RefinementRoutingDecision

logger = get_core_logger("control_plane_invalidation")


class ArtifactInvalidationService:
    """Persist stale artifact status for revision requests."""

    def __init__(
        self,
        *,
        session_store: Optional[SessionStateStore] = None,
        artifact_store: Optional[ArtifactStore] = None,
    ) -> None:
        self._session_store = session_store
        self._artifact_store = artifact_store

    async def invalidate_for_change_request(
        self,
        *,
        refinement_request: "RefinementRequest",
        routing_decision: "RefinementRoutingDecision",
        change_request_id: Optional[str],
        artifact_store: Optional[ArtifactStore] = None,
    ) -> dict[str, object]:
        resolved_change_request_id = str(change_request_id or "").strip() or None
        affected_artifact_kinds = [
            str(kind).strip()
            for kind in list(routing_decision.impact_set.affected_declarative_families or [])
            if str(kind).strip()
        ]
        if not resolved_change_request_id:
            return {
                "change_request_id": None,
                "affected_artifact_kinds": affected_artifact_kinds,
                "invalidated_artifact_version_ids": [],
            }

        if not routing_decision.impact_set.requires_rebuild:
            return {
                "change_request_id": resolved_change_request_id,
                "affected_artifact_kinds": affected_artifact_kinds,
                "invalidated_artifact_version_ids": [],
            }

        app_id = str(refinement_request.app_id or "").strip()
        if not app_id:
            return {
                "change_request_id": resolved_change_request_id,
                "affected_artifact_kinds": affected_artifact_kinds,
                "invalidated_artifact_version_ids": [],
            }

        artifact_version_refs: dict[str, str] = {}
        user_id = str(refinement_request.user_id or "").strip()
        if user_id:
            try:
                state = await (self._session_store or SessionStateStore()).load(app_id=app_id, user_id=user_id)
            except Exception as exc:
                logger.warning("Failed to load session state for artifact invalidation: %s", exc)
                state = None
            if state is not None:
                artifact_version_refs.update(dict(state.artifact_version_refs or {}))

        request_artifact_kind = str(refinement_request.artifact_kind or "").strip()
        request_artifact_version_id = str(refinement_request.artifact_version_id or "").strip()
        if request_artifact_kind and request_artifact_version_id:
            artifact_version_refs[request_artifact_kind] = request_artifact_version_id

        if not affected_artifact_kinds and request_artifact_kind:
            affected_artifact_kinds = [request_artifact_kind]

        invalidated_artifact_version_ids: list[str] = []
        if artifact_version_refs and affected_artifact_kinds:
            invalidated_artifact_version_ids = await (artifact_store or self._artifact_store or get_artifact_store()).invalidate_artifact_version_refs(
                app_id=app_id,
                artifact_version_refs=artifact_version_refs,
                affected_artifact_kinds=affected_artifact_kinds,
                reason=f"change_request:{resolved_change_request_id}",
            )

        return {
            "change_request_id": resolved_change_request_id,
            "affected_artifact_kinds": affected_artifact_kinds,
            "invalidated_artifact_version_ids": invalidated_artifact_version_ids,
        }


_invalidation_service: Optional[ArtifactInvalidationService] = None


def get_artifact_invalidation_service() -> ArtifactInvalidationService:
    global _invalidation_service
    if _invalidation_service is None:
        _invalidation_service = ArtifactInvalidationService()
    return _invalidation_service


__all__ = ["ArtifactInvalidationService", "get_artifact_invalidation_service"]

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional, Set

from logs.logging_config import get_core_logger
from mozaiksai.core.artifacts import ArtifactStore, get_artifact_store
from mozaiksai.core.session.persistence import SessionStateStore
from mozaiksai.core.workflow.pack.config import load_global_pack_graph

if TYPE_CHECKING:
    from .implementations.refinement_router import RefinementRequest, RefinementRoutingDecision

logger = get_core_logger("control_plane_invalidation")


def _get_downstream_families(
    written_families: Set[str],
    dependency_graph: Dict[str, List[str]],
) -> Set[str]:
    """BFS: return all families that depend (directly or transitively) on written_families.

    The dependency_graph maps each family to its upstream dependencies.
    A family is downstream of a written family if written_family appears
    in that family's dependency list (or transitively via another downstream).
    """
    downstream: Set[str] = set()
    queue = list(written_families)
    while queue:
        upstream = queue.pop()
        for candidate, deps in dependency_graph.items():
            if upstream in deps and candidate not in downstream and candidate not in written_families:
                downstream.add(candidate)
                queue.append(candidate)
    return downstream


class ArtifactInvalidationService:
    """Persist stale artifact status for revision requests.

    After directly invalidating the artifact families declared in the routing
    decision's impact_set, this service also marks all downstream-dependent
    families stale using the artifact_dependency_graph from the global pack.
    This ensures that a concept_patch, for example, propagates staleness to
    design_docs, workflow_bundle, and app_bundle without requiring a full_rebuild
    to be triggered — the classifier can then select the minimal covering sequence.
    """

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
                "downstream_staled_families": [],
            }

        if not routing_decision.impact_set.requires_rebuild:
            return {
                "change_request_id": resolved_change_request_id,
                "affected_artifact_kinds": affected_artifact_kinds,
                "invalidated_artifact_version_ids": [],
                "downstream_staled_families": [],
            }

        app_id = str(refinement_request.app_id or "").strip()
        if not app_id:
            return {
                "change_request_id": resolved_change_request_id,
                "affected_artifact_kinds": affected_artifact_kinds,
                "invalidated_artifact_version_ids": [],
                "downstream_staled_families": [],
            }

        resolved_store = artifact_store or self._artifact_store or get_artifact_store()
        reason = f"change_request:{resolved_change_request_id}"

        # --- Direct invalidation via session-tracked version refs ---
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
            invalidated_artifact_version_ids = await resolved_store.invalidate_artifact_version_refs(
                app_id=app_id,
                artifact_version_refs=artifact_version_refs,
                affected_artifact_kinds=affected_artifact_kinds,
                reason=reason,
            )

        # --- Downstream propagation via artifact_dependency_graph ---
        # Mark downstream families stale by family (not by version ID), so the
        # classifier sees accurate staleness even when a downstream artifact's
        # version ID is not in the current session's artifact_version_refs.
        downstream_staled_families: list[str] = []
        try:
            pack = load_global_pack_graph()
            dependency_graph: Dict[str, List[str]] = (
                getattr(pack, "artifact_dependency_graph", None) or {}
                if pack is not None
                else {}
            )
            if dependency_graph and affected_artifact_kinds:
                downstream = _get_downstream_families(set(affected_artifact_kinds), dependency_graph)
                for family in sorted(downstream):
                    count = await resolved_store.invalidate_artifact_family(
                        app_id=app_id,
                        artifact_kind=family,
                        artifact_key=family,
                        reason=reason,
                    )
                    if count:
                        downstream_staled_families.append(family)
                        logger.info(
                            "Downstream staleness propagated: %s → %s (%d version(s) staled)",
                            affected_artifact_kinds,
                            family,
                            count,
                        )
        except Exception as exc:
            logger.warning("Downstream staleness propagation failed: %s", exc)

        return {
            "change_request_id": resolved_change_request_id,
            "affected_artifact_kinds": affected_artifact_kinds,
            "invalidated_artifact_version_ids": invalidated_artifact_version_ids,
            "downstream_staled_families": downstream_staled_families,
        }


_invalidation_service: Optional[ArtifactInvalidationService] = None


def get_artifact_invalidation_service() -> ArtifactInvalidationService:
    global _invalidation_service
    if _invalidation_service is None:
        _invalidation_service = ArtifactInvalidationService()
    return _invalidation_service


__all__ = ["ArtifactInvalidationService", "get_artifact_invalidation_service"]

from __future__ import annotations

from typing import Any, Optional

from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.core.data.persistence.artifact_store import BuilderArtifactStore

from ._shared import list_head, normalize_context, text_excerpt


async def get_concept_overview(
    *,
    context: ControlPlaneToolContext | dict[str, Any] | None = None,
    store: Optional[BuilderArtifactStore] = None,
) -> dict[str, Any]:
    tool_context = normalize_context(context)
    app_id = str(tool_context.app_id or "").strip()
    if not app_id:
        return {"present": False, "reason": "missing_app_id"}

    concept_store = store or BuilderArtifactStore()
    concept = await concept_store.get_concept(app_id=app_id)
    if not isinstance(concept, dict):
        return {"present": False, "app_id": app_id}

    blueprint = concept.get("Blueprint")
    if not isinstance(blueprint, dict):
        blueprint = {}

    return {
        "present": True,
        "app_id": app_id,
        "app_name": str(concept.get("app_name") or blueprint.get("app_name") or "").strip() or None,
        "concept_overview": text_excerpt(
            concept.get("ConceptOverview") or blueprint.get("concept_overview"),
            max_length=800,
        ),
        "value_proposition": text_excerpt(blueprint.get("value_proposition"), max_length=300),
        "target_user": text_excerpt(blueprint.get("target_user"), max_length=200),
        "target_users": list_head(blueprint.get("target_users"), limit=6),
        "core_features": list_head(blueprint.get("core_features"), limit=8),
        "capability_pack_hints": list_head(
            concept.get("capability_pack_hints") or blueprint.get("capability_pack_hints"),
            limit=8,
        ),
        "surface_candidate_hints": list_head(
            concept.get("surface_candidate_hints") or blueprint.get("surface_candidate_hints"),
            limit=8,
        ),
        "agentic_capabilities": list_head(
            concept.get("agentic_capabilities") or blueprint.get("agentic_capabilities"),
            limit=8,
        ),
        "status": str(concept.get("status") or "").strip() or None,
        "updated_at": concept.get("updated_at"),
    }

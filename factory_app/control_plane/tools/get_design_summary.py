from __future__ import annotations

from typing import Any, Optional

from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.core.data.persistence.artifact_store import BuilderArtifactStore

from ._shared import normalize_context, text_excerpt


async def get_design_summary(
    *,
    context: ControlPlaneToolContext | dict[str, Any] | None = None,
    store: Optional[BuilderArtifactStore] = None,
) -> dict[str, Any]:
    tool_context = normalize_context(context)
    app_id = str(tool_context.app_id or "").strip()
    if not app_id:
        return {"present": False, "reason": "missing_app_id"}

    design_store = store or BuilderArtifactStore()
    docs = await design_store.list_design_docs(app_id=app_id)
    latest_database_intent = await design_store.get_latest_database_intent(app_id=app_id)
    if not docs and not latest_database_intent:
        return {"present": False, "app_id": app_id}

    by_kind: dict[str, dict[str, Any]] = {}
    surface_ids: list[str] = []
    for doc in docs:
        kind = str(doc.get("kind") or "").strip()
        if not kind:
            continue
        by_kind[kind] = {
            "status": str(doc.get("status") or "").strip() or None,
            "stage": str(doc.get("stage") or "").strip() or None,
            "content_excerpt": text_excerpt(doc.get("content"), max_length=450),
            "updated_at": doc.get("updated_at"),
        }
        surface_map = doc.get("surface_map")
        if isinstance(surface_map, dict):
            for surface in surface_map.get("surfaces") or []:
                if isinstance(surface, dict):
                    surface_id = str(surface.get("surface_id") or "").strip()
                    if surface_id and surface_id not in surface_ids:
                        surface_ids.append(surface_id)

    database_intent_bundle = None
    if isinstance(latest_database_intent, dict):
        bundle = latest_database_intent.get("database_intent_bundle")
        if isinstance(bundle, dict):
            database_intent_bundle = {
                "artifact_version_id": bundle.get("artifact_version_id"),
                "surface_count": len(bundle.get("surfaces") or []),
                "shared_collection_count": len(bundle.get("shared_collections") or []),
                "default_scope_field": ((bundle.get("policies") or {}).get("default_scope_field")),
                "updated_at": latest_database_intent.get("updated_at"),
            }

    return {
        "present": True,
        "app_id": app_id,
        "document_kinds": sorted(by_kind.keys()),
        "documents": by_kind,
        "surface_ids": sorted(surface_ids)[:12],
        "database_intent": database_intent_bundle,
    }

from __future__ import annotations

from typing import Any

from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.core.artifacts import ArtifactStore, get_artifact_store

from ._shared import normalize_context


async def get_stale_artifact_families(
    *,
    context: ControlPlaneToolContext | dict[str, Any] | None = None,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Return the artifact families that have stale versions for this app.

    A family is stale when a prior build sequence invalidated it — meaning a
    dependent artifact was written but this family was not yet refreshed. The
    classifier uses this signal to upgrade its routing decision: if the user
    requests an app_bundle patch but design_docs is stale, the route must
    include DesignDocs to avoid building against outdated design intent.

    Returns:
        {
            "stale_families": ["design_docs", "app_bundle"],   # sorted list
            "all_current": False,                               # True when nothing is stale
        }
    """
    ctx = normalize_context(context)
    app_id = str(ctx.app_id or "").strip()
    if not app_id:
        return {"stale_families": [], "all_current": True}

    store = artifact_store or get_artifact_store()
    stale = await store.get_stale_artifact_families(app_id=app_id)
    return {
        "stale_families": stale,
        "all_current": len(stale) == 0,
    }


__all__ = ["get_stale_artifact_families"]

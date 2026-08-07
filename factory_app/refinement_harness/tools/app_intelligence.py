from __future__ import annotations

from typing import Any

from mozaiksai.control_plane.app_context import get_current_app_intelligence_snapshot
from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.core.app_context import (
    AppIntelligenceSnapshot,
    build_app_intelligence_catalog,
    search_app_intelligence_snapshot,
)
from mozaiksai.core.artifacts.store import ArtifactStore

from ._context_graph import load_context_graph_for_tool
from ._shared import normalize_context


async def get_app_intelligence_context(
    *,
    query: str | None = None,
    max_results: int = 12,
    context: ControlPlaneToolContext | dict[str, Any] | None = None,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Return compact App Intelligence for refinement planning."""
    tool_context = normalize_context(context)
    loaded = await _load_app_intelligence_snapshot(
        tool_context=tool_context,
        artifact_store=artifact_store,
    )
    if not loaded.get("present"):
        return {**loaded, "results": []}

    snapshot = loaded["snapshot"]
    resolved_query = str(query or tool_context.raw_user_request or "").strip()
    return {
        "present": True,
        "source": loaded["source"],
        "app_intelligence_catalog": build_app_intelligence_catalog(snapshot),
        "results": (
            search_app_intelligence_snapshot(
                snapshot,
                resolved_query,
                max_results=_bounded_int(max_results, default=12, minimum=1, maximum=40),
            )
            if resolved_query
            else []
        ),
        "query": resolved_query or None,
        "warnings": list(loaded.get("warnings") or []),
    }


async def _load_app_intelligence_snapshot(
    *,
    tool_context: ControlPlaneToolContext,
    artifact_store: ArtifactStore | None,
) -> dict[str, Any]:
    app_id = str(tool_context.app_id or "").strip()
    if not app_id:
        return {"present": False, "reason": "missing_app_id", "warnings": []}

    current = await get_current_app_intelligence_snapshot(
        app_id=app_id,
        artifact_store=artifact_store,
    )
    if current.snapshot is not None:
        return {
            "present": True,
            "source": "current_app_context",
            "snapshot": current.snapshot,
            "warnings": list(current.warnings),
        }

    if str(tool_context.build_record_id or "").strip():
        loaded = await load_context_graph_for_tool(context=tool_context, artifact_store=artifact_store)
        snapshot = loaded.get("app_intelligence_snapshot")
        if loaded.get("present") and isinstance(snapshot, AppIntelligenceSnapshot):
            return {
                "present": True,
                "source": loaded["workspace"].get("source") or "artifact_workspace",
                "snapshot": snapshot,
                "warnings": [*list(current.warnings), *list(loaded.get("warnings") or [])],
            }

    return {
        "present": False,
        "reason": "app_intelligence_snapshot_unavailable",
        "warnings": list(current.warnings),
    }


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


__all__ = ["get_app_intelligence_context"]

from __future__ import annotations

from typing import Any

from mozaiksai.control_plane.context_graph import build_context_graph_scope
from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.core.artifacts.store import ArtifactStore

from ._context_graph import load_context_graph_for_tool, normalize_selected_paths
from ._shared import normalize_context


async def get_context_graph_scope(
    *,
    context: ControlPlaneToolContext | dict[str, Any] | None = None,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Return graph-neighborhood context for explicit scoped coding files."""
    tool_context = normalize_context(context)
    loaded = await load_context_graph_for_tool(context=tool_context, artifact_store=artifact_store)
    if not loaded.get("present"):
        return loaded

    artifact = loaded["artifact"]
    selected_paths = normalize_selected_paths(tool_context.extra.get("selected_file_paths"))
    scope = build_context_graph_scope(
        graph=loaded["graph"],
        selected_paths=selected_paths,
        file_map=loaded["file_map"],
        raw_user_request=tool_context.raw_user_request,
    )
    scope.update(
        {
            "build_record_id": artifact.id,
            "build_family": artifact.build_family,
            "build_key": artifact.build_key,
            "source": loaded["workspace"]["source"],
            "warnings": list(loaded.get("warnings") or []),
            "scan_health": loaded["workspace"].get("scan_health"),
        }
    )
    return scope

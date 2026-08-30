"""Refinement tool: load context graph catalog for contract surface planning.

Available at: contract_surface_requested

Returns the merged context graph catalog — ranked candidate files, matched
nodes, and file tree — scoped for the ContractSurfacePlanner to use when
resolving which workspace files exist for each classified surface.

This is a read-only context tool. It does not write files or artifacts.
"""

from __future__ import annotations

from typing import Any

from mozaiksai.control_plane.context_graph.query import build_context_graph_catalog
from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.core.artifacts.store import ArtifactStore

from ._context_graph import load_context_graph_for_tool
from ._shared import normalize_context


async def get_contract_surface_context(
    context: ControlPlaneToolContext | dict[str, Any] | None = None,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Return a compact context graph catalog for contract surface resolution.

    Loads the artifact workspace and the current AppContextGraph, then returns
    a catalog the ContractSurfacePlanner uses to confirm which canonical paths
    actually exist in the workspace before resolving surface file lists.
    """

    tool_context = normalize_context(context)
    loaded = await load_context_graph_for_tool(context=tool_context, artifact_store=artifact_store)
    if not loaded.get("present"):
        return loaded

    artifact = loaded["artifact"]
    raw_user_request = str(tool_context.raw_user_request or "").strip()
    context_graph_catalog = build_context_graph_catalog(
        graph=loaded["graph"],
        raw_user_request=raw_user_request,
        file_map=loaded["file_map"],
        max_nodes=25,
        max_edges=40,
    )

    return {
        "present": True,
        "app_id": tool_context.app_id,
        "build_record_id": artifact.id,
        "raw_user_request": raw_user_request,
        "context_graph": context_graph_catalog,
        "file_count": context_graph_catalog.get("file_count", 0),
        "warnings": list(loaded.get("warnings") or []),
        "scan_health": loaded["workspace"].get("scan_health"),
    }

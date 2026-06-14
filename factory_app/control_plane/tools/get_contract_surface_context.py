"""Control-plane tool: load context graph catalog for contract surface planning.

Available at: contract_surface_requested

Returns the merged context graph catalog — ranked candidate files, matched
nodes, and file tree — scoped for the ContractSurfacePlanner to use when
resolving which workspace files exist for each classified surface.

This is a read-only context tool. It does not write files or artifacts.
"""

from __future__ import annotations

from typing import Any

from factory_app.control_plane.tools._artifact_workspace import load_artifact_workspace
from mozaiksai.control_plane.context_graph.query import build_context_graph_catalog
from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.core.app_context.models import AppContextGraph


async def get_contract_surface_context(
    context: ControlPlaneToolContext | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Return a compact context graph catalog for contract surface resolution.

    Loads the artifact workspace and the current AppContextGraph, then returns
    a catalog the ContractSurfacePlanner uses to confirm which canonical paths
    actually exist in the workspace before resolving surface file lists.
    """

    if context is None:
        return {"present": False, "reason": "no tool context"}

    app_id = str(context.app_id or "").strip()
    artifact_version_id = str(context.artifact_version_id or "").strip()
    raw_user_request = str(context.raw_user_request or "").strip()

    if not app_id:
        return {"present": False, "reason": "missing app_id"}

    # Load the artifact workspace to get the file tree.
    workspace_result = await load_artifact_workspace(  # type: ignore[call-arg]
        app_id=app_id,
        artifact_kind=str(context.artifact_kind or "app_bundle").strip(),
        artifact_key=str(context.artifact_key or "").strip() or None,
        artifact_version_id=artifact_version_id or None,  # type: ignore[arg-type]
        max_file_bytes=8_192,
        include_content=False,
    )

    file_tree: list[str] = []
    if workspace_result.get("present") and isinstance(workspace_result.get("files"), dict):
        file_tree = sorted(workspace_result["files"].keys())

    # Attempt to load the context graph for richer semantic ranking.
    context_graph_catalog: dict[str, Any] = {
        "present": False,
        "file_tree": file_tree,
        "candidate_files": [],
    }

    try:
        from mozaiksai.core.app_context.context_graph import load_context_graph_for_artifact  # type: ignore[attr-defined]

        graph: AppContextGraph | None = await load_context_graph_for_artifact(
            app_id=app_id,
            artifact_version_id=artifact_version_id or None,
        )
        if graph is not None:
            catalog = build_context_graph_catalog(
                graph=graph,
                raw_user_request=raw_user_request,
                file_map={path: "" for path in file_tree},
                max_nodes=25,
                max_edges=40,
            )
            context_graph_catalog = catalog
            context_graph_catalog["file_tree"] = file_tree
    except Exception:
        # Context graph is best-effort. Fall back to file tree only.
        pass

    return {
        "present": True,
        "app_id": app_id,
        "artifact_version_id": artifact_version_id or None,
        "raw_user_request": raw_user_request,
        "context_graph": context_graph_catalog,
        "file_count": len(file_tree),
    }

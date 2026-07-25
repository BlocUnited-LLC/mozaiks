from __future__ import annotations

from typing import Any

from mozaiksai.control_plane.app_context import get_current_app_context_graph
from mozaiksai.core.app_context.context_graph import (
    build_context_graph_from_file_map,
    context_graph_parser_status,
    merge_context_graphs,
)
from mozaiksai.core.app_context.scan_policy import select_source_file_map
from mozaiksai.core.artifacts.store import ArtifactStore, get_artifact_store

from ._artifact_workspace import load_artifact_workspace, safe_relpath
from ._shared import normalize_context


async def load_context_graph_for_tool(
    *,
    context: Any = None,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Load artifact workspace and merge current app-context graph with code graph."""
    tool_context = normalize_context(context)
    app_id = str(tool_context.app_id or "").strip()
    artifact_version_id = str(tool_context.artifact_version_id or "").strip()
    if not app_id or not artifact_version_id:
        return {"present": False, "reason": "missing_app_id_or_artifact_version"}

    store = artifact_store or get_artifact_store()
    workspace = await load_artifact_workspace(
        artifact_store=store,
        app_id=app_id,
        artifact_version_id=artifact_version_id,
    )
    if not workspace.get("present"):
        return workspace

    artifact = workspace["artifact"]
    scan_result = select_source_file_map(
        workspace["file_map"],
        source=workspace.get("source") or "artifact_workspace",
    )
    file_map = scan_result.file_map
    scan_health = dict(scan_result.health)
    scan_health["parser_status"] = context_graph_parser_status()
    artifact_graph = build_context_graph_from_file_map(
        app_id=app_id,
        artifact_version_id=artifact.id,
        artifact_kind=artifact.artifact_kind,
        artifact_key=artifact.artifact_key,
        file_map=file_map,
    )
    current_graph_lookup = await get_current_app_context_graph(
        app_id=app_id,
        artifact_store=store,
    )
    merged_graph = merge_context_graphs(
        app_id=app_id,
        graph_id=f"context_graph:{app_id}:{artifact.id}",
        graphs=[current_graph_lookup.graph, artifact_graph],
    )

    return {
        "present": True,
        "artifact": artifact,
        "file_map": file_map,
        "workspace": {**workspace, "scan_health": scan_health},
        "graph": merged_graph,
        "warnings": [*list(current_graph_lookup.warnings), *list(scan_result.warnings)],
    }


def normalize_selected_paths(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    selected: list[str] = []
    for item in value:
        safe = safe_relpath(item)
        if safe and safe not in selected:
            selected.append(safe)
    return selected

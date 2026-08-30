from __future__ import annotations

from typing import Any

from mozaiksai.control_plane.app_context import get_current_app_context_graph
from mozaiksai.core.app_context.context_graph import (
    merge_context_graphs,
)
from mozaiksai.core.app_context.indexer import index_file_map
from mozaiksai.core.app_context.intelligence import build_app_intelligence_catalog
from mozaiksai.core.app_context.source_corpus import build_source_corpus_catalog
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
    build_record_id = str(tool_context.build_record_id or "").strip()
    if not app_id or not build_record_id:
        return {"present": False, "reason": "missing_app_id_or_build_record"}

    store = artifact_store or get_artifact_store()
    workspace = await load_artifact_workspace(
        artifact_store=store,
        app_id=app_id,
        build_record_id=build_record_id,
    )
    if not workspace.get("present"):
        return workspace

    artifact = workspace["artifact"]
    indexed = index_file_map(
        app_id=app_id,
        file_map=workspace["file_map"],
        artifact_version_id=artifact.id,
        artifact_kind=artifact.build_family,
        artifact_key=artifact.build_key,
        source=workspace.get("source") or "artifact_workspace",
    )
    current_graph_lookup = await get_current_app_context_graph(
        app_id=app_id,
        artifact_store=store,
    )
    merged_graph = merge_context_graphs(
        app_id=app_id,
        graph_id=f"context_graph:{app_id}:{artifact.id}",
        graphs=[current_graph_lookup.graph, indexed.app_context_graph],
    )

    return {
        "present": True,
        "artifact": artifact,
        "file_map": indexed.safe_file_map,
        "source_context_bundle": indexed.source_corpus,
        "source_context_catalog": build_source_corpus_catalog(indexed.source_corpus, max_files=40, max_chunks=8),
        "app_intelligence_snapshot": indexed.app_intelligence_snapshot,
        "app_intelligence_catalog": build_app_intelligence_catalog(indexed.app_intelligence_snapshot, max_items=24),
        "workspace": {
            **workspace,
            "scan_health": indexed.scan_health,
            "context_graph_health_report": indexed.health_report,
        },
        "graph": merged_graph,
        "warnings": [*list(current_graph_lookup.warnings), *list(indexed.scan_result.warnings)],
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

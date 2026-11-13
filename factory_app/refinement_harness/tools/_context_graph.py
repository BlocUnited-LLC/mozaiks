from __future__ import annotations

import zipfile
from typing import Any

from mozaiksai.control_plane.contracts import ControlPlaneToolContext
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
    require_verified: bool = False,
) -> dict[str, Any]:
    """Index only the requested BuildRecord; never substitute current app state."""
    tool_context = normalize_graph_context(context)
    app_id = str(tool_context.artifact_app_id or "").strip()
    build_record_id = str(tool_context.build_record_id or "").strip()
    if not app_id or not build_record_id:
        return {"present": False, "reason": "missing_app_id_or_build_record"}

    store = artifact_store or get_artifact_store()
    try:
        workspace = await load_artifact_workspace(
            artifact_store=store,
            app_id=app_id,
            build_record_id=build_record_id,
            build_family=tool_context.build_family,
            build_key=tool_context.build_key,
            require_verified=require_verified,
        )
    except (OSError, ValueError, zipfile.BadZipFile):
        return {"present": False, "reason": "artifact_source_unavailable", "build_record_id": build_record_id, "warnings": []}
    if not workspace.get("present"):
        return {
            "present": False,
            "reason": workspace["reason"],
            "build_record_id": build_record_id,
            "warnings": [],
        }

    artifact = workspace["artifact"]
    try:
        indexed = index_file_map(
            app_id=app_id,
            file_map=workspace["file_map"],
            artifact_version_id=artifact.id,
            artifact_kind=artifact.build_family,
            artifact_key=artifact.build_key,
            source=workspace.get("source") or "artifact_workspace",
        )
    except ValueError:
        return {"present": False, "reason": "artifact_source_unavailable", "build_record_id": artifact.id, "warnings": []}

    scan_health = indexed.scan_health
    scan_health["artifact_source_incomplete"] = bool(
        workspace["scan_health"]["artifact_source_incomplete"]
        or scan_health.get("limit_reached") or scan_health.get("skipped", {}).get("large_file")
    )
    return {
        "present": True,
        "artifact": artifact,
        "provenance": workspace["provenance"],
        "file_map": indexed.safe_file_map,
        "source_context_bundle": indexed.source_corpus,
        "source_context_catalog": build_source_corpus_catalog(indexed.source_corpus, max_files=40, max_chunks=8),
        "app_intelligence_snapshot": indexed.app_intelligence_snapshot,
        "app_intelligence_catalog": build_app_intelligence_catalog(indexed.app_intelligence_snapshot, max_items=24),
        "workspace": {
            "source": workspace["source"],
            "provenance": workspace["provenance"],
            "scan_health": scan_health,
            "context_graph_health_report": indexed.health_report,
        },
        "graph": indexed.app_context_graph,
        "warnings": list(indexed.scan_result.warnings),
    }


def normalize_graph_context(context: Any) -> ControlPlaneToolContext:
    """Reject conflicting selectors before alias normalization can erase evidence."""
    if isinstance(context, dict):
        for old, canonical in (
            ("artifact_version_id", "build_record_id"),
            ("artifact_kind", "build_family"),
            ("artifact_key", "build_key"),
        ):
            if old in context and canonical in context and context[old] != context[canonical]:
                raise ValueError(f"conflicting_context_selectors:{canonical}")
    return normalize_context(context)


def normalize_selected_paths(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    selected: list[str] = []
    for item in value:
        safe = safe_relpath(item)
        if safe and safe not in selected:
            selected.append(safe)
    return selected

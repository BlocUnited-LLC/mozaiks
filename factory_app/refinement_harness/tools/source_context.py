from __future__ import annotations

from typing import Any

from mozaiksai.control_plane.app_context import get_current_source_context_bundle
from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.core.app_context import (
    SourceCorpusBundle,
    build_source_corpus_catalog,
    get_related_source_files,
    read_source_file_from_bundle,
    search_source_corpus,
)
from mozaiksai.core.artifacts.store import ArtifactStore

from ._context_graph import load_context_graph_for_tool
from ._shared import normalize_context


async def search_app_source_context(
    *,
    query: str | None = None,
    max_results: int = 12,
    context: ControlPlaneToolContext | dict[str, Any] | None = None,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Search the current AppContext source corpus for refinement planning."""
    tool_context = normalize_context(context)
    bundle_result = await _load_source_bundle(tool_context=tool_context, artifact_store=artifact_store)
    if not bundle_result.get("present"):
        return {**bundle_result, "results": []}

    resolved_query = str(query or tool_context.raw_user_request or "").strip()
    if not resolved_query:
        return {**bundle_result, "present": False, "reason": "missing_query", "results": []}

    bundle = bundle_result["bundle"]
    results = search_source_corpus(
        bundle,
        resolved_query,
        max_results=_bounded_int(max_results, default=12, minimum=1, maximum=40),
    )
    return {
        "present": True,
        "query": resolved_query,
        "source": bundle_result["source"],
        "source_context_catalog": build_source_corpus_catalog(bundle, max_files=40, max_chunks=8),
        "results": results,
        "warnings": list(bundle_result.get("warnings") or []),
    }


async def read_app_source_file(
    *,
    path: str | None = None,
    max_chars: int = 24_000,
    context: ControlPlaneToolContext | dict[str, Any] | None = None,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Read an exact indexed source file from the current AppContext corpus."""
    tool_context = normalize_context(context)
    resolved_path = str(path or tool_context.extra.get("source_path") or tool_context.extra.get("path") or "").strip()
    if not resolved_path:
        return {"present": False, "reason": "missing_path", "path": ""}

    bundle_result = await _load_source_bundle(tool_context=tool_context, artifact_store=artifact_store)
    if not bundle_result.get("present"):
        return {**bundle_result, "path": resolved_path}

    payload = read_source_file_from_bundle(
        bundle_result["bundle"],
        resolved_path,
        max_chars=_bounded_int(max_chars, default=24_000, minimum=1_000, maximum=80_000),
    )
    payload.update(
        {
            "source": bundle_result["source"],
            "warnings": list(bundle_result.get("warnings") or []),
        }
    )
    return payload


async def get_related_app_source_files(
    *,
    path: str | None = None,
    max_results: int = 16,
    context: ControlPlaneToolContext | dict[str, Any] | None = None,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Return files related to an indexed source path for refinement context."""
    tool_context = normalize_context(context)
    resolved_path = str(path or tool_context.extra.get("source_path") or tool_context.extra.get("path") or "").strip()
    if not resolved_path:
        return {"present": False, "reason": "missing_path", "path": "", "files": []}

    bundle_result = await _load_source_bundle(tool_context=tool_context, artifact_store=artifact_store)
    if not bundle_result.get("present"):
        return {**bundle_result, "path": resolved_path, "files": []}

    return {
        "present": True,
        "path": resolved_path,
        "source": bundle_result["source"],
        "files": get_related_source_files(
            bundle_result["bundle"],
            resolved_path,
            max_results=_bounded_int(max_results, default=16, minimum=1, maximum=60),
        ),
        "warnings": list(bundle_result.get("warnings") or []),
    }


async def _load_source_bundle(
    *,
    tool_context: ControlPlaneToolContext,
    artifact_store: ArtifactStore | None,
) -> dict[str, Any]:
    app_id = str(tool_context.app_id or "").strip()
    if not app_id:
        return {"present": False, "reason": "missing_app_id", "warnings": []}

    current = await get_current_source_context_bundle(
        app_id=app_id,
        artifact_store=artifact_store,
    )
    if current.bundle is not None:
        return {
            "present": True,
            "source": "current_app_context",
            "bundle": current.bundle,
            "warnings": list(current.warnings),
        }

    if str(tool_context.build_record_id or "").strip():
        loaded = await load_context_graph_for_tool(context=tool_context, artifact_store=artifact_store)
        if loaded.get("present") and isinstance(loaded.get("source_context_bundle"), SourceCorpusBundle):
            return {
                "present": True,
                "source": loaded["workspace"].get("source") or "artifact_workspace",
                "bundle": loaded["source_context_bundle"],
                "warnings": [*list(current.warnings), *list(loaded.get("warnings") or [])],
            }

    return {
        "present": False,
        "reason": "source_context_bundle_unavailable",
        "warnings": list(current.warnings),
    }


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


__all__ = [
    "get_related_app_source_files",
    "read_app_source_file",
    "search_app_source_context",
]

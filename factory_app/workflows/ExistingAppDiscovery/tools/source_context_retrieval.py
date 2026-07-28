"""Workflow tools for retrieving bounded existing-app source context."""

from __future__ import annotations

from typing import Any

from mozaiksai.core.app_context.intelligence import (
    build_app_intelligence_catalog,
    search_app_intelligence_snapshot,
)
from mozaiksai.core.app_context.source_corpus import (
    get_related_source_files,
    read_source_file_from_bundle,
    search_source_corpus,
)


async def search_repo_source_context(
    query: str,
    context_variables: Any | None = None,
    max_results: int = 12,
) -> dict[str, Any]:
    """Search the preloaded source corpus for code chunks relevant to a query."""
    bundle = _source_context_bundle(context_variables)
    if not bundle:
        return {"present": False, "reason": "source_context_bundle_unavailable", "results": []}
    bounded_results = max(1, min(int(max_results or 12), 24))
    return {
        "present": True,
        "query": str(query or ""),
        "results": search_source_corpus(bundle, str(query or ""), max_results=bounded_results),
    }


async def get_repo_app_intelligence(
    query: str | None = None,
    context_variables: Any | None = None,
    max_results: int = 12,
) -> dict[str, Any]:
    """Return compact app intelligence for the preloaded repo context."""
    snapshot = _app_intelligence_snapshot(context_variables)
    if not snapshot:
        return {"present": False, "reason": "app_intelligence_snapshot_unavailable", "results": []}
    catalog = build_app_intelligence_catalog(snapshot)
    resolved_query = str(query or "").strip()
    results = (
        search_app_intelligence_snapshot(
            snapshot,
            resolved_query,
            max_results=max(1, min(int(max_results or 12), 32)),
        )
        if resolved_query
        else []
    )
    return {
        "present": True,
        "query": resolved_query or None,
        "catalog": catalog,
        "results": results,
    }


async def read_repo_source_file(
    path: str,
    context_variables: Any | None = None,
    max_chars: int = 24_000,
) -> dict[str, Any]:
    """Read one indexed source file from the preloaded source corpus."""
    bundle = _source_context_bundle(context_variables)
    if not bundle:
        return {"present": False, "path": str(path or ""), "reason": "source_context_bundle_unavailable"}
    bounded_chars = max(2_000, min(int(max_chars or 24_000), 80_000))
    return read_source_file_from_bundle(bundle, str(path or ""), max_chars=bounded_chars)


async def get_related_repo_source_files(
    path: str,
    context_variables: Any | None = None,
    max_results: int = 16,
) -> dict[str, Any]:
    """Return files related by relative imports or same-directory proximity."""
    bundle = _source_context_bundle(context_variables)
    if not bundle:
        return {"present": False, "path": str(path or ""), "reason": "source_context_bundle_unavailable", "files": []}
    bounded_results = max(1, min(int(max_results or 16), 32))
    return {
        "present": True,
        "path": str(path or ""),
        "files": get_related_source_files(bundle, str(path or ""), max_results=bounded_results),
    }


def _source_context_bundle(context_variables: Any | None) -> dict[str, Any] | None:
    value = _ctx_get(context_variables, "source_context_bundle")
    return value if isinstance(value, dict) else None


def _app_intelligence_snapshot(context_variables: Any | None) -> dict[str, Any] | None:
    value = _ctx_get(context_variables, "app_intelligence_snapshot")
    return value if isinstance(value, dict) else None


def _ctx_get(context_variables: Any | None, key: str) -> Any:
    if context_variables is None:
        return None
    if isinstance(context_variables, dict):
        return context_variables.get(key)
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key)
    getter = getattr(context_variables, "get", None)
    if callable(getter):
        try:
            return getter(key)
        except Exception:
            return None
    return None


__all__ = [
    "get_repo_app_intelligence",
    "get_related_repo_source_files",
    "read_repo_source_file",
    "search_repo_source_context",
]

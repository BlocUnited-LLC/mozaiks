from __future__ import annotations

from typing import Any


def build_context_graph_prompt_pack(
    *,
    catalog: dict[str, Any],
    source: str,
    status: str = "loaded",
    reason: str | None = None,
    warnings: list[str] | None = None,
    max_paths: int = 12,
    max_nodes: int = 12,
    max_edges: int = 16,
    max_semantic_targets: int = 8,
) -> dict[str, Any]:
    """Build the compact workflow prompt pack from a richer graph catalog."""
    selected_paths = (
        catalog.get("selected_file_paths")
        or catalog.get("candidate_files")
        or catalog.get("file_tree")
        or []
    )
    semantic_targets = (
        catalog.get("semantic_annotation_candidates")
        or (catalog.get("semantic_annotation_request") or {}).get("items")
        or []
    )
    health = catalog.get("scan_health") if isinstance(catalog.get("scan_health"), dict) else {}
    return {
        "pack_kind": "context_graph_prompt_pack",
        "present": bool(catalog.get("present", True)),
        "status": status,
        "reason": reason,
        "source": source,
        "graph_id": catalog.get("graph_id"),
        "app_id": catalog.get("app_id"),
        "stale_status": catalog.get("stale_status"),
        "graph_hash": catalog.get("graph_hash"),
        "summary": {
            "node_count": catalog.get("node_count"),
            "edge_count": catalog.get("edge_count"),
            "file_count": catalog.get("file_count"),
            "request_keywords": list(catalog.get("request_keywords") or []),
            "scan": _scan_summary(health),  # type: ignore[arg-type]
        },
        "relevant_paths": _path_hints(selected_paths, limit=max_paths),
        "matched_nodes": list(catalog.get("matched_nodes") or catalog.get("related_nodes") or [])[:max_nodes],
        "matched_edges": list(catalog.get("matched_edges") or catalog.get("related_edges") or [])[:max_edges],
        "semantic_annotation_targets": list(semantic_targets)[:max_semantic_targets],
        "warnings": _dedupe([*(warnings or []), *(catalog.get("warnings") or [])]),
    }


def build_context_graph_unavailable_pack(
    *,
    reason: str,
    warnings: list[str] | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Build a prompt-visible status pack when graph context is unavailable."""
    return {
        "pack_kind": "context_graph_prompt_pack",
        "present": False,
        "status": "unavailable",
        "reason": reason,
        "source": source,
        "warnings": list(warnings or []),
    }


def _path_hints(values: Any, *, limit: int) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for item in list(values or [])[:limit]:
        if isinstance(item, dict):
            path = item.get("path") or item.get("label") or item.get("node_id")
            if path:
                hints.append(
                    {
                        "path": path,
                        "score": item.get("score"),
                        "matched_terms": list(item.get("matched_terms") or []),
                        "node_id": item.get("node_id"),
                        "path_priority": item.get("path_priority"),
                    }
                )
        elif item:
            hints.append({"path": str(item)})
    return hints


def _dedupe(values: list[Any]) -> list[Any]:
    out: list[Any] = []
    for value in values:
        if value not in out:
            out.append(value)
    return out


def _scan_summary(health: dict[str, Any]) -> dict[str, Any] | None:
    if not health:
        return None
    parser_status = health.get("parser_status") if isinstance(health.get("parser_status"), dict) else {}
    languages = parser_status.get("languages") if isinstance(parser_status.get("languages"), dict) else {}  # type: ignore[union-attr]
    return {
        "policy_id": health.get("policy_id"),
        "selected_file_count": health.get("selected_file_count"),
        "candidate_file_count": health.get("candidate_file_count"),
        "limit_reached": health.get("limit_reached"),
        "selected_by_priority": health.get("selected_by_priority"),
        "parser_active": {
            key: value.get("active_parser")
            for key, value in languages.items()  # type: ignore[union-attr]
            if isinstance(value, dict) and value.get("active_parser")
        },
    }

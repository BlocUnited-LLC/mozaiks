"""Query helpers for Context Graph control-plane tools."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from mozaiksai.core.app_context.context_graph import build_semantic_annotation_request
from mozaiksai.core.app_context.models import AppContextGraph, GraphEdgeType, GraphNodeType
from mozaiksai.core.app_context.scan_policy import priority_for_source_path

_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "your",
    "when",
    "then",
    "just",
    "have",
    "will",
    "want",
    "needs",
    "need",
    "make",
    "change",
    "update",
    "add",
    "fix",
    "file",
    "files",
    "page",
    "component",
}


def build_context_graph_catalog(
    *,
    graph: AppContextGraph,
    raw_user_request: str = "",
    file_map: dict[str, str] | None = None,
    max_nodes: int = 20,
    max_edges: int = 30,
) -> dict[str, Any]:
    """Return a compact graph-aware catalog for scope selection."""
    keywords = _keywords(raw_user_request)
    ranked_nodes = _rank_nodes(graph=graph, keywords=keywords, file_map=file_map or {})
    node_counts = Counter(node.node_type.value for node in graph.nodes)
    edge_counts = Counter(edge.edge_type.value for edge in graph.edges)
    file_paths = sorted(
        str(node.metadata.get("path") or "")
        for node in graph.nodes
        if node.node_type == GraphNodeType.FILE and node.metadata.get("path")
    )
    candidate_files = [
        {
            "path": str(item["metadata"].get("path") or item["label"] or ""),
            "score": item["score"],
            "matched_terms": item["matched_terms"],
            "node_id": item["node_id"],
            "path_priority": item.get("path_priority"),
        }
        for item in ranked_nodes
        if item["node_type"] == GraphNodeType.FILE.value and (item["metadata"].get("path") or item["label"])
    ][:12]

    return {
        "present": True,
        "graph_id": graph.graph_id,
        "app_id": graph.app_id,
        "stale_status": graph.stale_status.value,
        "graph_hash": graph.graph_hash,
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "node_counts": dict(sorted(node_counts.items())),
        "edge_counts": dict(sorted(edge_counts.items())),
        "file_count": len(file_paths),
        "file_tree": file_paths[:160],
        "request_keywords": keywords,
        "candidate_files": candidate_files,
        "matched_nodes": ranked_nodes[:max_nodes],
        "matched_edges": _related_edge_hints(graph=graph, node_ids=[item["node_id"] for item in ranked_nodes], max_edges=max_edges),
        "semantic_annotation_candidates": build_semantic_annotation_request(
            graph=graph,
            selected_node_ids=[item["node_id"] for item in ranked_nodes],
            max_items=12,
        )["items"],
    }


def build_context_graph_scope(
    *,
    graph: AppContextGraph,
    selected_paths: list[str],
    file_map: dict[str, str] | None = None,
    raw_user_request: str = "",
    max_related_nodes: int = 40,
    max_related_files: int = 12,
) -> dict[str, Any]:
    """Return selected files, symbols, and relationship context for coding."""
    normalized_paths = _dedupe([_normalize_path(path) for path in selected_paths if _normalize_path(path)])
    by_path = {
        str(node.metadata.get("path")): node
        for node in graph.nodes
        if node.node_type == GraphNodeType.FILE and node.metadata.get("path")
    }
    selected_node_ids = [by_path[path].node_id for path in normalized_paths if path in by_path]
    related_node_ids = set(selected_node_ids)
    related_file_paths: list[str] = []

    for edge in graph.edges:
        if edge.source_node_id in selected_node_ids or edge.target_node_id in selected_node_ids:
            related_node_ids.add(edge.source_node_id)
            related_node_ids.add(edge.target_node_id)

    nodes_by_id = {node.node_id: node for node in graph.nodes}
    for node_id in sorted(related_node_ids):
        node = nodes_by_id.get(node_id)
        path = str((node.metadata or {}).get("path") or "")  # type: ignore[union-attr]
        if node and node.node_type == GraphNodeType.FILE and path and path not in related_file_paths:
            related_file_paths.append(path)

    file_map = file_map or {}
    selected_scope = {
        path: {
            "present": path in file_map,
            "node_id": by_path[path].node_id if path in by_path else None,
            "excerpt": _excerpt(file_map.get(path), max_length=900),
            "symbols": _symbols_for_file(graph=graph, file_node_id=by_path[path].node_id if path in by_path else None),
        }
        for path in normalized_paths
    }

    return {
        "present": True,
        "graph_id": graph.graph_id,
        "app_id": graph.app_id,
        "stale_status": graph.stale_status.value,
        "selected_file_paths": normalized_paths,
        "selected_scope": selected_scope,
        "related_file_paths": related_file_paths[:max_related_files],
        "related_nodes": [_node_hint(nodes_by_id[node_id]) for node_id in list(related_node_ids)[:max_related_nodes] if node_id in nodes_by_id],
        "related_edges": _related_edge_hints(graph=graph, node_ids=list(related_node_ids), max_edges=60),
        "semantic_annotation_request": build_semantic_annotation_request(
            graph=graph,
            selected_node_ids=list(related_node_ids),
            max_items=20,
        ),
        "request_keywords": _keywords(raw_user_request),
    }


def _rank_nodes(*, graph: AppContextGraph, keywords: list[str], file_map: dict[str, str]) -> list[dict[str, Any]]:
    if not keywords:
        return []
    ranked: list[tuple[int, int, str, dict[str, Any]]] = []
    wants_tests = bool({"test", "tests", "pytest", "spec"} & set(keywords))
    wants_docs = bool({"doc", "docs", "readme", "documentation"} & set(keywords))
    for node in graph.nodes:
        search_text = _node_search_text(node, file_map=file_map)
        matched = [term for term in keywords if term in search_text]
        if not matched:
            continue
        path = str(node.metadata.get("path") or "")
        score = len(matched) * 10
        if path and any(term in path.lower() for term in matched):
            score += 30
        if node.node_type == GraphNodeType.FILE:
            score += 8
        score += _contract_role_boost(node)
        path_priority, path_priority_label = priority_for_source_path(path) if path else (50, "unknown")
        score += _path_priority_score(path_priority, path_priority_label, wants_tests=wants_tests, wants_docs=wants_docs)
        ranked.append(
            (
                score,
                path_priority,
                node.node_id,
                {
                    **_node_hint(node),
                    "score": score,
                    "matched_terms": matched,
                    "path_priority": path_priority_label,
                },
            )
        )
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [entry for _, _, _, entry in ranked]


def _contract_role_boost(node: Any) -> int:
    role = str((node.metadata or {}).get("contract_role") or "")
    if role == "module_action_handler":
        return 45
    if role in {"module_service_symbol", "module_repo_symbol"}:
        return 35
    if role in {"page_component", "ui_component"}:
        return 25
    if role in {"module_contract", "module_action", "workflow_agent", "workflow_tool"}:
        return 20
    return 0


def _path_priority_score(priority: int, label: str, *, wants_tests: bool, wants_docs: bool) -> int:
    if label == "tests" and not wants_tests:
        return -35
    if label == "docs" and not wants_docs:
        return -25
    if label == "scripts":
        return -20
    if priority <= 12:
        return 40 - priority
    if priority <= 20:
        return 18
    if priority < 50:
        return 8
    return 0


def _node_search_text(node: Any, *, file_map: dict[str, str]) -> str:
    metadata = node.metadata or {}
    path = str(metadata.get("path") or "")
    content = file_map.get(path, "")
    return " ".join(
        [
            str(node.node_id),
            str(node.label or ""),
            node.node_type.value,
            " ".join(str(value) for value in metadata.values() if isinstance(value, (str, int, float))),
            content[:4000],
        ]
    ).lower()


def _related_edge_hints(*, graph: AppContextGraph, node_ids: list[str], max_edges: int) -> list[dict[str, Any]]:
    wanted = set(node_ids)
    edges = [
        edge
        for edge in graph.edges
        if edge.source_node_id in wanted or edge.target_node_id in wanted
    ]
    priority = {
        GraphEdgeType.IMPORTS.value: 0,
        GraphEdgeType.CALLS.value: 1,
        GraphEdgeType.DEFINES.value: 2,
        GraphEdgeType.DECLARES.value: 3,
        GraphEdgeType.CONTAINS.value: 4,
    }
    edges.sort(key=lambda edge: (priority.get(edge.edge_type.value, 50), edge.source_node_id, edge.target_node_id))
    return [
        {
            "edge_id": edge.edge_id,
            "edge_type": edge.edge_type.value,
            "source_node_id": edge.source_node_id,
            "target_node_id": edge.target_node_id,
            "metadata": dict(edge.metadata or {}),
        }
        for edge in edges[:max_edges]
    ]


def _symbols_for_file(*, graph: AppContextGraph, file_node_id: str | None) -> list[dict[str, Any]]:
    if not file_node_id:
        return []
    symbol_ids = [
        edge.target_node_id
        for edge in graph.edges
        if edge.source_node_id == file_node_id and edge.edge_type == GraphEdgeType.DEFINES
    ]
    nodes_by_id = {node.node_id: node for node in graph.nodes}
    return [_node_hint(nodes_by_id[node_id]) for node_id in symbol_ids if node_id in nodes_by_id]


def _node_hint(node: Any) -> dict[str, Any]:
    return {
        "node_id": node.node_id,
        "node_type": node.node_type.value,
        "label": node.label,
        "artifact_version_id": node.artifact_version_id,
        "metadata": dict(node.metadata or {}),
    }


def _keywords(raw_request: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", str(raw_request or "").lower())
    keywords: list[str] = []
    for token in tokens:
        if token in _STOPWORDS:
            continue
        if token not in keywords:
            keywords.append(token)
    return keywords[:16]


def _normalize_path(path: str) -> str | None:
    raw = str(path or "").replace("\\", "/").strip().strip("/")
    return raw or None


def _dedupe(values: list[str | None]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out


def _excerpt(content: str | None, *, max_length: int) -> str | None:
    if content is None:
        return None
    text = str(content)
    if len(text) <= max_length:
        return text
    return text[: max_length - 20].rstrip() + "\n... [truncated]"

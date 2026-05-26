"""Advisory AppContextGraph impact hints for refinement planning."""

from __future__ import annotations

import re
from collections import deque
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.control_plane.app_context import AppContextSummary
from mozaiksai.core.app_context.models import (
    AppContextGraph,
    AppContextGraphEdge,
    AppContextGraphNode,
    AppContextStaleStatus,
    GraphNodeType,
)

APP_CONTEXT_GRAPH_MISSING_WARNING = (
    "No AppContextGraph was available; impact hints use existing route and artifact evidence only."
)
APP_CONTEXT_GRAPH_STALE_WARNING = (
    "Current AppContextGraph is stale/unknown; graph impact path expansion is disabled."
)

_STALE_STATUSES = {
    AppContextStaleStatus.STALE.value,
    AppContextStaleStatus.PARTIALLY_STALE.value,
    AppContextStaleStatus.UNSAFE.value,
    AppContextStaleStatus.UNKNOWN.value,
}
_FRESH_STATUSES = {AppContextStaleStatus.CURRENT.value, "fresh"}
_SECRET_PATH_TERMS = (
    ".env",
    "credential",
    "credentials",
    "password",
    "private",
    "secret",
    "secrets",
    "token",
    "vault",
)
_PATH_METADATA_KEYS = (
    "path",
    "paths",
    "file_path",
    "file_paths",
    "bundle_path",
    "bundle_paths",
    "source_path",
    "source_paths",
    "location",
)
_GENERIC_QUERY_TOKENS = {
    "action",
    "change",
    "field",
    "layout",
    "module",
    "route",
    "service",
    "sync",
    "update",
}


class AppContextImpactNodeHint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    node_type: str
    label: str | None = None
    path_hints: list[str] = Field(default_factory=list)
    ownership: str | None = None


class AppContextImpactEdgeHint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edge_id: str | None = None
    edge_type: str
    source_node_id: str
    target_node_id: str


class AppContextImpactHints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool = False
    graph_snapshot_ref: str | None = None
    stale_status: str | None = None
    related_nodes: list[AppContextImpactNodeHint] = Field(default_factory=list)
    related_edges: list[AppContextImpactEdgeHint] = Field(default_factory=list)
    additional_path_hints: list[str] = Field(default_factory=list)
    ownership_warnings: list[str] = Field(default_factory=list)
    risk_warnings: list[str] = Field(default_factory=list)
    stale_graph_warning: str | None = None
    explanations: list[str] = Field(default_factory=list)


def derive_app_context_impact_hints(
    *,
    app_context_graph: AppContextGraph | dict[str, Any] | None,
    app_context_summary: AppContextSummary | dict[str, Any] | None = None,
    request: str,
    affected_bundle_paths: list[str],
    change_class: str | None = None,
    refinement_lane: str | None = None,
) -> AppContextImpactHints:
    """Derive non-authoritative graph hints without mutating routing inputs."""
    graph = _normalize_graph(app_context_graph)
    summary = _normalize_summary(app_context_summary)
    if graph is None:
        return AppContextImpactHints(
            available=False,
            explanations=[APP_CONTEXT_GRAPH_MISSING_WARNING],
        )

    stale_status = _combined_stale_status(graph=graph, summary=summary)
    graph_snapshot_ref = _graph_snapshot_ref(graph=graph, summary=summary)
    if stale_status not in _FRESH_STATUSES:
        return AppContextImpactHints(
            available=True,
            graph_snapshot_ref=graph_snapshot_ref,
            stale_status=stale_status,
            stale_graph_warning=APP_CONTEXT_GRAPH_STALE_WARNING,
            explanations=[
                "AppContextGraph was available, but stale or unknown graph state prevents "
                "path expansion."
            ],
        )

    paths = [str(path or "") for path in affected_bundle_paths or []]
    seed_node_ids = _seed_node_ids(
        graph=graph,
        request=request,
        affected_bundle_paths=paths,
        change_class=change_class,
        refinement_lane=refinement_lane,
    )
    related_node_ids, related_edge_ids = _expand_related_graph(graph, seed_node_ids)
    node_by_id = {node.node_id: node for node in graph.nodes}
    edge_by_id = {_edge_key(edge): edge for edge in graph.edges}

    related_nodes = [
        _node_hint(node_by_id[node_id])
        for node_id in sorted(related_node_ids)
        if node_id in node_by_id
    ]
    related_edges = [
        _edge_hint(edge_by_id[edge_id])
        for edge_id in sorted(related_edge_ids)
        if edge_id in edge_by_id
    ]
    existing_paths = {_normalize_path(path).lower() for path in paths}
    additional_path_hints = _dedupe(
        [
            hint
            for node_id in related_node_ids
            if node_id in node_by_id
            for hint in _node_path_hints(node_by_id[node_id])
            if hint.lower() not in existing_paths
        ]
    )
    ownership_warnings = _ownership_warnings(
        node_by_id[node_id] for node_id in related_node_ids if node_id in node_by_id
    )
    risk_warnings = _risk_warnings(
        node_by_id[node_id] for node_id in related_node_ids if node_id in node_by_id
    )
    explanation = (
        f"AppContextGraph matched {len(related_nodes)} related nodes and "
        f"{len(related_edges)} related edges."
        if seed_node_ids
        else "AppContextGraph was current, but no related graph nodes matched the request."
    )

    return AppContextImpactHints(
        available=True,
        graph_snapshot_ref=graph_snapshot_ref,
        stale_status=stale_status,
        related_nodes=related_nodes,
        related_edges=related_edges,
        additional_path_hints=additional_path_hints,
        ownership_warnings=ownership_warnings,
        risk_warnings=risk_warnings,
        explanations=[explanation],
    )


def _normalize_graph(value: AppContextGraph | dict[str, Any] | None) -> AppContextGraph | None:
    if value is None:
        return None
    if isinstance(value, AppContextGraph):
        return value
    return AppContextGraph.model_validate(value)


def _normalize_summary(value: AppContextSummary | dict[str, Any] | None) -> AppContextSummary | None:
    if value is None:
        return None
    if isinstance(value, AppContextSummary):
        return value
    return AppContextSummary.model_validate(value)


def _combined_stale_status(*, graph: AppContextGraph, summary: AppContextSummary | None) -> str:
    graph_status = _status_value(graph.stale_status) or AppContextStaleStatus.UNKNOWN.value
    if summary is None:
        return graph_status
    if not summary.available:
        return AppContextStaleStatus.UNKNOWN.value
    summary_status = _status_value(summary.stale_status)
    if summary_status in _STALE_STATUSES:
        return summary_status
    return graph_status


def _status_value(value: Any) -> str | None:
    raw = getattr(value, "value", value)
    normalized = str(raw or "").strip().lower()
    return normalized or None


def _graph_snapshot_ref(*, graph: AppContextGraph, summary: AppContextSummary | None) -> str | None:
    if summary is not None:
        for ref in summary.artifact_refs:
            if ref.kind == "app_context_graph":
                return ref.ref_id
    return graph.graph_id


def _seed_node_ids(
    *,
    graph: AppContextGraph,
    request: str,
    affected_bundle_paths: list[str],
    change_class: str | None,
    refinement_lane: str | None,
) -> set[str]:
    query = " ".join(
        [
            request,
            change_class or "",
            refinement_lane or "",
            " ".join(affected_bundle_paths),
        ]
    )
    query_tokens = _tokens(query)
    path_set = {_normalize_path(path).lower() for path in affected_bundle_paths if path}
    seeds: set[str] = set()
    for node in graph.nodes:
        node_paths = _node_path_hints(node)
        node_path_set = {path.lower() for path in node_paths}
        if node_path_set.intersection(path_set):
            seeds.add(node.node_id)
            continue
        if query_tokens and _node_matches_query(node=node, query_tokens=query_tokens):
            seeds.add(node.node_id)
    return seeds


def _node_matches_query(*, node: AppContextGraphNode, query_tokens: set[str]) -> bool:
    node_text = _node_search_text(node)
    if not node_text:
        return False
    meaningful_query_tokens = query_tokens - _GENERIC_QUERY_TOKENS
    node_tokens = _tokens(node_text)
    if meaningful_query_tokens.intersection(node_tokens):
        return True
    return any(
        len(token) >= 4 and token in node_text
        for token in meaningful_query_tokens
    )


def _node_search_text(node: AppContextGraphNode) -> str:
    metadata_values = " ".join(_flatten_metadata_values(node.metadata))
    return " ".join(
        [
            node.node_id,
            node.node_type.value,
            node.label or "",
            metadata_values,
            " ".join(_node_path_hints(node)),
        ]
    ).lower()


def _flatten_metadata_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [item for subvalue in value.values() for item in _flatten_metadata_values(subvalue)]
    if isinstance(value, list):
        return [item for subvalue in value for item in _flatten_metadata_values(subvalue)]
    if value is None:
        return []
    return [str(value)]


def _tokens(text: str) -> set[str]:
    raw_tokens = re.findall(r"[a-z0-9_]+", str(text or "").lower())
    tokens: set[str] = set()
    for token in raw_tokens:
        if len(token) < 3:
            continue
        tokens.add(token)
        tokens.update(part for part in re.split(r"[_-]+", token) if len(part) >= 3)
        if token.endswith("s") and len(token) > 3:
            tokens.add(token[:-1])
    return tokens


def _expand_related_graph(
    graph: AppContextGraph,
    seed_node_ids: set[str],
    *,
    max_depth: int = 2,
) -> tuple[set[str], set[str]]:
    if not seed_node_ids:
        return set(), set()

    adjacency: dict[str, list[tuple[str, str]]] = {}
    for edge in graph.edges:
        key = _edge_key(edge)
        adjacency.setdefault(edge.source_node_id, []).append((edge.target_node_id, key))
        adjacency.setdefault(edge.target_node_id, []).append((edge.source_node_id, key))

    related_nodes: set[str] = set(seed_node_ids)
    related_edges: set[str] = set()
    queue: deque[tuple[str, int]] = deque((node_id, 0) for node_id in sorted(seed_node_ids))
    seen_depth: dict[str, int] = {node_id: 0 for node_id in seed_node_ids}
    while queue:
        node_id, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for neighbor_id, edge_id in adjacency.get(node_id, []):
            related_edges.add(edge_id)
            related_nodes.add(neighbor_id)
            next_depth = depth + 1
            if next_depth < seen_depth.get(neighbor_id, max_depth + 1):
                seen_depth[neighbor_id] = next_depth
                queue.append((neighbor_id, next_depth))
    return related_nodes, related_edges


def _edge_key(edge: AppContextGraphEdge) -> str:
    return edge.edge_id or f"{edge.edge_type.value}:{edge.source_node_id}:{edge.target_node_id}"


def _node_hint(node: AppContextGraphNode) -> AppContextImpactNodeHint:
    return AppContextImpactNodeHint(
        node_id=node.node_id,
        node_type=node.node_type.value,
        label=node.label,
        path_hints=_node_path_hints(node),
        ownership=_ownership_value(node),
    )


def _edge_hint(edge: AppContextGraphEdge) -> AppContextImpactEdgeHint:
    return AppContextImpactEdgeHint(
        edge_id=edge.edge_id,
        edge_type=edge.edge_type.value,
        source_node_id=edge.source_node_id,
        target_node_id=edge.target_node_id,
    )


def _node_path_hints(node: AppContextGraphNode) -> list[str]:
    candidates: list[str] = []
    if node.node_type is GraphNodeType.FILE and node.label:
        candidates.append(node.label)
    for key in _PATH_METADATA_KEYS:
        candidates.extend(_as_path_candidates(node.metadata.get(key)))
    return _dedupe([path for path in (_safe_relative_path(path) for path in candidates) if path])


def _as_path_candidates(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for subvalue in value for item in _as_path_candidates(subvalue)]
    return []


def _safe_relative_path(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if any(char.isspace() for char in raw):
        return None
    if re.match(r"^[A-Za-z]:[\\/]", raw) or raw.startswith(("\\\\", "//", "~")):
        return None
    normalized = raw.replace("\\", "/").lstrip()
    if normalized.startswith("/"):
        return None
    parts = PurePosixPath(normalized).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    cleaned = "/".join(parts)
    lower = cleaned.lower()
    if any(term in lower for term in _SECRET_PATH_TERMS):
        return None
    return cleaned


def _ownership_warnings(nodes: Any) -> list[str]:
    warnings: list[str] = []
    for node in nodes:
        ownership = _ownership_value(node)
        if ownership != "read_only_discovered":
            continue
        label = node.label or node.node_id
        warnings.append(
            f"Graph node '{label}' is read_only_discovered; changes require ownership review."
        )
    return _dedupe(warnings)


def _ownership_value(node: AppContextGraphNode) -> str | None:
    for key in ("ownership", "ownership_class"):
        value = node.metadata.get(key)
        if value:
            raw = getattr(value, "value", value)
            return str(raw).strip().lower() or None
    return None


def _risk_warnings(nodes: Any) -> list[str]:
    warnings: list[str] = []
    for node in nodes:
        if node.node_type is GraphNodeType.RISK:
            warnings.append(node.label or f"Risk node '{node.node_id}' is related to this request.")
        for key in ("risk", "risk_warning", "warning"):
            value = node.metadata.get(key)
            if isinstance(value, str) and value.strip():
                warnings.append(value.strip())
    return _dedupe(warnings)


def _normalize_path(path: str) -> str:
    safe = _safe_relative_path(path)
    if safe is not None:
        return safe
    return str(path or "").replace("\\", "/").strip().lstrip("/")


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


__all__ = [
    "APP_CONTEXT_GRAPH_MISSING_WARNING",
    "APP_CONTEXT_GRAPH_STALE_WARNING",
    "AppContextImpactEdgeHint",
    "AppContextImpactHints",
    "AppContextImpactNodeHint",
    "derive_app_context_impact_hints",
]

"""Typed storage-neutral impact analysis over AppContextGraph snapshots."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from typing import Any

from pydantic import BaseModel

from .models import (
    AppContextGraph,
    AppContextGraphEdge,
    AppContextGraphNode,
    GraphEdgeType,
    GraphNodeType,
    GraphSnapshotIdentity,
    ImpactDirection,
    ImpactEvidence,
    ImpactQuery,
    ImpactReport,
    ParserAuthority,
)
from .scan_policy import safe_scan_relpath

GRAPH_SCHEMA_VERSION = "mozaiks.app_context.graph.v1"
PARSER_MANIFEST_VERSION = "mozaiks.context_graph.parser_manifest.v1"
_TIMESTAMP_KEYS = {"indexed_at", "generated_at", "annotated_at", "created_at", "updated_at"}


def build_graph_snapshot_identity(
    graph: AppContextGraph,
    *,
    repository_scope: str | None = None,
    application_scope: str | None = None,
    baseline_commit_sha: str | None = None,
    parser_manifest: dict[str, Any] | None = None,
    scan_health: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
) -> GraphSnapshotIdentity:
    """Return credential-free immutable identity for a graph snapshot."""
    parser_payload = _without_timestamps(parser_manifest or {})
    source_payload = [
        _without_timestamps(ref.model_dump(mode="json"))
        for ref in sorted(graph.source_refs, key=lambda item: item.source_ref_id)
    ]
    content_payload = _graph_content_payload(graph)
    scan_payload = _without_timestamps(scan_health or {})
    provenance_payload = _without_timestamps(provenance or {})
    identity_payload = {
        "repository_scope": repository_scope,
        "application_scope": application_scope or graph.app_id,
        "baseline_commit_sha": baseline_commit_sha,
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
        "parser_manifest_version": PARSER_MANIFEST_VERSION,
        "parser_manifest_digest": _digest(parser_payload),
        "source_manifest_digest": _digest(source_payload),
        "graph_content_digest": _digest(content_payload),
        "scan_health": scan_payload,
        "provenance": provenance_payload,
    }
    return GraphSnapshotIdentity(
        **identity_payload,
        identity_digest=_digest(identity_payload),
    )


def impact_query_digest(query: ImpactQuery) -> str:
    """Return the stable digest for an impact query."""
    return _digest(query.model_dump(mode="json"))


def analyze_impact(
    *,
    graph: AppContextGraph,
    snapshot_identity: GraphSnapshotIdentity,
    query: ImpactQuery,
) -> ImpactReport:
    """Run a deterministic bounded graph traversal without mutating the graph."""
    if query.snapshot_digest != snapshot_identity.identity_digest:
        raise ValueError("ImpactQuery snapshot_digest does not match GraphSnapshotIdentity identity_digest")
    nodes_by_id = {node.node_id: node for node in graph.nodes}
    missing = sorted(seed for seed in query.seed_node_ids if seed not in nodes_by_id)
    if missing:
        raise ValueError(f"ImpactQuery seed_node_ids are not present in graph: {missing}")
    if _requires_symbol_authority(query=query, nodes_by_id=nodes_by_id):
        insufficient = _symbol_authority_failures(query=query, graph=graph, nodes_by_id=nodes_by_id)
        if insufficient:
            raise ValueError(
                "Symbol-level impact query requires authoritative parser evidence: "
                + "; ".join(insufficient)
            )

    allowed_edges = set(query.allowed_edge_types)
    allowed_nodes = set(query.allowed_node_kinds)
    scoped_nodes = _nodes_in_scope(nodes_by_id, query.scope_path)
    adjacency = _adjacency(graph=graph, allowed_edges=allowed_edges, direction=query.direction, scoped_nodes=scoped_nodes)
    edge_by_id = {_edge_key(edge): edge for edge in graph.edges}

    evidence: list[ImpactEvidence] = []
    visited: dict[str, int] = {}
    queue: deque[tuple[str, str, int, tuple[str, ...]]] = deque()
    for seed in sorted(query.seed_node_ids):
        queue.append((seed, seed, 0, ()))
        visited[seed] = 0
        seed_node = nodes_by_id[seed]
        if _node_allowed(seed_node, allowed_nodes):
            evidence.append(_evidence(seed, seed_node, (), edge_by_id, nodes_by_id))

    truncated = False
    while queue:
        seed, node_id, distance, path = queue.popleft()
        if distance >= query.max_depth:
            continue
        for neighbor_id, edge_id in adjacency.get(node_id, []):
            next_distance = distance + 1
            next_path = (*path, edge_id)
            current_best = visited.get(neighbor_id)
            if current_best is not None and current_best <= next_distance:
                continue
            visited[neighbor_id] = next_distance
            neighbor = nodes_by_id[neighbor_id]
            if _node_allowed(neighbor, allowed_nodes):
                evidence.append(_evidence(seed, neighbor, next_path, edge_by_id, nodes_by_id))
                if len(evidence) >= query.max_results:
                    truncated = True
                    queue.clear()
                    break
            queue.append((seed, neighbor_id, next_distance, next_path))

    evidence = sorted(
        _dedupe_evidence(evidence),
        key=lambda item: (item.distance, item.seed_node_id, item.affected_node_id, item.ordered_edge_path),
    )
    if len(evidence) > query.max_results:
        evidence = evidence[: query.max_results]
        truncated = True

    report_payload = _report_payload(
        graph=graph,
        snapshot_identity=snapshot_identity,
        query=query,
        evidence=evidence,
        truncated=truncated,
    )
    report_digest = _digest(report_payload)
    return ImpactReport(**report_payload, report_digest=report_digest)


def _report_payload(
    *,
    graph: AppContextGraph,
    snapshot_identity: GraphSnapshotIdentity,
    query: ImpactQuery,
    evidence: list[ImpactEvidence],
    truncated: bool,
) -> dict[str, Any]:
    nodes_by_id = {node.node_id: node for node in graph.nodes}
    edge_by_id = {_edge_key(edge): edge for edge in graph.edges}
    affected_node_ids = sorted({item.affected_node_id for item in evidence})
    affected_nodes = [nodes_by_id[node_id] for node_id in affected_node_ids if node_id in nodes_by_id]
    affected_files = sorted(_dedupe(path for node in affected_nodes for path in _node_paths(node)))
    affected_symbols = sorted(
        _dedupe(
            str((node.metadata or {}).get("qualified_name") or node.label or node.node_id)
            for node in affected_nodes
            if node.node_type == GraphNodeType.SYMBOL
        )
    )
    affected_contracts = sorted(
        _dedupe(
            path
            for node in affected_nodes
            for path in _node_paths(node)
            if _is_contract_path(path)
        )
    )
    dependencies = sorted(
        _dedupe(
            edge.target_node_id
            for edge in graph.edges
            if edge.source_node_id in affected_node_ids and edge.edge_type == GraphEdgeType.DEPENDS_ON
        )
    )
    dependents = sorted(
        _dedupe(
            edge.source_node_id
            for edge in graph.edges
            if edge.target_node_id in affected_node_ids and edge.edge_type == GraphEdgeType.DEPENDS_ON
        )
    )
    ownership = sorted(
        (
            {
                "node_id": node.node_id,
                "ownership": _ownership(node),
                "paths": _node_paths(node),
            }
            for node in affected_nodes
            if _ownership(node)
        ),
        key=lambda item: (str(item["ownership"]), str(item["node_id"])),
    )
    ambiguity = _ambiguity_risks(affected_nodes)
    unresolved = sorted(
        _dedupe(
            edge.target_node_id
            for item in evidence
            for edge_id in item.ordered_edge_path
            for edge in [edge_by_id.get(edge_id)]
            if edge and nodes_by_id.get(edge.target_node_id, None)
            and nodes_by_id[edge.target_node_id].node_type == GraphNodeType.UNKNOWN
        )
    )
    parser_degradation = sorted(
        _dedupe(
            item.degradation_reason
            for item in evidence
            if item.authority != ParserAuthority.AUTHORITATIVE and item.degradation_reason
        )
    )
    return {
        "snapshot_identity": snapshot_identity,
        "query": query,
        "snapshot_digest": snapshot_identity.identity_digest,
        "query_digest": impact_query_digest(query),
        "affected_node_ids": affected_node_ids,
        "affected_files": affected_files,
        "affected_symbols": affected_symbols,
        "affected_contracts": affected_contracts,
        "ownership_boundaries": ownership,
        "dependencies": dependencies,
        "dependents": dependents,
        "collision_risks": _collision_risks(affected_nodes),
        "ambiguity_risks": ambiguity,
        "unresolved_relationships": unresolved,
        "parser_degradation": parser_degradation,
        "truncated": truncated,
        "evidence": evidence,
    }


def _evidence(
    seed_node_id: str,
    affected: AppContextGraphNode,
    path: tuple[str, ...],
    edge_by_id: dict[str, AppContextGraphEdge],
    nodes_by_id: dict[str, AppContextGraphNode],
) -> ImpactEvidence:
    edges = [edge_by_id[edge_id] for edge_id in path if edge_id in edge_by_id]
    authorities = [_node_authority(affected), *[_edge_authority(edge=edge, nodes_by_id=nodes_by_id) for edge in edges]]
    authority = _min_authority(authorities)
    reason = None
    if authority != ParserAuthority.AUTHORITATIVE:
        reason = _degradation_reason(affected, edges, nodes_by_id)
    return ImpactEvidence(
        seed_node_id=seed_node_id,
        affected_node_id=affected.node_id,
        ordered_edge_path=list(path),
        relationship_types=[edge.edge_type for edge in edges],
        distance=len(edges),
        structured_evidence={
            "affected_node_type": affected.node_type.value,
            "affected_label": affected.label,
            "paths": _node_paths(affected),
            "metadata": _safe_metadata(affected.metadata),
        },
        ownership=_ownership(affected),
        authority=authority,
        degradation_reason=reason,
    )


def _adjacency(
    *,
    graph: AppContextGraph,
    allowed_edges: set[GraphEdgeType],
    direction: ImpactDirection,
    scoped_nodes: set[str] | None,
) -> dict[str, list[tuple[str, str]]]:
    adjacency: dict[str, list[tuple[str, str]]] = {}
    for edge in graph.edges:
        if edge.edge_type not in allowed_edges:
            continue
        if scoped_nodes is not None and (edge.source_node_id not in scoped_nodes or edge.target_node_id not in scoped_nodes):
            continue
        edge_id = _edge_key(edge)
        if direction in {ImpactDirection.OUTBOUND, ImpactDirection.BOTH}:
            adjacency.setdefault(edge.source_node_id, []).append((edge.target_node_id, edge_id))
        if direction in {ImpactDirection.INBOUND, ImpactDirection.BOTH}:
            adjacency.setdefault(edge.target_node_id, []).append((edge.source_node_id, edge_id))
    edge_lookup: dict[str, AppContextGraphEdge] = {_edge_key(edge): edge for edge in graph.edges}
    for node_id in adjacency:
        adjacency[node_id].sort(key=lambda item: (edge_by_sort_key(edge_lookup, item[1]), item[0]))
    return adjacency


def edge_by_sort_key(edge_lookup: dict[str, AppContextGraphEdge], edge_id: str) -> tuple[str, str, str]:
    edge = edge_lookup.get(edge_id)
    if edge is None:
        return ("", "", edge_id)
    return (edge.edge_type.value, edge.source_node_id, edge.target_node_id)


def _requires_symbol_authority(*, query: ImpactQuery, nodes_by_id: dict[str, AppContextGraphNode]) -> bool:
    if GraphNodeType.SYMBOL in set(query.allowed_node_kinds):
        return True
    return any(nodes_by_id[node_id].node_type == GraphNodeType.SYMBOL for node_id in query.seed_node_ids if node_id in nodes_by_id)


def _symbol_authority_failures(
    *,
    query: ImpactQuery,
    graph: AppContextGraph,
    nodes_by_id: dict[str, AppContextGraphNode],
) -> list[str]:
    failures: list[str] = []
    allowed = set(query.allowed_edge_types)
    candidate_ids = set(query.seed_node_ids)
    for edge in graph.edges:
        if edge.edge_type not in allowed:
            continue
        if edge.source_node_id in candidate_ids:
            candidate_ids.add(edge.target_node_id)
        if edge.target_node_id in candidate_ids:
            candidate_ids.add(edge.source_node_id)
    for node_id in sorted(candidate_ids):
        node = nodes_by_id.get(node_id)
        if node is None or node.node_type != GraphNodeType.SYMBOL:
            continue
        authority = _node_authority(node)
        if authority != ParserAuthority.AUTHORITATIVE:
            failures.append(f"{node_id}:{authority.value}")
    return failures


def _node_authority(node: AppContextGraphNode) -> ParserAuthority:
    metadata = node.metadata or {}
    explicit = _authority_value(metadata.get("authority") or metadata.get("parser_authority"))
    if explicit is not None:
        return explicit
    parser = str(metadata.get("parser") or "").strip()
    if node.node_type != GraphNodeType.SYMBOL:
        return ParserAuthority.AUTHORITATIVE
    if parser in {"tree_sitter", "python_ast"}:
        return ParserAuthority.AUTHORITATIVE
    if parser == "regex":
        return ParserAuthority.DEGRADED
    return ParserAuthority.INSUFFICIENT


def _edge_authority(*, edge: AppContextGraphEdge, nodes_by_id: dict[str, AppContextGraphNode]) -> ParserAuthority:
    metadata = edge.metadata or {}
    explicit = _authority_value(metadata.get("authority") or metadata.get("parser_authority"))
    if explicit is not None:
        return explicit
    source = nodes_by_id.get(edge.source_node_id)
    target = nodes_by_id.get(edge.target_node_id)
    values = [_node_authority(node) for node in (source, target) if node is not None]
    if edge.edge_type in {
        GraphEdgeType.CONTAINS,
        GraphEdgeType.DECLARES,
        GraphEdgeType.GENERATED_FROM,
        GraphEdgeType.PRODUCES,
        GraphEdgeType.CONSUMES,
        GraphEdgeType.TRIGGERS,
        GraphEdgeType.PROTECTED_BY,
    }:
        values.append(ParserAuthority.AUTHORITATIVE)
    return _min_authority(values)


def _authority_value(value: Any) -> ParserAuthority | None:
    try:
        return ParserAuthority(str(value))
    except Exception:
        return None


def _min_authority(values: list[ParserAuthority]) -> ParserAuthority:
    rank = {
        ParserAuthority.AUTHORITATIVE: 0,
        ParserAuthority.DEGRADED: 1,
        ParserAuthority.INSUFFICIENT: 2,
    }
    if not values:
        return ParserAuthority.INSUFFICIENT
    return max(values, key=lambda item: rank[item])


def _degradation_reason(
    node: AppContextGraphNode,
    edges: list[AppContextGraphEdge],
    nodes_by_id: dict[str, AppContextGraphNode],
) -> str:
    candidates = [node, *[nodes_by_id[node_id] for edge in edges for node_id in (edge.source_node_id, edge.target_node_id) if node_id in nodes_by_id]]
    for candidate in candidates:
        authority = _node_authority(candidate)
        if authority != ParserAuthority.AUTHORITATIVE:
            parser = str((candidate.metadata or {}).get("parser") or "missing")
            return f"{candidate.node_id}:{authority.value}:{parser}"
    return "edge_authority_degraded"


def _nodes_in_scope(nodes_by_id: dict[str, AppContextGraphNode], scope_path: str | None) -> set[str] | None:
    if not scope_path:
        return None
    safe = safe_scan_relpath(scope_path)
    if not safe:
        raise ValueError("ImpactQuery scope_path must be repository-relative")
    return {
        node.node_id
        for node in nodes_by_id.values()
        if any(path == safe or path.startswith(f"{safe}/") for path in _node_paths(node))
    }


def _node_allowed(node: AppContextGraphNode, allowed_nodes: set[GraphNodeType]) -> bool:
    return not allowed_nodes or node.node_type in allowed_nodes


def _node_paths(node: AppContextGraphNode) -> list[str]:
    metadata = node.metadata or {}
    raw_paths: list[Any] = [metadata.get("path"), metadata.get("source_path"), metadata.get("file_path")]
    if node.node_type == GraphNodeType.FILE and node.label:
        raw_paths.append(node.label)
    out: list[str] = []
    for raw in raw_paths:
        if isinstance(raw, list):
            candidates = raw
        else:
            candidates = [raw]
        for item in candidates:
            safe = safe_scan_relpath(str(item)) if item else None
            if safe and safe not in out:
                out.append(safe)
    return out


def _ownership(node: AppContextGraphNode) -> str | None:
    metadata = node.metadata or {}
    for key in ("ownership", "ownership_class", "owner"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _is_contract_path(path: str) -> bool:
    return path.endswith((".yaml", ".yml", ".json")) and (
        path.endswith("module.yaml")
        or "/contracts/" in path
        or path in {"app.json", "ui/route_manifest.json", "data/contract.json", ".mozaiks/pack_provenance.json"}
        or path.startswith(("config/", "brand/", "workflows/"))
    )


def _collision_risks(nodes: list[AppContextGraphNode]) -> list[str]:
    by_label: dict[tuple[str, str], list[str]] = {}
    for node in nodes:
        label = str(node.label or "").strip().lower()
        if not label:
            continue
        by_label.setdefault((node.node_type.value, label), []).append(node.node_id)
    return [
        f"{node_type}:{label}:{','.join(sorted(ids))}"
        for (node_type, label), ids in sorted(by_label.items())
        if len(set(ids)) > 1
    ]


def _ambiguity_risks(nodes: list[AppContextGraphNode]) -> list[str]:
    risks = []
    for node in nodes:
        if node.node_type == GraphNodeType.UNKNOWN:
            risks.append(f"unknown_node:{node.node_id}")
        if (node.metadata or {}).get("resolved") is False:
            risks.append(f"unresolved:{node.node_id}")
    return sorted(_dedupe(risks))


def _dedupe_evidence(evidence: list[ImpactEvidence]) -> list[ImpactEvidence]:
    out: list[ImpactEvidence] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for item in evidence:
        key = (item.seed_node_id, item.affected_node_id, tuple(item.ordered_edge_path))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _edge_key(edge: AppContextGraphEdge) -> str:
    return edge.edge_id or f"{edge.edge_type.value}:{edge.source_node_id}:{edge.target_node_id}"


def _graph_content_payload(graph: AppContextGraph) -> dict[str, Any]:
    return {
        "graph_id": graph.graph_id,
        "app_id": graph.app_id,
        "stale_status": graph.stale_status.value,
        "nodes": sorted(
            (_without_timestamps(node.model_dump(mode="json")) for node in graph.nodes),
            key=lambda item: str(item["node_id"]),
        ),
        "edges": sorted(
            (_without_timestamps(edge.model_dump(mode="json")) for edge in graph.edges),
            key=lambda item: str(item.get("edge_id") or f"{item['edge_type']}:{item['source_node_id']}:{item['target_node_id']}"),
        ),
    }


def _safe_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return _without_timestamps(metadata)


def _without_timestamps(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _without_timestamps(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {
            str(key): _without_timestamps(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in _TIMESTAMP_KEYS
        }
    if isinstance(value, list):
        return [_without_timestamps(item) for item in value]
    return value


def _digest(value: Any) -> str:
    raw = json.dumps(_without_timestamps(value), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _dedupe(values: Any) -> list[str]:
    out: list[str] = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
    return out


__all__ = [
    "GRAPH_SCHEMA_VERSION",
    "PARSER_MANIFEST_VERSION",
    "analyze_impact",
    "build_graph_snapshot_identity",
    "impact_query_digest",
]

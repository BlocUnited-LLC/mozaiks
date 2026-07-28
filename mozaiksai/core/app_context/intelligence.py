"""Deterministic App Intelligence snapshots built from source and graph facts."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import AppContextGraph, GraphNodeType
from .source_corpus import SourceCorpusBundle

APP_INTELLIGENCE_SCHEMA_VERSION = "mozaiks.app_intelligence.snapshot.v1"


class AppIntelligenceSnapshot(BaseModel):
    """Compact, versioned app understanding for agents and Studio.

    The snapshot intentionally stores summaries, paths, graph ids, and policy
    hints. Exact source remains in SourceContextBundle retrieval tools.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = APP_INTELLIGENCE_SCHEMA_VERSION
    snapshot_id: str = Field(min_length=1)
    app_id: str = Field(min_length=1)
    indexed_at: datetime
    source_context_bundle_id: str | None = None
    graph_id: str | None = None
    graph_hash: str | None = None
    source_ref_ids: list[str] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
    architecture: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[dict[str, Any]] = Field(default_factory=list)
    ownership: dict[str, Any] = Field(default_factory=dict)
    integration_surfaces: list[dict[str, Any]] = Field(default_factory=list)
    data_surfaces: list[dict[str, Any]] = Field(default_factory=list)
    risk_hints: list[dict[str, Any]] = Field(default_factory=list)
    agent_context_policy: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def build_app_intelligence_snapshot(
    *,
    source_corpus: SourceCorpusBundle | dict[str, Any],
    app_context_graph: AppContextGraph | dict[str, Any],
    app_id: str | None = None,
    indexed_at: datetime | None = None,
    warnings: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> AppIntelligenceSnapshot:
    """Build a deterministic app intelligence snapshot from indexed code facts."""
    bundle = _coerce_bundle(source_corpus)
    graph = _coerce_graph(app_context_graph)
    resolved_app_id = str(app_id or bundle.app_id or graph.app_id or "").strip()
    if not resolved_app_id:
        raise ValueError("app_id is required")

    resolved_indexed_at = indexed_at or datetime.now(UTC)
    role_counts = Counter(file.role for file in bundle.files)
    language_counts = Counter(file.language for file in bundle.files)
    priority_counts = Counter(file.priority_label for file in bundle.files)
    node_counts = Counter(node.node_type.value for node in graph.nodes)
    edge_counts = Counter(edge.edge_type.value for edge in graph.edges)
    source_ref_ids = _dedupe([ref.source_ref_id for ref in graph.source_refs] + [ref.source_ref_id for ref in bundle.source_refs])

    architecture = _architecture_summary(bundle=bundle, graph=graph)
    capabilities = _capability_summaries(bundle=bundle, graph=graph)
    ownership = _ownership_summary(bundle=bundle, graph=graph)
    integration_surfaces = _integration_surfaces(bundle=bundle, graph=graph)
    data_surfaces = _data_surfaces(bundle=bundle, graph=graph)
    risk_hints = _risk_hints(
        bundle=bundle,
        graph=graph,
        role_counts=role_counts,
        warnings=[*list(bundle.warnings), *list(warnings or [])],
    )

    payload_identity = {
        "app_id": resolved_app_id,
        "bundle_id": bundle.bundle_id,
        "graph_id": graph.graph_id,
        "graph_hash": graph.graph_hash,
        "file_checksums": {file.path: file.checksum for file in bundle.files},
    }
    snapshot_id = _stable_id("app_intelligence", _json_hash(payload_identity))

    return AppIntelligenceSnapshot(
        snapshot_id=snapshot_id,
        app_id=resolved_app_id,
        indexed_at=resolved_indexed_at,
        source_context_bundle_id=bundle.bundle_id,
        graph_id=graph.graph_id,
        graph_hash=graph.graph_hash,
        source_ref_ids=source_ref_ids,
        coverage={
            "file_count": len(bundle.files),
            "chunk_count": len(bundle.chunks),
            "symbol_count": len(bundle.symbols),
            "import_count": len(bundle.imports),
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "role_counts": dict(sorted(role_counts.items())),
            "language_counts": dict(sorted(language_counts.items())),
            "priority_counts": dict(sorted(priority_counts.items())),
            "node_counts": dict(sorted(node_counts.items())),
            "edge_counts": dict(sorted(edge_counts.items())),
            "parser_status": dict(bundle.parser_status),
            "scan_health": dict(bundle.health),
        },
        architecture=architecture,
        capabilities=capabilities,
        ownership=ownership,
        integration_surfaces=integration_surfaces,
        data_surfaces=data_surfaces,
        risk_hints=risk_hints,
        agent_context_policy=_agent_context_policy(),
        warnings=_dedupe([*list(bundle.warnings), *list(warnings or [])]),
        metadata=dict(metadata or {}),
    )


def build_app_intelligence_catalog(
    snapshot: AppIntelligenceSnapshot | dict[str, Any],
    *,
    max_items: int = 16,
) -> dict[str, Any]:
    """Return prompt-safe App Intelligence context without source content."""
    resolved = _coerce_snapshot(snapshot)
    limit = max(1, min(int(max_items or 16), 80))
    architecture = dict(resolved.architecture)
    for key in (
        "entrypoints",
        "manifests",
        "module_roots",
        "service_roots",
        "ui_surfaces",
        "workflow_roots",
        "configuration_surfaces",
        "deployment_surfaces",
        "test_surfaces",
        "top_source_roots",
    ):
        value = architecture.get(key)
        if isinstance(value, list):
            architecture[key] = value[:limit]

    return {
        "present": True,
        "schema_version": resolved.schema_version,
        "snapshot_id": resolved.snapshot_id,
        "app_id": resolved.app_id,
        "indexed_at": resolved.indexed_at.isoformat(),
        "source_context_bundle_id": resolved.source_context_bundle_id,
        "graph_id": resolved.graph_id,
        "graph_hash": resolved.graph_hash,
        "coverage": resolved.coverage,
        "architecture": architecture,
        "capabilities": resolved.capabilities[:limit],
        "ownership": resolved.ownership,
        "integration_surfaces": resolved.integration_surfaces[:limit],
        "data_surfaces": resolved.data_surfaces[:limit],
        "risk_hints": resolved.risk_hints[:limit],
        "agent_context_policy": resolved.agent_context_policy,
        "warnings": list(resolved.warnings),
    }


def search_app_intelligence_snapshot(
    snapshot: AppIntelligenceSnapshot | dict[str, Any],
    query: str,
    *,
    max_results: int = 12,
) -> list[dict[str, Any]]:
    """Search compact App Intelligence paths and surfaces."""
    resolved = _coerce_snapshot(snapshot)
    terms = _keywords(query)
    if not terms:
        return []
    candidates: list[tuple[int, str, dict[str, Any]]] = []
    for section, values in _search_sections(resolved).items():
        for item in values:
            text = _search_text(item)
            matched = [term for term in terms if term in text]
            if not matched:
                continue
            path = str(item.get("path") or item.get("root") or item.get("location") or "")
            score = len(matched) * 10
            if path and any(term in path.lower() for term in matched):
                score += 20
            if section == "capabilities":
                score += 12
            candidates.append(
                (
                    score,
                    f"{section}:{path}:{item.get('id') or item.get('capability_id') or item.get('label')}",
                    {
                        "section": section,
                        "score": score,
                        "matched_terms": matched,
                        **item,
                    },
                )
            )
    candidates.sort(key=lambda value: (-value[0], value[1]))
    bounded = max(1, min(int(max_results or 12), 40))
    return [item for _, _, item in candidates[:bounded]]


def _architecture_summary(*, bundle: SourceCorpusBundle, graph: AppContextGraph) -> dict[str, Any]:
    files_by_role = defaultdict(list)
    for file in bundle.files:
        files_by_role[file.role].append(_file_hint(file))

    return {
        "entrypoints": files_by_role.get("entrypoint", [])[:24],
        "manifests": files_by_role.get("manifest", [])[:32],
        "module_roots": _module_roots(bundle),
        "service_roots": _service_roots(bundle),
        "ui_surfaces": [
            *files_by_role.get("route", []),
            *files_by_role.get("page", []),
            *files_by_role.get("component", []),
        ][:80],
        "workflow_roots": _workflow_roots(bundle),
        "configuration_surfaces": _configuration_surfaces(bundle),
        "deployment_surfaces": files_by_role.get("deployment", [])[:32],
        "test_surfaces": files_by_role.get("test", [])[:32],
        "top_source_roots": _top_source_roots(bundle),
        "dominant_graph_surfaces": _dominant_graph_surfaces(graph),
    }


def _capability_summaries(*, bundle: SourceCorpusBundle, graph: AppContextGraph) -> list[dict[str, Any]]:
    capabilities: list[dict[str, Any]] = []
    seen: set[str] = set()

    for node in graph.nodes:
        if node.node_type != GraphNodeType.CAPABILITY:
            continue
        metadata = dict(node.metadata or {})
        capability_id = _slug(metadata.get("capability_id") or node.label or node.node_id, fallback="capability")
        paths = _paths_connected_to_node(graph=graph, node_id=node.node_id)
        capabilities.append(
            _clean_item(
                {
                    "capability_id": capability_id,
                    "label": node.label or capability_id,
                    "kind": "declared_capability",
                    "confidence": metadata.get("confidence") or "graph_declared",
                    "evidence_paths": paths[:16],
                    "entrypoints": metadata.get("entrypoints") or [],
                    "connector_requirements": metadata.get("connector_requirements") or [],
                    "node_id": node.node_id,
                }
            )
        )
        seen.add(capability_id)

    for module in _module_roots(bundle):
        capability_id = f"module:{module['module_id']}"
        if capability_id in seen:
            continue
        capabilities.append(
            {
                "capability_id": capability_id,
                "label": module["module_id"],
                "kind": "module_boundary",
                "confidence": "source_structure",
                "evidence_paths": module["paths"][:16],
                "role_counts": module["role_counts"],
            }
        )
        seen.add(capability_id)

    for service in _service_roots(bundle):
        capability_id = f"service:{service['service_id']}"
        if capability_id in seen:
            continue
        capabilities.append(
            {
                "capability_id": capability_id,
                "label": service["service_id"],
                "kind": "service_boundary",
                "confidence": "source_structure",
                "evidence_paths": service["paths"][:16],
                "role_counts": service["role_counts"],
            }
        )
        seen.add(capability_id)

    capabilities.sort(key=lambda item: (str(item.get("kind")), str(item.get("capability_id"))))
    return capabilities[:120]


def _ownership_summary(*, bundle: SourceCorpusBundle, graph: AppContextGraph) -> dict[str, Any]:
    ownership_counts = Counter()
    review_paths: list[str] = []
    for node in graph.nodes:
        metadata = node.metadata or {}
        ownership = metadata.get("ownership") or metadata.get("ownership_class")
        if ownership:
            ownership_counts[str(ownership)] += 1
            path = metadata.get("path") or metadata.get("source_path")
            if path:
                review_paths.append(str(path))

    return {
        "ownership_counts": dict(sorted(ownership_counts.items())),
        "review_paths": _dedupe(review_paths)[:80],
        "editable_source_roots": [
            item
            for item in _top_source_roots(bundle)
            if str(item.get("root")) not in {"docs", "tests"}
        ][:24],
        "policy": "Use AppContext ownership boundaries and source retrieval before staging patches.",
    }


def _integration_surfaces(*, bundle: SourceCorpusBundle, graph: AppContextGraph) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for file in bundle.files:
        if file.role in {"integration", "api_client", "auth", "deployment"}:
            items.append({**_file_hint(file), "surface_type": file.role})
    for node in graph.nodes:
        if node.node_type != GraphNodeType.INTEGRATION:
            continue
        metadata = dict(node.metadata or {})
        items.append(
            _clean_item(
                {
                    "surface_type": "integration_node",
                    "label": node.label,
                    "node_id": node.node_id,
                    "provider_type": metadata.get("provider_type"),
                    "readiness_status": metadata.get("readiness_status"),
                    "path": metadata.get("path") or metadata.get("source_path"),
                }
            )
        )
    return _dedupe_dicts(items, key_fields=("surface_type", "path", "label"))[:120]


def _data_surfaces(*, bundle: SourceCorpusBundle, graph: AppContextGraph) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for file in bundle.files:
        if file.role in {"data_model", "persistence"}:
            items.append({**_file_hint(file), "surface_type": file.role})
    for node in graph.nodes:
        if node.node_type != GraphNodeType.DATA_ENTITY:
            continue
        metadata = dict(node.metadata or {})
        items.append(
            _clean_item(
                {
                    "surface_type": "data_entity",
                    "label": node.label,
                    "node_id": node.node_id,
                    "path": metadata.get("path") or metadata.get("source_path"),
                }
            )
        )
    return _dedupe_dicts(items, key_fields=("surface_type", "path", "label"))[:120]


def _risk_hints(
    *,
    bundle: SourceCorpusBundle,
    graph: AppContextGraph,
    role_counts: Counter[str],
    warnings: list[str],
) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    skipped = bundle.health.get("skipped")
    if isinstance(skipped, dict):
        if int(skipped.get("sensitive_path") or 0) > 0:
            risks.append(
                {
                    "risk_id": "sensitive_paths_skipped",
                    "severity": "medium",
                    "description": "Sensitive-looking paths were excluded from indexing.",
                    "count": int(skipped.get("sensitive_path") or 0),
                }
            )
        if int(skipped.get("large_file") or 0) > 0:
            risks.append(
                {
                    "risk_id": "large_files_skipped",
                    "severity": "low",
                    "description": "Large files were excluded by the source scan policy.",
                    "count": int(skipped.get("large_file") or 0),
                }
            )
    if any("file_limit" in str(warning) for warning in warnings):
        risks.append(
            {
                "risk_id": "source_scan_file_limit_reached",
                "severity": "medium",
                "description": "The source scan hit its configured file limit; context may be partial.",
            }
        )
    if role_counts.get("test", 0) == 0:
        risks.append(
            {
                "risk_id": "no_tests_indexed",
                "severity": "medium",
                "description": "No test files were indexed, so validation guidance may need explicit user input.",
            }
        )
    unknown_nodes = [node for node in graph.nodes if node.node_type == GraphNodeType.UNKNOWN]
    if unknown_nodes:
        risks.append(
            {
                "risk_id": "unresolved_graph_references",
                "severity": "low",
                "description": "Some imports or call targets could not be resolved inside the indexed corpus.",
                "count": len(unknown_nodes),
            }
        )
    parser_status = bundle.parser_status or {}
    if parser_status and not parser_status.get("tree_sitter_package_available", False):
        risks.append(
            {
                "risk_id": "tree_sitter_unavailable",
                "severity": "high",
                "description": "Tree-sitter is unavailable; source understanding is degraded.",
            }
        )
    return risks[:40]


def _agent_context_policy() -> dict[str, Any]:
    return {
        "policy": "retrieve_not_dump",
        "authority": {
            "facts": "SourceContextBundle and AppContextGraph",
            "summary": "AppIntelligenceSnapshot",
            "exact_code": "source retrieval tools",
        },
        "surfaces": [
            {
                "workflow_or_checkpoint": "ExistingAppDiscovery",
                "default_context": ["app_intelligence_catalog", "context_graph_pack", "source_context_catalog"],
                "tools": ["get_repo_app_intelligence", "search_repo_source_context", "read_repo_source_file"],
            },
            {
                "workflow_or_checkpoint": "AppGenerator/AgentGenerator",
                "default_context": ["app_intelligence_catalog", "context_graph_pack"],
                "tools": ["source retrieval only for selected surfaces"],
            },
            {
                "workflow_or_checkpoint": "refinement:request_submitted",
                "default_context": ["revision context", "artifact summary", "app intelligence freshness"],
                "tools": ["get_app_intelligence_context"],
            },
            {
                "workflow_or_checkpoint": "refinement:scope_requested",
                "default_context": ["app intelligence catalog", "graph catalog", "source search"],
                "tools": ["get_app_intelligence_context", "get_context_graph_catalog", "search_app_source_context"],
            },
            {
                "workflow_or_checkpoint": "refinement:coding_requested",
                "default_context": ["explicit scoped files", "related files", "exact source reads"],
                "tools": ["read_app_source_file", "get_related_app_source_files", "get_context_graph_scope"],
            },
        ],
    }


def _module_roots(bundle: SourceCorpusBundle) -> list[dict[str, Any]]:
    groups: dict[str, list[Any]] = defaultdict(list)
    for file in bundle.files:
        module_id = _module_id_for_path(file.path)
        if module_id:
            groups[module_id].append(file)
    out = []
    for module_id, files in sorted(groups.items()):
        out.append(
            {
                "module_id": module_id,
                "paths": [file.path for file in sorted(files, key=lambda item: item.path)][:80],
                "file_count": len(files),
                "role_counts": dict(sorted(Counter(file.role for file in files).items())),
            }
        )
    return out[:80]


def _service_roots(bundle: SourceCorpusBundle) -> list[dict[str, Any]]:
    groups: dict[str, list[Any]] = defaultdict(list)
    for file in bundle.files:
        service_id = _service_id_for_path(file.path)
        if service_id:
            groups[service_id].append(file)
    out = []
    for service_id, files in sorted(groups.items()):
        out.append(
            {
                "service_id": service_id,
                "paths": [file.path for file in sorted(files, key=lambda item: item.path)][:80],
                "file_count": len(files),
                "role_counts": dict(sorted(Counter(file.role for file in files).items())),
            }
        )
    return out[:80]


def _workflow_roots(bundle: SourceCorpusBundle) -> list[dict[str, Any]]:
    groups: dict[str, list[Any]] = defaultdict(list)
    for file in bundle.files:
        workflow_id = _workflow_id_for_path(file.path)
        if workflow_id:
            groups[workflow_id].append(file)
    return [
        {
            "workflow_id": workflow_id,
            "paths": [file.path for file in sorted(files, key=lambda item: item.path)][:60],
            "file_count": len(files),
            "role_counts": dict(sorted(Counter(file.role for file in files).items())),
        }
        for workflow_id, files in sorted(groups.items())
    ][:80]


def _configuration_surfaces(bundle: SourceCorpusBundle) -> list[dict[str, Any]]:
    config_terms = ("/config/", "config.", "settings.", ".env.example", "app.json", "route_manifest")
    return [
        _file_hint(file)
        for file in bundle.files
        if file.role == "manifest" or any(term in file.path.lower() for term in config_terms)
    ][:80]


def _top_source_roots(bundle: SourceCorpusBundle) -> list[dict[str, Any]]:
    groups: dict[str, list[Any]] = defaultdict(list)
    for file in bundle.files:
        root = PurePosixPath(file.path).parts[0] if PurePosixPath(file.path).parts else file.path
        groups[root].append(file)
    out = []
    for root, files in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        out.append(
            {
                "root": root,
                "file_count": len(files),
                "role_counts": dict(sorted(Counter(file.role for file in files).items())),
                "priority_counts": dict(sorted(Counter(file.priority_label for file in files).items())),
            }
        )
    return out[:32]


def _dominant_graph_surfaces(graph: AppContextGraph) -> list[dict[str, Any]]:
    wanted = {
        GraphNodeType.MODULE,
        GraphNodeType.PAGE,
        GraphNodeType.ROUTE,
        GraphNodeType.WORKFLOW,
        GraphNodeType.INTEGRATION,
        GraphNodeType.DATA_ENTITY,
    }
    items = []
    for node in graph.nodes:
        if node.node_type not in wanted:
            continue
        metadata = dict(node.metadata or {})
        items.append(
            _clean_item(
                {
                    "node_id": node.node_id,
                    "node_type": node.node_type.value,
                    "label": node.label,
                    "path": metadata.get("path") or metadata.get("source_path") or metadata.get("location"),
                }
            )
        )
    return items[:80]


def _paths_connected_to_node(*, graph: AppContextGraph, node_id: str) -> list[str]:
    nodes_by_id = {node.node_id: node for node in graph.nodes}
    paths: list[str] = []
    for edge in graph.edges:
        if edge.source_node_id != node_id and edge.target_node_id != node_id:
            continue
        for candidate_id in (edge.source_node_id, edge.target_node_id):
            node = nodes_by_id.get(candidate_id)
            if node is None:
                continue
            metadata = node.metadata or {}
            path = metadata.get("path") or metadata.get("source_path")
            if path:
                paths.append(str(path))
    return _dedupe(paths)


def _file_hint(file: Any) -> dict[str, Any]:
    return {
        "path": file.path,
        "role": file.role,
        "language": file.language,
        "priority_label": file.priority_label,
        "symbol_count": len(file.symbol_ids),
        "chunk_count": len(file.chunk_ids),
    }


def _module_id_for_path(path: str) -> str | None:
    parts = PurePosixPath(path).parts
    for index, part in enumerate(parts[:-1]):
        if part == "modules" and index + 1 < len(parts):
            return _slug(parts[index + 1], fallback="module")
    return None


def _service_id_for_path(path: str) -> str | None:
    parts = PurePosixPath(path).parts
    for index, part in enumerate(parts[:-1]):
        if part == "services" and index + 1 < len(parts):
            if parts[index + 1] == "__init__.py":
                return None
            if parts[index + 1] in {"adapters", "integrations", "routes"} and index + 2 < len(parts):
                if parts[index + 2] == "__init__.py":
                    return None
                return f"{parts[index + 1]}:{parts[index + 2]}"
            return parts[index + 1]
    return None


def _workflow_id_for_path(path: str) -> str | None:
    parts = PurePosixPath(path).parts
    for index, part in enumerate(parts[:-1]):
        if part == "workflows" and index + 1 < len(parts):
            return _slug(parts[index + 1], fallback="workflow")
    return None


def _search_sections(snapshot: AppIntelligenceSnapshot) -> dict[str, list[dict[str, Any]]]:
    architecture_items: list[dict[str, Any]] = []
    for value in snapshot.architecture.values():
        if isinstance(value, list):
            architecture_items.extend(item for item in value if isinstance(item, dict))
    return {
        "architecture": architecture_items,
        "capabilities": list(snapshot.capabilities),
        "integrations": list(snapshot.integration_surfaces),
        "data": list(snapshot.data_surfaces),
        "risks": list(snapshot.risk_hints),
    }


def _search_text(item: dict[str, Any]) -> str:
    values: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, (str, int, float)):
            values.append(str(value))

    visit(item)
    return " ".join(values).lower()


def _keywords(query: str) -> list[str]:
    out: list[str] = []
    for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", str(query or "").lower()):
        if token not in out:
            out.append(token)
    return out[:16]


def _coerce_bundle(value: SourceCorpusBundle | dict[str, Any]) -> SourceCorpusBundle:
    return value if isinstance(value, SourceCorpusBundle) else SourceCorpusBundle.model_validate(value)


def _coerce_graph(value: AppContextGraph | dict[str, Any]) -> AppContextGraph:
    return value if isinstance(value, AppContextGraph) else AppContextGraph.model_validate(value)


def _coerce_snapshot(value: AppIntelligenceSnapshot | dict[str, Any]) -> AppIntelligenceSnapshot:
    return value if isinstance(value, AppIntelligenceSnapshot) else AppIntelligenceSnapshot.model_validate(value)


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in out:
            out.append(clean)
    return out


def _dedupe_dicts(items: list[dict[str, Any]], *, key_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for item in items:
        clean = _clean_item(item)
        key = tuple(str(clean.get(field) or "") for field in key_fields)
        if key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out


def _clean_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if value not in (None, "", [], {})}


def _slug(value: Any, *, fallback: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", str(value or "").strip().lower()).strip("_")
    return text or fallback


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:16]}"


def _json_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


__all__ = [
    "APP_INTELLIGENCE_SCHEMA_VERSION",
    "AppIntelligenceSnapshot",
    "build_app_intelligence_catalog",
    "build_app_intelligence_snapshot",
    "search_app_intelligence_snapshot",
]

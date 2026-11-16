"""Deterministic Context Graph builders for app and workflow artifacts.

This module is deliberately storage-free. It turns artifact file maps and
existing app-context graph snapshots into provenance-rich `AppContextGraph`
objects that control-plane tools can query. Graph database adapters may mirror
these snapshots later, but this builder remains the canonical in-repo graph
construction path.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

import yaml

from mozaiksai.core.utils.sequences import dedupe_strings

from ._source_syntax import (
    ExtractedReference,
    ExtractedSymbol,
    SourceImport,
    extract_source_syntax,
    parser_status,
)
from ._source_syntax import (
    _tree_sitter_parser as _tree_sitter_parser,
)
from .models import (
    AppContextGraph,
    AppContextGraphEdge,
    AppContextGraphNode,
    AppContextStaleStatus,
    GraphEdgeType,
    GraphNodeType,
    SourceRef,
    SourceRefKind,
)
from .scan_policy import safe_scan_relpath

_RELATIVE_IMPORT_EXTENSIONS = (
    "",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".json",
    ".yaml",
    ".yml",
    ".css",
    "/index.js",
    "/index.jsx",
    "/index.ts",
    "/index.tsx",
)
_NOISE_CALL_TARGETS = {"bool", "dict", "float", "int", "len", "list", "print", "set", "str", "tuple", "super"}
_SEMANTIC_ANNOTATION_SCHEMA_VERSION = "mozaiks.context_graph.semantic_annotations.v1"






@dataclass
class ExtractedFileContext:
    path: str
    language: str
    checksum: str
    symbols: list[ExtractedSymbol] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    references: list[ExtractedReference] = field(default_factory=list)
    structured_metadata: dict[str, Any] = field(default_factory=dict)
    parser: str | None = None
    parse_status: str | None = None
    parse_error_count: int | None = None
    import_bindings: list[SourceImport] = field(default_factory=list, repr=False)
    local_bindings: set[tuple[str | None, str]] = field(default_factory=set, repr=False)
    exports: dict[str, str] = field(default_factory=dict, repr=False)


def build_context_graph_from_file_map(
    *,
    app_id: str,
    artifact_version_id: str,
    artifact_kind: str,
    artifact_key: str | None = None,
    file_map: dict[str, str],
    source_ref: SourceRef | None = None,
    indexed_at: datetime | None = None,
) -> AppContextGraph:
    """Build a versioned app-context graph snapshot from artifact file content."""
    resolved_app_id = str(app_id or "").strip()
    resolved_artifact_id = str(artifact_version_id or "").strip()
    if not resolved_app_id:
        raise ValueError("app_id is required")
    if not resolved_artifact_id:
        raise ValueError("artifact_version_id is required")

    resolved_indexed_at = indexed_at or datetime.now(UTC)
    resolved_source_ref = source_ref or SourceRef(
        source_ref_id=_stable_id("src", f"artifact:{resolved_artifact_id}"),
        kind=SourceRefKind.ARTIFACT_VERSION,
        uri=f"artifact-version://{resolved_artifact_id}",
        ref=artifact_kind,
        checksum=_file_map_checksum(file_map),
        indexed_at=resolved_indexed_at,
        metadata={
            "artifact_kind": artifact_kind,
            "artifact_key": artifact_key or artifact_kind,
            "artifact_version_id": resolved_artifact_id,
        },
    )

    graph = _GraphDraft(
        app_id=resolved_app_id,
        source_ref=resolved_source_ref,
        artifact_version_id=resolved_artifact_id,
    )
    app_node_id = "app"
    artifact_node_id = f"artifact:{resolved_artifact_id}"
    graph.add_node(
        app_node_id,
        GraphNodeType.APP,
        label=resolved_app_id,
        metadata={"app_id": resolved_app_id},
    )
    graph.add_node(
        resolved_source_ref.source_ref_id,
        GraphNodeType.SOURCE_REF,
        label=resolved_source_ref.uri,
        metadata=dict(resolved_source_ref.metadata),
    )
    graph.add_node(
        artifact_node_id,
        GraphNodeType.ARTIFACT,
        label=artifact_key or artifact_kind,
        metadata={
            "artifact_kind": artifact_kind,
            "artifact_key": artifact_key or artifact_kind,
            "artifact_version_id": resolved_artifact_id,
        },
    )
    graph.add_edge(app_node_id, artifact_node_id, GraphEdgeType.CONTAINS)
    graph.add_edge(artifact_node_id, resolved_source_ref.source_ref_id, GraphEdgeType.GENERATED_FROM)

    safe_file_map = _safe_file_map(file_map)
    extracted_by_path = {
        path: extract_file_context(path=path, content=content)
        for path, content in sorted(safe_file_map.items())
    }
    module_action_ids_by_module = _module_action_ids_by_module(extracted_by_path)
    file_node_by_path: dict[str, str] = {}
    owner_node_by_key: dict[str, str] = {}
    symbol_nodes: dict[tuple[str, str], list[str]] = defaultdict(list)
    exported_nodes: dict[tuple[str, str], list[str]] = defaultdict(list)

    for path, file_context in extracted_by_path.items():
        file_node_id = context_graph_file_node_id(path)
        file_node_by_path[path] = file_node_id
        graph.add_node(
            file_node_id,
            GraphNodeType.FILE,
            label=path,
            checksum=file_context.checksum,
            metadata={
                "path": path,
                "source_path": path,
                "language": file_context.language,
                "size_bytes": len(safe_file_map[path].encode("utf-8")),
                "parser": file_context.parser,
                "parse_status": file_context.parse_status,
                "parse_error_count": file_context.parse_error_count,
                **_file_contract_metadata(path=path, file_context=file_context),
            },
        )
        graph.add_edge(artifact_node_id, file_node_id, GraphEdgeType.CONTAINS, metadata={"source_path": path})
        _add_structural_nodes(
            graph=graph,
            path=path,
            file_context=file_context,
            file_node_id=file_node_id,
            owner_node_by_key=owner_node_by_key,
        )

    for path, file_context in extracted_by_path.items():
        file_node_id = file_node_by_path[path]
        symbol_occurrences: dict[tuple[str, str], int] = defaultdict(int)
        for symbol in file_context.symbols:
            symbol_key = symbol.qualified_name or symbol.name
            occurrence_key = (symbol_key, symbol.kind)
            symbol_occurrences[occurrence_key] += 1
            symbol_node_id = _symbol_node_id(path, symbol_key, symbol.kind, symbol_occurrences[occurrence_key])
            symbol_contract_metadata = _symbol_contract_metadata(
                path=path,
                symbol=symbol,
                file_context=file_context,
                module_action_ids_by_module=module_action_ids_by_module,
            )
            graph.add_node(
                symbol_node_id,
                GraphNodeType.SYMBOL,
                label=symbol.name,
                checksum=file_context.checksum,
                metadata={
                    "path": path,
                    "source_path": path,
                    "symbol_kind": symbol.kind,
                    "line": symbol.line,
                    "end_line": symbol.end_line,
                    "qualified_name": symbol.qualified_name,
                    "parent": symbol.parent,
                    **symbol.metadata,
                    **symbol_contract_metadata,
                },
            )
            symbol_nodes[(path, symbol_key)].append(symbol_node_id)
            graph.add_edge(
                file_node_id,
                symbol_node_id,
                GraphEdgeType.DEFINES,
                metadata={"source_path": path, "symbol_kind": symbol.kind},
            )
            action_id = symbol_contract_metadata.get("action_id")
            module_id = symbol_contract_metadata.get("module_id")
            if action_id and module_id:
                action_node_id = owner_node_by_key.get(f"action:{module_id}:{action_id}") or _stable_id(
                    "action",
                    f"{module_id}:{action_id}",
                )
                graph.add_node(
                    action_node_id,
                    GraphNodeType.API_ENDPOINT,
                    label=str(action_id),
                    metadata={
                        "module_id": module_id,
                        "action_id": action_id,
                        "contract_role": "module_action",
                        "source_path": path,
                    },
                )
                graph.add_edge(
                    symbol_node_id,
                    action_node_id,
                    GraphEdgeType.IMPLEMENTS_CAPABILITY,
                    metadata={"source_path": path, "module_id": module_id, "action_id": action_id},
                )
    for path, file_context in extracted_by_path.items():
        if file_context.language == "python":
            continue
        for export, local in file_context.exports.items():
            if (None, local) not in file_context.local_bindings:
                exported_nodes[(path, export)].extend(symbol_nodes.get((path, local), []))

    for path, file_context in extracted_by_path.items():
        file_node_id = file_node_by_path[path]
        bindings = _resolve_imports(path, file_context, file_node_by_path, symbol_nodes)
        for imported, resolved_path, _ in bindings:
            import_metadata = {
                "source_path": path,
                "import_target": imported.target,
                "line": imported.line,
                "parser": file_context.parser,
                "resolved": resolved_path is not None,
            }
            if resolved_path:
                graph.add_edge(
                    file_node_id,
                    file_node_by_path[resolved_path],
                    GraphEdgeType.IMPORTS,
                    metadata=import_metadata,
                )
            else:
                unknown_node_id = _dependency_id("unknown_import", f"{path}:{imported.target}")
                graph.add_node(
                    unknown_node_id,
                    GraphNodeType.UNKNOWN,
                    label=imported.target,
                    metadata={"path": path, **import_metadata},
                )
                graph.add_edge(
                    file_node_id,
                    unknown_node_id,
                    GraphEdgeType.IMPORTS,
                    metadata=import_metadata,
                )

        bindings_by_scope: dict[tuple[str | None, str], list[_ResolvedImport]] = defaultdict(list)
        for binding in bindings:
            imported = binding[0]
            if imported.local_name:
                bindings_by_scope[(imported.scope, imported.local_name)].append(binding)
        for reference in file_context.references[:120]:
            caller = _unique_node(symbol_nodes.get((path, reference.caller))) if reference.caller else None
            source_node_id = caller or file_node_id
            target_node_id = _reference_target_node_id(
                path, reference, bindings_by_scope, file_context.local_bindings, symbol_nodes,
                symbol_nodes if file_context.language == "python" else exported_nodes, graph,
            )
            if target_node_id is None and reference.target in _NOISE_CALL_TARGETS:
                continue
            reference_metadata = {
                "source_path": path,
                "reference_target": reference.target,
                "reference_kind": reference.kind,
                "line": reference.line,
                "caller": reference.caller,
                "resolved": target_node_id is not None,
                **reference.metadata,
            }
            if target_node_id is None:
                target_node_id = _dependency_id("unknown_reference", f"{path}:{reference.kind}:{reference.target}")
                graph.add_node(
                    target_node_id,
                    GraphNodeType.UNKNOWN,
                    label=reference.target,
                    metadata={"path": path, **reference_metadata},
                )
            graph.add_edge(
                source_node_id,
                target_node_id,
                GraphEdgeType.CALLS if reference.kind == "call" else GraphEdgeType.REFERENCES,
                metadata=reference_metadata,
            )

    return graph.to_graph(indexed_at=resolved_indexed_at)


def merge_context_graphs(
    *,
    app_id: str,
    graph_id: str,
    graphs: Iterable[AppContextGraph | None],
    indexed_at: datetime | None = None,
) -> AppContextGraph:
    """Merge ordered projections, dropping older graphs whose source changed.

    A conflicting FILE checksum invalidates the earlier projection as a whole:
    its symbols and relationships cannot safely be attached to the newer bytes.
    Compatible structural/parser projections enrich the same FILE identities.
    """
    source_refs: dict[str, SourceRef] = {}
    nodes: dict[str, AppContextGraphNode] = {}
    edges: dict[str, AppContextGraphEdge] = {}
    aliases_by_graph: list[dict[str, str]] = []
    stale_status = AppContextStaleStatus.CURRENT
    snapshots = []
    checksums: dict[str, str] = {}
    for graph in reversed([graph for graph in graphs if graph is not None]):
        file_checksums = {
            path: _normalized_file_checksum(node.checksum)
            for node in graph.nodes
            if node.node_type == GraphNodeType.FILE and node.checksum
            and (path := safe_scan_relpath(node.metadata.get("path") or node.metadata.get("source_path") or ""))
        }
        if any(path in checksums and checksums[path] != checksum for path, checksum in file_checksums.items()):
            continue
        snapshots.append(graph)
        for path, checksum in file_checksums.items():
            checksums.setdefault(path, checksum)
    snapshots.reverse()

    for graph in snapshots:
        aliases: dict[str, str] = {}
        aliases_by_graph.append(aliases)
        for ref in graph.source_refs:
            source_refs.setdefault(ref.source_ref_id, ref)
        for node in graph.nodes:
            path = safe_scan_relpath(node.metadata.get("path") or node.metadata.get("source_path") or "")
            if node.node_type == GraphNodeType.FILE and path:
                canonical_id = context_graph_file_node_id(path)
                if node.node_id in aliases and aliases[node.node_id] != canonical_id:
                    raise ValueError("One graph contains conflicting FILE paths for the same node ID")
                aliases[node.node_id] = canonical_id
                existing = nodes.get(canonical_id)
                if existing is not None:
                    nodes[canonical_id] = existing.model_copy(update={
                        "metadata": {**node.metadata, **existing.metadata, "path": path, "source_path": path},
                        "checksum": existing.checksum or node.checksum,
                        "artifact_version_id": existing.artifact_version_id or node.artifact_version_id,
                        "source_ref_id": existing.source_ref_id or node.source_ref_id,
                    })
                else:
                    nodes[canonical_id] = node.model_copy(update={
                        "node_id": canonical_id,
                        "label": path,
                        "metadata": {**node.metadata, "path": path, "source_path": path},
                    })
                continue
            nodes.setdefault(node.node_id, node)
        if graph.stale_status != AppContextStaleStatus.CURRENT:
            stale_status = graph.stale_status

    for graph, aliases in zip(snapshots, aliases_by_graph, strict=True):
        for edge in graph.edges:
            source_id = aliases.get(edge.source_node_id, edge.source_node_id)
            target_id = aliases.get(edge.target_node_id, edge.target_node_id)
            key = _stable_id(
                "edge",
                f"{edge.edge_type.value}:{source_id}:{target_id}",
            )
            edges.setdefault(key, edge.model_copy(update={
                "edge_id": key, "source_node_id": source_id, "target_node_id": target_id,
            }))

    payload = {
        "nodes": [node.model_dump(mode="json") for node in nodes.values()],
        "edges": [edge.model_dump(mode="json") for edge in edges.values()],
    }
    return AppContextGraph(
        graph_id=graph_id,
        app_id=app_id,
        source_refs=list(source_refs.values()),
        indexed_at=indexed_at or datetime.now(UTC),
        nodes=list(nodes.values()),
        edges=list(edges.values()),
        stale_status=stale_status,
        graph_hash=f"sha256:{_sha256_json(payload)}",
    )


def _normalized_file_checksum(checksum: str) -> str:
    value = checksum.strip()
    digest = value[7:] if value.lower().startswith("sha256:") else value
    return digest.lower() if re.fullmatch(r"[0-9a-fA-F]{64}", digest) else value


def build_semantic_annotation_request(
    *,
    graph: AppContextGraph,
    selected_node_ids: list[str] | None = None,
    max_items: int = 40,
) -> dict[str, Any]:
    """Build a bounded LLM annotation request from deterministic graph facts."""
    selected = set(selected_node_ids or [])
    nodes_by_id = {node.node_id: node for node in graph.nodes}
    symbol_nodes = [
        node
        for node in graph.nodes
        if node.node_type == GraphNodeType.SYMBOL
        and (not selected or node.node_id in selected or _symbol_path(node) in selected)
    ]
    symbol_nodes.sort(key=_semantic_candidate_sort_key)
    items = [
        _semantic_annotation_item(node=node, graph=graph, nodes_by_id=nodes_by_id)
        for node in symbol_nodes[:max_items]
    ]
    return {
        "schema_version": _SEMANTIC_ANNOTATION_SCHEMA_VERSION,
        "graph_id": graph.graph_id,
        "app_id": graph.app_id,
        "instructions": [
            "Annotate only the provided symbol nodes.",
            "Do not invent authority, routes, permissions, secrets, or runtime state.",
            "Use empty lists for unknown side effects, invariants, or tests.",
            "Risk is advisory and must be one of low, medium, high, unknown.",
        ],
        "allowed_fields": {
            "node_id": "required existing symbol node id",
            "purpose": "short human-readable symbol purpose",
            "domain_concepts": "list of domain nouns this symbol owns or manipulates",
            "side_effects": "list such as reads_persistence, writes_persistence, emits_event, calls_external_api",
            "invariants": "list of behavior or contract expectations",
            "risk_level": "low|medium|high|unknown",
            "test_targets": "list of likely test file paths or test symbol names",
            "confidence": "0.0-1.0 advisory confidence",
        },
        "items": items,
    }


def apply_semantic_annotations(
    *,
    graph: AppContextGraph,
    annotations: list[dict[str, Any]],
    annotated_at: datetime | None = None,
) -> AppContextGraph:
    """Return a graph copy with advisory semantic annotations merged into nodes."""
    by_node_id = {_annotation_node_id(item): item for item in annotations if _annotation_node_id(item)}
    if not by_node_id:
        return graph
    timestamp = (annotated_at or datetime.now(UTC)).isoformat()
    nodes: list[AppContextGraphNode] = []
    for node in graph.nodes:
        raw_annotation = by_node_id.get(node.node_id)
        if not raw_annotation or node.node_type != GraphNodeType.SYMBOL:
            nodes.append(node)
            continue
        metadata = dict(node.metadata or {})
        metadata["semantic_annotation"] = _normalize_semantic_annotation(
            raw_annotation,
            annotated_at=timestamp,
        )
        nodes.append(node.model_copy(update={"metadata": metadata}))
    payload = {
        "nodes": [node.model_dump(mode="json") for node in nodes],
        "edges": [edge.model_dump(mode="json") for edge in graph.edges],
    }
    return graph.model_copy(
        update={
            "nodes": nodes,
            "indexed_at": annotated_at or graph.indexed_at,
            "graph_hash": f"sha256:{_sha256_json(payload)}",
        }
    )


def context_graph_parser_status() -> dict[str, Any]:
    """Report deterministic parser availability and fallback behavior."""
    return parser_status()


def extract_file_context(*, path: str, content: str) -> ExtractedFileContext:
    safe_path = _safe_relpath(path)
    if safe_path is None:
        raise ValueError(f"unsafe path: {path!r}")
    text = str(content or "")
    language = _language_for_path(safe_path)
    context = ExtractedFileContext(
        path=safe_path,
        language=language,
        checksum=f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}",
    )
    if language in {"python", "javascript", "typescript", "tsx", "css"}:
        syntax = extract_source_syntax(language, text)
        context.parser = syntax.parser
        context.parse_status = syntax.parse_status
        context.parse_error_count = syntax.parse_error_count
        context.symbols = syntax.symbols
        context.imports = list(dict.fromkeys(item.target for item in syntax.imports))
        context.import_bindings = syntax.imports
        context.references = syntax.references
        context.local_bindings = syntax.local_bindings
        context.exports = syntax.exports
    elif language in {"yaml", "json"}:
        context.structured_metadata = _extract_structured_metadata(safe_path, text)
    return context


class _GraphDraft:
    def __init__(self, *, app_id: str, source_ref: SourceRef, artifact_version_id: str) -> None:
        self.app_id = app_id
        self.source_ref = source_ref
        self.artifact_version_id = artifact_version_id
        self.nodes: dict[str, AppContextGraphNode] = {}
        self.edges: dict[str, AppContextGraphEdge] = {}

    def add_node(
        self,
        node_id: str,
        node_type: GraphNodeType,
        *,
        label: str | None = None,
        checksum: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        existing = self.nodes.get(node_id)
        if existing is not None and node_type == GraphNodeType.FILE and existing.node_type == GraphNodeType.FILE:
            self.nodes[node_id] = existing.model_copy(update={
                "checksum": checksum or existing.checksum,
                "metadata": {**existing.metadata, **_clean_metadata(metadata)},
            })
            return
        self.nodes.setdefault(
            node_id,
            AppContextGraphNode(
                node_id=node_id,
                node_type=node_type,
                label=label,
                source_ref_id=self.source_ref.source_ref_id,
                artifact_version_id=self.artifact_version_id,
                checksum=checksum,
                stale_status=AppContextStaleStatus.CURRENT,
                metadata=_clean_metadata(metadata),
            ),
        )

    def add_edge(
        self,
        source_node_id: str,
        target_node_id: str,
        edge_type: GraphEdgeType,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        edge_id = _stable_id("edge", f"{edge_type.value}:{source_node_id}:{target_node_id}")
        self.edges.setdefault(
            edge_id,
            AppContextGraphEdge(
                edge_id=edge_id,
                edge_type=edge_type,
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                source_ref_id=self.source_ref.source_ref_id,
                artifact_version_id=self.artifact_version_id,
                stale_status=AppContextStaleStatus.CURRENT,
                metadata=_clean_metadata(metadata),
            ),
        )

    def to_graph(self, *, indexed_at: datetime) -> AppContextGraph:
        payload = {
            "nodes": [node.model_dump(mode="json") for node in self.nodes.values()],
            "edges": [edge.model_dump(mode="json") for edge in self.edges.values()],
        }
        return AppContextGraph(
            graph_id=f"graph_{self.app_id}_{self.artifact_version_id}",
            app_id=self.app_id,
            source_refs=[self.source_ref],
            indexed_at=indexed_at,
            nodes=list(self.nodes.values()),
            edges=list(self.edges.values()),
            stale_status=AppContextStaleStatus.CURRENT,
            graph_hash=f"sha256:{_sha256_json(payload)}",
        )


def _add_structural_nodes(
    *,
    graph: _GraphDraft,
    path: str,
    file_context: ExtractedFileContext,
    file_node_id: str,
    owner_node_by_key: dict[str, str],
) -> None:
    contract_path = _contract_path(path)
    parts = contract_path.split("/")
    if contract_path.startswith("modules/") and len(parts) >= 2:
        module_id = parts[1]
        if contract_path.endswith("module.yaml"):
            module_id = str(file_context.structured_metadata.get("module_id") or module_id)
        node_id = owner_node_by_key.setdefault(f"module:{module_id}", f"module:{_stable_token(module_id)}")
        graph.add_node(
            node_id,
            GraphNodeType.MODULE,
            label=module_id,
            metadata={"module_id": module_id, "path": f"modules/{module_id}"},
        )
        graph.add_edge("app", node_id, GraphEdgeType.CONTAINS, metadata={"source_path": path})
        graph.add_edge(node_id, file_node_id, GraphEdgeType.CONTAINS, metadata={"source_path": path})
        for action_id in file_context.structured_metadata.get("action_ids", []):
            action_node_id = _stable_id("action", f"{module_id}:{action_id}")
            owner_node_by_key.setdefault(f"action:{module_id}:{action_id}", action_node_id)
            graph.add_node(
                action_node_id,
                GraphNodeType.API_ENDPOINT,
                label=str(action_id),
                metadata={
                    "module_id": module_id,
                    "action_id": action_id,
                    "contract_role": "module_action",
                    "source_path": path,
                },
            )
            graph.add_edge(node_id, action_node_id, GraphEdgeType.DECLARES, metadata={"source_path": path})
        for event_ref in file_context.structured_metadata.get("event_producers", []):
            event_type = str(event_ref.get("event_type") or "").strip()
            action_id = str(event_ref.get("action_id") or "").strip()
            if not event_type or not action_id:
                continue
            action_node_id = owner_node_by_key.get(f"action:{module_id}:{action_id}") or _stable_id(
                "action",
                f"{module_id}:{action_id}",
            )
            event_node_id = _event_node_id(event_type)
            graph.add_node(
                event_node_id,
                GraphNodeType.EVENT,
                label=event_type,
                metadata={"event_type": event_type, "source_path": path, "authority": "authoritative"},
            )
            graph.add_edge(
                action_node_id,
                event_node_id,
                GraphEdgeType.PRODUCES,
                metadata={
                    "source_path": path,
                    "relationship": "event_producer",
                    "module_id": module_id,
                    "action_id": action_id,
                    "event_type": event_type,
                    "authority": "authoritative",
                },
            )
        for event_type in file_context.structured_metadata.get("event_types", []):
            event_node_id = _event_node_id(str(event_type))
            graph.add_node(
                event_node_id,
                GraphNodeType.EVENT,
                label=str(event_type),
                metadata={"event_type": event_type, "source_path": path, "authority": "authoritative"},
            )
            graph.add_edge(
                node_id,
                event_node_id,
                GraphEdgeType.DECLARES,
                metadata={
                    "source_path": path,
                    "relationship": "event_contract_declares_event",
                    "event_type": event_type,
                    "authority": "authoritative",
                },
            )
        for reaction in file_context.structured_metadata.get("reactions", []):
            event_type = str(reaction.get("event_type") or "").strip()
            if not event_type:
                continue
            event_node_id = _event_node_id(event_type)
            graph.add_node(
                event_node_id,
                GraphNodeType.EVENT,
                label=event_type,
                metadata={"event_type": event_type, "source_path": path, "authority": "authoritative"},
            )
            graph.add_edge(
                node_id,
                event_node_id,
                GraphEdgeType.CONSUMES,
                metadata={
                    "source_path": path,
                    "relationship": "event_reaction_consumer",
                    "event_type": event_type,
                    "authority": "authoritative",
                },
            )
            target_action_id = str(reaction.get("target_action_id") or "").strip()
            if target_action_id:
                target_node_id = owner_node_by_key.get(
                    f"action:{module_id}:{target_action_id}"
                ) or _stable_id("action", f"{module_id}:{target_action_id}")
                graph.add_node(
                    target_node_id,
                    GraphNodeType.API_ENDPOINT,
                    label=target_action_id,
                    metadata={
                        "module_id": module_id,
                        "action_id": target_action_id,
                        "contract_role": "module_action",
                        "source_path": path,
                        "authority": "authoritative",
                    },
                )
                graph.add_edge(
                    event_node_id,
                    target_node_id,
                    GraphEdgeType.TRIGGERS,
                    metadata={
                        "source_path": path,
                        "relationship": "reaction_target",
                        "event_type": event_type,
                        "module_id": module_id,
                        "action_id": target_action_id,
                        "authority": "authoritative",
                    },
                )
        return

    if contract_path.startswith("workflows/") and len(parts) >= 2:
        workflow_id = parts[1]
        node_id = owner_node_by_key.setdefault(f"workflow:{workflow_id}", f"workflow:{_stable_token(workflow_id)}")
        graph.add_node(
            node_id,
            GraphNodeType.WORKFLOW,
            label=workflow_id,
            metadata={"workflow_id": workflow_id, "path": f"workflows/{workflow_id}"},
        )
        graph.add_edge("app", node_id, GraphEdgeType.CONTAINS, metadata={"source_path": path})
        graph.add_edge(node_id, file_node_id, GraphEdgeType.CONTAINS, metadata={"source_path": path})
        for agent_name in file_context.structured_metadata.get("agent_names", []):
            agent_node_id = _stable_id("agent", f"{workflow_id}:{agent_name}")
            graph.add_node(
                agent_node_id,
                GraphNodeType.AGENT,
                label=str(agent_name),
                metadata={"workflow_id": workflow_id, "agent_name": agent_name, "source_path": path},
            )
            graph.add_edge(node_id, agent_node_id, GraphEdgeType.DECLARES, metadata={"source_path": path})
        for tool_name in file_context.structured_metadata.get("tool_names", []):
            tool_node_id = _stable_id("tool", f"{workflow_id}:{tool_name}")
            graph.add_node(
                tool_node_id,
                GraphNodeType.TOOL,
                label=str(tool_name),
                metadata={"workflow_id": workflow_id, "tool_name": tool_name, "source_path": path},
            )
            graph.add_edge(node_id, tool_node_id, GraphEdgeType.DECLARES, metadata={"source_path": path})
        return

    if contract_path.startswith("ui/pages/"):
        page_id = str(file_context.structured_metadata.get("page_id") or PurePosixPath(path).stem)
        node_id = owner_node_by_key.setdefault(f"page:{path}", f"page:{_stable_token(path)}")
        graph.add_node(
            node_id,
            GraphNodeType.PAGE,
            label=page_id,
            metadata={"page_id": page_id, "path": path, "source_path": path},
        )
        graph.add_edge("app", node_id, GraphEdgeType.CONTAINS, metadata={"source_path": path})
        graph.add_edge(node_id, file_node_id, GraphEdgeType.GENERATED_FROM, metadata={"source_path": path})
        for module_id in file_context.structured_metadata.get("module_ids", []):
            module_node_id = owner_node_by_key.get(f"module:{module_id}") or f"module:{_stable_token(str(module_id))}"
            graph.add_node(
                module_node_id,
                GraphNodeType.MODULE,
                label=str(module_id),
                metadata={"module_id": module_id},
            )
            graph.add_edge(node_id, module_node_id, GraphEdgeType.CALLS, metadata={"source_path": path})
        for action_ref in file_context.structured_metadata.get("action_refs", []):
            module_id = str(action_ref.get("module_id") or "").strip()
            action_id = str(action_ref.get("action_id") or "").strip()
            if not module_id or not action_id:
                continue
            action_node_id = owner_node_by_key.get(f"action:{module_id}:{action_id}") or _stable_id(
                "action",
                f"{module_id}:{action_id}",
            )
            graph.add_node(
                action_node_id,
                GraphNodeType.API_ENDPOINT,
                label=action_id,
                metadata={
                    "module_id": module_id,
                    "action_id": action_id,
                    "contract_role": "module_action",
                    "source_path": path,
                    "authority": "authoritative",
                },
            )
            graph.add_edge(
                node_id,
                action_node_id,
                GraphEdgeType.CALLS,
                metadata={
                    "source_path": path,
                    "relationship": "page_dispatches_action",
                    "module_id": module_id,
                    "action_id": action_id,
                    "authority": "authoritative",
                },
            )
        return

    if contract_path.startswith(("ui/components/", "ui/pages/custom/")):
        component_id = PurePosixPath(path).stem
        node_id = owner_node_by_key.setdefault(f"component:{path}", f"component:{_stable_token(path)}")
        graph.add_node(
            node_id,
            GraphNodeType.COMPONENT,
            label=component_id,
            metadata={"component_id": component_id, "path": path, "source_path": path},
        )
        graph.add_edge("app", node_id, GraphEdgeType.CONTAINS, metadata={"source_path": path})
        graph.add_edge(node_id, file_node_id, GraphEdgeType.GENERATED_FROM, metadata={"source_path": path})
        return

    if contract_path.startswith(("config/", "brand/")) or contract_path in {"app.json", "ui/route_manifest.json"}:
        config_id = PurePosixPath(path).stem
        node_id = owner_node_by_key.setdefault(f"config:{path}", f"config:{_stable_token(path)}")
        graph.add_node(
            node_id,
            GraphNodeType.CONFIG,
            label=config_id,
            metadata={"config_id": config_id, "path": path, "source_path": path},
        )
        graph.add_edge("app", node_id, GraphEdgeType.CONTAINS, metadata={"source_path": path})
        graph.add_edge(node_id, file_node_id, GraphEdgeType.GENERATED_FROM, metadata={"source_path": path})
        for route in file_context.structured_metadata.get("route_entries", []):
            route_path = str(route.get("route_path") or "").strip()
            if not route_path:
                continue
            route_node_id = _stable_id("route", route_path)
            graph.add_node(
                route_node_id,
                GraphNodeType.ROUTE,
                label=route_path,
                metadata={"route_path": route_path, "source_path": path, "authority": "authoritative"},
            )
            graph.add_edge(
                node_id,
                route_node_id,
                GraphEdgeType.DECLARES,
                metadata={"source_path": path, "relationship": "route_manifest_declares_route", "authority": "authoritative"},
            )
            page_id = str(route.get("page_id") or "").strip()
            if page_id:
                page_node_id = owner_node_by_key.get(f"page:{page_id}") or _stable_id("page", page_id)
                graph.add_node(
                    page_node_id,
                    GraphNodeType.PAGE,
                    label=page_id,
                    metadata={"page_id": page_id, "source_path": path, "authority": "authoritative"},
                )
                graph.add_edge(
                    route_node_id,
                    page_node_id,
                    GraphEdgeType.RENDERS,
                    metadata={
                        "source_path": path,
                        "relationship": "route_renders_page",
                        "route_path": route_path,
                        "page_id": page_id,
                        "authority": "authoritative",
                    },
                )
            for action_ref in route.get("action_refs") or []:
                module_id = str(action_ref.get("module_id") or "").strip()
                action_id = str(action_ref.get("action_id") or "").strip()
                if not module_id or not action_id:
                    continue
                action_node_id = owner_node_by_key.get(f"action:{module_id}:{action_id}") or _stable_id(
                    "action",
                    f"{module_id}:{action_id}",
                )
                graph.add_node(
                    action_node_id,
                    GraphNodeType.API_ENDPOINT,
                    label=action_id,
                    metadata={
                        "module_id": module_id,
                        "action_id": action_id,
                        "contract_role": "module_action",
                        "source_path": path,
                        "authority": "authoritative",
                    },
                )
                graph.add_edge(
                    route_node_id,
                    action_node_id,
                    GraphEdgeType.CALLS,
                    metadata={
                        "source_path": path,
                        "relationship": "route_dispatches_action",
                        "route_path": route_path,
                        "module_id": module_id,
                        "action_id": action_id,
                        "authority": "authoritative",
                    },
                )
        schema_version = str(file_context.structured_metadata.get("schema_version") or "").strip()
        if schema_version:
            schema_node_id = _stable_id("schema", schema_version)
            graph.add_node(
                schema_node_id,
                GraphNodeType.CONFIG,
                label=schema_version,
                metadata={"schema_version": schema_version, "authority": "authoritative"},
            )
            graph.add_edge(
                schema_node_id,
                node_id,
                GraphEdgeType.PROTECTED_BY,
                metadata={"source_path": path, "relationship": "schema_governs_manifest", "authority": "authoritative"},
            )
        return

    if contract_path == ".mozaiks/pack_provenance.json":
        for item in file_context.structured_metadata.get("pack_outputs", []):
            pack_id = str(item.get("pack_id") or "").strip()
            output_path = _safe_relpath(str(item.get("path") or ""))
            if not pack_id or not output_path:
                continue
            capability_node_id = owner_node_by_key.setdefault(f"capability:{pack_id}", f"capability:{_stable_token(pack_id)}")
            graph.add_node(
                capability_node_id,
                GraphNodeType.CAPABILITY,
                label=pack_id,
                metadata={"capability_id": pack_id, "source_path": path, "authority": "authoritative"},
            )
            output_file_node_id = context_graph_file_node_id(output_path)
            graph.add_node(
                output_file_node_id,
                GraphNodeType.FILE,
                label=output_path,
                metadata={
                    "path": output_path,
                    "source_path": output_path,
                    "ownership": item.get("owner"),
                    "authority": "authoritative",
                },
            )
            graph.add_edge(
                capability_node_id,
                output_file_node_id,
                GraphEdgeType.PRODUCES,
                metadata={"source_path": path, "relationship": "capability_pack_owns_output", "authority": "authoritative"},
            )
            graph.add_edge(
                output_file_node_id,
                capability_node_id,
                GraphEdgeType.GENERATED_FROM,
                metadata={
                    "source_path": path,
                    "relationship": "generated_artifact_derives_from_provenance",
                    "authority": "authoritative",
                },
            )


def _file_contract_metadata(*, path: str, file_context: ExtractedFileContext) -> dict[str, Any]:
    contract_path = _contract_path(path)
    parts = contract_path.split("/")
    if contract_path.startswith("modules/") and len(parts) >= 2:
        module_id = parts[1]
        role = _module_file_contract_role(contract_path)
        return {
            "module_id": module_id,
            "contract_role": role,
            "contract_layer": "module",
            "canonical_file_role": role,
        }
    if contract_path.startswith("workflows/") and len(parts) >= 2:
        workflow_id = parts[1]
        return {
            "workflow_id": workflow_id,
            "contract_role": _workflow_file_contract_role(contract_path),
            "contract_layer": "workflow",
        }
    if contract_path.startswith("ui/pages/"):
        return {
            "page_id": str(file_context.structured_metadata.get("page_id") or PurePosixPath(path).stem),
            "contract_role": "page_schema" if path.endswith((".yaml", ".yml", ".json")) else "page_component",
            "contract_layer": "app_ui",
        }
    if contract_path.startswith(("ui/components/", "ui/pages/custom/")):
        return {
            "component_id": PurePosixPath(path).stem,
            "contract_role": "ui_component",
            "contract_layer": "app_ui",
        }
    if contract_path.startswith(("config/", "brand/")) or contract_path in {"app.json", "ui/route_manifest.json"}:
        return {
            "config_id": PurePosixPath(path).stem,
            "contract_role": "app_config",
            "contract_layer": "app_config",
        }
    return {}


def _symbol_contract_metadata(
    *,
    path: str,
    symbol: ExtractedSymbol,
    file_context: ExtractedFileContext,
    module_action_ids_by_module: dict[str, list[str]],
) -> dict[str, Any]:
    file_metadata = _file_contract_metadata(path=path, file_context=file_context)
    role = str(file_metadata.get("contract_role") or "")
    metadata = {
        key: value
        for key, value in file_metadata.items()
        if key in {"module_id", "workflow_id", "page_id", "component_id", "contract_layer"}
    }
    if not role:
        return metadata

    symbol_name = symbol.name
    module_id = metadata.get("module_id")
    if role == "module_handler":
        action_ids = module_action_ids_by_module.get(str(module_id), [])
        action_id = symbol_name if symbol_name in action_ids else None
        metadata["contract_role"] = "module_action_handler" if action_id else "module_handler_symbol"
        if action_id:
            metadata["action_id"] = action_id
        return metadata
    if role == "module_service":
        metadata["contract_role"] = "module_service_symbol"
        return metadata
    if role == "module_repo":
        metadata["contract_role"] = "module_repo_symbol"
        metadata["side_effect_surface"] = "persistence"
        return metadata
    if role == "module_policy":
        metadata["contract_role"] = "module_policy_symbol"
        metadata["side_effect_surface"] = "authorization_scope"
        return metadata
    if role == "module_schemas":
        metadata["contract_role"] = "module_schema_symbol"
        return metadata
    if role in {"page_component", "ui_component"}:
        metadata["contract_role"] = role
        return metadata
    if role.startswith("workflow_"):
        metadata["contract_role"] = "workflow_symbol"
        return metadata
    if module_id:
        metadata["contract_role"] = "module_symbol"
    return metadata


def _module_file_contract_role(path: str) -> str:
    name = PurePosixPath(path).name
    if name == "module.yaml":
        return "module_contract"
    if path.endswith("/backend/handler.py"):
        return "module_handler"
    if path.endswith("/backend/service.py"):
        return "module_service"
    if path.endswith("/backend/repo.py"):
        return "module_repo"
    if path.endswith("/backend/policy.py"):
        return "module_policy"
    if path.endswith("/backend/schemas.py"):
        return "module_schemas"
    if path.endswith("/backend/settings.py"):
        return "module_settings"
    if path.endswith("/backend/admin.py"):
        return "module_admin"
    if "/contracts/" in path:
        return "module_companion_contract"
    if path.endswith("runtime_extensions.yaml"):
        return "module_runtime_extensions"
    return "module_support"


def _workflow_file_contract_role(path: str) -> str:
    name = PurePosixPath(path).name
    if name == "orchestrator.yaml":
        return "workflow_orchestrator_contract"
    if name == "agents.yaml":
        return "workflow_agent_contract"
    if name == "transition_graph.yaml":
        return "workflow_transition_graph_contract"
    if name == "structured_outputs.yaml":
        return "workflow_output_contract"
    if name == "tools.yaml":
        return "workflow_tool_contract"
    if name == "middleware.yaml":
        return "workflow_middleware_contract"
    if name == "context_variables.yaml":
        return "workflow_context_contract"
    if "/tools/" in path:
        return "workflow_tool_code"
    if "/ui/" in path:
        return "workflow_ui_code"
    return "workflow_support"


def _module_action_ids_by_module(extracted_by_path: dict[str, ExtractedFileContext]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for path, file_context in extracted_by_path.items():
        contract_path = _contract_path(path)
        parts = contract_path.split("/")
        if not contract_path.startswith("modules/") or len(parts) < 2 or not contract_path.endswith("module.yaml"):
            continue
        module_id = str(file_context.structured_metadata.get("module_id") or parts[1])
        out[module_id] = list(file_context.structured_metadata.get("action_ids") or [])
    return out


def _extract_structured_metadata(path: str, content: str) -> dict[str, Any]:
    try:
        data = json.loads(content) if path.endswith(".json") else yaml.safe_load(content)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}

    metadata: dict[str, Any] = {}
    contract_path = _contract_path(path)
    if path.endswith("module.yaml"):
        _raw_module = data.get("module")
        module_block: dict[str, Any] = _raw_module if isinstance(_raw_module, dict) else {}
        metadata["module_id"] = _first_text(data, "id", "module_id", "name") or _first_text(
            module_block,
            "id",
            "module_id",
            "name",
        )
        metadata["action_ids"] = _extract_action_ids(data)
        metadata["event_producers"] = _extract_action_event_producers(data)
    elif contract_path.startswith("modules/") and contract_path.endswith("contracts/events.yaml"):
        metadata["event_types"] = _extract_event_types(data)
    elif contract_path.startswith("modules/") and contract_path.endswith("contracts/reactions.yaml"):
        metadata["reactions"] = _extract_reactions(data)
    elif path.endswith("agents.yaml"):
        metadata["agent_names"] = _extract_named_items(data.get("agents"))
    elif path.endswith("tools.yaml"):
        metadata["tool_names"] = _extract_tool_names(data.get("tools"))
    elif contract_path == ".mozaiks/pack_provenance.json":
        metadata["pack_outputs"] = _extract_pack_outputs(data)
    elif contract_path == "ui/route_manifest.json":
        metadata["route_entries"] = _extract_route_entries(data)
        metadata["schema_version"] = _first_text(data, "schema_version", "schemaVersion")
    elif contract_path.startswith("ui/pages/"):
        metadata["page_id"] = _first_text(data, "id", "page_id", "name", "title")
        metadata["module_ids"] = _extract_module_refs(data)
        metadata["action_refs"] = _extract_action_refs(data)
        metadata["schema_version"] = _first_text(data, "schema_version", "schemaVersion")
    else:
        metadata["schema_version"] = _first_text(data, "schema_version", "schemaVersion")
    return {key: value for key, value in metadata.items() if value not in (None, "", [])}


def _extract_action_ids(data: dict[str, Any]) -> list[str]:
    actions = data.get("actions")
    if not isinstance(actions, list):
        return []
    ids: list[str] = []
    for item in actions:
        if isinstance(item, str):
            candidate = item
        elif isinstance(item, dict):
            candidate = _first_text(item, "id", "name", "action_id")  # type: ignore[assignment]
        else:
            candidate = None
        if candidate and candidate not in ids:
            ids.append(candidate)
    return ids


def _extract_action_event_producers(data: dict[str, Any]) -> list[dict[str, str]]:
    actions = data.get("actions")
    if not isinstance(actions, list):
        return []
    out: list[dict[str, str]] = []
    for item in actions:
        if not isinstance(item, dict):
            continue
        action_id = _first_text(item, "id", "name", "action_id")
        if not action_id:
            continue
        for key in ("emits", "emitted_events", "produces_events", "events"):
            for event_type in _string_values(item.get(key)):
                candidate = {"action_id": action_id, "event_type": event_type}
                if candidate not in out:
                    out.append(candidate)
    return out


def _extract_event_types(data: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("events", "event_types", "publishes", "produces"):
        for item in _string_values(data.get(key)):
            if item not in values:
                values.append(item)
    return values


def _extract_reactions(data: dict[str, Any]) -> list[dict[str, str]]:
    reactions = data.get("reactions") or data.get("handlers") or data.get("consumers")
    if not isinstance(reactions, list):
        return []
    out: list[dict[str, str]] = []
    for item in reactions:
        if not isinstance(item, dict):
            continue
        event_type = _first_text(item, "event", "event_type", "consumes", "on")
        target_action = _first_text(item, "action", "action_id", "target_action", "target_action_id")
        target = item.get("target")
        if isinstance(target, dict):
            target_action = target_action or _first_text(target, "action", "action_id", "target_action_id")
        if event_type:
            candidate = {"event_type": event_type}
            if target_action:
                candidate["target_action_id"] = target_action
            if candidate not in out:
                out.append(candidate)
    return out


def _extract_route_entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw_routes = data.get("routes")
    if isinstance(raw_routes, dict):
        iterable = [{"path": key, **(value if isinstance(value, dict) else {})} for key, value in raw_routes.items()]
    elif isinstance(raw_routes, list):
        iterable = raw_routes
    else:
        iterable = []
    out: list[dict[str, Any]] = []
    for item in iterable:
        if not isinstance(item, dict):
            continue
        route_path = _first_text(item, "path", "route", "route_path", "href")
        if not route_path:
            continue
        entry: dict[str, Any] = {
            "route_path": route_path,
            "page_id": _first_text(item, "page", "page_id", "pageId", "component"),
            "action_refs": _extract_action_refs(item),
        }
        if entry not in out:
            out.append(entry)
    return out


def _extract_action_refs(data: dict[str, Any]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            endpoint = _first_text(value, "endpoint", "url", "href")
            _append_action_ref(refs, _action_ref_from_endpoint(endpoint))
            module_id = _first_text(value, "module_id", "moduleId")
            action_id = _first_text(value, "action_id", "actionId", "action")
            if module_id and action_id:
                _append_action_ref(refs, {"module_id": module_id, "action_id": action_id})
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            _append_action_ref(refs, _action_ref_from_endpoint(value))

    visit(data)
    return refs


def _extract_pack_outputs(data: dict[str, Any]) -> list[dict[str, str]]:
    raw_items = data.get("materialized_owned_files") or data.get("owned_files") or data.get("files")
    if not isinstance(raw_items, list):
        return []
    default_pack_id = _first_text(data, "pack_id", "capability_id", "context_id")
    out: list[dict[str, str]] = []
    for item in raw_items:
        if isinstance(item, str):
            path = item
            pack_id = default_pack_id
            owner = default_pack_id
        elif isinstance(item, dict):
            path = _first_text(item, "path", "file", "output_path") or ""
            pack_id = _first_text(item, "pack_id", "capability_id", "owner") or default_pack_id
            owner = _first_text(item, "owner", "pack_id", "capability_id") or pack_id
        else:
            continue
        if path and pack_id:
            candidate = {"path": path, "pack_id": pack_id}
            if owner:
                candidate["owner"] = owner
            if candidate not in out:
                out.append(candidate)
    return out


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, dict):
                name = _first_text(item, "type", "event_type", "event", "name", "id")
                if name:
                    out.append(name)
        return dedupe_strings(out)
    if isinstance(value, dict):
        return dedupe_strings(str(key) for key in value if str(key).strip())
    return []


def _append_action_ref(refs: list[dict[str, str]], candidate: dict[str, str] | None) -> None:
    if not candidate:
        return
    module_id = str(candidate.get("module_id") or "").strip()
    action_id = str(candidate.get("action_id") or "").strip()
    if not module_id or not action_id:
        return
    normalized = {"module_id": module_id, "action_id": action_id}
    if normalized not in refs:
        refs.append(normalized)


def _action_ref_from_endpoint(value: str | None) -> dict[str, str] | None:
    if not value:
        return None
    match = re.search(r"/api/modules/([^/\s]+)/([^/?#\s]+)", value)
    if not match:
        return None
    return {"module_id": match.group(1), "action_id": match.group(2)}


def _extract_named_items(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(key) for key in value.keys() if str(key).strip()]
    if isinstance(value, list):
        names = []
        for item in value:
            if isinstance(item, dict):
                name = _first_text(item, "name", "id")
                if name and name not in names:
                    names.append(name)
        return names
    return []


def _extract_tool_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = _first_text(item, "id", "name", "function") or _first_text(item, "file")
        if name and name not in names:
            names.append(name)
    return names


def _extract_module_refs(data: dict[str, Any]) -> list[str]:
    refs: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            module_id = _first_text(value, "module_id", "module", "moduleId")
            if module_id and module_id not in refs:
                refs.append(module_id)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(data)
    return refs


_ResolvedImport = tuple[SourceImport, str | None, str | None]


def _resolve_imports(
    path: str,
    context: ExtractedFileContext,
    file_nodes: dict[str, str],
    symbol_nodes: dict[tuple[str, str], list[str]],
) -> list[_ResolvedImport]:
    resolved: list[_ResolvedImport] = []
    for imported in context.import_bindings:
        member = imported.member
        if context.language == "python":
            target = _python_import_path(path, imported.target, file_nodes)
            if member and not (target and symbol_nodes.get((target, member))):
                separator = "" if imported.target.endswith(".") else "."
                submodule = _python_import_path(path, f"{imported.target}{separator}{member}", file_nodes)
                if submodule:
                    target, member = submodule, None
        else:
            target = _relative_import_path(path, imported.target, file_nodes)
        resolved.append((imported, target, member))
    return resolved


def _python_import_path(path: str, target: str, file_nodes: dict[str, str]) -> str | None:
    name = target.lstrip(".")
    relative = len(target) - len(name)
    module_path = name.replace(".", "/")
    if relative:
        directory = PurePosixPath(path).parent
        if relative > len(directory.parts) + 1:
            return None
        for _ in range(relative - 1):
            directory = directory.parent
        roots = [directory]
    else:
        roots = [PurePosixPath(root) for root in (".", "src", "app")]
    candidates = []
    for root in roots:
        base = str(root / module_path)
        if name and f"{base}.py" in file_nodes:
            candidates.append(f"{base}.py")
        package = str(PurePosixPath(base) / "__init__.py")
        if package in file_nodes:
            candidates.append(package)
    return _unique_node(candidates)


def _relative_import_path(path: str, target: str, file_nodes: dict[str, str]) -> str | None:
    if not target.startswith(("./", "../")):
        return None
    parts = list(PurePosixPath(path).parent.parts)
    for part in PurePosixPath(target).parts:
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    base = "/".join(parts)
    if base in file_nodes:
        return base
    return _unique_node([f"{base}{suffix}" for suffix in _RELATIVE_IMPORT_EXTENSIONS if f"{base}{suffix}" in file_nodes])


def _reference_target_node_id(
    path: str,
    reference: ExtractedReference,
    bindings_by_scope: dict[tuple[str | None, str], list[_ResolvedImport]],
    local_bindings: set[tuple[str | None, str]],
    symbol_nodes: dict[tuple[str, str], list[str]],
    imported_nodes: dict[tuple[str, str], list[str]],
    graph: _GraphDraft,
) -> str | None:
    root, _, member_path = reference.target.partition(".")
    scope = reference.caller
    while scope:
        scope_id = _unique_node(symbol_nodes.get((path, scope)))
        kind = graph.nodes[scope_id].metadata.get("symbol_kind") if scope_id else None
        if kind == "class":
            if root in {"self", "this", "cls"} and member_path:
                return _unique_node(symbol_nodes.get((path, f"{scope}.{member_path}")))
        else:
            if (scope, root) in local_bindings and root not in {"self", "this", "cls"}:
                return None
            candidate = f"{scope}.{reference.target}"
            nodes = symbol_nodes.get((path, candidate))
            bindings = bindings_by_scope.get((scope, root))
            if nodes or bindings:
                return _resolve_name(nodes, bindings, member_path, imported_nodes)
        scope = scope.rpartition(".")[0]
    if (None, root) in local_bindings:
        return None
    return _resolve_name(
        symbol_nodes.get((path, reference.target)), bindings_by_scope.get((None, root)), member_path, imported_nodes,
    )


def _resolve_name(
    nodes: list[str] | None,
    bindings: list[_ResolvedImport] | None,
    member_path: str,
    symbol_nodes: dict[tuple[str, str], list[str]],
) -> str | None:
    if bindings:
        return None if nodes else _resolve_bound_reference(bindings, member_path, symbol_nodes)
    return _unique_node(nodes)


def _resolve_bound_reference(
    bindings: list[_ResolvedImport],
    member_path: str,
    symbol_nodes: dict[tuple[str, str], list[str]],
) -> str | None:
    candidates = []
    unresolved = False
    for binding, path, member in bindings:
        tail = member_path
        if binding.module_tail:
            if not tail.startswith(f"{binding.module_tail}."):
                continue
            tail = tail[len(binding.module_tail) + 1:]
        name = ".".join(part for part in (member, tail) if part)
        if not name and binding.commonjs:
            name = "default"
        nodes = symbol_nodes.get((path, name)) if path and name else None
        if nodes:
            candidates.extend(nodes)
        else:
            unresolved = True
    return None if unresolved else _unique_node(candidates)


def _unique_node(candidates: list[str] | None) -> str | None:
    unique = set(candidates or [])
    return next(iter(unique)) if len(unique) == 1 else None


def _safe_file_map(file_map: dict[str, str]) -> dict[str, str]:
    safe: dict[str, str] = {}
    for raw_path, content in (file_map or {}).items():
        path = _safe_relpath(raw_path)
        if path:
            safe[path] = str(content)
    return safe


def _safe_relpath(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    normalized = raw.replace("\\", "/").strip().strip("/")
    if not normalized:
        return None
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        return None
    return str(path)


def _contract_path(path: str) -> str:
    return path[4:] if path.startswith("app/") else path


def _language_for_path(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix in {".js", ".jsx"}:
        return "javascript"
    if suffix == ".ts":
        return "typescript"
    if suffix == ".tsx":
        return "tsx"
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    if suffix == ".json":
        return "json"
    if suffix == ".css":
        return "css"
    if suffix == ".md":
        return "markdown"
    return "text"


def context_graph_file_node_id(path: str) -> str:
    """Identify a file by its normalized, exact snapshot-relative path."""
    normalized = safe_scan_relpath(path)
    if normalized is None:
        raise ValueError(f"unsafe path: {path!r}")
    return _dependency_id("file", normalized)


def _symbol_node_id(path: str, name: str, kind: str, occurrence: int) -> str:
    return _dependency_id("symbol", f"{path}:{kind}:{name}:{occurrence}")


def _dependency_id(prefix: str, raw: str) -> str:
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{_stable_token(raw)}_{digest}"


def _event_node_id(event_type: str) -> str:
    return _stable_id("event", event_type)


def _stable_id(prefix: str, raw: str) -> str:
    return f"{prefix}:{_stable_token(raw)}"


def _stable_token(raw: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_]+", "_", str(raw or "").strip().lower()).strip("_")
    if clean:
        if len(clean) <= 96:
            return clean
        digest = hashlib.sha256(str(raw).encode("utf-8")).hexdigest()[:12]
        return f"{clean[:83]}_{digest}"
    return hashlib.sha256(str(raw).encode("utf-8")).hexdigest()[:24]


def _file_map_checksum(file_map: dict[str, str]) -> str:
    return f"sha256:{_sha256_json({path: file_map[path] for path in sorted(file_map)})}"


def _sha256_json(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _first_text(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None




def _clean_metadata(value: dict[str, Any] | None) -> dict[str, Any]:
    return {
        key: item
        for key, item in (value or {}).items()
        if item not in (None, "", [])
    }


def _semantic_candidate_sort_key(node: AppContextGraphNode) -> tuple[int, str, str]:
    metadata = node.metadata or {}
    role = str(metadata.get("contract_role") or "")
    priority = 20
    if role == "module_action_handler":
        priority = 0
    elif role in {"module_service_symbol", "module_repo_symbol"}:
        priority = 1
    elif role in {"page_component", "ui_component"}:
        priority = 2
    elif role:
        priority = 5
    return (priority, str(metadata.get("path") or ""), str(metadata.get("qualified_name") or node.label or ""))


def _semantic_annotation_item(
    *,
    node: AppContextGraphNode,
    graph: AppContextGraph,
    nodes_by_id: dict[str, AppContextGraphNode],
) -> dict[str, Any]:
    metadata = node.metadata or {}
    inbound, outbound = _symbol_relationship_hints(node=node, graph=graph, nodes_by_id=nodes_by_id)
    return {
        "node_id": node.node_id,
        "label": node.label,
        "path": metadata.get("path"),
        "qualified_name": metadata.get("qualified_name") or node.label,
        "symbol_kind": metadata.get("symbol_kind"),
        "line": metadata.get("line"),
        "end_line": metadata.get("end_line"),
        "contract_role": metadata.get("contract_role"),
        "contract_layer": metadata.get("contract_layer"),
        "module_id": metadata.get("module_id"),
        "action_id": metadata.get("action_id"),
        "page_id": metadata.get("page_id"),
        "side_effect_surface": metadata.get("side_effect_surface"),
        "inbound_relationships": inbound[:8],
        "outbound_relationships": outbound[:12],
    }


def _symbol_relationship_hints(
    *,
    node: AppContextGraphNode,
    graph: AppContextGraph,
    nodes_by_id: dict[str, AppContextGraphNode],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inbound: list[dict[str, Any]] = []
    outbound: list[dict[str, Any]] = []
    for edge in graph.edges:
        if edge.target_node_id == node.node_id:
            inbound.append(_relationship_hint(edge=edge, opposite=nodes_by_id.get(edge.source_node_id)))
        elif edge.source_node_id == node.node_id:
            outbound.append(_relationship_hint(edge=edge, opposite=nodes_by_id.get(edge.target_node_id)))
    return inbound, outbound


def _relationship_hint(
    *,
    edge: AppContextGraphEdge,
    opposite: AppContextGraphNode | None,
) -> dict[str, Any]:
    return {
        "edge_type": edge.edge_type.value,
        "node_id": opposite.node_id if opposite else None,
        "node_type": opposite.node_type.value if opposite else None,
        "label": opposite.label if opposite else None,
        "metadata": dict(edge.metadata or {}),
    }


def _symbol_path(node: AppContextGraphNode) -> str | None:
    path = (node.metadata or {}).get("path")
    return str(path) if path else None


def _annotation_node_id(item: dict[str, Any]) -> str | None:
    node_id = item.get("node_id") if isinstance(item, dict) else None
    return str(node_id).strip() if node_id else None


def _normalize_semantic_annotation(item: dict[str, Any], *, annotated_at: str) -> dict[str, Any]:
    risk = str(item.get("risk_level") or "unknown").lower()
    if risk not in {"low", "medium", "high", "unknown"}:
        risk = "unknown"
    confidence = item.get("confidence", 0.0)
    try:
        confidence_value = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence_value = 0.0
    return _clean_metadata(
        {
            "schema_version": _SEMANTIC_ANNOTATION_SCHEMA_VERSION,
            "source": "llm_advisory",
            "annotated_at": annotated_at,
            "purpose": _short_text(item.get("purpose"), max_length=240),
            "domain_concepts": _string_list(item.get("domain_concepts"), max_items=8),
            "side_effects": _string_list(item.get("side_effects"), max_items=8),
            "invariants": _string_list(item.get("invariants"), max_items=8),
            "risk_level": risk,
            "test_targets": _string_list(item.get("test_targets"), max_items=8),
            "confidence": confidence_value,
        }
    )


def _short_text(value: Any, *, max_length: int) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    return text[:max_length]


def _string_list(value: Any, *, max_items: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = _short_text(item, max_length=120)
        if text and text not in out:
            out.append(text)
        if len(out) >= max_items:
            break
    return out


__all__ = [
    "ExtractedFileContext",
    "ExtractedReference",
    "ExtractedSymbol",
    "apply_semantic_annotations",
    "build_context_graph_from_file_map",
    "build_semantic_annotation_request",
    "context_graph_file_node_id",
    "context_graph_parser_status",
    "extract_file_context",
    "merge_context_graphs",
]

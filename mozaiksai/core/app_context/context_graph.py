"""Deterministic Context Graph builders for app and workflow artifacts.

This module is deliberately storage-free. It turns artifact file maps and
existing app-context graph snapshots into provenance-rich `AppContextGraph`
objects that control-plane tools can query. Graph database adapters may mirror
these snapshots later, but this builder remains the canonical in-repo graph
construction path.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

import yaml

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

_JS_IMPORT_RE = re.compile(r"""(?:import\s+.*?\s+from\s+|require\()\s*['"]([^'"]+)['"]""")
_JS_FUNCTION_RE = re.compile(
    r"""(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)|(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>|(?:export\s+)?class\s+([A-Za-z_$][\w$]*)"""
)
_PY_IMPORT_RE = re.compile(r"""^\s*(?:from\s+([.\w]+)\s+import|import\s+([.\w]+))""", re.MULTILINE)
_RELATIVE_IMPORT_EXTENSIONS = (
    "",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".json",
    ".yaml",
    ".yml",
    "/index.js",
    "/index.jsx",
    "/index.ts",
    "/index.tsx",
)
_TREE_SITTER_LANGUAGE_MODULES = {
    "python": "tree_sitter_python",
    "javascript": "tree_sitter_javascript",
}
_TREE_SITTER_LANGUAGES: dict[str, Any | None] = {}
_JS_CALL_RE = re.compile(r"""\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)\s*\(""")
_NOISE_CALL_TARGETS = {
    "bool",
    "dict",
    "float",
    "int",
    "len",
    "list",
    "print",
    "set",
    "str",
    "tuple",
    "super",
    "return",
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "function",
    "console.log",
    "useState",
    "useEffect",
}
_SEMANTIC_ANNOTATION_SCHEMA_VERSION = "mozaiks.context_graph.semantic_annotations.v1"


@dataclass
class ExtractedSymbol:
    name: str
    kind: str
    line: int | None = None
    end_line: int | None = None
    qualified_name: str | None = None
    parent: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractedReference:
    target: str
    kind: str
    line: int | None = None
    caller: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractedFileContext:
    path: str
    language: str
    checksum: str
    symbols: list[ExtractedSymbol] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    references: list[ExtractedReference] = field(default_factory=list)
    structured_metadata: dict[str, Any] = field(default_factory=dict)


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

    extracted_by_path = {
        path: extract_file_context(path=path, content=content)
        for path, content in sorted(_safe_file_map(file_map).items())
    }
    module_action_ids_by_module = _module_action_ids_by_module(extracted_by_path)
    file_node_by_path: dict[str, str] = {}
    owner_node_by_key: dict[str, str] = {}
    symbol_node_by_lookup: dict[tuple[str, str], str] = {}

    for path, file_context in extracted_by_path.items():
        file_node_id = _file_node_id(path)
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
                "size_bytes": len(str(file_map.get(path, "")).encode("utf-8")),
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
        for symbol in file_context.symbols:
            symbol_key = symbol.qualified_name or symbol.name
            symbol_node_id = _symbol_node_id(path, symbol_key, symbol.kind)
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
            symbol_node_by_lookup[(path, symbol.name)] = symbol_node_id
            if symbol.qualified_name:
                symbol_node_by_lookup[(path, symbol.qualified_name)] = symbol_node_id
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
        for import_target in file_context.imports:
            resolved_path = _resolve_import_path(path=path, import_target=import_target, file_map=file_map)
            if resolved_path and resolved_path in file_node_by_path:
                graph.add_edge(
                    file_node_id,
                    file_node_by_path[resolved_path],
                    GraphEdgeType.IMPORTS,
                    metadata={"source_path": path, "import_target": import_target},
                )
            else:
                unknown_node_id = _stable_id("unknown_import", f"{path}:{import_target}")
                graph.add_node(
                    unknown_node_id,
                    GraphNodeType.UNKNOWN,
                    label=import_target,
                    metadata={"path": path, "source_path": path, "import_target": import_target},
                )
                graph.add_edge(
                    file_node_id,
                    unknown_node_id,
                    GraphEdgeType.IMPORTS,
                    metadata={"source_path": path, "import_target": import_target, "resolved": False},
                )

    for path, file_context in extracted_by_path.items():
        file_node_id = file_node_by_path[path]
        imported_paths = _resolved_import_paths(path=path, file_context=file_context, file_map=file_map)
        for reference in file_context.references[:120]:
            if _is_noise_reference(reference):
                continue
            source_node_id = _reference_source_node_id(
                path=path,
                reference=reference,
                fallback_file_node_id=file_node_id,
                symbol_node_by_lookup=symbol_node_by_lookup,
            )
            target_node_id = _reference_target_node_id(
                path=path,
                reference=reference,
                imported_paths=imported_paths,
                symbol_node_by_lookup=symbol_node_by_lookup,
            )
            if target_node_id is None:
                target_node_id = _stable_id("unknown_call", f"{path}:{reference.target}")
                graph.add_node(
                    target_node_id,
                    GraphNodeType.UNKNOWN,
                    label=reference.target,
                    metadata={
                        "path": path,
                        "source_path": path,
                        "reference_kind": reference.kind,
                        "line": reference.line,
                        "resolved": False,
                    },
                )
            graph.add_edge(
                source_node_id,
                target_node_id,
                GraphEdgeType.CALLS if reference.kind == "call" else GraphEdgeType.REFERENCES,
                metadata={
                    "source_path": path,
                    "reference_target": reference.target,
                    "reference_kind": reference.kind,
                    "line": reference.line,
                    "caller": reference.caller,
                },
            )

    return graph.to_graph(indexed_at=resolved_indexed_at)


def merge_context_graphs(
    *,
    app_id: str,
    graph_id: str,
    graphs: Iterable[AppContextGraph | None],
    indexed_at: datetime | None = None,
) -> AppContextGraph:
    """Merge graph snapshots without mutating any input graph."""
    source_refs: dict[str, SourceRef] = {}
    nodes: dict[str, AppContextGraphNode] = {}
    edges: dict[str, AppContextGraphEdge] = {}
    stale_status = AppContextStaleStatus.CURRENT

    for graph in graphs:
        if graph is None:
            continue
        for ref in graph.source_refs:
            source_refs.setdefault(ref.source_ref_id, ref)
        for node in graph.nodes:
            nodes.setdefault(node.node_id, node)
        for edge in graph.edges:
            key = edge.edge_id or _stable_id(
                "edge",
                f"{edge.edge_type.value}:{edge.source_node_id}:{edge.target_node_id}",
            )
            edges.setdefault(key, edge.model_copy(update={"edge_id": key}))
        if graph.stale_status != AppContextStaleStatus.CURRENT:
            stale_status = graph.stale_status

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
    tree_sitter_available = _module_available("tree_sitter")
    languages: dict[str, dict[str, Any]] = {}
    for language in ("python", "javascript"):
        module_name = _TREE_SITTER_LANGUAGE_MODULES.get(language)
        module_available = bool(module_name and _module_available(module_name))
        parser_available = bool(tree_sitter_available and module_available and _tree_sitter_parser(language))
        languages[language] = {
            "tree_sitter_module": module_name,
            "tree_sitter_available": parser_available,
            "fallback_parser": "python_ast" if language == "python" else "regex",
            "active_parser": "tree_sitter" if parser_available else ("python_ast" if language == "python" else "regex"),
        }
    languages["typescript"] = {
        "tree_sitter_module": None,
        "tree_sitter_available": False,
        "fallback_parser": "javascript_regex",
        "active_parser": "javascript_tree_sitter_or_regex",
    }
    languages["yaml"] = {
        "tree_sitter_module": None,
        "tree_sitter_available": False,
        "active_parser": "structured_yaml",
    }
    languages["json"] = {
        "tree_sitter_module": None,
        "tree_sitter_available": False,
        "active_parser": "structured_json",
    }
    return {
        "tree_sitter_package_available": tree_sitter_available,
        "languages": languages,
    }


def extract_file_context(*, path: str, content: str) -> ExtractedFileContext:
    safe_path = _safe_relpath(path)
    if safe_path is None:
        raise ValueError(f"unsafe path: {path!r}")
    text = str(content or "")
    language = _language_for_path(safe_path)
    symbols: list[ExtractedSymbol] = []
    imports: list[str] = []
    references: list[ExtractedReference] = []
    structured_metadata: dict[str, Any] = {}

    if language == "python":
        symbols, imports, references = _extract_python(text)
    elif language in {"javascript", "typescript"}:
        symbols, imports, references = _extract_javascript_like(text)
    elif language in {"yaml", "json"}:
        structured_metadata = _extract_structured_metadata(safe_path, text)

    return ExtractedFileContext(
        path=safe_path,
        language=language,
        checksum=f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}",
        symbols=symbols,
        imports=imports,
        references=references,
        structured_metadata=structured_metadata,
    )


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


def _extract_python(content: str) -> tuple[list[ExtractedSymbol], list[str], list[ExtractedReference]]:
    symbols = _extract_tree_sitter_symbols(language="python", content=content)
    references: list[ExtractedReference] = []
    if symbols is None:
        try:
            visitor = _PythonAstVisitor()
            visitor.visit(ast.parse(content))
            symbols = visitor.symbols
            references = visitor.references
        except SyntaxError:
            symbols = []
    else:
        references = _extract_python_references_with_ast(content)
    imports = [
        match[0] or match[1]
        for match in _PY_IMPORT_RE.findall(content)
        if (match[0] or match[1])
    ]
    return symbols, _dedupe(imports), references


def _extract_javascript_like(content: str) -> tuple[list[ExtractedSymbol], list[str], list[ExtractedReference]]:
    symbols = _extract_tree_sitter_symbols(language="javascript", content=content)
    if symbols is None:
        symbols = []
        for match in _JS_FUNCTION_RE.finditer(content):
            name = next((group for group in match.groups() if group), None)
            if name:
                symbols.append(
                    ExtractedSymbol(
                        name=name,
                        kind="class" if match.group(3) else "function",
                        line=content.count("\n", 0, match.start()) + 1,
                        qualified_name=name,
                        metadata={"parser": "regex"},
                    )
                )
    imports = [match.strip() for match in _JS_IMPORT_RE.findall(content) if match.strip()]
    return symbols, _dedupe(imports), _extract_javascript_references(content)


class _PythonAstVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.symbols: list[ExtractedSymbol] = []
        self.references: list[ExtractedReference] = []
        self._scope_stack: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        parent = self._current_scope()
        qualified_name = _qualified_symbol_name(parent=parent, name=node.name)
        self.symbols.append(
            ExtractedSymbol(
                name=node.name,
                kind="class",
                line=node.lineno,
                end_line=getattr(node, "end_lineno", None),
                qualified_name=qualified_name,
                parent=parent,
                metadata={
                    "parser": "python_ast",
                    "decorators": _python_decorator_names(node.decorator_list),
                },
            )
        )
        self._scope_stack.append(node.name)
        self.generic_visit(node)
        self._scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self._visit_function(node=node, async_function=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
        self._visit_function(node=node, async_function=True)

    def visit_Call(self, node: ast.Call) -> Any:
        target = _python_call_target(node.func)
        if target:
            self.references.append(
                ExtractedReference(
                    target=target,
                    kind="call",
                    line=getattr(node, "lineno", None),
                    caller=self._current_scope(),
                    metadata={"parser": "python_ast"},
                )
            )
        self.generic_visit(node)

    def _visit_function(self, *, node: ast.FunctionDef | ast.AsyncFunctionDef, async_function: bool) -> None:
        parent = self._current_scope()
        qualified_name = _qualified_symbol_name(parent=parent, name=node.name)
        self.symbols.append(
            ExtractedSymbol(
                name=node.name,
                kind="method" if parent else "function",
                line=node.lineno,
                end_line=getattr(node, "end_lineno", None),
                qualified_name=qualified_name,
                parent=parent,
                metadata={
                    "parser": "python_ast",
                    "async": async_function,
                    "decorators": _python_decorator_names(node.decorator_list),
                },
            )
        )
        self._scope_stack.append(node.name)
        self.generic_visit(node)
        self._scope_stack.pop()

    def _current_scope(self) -> str | None:
        return ".".join(self._scope_stack) if self._scope_stack else None


def _extract_python_references_with_ast(content: str) -> list[ExtractedReference]:
    try:
        visitor = _PythonAstVisitor()
        visitor.visit(ast.parse(content))
        return visitor.references
    except SyntaxError:
        return []


def _python_decorator_names(decorators: list[ast.expr]) -> list[str]:
    names: list[str] = []
    for decorator in decorators:
        name = _python_call_target(decorator)
        if name and name not in names:
            names.append(name)
    return names


def _python_call_target(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _python_call_target(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return _python_call_target(node.func)
    return None


def _qualified_symbol_name(*, parent: str | None, name: str) -> str:
    return f"{parent}.{name}" if parent else name


def _extract_javascript_references(content: str) -> list[ExtractedReference]:
    references: list[ExtractedReference] = []
    for match in _JS_CALL_RE.finditer(content):
        target = match.group(1)
        if not target or target in _NOISE_CALL_TARGETS:
            continue
        references.append(
            ExtractedReference(
                target=target,
                kind="call",
                line=content.count("\n", 0, match.start()) + 1,
                metadata={"parser": "regex"},
            )
        )
    return references


def _extract_tree_sitter_symbols(*, language: str, content: str) -> list[ExtractedSymbol] | None:
    parser = _tree_sitter_parser(language)
    if parser is None:
        return None
    try:
        tree = parser.parse(content.encode("utf-8"))
    except Exception:
        return None

    symbols: list[ExtractedSymbol] = []

    def visit(node: Any) -> None:
        symbol = _tree_sitter_symbol_for_node(language=language, node=node)
        if symbol is not None:
            symbols.append(symbol)
        for child in getattr(node, "children", []) or []:
            visit(child)

    visit(tree.root_node)
    return _dedupe_symbols(symbols)


def _tree_sitter_parser(language: str) -> Any | None:
    if language in _TREE_SITTER_LANGUAGES and _TREE_SITTER_LANGUAGES[language] is None:
        return None
    module_name = _TREE_SITTER_LANGUAGE_MODULES.get(language)
    if not module_name:
        _TREE_SITTER_LANGUAGES[language] = None
        return None
    try:
        from tree_sitter import Language, Parser

        language_obj = _TREE_SITTER_LANGUAGES.get(language)
        if language_obj is None:
            language_module = importlib.import_module(module_name)
            raw_language = language_module.language()
            language_obj = _coerce_tree_sitter_language(Language, raw_language)
            _TREE_SITTER_LANGUAGES[language] = language_obj
        parser = Parser()
        if hasattr(parser, "set_language"):
            parser.set_language(language_obj)
        else:
            parser.language = language_obj
    except Exception:
        _TREE_SITTER_LANGUAGES[language] = None
        return None
    return parser


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None  # type: ignore[attr-defined]
    except Exception:
        return False


def _coerce_tree_sitter_language(language_class: Any, raw_language: Any) -> Any:
    try:
        return language_class(raw_language)
    except Exception:
        return raw_language


def _tree_sitter_symbol_for_node(
    *,
    language: str,
    node: Any,
) -> ExtractedSymbol | None:
    node_type = str(getattr(node, "type", ""))
    if language == "python":
        kind_by_type = {
            "function_definition": "function",
            "class_definition": "class",
        }
        kind = kind_by_type.get(node_type)
        if kind:
            return _symbol_from_tree_sitter_node(node=node, kind=kind)
        return None

    if language == "javascript":
        kind_by_type = {
            "function_declaration": "function",
            "class_declaration": "class",
            "method_definition": "function",
        }
        kind = kind_by_type.get(node_type)
        if kind:
            return _symbol_from_tree_sitter_node(node=node, kind=kind)
        if node_type == "variable_declarator":
            value_node = _child_by_field_name(node, "value")
            value_type = str(getattr(value_node, "type", ""))
            if value_type in {"arrow_function", "function", "function_expression"}:
                return _symbol_from_tree_sitter_node(node=node, kind="function")
    return None


def _symbol_from_tree_sitter_node(*, node: Any, kind: str) -> ExtractedSymbol | None:
    name_node = _child_by_field_name(node, "name")
    if name_node is None:
        return None
    name = _node_text(name_node)
    if not name:
        return None
    start_point = getattr(node, "start_point", None)
    line = int(start_point[0]) + 1 if isinstance(start_point, tuple) and start_point else None
    end_point = getattr(node, "end_point", None)
    end_line = int(end_point[0]) + 1 if isinstance(end_point, tuple) and end_point else None
    return ExtractedSymbol(
        name=name,
        kind=kind,
        line=line,
        end_line=end_line,
        qualified_name=name,
        metadata={"parser": "tree_sitter"},
    )


def _child_by_field_name(node: Any, field_name: str) -> Any | None:
    try:
        return node.child_by_field_name(field_name)
    except Exception:
        return None


def _node_text(node: Any) -> str | None:
    text = getattr(node, "text", None)
    if isinstance(text, bytes):
        return text.decode("utf-8", errors="ignore").strip()
    if isinstance(text, str):
        return text.strip()
    return None


def _dedupe_symbols(symbols: list[ExtractedSymbol]) -> list[ExtractedSymbol]:
    out: list[ExtractedSymbol] = []
    seen: set[tuple[str, str, int | None]] = set()
    for symbol in symbols:
        key = (symbol.name, symbol.kind, symbol.line)
        if key in seen:
            continue
        seen.add(key)
        out.append(symbol)
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
        metadata["module_id"] = _first_text(data, "id", "module_id", "name")
        metadata["action_ids"] = _extract_action_ids(data)
    elif path.endswith("agents.yaml"):
        metadata["agent_names"] = _extract_named_items(data.get("agents"))
    elif path.endswith("tools.yaml"):
        metadata["tool_names"] = _extract_tool_names(data.get("tools"))
    elif contract_path.startswith("ui/pages/"):
        metadata["page_id"] = _first_text(data, "id", "page_id", "name", "title")
        metadata["module_ids"] = _extract_module_refs(data)
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


def _resolve_import_path(*, path: str, import_target: str, file_map: dict[str, str]) -> str | None:
    raw = str(import_target or "").strip()
    if not raw.startswith("."):
        return None
    base_dir = PurePosixPath(path).parent
    relative_target = raw
    if raw.startswith(".") and not raw.startswith("./") and not raw.startswith("../"):
        leading_dots = len(raw) - len(raw.lstrip("."))
        base_dir = _ascend(base_dir, max(0, leading_dots - 1))
        relative_target = raw[leading_dots:].replace(".", "/")
    elif raw.startswith("./"):
        relative_target = raw[2:]
    elif raw.startswith("../"):
        relative_target = raw
    for suffix in _RELATIVE_IMPORT_EXTENSIONS:
        safe = _safe_relpath(_join_relative(base_dir, f"{relative_target}{suffix}"))
        if safe and safe in file_map:
            return safe
    return None


def _resolved_import_paths(
    *,
    path: str,
    file_context: ExtractedFileContext,
    file_map: dict[str, str],
) -> list[str]:
    out: list[str] = []
    for import_target in file_context.imports:
        resolved = _resolve_import_path(path=path, import_target=import_target, file_map=file_map)
        if resolved and resolved not in out:
            out.append(resolved)
    return out


def _reference_source_node_id(
    *,
    path: str,
    reference: ExtractedReference,
    fallback_file_node_id: str,
    symbol_node_by_lookup: dict[tuple[str, str], str],
) -> str:
    if reference.caller:
        direct = symbol_node_by_lookup.get((path, reference.caller))
        if direct:
            return direct
        tail = reference.caller.rsplit(".", 1)[-1]
        tail_match = symbol_node_by_lookup.get((path, tail))
        if tail_match:
            return tail_match
    return fallback_file_node_id


def _reference_target_node_id(
    *,
    path: str,
    reference: ExtractedReference,
    imported_paths: list[str],
    symbol_node_by_lookup: dict[tuple[str, str], str],
) -> str | None:
    candidates = _reference_name_candidates(reference.target)
    raw_target = str(reference.target or "").strip()
    first_part = raw_target.split(".", 1)[0]
    prefer_imported = "." in raw_target and first_part not in {"self", "this", "cls"}
    if prefer_imported:
        for imported_path in imported_paths:
            for candidate in candidates:
                node_id = symbol_node_by_lookup.get((imported_path, candidate))
                if node_id:
                    return node_id
    for candidate in candidates:
        node_id = symbol_node_by_lookup.get((path, candidate))
        if node_id:
            return node_id
    for imported_path in imported_paths:
        for candidate in candidates:
            node_id = symbol_node_by_lookup.get((imported_path, candidate))
            if node_id:
                return node_id
    return None


def _reference_name_candidates(target: str) -> list[str]:
    raw = str(target or "").strip()
    if not raw:
        return []
    parts = raw.split(".")
    candidates = [raw]
    if parts[-1] not in candidates:
        candidates.append(parts[-1])
    if len(parts) > 1 and parts[0] not in candidates:
        candidates.append(parts[0])
    return candidates


def _is_noise_reference(reference: ExtractedReference) -> bool:
    target = str(reference.target or "").strip()
    return not target or target in _NOISE_CALL_TARGETS or target.rsplit(".", 1)[-1] in _NOISE_CALL_TARGETS


def _ascend(path: PurePosixPath, count: int) -> PurePosixPath:
    current = path
    for _ in range(count):
        current = current.parent
    return current


def _join_relative(base_dir: PurePosixPath, relative_target: str) -> str:
    parts = [part for part in base_dir.parts if part not in {"", "."}]
    for part in PurePosixPath(relative_target).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


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
    if suffix in {".ts", ".tsx"}:
        return "typescript"
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    if suffix == ".json":
        return "json"
    if suffix == ".css":
        return "css"
    if suffix == ".md":
        return "markdown"
    return "text"


def _file_node_id(path: str) -> str:
    return _stable_id("file", path)


def _symbol_node_id(path: str, name: str, kind: str) -> str:
    return _stable_id("symbol", f"{path}:{kind}:{name}")


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


def _dedupe(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        candidate = str(value or "").strip()
        if candidate and candidate not in out:
            out.append(candidate)
    return out


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
    "context_graph_parser_status",
    "extract_file_context",
    "merge_context_graphs",
]

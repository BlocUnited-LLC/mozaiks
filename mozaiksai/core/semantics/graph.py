"""Immutable, scoped, content-digested ``SemanticGraph`` contract.

``SemanticGraph`` is authored application intent — the document the compiler
compiles *from*.  It is deliberately distinct from ``AppContextGraph``, which
remains the observed/indexed view of files and runtime artifacts; the two
authorities are never merged.  Node and edge kinds are a closed, versioned set
seeded from the concepts in the existing ``GraphNodeType``/``GraphEdgeType``
enums without importing their observed-view semantics.

Identity rules (owned by this slice, with golden vectors in tests):

- A node identity is its namespace-qualified ``node_id`` — stable across graph
  versions, independent of list order and renderer layout.
- An edge identity is the canonical digest of
  ``{edge_kind, source_node_id, target_node_id, discriminator}``.  Duplicate
  identities fail closed; reordering nodes or edges changes neither identities
  nor the graph digest, because the canonical payload sorts both collections
  by identity.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.refs import (
    MUTABLE_ALIASES,
    ExecutionAccessScopeRef,
    SemanticPayloadRef,
    SemanticsModel,
    validate_node_id_grammar,
)
from mozaiksai.core.taxonomy import (
    SemanticCategory,
    TaxonomyRegistry,
    validate_identifier_grammar,
)

SEMANTIC_GRAPH_SCHEMA_VERSION: Literal["mozaiks.semantic_graph.v1"] = "mozaiks.semantic_graph.v1"
SEMANTIC_GRAPH_V2_SCHEMA_VERSION: Literal["mozaiks.semantic_graph.v2"] = (
    "mozaiks.semantic_graph.v2"
)

#: Core namespace root; nodes outside it must sit inside a granted namespace.
CORE_NODE_NAMESPACE_ROOT = "mozaiks"

_GRAPH_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class SemanticGraphError(ValueError):
    """Raised when a graph document violates the contract."""


class SemanticNodeKind(StrEnum):
    """Closed, versioned node-kind set for ``mozaiks.semantic_graph.v1``."""

    APPLICATION = "application"
    AUTH = "auth"
    INTEGRATION = "integration"
    SURFACE = "surface"
    PAGE = "page"
    SECTION = "section"
    MODULE = "module"
    ACTION = "action"
    CAPABILITY = "capability"
    PERMISSION = "permission"
    EVENT = "event"
    REACTION = "reaction"
    NOTIFICATION = "notification"
    DATA_COLLECTION = "data_collection"
    DATA_ALIAS = "data_alias"
    WORKFLOW = "workflow"
    TRIGGER = "trigger"
    PLAN = "plan"
    PRODUCT = "product"
    METER = "meter"
    LIMIT = "limit"
    DEPLOYMENT_TARGET = "deployment_target"
    STUB_DECLARATION = "stub_declaration"


class SemanticEdgeKind(StrEnum):
    """Closed, versioned edge-kind set for ``mozaiks.semantic_graph.v1``."""

    DECLARES = "declares"
    EMITS = "emits"
    CONSUMES = "consumes"
    RENDERS = "renders"
    BINDS = "binds"
    DEPENDS_ON = "depends_on"
    GATES = "gates"
    OWNS = "owns"


class TaxonomyReference(SemanticsModel):
    """A node's reference into the Slice 1 taxonomy registry."""

    category: SemanticCategory
    identifier: str

    @model_validator(mode="after")
    def _grammar(self) -> TaxonomyReference:
        validate_identifier_grammar(self.category, self.identifier)
        return self

    @property
    def identity_payload(self) -> dict[str, str]:
        return {"category": self.category.value, "identifier": self.identifier}


# The node-id grammar is shared contract state: graph v1/v2 nodes and
# SemanticPayloadRef validate through the single helper in refs.py.
_validate_node_id = validate_node_id_grammar


def _normalize_taxonomy_references(
    value: tuple[TaxonomyReference, ...],
) -> tuple[TaxonomyReference, ...]:
    ordered = tuple(sorted(value, key=lambda item: (item.category.value, item.identifier)))
    identities = [(item.category, item.identifier) for item in ordered]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate taxonomy references on one node")
    return ordered


def _normalize_node_references(value: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(sorted(_validate_node_id(item) for item in value))
    if len(normalized) != len(set(normalized)):
        raise ValueError("duplicate node references on one node")
    return normalized


def _validate_graph_id(value: str) -> str:
    text = str(value or "").strip()
    if _GRAPH_ID.fullmatch(text) is None:
        raise ValueError(f"graph_id must be a lowercase identifier, got {value!r}")
    if text in MUTABLE_ALIASES:
        raise ValueError(f"graph_id must be immutable identity, got {text!r}")
    return text


def _normalize_namespace_grants(value: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(sorted({str(item or "").strip() for item in value}))
    for item in normalized:
        if re.fullmatch(r"[a-z][a-z0-9_]*", item) is None:
            raise ValueError(f"namespace grant must be a lowercase root identifier, got {item!r}")
        if item == CORE_NODE_NAMESPACE_ROOT:
            raise ValueError("the core namespace root cannot be granted to extensions")
    return normalized


class SemanticNode(SemanticsModel):
    node_id: str
    kind: SemanticNodeKind
    taxonomy_references: tuple[TaxonomyReference, ...] = Field(default_factory=tuple)
    node_references: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("node_id")
    @classmethod
    def _node_id(cls, value: str) -> str:
        return _validate_node_id(value)

    @field_validator("taxonomy_references")
    @classmethod
    def _taxonomy_references(
        cls, value: tuple[TaxonomyReference, ...]
    ) -> tuple[TaxonomyReference, ...]:
        return _normalize_taxonomy_references(value)

    @field_validator("node_references")
    @classmethod
    def _node_references(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_node_references(value)

    @property
    def namespace_root(self) -> str:
        return self.node_id.split(".", 1)[0]

    @property
    def identity_payload(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "kind": self.kind.value,
            "taxonomy_references": [item.identity_payload for item in self.taxonomy_references],
            "node_references": list(self.node_references),
        }


class SemanticEdge(SemanticsModel):
    kind: SemanticEdgeKind
    source_node_id: str
    target_node_id: str
    discriminator: str | None = None

    @field_validator("source_node_id", "target_node_id")
    @classmethod
    def _endpoints(cls, value: str) -> str:
        return _validate_node_id(value)

    @field_validator("discriminator")
    @classmethod
    def _discriminator(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            raise ValueError("discriminator must be non-empty when present")
        return text

    @property
    def identity_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "source_node_id": self.source_node_id,
            "target_node_id": self.target_node_id,
            "discriminator": self.discriminator,
        }

    @property
    def edge_identity(self) -> str:
        """Deterministic identity derived from kind, endpoints, and discriminator."""
        return canonical_digest(self.identity_payload)


def _validate_graph_structure(graph: SemanticGraph | SemanticGraphV2) -> None:
    """Shared structural validation for graph v1 and v2 (identical rules)."""
    node_ids = [node.node_id for node in graph.nodes]
    if len(node_ids) != len(set(node_ids)):
        duplicates = sorted({item for item in node_ids if node_ids.count(item) > 1})
        raise ValueError(f"duplicate node identities: {duplicates}")
    known = set(node_ids)

    for node in graph.nodes:
        root = node.namespace_root
        if root != CORE_NODE_NAMESPACE_ROOT and root not in graph.namespace_grants:
            raise ValueError(
                f"node {node.node_id!r} is outside the core namespace and the "
                f"granted namespaces {list(graph.namespace_grants)!r}"
            )
        for reference in node.node_references:
            if reference not in known:
                raise ValueError(
                    f"node {node.node_id!r} references unknown node {reference!r}"
                )

    edge_identities: dict[str, SemanticEdge] = {}
    for edge in graph.edges:
        if edge.source_node_id not in known:
            raise ValueError(
                f"edge {edge.kind.value} has dangling source {edge.source_node_id!r}"
            )
        if edge.target_node_id not in known:
            raise ValueError(
                f"edge {edge.kind.value} has dangling target {edge.target_node_id!r}"
            )
        identity = edge.edge_identity
        if identity in edge_identities:
            raise ValueError(
                f"duplicate edge identity for {edge.kind.value} "
                f"{edge.source_node_id!r} -> {edge.target_node_id!r} "
                f"(discriminator {edge.discriminator!r})"
            )
        edge_identities[identity] = edge


class SemanticGraph(SemanticsModel):
    schema_version: Literal["mozaiks.semantic_graph.v1"] = SEMANTIC_GRAPH_SCHEMA_VERSION
    graph_id: str
    version: int = Field(ge=1, strict=True)
    scope: ExecutionAccessScopeRef
    namespace_grants: tuple[str, ...] = Field(default_factory=tuple)
    nodes: tuple[SemanticNode, ...] = Field(min_length=1)
    edges: tuple[SemanticEdge, ...] = Field(default_factory=tuple)
    graph_digest: str = Field(min_length=64, max_length=64)

    @field_validator("graph_id")
    @classmethod
    def _graph_id(cls, value: str) -> str:
        return _validate_graph_id(value)

    @field_validator("namespace_grants")
    @classmethod
    def _grants(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_namespace_grants(value)

    @field_validator("nodes")
    @classmethod
    def _nodes(cls, value: tuple[SemanticNode, ...]) -> tuple[SemanticNode, ...]:
        return tuple(sorted(value, key=lambda item: item.node_id))

    @field_validator("edges")
    @classmethod
    def _edges(cls, value: tuple[SemanticEdge, ...]) -> tuple[SemanticEdge, ...]:
        return tuple(sorted(value, key=lambda item: item.edge_identity))

    @model_validator(mode="after")
    def _validate_graph(self) -> SemanticGraph:
        _validate_graph_structure(self)
        expected = canonical_digest(self.canonical_payload(include_digest=False))
        if self.graph_digest != expected:
            raise ValueError("graph_digest does not match graph content")
        return self

    def canonical_payload(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "graph_id": self.graph_id,
            "version": self.version,
            "scope": self.scope.model_dump(mode="json"),
            "namespace_grants": list(self.namespace_grants),
            "nodes": [node.identity_payload for node in self.nodes],
            "edges": [edge.identity_payload for edge in self.edges],
        }
        if include_digest:
            payload["graph_digest"] = self.graph_digest
        return payload

    def node(self, node_id: str) -> SemanticNode:
        for candidate in self.nodes:
            if candidate.node_id == node_id:
                return candidate
        raise SemanticGraphError(f"unknown node {node_id!r}")


def build_semantic_graph(
    *,
    graph_id: str,
    version: int,
    scope: ExecutionAccessScopeRef,
    nodes: tuple[SemanticNode, ...] | list[SemanticNode],
    edges: tuple[SemanticEdge, ...] | list[SemanticEdge] = (),
    namespace_grants: tuple[str, ...] | list[str] = (),
) -> SemanticGraph:
    """Construct a graph with its content digest computed canonically."""
    ordered_nodes = tuple(sorted(tuple(nodes), key=lambda item: item.node_id))
    ordered_edges = tuple(sorted(tuple(edges), key=lambda item: item.edge_identity))
    ordered_grants = tuple(sorted({str(item).strip() for item in namespace_grants}))
    payload = {
        "schema_version": SEMANTIC_GRAPH_SCHEMA_VERSION,
        "graph_id": str(graph_id).strip(),
        "version": version,
        "scope": scope.model_dump(mode="json"),
        "namespace_grants": list(ordered_grants),
        "nodes": [node.identity_payload for node in ordered_nodes],
        "edges": [edge.identity_payload for edge in ordered_edges],
    }
    return SemanticGraph(
        graph_id=graph_id,
        version=version,
        scope=scope,
        namespace_grants=ordered_grants,
        nodes=ordered_nodes,
        edges=ordered_edges,
        graph_digest=canonical_digest(payload),
    )


def validate_semantic_graph_taxonomy_closure(
    graph: SemanticGraph, registry: TaxonomyRegistry
) -> None:
    """Fail closed unless every taxonomy reference resolves in ``registry``.

    Uses the Slice 1 taxonomy authority directly; this module introduces no
    parallel registry.
    """
    for node in graph.nodes:
        for reference in node.taxonomy_references:
            registry.resolve(reference.category, reference.identifier)


class SemanticNodeV2(SemanticsModel):
    """Graph-v2 node: v1 identity plus a required pinned semantic payload.

    Not a subclass of :class:`SemanticNode` — frozen models with distinct
    identity payloads stay independent; both validate through the shared
    module helpers so the grammars cannot diverge.
    """

    node_id: str
    kind: SemanticNodeKind
    taxonomy_references: tuple[TaxonomyReference, ...] = Field(default_factory=tuple)
    node_references: tuple[str, ...] = Field(default_factory=tuple)
    payload_ref: SemanticPayloadRef

    @field_validator("node_id")
    @classmethod
    def _node_id(cls, value: str) -> str:
        return _validate_node_id(value)

    @field_validator("taxonomy_references")
    @classmethod
    def _taxonomy_references(
        cls, value: tuple[TaxonomyReference, ...]
    ) -> tuple[TaxonomyReference, ...]:
        return _normalize_taxonomy_references(value)

    @field_validator("node_references")
    @classmethod
    def _node_references(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_node_references(value)

    @model_validator(mode="after")
    def _payload_pin(self) -> SemanticNodeV2:
        if self.payload_ref.node_id != self.node_id:
            raise ValueError(
                f"payload_ref pins node {self.payload_ref.node_id!r} but the node is "
                f"{self.node_id!r}"
            )
        if self.payload_ref.payload_kind != self.kind.value:
            raise ValueError(
                f"payload_ref kind {self.payload_ref.payload_kind!r} does not match "
                f"node kind {self.kind.value!r} on {self.node_id!r}"
            )
        return self

    @property
    def namespace_root(self) -> str:
        return self.node_id.split(".", 1)[0]

    @property
    def identity_payload(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "kind": self.kind.value,
            "taxonomy_references": [item.identity_payload for item in self.taxonomy_references],
            "node_references": list(self.node_references),
            "payload_ref": self.payload_ref.identity_payload,
        }


class SemanticGraphV2(SemanticsModel):
    """Graph v2: every node pins its typed payload; ``graph_digest`` is the
    Merkle root — any payload byte change flows payload digest → node identity
    → graph digest."""

    schema_version: Literal["mozaiks.semantic_graph.v2"] = SEMANTIC_GRAPH_V2_SCHEMA_VERSION
    graph_id: str
    version: int = Field(ge=1, strict=True)
    scope: ExecutionAccessScopeRef
    namespace_grants: tuple[str, ...] = Field(default_factory=tuple)
    nodes: tuple[SemanticNodeV2, ...] = Field(min_length=1)
    edges: tuple[SemanticEdge, ...] = Field(default_factory=tuple)
    graph_digest: str = Field(min_length=64, max_length=64)

    @field_validator("graph_id")
    @classmethod
    def _graph_id(cls, value: str) -> str:
        return _validate_graph_id(value)

    @field_validator("namespace_grants")
    @classmethod
    def _grants(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_namespace_grants(value)

    @field_validator("nodes")
    @classmethod
    def _nodes(cls, value: tuple[SemanticNodeV2, ...]) -> tuple[SemanticNodeV2, ...]:
        return tuple(sorted(value, key=lambda item: item.node_id))

    @field_validator("edges")
    @classmethod
    def _edges(cls, value: tuple[SemanticEdge, ...]) -> tuple[SemanticEdge, ...]:
        return tuple(sorted(value, key=lambda item: item.edge_identity))

    @model_validator(mode="after")
    def _validate_graph(self) -> SemanticGraphV2:
        _validate_graph_structure(self)
        for singleton_kind in (SemanticNodeKind.APPLICATION, SemanticNodeKind.AUTH):
            matching_nodes = [
                node.node_id for node in self.nodes if node.kind is singleton_kind
            ]
            if len(matching_nodes) > 1:
                raise ValueError(
                    f"semantic graph v2 permits at most one {singleton_kind.value} node"
                )
        for node in self.nodes:
            if node.payload_ref.scope != self.scope:
                raise ValueError(
                    f"payload_ref on {node.node_id!r} is pinned in a different scope "
                    "than the graph"
                )
        expected = canonical_digest(self.canonical_payload(include_digest=False))
        if self.graph_digest != expected:
            raise ValueError("graph_digest does not match graph content")
        return self

    def canonical_payload(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "graph_id": self.graph_id,
            "version": self.version,
            "scope": self.scope.model_dump(mode="json"),
            "namespace_grants": list(self.namespace_grants),
            "nodes": [node.identity_payload for node in self.nodes],
            "edges": [edge.identity_payload for edge in self.edges],
        }
        if include_digest:
            payload["graph_digest"] = self.graph_digest
        return payload

    def node(self, node_id: str) -> SemanticNodeV2:
        for candidate in self.nodes:
            if candidate.node_id == node_id:
                return candidate
        raise SemanticGraphError(f"unknown node {node_id!r}")


def build_semantic_graph_v2(
    *,
    graph_id: str,
    version: int,
    scope: ExecutionAccessScopeRef,
    nodes: tuple[SemanticNodeV2, ...] | list[SemanticNodeV2],
    edges: tuple[SemanticEdge, ...] | list[SemanticEdge] = (),
    namespace_grants: tuple[str, ...] | list[str] = (),
) -> SemanticGraphV2:
    """Construct a v2 graph with its Merkle-root digest computed canonically."""
    ordered_nodes = tuple(sorted(tuple(nodes), key=lambda item: item.node_id))
    ordered_edges = tuple(sorted(tuple(edges), key=lambda item: item.edge_identity))
    ordered_grants = tuple(sorted({str(item).strip() for item in namespace_grants}))
    payload = {
        "schema_version": SEMANTIC_GRAPH_V2_SCHEMA_VERSION,
        "graph_id": str(graph_id).strip(),
        "version": version,
        "scope": scope.model_dump(mode="json"),
        "namespace_grants": list(ordered_grants),
        "nodes": [node.identity_payload for node in ordered_nodes],
        "edges": [edge.identity_payload for edge in ordered_edges],
    }
    return SemanticGraphV2(
        graph_id=graph_id,
        version=version,
        scope=scope,
        namespace_grants=ordered_grants,
        nodes=ordered_nodes,
        edges=ordered_edges,
        graph_digest=canonical_digest(payload),
    )


def validate_semantic_graph_v2_taxonomy_closure(
    graph: SemanticGraphV2, registry: TaxonomyRegistry
) -> None:
    """Fail closed unless every v2 taxonomy reference resolves in ``registry``."""
    for node in graph.nodes:
        for reference in node.taxonomy_references:
            registry.resolve(reference.category, reference.identifier)


__all__ = [
    "CORE_NODE_NAMESPACE_ROOT",
    "SEMANTIC_GRAPH_SCHEMA_VERSION",
    "SEMANTIC_GRAPH_V2_SCHEMA_VERSION",
    "SemanticEdge",
    "SemanticEdgeKind",
    "SemanticGraph",
    "SemanticGraphError",
    "SemanticGraphV2",
    "SemanticNode",
    "SemanticNodeKind",
    "SemanticNodeV2",
    "TaxonomyReference",
    "build_semantic_graph",
    "build_semantic_graph_v2",
    "validate_semantic_graph_taxonomy_closure",
    "validate_semantic_graph_v2_taxonomy_closure",
]

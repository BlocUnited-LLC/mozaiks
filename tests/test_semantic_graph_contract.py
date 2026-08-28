"""Adversarial tests for the immutable SemanticGraph contract."""

from __future__ import annotations

import pydantic
import pytest

from mozaiksai.core.semantics import (
    ExecutionAccessScopeRef,
    SemanticEdge,
    SemanticEdgeKind,
    SemanticGraph,
    SemanticNode,
    SemanticNodeKind,
    TaxonomyReference,
    build_semantic_graph,
    validate_semantic_graph_taxonomy_closure,
)
from mozaiksai.core.taxonomy import (
    NamespaceKind,
    SemanticCategory,
    TaxonomyEntry,
    TaxonomyNamespace,
    UnknownTaxonomyIdentifier,
    build_taxonomy_registry,
)

SCOPE = ExecutionAccessScopeRef(tenant_id="tenant-1", workspace_id="ws-1")
OTHER_SCOPE = ExecutionAccessScopeRef(tenant_id="tenant-2")

EDGE_IDENTITY_GOLDEN = "4c8bfb1bdf3d2fa3b627a7fdb6044f25a7994d340841b94c3ec162ddcdb8f1ad"


def _node(node_id: str, kind: SemanticNodeKind = SemanticNodeKind.PAGE, **kw) -> SemanticNode:
    return SemanticNode(node_id=node_id, kind=kind, **kw)


def _basic_nodes() -> list[SemanticNode]:
    return [
        _node("mozaiks.pages.home"),
        _node("mozaiks.events.user_created", SemanticNodeKind.EVENT),
        _node("mozaiks.modules.users", SemanticNodeKind.MODULE),
    ]


def _basic_edges() -> list[SemanticEdge]:
    return [
        SemanticEdge(
            kind=SemanticEdgeKind.EMITS,
            source_node_id="mozaiks.modules.users",
            target_node_id="mozaiks.events.user_created",
        ),
        SemanticEdge(
            kind=SemanticEdgeKind.RENDERS,
            source_node_id="mozaiks.pages.home",
            target_node_id="mozaiks.modules.users",
        ),
    ]


def _graph(**overrides):
    fields = {
        "graph_id": "graph-1",
        "version": 1,
        "scope": SCOPE,
        "nodes": _basic_nodes(),
        "edges": _basic_edges(),
    }
    fields.update(overrides)
    return build_semantic_graph(**fields)


def test_edge_identity_golden_vector() -> None:
    edge = SemanticEdge(
        kind=SemanticEdgeKind.EMITS,
        source_node_id="mozaiks.pages.home",
        target_node_id="mozaiks.events.user_created",
        discriminator="primary",
    )
    assert edge.edge_identity == EDGE_IDENTITY_GOLDEN


def test_edge_identity_depends_on_every_typed_component() -> None:
    base = SemanticEdge(
        kind=SemanticEdgeKind.EMITS,
        source_node_id="mozaiks.a.x",
        target_node_id="mozaiks.b.y",
    )
    variants = [
        SemanticEdge(
            kind=SemanticEdgeKind.CONSUMES,
            source_node_id="mozaiks.a.x",
            target_node_id="mozaiks.b.y",
        ),
        SemanticEdge(
            kind=SemanticEdgeKind.EMITS,
            source_node_id="mozaiks.b.y",
            target_node_id="mozaiks.a.x",
        ),
        SemanticEdge(
            kind=SemanticEdgeKind.EMITS,
            source_node_id="mozaiks.a.x",
            target_node_id="mozaiks.b.y",
            discriminator="d1",
        ),
    ]
    identities = {base.edge_identity, *(v.edge_identity for v in variants)}
    assert len(identities) == 4


def test_ordering_does_not_change_identities_or_digest() -> None:
    forward = _graph()
    reversed_input = build_semantic_graph(
        graph_id="graph-1",
        version=1,
        scope=SCOPE,
        nodes=list(reversed(_basic_nodes())),
        edges=list(reversed(_basic_edges())),
    )
    assert forward.graph_digest == reversed_input.graph_digest
    assert [n.node_id for n in forward.nodes] == [n.node_id for n in reversed_input.nodes]


def test_duplicate_node_identities_fail_closed() -> None:
    with pytest.raises(pydantic.ValidationError, match="duplicate node"):
        _graph(nodes=[*_basic_nodes(), _node("mozaiks.pages.home", SemanticNodeKind.SURFACE)])


def test_duplicate_edge_identities_fail_closed() -> None:
    edge = _basic_edges()[0]
    with pytest.raises(pydantic.ValidationError, match="duplicate edge identity"):
        _graph(edges=[*_basic_edges(), edge.model_copy()])


def test_same_endpoints_with_distinct_discriminators_are_distinct_edges() -> None:
    edges = [
        SemanticEdge(
            kind=SemanticEdgeKind.DEPENDS_ON,
            source_node_id="mozaiks.pages.home",
            target_node_id="mozaiks.modules.users",
            discriminator="d1",
        ),
        SemanticEdge(
            kind=SemanticEdgeKind.DEPENDS_ON,
            source_node_id="mozaiks.pages.home",
            target_node_id="mozaiks.modules.users",
            discriminator="d2",
        ),
    ]
    graph = _graph(edges=edges)
    assert len(graph.edges) == 2


@pytest.mark.parametrize("field", ["source_node_id", "target_node_id"])
def test_dangling_edge_endpoints_fail_closed(field: str) -> None:
    edge = SemanticEdge(
        **{
            "kind": SemanticEdgeKind.EMITS,
            "source_node_id": "mozaiks.pages.home",
            "target_node_id": "mozaiks.events.user_created",
            field: "mozaiks.ghost.node",
        }
    )
    with pytest.raises(pydantic.ValidationError, match="dangling"):
        _graph(edges=[edge])


def test_dangling_node_references_fail_closed() -> None:
    nodes = [
        *_basic_nodes(),
        _node(
            "mozaiks.pages.about",
            node_references=("mozaiks.ghost.node",),
        ),
    ]
    with pytest.raises(pydantic.ValidationError, match="unknown node"):
        _graph(nodes=nodes)


def test_taxonomy_reference_closure() -> None:
    registry = build_taxonomy_registry(
        [
            TaxonomyNamespace(
                namespace_id="mozaiks.events",
                version=1,
                kind=NamespaceKind.CORE,
                entries=(
                    TaxonomyEntry(
                        category=SemanticCategory.EVENT, identifier="domain.users.user_created"
                    ),
                ),
            )
        ]
    )
    good = _graph(
        nodes=[
            *_basic_nodes(),
            _node(
                "mozaiks.events.declared",
                SemanticNodeKind.EVENT,
                taxonomy_references=(
                    TaxonomyReference(
                        category=SemanticCategory.EVENT,
                        identifier="domain.users.user_created",
                    ),
                ),
            ),
        ]
    )
    validate_semantic_graph_taxonomy_closure(good, registry)

    dangling = _graph(
        nodes=[
            *_basic_nodes(),
            _node(
                "mozaiks.events.declared",
                SemanticNodeKind.EVENT,
                taxonomy_references=(
                    TaxonomyReference(
                        category=SemanticCategory.EVENT,
                        identifier="domain.users.unknown_event",
                    ),
                ),
            ),
        ]
    )
    with pytest.raises(UnknownTaxonomyIdentifier):
        validate_semantic_graph_taxonomy_closure(dangling, registry)


def test_extension_nodes_stay_inside_granted_namespaces() -> None:
    with pytest.raises(pydantic.ValidationError, match="outside the core namespace"):
        _graph(nodes=[*_basic_nodes(), _node("acme.pages.custom")])

    granted = _graph(
        nodes=[*_basic_nodes(), _node("acme.pages.custom")],
        namespace_grants=("acme",),
    )
    assert any(node.node_id == "acme.pages.custom" for node in granted.nodes)

    with pytest.raises(pydantic.ValidationError, match="core namespace root"):
        _graph(namespace_grants=("mozaiks",))


def test_unknown_fields_fail_closed() -> None:
    graph = _graph()
    payload = graph.model_dump()
    payload["surprise"] = True
    with pytest.raises(pydantic.ValidationError):
        SemanticGraph(**payload)
    with pytest.raises(pydantic.ValidationError):
        SemanticNode(node_id="mozaiks.a.b", kind=SemanticNodeKind.PAGE, surprise=1)
    with pytest.raises(pydantic.ValidationError):
        SemanticEdge(
            kind=SemanticEdgeKind.EMITS,
            source_node_id="mozaiks.a.b",
            target_node_id="mozaiks.c.d",
            surprise=1,
        )


def test_graph_documents_are_immutable() -> None:
    graph = _graph()
    with pytest.raises(pydantic.ValidationError):
        graph.version = 2
    with pytest.raises(pydantic.ValidationError):
        graph.nodes[0].kind = SemanticNodeKind.SURFACE


def test_tampered_digest_fails_closed() -> None:
    graph = _graph()
    payload = graph.model_dump()
    payload["graph_digest"] = "0" * 64
    with pytest.raises(pydantic.ValidationError, match="graph_digest"):
        SemanticGraph(**payload)


def test_scope_participates_in_digest() -> None:
    assert _graph().graph_digest != _graph(scope=OTHER_SCOPE).graph_digest


def test_every_semantic_field_participates_in_digest() -> None:
    base = _graph()
    assert base.graph_digest != _graph(version=2).graph_digest
    assert base.graph_digest != _graph(graph_id="graph-2").graph_digest
    assert base.graph_digest != _graph(edges=_basic_edges()[:1]).graph_digest
    assert (
        base.graph_digest
        != _graph(namespace_grants=("acme",)).graph_digest
    )


@pytest.mark.parametrize(
    "bad_node_id",
    ["", "single_segment", "Upper.case", "latest.thing", "mozaiks..double", ".leading"],
)
def test_node_identifier_grammar_fails_closed(bad_node_id: str) -> None:
    with pytest.raises(pydantic.ValidationError):
        _node(bad_node_id)


def test_node_identity_is_stable_across_graph_versions() -> None:
    v1 = _graph()
    v2 = _graph(version=2, edges=_basic_edges()[:1])
    assert {n.node_id for n in v1.nodes} == {n.node_id for n in v2.nodes}
    assert v1.graph_digest != v2.graph_digest


def test_graph_version_zero_and_alias_ids_fail_closed() -> None:
    with pytest.raises(pydantic.ValidationError):
        _graph(version=0)
    with pytest.raises(pydantic.ValidationError, match="mutable alias|immutable"):
        _graph(graph_id="latest")

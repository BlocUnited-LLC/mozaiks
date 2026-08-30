from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from mozaiksai.core.app_context import (
    AppContextGraph,
    AppContextGraphEdge,
    AppContextGraphNode,
    GraphEdgeType,
    GraphNodeType,
    ImpactDirection,
    ImpactQuery,
    ImpactReport,
    ParserAuthority,
    analyze_impact,
    build_context_graph_from_file_map,
    build_graph_snapshot_identity,
    impact_query_digest,
)


def _fixture_file_map() -> dict[str, str]:
    return {
        "app/modules/orders/module.yaml": """
module:
  id: orders
actions:
  - id: create_order
    emits:
      - domain.order.created
  - id: notify_order
""",
        "app/modules/orders/contracts/events.yaml": """
events:
  - type: domain.order.created
""",
        "app/modules/orders/contracts/reactions.yaml": """
reactions:
  - event: domain.order.created
    target:
      action: notify_order
""",
        "app/ui/route_manifest.json": """
{
  "schema_version": "mozaiks.route_manifest.v1",
  "routes": [
    {
      "path": "/orders",
      "page": "orders_page",
      "endpoint": "/api/modules/orders/create_order"
    }
  ]
}
""",
        "app/ui/pages/orders.yaml": """
id: orders_page
components:
  - module_id: orders
    action_id: create_order
""",
        "app/.mozaiks/pack_provenance.json": """
{
  "pack_id": "commerce",
  "materialized_owned_files": [
    {"path": "app/modules/orders/module.yaml", "owner": "commerce"}
  ]
}
""",
        "app/ui/pages/custom/Orders.jsx": """
export const Orders = () => {
  return null;
};
""",
    }


def _graph(file_map: dict[str, str] | None = None, *, indexed_at: datetime | None = None) -> AppContextGraph:
    return build_context_graph_from_file_map(
        app_id="demo",
        artifact_version_id="artifact-1",
        artifact_kind="generated_app",
        artifact_key="demo",
        file_map=file_map or _fixture_file_map(),
        indexed_at=indexed_at or datetime(2026, 1, 1, tzinfo=UTC),
    )


def _identity(graph: AppContextGraph):
    return build_graph_snapshot_identity(
        graph,
        repository_scope="repo",
        baseline_commit_sha="66eae95091e6083260539ab82f1b60e756f7a244",
        parser_manifest={"active": "test", "generated_at": "ignored"},
        scan_health={"status": "current", "indexed_at": "ignored"},
        provenance={"source": "fixture"},
    )


def _query(graph: AppContextGraph, seed: str, **overrides) -> ImpactQuery:
    identity = _identity(graph)
    defaults = {
        "snapshot_digest": identity.identity_digest,
        "seed_node_ids": [seed],
        "allowed_edge_types": [
            GraphEdgeType.DECLARES,
            GraphEdgeType.CALLS,
            GraphEdgeType.PRODUCES,
            GraphEdgeType.CONSUMES,
            GraphEdgeType.TRIGGERS,
            GraphEdgeType.RENDERS,
            GraphEdgeType.PROTECTED_BY,
            GraphEdgeType.GENERATED_FROM,
        ],
        "direction": ImpactDirection.BOTH,
        "max_depth": 4,
        "max_results": 50,
    }
    defaults.update(overrides)
    return ImpactQuery(**defaults)


def _node(graph: AppContextGraph, node_type: GraphNodeType, label: str | None = None) -> AppContextGraphNode:
    for node in graph.nodes:
        if node.node_type == node_type and (label is None or node.label == label):
            return node
    raise AssertionError(f"missing node {node_type} {label}")


def test_snapshot_query_and_report_digests_are_stable_and_exclude_timestamps() -> None:
    graph_a = _graph(indexed_at=datetime(2026, 1, 1, tzinfo=UTC))
    graph_b = _graph(indexed_at=datetime(2026, 1, 2, tzinfo=UTC))
    identity_a = _identity(graph_a)
    identity_b = _identity(graph_b)
    assert identity_a.graph_content_digest == identity_b.graph_content_digest
    assert identity_a.source_manifest_digest == identity_b.source_manifest_digest
    assert identity_a.identity_digest == identity_b.identity_digest

    seed = _node(graph_a, GraphNodeType.ROUTE, "/orders").node_id
    query_a = _query(graph_a, seed)
    query_b = _query(graph_b, seed)
    assert impact_query_digest(query_a) == impact_query_digest(query_b)
    report_a = analyze_impact(graph=graph_a, snapshot_identity=identity_a, query=query_a)
    report_b = analyze_impact(graph=graph_b, snapshot_identity=identity_b, query=query_b)
    assert report_a.report_digest == report_b.report_digest


def test_insertion_order_independence() -> None:
    ordered = _fixture_file_map()
    reversed_map = dict(reversed(list(ordered.items())))
    graph_a = _graph(ordered)
    graph_b = _graph(reversed_map)
    assert _identity(graph_a).graph_content_digest == _identity(graph_b).graph_content_digest


def test_unknown_seeds_and_unknown_types_are_rejected() -> None:
    graph = _graph()
    identity = _identity(graph)
    with pytest.raises(ValueError, match="seed_node_ids"):
        analyze_impact(
            graph=graph,
            snapshot_identity=identity,
            query=ImpactQuery(
                snapshot_digest=identity.identity_digest,
                seed_node_ids=["missing"],
                allowed_edge_types=[GraphEdgeType.CALLS],
            ),
        )
    with pytest.raises(ValidationError):
        ImpactQuery(
            snapshot_digest=identity.identity_digest,
            seed_node_ids=["app"],
            allowed_edge_types=["not-an-edge"],
        )


def test_bounded_traversal_directionality_and_cycle_handling() -> None:
    nodes = [
        AppContextGraphNode(node_id="a", node_type=GraphNodeType.FILE, label="a.py", metadata={"path": "a.py"}),
        AppContextGraphNode(node_id="b", node_type=GraphNodeType.FILE, label="b.py", metadata={"path": "b.py"}),
        AppContextGraphNode(node_id="c", node_type=GraphNodeType.FILE, label="c.py", metadata={"path": "c.py"}),
    ]
    graph = AppContextGraph(
        graph_id="g",
        app_id="demo",
        indexed_at=datetime.now(UTC),
        nodes=nodes,
        edges=[
            AppContextGraphEdge(edge_type=GraphEdgeType.IMPORTS, source_node_id="a", target_node_id="b"),
            AppContextGraphEdge(edge_type=GraphEdgeType.IMPORTS, source_node_id="b", target_node_id="c"),
            AppContextGraphEdge(edge_type=GraphEdgeType.IMPORTS, source_node_id="c", target_node_id="a"),
        ],
    )
    identity = _identity(graph)
    shallow = ImpactQuery(
        snapshot_digest=identity.identity_digest,
        seed_node_ids=["a"],
        allowed_edge_types=[GraphEdgeType.IMPORTS],
        direction=ImpactDirection.OUTBOUND,
        max_depth=1,
    )
    assert analyze_impact(graph=graph, snapshot_identity=identity, query=shallow).affected_node_ids == ["a", "b"]

    inbound = shallow.model_copy(update={"direction": ImpactDirection.INBOUND})
    assert analyze_impact(graph=graph, snapshot_identity=identity, query=inbound).affected_node_ids == ["a", "c"]

    cyclic = shallow.model_copy(update={"max_depth": 8})
    report = analyze_impact(graph=graph, snapshot_identity=identity, query=cyclic)
    assert report.affected_node_ids == ["a", "b", "c"]
    assert max(item.distance for item in report.evidence) <= 2


def test_evidence_paths_and_authentic_route_event_reaction_edges() -> None:
    graph = _graph()
    identity = _identity(graph)
    route = _node(graph, GraphNodeType.ROUTE, "/orders")
    report = analyze_impact(graph=graph, snapshot_identity=identity, query=_query(graph, route.node_id))
    edge_relationships = {
        tuple(evidence.relationship_types): evidence.affected_node_id
        for evidence in report.evidence
        if evidence.relationship_types
    }
    assert any(GraphEdgeType.CALLS in relationship for relationship in edge_relationships)
    assert any(GraphEdgeType.PRODUCES in relationship for relationship in edge_relationships)
    assert all(evidence.authority == ParserAuthority.AUTHORITATIVE for evidence in report.evidence)

    module = _node(graph, GraphNodeType.MODULE, "orders")
    module_report = analyze_impact(
        graph=graph,
        snapshot_identity=identity,
        query=_query(graph, module.node_id, direction=ImpactDirection.OUTBOUND),
    )
    assert any(GraphEdgeType.CONSUMES in evidence.relationship_types for evidence in module_report.evidence)

    event = _node(graph, GraphNodeType.EVENT, "domain.order.created")
    event_report = analyze_impact(
        graph=graph,
        snapshot_identity=identity,
        query=_query(graph, event.node_id, direction=ImpactDirection.OUTBOUND),
    )
    assert any(GraphEdgeType.TRIGGERS in evidence.relationship_types for evidence in event_report.evidence)


def test_capability_provenance_and_schema_governance_edges() -> None:
    graph = _graph()
    relationships = {
        edge.metadata.get("relationship")
        for edge in graph.edges
        if edge.metadata.get("relationship")
    }
    assert "capability_pack_owns_output" in relationships
    assert "generated_artifact_derives_from_provenance" in relationships
    assert "schema_governs_manifest" in relationships


def test_ambiguity_degradation_and_file_level_fallback() -> None:
    graph = AppContextGraph(
        graph_id="g",
        app_id="demo",
        indexed_at=datetime.now(UTC),
        nodes=[
            AppContextGraphNode(
                node_id="file:ui",
                node_type=GraphNodeType.FILE,
                label="ui.jsx",
                metadata={"path": "ui.jsx"},
            ),
            AppContextGraphNode(
                node_id="symbol:ui:Orders",
                node_type=GraphNodeType.SYMBOL,
                label="Orders",
                metadata={"path": "ui.jsx", "parser": "regex", "qualified_name": "Orders"},
            ),
        ],
        edges=[
            AppContextGraphEdge(edge_type=GraphEdgeType.DEFINES, source_node_id="file:ui", target_node_id="symbol:ui:Orders"),
        ],
    )
    symbol = _node(graph, GraphNodeType.SYMBOL, "Orders")
    identity = _identity(graph)
    with pytest.raises(ValueError, match="Symbol-level"):
        analyze_impact(
            graph=graph,
            snapshot_identity=identity,
            query=_query(graph, symbol.node_id, allowed_node_kinds=[GraphNodeType.SYMBOL]),
        )

    file_node = _node(graph, GraphNodeType.FILE, "ui.jsx")
    report = analyze_impact(
        graph=graph,
        snapshot_identity=identity,
        query=_query(graph, file_node.node_id, allowed_node_kinds=[GraphNodeType.FILE]),
    )
    assert file_node.node_id in report.affected_node_ids

    unknown = AppContextGraphNode(node_id="unknown:x", node_type=GraphNodeType.UNKNOWN, label="x", metadata={"resolved": False})
    ambiguous = graph.model_copy(
        update={
            "nodes": [*graph.nodes, unknown],
            "edges": [
                *graph.edges,
                AppContextGraphEdge(edge_type=GraphEdgeType.REFERENCES, source_node_id=file_node.node_id, target_node_id=unknown.node_id),
            ],
            "indexed_at": graph.indexed_at + timedelta(days=1),
        }
    )
    ambiguous_identity = _identity(ambiguous)
    ambiguous_report = analyze_impact(
        graph=ambiguous,
        snapshot_identity=ambiguous_identity,
        query=ImpactQuery(
            snapshot_digest=ambiguous_identity.identity_digest,
            seed_node_ids=[file_node.node_id],
            allowed_edge_types=[GraphEdgeType.REFERENCES],
            max_depth=1,
        ),
    )
    assert "unknown_node:unknown:x" in ambiguous_report.ambiguity_risks


def test_strict_unknown_fields_no_external_service_and_graph_immutable() -> None:
    with pytest.raises(ValidationError):
        ImpactQuery(
            snapshot_digest="sha256:test",
            seed_node_ids=["app"],
            allowed_edge_types=[GraphEdgeType.CALLS],
            arbitrary=True,
        )

    graph = _graph()
    before = graph.model_dump(mode="json")
    identity = _identity(graph)
    query = _query(graph, "app", max_depth=1)
    analyze_impact(graph=graph, snapshot_identity=identity, query=query)
    assert graph.model_dump(mode="json") == before

    source = __import__("pathlib").Path("mozaiksai/core/app_context/impact_analysis.py").read_text()
    assert "openai" not in source.lower()
    assert "requests" not in source.lower()
    assert "httpx" not in source.lower()


def test_snapshot_mismatch_rejected() -> None:
    """Wrong identity_digest raises ValueError; graph_content_digest alone is insufficient."""
    graph = _graph()
    identity = _identity(graph)
    seed = _node(graph, GraphNodeType.ROUTE, "/orders").node_id

    # Totally wrong digest — must fail regardless of graph content match
    with pytest.raises(ValueError, match="identity_digest"):
        analyze_impact(
            graph=graph,
            snapshot_identity=identity,
            query=ImpactQuery(
                snapshot_digest="sha256:" + "0" * 64,
                seed_node_ids=[seed],
                allowed_edge_types=[GraphEdgeType.CALLS],
            ),
        )

    # graph_content_digest is NOT sufficient — identity_digest covers parser+baseline too
    assert identity.graph_content_digest != identity.identity_digest
    with pytest.raises(ValueError, match="identity_digest"):
        analyze_impact(
            graph=graph,
            snapshot_identity=identity,
            query=ImpactQuery(
                snapshot_digest=identity.graph_content_digest,
                seed_node_ids=[seed],
                allowed_edge_types=[GraphEdgeType.CALLS],
            ),
        )


def test_stable_evidence_ordering() -> None:
    """Multiple analyze_impact calls on the same inputs produce identical ordered output."""
    graph = _graph()
    identity = _identity(graph)
    seed = _node(graph, GraphNodeType.MODULE, "orders").node_id
    query = _query(graph, seed)

    report_a = analyze_impact(graph=graph, snapshot_identity=identity, query=query)
    report_b = analyze_impact(graph=graph, snapshot_identity=identity, query=query)

    assert report_a.report_digest == report_b.report_digest
    assert report_a.affected_node_ids == report_b.affected_node_ids
    assert [e.affected_node_id for e in report_a.evidence] == [e.affected_node_id for e in report_b.evidence]
    assert [e.distance for e in report_a.evidence] == [e.distance for e in report_b.evidence]


def test_degraded_parser_propagation() -> None:
    """DEGRADED authority on a symbol node propagates through evidence for that node."""
    graph = AppContextGraph(
        graph_id="g",
        app_id="demo",
        indexed_at=datetime.now(UTC),
        nodes=[
            AppContextGraphNode(
                node_id="file:src",
                node_type=GraphNodeType.FILE,
                label="src.py",
                metadata={"path": "src.py"},
            ),
            AppContextGraphNode(
                node_id="symbol:src:Foo",
                node_type=GraphNodeType.SYMBOL,
                label="Foo",
                metadata={"path": "src.py", "parser": "regex", "qualified_name": "Foo"},
            ),
        ],
        edges=[
            AppContextGraphEdge(
                edge_type=GraphEdgeType.DEFINES,
                source_node_id="file:src",
                target_node_id="symbol:src:Foo",
            ),
        ],
    )
    identity = _identity(graph)
    file_node = _node(graph, GraphNodeType.FILE, "src.py")

    # File-level query succeeds and reaches the symbol node via DEFINES
    report = analyze_impact(
        graph=graph,
        snapshot_identity=identity,
        query=_query(graph, file_node.node_id, allowed_edge_types=[GraphEdgeType.DEFINES]),
    )
    symbol_evidence = [e for e in report.evidence if e.affected_node_id == "symbol:src:Foo"]
    assert symbol_evidence, "Symbol node must be reachable from file node via DEFINES edge"
    assert symbol_evidence[0].authority == ParserAuthority.DEGRADED
    assert symbol_evidence[0].degradation_reason is not None
    assert report.parser_degradation, "Parser degradation should be surfaced in report"


def test_impact_report_serialization_round_trip() -> None:
    """ImpactReport round-trips through model_dump/model_validate without loss."""
    graph = _graph()
    identity = _identity(graph)
    seed = _node(graph, GraphNodeType.ROUTE, "/orders").node_id
    report = analyze_impact(graph=graph, snapshot_identity=identity, query=_query(graph, seed))

    dumped = report.model_dump(mode="json")
    restored = ImpactReport.model_validate(dumped)

    assert restored.report_digest == report.report_digest
    assert restored.affected_node_ids == report.affected_node_ids
    assert restored.snapshot_identity.identity_digest == identity.identity_digest
    assert len(restored.evidence) == len(report.evidence)
    for orig, back in zip(report.evidence, restored.evidence, strict=True):
        assert orig.affected_node_id == back.affected_node_id
        assert orig.authority == back.authority


def test_symbol_authority_failure_bounded_to_allowed_edge_types() -> None:
    """_symbol_authority_failures must not expand candidates via edges outside allowed_edge_types."""
    nodes = [
        AppContextGraphNode(
            node_id="file:a",
            node_type=GraphNodeType.FILE,
            label="a.py",
            metadata={"path": "a.py"},
        ),
        AppContextGraphNode(
            node_id="symbol:a:Foo",
            node_type=GraphNodeType.SYMBOL,
            label="Foo",
            metadata={"path": "a.py", "parser": "tree_sitter", "qualified_name": "Foo"},
        ),
        AppContextGraphNode(
            node_id="symbol:a:Bar",
            node_type=GraphNodeType.SYMBOL,
            label="Bar",
            # regex parser → DEGRADED; only reachable via REFERENCES, not DEFINES
            metadata={"path": "a.py", "parser": "regex", "qualified_name": "Bar"},
        ),
    ]
    graph = AppContextGraph(
        graph_id="g",
        app_id="demo",
        indexed_at=datetime.now(UTC),
        nodes=nodes,
        edges=[
            AppContextGraphEdge(
                edge_type=GraphEdgeType.DEFINES,
                source_node_id="file:a",
                target_node_id="symbol:a:Foo",
            ),
            # Bar is only reachable via REFERENCES — not in allowed_edge_types
            AppContextGraphEdge(
                edge_type=GraphEdgeType.REFERENCES,
                source_node_id="symbol:a:Foo",
                target_node_id="symbol:a:Bar",
            ),
        ],
    )
    identity = _identity(graph)
    symbol_foo = _node(graph, GraphNodeType.SYMBOL, "Foo")

    # Querying for symbols via DEFINES only — Bar is not reachable via allowed edges
    # so its DEGRADED authority must NOT block this query
    report = analyze_impact(
        graph=graph,
        snapshot_identity=identity,
        query=ImpactQuery(
            snapshot_digest=identity.identity_digest,
            seed_node_ids=[symbol_foo.node_id],
            allowed_edge_types=[GraphEdgeType.DEFINES],
            allowed_node_kinds=[GraphNodeType.SYMBOL],
        ),
    )
    assert symbol_foo.node_id in report.affected_node_ids

from __future__ import annotations

from pathlib import Path

from mozaiksai.control_plane.app_context import AppContextRefSummary, AppContextSummary
from mozaiksai.control_plane.app_context_impact import (
    APP_CONTEXT_GRAPH_MISSING_WARNING,
    APP_CONTEXT_GRAPH_STALE_WARNING,
    derive_app_context_impact_hints,
)
from mozaiksai.core.app_context.models import (
    AppContextGraph,
    AppContextGraphEdge,
    AppContextGraphNode,
    AppContextStaleStatus,
    GraphEdgeType,
    GraphNodeType,
)

ROOT = Path(__file__).resolve().parents[1]


def _node(
    node_id: str,
    node_type: GraphNodeType,
    *,
    label: str | None = None,
    metadata: dict | None = None,
) -> AppContextGraphNode:
    return AppContextGraphNode(
        node_id=node_id,
        node_type=node_type,
        label=label,
        source_ref_id="src_app_bundle",
        artifact_version_id="av_graph",
        stale_status=AppContextStaleStatus.CURRENT,
        metadata=metadata or {},
    )


def _edge(
    source: str,
    target: str,
    edge_type: GraphEdgeType,
) -> AppContextGraphEdge:
    return AppContextGraphEdge(
        edge_id=f"edge:{edge_type.value}:{source}:{target}",
        edge_type=edge_type,
        source_node_id=source,
        target_node_id=target,
        source_ref_id="src_app_bundle",
        artifact_version_id="av_graph",
        stale_status=AppContextStaleStatus.CURRENT,
    )


def _graph(
    *,
    nodes: list[AppContextGraphNode],
    edges: list[AppContextGraphEdge],
    stale_status: AppContextStaleStatus = AppContextStaleStatus.CURRENT,
) -> AppContextGraph:
    return AppContextGraph(
        graph_id="graph_sample_app",
        app_id="sample_app",
        nodes=nodes,
        edges=edges,
        stale_status=stale_status,
        graph_hash="sha256:test",
    )


def _summary(
    stale_status: str = "current",
) -> AppContextSummary:
    return AppContextSummary(
        app_id="sample_app",
        available=True,
        context_version_id="ctx_sample_app",
        mode="greenfield",
        stale_status=stale_status,
        artifact_refs=[
            AppContextRefSummary(
                ref_id="av_graph",
                kind="app_context_graph",
                target="app_context_graph",
            )
        ],
    )


def test_missing_graph_returns_unavailable_without_failure() -> None:
    affected_paths = ["ui/pages/dashboard.yaml"]

    hints = derive_app_context_impact_hints(
        app_context_graph=None,
        request="Change dashboard route layout",
        affected_bundle_paths=affected_paths,
    )

    assert hints.available is False
    assert hints.additional_path_hints == []
    assert APP_CONTEXT_GRAPH_MISSING_WARNING in hints.explanations
    assert affected_paths == ["ui/pages/dashboard.yaml"]


def test_stale_graph_warns_and_does_not_add_path_hints() -> None:
    graph = _graph(
        stale_status=AppContextStaleStatus.STALE,
        nodes=[
            _node(
                "page:dashboard",
                GraphNodeType.COMPONENT,
                label="dashboard page",
                metadata={"path": "ui/pages/dashboard.yaml"},
            )
        ],
        edges=[],
    )

    hints = derive_app_context_impact_hints(
        app_context_graph=graph,
        app_context_summary=_summary("stale"),
        request="Change dashboard route layout",
        affected_bundle_paths=[],
    )

    assert hints.available is True
    assert hints.stale_graph_warning == APP_CONTEXT_GRAPH_STALE_WARNING
    assert hints.additional_path_hints == []
    assert hints.related_nodes == []


def test_ui_route_graph_returns_related_nodes_edges_and_safe_path_hints() -> None:
    graph = _graph(
        nodes=[
            _node("route:dashboard", GraphNodeType.ROUTE, label="dashboard route"),
            _node(
                "page:dashboard",
                GraphNodeType.COMPONENT,
                label="dashboard page",
                metadata={"path": "ui/pages/dashboard.yaml"},
            ),
            _node(
                "component:dashboard_layout",
                GraphNodeType.COMPONENT,
                label="DashboardLayout",
                metadata={"path": "ui/pages/custom/DashboardLayout.jsx"},
            ),
        ],
        edges=[
            _edge("route:dashboard", "page:dashboard", GraphEdgeType.RENDERS),
            _edge("page:dashboard", "component:dashboard_layout", GraphEdgeType.RENDERS),
        ],
    )
    affected_paths = ["ui/route_manifest.json"]

    hints = derive_app_context_impact_hints(
        app_context_graph=graph,
        app_context_summary=_summary(),
        request="Change dashboard route layout",
        affected_bundle_paths=affected_paths,
        change_class="design",
        refinement_lane="ui_patch",
    )

    assert {"route", "component"} <= {node.node_type for node in hints.related_nodes}
    assert {edge.edge_type for edge in hints.related_edges} == {"renders"}
    assert "ui/pages/dashboard.yaml" in hints.additional_path_hints
    assert "ui/pages/custom/DashboardLayout.jsx" in hints.additional_path_hints
    assert affected_paths == ["ui/route_manifest.json"]


def test_module_api_graph_returns_module_and_action_hints() -> None:
    graph = _graph(
        nodes=[
            _node(
                "module:projects",
                GraphNodeType.MODULE,
                label="projects module",
                metadata={"path": "modules/projects/module.yaml"},
            ),
            _node(
                "api:archive_project",
                GraphNodeType.API_ENDPOINT,
                label="archive_project action",
                metadata={"path": "modules/projects/backend/handler.py"},
            ),
        ],
        edges=[_edge("module:projects", "api:archive_project", GraphEdgeType.CALLS)],
    )

    hints = derive_app_context_impact_hints(
        app_context_graph=graph,
        app_context_summary=_summary(),
        request="Add archive action to projects module",
        affected_bundle_paths=[],
        change_class="feature",
        refinement_lane="feature_addition",
    )

    assert {"module", "api_endpoint"} <= {node.node_type for node in hints.related_nodes}
    assert "modules/projects/module.yaml" in hints.additional_path_hints
    assert "modules/projects/backend/handler.py" in hints.additional_path_hints


def test_integration_graph_returns_integration_hints_without_secrets() -> None:
    graph = _graph(
        nodes=[
            _node(
                "module:reports",
                GraphNodeType.MODULE,
                label="reports module",
                metadata={"path": "modules/reports/backend/service.py"},
            ),
            _node(
                "integration:analytics_provider",
                GraphNodeType.INTEGRATION,
                label="analytics_provider",
                metadata={
                    "paths": [
                        "services/integrations/analytics_provider_client.py",
                        ".env",
                        "config/integrations.credentials.json",
                    ]
                },
            ),
        ],
        edges=[_edge("module:reports", "integration:analytics_provider", GraphEdgeType.USES_INTEGRATION)],
    )

    hints = derive_app_context_impact_hints(
        app_context_graph=graph,
        app_context_summary=_summary(),
        request="Change analytics_provider sync behavior",
        affected_bundle_paths=[],
        refinement_lane="integration",
    )

    assert "integration" in {node.node_type for node in hints.related_nodes}
    assert "services/integrations/analytics_provider_client.py" in hints.additional_path_hints
    assert all(".env" not in path for path in hints.additional_path_hints)
    assert all("credential" not in path.lower() for path in hints.additional_path_hints)


def test_data_entity_graph_returns_data_model_hints() -> None:
    graph = _graph(
        nodes=[
            _node(
                "module:projects",
                GraphNodeType.MODULE,
                label="projects module",
                metadata={
                    "paths": [
                        "modules/projects/backend/schemas.py",
                        "modules/projects/backend/repo.py",
                        "modules/projects/backend/policy.py",
                    ]
                },
            ),
            _node(
                "data:project",
                GraphNodeType.DATA_ENTITY,
                label="project record",
                metadata={"path": "data/contract.json"},
            ),
        ],
        edges=[
            _edge("module:projects", "data:project", GraphEdgeType.READS),
            _edge("module:projects", "data:project", GraphEdgeType.WRITES),
        ],
    )

    hints = derive_app_context_impact_hints(
        app_context_graph=graph,
        app_context_summary=_summary(),
        request="Add project phase field",
        affected_bundle_paths=[],
        refinement_lane="data_model_migration",
    )

    assert {"module", "data_entity"} <= {node.node_type for node in hints.related_nodes}
    assert "data/contract.json" in hints.additional_path_hints
    assert "modules/projects/backend/schemas.py" in hints.additional_path_hints


def test_brownfield_read_only_discovered_graph_returns_ownership_warning() -> None:
    graph = _graph(
        nodes=[
            _node(
                "file:legacy_orders_service",
                GraphNodeType.FILE,
                label="retired orders service",
                metadata={
                    "path": "src/orders/service.py",
                    "ownership": "read_only_discovered",
                },
            )
        ],
        edges=[],
    )

    hints = derive_app_context_impact_hints(
        app_context_graph=graph,
        app_context_summary=_summary(),
        request="Change retired orders service",
        affected_bundle_paths=[],
        change_class="feature",
    )

    assert hints.ownership_warnings
    assert "read_only_discovered" in hints.ownership_warnings[0]
    assert hints.additional_path_hints == ["src/orders/service.py"]


def test_unsafe_paths_are_filtered_from_hints() -> None:
    graph = _graph(
        nodes=[
            _node(
                "module:projects",
                GraphNodeType.MODULE,
                label="projects module",
                metadata={
                    "paths": [
                        "modules/projects/module.yaml",
                        ".env",
                        "secrets/api.json",
                        "config/credentials.json",
                        "/absolute/path.py",
                        "../outside.py",
                        "C:/workspace/app.py",
                        "vault/config.json",
                    ]
                },
            )
        ],
        edges=[],
    )

    hints = derive_app_context_impact_hints(
        app_context_graph=graph,
        app_context_summary=_summary(),
        request="Update projects module",
        affected_bundle_paths=[],
    )

    assert hints.additional_path_hints == ["modules/projects/module.yaml"]


def test_helper_does_not_modify_affected_paths_or_route_values() -> None:
    graph = _graph(
        nodes=[
            _node(
                "page:dashboard",
                GraphNodeType.COMPONENT,
                label="dashboard page",
                metadata={"path": "ui/pages/dashboard.yaml"},
            )
        ],
        edges=[],
    )
    affected_paths = ["ui/route_manifest.json"]
    workflow_sequence = "app_surface_revision"

    hints = derive_app_context_impact_hints(
        app_context_graph=graph,
        app_context_summary=_summary(),
        request="Change dashboard route layout",
        affected_bundle_paths=affected_paths,
    )

    assert hints.additional_path_hints == ["ui/pages/dashboard.yaml"]
    assert affected_paths == ["ui/route_manifest.json"]
    assert workflow_sequence == "app_surface_revision"
    assert "workflow_sequence" not in hints.model_dump()


def test_app_context_impact_helper_has_no_graph_database_or_proprietary_terms() -> None:
    paths = [
        ROOT / "mozaiksai/control_plane/app_context_impact.py",
        ROOT / "tests/test_app_context_impact_hints.py",
    ]
    forbidden_terms = (
        "Fal" + "kor",
        "Artifact" + "Store",
        "get_artifact" + "_store",
        "app " + "zero",
        "app_" + "zero",
        "mozaiks" + "-app",
        "mozaiks" + "pay",
    )

    for path in paths:
        text = path.read_text(encoding="utf-8")
        for term in forbidden_terms:
            assert term.lower() not in text.lower()


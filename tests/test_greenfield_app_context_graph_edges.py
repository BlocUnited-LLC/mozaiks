from __future__ import annotations

from pathlib import Path

from mozaiksai.control_plane.app_context import AppContextRefSummary, AppContextSummary
from mozaiksai.control_plane.app_context_impact import derive_app_context_impact_hints
from mozaiksai.core.app_context.models import GraphEdgeType, GraphNodeType
from mozaiksai.core.app_context.store import build_greenfield_app_context_from_app_bundle
from mozaiksai.core.artifacts.models import (
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    ArtifactVersionDoc,
)

ROOT = Path(__file__).resolve().parents[1]


def _manifest() -> list[dict[str, str]]:
    return [
        {"path": "SampleApp/app/app.json", "content": "{}"},
        {
            "path": "SampleApp/app/ui/route_manifest.json",
            "content": """
{
  "pages": [
    {
      "path": "/dashboard",
      "page": "dashboard",
      "component": "DashboardPage",
      "label": "Dashboard"
    }
  ]
}
""",
        },
        {
            "path": "SampleApp/app/ui/pages/dashboard.yaml",
            "content": """
name: dashboard
route: /dashboard
sections:
  - id: project-list
    primitive: ResourceTable
    config:
      api_endpoint: /api/modules/projects/list_projects
""",
        },
        {"path": "SampleApp/app/ui/pages/custom/DashboardPage.jsx", "content": "export default function DashboardPage() {}"},
        {
            "path": "SampleApp/app/modules/projects/module.yaml",
            "content": """
schema_version: mozaiks.module.v1
module:
  id: projects
  display_name: Projects
actions:
  - id: list_projects
    handler_method: list_projects
integrations:
  - id: analytics_provider
""",
        },
        {"path": "SampleApp/app/modules/projects/backend/handler.py", "content": "class Handler: pass"},
        {"path": "SampleApp/app/services/integrations/analytics_provider_client.py", "content": "class Client: pass"},
        {
            "path": "SampleApp/app/data/contract.json",
            "content": """
{
  "entities": [
    {
      "entity_id": "project_phase",
      "module_id": "projects",
      "operations": ["read", "write"]
    }
  ]
}
""",
        },
        {
            "path": "SampleApp/workflows/ProjectSummary/orchestrator.yaml",
            "content": """
workflow:
  id: ProjectSummary
module_ids:
  - projects
""",
        },
    ]


def _app_bundle() -> ArtifactVersionDoc:
    return ArtifactVersionDoc(
        _id="av_app_bundle_edges",
        app_id="sample_app",
        build_family="app_bundle",
        build_key="app_bundle",
        version_number=1,
        lineage_root_id="av_app_bundle_edges",
        lifecycle_status=ArtifactLifecycleStatus.CURRENT,
        validation_status=ArtifactValidationStatus.PASSED,
        files_manifest=[{"path": entry["path"]} for entry in _manifest()],
    )


def _drafts():
    return build_greenfield_app_context_from_app_bundle(
        app_bundle_artifact=_app_bundle(),
        files_manifest=_manifest(),
    )


def _edge_types_between(source_prefix: str, target_prefix: str) -> set[str]:
    graph = _drafts().app_context_graph
    return {
        edge.edge_type.value
        for edge in graph.edges
        if edge.source_node_id.startswith(source_prefix) and edge.target_node_id.startswith(target_prefix)
    }


def test_route_manifest_produces_route_node_and_route_page_render_edge() -> None:
    graph = _drafts().app_context_graph

    route_nodes = [
        node
        for node in graph.nodes
        if node.node_type is GraphNodeType.ROUTE and node.metadata.get("route_path") == "/dashboard"
    ]

    assert route_nodes
    assert "renders" in _edge_types_between("route:", "page:")
    assert all(edge.source_ref_id for edge in graph.edges)


def test_page_manifest_produces_page_node_with_file_path_metadata() -> None:
    graph = _drafts().app_context_graph
    page = next(node for node in graph.nodes if node.node_id.startswith("page:") and node.label == "dashboard")

    assert page.metadata["path"] == "ui/pages/dashboard.yaml"
    assert page.metadata["source_path"] == "ui/pages/dashboard.yaml"


def test_page_yaml_module_endpoint_produces_page_module_call_edge() -> None:
    drafts = _drafts()
    page = next(page for page in drafts.application_inventory.pages if page.location == "ui/pages/dashboard.yaml")

    assert page.metadata["module_ids"] == ["projects"]
    assert "calls" in _edge_types_between("page:", "module:")


def test_module_manifest_produces_module_node_and_generated_from_edge() -> None:
    graph = _drafts().app_context_graph
    module = next(node for node in graph.nodes if node.node_type is GraphNodeType.MODULE and node.label == "projects")

    assert module.metadata["path"] == "modules/projects/module.yaml"
    assert any(
        edge.edge_type is GraphEdgeType.GENERATED_FROM
        and edge.source_node_id == module.node_id
        and edge.metadata.get("source_path") == "modules/projects/module.yaml"
        for edge in graph.edges
    )


def test_integration_client_produces_integration_node_and_module_integration_edge() -> None:
    graph = _drafts().app_context_graph

    assert any(
        node.node_type is GraphNodeType.INTEGRATION and node.label == "analytics_provider"
        for node in graph.nodes
    )
    assert "uses_integration" in _edge_types_between("module:", "integration:")


def test_data_contract_produces_data_entity_and_explicit_read_write_edges() -> None:
    graph = _drafts().app_context_graph

    assert any(node.node_type is GraphNodeType.DATA_ENTITY and node.label == "project_phase" for node in graph.nodes)
    data_edge_types = _edge_types_between("module:", "data:")
    assert {"reads", "writes"} <= data_edge_types


def test_workflow_manifest_explicit_module_id_produces_workflow_module_dependency() -> None:
    graph = _drafts().app_context_graph

    assert any(node.node_type is GraphNodeType.WORKFLOW and node.label == "ProjectSummary" for node in graph.nodes)
    assert "depends_on" in _edge_types_between("workflow:", "module:")


def test_graph_is_deterministic_source_ref_backed_and_advisory_hints_use_edges() -> None:
    first = _drafts().app_context_graph
    second = _drafts().app_context_graph
    affected_paths = ["ui/route_manifest.json"]

    hints = derive_app_context_impact_hints(
        app_context_graph=first,
        app_context_summary=AppContextSummary(
            app_id="sample_app",
            available=True,
            context_version_id="ctx_sample",
            mode="greenfield",
            stale_status="current",
            artifact_refs=[
                AppContextRefSummary(
                    ref_id="av_graph",
                    kind="app_context_graph",
                    target="app_context_graph",
                )
            ],
        ),
        request="Change dashboard projects layout",
        affected_bundle_paths=affected_paths,
        change_class="design",
        refinement_lane="ui_patch",
    )

    assert first.graph_hash == second.graph_hash
    assert all(node.source_ref_id for node in first.nodes)
    assert all(edge.source_ref_id and edge.metadata.get("source_path") for edge in first.edges if edge.edge_type.value != "contains")
    assert "ui/pages/dashboard.yaml" in hints.additional_path_hints
    assert "modules/projects/module.yaml" in hints.additional_path_hints
    assert affected_paths == ["ui/route_manifest.json"]


def test_greenfield_graph_edge_enrichment_has_no_graph_database_or_proprietary_terms() -> None:
    paths = [
        ROOT / "mozaiksai/core/app_context/store.py",
        ROOT / "tests/test_greenfield_app_context_graph_edges.py",
    ]
    forbidden_terms = (
        "Fal" + "kor",
        "app " + "zero",
        "app_" + "zero",
        "mozaiks" + "-app",
        "mozaiks" + "pay",
    )

    for path in paths:
        text = path.read_text(encoding="utf-8").lower()
        for term in forbidden_terms:
            assert term.lower() not in text


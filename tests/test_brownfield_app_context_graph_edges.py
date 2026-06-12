from __future__ import annotations

import json
from pathlib import Path

from factory_app.workflows.ExistingAppDiscovery.tools import app_context_mapping
from mozaiksai.core.app_context.models import GraphEdgeType, GraphNodeType, OwnershipClass

ROOT = Path(__file__).resolve().parents[1]


def _decomposition_evidence() -> dict:
    return {
        "proposed_modules": [
            {
                "module_id": "work_order_manager",
                "source_capabilities": ["work_order_triage"],
                "source_files": ["src/work_orders/service.py"],
            }
        ],
        "proposed_pages": [
            {
                "page_id": "work_order_queue",
                "route": "/work-orders",
                "module_bindings": ["work_order_manager"],
            }
        ],
        "proposed_adapters": [
            {
                "provider_id": "email_gateway",
                "secret_requirements": ["EMAIL_API_KEY"],
            }
        ],
    }


def _discovery_output() -> dict:
    return {
        "request_intent": "brownfield_app",
        "existing_product_spec": {
            "app_name": "Operations Studio",
            "app_description": "Internal work-order operations app.",
            "tech_stack": "React, Node.js, PostgreSQL",
            "hosting_environment": "container platform",
            "api_surface": "REST endpoints under /api/work-orders",
            "service_surfaces": [
                {
                    "name": "Work Orders API",
                    "kind": "rest_api",
                    "location": "/api/work-orders",
                }
            ],
            "route_surfaces": [
                {
                    "path": "/work-orders",
                    "module": "work_orders",
                }
            ],
            "key_entities": [
                {
                    "name": "WorkOrder",
                    "description": "A field-service work order.",
                    "evidence_source": "repo scan",
                }
            ],
            "detected_connectors": [
                {
                    "provider_id": "email_gateway",
                    "package_or_import": "generic-mail-client",
                    "category": "email",
                    "source_files": ["src/integrations/email_gateway.py"],
                    "likely_secret_envs": ["EMAIL_API_KEY"],
                    "mozaiks_adapter_exists": False,
                }
            ],
            "storage_migration_required": True,
        },
        "capability_specs": [
            {
                "capability_id": "work_order_triage",
                "label": "Work Order Triage",
                "confidence": "confirmed",
                "delivery_surface": "rest_api",
                "entrypoints": ["/api/work-orders"],
                "entity_refs": ["WorkOrder"],
                "connector_requirements": ["email_gateway"],
                "migration_priority": "p1_critical",
            }
        ],
        "agent_augmentation_plan": {
            "adoption_level": "gradual_modernization",
            "storage_migration_required": True,
            "new_adapters_required": ["email_gateway"],
            "initial_workflows": ["WorkOrderSummary"],
        },
        "unresolved_questions": [
            {
                "question": "Does Work Order Triage require manager approval?",
                "context": "Affects staging risk for the work order module.",
                "priority": "high",
            }
        ],
        "artifact_version": "1.0",
    }


def _context() -> dict:
    return {
        "app_id": "ops_studio",
        "chat_id": "chat_ops_001",
        "repo_summary": {
            "repo_path": "repos/ops-studio",
            "repo_name": "ops-studio",
            "git_ref": "main",
            "checksum": "sha256:repo123",
        },
        "api_inventory": {
            "spec_location": "openapi/work-orders.json",
            "checksum": "sha256:api123",
            "endpoints": [
                {
                    "method": "GET",
                    "path": "/api/work-orders",
                    "operation_id": "list_work_orders",
                }
            ],
        },
        "module_decomposition_plan": json.dumps(_decomposition_evidence()),
    }


def _graph():
    artifacts = app_context_mapping.build_existing_app_context_artifacts(
        _discovery_output(),
        context_variables=_context(),
    )
    assert artifacts.app_context_graph is not None
    return artifacts.app_context_graph


def test_brownfield_graph_links_capabilities_to_discovered_surfaces() -> None:
    graph = _graph()
    capability_nodes = [node for node in graph.nodes if node.node_type is GraphNodeType.CAPABILITY]
    assert capability_nodes
    capability_node = capability_nodes[0]
    assert capability_node.metadata["capability_id"] == "work_order_triage"

    assert any(
        edge.source_node_id == capability_node.node_id
        and edge.edge_type is GraphEdgeType.USES_INTEGRATION
        and graph_node(graph, edge.target_node_id).node_type is GraphNodeType.INTEGRATION
        for edge in graph.edges
    )
    assert any(
        edge.source_node_id == capability_node.node_id
        and edge.edge_type is GraphEdgeType.CALLS
        and graph_node(graph, edge.target_node_id).node_type is GraphNodeType.API_ENDPOINT
        for edge in graph.edges
    )
    assert any(
        edge.source_node_id == capability_node.node_id
        and edge.edge_type is GraphEdgeType.DEPENDS_ON
        and graph_node(graph, edge.target_node_id).node_type is GraphNodeType.DATA_ENTITY
        for edge in graph.edges
    )


def test_brownfield_graph_links_routes_apis_risks_unknowns_and_ownership() -> None:
    graph = _graph()

    assert any(
        graph_node(graph, edge.source_node_id).node_type is GraphNodeType.ROUTE
        and edge.edge_type is GraphEdgeType.CALLS
        and graph_node(graph, edge.target_node_id).node_type is GraphNodeType.API_ENDPOINT
        for edge in graph.edges
    )
    assert any(node.node_type is GraphNodeType.RISK for node in graph.nodes)
    assert any(node.node_type is GraphNodeType.UNKNOWN for node in graph.nodes)
    assert any(
        graph_node(graph, edge.source_node_id).node_type is GraphNodeType.UNKNOWN
        and edge.edge_type is GraphEdgeType.BLOCKS
        and graph_node(graph, edge.target_node_id).node_type is GraphNodeType.CAPABILITY
        for edge in graph.edges
    )

    ownership_nodes = [
        node
        for node in graph.nodes
        if node.metadata.get("ownership") == OwnershipClass.READ_ONLY_DISCOVERED.value
    ]
    assert ownership_nodes
    assert any(node.metadata.get("source_path") == "src/integrations/email_gateway.py" for node in ownership_nodes)
    assert any(
        edge.source_node_id in {node.node_id for node in ownership_nodes}
        and edge.edge_type is GraphEdgeType.PROTECTED_BY
        for edge in graph.edges
    )


def test_internal_decomposition_evidence_enriches_graph_without_becoming_canonical() -> None:
    artifacts = app_context_mapping.build_existing_app_context_artifacts(
        _discovery_output(),
        context_variables=_context(),
    )
    graph = artifacts.app_context_graph
    assert graph is not None
    payloads = artifacts.as_artifact_payloads()

    internal_nodes = [node for node in graph.nodes if node.metadata.get("internal_evidence") is True]
    assert {node.metadata.get("candidate_type") for node in internal_nodes} >= {
        "module",
        "page",
        "adapter",
    }
    assert any(
        edge.metadata.get("internal_evidence") is True
        and edge.edge_type is GraphEdgeType.IMPLEMENTS_CAPABILITY
        for edge in graph.edges
    )
    assert "module_decomposition_plan" not in app_context_mapping.APP_CONTEXT_ARTIFACT_KINDS
    assert "module_decomposition_plan" not in payloads
    assert "module_decomposition_plan" not in json.dumps(payloads, default=str)
    assert "EMAIL_API_KEY" not in json.dumps(payloads, default=str)


def test_brownfield_graph_edges_are_provenance_backed_and_non_authoritative() -> None:
    graph = _graph()

    assert graph.stale_status.value == "unknown"
    assert all(edge.source_ref_id for edge in graph.edges)
    assert all(edge.metadata.get("provenance") for edge in graph.edges)
    assert all(node.source_ref_id for node in graph.nodes)
    assert not any("authoritative" in node.metadata for node in graph.nodes)


def test_brownfield_graph_mapping_has_no_graph_database_or_proprietary_terms() -> None:
    paths = [ROOT / "factory_app/workflows/ExistingAppDiscovery/tools/app_context_mapping.py"]
    forbidden_terms = (
        "Falkor",
        "app " + "zero",
        "app_" + "zero",
        "mozaiks" + "-app",
        "mozaiks" + "pay",
    )

    for path in paths:
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        assert "workflow_" + "sequence" not in text
        for term in forbidden_terms:
            assert term.lower() not in lowered


def graph_node(graph, node_id):
    return next(node for node in graph.nodes if node.node_id == node_id)


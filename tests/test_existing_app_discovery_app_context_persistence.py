from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

from factory_app.workflows.ExistingAppDiscovery.tools import app_context_mapping
from mozaiksai.core.app_context.models import (
    AdoptionPath,
    GraphEdgeType,
    GraphNodeType,
    OwnershipClass,
)
from mozaiksai.core.app_context.store import APP_CONTEXT_VERSION_ARTIFACT_KIND
from mozaiksai.core.artifacts.models import ArtifactLifecycleStatus, ArtifactValidationStatus

ROOT = Path(__file__).resolve().parents[1]
save_module = importlib.import_module(
    "factory_app.workflows.ExistingAppDiscovery.tools.save_existing_app_artifacts"
)


class _FakeArtifactStore:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.versions: dict[str, SimpleNamespace] = {}
        self._counter = 0

    async def create_artifact_version(self, **kwargs):
        self._counter += 1
        version_id = f"av_{kwargs['artifact_kind']}_{self._counter}"
        self.calls.append(kwargs)
        doc = SimpleNamespace(
            id=version_id,
            app_id=kwargs["app_id"],
            artifact_kind=kwargs["artifact_kind"],
            artifact_key=kwargs["artifact_key"],
            lifecycle_status=kwargs["lifecycle_status"],
            commit_metadata=SimpleNamespace(
                metadata=kwargs.get("commit_metadata", {}).get("metadata", {})
            ),
        )
        self.versions[version_id] = doc
        return doc

    async def get_artifact_version(self, *, app_id, artifact_version_id):
        artifact = self.versions.get(artifact_version_id)
        if artifact is None or artifact.app_id != app_id:
            return None
        return artifact

    async def accept_artifact_version(self, *, app_id, artifact_version_id):
        artifact = await self.get_artifact_version(
            app_id=app_id,
            artifact_version_id=artifact_version_id,
        )
        if artifact is None:
            return None
        for version in self.versions.values():
            if (
                version.app_id == app_id
                and version.artifact_kind == artifact.artifact_kind
                and version.artifact_key == artifact.artifact_key
                and version.id != artifact.id
                and version.lifecycle_status is ArtifactLifecycleStatus.CURRENT
            ):
                version.lifecycle_status = ArtifactLifecycleStatus.SUPERSEDED
        artifact.lifecycle_status = ArtifactLifecycleStatus.CURRENT
        return artifact


def _decomposition_evidence() -> dict:
    return {
        "proposed_modules": [
            {
                "module_id": "work_order_manager",
                "source_files": ["src/work_orders/service.py"],
            }
        ],
        "proposed_pages": [
            {
                "page_id": "work_order_queue",
                "route": "/work-orders",
            }
        ],
        "proposed_adapters": [
            {
                "provider_id": "email_gateway",
                "secret_requirements": ["EMAIL_API_KEY"],
            }
        ],
        "implementation_phases": ["review_inventory", "stage_adapter"],
    }


def _discovery_output() -> dict:
    return {
        "request_intent": "brownfield_app",
        "existing_product_spec": {
            "app_name": "Operations Studio",
            "app_description": "Internal work-order operations app.",
            "app_url": "https://ops.example.invalid",
            "tech_stack": "React, Node.js, PostgreSQL",
            "hosting_environment": "container platform",
            "auth_model": "OIDC",
            "api_surface": "REST endpoints under /api/work-orders",
            "service_surfaces": [
                {
                    "name": "Work Orders API",
                    "kind": "rest_api",
                    "location": "/api/work-orders",
                    "description": "Work-order management API.",
                }
            ],
            "route_surfaces": [
                {
                    "path": "/work-orders",
                    "module": "work_orders",
                    "description": "Work-order queue.",
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
                    "confidence": "confirmed",
                    "source_files": ["src/integrations/email_gateway.py"],
                    "likely_secret_envs": ["EMAIL_API_KEY"],
                    "mozaiks_adapter_exists": False,
                }
            ],
            "storage_pattern": "sql",
            "storage_migration_required": True,
        },
        "capability_specs": [
            {
                "capability_id": "work_order_triage",
                "label": "Work Order Triage",
                "confidence": "confirmed",
                "delivery_surface": "rest_api",
                "entrypoints": ["/api/work-orders"],
                "agent_ready": False,
                "migration_priority": "p1_critical",
                "connector_requirements": ["email_gateway"],
            }
        ],
        "agent_augmentation_plan": {
            "adoption_level": "gradual_modernization",
            "migration_complexity": "moderate",
            "adoption_rationale": "Move selected operations into reviewed owned modules over time.",
            "storage_migration_required": True,
            "new_adapters_required": ["email_gateway"],
            "auth_delegation_model": "user_token_forwarding",
            "ui_surface_preference": "admin_studio",
            "ai_accessible_capabilities": [],
            "initial_workflows": ["WorkOrderSummary"],
            "ecosystem_bindings": [],
            "theme_adaptation_strategy": "Use neutral admin shell styling for overlays.",
            "embed_theme_ready": False,
        },
        "discovery_brief": "Review work-order surfaces before staged modernization.",
        "unresolved_questions": [
            {
                "question": "Who owns the work-order status transition rules?",
                "context": "Ownership affects staged patch approval.",
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
            "source": "local_repo",
        },
        "scan_policy_ref": "scan_policy_ops",
        "preload_status": "ready",
        "source_context_bundle": {
            "schema_version": "mozaiks.source_context.bundle.v1",
            "bundle_id": "source_context_ops_studio",
            "app_id": "ops_studio",
            "source_refs": [],
            "indexed_at": "2026-01-01T00:00:00Z",
            "files": [],
            "chunks": [],
            "symbols": [],
            "imports": [],
            "file_contents": {},
            "health": {"source_context_file_count": 0},
            "warnings": [],
            "parser_status": {},
        },
        "app_intelligence_snapshot": {
            "schema_version": "mozaiks.app_intelligence.snapshot.v1",
            "snapshot_id": "app_intelligence_ops_studio",
            "app_id": "ops_studio",
            "indexed_at": "2026-01-01T00:00:00Z",
            "source_context_bundle_id": "source_context_ops_studio",
            "graph_id": "graph_ops_studio",
            "graph_hash": "sha256:graph",
            "source_ref_ids": [],
            "coverage": {"file_count": 0},
            "architecture": {},
            "capabilities": [],
            "ownership": {},
            "integration_surfaces": [],
            "data_surfaces": [],
            "risk_hints": [],
            "agent_context_policy": {"policy": "retrieve_not_dump"},
            "warnings": [],
            "metadata": {},
        },
        "module_decomposition_plan": json.dumps(_decomposition_evidence()),
        "structured_output": _discovery_output(),
    }


def test_mapper_derives_canonical_brownfield_contracts_without_secret_values() -> None:
    context = _context()

    artifacts = app_context_mapping.build_existing_app_context_artifacts(
        context["structured_output"],
        context_variables=context,
    )
    payloads = artifacts.as_artifact_payloads()

    assert artifacts.application_inventory.app_id == "ops_studio"
    assert artifacts.application_inventory.routes[0].location == "/work-orders"
    assert artifacts.application_inventory.data_entities[0].label == "WorkOrder"
    assert artifacts.integration_inventory[0].integration_id == "email_gateway"
    assert artifacts.integration_inventory[0].secret_required is True
    assert "EMAIL_API_KEY" not in json.dumps(payloads, default=str)

    assert any(
        boundary.ownership is OwnershipClass.READ_ONLY_DISCOVERED
        and boundary.path_or_artifact == "src/integrations/email_gateway.py"
        for boundary in artifacts.ownership_boundaries
    )
    assert artifacts.risk_report.unknowns[0].priority == "high"
    assert artifacts.risk_report.risks


def test_adoption_plan_translates_legacy_recommendation_without_canonicalizing_it() -> None:
    artifacts = app_context_mapping.build_existing_app_context_artifacts(
        _discovery_output(),
        context_variables=_context(),
    )
    payloads = artifacts.as_artifact_payloads()

    assert artifacts.adoption_plan.recommended_path is AdoptionPath.GRADUAL_MODERNIZATION
    assert "module:work_order_manager" in artifacts.adoption_plan.candidate_migrations
    assert "page:work_order_queue" in artifacts.adoption_plan.candidate_overlays
    assert "adapter:email_gateway" in artifacts.adoption_plan.candidate_adapters
    assert "native_migration" not in json.dumps(payloads, default=str)
    assert "module_decomposition_plan" not in payloads
    assert "module_decomposition_plan" not in json.dumps(payloads, default=str)
    assert "module_decomposition_plan" not in app_context_mapping.APP_CONTEXT_ARTIFACT_KINDS


def test_brownfield_registration_and_graph_are_draft_source_ref_backed() -> None:
    artifacts = app_context_mapping.build_existing_app_context_artifacts(
        _discovery_output(),
        context_variables=_context(),
    )

    assert artifacts.brownfield_registration.context_version_id
    assert artifacts.brownfield_registration.status.value == "pending"
    assert artifacts.brownfield_registration.scan_policy_ref == "scan_policy_ops"

    graph = artifacts.app_context_graph
    assert graph is not None
    assert graph.graph_hash and graph.graph_hash.startswith("sha256:")
    assert {node.node_type for node in graph.nodes} <= set(GraphNodeType)
    assert {edge.edge_type for edge in graph.edges} <= set(GraphEdgeType)
    assert all(node.source_ref_id for node in graph.nodes)
    assert all(edge.source_ref_id for edge in graph.edges)


def test_save_step_persists_draft_artifact_versions_and_preserves_existing_context(
    monkeypatch,
) -> None:
    context = _context()
    fake_store = _FakeArtifactStore()
    emitted = {}

    async def _fake_emit(component, payload, **kwargs):
        emitted["component"] = component
        emitted["payload"] = payload
        emitted["kwargs"] = kwargs

    monkeypatch.setattr(save_module, "emit_ui_surface", _fake_emit)
    monkeypatch.setattr(save_module, "get_artifact_store", lambda: fake_store)

    result = asyncio.run(save_module.save_existing_app_artifacts(context_variables=context))

    assert result["success"] is True
    assert context["existing_product_spec"]["app_name"] == "Operations Studio"
    assert context["capability_specs"][0]["capability_id"] == "work_order_triage"
    assert context["agent_augmentation_plan"]["adoption_level"] == "gradual_modernization"
    assert context["existing_app_discovery_artifact"]["request_intent"] == "brownfield_app"
    assert context["module_decomposition_plan"] == json.dumps(_decomposition_evidence())
    assert "module_decomposition_plan" not in context["existing_app_discovery_artifact"]
    assert emitted["component"] == "DiscoveryBriefCard"

    persisted_kinds = {call["artifact_kind"] for call in fake_store.calls}
    assert persisted_kinds == {
        *app_context_mapping.APP_CONTEXT_ARTIFACT_KINDS,
        APP_CONTEXT_VERSION_ARTIFACT_KIND,
    }
    assert "module_decomposition_plan" not in persisted_kinds
    assert context["brownfield_app_context_artifact_version_refs"]["application_inventory"]
    assert context["brownfield_app_context_artifact_version_refs"]["source_context_bundle"]
    assert context["brownfield_app_context_artifact_version_refs"]["app_intelligence_snapshot"]
    assert context["app_context_version_artifact_version_id"]
    assert context["current_app_context_version_id"] == context["brownfield_registration"][
        "context_version_id"
    ]
    assert context["app_context_version"]["mode"] == "brownfield"
    assert any(
        ref["artifact_kind"] == "source_context_bundle"
        for ref in context["app_context_version"]["artifact_refs"]
    )
    assert any(
        ref["artifact_kind"] == "app_intelligence_snapshot"
        for ref in context["app_context_version"]["artifact_refs"]
    )
    assert context["app_context_version"]["stale_status"] == "current"
    assert context["application_inventory"]["app_id"] == "ops_studio"
    assert context["ownership_boundary"]["ownership_boundaries"]
    assert context["integration_inventory"]["integrations"][0]["secret_required"] is True

    for call in fake_store.calls:
        assert call["lifecycle_status"] is ArtifactLifecycleStatus.DRAFT
        assert call["validation_status"] is ArtifactValidationStatus.PENDING
        assert call["source_workflow"] == "ExistingAppDiscovery"
        assert call["source_chat_id"] == "chat_ops_001"
        assert call["commit_metadata"]["metadata"]["source_workflow"] == "ExistingAppDiscovery"
        assert "EMAIL_API_KEY" not in json.dumps(call["commit_metadata"], default=str)
        assert "native_migration" not in json.dumps(call["commit_metadata"], default=str)
        assert "module_decomposition_plan" not in json.dumps(call["commit_metadata"], default=str)


def test_existing_app_context_persistence_has_no_graph_database_or_sequence_dependency() -> None:
    mapper_text = (
        ROOT / "factory_app/workflows/ExistingAppDiscovery/tools/app_context_mapping.py"
    ).read_text(encoding="utf-8")
    saver_text = (
        ROOT / "factory_app/workflows/ExistingAppDiscovery/tools/save_existing_app_artifacts.py"
    ).read_text(encoding="utf-8")
    combined = f"{mapper_text}\n{saver_text}"

    assert "Falkor" not in combined
    assert "workflow_sequence" not in combined


def test_existing_app_context_persistence_uses_neutral_oss_terms() -> None:
    paths = [
        ROOT / "factory_app/workflows/ExistingAppDiscovery/tools/app_context_mapping.py",
        ROOT / "tests/test_existing_app_discovery_app_context_persistence.py",
    ]
    forbidden_terms = (
        "app " + "zero",
        "app_" + "zero",
        "mozaiks" + "-app",
        "mozaiks" + "pay",
    )

    for path in paths:
        text = path.read_text(encoding="utf-8").lower()
        for term in forbidden_terms:
            assert term not in text


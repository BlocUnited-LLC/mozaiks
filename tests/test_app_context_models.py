from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mozaiksai.core.app_context.models import (
    AdoptionPath,
    AdoptionPlan,
    AllowedOperation,
    AppContextGraph,
    AppContextGraphEdge,
    AppContextGraphNode,
    AppContextMode,
    AppContextVersion,
    ApplicationInventory,
    BrownfieldRegistration,
    GraphEdgeType,
    GraphNodeType,
    IntegrationInventory,
    IntegrationReadinessStatus,
    OwnershipBoundary,
    OwnershipClass,
    RiskItem,
    RiskReport,
    SourceRef,
    SourceRefKind,
    SurfaceRef,
    UnknownItem,
)

ROOT = Path(__file__).resolve().parents[1]


def _source_ref() -> SourceRef:
    return SourceRef(
        source_ref_id="src_repo_1",
        kind=SourceRefKind.REPO,
        uri="https://example.invalid/repo.git",
        ref="main",
        checksum="sha256:abc123",
    )


def test_app_context_version_accepts_supported_modes() -> None:
    for mode in ("greenfield", "brownfield", "hybrid"):
        context = AppContextVersion(
            context_version_id=f"ctx_{mode}",
            app_id="app_1",
            mode=mode,
            source_refs=[_source_ref()],
            graph_snapshot_ref="graph_1",
        )

        assert context.mode is AppContextMode(mode)


def test_app_context_version_rejects_unknown_mode() -> None:
    with pytest.raises(ValidationError):
        AppContextVersion(
            context_version_id="ctx_unknown",
            app_id="app_1",
            mode="unknown",
        )


def test_ownership_boundary_accepts_all_canonical_ownership_classes() -> None:
    for ownership in (
        "read_only_discovered",
        "generated_overlay",
        "staged_patch",
        "migrated_owned",
        "external_system",
    ):
        boundary = OwnershipBoundary(
            path_or_artifact=f"surface/{ownership}",
            ownership=ownership,
            source_ref="src_repo_1",
            allowed_operations=[AllowedOperation.INSPECT],
            requires_review=True,
        )

        assert boundary.ownership is OwnershipClass(ownership)


def test_ownership_boundary_rejects_unknown_ownership_class() -> None:
    with pytest.raises(ValidationError):
        OwnershipBoundary(path_or_artifact="ui/pages/home.yaml", ownership="owned_by_default")


def test_app_context_graph_accepts_neutral_node_and_edge_types() -> None:
    assert {
        "app",
        "repo",
        "source_ref",
        "file",
        "route",
        "component",
        "api_endpoint",
        "data_entity",
        "integration",
        "capability",
        "module",
        "workflow",
        "generated_overlay",
        "staged_patch",
        "risk",
        "unknown",
    } <= {item.value for item in GraphNodeType}
    assert {
        "contains",
        "imports",
        "renders",
        "calls",
        "reads",
        "writes",
        "uses_integration",
        "protected_by",
        "implements_capability",
        "generated_from",
        "staged_patch_touches",
        "wraps",
        "replaces",
        "depends_on",
        "blocks",
    } <= {item.value for item in GraphEdgeType}

    graph = AppContextGraph(
        graph_id="graph_1",
        app_id="app_1",
        source_refs=[_source_ref()],
        nodes=[
            AppContextGraphNode(node_id="app", node_type=GraphNodeType.APP),
            AppContextGraphNode(node_id="repo", node_type=GraphNodeType.REPO),
            AppContextGraphNode(node_id="route", node_type=GraphNodeType.ROUTE),
            AppContextGraphNode(node_id="api", node_type=GraphNodeType.API_ENDPOINT),
            AppContextGraphNode(node_id="integration", node_type=GraphNodeType.INTEGRATION),
            AppContextGraphNode(node_id="capability", node_type=GraphNodeType.CAPABILITY),
            AppContextGraphNode(node_id="overlay", node_type=GraphNodeType.GENERATED_OVERLAY),
            AppContextGraphNode(node_id="patch", node_type=GraphNodeType.STAGED_PATCH),
            AppContextGraphNode(node_id="risk", node_type=GraphNodeType.RISK),
            AppContextGraphNode(node_id="unknown", node_type=GraphNodeType.UNKNOWN),
        ],
        edges=[
            AppContextGraphEdge(
                edge_type=GraphEdgeType.CONTAINS,
                source_node_id="app",
                target_node_id="repo",
            ),
            AppContextGraphEdge(
                edge_type=GraphEdgeType.CALLS,
                source_node_id="route",
                target_node_id="api",
            ),
            AppContextGraphEdge(
                edge_type=GraphEdgeType.USES_INTEGRATION,
                source_node_id="api",
                target_node_id="integration",
            ),
            AppContextGraphEdge(
                edge_type=GraphEdgeType.STAGED_PATCH_TOUCHES,
                source_node_id="patch",
                target_node_id="route",
            ),
            AppContextGraphEdge(
                edge_type=GraphEdgeType.BLOCKS,
                source_node_id="risk",
                target_node_id="patch",
            ),
        ],
        graph_hash="sha256:def456",
    )

    assert graph.nodes[0].node_type is GraphNodeType.APP
    assert graph.edges[-1].edge_type is GraphEdgeType.BLOCKS


def test_application_inventory_and_risk_report_contracts_are_neutral() -> None:
    inventory = ApplicationInventory(
        app_id="app_1",
        source_refs=[_source_ref()],
        stacks=["python", "react"],
        services=[SurfaceRef(surface_id="api", kind="service", location="services/api")],
        routes=[SurfaceRef(surface_id="home", kind="route", location="/")],
        api_endpoints=[SurfaceRef(surface_id="list_items", kind="api_endpoint", location="/api/items")],
        pages=[SurfaceRef(surface_id="home_page", kind="page", location="ui/pages/home.yaml")],
        components=[SurfaceRef(surface_id="item_table", kind="component", location="ui/components")],
        data_entities=[SurfaceRef(surface_id="item", kind="data_entity")],
        integrations=[
            IntegrationInventory(
                integration_id="analytics",
                provider_type="analytics",
                config_required=True,
            )
        ],
        deployment_boundaries=[SurfaceRef(surface_id="deploy", kind="deployment")],
        ci_boundaries=[SurfaceRef(surface_id="ci", kind="ci")],
        unknowns=[UnknownItem(item_id="unknown_auth", description="Auth boundary requires review.")],
    )
    report = RiskReport(
        risks=[RiskItem(risk_id="risk_auth", description="Auth ownership is not confirmed.")],
        unknowns=inventory.unknowns,
        blocked_paths=[".env"],
        secret_sensitive_paths=["config/secrets"],
        stale_context_warnings=["source ref changed"],
        recommended_next_steps=["refresh context before staging changes"],
    )

    assert inventory.integrations[0].provider_type == "analytics"
    assert report.risks[0].risk_id == "risk_auth"


def test_app_context_graph_rejects_invalid_edge_endpoint() -> None:
    with pytest.raises(ValidationError, match="existing node_id"):
        AppContextGraph(
            graph_id="graph_1",
            app_id="app_1",
            nodes=[AppContextGraphNode(node_id="app", node_type=GraphNodeType.APP)],
            edges=[
                AppContextGraphEdge(
                    edge_type=GraphEdgeType.CONTAINS,
                    source_node_id="app",
                    target_node_id="missing",
                )
            ],
        )


def test_app_context_graph_rejects_extra_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        AppContextGraph(
            graph_id="graph_1",
            app_id="app_1",
            unknown_field=True,
        )


def test_adoption_plan_does_not_expose_legacy_top_level_path() -> None:
    assert "native_migration" not in {item.value for item in AdoptionPath}

    with pytest.raises(ValidationError):
        AdoptionPlan(recommended_path="native_migration")

    plan = AdoptionPlan(
        recommended_path=AdoptionPath.GRADUAL_MODERNIZATION,
        candidate_migrations=["move reporting data behind a reviewed module boundary"],
    )
    assert plan.candidate_migrations


def test_brownfield_registration_references_context_version_id() -> None:
    registration = BrownfieldRegistration(
        registration_id="reg_1",
        app_id="app_1",
        source_refs=[_source_ref()],
        context_version_id="ctx_1",
        scan_policy_ref="scan_policy_1",
    )

    assert registration.context_version_id == "ctx_1"


def test_integration_inventory_excludes_secret_values() -> None:
    inventory = IntegrationInventory(
        integration_id="analytics",
        provider_type="analytics",
        config_required=True,
        secret_required=True,
        frontend_safe_config={"public_project_id": "project_1"},
        readiness_status=IntegrationReadinessStatus.CONFIG_REQUIRED,
        owner="external_system",
    )

    assert inventory.secret_required is True
    assert "secret_value" not in IntegrationInventory.model_fields

    with pytest.raises(ValidationError):
        IntegrationInventory(
            integration_id="analytics",
            provider_type="analytics",
            secret_value="do-not-store",
        )

    with pytest.raises(ValidationError, match="secret-sensitive"):
        IntegrationInventory(
            integration_id="analytics",
            provider_type="analytics",
            frontend_safe_config={"private_token": "do-not-store"},
        )


def test_app_context_models_serialize_to_json_compatible_dicts() -> None:
    context = AppContextVersion(
        context_version_id="ctx_1",
        app_id="app_1",
        mode=AppContextMode.HYBRID,
        source_refs=[_source_ref()],
        ownership_boundaries=[
            OwnershipBoundary(
                path_or_artifact="ui/pages/home.yaml",
                ownership=OwnershipClass.GENERATED_OVERLAY,
                allowed_operations=[AllowedOperation.PROMOTE],
            )
        ],
    )

    payload = context.model_dump(mode="json")

    assert payload["mode"] == "hybrid"
    assert payload["source_refs"][0]["kind"] == "repo"
    assert payload["ownership_boundaries"][0]["ownership"] == "generated_overlay"


def test_app_context_architecture_doc_mentions_model_location() -> None:
    doc = (ROOT / "docs/architecture/foundations/app-context-and-brownfield-adoption.md").read_text(
        encoding="utf-8"
    )

    assert "mozaiksai/core/app_context/models.py" in doc


def test_app_context_model_files_have_no_proprietary_terms() -> None:
    paths = [
        ROOT / "mozaiksai/core/app_context/models.py",
        ROOT / "mozaiksai/core/app_context/__init__.py",
    ]
    forbidden = (
        "app " + "zero",
        "app_" + "zero",
        "mozaiks" + "-app",
        "mozaiks" + "pay",
    )

    for path in paths:
        text = path.read_text(encoding="utf-8").lower()
        for term in forbidden:
            assert term not in text, f"{path.relative_to(ROOT)} contains {term!r}"


def test_app_context_model_files_do_not_canonicalize_legacy_placeholders() -> None:
    model_text = (ROOT / "mozaiksai/core/app_context/models.py").read_text(encoding="utf-8")
    legacy_light_sequence = "brownfield_" + "build_light"
    legacy_full_sequence = "brownfield_" + "build_full"

    for term in (
        legacy_light_sequence,
        legacy_full_sequence,
        "native_migration",
        "module_decomposition_plan",
        "workspace_app",
    ):
        assert term not in model_text

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from mozaiksai.control_plane import (
    ControlPlaneArtifactChangeRoutesManifest,
    ControlPlaneArtifactRoutingManifest,
    ControlPlaneChangeRouteManifest,
    ControlPlaneHarnessManifest,
    ControlPlaneManifest,
    ControlPlaneProfileInfo,
    ControlPlanePromptsManifest,
    ControlPlaneRoutingManifest,
    ControlPlaneToolsManifest,
    LoadedControlPlanePack,
    RefinementTriggerRouteResolver,
    load_control_plane_pack,
)


class _FakeChangeClassifier:
    def __init__(self, *, change_class: str, rationale: str) -> None:
        self._result = SimpleNamespace(
            change_class=change_class,
            rationale=rationale,
            confidence=0.92,
            signals=["test_signal"],
        )

    async def classify(self, **kwargs):  # noqa: ANN003
        return self._result


def _write_business_plan_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workflows_root = tmp_path / "workflows"
    registry_root = workflows_root / "extended_orchestration"
    registry_root.mkdir(parents=True)
    workflows = [
        "MarketResearch",
        "CustomerPersona",
        "BusinessModel",
        "FinancialModel",
        "RiskAnalysis",
        "ExecutiveSummary",
        "FinalMemoAssembly",
    ]
    sequences = [
        {
            "id": "business_plan_patch",
            "affected_declarative_families": ["business_plan_bundle"],
            "steps": [{"workflows": ["FinalMemoAssembly"]}],
        },
        {
            "id": "business_plan_design",
            "affected_declarative_families": ["executive_summary", "business_plan_bundle"],
            "steps": [{"workflows": ["ExecutiveSummary"]}, {"workflows": ["FinalMemoAssembly"]}],
        },
        {
            "id": "business_plan_feature",
            "affected_declarative_families": ["business_model", "financial_model", "business_plan_bundle"],
            "steps": [
                {"workflows": ["BusinessModel"]},
                {"workflows": ["FinancialModel"]},
                {"workflows": ["ExecutiveSummary"]},
                {"workflows": ["FinalMemoAssembly"]},
            ],
        },
        {
            "id": "business_plan_core",
            "affected_declarative_families": [
                "market_research",
                "customer_persona",
                "business_model",
                "financial_model",
                "executive_summary",
                "business_plan_bundle",
            ],
            "steps": [
                {"workflows": ["MarketResearch"]},
                {"workflows": ["CustomerPersona"]},
                {"workflows": ["BusinessModel"]},
                {"workflows": ["FinancialModel"]},
                {"workflows": ["RiskAnalysis"]},
                {"workflows": ["ExecutiveSummary"]},
                {"workflows": ["FinalMemoAssembly"]},
            ],
        },
        {
            "id": "executive_summary_patch",
            "affected_declarative_families": ["executive_summary"],
            "steps": [{"workflows": ["ExecutiveSummary"]}],
        },
    ]
    (registry_root / "extension_registry.json").write_text(
        json.dumps(
            {
                "version": 3,
                "workflows": [{"id": workflow} for workflow in workflows],
                "entrypoints": [],
                "workflow_sequences": sequences,
                "transitions": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(workflows_root))


def _pack() -> LoadedControlPlanePack:
    return LoadedControlPlanePack(
        path=Path("custom/control_plane"),
        manifest=ControlPlaneManifest(
            schema_version="mozaiks.control_plane",
            profile=ControlPlaneProfileInfo(
                id="business_plan",
                display_name="Business Plan Harness",
                description="Control-plane pack for a business-plan workflow app.",
            ),
            harness=ControlPlaneHarnessManifest(
                implementation="example.harness:Harness",
            ),
            routing=ControlPlaneRoutingManifest(
                default_artifact_kind="business_plan_bundle",
                artifacts=[
                    ControlPlaneArtifactRoutingManifest(
                        artifact_kind="business_plan_bundle",
                        label="business plan bundle",
                        routes=ControlPlaneArtifactChangeRoutesManifest(
                            patch=ControlPlaneChangeRouteManifest(
                                workflow_sequence="business_plan_patch",
                            ),
                            design=ControlPlaneChangeRouteManifest(
                                workflow_sequence="business_plan_design",
                            ),
                            feature=ControlPlaneChangeRouteManifest(
                                workflow_sequence="business_plan_feature",
                            ),
                            core=ControlPlaneChangeRouteManifest(
                                workflow_sequence="business_plan_core",
                            ),
                        ),
                    ),
                    ControlPlaneArtifactRoutingManifest(
                        artifact_kind="executive_summary",
                        label="executive summary",
                        routes=ControlPlaneArtifactChangeRoutesManifest(
                            patch=ControlPlaneChangeRouteManifest(
                                workflow_sequence="executive_summary_patch",
                            ),
                            design=ControlPlaneChangeRouteManifest(
                                workflow_sequence="business_plan_design",
                            ),
                            feature=ControlPlaneChangeRouteManifest(
                                workflow_sequence="business_plan_design",
                            ),
                            core=ControlPlaneChangeRouteManifest(
                                workflow_sequence="business_plan_core",
                            ),
                        ),
                    ),
                ],
            ),
            checkpoints=[],
        ),
        prompts=ControlPlanePromptsManifest(
            schema_version="mozaiks.control_plane.prompts",
            prompts=[],
        ),
        tools=ControlPlaneToolsManifest(
            schema_version="mozaiks.control_plane.tools",
            tools=[],
        ),
    )


@pytest.mark.asyncio
async def test_refinement_router_uses_pack_default_artifact_kind_for_core_reentry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_business_plan_registry(tmp_path, monkeypatch)
    resolver = RefinementTriggerRouteResolver(
        classifier=_FakeChangeClassifier(
            change_class="core",
            rationale="Changing the target customer invalidates the upstream build assumptions.",
        ),
        pack_loader=_pack,
    )

    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "raw_user_request": "Target enterprise banks instead of small businesses.",
            }
        },
        app_id="app_1",
        requested_workflow_id="FinalMemoAssembly",
    )

    assert request is not None
    assert request.artifact_kind == "business_plan_bundle"

    decision = await resolver.route(request)

    assert decision.workflow_id == "MarketResearch"
    assert decision.is_full_restart is True
    assert decision.impact_set.restart_from == "MarketResearch"
    assert decision.impact_set.affected_workflows[:2] == ["MarketResearch", "CustomerPersona"]
    assert decision.context_seed["artifact_kind"] == "business_plan_bundle"


@pytest.mark.asyncio
async def test_refinement_router_keeps_local_patch_in_declared_owner_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_business_plan_registry(tmp_path, monkeypatch)
    resolver = RefinementTriggerRouteResolver(
        classifier=_FakeChangeClassifier(
            change_class="patch",
            rationale="This only updates the executive summary title.",
        ),
        pack_loader=_pack,
    )

    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "executive_summary",
                "raw_user_request": "Change the title to 'The Future of Banking Automation.'",
            }
        },
        app_id="app_1",
        requested_workflow_id="FinalMemoAssembly",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.workflow_id == "ExecutiveSummary"
    assert decision.is_full_restart is False
    assert decision.impact_set.affected_workflows == ["ExecutiveSummary"]
    assert decision.impact_set.requires_replanning is False
    assert decision.context_seed["artifact_kind"] == "executive_summary"


@pytest.mark.asyncio
async def test_refinement_router_derives_route_from_workflow_sequence() -> None:
    app_root = Path(__file__).resolve().parents[1] / "factory_app" / "app"
    resolver = RefinementTriggerRouteResolver(
        classifier=_FakeChangeClassifier(
            change_class="design",
            rationale="The dashboard IA should be revised without changing the product concept.",
        ),
        pack_loader=lambda: load_control_plane_pack(app_root=app_root),
    )

    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "artifact_version_id": "av_app_1",
                "raw_user_request": "Restructure the app dashboard layout.",
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.workflow_id == "DesignDocs"
    assert decision.workflow_sequence == "app_surface_revision"
    assert decision.impact_set.workflow_sequence == "app_surface_revision"
    assert decision.impact_set.affected_workflows == ["DesignDocs", "AppGenerator"]
    assert decision.context_seed["workflow_sequence"] == "app_surface_revision"


@pytest.mark.asyncio
async def test_concept_core_routes_to_conceptual_replan() -> None:
    """concept/core in the factory pack routes to conceptual_replan (concept pivot),
    not full_rebuild (complete restart). full_rebuild remains available for other
    artifact kinds whose core class still points to it."""
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="core",
            rationale="This should be a marketplace, not a CRM.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "concept",
                "raw_user_request": "Actually this should be a marketplace, not a CRM.",
            }
        },
        app_id="app_1",
        requested_workflow_id="ValueEngine",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.workflow_sequence == "conceptual_replan"
    assert decision.workflow_id == "ValueEngine"
    assert decision.is_full_restart is True


@pytest.mark.asyncio
async def test_app_bundle_core_still_routes_to_full_rebuild() -> None:
    """app_bundle/core routes to full_rebuild — conceptual_replan is only for concept/core."""
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="core",
            rationale="The whole product direction needs to change.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Change the whole product direction.",
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.workflow_sequence == "full_rebuild"
    assert decision.workflow_id == "ValueEngine"
    assert decision.is_full_restart is True


@pytest.mark.asyncio
async def test_experience_spec_impact_uses_glob_hints_without_file_manifest() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="design",
            rationale="The dashboard experience should be revised.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Replace the dashboard experience.",
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.workflow_sequence == "app_surface_revision"
    assert decision.impact_set.affected_declarative_families == ["experience_spec", "app_bundle"]
    assert decision.impact_set.affected_bundle_paths == [
        "ui/pages/*.yaml",
        "ui/route_manifest.json",
    ]


@pytest.mark.asyncio
async def test_experience_spec_impact_uses_concrete_page_paths_from_manifest() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="design",
            rationale="The dashboard experience should be revised.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Replace the dashboard experience.",
                "extra": {
                    "files_manifest": [
                        {"path": "ui/pages/dashboard.yaml"},
                        {"path": "ui/pages/settings.yaml"},
                        {"path": "ui/route_manifest.json"},
                        {"path": "modules/projects/module.yaml"},
                        {"path": "ui/pages/dashboard.yaml"},
                    ]
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.impact_set.affected_bundle_paths == [
        "ui/pages/dashboard.yaml",
        "ui/pages/settings.yaml",
        "ui/route_manifest.json",
    ]


@pytest.mark.asyncio
async def test_experience_spec_impact_uses_artifact_version_file_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    store_mod = importlib.import_module("mozaiksai.core.artifacts.store")
    monkeypatch.setattr(store_mod.ArtifactStore, "__init__", lambda self: None)
    monkeypatch.setattr(
        store_mod.ArtifactStore,
        "get_artifact_version",
        AsyncMock(
            return_value=SimpleNamespace(
                files_manifest=[
                    SimpleNamespace(path="ui/pages/dashboard.yaml"),
                    SimpleNamespace(path="ui/route_manifest.json"),
                    SimpleNamespace(path="config/shell.json"),
                ]
            )
        ),
    )
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="design",
            rationale="The navigation model changes.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "artifact_version_id": "av_app_1",
                "raw_user_request": "Move the dashboard into top navigation.",
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.impact_set.affected_bundle_paths == [
        "ui/pages/dashboard.yaml",
        "ui/route_manifest.json",
        "config/shell.json",
    ]


@pytest.mark.asyncio
async def test_experience_spec_impact_includes_shell_only_for_navigation_signal() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="design",
            rationale="The navigation model changes.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Move settings into the sidebar navigation.",
                "extra": {
                    "files_manifest": [
                        {"path": "ui/pages/dashboard.yaml"},
                        {"path": "ui/route_manifest.json"},
                        {"path": "config/shell.json"},
                    ]
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.impact_set.affected_bundle_paths == [
        "ui/pages/dashboard.yaml",
        "ui/route_manifest.json",
        "config/shell.json",
    ]


@pytest.mark.asyncio
async def test_experience_spec_impact_includes_custom_route_files_only_when_present() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="design",
            rationale="The custom dashboard experience should be revised.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Replace the dashboard experience.",
                "extra": {
                    "files_manifest": [
                        {"path": "ui/pages/dashboard.yaml"},
                        {"path": "ui/route_manifest.json"},
                        {"path": "ui/index.js"},
                        {"path": "ui/pages/custom/Dashboard.jsx"},
                    ]
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.impact_set.affected_bundle_paths == [
        "ui/pages/dashboard.yaml",
        "ui/route_manifest.json",
        "ui/index.js",
        "ui/pages/custom/Dashboard.jsx",
    ]


@pytest.mark.asyncio
async def test_non_experience_patch_does_not_add_ui_surface_paths() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="patch",
            rationale="This is a small generated app bundle patch.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Fix a typo.",
                "extra": {
                    "files_manifest": [
                        {"path": "ui/pages/dashboard.yaml"},
                        {"path": "ui/route_manifest.json"},
                    ]
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.workflow_sequence == "app_revision"
    assert decision.impact_set.affected_declarative_families == ["app_bundle"]
    assert decision.impact_set.affected_bundle_paths == []


@pytest.mark.asyncio
async def test_module_impact_uses_exact_paths_for_known_module_from_manifest() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="feature",
            rationale="The projects module needs a new action.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Add an archive action to the projects module.",
                "extra": {
                    "files_manifest": [
                        {"path": "modules/projects/backend/service.py"},
                        {"path": "modules/projects/contracts/events.yaml"},
                        {"path": "modules/projects/module.yaml"},
                        {"path": "modules/projects/backend/handler.py"},
                        {"path": "modules/projects/backend/repo.py"},
                        {"path": "modules/projects/backend/policy.py"},
                        {"path": "modules/projects/backend/schemas.py"},
                        {"path": "modules/projects/contracts/reactions.yaml"},
                        {"path": "modules/projects/contracts/notifications.yaml"},
                        {"path": "modules/projects/contracts/settings.yaml"},
                        {"path": "modules/projects/contracts/admin.yaml"},
                        {"path": "modules/projects/runtime_extensions.yaml"},
                        {"path": "modules/orders/module.yaml"},
                        {"path": "ui/pages/projects.yaml"},
                        {"path": "modules/projects/backend/service.py"},
                    ]
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.workflow_sequence == "app_revision"
    assert decision.impact_set.affected_declarative_families == ["app_bundle"]
    assert decision.impact_set.affected_bundle_paths == [
        "modules/projects/module.yaml",
        "modules/projects/contracts/events.yaml",
        "modules/projects/contracts/reactions.yaml",
        "modules/projects/contracts/notifications.yaml",
        "modules/projects/contracts/settings.yaml",
        "modules/projects/contracts/admin.yaml",
        "modules/projects/backend/handler.py",
        "modules/projects/backend/service.py",
        "modules/projects/backend/repo.py",
        "modules/projects/backend/policy.py",
        "modules/projects/backend/schemas.py",
        "modules/projects/runtime_extensions.yaml",
    ]


@pytest.mark.asyncio
async def test_module_impact_includes_runtime_extensions_only_when_present() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="feature",
            rationale="The orders API needs a new endpoint.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Add a cancel endpoint to the orders API.",
                "extra": {
                    "files_manifest": [
                        {"path": "modules/orders/module.yaml"},
                        {"path": "modules/orders/backend/handler.py"},
                        {"path": "modules/orders/backend/service.py"},
                        {"path": "modules/projects/module.yaml"},
                        {"path": "modules/projects/runtime_extensions.yaml"},
                    ]
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.impact_set.affected_bundle_paths == [
        "modules/orders/module.yaml",
        "modules/orders/backend/handler.py",
        "modules/orders/backend/service.py",
    ]
    assert "modules/orders/runtime_extensions.yaml" not in decision.impact_set.affected_bundle_paths


@pytest.mark.asyncio
async def test_module_impact_uses_glob_hints_when_module_id_is_unknown() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="feature",
            rationale="A backend API extension is needed.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Add a backend API endpoint.",
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.impact_set.affected_bundle_paths == [
        "modules/*/module.yaml",
        "modules/*/contracts/*.yaml",
        "modules/*/backend/*.py",
    ]


@pytest.mark.asyncio
async def test_visual_ui_patch_does_not_trigger_module_paths() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="patch",
            rationale="This is a visual spacing patch.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Tighten visual spacing on the report cards.",
                "extra": {
                    "files_manifest": [
                        {"path": "modules/reports/module.yaml"},
                        {"path": "modules/reports/backend/service.py"},
                    ]
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.workflow_sequence == "app_revision"
    assert decision.impact_set.affected_bundle_paths == []


@pytest.mark.asyncio
async def test_module_impact_handles_multiple_module_ids_deterministically() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="feature",
            rationale="Two module APIs need to coordinate.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Connect the projects and orders APIs.",
                "extra": {
                    "files_manifest": [
                        {"path": "modules/orders/backend/service.py"},
                        {"path": "modules/projects/backend/service.py"},
                        {"path": "modules/orders/module.yaml"},
                        {"path": "modules/projects/module.yaml"},
                        {"path": "modules/orders/contracts/events.yaml"},
                        {"path": "modules/projects/backend/handler.py"},
                        {"path": "modules/orders/backend/service.py"},
                    ]
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.impact_set.affected_bundle_paths == [
        "modules/orders/module.yaml",
        "modules/orders/contracts/events.yaml",
        "modules/orders/backend/service.py",
        "modules/projects/module.yaml",
        "modules/projects/backend/handler.py",
        "modules/projects/backend/service.py",
    ]


@pytest.mark.asyncio
async def test_data_model_impact_includes_data_contract_migrations_and_known_module_paths() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="feature",
            rationale="The projects data model needs a migration.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Add an optional field to the projects data model.",
                "extra": {
                    "files_manifest": [
                        {"path": "config/data.json"},
                        {"path": "config/data_migrations/001_projects_status.json"},
                        {"path": "modules/projects/module.yaml"},
                        {"path": "modules/projects/contracts/events.yaml"},
                        {"path": "modules/projects/contracts/admin.yaml"},
                        {"path": "modules/projects/backend/schemas.py"},
                        {"path": "modules/projects/backend/repo.py"},
                        {"path": "modules/projects/backend/policy.py"},
                        {"path": "modules/orders/module.yaml"},
                        {"path": "modules/orders/backend/schemas.py"},
                        {"path": "ui/pages/projects.yaml"},
                        {"path": "secrets/projects.json"},
                    ]
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.impact_set.affected_bundle_paths == [
        "config/data.json",
        "config/data_migrations/001_projects_status.json",
        "modules/projects/module.yaml",
        "modules/projects/contracts/events.yaml",
        "modules/projects/contracts/admin.yaml",
        "modules/projects/backend/repo.py",
        "modules/projects/backend/policy.py",
        "modules/projects/backend/schemas.py",
    ]
    assert "modules/orders/backend/schemas.py" not in decision.impact_set.affected_bundle_paths
    assert "ui/pages/projects.yaml" not in decision.impact_set.affected_bundle_paths
    assert "secrets/projects.json" not in decision.impact_set.affected_bundle_paths
    assert "Data model migration impact detected." in decision.impact_set.scope_summary


@pytest.mark.asyncio
async def test_data_model_impact_unknown_module_uses_conservative_hints() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="feature",
            rationale="A persistence schema migration is needed.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Add a required field to the database schema.",
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.impact_set.affected_bundle_paths == [
        "config/data.json",
        "config/data_migrations/*.json",
        "modules/*/backend/schemas.py",
        "modules/*/backend/repo.py",
        "modules/*/backend/policy.py",
        "modules/*/module.yaml",
    ]


@pytest.mark.asyncio
async def test_data_model_impact_ui_request_includes_page_paths() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="feature",
            rationale="The customers field and form need a scoped revision.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Add a customers field and show it on the edit form.",
                "extra": {
                    "files_manifest": [
                        {"path": "config/data.json"},
                        {"path": "modules/customers/module.yaml"},
                        {"path": "modules/customers/backend/schemas.py"},
                        {"path": "modules/customers/backend/repo.py"},
                        {"path": "modules/customers/backend/policy.py"},
                        {"path": "ui/pages/customers.yaml"},
                    ]
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.impact_set.affected_bundle_paths == [
        "config/data.json",
        "config/data_migrations/*.json",
        "modules/customers/module.yaml",
        "modules/customers/backend/repo.py",
        "modules/customers/backend/policy.py",
        "modules/customers/backend/schemas.py",
        "ui/pages/customers.yaml",
    ]


@pytest.mark.asyncio
async def test_data_model_destructive_change_adds_review_warning() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="feature",
            rationale="The reports schema needs a destructive migration.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Remove field from reports and backfill existing records.",
                "extra": {
                    "files_manifest": [
                        {"path": "config/data.json"},
                        {"path": "modules/reports/module.yaml"},
                        {"path": "modules/reports/backend/schemas.py"},
                        {"path": "modules/reports/backend/repo.py"},
                        {"path": "modules/reports/backend/policy.py"},
                    ]
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert "Data model migration impact detected." in decision.impact_set.scope_summary
    assert "Destructive changes require explicit review." in decision.impact_set.scope_summary


@pytest.mark.asyncio
async def test_non_data_model_backend_request_does_not_include_database_paths() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="feature",
            rationale="A backend API extension is needed.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Add a tasks backend endpoint for closing tasks.",
                "extra": {
                    "files_manifest": [
                        {"path": "config/data.json"},
                        {"path": "config/data_migrations/001_tasks_status.json"},
                        {"path": "modules/tasks/module.yaml"},
                        {"path": "modules/tasks/backend/handler.py"},
                        {"path": "modules/tasks/backend/service.py"},
                        {"path": "modules/tasks/backend/repo.py"},
                        {"path": "modules/tasks/backend/policy.py"},
                        {"path": "modules/tasks/backend/schemas.py"},
                    ]
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.impact_set.affected_bundle_paths == [
        "modules/tasks/module.yaml",
        "modules/tasks/backend/handler.py",
        "modules/tasks/backend/service.py",
        "modules/tasks/backend/repo.py",
        "modules/tasks/backend/policy.py",
        "modules/tasks/backend/schemas.py",
    ]
    assert "config/data.json" not in decision.impact_set.affected_bundle_paths
    assert "config/data_migrations/001_tasks_status.json" not in decision.impact_set.affected_bundle_paths


@pytest.mark.asyncio
async def test_hosted_capability_impact_includes_adapter_facade_and_dependent_page() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="feature",
            rationale="Hosted analytics display needs a scoped revision.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Change how hosted analytics metrics display on the dashboard.",
                "extra": {
                    "files_manifest": [
                        {"path": "services/integrations/hosted_analytics_client.py"},
                        {"path": "services/integrations/reporting_provider_client.py"},
                        {"path": "modules/analytics_dashboard/backend/service.py"},
                        {"path": "modules/analytics_dashboard/module.yaml"},
                        {"path": "modules/analytics_dashboard/backend/handler.py"},
                        {"path": "modules/analytics_dashboard/contracts/events.yaml"},
                        {"path": "modules/hosted_analytics/module.yaml"},
                        {
                            "path": "ui/pages/analytics.yaml",
                            "content": "api_endpoint: /api/modules/analytics_dashboard/get_metrics",
                        },
                        {"path": "ui/pages/reports.yaml"},
                        {"path": "services/integrations/hosted_analytics_client.py"},
                    ]
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.impact_set.affected_bundle_paths == [
        "services/integrations/hosted_analytics_client.py",
        "modules/analytics_dashboard/module.yaml",
        "modules/analytics_dashboard/contracts/events.yaml",
        "modules/analytics_dashboard/backend/handler.py",
        "modules/analytics_dashboard/backend/service.py",
        "ui/pages/analytics.yaml",
    ]
    assert "services/integrations/reporting_provider_client.py" not in decision.impact_set.affected_bundle_paths
    assert "modules/hosted_analytics/module.yaml" not in decision.impact_set.affected_bundle_paths


@pytest.mark.asyncio
async def test_integration_impact_includes_app_backend_provider_adapters() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="patch",
            rationale="Provider adapter behavior needs a scoped revision.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Update the search provider adapter retry behavior.",
                "extra": {
                    "files_manifest": [
                        {"path": "services/adapters/search/vector_provider.py"},
                        {"path": "services/integrations/search_provider_client.py"},
                        {"path": "services/adapters/billing/payment_provider.py"},
                        {"path": "modules/search/backend/service.py"},
                        {"path": "modules/search/module.yaml"},
                    ]
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert "services/adapters/search/vector_provider.py" in decision.impact_set.affected_bundle_paths
    assert "services/integrations/search_provider_client.py" in decision.impact_set.affected_bundle_paths
    assert "services/adapters/billing/payment_provider.py" not in decision.impact_set.affected_bundle_paths


@pytest.mark.asyncio
async def test_hosted_capability_non_ui_request_does_not_force_page_paths() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="feature",
            rationale="Hosted analytics adapter behavior needs a scoped revision.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Update hosted analytics provider-backed refresh policy.",
                "extra": {
                    "files_manifest": [
                        {"path": "services/integrations/hosted_analytics_client.py"},
                        {"path": "modules/analytics_dashboard/module.yaml"},
                        {"path": "modules/analytics_dashboard/backend/service.py"},
                        {"path": "ui/pages/analytics.yaml"},
                    ]
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.impact_set.affected_bundle_paths == [
        "services/integrations/hosted_analytics_client.py",
        "modules/analytics_dashboard/module.yaml",
        "modules/analytics_dashboard/backend/service.py",
    ]


@pytest.mark.asyncio
async def test_hosted_capability_ui_request_uses_page_glob_when_page_binding_is_unknown() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="feature",
            rationale="Hosted analytics page display needs a scoped revision.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Change hosted analytics dashboard display.",
                "extra": {
                    "files_manifest": [
                        {"path": "services/integrations/hosted_analytics_client.py"},
                        {"path": "modules/analytics_dashboard/module.yaml"},
                        {"path": "modules/analytics_dashboard/backend/service.py"},
                        {"path": "ui/pages/analytics.yaml"},
                    ]
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.impact_set.affected_bundle_paths == [
        "services/integrations/hosted_analytics_client.py",
        "modules/analytics_dashboard/module.yaml",
        "modules/analytics_dashboard/backend/service.py",
        "ui/pages/*.yaml",
    ]


@pytest.mark.asyncio
async def test_hosted_capability_without_manifest_uses_conservative_hints() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="feature",
            rationale="Hosted pack adapter behavior needs a scoped revision.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Change hosted pack external adapter behavior.",
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.impact_set.affected_bundle_paths == [
        "services/integrations/*_client.py",
        "modules/*/module.yaml",
        "modules/*/contracts/*.yaml",
        "modules/*/backend/*.py",
    ]


@pytest.mark.asyncio
async def test_hosted_capability_without_manifest_adds_page_hint_only_for_ui_requests() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="feature",
            rationale="Hosted analytics dashboard needs a scoped revision.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Change hosted analytics dashboard page display.",
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.impact_set.affected_bundle_paths == [
        "services/integrations/*_client.py",
        "modules/*/module.yaml",
        "modules/*/contracts/*.yaml",
        "modules/*/backend/*.py",
        "ui/pages/*.yaml",
    ]


@pytest.mark.asyncio
async def test_integration_impact_includes_exact_connector_adapter_and_module_paths() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="feature",
            rationale="The analytics provider connector needs a scoped revision.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Update analytics provider connector sync behavior for reports.",
                "extra": {
                    "files_manifest": [
                        {"path": "services/integrations/reporting_provider_client.py"},
                        {"path": "services/integrations/analytics_provider_client.py"},
                        {"path": "modules/reports/backend/service.py", "content": "from backend.integrations.analytics_provider_client import AnalyticsProviderClient"},
                        {"path": "modules/reports/backend/schemas.py", "content": "connector_id = 'analytics_provider'"},
                        {"path": "modules/reports/backend/policy.py"},
                        {"path": "modules/reports/module.yaml"},
                        {"path": "config/integrations.json"},
                        {"path": "docs/integrations.md"},
                        {"path": ".env"},
                        {"path": "config/integrations.credentials.json"},
                    ]
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.impact_set.affected_bundle_paths == [
        "services/integrations/analytics_provider_client.py",
        "modules/reports/module.yaml",
        "modules/reports/backend/service.py",
        "modules/reports/backend/policy.py",
        "modules/reports/backend/schemas.py",
        "config/integrations.json",
        "docs/integrations.md",
    ]
    assert "services/integrations/reporting_provider_client.py" not in decision.impact_set.affected_bundle_paths
    assert ".env" not in decision.impact_set.affected_bundle_paths
    assert "config/integrations.credentials.json" not in decision.impact_set.affected_bundle_paths
    assert "Integration readiness may need to be rechecked." in decision.impact_set.scope_summary


@pytest.mark.asyncio
async def test_integration_impact_without_manifest_uses_conservative_hints() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="feature",
            rationale="An email provider integration needs a scoped revision.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Add email provider integration setup.",
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.impact_set.affected_bundle_paths == [
        "services/integrations/*_client.py",
        "services/adapters/**/*.py",
        "modules/*/backend/service.py",
        "modules/*/backend/schemas.py",
        "modules/*/module.yaml",
        "config/integrations*.json",
        "docs/integrations*.md",
    ]
    assert "Integration readiness may need to be rechecked." in decision.impact_set.scope_summary


@pytest.mark.asyncio
async def test_integration_impact_ui_request_includes_setup_page_when_present() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="feature",
            rationale="The search provider setup page needs a scoped revision.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Update search provider integration setup page.",
                "extra": {
                    "files_manifest": [
                        {"path": "services/integrations/search_provider_client.py"},
                        {"path": "modules/reports/module.yaml"},
                        {"path": "modules/reports/backend/service.py", "content": "connector_id = 'search_provider'"},
                        {"path": "modules/reports/backend/schemas.py"},
                        {"path": "ui/pages/integrations.yaml"},
                    ]
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.impact_set.affected_bundle_paths == [
        "services/integrations/search_provider_client.py",
        "modules/reports/module.yaml",
        "modules/reports/backend/service.py",
        "modules/reports/backend/schemas.py",
        "ui/pages/integrations.yaml",
    ]


@pytest.mark.asyncio
async def test_integration_impact_non_ui_request_does_not_force_pages() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="feature",
            rationale="The reporting provider connector needs a scoped revision.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Update reporting provider webhook sync.",
                "extra": {
                    "files_manifest": [
                        {"path": "services/integrations/reporting_provider_client.py"},
                        {"path": "modules/reports/module.yaml"},
                        {"path": "modules/reports/backend/service.py", "content": "connector_id = 'reporting_provider'"},
                        {"path": "ui/pages/reports.yaml"},
                    ]
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.impact_set.affected_bundle_paths == [
        "services/integrations/reporting_provider_client.py",
        "modules/reports/module.yaml",
        "modules/reports/backend/service.py",
    ]
    assert "ui/pages/reports.yaml" not in decision.impact_set.affected_bundle_paths


@pytest.mark.asyncio
async def test_integration_impact_never_emits_secret_path_hints() -> None:
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="feature",
            rationale="A storage provider integration needs a scoped revision.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Update storage provider API key handling.",
                "extra": {
                    "files_manifest": [
                        {"path": "services/integrations/storage_provider_client.py"},
                        {"path": "services/integrations/storage_provider_secret.py"},
                        {"path": "config/integrations.json"},
                        {"path": "config/integrations.keys.json"},
                        {"path": "secrets/storage_provider.json"},
                    ]
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.impact_set.affected_bundle_paths == [
        "services/integrations/storage_provider_client.py",
        "config/integrations.json",
    ]
    assert all("secret" not in path for path in decision.impact_set.affected_bundle_paths)
    assert all("key" not in path for path in decision.impact_set.affected_bundle_paths)


class _CountingClassifier:
    """Classifier that tracks call count — used to verify the LLM is not called on stale routes."""

    def __init__(self) -> None:
        self.call_count = 0

    async def classify(self, **kwargs):  # noqa: ANN003
        self.call_count += 1
        return SimpleNamespace(change_class="patch", rationale="patch", confidence=0.9, signals=[])


def _factory_resolver(classifier=None) -> RefinementTriggerRouteResolver:
    app_root = Path(__file__).resolve().parents[1] / "factory_app" / "app"
    return RefinementTriggerRouteResolver(
        classifier=classifier or _CountingClassifier(),
        pack_loader=lambda: load_control_plane_pack(app_root=app_root),
    )


def _patch_artifact_store(monkeypatch: pytest.MonkeyPatch, stale_families: list) -> None:
    """Patch ArtifactStore via importlib so the reference is stable across test-suite ordering."""
    import importlib
    store_mod = importlib.import_module("mozaiksai.core.artifacts.store")
    monkeypatch.setattr(store_mod.ArtifactStore, "__init__", lambda self: None)
    monkeypatch.setattr(store_mod.ArtifactStore, "get_stale_artifact_families", AsyncMock(return_value=stale_families))


@pytest.mark.asyncio
async def test_stale_route_bypasses_llm_and_routes_to_earliest_stale_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    """When design_docs is stale, route deterministically to DesignDocs via design_revision."""
    _patch_artifact_store(monkeypatch, ["design_docs"])

    classifier = _CountingClassifier()
    resolver = _factory_resolver(classifier)
    request = resolver.request_from_payload(
        payload={"refinement_request": {"artifact_kind": "app_bundle", "raw_user_request": "Add a dark mode toggle."}},
        app_id="app_1",
    )

    assert request is not None
    decision = await resolver.route(request)

    # LLM must NOT be called — stale routing is deterministic
    assert classifier.call_count == 0
    assert decision.workflow_id == "DesignDocs"
    assert decision.workflow_sequence == "design_revision"
    assert decision.change_intent.source == "stale_upstream"
    assert "design_docs" in decision.change_intent.signals
    assert decision.is_full_restart is False
    assert decision.impact_set.restart_from == "DesignDocs"
    assert "DesignDocs" in decision.impact_set.affected_workflows


@pytest.mark.asyncio
async def test_stale_route_prioritizes_concept_over_downstream_families(monkeypatch: pytest.MonkeyPatch) -> None:
    """When both concept and app_bundle are stale, route to full_rebuild (concept has highest priority)."""
    _patch_artifact_store(monkeypatch, ["app_bundle", "concept"])

    classifier = _CountingClassifier()
    resolver = _factory_resolver(classifier)
    request = resolver.request_from_payload(
        payload={"refinement_request": {"artifact_kind": "app_bundle", "raw_user_request": "Revamp the whole thing."}},
        app_id="app_1",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert classifier.call_count == 0
    assert decision.workflow_sequence == "full_rebuild"
    assert decision.workflow_id == "ValueEngine"
    assert decision.is_full_restart is True
    assert decision.change_intent.requires_concept_revision is True


@pytest.mark.asyncio
async def test_stale_route_handles_experience_spec_as_design_owned_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    """When experience_spec is stale, route to DesignDocs via app_surface_revision."""
    _patch_artifact_store(monkeypatch, ["experience_spec"])

    classifier = _CountingClassifier()
    resolver = _factory_resolver(classifier)
    request = resolver.request_from_payload(
        payload={"refinement_request": {"artifact_kind": "app_bundle", "raw_user_request": "Replace the dashboard flow."}},
        app_id="app_1",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert classifier.call_count == 0
    assert decision.workflow_id == "DesignDocs"
    assert decision.workflow_sequence == "app_surface_revision"
    assert decision.change_intent.source == "stale_upstream"
    assert "experience_spec" in decision.change_intent.signals
    assert decision.impact_set.affected_declarative_families == ["experience_spec", "app_bundle"]
    assert decision.is_full_restart is False


@pytest.mark.asyncio
async def test_stale_route_falls_through_to_llm_when_no_stale_families(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no stale families exist, the router falls through to normal LLM classification."""
    _patch_artifact_store(monkeypatch, [])

    class _PatchClassifier:
        def __init__(self) -> None:
            self.call_count = 0

        async def classify(self, **kwargs):  # noqa: ANN003
            self.call_count += 1
            return SimpleNamespace(change_class="patch", rationale="scoped patch", confidence=0.9, signals=[])

    patch_classifier = _PatchClassifier()
    resolver = _factory_resolver(patch_classifier)
    request = resolver.request_from_payload(
        payload={"refinement_request": {"artifact_kind": "app_bundle", "raw_user_request": "Fix a typo."}},
        app_id="app_1",
    )

    assert request is not None
    decision = await resolver.route(request)

    # LLM must be called since no stale families were found
    assert patch_classifier.call_count == 1
    assert decision.change_intent.source == "llm"


# ---------------------------------------------------------------------------
# conceptual_replan context seed tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_conceptual_replan_context_seed_has_pivot_description() -> None:
    """concept/core routes to conceptual_replan and injects pivot_description from raw_user_request."""
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="core",
            rationale="This should be a marketplace, not a CRM.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "concept",
                "raw_user_request": "Actually this should be a marketplace, not a CRM.",
            }
        },
        app_id="app_1",
        requested_workflow_id="ValueEngine",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.workflow_sequence == "conceptual_replan"
    assert decision.context_seed["pivot_description"] == "Actually this should be a marketplace, not a CRM."


@pytest.mark.asyncio
async def test_conceptual_replan_context_seed_has_default_preserve_families() -> None:
    """conceptual_replan seeds preserve_families defaulting to ['brand'] when not in extra."""
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="core",
            rationale="Concept pivot without extra fields.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "concept",
                "raw_user_request": "Pivot to a logistics platform.",
            }
        },
        app_id="app_1",
        requested_workflow_id="ValueEngine",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.workflow_sequence == "conceptual_replan"
    assert decision.context_seed["preserve_families"] == ["brand"]


@pytest.mark.asyncio
async def test_conceptual_replan_context_seed_includes_extra_refs_when_present() -> None:
    """conceptual_replan injects existing_concept_ref, previous_brand_ref, previous_app_bundle_ref when provided in extra."""
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="core",
            rationale="Concept pivot with all refs supplied by the workbench.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "concept",
                "raw_user_request": "Rebuild as a marketplace.",
                "extra": {
                    "existing_concept_ref": "av_concept_42",
                    "previous_brand_ref": "av_brand_17",
                    "previous_app_bundle_ref": "av_app_99",
                    "preserve_families": ["brand", "design_docs"],
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="ValueEngine",
    )

    assert request is not None
    # Phase 3: previous_app_bundle_ref triggers _auto_carry_forward_resolution.
    # Mock it to avoid real artifact store access in tests.
    with patch.object(
        resolver,
        "_auto_carry_forward_resolution",
        new=AsyncMock(return_value=([], [])),
    ):
        decision = await resolver.route(request)

    assert decision.workflow_sequence == "conceptual_replan"
    assert decision.context_seed["existing_concept_ref"] == "av_concept_42"
    assert decision.context_seed["previous_brand_ref"] == "av_brand_17"
    assert decision.context_seed["previous_app_bundle_ref"] == "av_app_99"
    assert decision.context_seed["preserve_families"] == ["brand", "design_docs"]


@pytest.mark.asyncio
async def test_full_rebuild_context_seed_has_no_conceptual_replan_fields() -> None:
    """app_bundle/core routes to full_rebuild and does NOT inject conceptual-replan context."""
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="core",
            rationale="The whole product direction needs to change.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Change the whole product direction.",
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.workflow_sequence == "full_rebuild"
    assert "pivot_description" not in decision.context_seed
    assert "preserve_families" not in decision.context_seed
    assert "existing_concept_ref" not in decision.context_seed
    assert "previous_brand_ref" not in decision.context_seed
    assert "previous_app_bundle_ref" not in decision.context_seed


@pytest.mark.asyncio
async def test_conceptual_replan_omits_missing_optional_refs() -> None:
    """conceptual_replan does NOT inject optional ref keys when they are absent from extra."""
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="core",
            rationale="Pivot without refs.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "concept",
                "raw_user_request": "Pivot to analytics SaaS.",
            }
        },
        app_id="app_1",
        requested_workflow_id="ValueEngine",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.workflow_sequence == "conceptual_replan"
    assert "existing_concept_ref" not in decision.context_seed
    assert "previous_brand_ref" not in decision.context_seed
    assert "previous_app_bundle_ref" not in decision.context_seed


# ---------------------------------------------------------------------------
# context_variables.yaml contract tests for conceptual_replan fields
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _factory_workflows_root() -> Path:
    return Path(__file__).resolve().parents[1] / "factory_app" / "workflows"


def test_value_engine_declares_pivot_description() -> None:
    """ValueEngine/context_variables.yaml declares pivot_description as a state variable."""
    cv = _load_yaml(_factory_workflows_root() / "ValueEngine" / "context_variables.yaml")
    defn = cv["definitions"]["pivot_description"]
    assert defn["source"]["type"] == "state"
    assert defn["source"]["default"] is None


def test_value_engine_declares_preserve_families() -> None:
    """ValueEngine/context_variables.yaml declares preserve_families with default []."""
    cv = _load_yaml(_factory_workflows_root() / "ValueEngine" / "context_variables.yaml")
    defn = cv["definitions"]["preserve_families"]
    assert defn["source"]["type"] == "state"
    assert defn["source"]["default"] == []


def test_value_engine_injects_pivot_variables_into_all_agents() -> None:
    """All ValueEngine agents receive the four conceptual_replan context variables."""
    cv = _load_yaml(_factory_workflows_root() / "ValueEngine" / "context_variables.yaml")
    pivot_vars = {"pivot_description", "existing_concept_ref", "preserve_families", "previous_brand_ref"}
    for agent_name, agent_cfg in cv["agents"].items():
        injected = set(agent_cfg.get("variables", []))
        missing = pivot_vars - injected
        assert not missing, f"{agent_name} is missing conceptual_replan variables: {missing}"


def test_design_docs_declares_pivot_description() -> None:
    """DesignDocs/context_variables.yaml declares pivot_description as a state variable."""
    cv = _load_yaml(_factory_workflows_root() / "DesignDocs" / "context_variables.yaml")
    defn = cv["definitions"]["pivot_description"]
    assert defn["source"]["type"] == "state"
    assert defn["source"]["default"] is None


def test_design_docs_declares_preserve_families() -> None:
    """DesignDocs/context_variables.yaml declares preserve_families with default []."""
    cv = _load_yaml(_factory_workflows_root() / "DesignDocs" / "context_variables.yaml")
    defn = cv["definitions"]["preserve_families"]
    assert defn["source"]["type"] == "state"
    assert defn["source"]["default"] == []


def test_design_docs_injects_pivot_variables_into_design_docs_agent() -> None:
    """DesignDocsAgent receives the four conceptual_replan context variables."""
    cv = _load_yaml(_factory_workflows_root() / "DesignDocs" / "context_variables.yaml")
    injected = set(cv["agents"]["DesignDocsAgent"]["variables"])
    pivot_vars = {"pivot_description", "existing_concept_ref", "preserve_families", "previous_brand_ref"}
    missing = pivot_vars - injected
    assert not missing, f"DesignDocsAgent is missing conceptual_replan variables: {missing}"


# ---------------------------------------------------------------------------
# carry_forward_modules context seed tests (Step 4)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_conceptual_replan_context_seed_includes_carry_forward_modules_from_extra() -> None:
    """conceptual_replan injects carry_forward_modules from request.extra when supplied."""
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="core",
            rationale="Pivot to a marketplace.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "concept",
                "raw_user_request": "Make this a marketplace.",
                "extra": {
                    "carry_forward_modules": ["notifications", "files", "billing_portal"],
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="ValueEngine",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.workflow_sequence == "conceptual_replan"
    assert decision.context_seed["carry_forward_modules"] == ["notifications", "files", "billing_portal"]


@pytest.mark.asyncio
async def test_conceptual_replan_context_seed_defaults_carry_forward_modules_to_empty() -> None:
    """conceptual_replan defaults carry_forward_modules to [] when absent from extra."""
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="core",
            rationale="Pivot without modules specified.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "concept",
                "raw_user_request": "Rebuild as an analytics platform.",
            }
        },
        app_id="app_1",
        requested_workflow_id="ValueEngine",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.workflow_sequence == "conceptual_replan"
    assert decision.context_seed["carry_forward_modules"] == []


@pytest.mark.asyncio
async def test_full_rebuild_context_seed_has_no_carry_forward_modules() -> None:
    """app_bundle/core routes to full_rebuild and does NOT inject carry_forward_modules."""
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="core",
            rationale="Full reset of the product.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Reset everything.",
                "extra": {
                    "carry_forward_modules": ["notifications"],
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.workflow_sequence == "full_rebuild"
    assert "carry_forward_modules" not in decision.context_seed


# ---------------------------------------------------------------------------
# AppGenerator context_variables.yaml contract tests (Step 4)
# ---------------------------------------------------------------------------

def test_app_generator_declares_carry_forward_modules() -> None:
    """AppGenerator/context_variables.yaml declares carry_forward_modules as a state variable with default []."""
    cv = _load_yaml(_factory_workflows_root() / "AppGenerator" / "context_variables.yaml")
    defn = cv["definitions"]["carry_forward_modules"]
    assert defn["source"]["type"] == "state"
    assert defn["source"]["default"] == []


def test_app_generator_declares_pivot_description() -> None:
    """AppGenerator/context_variables.yaml declares pivot_description as a state variable."""
    cv = _load_yaml(_factory_workflows_root() / "AppGenerator" / "context_variables.yaml")
    defn = cv["definitions"]["pivot_description"]
    assert defn["source"]["type"] == "state"
    assert defn["source"]["default"] is None


def test_app_generator_declares_previous_app_bundle_ref() -> None:
    """AppGenerator/context_variables.yaml declares previous_app_bundle_ref as a state variable."""
    cv = _load_yaml(_factory_workflows_root() / "AppGenerator" / "context_variables.yaml")
    defn = cv["definitions"]["previous_app_bundle_ref"]
    assert defn["source"]["type"] == "state"
    assert defn["source"]["default"] is None


def test_app_plan_agent_receives_carry_forward_modules() -> None:
    """AppPlanAgent injection list includes carry_forward_modules and the three companion fields."""
    cv = _load_yaml(_factory_workflows_root() / "AppGenerator" / "context_variables.yaml")
    injected = set(cv["agents"]["AppPlanAgent"]["variables"])
    required = {"carry_forward_modules", "pivot_description", "preserve_families", "previous_app_bundle_ref"}
    missing = required - injected
    assert not missing, f"AppPlanAgent is missing conceptual_replan variables: {missing}"


def test_assembly_agent_does_not_receive_carry_forward_modules() -> None:
    """AssemblyAgent must not receive carry_forward_modules — no automatic merge behavior."""
    cv = _load_yaml(_factory_workflows_root() / "AppGenerator" / "context_variables.yaml")
    injected = set(cv["agents"]["AssemblyAgent"]["variables"])
    assert "carry_forward_modules" not in injected, (
        "AssemblyAgent must not receive carry_forward_modules until merge behavior is implemented"
    )


def test_app_plan_agent_prompt_mentions_carry_forward_advisory() -> None:
    """AppPlanAgent prompt warns that carry_forward_modules is advisory and must not be blindly re-emitted."""
    import yaml
    agents_path = _factory_workflows_root() / "AppGenerator" / "agents.yaml"
    agents_raw = yaml.safe_load(agents_path.read_text(encoding="utf-8"))
    app_plan_agent = next(a for a in agents_raw["agents"] if a["name"] == "AppPlanAgent")
    full_prompt = " ".join(
        section.get("content", "")
        for section in app_plan_agent.get("prompt_sections", [])
    )
    assert "carry_forward_modules" in full_prompt
    assert "advisory" in full_prompt.lower()
    assert "blindly" in full_prompt.lower()


# ---------------------------------------------------------------------------
# llm_profile / architecture signal tests (Step 5)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_conceptual_replan_context_seed_has_architecture_llm_profile() -> None:
    """conceptual_replan injects llm_profile='architecture' into context seed."""
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="core",
            rationale="Pivot to marketplace.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "concept",
                "raw_user_request": "Make this a marketplace.",
            }
        },
        app_id="app_1",
        requested_workflow_id="ValueEngine",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.workflow_sequence == "conceptual_replan"
    assert decision.context_seed.get("llm_profile") == "architecture"


@pytest.mark.asyncio
async def test_full_rebuild_context_seed_has_architecture_llm_profile() -> None:
    """full_rebuild (app_bundle/core) injects llm_profile='architecture' into context seed."""
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="core",
            rationale="Full product reset.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Reset the whole product.",
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert decision.workflow_sequence == "full_rebuild"
    assert decision.context_seed.get("llm_profile") == "architecture"


@pytest.mark.asyncio
async def test_patch_route_context_seed_has_no_llm_profile() -> None:
    """Patch routes do NOT inject llm_profile."""
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="patch",
            rationale="Tiny scoped patch.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Fix a typo in the dashboard label.",
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert "llm_profile" not in decision.context_seed


@pytest.mark.asyncio
async def test_design_route_context_seed_has_no_llm_profile() -> None:
    """Design routes do NOT inject llm_profile."""
    resolver = _factory_resolver(
        _FakeChangeClassifier(
            change_class="design",
            rationale="Layout revision.",
        )
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Restructure the sidebar layout.",
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )

    assert request is not None
    decision = await resolver.route(request)

    assert "llm_profile" not in decision.context_seed


# ---------------------------------------------------------------------------
# ai.json architecture profile contract tests
# ---------------------------------------------------------------------------

def _load_ai_json() -> dict:
    runtime_yaml_path = Path(__file__).resolve().parents[1] / "factory_app" / "control_plane" / "config" / "runtime.yaml"
    return yaml.safe_load(runtime_yaml_path.read_text(encoding="utf-8"))


def test_ai_json_declares_architecture_llm_profile() -> None:
    """control-plane runtime config declares an 'architecture' profile."""
    ai = _load_ai_json()
    profiles = ai["llm_profiles"]
    assert "architecture" in profiles
    arch = profiles["architecture"]
    assert "purpose" in arch
    assert "llm_config" in arch
    assert "model" in arch["llm_config"]


def test_ai_json_classifier_profile_unchanged() -> None:
    """classifier llm_profile still points to the classifier profile (unchanged)."""
    ai = _load_ai_json()
    assert ai["classifier"]["llm_profile"] == "classifier"


def test_ai_json_coding_profile_unchanged() -> None:
    """coding llm_profile still points to the codegen profile (unchanged)."""
    ai = _load_ai_json()
    assert ai["coding"]["llm_profile"] == "codegen"


# ---------------------------------------------------------------------------
# context_variables.yaml llm_profile declaration tests
# ---------------------------------------------------------------------------

def test_value_engine_declares_llm_profile() -> None:
    """ValueEngine/context_variables.yaml declares llm_profile as a state variable."""
    cv = _load_yaml(_factory_workflows_root() / "ValueEngine" / "context_variables.yaml")
    defn = cv["definitions"]["llm_profile"]
    assert defn["source"]["type"] == "state"
    assert defn["source"]["default"] is None


def test_value_engine_injects_llm_profile_into_all_agents() -> None:
    """All ValueEngine agents receive llm_profile."""
    cv = _load_yaml(_factory_workflows_root() / "ValueEngine" / "context_variables.yaml")
    for agent_name, agent_cfg in cv["agents"].items():
        assert "llm_profile" in agent_cfg.get("variables", []), (
            f"{agent_name} is missing llm_profile"
        )


def test_design_docs_declares_llm_profile() -> None:
    """DesignDocs/context_variables.yaml declares llm_profile as a state variable."""
    cv = _load_yaml(_factory_workflows_root() / "DesignDocs" / "context_variables.yaml")
    defn = cv["definitions"]["llm_profile"]
    assert defn["source"]["type"] == "state"
    assert defn["source"]["default"] is None


def test_design_docs_injects_llm_profile_into_design_docs_agent() -> None:
    """DesignDocsAgent receives llm_profile."""
    cv = _load_yaml(_factory_workflows_root() / "DesignDocs" / "context_variables.yaml")
    assert "llm_profile" in cv["agents"]["DesignDocsAgent"]["variables"]


def test_agent_generator_declares_llm_profile() -> None:
    """AgentGenerator/context_variables.yaml declares llm_profile as a state variable."""
    cv = _load_yaml(_factory_workflows_root() / "AgentGenerator" / "context_variables.yaml")
    defn = cv["definitions"]["llm_profile"]
    assert defn["source"]["type"] == "state"
    assert defn["source"]["default"] is None


def test_agent_generator_injects_llm_profile_into_interview_agent() -> None:
    """AgentGenerator InterviewAgent receives llm_profile."""
    cv = _load_yaml(_factory_workflows_root() / "AgentGenerator" / "context_variables.yaml")
    assert "llm_profile" in cv["agents"]["InterviewAgent"]["variables"]


def test_app_generator_declares_llm_profile() -> None:
    """AppGenerator/context_variables.yaml declares llm_profile as a state variable."""
    cv = _load_yaml(_factory_workflows_root() / "AppGenerator" / "context_variables.yaml")
    defn = cv["definitions"]["llm_profile"]
    assert defn["source"]["type"] == "state"
    assert defn["source"]["default"] is None


def test_app_plan_agent_receives_llm_profile() -> None:
    """AppPlanAgent receives llm_profile."""
    cv = _load_yaml(_factory_workflows_root() / "AppGenerator" / "context_variables.yaml")
    assert "llm_profile" in cv["agents"]["AppPlanAgent"]["variables"]


# ---------------------------------------------------------------------------
# Phase 3: auto-populate carry_forward_modules tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_conceptual_replan_explicit_list_used_without_extraction() -> None:
    """Explicit carry_forward_modules from extra is used as-is; extraction is not called."""
    resolver = _factory_resolver(
        _FakeChangeClassifier(change_class="core", rationale="Pivot with explicit module list.")
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "concept",
                "raw_user_request": "Make this a marketplace.",
                "extra": {
                    "previous_app_bundle_ref": "av_app_explicit",
                    "carry_forward_modules": ["notifications", "files", "billing_portal"],
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="ValueEngine",
    )
    assert request is not None

    mock_extractor = AsyncMock(return_value=(["should_not_appear"], []))
    with patch.object(resolver, "_auto_carry_forward_resolution", new=mock_extractor):
        decision = await resolver.route(request)

    assert decision.workflow_sequence == "conceptual_replan"
    # Explicit list wins; extractor must NOT have been called.
    assert decision.context_seed["carry_forward_modules"] == ["notifications", "files", "billing_portal"]
    mock_extractor.assert_not_called()


@pytest.mark.asyncio
async def test_conceptual_replan_auto_populates_when_ref_present_no_explicit_list() -> None:
    """When previous_app_bundle_ref is set and carry_forward_modules absent, auto-populates from extractor."""
    resolver = _factory_resolver(
        _FakeChangeClassifier(change_class="core", rationale="Pivot without explicit module list.")
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "concept",
                "raw_user_request": "Rebuild as an analytics platform.",
                "extra": {
                    "previous_app_bundle_ref": "av_app_auto",
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="ValueEngine",
    )
    assert request is not None

    mock_extractor = AsyncMock(return_value=(["notifications", "audit_log"], []))
    with patch.object(resolver, "_auto_carry_forward_resolution", new=mock_extractor):
        decision = await resolver.route(request)

    assert decision.workflow_sequence == "conceptual_replan"
    assert decision.context_seed["carry_forward_modules"] == ["notifications", "audit_log"]
    mock_extractor.assert_called_once()


@pytest.mark.asyncio
async def test_conceptual_replan_extraction_called_with_correct_ref() -> None:
    """Extractor is called with the stripped previous_app_bundle_ref value."""
    resolver = _factory_resolver(
        _FakeChangeClassifier(change_class="core", rationale="Pivot.")
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "concept",
                "raw_user_request": "Pivot to logistics.",
                "extra": {
                    "previous_app_bundle_ref": "  av_bundle_xyz  ",
                },
            }
        },
        app_id="app_auto",
        requested_workflow_id="ValueEngine",
    )
    assert request is not None

    mock_extractor = AsyncMock(return_value=(["billing_portal"], []))
    with patch.object(resolver, "_auto_carry_forward_resolution", new=mock_extractor):
        decision = await resolver.route(request)

    assert decision.workflow_sequence == "conceptual_replan"
    _, call_kwargs = mock_extractor.call_args
    assert call_kwargs["previous_app_bundle_ref"] == "av_bundle_xyz"


@pytest.mark.asyncio
async def test_conceptual_replan_extraction_warnings_in_context_seed() -> None:
    """Extraction warnings appear in carry_forward_warnings when present."""
    resolver = _factory_resolver(
        _FakeChangeClassifier(change_class="core", rationale="Pivot.")
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "concept",
                "raw_user_request": "Change the concept.",
                "extra": {
                    "previous_app_bundle_ref": "av_app_warn",
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="ValueEngine",
    )
    assert request is not None

    mock_extractor = AsyncMock(return_value=([], ["workspace_unavailable: artifact_not_found"]))
    with patch.object(resolver, "_auto_carry_forward_resolution", new=mock_extractor):
        decision = await resolver.route(request)

    assert decision.workflow_sequence == "conceptual_replan"
    assert decision.context_seed["carry_forward_modules"] == []
    assert "carry_forward_warnings" in decision.context_seed
    assert any("workspace_unavailable" in w for w in decision.context_seed["carry_forward_warnings"])


@pytest.mark.asyncio
async def test_conceptual_replan_no_warnings_key_when_extraction_clean() -> None:
    """carry_forward_warnings is absent when extraction succeeds with no warnings."""
    resolver = _factory_resolver(
        _FakeChangeClassifier(change_class="core", rationale="Clean pivot.")
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "concept",
                "raw_user_request": "Rebuild as a marketplace.",
                "extra": {
                    "previous_app_bundle_ref": "av_app_clean",
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="ValueEngine",
    )
    assert request is not None

    mock_extractor = AsyncMock(return_value=(["notifications"], []))
    with patch.object(resolver, "_auto_carry_forward_resolution", new=mock_extractor):
        decision = await resolver.route(request)

    assert decision.workflow_sequence == "conceptual_replan"
    assert decision.context_seed["carry_forward_modules"] == ["notifications"]
    assert "carry_forward_warnings" not in decision.context_seed


@pytest.mark.asyncio
async def test_conceptual_replan_extraction_failure_returns_empty_with_warning() -> None:
    """Extraction failure produces carry_forward_modules=[] and a warning, no crash."""
    resolver = _factory_resolver(
        _FakeChangeClassifier(change_class="core", rationale="Pivot.")
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "concept",
                "raw_user_request": "Rebuild as a marketplace.",
                "extra": {
                    "previous_app_bundle_ref": "av_app_fail",
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="ValueEngine",
    )
    assert request is not None

    mock_extractor = AsyncMock(return_value=([], ["carry_forward_extraction_error: db connection refused"]))
    with patch.object(resolver, "_auto_carry_forward_resolution", new=mock_extractor):
        decision = await resolver.route(request)

    assert decision.workflow_sequence == "conceptual_replan"
    assert decision.context_seed["carry_forward_modules"] == []
    assert "carry_forward_warnings" in decision.context_seed
    assert any("carry_forward_extraction_error" in w for w in decision.context_seed["carry_forward_warnings"])


@pytest.mark.asyncio
async def test_conceptual_replan_no_ref_no_explicit_list_returns_empty() -> None:
    """Without previous_app_bundle_ref and without explicit list, carry_forward_modules is []."""
    resolver = _factory_resolver(
        _FakeChangeClassifier(change_class="core", rationale="Pivot without any bundle ref.")
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "concept",
                "raw_user_request": "Make this a logistics app.",
            }
        },
        app_id="app_1",
        requested_workflow_id="ValueEngine",
    )
    assert request is not None

    mock_extractor = AsyncMock(return_value=(["should_not_appear"], []))
    with patch.object(resolver, "_auto_carry_forward_resolution", new=mock_extractor):
        decision = await resolver.route(request)

    assert decision.workflow_sequence == "conceptual_replan"
    assert decision.context_seed["carry_forward_modules"] == []
    # No ref → extractor should not be called.
    mock_extractor.assert_not_called()


@pytest.mark.asyncio
async def test_full_rebuild_does_not_invoke_extraction() -> None:
    """full_rebuild does not call _auto_carry_forward_resolution and has no carry_forward_modules."""
    resolver = _factory_resolver(
        _FakeChangeClassifier(change_class="core", rationale="Full reset.")
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "app_bundle",
                "raw_user_request": "Reset everything.",
                "extra": {
                    "previous_app_bundle_ref": "av_app_rebuild",
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )
    assert request is not None

    mock_extractor = AsyncMock(return_value=(["should_not_appear"], []))
    with patch.object(resolver, "_auto_carry_forward_resolution", new=mock_extractor):
        decision = await resolver.route(request)

    assert decision.workflow_sequence == "full_rebuild"
    assert "carry_forward_modules" not in decision.context_seed
    mock_extractor.assert_not_called()


@pytest.mark.asyncio
async def test_conceptual_replan_explicit_empty_list_skips_extraction() -> None:
    """Client-supplied empty list [] is an explicit override — extraction is not called."""
    resolver = _factory_resolver(
        _FakeChangeClassifier(change_class="core", rationale="Pivot with empty override.")
    )
    request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": "concept",
                "raw_user_request": "Pivot to a greenfield build.",
                "extra": {
                    "previous_app_bundle_ref": "av_app_greenfield",
                    "carry_forward_modules": [],
                },
            }
        },
        app_id="app_1",
        requested_workflow_id="ValueEngine",
    )
    assert request is not None

    mock_extractor = AsyncMock(return_value=(["should_not_appear"], []))
    with patch.object(resolver, "_auto_carry_forward_resolution", new=mock_extractor):
        decision = await resolver.route(request)

    assert decision.workflow_sequence == "conceptual_replan"
    assert decision.context_seed["carry_forward_modules"] == []
    mock_extractor.assert_not_called()


# ---------------------------------------------------------------------------
# Module-id sanitization tests (12 tests)
# ---------------------------------------------------------------------------


def test_sanitize_valid_module_ids_pass_through() -> None:
    """[S1] Valid module ids are returned unchanged."""
    ids = ["settings", "contacts", "my-module", "module_v2"]
    valid, warnings = RefinementTriggerRouteResolver._sanitize_carry_forward_module_ids(ids)
    assert valid == ids
    assert warnings == []


def test_sanitize_rejects_empty_string() -> None:
    """[S2] Empty string is rejected."""
    valid, warnings = RefinementTriggerRouteResolver._sanitize_carry_forward_module_ids([""])
    assert valid == []
    assert len(warnings) == 1
    assert "empty" in warnings[0]


def test_sanitize_rejects_dot() -> None:
    """[S3] '.' is rejected as a reserved name."""
    valid, warnings = RefinementTriggerRouteResolver._sanitize_carry_forward_module_ids(["."])
    assert valid == []
    assert any("reserved" in w for w in warnings)


def test_sanitize_rejects_dotdot() -> None:
    """[S4] '..' is rejected as a reserved name."""
    valid, warnings = RefinementTriggerRouteResolver._sanitize_carry_forward_module_ids([".."])
    assert valid == []
    assert any("reserved" in w for w in warnings)


def test_sanitize_rejects_forward_slash() -> None:
    """[S5] Module id containing '/' is rejected."""
    valid, warnings = RefinementTriggerRouteResolver._sanitize_carry_forward_module_ids(
        ["modules/settings"]
    )
    assert valid == []
    assert any("path separator" in w for w in warnings)


def test_sanitize_rejects_backslash() -> None:
    """[S6] Module id containing '\\' is rejected."""
    valid, warnings = RefinementTriggerRouteResolver._sanitize_carry_forward_module_ids(
        ["modules\\settings"]
    )
    assert valid == []
    assert any("path separator" in w for w in warnings)


def test_sanitize_rejects_disallowed_characters() -> None:
    """[S7] Module id with characters outside [a-zA-Z0-9_-] is rejected."""
    bad_ids = ["my module", "mod@ule", "mod!ule", "mod;ule"]
    for bad_id in bad_ids:
        valid, warnings = RefinementTriggerRouteResolver._sanitize_carry_forward_module_ids([bad_id])
        assert valid == [], f"expected {bad_id!r} to be rejected"
        assert any("disallowed" in w for w in warnings)


def test_sanitize_rejects_id_exceeding_max_length() -> None:
    """[S8] Module id longer than 80 characters is rejected."""
    long_id = "a" * 81
    valid, warnings = RefinementTriggerRouteResolver._sanitize_carry_forward_module_ids([long_id])
    assert valid == []
    assert any("exceeds" in w for w in warnings)


def test_sanitize_accepts_id_at_max_length() -> None:
    """[S9] Module id exactly 80 characters is accepted."""
    max_id = "a" * 80
    valid, warnings = RefinementTriggerRouteResolver._sanitize_carry_forward_module_ids([max_id])
    assert valid == [max_id]
    assert warnings == []


def test_sanitize_deduplicates_preserving_order() -> None:
    """[S10] Duplicate valid ids are deduplicated; first occurrence is kept."""
    ids = ["alpha", "beta", "alpha", "gamma", "beta"]
    valid, warnings = RefinementTriggerRouteResolver._sanitize_carry_forward_module_ids(ids)
    assert valid == ["alpha", "beta", "gamma"]
    assert warnings == []


def test_sanitize_mixed_valid_and_invalid() -> None:
    """[S11] Valid ids are kept; invalid ids produce warnings; both in same input."""
    ids = ["good_module", "../etc/passwd", "also-good", "bad/path"]
    valid, warnings = RefinementTriggerRouteResolver._sanitize_carry_forward_module_ids(ids)
    assert valid == ["good_module", "also-good"]
    assert len(warnings) == 2
    assert all("carry_forward_invalid_module_id" in w for w in warnings)


@pytest.mark.asyncio
async def test_auto_carry_forward_resolution_rejects_unsafe_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[S12] _auto_carry_forward_resolution sanitizes unsafe module ids returned by the tool."""
    _write_business_plan_registry(tmp_path, monkeypatch)
    resolver = RefinementTriggerRouteResolver(
        classifier=_FakeChangeClassifier(change_class="patch", rationale="test"),
        pack_loader=_pack,
    )

    # Create the request before patching _load_pack so request_from_payload uses the real pack.
    request = resolver.request_from_payload(
        payload={"refinement_request": {"raw_user_request": "test"}},
        app_id="app_s12",
        requested_workflow_id="FinalMemoAssembly",
    )

    unsafe_modules = [
        {"module_id": "good_module"},
        {"module_id": "../etc/passwd"},
        {"module_id": "also-good"},
        {"module_id": "bad/path"},
    ]

    async def _fake_tool(context=None):
        return {"modules": unsafe_modules, "warnings": []}

    mock_tool_def = type("T", (), {"entrypoint": "fake.entry:fn"})()
    mock_loaded_pack = type(
        "P", (), {"tool_by_id": lambda self, _: mock_tool_def}
    )()

    with patch.object(resolver, "_load_pack", return_value=mock_loaded_pack), patch(
        "mozaiksai.control_plane.implementations.refinement_router"
        ".resolve_control_plane_tool_entrypoint",
        return_value=_fake_tool,
    ):
        module_ids, warnings = await resolver._auto_carry_forward_resolution(
            request=request,
            previous_app_bundle_ref="av_s12",
        )

    assert "good_module" in module_ids
    assert "also-good" in module_ids
    assert "../etc/passwd" not in module_ids
    assert "bad/path" not in module_ids
    assert any("carry_forward_invalid_module_id" in w for w in warnings)


def test_docs_mention_phase_3_auto_population() -> None:
    """refinement-control-plane.md documents Phase 3 auto-population behavior."""
    doc_path = (
        Path(__file__).resolve().parents[1]
        / "docs" / "architecture" / "workflows" / "refinement-control-plane.md"
    )
    doc = doc_path.read_text(encoding="utf-8")
    assert "phase 3" in doc.lower()
    assert "auto-popul" in doc.lower()


def test_docs_do_not_say_carry_forward_is_only_manual() -> None:
    """carry_forward_modules must not be described as exclusively manually supplied."""
    doc_path = (
        Path(__file__).resolve().parents[1]
        / "docs" / "architecture" / "workflows" / "refinement-control-plane.md"
    )
    doc = doc_path.read_text(encoding="utf-8").lower()
    assert "only manually" not in doc

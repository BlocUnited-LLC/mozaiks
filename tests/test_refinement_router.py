from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

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

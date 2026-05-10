from __future__ import annotations

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
                                route_to="FinalMemoAssembly",
                                affected_workflows=["FinalMemoAssembly"],
                                affected_declarative_families=["business_plan_bundle"],
                                requires_replanning=False,
                                requires_rebuild=True,
                            ),
                            design=ControlPlaneChangeRouteManifest(
                                route_to="ExecutiveSummary",
                                affected_workflows=["ExecutiveSummary", "FinalMemoAssembly"],
                                affected_declarative_families=["executive_summary", "business_plan_bundle"],
                                requires_replanning=True,
                                requires_rebuild=True,
                            ),
                            feature=ControlPlaneChangeRouteManifest(
                                route_to="BusinessModel",
                                affected_workflows=["BusinessModel", "FinancialModel", "ExecutiveSummary", "FinalMemoAssembly"],
                                affected_declarative_families=["business_model", "financial_model", "business_plan_bundle"],
                                requires_replanning=True,
                                requires_rebuild=True,
                            ),
                            core=ControlPlaneChangeRouteManifest(
                                route_to="MarketResearch",
                                affected_workflows=[
                                    "MarketResearch",
                                    "CustomerPersona",
                                    "BusinessModel",
                                    "FinancialModel",
                                    "RiskAnalysis",
                                    "ExecutiveSummary",
                                    "FinalMemoAssembly",
                                ],
                                affected_declarative_families=[
                                    "market_research",
                                    "customer_persona",
                                    "business_model",
                                    "financial_model",
                                    "executive_summary",
                                    "business_plan_bundle",
                                ],
                                requires_replanning=True,
                                requires_rebuild=True,
                            ),
                        ),
                    ),
                    ControlPlaneArtifactRoutingManifest(
                        artifact_kind="executive_summary",
                        label="executive summary",
                        routes=ControlPlaneArtifactChangeRoutesManifest(
                            patch=ControlPlaneChangeRouteManifest(
                                route_to="ExecutiveSummary",
                                affected_workflows=["ExecutiveSummary"],
                                affected_declarative_families=["executive_summary"],
                                requires_replanning=False,
                                requires_rebuild=True,
                            ),
                            design=ControlPlaneChangeRouteManifest(
                                route_to="ExecutiveSummary",
                                affected_workflows=["ExecutiveSummary", "FinalMemoAssembly"],
                                affected_declarative_families=["executive_summary", "business_plan_bundle"],
                                requires_replanning=True,
                                requires_rebuild=True,
                            ),
                            feature=ControlPlaneChangeRouteManifest(
                                route_to="ExecutiveSummary",
                                affected_workflows=["ExecutiveSummary", "FinalMemoAssembly"],
                                affected_declarative_families=["executive_summary", "business_plan_bundle"],
                                requires_replanning=True,
                                requires_rebuild=True,
                            ),
                            core=ControlPlaneChangeRouteManifest(
                                route_to="MarketResearch",
                                affected_workflows=[
                                    "MarketResearch",
                                    "CustomerPersona",
                                    "BusinessModel",
                                    "FinancialModel",
                                    "RiskAnalysis",
                                    "ExecutiveSummary",
                                    "FinalMemoAssembly",
                                ],
                                affected_declarative_families=[
                                    "market_research",
                                    "customer_persona",
                                    "business_model",
                                    "financial_model",
                                    "executive_summary",
                                    "business_plan_bundle",
                                ],
                                requires_replanning=True,
                                requires_rebuild=True,
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
async def test_refinement_router_uses_pack_default_artifact_kind_for_core_reentry() -> None:
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
async def test_refinement_router_keeps_local_patch_in_declared_owner_workflow() -> None:
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

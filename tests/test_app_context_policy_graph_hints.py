from __future__ import annotations

from pathlib import Path

from mozaiksai.control_plane.app_context import (
    APP_CONTEXT_STALE_WARNING,
    AppContextOwnershipBoundarySummary,
    AppContextSummary,
)
from mozaiksai.control_plane.app_context_impact import AppContextImpactHints
from mozaiksai.control_plane.app_context_policy import (
    AppContextPolicyDecision,
    enrich_app_context_policy_with_graph_hints,
    evaluate_app_context_policy,
)
from mozaiksai.control_plane.dry_run import build_refinement_execution_plan_from_route

ROOT = Path(__file__).resolve().parents[1]


def _fresh_context() -> AppContextSummary:
    return AppContextSummary(
        app_id="field_service",
        available=True,
        context_version_id="ctx_current",
        mode="greenfield",
        stale_status="current",
    )


def _stale_brownfield_context() -> AppContextSummary:
    return AppContextSummary(
        app_id="field_service",
        available=True,
        context_version_id="ctx_stale",
        mode="brownfield",
        stale_status="stale",
        stale_reasons=["source ref changed"],
        warnings=[APP_CONTEXT_STALE_WARNING],
        ownership_boundaries=[
            AppContextOwnershipBoundarySummary(
                path_or_artifact="src/orders",
                ownership="read_only_discovered",
            )
        ],
    )


def _impact_hints() -> AppContextImpactHints:
    return AppContextImpactHints(
        available=True,
        graph_snapshot_ref="av_graph_1",
        stale_status="current",
        ownership_warnings=[
            "Graph node 'src/orders/service.py' is read_only_discovered; changes require ownership review."
        ],
        risk_warnings=["Integration adapter readiness is related to this request."],
        explanations=["AppContextGraph matched 3 related nodes and 2 related edges."],
        additional_path_hints=["src/orders/service.py", "modules/orders/backend/service.py"],
    )


def test_graph_hint_warnings_are_added_to_policy_result() -> None:
    base = evaluate_app_context_policy(
        app_context_summary=_fresh_context(),
        change_class="patch",
        refinement_lane="ui_patch",
        affected_bundle_paths=["ui/pages/orders.yaml"],
    )
    enriched = enrich_app_context_policy_with_graph_hints(base, _impact_hints())

    assert enriched.decision is base.decision
    assert enriched.allowed is base.allowed
    assert enriched.blocking is base.blocking
    assert _impact_hints().ownership_warnings[0] in enriched.warnings
    assert _impact_hints().risk_warnings[0] in enriched.warnings
    assert "AppContextGraph: AppContextGraph matched 3 related nodes and 2 related edges." in enriched.warnings
    assert enriched.graph_warnings == [
        *_impact_hints().ownership_warnings,
        *_impact_hints().risk_warnings,
    ]
    assert enriched.graph_explanations


def test_stale_graph_warning_enriches_policy_without_blocking_low_risk_patch() -> None:
    base = evaluate_app_context_policy(
        app_context_summary=_fresh_context(),
        change_class="patch",
        refinement_lane="ui_patch",
        affected_bundle_paths=["ui/pages/orders.yaml"],
    )
    enriched = enrich_app_context_policy_with_graph_hints(
        base,
        AppContextImpactHints(
            available=True,
            stale_status="unknown",
            stale_graph_warning="Current AppContextGraph is stale/unknown; graph impact path expansion is disabled.",
        ),
    )

    assert enriched.decision is AppContextPolicyDecision.ALLOW
    assert enriched.allowed is True
    assert enriched.blocking is False
    assert "Current AppContextGraph is stale/unknown; graph impact path expansion is disabled." in enriched.warnings


def test_graph_hints_explain_existing_brownfield_block_without_creating_new_decision() -> None:
    base = evaluate_app_context_policy(
        app_context_summary=_stale_brownfield_context(),
        change_class="feature",
        refinement_lane="feature_addition",
        affected_bundle_paths=["src/orders/service.py"],
    )
    enriched = enrich_app_context_policy_with_graph_hints(base, _impact_hints())

    assert base.decision is AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH
    assert enriched.decision is base.decision
    assert enriched.requires_context_refresh is base.requires_context_refresh
    assert "read_only_discovered_boundary" in enriched.risky_signals
    assert _impact_hints().ownership_warnings[0] in enriched.warnings


def test_graph_hints_attach_to_plan_without_mutating_paths_or_routing() -> None:
    affected_paths = ["ui/pages/orders.yaml"]
    plan = build_refinement_execution_plan_from_route(
        request="Adjust the orders page layout.",
        artifact_kind="app_bundle",
        change_class="patch",
        workflow_id="AppGenerator",
        workflow_sequence="app_revision",
        affected_workflows=["AppGenerator"],
        affected_declarative_families=["app_bundle"],
        affected_bundle_paths=list(affected_paths),
        scope_summary="UI page patch.",
        app_id="field_service",
        app_context_summary=_fresh_context(),
        app_context_impact_hints=_impact_hints(),
    )

    assert plan.workflow_sequence == "app_revision"
    assert plan.target_workflow == "AppGenerator"
    assert plan.affected_bundle_paths == affected_paths
    assert plan.context_policy_decision is not None
    assert _impact_hints().ownership_warnings[0] in plan.context_policy_decision.warnings
    assert "src/orders/service.py" not in plan.affected_bundle_paths
    assert "modules/orders/backend/service.py" not in plan.affected_bundle_paths


def test_app_context_policy_graph_hints_have_no_graph_database_or_proprietary_terms() -> None:
    paths = [
        ROOT / "mozaiksai/control_plane/app_context_policy.py",
        ROOT / "tests/test_app_context_policy_graph_hints.py",
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


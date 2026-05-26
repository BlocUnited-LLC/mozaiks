from __future__ import annotations

from pathlib import Path

from mozaiksai.control_plane.app_context import (
    APP_CONTEXT_MISSING_WARNING,
    APP_CONTEXT_STALE_WARNING,
    AppContextOwnershipBoundarySummary,
    AppContextSummary,
)
from mozaiksai.control_plane.app_context_policy import (
    APP_CONTEXT_HIGH_RISK_BLOCK_WARNING,
    AppContextPolicyDecision,
    evaluate_app_context_policy,
)
from mozaiksai.control_plane.dry_run import build_refinement_execution_plan_from_route

ROOT = Path(__file__).resolve().parents[1]


def _missing_context() -> AppContextSummary:
    return AppContextSummary(
        app_id="field_service",
        available=False,
        warnings=[APP_CONTEXT_MISSING_WARNING],
    )


def _stale_context(*, mode: str = "greenfield") -> AppContextSummary:
    return AppContextSummary(
        app_id="field_service",
        available=True,
        context_version_id="ctx_stale",
        mode=mode,
        stale_status="stale",
        stale_reasons=["source ref changed"],
        warnings=[APP_CONTEXT_STALE_WARNING],
    )


def _fresh_greenfield_context() -> AppContextSummary:
    return AppContextSummary(
        app_id="field_service",
        available=True,
        context_version_id="ctx_fresh",
        mode="greenfield",
        stale_status="current",
    )


def test_missing_context_ui_patch_warns_without_blocking() -> None:
    result = evaluate_app_context_policy(
        app_context_summary=_missing_context(),
        change_class="patch",
        refinement_lane="ui_patch",
        affected_bundle_paths=["ui/pages/home.yaml"],
    )

    assert result.decision is AppContextPolicyDecision.WARN
    assert result.allowed is True
    assert result.blocking is False
    assert APP_CONTEXT_MISSING_WARNING in result.warnings


def test_stale_context_ui_patch_warns_without_blocking() -> None:
    result = evaluate_app_context_policy(
        app_context_summary=_stale_context(),
        change_class="patch",
        refinement_lane="ui_patch",
        affected_bundle_paths=["ui/pages/home.yaml"],
    )

    assert result.decision is AppContextPolicyDecision.WARN
    assert result.allowed is True
    assert result.blocking is False
    assert APP_CONTEXT_STALE_WARNING in result.warnings


def test_missing_context_data_model_migration_blocks_for_refresh() -> None:
    result = evaluate_app_context_policy(
        app_context_summary=_missing_context(),
        change_class="feature",
        refinement_lane="data_model_migration",
        affected_bundle_paths=["config/database_migrations/add_phase.json"],
    )

    assert result.decision is AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH
    assert result.requires_context_refresh is True
    assert APP_CONTEXT_HIGH_RISK_BLOCK_WARNING in result.warnings


def test_stale_context_data_model_migration_blocks_for_refresh() -> None:
    result = evaluate_app_context_policy(
        app_context_summary=_stale_context(),
        change_class="feature",
        refinement_lane="data_model_migration",
        affected_bundle_paths=["config/database_intent.json"],
    )

    assert result.decision is AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH
    assert "data_model_migration" in result.risky_signals


def test_stale_context_integration_change_blocks_for_refresh() -> None:
    result = evaluate_app_context_policy(
        app_context_summary=_stale_context(),
        change_class="patch",
        refinement_lane="integration",
        affected_bundle_paths=["backend/integrations/email_gateway_client.py"],
    )

    assert result.decision is AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH
    assert "integration" in result.risky_signals


def test_stale_context_hosted_capability_change_blocks_for_refresh() -> None:
    result = evaluate_app_context_policy(
        app_context_summary=_stale_context(),
        change_class="design",
        refinement_lane="hosted_capability_change",
        affected_bundle_paths=["modules/hosted_reports/module.yaml"],
    )

    assert result.decision is AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH
    assert "hosted_capability_change" in result.risky_signals


def test_stale_context_conceptual_reframe_core_blocks_for_refresh() -> None:
    result = evaluate_app_context_policy(
        app_context_summary=_stale_context(),
        change_class="core",
        refinement_lane="conceptual_reframe",
        affected_bundle_paths=[],
    )

    assert result.decision is AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH
    assert "conceptual_or_architecture_replan" in result.risky_signals


def test_fresh_greenfield_context_allows_module_backend_feature() -> None:
    result = evaluate_app_context_policy(
        app_context_summary=_fresh_greenfield_context(),
        change_class="feature",
        refinement_lane="feature_addition",
        affected_bundle_paths=["modules/work_orders/backend/service.py"],
    )

    assert result.decision is AppContextPolicyDecision.ALLOW
    assert result.allowed is True
    assert result.blocking is False
    assert result.risk_level == "high"


def test_stale_brownfield_source_affecting_change_blocks_for_refresh() -> None:
    summary = _stale_context(mode="brownfield")
    summary.ownership_boundaries = [
        AppContextOwnershipBoundarySummary(
            path_or_artifact="src/orders",
            ownership="read_only_discovered",
        )
    ]

    result = evaluate_app_context_policy(
        app_context_summary=summary,
        change_class="feature",
        refinement_lane="feature_addition",
        affected_bundle_paths=["src/orders/service.py"],
    )

    assert result.decision is AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH
    assert "read_only_discovered_boundary" in result.risky_signals
    assert "brownfield_source_affecting_change" in result.risky_signals


def test_context_policy_result_attaches_to_execution_plan_without_rerouting() -> None:
    base_kwargs = {
        "request": "Change the email gateway integration behavior.",
        "artifact_kind": "app_bundle",
        "change_class": "patch",
        "workflow_id": "AppGenerator",
        "workflow_sequence": "app_revision",
        "affected_workflows": ["AppGenerator"],
        "affected_declarative_families": ["app_bundle"],
        "affected_bundle_paths": ["backend/integrations/email_gateway_client.py"],
        "scope_summary": "Integration update.",
        "app_id": "field_service",
    }
    fresh_plan = build_refinement_execution_plan_from_route(
        **base_kwargs,
        app_context_summary=_fresh_greenfield_context(),
    )
    stale_plan = build_refinement_execution_plan_from_route(
        **base_kwargs,
        app_context_summary=_stale_context(),
    )

    assert stale_plan.context_policy_decision is not None
    assert stale_plan.context_policy_decision.decision is AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH
    assert APP_CONTEXT_HIGH_RISK_BLOCK_WARNING in stale_plan.warnings
    assert fresh_plan.workflow_sequence == stale_plan.workflow_sequence == "app_revision"
    assert fresh_plan.target_workflow == stale_plan.target_workflow == "AppGenerator"
    assert fresh_plan.affected_bundle_paths == stale_plan.affected_bundle_paths


def test_control_plane_app_context_policy_has_no_graph_database_or_proprietary_terms() -> None:
    paths = [
        ROOT / "mozaiksai/control_plane/app_context_policy.py",
        ROOT / "tests/test_control_plane_app_context_policy.py",
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

from __future__ import annotations

from pathlib import Path

import pytest

from mozaiksai.control_plane.app_context import (
    APP_CONTEXT_MISSING_WARNING,
    APP_CONTEXT_STALE_WARNING,
    AppContextSummary,
)
from mozaiksai.control_plane.app_context_override import (
    APP_CONTEXT_POLICY_OVERRIDE_ALLOW_WARNING,
    APP_CONTEXT_POLICY_OVERRIDE_REFRESH_WARNING,
    APP_CONTEXT_POLICY_OVERRIDE_REJECT_WARNING,
    APP_CONTEXT_POLICY_OVERRIDE_WARNING,
    AppContextPolicyOverrideDecision,
    apply_app_context_policy_override,
    create_app_context_policy_override,
)
from mozaiksai.control_plane.app_context_policy import (
    AppContextPolicyDecision,
    evaluate_app_context_policy,
)
from mozaiksai.control_plane.dry_run import build_refinement_execution_plan_from_route

ROOT = Path(__file__).resolve().parents[1]


def _stale_context() -> AppContextSummary:
    return AppContextSummary(
        app_id="field_service",
        available=True,
        context_version_id="ctx_stale",
        mode="brownfield",
        stale_status="stale",
        stale_reasons=["source ref changed"],
        warnings=[APP_CONTEXT_STALE_WARNING],
    )


def _missing_context() -> AppContextSummary:
    return AppContextSummary(
        app_id="field_service",
        available=False,
        warnings=[APP_CONTEXT_MISSING_WARNING],
    )


def _blocked_execution_plan():
    return build_refinement_execution_plan_from_route(
        request="Add a required project phase field and migrate existing records.",
        artifact_kind="app_bundle",
        change_class="feature",
        workflow_id="AppGenerator",
        workflow_sequence="app_revision",
        affected_workflows=["AppGenerator"],
        affected_declarative_families=["app_bundle"],
        affected_bundle_paths=["data/contract.json"],
        scope_summary="Data migration update.",
        app_id="field_service",
        request_id="request_123",
        app_context_summary=_stale_context(),
    )


def _validation_ids(plan) -> list[str]:
    return [item.id for item in plan.validation_plan.items if item.required]


def test_can_create_override_for_block_requires_context_refresh() -> None:
    plan = _blocked_execution_plan()
    assert plan.context_policy_decision is not None
    assert plan.context_policy_decision.decision is AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH

    override = create_app_context_policy_override(
        policy_result=plan.context_policy_decision,
        app_id="field_service",
        request_id="request_123",
        context_version_id="ctx_stale",
        override_decision=AppContextPolicyOverrideDecision.ALLOW_WITH_WARNING,
        reason="Operator confirmed source refs and accepts planning risk.",
        reviewer="reviewer@example.invalid",
        applies_to_paths=list(plan.affected_bundle_paths),
        applies_to_change_class=plan.change_class,
        applies_to_refinement_lane=plan.refinement_lane,
    )

    assert override.original_policy_decision is AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH
    assert override.override_decision is AppContextPolicyOverrideDecision.ALLOW_WITH_WARNING
    assert override.context_version_id == "ctx_stale"
    assert override.mutation_allowed is False
    assert APP_CONTEXT_POLICY_OVERRIDE_WARNING in override.warnings


def test_can_create_override_for_block_requires_human_override() -> None:
    policy = evaluate_app_context_policy(
        app_context_summary=_missing_context(),
        change_class="feature",
        refinement_lane="data_model_migration",
        affected_bundle_paths=["data/contract.json"],
        human_override_requested=True,
    )
    assert policy.decision is AppContextPolicyDecision.BLOCK_REQUIRES_HUMAN_OVERRIDE

    override = create_app_context_policy_override(
        policy_result=policy,
        app_id="field_service",
        request_id="request_123",
        override_decision="allow_with_warning",
        reason="Reviewer accepts missing context risk for planning only.",
        reviewer="reviewer@example.invalid",
    )

    assert override.original_policy_decision is AppContextPolicyDecision.BLOCK_REQUIRES_HUMAN_OVERRIDE
    assert override.override_decision is AppContextPolicyOverrideDecision.ALLOW_WITH_WARNING


def test_allow_with_warning_attaches_to_human_override_block() -> None:
    policy = evaluate_app_context_policy(
        app_context_summary=_missing_context(),
        change_class="feature",
        refinement_lane="data_model_migration",
        affected_bundle_paths=["data/contract.json"],
        human_override_requested=True,
    )
    plan = _blocked_execution_plan().model_copy(
        update={
            "app_context_summary": _missing_context(),
            "context_policy_decision": policy,
        }
    )
    override = create_app_context_policy_override(
        policy_result=policy,
        app_id="field_service",
        request_id="request_123",
        override_decision="allow_with_warning",
        reason="Reviewer accepts missing context risk for planning only.",
        reviewer="reviewer@example.invalid",
        applies_to_paths=list(plan.affected_bundle_paths),
        applies_to_change_class=plan.change_class,
        applies_to_refinement_lane=plan.refinement_lane,
    )

    updated = apply_app_context_policy_override(plan, override)

    assert updated.context_policy_decision.decision is AppContextPolicyDecision.WARN
    assert updated.context_policy_decision.requires_human_override is False
    assert updated.app_context_policy_override == override


def test_override_requires_reviewer() -> None:
    plan = _blocked_execution_plan()

    with pytest.raises(ValueError):
        create_app_context_policy_override(
            policy_result=plan.context_policy_decision,
            app_id="field_service",
            request_id="request_123",
            override_decision="allow_with_warning",
            reason="Reason is present.",
            reviewer=" ",
        )


def test_override_requires_reason() -> None:
    plan = _blocked_execution_plan()

    with pytest.raises(ValueError):
        create_app_context_policy_override(
            policy_result=plan.context_policy_decision,
            app_id="field_service",
            request_id="request_123",
            override_decision="allow_with_warning",
            reason=" ",
            reviewer="reviewer@example.invalid",
        )


def test_allow_with_warning_unblocks_planning_but_preserves_scope_and_validation() -> None:
    plan = _blocked_execution_plan()
    original_paths = list(plan.affected_bundle_paths)
    original_route = (plan.workflow_sequence, plan.target_workflow)
    original_validation = _validation_ids(plan)

    override = create_app_context_policy_override(
        policy_result=plan.context_policy_decision,
        app_id="field_service",
        request_id="request_123",
        context_version_id="ctx_stale",
        override_decision="allow_with_warning",
        reason="Proceed with planning only after reviewer assessment.",
        reviewer="reviewer@example.invalid",
        applies_to_paths=original_paths,
        applies_to_change_class=plan.change_class,
        applies_to_refinement_lane=plan.refinement_lane,
    )
    updated = apply_app_context_policy_override(plan, override)

    assert updated.context_policy_decision.decision is AppContextPolicyDecision.WARN
    assert updated.context_policy_decision.allowed is True
    assert updated.context_policy_decision.blocking is False
    assert APP_CONTEXT_POLICY_OVERRIDE_ALLOW_WARNING in updated.warnings
    assert updated.app_context_policy_override == override
    assert updated.workflow_sequence == original_route[0]
    assert updated.target_workflow == original_route[1]
    assert updated.affected_bundle_paths == original_paths
    assert _validation_ids(updated) == original_validation
    assert updated.mutation_allowed is False


def test_require_refresh_first_does_not_unblock() -> None:
    plan = _blocked_execution_plan()
    override = create_app_context_policy_override(
        policy_result=plan.context_policy_decision,
        app_id="field_service",
        request_id="request_123",
        context_version_id="ctx_stale",
        override_decision="require_refresh_first",
        reason="Reviewer requires refresh before proceeding.",
        reviewer="reviewer@example.invalid",
        applies_to_paths=list(plan.affected_bundle_paths),
        applies_to_change_class=plan.change_class,
        applies_to_refinement_lane=plan.refinement_lane,
    )
    updated = apply_app_context_policy_override(plan, override)

    assert updated.context_policy_decision.decision is AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH
    assert updated.context_policy_decision.blocking is True
    assert APP_CONTEXT_POLICY_OVERRIDE_REFRESH_WARNING in updated.warnings


def test_reject_does_not_unblock() -> None:
    plan = _blocked_execution_plan()
    override = create_app_context_policy_override(
        policy_result=plan.context_policy_decision,
        app_id="field_service",
        request_id="request_123",
        context_version_id="ctx_stale",
        override_decision="reject",
        reason="Reviewer rejected the requested refinement.",
        reviewer="reviewer@example.invalid",
        applies_to_paths=list(plan.affected_bundle_paths),
        applies_to_change_class=plan.change_class,
        applies_to_refinement_lane=plan.refinement_lane,
    )
    updated = apply_app_context_policy_override(plan, override)

    assert updated.context_policy_decision.decision is AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH
    assert updated.context_policy_decision.blocking is True
    assert APP_CONTEXT_POLICY_OVERRIDE_REJECT_WARNING in updated.warnings


def test_override_is_scoped_to_app_request_context_paths_and_lane() -> None:
    plan = _blocked_execution_plan()
    override = create_app_context_policy_override(
        policy_result=plan.context_policy_decision,
        app_id="field_service",
        request_id="request_123",
        context_version_id="ctx_stale",
        override_decision="allow_with_warning",
        reason="Proceed with scoped planning only.",
        reviewer="reviewer@example.invalid",
        applies_to_paths=["data/contract.json"],
        applies_to_change_class="feature",
        applies_to_refinement_lane="data_model_migration",
    )

    assert override.app_id == "field_service"
    assert override.request_id == "request_123"
    assert override.context_version_id == "ctx_stale"
    assert override.applies_to_paths == ["data/contract.json"]
    assert override.applies_to_change_class == "feature"
    assert override.applies_to_refinement_lane == "data_model_migration"

    mismatched_plan = plan.model_copy(update={"affected_bundle_paths": ["modules/orders/module.yaml"]})
    with pytest.raises(ValueError):
        apply_app_context_policy_override(mismatched_plan, override)


def test_override_has_no_graph_database_or_proprietary_terms() -> None:
    paths = [
        ROOT / "mozaiksai/control_plane/app_context_override.py",
        ROOT / "tests/test_app_context_policy_override.py",
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


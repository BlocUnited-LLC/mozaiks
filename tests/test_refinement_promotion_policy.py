from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from mozaiksai.control_plane import dry_run
from mozaiksai.control_plane.promotion_policy import evaluate_refinement_promotion_policy
from mozaiksai.control_plane.review import RefinementReviewRecord
from mozaiksai.control_plane.scoped_execution import (
    ScopedRefinementChangedFile,
    ScopedRefinementResult,
)
from mozaiksai.control_plane.validation_evidence import ValidationEvidence


def _build_plan(
    tmp_path: Path,
    *,
    request: str,
    change_class: str,
    workflow_sequence: str,
    affected_bundle_paths: list[str],
    affected_declarative_families: list[str] | None = None,
    request_id: str = "req_policy_001",
) -> dry_run.RefinementExecutionPlan:
    return dry_run.build_refinement_execution_plan_from_route(
        request=request,
        build_family="app_bundle",
        change_class=change_class,
        workflow_id="AppGenerator",
        workflow_sequence=workflow_sequence,
        affected_workflows=["AppGenerator"],
        affected_declarative_families=affected_declarative_families or [],
        affected_bundle_paths=affected_bundle_paths,
        scope_summary="Policy test refinement scope.",
        app_id="sample_app",
        request_id=request_id,
        execution_mode="staged",
        staging_base_path=tmp_path / ".refinement_staging",
    )


def _policy_inputs(
    tmp_path: Path,
    *,
    request: str,
    change_class: str,
    workflow_sequence: str,
    affected_bundle_paths: list[str],
    candidate_path: str,
    affected_declarative_families: list[str] | None = None,
    validation_completed: list[str] | None = None,
    validation_failed: list[str] | None = None,
    validation_artifacts: list[str] | None = None,
    request_id: str = "req_policy_001",
) -> tuple[
    dry_run.RefinementExecutionPlan,
    RefinementReviewRecord,
    ScopedRefinementResult,
    ScopedRefinementChangedFile,
    ValidationEvidence,
]:
    plan = _build_plan(
        tmp_path,
        request=request,
        change_class=change_class,
        workflow_sequence=workflow_sequence,
        affected_bundle_paths=affected_bundle_paths,
        affected_declarative_families=affected_declarative_families,
        request_id=request_id,
    )
    staging_area = Path(plan.staging_area or tmp_path / ".refinement_staging" / "sample_app" / request_id)
    review = RefinementReviewRecord(
        request_id=plan.request_id,
        status="promotion_ready",
        reviewer="reviewer-1",
        reviewed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        decision="promotion_ready",
        notes=None,
        promotion_allowed=True,
        source_bundle_path=None,
        staging_area=staging_area.as_posix(),
        affected_bundle_paths=list(plan.affected_bundle_paths),
        mutation_allowed=False,
    )
    changed_file = ScopedRefinementChangedFile(
        path=candidate_path,
        status="updated",
        reason="Policy test change.",
        staged_path=(staging_area / "workspace" / candidate_path).as_posix(),
    )
    execution_result = ScopedRefinementResult(
        request_id=plan.request_id,
        staging_area=staging_area.as_posix(),
        changed_files=[changed_file],
        result_path=(staging_area / "execution_result.json").as_posix(),
    )
    evidence = ValidationEvidence(
        completed=list(validation_completed or []),
        failed=list(validation_failed or []),
        warnings=[],
        artifacts=list(validation_artifacts or []),
        checked_at="2026-05-21T00:00:00Z",
        source="policy-test",
    )
    return plan, review, execution_result, changed_file, evidence


def test_validation_evidence_tracks_completed_failed_warnings_and_artifacts() -> None:
    evidence = ValidationEvidence(
        completed=["route_component_validation", "ui_theme_primitive_validation"],
        failed=["migration_plan_validation"],
        warnings=["manual review recommended"],
        artifacts=["data/contract.json"],
        checked_at="2026-05-21T00:00:00Z",
        source="unit-test",
    )

    assert evidence.completed_names() == {"route_component_validation", "ui_theme_primitive_validation"}
    assert evidence.failed_names() == {"migration_plan_validation"}
    assert evidence.artifact_names() == {"data/contract.json"}
    assert evidence.warnings == ["manual review recommended"]
    assert evidence.checked_at == "2026-05-21T00:00:00Z"
    assert evidence.source == "unit-test"


def test_ui_patch_allows_direct_promotion_of_dashboard_yaml(tmp_path: Path) -> None:
    plan, review, execution_result, changed_file, evidence = _policy_inputs(
        tmp_path,
        request="Fix a dashboard label.",
        change_class="patch",
        workflow_sequence="app_revision",
        affected_bundle_paths=["ui/pages/dashboard.yaml"],
        candidate_path="ui/pages/dashboard.yaml",
        validation_completed=["route_component_validation", "ui_theme_primitive_validation"],
    )

    decision = evaluate_refinement_promotion_policy(
        plan=plan,
        review_record=review,
        execution_result=execution_result,
        change=changed_file,
        validation_evidence=evidence,
    )

    assert decision.allowed is True
    assert decision.mode == "direct_leaf_patch"


def test_ui_patch_blocks_when_validation_failed(tmp_path: Path) -> None:
    plan, review, execution_result, changed_file, evidence = _policy_inputs(
        tmp_path,
        request="Fix a dashboard label.",
        change_class="patch",
        workflow_sequence="app_revision",
        affected_bundle_paths=["ui/pages/dashboard.yaml"],
        candidate_path="ui/pages/dashboard.yaml",
        validation_failed=["route_component_validation"],
    )

    decision = evaluate_refinement_promotion_policy(
        plan=plan,
        review_record=review,
        execution_result=execution_result,
        change=changed_file,
        validation_evidence=evidence,
    )

    assert decision.allowed is False
    assert decision.mode == "blocked_requires_validation"
    assert "route_component_validation" in decision.required_validation


def test_experience_design_blocks_dashboard_yaml_without_experience_spec(tmp_path: Path) -> None:
    plan, review, execution_result, changed_file, evidence = _policy_inputs(
        tmp_path,
        request="Change the dashboard experience to highlight reports first.",
        change_class="design",
        workflow_sequence="app_surface_revision",
        affected_bundle_paths=["ui/pages/dashboard.yaml"],
        affected_declarative_families=["experience_spec"],
        candidate_path="ui/pages/dashboard.yaml",
        validation_completed=["route_component_validation", "ui_theme_primitive_validation"],
    )

    decision = evaluate_refinement_promotion_policy(
        plan=plan,
        review_record=review,
        execution_result=execution_result,
        change=changed_file,
        validation_evidence=evidence,
    )

    assert decision.allowed is False
    assert decision.mode == "blocked_requires_upstream_artifact"
    assert decision.required_artifacts == ["experience_spec"]
    assert "experience_spec_update" in decision.required_validation


def test_experience_design_allows_dashboard_yaml_with_experience_spec_evidence(tmp_path: Path) -> None:
    plan, review, execution_result, changed_file, evidence = _policy_inputs(
        tmp_path,
        request="Change the dashboard experience to highlight reports first.",
        change_class="design",
        workflow_sequence="app_surface_revision",
        affected_bundle_paths=["ui/pages/dashboard.yaml"],
        affected_declarative_families=["experience_spec"],
        candidate_path="ui/pages/dashboard.yaml",
        validation_completed=[
            "experience_spec_validation",
            "app_bundle_validation",
            "route_component_validation",
            "ui_theme_primitive_validation",
        ],
        validation_artifacts=["experience_spec"],
    )

    decision = evaluate_refinement_promotion_policy(
        plan=plan,
        review_record=review,
        execution_result=execution_result,
        change=changed_file,
        validation_evidence=evidence,
    )

    assert decision.allowed is True
    assert decision.mode == "direct_leaf_patch"


def test_data_model_migration_blocks_repo_py_without_database_artifacts(tmp_path: Path) -> None:
    plan, review, execution_result, changed_file, evidence = _policy_inputs(
        tmp_path,
        request="Add a required project phase field and migrate existing project records.",
        change_class="feature",
        workflow_sequence="app_revision",
        affected_bundle_paths=["modules/projects/backend/repo.py"],
        candidate_path="modules/projects/backend/repo.py",
        validation_completed=["data_contract_validation", "migration_plan_validation"],
    )

    decision = evaluate_refinement_promotion_policy(
        plan=plan,
        review_record=review,
        execution_result=execution_result,
        change=changed_file,
        validation_evidence=evidence,
    )

    assert decision.allowed is False
    assert decision.mode == "blocked_requires_upstream_artifact"
    assert decision.required_artifacts == ["data/contract.json", "data/migrations/*.json"]


def test_data_model_migration_allows_repo_py_with_data_contract_evidence(tmp_path: Path) -> None:
    plan, review, execution_result, changed_file, evidence = _policy_inputs(
        tmp_path,
        request="Add a required project phase field and migrate existing project records.",
        change_class="feature",
        workflow_sequence="app_revision",
        affected_bundle_paths=["modules/projects/backend/repo.py"],
        candidate_path="modules/projects/backend/repo.py",
        validation_completed=["data_contract_validation", "migration_plan_validation"],
        validation_artifacts=["data/contract.json", "data/migrations/001_initial.json"],
    )

    decision = evaluate_refinement_promotion_policy(
        plan=plan,
        review_record=review,
        execution_result=execution_result,
        change=changed_file,
        validation_evidence=evidence,
    )

    assert decision.allowed is True
    assert decision.mode == "staged_generated_artifact"


def test_core_conceptual_reframe_blocks_direct_promotion(tmp_path: Path) -> None:
    plan, review, execution_result, changed_file, evidence = _policy_inputs(
        tmp_path,
        request="Reframe the product around a different value proposition.",
        change_class="core",
        workflow_sequence="conceptual_replan",
        affected_bundle_paths=["ui/pages/dashboard.yaml"],
        candidate_path="ui/pages/dashboard.yaml",
    )

    decision = evaluate_refinement_promotion_policy(
        plan=plan,
        review_record=review,
        execution_result=execution_result,
        change=changed_file,
        validation_evidence=evidence,
    )

    assert decision.allowed is False
    assert decision.mode == "blocked_requires_replan"


def test_managed_capability_blocks_managed_module_internal_path(tmp_path: Path) -> None:
    plan, review, execution_result, changed_file, evidence = _policy_inputs(
        tmp_path,
        request="Change managed analytics dashboard display.",
        change_class="design",
        workflow_sequence="app_surface_revision",
        affected_bundle_paths=["modules/managed_analytics/backend/service.py"],
        candidate_path="modules/managed_analytics/backend/service.py",
    )

    decision = evaluate_refinement_promotion_policy(
        plan=plan,
        review_record=review,
        execution_result=execution_result,
        change=changed_file,
        validation_evidence=evidence,
    )

    assert decision.allowed is False
    assert decision.mode == "blocked_requires_replan"


def test_managed_capability_allows_backend_integrations_client_path(tmp_path: Path) -> None:
    plan, review, execution_result, changed_file, evidence = _policy_inputs(
        tmp_path,
        request="Change managed analytics dashboard display.",
        change_class="design",
        workflow_sequence="app_surface_revision",
        affected_bundle_paths=["services/integrations/managed_analytics_client.py"],
        candidate_path="services/integrations/managed_analytics_client.py",
        validation_completed=["managed_facade_boundary_validation"],
    )

    decision = evaluate_refinement_promotion_policy(
        plan=plan,
        review_record=review,
        execution_result=execution_result,
        change=changed_file,
        validation_evidence=evidence,
    )

    assert decision.allowed is True
    assert decision.mode == "staged_generated_artifact"


def test_integration_allows_analytics_provider_client_path(tmp_path: Path) -> None:
    plan, review, execution_result, changed_file, evidence = _policy_inputs(
        tmp_path,
        request="Change the analytics_provider connector sync behavior.",
        change_class="patch",
        workflow_sequence="app_revision",
        affected_bundle_paths=["services/integrations/analytics_provider_client.py"],
        candidate_path="services/integrations/analytics_provider_client.py",
        validation_completed=["integration_readiness_validation"],
    )

    decision = evaluate_refinement_promotion_policy(
        plan=plan,
        review_record=review,
        execution_result=execution_result,
        change=changed_file,
        validation_evidence=evidence,
    )

    assert decision.allowed is True
    assert decision.mode == "staged_generated_artifact"


def test_default_empty_evidence_is_conservative(tmp_path: Path) -> None:
    plan, review, execution_result, changed_file, evidence = _policy_inputs(
        tmp_path,
        request="Fix a dashboard label.",
        change_class="patch",
        workflow_sequence="app_revision",
        affected_bundle_paths=["ui/pages/dashboard.yaml"],
        candidate_path="ui/pages/dashboard.yaml",
    )

    decision = evaluate_refinement_promotion_policy(
        plan=plan,
        review_record=review,
        execution_result=execution_result,
        change=changed_file,
        validation_evidence=evidence,
    )

    assert decision.allowed is False
    assert decision.mode == "blocked_requires_validation"
    assert decision.required_validation == ["route_component_validation", "ui_theme_primitive_validation"]


def test_failed_validation_blocks_even_with_other_completed_validations(tmp_path: Path) -> None:
    plan, review, execution_result, changed_file, evidence = _policy_inputs(
        tmp_path,
        request="Fix a dashboard label.",
        change_class="patch",
        workflow_sequence="app_revision",
        affected_bundle_paths=["ui/pages/dashboard.yaml"],
        candidate_path="ui/pages/dashboard.yaml",
        validation_completed=["route_component_validation", "ui_theme_primitive_validation"],
        validation_failed=["ui_theme_primitive_validation"],
    )

    decision = evaluate_refinement_promotion_policy(
        plan=plan,
        review_record=review,
        execution_result=execution_result,
        change=changed_file,
        validation_evidence=evidence,
    )

    assert decision.allowed is False
    assert decision.mode == "blocked_requires_validation"



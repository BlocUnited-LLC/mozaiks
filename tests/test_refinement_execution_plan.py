from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from mozaiksai.control_plane import dry_run
from mozaiksai.control_plane.config import load_control_plane_config

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "factory_app" / "app"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "refinement_classifier_smoke_output.json"


def _run_execution_plan(request: str, change_class: str | None = None) -> dry_run.RefinementExecutionPlan:
    return asyncio.run(
        dry_run.build_refinement_execution_plan(
            request=request,
            change_class=change_class,
            app_root=APP_ROOT,
            app_id="sample_app",
        )
    )


def _required_validation_ids(plan: dry_run.RefinementExecutionPlan) -> set[str]:
    return {item.id for item in plan.validation_plan.items if item.required}


def _generated_status() -> str:
    completed = subprocess.run(
        ["git", "status", "--short", "--", "generated", "generated_apps"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_ui_refinement_execution_plan_uses_app_surface_revision() -> None:
    plan = _run_execution_plan("Change the dashboard experience to highlight reports first.", "design")

    assert plan.workflow_sequence == "app_surface_revision"
    assert plan.refinement_lane == "experience_design"
    assert "design_docs" in plan.affected_declarative_families
    assert {"route_component_validation", "ui_theme_primitive_validation"} <= _required_validation_ids(plan)


def test_module_backend_refinement_execution_plan_uses_app_revision() -> None:
    plan = _run_execution_plan("Add an archive_project action to the projects module API.", "feature")

    assert plan.workflow_sequence == "app_revision"
    assert plan.refinement_lane == "feature_addition"
    assert "module_contract_validation" in _required_validation_ids(plan)
    assert "modules/projects/backend/service.py" in plan.affected_bundle_paths


def test_integration_refinement_execution_plan_requires_readiness_validation() -> None:
    plan = _run_execution_plan("Change the analytics_provider connector sync behavior.", "patch")

    assert plan.workflow_sequence == "app_revision"
    assert "integration_readiness" in _required_validation_ids(plan)
    assert plan.mutation_allowed is False


def test_data_model_refinement_execution_plan_requires_migration_review_and_human_review() -> None:
    plan = _run_execution_plan(
        "Add a required project phase field and migrate existing project records.",
        "feature",
    )

    assert "database_migration_review" in _required_validation_ids(plan)
    assert plan.human_review_required is True
    assert "Data migration review is required before mutation." in plan.warnings
    assert "Human review is required before mutation." in plan.warnings


def test_managed_capability_refinement_execution_plan_requires_facade_validation() -> None:
    plan = _run_execution_plan("Change managed analytics dashboard display.", "design")

    assert plan.workflow_sequence == "app_surface_revision"
    assert "managed_facade_validation" in _required_validation_ids(plan)
    assert "services/integrations/managed_analytics_client.py" in plan.affected_bundle_paths


def test_execution_plan_defaults_to_dry_run_without_mutation() -> None:
    plan = _run_execution_plan("Change the dashboard experience to highlight reports first.", "design")

    assert plan.execution_mode == "dry_run"
    assert plan.mutation_allowed is False
    assert plan.output_workspace is None
    assert plan.staging_area is None
    assert dry_run.DRY_RUN_NOTICE in plan.warnings


def test_staged_execution_plan_reserves_path_without_writing_files(tmp_path: Path) -> None:
    staging_root = tmp_path / "refinement-staging"
    plan = asyncio.run(
        dry_run.build_refinement_execution_plan(
            request="Add an archive_project action to the projects module API.",
            change_class="feature",
            app_root=APP_ROOT,
            app_id="sample_app",
            execution_mode="staged",
            staging_base_path=staging_root,
        )
    )

    assert plan.execution_mode == "staged"
    assert plan.mutation_allowed is False
    assert plan.staging_area is not None
    assert plan.output_workspace == plan.staging_area
    assert not staging_root.exists()


def test_execution_plan_does_not_write_generated_app_files() -> None:
    before = _generated_status()
    _run_execution_plan("Add an archive_project action to the projects module API.", "feature")
    after = _generated_status()

    assert after == before


def test_execution_plan_profiles_resolve_through_named_registry() -> None:
    plan = _run_execution_plan("Add an archive_project action to the projects module API.", "feature")

    assert plan.profiles.classifier == "classifier"
    assert plan.profiles.planner_replanner == "planner_replanner"
    assert plan.profiles.codegen == "codegen"
    assert plan.profiles.reviewer_validator == "reviewer_validator"


def test_live_classifier_fixture_cases_convert_to_execution_plans() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    config = load_control_plane_config(APP_ROOT)

    plans = [
        dry_run.build_refinement_execution_plan_from_route(
            request=case["request"],
            build_family="app_bundle",
            change_class=case["classifier"]["change_class"],
            workflow_id=case["route"]["workflow_id"],
            workflow_sequence=case["route"]["workflow_sequence"],
            affected_workflows=case["route"]["affected_workflows"],
            affected_declarative_families=case["impact"]["affected_declarative_families"],
            affected_bundle_paths=case["impact"]["affected_bundle_paths"],
            scope_summary=case["impact"]["scope_summary"],
            requires_replanning=case["route"]["workflow_sequence"] != "app_revision",
            config=config,
            app_id="fixture_app",
            request_id=f"fixture_{case['id']}",
        )
        for case in payload["cases"]
    ]

    by_request_id = {plan.request_id: plan for plan in plans}
    assert len(by_request_id) == len(payload["cases"])
    assert all(plan.mutation_allowed is False for plan in plans)
    assert "integration_readiness" in _required_validation_ids(by_request_id["fixture_external_integration"])
    assert "database_migration_review" in _required_validation_ids(by_request_id["fixture_data_model_migration"])
    assert "managed_facade_validation" in _required_validation_ids(by_request_id["fixture_managed_capability_facade"])


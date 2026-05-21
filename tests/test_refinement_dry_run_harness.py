from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from mozaiksai.control_plane import dry_run
from scripts import dry_run_refinement

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "factory_app" / "app"


def _run_plan(request: str, change_class: str | None = None) -> dry_run.RefinementDryRunPlan:
    return asyncio.run(
        dry_run.build_refinement_dry_run_plan(
            request=request,
            change_class=change_class,
            app_root=APP_ROOT,
        )
    )


def _generated_status() -> str:
    completed = subprocess.run(
        ["git", "status", "--short", "--", "generated", "generated_apps"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_dry_run_returns_mutation_allowed_false() -> None:
    plan = _run_plan("Change the dashboard experience to highlight reports first.", "design")

    assert plan.execution_mode == "dry_run"
    assert plan.mutation_allowed is False
    assert dry_run.DRY_RUN_NOTICE in plan.warnings


def test_dry_run_returns_generated_files_changed_false() -> None:
    plan = _run_plan("Change the dashboard experience to highlight reports first.", "design")

    assert plan.generated_files_changed is False


def test_ui_request_returns_app_surface_revision_and_ui_paths() -> None:
    plan = _run_plan("Change the dashboard experience to highlight reports first.", "design")

    assert plan.workflow_sequence == "app_surface_revision"
    assert plan.next_step == "app_surface_revision"
    assert "experience_spec" in plan.affected_declarative_families
    assert "ui/pages/dashboard.yaml" in plan.affected_bundle_paths
    assert "ui/route_manifest.json" in plan.affected_bundle_paths


def test_module_request_returns_app_revision_and_module_paths() -> None:
    plan = _run_plan("Add an archive_project action to the projects module API.", "feature")

    assert plan.workflow_sequence == "app_revision"
    assert plan.next_step == "app_revision"
    assert "modules/projects/module.yaml" in plan.affected_bundle_paths
    assert "modules/projects/backend/handler.py" in plan.affected_bundle_paths
    assert "modules/projects/backend/service.py" in plan.affected_bundle_paths
    assert "modules/projects/backend/schemas.py" in plan.affected_bundle_paths


def test_integration_request_includes_adapter_and_readiness_paths() -> None:
    plan = _run_plan("Change the analytics_provider connector sync behavior.", "patch")

    assert plan.workflow_sequence == "app_revision"
    assert plan.next_step == "scoped_patch_candidate"
    assert "backend/integrations/analytics_provider_client.py" in plan.affected_bundle_paths
    assert "modules/reports/backend/service.py" in plan.affected_bundle_paths
    assert "modules/reports/backend/schemas.py" in plan.affected_bundle_paths
    assert "Integration readiness may need to be rechecked." in plan.scope_summary


def test_data_model_request_includes_database_paths_and_review_warning() -> None:
    plan = _run_plan(
        "Add a required project phase field and migrate existing project records.",
        "feature",
    )

    assert "config/database_intent.json" in plan.affected_bundle_paths
    assert "config/database_migrations/001_initial.json" in plan.affected_bundle_paths
    assert "modules/projects/backend/repo.py" in plan.affected_bundle_paths
    assert "modules/projects/backend/policy.py" in plan.affected_bundle_paths
    assert "Destructive changes require explicit review." in plan.scope_summary
    assert plan.next_step == "needs_human_review"


def test_hosted_capability_request_includes_adapter_facade_and_page_paths() -> None:
    plan = _run_plan("Change hosted analytics dashboard display.", "design")

    assert plan.workflow_sequence == "app_surface_revision"
    assert "backend/integrations/hosted_analytics_client.py" in plan.affected_bundle_paths
    assert "modules/analytics_dashboard/module.yaml" in plan.affected_bundle_paths
    assert "modules/analytics_dashboard/backend/service.py" in plan.affected_bundle_paths
    assert "ui/pages/analytics.yaml" in plan.affected_bundle_paths


def test_no_secret_paths_are_emitted() -> None:
    plan = _run_plan("Change the analytics_provider connector sync behavior.", "patch")

    assert not any(dry_run.path_has_secret_marker(path) for path in plan.affected_bundle_paths)


def test_no_generated_files_are_written() -> None:
    before = _generated_status()
    _run_plan("Add an archive_project action to the projects module API.", "feature")
    after = _generated_status()

    assert after == before


def test_live_classifier_is_not_used_by_default(monkeypatch) -> None:
    def fail_classifier(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("Live classifier should not be instantiated by default")

    monkeypatch.setattr(dry_run, "LLMChangeClassifier", fail_classifier)

    plan = _run_plan("Change the dashboard experience to highlight reports first.")

    assert plan.change_class == "design"


def test_profile_references_resolve() -> None:
    plan = _run_plan("Add an archive_project action to the projects module API.", "feature")

    assert plan.profiles.classifier == "classifier"
    assert plan.profiles.planner_or_codegen == "planner_replanner"
    assert plan.profiles.reviewer_validator == "reviewer_validator"


def test_json_output_path_works(capsys) -> None:
    exit_code = dry_run_refinement.main(
        [
            "--request",
            "Change the dashboard experience to highlight reports first.",
            "--change-class",
            "design",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["execution_mode"] == "dry_run"
    assert payload["mutation_allowed"] is False
    assert payload["workflow_sequence"] == "app_surface_revision"


def test_save_plan_writes_only_to_explicit_path(tmp_path: Path) -> None:
    save_path = tmp_path / "dry-run-plan.json"
    before = _generated_status()
    exit_code = dry_run_refinement.main(
        [
            "--request",
            "Add an archive_project action to the projects module API.",
            "--change-class",
            "feature",
            "--save-plan",
            str(save_path),
        ]
    )
    after = _generated_status()

    assert exit_code == 0
    assert save_path.exists()
    payload = json.loads(save_path.read_text(encoding="utf-8"))
    assert payload["execution_mode"] == "dry_run"
    assert after == before


def test_no_proprietary_examples() -> None:
    combined = "\n".join(
        [
            (REPO_ROOT / "mozaiksai" / "control_plane" / "dry_run.py").read_text(encoding="utf-8"),
            (REPO_ROOT / "scripts" / "dry_run_refinement.py").read_text(encoding="utf-8"),
        ]
    ).lower()

    assert "app zero" not in combined
    assert "app_zero" not in combined
    assert "mozaiks-app" not in combined
    assert "blocunited" not in combined

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mozaiksai.control_plane import dry_run
from mozaiksai.control_plane.promotion import RefinementPromotionError, promote_refinement_staging
from mozaiksai.control_plane.review import (
    approve_refinement_staging,
    create_refinement_review_record,
    load_refinement_review_record,
    mark_refinement_promotion_ready,
)
from mozaiksai.control_plane.scoped_execution import (
    EXECUTION_RESULT_FILENAME,
    ScopedRefinementChange,
    ScopedRefinementChangedFile,
    ScopedRefinementResult,
    apply_scoped_refinement_changes,
)
from mozaiksai.control_plane.staging import (
    RefinementStagedFile,
    RefinementStagingResult,
    create_refinement_staging_workspace,
)
from mozaiksai.control_plane.validation_evidence import ValidationEvidence

REPO_ROOT = Path(__file__).resolve().parents[1]


def _build_plan(
    staging_root: Path,
    *,
    request: str = "Update the dashboard review surface.",
    change_class: str = "design",
    workflow_sequence: str = "app_surface_revision",
    affected_declarative_families: list[str] | None = None,
    affected_paths: list[str] | None = None,
    request_id: str = "req_promote_001",
) -> dry_run.RefinementExecutionPlan:
    families = ["experience_spec"] if affected_declarative_families is None else affected_declarative_families
    paths = ["ui/pages/dashboard.yaml"] if affected_paths is None else affected_paths
    return dry_run.build_refinement_execution_plan_from_route(
        request=request,
        artifact_kind="app_bundle",
        change_class=change_class,
        workflow_id="AppGenerator",
        workflow_sequence=workflow_sequence,
        affected_workflows=["AppGenerator"],
        affected_declarative_families=families,
        affected_bundle_paths=paths,
        scope_summary="Apply a local patch to the current app bundle without widening upstream scope.",
        app_id="sample_app",
        request_id=request_id,
        execution_mode="staged",
        staging_base_path=staging_root,
    )


def _source_bundle(tmp_path: Path) -> Path:
    source = tmp_path / "source_app_bundle"
    (source / "ui/pages").mkdir(parents=True)
    (source / "modules/projects/backend").mkdir(parents=True)
    (source / "config/secrets").mkdir(parents=True)
    (source / "ui/pages/dashboard.yaml").write_text("title: Dashboard\n", encoding="utf-8")
    (source / "ui/pages/reports.yaml").write_text("title: Reports\n", encoding="utf-8")
    (source / "modules/projects/backend/service.py").write_text("VALUE = 'original'\n", encoding="utf-8")
    (source / "config/secrets/provider_credentials.json").write_text('{"token": "original"}\n', encoding="utf-8")
    return source


def _reviewed_workspace(
    tmp_path: Path,
    *,
    request: str = "Update the dashboard review surface.",
    change_class: str = "design",
    workflow_sequence: str = "app_surface_revision",
    affected_declarative_families: list[str] | None = None,
    affected_paths: list[str] | None = None,
    request_id: str = "req_promote_001",
):
    staging_root = tmp_path / ".refinement_staging"
    source = _source_bundle(tmp_path)
    plan = _build_plan(
        staging_root,
        request=request,
        change_class=change_class,
        workflow_sequence=workflow_sequence,
        affected_declarative_families=affected_declarative_families,
        affected_paths=affected_paths,
        request_id=request_id,
    )
    staging_result = create_refinement_staging_workspace(plan, source_bundle_path=source, staging_root=staging_root)
    create_refinement_review_record(staging_result, write_back_mode="local_workspace")
    approve_refinement_staging(staging_result.staging_area, reviewer="operator-1")
    review_record = load_refinement_review_record(staging_result.staging_area)
    return source, plan, staging_result, review_record


def _promotable_workspace(
    tmp_path: Path,
    *,
    affected_paths: list[str] | None = None,
    request_id: str = "req_promote_001",
):
    source, plan, staging_result, _ = _reviewed_workspace(
        tmp_path,
        request="Fix a dashboard label.",
        change_class="patch",
        workflow_sequence="app_revision",
        affected_declarative_families=[],
        affected_paths=affected_paths,
        request_id=request_id,
    )
    review_record = mark_refinement_promotion_ready(staging_result.staging_area, reviewer="operator-1")
    execution_result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[
            ScopedRefinementChange(
                path="ui/pages/dashboard.yaml",
                new_content="title: Updated Dashboard\n",
                reason="Deterministic promotion test update.",
            )
        ],
    )
    validation_evidence = ValidationEvidence(
        completed=["route_component_validation", "ui_theme_primitive_validation"],
        warnings=[],
        artifacts=[],
        checked_at="2026-05-21T00:00:00Z",
        source="test-refinement-promotion",
    )
    return source, plan, staging_result, review_record, execution_result, validation_evidence


def _write_execution_result(
    staging_result: RefinementStagingResult,
    changed_files: list[ScopedRefinementChangedFile],
) -> ScopedRefinementResult:
    staging_area = Path(staging_result.staging_area)
    result = ScopedRefinementResult(
        request_id=staging_result.request_id,
        staging_area=staging_result.staging_area,
        changed_files=changed_files,
        result_path=str(staging_area / EXECUTION_RESULT_FILENAME),
    )
    Path(result.result_path).write_text(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def _mark_staged_copy(
    staging_result: RefinementStagingResult,
    relative_path: str,
    *,
    content: str,
) -> RefinementStagingResult:
    staged_file = Path(staging_result.staging_area) / "workspace" / relative_path
    staged_file.parent.mkdir(parents=True, exist_ok=True)
    staged_file.write_text(content, encoding="utf-8")
    files = [file for file in staging_result.files if file.path != relative_path]
    files.append(
        RefinementStagedFile(
            path=relative_path,
            status="copied",
            reason="Manually staged for promotion test.",
            staged_path=staged_file.as_posix(),
        )
    )
    return staging_result.model_copy(update={"files": files})


def _generated_status() -> str:
    completed = subprocess.run(
        ["git", "status", "--short", "--", "generated", "generated_apps"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _outside_files(tmp_path: Path, *roots: Path) -> set[Path]:
    resolved_roots = [root.resolve() for root in roots]
    files: set[Path] = set()
    for path in tmp_path.rglob("*"):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if not any(resolved.is_relative_to(root) for root in resolved_roots):
            files.add(resolved)
    return files


def test_dry_run_promotion_reports_files_but_does_not_mutate_source(tmp_path: Path) -> None:
    source, plan, staging_result, review_record, execution_result, validation_evidence = _promotable_workspace(tmp_path)
    source_file = source / "ui/pages/dashboard.yaml"
    before = source_file.read_text(encoding="utf-8")

    result = promote_refinement_staging(
        plan,
        staging_result,
        review_record,
        execution_result,
        source_bundle_path=source,
        confirm=False,
        dry_run=True,
        backup=True,
        validation_evidence=validation_evidence,
    )

    assert result.dry_run is True
    assert result.mutation_applied is False
    assert result.review_status_before == "promotion_ready"
    assert result.review_status_after == "promotion_ready"
    assert result.promoted_files[0].status == "promoted"
    assert result.policy_decisions[0].allowed is True
    assert result.policy_decisions[0].mode == "direct_leaf_patch"
    assert result.policy_decisions[0].required_validation == ["route_component_validation", "ui_theme_primitive_validation"]
    assert source_file.read_text(encoding="utf-8") == before
    assert result.promoted_files[0].backup_path is not None
    assert not Path(result.promoted_files[0].backup_path).exists()


def test_confirm_false_prevents_mutation(tmp_path: Path) -> None:
    source, plan, staging_result, review_record, execution_result, validation_evidence = _promotable_workspace(tmp_path)
    source_file = source / "ui/pages/dashboard.yaml"
    before = source_file.read_text(encoding="utf-8")

    with pytest.raises(RefinementPromotionError, match="confirm=true"):
        promote_refinement_staging(
            plan,
            staging_result,
            review_record,
            execution_result,
            source_bundle_path=source,
            confirm=False,
            dry_run=False,
            backup=True,
            validation_evidence=validation_evidence,
        )

    assert source_file.read_text(encoding="utf-8") == before
    assert load_refinement_review_record(staging_result.staging_area).status == "promotion_ready"


def test_generated_artifact_review_blocks_direct_source_mutation(tmp_path: Path) -> None:
    staging_root = tmp_path / ".refinement_staging"
    source = _source_bundle(tmp_path)
    plan = _build_plan(
        staging_root,
        request="Fix a dashboard label.",
        change_class="patch",
        workflow_sequence="app_revision",
        affected_declarative_families=[],
        affected_paths=["ui/pages/dashboard.yaml"],
        request_id="req_generated_writeback",
    )
    staging_result = create_refinement_staging_workspace(plan, source_bundle_path=source, staging_root=staging_root)
    create_refinement_review_record(staging_result)
    approve_refinement_staging(staging_result.staging_area, reviewer="operator-1")
    review_record = mark_refinement_promotion_ready(staging_result.staging_area, reviewer="operator-1")
    execution_result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[
            ScopedRefinementChange(
                path="ui/pages/dashboard.yaml",
                new_content="title: Updated Dashboard\n",
                reason="Generated-artifact review must not write source directly.",
            )
        ],
    )

    with pytest.raises(RefinementPromotionError, match="write_back_mode='local_workspace'"):
        promote_refinement_staging(
            plan,
            staging_result,
            review_record,
            execution_result,
            source_bundle_path=source,
            confirm=True,
            dry_run=False,
            backup=True,
        )


def test_review_status_approved_is_not_enough(tmp_path: Path) -> None:
    source, plan, staging_result, review_record = _reviewed_workspace(tmp_path)
    execution_result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[
            ScopedRefinementChange(
                path="ui/pages/dashboard.yaml",
                new_content="title: Updated Dashboard\n",
                reason="Deterministic promotion test update.",
            )
        ],
    )

    with pytest.raises(RefinementPromotionError, match="promotion_ready"):
        promote_refinement_staging(
            plan,
            staging_result,
            review_record,
            execution_result,
            source_bundle_path=source,
            confirm=True,
            dry_run=True,
            backup=True,
        )


def test_experience_design_blocks_apply_without_experience_spec(tmp_path: Path) -> None:
    source, plan, staging_result, review_record = _reviewed_workspace(tmp_path)
    review_record = mark_refinement_promotion_ready(staging_result.staging_area, reviewer="operator-1")
    execution_result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[
            ScopedRefinementChange(
                path="ui/pages/dashboard.yaml",
                new_content="title: Updated Dashboard\n",
                reason="Experience design test update.",
            )
        ],
    )
    source_file = source / "ui/pages/dashboard.yaml"
    before = source_file.read_text(encoding="utf-8")

    result = promote_refinement_staging(
        plan,
        staging_result,
        review_record,
        execution_result,
        source_bundle_path=source,
        confirm=True,
        dry_run=False,
        backup=True,
    )

    assert result.promoted_files[0].status == "blocked"
    assert result.policy_decisions[0].allowed is False
    assert result.policy_decisions[0].mode == "blocked_requires_upstream_artifact"
    assert "experience_spec_update" in result.policy_decisions[0].required_validation
    assert source_file.read_text(encoding="utf-8") == before


def test_promotion_ready_confirm_true_dry_run_false_promotes_updated_staged_file(tmp_path: Path) -> None:
    source, plan, staging_result, review_record, execution_result, validation_evidence = _promotable_workspace(tmp_path)
    source_file = source / "ui/pages/dashboard.yaml"

    result = promote_refinement_staging(
        plan,
        staging_result,
        review_record,
        execution_result,
        source_bundle_path=source,
        confirm=True,
        dry_run=False,
        backup=True,
        validation_evidence=validation_evidence,
    )

    assert result.dry_run is False
    assert result.mutation_applied is True
    assert result.promoted_files[0].status == "promoted"
    assert result.policy_decisions[0].allowed is True
    assert source_file.read_text(encoding="utf-8") == "title: Updated Dashboard\n"


def test_promotion_blocks_without_required_validation_evidence(tmp_path: Path) -> None:
    source, plan, staging_result, review_record, execution_result, _ = _promotable_workspace(tmp_path)
    source_file = source / "ui/pages/dashboard.yaml"
    before = source_file.read_text(encoding="utf-8")

    result = promote_refinement_staging(
        plan,
        staging_result,
        review_record,
        execution_result,
        source_bundle_path=source,
        confirm=True,
        dry_run=False,
        backup=True,
        validation_evidence=ValidationEvidence(),
    )

    assert result.promoted_files[0].status == "blocked"
    assert result.policy_decisions[0].allowed is False
    assert result.policy_decisions[0].mode == "blocked_requires_validation"
    assert result.policy_decisions[0].required_validation == ["route_component_validation", "ui_theme_primitive_validation"]
    assert source_file.read_text(encoding="utf-8") == before


def test_successful_promotion_updates_review_status_to_promoted(tmp_path: Path) -> None:
    source, plan, staging_result, review_record, execution_result, validation_evidence = _promotable_workspace(tmp_path)

    promote_refinement_staging(
        plan,
        staging_result,
        review_record,
        execution_result,
        source_bundle_path=source,
        confirm=True,
        dry_run=False,
        backup=True,
        validation_evidence=validation_evidence,
    )

    record = load_refinement_review_record(staging_result.staging_area)
    assert record.status == "promoted"
    assert record.promotion_allowed is False


def test_request_id_mismatch_blocks_promotion(tmp_path: Path) -> None:
    source, plan, staging_result, review_record, execution_result, validation_evidence = _promotable_workspace(tmp_path)
    mismatched_plan = plan.model_copy(update={"request_id": "req_promote_other"})

    with pytest.raises(RefinementPromotionError, match="request_id"):
        promote_refinement_staging(
            mismatched_plan,
            staging_result,
            review_record,
            execution_result,
            source_bundle_path=source,
            confirm=True,
            dry_run=False,
            backup=True,
            validation_evidence=validation_evidence,
        )


def test_missing_execution_result_blocks_promotion(tmp_path: Path) -> None:
    source, plan, staging_result, review_record, execution_result, validation_evidence = _promotable_workspace(tmp_path)
    Path(execution_result.result_path).unlink()

    with pytest.raises(RefinementPromotionError, match="Execution result not found"):
        promote_refinement_staging(
            plan,
            staging_result,
            review_record,
            execution_result,
            source_bundle_path=source,
            confirm=True,
            dry_run=False,
            backup=True,
            validation_evidence=validation_evidence,
        )


def test_file_not_in_affected_bundle_paths_is_blocked_by_policy(tmp_path: Path) -> None:
    source, plan, staging_result, review_record, _, validation_evidence = _promotable_workspace(tmp_path)
    staging_result = _mark_staged_copy(
        staging_result,
        "modules/projects/backend/service.py",
        content="VALUE = 'updated'\n",
    )
    execution_result = _write_execution_result(
        staging_result,
        [
            ScopedRefinementChangedFile(
                path="modules/projects/backend/service.py",
                status="updated",
                reason="Out-of-scope test update.",
            )
        ],
    )

    result = promote_refinement_staging(
        plan,
        staging_result,
        review_record,
        execution_result,
        source_bundle_path=source,
        confirm=True,
        dry_run=True,
        backup=True,
        validation_evidence=validation_evidence,
    )

    assert result.promoted_files[0].status == "blocked"
    assert result.policy_decisions[0].allowed is False
    assert result.policy_decisions[0].mode == "blocked_requires_replan"
    assert "affected_bundle_paths" in result.promoted_files[0].reason
    assert (source / "modules/projects/backend/service.py").read_text(encoding="utf-8") == "VALUE = 'original'\n"


def test_secret_path_is_skipped(tmp_path: Path) -> None:
    source, plan, staging_result, review_record = _reviewed_workspace(
        tmp_path,
        affected_paths=["config/secrets/provider_credentials.json"],
    )
    Path(staging_result.staging_area, "workspace").mkdir(parents=True, exist_ok=True)
    review_record = mark_refinement_promotion_ready(staging_result.staging_area, reviewer="operator-1")
    execution_result = _write_execution_result(
        staging_result,
        [
            ScopedRefinementChangedFile(
                path="config/secrets/provider_credentials.json",
                status="updated",
                reason="Secret test update.",
            )
        ],
    )

    result = promote_refinement_staging(
        plan,
        staging_result,
        review_record,
        execution_result,
        source_bundle_path=source,
        confirm=True,
        dry_run=True,
        backup=True,
    )

    assert result.promoted_files[0].status == "skipped"
    assert "Secret-sensitive" in result.promoted_files[0].reason


def test_traversal_path_is_skipped(tmp_path: Path) -> None:
    source, plan, staging_result, review_record, _, validation_evidence = _promotable_workspace(tmp_path)
    execution_result = _write_execution_result(
        staging_result,
        [ScopedRefinementChangedFile(path="../outside.txt", status="updated", reason="Traversal test update.")],
    )

    result = promote_refinement_staging(
        plan,
        staging_result,
        review_record,
        execution_result,
        source_bundle_path=source,
        confirm=True,
        dry_run=True,
        backup=True,
        validation_evidence=validation_evidence,
    )

    assert result.promoted_files[0].status == "skipped"
    assert "Path traversal" in result.promoted_files[0].reason


def test_absolute_path_is_skipped(tmp_path: Path) -> None:
    source, plan, staging_result, review_record, _, validation_evidence = _promotable_workspace(tmp_path)
    execution_result = _write_execution_result(
        staging_result,
        [
            ScopedRefinementChangedFile(
                path=str(tmp_path / "outside.txt"),
                status="updated",
                reason="Absolute path test update.",
            )
        ],
    )

    result = promote_refinement_staging(
        plan,
        staging_result,
        review_record,
        execution_result,
        source_bundle_path=source,
        confirm=True,
        dry_run=True,
        backup=True,
        validation_evidence=validation_evidence,
    )

    assert result.promoted_files[0].status == "skipped"
    assert "Absolute" in result.promoted_files[0].reason


def test_symlink_source_is_skipped_when_supported(tmp_path: Path) -> None:
    source, plan, staging_result, review_record, execution_result, validation_evidence = _promotable_workspace(tmp_path)
    source_file = source / "ui/pages/dashboard.yaml"
    target = tmp_path / "outside-source.txt"
    target.write_text("outside\n", encoding="utf-8")
    source_file.unlink()
    try:
        source_file.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"Symlink creation is not available in this environment: {exc}")

    result = promote_refinement_staging(
        plan,
        staging_result,
        review_record,
        execution_result,
        source_bundle_path=source,
        confirm=True,
        dry_run=True,
        backup=True,
        validation_evidence=validation_evidence,
    )

    assert result.promoted_files[0].status == "skipped"
    assert target.read_text(encoding="utf-8") == "outside\n"


def test_symlink_staged_file_is_skipped_when_supported(tmp_path: Path) -> None:
    source, plan, staging_result, review_record, execution_result, validation_evidence = _promotable_workspace(tmp_path)
    staged_file = Path(staging_result.staging_area) / "workspace/ui/pages/dashboard.yaml"
    target = tmp_path / "outside-staged.txt"
    target.write_text("outside\n", encoding="utf-8")
    staged_file.unlink()
    try:
        staged_file.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"Symlink creation is not available in this environment: {exc}")

    result = promote_refinement_staging(
        plan,
        staging_result,
        review_record,
        execution_result,
        source_bundle_path=source,
        confirm=True,
        dry_run=True,
        backup=True,
        validation_evidence=validation_evidence,
    )

    assert result.promoted_files[0].status == "skipped"
    assert target.read_text(encoding="utf-8") == "outside\n"


def test_backup_is_written_under_staging_area(tmp_path: Path) -> None:
    source, plan, staging_result, review_record, execution_result, validation_evidence = _promotable_workspace(tmp_path)

    result = promote_refinement_staging(
        plan,
        staging_result,
        review_record,
        execution_result,
        source_bundle_path=source,
        confirm=True,
        dry_run=False,
        backup=True,
        validation_evidence=validation_evidence,
    )

    backup_path = Path(result.promoted_files[0].backup_path)
    assert backup_path.exists()
    assert backup_path.resolve().is_relative_to(Path(staging_result.staging_area).resolve())


def test_source_backup_content_equals_original(tmp_path: Path) -> None:
    source, plan, staging_result, review_record, execution_result, validation_evidence = _promotable_workspace(tmp_path)
    original = (source / "ui/pages/dashboard.yaml").read_text(encoding="utf-8")

    result = promote_refinement_staging(
        plan,
        staging_result,
        review_record,
        execution_result,
        source_bundle_path=source,
        confirm=True,
        dry_run=False,
        backup=True,
        validation_evidence=validation_evidence,
    )

    backup_path = Path(result.promoted_files[0].backup_path)
    assert backup_path.read_text(encoding="utf-8") == original


def test_no_writes_outside_source_bundle(tmp_path: Path) -> None:
    source, plan, staging_result, review_record, execution_result, validation_evidence = _promotable_workspace(tmp_path)
    outside_source = tmp_path / "outside_source.txt"
    outside_source.write_text("original-outside-source\n", encoding="utf-8")
    before = outside_source.read_text(encoding="utf-8")

    promote_refinement_staging(
        plan,
        staging_result,
        review_record,
        execution_result,
        source_bundle_path=source,
        confirm=True,
        dry_run=False,
        backup=True,
        validation_evidence=validation_evidence,
    )

    assert outside_source.read_text(encoding="utf-8") == before


def test_no_writes_outside_staging_area(tmp_path: Path) -> None:
    source, plan, staging_result, review_record, execution_result, validation_evidence = _promotable_workspace(tmp_path)
    outside_staging = tmp_path / "outside_staging.txt"
    outside_staging.write_text("original-outside-staging\n", encoding="utf-8")
    before = outside_staging.read_text(encoding="utf-8")

    promote_refinement_staging(
        plan,
        staging_result,
        review_record,
        execution_result,
        source_bundle_path=source,
        confirm=True,
        dry_run=False,
        backup=True,
        validation_evidence=validation_evidence,
    )

    assert outside_staging.read_text(encoding="utf-8") == before


def test_new_files_are_skipped_unless_explicit_support_is_added(tmp_path: Path) -> None:
    source, plan, staging_result, review_record = _reviewed_workspace(
        tmp_path,
        affected_paths=["ui/pages/new_page.yaml"],
    )
    staging_result = _mark_staged_copy(
        staging_result,
        "ui/pages/new_page.yaml",
        content="title: New Page\n",
    )
    review_record = mark_refinement_promotion_ready(staging_result.staging_area, reviewer="operator-1")
    execution_result = _write_execution_result(
        staging_result,
        [
            ScopedRefinementChangedFile(
                path="ui/pages/new_page.yaml",
                status="updated",
                reason="New file test update.",
            )
        ],
    )

    result = promote_refinement_staging(
        plan,
        staging_result,
        review_record,
        execution_result,
        source_bundle_path=source,
        confirm=True,
        dry_run=True,
        backup=True,
    )

    assert result.promoted_files[0].status == "skipped"
    assert "Source file does not exist" in result.promoted_files[0].reason


def test_promotion_does_not_write_generated_app_files(tmp_path: Path) -> None:
    before = _generated_status()
    source, plan, staging_result, review_record, execution_result, validation_evidence = _promotable_workspace(tmp_path)

    promote_refinement_staging(
        plan,
        staging_result,
        review_record,
        execution_result,
        source_bundle_path=source,
        confirm=True,
        dry_run=False,
        backup=True,
        validation_evidence=validation_evidence,
    )
    after = _generated_status()

    assert after == before


def test_promotion_helpers_do_not_call_llm() -> None:
    source = (REPO_ROOT / "mozaiksai/control_plane/promotion.py").read_text(encoding="utf-8")

    assert "LLMChangeClassifier" not in source
    assert "OpenAI" not in source
    assert "chat.completions" not in source


def test_promotion_helpers_do_not_execute_workflows() -> None:
    source = (REPO_ROOT / "mozaiksai/control_plane/promotion.py").read_text(encoding="utf-8")

    assert "RefinementTriggerRouteResolver" not in source
    assert "load_refinement_harness" not in source
    assert "execute_workflow" not in source


def test_promotion_fixtures_are_neutral() -> None:
    combined = "\n".join(
        [
            (REPO_ROOT / "mozaiksai/control_plane/promotion.py").read_text(encoding="utf-8"),
            (REPO_ROOT / "tests/test_refinement_promotion.py").read_text(encoding="utf-8"),
        ]
    ).lower()
    forbidden_terms = [
        " ".join(("app", "zero")),
        "app_" + "zero",
        "mozaiks-" + "app",
        "bloc" + "united",
    ]

    assert not any(term in combined for term in forbidden_terms)


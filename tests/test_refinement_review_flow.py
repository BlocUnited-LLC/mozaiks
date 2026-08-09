from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mozaiksai.control_plane import dry_run
from mozaiksai.control_plane.review import (
    REVIEW_FILENAME,
    RefinementReviewTransitionError,
    approve_refinement_staging,
    create_refinement_review_record,
    mark_refinement_promoted,
    mark_refinement_promotion_ready,
    mark_refinement_review_pending,
    reject_refinement_staging,
)
from mozaiksai.control_plane.staging import create_refinement_staging_workspace

REPO_ROOT = Path(__file__).resolve().parents[1]


def _build_plan(staging_root: Path, *, request_id: str = "req_review_001") -> dry_run.RefinementExecutionPlan:
    return dry_run.build_refinement_execution_plan_from_route(
        request="Update the dashboard review surface.",
        build_family="app_bundle",
        change_class="patch",
        workflow_id="AppGenerator",
        workflow_sequence="app_revision",
        affected_workflows=["AppGenerator"],
        affected_declarative_families=["app_bundle"],
        affected_bundle_paths=["ui/pages/dashboard.yaml"],
        scope_summary="Apply a local patch to the current app bundle without widening upstream scope.",
        app_id="sample_app",
        request_id=request_id,
        execution_mode="staged",
        staging_base_path=staging_root,
    )


def _source_bundle(tmp_path: Path) -> Path:
    source = tmp_path / "source_app_bundle"
    (source / "ui/pages").mkdir(parents=True)
    (source / "ui/pages/dashboard.yaml").write_text("title: Dashboard\n", encoding="utf-8")
    return source


def _stage(tmp_path: Path, *, request_id: str = "req_review_001"):
    staging_root = tmp_path / ".refinement_staging"
    source = _source_bundle(tmp_path)
    plan = _build_plan(staging_root, request_id=request_id)
    result = create_refinement_staging_workspace(plan, source_bundle_path=source, staging_root=staging_root)
    return source, result


def _generated_status() -> str:
    completed = subprocess.run(
        ["git", "status", "--short", "--", "generated", "generated_apps"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _outside_staging_files(tmp_path: Path, staging_area: Path) -> set[Path]:
    staging_resolved = staging_area.resolve()
    return {
        path.resolve()
        for path in tmp_path.rglob("*")
        if path.is_file() and not path.resolve().is_relative_to(staging_resolved)
    }


def test_create_review_record_from_staging_result(tmp_path: Path) -> None:
    _, staging_result = _stage(tmp_path)

    record = create_refinement_review_record(staging_result)

    assert record.request_id == staging_result.request_id
    assert record.staging_area == staging_result.staging_area
    assert record.affected_bundle_paths == ["ui/pages/dashboard.yaml"]
    assert record.write_back_mode == "generated_artifact"
    assert record.write_back_target is None
    assert Path(staging_result.staging_area, REVIEW_FILENAME).exists()


def test_initial_review_status_is_review_pending(tmp_path: Path) -> None:
    _, staging_result = _stage(tmp_path)

    record = create_refinement_review_record(staging_result)

    assert record.status == "review_pending"
    assert record.decision is None
    assert record.promotion_allowed is False


def test_review_record_can_declare_external_write_back_target(tmp_path: Path) -> None:
    _, staging_result = _stage(tmp_path)

    record = create_refinement_review_record(
        staging_result,
        write_back_mode="external_patch",
        write_back_target="repo:customer/main",
    )
    payload = json.loads(Path(staging_result.staging_area, REVIEW_FILENAME).read_text(encoding="utf-8"))

    assert record.write_back_mode == "external_patch"
    assert record.write_back_target == "repo:customer/main"
    assert payload["write_back_mode"] == "external_patch"
    assert payload["write_back_target"] == "repo:customer/main"


def test_approve_moves_review_pending_to_approved(tmp_path: Path) -> None:
    _, staging_result = _stage(tmp_path)
    create_refinement_review_record(staging_result)

    record = approve_refinement_staging(staging_result.staging_area, reviewer="operator-1")

    assert record.status == "approved"
    assert record.decision == "approved"
    assert record.reviewer == "operator-1"
    assert record.promotion_allowed is False
    assert record.reviewed_at is not None


def test_staged_record_can_move_to_review_pending(tmp_path: Path) -> None:
    _, staging_result = _stage(tmp_path)
    create_refinement_review_record(staging_result, status="staged")

    record = mark_refinement_review_pending(staging_result.staging_area, reviewer="operator-1")

    assert record.status == "review_pending"
    assert record.decision == "review_pending"
    assert record.reviewer == "operator-1"


def test_reject_moves_review_pending_to_rejected(tmp_path: Path) -> None:
    _, staging_result = _stage(tmp_path)
    create_refinement_review_record(staging_result)

    record = reject_refinement_staging(staging_result.staging_area, reviewer="operator-1")

    assert record.status == "rejected"
    assert record.decision == "rejected"
    assert record.promotion_allowed is False


def test_promotion_ready_requires_approved(tmp_path: Path) -> None:
    _, staging_result = _stage(tmp_path)
    create_refinement_review_record(staging_result)

    with pytest.raises(RefinementReviewTransitionError, match="review_pending.*promotion_ready"):
        mark_refinement_promotion_ready(staging_result.staging_area)

    approve_refinement_staging(staging_result.staging_area, reviewer="operator-1")
    record = mark_refinement_promotion_ready(staging_result.staging_area, reviewer="operator-1")

    assert record.status == "promotion_ready"
    assert record.promotion_allowed is True


def test_rejected_cannot_become_promotion_ready(tmp_path: Path) -> None:
    _, staging_result = _stage(tmp_path)
    create_refinement_review_record(staging_result)
    reject_refinement_staging(staging_result.staging_area, reviewer="operator-1")

    with pytest.raises(RefinementReviewTransitionError, match="rejected.*promotion_ready"):
        mark_refinement_promotion_ready(staging_result.staging_area)


def test_promoted_requires_promotion_ready_and_promotion_allowed(tmp_path: Path) -> None:
    _, staging_result = _stage(tmp_path)
    create_refinement_review_record(staging_result)
    approve_refinement_staging(staging_result.staging_area, reviewer="operator-1")
    mark_refinement_promotion_ready(staging_result.staging_area, reviewer="operator-1")
    review_path = Path(staging_result.staging_area, REVIEW_FILENAME)
    payload = json.loads(review_path.read_text(encoding="utf-8"))
    payload["promotion_allowed"] = False
    review_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(RefinementReviewTransitionError, match="promotion_allowed"):
        mark_refinement_promoted(staging_result.staging_area)

    payload["promotion_allowed"] = True
    review_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    record = mark_refinement_promoted(staging_result.staging_area, reviewer="operator-1")

    assert record.status == "promoted"
    assert record.promotion_allowed is False


def test_approval_does_not_copy_files_to_source_bundle(tmp_path: Path) -> None:
    source, staging_result = _stage(tmp_path)
    source_file = source / "ui/pages/dashboard.yaml"
    before_files = {path.resolve() for path in source.rglob("*") if path.is_file()}

    create_refinement_review_record(staging_result)
    approve_refinement_staging(staging_result.staging_area, reviewer="operator-1")
    after_files = {path.resolve() for path in source.rglob("*") if path.is_file()}

    assert after_files == before_files
    assert source_file.read_text(encoding="utf-8") == "title: Dashboard\n"


def test_source_files_remain_unchanged_after_review_transitions(tmp_path: Path) -> None:
    source, staging_result = _stage(tmp_path)
    source_file = source / "ui/pages/dashboard.yaml"
    before = source_file.read_text(encoding="utf-8")

    create_refinement_review_record(staging_result)
    approve_refinement_staging(staging_result.staging_area, reviewer="operator-1")
    mark_refinement_promotion_ready(staging_result.staging_area, reviewer="operator-1")

    assert source_file.read_text(encoding="utf-8") == before


def test_review_mutation_allowed_remains_false(tmp_path: Path) -> None:
    _, staging_result = _stage(tmp_path)
    create_refinement_review_record(staging_result)

    record = approve_refinement_staging(staging_result.staging_area, reviewer="operator-1")
    payload = json.loads(Path(staging_result.staging_area, REVIEW_FILENAME).read_text(encoding="utf-8"))

    assert record.mutation_allowed is False
    assert payload["mutation_allowed"] is False


def test_reviewer_and_notes_are_recorded(tmp_path: Path) -> None:
    _, staging_result = _stage(tmp_path)
    create_refinement_review_record(staging_result)

    record = approve_refinement_staging(
        staging_result.staging_area,
        reviewer="operator-1",
        notes="Reviewed route and module scope.",
    )

    assert record.reviewer == "operator-1"
    assert record.notes == "Reviewed route and module scope."


def test_secret_looking_notes_are_redacted(tmp_path: Path) -> None:
    _, staging_result = _stage(tmp_path)
    create_refinement_review_record(staging_result)

    record = approve_refinement_staging(
        staging_result.staging_area,
        reviewer="operator-1",
        notes="Looks fine; api_key=plain-value should not persist.",
    )

    assert "plain-value" not in (record.notes or "")
    assert "api_key=<redacted>" in (record.notes or "")


def test_invalid_transition_raises_clear_error(tmp_path: Path) -> None:
    _, staging_result = _stage(tmp_path)
    create_refinement_review_record(staging_result)
    approve_refinement_staging(staging_result.staging_area, reviewer="operator-1")

    with pytest.raises(RefinementReviewTransitionError, match="Cannot transition.*approved.*rejected"):
        reject_refinement_staging(staging_result.staging_area, reviewer="operator-1")


def test_refinement_review_json_is_written_inside_staging_area_only(tmp_path: Path) -> None:
    _, staging_result = _stage(tmp_path)

    create_refinement_review_record(staging_result)
    review_path = Path(staging_result.staging_area, REVIEW_FILENAME).resolve()

    assert review_path.exists()
    assert review_path.is_relative_to(Path(staging_result.staging_area).resolve())


def test_review_writes_do_not_escape_staging_area(tmp_path: Path) -> None:
    _, staging_result = _stage(tmp_path)
    staging_area = Path(staging_result.staging_area)
    before = _outside_staging_files(tmp_path, staging_area)

    create_refinement_review_record(staging_result)
    approve_refinement_staging(staging_result.staging_area, reviewer="operator-1")
    mark_refinement_promotion_ready(staging_result.staging_area, reviewer="operator-1")
    after = _outside_staging_files(tmp_path, staging_area)

    assert after == before


def test_review_helpers_do_not_execute_workflows() -> None:
    source = (REPO_ROOT / "mozaiksai/control_plane/review.py").read_text(encoding="utf-8")

    assert "RefinementTriggerRouteResolver" not in source
    assert "load_refinement_harness" not in source
    assert "workflow_sequence" not in source


def test_review_helpers_do_not_call_llm() -> None:
    source = (REPO_ROOT / "mozaiksai/control_plane/review.py").read_text(encoding="utf-8")

    assert "LLMChangeClassifier" not in source
    assert "OpenAI" not in source
    assert "chat.completions" not in source


def test_review_flow_does_not_write_generated_app_files(tmp_path: Path) -> None:
    _, staging_result = _stage(tmp_path)
    before = _generated_status()

    create_refinement_review_record(staging_result)
    approve_refinement_staging(staging_result.staging_area, reviewer="operator-1")
    after = _generated_status()

    assert after == before


def test_review_fixtures_are_neutral() -> None:
    combined = "\n".join(
        [
            (REPO_ROOT / "mozaiksai/control_plane/review.py").read_text(encoding="utf-8"),
            (REPO_ROOT / "tests/test_refinement_review_flow.py").read_text(encoding="utf-8"),
        ]
    ).lower()
    forbidden_terms = [
        " ".join(("app", "zero")),
        "app_" + "zero",
        "mozaiks-" + "app",
        "bloc" + "united",
    ]

    assert not any(term in combined for term in forbidden_terms)


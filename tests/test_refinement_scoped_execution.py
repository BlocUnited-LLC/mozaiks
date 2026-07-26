from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mozaiksai.control_plane import dry_run
from mozaiksai.control_plane.scoped_execution import (
    EXECUTION_RESULT_FILENAME,
    ScopedRefinementChange,
    apply_scoped_refinement_changes,
)
from mozaiksai.control_plane.staging import create_refinement_staging_workspace

REPO_ROOT = Path(__file__).resolve().parents[1]


def _build_plan(
    affected_paths: list[str],
    staging_root: Path,
    *,
    request_id: str = "req_scoped_001",
) -> dry_run.RefinementExecutionPlan:
    return dry_run.build_refinement_execution_plan_from_route(
        request="Update the dashboard review surface.",
        artifact_kind="app_bundle",
        change_class="patch",
        workflow_id="AppGenerator",
        workflow_sequence="app_revision",
        affected_workflows=["AppGenerator"],
        affected_declarative_families=["app_bundle"],
        affected_bundle_paths=affected_paths,
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
    (source / "ui/pages/dashboard.yaml").write_text("title: Dashboard\n", encoding="utf-8")
    (source / "ui/pages/reports.yaml").write_text("title: Reports\n", encoding="utf-8")
    (source / "modules/projects/backend/service.py").write_text("VALUE = 'original'\n", encoding="utf-8")
    return source


def _stage(
    tmp_path: Path,
    affected_paths: list[str] | None = None,
    *,
    request_id: str = "req_scoped_001",
):
    staging_root = tmp_path / ".refinement_staging"
    source = _source_bundle(tmp_path)
    plan = _build_plan(affected_paths or ["ui/pages/dashboard.yaml"], staging_root, request_id=request_id)
    staging_result = create_refinement_staging_workspace(plan, source_bundle_path=source, staging_root=staging_root)
    return source, plan, staging_result


def _generated_status() -> str:
    completed = subprocess.run(
        ["git", "status", "--short", "--", "generated", "generated_apps"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _statuses(result) -> dict[str, str]:  # noqa: ANN001
    return {file.path: file.status for file in result.changed_files}


def test_scoped_execution_updates_copied_staged_file(tmp_path: Path) -> None:
    _, plan, staging_result = _stage(tmp_path)

    result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[
            ScopedRefinementChange(
                path="ui/pages/dashboard.yaml",
                new_content="title: Updated Dashboard\n",
                reason="Deterministic test update.",
            )
        ],
    )
    staged_file = Path(staging_result.staging_area) / "workspace/ui/pages/dashboard.yaml"

    assert staged_file.read_text(encoding="utf-8") == "title: Updated Dashboard\n"
    assert _statuses(result)["ui/pages/dashboard.yaml"] == "updated"


def test_source_file_remains_unchanged_after_scoped_execution(tmp_path: Path) -> None:
    source, plan, staging_result = _stage(tmp_path)
    source_file = source / "ui/pages/dashboard.yaml"
    before = source_file.read_text(encoding="utf-8")

    apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[{"path": "ui/pages/dashboard.yaml", "new_content": "title: Updated Dashboard\n", "reason": "test"}],
    )

    assert source_file.read_text(encoding="utf-8") == before


def test_execution_result_json_is_written_inside_staging(tmp_path: Path) -> None:
    _, plan, staging_result = _stage(tmp_path)

    result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[{"path": "ui/pages/dashboard.yaml", "new_content": "title: Updated Dashboard\n", "reason": "test"}],
    )
    payload = json.loads(Path(result.result_path).read_text(encoding="utf-8"))

    assert Path(result.result_path).name == EXECUTION_RESULT_FILENAME
    assert Path(result.result_path).resolve().is_relative_to(Path(staging_result.staging_area).resolve())
    assert payload["execution_mode"] == "scoped_staging"
    assert payload["source_mutated"] is False


def test_path_not_in_affected_bundle_paths_is_skipped(tmp_path: Path) -> None:
    _, plan, staging_result = _stage(tmp_path, ["ui/pages/dashboard.yaml", "ui/pages/reports.yaml"])

    result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[{"path": "modules/projects/backend/service.py", "new_content": "VALUE = 'updated'\n", "reason": "test"}],
    )

    assert _statuses(result)["modules/projects/backend/service.py"] == "skipped_unsafe"


def test_missing_staged_file_is_skipped(tmp_path: Path) -> None:
    _, plan, staging_result = _stage(tmp_path, ["ui/pages/missing.yaml"])

    result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[{"path": "ui/pages/missing.yaml", "new_content": "title: Missing\n", "reason": "test"}],
    )

    assert _statuses(result)["ui/pages/missing.yaml"] == "skipped_missing"


def test_existing_uncopied_workspace_file_is_skipped(tmp_path: Path) -> None:
    _, plan, staging_result = _stage(tmp_path, ["ui/pages/missing.yaml"])
    uncopied = Path(staging_result.staging_area) / "workspace/ui/pages/missing.yaml"
    uncopied.parent.mkdir(parents=True, exist_ok=True)
    uncopied.write_text("title: Manual\n", encoding="utf-8")

    result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[{"path": "ui/pages/missing.yaml", "new_content": "title: Updated\n", "reason": "test"}],
    )

    assert _statuses(result)["ui/pages/missing.yaml"] == "skipped_missing"
    assert uncopied.read_text(encoding="utf-8") == "title: Manual\n"


def test_secret_path_is_skipped(tmp_path: Path) -> None:
    _, plan, staging_result = _stage(tmp_path, ["config/secrets/provider_credentials.json"])

    result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[
            {
                "path": "config/secrets/provider_credentials.json",
                "new_content": "{}\n",
                "reason": "test",
            }
        ],
    )

    assert _statuses(result)["config/secrets/provider_credentials.json"] == "skipped_secret"


def test_absolute_path_is_skipped(tmp_path: Path) -> None:
    _, plan, staging_result = _stage(tmp_path)

    result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[{"path": str(tmp_path / "outside.txt"), "new_content": "bad\n", "reason": "test"}],
    )

    assert result.changed_files[0].status == "skipped_unsafe"


def test_path_traversal_is_skipped(tmp_path: Path) -> None:
    _, plan, staging_result = _stage(tmp_path)

    result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[{"path": "../outside.txt", "new_content": "bad\n", "reason": "test"}],
    )

    assert result.changed_files[0].status == "skipped_unsafe"


def test_symlink_path_is_skipped_when_supported(tmp_path: Path) -> None:
    _, plan, staging_result = _stage(tmp_path)
    staged_file = Path(staging_result.staging_area) / "workspace/ui/pages/dashboard.yaml"
    staged_file.unlink()
    target = tmp_path / "outside-target.txt"
    target.write_text("outside\n", encoding="utf-8")
    try:
        staged_file.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"Symlink creation is not available in this environment: {exc}")

    result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[{"path": "ui/pages/dashboard.yaml", "new_content": "bad\n", "reason": "test"}],
    )

    assert _statuses(result)["ui/pages/dashboard.yaml"] == "skipped_unsafe"
    assert target.read_text(encoding="utf-8") == "outside\n"


def test_scoped_execution_writes_never_escape_staging_root(tmp_path: Path) -> None:
    source, plan, staging_result = _stage(tmp_path)
    staging_root = (tmp_path / ".refinement_staging").resolve()
    source_before = {path: path.read_text(encoding="utf-8") for path in source.rglob("*") if path.is_file()}

    result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[{"path": "ui/pages/dashboard.yaml", "new_content": "title: Updated Dashboard\n", "reason": "test"}],
    )

    assert Path(result.result_path).resolve().is_relative_to(staging_root)
    for path in source.rglob("*"):
        if path.is_file():
            assert path.read_text(encoding="utf-8") == source_before[path]


def test_mutation_allowed_remains_false(tmp_path: Path) -> None:
    _, plan, staging_result = _stage(tmp_path)

    result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[{"path": "ui/pages/dashboard.yaml", "new_content": "title: Updated Dashboard\n", "reason": "test"}],
    )
    plan_payload = json.loads(Path(staging_result.plan_path).read_text(encoding="utf-8"))

    assert plan.mutation_allowed is False
    assert staging_result.mutation_allowed is False
    assert result.mutation_allowed is False
    assert plan_payload["mutation_allowed"] is False


def test_source_mutated_false(tmp_path: Path) -> None:
    _, plan, staging_result = _stage(tmp_path)

    result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[{"path": "ui/pages/dashboard.yaml", "new_content": "title: Updated Dashboard\n", "reason": "test"}],
    )

    assert result.source_mutated is False
    assert result.mutation_scope == "staging_only"


def test_fake_executor_does_not_call_llm() -> None:
    source = (REPO_ROOT / "mozaiksai/control_plane/scoped_execution.py").read_text(encoding="utf-8")

    assert "LLMChangeClassifier" not in source
    assert "OpenAI" not in source
    assert "chat.completions" not in source


def test_fake_executor_does_not_execute_workflows() -> None:
    source = (REPO_ROOT / "mozaiksai/control_plane/scoped_execution.py").read_text(encoding="utf-8")

    assert "RefinementTriggerRouteResolver" not in source
    assert "load_refinement_harness" not in source
    assert "execute_workflow" not in source


def test_scoped_execution_does_not_write_generated_app_files(tmp_path: Path) -> None:
    _, plan, staging_result = _stage(tmp_path)
    before = _generated_status()

    apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[{"path": "ui/pages/dashboard.yaml", "new_content": "title: Updated Dashboard\n", "reason": "test"}],
    )
    after = _generated_status()

    assert after == before


def test_scoped_execution_fixtures_are_neutral() -> None:
    combined = "\n".join(
        [
            (REPO_ROOT / "mozaiksai/control_plane/scoped_execution.py").read_text(encoding="utf-8"),
            (REPO_ROOT / "tests/test_refinement_scoped_execution.py").read_text(encoding="utf-8"),
        ]
    ).lower()
    forbidden_terms = [
        " ".join(("app", "zero")),
        "app_" + "zero",
        "mozaiks-" + "app",
        "bloc" + "united",
    ]

    assert not any(term in combined for term in forbidden_terms)


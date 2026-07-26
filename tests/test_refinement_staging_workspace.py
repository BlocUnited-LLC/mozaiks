from __future__ import annotations

import json
import subprocess
from pathlib import Path

from mozaiksai.control_plane import dry_run
from mozaiksai.control_plane.staging import create_refinement_staging_workspace

REPO_ROOT = Path(__file__).resolve().parents[1]


def _build_plan(
    affected_paths: list[str],
    staging_root: Path,
    *,
    request_id: str = "req_stage_001",
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
    (source / "modules/projects/backend/service.py").write_text("VALUE = 'original'\n", encoding="utf-8")
    return source


def _generated_status() -> str:
    completed = subprocess.run(
        ["git", "status", "--short", "--", "generated", "generated_apps"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _file_statuses(result) -> dict[str, str]:  # noqa: ANN001
    return {file.path: file.status for file in result.files}


def test_staged_workspace_creates_refinement_plan_json(tmp_path: Path) -> None:
    staging_root = tmp_path / ".refinement_staging"
    plan = _build_plan(["ui/pages/dashboard.yaml"], staging_root)

    result = create_refinement_staging_workspace(plan, staging_root=staging_root)
    payload = json.loads(Path(result.plan_path).read_text(encoding="utf-8"))

    assert Path(result.plan_path).name == "refinement_plan.json"
    assert payload["request_id"] == plan.request_id
    assert payload["mutation_allowed"] is False


def test_staged_workspace_creates_affected_paths_json(tmp_path: Path) -> None:
    staging_root = tmp_path / ".refinement_staging"
    plan = _build_plan(["ui/pages/dashboard.yaml"], staging_root)

    result = create_refinement_staging_workspace(plan, staging_root=staging_root)
    payload = json.loads(Path(result.affected_paths_path).read_text(encoding="utf-8"))

    assert Path(result.affected_paths_path).name == "affected_paths.json"
    assert payload["affected_bundle_paths"] == ["ui/pages/dashboard.yaml"]
    assert payload["files"][0]["path"] == "ui/pages/dashboard.yaml"


def test_staged_workspace_creates_readme_manifest(tmp_path: Path) -> None:
    staging_root = tmp_path / ".refinement_staging"
    plan = _build_plan(["ui/pages/dashboard.yaml"], staging_root)

    result = create_refinement_staging_workspace(plan, staging_root=staging_root)
    readme = Path(result.manifest_path).read_text(encoding="utf-8")

    assert Path(result.manifest_path).name == "README.md"
    assert "No source app files were mutated." in readme
    assert "Refinement execution has not run." in readme
    assert "Human approval is required before applying or promoting any staged output." in readme


def test_copied_affected_file_lands_under_workspace_mirror(tmp_path: Path) -> None:
    staging_root = tmp_path / ".refinement_staging"
    source = _source_bundle(tmp_path)
    plan = _build_plan(["ui/pages/dashboard.yaml"], staging_root)

    result = create_refinement_staging_workspace(plan, source_bundle_path=source, staging_root=staging_root)
    copied_path = Path(result.staging_area) / "workspace/ui/pages/dashboard.yaml"

    assert copied_path.exists()
    assert copied_path.read_text(encoding="utf-8") == "title: Dashboard\n"
    assert _file_statuses(result)["ui/pages/dashboard.yaml"] == "copied"


def test_source_file_remains_unchanged(tmp_path: Path) -> None:
    staging_root = tmp_path / ".refinement_staging"
    source = _source_bundle(tmp_path)
    source_file = source / "modules/projects/backend/service.py"
    before = source_file.read_text(encoding="utf-8")
    plan = _build_plan(["modules/projects/backend/service.py"], staging_root)

    create_refinement_staging_workspace(plan, source_bundle_path=source, staging_root=staging_root)

    assert source_file.read_text(encoding="utf-8") == before


def test_missing_affected_file_records_missing(tmp_path: Path) -> None:
    staging_root = tmp_path / ".refinement_staging"
    source = _source_bundle(tmp_path)
    plan = _build_plan(["ui/pages/missing.yaml"], staging_root)

    result = create_refinement_staging_workspace(plan, source_bundle_path=source, staging_root=staging_root)

    assert _file_statuses(result)["ui/pages/missing.yaml"] == "missing"


def test_glob_path_records_skipped_glob(tmp_path: Path) -> None:
    staging_root = tmp_path / ".refinement_staging"
    plan = _build_plan(["ui/pages/*.yaml"], staging_root)

    result = create_refinement_staging_workspace(plan, staging_root=staging_root)

    assert _file_statuses(result)["ui/pages/*.yaml"] == "skipped_glob"


def test_secret_path_records_skipped_secret(tmp_path: Path) -> None:
    staging_root = tmp_path / ".refinement_staging"
    plan = _build_plan(["config/secrets/provider_credentials.json"], staging_root)

    result = create_refinement_staging_workspace(plan, staging_root=staging_root)

    assert _file_statuses(result)["config/secrets/provider_credentials.json"] == "skipped_secret"


def test_path_traversal_records_skipped_unsafe(tmp_path: Path) -> None:
    staging_root = tmp_path / ".refinement_staging"
    plan = _build_plan(["../outside.txt"], staging_root)

    result = create_refinement_staging_workspace(plan, staging_root=staging_root)

    assert result.files[0].status == "skipped_unsafe"


def test_absolute_path_records_skipped_unsafe(tmp_path: Path) -> None:
    staging_root = tmp_path / ".refinement_staging"
    plan = _build_plan([str(tmp_path / "outside.txt")], staging_root)

    result = create_refinement_staging_workspace(plan, staging_root=staging_root)

    assert result.files[0].status == "skipped_unsafe"


def test_writes_never_escape_staging_root(tmp_path: Path) -> None:
    staging_root = tmp_path / ".refinement_staging"
    source = _source_bundle(tmp_path)
    plan = _build_plan(["ui/pages/dashboard.yaml", "../outside.txt"], staging_root)

    result = create_refinement_staging_workspace(plan, source_bundle_path=source, staging_root=staging_root)
    staging_root_resolved = staging_root.resolve()
    staging_area_resolved = Path(result.staging_area).resolve()

    for output_path in [result.staging_area, result.plan_path, result.affected_paths_path, result.manifest_path]:
        assert Path(output_path).resolve().is_relative_to(staging_root_resolved)
    for file in staging_root.rglob("*"):
        if file.is_file():
            assert file.resolve().is_relative_to(staging_area_resolved)


def test_staging_result_keeps_mutation_allowed_false(tmp_path: Path) -> None:
    staging_root = tmp_path / ".refinement_staging"
    plan = _build_plan(["ui/pages/dashboard.yaml"], staging_root)

    result = create_refinement_staging_workspace(plan, staging_root=staging_root)
    payload = json.loads(Path(result.plan_path).read_text(encoding="utf-8"))

    assert result.mutation_allowed is False
    assert payload["mutation_allowed"] is False


def test_staging_helper_does_not_execute_workflows_or_llm() -> None:
    source = (REPO_ROOT / "mozaiksai/control_plane/staging.py").read_text(encoding="utf-8")

    assert "RefinementTriggerRouteResolver" not in source
    assert "load_refinement_harness" not in source
    assert "LLMChangeClassifier" not in source
    assert "build_refinement_execution_plan(" not in source


def test_staging_does_not_write_generated_app_files_outside_staging_root(tmp_path: Path) -> None:
    staging_root = tmp_path / ".refinement_staging"
    plan = _build_plan(["ui/pages/dashboard.yaml"], staging_root)
    before = _generated_status()

    create_refinement_staging_workspace(plan, staging_root=staging_root)
    after = _generated_status()

    assert after == before


def test_staging_fixtures_are_neutral() -> None:
    combined = "\n".join(
        [
            (REPO_ROOT / "mozaiksai/control_plane/staging.py").read_text(encoding="utf-8"),
            (REPO_ROOT / "tests/test_refinement_staging_workspace.py").read_text(encoding="utf-8"),
        ]
    ).lower()
    forbidden_terms = [
        " ".join(("app", "zero")),
        "app_" + "zero",
        "mozaiks-" + "app",
        "bloc" + "united",
    ]

    assert not any(term in combined for term in forbidden_terms)


from __future__ import annotations

import json
from pathlib import Path

from mozaiksai.control_plane import dry_run
from mozaiksai.control_plane import validation_runner as validation_runner_module
from mozaiksai.control_plane.promotion import promote_refinement_staging
from mozaiksai.control_plane.review import (
    approve_refinement_staging,
    create_refinement_review_record,
    mark_refinement_promotion_ready,
)
from mozaiksai.control_plane.scoped_execution import (
    ScopedRefinementChange,
    apply_scoped_refinement_changes,
)
from mozaiksai.control_plane.staging import create_refinement_staging_workspace
from mozaiksai.control_plane.validation_evidence import ValidationEvidence
from mozaiksai.control_plane.validation_runner import run_refinement_validations


def _write_bundle(tmp_path: Path, files: dict[str, str]) -> Path:
    source = tmp_path / "source_app_bundle"
    for relative_path, content in files.items():
        file_path = source / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
    return source


def _json(content: dict[str, object]) -> str:
    return json.dumps(content, indent=2, sort_keys=True) + "\n"


def _build_plan(
    tmp_path: Path,
    *,
    request: str,
    change_class: str,
    workflow_sequence: str,
    affected_bundle_paths: list[str],
    affected_declarative_families: list[str] | None = None,
    request_id: str = "req_validation_001",
) -> dry_run.RefinementExecutionPlan:
    return dry_run.build_refinement_execution_plan_from_route(
        request=request,
        artifact_kind="app_bundle",
        change_class=change_class,
        workflow_id="AppGenerator",
        workflow_sequence=workflow_sequence,
        affected_workflows=["AppGenerator"],
        affected_declarative_families=affected_declarative_families or [],
        affected_bundle_paths=affected_bundle_paths,
        scope_summary="Validation runner test scope.",
        app_id="sample_app",
        request_id=request_id,
        execution_mode="staged",
        staging_base_path=tmp_path / ".refinement_staging",
    )


def _stage_workspace(
    tmp_path: Path,
    *,
    request: str,
    change_class: str,
    workflow_sequence: str,
    affected_bundle_paths: list[str],
    files: dict[str, str],
    affected_declarative_families: list[str] | None = None,
    request_id: str = "req_validation_001",
):
    source = _write_bundle(tmp_path, files)
    plan = _build_plan(
        tmp_path,
        request=request,
        change_class=change_class,
        workflow_sequence=workflow_sequence,
        affected_bundle_paths=affected_bundle_paths,
        affected_declarative_families=affected_declarative_families,
        request_id=request_id,
    )
    staging_result = create_refinement_staging_workspace(
        plan,
        source_bundle_path=source,
        staging_root=tmp_path / ".refinement_staging",
    )
    return source, plan, staging_result


def _item(result, name: str):  # noqa: ANN001
    for item in result.items:
        if item.name == name:
            return item
    raise AssertionError(f"Validation item not found: {name}")


def _route_bundle_files(*, register_component: bool = True, clean_react: bool = True) -> dict[str, str]:
    index_js = (
        "import OverviewPage from './pages/custom/OverviewPage.jsx';\n\n"
        "export function register(registerComponent) {\n"
        "  registerComponent('OverviewPage', OverviewPage);\n"
        "}\n"
    )
    if not register_component:
        index_js = "export function register(registerComponent) {}\n"

    react_surface = (
        "export default function OverviewPage() {\n"
        "  return <main><h1>Overview</h1></main>;\n"
        "}\n"
    )
    if not clean_react:
        react_surface = (
            "import { Card } from '@mozaiks/chat-ui/ui';\n\n"
            "export default function OverviewPage() {\n"
            "  return <Card style={{ color: '#fff' }}>Overview</Card>;\n"
            "}\n"
        )

    return {
        "ui/pages/dashboard.yaml": "page_type: landing\ntitle: Overview\n",
        "ui/route_manifest.json": _json(
            {
                "pages": [
                    {
                        "id": "overview",
                        "label": "Overview",
                        "path": "/overview",
                        "component": "OverviewPage",
                        "requiresAuth": True,
                        "purpose": "Overview surface.",
                    }
                ]
            }
        ),
        "ui/index.js": index_js,
        "ui/pages/custom/OverviewPage.jsx": react_surface,
    }


def _database_bundle_files(*, valid_intent: bool = True, valid_migration: bool = True) -> dict[str, str]:
    intent = _json({"collections": ["projects"], "version": 1})
    if not valid_intent:
        intent = '{"collections": [}\n'
    migration = _json({"migration_id": "001_initial", "changes": []})
    if not valid_migration:
        migration = '{"migration_id": "001_initial", "changes": [}\n'
    return {
        "config/data.json": intent,
        "config/data_migrations/001_initial.json": migration,
    }


def _module_bundle_files(*, valid: bool = True) -> dict[str, str]:
    if valid:
        module_yaml = (
            "schema_version: mozaiks.module.v1\n"
            "module:\n"
            "  id: projects\n"
            "  display_name: Projects\n"
            "  version: 1.0.0\n"
            "  description: Projects module.\n"
            "  handler: backend.handler:ProjectsModule\n"
            "permissions: []\n"
            "actions: []\n"
            "capabilities: []\n"
        )
    else:
        module_yaml = "schema_version: mozaiks.module.v1\nmodule: projects\n"
    return {
        "modules/projects/module.yaml": module_yaml,
    }


def _hosted_bundle_files() -> dict[str, str]:
    return {
        "modules/hosted_analytics/backend/service.py": "VALUE = 'hosted'\n",
    }


def _integration_bundle_files() -> dict[str, str]:
    return {
        "services/integrations/analytics_provider_client.py": "def fetch_metrics():\n    return {}\n",
    }


def test_runner_returns_validation_evidence(tmp_path: Path) -> None:
    source, plan, staging_result = _stage_workspace(
        tmp_path,
        request="Fix the dashboard label.",
        change_class="patch",
        workflow_sequence="app_revision",
        affected_bundle_paths=list(_route_bundle_files().keys()),
        files=_route_bundle_files(),
    )

    result = run_refinement_validations(plan, staging_result)

    assert isinstance(result.evidence, ValidationEvidence)
    assert result.evidence.source == "refinement_validation_runner"
    assert result.items
    assert (source / "ui/pages/dashboard.yaml").exists()


def test_route_component_validation_passes_for_valid_staged_route_and_index(tmp_path: Path) -> None:
    _, plan, staging_result = _stage_workspace(
        tmp_path,
        request="Fix the dashboard label.",
        change_class="patch",
        workflow_sequence="app_revision",
        affected_bundle_paths=list(_route_bundle_files().keys()),
        files=_route_bundle_files(),
    )

    result = run_refinement_validations(plan, staging_result)

    assert _item(result, "route_component_validation").status == "passed"
    assert "route_component_validation" in result.evidence.completed


def test_route_component_validation_fails_for_missing_component_registration(tmp_path: Path) -> None:
    _, plan, staging_result = _stage_workspace(
        tmp_path,
        request="Fix the dashboard label.",
        change_class="patch",
        workflow_sequence="app_revision",
        affected_bundle_paths=list(_route_bundle_files(register_component=False).keys()),
        files=_route_bundle_files(register_component=False),
    )

    result = run_refinement_validations(plan, staging_result)

    assert _item(result, "route_component_validation").status == "failed"
    assert "route_component_validation" in result.evidence.failed


def test_ui_theme_primitive_validation_passes_for_clean_custom_react(tmp_path: Path) -> None:
    _, plan, staging_result = _stage_workspace(
        tmp_path,
        request="Fix the dashboard label.",
        change_class="patch",
        workflow_sequence="app_revision",
        affected_bundle_paths=list(_route_bundle_files().keys()),
        files=_route_bundle_files(),
    )

    result = run_refinement_validations(plan, staging_result)

    assert _item(result, "ui_theme_primitive_validation").status == "passed"
    assert "ui_theme_primitive_validation" in result.evidence.completed


def test_ui_theme_primitive_validation_fails_on_local_primitive_clone(tmp_path: Path) -> None:
    _, plan, staging_result = _stage_workspace(
        tmp_path,
        request="Fix the dashboard label.",
        change_class="patch",
        workflow_sequence="app_revision",
        affected_bundle_paths=list(_route_bundle_files(clean_react=False).keys()),
        files=_route_bundle_files(clean_react=False),
    )

    result = run_refinement_validations(plan, staging_result)

    assert _item(result, "ui_theme_primitive_validation").status == "failed"
    assert "ui_theme_primitive_validation" in result.evidence.failed


def test_data_contract_validation_passes_valid_json(tmp_path: Path) -> None:
    files = _database_bundle_files()
    _, plan, staging_result = _stage_workspace(
        tmp_path,
        request="Add a required project phase field and migrate existing project records.",
        change_class="feature",
        workflow_sequence="app_revision",
        affected_bundle_paths=list(files.keys()),
        files=files,
    )

    result = run_refinement_validations(plan, staging_result)

    assert _item(result, "data_contract_validation").status == "passed"
    assert "data_contract_validation" in result.evidence.completed


def test_data_contract_validation_fails_invalid_json(tmp_path: Path) -> None:
    files = _database_bundle_files(valid_intent=False)
    _, plan, staging_result = _stage_workspace(
        tmp_path,
        request="Add a required project phase field and migrate existing project records.",
        change_class="feature",
        workflow_sequence="app_revision",
        affected_bundle_paths=list(files.keys()),
        files=files,
    )

    result = run_refinement_validations(plan, staging_result)

    assert _item(result, "data_contract_validation").status == "failed"
    assert "data_contract_validation" in result.evidence.failed


def test_migration_plan_validation_passes_valid_json_migration_file(tmp_path: Path) -> None:
    files = _database_bundle_files()
    _, plan, staging_result = _stage_workspace(
        tmp_path,
        request="Add a required project phase field and migrate existing project records.",
        change_class="feature",
        workflow_sequence="app_revision",
        affected_bundle_paths=list(files.keys()),
        files=files,
    )

    result = run_refinement_validations(plan, staging_result)

    assert _item(result, "migration_plan_validation").status == "passed"
    assert "migration_plan_validation" in result.evidence.completed


def test_module_contract_validation_passes_for_valid_module_yaml(tmp_path: Path) -> None:
    files = _module_bundle_files()
    _, plan, staging_result = _stage_workspace(
        tmp_path,
        request="Add a projects module action.",
        change_class="feature",
        workflow_sequence="app_revision",
        affected_bundle_paths=list(files.keys()),
        files=files,
    )

    result = run_refinement_validations(plan, staging_result)

    assert _item(result, "module_contract_validation").status == "passed"
    assert "module_contract_validation" in result.evidence.completed


def test_module_contract_validation_fails_for_invalid_module_yaml(tmp_path: Path) -> None:
    files = _module_bundle_files(valid=False)
    _, plan, staging_result = _stage_workspace(
        tmp_path,
        request="Add a projects module action.",
        change_class="feature",
        workflow_sequence="app_revision",
        affected_bundle_paths=list(files.keys()),
        files=files,
    )

    result = run_refinement_validations(plan, staging_result)

    assert _item(result, "module_contract_validation").status == "failed"
    assert "module_contract_validation" in result.evidence.failed


def test_hosted_facade_boundary_validation_blocks_hosted_internal_path(tmp_path: Path) -> None:
    files = _hosted_bundle_files()
    _, plan, staging_result = _stage_workspace(
        tmp_path,
        request="Change hosted analytics service behavior.",
        change_class="design",
        workflow_sequence="app_surface_revision",
        affected_bundle_paths=list(files.keys()),
        files=files,
    )

    result = run_refinement_validations(plan, staging_result)

    assert _item(result, "hosted_facade_boundary_validation").status == "failed"
    assert "hosted_facade_boundary_validation" in result.evidence.failed


def test_integration_readiness_validation_is_warning_without_deterministic_context(tmp_path: Path) -> None:
    files = _integration_bundle_files()
    _, plan, staging_result = _stage_workspace(
        tmp_path,
        request="Change the analytics_provider connector sync behavior.",
        change_class="patch",
        workflow_sequence="app_revision",
        affected_bundle_paths=list(files.keys()),
        files=files,
    )

    result = run_refinement_validations(plan, staging_result)

    item = _item(result, "integration_readiness_validation")
    assert item.status == "warning"
    assert "integration_readiness_validation" not in result.evidence.completed
    assert "integration_readiness_validation" not in result.evidence.failed


def test_unknown_validation_names_are_skipped_or_warning(tmp_path: Path) -> None:
    plan = _build_plan(
        tmp_path,
        request="No-op validation request.",
        change_class="patch",
        workflow_sequence="app_revision",
        affected_bundle_paths=[],
    )
    staging_result = create_refinement_staging_workspace(
        plan,
        staging_root=tmp_path / ".refinement_staging",
    )

    result = run_refinement_validations(plan, staging_result, selected=["mystery_validator"])

    item = _item(result, "mystery_validator")
    assert item.status == "warning"
    assert "No deterministic validator is registered" in item.reason


def test_evidence_completed_only_includes_actual_passed_validators(tmp_path: Path) -> None:
    _, plan, staging_result = _stage_workspace(
        tmp_path,
        request="Fix the dashboard label.",
        change_class="patch",
        workflow_sequence="app_revision",
        affected_bundle_paths=list(_route_bundle_files(register_component=False).keys()),
        files=_route_bundle_files(register_component=False),
    )

    result = run_refinement_validations(plan, staging_result)

    assert result.evidence.completed_names() == {"ui_theme_primitive_validation"}
    assert "route_component_validation" not in result.evidence.completed_names()


def test_evidence_failed_includes_failed_validators(tmp_path: Path) -> None:
    _, plan, staging_result = _stage_workspace(
        tmp_path,
        request="Fix the dashboard label.",
        change_class="patch",
        workflow_sequence="app_revision",
        affected_bundle_paths=list(_route_bundle_files(register_component=False).keys()),
        files=_route_bundle_files(register_component=False),
    )

    result = run_refinement_validations(plan, staging_result)

    assert "route_component_validation" in result.evidence.failed
    assert "ui_theme_primitive_validation" not in result.evidence.failed


def test_runner_does_not_mutate_source_files(tmp_path: Path) -> None:
    source, plan, staging_result = _stage_workspace(
        tmp_path,
        request="Fix the dashboard label.",
        change_class="patch",
        workflow_sequence="app_revision",
        affected_bundle_paths=list(_route_bundle_files().keys()),
        files=_route_bundle_files(),
    )
    source_file = source / "ui/pages/dashboard.yaml"
    before = source_file.read_text(encoding="utf-8")

    run_refinement_validations(plan, staging_result)

    assert source_file.read_text(encoding="utf-8") == before


def test_runner_does_not_run_workflows_llms_or_appgenerator() -> None:
    source = Path(validation_runner_module.__file__).read_text(encoding="utf-8")

    assert "factory_app.workflows.AppGenerator" not in source
    assert "LLMChangeClassifier" not in source
    assert "execute_workflow" not in source


def test_validation_evidence_from_runner_can_enable_ui_patch_promotion(tmp_path: Path) -> None:
    source, plan, staging_result = _stage_workspace(
        tmp_path,
        request="Fix the dashboard label.",
        change_class="patch",
        workflow_sequence="app_revision",
        affected_bundle_paths=list(_route_bundle_files().keys()),
        files=_route_bundle_files(),
    )
    create_refinement_review_record(staging_result)
    approve_refinement_staging(staging_result.staging_area, reviewer="reviewer-1")
    review_record = mark_refinement_promotion_ready(staging_result.staging_area, reviewer="reviewer-1")
    execution_result = apply_scoped_refinement_changes(
        plan=plan,
        staging_result=staging_result,
        changes=[
            ScopedRefinementChange(
                path="ui/pages/dashboard.yaml",
                new_content="page_type: landing\ntitle: Overview Updated\n",
                reason="Runner-backed UI patch update.",
            )
        ],
    )
    validation_result = run_refinement_validations(plan, staging_result, scoped_result=execution_result)

    result = promote_refinement_staging(
        plan,
        staging_result,
        review_record,
        execution_result,
        source_bundle_path=source,
        confirm=True,
        dry_run=False,
        backup=True,
        validation_evidence=validation_result.evidence,
    )

    assert result.mutation_applied is True
    assert result.promoted_files[0].status == "promoted"
    assert (source / "ui/pages/dashboard.yaml").read_text(encoding="utf-8") == "page_type: landing\ntitle: Overview Updated\n"

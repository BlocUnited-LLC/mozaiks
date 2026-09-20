"""The compiled AG2 graph preserves repair priority and mandatory quality gates."""

from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_validation import run_app_bundle_acceptance_gate
from factory_app.workflows.AppGenerator.tools.generate_and_download import _export_repair_outcome
from mozaiksai.core.workflow.execution.network_graph import (
    compile_transition_rules_to_graph,
    resolve_next_agent,
)
from tests.test_appgenerator_bounded_recovery import _fixture
from tests.test_appplan_materialization_acceptance import _materialize_plan_bundle


@pytest.fixture(scope="module")
def graph():
    root = Path(__file__).resolve().parents[1] / "factory_app/workflows/AppGenerator"
    rules = yaml.safe_load((root / "transition_graph.yaml").read_text(encoding="utf-8"))["transition_rules"]
    config = yaml.safe_load((root / "agents.yaml").read_text(encoding="utf-8"))
    return compile_transition_rules_to_graph(
        rules, initial_agent_name="AppValidationAgent",
        agent_id_by_name={agent["name"]: agent["name"] for agent in config["agents"]},
        max_turns=30,
    )


def _next(graph, source, **context):
    return resolve_next_agent(
        graph, current_agent_name=source, context_variables={"task_run_mode": False, **context},
    )


@pytest.mark.parametrize("status", ["completed", "partial"])
def test_recovered_batch_reassembles_before_artifact_repair_or_final_block(graph, status):
    assert _next(
        graph, "AppValidationAgent", app_task_recovery_status=status,
        bundle_repair_status="blocked", bundle_repair_target="ServiceAgent",
        app_validation_status="failed", integration_tests_passed=False,
    ) == "AssemblyAgent"


@pytest.mark.parametrize("status", ["partial", "failed"])
def test_incomplete_assembly_returns_to_validation_before_auth_scaffolding(graph, status):
    assert _next(
        graph, "AssemblyAgent", app_task_batch_status=status, generated_files={},
        app_schema_ready=False, integration_readiness_status="blocked",
    ) == "AppValidationAgent"


@pytest.mark.parametrize("target", [
    "AppSchemaAgent", "ConfigMiddlewareAgent", "ServiceAgent", "DatabaseAgent",
    "ModelAgent", "FrontendStubAgent", "ControllerAgent", "RefinementHarnessAgent",
])
def test_independent_owned_target_precedes_unresolved_batch_and_final_block(graph, target):
    assert _next(
        graph, "AppValidationAgent", app_task_recovery_status="blocked",
        bundle_repair_status="blocked", bundle_repair_target=target,
        app_validation_status="failed", integration_tests_passed=False,
    ) == target


@pytest.mark.asyncio
async def test_rejected_batch_correction_revalidates_and_selects_independent_artifact_repair(graph, monkeypatch):
    from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
    from tests.test_appplan_materialization_acceptance import _file_map

    context, execute, counts, _, _ = _fixture(monkeypatch, correction="invalid")
    await execute("AppPlanAgent")
    files = _file_map(await assemble_app_tasks(context_variables=context))
    del files["modules/reports/backend/schemas.py"]
    first = await run_app_bundle_acceptance_gate(files=files, context_variables=context)
    assert first["task_recovery_request"]["root_task_ids"] == ["task_reports_services"]
    assert first["bundle_repair"]["target_agent"] is None
    await execute("AppValidationAgent")
    assert context.get("app_task_recovery_status") == "blocked"
    assert _next(graph, "AppValidationAgent", app_task_recovery_status="blocked",
                 bundle_repair_status="blocked", bundle_repair_target=None) == "AppValidationAgent"

    second = await run_app_bundle_acceptance_gate(files=files, context_variables=context)
    assert second["task_recovery_request"] is None
    assert second["bundle_repair"]["target_agent"] == "ModelAgent"
    assert second["bundle_repair"]["attempt"] == 1
    await execute("AppValidationAgent")
    assert context.get("app_task_recovery_status") == "idle"
    assert _next(graph, "AppValidationAgent", app_task_recovery_status="idle",
                 bundle_repair_target="ModelAgent") == "ModelAgent"
    assert counts["task_reports_services"] == 2
    assert counts["task_reports_models"] == 1
    assert _next(graph, "AppValidationAgent", app_task_recovery_status="idle",
                 bundle_repair_status="blocked", bundle_repair_target=None) == "user"


@pytest.mark.parametrize("owner,quality,status_key", [
    ("AppSchemaAgent", "AppUIQualityAgent", "app_ui_quality_status"),
    ("ConfigMiddlewareAgent", "ModuleContractQualityAgent", "module_contract_quality_status"),
    ("ServiceAgent", "ModuleRuntimeQualityAgent", "module_runtime_quality_status"),
])
@pytest.mark.parametrize("status", ["passed", "needs_revision", "blocked"])
def test_scoped_repair_crosses_its_quality_gate_then_returns_to_acceptance(graph, owner, quality, status_key, status):
    context = {"bundle_repair_target": owner, status_key: status}
    assert _next(graph, owner, **context) == quality
    assert _next(graph, quality, **context) == "AppValidationAgent"


@pytest.mark.parametrize("owner", ["DatabaseAgent", "ModelAgent", "FrontendStubAgent", "ControllerAgent", "RefinementHarnessAgent"])
def test_scoped_nonquality_lane_returns_without_generating_unrelated_outputs(graph, owner):
    assert _next(graph, owner, bundle_repair_target=owner) == "AppValidationAgent"


@pytest.mark.parametrize("outcome,target", [
    ("repair_models", "ModelAgent"), ("repair_controller", "ControllerAgent"),
    ("repair_tasks", "AppValidationAgent"),
    ("repair_harness", "RefinementHarnessAgent"),
])
def test_final_export_routes_models_controller_and_task_recovery_to_existing_lanes(graph, outcome, target):
    acceptance = (
        {"task_recovery_request": {"root_task_ids": ["failed_task"]}}
        if outcome == "repair_tasks"
        else {"bundle_repair": {"status": "needs_revision", "target_agent": target}}
    )
    assert _export_repair_outcome(acceptance) == outcome
    assert _next(graph, "DownloadAgent", app_bundle_export_outcome=outcome) == target


@pytest.mark.asyncio
@pytest.mark.parametrize("quality_status", [None, "needs_revision", "blocked"])
async def test_schema_quality_cannot_be_bypassed_by_returning_to_acceptance(graph, tmp_path, quality_status):
    files, _, _, context, _ = await _materialize_plan_bundle(tmp_path=tmp_path)
    assert context.get("app_bundle_acceptance_status") == "passed"
    page_task = next(task for task in context.get("app_task_batch_items") if task["task_type"] == "page_bundle")
    context.set("current_build_task", page_task)
    context.set("app_schema_ready", True)
    context.set("app_ui_quality_status", quality_status)
    context.set("app_ui_quality_warnings", ["Reports has unresolved schema quality findings"])
    assert _next(graph, "AppUIQualityAgent", bundle_repair_target="AppSchemaAgent",
                 app_ui_quality_status=quality_status) == "AppValidationAgent"

    gate = await run_app_bundle_acceptance_gate(files=files, context_variables=context)

    assert gate["passed"] is False
    assert gate["schema_quality"]["passed"] is False
    assert "unresolved schema quality" in gate["schema_quality"]["failed_tests"][0]["error"]
    assert _next(graph, "AppValidationAgent", **{
        key: context.get(key) for key in (
            "app_task_recovery_status", "bundle_repair_target", "bundle_repair_status",
            "app_validation_status", "integration_tests_passed",
        )
    }) != "DownloadAgent"

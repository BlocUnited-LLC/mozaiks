"""Assembly errors retain their cause and cannot accept the retained bundle."""

import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools import app_validation, assembly_phase
from factory_app.workflows.AppGenerator.tools import assemble_app_tasks as assembly
from factory_app.workflows.AppGenerator.tools import (
    resolve_carry_forward_preservation as preservation,
)
from factory_app.workflows.AppGenerator.tools.code_file_utils import save_generated_code
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge, _workflow_tool_invocation
from mozaiksai.core.workflow.context.authority import build_context_authority_policy
from mozaiksai.core.workflow.execution.network_graph import (
    compile_transition_rules_to_graph,
    resolve_next_agent,
)
from tests.factory_context import factory_context
from tests.test_generated_app_functional_acceptance import _basic_crud_files

WORKFLOW = Path(__file__).resolve().parents[1] / "factory_app/workflows/AppGenerator"
PREVIOUS_FILES = {"README.md": "Previously assembled bundle"}


def _context(**overrides):
    declarations = yaml.safe_load((WORKFLOW / "context_variables.yaml").read_text(encoding="utf-8"))
    transitions = yaml.safe_load((WORKFLOW / "transition_graph.yaml").read_text(encoding="utf-8"))
    policy = build_context_authority_policy(
        workflow_name="AppGenerator", definitions=declarations["definitions"],
        transition_rules=transitions["transition_rules"],
    )
    context = ContextVariablesBridge(factory_context({
        "generated_files": deepcopy(PREVIOUS_FILES),
        "assembled_source": "previous",
        "integration_tests_passed": True,
        "app_validation_status": "passed",
        "app_bundle_acceptance_status": "passed",
        "app_task_batch_results": {"module_contract": {"code_files": [
            {"filename": "README.md", "content": "New bundle"},
        ]}},
        **overrides,
    }), authority_policy=policy)
    context._bind_run(("AppGenerator", "assembly-app", "assembly-chat"), policy)
    return context


@pytest.mark.asyncio
@pytest.mark.parametrize("materializer", ["materialize_module_read_actions", "materialize_module_policies"])
async def test_materializer_cause_reaches_tool_and_context_without_later_checks(monkeypatch, materializer):
    context = _context()
    cause = "modules/tasks/module.yaml: forced materializer failure"
    monkeypatch.setattr(assembly_phase, materializer, Mock(side_effect=ValueError(cause)))
    page_check = Mock(side_effect=AssertionError("must not reach page checks"))
    monkeypatch.setattr(assembly, "_apply_planned_page_contracts", page_check)

    with _workflow_tool_invocation(context):
        result = await assembly.assemble_app_tasks(context_variables=context)

    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["error"] == f"ValueError: {cause}"
    assert "code_files" not in result
    assert context.get("app_assembly_error") == result["error"]
    assert context.get("app_assembly_status") == "failed"
    assert context.get("generated_files") == PREVIOUS_FILES
    assert context.get("assembled_source") == "previous"
    assert context.get("app_validation_status") == "failed"
    assert context.get("app_bundle_acceptance_status") == "failed"
    assert context.get("integration_tests_passed") is False
    page_check.assert_not_called()


@pytest.mark.asyncio
async def test_late_assembly_error_preserves_bundle_and_successful_retry_clears_error(monkeypatch):
    context = _context()
    original = assembly._apply_app_config_contracts
    monkeypatch.setattr(assembly, "_apply_app_config_contracts", Mock(side_effect=ValueError("config could not compile")))
    with _workflow_tool_invocation(context):
        failed = await assembly.assemble_app_tasks(context_variables=context)
    assert failed["error"] == "ValueError: config could not compile"
    assert context.get("generated_files") == PREVIOUS_FILES

    monkeypatch.setattr(assembly, "_apply_app_config_contracts", original)
    with _workflow_tool_invocation(context):
        succeeded = await assembly.assemble_app_tasks(context_variables=context)
    assert succeeded["success"] is True
    assert succeeded["status"] == context.get("app_assembly_status") == "passed"
    assert context.get("app_assembly_error") is None
    assert context.get("generated_files")["README.md"] == "New bundle"
    assert context.get("integration_tests_passed") is False
    assert context.get("app_bundle_acceptance_status") == "pending"


@pytest.mark.asyncio
async def test_failed_assembly_blocks_validation_export_gate_and_preservation(monkeypatch):
    context = _context(app_assembly_status="failed", app_assembly_error="ValueError: original failure")
    runtime = AsyncMock(side_effect=AssertionError("stale bundle reached runtime validation"))
    auth = AsyncMock(side_effect=AssertionError("stale bundle reached auth materialization"))
    carry_forward = AsyncMock(side_effect=AssertionError("stale bundle reached preservation"))
    monkeypatch.setattr(app_validation, "_app_runtime_load_result", runtime)
    monkeypatch.setattr(app_validation, "save_auth_scaffold", auth)
    monkeypatch.setattr(preservation, "_core", carry_forward)
    with _workflow_tool_invocation(context):
        validated = await app_validation.validate_app_bundle_from_request({}, context_variables=context)
        accepted = await app_validation.run_app_bundle_acceptance_gate(
            files={"README.md": "Must not overwrite retained bundle"}, context_variables=context,
        )
        preserved = await preservation.resolve_carry_forward_preservation(context_variables=context)
    for result in (validated, accepted):
        assert result["passed"] is False
        assert result["error"] == "ValueError: original failure"
        assert result["validation_evidence"] == {"completed": [], "failed": ["assembly"]}
        assert "snapshot_digest" not in result
    assert preserved["reason"] == "assembly_failed"
    assert context.get("generated_files") == PREVIOUS_FILES
    assert context.get("integration_tests_passed") is False
    runtime.assert_not_called()
    auth.assert_not_called()
    carry_forward.assert_not_called()


def _next(source, context):
    rules = yaml.safe_load((WORKFLOW / "transition_graph.yaml").read_text(encoding="utf-8"))["transition_rules"]
    names = {rule[key] for rule in rules for key in ("source_agent", "target_agent")}
    graph = compile_transition_rules_to_graph(
        rules, initial_agent_name="AssemblyAgent",
        agent_id_by_name={name: name for name in names - {"user", "terminate"}},
    )
    return resolve_next_agent(graph, current_agent_name=source, context_variables=context.snapshot())


@pytest.mark.asyncio
async def test_unattributed_failure_terminates_after_recovery_policy_without_user_retry_loop(monkeypatch):
    context = _context()
    monkeypatch.setattr(assembly_phase, "materialize_module_read_actions", Mock(side_effect=ValueError("unknown materializer cause")))
    with _workflow_tool_invocation(context):
        failed = await assembly.assemble_app_tasks(context_variables=context)
        assert _next("AssemblyAgent", context) == "AppValidationAgent"
        assert _next("user", context) == "AppValidationAgent"
        validation = await app_validation.validate_app_bundle_from_request({}, context_variables=context)
    assert validation["error"] == failed["error"]
    assert validation["bundle_repair"]["repairable"] is False
    assert validation["task_recovery_request"] is None
    assert _next("AppValidationAgent", context) == "terminate"
    rules = yaml.safe_load((WORKFLOW / "transition_graph.yaml").read_text(encoding="utf-8"))["transition_rules"]
    terminal = next(rule for rule in rules if rule["source_agent"] == "AppValidationAgent"
                    and rule.get("condition_key") == "app_assembly_status")
    assert terminal["termination_reason"] == "workflow_failed"


@pytest.mark.asyncio
async def test_owned_module_failure_repairs_overlay_before_real_reassembly_and_validation():
    files = _basic_crud_files()
    path = "modules/orders/module.yaml"
    task = {"task_id": "orders.contract", "task_type": "module_contract", "initial_agent": "ConfigMiddlewareAgent",
            "owned_paths": [path], "depends_on": []}
    context = _context(
        generated_files=files, app_build_plan={"build_tasks": [task]},
        data_contract=json.loads(files["data/contract.json"]),
        app_task_batch_results={task["task_id"]: {"code_files": [{"filename": path, "content": "[]"}]}},
        structured_output={"code_files": [{"filename": path, "content": files[path]}]},
    )
    with _workflow_tool_invocation(context):
        failed = await assembly.assemble_app_tasks(context_variables=context)
        assert failed["error"] == f"ValueError: {path}: module manifest must be an object"
        first = await app_validation.validate_app_bundle_from_request({}, context_variables=context)
        assert first["bundle_repair"]["target_agent"] == "ConfigMiddlewareAgent"
        assert first["bundle_repair"]["active"]["allowed_paths"] == [path]
        assert _next("AppValidationAgent", context) == "ConfigMiddlewareAgent"
        assert context.get("generated_files") == files
        saved = save_generated_code(context)
        assert saved["saved_files"] == [path]
        assert context.get("bundle_repair_result")["active"]["status"] == "responded"
        final = await app_validation.validate_app_bundle_from_request(
            {"validation_strategy": "skip", "start_dev_server": False}, context_variables=context,
        )
    assert context.get("app_assembly_status") == "passed"
    assert context.get("app_assembly_error") is None

    assert context.get("app_task_batch_results")[task["task_id"]]["code_files"][0]["content"] == "[]"
    assert yaml.safe_load(context.get("generated_files")[path])["module"]["id"] == "orders"
    assert final["integration_tests_passed"] is True, final
    assert final["app_runtime_load_result"]["passed"] is True


@pytest.mark.asyncio
async def test_same_failure_after_owned_repair_exhausts_progress_without_reassembly_loop(monkeypatch):
    task = {"task_id": "readme", "task_type": "api_surface", "initial_agent": "ControllerAgent",
            "owned_paths": ["README.md"], "depends_on": []}
    context = _context(
        app_build_plan={"build_tasks": [task]},
        app_task_batch_results={"readme": {"code_files": [{"filename": "README.md", "content": "bad"}]}},
        structured_output={"code_files": [{"filename": "README.md", "content": "corrected"}]},
    )
    materializer = Mock(side_effect=ValueError("README.md: deterministic rejection"))
    monkeypatch.setattr(assembly, "_apply_app_config_contracts", materializer)
    with _workflow_tool_invocation(context):
        await assembly.assemble_app_tasks(context_variables=context)
        first = await app_validation.validate_app_bundle_from_request({}, context_variables=context)
        assert first["bundle_repair"]["target_agent"] == "ControllerAgent"
        save_generated_code(context)
        second = await app_validation.validate_app_bundle_from_request({}, context_variables=context)
        third = await app_validation.validate_app_bundle_from_request({}, context_variables=context)
    assert materializer.call_count == 2
    for result in (second, third):
        assert result["error"] == "ValueError: README.md: deterministic rejection"
        assert result["bundle_repair"]["no_progress"] is True
    assert context.get("bundle_repair_attempt_count") == 1
    assert _next("AppValidationAgent", context) == "terminate"


@pytest.mark.asyncio
async def test_failed_assembly_preserves_real_partial_batch_recovery_and_reassembles(monkeypatch):
    from tests.test_appgenerator_bounded_recovery import _fixture

    context, execute, counts, _, _ = _fixture(monkeypatch)
    await execute("AppPlanAgent")
    assert context.get("app_task_batch_status") == "partial"
    original = assembly._apply_app_config_contracts
    monkeypatch.setattr(assembly, "_apply_app_config_contracts", Mock(side_effect=ValueError("partial batch assembly failed")))
    failed = await assembly.assemble_app_tasks(context_variables=context)
    assert failed["success"] is False
    validation = await app_validation.validate_app_bundle_from_request({}, context_variables=context)
    assert validation["task_recovery_request"]["root_task_ids"] == ["task_reports_services"]
    assert validation["bundle_repair"]["target_agent"] is None
    await execute("AppValidationAgent")
    assert _next("AppValidationAgent", context) == "AssemblyAgent"
    assert counts["task_reports_services"] == 2
    assert counts["task_report_pages"] == 1
    monkeypatch.setattr(assembly, "_apply_app_config_contracts", original)
    assembled = await assembly.assemble_app_tasks(context_variables=context)
    assert assembled["success"] is True
    assert context.get("app_assembly_status") == "passed"
    assert context.get("app_assembly_error") is None

    # A later failure must not reuse the previous successful recovery status.
    monkeypatch.setattr(assembly, "_apply_app_config_contracts", Mock(side_effect=ValueError("unowned later failure")))
    await assembly.assemble_app_tasks(context_variables=context)
    rejected = await app_validation.validate_app_bundle_from_request({}, context_variables=context)
    assert rejected["task_recovery_request"] is None
    counts_before = counts.copy()
    await execute("AppValidationAgent")
    assert counts == counts_before
    assert context.get("app_task_recovery_status") == "idle"
    assert _next("AppValidationAgent", context) == "terminate"

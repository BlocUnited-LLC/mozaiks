"""Offline proof through Factory policy, AG2 batch execution, assembly, and acceptance."""

from __future__ import annotations

import asyncio
from collections import Counter
from copy import deepcopy

import pytest

from factory_app.workflows.AppGenerator.tools.app_build_plan import app_build_plan
from factory_app.workflows.AppGenerator.tools.app_validation import run_app_bundle_acceptance_gate
from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
from factory_app.workflows.AppGenerator.tools.code_file_utils import compose_bundle_auth_routes
from factory_app.workflows.AppGenerator.tools.render_auth_scaffold import save_auth_scaffold
from factory_app.workflows.AppGenerator.tools.repair_policy import prepare_task_recovery
from mozaiksai.core.adapters.ag2_task_batch_runner import AG2TaskBatchRunnerResult
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow import task_batches as tb
from tests.test_appplan_materialization_acceptance import (
    WORKFLOWS_ROOT,
    _Context,
    _file_map,
    _load_fixture_plan,
    _task_output,
)


def _fixture(monkeypatch, *, correction="valid"):
    context = _Context({
        "app_id": "generated-saas-plan", "app_name": "Generated SaaS Plan",
        "app_slug": "generated-saas-plan", "landing_spot": "/reports",
        "chat_id": "bounded-recovery-proof", "build_task_model": "AppBuildTask",
        "app_validation_strategy_used": "skip", "app_validation_status": "skipped",
    })
    app_build_plan(AppBuildPlan=_load_fixture_plan(), context_variables=context)
    config = tb.load_task_batches_config("AppGenerator", workflows_root=WORKFLOWS_ROOT)
    assert config.batches[0].recovery is not None
    counts, checkpoints, calls = Counter(), [], []

    async def checkpoint(updates):
        checkpoints.append(deepcopy(updates))

    async def execute(trigger):
        await tb.execute_task_batches_for_trigger(
            workflow_name="AppGenerator", trigger_agent=trigger, batches_config=config,
            agents={name: object() for name in (
                "ConfigMiddlewareAgent", "DatabaseAgent", "ModelAgent", "ServiceAgent", "AppSchemaAgent",
            )}, context_variables=context.data, chat_id=context.get("chat_id"),
            app_id=context.get("app_id"), user_id="user-1", fresh_agents_per_task=False,
            checkpoint=checkpoint,
            parent_channel_id="test-parent-channel",
        )

    async def run(self, request):
        task = request.context_variables["current_task"]
        counts[request.task_id] += 1
        calls.append(request)
        assert set(request.context_variables["dependency_task_outputs"]) == set(task["depends_on"])
        reservation = checkpoints[-1]["app_task_batch_results"]["_meta"]["in_flight"]
        assert reservation[request.task_id]["attempt"] == counts[request.task_id]
        if request.task_id == "task_reports_services" and counts[request.task_id] == 2 and correction == "interrupted":
            raise asyncio.CancelledError()
        output = _task_output(task_id=request.task_id, task_type=task["task_type"], task=task)
        if request.task_id == "task_reports_services" and (counts[request.task_id] == 1 or correction == "invalid"):
            output["code_files"].append({
                "filename": "modules/reports/backend/schemas.py", "content": "class ForeignServiceSchema: pass\n",
            })
        return AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output=output)

    monkeypatch.setattr(tb.AG2TaskBatchRunner, "run", run)
    return context, execute, counts, checkpoints, calls


@pytest.mark.asyncio
async def test_failed_service_correction_releases_page_and_passes_complete_app_acceptance(monkeypatch):
    context, execute, counts, _, calls = _fixture(monkeypatch)
    await execute("AppPlanAgent")
    assert context.get("app_task_batch_status") == "partial"
    evidence = deepcopy(context.get("app_task_batch_results"))
    failure = evidence["_failed"]["task_reports_services"]
    assert failure["failure_kind"] == "output_rejected"
    assert "modules/reports/backend/schemas.py" in failure["error"]
    assert evidence["_failed"]["task_report_pages"]["failure_kind"] == "dependency_blocked"
    assert counts["task_report_pages"] == 0
    retained = {key: value for key, value in evidence.items() if not key.startswith("_")}

    partial = _file_map(await assemble_app_tasks(context_variables=context))
    assert context.get("app_task_batch_results") == evidence
    assert "app.json" not in partial
    before = await run_app_bundle_acceptance_gate(files=partial, context_variables=context)
    assert before["passed"] is False
    request = context.get("app_task_recovery_request")
    assert request["root_task_ids"] == ["task_reports_services"]

    await execute("AppValidationAgent")
    assert context.get("app_task_batch_status") == "completed"
    assert context.get("app_task_recovery_status") == "completed"
    assert context.get("app_task_batch_results")["_meta"]["failure_history"]["task_reports_services"][0] == failure
    assert counts["task_reports_services"] == 2
    assert counts["task_report_pages"] == 1
    for task_id, accepted in retained.items():
        assert counts[task_id] == 1
        assert context.get("app_task_batch_results")[task_id] == accepted
    corrected_call = [call for call in calls if call.task_id == "task_reports_services"][1]
    assert failure["error"] in corrected_call.prompt
    assert any("schemas.py" in item["filename"]
               for output in corrected_call.context_variables["dependency_task_outputs"].values()
               for item in output.get("code_files", []))

    files = _file_map(await assemble_app_tasks(context_variables=context))
    files.update(_file_map(await save_auth_scaffold(context_variables=context.data)))
    compose_bundle_auth_routes(files)
    gate = await run_app_bundle_acceptance_gate(files=files, context_variables=context)
    assert gate["passed"] is True, gate
    assert gate["functional_completeness"]["passed"] is True
    assert "ForeignServiceSchema" not in files["modules/reports/backend/schemas.py"]

    # Explicit final snapshot validation cannot refill an omitted page from stale context.
    incomplete = {path: content for path, content in files.items() if path != "ui/pages/reports.yaml"}
    rejected = await run_app_bundle_acceptance_gate(files=incomplete, context_variables=context)
    assert rejected["passed"] is False
    assert any(item["path"] == "ui/pages/reports.yaml"
               for item in rejected["planned_completeness"]["diagnostics"])


@pytest.mark.asyncio
@pytest.mark.parametrize("correction", ["invalid", "interrupted"])
async def test_failed_or_interrupted_correction_stays_blocked_without_replaying_successes(monkeypatch, correction):
    context, execute, counts, checkpoints, _ = _fixture(monkeypatch, correction=correction)
    await execute("AppPlanAgent")
    retained = deepcopy(context.get("app_task_batch_results")["task_reports_models"])
    failure = deepcopy(context.get("app_task_batch_results")["_failed"]["task_reports_services"])
    partial = _file_map(await assemble_app_tasks(context_variables=context))
    assert prepare_task_recovery(context)["root_task_ids"] == ["task_reports_services"]
    if correction == "interrupted":
        with pytest.raises(asyncio.CancelledError):
            await execute("AppValidationAgent")
        context.data.update(deepcopy(checkpoints[-1]))
    else:
        await execute("AppValidationAgent")
    assert prepare_task_recovery(context) is None
    counts_before = counts.copy()
    await execute("AppValidationAgent")
    assert counts == counts_before
    assert counts["task_reports_services"] == 2
    assert counts["task_report_pages"] == 0
    assert context.get("app_task_batch_results")["task_reports_models"] == retained
    assert context.get("app_task_batch_results")["_meta"]["failure_history"]["task_reports_services"][0] == failure
    gate = await run_app_bundle_acceptance_gate(files=partial, context_variables=context)
    assert gate["passed"] is False
    assert context.get("app_task_recovery_request") is None

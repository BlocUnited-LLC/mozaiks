"""Bounded continuation keeps the original DAG and accepted task evidence."""

from __future__ import annotations

import asyncio
import copy
import json
from collections import Counter

import pytest

from mozaiksai.core.adapters.ag2_task_batch_runner import AG2TaskBatchRunnerResult
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow import task_batches as tb


def _config(retries=3):
    return tb.parse_task_batches_config({"batches": [{
        "id": "build", "trigger_agent": "Plan", "source": {
            "kind": "context_variable", "path": "tasks", "task_model": "BuildTask",
        },
        "execution": {"failure_policy": "continue_with_available", "retry_limit": retries},
        "result": {"context_key": "results", "status_key": "status"},
        "recovery": {"trigger_agent": "Validate", "request_key": "recovery_request",
                     "outcome_key": "recovery_result", "status_key": "recovery_status", "input_keys": ["approved_plan"]},
    }]})


def _context():
    return {"approved_plan": {"revision": 1}, "tasks": [
        {"task_id": "models", "initial_agent": "ModelAgent", "initial_message": "Emit models",
         "owned_paths": ["modules/task_management/backend/schemas.py"], "depends_on": []},
        {"task_id": "services", "initial_agent": "ServiceAgent", "initial_message": "Emit services",
         "owned_paths": ["modules/task_management/backend/service.py"], "depends_on": ["models"]},
        {"task_id": "page", "initial_agent": "PageAgent", "initial_message": "Emit page",
         "owned_paths": ["app.json"], "depends_on": ["models", "services"]},
        {"task_id": "independent", "initial_agent": "OtherAgent", "initial_message": "Emit independent",
         "owned_paths": ["config/independent.json"], "depends_on": []},
    ]}


def _request(context, request_id="repair-1", roots=None):
    context["recovery_request"] = {
        "batch_id": "build", "request_id": request_id,
        "input_fingerprint": context["results"]["_meta"]["input_fingerprint"],
        "root_task_ids": roots or ["services"],
    }


async def _execute(context, config, checkpoints, trigger="Plan", parent_channel_id="test-parent-channel"):
    async def checkpoint(updates):
        checkpoints.append(copy.deepcopy(updates))

    return await tb.execute_task_batches_for_trigger(
        workflow_name="RecoveryTest", trigger_agent=trigger, batches_config=config,
        agents={name: object() for name in ("ModelAgent", "ServiceAgent", "PageAgent", "OtherAgent")},
        context_variables=context, fresh_agents_per_task=False, checkpoint=checkpoint,
        chat_id="chat", app_id="app", parent_channel_id=parent_channel_id,
    )


def _runner(monkeypatch, checkpoints, *, repair=True, interrupted=False, failure_status=None):
    calls = []
    counts = Counter()

    async def run(self, request):
        calls.append(request)
        counts[request.task_id] += 1
        # The persisted reservation must precede the actual AG2 call.
        latest = checkpoints[-1]["results"]["_meta"]
        assert latest["in_flight"][request.task_id]["attempt"] == counts[request.task_id]
        task = request.context_variables["current_task"]
        deps = request.context_variables["dependency_task_outputs"]
        assert set(deps) == set(task["depends_on"])
        if request.task_id == "services":
            assert "schemas.py" in deps["models"]["code_files"][0]["filename"]
            if counts[request.task_id] == 2 and interrupted:
                raise asyncio.CancelledError()
            if failure_status:
                return AG2TaskBatchRunnerResult(status=failure_status, error="worker execution failed")
        output = {"code_files": [{"filename": path, "content": "{}"}
                                  for path in task["owned_paths"]]}
        if request.task_id == "services" and (not repair or counts["services"] == 1):
            output["code_files"].append({"filename": "modules/task_management/backend/schemas.py", "content": "bad"})
        return AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output=output)

    monkeypatch.setattr(tb.AG2TaskBatchRunner, "run", run)
    return calls, counts


@pytest.mark.asyncio
async def test_recovery_releases_drained_work_and_preserves_accepted_outputs(monkeypatch):
    context, checkpoints, config = _context(), [], _config()
    calls, counts = _runner(monkeypatch, checkpoints)
    await _execute(context, config, checkpoints)
    results = context["results"]
    assert context["status"] == "partial"
    rejection = results["_failed"]["services"]
    assert rejection["failure_kind"] == "output_rejected"
    assert "outside owned_paths" in rejection["error"]
    assert rejection["attempts"] == 1 and rejection["recoverable"] is True
    assert rejection["rejected_output"]["code_files"][-1]["content"] == "bad"
    assert results["_failed"]["page"]["failure_kind"] == "dependency_blocked"
    assert results["_failed"]["page"]["blocked_by"] == ["services"]
    retained = {task_id: copy.deepcopy(results[task_id]) for task_id in ("models", "independent")}

    _request(context)
    await _execute(context, config, checkpoints, "Validate")

    assert context["status"] == "completed"
    assert context["recovery_result"]["status"] == "completed"
    assert context["recovery_status"] == "completed"
    assert context["recovery_result"]["recovered_tasks"] == ["page", "services"]
    assert counts == {"models": 1, "services": 2, "page": 1, "independent": 1}
    assert {task_id: context["results"][task_id] for task_id in retained} == retained
    assert context["results"]["_meta"]["in_flight"] == {}
    assert context["results"]["_meta"]["failure_history"]["services"][0] == rejection
    repaired = [request for request in calls if request.task_id == "services"][1]
    assert "outside owned_paths" in repaired.prompt
    assert json.dumps(rejection["rejected_output"], separators=(",", ":")) in repaired.prompt
    assert len(repaired.context_variables["decomposition_plan"]["tasks"]) == 4
    await _execute(context, config, checkpoints, "Validate")
    assert context["recovery_result"]["status"] == "blocked"
    assert context["recovery_result"]["blocked_reasons"] == ["recovery request has already been consumed"]
    assert counts == {"models": 1, "services": 2, "page": 1, "independent": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("retries", [0, 1, 3])
async def test_exhausted_or_unchanged_correction_never_restarts(monkeypatch, retries):
    context, checkpoints, config = _context(), [], _config(retries)
    _, counts = _runner(monkeypatch, checkpoints, repair=False)
    await _execute(context, config, checkpoints)
    _request(context)
    await _execute(context, config, checkpoints, "Validate")
    _request(context, "repair-2")
    await _execute(context, config, checkpoints, "Validate")
    assert counts["services"] == min(2, retries + 1)
    assert counts["page"] == 0
    assert context["recovery_result"]["status"] == "blocked"
    assert context["results"]["_failed"]["services"]["recoverable"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["root_error", "accepted_output", "approved_plan", "dependency", "historical", "request_scope"])
async def test_missing_or_incompatible_evidence_blocks_without_worker_calls(monkeypatch, damage):
    context, checkpoints, config = _context(), [], _config()
    _, counts = _runner(monkeypatch, checkpoints)
    await _execute(context, config, checkpoints)
    _request(context)
    if damage == "root_error":
        context["results"]["_failed"]["services"]["error"] = ""
    elif damage == "accepted_output":
        context["results"]["models"]["code_files"][0]["content"] = "modified"
    elif damage == "approved_plan":
        context["approved_plan"]["revision"] = 2
    elif damage == "dependency":
        context["tasks"][1]["depends_on"] = []
    elif damage == "historical":
        context["results"]["_meta"].pop("evidence_version")
    else:
        context["recovery_request"]["root_task_ids"] = ["models"]
    original_counts = counts.copy()
    await _execute(context, config, checkpoints, "Validate")
    assert counts == original_counts
    assert context["recovery_result"]["status"] == "blocked"


@pytest.mark.asyncio
async def test_interrupted_recovery_preserves_checkpoint_and_blocks_replay(monkeypatch):
    context, checkpoints, config = _context(), [], _config()
    _, counts = _runner(monkeypatch, checkpoints, interrupted=True)
    await _execute(context, config, checkpoints)
    preserved = copy.deepcopy(context["results"]["models"])
    _request(context)
    with pytest.raises(asyncio.CancelledError):
        await _execute(context, config, checkpoints, "Validate")
    assert checkpoints[-1]["results"]["_meta"]["in_flight"]["services"]["attempt"] == 2
    assert checkpoints[-1]["results"]["_failed"]["services"]["failure_kind"] == "interrupted"
    # Rehydrate exactly the context update committed before the interrupted call.
    context.update(copy.deepcopy(checkpoints[-1]))
    await _execute(context, config, checkpoints, "Validate")
    assert counts["services"] == 2
    assert context["recovery_result"]["status"] == "blocked"
    assert "uncertain" in context["recovery_result"]["blocked_reasons"][0]
    assert context["results"]["models"] == preserved


@pytest.mark.asyncio
async def test_runtime_failures_are_not_artifact_corrections(monkeypatch):
    context, checkpoints, config = _context(), [], _config(1)
    _, counts = _runner(monkeypatch, checkpoints, failure_status=RunStatus.FAILED)
    await _execute(context, config, checkpoints)
    failure = context["results"]["_failed"]["services"]
    assert failure["failure_kind"] == "execution_failed"
    assert failure["recoverable"] is False
    _request(context)
    await _execute(context, config, checkpoints, "Validate")
    assert counts["services"] == 2
    assert context["recovery_result"]["status"] == "blocked"


@pytest.mark.asyncio
async def test_initial_dispatch_cannot_replay_persisted_work(monkeypatch):
    context, checkpoints, config = _context(), [], _config()
    _, counts = _runner(monkeypatch, checkpoints)
    await _execute(context, config, checkpoints)
    before = counts.copy()
    with pytest.raises(ValueError, match="initial dispatch cannot replay"):
        await _execute(context, config, checkpoints)
    assert counts == before


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_channel_id", [None, "another-channel"])
async def test_recovery_cannot_reuse_evidence_without_original_parent_channel(monkeypatch, parent_channel_id):
    context, checkpoints, config = _context(), [], _config()
    _, counts = _runner(monkeypatch, checkpoints)
    await _execute(context, config, checkpoints)
    _request(context)
    before = counts.copy()
    await _execute(context, config, checkpoints, "Validate", parent_channel_id=parent_channel_id)
    assert counts == before
    assert context["recovery_status"] == "blocked"
    assert "parent AG2 channel lineage" in context["recovery_result"]["blocked_reasons"][0]


@pytest.mark.asyncio
async def test_initial_recoverable_batch_requires_trusted_parent_channel(monkeypatch):
    context, checkpoints, config = _context(), [], _config()
    _, counts = _runner(monkeypatch, checkpoints)
    with pytest.raises(ValueError, match="parent AG2 channel lineage"):
        await _execute(context, config, checkpoints, parent_channel_id=None)
    assert not counts


def test_embedded_worker_context_cannot_replace_dependency_evidence():
    context, config = _context(), _config()
    task = context["tasks"][1]
    task["context_variables"] = {"dependency_task_outputs": {"fake": {}}, "current_task_id": "fake"}
    accepted = {"models": {"code_files": [{"filename": "models.py", "content": "accepted"}]}}
    projected = tb._build_task_context(
        base_context=context, task=task, all_task_items=context["tasks"], batch=config.batches[0],
        completed_task_outputs=accepted, current_batch_outputs={}, chat_id=None, app_id=None, user_id=None,
    )
    assert projected["current_task_id"] == "services"
    assert projected["dependency_task_outputs"] == accepted
    projected["dependency_task_outputs"]["models"]["code_files"][0]["content"] = "changed"
    assert accepted["models"]["code_files"][0]["content"] == "accepted"


@pytest.mark.asyncio
async def test_absent_recovery_request_clears_stale_completed_routing(monkeypatch):
    context, checkpoints, config = _context(), [], _config()
    _runner(monkeypatch, checkpoints)
    await _execute(context, config, checkpoints)
    _request(context)
    await _execute(context, config, checkpoints, "Validate")
    context["recovery_request"] = None
    await _execute(context, config, checkpoints, "Validate")
    assert context["recovery_status"] == "idle"
    assert context["recovery_result"] == {}


@pytest.mark.asyncio
async def test_partial_recovery_routes_new_accepted_outputs_to_assembly(monkeypatch):
    context, checkpoints, config = _context(), [], _config(1)
    _, counts = _runner(monkeypatch, checkpoints)
    original_run = tb.AG2TaskBatchRunner.run

    async def fail_newly_unblocked_page(self, request):
        result = await original_run(self, request)
        if request.task_id == "page":
            result.output = {"code_files": [{"filename": "wrong.json", "content": "{}"}]}
        return result

    monkeypatch.setattr(tb.AG2TaskBatchRunner, "run", fail_newly_unblocked_page)
    await _execute(context, config, checkpoints)
    _request(context)
    await _execute(context, config, checkpoints, "Validate")
    assert context["status"] == "partial"
    assert context["recovery_result"]["status"] == "blocked"
    assert context["recovery_result"]["recovered_tasks"] == ["services"]
    assert context["recovery_status"] == "partial"
    assert context["results"]["_failed"]["page"]["recoverable"] is False
    assert counts["services"] == 2 and counts["page"] == 2

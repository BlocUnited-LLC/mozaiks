"""Replay deterministic artifact candidates through the real task admission boundary."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from mozaiksai.core.adapters.ag2_task_batch_runner import (
    AG2TaskBatchRunner,
    AG2TaskBatchRunnerResult,
)
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow.task_batches import (
    execute_task_batches_for_trigger,
    load_task_batches_config,
)


async def execute_file_replay(context_values: dict, files: dict[str, str], *, task_outputs: dict | None = None) -> dict:
    tasks = context_values["app_task_batch_items"]
    supplied = task_outputs or {}
    assert not set(supplied).difference(task["task_id"] for task in tasks)
    context_values["build_task_model"] = "AppBuildTask"
    config = load_task_batches_config(
        "AppGenerator", workflows_root=Path(__file__).resolve().parents[1] / "factory_app/workflows",
    )
    assert config is not None
    seen, checkpoints = [], []

    async def run(_self, request):
        task = request.context_variables["current_build_task"]
        assert set(request.context_variables["dependency_task_outputs"]) == set(task["depends_on"])
        seen.append(task["task_id"])
        output = deepcopy(supplied[task["task_id"]]) if task["task_id"] in supplied else {
            "code_files": [{"filename": path, "content": files[path]} for path in task["owned_paths"]],
        }
        return AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output=output)

    async def checkpoint(updates):
        checkpoints.append(deepcopy(updates))

    with patch.object(AG2TaskBatchRunner, "run", run):
        await execute_task_batches_for_trigger(
            workflow_name="AppGenerator", trigger_agent="AppPlanAgent", batches_config=config,
            agents={task["initial_agent"]: object() for task in tasks}, context_variables=context_values,
            chat_id="offline-artifact-replay", app_id=context_values["app_id"], user_id="offline-user",
            fresh_agents_per_task=False, checkpoint=checkpoint, parent_channel_id="offline-parent-channel",
        )
    results = context_values["app_task_batch_results"]
    assert context_values["app_task_batch_status"] == "completed", results.get("_failed")
    assert set(seen) == {task["task_id"] for task in tasks}
    assert len(seen) == len(tasks)
    assert not results.get("_failed")
    assert checkpoints[-1]["app_task_batch_results"]["_meta"]["in_flight"] == {}
    return deepcopy(results)

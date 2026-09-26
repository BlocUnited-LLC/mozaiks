"""Repaired plan identities survive execution projection and prerequisite delivery."""

from pathlib import Path

from factory_app.workflows.AppGenerator.tools.task_integrity import approved_task_inventory
from mozaiksai.core.workflow import task_batches as tb
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from tests.test_app_plan_task_identity_repair import _live_plan, _review
from tests.test_continuous_deterministic_materialization import _load_models


def test_repaired_module_tasks_receive_their_own_accepted_contract_outputs():
    _load_models()
    plan, context = _live_plan()
    assert isinstance(context, ContextVariablesBridge)
    cached = _review(plan, context)
    queued = detach(context.get("app_task_batch_items"))
    batch = tb.load_task_batches_config(
        "AppGenerator", workflows_root=Path(__file__).resolve().parents[1] / "factory_app/workflows",
    ).batches[0]

    completed = {}
    for task in queued:
        if task["task_type"] in {"business_services", "page_bundle"}:
            continue
        output = {"code_files": [
            {"filename": path, "content": f"# accepted output from {task['task_id']}\n"}
            for path in task["owned_paths"]
        ]}
        tb._stamp_task_output_identity(task, output)
        completed[task["task_id"]] = output
    collected = tb._build_batch_outputs(
        batch=batch, completed=completed, failed={}, task_count=len(queued), status="running",
    )
    context.set(batch.result.context_key, collected)
    assert len(collected["_meta"]["completed_tasks"]) == len(completed)
    assert approved_task_inventory(context) == cached["build_tasks"]

    for module_id in ("task_management", "billing_portal"):
        task = next(item for item in queued if (
            item["capability_pack_id"] == module_id and item["task_type"] == "business_services"
        ))
        worker = tb._build_task_context(
            base_context=context.to_dict(), task=task, all_task_items=queued, batch=batch,
            completed_task_outputs=completed, current_batch_outputs=collected,
            chat_id="task-identity-consumer", app_id="task-identity-app", user_id="test-user",
        )
        assert worker["current_task_id"] == worker["current_build_task_id"] == task["task_id"]
        assert worker["current_build_task_type"] == "business_services"
        assert worker["current_task_id"] != worker["current_build_task_type"]
        assert worker["current_build_task"]["task_id"] == task["task_id"]
        prerequisite_outputs = worker["dependency_task_outputs"]
        assert set(prerequisite_outputs) == set(task["depends_on"])
        for kind in ("module_contract", "data_models"):
            dependency_id = f"{module_id}.{kind}"
            assert prerequisite_outputs[dependency_id]["task_id"] == dependency_id
            assert prerequisite_outputs[dependency_id] == collected[dependency_id]
        foreign = "billing_portal" if module_id == "task_management" else "task_management"
        assert not any(identifier.startswith(f"{foreign}.") for identifier in prerequisite_outputs)

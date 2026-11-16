from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_build_plan import _normalize_context_variables
from mozaiksai.core.workflow.context.authority import (
    ContextAuthorityError,
    build_context_authority_policy,
)
from mozaiksai.core.workflow.task_batches import (
    _build_task_context,
    _execute_one_batch,
    parse_task_batches_config,
)


def _build(task, *, policy=None, fields=(), base=None):
    batch = SimpleNamespace(
        id="app_build_tasks", worker=SimpleNamespace(context_fields=fields),
        execution=SimpleNamespace(dependency_field="depends_on"),
        result=SimpleNamespace(context_key="results", status_key="results_status"),
    )
    return _build_task_context(
        base_context=base or {}, task={"task_id": "task-1", **task}, all_task_items=[],
        batch=batch, completed_task_outputs={}, current_batch_outputs={},
        chat_id="chat", app_id="factory", user_id="owner", context_authority_policy=policy,
    )


@pytest.mark.parametrize("key", ["run_build_binding", "app_id", "user_id", "workspace_id"])
@pytest.mark.parametrize("use_fields", [True, False])
def test_task_data_cannot_replace_runtime_identity_without_declarations(key, use_fields):
    task = {key: "foreign"} if use_fields else {"context_variables": {key: "foreign"}}
    with pytest.raises(ContextAuthorityError, match=key):
        _build(task, fields=(key,) if use_fields else ())


@pytest.mark.parametrize("key", ["context_graph_pack", "context_graph_catalog", "revision_source_artifact_version_id"])
def test_app_plan_task_context_cannot_replace_lifecycle_owned_evidence(key):
    root = Path(__file__).resolve().parents[1]
    declarations = yaml.safe_load((root / "factory_app/workflows/AppGenerator/context_variables.yaml").read_text(encoding="utf-8"))
    policy = build_context_authority_policy(workflow_name="AppGenerator", definitions=declarations["definitions"])
    # This is the real normalization from schema-valid AppContextVariable task items.
    embedded = _normalize_context_variables([{"key": key, "value_type": "json", "value": '{"forged":true}'}])
    with pytest.raises(ContextAuthorityError, match=key):
        _build({"context_variables": embedded}, policy=policy)


def test_task_context_retains_server_evidence_and_accepts_ordinary_local_inputs():
    policy = build_context_authority_policy(workflow_name="Test", definitions={
        "context_graph_pack": {"type": "object", "authority_class": "tool_only_information", "writer_ids": ["lifecycle_tool"]},
        "prompt_hint": {"type": "string", "source": {"type": "state"}},
    })
    evidence = {"build_record_id": "baseline", "recommended_paths": ["modules/tasks/backend/service.py"]}
    base = {"context_graph_pack": evidence, "run_build_binding": {"target_app_id": "customer"}}
    result = _build({"context_variables": {"prompt_hint": "list tasks", "local_mode": "compact"}, "task_type": "service"},
                    fields=("task_type",), policy=policy, base=base)
    assert result["context_graph_pack"] == evidence
    assert result["context_graph_pack"] is not evidence
    assert result["run_build_binding"] == base["run_build_binding"]
    assert (result["prompt_hint"], result["local_mode"], result["task_type"]) == ("list tasks", "compact", "service")
    assert (result["app_id"], result["user_id"], result["current_task_id"]) == ("factory", "owner", "task-1")


@pytest.mark.asyncio
async def test_protected_task_input_uses_existing_task_failure_boundary():
    batch = parse_task_batches_config({"version": 1, "batches": [{
        "id": "tasks", "trigger_agent": "Planner",
        "source": {"kind": "context_variable", "path": "tasks", "task_model": "Task"},
        "execution": {"failure_policy": "continue_with_available"},
        "result": {"context_key": "results", "status_key": "results_status"},
    }]}).batches[0]
    task = {"task_id": "task-1", "initial_agent": "Worker", "initial_message": "work", "context_variables": {"run_build_binding": {"target_app_id": "foreign"}}}
    result = await _execute_one_batch(
        workflow_name="AppGenerator", batch=batch, task_items=[task], agents={},
        context_variables={}, chat_id=None, app_id="factory", user_id="owner", wf_logger=None,
        fresh_agents_per_task=False, agents_factory=None, context_authority_policy=None,
    )
    assert result["status"] == "partial"
    assert result["failed_tasks"] == ["task-1"]
    assert result["completed_tasks"] == []
    assert "context_authority.rejected" in str(result["outputs"])

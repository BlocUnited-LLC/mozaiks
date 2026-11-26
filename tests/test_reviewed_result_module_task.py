import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools import reviewed_result_module_task as compiler
from mozaiksai.core.workflow.generator_support.module_revision import RetainedAskRead
from tests.test_revision_result_module_coverage import _case, _design


def _setup(tmp_path, monkeypatch):
    ctx, files = _case(tmp_path)
    module = "modules/tasks/"
    manifest = module + "module.yaml"
    document = yaml.safe_load(files[manifest])
    document["permissions"] = []
    create = document["actions"][0]
    create.update(id="create_task", permissions=[], input_schema={
        "type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"], "additionalProperties": False,
    })
    for capability in document["capabilities"]:
        capability["permissions"] = []
        if capability["kind"] == "action":
            capability["target"] = "create_task"
    read = {"id": "metrics", "description": "Read metrics", "handler_method": "metrics",
            "input_schema": {}, "output_schema": {}, "permissions": [], "ask_context_safe": True}
    document["actions"] = [create, read]
    files[manifest] = yaml.safe_dump(document)
    design = ctx["design_surface_map"]
    design["surfaces"][0].update(owned_mutations=["create_task", "update_task"], primary_entities=["Task"], custom_reads=["metrics"])
    data = ctx["data_contract"]
    old = json.loads(files["data/contract.json"])
    old["surfaces"][0]["collections"][0]["fields"] = [f for f in data["surfaces"][0]["collections"][0]["fields"] if f["name"] != "summary"]
    files["data/contract.json"] = json.dumps(old)
    _design(ctx, data, design)
    pack = {"surface_id": "tasks", "capability_pack_id": "tasks", "capability_source": "generated_module"}
    row = {"task_id": "contract", "task_type": "module_contract", "capability_pack_id": "tasks",
           "surface_kind": "module", "surface_id": "tasks", "initial_agent": "ConfigMiddlewareAgent",
           "owned_paths": [manifest, *(module + f"contracts/{suffix}.yaml" for suffix in ("events", "reactions", "admin"))]}
    task = {**deepcopy(row), "current_build_task": deepcopy(row)}
    hook = ctx["workflow_integration_metadata"]["workflows"][0]
    event = hook["result_write_plans"][0]["event_type"]
    flow = {"producer_pack_id": "tasks", "event_type": event, "producing_action": "create_task",
            "subscriber_intents": ["workflow"], "workflow_capability_ids": [hook["capability_id"]]}
    ctx.update(revision_source_artifact_version_id="original", user_id="owner", app_task_batch_items=[deepcopy(task)],
               app_build_plan={"build_tasks": [row], "capability_packs": [pack], "event_flows": [flow]}, generated_files=deepcopy(files))
    ctx["impact_set"]["affected_bundle_paths"] = ["modules/*/module.yaml", "data/contract.json", "ui/pages/*.yaml"]
    ctx["workflow_integration_metadata"]["workflows"][0]["result_write_plans"][0]["result_write"]["source_action"] = "create_task"
    ctx["upstream_workflow_bundle_artifact_version_id"] = ctx["workflow_bundle_artifact_version_id"]
    prior = deepcopy(ctx)
    prior["design_surface_map"]["surfaces"][0]["owned_mutations"] = ["create_task"]
    _design(prior, old, prior["design_surface_map"])
    record = SimpleNamespace(id="original", app_id="generated-app", commit_metadata=SimpleNamespace(metadata={"build_id": "v1"}))
    lookup = AsyncMock(return_value=(record, files))
    monkeypatch.setattr(compiler, "read_revision_source", lookup)
    monkeypatch.setattr(compiler, "resolve_app_compilation_inputs", AsyncMock(return_value=SimpleNamespace(context_for=lambda app: prior)))
    ctx["revision_result_module_coverage"] = compiler.revision_result_module_coverage(ctx, files)
    return task, ctx, files, prior, {"metrics": RetainedAskRead("ui/pages/report.yaml", read, True, "v1")}, lookup


async def test_verified_compiler_preserves_source_and_leaves_an_untouched_optional_admin_companion(tmp_path, monkeypatch):
    task, ctx, files, _, callers, lookup = _setup(tmp_path, monkeypatch)
    candidate = await compiler.reviewed_result_module_task_output(task, ctx, retained_ask_callers=callers)
    lookup.assert_awaited_once_with(ctx, "original", require_accepted=True)
    assert candidate["module_contract"]["module_yaml"] == yaml.safe_load(files["modules/tasks/module.yaml"])
    assert candidate["module_contract"]["events_yaml"] == yaml.safe_load(files["modules/tasks/contracts/events.yaml"])
    assert "admin_yaml" not in candidate["module_contract"]
    assert "code_files" not in candidate


@pytest.mark.parametrize("fault", ["new_custom", "unproven_read", "new_field", "changed_scope", "admin_requested",
                                   "selected_companion", "new_subscriber", "new_companion", "access", "prior_missing",
                                   "overlay", "new_integration"])
async def test_discretionary_module_work_uses_the_ordinary_worker(tmp_path, monkeypatch, fault):
    task, ctx, files, prior, callers, _ = _setup(tmp_path, monkeypatch)
    if fault == "new_custom":
        ctx["design_surface_map"]["surfaces"][0]["custom_reads"].append("new_metrics")
        _design(ctx, ctx["data_contract"], ctx["design_surface_map"])
    elif fault == "unproven_read":
        callers.clear()
    elif fault == "new_field":
        ctx["data_contract"]["surfaces"][0]["collections"][0]["fields"].append({"name": "unrelated", "type": "string"})
        _design(ctx, ctx["data_contract"], ctx["design_surface_map"])
    elif fault == "changed_scope":
        ctx["design_surface_map"]["surfaces"][0]["primary_entities"].append("ArchivedTask")
        _design(ctx, ctx["data_contract"], ctx["design_surface_map"])
    elif fault == "admin_requested":
        ctx["impact_set"]["affected_bundle_paths"].append("modules/*/contracts/admin.yaml")
    elif fault == "selected_companion":
        ctx["refinement_request_meta"] = {"extra": {"selected_file_paths": ["modules/tasks/contracts/notifications.yaml"]}}
    elif fault == "new_subscriber":
        ctx["app_build_plan"]["event_flows"][0]["subscriber_intents"].append("notification")
    elif fault == "new_companion":
        del files["modules/tasks/contracts/admin.yaml"]
    elif fault == "access":
        prior["subscription_contract"] = {"contract_required": True}
    elif fault == "prior_missing":
        monkeypatch.setattr(compiler, "resolve_app_compilation_inputs", AsyncMock(return_value=None))
    elif fault == "overlay":
        ctx["generated_files"]["modules/tasks/module.yaml"] = "superseding edit"
    else:
        ctx["design_surface_map"]["surfaces"][0]["integrations"] = ["calendar"]
        _design(ctx, ctx["data_contract"], ctx["design_surface_map"])
    assert await compiler.reviewed_result_module_task_output(task, ctx, retained_ask_callers=callers) is None


@pytest.mark.parametrize("fault", ["registered", "task", "coverage"])
async def test_contradictory_source_or_dispatch_fails_closed(tmp_path, monkeypatch, fault):
    task, ctx, _, _, callers, _ = _setup(tmp_path, monkeypatch)
    if fault == "registered":
        ctx["app_build_plan"]["build_tasks"][0]["owned_paths"] = []
    elif fault == "task":
        task["owned_paths"].append("config/auth.yaml")
    else:
        ctx["revision_result_module_coverage"]["tasks"]["business_services"] = False
    with pytest.raises(ValueError, match="exact registered dispatch|coverage changed"):
        await compiler.reviewed_result_module_task_output(task, ctx, retained_ask_callers=callers)

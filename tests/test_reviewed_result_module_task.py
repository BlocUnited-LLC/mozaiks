import hashlib
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
    # This fixture uses the canonical collection create action and its emitted event.
    from tests.test_factory_workflow_result_generation import EVENT

    ctx = json.loads(json.dumps(ctx).replace(EVENT, "domain.task.created"))
    files = {path: text.replace(EVENT, "domain.task.created") for path, text in files.items()}
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
    pack = {"surface_id": "tasks", "capability_pack_id": "tasks", "capability_source": "generated_module",
            "primary_entities": ["Task"], "surface_kind": "module"}
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
    source = await compiler.reviewed_result_module_task_output(task, ctx, retained_ask_callers=callers)
    candidate = source.candidate
    lookup.assert_awaited_once_with(ctx, "original", require_accepted=True)
    assert candidate["module_contract"]["module_yaml"] == yaml.safe_load(files["modules/tasks/module.yaml"])
    assert candidate["module_contract"]["events_yaml"] == yaml.safe_load(files["modules/tasks/contracts/events.yaml"])
    assert "admin_yaml" not in candidate["module_contract"]
    assert "code_files" not in candidate
    assert source.accepted_module_ids == ["tasks"]
    assert source.source_build_id == "v1"
    assert source.absent_optional_paths == []
    admin = next(r for r in source.retained_companions if r["path"] == "modules/tasks/contracts/admin.yaml")
    assert admin["document"] == yaml.safe_load(files[admin["path"]])
    assert admin["sha256"] == hashlib.sha256(files[admin["path"]].encode()).hexdigest()


@pytest.mark.parametrize("requirement", ["reservation", "unrelated_manifest", "declared_manifest", "unknown_manifest",
                                         "selected", "affected", "overlay", "deleted", "integration", "additional_subscriber"])
async def test_source_omits_only_unrequested_optional_reservations(tmp_path, monkeypatch, requirement):
    task, ctx, files, _, callers, _ = _setup(tmp_path, monkeypatch)
    extras = ["modules/tasks/contracts/policy_hooks.yaml", "modules/tasks/contracts/relationships.yaml",
              "modules/tasks/runtime_extensions.yaml"]
    for path in extras:
        files.pop(path, None)
        ctx["generated_files"].pop(path, None)
    task["owned_paths"].extend(extras)
    if requirement in {"unrelated_manifest", "declared_manifest", "unknown_manifest"}:
        task["file_manifest"] = {"yaml_files": [{"unrelated_manifest": "events.yaml", "declared_manifest": "policy_hooks.yaml",
                                                "unknown_manifest": "unknown.yaml"}[requirement]]}
    elif requirement == "selected":
        ctx["refinement_request_meta"] = {"extra": {"selected_file_paths": extras[:1]}}
    elif requirement == "affected":
        ctx["impact_set"]["affected_bundle_paths"].append("modules/*/contracts/policy_hooks.yaml")
    elif requirement == "overlay":
        ctx["generated_files"][extras[0]] = "{}"
    elif requirement == "deleted":
        ctx["deleted_files"] = extras[:1]
    elif requirement == "integration":
        task["integration_needs"] = ["integration"]
    elif requirement == "additional_subscriber":
        ctx["app_build_plan"]["event_flows"][0]["subscriber_intents"].append("notification")
    plan_row = {k: deepcopy(v) for k, v in task.items() if k != "current_build_task"}
    task["current_build_task"] = deepcopy(plan_row)
    ctx["app_build_plan"]["build_tasks"] = [plan_row]
    ctx["app_task_batch_items"] = [deepcopy(task)]
    source = await compiler.reviewed_result_module_task_output(task, ctx, retained_ask_callers=callers)
    if requirement in {"reservation", "unrelated_manifest"}:
        assert source is not None and source.absent_optional_paths == extras
        assert not set(extras).intersection(r["path"] for r in source.retained_companions)
    else:
        assert source is None


@pytest.mark.parametrize("fault", ["new_custom", "unproven_read", "new_field", "changed_scope", "admin_requested",
                                   "selected_companion", "new_subscriber", "new_companion", "access", "prior_missing",
                                   "overlay", "oversize_companion", "new_integration"])
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
    elif fault == "oversize_companion":
        path = "modules/tasks/contracts/admin.yaml"
        files[path] += "\n# " + "é" * 16_000
        ctx["generated_files"][path] = files[path]
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

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools import hydrate_app_revision_context as hydration
from factory_app.workflows.AppGenerator.tools.revision_result_coverage import (
    revision_result_module_coverage,
)
from factory_app.workflows.AppGenerator.tools.workflow_result_writes import workflow_result_files
from mozaiksai.core.workflow.generator_support.design_access_policy import (
    access_policy_sha256,
    explicit_module_writes,
)
from tests.factory_context import factory_context
from tests.test_factory_workflow_result_generation import EVENT, WRITE, app_files, workflow_context


def _design(ctx, data, surfaces):
    policy = {"roles": [], "grants": []}
    binding = ctx["run_build_binding"]
    receipt = {
        "status": "approved", "review_id": "approval", "reviewed_by": "owner", "build_id": binding["build_id"],
        "target_app_id": binding["target_app_id"],
        "policy_sha256": access_policy_sha256(policy, None, module_writes=explicit_module_writes(data)),
    }
    summary = {"data_contract": deepcopy(data), "surface_map": deepcopy(surfaces),
               "access_policy": policy, "access_policy_review": receipt}
    ctx.update(data_contract=data, design_surface_map=surfaces, design_docs_build_artifact={
        "app_id": binding["target_app_id"], "build_family": "design_docs", "build_key": "design_docs",
        "source_workflow": "DesignDocs", "lifecycle_status": "draft",
        "commit_metadata": {"author_user_id": "owner", "metadata": {"summary_payload": summary}},
    })


def _case(tmp_path):
    ctx = factory_context(workflow_context())
    ctx.update(build_mode="revision", workflow_sequence="design_revision", revision_scope="feature",
               change_intent={"requires_workflow_revision": True}, impact_set={
                   "workflow_sequence": "design_revision", "requires_rebuild": True, "affected_workflows": ["AppGenerator"],
               })
    files = app_files(tmp_path)
    data = json.loads(files["data/contract.json"])
    coll = data["surfaces"][0]["collections"][0]
    coll["fields"].append({"name": "title", "type": "string", "required": True})
    coll["lifecycle"] = {"write_mode": "module_action", "module_writes": [{"operation": k, "fields": ["title"]} for k in ("create", "update")]}
    surfaces = {"surfaces": [
        {"surface_id": "tasks", "owner": "app", "surface_kind": "module",
         "owned_mutations": ["create", "create_task", "update_task"], "events_emitted": [EVENT]},
        {"surface_id": "ReviewWorkflow", "owner": "app", "surface_kind": "workflow", "workflow_triggers": [EVENT]},
    ]}
    _design(ctx, data, surfaces)
    return ctx, files


def test_reviewed_result_requires_the_actual_missing_owners(tmp_path):
    ctx, files = _case(tmp_path)
    ctx["generated_files"] = {"data/contract.json": json.dumps(ctx["data_contract"])}
    ctx["revision_result_module_coverage"] = {}
    assert revision_result_module_coverage(ctx, files) == {
        "tasks": {"module_contract": True, "data_models": True, "business_services": True},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("corrupt", [False, True])
async def test_hydration_verifies_coverage_before_overlay_and_clears_a_failed_recheck(tmp_path, monkeypatch, corrupt):
    ctx, files = _case(tmp_path)
    artifact = SimpleNamespace(id="accepted", build_family="app_bundle", build_key="app_bundle", validation_status="passed",
                               lifecycle_status="current", commit_metadata=SimpleNamespace(metadata={}))
    monkeypatch.setattr(hydration, "design_revision_source", AsyncMock(return_value=artifact.id))
    monkeypatch.setattr(hydration, "read_revision_source", AsyncMock(return_value=(artifact, files)))
    ctx.update(generated_files={"modules/tasks/module.yaml": "unverified draft overlay"}, app_plan_ready=True,
               app_build_plan={"cached": True}, app_task_batch_items=[{"task_id": "previous"}], revision_result_module_coverage={})
    if corrupt:
        ctx["workflow_bundle_artifact_version_id"] = "other-workflow"
        with pytest.raises(ValueError, match="registered workflow bundle"):
            await hydration.hydrate_app_revision_context(ctx)
        assert ctx["revision_result_module_coverage"] is None
        assert ctx["app_task_batch_items"] == [] and ctx["app_plan_ready"] is False
    else:
        result = await hydration.hydrate_app_revision_context(ctx)
        assert result["artifact_version_id"] == "accepted"
        assert ctx["revision_result_module_coverage"] == {
            "tasks": {"module_contract": True, "data_models": True, "business_services": True},
        }
        assert ctx["generated_files"]["modules/tasks/module.yaml"] == "unverified draft overlay"


def test_custom_source_does_not_assign_unreviewed_public_edits_or_worker_owned_callbacks(tmp_path):
    ctx, files = _case(tmp_path)
    _design(ctx, json.loads(files["data/contract.json"]), ctx["design_surface_map"])
    assert revision_result_module_coverage(ctx, files) == {
        "tasks": {"module_contract": True, "data_models": False, "business_services": False},
    }
    files.update(workflow_result_files(files, ctx["workflow_integration_metadata"]))
    assert revision_result_module_coverage(ctx, files) == {}


@pytest.mark.parametrize(("fault", "expected"), [
    ("design", "approved design does not match"), ("workflow", "registered workflow bundle"),
    ("owner", "app-owned design"), ("event", "source event"), ("reaction", "unreviewed event"),
    ("receiver", "accepted receiver"), ("public", "reviewed public write"), ("record", "accepted record identity"),
    ("scope", "rebuilds the app"), ("field", "private to its receiver"),
])
def test_result_coverage_rejects_mismatched_review_or_accepted_binding(tmp_path, fault, expected):
    ctx, files = _case(tmp_path)
    if fault == "workflow":
        ctx["workflow_bundle_artifact_version_id"] = "unrelated"
    elif fault == "design":
        ctx["data_contract"]["surfaces"][0]["collections"][0]["fields"].pop()
    elif fault == "event":
        path = "modules/tasks/contracts/events.yaml"
        payload = yaml.safe_load(files[path])
        payload["events"][0]["payload_schema"]["required"].remove("task_id")
        files[path] = yaml.safe_dump(payload)
    elif fault == "reaction":
        path = "modules/tasks/contracts/reactions.yaml"
        payload = yaml.safe_load(files[path])
        payload["reactions"][0]["event_type"] = "domain.tasks.updated"
        files[path] = yaml.safe_dump(payload)
    elif fault == "receiver":
        path = "modules/tasks/module.yaml"
        payload = yaml.safe_load(files[path])
        next(a for a in payload["actions"] if a["id"] == WRITE["action"])["api_surface"] = "public"
        files[path] = yaml.safe_dump(payload)
    elif fault == "record":
        data = json.loads(files["data/contract.json"])
        data["surfaces"][0]["collections"][0]["fields"].pop()
        files["data/contract.json"] = json.dumps(data)
    elif fault == "scope":
        ctx["impact_set"]["requires_rebuild"] = False
    else:
        surface = ctx["design_surface_map"]["surfaces"][0]
        if fault == "owner":
            surface["owner"] = "platform"
        elif fault == "field":
            ctx["data_contract"]["surfaces"][0]["collections"][0]["lifecycle"]["module_writes"][0]["fields"].append("summary")
        else:
            surface["owned_mutations"].remove("update_task")
        _design(ctx, ctx["data_contract"], ctx["design_surface_map"])
    with pytest.raises(ValueError, match=expected):
        revision_result_module_coverage(ctx, files)


def test_patch_and_unrelated_workflow_do_not_request_module_coverage(tmp_path):
    ctx, files = _case(tmp_path)
    ctx["workflow_sequence"] = "app_revision"
    assert revision_result_module_coverage(ctx, files) == {}

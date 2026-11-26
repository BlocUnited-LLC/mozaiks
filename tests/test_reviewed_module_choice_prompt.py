import json
from copy import deepcopy

import pytest

from factory_app.workflows._shared.context_graph.prompt_pack import build_context_graph_prompt_pack
from factory_app.workflows.AppGenerator.tools.reviewed_module_choice import (
    reviewed_module_choice_prompt,
)
from factory_app.workflows.AppGenerator.tools.reviewed_result_module_task import (
    reviewed_result_module_task_output,
)
from tests.test_reviewed_result_module_task import _setup


async def test_choice_binds_the_request_task_accepted_actions_graph_and_compiled_inputs(tmp_path, monkeypatch):
    task, ctx, _, _, callers, _ = _setup(tmp_path, monkeypatch)
    ctx["workflow_name"] = "AppGenerator"
    ctx["refinement_request"] = "Suggest a summary after I create a task. Let me edit its title."
    identity = {"app_id": ctx["run_build_binding"]["target_app_id"], "build_record_id": "original",
                "build_family": "app_bundle", "build_key": "app_bundle",
                "bundle_digest": "sha256:" + "a" * 64, "graph_hash": "sha256:" + "b" * 64}
    edge = {"source_path": "modules/tasks/backend/service.py", "target_path": "modules/tasks/backend/repo.py", "kind": "imports"}
    graph = build_context_graph_prompt_pack(catalog={**identity, "verified": True}, source="verified_artifact_bundle")
    graph["dependency_graph"] = {**identity, "files": [edge["source_path"], edge["target_path"]],
                                 "dependencies": [edge], "coverage_incomplete": False}
    ctx["context_graph_pack"] = graph
    task["acceptance_criteria"] = ["The title suggestion stays private."]
    ctx["app_build_plan"]["build_tasks"][0]["acceptance_criteria"] = task["acceptance_criteria"]
    task["current_build_task"]["acceptance_criteria"] = task["acceptance_criteria"]
    ctx["app_task_batch_items"] = [deepcopy(task)]
    candidate = await reviewed_result_module_task_output(task, ctx, retained_ask_callers=callers)
    assert candidate
    digest, prompt = reviewed_module_choice_prompt(ctx, task, candidate, callers)
    offer = json.loads(prompt)
    assert offer["candidate_sha256"] == digest
    assert offer["source_build_id"] == "v1" and offer["refinement_build_id"] == ctx["run_build_binding"]["build_id"]
    assert offer["source_artifact_version_id"] == "original" and offer["app_id"] == identity["app_id"]
    assert offer["refinement_request"] == ctx["refinement_request"]
    assert offer["task"]["acceptance_criteria"] == task["acceptance_criteria"]
    assert offer["accepted_action_manifests"] == candidate["module_contract"]["module_yaml"]["actions"]
    assert offer["retained_ask_callers"] == {"metrics": "ui/pages/report.yaml"}
    assert offer["compiled_public_inputs"]["update_task"] == {
        "type": "object", "properties": {"title": {"type": "string"}, "task_id": {"type": "string"}},
        "required": ["task_id"], "additionalProperties": False,
    }
    assert offer["compiled_internal_receivers"][0]["result_write"]["action"] == "save_summary"
    assert offer["carried_companion_paths"] == ["modules/tasks/contracts/admin.yaml"]
    for key in ("bundle_digest", "graph_hash"):
        assert offer["dependency_graph"][key] == identity[key]
    assert offer["dependency_graph"]["dependencies"] == [edge]
    assert "generated_files" not in offer and "context_variables" not in offer["task"]
    # The same accepted manifest must yield a different receipt if the exact source or intended edits change.
    ctx["revision_source_artifact_version_id"] = "another-source"
    assert reviewed_module_choice_prompt(ctx, task, candidate, callers)[0] != digest
    ctx["revision_source_artifact_version_id"] = "original"
    title = next(f for f in ctx["data_contract"]["surfaces"][0]["collections"][0]["fields"] if f["name"] == "title")
    title["enum"] = ["Inbox", "Work"]
    changed_digest, changed_prompt = reviewed_module_choice_prompt(ctx, task, candidate, callers)
    assert changed_digest != digest
    assert json.loads(changed_prompt)["compiled_public_inputs"]["update_task"]["properties"]["title"]["enum"] == title["enum"]


@pytest.mark.parametrize(("text", "recorded"), [("", ""), ("change its title", "keep its title"), ("x" * 4001, None)])
def test_incomplete_or_conflicting_refinement_request_keeps_the_ordinary_author(text, recorded):
    ctx = {"refinement_request": text, "refinement_request_meta": {"raw_user_request": recorded}}
    assert reviewed_module_choice_prompt(ctx, {}, {}, {}) is None


async def test_source_without_verified_build_identity_uses_the_ordinary_author(tmp_path, monkeypatch):
    task, ctx, _, _, callers, _ = _setup(tmp_path, monkeypatch)
    ctx["refinement_request"] = "Suggest summaries for new tasks."
    candidate = await reviewed_result_module_task_output(task, ctx, retained_ask_callers=callers)
    assert candidate
    ctx["source_build_id"] = ctx["run_build_binding"]["build_id"]
    assert reviewed_module_choice_prompt(ctx, task, candidate, {}) is None

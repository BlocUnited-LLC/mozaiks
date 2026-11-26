import json
from copy import deepcopy

import pytest
import yaml

from factory_app.workflows._shared.context_graph.prompt_pack import build_context_graph_prompt_pack
from factory_app.workflows.AppGenerator.tools.reviewed_module_choice import (
    reviewed_module_choice_prompt,
)
from factory_app.workflows.AppGenerator.tools.reviewed_result_module_task import (
    reviewed_result_module_task_output,
)
from mozaiksai.core.runtime.app.module_loader import ModuleSettingsManifest
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
from mozaiksai.core.workflow.task_batches import (
    _apply_task_entitlement_gates,
    _close_task_module_actions,
)
from tests.test_reviewed_result_module_task import _setup


def _compiled_files(source, task, ctx, callers):
    closed, _, subscription, results = _close_task_module_actions(source.candidate, workflow_name="AppGenerator",
                                                                 task=task, base_context=ctx, task_context=ctx,
                                                                 retained_ask_callers=callers)
    return _apply_task_entitlement_gates(extract_code_file_map_from_payload(closed), task_context=ctx, base_context=ctx,
                                         subscription_contract=subscription, result_schemas=results)


async def test_choice_binds_the_request_task_accepted_actions_graph_and_compiled_inputs(tmp_path, monkeypatch):
    task, ctx, files, _, callers, _ = _setup(tmp_path, monkeypatch)
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
    source = await reviewed_result_module_task_output(task, ctx, retained_ask_callers=callers)
    assert source
    outputs = _compiled_files(source, task, ctx, callers)
    evidence = source.compiled_evidence("tasks", outputs)
    def project():
        return reviewed_module_choice_prompt(ctx, task, source.candidate, callers, compiled_evidence=evidence)
    digest, prompt = project()
    offer = json.loads(prompt)
    assert offer["candidate_sha256"] == digest
    assert offer["source_build_id"] == "v1" and offer["refinement_build_id"] == ctx["run_build_binding"]["build_id"]
    assert offer["source_artifact_version_id"] == "original" and offer["app_id"] == identity["app_id"]
    assert offer["refinement_request"] == ctx["refinement_request"]
    assert offer["task"]["acceptance_criteria"] == task["acceptance_criteria"]
    assert offer["retained_ask_callers"] == {"metrics": "ui/pages/report.yaml"}
    rendered = offer["proposed_files"]
    assert {p: row["document"] for p, row in rendered.items()} == {p: yaml.safe_load(text) for p, text in outputs.items()}
    actions = {a["id"]: a for a in rendered["modules/tasks/module.yaml"]["document"]["actions"]}
    assert actions["metrics"]["ask_context_safe"] is True
    assert actions["update_task"]["input_schema"] == {
        "type": "object", "properties": {"title": {"type": "string"}, "task_id": {"type": "string"}},
        "required": ["task_id"], "additionalProperties": False,
    }
    assert any(d["path"] == ["actions", "update_task"] and d["absent_in_accepted"]
               for d in rendered["modules/tasks/module.yaml"]["accepted_differences"])
    assert actions["save_summary"]["api_surface"] == "internal" and actions["save_summary"]["emits"] == []
    changes = rendered["modules/tasks/contracts/reactions.yaml"]["document"]["reactions"]
    assert any(r["target"].get("result_write", {}).get("action") == "save_summary" for r in changes)
    admin = next(c for c in offer["retained_companions"] if c["path"] == "modules/tasks/contracts/admin.yaml")
    assert admin["document"] == yaml.safe_load(files[admin["path"]])
    assert offer["accepted_module_ids"] == offer["planned_module_ids"] == ["tasks"]
    assert "absent_optional_paths" not in offer
    for key in ("bundle_digest", "graph_hash"):
        assert offer["dependency_graph"][key] == identity[key]
    assert offer["dependency_graph"]["dependencies"] == [edge]
    assert "generated_files" not in offer and "context_variables" not in offer["task"]
    # A source with no Ask callers can use the compiler's verified source build. Conflicting receipts cannot replace it.
    assert reviewed_module_choice_prompt(ctx, task, source.candidate, {}, compiled_evidence=evidence)
    evidence["source_build_id"] = "different-build"
    assert project() is None
    evidence["source_build_id"] = None
    ctx["source_build_id"] = "unverified"
    assert reviewed_module_choice_prompt(ctx, task, source.candidate, {}, compiled_evidence=evidence) is None
    evidence["source_build_id"] = "v1"
    # An old compiled proof cannot be offered after the selected source changes.
    ctx["revision_source_artifact_version_id"] = "another-source"
    assert project() is None
    ctx["revision_source_artifact_version_id"] = "original"
    # A changed proposed output under the same action ID must reach the decision, together with the old field shape.
    changed = yaml.safe_load(outputs["modules/tasks/module.yaml"])
    next(a for a in changed["actions"] if a["id"] == "metrics")["output_schema"] = {
        "type": "object", "properties": {"total": {"type": "integer"}}, "required": ["total"],
    }
    evidence = source.compiled_evidence("tasks", {**outputs, "modules/tasks/module.yaml": yaml.safe_dump(changed)})
    changed_digest, changed_prompt = project()
    assert changed_digest != digest
    changed_module = json.loads(changed_prompt)["proposed_files"]["modules/tasks/module.yaml"]
    assert any(a["id"] == "metrics" and a["output_schema"]["required"] == ["total"] for a in changed_module["document"]["actions"])
    assert any(d["path"][:2] == ["actions", "metrics"] for d in changed_module["accepted_differences"])
    evidence["compiled_file_sha256"]["modules/tasks/contracts/unrepresented.yaml"] = "a" * 64
    assert project() is None


@pytest.mark.parametrize(("text", "recorded"), [("", ""), ("change its title", "keep its title"), ("x" * 4001, None)])
def test_incomplete_or_conflicting_refinement_request_keeps_the_ordinary_author(text, recorded):
    ctx = {"refinement_request": text, "refinement_request_meta": {"raw_user_request": recorded}}
    assert reviewed_module_choice_prompt(ctx, {}, {}, {}, compiled_evidence={}) is None


async def test_ordinary_author_keeps_a_valid_yaml_date_in_an_accepted_optional_setting(tmp_path, monkeypatch):
    task, ctx, files, _, callers, _ = _setup(tmp_path, monkeypatch)
    ctx["refinement_request"] = "Suggest a summary for each new task."
    path = "modules/tasks/contracts/settings.yaml"
    text = "schema_version: mozaiks.settings.v1\nsettings:\n  - id: start_date\n    default: 2026-11-01\n"
    files[path] = ctx["generated_files"][path] = text
    setting = ModuleSettingsManifest.model_validate(yaml.safe_load(text)).settings[0]
    source = await reviewed_result_module_task_output(task, ctx, retained_ask_callers=callers)
    evidence = source.compiled_evidence("tasks", _compiled_files(source, task, ctx, callers))
    carried = next(c["document"] for c in evidence["retained_companions"] if c["path"] == path)
    assert setting.default.isoformat() == "2026-11-01"
    assert reviewed_module_choice_prompt(ctx, task, source.candidate, callers, compiled_evidence=evidence) is None
    assert carried["settings"][0]["default"] == setting.default and files[path] == text


async def test_only_proven_absent_owned_optional_contracts_can_remain_absent(tmp_path, monkeypatch):
    task, ctx, files, _, callers, _ = _setup(tmp_path, monkeypatch)
    ctx["refinement_request"] = "Suggest a summary. Keep existing task settings and notifications."
    path = "modules/tasks/contracts/notifications.yaml"
    assert path not in files
    for row in (task, task["current_build_task"], ctx["app_build_plan"]["build_tasks"][0]):
        row["owned_paths"] = [*row["owned_paths"], path]
    ctx["app_task_batch_items"] = [deepcopy(task)]
    source = await reviewed_result_module_task_output(task, ctx, retained_ask_callers=callers)
    assert source
    proof = source.compiled_evidence("tasks", _compiled_files(source, task, ctx, callers))
    def select(evidence):
        return reviewed_module_choice_prompt(ctx, task, source.candidate, callers, compiled_evidence=evidence)
    digest, message = select(proof)
    offer = json.loads(message)
    assert digest == offer["candidate_sha256"] and offer["absent_optional_paths"] == [path]
    assert path not in offer["proposed_files"] and path not in {c["path"] for c in offer["retained_companions"]}
    assert "author_module" in offer["absent_optional_path_decision"]
    without_absence = {key: value for key, value in proof.items() if key != "absent_optional_paths"}
    assert select(without_absence) is None
    for invalid in ([], [path, path], [path, "modules/tasks/contracts/admin.yaml"], [path, "modules/tasks/module.yaml"],
                    [path, "modules/tasks/contracts/fixtures.yaml"], [path, "modules/unrelated/contracts/notifications.yaml"]):
        assert select({**proof, "absent_optional_paths": invalid}) is None

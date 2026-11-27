import asyncio
import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows._shared.context_graph.prompt_pack import build_context_graph_prompt_pack
from factory_app.workflows.AppGenerator.tools.reviewed_module_choice import run_reviewed_file_choice
from factory_app.workflows.AppGenerator.tools.reviewed_page_choice import (
    REVIEWED_PAGE_SYSTEM_PROMPT,
    reviewed_page_choice_prompt,
)
from factory_app.workflows.AppGenerator.tools.reviewed_result_page_task import (
    VerifiedResultPageSource,
)
from mozaiksai.core.adapters.ag2_task_batch_runner import (
    AG2TaskBatchRunner,
    AG2TaskBatchRunnerRequest,
    AG2TaskBatchRunnerResult,
)
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow import task_batches
from tests.test_module_contract_raw_companions import _batch

PAGE = "ui/pages/reports.yaml"
MANIFEST = "app.json"
EDGE = {"source_path": "modules/reports/backend/service.py", "target_path": "modules/reports/backend/repo.py", "kind": "imports"}
REQUEST = "Keep the report list and let me edit a report when I select it."


@pytest.fixture
def page_choice():
    task = {"task_id": "reports-page", "task_type": "page_bundle", "surface_id": "reports", "capability_pack_id": "reports",
            "initial_agent": "AppSchemaAgent", "owned_paths": [PAGE, MANIFEST], "depends_on": ["contract"],
            "acceptance_criteria": ["Keep Create and Ask while adding the selected-report editor."]}
    upstream = {"task_id": "contract", "task_type": "module_contract", "owned_paths": ["modules/reports/module.yaml"]}
    child = {"task_id": "review", "task_type": "admin_contribution", "owned_paths": ["admin/admin_registry.yaml"],
             "depends_on": [task["task_id"]]}
    unrelated = {"task_id": "alerts", "task_type": "module_contract", "owned_paths": ["modules/alerts/module.yaml"]}
    app_id = "reports-app"
    record = "accepted-record"
    graph_identity = {"app_id": app_id, "build_record_id": record, "build_family": "app_bundle", "build_key": "app_bundle",
                      "bundle_digest": "sha256:" + "a" * 64, "graph_hash": "sha256:" + "b" * 64}
    graph = build_context_graph_prompt_pack(catalog={**graph_identity, "verified": True}, source="verified_artifact_bundle")
    graph["dependency_graph"] = {**graph_identity, "files": [EDGE["source_path"], EDGE["target_path"]],
                                 "dependencies": [EDGE], "coverage_incomplete": False}
    ctx = {"workflow_name": "AppGenerator", "build_mode": "revision",
           "run_build_binding": {"target_app_id": app_id, "build_id": "new-build", "build_registry_id": "registry", "phase": "refinement"},
           "revision_source_artifact_version_id": record, "workflow_bundle_artifact_version_id": "reviewed-workflow",
           "refinement_request": REQUEST, "refinement_request_meta": {"raw_user_request": REQUEST},
           "context_graph_pack": graph, "app_build_plan": {"build_tasks": [upstream, task, child, unrelated]},
           "generated_files": {PAGE: "untrusted work in progress"}}
    manifest = '{ "appName": "Reports", "authRequired": true }\n'
    page = {"name": "reports", "route": "/reports", "meta": {"ask_context": [{"module": "reports", "action": "summarize_reports"}]},
            "sections": [{"id": "create", "primitive": "Form", "config": {"title": "Create report"}}]}
    accepted_files = {MANIFEST: manifest, PAGE: yaml.safe_dump(page)}
    updated = deepcopy(page)
    updated["sections"].append({"id": "edit", "primitive": "Form", "config": {"title": "Edit selected report"}})
    proposed = {MANIFEST: manifest, PAGE: yaml.safe_dump(updated)}
    candidate = {"code_files": [{"filename": p, "content": text} for p, text in sorted(proposed.items())], "deleted_files": []}
    source = VerifiedResultPageSource(candidate=candidate, source_files=accepted_files, source_build_id="accepted-build",
                                      source_artifact_version_id=record, module_id="reports", page_path=PAGE)
    return ctx, task, source, proposed


def test_page_choice_binds_the_actual_accepted_page_and_the_offered_files(page_choice):
    ctx, task, source, proposed = page_choice
    digest, prompt = reviewed_page_choice_prompt(ctx, task, source, proposed)
    offer = json.loads(prompt)
    assert offer["refinement_request"] == REQUEST and offer["page_path"] == PAGE
    assert (offer["source_build_id"], offer["refinement_build_id"]) == ("accepted-build", "new-build")
    assert (offer["source_artifact_version_id"], offer["workflow_bundle_artifact_version_id"]) == ("accepted-record", "reviewed-workflow")
    assert offer["candidate_sha256"] == digest
    assert offer["task"]["acceptance_criteria"] == task["acceptance_criteria"]
    assert {row["task_id"] for row in offer["other_workers"]} == {"contract", "review"}
    assert offer["dependency_graph"]["dependencies"] == [EDGE]
    assert offer["dependency_graph"]["graph_hash"] == ctx["context_graph_pack"]["graph_hash"]
    assert offer["dependency_graph"]["bundle_digest"] == ctx["context_graph_pack"]["bundle_digest"]
    assert offer["accepted_files"] == source.source_files and offer["proposed_files"] == proposed
    assert offer["compiled_file_sha256"] == {p: hashlib.sha256(text.encode()).hexdigest() for p, text in proposed.items()}
    assert "untrusted work in progress" not in prompt
    assert len(prompt.encode()) <= 32_000

    changed_source = replace(source, source_files={**source.source_files, PAGE: "# Accepted by owner\n" + source.source_files[PAGE]})
    source_digest, _ = reviewed_page_choice_prompt(ctx, task, changed_source, proposed)
    assert source_digest != digest


@pytest.mark.parametrize("fault", ["source", "request", "missing_path", "extra_path", "manifest_bytes", "oversize", "inline_secret"])
def test_unproven_or_incomplete_page_evidence_keeps_the_ordinary_author(page_choice, fault):
    ctx, task, source, proposed = page_choice
    if fault == "source":
        source = replace(source, source_artifact_version_id="another-record")
    elif fault == "request":
        ctx["refinement_request_meta"]["raw_user_request"] = "Change the reports page."
    elif fault == "missing_path":
        proposed.pop(MANIFEST)
    elif fault == "extra_path":
        proposed["ui/pages/extra.yaml"] = proposed[PAGE]
    elif fault == "manifest_bytes":
        proposed[MANIFEST] = json.dumps(json.loads(proposed[MANIFEST]))
    elif fault == "oversize":
        proposed[PAGE] += "# " + "é" * 17000
    else:
        source = replace(source, source_files={**source.source_files, PAGE: source.source_files[PAGE] + '\napi_key: "fixture-value"\n'})
    assert reviewed_page_choice_prompt(ctx, task, source, proposed) is None


@pytest.mark.parametrize("decision", ["use_compiled", "author_page", "wrong_digest", "provider_failed"])
async def test_page_worker_can_select_the_same_files_or_request_its_ordinary_authoring_turn(page_choice, monkeypatch, decision):
    ctx, task, source, proposed = page_choice
    offer = reviewed_page_choice_prompt(ctx, task, source, proposed)
    digest, prompt = offer
    request = AG2TaskBatchRunnerRequest(workflow_name="AppGenerator", batch_id="app_build_tasks", task_id=task["task_id"],
                                        chat_id="fresh-chat", app_id="reports-app", agent_name="AppSchemaAgent", agent=object(),
                                        prompt="Ordinary schema authoring task", context_variables={"build": "new-build"})
    decision_receipt = AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, wal=[{"event": "selection"}], output={
        "candidate_sha256": "0" * 64 if decision == "wrong_digest" else digest,
        "decision": "use_compiled" if decision == "wrong_digest" else decision,
    })
    if decision == "provider_failed":
        decision_receipt = AG2TaskBatchRunnerResult(status=RunStatus.FAILED, error="Provider unavailable")
    authored = AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output={"agent_message": "Authored"}, wal=[{"event": "authored"}])
    runner = AsyncMock(side_effect=[decision_receipt, authored])
    monkeypatch.setattr(AG2TaskBatchRunner, "run", runner)
    result = await run_reviewed_file_choice(request, source.candidate, offer, model_name="ReviewedPageChoice",
                                            prompt_projection=REVIEWED_PAGE_SYSTEM_PROMPT, author_decision="author_page")
    first = runner.await_args_list[0].args[0]
    assert first.agent is request.agent and first.agent_name == "AppSchemaAgent" and first.prompt == prompt
    assert first.response_model.__name__ == "ReviewedPageChoice" and first.prompt_projection is REVIEWED_PAGE_SYSTEM_PROMPT
    if decision == "author_page":
        assert runner.await_count == 2 and runner.await_args_list[1].args[0] is request
        assert result is authored
    elif decision == "use_compiled":
        assert runner.await_count == 1 and result.status is RunStatus.COMPLETED and result.wal == decision_receipt.wal
        assert result.output.payload == source.candidate and result.output.payload is not source.candidate
    else:
        assert runner.await_count == 1 and result.status is RunStatus.FAILED and result.output is None


@pytest.mark.parametrize("edit", ["valid", "stale", "empty"])
async def test_display_text_disposition_is_bound_to_the_worker_offer(page_choice, monkeypatch, edit):
    from functools import partial

    from factory_app.workflows.AppGenerator.tools.reviewed_result_page_task import (
        apply_reviewed_page_copy,
    )

    ctx, task, source, proposed = page_choice
    page = yaml.safe_load(proposed[PAGE])
    page["sections"].insert(0, {"id": "intro", "primitive": "PageHeader", "config": {"subtitle": "Reports are final."}})
    proposed[PAGE] = yaml.safe_dump(page)
    source = replace(source, source_files=dict(proposed))
    digest, prompt = reviewed_page_choice_prompt(ctx, task, source, proposed)
    field, = json.loads(prompt)["editable_copy_fields"]
    edits = [{**field, "replacement": "Select a report to edit it."}]
    if edit == "stale":
        edits[0]["expected"] = "A different original value"
    if edit == "empty":
        edits = []
    request = AG2TaskBatchRunnerRequest(workflow_name="AppGenerator", batch_id="app_build_tasks", task_id=task["task_id"],
                                        chat_id="fresh-chat", app_id="reports-app", agent_name="AppSchemaAgent",
                                        agent=object(), prompt="Author the page")
    runner = AsyncMock(return_value=AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output={
        "candidate_sha256": digest, "decision": "edit_copy", "copy_edits": edits,
    }))
    monkeypatch.setattr(AG2TaskBatchRunner, "run", runner)
    result = await run_reviewed_file_choice(request, source.candidate, (digest, prompt), model_name="ReviewedPageChoice",
                                            prompt_projection=REVIEWED_PAGE_SYSTEM_PROMPT, author_decision="author_page",
                                            edit_files=partial(apply_reviewed_page_copy, source, proposed))
    assert runner.await_count == 1
    if edit == "valid":
        assert result.status is RunStatus.COMPLETED
        emitted = {entry["filename"]: entry["content"] for entry in result.output.payload["code_files"]}
        assert emitted == result.output.edited_files and emitted[MANIFEST] == proposed[MANIFEST]
        assert yaml.safe_load(emitted[PAGE])["sections"][0]["config"]["subtitle"] == edits[0]["replacement"]
    else:
        assert result.status is RunStatus.FAILED and result.output is None


async def test_ordinary_author_returning_the_offered_page_bytes_still_requires_data_source(page_choice, monkeypatch):
    from factory_app.workflows.AppGenerator.tools import reviewed_result_page_task

    ctx, task, source, proposed = page_choice
    task["initial_message"] = "Add the editor to the existing page."
    # A compiled table contains its resolved endpoint. An author must return a module/action reference.
    page = yaml.safe_load(proposed[PAGE])
    page["sections"] = [{"id": "reports", "primitive": "ResourceTable", "config": {
        "api_endpoint": "/api/modules/reports/list_reports", "data_key": "items", "columns": [{"key": "title", "label": "Title"}],
    }}]
    proposed[PAGE] = yaml.safe_dump(page)
    candidate = {"code_files": [{"filename": k, "content": v} for k, v in sorted(proposed.items())], "deleted_files": []}
    source = replace(source, candidate=candidate)
    monkeypatch.setattr(reviewed_result_page_task, "reviewed_result_page_task_output", AsyncMock(return_value=source))
    # This case starts with an already compiled page. The normal authoring compiler below is the boundary under test.
    monkeypatch.setattr(task_batches, "_normalize_owned_page_files_from_plan", lambda files, **kwargs: files)
    offer = reviewed_page_choice_prompt(ctx, task, source, proposed)
    runner = AsyncMock(side_effect=[
        AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output={"candidate_sha256": offer[0], "decision": "author_page"}),
        AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output=deepcopy(candidate)),
    ])
    monkeypatch.setattr(AG2TaskBatchRunner, "run", runner)
    batch = _batch()
    batch = batch.model_copy(update={"execution": batch.execution.model_copy(update={"retry_limit": 0})})
    with pytest.raises(task_batches._TaskRejected, match="model-authored endpoint URLs are forbidden"):
        await task_batches._run_one_task(
            workflow_name="AppGenerator", batch=batch, task=task, all_task_items=[task], base_context=ctx, completed_task_outputs={},
            current_batch_outputs={}, agents={"AppSchemaAgent": object()}, chat_id="fresh-chat", app_id="reports-app", user_id="owner",
            semaphore=asyncio.Semaphore(1), fresh_agents_per_task=False, agents_factory=None, context_authority_policy=None,
        )
    assert runner.await_count == 2

import hashlib
import importlib
import json
import zipfile
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows._shared.workflow_integration import (
    apply_workflow_integration_context,
    extract_workflow_integration_metadata_from_bundle_entries,
    hydrate_workflow_integration_context_from_latest_artifact,
    workflow_integration_metadata_from_artifact,
)
from factory_app.workflows.AgentGenerator.tools.result_write_materialization import (
    materialize_workflow_result_write,
)
from factory_app.workflows.AppGenerator.tools.app_validation import (
    validate_workflow_integration_contract,
)
from factory_app.workflows.AppGenerator.tools.assembly_phase import (
    assemble_features,
    compiled_pack_module_templates,
)
from factory_app.workflows.AppGenerator.tools.workflow_result_writes import workflow_result_files
from mozaiksai.core.artifacts.models import BuildRecordFileEntry
from mozaiksai.core.workflow import module_tools
from tests.factory_context import factory_context
from tests.test_module_loader_contracts import _write_canonical_module
from tests.test_workflow_integration_metadata import _FakeArtifactStore, _record

EVENT = "domain.tasks.task_created"
WRITE = {"source_action": "create", "action": "save_summary", "collection": "tasks",
         "record_key": "task_id", "fields": ["summary"]}
SPEC = {"name": "ReviewWorkflow", "result_write_plans": [
    {"agent": "ResultAgent", "module_id": "tasks", "event_type": EVENT, "result_write": WRITE},
]}


def entry(*, fields=None, triggers=None, models=None):
    orchestrator = {"workflow_name": "ReviewWorkflow", "workflow_startup_mode": "BackendOnly", "initial_agent": "ResultAgent",
                    "triggers": [{"type": "event", "event": name, "capability_id": "tasks.review"}
                                 for name in triggers or [EVENT]]}
    outputs = {"schema_version": "mozaiks.structured_outputs.v1", "registry": {"ResultAgent": "Summary"},
               "models": {**(models or {}), "Summary": {"type": "model", "fields": fields or {"summary": {"type": "str"}}}}}
    return {"workflow_name": "ReviewWorkflow", "files": [
        {"filename": path, "content": yaml.safe_dump(document)}
        for path, document in [("tools.yaml", {"tools": []}), ("structured_outputs.yaml", outputs),
                               ("context_variables.yaml", {"definitions": {}}),
                               ("transition_graph.yaml", {"transition_rules": []}),
                               ("agents.yaml", {"agents": [{"name": "ResultAgent", "system_message": "Summarize the task.",
                                                            "structured_outputs_required": True}]}),
                               ("orchestrator.yaml", orchestrator)]
    ]}


def workflow_context():
    rendered = materialize_workflow_result_write(entry(), SPEC)
    metadata = extract_workflow_integration_metadata_from_bundle_entries([rendered])
    context = {}
    bundle = _record("approved-workflow", {"workflow_integration_metadata": metadata})
    apply_workflow_integration_context(context, workflow_integration_metadata_from_artifact(bundle))
    return context


def app_files(tmp_path):
    folder = _write_canonical_module(tmp_path, reaction_target="capability")
    module = yaml.safe_load((folder / "module.yaml").read_text())
    module["actions"].append({
        "id": "save_summary", "handler_method": "receive_summary", "description": "Save the summary",
        "api_surface": "internal", "permissions": [], "emits": [],
        "input_schema": {"type": "object", "properties": {"summary": {"type": "string"}},
                         "required": ["summary"], "additionalProperties": False},
    })
    events = yaml.safe_load((folder / "contracts/events.yaml").read_text())
    events["events"][0]["payload_schema"]["properties"] = {"task_id": {"type": "string"}, "title": {"type": "string"}}
    data = {"version": "1", "surfaces": [{"surface_id": "tasks", "surface_kind": "module", "collections": [
        {"name": "tasks", "scope": "app", "entity": "Task", "tenancy": "per_user", "owner_field": "owner",
         "ownership": {"surface_id": "tasks", "surface_kind": "module"},
         "fields": [{"name": name, "type": "string"} for name in ["owner", "summary", "task_id"]]}
    ]}]}
    files = {p.relative_to(tmp_path).as_posix(): p.read_text() for p in folder.rglob("*") if p.is_file()}
    files["modules/tasks/module.yaml"] = yaml.safe_dump(module)
    files["modules/tasks/contracts/events.yaml"] = yaml.safe_dump(events)
    files["data/contract.json"] = json.dumps(data)
    files["modules/tasks/backend/service.py"] = "def existing_service():\n    return 13\n"
    return files


async def test_reviewed_agent_result_is_flat_and_automatic_for_multiple_triggers(monkeypatch):
    second = deepcopy(SPEC["result_write_plans"][0])
    second["event_type"] = "domain.tasks.submitted"
    second["result_write"]["source_action"] = "submit"
    spec = {**SPEC, "result_write_plans": [*SPEC["result_write_plans"], second]}
    rendered = materialize_workflow_result_write(entry(triggers=[EVENT, second["event_type"]]), spec)
    assert materialize_workflow_result_write(rendered, spec) == rendered
    metadata = extract_workflow_integration_metadata_from_bundle_entries([rendered])
    assert metadata["workflows"][0]["result_write_plans"] == spec["result_write_plans"]
    files = {f["filename"]: f["content"] for f in rendered["files"]}
    binding, = yaml.safe_load(files["tools.yaml"])["tools"]
    assert (binding["agent"], binding["auto_tool_call"], binding["bind_to_agent"]) == ("ResultAgent", True, False)
    commit = AsyncMock(return_value={"status": "committed"})
    monkeypatch.setattr(module_tools, "commit_workflow_result", commit)
    scope = {}
    exec(compile(files[binding["file"]], binding["file"], "exec"), scope)
    receipt = await scope[binding["function"]]({"structured_output": {"summary": "Ready"}, "task_id": "ignored"})
    commit.assert_awaited_once_with({"summary": "Ready"})
    assert receipt == {"status": "committed"}


@pytest.mark.parametrize("fields", [
    {"summary": {"type": "str"}, "task_id": {"type": "str"}},
    {"summary": {"type": "str", "default": ""}},
    {"nested_result": {"type": "str"}},
])
def test_result_agent_cannot_select_record_or_weaken_approved_output(fields):
    with pytest.raises(ValueError, match="exactly the approved"):
        materialize_workflow_result_write(entry(fields=fields), SPEC)


def test_builder_cannot_add_an_unreviewed_result_or_leave_an_unbound_trigger():
    with pytest.raises(ValueError, match="cannot change"):
        materialize_workflow_result_write({**entry(), "result_write_plans": SPEC["result_write_plans"]}, {})
    rendered = materialize_workflow_result_write(entry(triggers=[EVENT, "domain.tasks.other"]), SPEC)
    with pytest.raises(ValueError, match="each declared"):
        extract_workflow_integration_metadata_from_bundle_entries([rendered])


def test_required_result_preserves_the_success_continuation_and_refuses_a_bypass():
    candidate = entry()
    files = {file["filename"]: file for file in candidate["files"]}
    orchestrator = yaml.safe_load(files["orchestrator.yaml"]["content"])
    files["orchestrator.yaml"]["content"] = yaml.safe_dump({**orchestrator, "initial_agent": "TriageAgent"})
    rules = [
        {"source_agent": "TriageAgent", "target_agent": "ResultAgent", "transition_type": "after_turn"},
        {"source_agent": "ResultAgent", "target_agent": "ClosingAgent", "transition_type": "after_turn"},
        {"source_agent": "ClosingAgent", "target_agent": "terminate", "transition_type": "after_turn"},
    ]
    files["transition_graph.yaml"]["content"] = yaml.safe_dump({"transition_rules": rules})
    compiled = materialize_workflow_result_write(candidate, SPEC)
    assert materialize_workflow_result_write(compiled, SPEC) == compiled
    graph = next(file for file in compiled["files"] if file["filename"] == "transition_graph.yaml")
    result_routes = [rule for rule in yaml.safe_load(graph["content"])["transition_rules"] if rule["source_agent"] == "ResultAgent"]
    assert {rule.get("condition_value"): rule["target_agent"] for rule in result_routes} == {
        "applied": "ClosingAgent", "already_applied": "ClosingAgent", "failed": "terminate", None: "terminate",
    }
    assert all(rule["termination_reason"] == "workflow_failed" for rule in result_routes if rule["target_agent"] == "terminate")
    rules.append({"source_agent": "TriageAgent", "target_agent": "terminate", "transition_type": "condition",
                  "condition_type": "context_equals", "condition_key": "finish_early", "condition_value": True})
    files["transition_graph.yaml"]["content"] = yaml.safe_dump({"transition_rules": rules})
    with pytest.raises(ValueError, match="bypasses the approved result agent"):
        materialize_workflow_result_write(candidate, SPEC)


@pytest.mark.parametrize(("receiver", "storage", "diagnostic"), [
    ("integer", "integer", "compiled workflow result schema"),
    ("string", "integer", "collection field type"),
])
def test_actual_workflow_artifact_must_match_result_action_and_storage(tmp_path, receiver, storage, diagnostic):
    context = workflow_context()
    files = app_files(tmp_path)
    path = "modules/tasks/module.yaml"
    module = yaml.safe_load(files[path])
    module["actions"][-1]["input_schema"]["properties"]["summary"]["type"] = receiver
    files[path] = yaml.safe_dump(module)
    contract = json.loads(files["data/contract.json"])
    field = next(field for field in contract["surfaces"][0]["collections"][0]["fields"] if field["name"] == "summary")
    field["type"] = storage
    files["data/contract.json"] = json.dumps(contract)
    with pytest.raises(ValueError, match=diagnostic):
        workflow_result_files(files, context["workflow_integration_metadata"])
    verdict = validate_workflow_integration_contract(files, context)
    assert verdict["passed"] is False and diagnostic in verdict["failed_tests"][0]["error"]


def test_nested_compiled_result_shapes_survive_pinning_and_reject_incompatible_items(tmp_path):
    local = entry(fields={"summary": {"type": "list", "items": "Review"}}, models={
        "Review": {"type": "model", "fields": {"rating": {"type": "int", "description": "Review rating"}}},
    })
    rendered = materialize_workflow_result_write(local, SPEC)
    metadata = extract_workflow_integration_metadata_from_bundle_entries([rendered])
    record = _record("typed-workflow", {"workflow_integration_metadata": metadata})
    pinned = workflow_integration_metadata_from_artifact(record)
    expected = {"type": "array", "items": {"type": "object", "properties": {"rating": {"type": "integer"}},
                                           "required": ["rating"], "additionalProperties": False}}
    assert pinned["workflows"][0]["result_output_schema"]["properties"]["summary"] == expected
    files = app_files(tmp_path)
    data = json.loads(files["data/contract.json"])
    next(f for f in data["surfaces"][0]["collections"][0]["fields"] if f["name"] == "summary")["type"] = "array"
    files["data/contract.json"] = json.dumps(data)
    path = "modules/tasks/module.yaml"
    module = yaml.safe_load(files[path])
    props = module["actions"][-1]["input_schema"]["properties"]
    props["summary"] = deepcopy(expected)
    files[path] = yaml.safe_dump(module)
    files.update(workflow_result_files(files, pinned))
    assert workflow_result_files(files, pinned) == {}
    props["summary"]["items"]["properties"]["rating"]["type"] = "string"
    files[path] = yaml.safe_dump(module)
    with pytest.raises(ValueError, match="compiled workflow result schema"):
        workflow_result_files(files, pinned)


async def test_historical_producer_schema_is_recovered_only_from_verified_workflow_bytes(tmp_path):
    rendered = materialize_workflow_result_write(entry(), SPEC)
    metadata = extract_workflow_integration_metadata_from_bundle_entries([rendered])
    expected = metadata["workflows"][0].pop("result_output_schema")
    path = tmp_path / "TicketOps.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for file in rendered["files"]:
            archive.writestr(f"ReviewWorkflow/{file['filename']}", file["content"])
    record = _record("archived-result-workflow", {"artifact_path": str(path), "workflow_integration_metadata": metadata})
    raw = path.read_bytes()
    record.files_manifest = [BuildRecordFileEntry(path="TicketOps/TicketOps.zip", sha256=hashlib.sha256(raw).hexdigest(),
                                                  size_bytes=len(raw), content_type="application/zip")]
    for saved in (record, record.model_dump(by_alias=True)):
        pinned = workflow_integration_metadata_from_artifact(saved)
        assert pinned["workflows"][0]["result_output_schema"] == expected
        assert pinned["source_artifact_version_id"] == record.id
    context = factory_context({"artifact_version_refs": {"workflow_bundle": record.id},
                               "workflow_integration_metadata": metadata})
    store = SimpleNamespace(get_build_record=AsyncMock(return_value=record))
    await hydrate_workflow_integration_context_from_latest_artifact(context, artifact_store=store)
    assert context["workflow_integration_metadata"]["workflows"][0]["result_output_schema"] == expected
    assert "result_output_schema" not in metadata["workflows"][0]
    path.write_bytes(raw + b"tampered")
    with pytest.raises(ValueError, match="digest differs"):
        workflow_integration_metadata_from_artifact(record)
    path.unlink()
    with pytest.raises(ValueError, match="Could not read"):
        workflow_integration_metadata_from_artifact(record)


def test_a_string_producer_cannot_write_unrestricted_values_to_an_enum_field(tmp_path):
    files = app_files(tmp_path)
    data = json.loads(files["data/contract.json"])
    summary = next(f for f in data["surfaces"][0]["collections"][0]["fields"] if f["name"] == "summary")
    summary["enum"] = ["draft", "complete"]
    files["data/contract.json"] = json.dumps(data)
    with pytest.raises(ValueError, match="action enum must fit"):
        workflow_result_files(files, workflow_context()["workflow_integration_metadata"])


async def test_app_assembly_compiles_the_actual_internal_receiver(tmp_path, monkeypatch):
    files = app_files(tmp_path)
    ctx = workflow_context()
    result = await assemble_features("generated-app", [{"code_files": [{"filename": path, "content": source}
                                    for path, source in files.items()]}], context_variables=ctx,
                                    data_contract=json.loads(files["data/contract.json"]))
    built = {f["filename"]: f["content"] for f in result["code_files"]}
    assert workflow_result_files(built, ctx["workflow_integration_metadata"]) == {}
    assert validate_workflow_integration_contract(built, ctx)["passed"]
    assert yaml.safe_load(built["modules/tasks/contracts/reactions.yaml"])["reactions"][0]["target"]["result_write"] == WRITE
    package = tmp_path / "compiled_result_receiver"
    package.mkdir()
    (package / "__init__.py").write_text("")
    for name in ["handler.py", "service.py"]:
        (package / name).write_text(built[f"modules/tasks/backend/{name}"], encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    service = importlib.import_module("compiled_result_receiver.service")
    handler = importlib.import_module("compiled_result_receiver.handler").TasksModule()
    assert service.existing_service() == 13
    assert hasattr(handler, "create_task")
    host = SimpleNamespace(commit_workflow_result=AsyncMock(return_value={"status": "committed"}))
    assert await handler.receive_summary(host, summary="Written") == {"status": "committed"}
    host.commit_workflow_result.assert_awaited_once_with({"summary": "Written"})
    assert not validate_workflow_integration_contract(built, {})["passed"]


async def test_unrelated_app_revision_retains_only_the_selected_parents_result_grant(tmp_path):
    original = workflow_context()
    files = app_files(tmp_path)
    files.update(workflow_result_files(files, original["workflow_integration_metadata"]))
    parent = _record("accepted-app", {}, family="app_bundle", inputs={"workflow_bundle": "approved-workflow"})
    workflow = _record("approved-workflow", {"workflow_integration_metadata": original["workflow_integration_metadata"]})
    store = _FakeArtifactStore(None)
    store.get_build_record = AsyncMock(side_effect=[parent, workflow])
    ctx = factory_context({"artifact_kind": "app_bundle", "artifact_version_id": "accepted-app",
                           "artifact_version_refs": {"workflow_bundle": "unrelated-current"}})
    await hydrate_workflow_integration_context_from_latest_artifact(ctx, artifact_store=store)
    assert ctx["workflow_integration_metadata"]["source_artifact_version_id"] == "approved-workflow"
    assert not store.list_calls
    files["brand/theme_config.json"] = '{"accent":"blue"}'
    assert workflow_result_files(files, ctx["workflow_integration_metadata"]) == {}
    assert validate_workflow_integration_contract(files, ctx)["passed"]
    assert compiled_pack_module_templates(context_variables=ctx, data_contract=None, subscription_contract=None) == {}
    path = "modules/tasks/contracts/reactions.yaml"
    altered = yaml.safe_load(files[path])
    altered["reactions"][0]["target"]["result_write"]["fields"].append("title")
    files[path] = yaml.safe_dump(altered)
    assert not validate_workflow_integration_contract(files, ctx)["passed"]
    detached = {**original["workflow_integration_metadata"], "source_artifact_version_id": None}
    with pytest.raises(ValueError, match="registered workflow bundle ID"):
        workflow_result_files(files, detached)


@pytest.mark.parametrize("removed", ["modules/tasks/contracts/reactions.yaml", "modules/tasks/module.yaml",
                                     "data/contract.json", "modules/tasks/backend/service.py"])
def test_missing_result_owner_fails_closed(tmp_path, removed):
    files = app_files(tmp_path)
    files.pop(removed)
    with pytest.raises(ValueError):
        workflow_result_files(files, workflow_context()["workflow_integration_metadata"])

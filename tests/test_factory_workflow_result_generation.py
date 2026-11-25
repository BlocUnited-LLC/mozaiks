import importlib
import json
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


def entry(*, fields=None, triggers=None):
    orchestrator = {"workflow_name": "ReviewWorkflow", "workflow_startup_mode": "BackendOnly",
                    "triggers": [{"type": "event", "event": name, "capability_id": "tasks.review"}
                                 for name in triggers or [EVENT]]}
    outputs = {"schema_version": "mozaiks.structured_outputs.v1", "registry": {"ResultAgent": "Summary"},
               "models": {"Summary": {"type": "model", "fields": fields or {"summary": {"type": "str"}}}}}
    return {"workflow_name": "ReviewWorkflow", "files": [
        {"filename": path, "content": yaml.safe_dump(document)}
        for path, document in [("tools.yaml", {"tools": []}), ("structured_outputs.yaml", outputs),
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

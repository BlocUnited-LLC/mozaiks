from __future__ import annotations

import json

import pytest
import yaml

from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.runtime.app.module_loader import (
    ModuleLoader,
    ModuleLoadError,
    resolve_result_write_collection,
)
from mozaiksai.core.validation.functional_generated_app import scan_functional_generated_app
from mozaiksai.core.validation.generated_app import (
    GeneratedAppValidationRequest,
    validate_generated_app_bundle,
)
from mozaiksai.core.validation.workspace import validate_app_workspace
from tests.test_module_loader_contracts import _write_canonical_module


@pytest.fixture
def bundle(tmp_path):
    folder = _write_canonical_module(tmp_path, reaction_target="capability")
    module = yaml.safe_load((folder / "module.yaml").read_text())
    schema = {"type": "object", "properties": {"summary": {"type": "string"}},
              "required": ["summary"], "additionalProperties": False}
    module["actions"].append({
        "id": "save_summary", "handler_method": "save_summary", "description": "Save the workflow result",
        "api_surface": "internal", "input_schema": schema,
    })
    handler = folder / "backend/handler.py"
    handler.write_text(handler.read_text() + "\n    async def save_summary(self, ctx, *, summary):\n        return summary\n")
    reactions = yaml.safe_load((folder / "contracts/reactions.yaml").read_text())
    reactions["reactions"][0]["target"]["result_write"] = {
        "source_action": "create", "action": "save_summary", "collection": "tasks",
        "record_key": "task_id", "fields": ["summary"],
    }
    events = yaml.safe_load((folder / "contracts/events.yaml").read_text())
    events["events"][0]["payload_schema"]["properties"] = {"task_id": {"type": "string"}}
    collection = {
        "scope": "app", "name": "tasks", "entity": "Task", "tenancy": "per_user", "owner_field": "author",
        "fields": [{"name": k, "type": "string"} for k in ["task_id", "other_key", "summary", "author"]],
    }
    data = {"version": "1", "surfaces": [{"surface_id": "tasks", "surface_kind": "module", "collections": [collection]}]}
    files = {p.relative_to(tmp_path).as_posix(): p.read_text() for p in tmp_path.rglob("*") if p.is_file()}
    def write():
        files["modules/tasks/module.yaml"] = yaml.safe_dump(module)
        files["modules/tasks/contracts/events.yaml"] = yaml.safe_dump(events)
        files["modules/tasks/contracts/reactions.yaml"] = yaml.safe_dump(reactions)
        if data:
            files["data/contract.json"] = json.dumps(data)
        for path, content in files.items():
            source = tmp_path / path
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(content, encoding="utf-8")
        return files

    return module, reactions, events, data, write


async def test_scoped_result_write_loads_the_owning_private_collection(tmp_path, bundle):
    _, _, _, data, write = bundle
    files = write()
    assert not [d for d in scan_functional_generated_app(files) if d.code == "INVALID_RESULT_WRITE"]
    app = await AppLoader.load(str(tmp_path))
    assert app.failed_module_names == []
    target = app.modules[0].manifests.reactions.reactions[0].target
    assert target.result_write.fields == ("summary",)
    assert resolve_result_write_collection("tasks", target.result_write, data)["owner_field"] == "author"


@pytest.mark.parametrize(("part", "updates"), [
    ("target", {"kind": "handler", "handler_method": "save_summary"}),
    ("target", {"capability_id": "tasks.create"}),
    ("write", {"source_action": "unknown"}),
    ("write", {"source_action": "save_summary"}),
    ("write", {"action": "unknown"}),
    ("write", {"collection": "other"}),
    ("write", {"fields": []}),
    ("write", {"fields": ["summary", "summary"]}),
    ("write", {"fields": ["task_id"]}),
    ("write", {"fields": ["app_id"]}),
    ("write", {"fields": ["_id"]}),
    ("write", {"fields": ["summary.other"]}),
    ("write", {"fields": ["$set"]}),
    ("write", {"record_key": "workspace_id"}),
    ("action", {"api_surface": "public"}),
    ("action", {"permissions": ["tasks.write"]}),
    ("action", {"entitlement_gate": "tasks.paid"}),
    ("action", {"ask_context_safe": True}),
    ("action", {"emits": ["domain.tasks.task_created"]}),
    ("schema", {"additionalProperties": True}),
    ("schema", {"patternProperties": {"^other": {"type": "string"}}}),
    ("schema", {"required": []}),
    ("schema", {"required": "summary"}),
    ("schema", {"properties": {"summary": {}, "task_id": {}}}),
    ("event_schema", {"required": []}),
    ("event_schema", {"properties": {"task_id": {"type": "number"}}}),
    ("event_schema", {"properties": {"task_id": {"type": "string", "nullable": True}}}),
    ("event", {"producer": "other"}),
    ("schema", {"properties": {"summary": {"type": "integer"}}}),
    ("collection", {"fields": [{"name": "task_id"}, {"name": "author"}]}),
    ("collection", {"owner_field": "summary"}),
    ("collection", {"owner_field": "task_id"}),
    ("collection", {"scope": "platform"}),
    ("collection", {"tenancy": "per_workspace"}),
    ("collection", {"tenancy": "app_wide", "owner_field": None}),
    ("surface", {"surface_id": "another_module"}),
    ("surface", {"surface_kind": "workflow"}),
])
def test_invalid_result_write_fails_static_and_runtime_admission(tmp_path, bundle, part, updates):
    module, reactions, events, data, write = bundle
    action = module["actions"][-1]
    target = reactions["reactions"][0]["target"]
    event = events["events"][0]
    surface = data["surfaces"][0]
    parts = {"target": target, "write": target["result_write"], "action": action, "schema": action["input_schema"],
             "event": event, "event_schema": event["payload_schema"], "surface": surface, "collection": surface["collections"][0]}
    parts[part].update(updates)
    files = write()
    assert any(d.code == "INVALID_RESULT_WRITE" for d in scan_functional_generated_app(files))
    with pytest.raises(ModuleLoadError):
        ModuleLoader(str(tmp_path)).load("tasks")


def test_result_write_needs_data_contract(tmp_path, bundle):
    _, _, _, data, write = bundle
    data.clear()
    files = write()
    result = validate_generated_app_bundle(GeneratedAppValidationRequest(files=files))
    assert not result.passed
    assert any(d.code == "INVALID_RESULT_WRITE" for d in result.diagnostics)
    assert any(d.code == "INVALID_RESULT_WRITE" for d in validate_app_workspace(tmp_path))
    with pytest.raises(ModuleLoadError, match="data/contract.json"):
        ModuleLoader(str(tmp_path)).load("tasks")


@pytest.mark.parametrize(("second_key", "other_event"), [("task_id", False), ("other_key", True)])
def test_one_source_action_cannot_authorize_two_writers(tmp_path, bundle, second_key, other_event):
    module, reactions, events, _, write = bundle
    first = reactions["reactions"][0]
    second_write = {**first["target"]["result_write"], "record_key": second_key}
    second = {**first, "id": "second", "target": {**first["target"], "result_write": second_write}}
    reactions["reactions"].append(second)
    schema = events["events"][0]["payload_schema"]
    schema["properties"]["other_key"] = {"type": "string"}
    schema["required"].append("other_key")
    if other_event:
        event_type = "domain.tasks.task_queued"
        second["event_type"] = event_type
        events["events"].append({**events["events"][0], "type": event_type})
        next(action for action in module["actions"] if action["id"] == "create")["emits"].append(event_type)
    files = write()
    assert any(d.code == "INVALID_RESULT_WRITE" for d in scan_functional_generated_app(files))
    with pytest.raises(ModuleLoadError, match="only one result_write reaction"):
        ModuleLoader(str(tmp_path)).load("tasks")


def test_other_nonwriting_workflow_can_still_react(tmp_path, bundle):
    _, reactions, _, _, write = bundle
    first = reactions["reactions"][0]
    target = {key: value for key, value in first["target"].items() if key != "result_write"}
    reactions["reactions"].append({**first, "id": "observer", "target": target})
    files = write()
    assert not [d for d in scan_functional_generated_app(files) if d.code == "INVALID_RESULT_WRITE"]
    assert len(ModuleLoader(str(tmp_path)).load("tasks").manifests.reactions.reactions) == 2

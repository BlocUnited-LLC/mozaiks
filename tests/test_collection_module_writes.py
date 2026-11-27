"""Operation and input scopes travel from the physical collection to the installed app."""


import pytest

from mozaiksai.core.runtime.app.module_loader import ModuleLoader, ModuleLoadError
from mozaiksai.core.runtime.persistence.collection_writes import (
    DataContractFieldError,
    canonical_write_fields,
)
from mozaiksai.core.runtime.persistence.intent_loader import (
    DataContractLoadError,
    load_data_contract,
)
from mozaiksai.core.validation.functional_generated_app import scan_functional_generated_app
from tests.test_module_reaction_result_write import bundle as bundle


@pytest.fixture
def collection():
    return {
        "name": "tasks", "entity": "Task", "owner_field": "author", "fields": [
            {"name": "task_id", "type": "string"}, {"name": "author", "type": "string"},
            {"name": "title", "type": "string", "required": True}, {"name": "summary", "type": "string"},
            {"name": "updated_at", "type": "datetime"}, {"name": "labels", "type": "array"},
        ], "lifecycle": {"write_mode": "module_action"},
    }


def test_optional_operation_list_distinguishes_legacy_and_no_writes(collection):
    assert canonical_write_fields(collection) is None
    collection["lifecycle"]["module_writes"] = []
    assert canonical_write_fields(collection) == {}
    collection["lifecycle"]["module_writes"] = [{"operation": "delete", "fields": []}]
    assert canonical_write_fields(collection) == {"delete": ()}


@pytest.mark.parametrize("entry", [
    {"operation": "rename", "fields": ["title"]}, {"operation": "update"},
    {"operation": "update", "fields": ["title"], "wildcard": True}, {"operation": "update", "fields": []},
    {"operation": "create", "fields": ["summary"]}, {"operation": "delete", "fields": ["title"]},
    {"operation": "update", "fields": ["title", "title"]}, {"operation": "update", "fields": "title"},
    *[{"operation": "update", "fields": [field]} for field in ("task_id", "author", "updated_at", "labels", "unknown")],
])
def test_invalid_operation_scopes_fail_before_generation(collection, entry):
    collection["lifecycle"]["module_writes"] = [entry]
    with pytest.raises(DataContractFieldError, match="module_writes"):
        canonical_write_fields(collection)


def test_repair_distinguishes_public_update_from_record_selector_and_workflow_result(collection):
    public = {"operation": "update", "fields": ["task_id", "title"]}
    background = {"operation": "update", "fields": ["summary"]}
    writes = collection["lifecycle"]["module_writes"] = [public, background]
    with pytest.raises(DataContractFieldError, match="record selector 'task_id'.*separate internal action"):
        canonical_write_fields(collection)
    public["fields"] = ["title"]
    with pytest.raises(DataContractFieldError, match="repeats 'update'.*separate internal action"):
        canonical_write_fields(collection)
    writes.remove(background)
    assert canonical_write_fields(collection) == {"update": ("title",)}


@pytest.mark.parametrize("problem", [
    "none", "disabled", "extra_field", "wrong_type", "no_record_key", "open_schema", "missing_action", "wrong_owner",
])
def test_runtime_and_static_validation_use_the_same_collection_scope(tmp_path, bundle, problem):
    module, _, _, data, write = bundle
    group = data["surfaces"][0]
    collection = group["collections"][0]
    collection["lifecycle"] = {"write_mode": "module_action", "module_writes": [{"operation": "update", "fields": ["title"]}]}
    if not any(field["name"] == "title" for field in collection["fields"]):
        collection["fields"].append({"name": "title", "type": "string"})
    action = {"id": "update_task", "handler_method": "update_task", "description": "Update this task",
              "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}, "title": {"type": "string"}},
                               "required": ["task_id"], "additionalProperties": False}}
    if problem != "missing_action":
        module["actions"].append(action)
    if problem == "disabled":
        module["actions"].append({**action, "id": "delete_task"})
    if problem == "extra_field":
        action["input_schema"]["properties"]["summary"] = {"type": "string"}
    if problem == "wrong_type":
        action["input_schema"]["properties"]["title"] = {"type": "integer"}
    if problem == "no_record_key":
        action["input_schema"]["required"] = []
    if problem == "open_schema":
        action["input_schema"].pop("additionalProperties")
    if problem == "wrong_owner":
        group["surface_kind"] = "workflow"
        collection["ownership"] = {"surface_id": group["surface_id"], "surface_kind": "workflow"}
    files = write()
    handler = tmp_path / "modules/tasks/backend/handler.py"
    handler.write_text(handler.read_text() + "\n    async def update_task(self, ctx, **kwargs):\n        return kwargs\n")
    problems = [d for d in scan_functional_generated_app(files) if d.code == "INVALID_COLLECTION_WRITES"]
    if problem == "none":
        assert not problems
        assert "update_task" in ModuleLoader(str(tmp_path)).load("tasks").definition.action_method_map
    elif problem == "wrong_owner":
        assert problems
        with pytest.raises(DataContractLoadError, match="module-owned"):
            load_data_contract(tmp_path)
    else:
        assert problems
        with pytest.raises(ModuleLoadError, match="collection writes"):
            ModuleLoader(str(tmp_path)).load("tasks")


def test_static_validation_requires_the_owner_to_ship_its_restricted_actions(bundle):
    _, _, _, data, write = bundle
    data["surfaces"][0]["collections"][0]["lifecycle"] = {"write_mode": "module_action", "module_writes": []}
    files = write()
    del files["modules/tasks/module.yaml"]
    assert any(d.code == "INVALID_COLLECTION_WRITES" for d in scan_functional_generated_app(files))

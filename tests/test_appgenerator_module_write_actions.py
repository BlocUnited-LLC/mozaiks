"""Canonical writes, typed schemas and their implementations derive from approved ownership."""
from __future__ import annotations

import importlib
import json
import logging
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows._shared.subscription_contract_context import approved_module_actions
from factory_app.workflows.AppGenerator.tools.app_validation import (
    validate_module_implementation_contract,
)
from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
from factory_app.workflows.AppGenerator.tools.code_file_utils import save_generated_code
from factory_app.workflows.AppGenerator.tools.module_persistence_guard import (
    scan_module_persistence,
)
from factory_app.workflows.AppGenerator.tools.module_runtime_quality import (
    audit_module_runtime_quality,
)
from mozaiksai.core.adapters.ag2_task_batch_runner import AG2TaskBatchRunnerResult
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.runtime import ModuleInputValidationError, ModuleRecordNotFoundError
from mozaiksai.core.runtime.composition.module_context import ModuleContext
from mozaiksai.core.runtime.persistence.adapter import PersistencePrincipal, PersistenceScopeError
from mozaiksai.core.runtime.persistence.mongo import MongoPersistenceCollection
from mozaiksai.core.runtime.persistence.ownership import CollectionOwnership
from mozaiksai.core.semantics.closed_contract_schema import import_closed_contract_schema
from mozaiksai.core.workflow import task_batches
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
from mozaiksai.core.workflow.generator_support.code_files import (
    compile_data_contract,
    extract_code_file_map_from_payload,
)
from mozaiksai.core.workflow.generator_support.data_contract_fields import (
    CANONICAL_FIELD_TYPES,
    DataContractFieldError,
    validate_collection_fields,
)
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    all_module_actions,
    canonical_write_action_id,
    canonical_write_actions_for_surface,
    entity_identifier,
    ungated_module_actions,
)
from mozaiksai.core.workflow.generator_support.module_policy import materialize_module_policies
from mozaiksai.core.workflow.generator_support.module_read_actions import (
    materialize_module_read_implementations,
)
from mozaiksai.core.workflow.generator_support.module_write_actions import (
    auth_contract_scopes,
    close_module_actions,
    code_owned_schema_paths,
    materialize_module_actions,
    materialize_module_schemas,
    materialize_module_write_implementations,
    materialize_task_module_schemas,
    render_module_schemas,
    validate_write_hooks,
)
from mozaiksai.core.workflow.generator_support.persistence_artifacts import (
    normalize_data_contract_indexes,
)

MODULE = "task_management"
MANIFEST = f"modules/{MODULE}/module.yaml"
BACKEND = f"modules/{MODULE}/backend"
SCHEMAS = f"{BACKEND}/schemas.py"
WRITES = ("create_task", "update_task", "delete_task")


def _field(name, kind="string", *, required=True, default=None, nullable=False, enum=None):
    return {"name": name, "type": kind, "required": required, "default": default, "enum": enum, "nullable": nullable}


def _collection(tenancy="per_user", *, write_mode="module_action", search_by="task_id", fields=None, indexes=None):
    owner = None if tenancy == "app_wide" else ("workspace_id" if tenancy == "per_workspace" else "user_id")
    declared = fields or [
        _field("task_id"), *([_field(owner)] if owner else []), _field("title"),
        _field("description", required=False, nullable=True),
        _field("is_completed", "boolean", required=False, default="false"),
        _field("priority", required=False, default='"normal"', enum=["low", "normal", "high"]),
        _field("created_at", "date"), _field("updated_at", "datetime"),
    ]
    return {
        "name": "tasks", "scope": "app", "tenancy": tenancy, "owner_field": owner, "entity": "Task",
        "ownership": {"surface_id": MODULE, "surface_kind": "module"}, "fields": declared,
        "indexes": indexes if indexes is not None else [{"keys": [{"field": "task_id", "order": 1}], "unique": True, "name": None}],
        "search_by": search_by,
        "lifecycle": {"write_mode": write_mode, "migration_policy": "additive_only"},
    }


def _contract(tenancy="per_user", **overrides):
    return {"version": "1", "surfaces": [{
        "surface_id": MODULE, "surface_kind": "module", "collections": [_collection(tenancy, **overrides)],
    }]}


def _plan():
    return {
        "capability_packs": [{
            "capability_pack_id": MODULE, "capability_source": "generated_module", "primary_entities": ["Task"],
        }],
        "pages": [],
    }


def _output(actions=None):
    return {"module_contract": {
        "module_id": MODULE,
        "module_yaml": {
            "schema_version": "mozaiks.module.v1",
            "module": {"id": MODULE, "handler": "backend.handler:TaskManagementHandler"},
            "actions": actions if actions is not None else [],
        },
    }}


def _closed(output=None, contract=None, plan=None, **kwargs):
    context = ContextVariablesBridge({
        "structured_output": output or _output(), "app_build_plan": plan or _plan(),
        "data_contract": contract or _contract(), **kwargs,
    })
    return close_module_actions(
        context.get("structured_output"), app_build_plan=context.get("app_build_plan"),
        data_contract=context.get("data_contract"),
        design_surface_map=context.get("design_surface_map"),
        subscription_contract=context.get("subscription_contract"),
    )


def _actions(closed):
    return {action["id"]: action for action in closed["module_contract"]["module_yaml"]["actions"]}


def _design(events):
    return {"surfaces": [{"surface_id": MODULE, "surface_kind": "module", "events_emitted": list(events)}]}


def _files(contract=None, model_files=None, design_surface_map=None):
    contract = contract or _contract()
    files = extract_code_file_map_from_payload(_closed(contract=contract, design_surface_map=design_surface_map))
    files.update(model_files or {})
    files.update(materialize_module_schemas(files, app_build_plan=_plan(), data_contract=contract))
    files.update(materialize_module_policies(files, contract))
    files.update(materialize_module_read_implementations(files, app_build_plan=_plan(), data_contract=contract))
    files.update(materialize_module_write_implementations(files, app_build_plan=_plan(), data_contract=contract))
    return files


# --------------------------------------------------------------------------- closure


def test_entity_names_project_to_canonical_write_ids():
    assert entity_identifier("Task") == "task"
    assert entity_identifier("ProjectMilestone") == "project_milestone"
    assert canonical_write_action_id("HTTPRequestLog", "create") == "create_http_request_log"
    with pytest.raises(ValueError, match="identifier-safe entity"):
        canonical_write_action_id("billing.subscriptions", "create")
    with pytest.raises(ValueError, match="choose create, update or delete"):
        canonical_write_action_id("Task", "list")


@pytest.mark.parametrize("tenancy", ["per_user", "per_workspace", "app_wide"])
def test_canonical_writes_are_constructed_from_the_contract(tenancy):
    actions = _actions(_closed(contract=_contract(tenancy)))
    assert [action for action in actions if action in WRITES] == list(WRITES)
    create, update, delete = (actions[name] for name in WRITES)
    for action in (create, update, delete):
        assert action["permissions"] == [] and action["api_surface"] is None and action["entitlement_gate"] is None
        assert action["handler_method"] == action["id"] and action["emits"] == [] and action["ask_context_safe"] is False
        import_closed_contract_schema(action["input_schema"])
    # Ids, ownership and timestamps are never client input; defaults make fields optional.
    assert create["input_schema"]["properties"] == {
        "title": {"type": "string"}, "description": {"type": "string"}, "is_completed": {"type": "boolean"},
        "priority": {"type": "string", "enum": ["low", "normal", "high"]},
    }
    assert create["input_schema"]["required"] == ["title"]
    assert update["input_schema"]["required"] == ["task_id"]
    assert set(update["input_schema"]["properties"]) == {"task_id", "title", "description", "is_completed", "priority"}
    assert delete["input_schema"] == {
        "type": "object", "properties": {"task_id": {"type": "string"}}, "additionalProperties": False,
        "required": ["task_id"],
    }
    record = create["output_schema"]["properties"]["item"]
    assert record["properties"]["description"] == {"type": ["string", "null"]}
    assert record["properties"]["created_at"] == {} and record["properties"]["priority"]["enum"] == ["low", "normal", "high"]
    assert delete["output_schema"]["properties"] == {"deleted": {"type": "boolean"}}
    assert {"get_tasks", "list_tasks"} <= set(actions)


@pytest.mark.parametrize("write_mode", ["workflow_write", "platform_sync", None])
def test_only_module_action_collections_receive_canonical_writes(write_mode):
    contract = _contract(write_mode=write_mode)
    if write_mode is None:
        contract["surfaces"][0]["collections"][0].pop("lifecycle")
    actions = _actions(_closed(contract=contract))
    assert not set(actions) & set(WRITES)
    assert {"get_tasks", "list_tasks"} <= set(actions)
    assert canonical_write_actions_for_surface({"surface_id": MODULE, "primary_entities": ["Task"]}, contract) == []


@pytest.mark.parametrize("tenancy", ["per_user", "per_workspace"])
def test_authored_permissions_on_owner_scoped_writes_are_stripped_with_a_logged_normalization(tenancy, caplog):
    output = _output([
        {"id": "create_task", "handler_method": "create_task", "permissions": ["task.create"],
         "emits": ["domain.tasks.task_created"]},
        {"id": "update_task", "handler_method": "update_task", "permissions": ["task.update"],
         "entitlement_gate": "task.edit"},
    ])
    output["module_contract"]["module_yaml"]["permissions"] = [
        {"id": "task.create", "description": "create"}, {"id": "task.update", "description": "update"},
        {"id": "task.audit", "description": "kept: referenced by a custom action"},
    ]
    output["module_contract"]["module_yaml"]["actions"].append(
        {"id": "audit_tasks", "handler_method": "audit_tasks", "permissions": ["task.audit"]},
    )
    before = deepcopy(output)
    with caplog.at_level(logging.WARNING):
        closed = _closed(output, _contract(tenancy))
    actions = _actions(closed)
    assert actions["create_task"]["permissions"] == [] and actions["create_task"]["api_surface"] is None
    # 'domain.tasks.task_created' names the canonical create event under the one naming rule.
    assert actions["create_task"]["emits"] == ["domain.task.created"]
    assert actions["update_task"]["entitlement_gate"] == "task.edit"
    assert actions["update_task"]["permissions"] == []
    # The stripped catalogue entries go with them; a still-referenced declaration stays.
    assert [entry["id"] for entry in closed["module_contract"]["module_yaml"]["permissions"]] == ["task.audit"]
    normalized = [record.message for record in caplog.records if "CANONICAL_WRITE_NORMALIZED" in record.message]
    assert len(normalized) == 3
    assert "action=create_task" in normalized[0] and "permissions=['task.create']" in normalized[0]
    assert f"tenancy={tenancy}" in normalized[0] and "declared auth grants=[]" in normalized[0]
    assert "removed unreferenced permission declarations ['task.create', 'task.update']" in normalized[2]
    assert output == before


def test_permissions_the_auth_contract_declares_survive_on_canonical_writes():
    output = _output([{"id": "create_task", "handler_method": "create_task", "permissions": ["openid", "task.create"]}])
    auth = {"config/auth.yaml": yaml.safe_dump({"frontend": {"default_scopes": ["openid", "profile", "email"]}})}
    context = ContextVariablesBridge({"structured_output": output, "app_build_plan": _plan(), "data_contract": _contract()})
    closed = close_module_actions(
        context.get("structured_output"), app_build_plan=context.get("app_build_plan"),
        data_contract=context.get("data_contract"), companion_files=auth,
    )
    assert _actions(closed)["create_task"]["permissions"] == ["openid"]
    assert auth_contract_scopes(auth) == frozenset({"openid", "profile", "email"})
    assert auth_contract_scopes({}) == frozenset()


@pytest.mark.parametrize("surface", ["internal", "admin_internal"])
def test_authored_restricted_surfaces_on_owner_scoped_writes_are_kept_not_widened(surface):
    output = _output([{"id": "delete_task", "handler_method": "delete_task", "api_surface": surface}])
    actions = _actions(_closed(output, _contract()))
    assert actions["delete_task"]["api_surface"] == surface
    assert actions["delete_task"]["permissions"] == [] and actions["create_task"]["api_surface"] is None
    files = _files(model_files=extract_code_file_map_from_payload(_closed(output, _contract())))
    assert "async def delete_task" in files[f"{BACKEND}/service.py"]


@pytest.mark.parametrize("surface", ["public", "public_readonly"])
def test_anonymous_surfaces_on_canonical_writes_fail_closed(surface):
    output = _output([{"id": "create_task", "handler_method": "create_task", "api_surface": surface}])
    with pytest.raises(ValueError, match=f"declares api_surface '{surface}'; anonymous canonical writes are never constructed"):
        _closed(output, _contract())


def test_app_wide_authored_permissions_on_canonical_writes_fail_closed():
    output = _output([{"id": "create_task", "handler_method": "create_task", "permissions": ["task.create"]}])
    with pytest.raises(ValueError) as excinfo:
        _closed(output, _contract("app_wide"))
    assert "protected actions [\"create_task (permissions=['task.create'])\"]" in str(excinfo.value)
    assert "not constructed open" in str(excinfo.value)


@pytest.mark.parametrize("sibling, expected", [
    ({"permissions": ["task.archive"]}, "archive_task (permissions=['task.archive'])"),
    ({"api_surface": "admin_internal"}, "archive_task (api_surface='admin_internal')"),
    ({"api_surface": "internal"}, "archive_task (api_surface='internal')"),
    ({"entitlement_gate": "task.archive"}, "archive_task (entitlement_gate='task.archive')"),
])
def test_app_wide_protected_siblings_block_open_canonical_writes(sibling, expected):
    """D1: any restricted sibling means the app-wide collection gets no open writes."""
    output = _output([{"id": "archive_task", "handler_method": "archive_task", **sibling}])
    with pytest.raises(ValueError) as excinfo:
        _closed(output, _contract("app_wide"))
    message = str(excinfo.value)
    assert expected in message
    assert "canonical writes ['create_task', 'update_task', 'delete_task'] are not constructed open" in message
    assert "Explicitly declare each of them in module.yaml.actions with handler_method, input_schema, output_schema, permissions and api_surface" in message
    assert "per_user or per_workspace tenancy" in message


def test_app_wide_gate_from_the_subscription_contract_is_protection_too():
    output = _output([{"id": "archive_task", "handler_method": "archive_task"}])
    subscription = {"contract_required": True, "module_contract_updates": [
        {"module_id": MODULE, "action_id": "archive_task", "entitlement_gate": "task.archive"},
    ]}
    with pytest.raises(ValueError, match="archive_task \\(entitlement_gate='task.archive'\\)"):
        _closed(output, _contract("app_wide"), subscription_contract=subscription)


def test_explicitly_declared_protected_app_wide_writes_are_preserved_and_model_implemented():
    """Mirror of the read closure: authored access on a protected app-wide collection stays authored."""
    declared = [
        {"id": name, "description": f"{name} for staff", "handler_method": name, "api_surface": "admin_internal",
         "permissions": [], "emits": [], "ask_context_safe": False,
         "input_schema": {"type": "object", "properties": [], "description": None, "items_type": None},
         "output_schema": {"type": "object", "properties": [], "description": None, "items_type": None}}
        for name in WRITES
    ]
    reads = [action for action in _actions(_closed()).values() if action["id"] in {"get_tasks", "list_tasks"}]
    output = _output([{"id": "archive_task", "handler_method": "archive_task", "api_surface": "admin_internal"}, *declared, *reads])
    closed = _closed(output, _contract("app_wide"))
    actions = _actions(closed)
    for name in WRITES:
        assert actions[name]["api_surface"] == "admin_internal" and actions[name]["description"] == f"{name} for staff"
    assert _closed(closed, _contract("app_wide")) == closed
    files = extract_code_file_map_from_payload(closed)
    files[f"{BACKEND}/handler.py"] = "class TaskManagementHandler:\n    async def create_task(self, ctx, **params):\n        return {'staff': True}\n"
    changes = materialize_module_write_implementations(files, app_build_plan=_plan(), data_contract=_contract("app_wide"))
    assert changes == {}


def test_closure_is_idempotent_and_leaves_custom_actions_alone():
    custom = {"id": "complete_task", "handler_method": "complete_task", "permissions": [], "emits": [],
              "api_surface": None, "ask_context_safe": False, "input_schema": {"type": "object", "properties": []},
              "output_schema": {"type": "object", "properties": []}}
    closed = _closed(_output([deepcopy(custom)]))
    assert _actions(closed)["complete_task"] == custom
    assert _closed(closed) == closed


def test_design_owned_mutations_may_omit_canonical_writes_but_not_custom_ones():
    design = {"surfaces": [{
        "surface_id": MODULE, "surface_kind": "module", "owner": "app", "primary_entities": ["Task"],
        "owned_mutations": ["create_task", "complete_task"], "custom_reads": [],
    }]}
    with pytest.raises(ValueError, match=r"declare approved actions \['complete_task'\]"):
        _closed(design_surface_map=design)
    design["surfaces"][0]["owned_mutations"] = ["create_task", "update_task"]
    assert set(WRITES) <= set(_actions(_closed(design_surface_map=design)))


@pytest.mark.parametrize("field, message", [
    (_field("tags", "array"), "required field 'tags' has structured type 'array'.*declare a JSON default"),
    (_field("payload", "blob"), r"type 'blob' is not a canonical contract type; valid choices=\['string', 'boolean', 'integer', 'number', 'date', 'datetime', 'object', 'array'\]"),
    (_field("label", required=False, default="true"), "default 'true' does not match declared type 'string'"),
    (_field("count", "integer", required=False, default="1.5"), "default '1.5' does not match declared type 'integer'"),
    (_field("title2", default="null"), "default 'null' is not allowed"),
    (_field("tags", "array", required=False, default="{}"), "default '{}' does not match declared type 'array'"),
    (_field("level", "integer", required=False, enum=["a"]), "enum requires type 'string'"),
])
def test_unrepresentable_contract_fields_fail_closed_with_valid_choices(field, message):
    """AppGenerator backstop; DesignDocs save applies the same validator first."""
    contract = _contract()
    contract["surfaces"][0]["collections"][0]["fields"].append(field)
    with pytest.raises(ValueError, match=message):
        _closed(contract=contract)
    with pytest.raises(DataContractFieldError, match=message):
        validate_collection_fields(contract["surfaces"][0]["collections"][0], "data_contract collection 'tasks'")


def test_designdocs_save_rejects_field_shapes_with_the_valid_choices():
    from factory_app.workflows.DesignDocs.tools.save_design_doc import _validate_design_collections

    surface_map = {"surfaces": [{"surface_id": MODULE, "surface_kind": "module", "primary_entities": ["Task"],
                                 "owned_mutations": [], "custom_reads": []}]}
    contract = _contract()
    contract["shared_collections"] = []
    contract["surfaces"][0]["collections"][0]["fields"].append(_field("refs", "ObjectId", required=False))
    with pytest.raises(ValueError, match="type 'ObjectId' is not a canonical contract type; valid choices="):
        _validate_design_collections(contract, surface_map)
    contract = _contract()
    contract["shared_collections"] = []
    _validate_design_collections(contract, surface_map)


def test_designdocs_save_supplies_the_empty_default_for_required_structured_fields(caplog):
    """The correction is determined, so DesignDocs saves it with a logged normalization."""
    from factory_app.workflows.DesignDocs.tools.save_design_doc import _validate_design_collections

    surface_map = {"surfaces": [{"surface_id": MODULE, "surface_kind": "module", "primary_entities": ["Task"],
                                 "owned_mutations": [], "custom_reads": []}]}
    contract = _contract()
    contract["shared_collections"] = []
    fields = contract["surfaces"][0]["collections"][0]["fields"]
    fields.extend([_field("members", "array"), _field("settings", "object"), _field("labels", "array", default='["a"]')])
    with caplog.at_level(logging.WARNING):
        _validate_design_collections(contract, surface_map)
    by_name = {field["name"]: field for field in fields}
    assert by_name["members"]["default"] == "[]" and by_name["settings"]["default"] == "{}"
    assert by_name["labels"]["default"] == '["a"]'
    messages = [record.getMessage() for record in caplog.records if "DATA_CONTRACT_FIELD_NORMALIZED" in record.getMessage()]
    assert len(messages) == 2 and "field 'members': required array default -> []" in messages[0]
    actions = _actions(_closed(contract=contract))
    assert "members" not in actions["create_task"]["input_schema"]["properties"]
    namespace: dict = {}
    exec(render_module_schemas(MODULE, contract["surfaces"][0]["collections"]), namespace)
    assert namespace["task_create_values"]({"title": "A"})["members"] == []
    assert namespace["task_create_values"]({"title": "A"})["settings"] == {}


def test_designdocs_schema_prompt_and_validator_share_one_type_list():
    schema = yaml.safe_load((Path(__file__).resolve().parents[1] / "factory_app/workflows/DesignDocs/structured_outputs.yaml").read_text(encoding="utf-8"))
    assert schema["models"]["DataContractField"]["fields"]["type"]["values"] == list(CANONICAL_FIELD_TYPES)
    prompt = (Path(__file__).resolve().parents[1] / "factory_app/workflows/DesignDocs/agents.yaml").read_text(encoding="utf-8")
    assert "one of string, boolean, integer, number, date, datetime, object, array" in prompt
    assert "Declare the generated record id as `<entity>_id`" in prompt


def test_optional_structured_fields_are_left_to_hooks_and_defaults():
    contract = _contract()
    contract["surfaces"][0]["collections"][0]["fields"].append(_field("tags", "array", required=False))
    actions = _actions(_closed(contract=contract))
    assert "tags" not in actions["create_task"]["input_schema"]["properties"]
    assert actions["create_task"]["output_schema"]["properties"]["item"]["properties"]["tags"] == {"type": "array"}


def test_inventory_and_gate_selection_include_canonical_writes_but_not_reads():
    bridge = ContextVariablesBridge({
        "data_contract": _contract(),
        "design_surface_map": {"surfaces": [{
            "surface_id": MODULE, "surface_kind": "module", "owner": "app", "primary_entities": ["Task"],
            "owned_mutations": ["complete_task"], "custom_reads": ["task_summary"],
        }]},
    })
    assert all_module_actions(bridge) == {MODULE: [
        "complete_task", "create_task", "delete_task", "get_tasks", "list_tasks", "task_summary", "update_task",
    ]}
    assert ungated_module_actions(bridge) == {MODULE: ["get_tasks", "list_tasks"]}
    assert approved_module_actions(bridge) == {MODULE: ["complete_task", "create_task", "delete_task", "task_summary", "update_task"]}


# --------------------------------------------------------------------------- indexes


def test_owned_unique_indexes_are_scoped_to_the_owner_field():
    contract = _contract(indexes=[
        {"keys": [{"field": "task_id", "order": 1}], "unique": True, "name": None},
        {"keys": [{"field": "title", "order": 1}], "unique": True, "name": "explicit_title"},
        {"keys": [{"field": "user_id", "order": 1}, {"field": "task_id", "order": -1}], "unique": True, "name": None},
        {"keys": [{"field": "created_at", "order": -1}], "unique": False, "name": None},
    ])
    original = normalize_data_contract_indexes(_contract("app_wide", indexes=[
        {"keys": [{"field": "task_id", "order": 1}], "unique": True, "name": None},
    ]))
    unscoped_name = original["surfaces"][0]["collections"][0]["indexes"][0]["name"]
    indexes = compile_data_contract(contract)["surfaces"][0]["collections"][0]["indexes"]
    assert indexes[0]["keys"] == [{"field": "user_id", "order": 1}, {"field": "task_id", "order": 1}]
    assert indexes[0]["unique"] is True and indexes[0]["name"] != unscoped_name
    assert indexes[1]["keys"] == [{"field": "user_id", "order": 1}, {"field": "title", "order": 1}]
    assert indexes[1]["name"] == "explicit_title"
    assert indexes[2]["keys"] == [{"field": "user_id", "order": 1}, {"field": "task_id", "order": -1}]
    assert indexes[3]["keys"] == [{"field": "created_at", "order": -1}]
    # A recorded contract already carrying the unscoped derived name is re-keyed and renamed.
    recorded = deepcopy(contract)
    recorded["surfaces"][0]["collections"][0]["indexes"][0]["name"] = unscoped_name
    assert compile_data_contract(recorded) == compile_data_contract(contract)
    assert compile_data_contract(compile_data_contract(contract)) == compile_data_contract(contract)


def test_app_wide_and_partial_contracts_keep_their_unique_keys():
    for contract in (_contract("app_wide"), _contract()):
        collection = contract["surfaces"][0]["collections"][0]
        if collection["tenancy"] != "app_wide":
            for field in ("tenancy", "owner_field", "entity"):
                collection.pop(field)
        indexes = normalize_data_contract_indexes(contract)["surfaces"][0]["collections"][0]["indexes"]
        assert indexes[0]["keys"] == [{"field": "task_id", "order": 1}]


# --------------------------------------------------------------------------- schemas.py


def test_schemas_are_rendered_from_the_contract_with_one_record_representation():
    source = render_module_schemas(MODULE, [_collection()])
    namespace: dict = {}
    exec(source, namespace)
    assert namespace["TASK_ID_FIELD"] == "task_id" and namespace["TASK_OWNER_FIELD"] == "user_id"
    assert namespace["TASK_LOOKUP_FIELD"] == "task_id"
    assert namespace["task_hook_values"]({"title": "A", "user_id": "x", "created_at": "y", "task_id": "custom"}, "create") == {"title": "A", "user_id": "x"}
    assert namespace["task_hook_values"]({"title": "A", "task_id": "custom", "updated_at": "y", "user_id": "x"}, "update") == {"title": "A"}
    assert namespace["TASK_CREATED_AT_FIELD"] == "created_at" and namespace["TASK_UPDATED_AT_FIELD"] == "updated_at"
    assert namespace["TASK_WRITABLE_FIELDS"] == ("title", "description", "is_completed", "priority")
    assert namespace["TASK_REQUIRED_CREATE_FIELDS"] == ("title",)
    assert namespace["TASK_DEFAULTS"] == {"is_completed": False, "priority": "normal"}
    assert namespace["TASK_NULLABLE_FIELDS"] == ("description",)
    assert namespace["task_create_values"]({"title": "A", "user_id": "forged", "task_id": "x"}) == {
        "title": "A", "description": None, "is_completed": False, "priority": "normal",
    }
    assert namespace["task_update_changes"]({"task_id": "x", "title": "B", "created_at": "never"}) == {"title": "B"}
    assert namespace["serialize_task"]({"_id": 1, "task_id": "x", "title": "A", "secret": "no"}) == {"task_id": "x", "title": "A"}
    assert namespace["new_task_id"]() != namespace["new_task_id"]()
    assert "    description: str | None" + chr(10) in source and "    created_at: datetime" + chr(10) in source
    assert scan_module_persistence({SCHEMAS: source}) == []
    assert audit_module_runtime_quality([{"filename": SCHEMAS, "content": source}]) == []


def test_model_authored_schemas_are_overwritten_with_a_warning(caplog):
    """D7: the file is code-owned, so an authored copy is replaced, never a dead end."""
    files = extract_code_file_map_from_payload(_closed())
    files[SCHEMAS] = "class TaskCreateRequest(dict):\n    pass\n"
    with caplog.at_level(logging.WARNING):
        rendered = materialize_module_schemas(files, app_build_plan=_plan(), data_contract=_contract())
    assert "class TaskRecord(TypedDict, total=False):" in rendered[SCHEMAS]
    assert any("SCHEMAS_OVERWRITTEN" in record.message and SCHEMAS in record.message for record in caplog.records)
    files[SCHEMAS] = rendered[SCHEMAS]
    caplog.clear()
    assert materialize_module_schemas(files, app_build_plan=_plan(), data_contract=_contract()) == rendered
    assert not caplog.records
    assert materialize_task_module_schemas(
        {MANIFEST: files[MANIFEST]}, task={"owned_paths": [SCHEMAS]}, app_build_plan=_plan(), data_contract=_contract(),
    ) == rendered
    assert materialize_task_module_schemas({}, task={"owned_paths": [f"{BACKEND}/handler.py"]}, app_build_plan=_plan(), data_contract=_contract()) == {}
    assert materialize_module_schemas(files, app_build_plan=None, data_contract=_contract()) == {}
    assert code_owned_schema_paths([SCHEMAS, f"{BACKEND}/handler.py"], app_build_plan=_plan(), data_contract=_contract()) == {SCHEMAS}


# --------------------------------------------------------------------------- implementations


def test_constructed_writes_pass_module_checks_scanner_and_runtime_quality():
    files = _files()
    result = validate_module_implementation_contract(files)
    assert result["passed"], result["failed_tests"]
    assert scan_module_persistence(files) == []
    assert audit_module_runtime_quality([{"filename": path, "content": content} for path, content in files.items()]) == []
    assert materialize_module_write_implementations(files, app_build_plan=_plan(), data_contract=_contract()) == {}
    assert materialize_module_actions(files, app_build_plan=_plan(), data_contract=_contract()) == {}
    for path in (f"{BACKEND}/handler.py", f"{BACKEND}/service.py", f"{BACKEND}/repo.py"):
        assert all(f"def {name}" in files[path] for name in WRITES), path


def test_implementations_require_declared_actions_and_respect_task_ownership():
    files = extract_code_file_map_from_payload(_output())
    with pytest.raises(ValueError, match="construct 'create_task' in module.yaml"):
        materialize_module_write_implementations(files, app_build_plan=_plan(), data_contract=_contract())
    files = extract_code_file_map_from_payload(_closed())
    changes = materialize_module_write_implementations(
        files, app_build_plan=_plan(), data_contract=_contract(), owned_paths=[f"{BACKEND}/service.py"],
    )
    assert list(changes) == [f"{BACKEND}/service.py"]


def test_model_authored_canonical_crud_is_replaced_and_hooks_are_kept():
    authored = {
        f"{BACKEND}/handler.py": (
            "class TaskManagementHandler:\n"
            "    async def create_task(self, ctx, **params):\n        return {'created': params['title']}\n"
            "    async def complete_task(self, ctx, **params):\n        return {'done': True}\n"
        ),
        f"{BACKEND}/service.py": (
            "async def create_task(ctx, values):\n    return {'stale': True}\n\n"
            "async def before_create_task(ctx, values):\n    return {**values, 'title': values['title'].strip()}\n"
        ),
        f"{BACKEND}/repo.py": "async def create_task(ctx, values):\n    return {'stale': True}\n",
    }
    files = _files(model_files=authored)
    assert "return {'created': params['title']}" not in files[f"{BACKEND}/handler.py"]
    assert "async def complete_task" in files[f"{BACKEND}/handler.py"]
    assert "'stale'" not in files[f"{BACKEND}/service.py"] and "'stale'" not in files[f"{BACKEND}/repo.py"]
    assert "async def before_create_task" in files[f"{BACKEND}/service.py"]


class _Cursor:
    def __init__(self, rows):
        self.rows = deepcopy(rows)

    def sort(self, fields):
        for name, direction in reversed(fields):
            self.rows.sort(key=lambda row: str(row.get(name)), reverse=direction < 0)
        return self

    def limit(self, limit):
        self.rows = self.rows[:limit]
        return self

    async def to_list(self, length=None):
        return deepcopy(self.rows if length is None else self.rows[:length])


def _matches(document, query):
    for key, value in query.items():
        if key == "$and":
            if not all(_matches(document, item) for item in value):
                return False
        elif key == "$nor":
            if any(_matches(document, item) for item in value):
                return False
        elif key == "$or":
            if not any(_matches(document, item) for item in value):
                return False
        elif isinstance(value, dict) and "$type" in value:
            if not isinstance(document.get(key), list):
                return False
        elif isinstance(value, dict) and "$in" in value:
            if document.get(key) not in value["$in"]:
                return False
        elif isinstance(value, dict) and "$ne" in value:
            if document.get(key) == value["$ne"]:
                return False
        elif isinstance(value, dict) and "$regex" in value:
            import re
            if not re.search(value["$regex"], str(document.get(key, "")), re.I):
                return False
        elif document.get(key) != value:
            return False
    return True


class _RawCollection:
    """A bounded in-memory stand-in for the Motor collection behind the real adapter."""

    def __init__(self):
        self.rows: list[dict] = []
        self.unique = set()

    async def insert_one(self, document):
        key = (document.get("user_id"), document.get("task_id"))
        if key in self.unique:
            raise RuntimeError("E11000 duplicate key")
        self.unique.add(key)
        self.rows.append({"_id": len(self.rows) + 1, **deepcopy(document)})
        return SimpleNamespace(inserted_id=len(self.rows))

    def find(self, query, projection=None, **kwargs):
        return _Cursor([row for row in self.rows if _matches(row, query)])

    async def find_one(self, query, projection=None, **kwargs):
        rows = await self.find(query).to_list(length=1)
        return rows[0] if rows else None

    async def count_documents(self, query, **kwargs):
        return len(await self.find(query).to_list())

    async def update_one(self, query, update, *, upsert=False, **kwargs):
        for row in self.rows:
            if _matches(row, query):
                row.update(deepcopy(update.get("$set", {})))
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)

    async def delete_one(self, query, **kwargs):
        for row in self.rows:
            if _matches(row, query):
                self.rows.remove(row)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    def aggregate(self, pipeline, **kwargs):
        rows = deepcopy(self.rows)
        for stage in pipeline:
            if "$match" in stage:
                rows = [row for row in rows if _matches(row, stage["$match"])]
            elif "$sort" in stage:
                rows = _Cursor(rows).sort(list(stage["$sort"].items())).rows
            elif "$skip" in stage:
                rows = rows[stage["$skip"]:]
            elif "$limit" in stage:
                rows = rows[:stage["$limit"]]
        return _Cursor(rows)


def _import_backend(tmp_path, monkeypatch, files, package_name):
    package = tmp_path / package_name
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    for path, source in files.items():
        if path.startswith(BACKEND):
            (package / Path(path).name).write_text(source, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    return importlib.import_module(f"{package_name}.handler"), importlib.import_module(f"{package_name}.service")


def _context(raw, user, tenancy="per_user"):
    principal = PersistencePrincipal(user_id=user, workspace_id=f"space-{user}")
    owner_field = "workspace_id" if tenancy == "per_workspace" else "user_id"
    collection = MongoPersistenceCollection(
        collection=raw, app_id="app", user_id=user, ownership=CollectionOwnership(tenancy, owner_field),
        principal=principal,
    )
    context = ModuleContext(app_id="app", user_id=user, workspace_id=f"space-{user}")
    context.persistence = SimpleNamespace(app_id="app", principal=principal, collection=lambda module, name: collection)
    context.events = []

    async def emit(event_type, payload):
        context.events.append((event_type, payload))

    context.emit = emit
    return context


@pytest.mark.asyncio
@pytest.mark.parametrize("tenancy", ["per_user", "per_workspace"])
async def test_generated_writes_scope_stamp_and_hook_through_the_real_adapter(tmp_path, monkeypatch, tenancy):
    hooks = {
        f"{BACKEND}/service.py": (
            "async def before_create_task(ctx, values):\n"
            "    if not values['title'].strip():\n"
            "        from mozaiksai.core.runtime import ModuleInputValidationError\n"
            "        raise ModuleInputValidationError('blank title')\n"
            "    return {**values, 'title': values['title'].strip()}\n\n"
            "async def before_update_task(ctx, record, changes):\n"
            "    return {**changes, 'title': changes.get('title', record['title']).upper()}\n"
        ),
    }
    # The approved design names the create and delete events; the rendered service emits them.
    files = _files(_contract(tenancy), model_files=hooks, design_surface_map=_design(["task.created", "task.deleted"]))
    handler_module, _service = _import_backend(tmp_path, monkeypatch, files, f"generated_writes_{tenancy}")
    handler = handler_module.TaskManagementHandler()
    raw = _RawCollection()
    owner = "user_id" if tenancy == "per_user" else "workspace_id"
    user_a, user_b = _context(raw, "a", tenancy), _context(raw, "b", tenancy)

    created = await handler.create_task(user_a, title="  First  ", description="one")
    item = created["item"]
    assert item["title"] == "First" and item["is_completed"] is False and item["priority"] == "normal"
    assert item[owner] == ("a" if tenancy == "per_user" else "space-a")
    assert item["created_at"] == item["updated_at"] and len(item["task_id"]) == 32
    assert set(item) == {"task_id", owner, "title", "description", "is_completed", "priority", "created_at", "updated_at"}
    assert user_a.events == [("domain.task.created", item)]
    stored = raw.rows[0]
    assert stored["app_id"] == "app" and stored[owner] == item[owner]
    with pytest.raises(ModuleInputValidationError):
        await handler.create_task(user_a, title="   ")
    second = await handler.create_task(user_a, title="Second")
    foreign = await handler.create_task(user_b, title="Theirs")
    assert (await handler.list_tasks(user_a))["total"] == 2
    assert [row["task_id"] for row in (await handler.list_tasks(user_b))["items"]] == [foreign["item"]["task_id"]]
    assert (await handler.get_tasks(user_a, id=item["task_id"]))["item"]["title"] == "First"
    with pytest.raises(ModuleRecordNotFoundError):
        await handler.get_tasks(user_b, id=item["task_id"])
    updated = await handler.update_task(user_a, task_id=item["task_id"], title="edited", is_completed=True)
    assert updated["item"]["title"] == "EDITED" and updated["item"]["is_completed"] is True
    assert updated["item"]["updated_at"] > updated["item"]["created_at"]
    assert updated["item"]["description"] == "one"
    for attempt in (
        handler.update_task(user_b, task_id=item["task_id"], title="stolen"),
        handler.delete_task(user_b, task_id=item["task_id"]),
    ):
        with pytest.raises(ModuleRecordNotFoundError):
            await attempt
    assert raw.rows[0]["title"] == "EDITED"
    assert await handler.delete_task(user_a, task_id=item["task_id"]) == {"deleted": True}
    deleted_event, deleted_payload = user_a.events[-1]
    assert deleted_event == "domain.task.deleted" and deleted_payload["task_id"] == item["task_id"]
    assert deleted_payload["title"] == "EDITED"  # the stored record at deletion
    assert [event for event, _payload in user_a.events] == [
        "domain.task.created", "domain.task.created", "domain.task.deleted",
    ]
    assert [row["task_id"] for row in (await handler.list_tasks(user_a))["items"]] == [second["item"]["task_id"]]
    with pytest.raises(PersistenceScopeError):
        await user_b.persistence.collection(MODULE, "tasks").insert_one({owner: "a", "task_id": "forged"})


@pytest.mark.asyncio
async def test_object_id_keyed_collections_create_and_address_records_by_id(tmp_path, monkeypatch):
    from bson import ObjectId

    fields = [_field("user_id"), _field("title"), _field("created_at", "datetime")]
    contract = _contract(search_by=None, fields=fields, indexes=[])
    actions = _actions(_closed(contract=contract))
    assert actions["delete_task"]["input_schema"]["required"] == ["_id"]
    files = _files(contract)
    handler_module, _service = _import_backend(tmp_path, monkeypatch, files, "generated_writes_object_id")
    handler = handler_module.TaskManagementHandler()
    raw = _RawCollection()
    identity = ObjectId()

    async def insert_one(document):
        raw.rows.append({"_id": identity, **deepcopy(document)})
        return SimpleNamespace(inserted_id=identity)

    raw.insert_one = insert_one
    user = _context(raw, "a")
    created = await handler.create_task(user, title="One")
    assert created["item"]["_id"] == str(identity) and "updated_at" not in created["item"]
    assert (await handler.update_task(user, _id=str(identity), title="Two"))["item"]["title"] == "Two"
    assert await handler.delete_task(user, _id=str(identity)) == {"deleted": True}
    with pytest.raises(ModuleRecordNotFoundError):
        await handler.delete_task(user, _id=str(ObjectId()))


# --------------------------------------------------------------------------- task batch and save


def test_real_context_save_renders_owned_schemas_and_writes():
    files = extract_code_file_map_from_payload(_closed())
    owned = [f"{BACKEND}/handler.py", f"{BACKEND}/service.py", f"{BACKEND}/repo.py"]
    bridge = ContextVariablesBridge({
        "app_build_plan": _plan(), "data_contract": _contract(),
        "code_files": [{"filename": MANIFEST, "content": files[MANIFEST]}],
        "current_build_task": {"owned_paths": owned},
    })
    result = save_generated_code(StructuredOutputOverlay(bridge, {"python_files": [], "code_files": []}))
    assert result["saved_files"] == sorted(owned)
    saved = {entry["filename"]: entry["content"] for entry in detach(bridge.get("code_files"))}
    assert "async def create_task" in saved[f"{BACKEND}/repo.py"]
    schemas_bridge = ContextVariablesBridge({
        "app_build_plan": _plan(), "data_contract": _contract(),
        "code_files": [{"filename": MANIFEST, "content": files[MANIFEST]}],
        "current_build_task": {"owned_paths": [SCHEMAS]},
    })
    result = save_generated_code(StructuredOutputOverlay(schemas_bridge, {"model_files": [], "code_files": []}))
    assert result["saved_files"] == [SCHEMAS]
    assert json.loads(json.dumps(schemas_bridge.snapshot()["data_contract"])) == _contract()


@pytest.mark.asyncio
async def test_task_batch_builds_writes_from_empty_model_output_and_applies_approved_gates(monkeypatch):
    tasks = [{
        "task_id": "contract", "task_type": "module_contract", "capability_pack_id": MODULE,
        "initial_agent": "ConfigMiddlewareAgent", "initial_message": "Declare custom actions.",
        "owned_paths": [MANIFEST], "depends_on": [],
    }, {
        "task_id": "models", "task_type": "data_models", "capability_pack_id": MODULE,
        "initial_agent": "ModelAgent", "initial_message": "Schemas are code-owned.",
        "owned_paths": [SCHEMAS], "depends_on": ["contract"],
    }, {
        "task_id": "service", "task_type": "business_services", "capability_pack_id": MODULE,
        "initial_agent": "ServiceAgent", "initial_message": "Implement hooks and custom actions.",
        # Plan review assigns the per_user module's code-rendered account-data handler here.
        "owned_paths": [
            f"{BACKEND}/handler.py", f"{BACKEND}/service.py", f"{BACKEND}/repo.py", f"{BACKEND}/policy.py",
            f"{BACKEND}/account_data_handler.py",
        ],
        "depends_on": ["contract", "models"],
    }]
    plan = {**_plan(), "build_tasks": tasks}
    bridge = ContextVariablesBridge({
        "app_build_plan": plan, "data_contract": _contract(), "app_task_batch_items": tasks,
        "design_surface_map": {"surfaces": [{
            "surface_id": MODULE, "surface_kind": "module", "owner": "app", "primary_entities": ["Task"],
            "owned_mutations": ["complete_task"], "custom_reads": [],
        }]},
        "subscription_contract": {"contract_required": True, "module_contract_updates": [
            {"module_id": MODULE, "action_id": "update_task", "entitlement_gate": "task.edit"},
        ]},
    })
    custom = {"id": "complete_task", "description": "Complete a task.", "handler_method": "complete_task",
              "api_surface": None, "permissions": [], "emits": [], "ask_context_safe": False,
              "input_schema": {"type": "object", "properties": [
                  {"name": "task_id", "type": "string", "required": True, "description": None, "enum_values": None, "items_type": None}]},
              "output_schema": {"type": "object", "properties": [
                  {"name": "item", "type": "object", "required": True, "description": None, "enum_values": None, "items_type": None}]}}
    authored = {
        f"{BACKEND}/handler.py": (
            "class TaskManagementHandler:\n"
            "    async def complete_task(self, ctx, **params):\n"
            "        from . import service\n        return await service.complete_task(ctx, params['task_id'])\n"
        ),
        f"{BACKEND}/service.py": (
            "async def complete_task(ctx, task_id):\n"
            "    from . import repo, schemas\n"
            "    record = await repo.update_task(ctx, task_id, {'is_completed': True})\n"
            "    return {'item': schemas.serialize_task(record)}\n\n"
            "async def before_create_task(ctx, values):\n    return values\n"
        ),
    }
    seen = {}

    async def run(_runner, request):
        worker = ContextVariablesBridge(request.context_variables)
        if request.task_id == "contract":
            output = _output([deepcopy(custom)])
        elif request.task_id == "models":
            raise AssertionError("the data_models task is code-owned and must not reach the worker")
        else:
            dependency = worker.get("dependency_task_outputs")["contract"]
            seen["contract_actions"] = [
                action["id"] for action in yaml.safe_load(extract_code_file_map_from_payload(detach(dependency))[MANIFEST])["actions"]
            ]
            dependency = worker.get("dependency_task_outputs")["models"]
            seen["schemas"] = extract_code_file_map_from_payload(detach(dependency))[SCHEMAS]
            assert detach(dependency)["_ag2_task_lifecycle"]["status"] == "code_owned"
            output = {"python_files": [], "code_files": [
                {"filename": path, "content": source} for path, source in authored.items()
            ]}
        return AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output=output)

    monkeypatch.setattr(task_batches.AG2TaskBatchRunner, "run", run)
    config = task_batches.load_task_batches_config(
        "AppGenerator", workflows_root=Path(__file__).resolve().parents[1] / "factory_app" / "workflows",
    )

    async def checkpoint(updates):
        for key, value in updates.items():
            bridge.set(key, value)

    snapshot = bridge.snapshot()
    await task_batches.execute_task_batches_for_trigger(
        workflow_name="AppGenerator", trigger_agent="AppPlanAgent", batches_config=config,
        agents={"ConfigMiddlewareAgent": object(), "ModelAgent": object(), "ServiceAgent": object()},
        context_variables=snapshot, chat_id="write-actions", app_id="write-actions", user_id="user-1",
        fresh_agents_per_task=False, parent_channel_id="write-actions-parent", checkpoint=checkpoint,
    )
    results = snapshot["app_task_batch_results"]
    assert snapshot["app_task_batch_status"] == "completed", results
    assert seen["contract_actions"] == ["complete_task", "create_task", "update_task", "delete_task", "get_tasks", "list_tasks"]
    assert "TASK_WRITABLE_FIELDS" in seen["schemas"]
    task_files = {
        path: source for candidate in (results["contract"], results["models"], results["service"])
        for path, source in extract_code_file_map_from_payload(candidate).items()
    }
    actions = {action["id"]: action for action in yaml.safe_load(task_files[MANIFEST])["actions"]}
    assert actions["update_task"]["entitlement_gate"] == "task.edit"
    assert "entitlement_gate" not in actions["create_task"] and actions["create_task"]["permissions"] == []
    assert "async def before_create_task" in task_files[f"{BACKEND}/service.py"]
    assert "async def complete_task" in task_files[f"{BACKEND}/handler.py"]
    for layer in ("handler", "service", "repo"):
        assert all(f"def {name}" in task_files[f"{BACKEND}/{layer}.py"] for name in WRITES), layer
    assert "_SCOPES" in task_files[f"{BACKEND}/policy.py"]
    assembled = {item["filename"]: item["content"] for item in _merge_code_files(
        [results["contract"], results["models"], results["service"]], app_build_plan=plan, data_contract=_contract(),
        subscription_contract=detach(bridge.get("subscription_contract")),
    )}
    assert {path: source for path, source in assembled.items() if path != MANIFEST} == {
        path: source for path, source in task_files.items() if path != MANIFEST
    }
    # Assembly re-closes the manifest and keeps null gate keys the typed task output popped.
    assembled_actions = yaml.safe_load(assembled[MANIFEST])["actions"]
    for action in assembled_actions:
        if action.get("entitlement_gate") is None:
            action.pop("entitlement_gate", None)
    assert assembled_actions == yaml.safe_load(task_files[MANIFEST])["actions"]
    result = validate_module_implementation_contract(assembled)
    assert result["passed"], result["failed_tests"]
    assert scan_module_persistence(assembled) == []


@pytest.mark.asyncio
async def test_data_models_task_with_nothing_to_author_completes_without_a_worker_turn(monkeypatch):
    tasks = [{
        "task_id": "contract", "task_type": "module_contract", "capability_pack_id": MODULE,
        "initial_agent": "ConfigMiddlewareAgent", "initial_message": "Declare.",
        "owned_paths": [MANIFEST], "depends_on": [],
    }, {
        "task_id": "models", "task_type": "data_models", "capability_pack_id": MODULE,
        "initial_agent": "ModelAgent", "initial_message": "Schemas.",
        "owned_paths": [SCHEMAS], "depends_on": ["contract"],
    }]
    bridge = ContextVariablesBridge({
        "app_build_plan": {**_plan(), "build_tasks": tasks}, "data_contract": _contract(), "app_task_batch_items": tasks,
    })
    calls = []

    async def run(_runner, request):
        calls.append(request.task_id)
        return AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output=_output())

    monkeypatch.setattr(task_batches.AG2TaskBatchRunner, "run", run)
    config = task_batches.load_task_batches_config(
        "AppGenerator", workflows_root=Path(__file__).resolve().parents[1] / "factory_app" / "workflows",
    )
    snapshot = bridge.snapshot()
    await task_batches.execute_task_batches_for_trigger(
        workflow_name="AppGenerator", trigger_agent="AppPlanAgent", batches_config=config,
        agents={"ConfigMiddlewareAgent": object(), "ModelAgent": object()}, context_variables=snapshot,
        chat_id="schemas", app_id="schemas", user_id="user-1", fresh_agents_per_task=False,
        parent_channel_id="schemas-parent", checkpoint=AsyncMock(),
    )
    results = snapshot["app_task_batch_results"]
    assert snapshot["app_task_batch_status"] == "completed", results
    assert calls == ["contract"]
    rendered = extract_code_file_map_from_payload(results["models"])
    assert list(rendered) == [SCHEMAS] and "TASK_WRITABLE_FIELDS" in rendered[SCHEMAS]
    assert results["models"]["_ag2_task_lifecycle"]["status"] == "code_owned"
    assert results["models"]["agent_message"].endswith("nothing left to author.")


# --------------------------------------------------------------------------- HTTP


@pytest.mark.parametrize("tenancy", ["per_user", "per_workspace"])
def test_two_users_drive_compiled_writes_over_http_with_closed_schemas_and_gates(tmp_path, monkeypatch, tenancy):
    """Real router, executor, JWT validation and persistence adapter; only Mongo and JWKS are in memory."""
    import time
    from collections import defaultdict

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from mozaiksai.core.auth.adapters.jwt_adapter import GenericJWTAdapter, JWTAdapterConfig
    from mozaiksai.core.ports.entitlement import EntitlementResult
    from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
    from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
    from mozaiksai.hosts.routers import modules as module_router

    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": "writes-test", "alg": "RS256", "use": "sig"})
    adapter = GenericJWTAdapter(config=JWTAdapterConfig(
        jwks_url="https://auth.test/jwks", issuer="https://auth.test", audience="writes-test",
    ))
    monkeypatch.setattr(adapter, "_get_jwks_client_async", AsyncMock(return_value=SimpleNamespace(
        get_signing_key=AsyncMock(return_value=jwk),
    )))
    monkeypatch.setattr("mozaiksai.core.auth.dependencies.get_auth_adapter", lambda: adapter)
    monkeypatch.setattr(module_router, "record_action_invocation", lambda **kwargs: None)
    hooks = PlatformHookRegistry()
    monkeypatch.setattr(module_router, "get_platform_hooks", lambda: hooks)
    monkeypatch.setattr("mozaiksai.core.runtime.composition.module_executor.get_platform_hooks", lambda: hooks)
    monkeypatch.setattr(ModuleExecutor, "_emit_dispatch_audit", AsyncMock())
    database = defaultdict(_RawCollection)
    monkeypatch.setattr("mozaiksai.core.runtime.persistence.mongo.get_mongo_client", lambda: defaultdict(lambda: database))

    def token(user, workspace):
        claims = {"sub": user, "iss": "https://auth.test", "aud": "writes-test", "app_id": "writes-app",
                  "workspace_id": workspace, "exp": int(time.time()) + 300}
        return {"Authorization": "Bearer " + jwt.encode(claims, key, algorithm="RS256", headers={"kid": "writes-test"})}

    class PaidUsersOnly:
        async def check(self, capability_id, *, app_id, user_id=None, tenant_id=None, workspace_id=None):
            granted = user_id == "user-a"
            return EntitlementResult(granted=granted, reason="active_grant" if granted else "no_grant")

    contract = _contract(tenancy)
    files = _files(contract)
    handler_module, _service = _import_backend(tmp_path, monkeypatch, files, f"generated_http_writes_{tenancy}")
    manifest = yaml.safe_load(files[MANIFEST])
    actions = {action["id"]: action["handler_method"] for action in manifest["actions"]}
    executor = ModuleExecutor(data_contract=contract, entitlement_checker=PaidUsersOnly())
    executor.register(
        MODULE, handler_module.TaskManagementHandler(), action_method_map=actions,
        action_schemas={action["id"]: {"input": action["input_schema"], "output": action["output_schema"]}
                        for action in manifest["actions"]},
        action_entitlements={"update_task": "task.edit"},
    )
    app = FastAPI()
    app.state.module_action_surfaces = {MODULE: dict.fromkeys(actions)}
    app.state.executor_registry = SimpleNamespace(module_executor=executor)
    app.include_router(module_router.router)
    client = TestClient(app)
    user_a, user_b = token("user-a", "workspace-a"), token("user-b", "workspace-b")
    url = f"/api/modules/{MODULE}/"
    owner = "user_id" if tenancy == "per_user" else "workspace_id"

    assert client.get(url + "list_tasks").status_code in (401, 403)
    created = client.post(url + "create_task", headers=user_a, json={"title": "A1", "description": "first"})
    assert created.status_code == 200, created.text
    item = created.json()["item"]
    assert item[owner] == ("user-a" if tenancy == "per_user" else "workspace-a")
    assert item["is_completed"] is False and item["priority"] == "normal" and len(item["task_id"]) == 32
    assert client.post(url + "create_task", headers=user_a, json={"title": "A2"}).status_code == 200
    assert client.post(url + "create_task", headers=user_b, json={"title": "B1"}).status_code == 200
    forged = client.post(url + "create_task", headers=user_b, json={"title": "forged", owner: "user-a"})
    assert forged.status_code in (400, 403), forged.text
    assert client.post(url + "create_task", headers=user_a, json={"description": "untitled"}).status_code == 400
    assert client.post(url + "create_task", headers=user_a, json={"title": "x", "priority": "urgent"}).status_code == 400
    assert client.post(url + "create_task", headers=user_a, json={"title": "x", "task_id": "chosen"}).status_code == 400
    stored = database[next(iter(database))].rows
    assert all(set(row) >= {"task_id", owner, "title", "is_completed", "priority", "created_at", "updated_at", "app_id"} for row in stored)
    assert {row["title"] for row in stored} == {"A1", "A2", "B1"}
    listed = client.get(url + "list_tasks", headers=user_a).json()
    assert listed["total"] == 2 and {row["title"] for row in listed["items"]} == {"A1", "A2"}
    assert [row["title"] for row in client.get(url + "list_tasks", headers=user_b).json()["items"]] == ["B1"]
    assert client.get(url + "get_tasks", params={"id": item["task_id"]}, headers=user_a).status_code == 200
    assert client.get(url + "get_tasks", params={"id": item["task_id"]}, headers=user_b).status_code == 404
    updated = client.post(url + "update_task", headers=user_a, json={"task_id": item["task_id"], "title": "A1-edited", "is_completed": True})
    assert updated.status_code == 200, updated.text
    assert updated.json()["item"]["title"] == "A1-edited" and updated.json()["item"]["description"] == "first"
    assert updated.json()["item"]["updated_at"] != updated.json()["item"]["created_at"]
    gated = client.post(url + "update_task", headers=user_b, json={"task_id": item["task_id"], "title": "stolen"})
    assert gated.status_code == 402, gated.text
    assert client.post(url + "delete_task", headers=user_b, json={"task_id": item["task_id"]}).status_code == 404
    assert client.post(url + "delete_task", headers=user_a, json={"task_id": item["task_id"]}).json() == {"deleted": True}
    assert client.post(url + "delete_task", headers=user_a, json={"task_id": item["task_id"]}).status_code == 404
    assert {row["title"] for row in client.get(url + "list_tasks", headers=user_a).json()["items"]} == {"A2"}



# --------------------------------------------------------------------------- record id, hooks, stale imports


def test_generated_id_is_the_entity_id_field_never_a_natural_search_key():
    """D3: search_by is a lookup; the generated id is <entity>_id, else id, else _id."""
    natural = _contract(search_by="title")
    actions = _actions(_closed(contract=natural))
    create = actions["create_task"]["input_schema"]
    assert "title" in create["properties"] and create["required"] == ["title"]
    assert "task_id" not in create["properties"]
    assert actions["update_task"]["input_schema"]["required"] == ["task_id"]
    assert actions["delete_task"]["input_schema"]["required"] == ["task_id"]
    files = _files(natural)
    assert "document['task_id'] = schemas.new_task_id()" in files[f"{BACKEND}/repo.py"]
    assert "document['title'] = schemas.new_task_id()" not in files[f"{BACKEND}/repo.py"]
    assert "TASK_LOOKUP_FIELD = 'title'" in files[SCHEMAS]
    assert "filters = {'title': id}" in files[f"{BACKEND}/repo.py"]  # canonical get keeps the natural lookup
    assert "async def load_task(ctx, id):\n    collection = ctx.persistence.collection('task_management', 'tasks')\n    record = await collection.find_one({'task_id': id})" in files[f"{BACKEND}/repo.py"]
    fields = [_field("email"), _field("user_id"), _field("display_name"), _field("created_at", "date")]
    no_declared_id = _contract(search_by="email", fields=fields, indexes=[])
    actions = _actions(_closed(contract=no_declared_id))
    assert sorted(actions["create_task"]["input_schema"]["properties"]) == ["display_name", "email"]
    assert actions["update_task"]["input_schema"]["required"] == ["_id"]
    owner_named_like_id = _contract(fields=[_field("user_id"), _field("title"), _field("created_at", "date")])
    owner_named_like_id["surfaces"][0]["collections"][0]["entity"] = "User"
    owner_named_like_id["surfaces"][0]["collections"][0]["search_by"] = "user_id"
    plan = {**_plan(), "capability_packs": [{**_plan()["capability_packs"][0], "primary_entities": ["User"]}]}
    actions = _actions(_closed(contract=owner_named_like_id, plan=plan))
    assert actions["update_user"]["input_schema"]["required"] == ["_id"]


@pytest.mark.asyncio
async def test_natural_key_lookup_keeps_user_entered_values_end_to_end(tmp_path, monkeypatch):
    files = _files(_contract(search_by="title"))
    handler_module, _service = _import_backend(tmp_path, monkeypatch, files, "generated_writes_natural_key")
    handler = handler_module.TaskManagementHandler()
    raw = _RawCollection()
    user = _context(raw, "a")
    created = (await handler.create_task(user, title="Buy milk"))["item"]
    assert created["title"] == "Buy milk" and len(created["task_id"]) == 32
    assert (await handler.get_tasks(user, id="Buy milk"))["item"]["task_id"] == created["task_id"]
    updated = await handler.update_task(user, task_id=created["task_id"], title="Buy oat milk")
    assert updated["item"]["title"] == "Buy oat milk"
    assert await handler.delete_task(user, task_id=created["task_id"]) == {"deleted": True}


@pytest.mark.parametrize("source, message", [
    ("def before_create_task(ctx, values):\n    return values\n",
     "hook 'before_create_task' must be async: async def before_create_task(ctx, values)"),
    ("async def before_update_task(ctx, changes):\n    return changes\n",
     "hook 'before_update_task' has signature (ctx, changes); expected async def before_update_task(ctx, record, changes)"),
    ("async def after_create_tasks(ctx, record):\n    return None\n",
     "hook 'after_create_tasks' names no canonical entity of this module; expected one of ['after_create_task'"),
    ("class TaskService:\n    async def before_delete_task(self, ctx, record):\n        return None\n",
     "hook 'before_delete_task' must be a module-level function, not a method"),
    ("async def before_create_task(ctx, values, *extra):\n    return values\n",
     "has signature"),
])
def test_hooks_are_validated_at_task_time_with_the_expected_signature(source, message):
    """D4: a hook the rendered service could not call is rejected where the model can fix it."""
    with pytest.raises(ValueError) as excinfo:
        validate_write_hooks(f"modules/{MODULE}/backend/service.py", source, ["task"])
    assert str(excinfo.value).startswith(f"modules/{MODULE}/backend/service.py:") and message in str(excinfo.value)
    files = extract_code_file_map_from_payload(_closed())
    files[f"{BACKEND}/service.py"] = source
    with pytest.raises(ValueError) as excinfo:
        materialize_module_write_implementations(files, app_build_plan=_plan(), data_contract=_contract())
    assert message in str(excinfo.value)


def test_valid_hooks_and_unrelated_functions_pass_hook_validation():
    validate_write_hooks("modules/x/backend/service.py", (
        "async def before_create_task(ctx, values):\n    return values\n"
        "async def after_update_task(ctx, record, changes, *, audit=None):\n    return None\n"
        "async def before_task_export(ctx):\n    return None\n"
        "def helper(values):\n    return values\n"
    ), ["task"])


@pytest.mark.asyncio
async def test_none_returning_hooks_leave_the_payload_unchanged_and_code_owned_keys_are_stripped(tmp_path, monkeypatch, caplog):
    """D4 and D9: None means unchanged; ids, owners and timestamps from a hook never reach the store."""
    hooks = {f"{BACKEND}/service.py": (
        "async def before_create_task(ctx, values):\n"
        "    values['title'] = values['title'].strip()\n"  # mutates in place, returns None
        "async def before_update_task(ctx, record, changes):\n"
        "    return {**changes, 'task_id': 'renamed', 'user_id': 'user-zzz', 'updated_at': 'never', 'created_at': 'never'}\n"
    )}
    files = _files(model_files=hooks)
    handler_module, _service = _import_backend(tmp_path, monkeypatch, files, "generated_writes_none_hooks")
    handler = handler_module.TaskManagementHandler()
    raw = _RawCollection()
    user = _context(raw, "a")
    created = (await handler.create_task(user, title="  First  "))["item"]
    assert created["title"] == "First"
    with caplog.at_level(logging.WARNING):
        updated = (await handler.update_task(user, task_id=created["task_id"], title="Second"))["item"]
    assert updated["task_id"] == created["task_id"] and updated["user_id"] == "a" and updated["title"] == "Second"
    assert updated["created_at"] == created["created_at"] and updated["updated_at"] != "never"
    stripped = [record.message for record in caplog.records if "HOOK_OUTPUT_STRIPPED" in record.message]
    assert stripped and "operation=update" in stripped[0]
    assert "['created_at', 'task_id', 'updated_at', 'user_id']" in stripped[0]
    assert (await handler.get_tasks(user, id=created["task_id"]))["item"]["title"] == "Second"


def test_stale_class_based_service_importing_retired_dtos_is_rejected_at_task_time():
    """D6: the recorded ServiceAgent shape fails admission with a message naming the fix."""
    files = extract_code_file_map_from_payload(_closed())
    files[f"{BACKEND}/service.py"] = (
        "from .repo import TaskManagerRepo\n"
        "from .schemas import TaskCreateRequest, TaskResponse\n\n"
        "class TaskManagerService:\n"
        "    async def summarize_tasks(self, *, ctx):\n        return {}\n"
    )
    with pytest.raises(ValueError) as excinfo:
        materialize_module_write_implementations(files, app_build_plan=_plan(), data_contract=_contract())
    message = str(excinfo.value)
    assert f"{BACKEND}/service.py:2: imports ['TaskCreateRequest', 'TaskResponse'] from .schemas" in message
    assert "'TaskRecord'" in message and "'serialize_task'" in message
    assert "author only write hooks, custom mutations and custom reads" in message
    files[f"{BACKEND}/service.py"] = "from .schemas import TaskRecord, serialize_task\n\nasync def summarize_tasks(ctx):\n    return {}\n"
    assert f"{BACKEND}/service.py" in materialize_module_write_implementations(files, app_build_plan=_plan(), data_contract=_contract())


@pytest.mark.parametrize("declared_access", [
    {"api_surface": None, "permissions": []},
    {"api_surface": None, "permissions": ["task.write"]},
])
def test_fully_declared_but_unrestricted_app_wide_writes_are_still_rejected(declared_access):
    """D1: a model that declares every access key but no restriction does not get open writes."""
    declared = [
        {"id": name, "description": name, "handler_method": name, "ask_context_safe": False, "emits": [],
         "input_schema": {"type": "object", "properties": [], "description": None, "items_type": None},
         "output_schema": {"type": "object", "properties": [], "description": None, "items_type": None},
         **declared_access}
        for name in WRITES
    ]
    output = _output([{"id": "archive_task", "handler_method": "archive_task", "api_surface": "admin_internal"}, *declared])
    with pytest.raises(ValueError) as excinfo:
        _closed(output, _contract("app_wide"))
    message = str(excinfo.value)
    assert "canonical writes ['create_task', 'update_task', 'delete_task'] are not constructed open" in message
    assert "restricted by api_surface internal or admin_internal, an approved entitlement gate, or permissions declared in config/auth.yaml" in message
    gated = deepcopy(output)
    subscription = {"contract_required": True, "module_contract_updates": [
        {"module_id": MODULE, "action_id": name, "entitlement_gate": "task.staff"} for name in WRITES
    ]}
    reads = [action for action in _actions(_closed()).values() if action["id"] in {"get_tasks", "list_tasks"}]
    gated["module_contract"]["module_yaml"]["actions"].extend(reads)
    closed = _closed(gated, _contract("app_wide"), subscription_contract=subscription)
    assert all(_actions(closed)[name]["api_surface"] is None for name in WRITES)  # gated, authored, preserved


@pytest.mark.asyncio
async def test_create_hook_returning_a_foreign_owner_is_rejected_by_the_runtime(tmp_path, monkeypatch):
    """A create hook cannot forge ownership: the value reaches the adapter, which refuses it."""
    source = "async def before_create_task(ctx, values):" + chr(10) + "    return {**values, 'user_id': 'user-zzz'}" + chr(10)
    files = _files(model_files={f"{BACKEND}/service.py": source})
    handler_module, _service = _import_backend(tmp_path, monkeypatch, files, "generated_writes_forged_owner")
    raw = _RawCollection()
    with pytest.raises(PersistenceScopeError, match="cannot override ownership field 'user_id'"):
        await handler_module.TaskManagementHandler().create_task(_context(raw, "a"), title="One")
    assert raw.rows == []

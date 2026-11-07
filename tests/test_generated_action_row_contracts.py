"""Custom read rows cross the typed producer, page and runtime boundaries exactly."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
from mozaiksai.core.runtime.composition.schema_validation import validate_json_schema
from mozaiksai.core.semantics.closed_contract_schema import import_closed_contract_schema
from mozaiksai.core.workflow.generator_support.code_files import (
    _materialize_schema_contract,
    extract_code_file_map_from_payload,
)
from mozaiksai.core.workflow.generator_support.module_smoke_fixtures import _schema_at
from mozaiksai.core.workflow.generator_support.page_data_bindings import page_data_binding_errors
from mozaiksai.core.workflow.outputs.structured import (
    build_models_from_config,
    get_provider_response_model,
)
from tests.test_module_executor_dispatch import _request

ROOT = Path(__file__).resolve().parents[1]
# Minimal response shapes reproducing four actual custom-read page failures.
# Field names are explicit fixture declarations, never extracted from descriptions.
CASES = [
    ("tool_catalog", "search_tools", "tool_id name category description availability_status condition_note location_note updated_at"),
    ("borrowing", "list_my_loans", "borrow_request_id tool_id tool_name request_status loan_status requested_start_date due_date returned_at created_at"),
    ("borrowing", "list_pending_requests", "borrow_request_id tool_name requester_display_name requested_duration_days reason status created_at decision_at"),
    ("borrowing", "list_overdue_loans", "loan_id tool_name borrower_display_name due_date days_overdue requested_duration_days borrow_request_id"),
]


@pytest.fixture(scope="module")
def models():
    definitions = yaml.safe_load((ROOT / "factory_app/workflows/AppGenerator/structured_outputs.yaml").read_text())['models']
    return build_models_from_config(definitions, exact_model_ids=frozenset(definitions))


def _field(name, kind="string", *, nullable=False, required=True, **kwargs):
    return {"name": name, "type": kind, "description": None, "required": required,
            "enum_values": [], "items_type": None, "nullable": nullable, **kwargs}


def _rows(names="record_id display_name closed_at", *, declared=True):
    fields = [_field(name, "integer" if name in {"days_overdue", "requested_duration_days"} else "string",
                     nullable=name in {"closed_at", "loan_status", "due_date", "returned_at", "decision_at"})
              for name in names.split()]
    rows = _field("items", "array", items_type="object")
    rows["description"] = "Rows contain " + names
    if declared:
        rows["items_properties"] = fields
    return {"type": "object", "description": None, "items_type": None,
            "properties": [rows, _field("total", "integer")]}


def _payload(module, actions):
    return {"module_contract": {"module_id": module, "module_yaml": {"module": {"id": module}, "actions": actions}}}


def _action(name, outputs):
    return {"id": name, "input_schema": {"type": "object", "properties": [], "items_type": None},
            "output_schema": outputs}


@pytest.mark.parametrize(("module", "action", "names"), CASES)
def test_actual_custom_read_shapes_round_trip_to_table_and_runtime(models, module, action, names):
    typed = models["JsonSchemaContract"].model_validate(_rows(names)).model_dump(mode="json")
    payload = _payload(module, [_action(action, typed)])
    before = deepcopy(payload)
    files = extract_code_file_map_from_payload(payload)
    contract = yaml.safe_load(files[f"modules/{module}/module.yaml"])["actions"][0]
    outputs = contract["output_schema"]
    rows = outputs["properties"]["items"]["items"]
    assert list(rows["properties"]) == names.split()
    page = {"name": "Records", "sections": [{"id": "records", "primitive": "DataTable", "config": {
        "api_endpoint": f"/api/modules/{module}/{action}", "data_key": "items", "total_key": "total",
        "columns": [{"key": name} for name in names.split()],
    }}]}
    assert page_data_binding_errors(page, {f"{module}/{action}": contract}) == []
    page["sections"][0]["config"]["columns"].append({"key": "invented"})
    assert "'invented' is not a declared row field" in page_data_binding_errors(page, {f"{module}/{action}": contract})[0]
    value = {"items": [{name: None if isinstance(field["type"], list) else 3 if field["type"] == "integer" else "example"
                        for name, field in rows["properties"].items()}], "total": 1}
    assert validate_json_schema(value, outputs) is None
    missing = deepcopy(value)
    missing["items"][0].pop(names.split()[0])
    assert validate_json_schema(missing, outputs).category == "value_invalid"
    wrong = deepcopy(value)
    wrong["items"][0][names.split()[0]] = {"unexpected": "object"}
    assert validate_json_schema(wrong, outputs).category == "value_invalid"
    assert payload == before


@pytest.mark.parametrize("module", ["tool_catalog", "borrowing"])
def test_description_only_rows_fail_at_producer_with_all_action_names(module):
    actions = [_action(action, _rows(names, declared=False)) for owner, action, names in CASES if owner == module]
    payload = _payload(module, actions)
    before = deepcopy(payload)
    with pytest.raises(ValueError, match="nonempty items_properties") as caught:
        extract_code_file_map_from_payload(payload)
    for action in actions:
        assert f"{module}/{action['id']}.output_schema" in str(caught.value)
    assert payload == before


@pytest.mark.parametrize("invalid", ["empty_rows", "empty_object", "duplicate", "wrong_kind", "recursive", "unknown", "object_leaf", "array_leaf_object"])
def test_malformed_members_fail_before_materialization(invalid):
    schema = _rows()
    row = schema["properties"][0]
    member = row["items_properties"][0]
    if invalid == "empty_rows":
        row["items_properties"] = []
    elif invalid == "empty_object":
        row.update(type="object", items_type=None, items_properties=None, properties=[])
    elif invalid == "duplicate":
        row["items_properties"].append(deepcopy(member))
    elif invalid == "wrong_kind":
        row.update(type="string", items_type=None)
    elif invalid == "recursive":
        member["properties"] = [_field("deeper")]
    elif invalid == "unknown":
        member["type"] = "imaginary"
    elif invalid == "object_leaf":
        member["type"] = "object"
    else:
        member.update(type="array", items_type="object")
    before = deepcopy(schema)
    with pytest.raises(ValueError):
        _materialize_schema_contract(schema)
    assert schema == before


def test_provider_model_is_finite_and_rejects_recursive_members(models):
    model = models["JsonSchemaContract"]
    value = model.model_validate(_rows()).model_dump(mode="json")
    Draft202012Validator(get_provider_response_model(model).model_json_schema()).validate(value)
    member = value["properties"][0]["items_properties"][0]
    for invalid in ({**member, "properties": []}, {**member, "type": "object"}, {**member, "items_type": "imaginary"}):
        with pytest.raises(ValidationError):
            models["JsonSchemaLeafProperty"].model_validate(invalid)


def test_nested_request_event_and_policy_consumers_keep_their_boundaries():
    schema = {"type": "object", "items_type": None, "properties": [
        _field("item", "object", properties=[_field("record_id"), _field("tags", "array", items_type="string")]),
        _field("rows", "array", items_type="object", items_properties=[_field("record_id")]),
    ]}
    payload = _payload("records", [_action("submit", schema)])
    bundle = payload["module_contract"]
    bundle["module_yaml"]["actions"][0]["input_schema"] = schema
    bundle["module_yaml"]["capabilities"] = [{"input_schema": schema}]
    bundle["events_yaml"] = {"events": [{"payload_schema": schema}]}
    bundle["policy_hooks_yaml"] = {"hooks": [{"input_schema": schema, "output_schema": schema}]}
    files = extract_code_file_map_from_payload(payload)
    module = yaml.safe_load(files["modules/records/module.yaml"])
    request = module["actions"][0]["input_schema"]
    assert request == module["capabilities"][0]["input_schema"]
    import_closed_contract_schema(request)
    value = {"item": {"record_id": "a", "tags": ["one"]}, "rows": [{"record_id": "b"}]}
    assert validate_json_schema(value, request) is None
    for key in ("item", "rows"):
        extra = deepcopy(value)
        (extra[key] if key == "item" else extra[key][0])["undeclared"] = True
        assert validate_json_schema(extra, request).category == "value_invalid"
    events = yaml.safe_load(files["modules/records/contracts/events.yaml"])
    hook = yaml.safe_load(files["modules/records/contracts/policy_hooks.yaml"])["hooks"][0]
    for output in [module["actions"][0]["output_schema"], events["events"][0]["payload_schema"], *hook.values()]:
        assert validate_json_schema(value, output) is None
        assert _schema_at(output, ["item", "record_id"])["type"] == "string"
        with pytest.raises(ValueError, match="not a declared object field"):
            _schema_at(output, ["item", "invented"])


def test_nullable_required_and_enum_are_independent_and_requests_remain_exact():
    schema = {"type": "object", "properties": [_field("item", "object", properties=[
        _field("state", nullable=True, enum_values=["pending", "done"]),
        _field("note", required=False),
    ])], "items_type": None}
    result = _materialize_schema_contract(schema)
    assert validate_json_schema({"item": {"state": None}}, result) is None
    assert validate_json_schema({"item": {"state": "done"}}, result) is None
    for value in ({"item": {}}, {"item": {"state": "other"}}, {"item": {"state": None, "note": None}}):
        assert validate_json_schema(value, result).category == "value_invalid"
    with pytest.raises(ValueError, match="closed requests require false"):
        _materialize_schema_contract(schema, closed_request=True)


def test_root_array_members_use_the_same_bounded_shape():
    root = {"type": "array", "properties": [], "items_type": "object", "items_properties": [_field("id")]}
    result = _materialize_schema_contract(root)
    assert validate_json_schema([{"id": "one"}], result) is None
    assert validate_json_schema([{}], result).category == "value_invalid"


@pytest.mark.asyncio
async def test_service_response_uses_the_admitted_joined_row_contract():
    schema = _materialize_schema_contract(_rows())
    response = {"items": [{"record_id": "a", "display_name": "Joined display", "closed_at": None}], "total": 1}

    class Handler:
        async def list_rows(self, ctx):
            return response

    executor = ModuleExecutor()
    executor.register("records", Handler(), action_method_map={"list_rows": "list_rows"},
                      action_schemas={"list_rows": {"output": schema}})
    result = await executor.execute(_request(module="records", action="list_rows"))
    assert result.success and result.data == response
    assert validate_json_schema(result.data, schema) is None

"""Fixture data cannot select acceptance outcomes, permissions or undeclared calls."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from mozaiksai.core.runtime.app.layout_registry import (
    PathScope,
    RuntimeConsumerIdentifier,
    match_path,
)
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
from mozaiksai.core.workflow.generator_support.module_smoke_fixtures import (
    SCHEMA_VERSION,
    fixture_record_identity,
    load_module_smoke_fixtures,
    parse_module_smoke_fixtures,
    resolve_fixture_params,
)
from mozaiksai.core.workflow.outputs.structured import build_models_from_config

ROOT = Path(__file__).resolve().parents[1]
PATH = "modules/borrowing/contracts/fixtures.yaml"


def _schema(**fields):
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {key: {"type": kind} for key, kind in fields.items()},
        "required": list(fields),
    }


def _action(name, permission, inputs, outputs):
    return {
        "id": name,
        "permissions": [permission],
        "input_schema": inputs,
        "output_schema": outputs,
    }


def _literal(field, value):
    return {"field": field, "source": "literal", "value_json": json.dumps(value)}


def _response(field, step, path):
    return {"field": field, "source": "response", "step_id": step, "response_path": path}


def _fixture():
    return {
        "action": "approve_borrow_request",
        "roles": ["admin"],
        "setup": [
            {
                "id": "tool",
                "module": "tool_catalog",
                "action": "create_tool",
                "roles": ["admin"],
                "params": [_literal("name", "Observer-created tool")],
            },
            {
                "id": "request",
                "module": "borrowing",
                "action": "create_borrow_request",
                "roles": ["member"],
                "params": [
                    _response("tool_id", "tool", ["tool_id"]),
                    {"field": "start_date", "source": "relative_date", "offset_days": 2},
                ],
            },
        ],
        "params": [_response("borrow_request_id", "request", ["borrow_request_id"])],
    }


def _files(fixture=None):
    auth = yaml.safe_load((ROOT / "factory_app/app/config/auth.yaml").read_text(encoding="utf-8"))
    auth["authorization"] = {
        "roles": [
            {"id": "admin", "permissions": ["tools.create", "requests.approve"]},
            {"id": "member", "permissions": ["requests.create", "requests.update"]},
            {"id": "reader", "permissions": ["requests.read"]},
        ]
    }
    modules = {
        "tool_catalog": [
            _action(
                "create_tool", "tools.create", _schema(name="string"), _schema(tool_id="string")
            )
        ],
        "borrowing": [
            _action(
                "create_borrow_request",
                "requests.create",
                _schema(tool_id="string", start_date="string"),
                _schema(borrow_request_id="string"),
            ),
            _action(
                "approve_borrow_request",
                "requests.approve",
                _schema(borrow_request_id="string"),
                _schema(loan_id="string"),
            ),
            _action(
                "update_borrow_request",
                "requests.update",
                _schema(borrow_request_id="string", reason="string"),
                _schema(borrow_request_id="string"),
            ),
        ],
    }
    data = {
        "surfaces": [
            {
                "surface_id": "borrowing",
                "surface_kind": "module",
                "collections": [
                    {
                        "name": "borrow_requests",
                        "entity": "BorrowRequest",
                        "fields": [
                            {"name": "borrow_request_id", "type": "string"},
                            {"name": "reason", "type": "string"},
                        ],
                    },
                ],
            }
        ]
    }
    return {
        **{
            f"modules/{module}/module.yaml": yaml.safe_dump(
                {"module": {"id": module}, "actions": actions}
            )
            for module, actions in modules.items()
        },
        "config/auth.yaml": yaml.safe_dump(auth),
        "data/contract.json": json.dumps(data),
        PATH: yaml.safe_dump(
            {"schema_version": SCHEMA_VERSION, "fixtures": [fixture or _fixture()]}
        ),
    }


def test_multi_entity_inputs_resolve_only_observed_results_and_run_clock():
    files = _files()
    original = deepcopy(files)
    fixture = load_module_smoke_fixtures(files)["borrowing", "approve_borrow_request"]
    assert fixture.roles == ["admin"]
    assert [step.roles for step in fixture.setup] == [["admin"], ["member"]]
    now = datetime(2026, 11, 7, 23, 30, tzinfo=UTC)
    tool = resolve_fixture_params(
        fixture.setup[0].params, responses={}, actor_subject="subject-a", record=None, now=now
    )
    assert tool == {"name": "Observer-created tool"}
    request = resolve_fixture_params(
        fixture.setup[1].params,
        responses={"tool": {"tool_id": "observed-tool-id"}},
        actor_subject="subject-a",
        record=None,
        now=now,
    )
    assert request == {"tool_id": "observed-tool-id", "start_date": "2026-11-09"}
    approval = resolve_fixture_params(
        fixture.params,
        responses={"request": {"borrow_request_id": "observed-request-id"}},
        actor_subject="subject-a",
        record=None,
        now=now,
    )
    assert approval == {"borrow_request_id": "observed-request-id"}
    assert files == original


@pytest.mark.parametrize("output_field", [
    {"type": ["string", "null"]},
    {"anyOf": [{"type": "string"}, {"type": "null"}]},
])
def test_nullable_response_reference_rejects_with_bounded_contract_diagnostic(output_field):
    files = _files()
    path = "modules/tool_catalog/module.yaml"
    module = yaml.safe_load(files[path])
    module["actions"][0]["output_schema"]["properties"]["tool_id"] = output_field
    files[path] = yaml.safe_dump(module)
    with pytest.raises(ValueError, match="response field requires one declared type"):
        load_module_smoke_fixtures(files)


@pytest.mark.parametrize(
    "case",
    [
        "unknown_module",
        "unknown_action",
        "internal",
        "admin_internal",
        "unknown_role",
        "missing_role",
        "unrelated_role",
        "redundant_role",
        "unknown_input",
        "missing_input",
        "literal_type",
        "response_type",
        "response_field",
        "response_root",
        "forward_reference",
        "cycle",
        "duplicate_step",
        "duplicate_param",
        "duplicate_role",
        "target_unknown_action",
        "target_unknown_role",
        "expected_status",
        "skip",
        "record_on_custom",
        "record_in_setup",
        "input_open",
        "input_extra_keyword",
        "source_fields",
        "offset_bound",
        "offset_bool",
        "expression_source",
        "wildcard_path",
        "duplicate_literal_key",
        "nonfinite_literal",
        "setup_limit",
    ],
)
def test_invalid_fixtures_fail_before_any_execution(case):
    fixture = _fixture()
    files = _files()
    manifests = {
        key: yaml.safe_load(value) for key, value in files.items() if key.endswith("module.yaml")
    }
    first, second = fixture["setup"]
    if case == "unknown_module":
        first["module"] = "missing"
    elif case == "unknown_action":
        first["action"] = "missing"
    elif case in {"internal", "admin_internal"}:
        manifests["modules/tool_catalog/module.yaml"]["actions"][0]["api_surface"] = case
    elif case == "unknown_role":
        first["roles"] = ["unknown"]
    elif case == "missing_role":
        first["roles"] = []
    elif case == "unrelated_role":
        first["roles"].append("reader")
    elif case == "redundant_role":
        auth = yaml.safe_load(files["config/auth.yaml"])
        auth["authorization"]["roles"].append({"id": "duplicate", "permissions": ["tools.create"]})
        files["config/auth.yaml"] = yaml.safe_dump(auth)
        first["roles"].append("duplicate")
    elif case == "unknown_input":
        first["params"].append(_literal("extra", "value"))
    elif case == "missing_input":
        first["params"] = []
    elif case == "literal_type":
        first["params"][0]["value_json"] = "2"
    elif case == "response_type":
        manifests["modules/tool_catalog/module.yaml"]["actions"][0]["output_schema"] = _schema(
            tool_id="integer"
        )
    elif case == "response_field":
        second["params"][0]["response_path"] = ["missing"]
    elif case == "response_root":
        second["params"][0]["response_path"] = []
    elif case == "forward_reference":
        first["params"] = [_response("name", "request", ["borrow_request_id"])]
    elif case == "cycle":
        second["params"][0]["step_id"] = "request"
    elif case == "duplicate_step":
        second["id"] = "tool"
    elif case == "duplicate_param":
        first["params"] *= 2
    elif case == "duplicate_role":
        first["roles"] *= 2
    elif case == "target_unknown_action":
        fixture["action"] = "missing"
    elif case == "target_unknown_role":
        fixture["roles"] = ["owner"]
    elif case in {"expected_status", "skip"}:
        fixture[case] = 200
    elif case == "record_on_custom":
        fixture["params"] = [
            {"field": "borrow_request_id", "source": "record", "record_field": "borrow_request_id"}
        ]
    elif case == "record_in_setup":
        first["params"] = [{"field": "name", "source": "record", "record_field": "reason"}]
    elif case == "input_open":
        manifests["modules/tool_catalog/module.yaml"]["actions"][0]["input_schema"][
            "additionalProperties"
        ] = True
    elif case == "input_extra_keyword":
        manifests["modules/tool_catalog/module.yaml"]["actions"][0]["input_schema"]["default"] = {}
    elif case == "source_fields":
        first["params"][0]["step_id"] = "tool"
    elif case == "offset_bound":
        second["params"][1]["offset_days"] = 3651
    elif case == "offset_bool":
        second["params"][1]["offset_days"] = True
    elif case == "expression_source":
        first["params"][0]["source"] = "python"
    elif case == "wildcard_path":
        second["params"][0]["response_path"] = ["*"]
    elif case == "duplicate_literal_key":
        first["params"][0]["value_json"] = '{"id":1,"id":2}'
    elif case == "nonfinite_literal":
        first["params"][0]["value_json"] = "NaN"
    elif case == "setup_limit":
        fixture["setup"] = [dict(first, id=f"step_{n}") for n in range(25)]
    files.update({key: yaml.safe_dump(value) for key, value in manifests.items()})
    files[PATH] = yaml.safe_dump({"schema_version": SCHEMA_VERSION, "fixtures": [fixture]})
    with pytest.raises(ValueError):
        load_module_smoke_fixtures(files)


def test_record_binding_uses_only_observer_current_declared_row():
    fixture = {
        "action": "update_borrow_request",
        "roles": ["member"],
        "setup": [],
        "params": [
            {"field": "borrow_request_id", "source": "record", "record_field": "borrow_request_id"},
            _literal("reason", "Changed reason"),
        ],
    }
    bound = load_module_smoke_fixtures(_files(fixture))["borrowing", fixture["action"]]
    now = datetime.now(UTC)
    params = resolve_fixture_params(
        bound.params,
        responses={},
        actor_subject="subject-a",
        record={"borrow_request_id": "actual-crud-row"},
        now=now,
    )
    assert params == {"borrow_request_id": "actual-crud-row", "reason": "Changed reason"}
    with pytest.raises(ValueError, match="record field is unavailable"):
        resolve_fixture_params(
            bound.params, responses={}, actor_subject="subject-a", record=None, now=now
        )
    fixture["params"][0]["record_field"] = "not_declared"
    with pytest.raises(ValueError, match="declared field"):
        load_module_smoke_fixtures(_files(fixture))


def test_runtime_missing_response_does_not_fabricate_identifier():
    fixture = load_module_smoke_fixtures(_files())["borrowing", "approve_borrow_request"]
    for responses in ({}, {"request": {}}, {"request": {"other": "not-the-id"}}):
        with pytest.raises(ValueError, match="unavailable"):
            resolve_fixture_params(
                fixture.params,
                responses=responses,
                actor_subject="subject-a",
                record=None,
                now=datetime.now(UTC),
            )


def test_default_scope_actions_require_no_added_roles():
    files = _files()
    manifest = yaml.safe_load(files["modules/tool_catalog/module.yaml"])
    manifest["actions"][0]["permissions"] = ["openid"]
    files["modules/tool_catalog/module.yaml"] = yaml.safe_dump(manifest)
    fixture = _fixture()
    fixture["setup"][0]["roles"] = []
    files[PATH] = yaml.safe_dump({"schema_version": SCHEMA_VERSION, "fixtures": [fixture]})
    assert load_module_smoke_fixtures(files)
    fixture["setup"][0]["roles"] = ["admin"]
    files[PATH] = yaml.safe_dump({"schema_version": SCHEMA_VERSION, "fixtures": [fixture]})
    with pytest.raises(ValueError, match="unrelated or redundant"):
        load_module_smoke_fixtures(files)


def test_actor_subject_and_relative_datetime_are_finite_sources():
    document = parse_module_smoke_fixtures(
        {
            "schema_version": SCHEMA_VERSION,
            "fixtures": [
                {
                    "action": "create_note",
                    "roles": [],
                    "setup": [],
                    "params": [
                        {"field": "author", "source": "actor_subject"},
                        {"field": "scheduled_at", "source": "relative_datetime", "offset_days": 1},
                    ],
                }
            ],
        }
    )
    assert resolve_fixture_params(
        document.fixtures[0].params,
        responses={},
        actor_subject="trusted-subject",
        record=None,
        now=datetime(2026, 11, 7, 2, tzinfo=UTC),
    ) == {
        "author": "trusted-subject",
        "scheduled_at": "2026-11-08T02:00:00+00:00",
    }


def test_structured_model_materialization_and_raw_companion_rules():
    config = yaml.safe_load(
        (ROOT / "factory_app/workflows/AppGenerator/structured_outputs.yaml").read_text(
            encoding="utf-8"
        )
    )
    models = build_models_from_config(config["models"])
    document = {"schema_version": SCHEMA_VERSION, "fixtures": [_fixture()]}
    typed = models["ModuleSmokeFixtures"].model_validate(document).model_dump(mode="json")
    assert parse_module_smoke_fixtures(typed).model_dump(exclude_none=True) == document
    payload = {"module_contract": {"module_id": "borrowing", "fixtures_yaml": typed}}
    files = extract_code_file_map_from_payload(payload)
    assert yaml.safe_load(files[PATH]) == document
    assert parse_module_smoke_fixtures(yaml.safe_load(files[PATH])) == parse_module_smoke_fixtures(
        document
    )
    with pytest.raises(ValueError, match="fixtures_yaml is null"):
        extract_code_file_map_from_payload(
            {
                "module_contract": {"module_id": "borrowing", "fixtures_yaml": None},
                "code_files": [{"filename": PATH, "content": yaml.safe_dump(document)}],
            }
        )
    assert PATH not in extract_code_file_map_from_payload(
        {"module_contract": {"module_id": "borrowing", "fixtures_yaml": None},
         "code_files": [{"filename": PATH, "content": "{}"}]}
    )
    payload["module_contract"]["fixtures_yaml"]["fixtures"][0]["expected_status"] = 200
    with pytest.raises(ValueError):
        extract_code_file_map_from_payload(payload)


def test_fixture_path_has_one_layout_owner_and_carry_forward_contract():
    from factory_app.refinement_harness.tools.read_carry_forward_module_contract import (
        _ALLOWED_CONTRACT_FILES,
    )
    from factory_app.refinement_harness.tools.resolve_carry_forward_preservation import (
        _PHASE_7A_MODULE_ALLOWLIST,
    )

    family = match_path(PATH, PathScope.APP_BUNDLE_ROOT).family
    assert family.path_template == "modules/{module_id}/contracts/fixtures.yaml"
    assert family.runtime_consumer is RuntimeConsumerIdentifier.NONE
    assert "contracts/fixtures.yaml" in _ALLOWED_CONTRACT_FILES
    assert "contracts/fixtures.yaml" in _PHASE_7A_MODULE_ALLOWLIST


def test_no_fixture_preserves_existing_observer_input_path():
    files = _files()
    files.pop(PATH)
    assert load_module_smoke_fixtures(files) == {}


def test_observer_copied_utf8_bytes_use_the_same_parser():
    files = _files()
    copied = {path: content.encode("utf-8") for path, content in files.items()}
    assert load_module_smoke_fixtures(copied) == load_module_smoke_fixtures(files)
    for invalid in (b"\xff", b" " * 131073):
        copied[PATH] = invalid
        with pytest.raises(ValueError):
            load_module_smoke_fixtures(copied)


def test_recursive_yaml_and_misplaced_companions_fail_closed():
    files = _files()
    files[PATH] = "&recursive {fixtures: [*recursive]}"
    with pytest.raises(ValueError, match="depth or node"):
        load_module_smoke_fixtures(files)
    files = _files()
    files["other/contracts/fixtures.yaml"] = files.pop(PATH)
    with pytest.raises(ValueError, match="canonical module companion path"):
        load_module_smoke_fixtures(files)


def test_deep_json_literal_has_a_validation_failure_not_parser_recursion():
    fixture = _fixture()
    fixture["setup"][0]["params"][0]["value_json"] = "[" * 1500 + "0" + "]" * 1500
    with pytest.raises(ValueError, match="literal exceeds (depth or node bounds|the JSON nesting bound)"):
        parse_module_smoke_fixtures({"schema_version": SCHEMA_VERSION, "fixtures": [fixture]})


@pytest.mark.parametrize("source", ["literal", "response", "actor_subject"])
def test_canonical_update_rejects_substitute_record_identity(source):
    fixture = _fixture()
    fixture.update(action="update_borrow_request", roles=["member"])
    identity = (_literal("borrow_request_id", "another-row") if source == "literal" else
                _response("borrow_request_id", "request", ["borrow_request_id"]) if source == "response" else
                {"field": "borrow_request_id", "source": "actor_subject"})
    fixture["params"] = [identity, _literal("reason", "Changed reason")]
    with pytest.raises(ValueError, match="update_borrow_request.*borrow_request_id.*source=record"):
        load_module_smoke_fixtures(_files(fixture))


def test_authored_get_identity_uses_declared_input_and_natural_lookup_field():
    collection = {"name": "borrow_requests", "entity": "BorrowRequest", "search_by": "reason",
                  "fields": [{"name": "borrow_request_id", "type": "string"},
                             {"name": "reason", "type": "string"}]}
    assert fixture_record_identity(None, collection=collection, operation="get") == ("id", "reason")
    assert fixture_record_identity(None, collection=collection, operation="delete") == (
        "borrow_request_id", "borrow_request_id")
    fixture = _fixture()
    fixture.update(action="get_borrow_requests", roles=["reader"], setup=[], params=[
        {"field": "lookup", "source": "record", "record_field": "reason"}])
    files = _files(fixture)
    data = json.loads(files["data/contract.json"])
    data["surfaces"][0]["collections"][0] = collection
    files["data/contract.json"] = json.dumps(data)
    path = "modules/borrowing/module.yaml"
    module = yaml.safe_load(files[path])
    module["actions"].append(_action("get_borrow_requests", "requests.read", _schema(lookup="string"),
                                     _schema(borrow_request_id="string")))
    files[path] = yaml.safe_dump(module)
    bound = load_module_smoke_fixtures(files)["borrowing", "get_borrow_requests"]
    assert fixture_record_identity(bound, collection=collection, operation="get") == ("lookup", "reason")
    for params in ([_literal("lookup", "substitute")], [
        {"field": "lookup", "source": "record", "record_field": "borrow_request_id"}]):
        document = bound.model_dump(exclude_none=True)
        document["params"] = params
        parsed = parse_module_smoke_fixtures({"schema_version": SCHEMA_VERSION, "fixtures": [document]})
        with pytest.raises(ValueError, match="get_borrow_requests.*source=record.*reason"):
            fixture_record_identity(parsed.fixtures[0], collection=collection, operation="get")


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_module_action_closure_rejects_substitute_fixture_target(operation):
    from mozaiksai.core.workflow.generator_support.module_write_actions import close_module_actions
    from tests.test_generated_role_grants import _arguments, _closed

    payload = _closed()
    fixture = {"action": f"{operation}_task", "roles": ["coordinator"],
               "setup": [{"id": "created", "module": "task_management", "action": "create_task",
                          "roles": ["coordinator"], "params": [_literal("title", "Another task")]}],
               "params": [_response("task_id", "created", ["item", "task_id"])]}
    payload["module_contract"]["fixtures_yaml"] = {"schema_version": SCHEMA_VERSION, "fixtures": [fixture]}
    before = deepcopy(payload)
    with pytest.raises(ValueError, match=f"task_management.*{operation}_task.*task_id.*source=record"):
        close_module_actions(payload, **_arguments())
    assert payload == before


def test_module_action_closure_preserves_authored_get_input_with_record_lookup():
    from mozaiksai.core.workflow.generator_support.module_write_actions import close_module_actions
    from tests.test_generated_role_grants import _arguments, _closed

    payload = _closed()
    action = next(action for action in payload["module_contract"]["module_yaml"]["actions"]
                  if action["id"] == "get_tasks")
    action["input_schema"] = _schema(lookup="string")
    fixture = {"action": "get_tasks", "roles": [], "setup": [], "params": [
        {"field": "lookup", "source": "record", "record_field": "task_id"}]}
    payload["module_contract"]["fixtures_yaml"] = {"schema_version": SCHEMA_VERSION, "fixtures": [fixture]}
    result = close_module_actions(payload, **_arguments())
    actual = next(action for action in result["module_contract"]["module_yaml"]["actions"]
                  if action["id"] == "get_tasks")
    assert actual["input_schema"] == _schema(lookup="string")
    assert result["module_contract"]["fixtures_yaml"]["fixtures"] == [fixture]


@pytest.mark.parametrize("operation", ["get", "update", "delete"])
@pytest.mark.parametrize("declared_id_type", [None, "integer"])
def test_generated_mongo_id_fallback_is_an_allowed_observed_record_field(operation, declared_id_type):
    action = "get_borrow_requests" if operation == "get" else f"{operation}_borrow_request"
    input_field = "lookup" if operation == "get" else "_id"
    fixture = {"action": action, "roles": ["reader" if operation == "get" else "member"],
               "setup": [], "params": [{"field": input_field, "source": "record", "record_field": "_id"}]}
    inputs = {input_field: "string"}
    if operation == "update":
        inputs["reason"] = "string"
        fixture["params"].append(_literal("reason", "Changed reason"))
    files = _files(fixture)
    data = json.loads(files["data/contract.json"])
    collection = data["surfaces"][0]["collections"][0]
    collection["fields"] = [{"name": "reason", "type": "string"}]
    if declared_id_type is not None:
        collection["fields"].append({"name": "_id", "type": declared_id_type})
    files["data/contract.json"] = json.dumps(data)
    path = "modules/borrowing/module.yaml"
    module = yaml.safe_load(files[path])
    module["actions"] = [item for item in module["actions"] if item["id"] != action]
    module["actions"].append(_action(action, "requests.read" if operation == "get" else "requests.update",
                                     _schema(**inputs), _schema(_id="string")))
    files[path] = yaml.safe_dump(module)
    bound = load_module_smoke_fixtures(files)["borrowing", action]
    assert fixture_record_identity(bound, collection=collection, operation=operation) == (input_field, "_id")
    assert fixture_record_identity(None, collection=collection, operation=operation) == (
        "id" if operation == "get" else "_id", "_id")
    params = resolve_fixture_params(bound.params, responses={}, actor_subject="subject-a",
                                    record={"_id": "observed-row", "reason": "Before"},
                                    now=datetime(2026, 11, 7, tzinfo=UTC))
    assert params[input_field] == "observed-row"


def test_generated_mongo_id_is_not_added_when_canonical_helpers_select_declared_fields():
    fixture = {"action": "get_borrow_requests", "roles": ["reader"], "setup": [], "params": [
        {"field": "lookup", "source": "record", "record_field": "reason"},
        {"field": "extra", "source": "record", "record_field": "_id"}]}
    files = _files(fixture)
    data = json.loads(files["data/contract.json"])
    data["surfaces"][0]["collections"][0]["search_by"] = "reason"
    files["data/contract.json"] = json.dumps(data)
    path = "modules/borrowing/module.yaml"
    module = yaml.safe_load(files[path])
    module["actions"].append(_action("get_borrow_requests", "requests.read", _schema(lookup="string", extra="string"),
                                     _schema(borrow_request_id="string")))
    files[path] = yaml.safe_dump(module)
    with pytest.raises(ValueError, match="record source requires a declared field"):
        load_module_smoke_fixtures(files)


@pytest.mark.parametrize("kind", ["renamed", "integer", "number", "boolean", "enum", "extra_required", "list_required"])
def test_module_closure_requires_fixture_for_incompatible_default_read(kind):
    from mozaiksai.core.workflow.generator_support.module_write_actions import close_module_actions
    from tests.test_generated_role_grants import _arguments, _closed

    payload = _closed()
    action_id = "list_tasks" if kind == "list_required" else "get_tasks"
    action = next(item for item in payload["module_contract"]["module_yaml"]["actions"] if item["id"] == action_id)
    field = "lookup" if kind == "renamed" else "page" if kind == "list_required" else "id"
    action["input_schema"] = _schema(**{field: kind if kind in {"integer", "number", "boolean"} else "string"})
    if kind == "enum":
        action["input_schema"]["properties"]["id"]["enum"] = ["one"]
    if kind == "extra_required":
        action["input_schema"] = _schema(id="string", filter="string")
    before = deepcopy(payload)
    with pytest.raises(ValueError, match=f"{action_id}.*observer default.*fixtures_yaml"):
        close_module_actions(payload, **_arguments())
    assert payload == before


@pytest.mark.parametrize("surface", [None, "internal", "admin_internal"])
def test_module_closure_keeps_compatible_or_non_http_reads_fixture_free(surface):
    from mozaiksai.core.workflow.generator_support.module_write_actions import close_module_actions
    from tests.test_generated_role_grants import _arguments, _closed

    payload = _closed()
    action = next(item for item in payload["module_contract"]["module_yaml"]["actions"] if item["id"] == "get_tasks")
    action["api_surface"] = surface
    action["input_schema"] = _schema(**({"id": "string"} if surface is None else {"lookup": "boolean"}))
    if surface is None:
        action["input_schema"]["properties"]["optional_filter"] = {"type": "boolean"}
    result = close_module_actions(payload, **_arguments())
    assert result["module_contract"].get("fixtures_yaml") is None
    assert next(item for item in result["module_contract"]["module_yaml"]["actions"] if item["id"] == "get_tasks")["input_schema"] == action["input_schema"]


def test_missing_fixture_diagnostics_aggregate_get_and_list():
    from mozaiksai.core.workflow.generator_support.module_write_actions import close_module_actions
    from tests.test_generated_role_grants import _arguments, _closed

    payload = _closed()
    for action in payload["module_contract"]["module_yaml"]["actions"]:
        if action["id"] in {"get_tasks", "list_tasks"}:
            action["input_schema"] = _schema(lookup="string")
    with pytest.raises(ValueError) as error:
        close_module_actions(payload, **_arguments())
    assert "get_tasks" in str(error.value) and "list_tasks" in str(error.value)


def test_no_create_collection_keeps_nondefault_read_fixture_optional():
    from mozaiksai.core.workflow.generator_support.module_smoke_fixtures import (
        validate_module_fixture_record_identities,
    )
    from tests.test_generated_role_grants import _arguments, _closed

    manifest = _closed()["module_contract"]["module_yaml"]
    manifest["actions"] = [action for action in manifest["actions"] if action["id"] != "create_task"]
    for action in manifest["actions"]:
        if action["id"] in {"get_tasks", "list_tasks"}:
            action["input_schema"] = _schema(lookup="boolean")
    before = deepcopy(manifest)
    validate_module_fixture_record_identities(
        "task_management", None, data_contract=_arguments()["data_contract"], manifest=manifest,
    )
    assert manifest == before


@pytest.mark.parametrize("typed", [False, True])
def test_descriptive_get_input_keeps_compatible_default_fixture_free(typed):
    from mozaiksai.core.workflow.generator_support.module_write_actions import close_module_actions
    from tests.test_generated_role_grants import _arguments, _closed

    payload = _closed()
    action = next(item for item in payload["module_contract"]["module_yaml"]["actions"] if item["id"] == "get_tasks")
    if typed:
        schema = {"type": "object", "description": "Read an existing record.", "properties": [
            {"name": "id", "type": "string", "required": True, "description": "Record identifier."}]}
    else:
        schema = _schema(id="string")
        schema["description"] = "Read an existing record."
        schema["properties"]["id"]["description"] = "Record identifier."
    action["input_schema"] = deepcopy(schema)
    result = close_module_actions(payload, **_arguments())
    actual = next(item for item in result["module_contract"]["module_yaml"]["actions"] if item["id"] == "get_tasks")
    assert actual["input_schema"] == schema
    assert result["module_contract"].get("fixtures_yaml") is None


@pytest.mark.parametrize("assertion", [{"pattern": "^approved-"}, {"format": "uuid"}, {"minLength": 100}])
def test_native_read_assertion_requires_fixture_without_rewriting_schema(assertion):
    from mozaiksai.core.workflow.generator_support.module_write_actions import close_module_actions
    from tests.test_generated_role_grants import _arguments, _closed

    payload = _closed()
    action = next(item for item in payload["module_contract"]["module_yaml"]["actions"] if item["id"] == "get_tasks")
    action["input_schema"] = _schema(id="string")
    action["input_schema"]["properties"]["id"].update(assertion)
    before = deepcopy(payload)
    with pytest.raises(ValueError, match="get_tasks.*observer default.*fixtures_yaml"):
        close_module_actions(payload, **_arguments())
    assert payload == before
    fixture = {"action": "get_tasks", "roles": [], "setup": [], "params": [
        {"field": "id", "source": "record", "record_field": "task_id"}]}
    payload["module_contract"]["fixtures_yaml"] = {"schema_version": SCHEMA_VERSION, "fixtures": [fixture]}
    result = close_module_actions(payload, **_arguments())
    actual = next(item for item in result["module_contract"]["module_yaml"]["actions"] if item["id"] == "get_tasks")
    assert actual["input_schema"] == action["input_schema"]
    # This early default check adds no new restriction or schema support. The
    # existing full fixture schema/role/reference validator remains authoritative.


@pytest.mark.parametrize("schema", [
    {"type": "object"},
    {"type": "object", "properties": {}},
    {"type": "object", "properties": {}, "additionalProperties": True},
    {"type": "object", "properties": {"filter": {"type": "string", "pattern": "^active$"}},
     "minProperties": 0, "maxProperties": 1},
    {"type": "object", "allOf": [{"maxProperties": 0}, {"not": {"required": ["filter"]}}]},
])
def test_native_list_empty_default_uses_runtime_schema_without_rewriting_manifest(schema):
    from mozaiksai.core.runtime.composition.schema_validation import validate_json_schema
    from mozaiksai.core.workflow.generator_support.module_write_actions import close_module_actions
    from tests.test_generated_role_grants import _arguments, _closed

    assert validate_json_schema({}, schema) is None
    payload = _closed()
    action = next(item for item in payload["module_contract"]["module_yaml"]["actions"] if item["id"] == "list_tasks")
    action["input_schema"] = deepcopy(schema)
    before = deepcopy(payload)
    result = close_module_actions(payload, **_arguments())
    assert payload == before
    actual = next(item for item in result["module_contract"]["module_yaml"]["actions"] if item["id"] == "list_tasks")
    assert actual["input_schema"] == schema
    assert result["module_contract"].get("fixtures_yaml") is None


@pytest.mark.parametrize("schema", [
    {"type": "object", "required": ["filter"], "properties": {"filter": {"type": "string"}}},
    {"type": "object", "minProperties": 1},
    {"type": "object", "not": {"maxProperties": 0}},
    {"type": "object", "enum": [{"filter": "active"}]},
    {"type": "object", "required": "filter"},
])
def test_native_list_empty_default_preserves_assertion_and_schema_failures(schema):
    from mozaiksai.core.runtime.composition.schema_validation import validate_json_schema
    from mozaiksai.core.workflow.generator_support.module_write_actions import close_module_actions
    from tests.test_generated_role_grants import _arguments, _closed

    assert validate_json_schema({}, schema) is not None
    payload = _closed()
    action = next(item for item in payload["module_contract"]["module_yaml"]["actions"] if item["id"] == "list_tasks")
    action["input_schema"] = deepcopy(schema)
    before = deepcopy(payload)
    with pytest.raises(ValueError, match="list_tasks.*observer default.*fixtures_yaml"):
        close_module_actions(payload, **_arguments())
    assert payload == before


@pytest.mark.parametrize("schema", [
    {"$ref": "https://example.invalid/schema.json"},
    {"type": "object", "allOf": [{"$ref": "#/definitions/empty"}], "definitions": {"empty": {"type": "object"}}},
])
def test_native_list_reference_proof_stays_conservative_without_schema_resolution(monkeypatch, schema):
    from mozaiksai.core.workflow.generator_support import module_smoke_fixtures

    def unexpected_validation(*args, **kwargs):
        pytest.fail("reference-bearing default proof must not resolve a schema")

    monkeypatch.setattr(module_smoke_fixtures, "validate_json_schema", unexpected_validation)
    before = deepcopy(schema)
    assert module_smoke_fixtures._default_read_inputs_compatible(schema, "list") is False
    assert schema == before


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_same_record_lifecycle_setup_closes_and_round_trips(operation):
    fixture = _fixture()
    fixture.update(action=f"{operation}_borrow_request", roles=["member"])
    fixture["setup"] = [{"id": "prepare", "module": "borrowing", "action": "approve_borrow_request",
                         "roles": ["admin"], "params": [
                             {"field": "borrow_request_id", "source": "record", "record_field": "borrow_request_id"}]}]
    fixture["params"] = [{"field": "borrow_request_id", "source": "record", "record_field": "borrow_request_id"}]
    if operation == "update":
        fixture["params"].append(_literal("reason", "A distinct change after preparation"))
    files = _files(fixture)
    if operation == "delete":
        module = yaml.safe_load(files["modules/borrowing/module.yaml"])
        module["actions"].append(_action("delete_borrow_request", "requests.update",
                                         _schema(borrow_request_id="string"), _schema(deleted="boolean")))
        files["modules/borrowing/module.yaml"] = yaml.safe_dump(module)
    original = deepcopy(files)
    bound = load_module_smoke_fixtures(files)["borrowing", fixture["action"]]
    now = datetime(2026, 11, 7, tzinfo=UTC)
    params = resolve_fixture_params(bound.setup[0].params, responses={}, actor_subject="verified-subject",
                                    record={"borrow_request_id": "actual-row"}, now=now)
    assert params == {"borrow_request_id": "actual-row"}
    assert files == original
    config = yaml.safe_load((ROOT / "factory_app/workflows/AppGenerator/structured_outputs.yaml").read_text(encoding="utf-8"))
    model = build_models_from_config(config["models"])["ModuleSmokeFixtures"]
    document = {"schema_version": SCHEMA_VERSION, "fixtures": [fixture]}
    typed = model.model_validate(document).model_dump(mode="json")
    rendered = extract_code_file_map_from_payload({"module_contract": {"module_id": "borrowing", "fixtures_yaml": typed}})
    assert yaml.safe_load(rendered[PATH]) == document


@pytest.mark.parametrize("action", ["get_borrow_requests", "list_borrow_requests", "create_borrow_request", "approve_borrow_request"])
def test_record_setup_without_canonical_write_target_rejects_during_admission(action):
    from mozaiksai.core.workflow.generator_support.module_smoke_fixtures import (
        validate_module_fixture_record_identities,
    )

    fixture = _fixture()
    fixture["action"] = action
    fixture["setup"][0]["params"] = [{"field": "name", "source": "record", "record_field": "reason"}]
    if action == "get_borrow_requests":
        fixture["params"] = [{"field": "id", "source": "record", "record_field": "_id"}]
    data = json.loads(_files()["data/contract.json"])
    with pytest.raises(ValueError, match="setup record source.*canonical update/delete"):
        validate_module_fixture_record_identities("borrowing", {"schema_version": SCHEMA_VERSION, "fixtures": [fixture]},
                                                  data_contract=data)


def test_setup_record_field_and_type_close_without_relaxing_target_identity():
    fixture = _fixture()
    fixture.update(action="update_borrow_request", roles=["member"])
    fixture["setup"] = [{"id": "prepare", "module": "borrowing", "action": "approve_borrow_request",
                         "roles": ["admin"], "params": [
                             {"field": "borrow_request_id", "source": "record", "record_field": "unknown"}]}]
    fixture["params"] = [{"field": "borrow_request_id", "source": "record", "record_field": "borrow_request_id"},
                         _literal("reason", "After preparation")]
    with pytest.raises(ValueError, match="declared field"):
        load_module_smoke_fixtures(_files(fixture))
    fixture["setup"][0]["params"][0]["record_field"] = "reason"
    files = _files(fixture)
    data = json.loads(files["data/contract.json"])
    data["surfaces"][0]["collections"][0]["fields"][1]["type"] = "integer"
    files["data/contract.json"] = json.dumps(data)
    with pytest.raises(ValueError, match="type"):
        load_module_smoke_fixtures(files)
    fixture["params"][0] = _response("borrow_request_id", "prepare", ["loan_id"])
    with pytest.raises(ValueError, match="source=record"):
        load_module_smoke_fixtures(_files(fixture))

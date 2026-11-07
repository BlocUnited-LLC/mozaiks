"""Generated admin data closes against declared read, input and output contracts."""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.validate_wiring import validate_wiring
from mozaiksai.core.runtime.app.page_schema import (
    PageSchemaValidationError,
    build_page_action_contracts_from_module_contracts,
    load_and_validate_page_schema,
    validate_page_schema,
)
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    compile_authored_page_files,
    compile_page_data_sources,
    module_action_index,
)
from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs


def _manifest():
    read = {
        "id": "list_inventory", "description": "Read inventory", "handler_method": "list_inventory",
        "api_surface": "admin_internal", "read_only": True, "permissions": ["inventory.read"],
        "input_schema": {"type": "object", "additionalProperties": False},
        "output_schema": {"type": "object", "properties": {
            "items": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}}}},
        }},
    }
    writer = {**deepcopy(read), "id": "refresh_inventory", "handler_method": "refresh_inventory", "read_only": False}
    public = {**deepcopy(read), "id": "list_public", "handler_method": "list_public", "api_surface": "public_readonly"}
    internal = {**deepcopy(read), "id": "list_internal", "handler_method": "list_internal", "api_surface": "internal"}
    return {
        "schema_version": "mozaiks.module.v1", "module": {"id": "inventory", "handler": "backend.handler:Handler"},
        "actions": [read, writer, public, internal],
    }


def _panel():
    return {"schema_version": "mozaiks.admin.v2", "panels": [{
        "id": "inventory.list", "page": "inventory_status", "label": "Inventory", "renderer": "schema",
        "layout": "full-width", "sections": [{"id": "inventory-table", "primitive": "DataTable", "config": {
            "data_source": {"module_id": "inventory", "action_id": "list_inventory"},
            "data_key": "items", "columns": [{"key": "name", "label": "Name"}],
            "actions": [{"action_type": "submit", "label": "Refresh inventory",
                         "data_source": {"module_id": "inventory", "action_id": "refresh_inventory"}}],
        }}],
    }]}


def _compile(manifest=None, panel=None, *, typed=True):
    manifest = manifest if manifest is not None else _manifest()
    panel = panel if panel is not None else _panel()
    if typed:
        return extract_code_file_map_from_payload({"module_contract": {
            "module_id": "inventory", "module_yaml": manifest, "admin_yaml": panel,
        }})
    files = {"modules/inventory/module.yaml": yaml.safe_dump(manifest),
             "modules/inventory/contracts/admin.yaml": yaml.safe_dump(panel)}
    return compile_authored_page_files(files, payload={}, context={})


def _page(panel):
    return {"schema_version": "mozaiks.app_page.v1", "name": "inventory_status", "route": "/admin/inventory_status",
            "title": "Inventory", "page_type": "record_list", "layout": "full-width",
            "sections": panel["panels"][0]["sections"]}


@pytest.mark.parametrize("typed", [True, False])
def test_typed_and_raw_admin_compile_read_and_explicit_writer_then_pass_wiring(typed, tmp_path):
    files = _compile(typed=typed)
    panel = yaml.safe_load(files["modules/inventory/contracts/admin.yaml"])
    config = panel["panels"][0]["sections"][0]["config"]
    assert config["api_endpoint"] == "/api/admin/modules/inventory/list_inventory/read"
    assert config["actions"][0]["href"] == "/api/admin/modules/inventory/refresh_inventory"
    assert "data_source" not in config
    module_path = tmp_path / "modules/inventory/module.yaml"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(files["modules/inventory/module.yaml"], encoding="utf-8")
    contracts = build_page_action_contracts_from_module_contracts(tmp_path)
    page = _page(panel)
    validate_page_schema(page, action_contracts=contracts)
    page_path = tmp_path / "ui/pages/inventory_status.yaml"
    page_path.parent.mkdir(parents=True)
    page_path.write_text(yaml.safe_dump(page), encoding="utf-8")
    served = load_and_validate_page_schema(
        page_path, action_index={"inventory": frozenset(action["id"] for action in _manifest()["actions"])},
    )
    assert served.sections[0].config["api_endpoint"].endswith("/list_inventory/read")
    # The actual bundle wiring gate must see module-admin documents too.
    report = asyncio.run(validate_wiring({"generated_files": files}))
    assert report["passed"] is True, report["failed_tests"]
    assert {row["endpoint"] for row in report["wired"]} == {
        "/api/admin/modules/inventory/list_inventory/read",
        "/api/admin/modules/inventory/refresh_inventory",
    }


@pytest.mark.parametrize("typed", [True, False])
@pytest.mark.parametrize("change", [
    {"read_only": False}, {"read_only": "true"}, {"read_only": 1}, {"read_only": None},
    {"input_schema": {"type": "object", "required": ["scope"]}},
    {"input_schema": {"type": "object", "minProperties": 1}},
    {"input_schema": {"not": {"maxProperties": 0}}},
    {"input_schema": {"type": "array"}},
    {"input_schema": {"type": "object", "minProperties": "bad"}},
    {"emits": ["domain.inventory.changed"]},
])
def test_automatic_admin_reads_reject_ineligible_or_parameterized_actions(typed, change):
    manifest = _manifest()
    manifest["actions"][0].update(change)
    with pytest.raises(ValueError, match="automatic admin data requires"):
        _compile(manifest, typed=typed)


def test_missing_read_metadata_is_not_inferred_from_list_name_or_empty_schema():
    manifest = _manifest()
    manifest["actions"][0].pop("read_only")
    with pytest.raises(ValueError, match="automatic admin data requires"):
        _compile(manifest)


@pytest.mark.parametrize("relative_path", ["ui/pages/inventory_status.yaml", "ui/pages/inventory_status/page.yaml"])
def test_direct_serving_uses_owning_contract_and_preserves_explicit_metadata(relative_path, tmp_path):
    files = _compile()
    panel = yaml.safe_load(files["modules/inventory/contracts/admin.yaml"])
    module_path = tmp_path / "modules/inventory/module.yaml"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(files["modules/inventory/module.yaml"], encoding="utf-8")
    page_path = tmp_path / relative_path
    page_path.parent.mkdir(parents=True)
    page_path.write_text(yaml.safe_dump(_page(panel)), encoding="utf-8")
    index = {"inventory": frozenset(action["id"] for action in _manifest()["actions"])}
    load_and_validate_page_schema(page_path, action_index=index)
    # Explicit empty metadata must not be replaced by the valid disk contract.
    with pytest.raises(PageSchemaValidationError):
        load_and_validate_page_schema(page_path, action_index=index, action_contracts={})


@pytest.mark.parametrize("relative_path", [
    "ui/pages/archive/deep/inventory_status.yaml", "ui/pages/archive/deep/page.yaml", "docs/pages/inventory_status.yaml",
])
def test_direct_serving_does_not_discover_unrelated_ancestor_contracts(relative_path, tmp_path, monkeypatch):
    files = _compile()
    panel = yaml.safe_load(files["modules/inventory/contracts/admin.yaml"])
    page_path = tmp_path / relative_path
    page_path.parent.mkdir(parents=True)
    page_path.write_text(yaml.safe_dump(_page(panel)), encoding="utf-8")

    def forbidden_lookup(_):
        pytest.fail("Noncanonical page paths must not discover module authority")

    monkeypatch.setattr(
        "mozaiksai.core.runtime.app.page_schema.build_page_action_contracts_from_module_contracts", forbidden_lookup,
    )
    with pytest.raises(PageSchemaValidationError):
        load_and_validate_page_schema(
            page_path, action_index={"inventory": frozenset(action["id"] for action in _manifest()["actions"])},
        )


@pytest.mark.parametrize("typed", [True, False])
@pytest.mark.parametrize("change", ["unknown_action", "internal", "missing_column", "missing_data_key", "server_pagination", "raw_endpoint"])
def test_admin_authoring_rejects_unclosed_refs_and_response_fields(typed, change):
    panel = _panel()
    config = panel["panels"][0]["sections"][0]["config"]
    if change in {"unknown_action", "internal"}:
        config["data_source"]["action_id"] = "missing" if change == "unknown_action" else "list_internal"
    elif change == "missing_column":
        config["columns"][0]["key"] = "unreturned_field"
    elif change == "missing_data_key":
        config["data_key"] = "unreturned_rows"
    elif change == "server_pagination":
        config.update(pagination_mode="server", total_key="total")
    else:
        config.pop("data_source")
        config["api_endpoint"] = "/api/admin/modules/inventory/refresh_inventory"
    with pytest.raises(ValueError):
        _compile(panel=panel, typed=typed)


@pytest.mark.parametrize("endpoint", [
    "/api/admin/modules/inventory/refresh_inventory",
    "/api/admin/modules/inventory/refresh_inventory/read",
    "/api/admin/modules/inventory/missing/read",
    "/api/admin/modules/inventory/list_inventory/read/extra",
    "/api/admin/modules/inventory/list_inventory/read?scope=other",
    "/api/modules/inventory/list_inventory",
])
def test_runtime_and_wiring_reject_unadmitted_hand_authored_data_endpoint(endpoint):
    files = _compile()
    panel = yaml.safe_load(files["modules/inventory/contracts/admin.yaml"])
    panel["panels"][0]["sections"][0]["config"]["api_endpoint"] = endpoint
    contracts = {f"inventory/{action['id']}": action for action in _manifest()["actions"]}
    with pytest.raises(PageSchemaValidationError):
        validate_page_schema(_page(panel), action_contracts=contracts)
    files["modules/inventory/contracts/admin.yaml"] = yaml.safe_dump(panel)
    report = asyncio.run(validate_wiring({"generated_files": files}))
    assert not report["passed"] and report["failed_tests"]


def test_public_get_pagination_and_explicit_admin_actions_keep_their_paths():
    panel = _panel()
    config = panel["panels"][0]["sections"][0]["config"]
    config["data_source"]["action_id"] = "list_public"
    config.update(pagination_mode="server", pagination=True, page_size=10, total_key="total")
    manifest = _manifest()
    manifest["actions"][2]["output_schema"]["properties"]["total"] = {"type": "integer"}
    files = _compile(manifest, panel)
    compiled = yaml.safe_load(files["modules/inventory/contracts/admin.yaml"])
    config = compiled["panels"][0]["sections"][0]["config"]
    assert config["api_endpoint"] == "/api/modules/inventory/list_public"
    assert config["actions"][0]["href"] == "/api/admin/modules/inventory/refresh_inventory"
    assert config["pagination_mode"] == "server"


@pytest.mark.parametrize("typed", [True, False])
def test_existing_dotted_public_action_keeps_response_field_validation(typed):
    manifest = _manifest()
    manifest["actions"][2]["id"] = "list.public"
    manifest["actions"][2]["output_schema"]["properties"]["total"] = {"type": "integer"}
    panel = _panel()
    config = panel["panels"][0]["sections"][0]["config"]
    config["data_source"]["action_id"] = "list.public"
    config.pop("data_key")
    files = _compile(manifest, panel, typed=typed)
    compiled = yaml.safe_load(files["modules/inventory/contracts/admin.yaml"])
    compiled_config = compiled["panels"][0]["sections"][0]["config"]
    assert compiled_config["api_endpoint"] == "/api/modules/inventory/list.public"
    assert compiled_config["data_key"] == "items"
    config["columns"][0]["key"] = "NOT_RETURNED"
    with pytest.raises(ValueError, match="not a declared row field"):
        _compile(manifest, panel, typed=typed)


@pytest.mark.parametrize("typed", [True, False])
def test_admin_invalid_selectors_are_rejected_instead_of_repaired(typed):
    manifest = _manifest()
    manifest["actions"][0]["output_schema"]["properties"]["total"] = {"type": "integer"}
    panel = _panel()
    panel["panels"][0]["sections"][0]["config"]["data_key"] = "missing_rows"
    with pytest.raises(ValueError, match="must select a declared array"):
        _compile(manifest, panel, typed=typed)
    panel["panels"][0]["sections"] = [{"id": "inventory-count", "primitive": "Metric", "config": {
        "label": "Inventory", "data_source": {"module_id": "inventory", "action_id": "list_inventory"},
        "value_key": "missing_count",
    }}]
    with pytest.raises(ValueError, match="is not declared"):
        _compile(manifest, panel, typed=typed)


def test_admin_contracts_do_not_construct_user_facing_mutation_controls():
    from tests.test_appgenerator_module_write_actions import _contract

    document = _page(_panel())
    document["sections"][0]["config"]["actions"] = []
    contracts = _manifest()
    contracts["module"]["id"] = "task_management"
    for action in contracts["actions"]:
        if action["api_surface"] == "admin_internal":
            action["id"] = "list_tasks" if action["read_only"] else "create_task"
    document["sections"][0]["config"]["data_source"] = {"module_id": "task_management", "action_id": "list_tasks"}
    inventory = module_action_index({"modules/task_management/module.yaml": yaml.safe_dump(contracts)})
    compile_page_data_sources(document, inventory, data_contract=_contract(), design_surface_map={"surfaces": []})
    assert document["sections"][0]["config"]["actions"] == []
    assert len(document["sections"]) == 1


def test_generated_read_and_write_metadata_matches_known_implementation_semantics():
    from tests.test_appgenerator_module_write_actions import _actions, _closed, _output

    authored = _output([{ "id": "create_task", "read_only": True}])
    actions = _actions(_closed(authored))
    assert actions["get_tasks"]["read_only"] is True
    assert actions["list_tasks"]["read_only"] is True
    for action in ("create_task", "update_task", "delete_task"):
        assert actions[action]["read_only"] is False


def test_generator_structured_output_declares_explicit_read_metadata():
    models, _ = load_workflow_structured_outputs("AppGenerator")
    field = models["ModuleAction"].model_json_schema()["properties"]["read_only"]
    assert field["type"] == "boolean" and field["default"] is False

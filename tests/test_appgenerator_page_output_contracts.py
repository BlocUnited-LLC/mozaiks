"""Pages compile only against real module responses and supplied workflows."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.hook_primitive_catalog import inject_primitive_catalog
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge, _workflow_tool_invocation
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
from mozaiksai.core.workflow.generator_support.module_read_actions import close_module_read_actions
from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    compile_page_data_sources,
    module_action_index,
    module_action_index_from_context,
    normalize_planned_page_content,
)
from tests.factory_context import factory_context


def _files():
    return {"modules/tasks/module.yaml": yaml.safe_dump({
        "module": {"id": "tasks"}, "actions": [
            {"id": "list_tasks", "output_schema": {"type": "object", "properties": {
                "items": {"type": "array", "items": {"type": "object", "properties": {
                    "id": {"type": "string"}, "title": {"type": "string"}, "done": {"type": "boolean"},
                }}}, "total": {"type": "integer"},
            }}},
            {"id": "summarize_tasks", "output_schema": {"type": "object", "properties": {
                "tasks_completed": {"type": "integer"}, "total_tasks": {"type": "integer"},
            }}},
            {"id": "update_task", "entitlement_gate": "task.edit", "input_schema": {
                "type": "object", "properties": {"id": {"type": "string"}, "title": {"type": "string"}},
            }, "output_schema": {"type": "object", "properties": {"updated": {"type": "boolean"}}}},
        ],
    })}


def _dashboard():
    return {"name": "dashboard", "sections": [
        {"id": "summary", "primitive": "SummaryStrip", "config": {
            "data_source": {"module_id": "tasks", "action_id": "summarize_tasks"},
            "items": [{"label": "Completed tasks", "value_key": "tasks_completed"}],
        }},
        {"id": "tasks", "primitive": "ResourceTable", "config": {
            "data_source": {"module_id": "tasks", "action_id": "list_tasks"},
            "data_key": "tasks", "columns": [{"key": "title", "label": "Task"}],
        }},
    ]}


def test_live_kpi_mismatch_rejects_with_real_field_choices_and_no_semantic_rename():
    page = _dashboard()
    metric = page["sections"][0]["config"]["items"][0]
    metric["value_key"] = "completed_tasks"
    with pytest.raises(ValueError, match="completed_tasks.*Valid fields: tasks_completed, total_tasks"):
        compile_page_data_sources(page, module_action_index(_files()), reject_api_endpoints=True)
    assert metric["value_key"] == "completed_tasks"
    assert page["sections"][1]["config"]["data_key"] == "items"


@pytest.mark.parametrize("primitive,mode", [("ResourceTable", "client"), ("DataTable", "client"), ("DataTable", "server")])
def test_compiler_fills_declared_canonical_list_envelope_in_every_table_mode(primitive, mode):
    page = _dashboard()
    table = page["sections"][1]
    table["primitive"] = primitive
    table["config"]["pagination_mode"] = mode
    if mode == "server":
        table["config"].update(pagination=True, page_size=20, total_key="task_count")
    index = module_action_index(_files())
    compile_page_data_sources(page, index, reject_api_endpoints=True)
    assert table["config"]["data_key"] == "items"
    assert table["config"].get("total_key") == ("total" if mode == "server" else None)
    unchanged = deepcopy(page)
    assert compile_page_data_sources(page, index) == 0
    assert page == unchanged


def test_table_columns_are_checked_against_item_properties_before_materialization():
    page = _dashboard()
    page["sections"][1]["config"]["columns"] = [{"key": "task_title"}]
    with pytest.raises(ValueError, match="task_title.*Valid fields: done, id, title"):
        normalize_planned_page_content(yaml.safe_dump(page), modules=module_action_index(_files()))


def test_compiler_does_not_guess_between_multiple_returned_arrays():
    index = module_action_index(_files())
    output = index["tasks"]["list_tasks"]["output_schema"]
    output["properties"]["archived_items"] = deepcopy(output["properties"]["items"])
    with pytest.raises(ValueError, match="data_key.*tasks.*Valid fields: archived_items, items, total"):
        compile_page_data_sources(_dashboard(), index)


def test_compiler_preserves_explicit_nested_table_response_paths():
    index = module_action_index(_files())
    output = index["tasks"]["list_tasks"]["output_schema"]
    output["properties"]["archive"] = {"type": "object", "properties": {
        "items": deepcopy(output["properties"]["items"]), "total": {"type": "integer"},
    }}
    page = _dashboard()
    config = page["sections"][1]["config"]
    config.update(data_key="archive.items", total_key="archive.total", pagination_mode="server")
    compile_page_data_sources(page, index)
    assert config["data_key"] == "archive.items"
    assert config["total_key"] == "archive.total"


def test_dependency_inventory_and_prompt_preserve_output_fields_and_paid_actions():
    files = _files()
    context = ContextVariablesBridge({"dependency_task_outputs": {"tasks-module": {
        "code_files": [{"filename": path, "content": content} for path, content in files.items()],
    }}})
    index = module_action_index_from_context(context)
    assert index == module_action_index(files)
    agent = SimpleNamespace(name="AppSchemaAgent", system_message="", context_variables=context)
    agent.update_system_message = lambda message: setattr(agent, "system_message", message)
    inject_primitive_catalog(agent, [])
    assert "tasks_completed:" in agent.system_message
    assert "entitlement_gate: task.edit" in agent.system_message
    assert "title:" in agent.system_message
    assert "[AVAILABLE PAGE WORKFLOWS]\n" in agent.system_message
    assert "[]" in agent.system_message


def test_compiler_rejects_invented_workflow_and_accepts_supplied_workflow():
    page = {"name": "dashboard", "sections": [{"id": "create", "primitive": "Button", "config": {
        "action": {"action_type": "workflow", "label": "Create", "workflow_id": "CreateTask"},
    }}]}
    with pytest.raises(ValueError, match="CreateTask.*not present.*Valid workflows: \\(none in the bundle\\)"):
        compile_page_data_sources(deepcopy(page), {})
    compile_page_data_sources(page, {}, workflow_names={"CreateTask"})


def test_canonical_read_schema_exposes_actual_service_projection_fields_to_pages():
    collection = {
        "name": "tasks", "entity": "Task", "scope": "app", "tenancy": "per_user", "owner_field": "owner_id",
        "ownership": {"surface_id": "tasks", "surface_kind": "module"},
        "fields": [{"name": name, "type": "string", "required": True} for name in ("_id", "owner_id", "title")],
    }
    contract = {"surfaces": [{"surface_id": "tasks", "surface_kind": "module", "collections": [collection]}]}
    plan = {"capability_packs": [{"capability_pack_id": "tasks", "capability_source": "generated_module", "primary_entities": ["Task"]}]}
    output = close_module_read_actions({"module_contract": {"module_id": "tasks", "module_yaml": {
        "module": {"id": "tasks"}, "actions": [],
    }}}, app_build_plan=plan, data_contract=contract)
    index = module_action_index(extract_code_file_map_from_payload(output))
    rows = index["tasks"]["list_tasks"]["output_schema"]["properties"]["items"]["items"]
    assert rows["properties"] == {name: {"type": "string"} for name in ("_id", "owner_id", "title")}
    assert index["tasks"]["get_tasks"]["output_schema"]["properties"]["item"] == rows
    page = _dashboard()
    page["sections"] = [page["sections"][1]]
    compile_page_data_sources(page, index, reject_api_endpoints=True)
    assert page["sections"][0]["config"]["data_key"] == "items"


@pytest.mark.asyncio
@pytest.mark.parametrize("template_key,passed", [("count", True), ("invented", False)])
async def test_assembly_validates_actual_pack_page_and_action_outputs(monkeypatch, template_key, passed):
    from factory_app.workflows.AppGenerator.tools import assemble_app_tasks as assembly

    page = {
        "schema_version": "mozaiks.app_page.v1", "name": "dashboard", "title": "Dashboard", "route": "/dashboard",
        "page_type": "analytics_dashboard", "layout": "full-width", "sections": [{
            "id": "count", "primitive": "Metric", "config": {
                "label": "Count", "api_endpoint": "/api/modules/reports/summary", "value_key": "stale_field",
            },
        }],
    }
    manifest = {"module": {"id": "reports"}, "actions": [{"id": "summary", "output_schema": {
        "type": "object", "properties": {"count": {"type": "integer"}},
    }}]}
    original_files = {
        "modules/reports/module.yaml": yaml.safe_dump(manifest), "ui/pages/dashboard.yaml": yaml.safe_dump(page),
    }
    final_page = deepcopy(page)
    final_page["sections"][0]["config"]["value_key"] = template_key
    template_files = {**original_files, "ui/pages/dashboard.yaml": yaml.safe_dump(final_page)}
    monkeypatch.setattr(assembly, "resolve_managed_capability_templates", lambda *args, **kwargs: [
        {"filename": path, "content": content} for path, content in template_files.items()
    ])
    context = ContextVariablesBridge(factory_context({
        "generated_files": original_files, "capability_packs": [], "app_build_plan": {
            "pages": [{"name": "dashboard", "route": "/dashboard"}],
            "capability_packs": [], "build_tasks": [{
                "task_id": "page", "task_type": "page_bundle", "owned_paths": ["ui/pages/dashboard.yaml"],
            }],
        },
    }))
    with _workflow_tool_invocation(context):
        result = await assembly.assemble_app_tasks(context_variables=context)
    assert result["success"] is passed, result
    if passed:
        assert yaml.safe_load(context.get("generated_files")["ui/pages/dashboard.yaml"])["sections"][0]["config"]["value_key"] == "count"
    else:
        assert "invented" in result["error"] and "Valid fields: count" in result["error"]
        assert context.get("generated_files") == original_files

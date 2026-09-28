"""Generated dashboard contracts close fields, workflow targets and paid actions."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.validate_wiring import validate_wiring
from mozaiksai.core.runtime.app.page_schema import validate_page_schema
from mozaiksai.core.usage.ledger import summarize_usage_events
from mozaiksai.core.workflow.generator_support.page_data_bindings import page_data_binding_errors


def _contracts():
    return {
        "tasks/list_tasks": {"id": "list_tasks", "output_schema": {
            "type": "object", "required": ["items", "total"], "properties": {
                "items": {"type": "array", "items": {"type": "object", "properties": {
                    "id": {"type": "string"}, "title": {"type": "string"}, "status": {"type": "string"},
                }}}, "total": {"type": "integer"},
            },
        }},
        "tasks/summarize_tasks": {"id": "summarize_tasks", "output_schema": {
            "type": "object", "properties": {"tasks_completed": {"type": "integer"},
                                               "tasks_total": {"type": "integer"}},
        }},
        "tasks/update_task": {"id": "update_task", "entitlement_gate": "task.edit", "api_surface": "public"},
    }


def _page():
    return {"name": "Dashboard", "sections": [
        {"id": "summary", "primitive": "SummaryStrip", "config": {
            "api_endpoint": "/api/modules/tasks/summarize_tasks",
            "items": [{"label": "Completed", "value_key": "tasks_completed"}],
        }},
        {"id": "tasks", "primitive": "ResourceTable", "config": {
            "api_endpoint": "/api/modules/tasks/list_tasks", "data_key": "items",
            "selection": "single",
            "columns": [{"key": "title"}, "status"],
            "actions": [{"label": "Edit", "action_type": "event", "event_type": "ui.modal.open",
                         "requires_selection": True, "payload": {"modal_id": "edit-task"}}],
        }},
        {"id": "edit-task", "primitive": "Modal", "config": {"children": [
            {"id": "edit-form", "primitive": "Form", "config": {
                "initial_values_key": "selected_row", "fields": [{"name": "title", "label": "Title", "type": "text"}],
                "submit_action": {"label": "Save", "action_type": "submit", "href": "/api/modules/tasks/update_task"},
            }},
        ]}},
    ]}


async def _validate(page=None, contracts=None, **context):
    contracts = _contracts() if contracts is None else contracts
    modules = {}
    for key, action in contracts.items():
        modules.setdefault(key.split("/", 1)[0], []).append(action)
    files = {
        "ui/pages/dashboard.yaml": yaml.safe_dump(_page() if page is None else page),
        **{f"modules/{module}/module.yaml": yaml.safe_dump({"module": {"id": module}, "actions": actions})
           for module, actions in modules.items()},
    }
    files.update(context.pop("extra_files", {}))
    return await validate_wiring({"generated_files": files, **context})


@pytest.mark.asyncio
async def test_dashboard_binds_declared_outputs_and_reachable_paid_modal_action():
    result = await _validate()
    assert result["passed"]
    assert result["checks"][0]["details"]["unreachable_gated_actions"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field,key,valid", [
    ("value_key", "completed_tasks", "tasks_completed"),
    ("detail_key", "completed_label", "tasks_total"),
    ("trend_key", "completion_change", "tasks_completed"),
])
async def test_kpi_key_must_exist_in_bound_action_output(field, key, valid):
    page = _page()
    page["sections"][0]["config"]["items"][0][field] = key
    result = await _validate(page)
    failure = next(item for item in result["failed_tests"] if item["test"] == "wiring_page_output")
    assert not result["passed"]
    assert key in failure["error"]
    assert "Valid fields:" in failure["error"] and valid in failure["error"]


def test_data_bound_metric_requires_a_key_or_static_value():
    page = _page()
    metric = page["sections"][0]["config"]["items"][0]
    metric.pop("value_key")
    assert "when no static value is supplied" in page_data_binding_errors(page, _contracts())[0]
    metric["value"] = 0
    assert page_data_binding_errors(page, _contracts()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("primitive", ["DataTable", "ResourceTable"])
@pytest.mark.parametrize("failure", ["data_key", "column", "missing_output"])
async def test_client_table_closes_response_and_row_keys_without_server_paging(primitive, failure):
    page, contracts = _page(), _contracts()
    table = page["sections"][1]
    table["primitive"] = primitive
    if failure == "data_key":
        table["config"]["data_key"] = "tasks"
    elif failure == "column":
        table["config"]["columns"].append({"key": "task_title"})
    else:
        contracts["tasks/list_tasks"].pop("output_schema")
    result = await _validate(page, contracts)
    assert not result["passed"]
    errors = [item["error"] for item in result["failed_tests"] if item["test"] == "wiring_page_output"]
    assert errors and all("Valid fields:" in error for error in errors)
    assert ("title" if failure == "column" else "items" if failure == "data_key" else "none declared") in errors[0]


def test_metric_inherits_container_data_contract_and_resolves_nested_fields():
    contracts = {"tasks/stats": {"output_schema": {"type": "object", "properties": {
        "counts": {"type": "object", "properties": {"completed": {"type": "integer"}}},
    }}}}
    page = {"name": "Dashboard", "sections": [{"id": "counts", "primitive": "Grid", "config": {
        "api_endpoint": "/api/modules/tasks/stats", "children": [
            {"id": "done", "primitive": "Metric", "config": {"value_key": "counts.completed"}},
        ],
    }}]}
    assert page_data_binding_errors(page, contracts) == []
    page["sections"][0]["config"]["children"][0]["config"]["value_key"] = "counts.done"
    assert "counts.completed" in page_data_binding_errors(page, contracts)[0]


def test_table_column_keys_follow_literal_renderer_access():
    contracts = _contracts()
    contracts["tasks/list_tasks"]["output_schema"]["properties"]["items"]["items"]["properties"]["owner"] = {
        "type": "object", "properties": {"name": {"type": "string"}},
    }
    page = _page()
    page["sections"][1]["config"]["columns"] = ["owner.name"]
    assert "not a declared row field" in page_data_binding_errors(page, contracts)[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("projection", [
    {}, {"generated_workflow_name": "InventedFlow"},
    {"workflow_integration_metadata": {"source": "context_variables", "workflows": [{"workflow_name": "InventedFlow"}]}},
])
async def test_workflow_names_without_generated_bundle_evidence_are_rejected(projection):
    page = _page()
    page["sections"][1]["config"]["empty"] = {"action": {
        "label": "Create", "action_type": "workflow", "workflow_id": "InventedFlow",
    }}
    result = await _validate(page, **projection)
    assert not result["passed"]
    failure = next(item for item in result["failed_tests"] if item["test"] == "wiring_page_workflow")
    assert "InventedFlow" in failure["error"] and "none in the bundle" in failure["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["files", "artifact"])
async def test_page_workflow_references_close_against_actual_bundle(source):
    page = _page()
    page["sections"][1]["config"]["actions"].append({
        "label": "Triage", "action_type": "workflow", "workflow_id": "Triage",
    })
    context = {"extra_files": {"workflows/Triage/orchestrator.yaml": "workflow_name: Triage\n"}} if source == "files" else {
        "workflow_integration_metadata": {"source_artifact_version_id": "bundle-v1",
                                           "workflows": [{"workflow_name": "Triage"}]},
    }
    assert (await _validate(page, **context))["passed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("slot", ["plan_action", "add_on_action"])
async def test_pricing_catalog_actions_require_real_workflows_or_reachable_module_actions(slot):
    page = _page()
    page["sections"] = [{"id": "plans", "primitive": "PricingCatalog", "config": {
        slot: {"label": "Select", "action_type": "workflow", "workflow_id": "InventedFlow"},
    }}]
    result = await _validate(page)
    assert any(item["test"] == "wiring_page_workflow" for item in result["failed_tests"])
    page["sections"][0]["config"][slot] = {
        "label": "Select", "action_type": "submit", "href": "/api/modules/tasks/update_task",
    }
    rows_key = "plans" if slot == "plan_action" else "add_ons"
    page["sections"][0]["config"][rows_key] = [{"plan_id": "pro", "label": "Pro"}] if rows_key == "plans" else [{"id": "extra", "label": "Extra"}]
    result = await _validate(page)
    assert result["passed"]
    assert result["wired"][0]["endpoint"] == "/api/modules/tasks/update_task"


@pytest.mark.asyncio
@pytest.mark.parametrize("slot,rows_key", [("plan_action", "plans"), ("add_on_action", "add_ons")])
@pytest.mark.parametrize("source", ["absent", "empty", "api", "api_with_empty_override"])
async def test_catalog_actions_require_a_source_of_visible_cards(slot, rows_key, source):
    config = {slot: {"label": "Select", "action_type": "submit", "href": "/api/modules/tasks/update_task"}}
    if source.startswith("api"):
        config["api_endpoint"] = "/api/modules/tasks/list_tasks"
        config["plans_key" if rows_key == "plans" else "add_ons_key"] = "items"
    if source in {"empty", "api_with_empty_override"}:
        config[rows_key] = []
    page = {"name": "Plans", "sections": [{"id": "plans", "primitive": "PricingCatalog", "config": config}]}
    result = await _validate(page)
    assert result["passed"] is (source == "api")
    if source != "api":
        assert result["checks"][0]["details"]["unreachable_gated_actions"] == ["tasks/update_task"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["no_form", "no_opener", "disabled_form", "wrong_module", "cyclic_modals", "selection_disabled", "selection_omitted"])
async def test_paid_action_requires_reachable_page_not_just_a_hidden_reference(failure):
    page = _page()
    contracts = _contracts()
    if failure == "no_form":
        page["sections"].pop()
    elif failure == "no_opener":
        page["sections"][1]["config"].pop("actions")
    elif failure == "disabled_form":
        page["sections"][2]["config"]["children"][0]["config"]["disabled"] = True
    elif failure == "wrong_module":
        contracts["other/update_task"] = {"id": "update_task"}
        page["sections"][2]["config"]["children"][0]["config"]["submit_action"]["href"] = "/api/modules/other/update_task"
    elif failure == "selection_disabled":
        page["sections"][1]["config"]["selection"] = "none"
    elif failure == "selection_omitted":
        page["sections"][1]["config"].pop("selection")
    else:
        opener = page["sections"][1]["config"].pop("actions")
        page["sections"][2]["config"]["actions"] = opener
    result = await _validate(page, contracts)
    assert not result["passed"]
    assert result["checks"][0]["details"]["unreachable_gated_actions"] == ["tasks/update_task"]


@pytest.mark.asyncio
async def test_reachable_modal_can_open_another_modal():
    page = _page()
    page["sections"][1]["config"]["actions"][0]["payload"]["modal_id"] = "review-task"
    page["sections"].append({"id": "review-task", "primitive": "Modal", "config": {
        "actions": [{"label": "Edit", "action_type": "event", "event_type": "ui.modal.open", "payload": {"modal_id": "edit-task"}}],
    }})
    assert (await _validate(page))["passed"]


@pytest.mark.asyncio
async def test_disabled_form_keeps_its_cancel_action_reachable():
    page = _page()
    form = page["sections"][2]["config"]["children"][0]["config"]
    form["disabled"] = True
    form["cancel_action"] = form.pop("submit_action")
    assert (await _validate(page))["passed"]


@pytest.mark.asyncio
async def test_empty_table_cta_does_not_require_toolbar_selection():
    page = _page()
    table = page["sections"][1]["config"]
    table["selection"] = "none"
    table["empty"] = {"action": table.pop("actions")[0]}
    assert (await _validate(page))["passed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["internal", "admin_internal"])
async def test_internal_entitlement_gates_do_not_require_page_actions(surface):
    contracts = _contracts()
    internal = deepcopy(contracts["tasks/update_task"])
    internal.update(id="background_update", api_surface=surface)
    contracts["tasks/background_update"] = internal
    assert (await _validate(contracts=contracts))["passed"]


@pytest.mark.asyncio
async def test_public_gated_read_is_reached_by_page_data_source():
    contracts = _contracts()
    contracts["tasks/summarize_tasks"]["entitlement_gate"] = "dashboard.view"
    assert (await _validate(contracts=contracts))["passed"]


@pytest.mark.parametrize("name", ["billing", "usage"])
def test_shipped_billing_pages_select_scalar_fields_from_declared_response(name):
    templates = Path(__file__).resolve().parents[1] / "factory_app/build_context/mozaikspay/templates"
    module = yaml.safe_load((templates / "modules/billing_portal/module.yaml").read_text(encoding="utf-8"))
    contracts = {f"billing_portal/{action['id']}": action for action in module["actions"]}
    page = yaml.safe_load((templates / f"ui/pages/{name}.yaml").read_text(encoding="utf-8"))
    validate_page_schema(page)
    assert page_data_binding_errors(page, contracts) == []
    summary = page["sections"][0]
    assert summary["primitive"] == "SummaryStrip"
    if name == "usage":
        response = {"runtime_ai_usage": summarize_usage_events([])}
        for item in summary["config"]["items"]:
            value = response
            for key in item["value_key"].split("."):
                value = value[key]
            assert value == 0

"""Page producers must not hide bounded responses behind client-only controls."""
from copy import deepcopy

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.validate_wiring import validate_wiring
from mozaiksai.core.runtime.app.page_schema import validate_page_schema
from mozaiksai.core.workflow.generator_support.page_action_bindings import (
    page_refresh_binding_errors,
)
from mozaiksai.core.workflow.generator_support.page_data_bindings import page_query_binding_errors
from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    compile_page_data_sources,
    pack_template_page_errors,
)


def _action(*, search=True):
    properties = {"page": {"type": "integer"}, "page_size": {"type": "integer"}}
    if search:
        properties["search"] = {"type": "string"}
    return {
        "id": "browse_records", "permissions": [],
        "input_schema": {"type": "object", "additionalProperties": False, "properties": properties},
        "output_schema": {"type": "object", "required": ["items", "total"], "properties": {
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "record_id": {"type": "string"}, "name": {"type": "string"},
            }}}, "total": {"type": "integer"},
        }},
    }


def _page(*, mode="client", primitive="DataTable"):
    config = {"api_endpoint": "/api/modules/records/browse_records", "data_key": "items",
              "columns": [{"key": "name"}], "pagination": True, "page_size": 20, "search": True}
    if mode is not None:
        config["pagination_mode"] = mode
    if mode == "server":
        config["total_key"] = "total"
    return {"schema_version": "mozaiks.app_page.v1", "name": "records", "route": "/records",
            "title": "Records", "page_type": "record_list", "layout": "full-width",
            "sections": [{"id": "records-table", "primitive": primitive, "config": config}]}


def _contracts(action):
    return {"records/browse_records": action}


@pytest.mark.parametrize("primitive,mode", [("DataTable", "client"), ("DataTable", None),
                                            ("ResourceTable", "client")])
@pytest.mark.parametrize("custom_fields", [[], ["query", "category", "availability_status"], ["status"]])
def test_explicit_paged_envelope_rejects_client_controls_and_reports_missing_search(primitive, mode, custom_fields):
    action = _action(search=False)
    action["input_schema"]["properties"].update({name: {"type": "string"} for name in custom_fields})
    page = _page(mode=mode, primitive=primitive)
    before = deepcopy((page, action))
    errors = page_query_binding_errors(page, _contracts(action))
    assert len(errors) == 1
    assert "records/records-table (records/browse_records)" in errors[0]["error"]
    assert "client controls cannot reach later backend pages" in errors[0]["error"]
    assert "explicit string input named search" in errors[0]["error"]
    assert (page, action) == before


@pytest.mark.parametrize("change", ["unpaged", "optional_total", "wrong_page_kind", "unrelated_count"])
def test_fully_loaded_or_noncanonical_outputs_do_not_gain_server_contract(change):
    action = _action()
    if change == "unpaged":
        action["input_schema"]["properties"] = {}
    elif change == "optional_total":
        action["output_schema"]["required"] = ["items"]
    elif change == "wrong_page_kind":
        action["input_schema"]["properties"]["page"] = {"type": "string"}
    else:
        action["output_schema"]["properties"]["overall_count"] = action["output_schema"]["properties"].pop("total")
        action["output_schema"]["required"] = ["items", "overall_count"]
    assert page_query_binding_errors(_page(), _contracts(action)) == []


@pytest.mark.parametrize("failure", [None, "missing_search", "extra_required", "invalid_size", "internal"])
def test_shared_server_contract_runs_at_authoring_and_pack_validation(failure):
    action = _action()
    page = _page(mode="server")
    if failure == "missing_search":
        del action["input_schema"]["properties"]["search"]
    elif failure == "extra_required":
        action["input_schema"]["required"] = ["owner_id"]
    elif failure == "invalid_size":
        page["sections"][0]["config"]["page_size"] = 101
    elif failure == "internal":
        action["api_surface"] = "internal"
    modules = {"records": {"browse_records": action}}
    pack_errors = pack_template_page_errors(yaml.safe_dump(page), path="ui/pages/records.yaml", modules=modules)
    if failure:
        assert pack_errors
        with pytest.raises(ValueError):
            compile_page_data_sources(deepcopy(page), modules)
    else:
        assert pack_errors == []
        compile_page_data_sources(deepcopy(page), modules)


def _refresh(page, payload):
    page["sections"].append({"id": "filters", "primitive": "Form", "config": {
        "fields": [{"name": "category", "label": "Category", "type": "text"}],
        "submit_action": {"action_type": "event", "label": "Apply",
                          "event_type": "ui.datatable.refresh", "payload": payload},
    }})
    return page


@pytest.mark.parametrize("payload", [{}, {"component_id": "missing"}, {"component_id": "{form.target}"},
                                    {"component_id": "records-table", "category": "{form.category}"},
                                    {"query": "{form.query}"}])
def test_refresh_cannot_invent_filter_contract_or_unresolved_target(payload):
    page = _refresh(_page(), payload)
    before = deepcopy(page)
    errors = page_refresh_binding_errors(page)
    assert len(errors) == 1
    assert "records/filters.submit_action" in errors[0]
    assert "Valid targets: records-table" in errors[0]
    assert "form fields and filters are not query bindings" in errors[0]
    assert page == before


def test_targeted_nested_refresh_and_unrelated_events_keep_existing_meaning():
    page = _refresh(_page(), [{"key": "component_id", "value": "records-table"}])
    table = page["sections"].pop(0)
    page["sections"].append({"id": "container", "primitive": "Grid", "config": {"children": [table]}})
    assert page_refresh_binding_errors(page) == []
    page["sections"][0]["config"]["submit_action"].update(event_type="ui.form.set_field", payload={"field": "name"})
    assert page_refresh_binding_errors(page) == []


@pytest.mark.parametrize("endpoint", ["/api/modules/records/browse_records", "/api/notifications", None])
def test_nested_refresh_preserves_admitted_module_and_platform_fetch_targets(endpoint):
    page = _refresh(_page(), {"component_id": "records-table"})
    table = page["sections"].pop(0)
    table["config"]["api_endpoint"] = endpoint
    page["sections"].append({"id": "container", "primitive": "Grid", "config": {
        "columns": 1, "children": [table],
    }})
    admitted = validate_page_schema(page).model_dump(mode="json", exclude_none=True)
    errors = page_refresh_binding_errors(admitted)
    if endpoint:
        assert errors == []
    else:
        assert len(errors) == 1
        assert "Valid targets: (none)" in errors[0]


def test_canonical_live_acceptance_fixture_uses_its_compiled_server_query():
    from scripts.smoke_appgenerator_live_acceptance import build_appgenerator_acceptance_files

    files = build_appgenerator_acceptance_files()
    module = yaml.safe_load(files["modules/support_tickets/module.yaml"])
    contracts = {f"support_tickets/{action['id']}": action for action in module["actions"]}
    page = yaml.safe_load(files["ui/pages/support_tickets.yaml"])
    table = page["sections"][0]["config"]
    assert table["pagination_mode"] == "server"
    assert page_query_binding_errors(page, contracts) == []


@pytest.mark.asyncio
async def test_producer_and_final_gate_report_all_four_paging_failures_and_filter_error():
    action = _action(search=False)
    contracts = _contracts(action)
    pages = []
    for index in range(4):
        page = _page(primitive="ResourceTable" if index == 0 else "DataTable")
        page.update(name=f"records{index}", route=f"/records{index}")
        if index == 0:
            _refresh(page, {"query": "{form.query}", "category": "{form.category}"})
        pages.append(page)
        with pytest.raises(ValueError, match="client controls cannot reach later backend pages"):
            compile_page_data_sources(deepcopy(page), {"records": {"browse_records": action}})
    assert sum(len(page_query_binding_errors(page, contracts)) for page in pages) == 4
    files = {f"ui/pages/{page['name']}.yaml": yaml.safe_dump(page) for page in pages}
    files["modules/records/module.yaml"] = yaml.safe_dump({"module": {"id": "records"}, "actions": [action]})
    result = await validate_wiring({"generated_files": files})
    failures = result["failed_tests"]
    assert result["passed"] is False
    assert sum(item["test"] == "wiring_server_table_contract" for item in failures) == 4
    assert sum(item["test"] == "wiring_page_refresh" for item in failures) == 1

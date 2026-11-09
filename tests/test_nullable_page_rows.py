"""Nullable custom-read rows keep their declared selector and column contract."""
from copy import deepcopy

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.validate_wiring import validate_wiring
from mozaiksai.core.workflow.generator_support.code_files import _materialize_schema_contract
from mozaiksai.core.workflow.generator_support.page_data_bindings import (
    page_data_binding_errors,
    page_query_binding_errors,
)
from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    compile_page_data_sources,
    pack_template_page_errors,
)
from tests.test_generated_page_query_contracts import _action as paged_action
from tests.test_generated_page_query_contracts import _page as paged_page


def summary_action():
    return {"id": "summary", "permissions": [], "input_schema": {"type": "object", "properties": {}},
            "output_schema": _materialize_schema_contract({"type": "object", "properties": [{
                "name": "recent", "type": "array", "required": False, "nullable": True, "items_type": "object",
                "items_properties": [
                    {"name": "title", "type": "string", "required": True, "nullable": False},
                    {"name": "updated_at", "type": "string", "required": True, "nullable": False},
                ],
            }]})}


def summary_page(primitive="DataTable"):
    return {"schema_version": "mozaiks.app_page.v1", "name": "summary", "title": "Summary", "route": "/summary",
            "layout": "full-width", "page_type": "analytics_dashboard", "sections": [{"id": "recent", "primitive": primitive, "config": {
                "data_source": {"module_id": "records", "action_id": "summary"}, "data_key": "recent",
                "pagination_mode": "client", "pagination": False, "search": False,
                "columns": [{"key": "title", "label": "Title"}, {"key": "updated_at", "label": "Updated"}],
            }}]}


@pytest.mark.parametrize("primitive", ["DataTable", "ResourceTable"])
@pytest.mark.parametrize("reverse_types", [False, True])
async def test_nullable_optional_rows_close_producer_pack_and_final_wiring(primitive, reverse_types):
    action, page = summary_action(), summary_page(primitive)
    rows = action["output_schema"]["properties"]["recent"]
    assert rows["type"] == ["array", "null"] and "recent" not in action["output_schema"].get("required", [])
    if reverse_types:
        rows["type"].reverse()
    before = deepcopy(action)
    modules = {"records": {"summary": action}}
    compile_page_data_sources(page, modules, reject_api_endpoints=True)
    assert page["sections"][0]["config"]["data_key"] == "recent"
    assert pack_template_page_errors(yaml.safe_dump(page), path="ui/pages/summary.yaml", modules=modules) == []
    files = {"ui/pages/summary.yaml": yaml.safe_dump(page),
             "modules/records/module.yaml": yaml.safe_dump({"module": {"id": "records"}, "actions": [action]})}
    assert (await validate_wiring({"generated_files": files}))["passed"]
    assert action == before


def test_nullable_rows_do_not_hide_an_undeclared_column():
    action, page = summary_action(), summary_page()
    page["sections"][0]["config"]["columns"].append({"key": "created_at", "label": "Created"})
    with pytest.raises(ValueError, match="'created_at' is not a declared row field") as caught:
        compile_page_data_sources(page, {"records": {"summary": action}}, reject_api_endpoints=True)
    assert "Valid fields: title, updated_at" in str(caught.value)


def test_canonical_list_materialization_preserves_an_explicit_nullable_selector():
    action, page = summary_action(), summary_page()
    action["output_schema"]["properties"].update({
        "items": {"type": "array", "items": {"type": "object", "properties": {"other": {"type": "string"}}}},
        "total": {"type": "integer"},
    })
    compile_page_data_sources(page, {"records": {"summary": action}}, reject_api_endpoints=True)
    assert page["sections"][0]["config"]["data_key"] == "recent"


@pytest.mark.parametrize("schema", [
    {"type": "object"}, {"type": "null"}, {"type": ["object", "null"]}, {"type": ["array", "object"]},
    {"type": ["array", "null", "string"]}, {"type": ["array", "array", "null"]}, {"type": ["array"]},
    {"type": ["array", {}]}, {"$ref": "#/definitions/rows"}, {"$dynamicRef": "#rows"},
    {"anyOf": [{"type": "array"}, {"type": "null"}]}, {},
])
def test_noncanonical_array_shapes_remain_rejected(schema):
    action, page = summary_action(), summary_page()
    schema = {**schema, "items": action["output_schema"]["properties"]["recent"]["items"]}
    action["output_schema"]["properties"]["recent"] = schema
    with pytest.raises(ValueError, match="must select a declared array"):
        compile_page_data_sources(page, {"records": {"summary": action}}, reject_api_endpoints=True)


@pytest.mark.parametrize("primitive,mode", [("DataTable", "client"), ("DataTable", None),
                                            ("ResourceTable", "client"), ("DataTable", "server")])
def test_nullable_paged_rows_cannot_bypass_strict_server_contract(primitive, mode):
    action, page = paged_action(), paged_page(mode=mode, primitive=primitive)
    action["output_schema"]["properties"]["items"]["type"] = ["array", "null"]
    with pytest.raises(ValueError, match="must resolve to a required array") as caught:
        compile_page_data_sources(page, {"records": {"browse_records": action}})
    if mode != "server":
        assert "client controls cannot reach later backend pages" in str(caught.value)


@pytest.mark.parametrize("defect", ["optional_rows", "nullable_total", "optional_total", "reference_rows"])
def test_other_server_requirements_stay_strict(defect):
    action, page = paged_action(), paged_page(mode="server")
    output = action["output_schema"]
    if defect == "optional_rows":
        output["required"].remove("items")
    elif defect == "optional_total":
        output["required"].remove("total")
    elif defect == "nullable_total":
        output["properties"]["total"]["type"] = ["integer", "null"]
    else:
        output["properties"]["items"] = {"$ref": "#/definitions/rows"}
    with pytest.raises(ValueError):
        compile_page_data_sources(page, {"records": {"browse_records": action}})


def test_nonnullable_array_still_has_exact_column_checks():
    action, page = summary_action(), summary_page()
    action["output_schema"]["properties"]["recent"]["type"] = "array"
    compile_page_data_sources(page, {"records": {"summary": action}})
    assert page_data_binding_errors(page, {"records/summary": action}) == []


@pytest.mark.parametrize("rows_type", ["array", ["array", "null"]])
@pytest.mark.parametrize("total", ["optional", "nullable"])
def test_noncanonical_total_preserves_existing_client_classification(rows_type, total):
    action, page = paged_action(), paged_page(mode="client")
    output = action["output_schema"]
    output["properties"]["items"]["type"] = rows_type
    if total == "optional":
        output["required"].remove("total")
    else:
        output["properties"]["total"]["type"] = ["integer", "null"]
    assert page_query_binding_errors(page, {"records/browse_records": action}) == []
    compile_page_data_sources(page, {"records": {"browse_records": action}})
    # Existing noncanonical-envelope inference is not proof of valid server paging.
    page["sections"][0]["config"].update(pagination_mode="server", total_key="total")
    with pytest.raises(ValueError, match="Server paging"):
        compile_page_data_sources(page, {"records": {"browse_records": action}})

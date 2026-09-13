from copy import deepcopy

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.validate_wiring import validate_wiring
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge


def bundle():
    action = {
        "id": "list",
        "input_schema": {
            "type": "object", "additionalProperties": False,
            "properties": {"page": {"type": "integer", "minimum": 1},
                           "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
                           "search": {"type": "string"}},
        },
        "output_schema": {
            "type": "object", "required": ["books", "stats"],
            "properties": {"books": {"type": "array", "items": {"type": "object"}},
                           "stats": {"type": "object", "required": ["total"],
                                     "properties": {"total": {"type": "integer"}}}},
        },
    }
    page = {"name": "books", "sections": [{"id": "grid", "primitive": "Grid", "config": {
        "children": [{"id": "books", "primitive": "DataTable", "config": {
            "api_endpoint": "/api/modules/books/list", "pagination_mode": "server",
            "pagination": True, "search": True, "page_size": 20, "data_key": "books", "total_key": "stats.total",
        }}],
    }}]}
    return action, page


async def check(action, page):
    context = ContextVariablesBridge({"generated_files": {
        "ui/pages/books.yaml": yaml.safe_dump(page),
        "modules/books/module.yaml": yaml.safe_dump({"module": {"id": "books"}, "actions": [action]}),
    }})
    result = await validate_wiring(context)
    assert context.get("wiring_validation_passed") == result["passed"]
    return result


@pytest.mark.asyncio
async def test_server_table_contract_closes_against_exact_generated_action():
    action, page = bundle()
    assert (await check(action, page))["passed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["page", "page_size", "search"])
@pytest.mark.parametrize("failure", ["missing", "wrong_type"])
async def test_server_table_requires_explicit_matching_query_fields(field, failure):
    action, page = bundle()
    if failure == "missing":
        del action["input_schema"]["properties"][field]
    else:
        action["input_schema"]["properties"][field] = {"type": "boolean"}
    result = await check(action, page)
    assert not result["passed"]
    assert result["failed_tests"][0]["test"] == "wiring_server_table_contract"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["extra_required", "page_size_limit", "nonempty_search", "empty_only_search", "page_limit", "schema_ref"])
async def test_server_table_query_must_satisfy_action_schema(failure):
    action, page = bundle()
    schema = action["input_schema"]
    if failure == "extra_required":
        schema["required"] = ["category"]
    elif failure == "page_size_limit":
        schema["properties"]["page_size"]["maximum"] = 10
    elif failure == "nonempty_search":
        schema["properties"]["search"]["minLength"] = 1
    elif failure == "empty_only_search":
        schema["properties"]["search"]["maxLength"] = 0
    elif failure == "page_limit":
        schema["properties"]["page"]["maximum"] = 1
    else:
        schema["$ref"] = "https://must-not-fetch.invalid/schema"
    assert not (await check(action, page))["passed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["wrong_rows", "string_rows", "untyped_rows", "wrong_total", "missing_total", "optional_parent", "optional_total", "missing_output"])
async def test_server_table_requires_declared_row_and_count_paths(failure):
    action, page = bundle()
    schema = action["output_schema"]
    if failure == "wrong_rows":
        schema["properties"]["books"]["type"] = "object"
    elif failure == "string_rows":
        schema["properties"]["books"]["items"]["type"] = "string"
    elif failure == "untyped_rows":
        del schema["properties"]["books"]["items"]
    elif failure == "wrong_total":
        schema["properties"]["stats"]["properties"]["total"]["type"] = "string"
    elif failure == "missing_total":
        del schema["properties"]["stats"]["properties"]["total"]
    elif failure == "optional_parent":
        schema["required"] = ["books"]
    elif failure == "optional_total":
        schema["properties"]["stats"]["required"] = []
    else:
        del action["output_schema"]
    assert not (await check(action, page))["passed"]


@pytest.mark.asyncio
async def test_client_table_does_not_gain_server_query_requirements():
    action, page = bundle()
    page = deepcopy(page)
    page["sections"][0]["config"]["children"][0]["config"]["pagination_mode"] = "client"
    action["input_schema"] = {"type": "object", "properties": {}}
    action["output_schema"] = {}
    assert (await check(action, page))["passed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["internal", "admin_internal"])
async def test_server_table_cannot_call_internal_action(surface):
    action, page = bundle()
    action["api_surface"] = surface
    assert not (await check(action, page))["passed"]


@pytest.mark.asyncio
async def test_malformed_surface_returns_a_persisted_failure_report():
    action, page = bundle()
    action["api_surface"] = ["public"]
    assert not (await check(action, page))["passed"]


@pytest.mark.asyncio
async def test_server_table_standalone_check_reads_actual_module_contract(tmp_path):
    action, page = bundle()
    module = tmp_path / "modules/books/module.yaml"
    module.parent.mkdir(parents=True)
    module.write_text(yaml.safe_dump({"module": {"id": "books"}, "actions": [action]}), encoding="utf-8")
    context = {"app_pages": [page], "generated_app_dir": str(tmp_path)}
    assert (await validate_wiring(context))["passed"]
    module.write_bytes(b"\xff")
    assert not (await validate_wiring(context))["passed"]


@pytest.mark.asyncio
async def test_planned_action_names_cannot_authorize_server_bindings():
    _, page = bundle()
    result = await validate_wiring({"app_pages": [page], "app_build_plan": {
        "capability_packs": [{"module_id": "books", "actions": ["list"]}],
    }})
    assert not result["passed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["snapshot", "disk"])
@pytest.mark.parametrize("fault", [None, "server", "ask", "endpoint"])
async def test_ask_and_server_bindings_share_resolved_pages(tmp_path, source, fault):
    action, page = bundle()
    action.update(ask_context_safe=True, permissions=[])
    page["meta"] = {"ask_context": [{"module": "books", "action": "list"}]}
    if fault == "server":
        action["output_schema"]["properties"]["stats"]["required"] = []
    elif fault == "ask":
        page["meta"]["ask_context"][0]["action"] = "missing"
    elif fault == "endpoint":
        page["sections"][0]["config"]["children"][0]["config"]["api_endpoint"] = "/api/modules/books/missing"
    files = {
        "ui/pages/books.yaml": yaml.safe_dump(page),
        "modules/books/module.yaml": yaml.safe_dump({"module": {"id": "books"}, "actions": [action]}),
    }
    if source == "disk":
        for name, content in files.items():
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        context = {"generated_app_dir": str(tmp_path)}
    else:
        context = {"generated_files": files}
    result = await validate_wiring(ContextVariablesBridge(context))
    assert result["passed"] is (fault is None)
    assert result["checks"][0]["details"]["total_endpoints_referenced"] == 1
    if fault:
        expected = {"server": "wiring_server_table_contract", "ask": "wiring_ask_context",
                    "endpoint": "wiring_orphaned_endpoint"}[fault]
        assert expected in {item["test"] for item in result["failed_tests"]}


@pytest.mark.asyncio
async def test_empty_saved_snapshot_cannot_fall_back_to_disk_ask_pages(tmp_path):
    path = tmp_path / "ui/route_manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"pages":[{"path":"/books","component":"Books"}]}', encoding="utf-8")
    result = await validate_wiring({"generated_files": {}, "generated_app_dir": str(tmp_path)})
    assert result["passed"] is False
    assert result["failed_tests"][0]["test"] == "wiring_missing_input"

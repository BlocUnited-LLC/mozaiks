from __future__ import annotations

from copy import deepcopy

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools import app_validation
from factory_app.workflows.AppGenerator.tools.validate_wiring import validate_wiring
from mozaiksai.core.runtime.app.page_schema import validate_page_schema
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from tests.test_generated_app_functional_acceptance import _basic_crud_files


def _page(endpoint: str = "/api/modules/orders/list_orders") -> dict:
    return {
        "schema_version": "mozaiks.app_page.v1",
        "name": "orders",
        "route": "/orders",
        "title": "Orders",
        "page_type": "record_list",
        "layout": "full-width",
        "sections": [{
            "id": "orders-list",
            "primitive": "DataTable",
            "config": {"columns": ["order_id"], "api_endpoint": endpoint},
        }],
    }


def _files(endpoint: str = "/api/modules/orders/list_orders") -> dict[str, str]:
    files = _basic_crud_files()
    files["ui/pages/orders.yaml"] = yaml.safe_dump(_page(endpoint))
    return files


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["generated_files", "app_pages"])
@pytest.mark.parametrize("endpoint,passed", [
    ("/api/modules/orders/list_orders", True), ("/api/orders", False),
])
async def test_wiring_reads_real_frozen_bridge(source, endpoint, passed):
    data = {"app_build_plan": {"capability_packs": [
        {"module_id": "orders", "actions": [{"id": "list_orders"}]},
    ]}}
    data[source] = _files(endpoint) if source == "generated_files" else [_page(endpoint)]
    context = ContextVariablesBridge(data)
    before = context.snapshot()

    result = await validate_wiring(context)

    assert result["checks"][0]["details"]["total_endpoints_referenced"] == 1
    assert result["checks"][0]["details"]["module_registry_available"] is True
    assert result["passed"] is passed
    assert context.get("wiring_validation_passed") is passed
    for key, value in before.items():
        assert context.snapshot()[key] == value


@pytest.mark.asyncio
async def test_saved_pages_override_stale_app_pages():
    result = await validate_wiring({
        "generated_files": _files("/api/orders"), "app_pages": [_page()],
    })
    assert result["passed"] is False
    assert result["orphaned_pages"][0]["endpoint"] == "/api/orders"


@pytest.mark.asyncio
async def test_plan_and_old_disk_actions_cannot_mask_missing_saved_action(tmp_path):
    files = _files("/api/modules/orders/invented")
    module = tmp_path / "modules/orders/module.yaml"
    module.parent.mkdir(parents=True)
    module.write_text("module: {id: orders}\nactions: [{id: invented}]\n", encoding="utf-8")
    result = await validate_wiring({
        "generated_files": files,
        "generated_app_dir": str(tmp_path),
        "app_build_plan": {"capability_packs": [{"module_id": "orders", "actions": ["invented"]}]},
    })
    assert result["passed"] is False
    assert result["orphaned_pages"][0]["endpoint"] == "/api/modules/orders/invented"


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/api/orders", "/api/modules/orders/list_orders"])
async def test_missing_module_registry_does_not_downgrade_unresolved_references(endpoint):
    result = await validate_wiring({
        "generated_files": {"ui/pages/orders.yaml": yaml.safe_dump(_page(endpoint))},
    })
    assert result["passed"] is False
    assert result["checks"][0]["details"]["blocking"] is True
    assert result["failed_tests"][0]["test"] == "wiring_orphaned_endpoint"


@pytest.mark.asyncio
@pytest.mark.parametrize("context", [None, {}, {"generated_files": {}}, {"generated_files": "invalid"}])
async def test_no_input_is_not_a_successful_wiring_check(context):
    result = await validate_wiring(context)
    assert result["passed"] is False
    assert result["failed_tests"][0]["test"] == "wiring_missing_input"


@pytest.mark.asyncio
@pytest.mark.parametrize("placement", ["action", "empty", "submit_action", "cancel_action", "actions"])
async def test_all_declared_nested_action_slots_are_checked(placement):
    action = {"label": "Save", "action_type": "submit", "href": "/api/orders"}
    config = {placement: action}
    if placement == "empty":
        config = {"empty": {"action": action}}
    elif placement == "actions":
        config = {"actions": [action]}
    page = _page()
    page["sections"][0]["config"] = {"children": [
        {"id": "inner", "config": config},
    ]}
    result = await validate_wiring({
        "generated_files": {**_files(), "ui/pages/orders.yaml": yaml.safe_dump(page)},
    })
    assert result["passed"] is False
    assert result["checks"][0]["details"]["total_endpoints_referenced"] == 1


@pytest.mark.asyncio
async def test_static_navigation_and_custom_ui_bundle_need_no_module_endpoints():
    page = _page()
    page["sections"] = [{"id": "header", "primitive": "PageHeader", "config": {
        "title": "Orders", "actions": [{"label": "Help", "action_type": "navigate", "href": "/help"}],
    }}]
    for files in (
        {"ui/pages/orders.yaml": yaml.safe_dump(page)},
        {"ui/route_manifest.json": '{"pages":[{"path":"/help","component":"HelpPage"}]}',
         "ui/pages/custom/HelpPage.jsx": "export default function HelpPage() { return null; }"},
    ):
        result = await validate_wiring({"generated_files": files})
        assert result["passed"] is True
        assert result["checks"][0]["details"]["total_endpoints_referenced"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/api/me/usage", "/api/me/tokens", "/api/me/tokens/ledger"])
async def test_supported_platform_reads_do_not_require_app_modules(endpoint):
    result = await validate_wiring({"generated_files": {
        "ui/pages/orders.yaml": yaml.safe_dump(_page(endpoint)),
    }})
    assert result["passed"] is True
    assert result["checks"][0]["details"]["platform_endpoint_count"] == 1


@pytest.mark.parametrize("endpoint", ["/api/notifications", "/api/custom/reports"])
def test_runtime_schema_keeps_host_and_custom_api_paths(endpoint):
    page = validate_page_schema(_page(endpoint), action_index={})
    assert page.sections[0].config["api_endpoint"] == endpoint


@pytest.mark.asyncio
@pytest.mark.parametrize("context_kind", ["none", "dict", "bridge"])
async def test_acceptance_wires_the_explicit_snapshot_without_context_roundtrip(context_kind):
    data = {"app_pages": [_page()], "generated_files": _files()}
    context = {"none": None, "dict": data, "bridge": ContextVariablesBridge(data)}[context_kind]
    result = await app_validation.run_app_bundle_acceptance_gate(files=_files("/api/orders"), context_variables=context)
    assert result["module_wiring"]["passed"] is False
    assert result["module_wiring"]["checks"][0]["details"]["total_endpoints_referenced"] == 1
    assert result["passed"] is False
    if context is not None:
        assert context.get("wiring_validation_passed") is False
        assert context.get("app_bundle_acceptance_status") == "failed"


@pytest.mark.asyncio
async def test_skip_runtime_still_fails_mandatory_wiring_with_real_bridge():
    context = ContextVariablesBridge({"generated_files": _files("/api/orders")})
    result = await app_validation.validate_app_bundle_from_request(
        {"validation_strategy": "skip", "start_dev_server": False}, context_variables=context,
    )
    assert result["wiring_validation_result"]["passed"] is False
    assert result["status"] == "failed"
    assert context.get("app_bundle_acceptance_status") == "failed"


@pytest.mark.asyncio
async def test_valid_explicit_bundle_counts_real_wired_endpoints():
    files = _basic_crud_files()
    before = deepcopy(files)
    result = await app_validation.run_app_bundle_acceptance_gate(files=files)
    assert result["module_wiring"]["checks"][0]["details"]["total_endpoints_referenced"] == 2
    assert result["module_wiring"]["checks"][0]["details"]["wired_count"] == 2
    assert result["passed"] is True
    assert files == before

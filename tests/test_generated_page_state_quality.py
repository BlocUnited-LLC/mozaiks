"""Live-generation regressions: status sections rendered alongside populated tables."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from factory_app.workflows._shared.generated_ui_contract import audit_page_schemas
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_generated_bundle
from factory_app.workflows.AppGenerator.tools.repair_policy import prepare_bundle_repair
from factory_app.workflows.AppGenerator.tools.ui_quality import review_ui_quality
from mozaiksai.core.runtime.app.page_schema import validate_page_schema

FIXTURES = Path(__file__).parent / "fixtures" / "generated-page-states"
STATE_PRIMITIVES = {"InlineEmptyState", "LoadingState", "ErrorState"}


def _page(name):
    return yaml.safe_load((FIXTURES / f"{name}.yaml").read_text(encoding="utf-8"))


def _corrected_page(name):
    page = _page(name)
    page["sections"] = [s for s in page["sections"] if s["primitive"] not in STATE_PRIMITIVES]
    if name == "notes":
        table = page["sections"][0]["config"]
        create = deepcopy(table["empty"]["action"])
        create["id"] = "new-note"
        table["actions"].insert(0, create)
    return page


@pytest.mark.parametrize("name,count", [("notes", 3), ("dashboard", 1)])
def test_live_generated_pages_reject_unconditional_states(name, count):
    page = _page(name)
    original = deepcopy(page)
    validate_page_schema(page)  # The individual primitives are valid; their composition is not.
    warnings = audit_page_schemas([page])
    assert len(warnings) == count
    assert all("unconditional" in warning and "config.empty" in warning for warning in warnings)
    assert page == original


@pytest.mark.parametrize("name", ["notes", "dashboard"])
def test_final_scanner_routes_task_batch_page_states_to_existing_owned_repair(name):
    path = f"ui/pages/{name}.yaml"
    content = (FIXTURES / f"{name}.yaml").read_text(encoding="utf-8")
    errors = scan_generated_bundle({path: content})
    state_errors = [error for error in errors if "unconditional" in error]
    assert state_errors and all(error.startswith(path + ":") for error in state_errors)
    context = {
        "app_schema_ready": False,
        "app_task_batch_status": "completed",
        "app_build_plan": {"build_tasks": [{
            "task_id": "page", "initial_agent": "AppSchemaAgent", "task_type": "page_bundle",
            "owned_paths": [path], "depends_on": [],
        }]},
        "app_task_batch_results": {"page": {"code_files": [{"filename": path, "content": content}]}},
    }
    repair = prepare_bundle_repair({"passed": False, "errors": state_errors}, context)
    assert repair["status"] == "needs_revision"
    assert repair["target_agent"] == "AppSchemaAgent"
    assert repair["active"]["allowed_paths"] == [path]


@pytest.mark.parametrize("name", ["notes", "dashboard"])
def test_schema_quality_gate_requests_revision_for_live_generated_states(name):
    context = {"app_schema_ready": True, "app_manifest": {"app_name": "Notes"}, "app_pages": [_page(name)]}
    result = review_ui_quality(context_variables=context)
    assert result["status"] == "needs_revision"
    assert "unconditional" in result["revision_request"]


@pytest.mark.parametrize("name", ["notes", "dashboard"])
def test_corrected_pages_keep_table_states_actions_and_roles(name):
    page = _corrected_page(name)
    validate_page_schema(page)
    assert audit_page_schemas([page]) == []
    errors = scan_generated_bundle({f"ui/pages/{name}.yaml": yaml.safe_dump(page)})
    assert not any("unconditional" in error for error in errors)
    assert page["roles"] == ["member"]
    if name == "notes":
        table = page["sections"][0]["config"]
        assert table["empty"] == _page(name)["sections"][0]["config"]["empty"]
        assert table["actions"][0]["requires_selection"] is False
        assert table["actions"][0]["payload"]["modal_id"] == "note-create-modal"


@pytest.mark.parametrize("primitive", sorted(STATE_PRIMITIVES))
@pytest.mark.parametrize("placement", ["dedicated_page", "modal"])
def test_static_status_pages_and_modal_content_remain_valid(primitive, placement):
    page = _corrected_page("dashboard")
    config = {"ErrorState": {"message": "Unavailable"}, "InlineEmptyState": {"title": "No results"}, "LoadingState": {}}
    state = {"id": "status", "primitive": primitive, "config": config[primitive]}
    if placement == "dedicated_page":
        page["sections"] = [state]
    else:
        page["sections"].append({"id": "details", "primitive": "Modal", "config": {"children": [state]}})
    validate_page_schema(page)
    assert audit_page_schemas([page]) == []
    assert not any("unconditional" in error for error in scan_generated_bundle({
        "ui/pages/dashboard.yaml": yaml.safe_dump(page),
    }))


@pytest.mark.parametrize("table_primitive", ["DataTable", "ResourceTable"])
def test_check_uses_binding_and_page_taxonomy_not_status_copy(table_primitive):
    page = _page("dashboard")
    page["sections"][1]["primitive"] = table_primitive
    page["sections"][2]["config"]["title"] = "A completely different message"
    assert any("unconditional" in warning for warning in audit_page_schemas([page]))
    page["sections"][1]["config"].pop("api_endpoint")
    assert audit_page_schemas([page]) == []  # An unrelated summary is not evidence of a live table.
    page["sections"][1]["config"]["api_endpoint"] = "/api/modules/notes/summarize_notes"
    page["page_type"] = "settings"
    assert audit_page_schemas([page]) == []

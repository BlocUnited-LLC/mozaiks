"""A reviewed declarative page must expose its required write actions on its own route."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import (
    _apply_planned_page_contracts,
)
from mozaiksai.core.workflow.generator_support import design_access_policy
from mozaiksai.core.workflow.generator_support.page_action_bindings import (
    planned_page_writes,
    validate_required_page_writes,
)
from mozaiksai.core.workflow.task_batches import execute_task_batches_for_trigger
from tests.test_appschema_scoped_manifest import _batch_config, _task


def _write(action, action_type="submit"):
    return {"label": action, "action_type": action_type, "href": f"/api/modules/content/{action}"}


def _editor():
    return {
        "schema_version": "mozaiks.app_page.v1", "name": "editor", "route": "/editor",
        "title": "Editor", "page_type": "record_list", "layout": "full-width",
        "sections": [
            {"id": "entries", "primitive": "DataTable", "config": {
                "columns": [{"key": "title"}], "api_endpoint": "/api/modules/content/list_entries",
                "selection": "single", "actions": [
                    {**_write("archive_entry", "delete"), "requires_selection": True},
                    {"label": "New draft", "action_type": "event", "event_type": "ui.modal.open",
                     "payload": {"modal_id": "create"}},
                ],
            }},
            {"id": "create", "primitive": "Modal", "config": {"children": [
                {"id": "form", "primitive": "Form", "config": {
                    "fields": [{"name": "title", "type": "text", "label": "Title"}],
                    "submit_action": _write("create_entry"),
                }},
            ]}},
        ],
    }


def _plan(page, *writes):
    return {"name": page["name"], "route": page["route"], "ui_surface": "declarative_page",
            "required_write_actions": [{"module_id": "content", "action_id": action} for action in writes]}


def _validate(page, planned):
    validate_required_page_writes(yaml.safe_dump(page), page["route"], planned_page_writes([planned]), "ui/pages/editor.yaml")


def test_required_controls_accept_submit_in_an_openable_modal_and_delete_on_a_selectable_table():
    page = _editor()
    _validate(page, _plan(page, "create_entry", "archive_entry"))


@pytest.mark.parametrize("reference", ["{selected_row.entry_id}", "archived-{ row.entry_id }", "{selected_rows.0.entry_id}"])
def test_header_cannot_supply_a_record_even_if_it_requests_selection(reference):
    page = _editor()
    action = {**_write("archive_entry"), "requires_selection": True, "payload": {"entry_id": reference}}
    page["sections"] = [{"id": "header", "primitive": "PageHeader", "config": {"title": "Entries", "actions": [action]}}]
    with pytest.raises(ValueError, match="missing reachable write controls for content/archive_entry"):
        _validate(page, _plan(page, "archive_entry"))
    # The renderer can submit an empty selection as a list without needing an individual record.
    action["payload"] = {"selection": "{selected_rows}", "count": "{selected_rows.length}"}
    _validate(page, _plan(page, "archive_entry"))


@pytest.mark.parametrize("source", ["header", "button", "unbound_table", "table_empty", "selectable_table"])
def test_a_modal_receives_only_rows_its_opener_can_supply(source):
    page = _editor()
    table, modal = page["sections"]
    opener = table["config"]["actions"][1]
    form = modal["config"]["children"][0]["config"]
    form["submit_action"] = {**_write("archive_entry"), "payload": [
        {"key": "entry_id", "value": "{selected_row.entry_id}"}, {"key": "title", "value": "{form.title}"},
    ]}
    if source == "header":
        page["sections"][0] = {"id": "header", "primitive": "PageHeader", "config": {"title": "Edit", "actions": [opener]}}
    elif source == "button":
        page["sections"][0] = {"id": "open", "primitive": "Button", "config": {"label": "Edit", "action": opener}}
    elif source == "table_empty":
        table["config"]["actions"] = []
        table["config"]["empty"] = {"title": "No entries", "action": opener}
    else:
        table["config"]["actions"] = [opener]  # A selectable toolbar may pass a row with or without requires_selection.
        if source == "unbound_table":
            table["config"].pop("api_endpoint")
    if source == "selectable_table":
        _validate(page, _plan(page, "archive_entry"))
    else:
        with pytest.raises(ValueError, match="missing reachable write controls for content/archive_entry"):
            _validate(page, _plan(page, "archive_entry"))


def test_table_selection_needs_a_source_and_a_multi_row_token_needs_multi_selection():
    page = _editor()
    table = page["sections"][0]["config"]
    endpoint = table.pop("api_endpoint")
    with pytest.raises(ValueError, match="missing reachable write controls for content/archive_entry"):
        _validate(page, _plan(page, "archive_entry"))
    table["api_endpoint"] = endpoint
    table["actions"][0]["payload"] = {"second": "{selected_rows.1.entry_id}"}
    with pytest.raises(ValueError, match="missing reachable write controls for content/archive_entry"):
        _validate(page, _plan(page, "archive_entry"))
    table["selection"] = "multi"
    _validate(page, _plan(page, "archive_entry"))
    # A server table discards its selection on page changes. A second row
    # can only supply the action's ID when both rows fit on one page.
    table.update(pagination_mode="server", pagination=True, data_key="entries", total_key="total", page_size=1)
    with pytest.raises(ValueError, match="missing reachable write controls for content/archive_entry"):
        _validate(page, _plan(page, "archive_entry"))
    table["page_size"] = 2
    _validate(page, _plan(page, "archive_entry"))

    table.update(pagination_mode="client", total_key=None, page_size=1)
    with pytest.raises(ValueError, match="missing reachable write controls for content/archive_entry"):
        _validate(page, _plan(page, "archive_entry"))
    table["columns"][0]["sortable"] = True
    _validate(page, _plan(page, "archive_entry"))
    table["actions"][0]["payload"] = {"fourth": "{selected_rows.3.entry_id}"}
    with pytest.raises(ValueError, match="missing reachable write controls for content/archive_entry"):
        _validate(page, _plan(page, "archive_entry"))


def test_modal_reached_again_from_a_table_can_receive_a_record_after_a_header_open():
    page = _editor()
    table, modal = page["sections"]
    opener = table["config"]["actions"][1]
    table["config"]["actions"] = [opener]
    modal["config"]["children"][0]["config"]["submit_action"] = {
        **_write("archive_entry"), "payload": {"entry_id": "{selected_row.entry_id}", "title": "{form.title}"},
    }
    header = {"id": "header", "primitive": "PageHeader", "config": {"title": "Entries", "actions": [deepcopy(opener)]}}
    page["sections"] = [header, modal, table]
    _validate(page, _plan(page, "archive_entry"))


def test_nested_table_does_not_borrow_its_openers_rows_and_can_supply_its_own():
    page = _editor()
    table, modal = page["sections"]
    inner = deepcopy(table)
    inner["id"] = "related"
    del inner["config"]["api_endpoint"]
    table["config"]["actions"].pop(0)
    inner["config"]["actions"] = [{
        **_write("archive_entry"), "requires_selection": True, "payload": {"entry_id": "{row.entry_id}"},
    }]
    modal["config"]["children"] = [inner]
    with pytest.raises(ValueError, match="missing reachable write controls for content/archive_entry"):
        _validate(page, _plan(page, "archive_entry"))
    inner["config"]["api_endpoint"] = "/api/modules/content/list_entries"
    _validate(page, _plan(page, "archive_entry"))


def test_catalog_item_callbacks_supply_the_clicked_item():
    page = _editor()
    config = {"plans": [{"plan_id": "plus", "label": "Plus"}],
              "plan_action": {**_write("choose_plan"), "payload": {"id": "{selected_row.plan_id}"}}}
    page["sections"] = [{"id": "plans", "primitive": "PricingCatalog", "config": config}]
    _validate(page, _plan(page, "choose_plan"))
    config["plans"] = []
    with pytest.raises(ValueError, match="missing reachable write controls for content/choose_plan"):
        _validate(page, _plan(page, "choose_plan"))


@pytest.mark.parametrize("case", ["malformed_yaml", "invalid_schema", "moved_route"])
def test_changed_snapshot_must_still_match_the_reviewed_page_identity_and_schema(case):
    page = _editor()
    expected = planned_page_writes([_plan(page, "archive_entry")])
    if case == "moved_route":
        page["route"] = "/other"
    elif case == "invalid_schema":
        page["sections"][0]["primitive"] = "InventedTable"
    content = "]" if case == "malformed_yaml" else yaml.safe_dump(page)
    with pytest.raises(ValueError, match=r"ui/pages/editor.yaml: "):
        validate_required_page_writes(content, "/editor", expected, "ui/pages/editor.yaml")


def test_only_legacy_declarative_rows_read_their_bound_design_and_only_once(monkeypatch):
    reviewed = {route: [{"module_id": "content", "action_id": "archive_entry"}]
                for route in ("/one", "/two", "/empty", "/custom")}
    context = {"design_docs_build_artifact": {"artifact_version_id": "reviewed-version"}}
    calls = []

    def resolve(value):
        calls.append(value)
        return reviewed

    monkeypatch.setattr(design_access_policy, "resolve_reviewed_page_writes", resolve)
    pages = [{"route": "/one"}, {"route": "/two"}, {"route": "/empty", "required_write_actions": []},
             {"route": "/custom", "ui_surface": "custom_react_page"}]
    assert planned_page_writes(pages, context) == {"/one": {"content/archive_entry"}, "/two": {"content/archive_entry"}}
    assert planned_page_writes(pages[2:], context) == {}
    assert calls == [context]


@pytest.mark.parametrize("case, missing", [
    ("closed_modal", "content/create_entry"),
    ("disabled_form", "content/create_entry"),
    ("selection_disabled", "content/archive_entry"),
    ("read_only", "content/archive_entry, content/create_entry"),
    ("read_source_spoof", "content/archive_entry"),
])
def test_missing_writes_name_the_route_and_reachable_controls_for_repair(case, missing):
    page = _editor()
    table = page["sections"][0]["config"]
    if case == "closed_modal":
        table["actions"].pop()
    elif case == "disabled_form":
        page["sections"][1]["config"]["children"][0]["config"]["disabled"] = True
    elif case == "selection_disabled":
        table["selection"] = "none"
    elif case == "read_only":
        table["actions"] = []
    else:
        table["actions"].pop(0)
        table["api_endpoint"] = "/api/modules/content/archive_entry"
    with pytest.raises(ValueError) as error:
        _validate(page, _plan(page, "create_entry", "archive_entry"))
    assert f"ui/pages/editor.yaml: route '/editor' is missing reachable write controls for {missing}." in str(error.value)
    assert "data_source" in str(error.value)


def test_required_writes_are_per_page_and_legacy_pages_have_no_implied_writes():
    page = _editor()
    other = {**deepcopy(page), "name": "secondary", "route": "/secondary"}
    page["sections"][0]["config"]["actions"].pop(0)
    legacy = {"route": "/editor", "primary_actions": ["archive_entry"], "primary_entities": ["ContentEntry"]}
    _validate(page, legacy)
    assert planned_page_writes([{**_plan(other, "archive_entry"), "ui_surface": "custom_react_page"}]) == {}
    pages = [_plan(page, "archive_entry"), _plan(other)]
    task = _task("both")
    task["owned_paths"] = ["ui/pages/secondary.yaml", "ui/pages/editor.yaml"]
    files = [{"filename": f"ui/pages/{item['name']}.yaml", "content": yaml.safe_dump(item)} for item in (other, page)]
    with pytest.raises(ValueError, match="ui/pages/editor.yaml: route '/editor'.*content/archive_entry"):
        _apply_planned_page_contracts(files, {"pages": pages, "build_tasks": [task]})


@pytest.mark.asyncio
async def test_detached_worker_cannot_erase_writes_from_its_parent_plan():
    page = _editor()
    page["sections"] = [{"id": "heading", "primitive": "PageHeader", "config": {"title": "Entries"}}]
    task = _task("editor")
    task["context_variables"] = {"app_build_plan": {"pages": [_plan(page)]}}
    original = _plan(page, "archive_entry")
    context = {"app_build_plan": {"pages": [original]}, "app_task_batch_items": [task]}

    class Worker:
        async def ask(self, message, **kwargs):
            return SimpleNamespace(body=json.dumps({"agent_message": "Prepared the page.", "manifest": None,
                                                    "pages": [page], "custom_route_bundle": None,
                                                    "theme_config_patch": None, "shell_config": None, "asset_manifest": None}))

    with pytest.raises(RuntimeError, match="missing reachable write controls for content/archive_entry"):
        await execute_task_batches_for_trigger(
            workflow_name="AppGenerator", trigger_agent="AppPlanAgent", batches_config=_batch_config(),
            agents={"WorkerAgent": Worker()}, context_variables=context, fresh_agents_per_task=False,
        )
    assert context["app_task_batch_status"] == "failed"
    assert "editor" not in context["app_task_batch_results"]

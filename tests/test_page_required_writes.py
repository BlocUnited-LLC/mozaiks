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

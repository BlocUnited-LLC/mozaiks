"""Determined page bindings are constructed and logged; only judgment gaps reject.

Every scenario runs the real authoring compiler through a real
ContextVariablesBridge, the same way the task worker supplies it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_validation import _wiring_repair_errors
from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import (
    _apply_planned_page_contracts,
)
from factory_app.workflows.AppGenerator.tools.validate_wiring import validate_wiring
from mozaiksai.core.runtime.app.page_schema import validate_page_schema
from mozaiksai.core.session.build_binding import RunBuildBinding
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.authority import build_context_authority_policy
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.context.schema import load_context_variables_config
from mozaiksai.core.workflow.generator_support.code_files import discard_pack_owned_outputs
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    pack_owned_output_paths,
)
from mozaiksai.core.workflow.generator_support.page_action_bindings import (
    reachable_page_action_keys,
)
from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    compile_authored_page_files,
    module_action_index_from_context,
    normalize_planned_page_content,
)
from mozaiksai.core.workflow.task_batches import (
    _normalize_owned_page_files_from_plan,
    _validation_feedback,
)
from tests.factory_context import factory_context

ROOT = Path(__file__).resolve().parents[1]
MOZAIKSPAY_PACK = ROOT / "factory_app" / "build_context" / "mozaikspay"


def _bridge(initial=None):
    config = load_context_variables_config(yaml.safe_load(
        (ROOT / "factory_app/workflows/AppGenerator/context_variables.yaml").read_text(encoding="utf-8"),
    ))
    policy = build_context_authority_policy(workflow_name="AppGenerator", definitions=config.definitions)
    bridge = ContextVariablesBridge(factory_context(initial), authority_policy=policy)
    bridge._bind_run(("AppGenerator", "page-binding-app", "9f1a2f0e-2f41-4d54-9b83-0a7f6d8c1a11"), policy)
    return bridge


def _string(required: bool = False) -> dict:
    return {"type": "string"} if required else {"type": ["string", "null"]}


def _module_yaml(extra_actions: list[dict] | None = None) -> str:
    record = {"type": "object", "properties": {
        "task_id": {"type": "string"}, "user_id": {"type": "string"}, "title": {"type": "string"},
        "description": {"type": ["string", "null"]}, "is_completed": {"type": "boolean"},
    }, "required": ["task_id", "user_id", "title"]}
    actions = [
        {"id": "create_task", "handler_method": "create_task", "api_surface": None, "permissions": [],
         "description": "Create a new task.",
         "input_schema": {"type": "object", "properties": {"title": {"type": "string"}, "description": {"type": "string"}},
                          "additionalProperties": False, "required": ["title"]},
         "output_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}},
        {"id": "update_task", "handler_method": "update_task", "api_surface": None, "permissions": [],
         "description": "Update an existing task.", "entitlement_gate": "task.edit",
         "input_schema": {"type": "object", "properties": {
             "task_id": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"}},
             "additionalProperties": False, "required": ["task_id"]},
         "output_schema": {"type": "object", "properties": {"success": {"type": "boolean"}}, "required": ["success"]}},
        {"id": "delete_task", "handler_method": "delete_task", "api_surface": None, "permissions": [],
         "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}},
                          "additionalProperties": False, "required": ["task_id"]},
         "output_schema": {"type": "object", "properties": {"success": {"type": "boolean"}}, "required": ["success"]}},
        {"id": "summarize_tasks", "handler_method": "summarize_tasks", "api_surface": None, "permissions": [],
         "entitlement_gate": "dashboard.view",
         "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
         "output_schema": {"type": "object", "properties": {
             "tasks_completed": {"type": "integer"}, "pending_tasks": {"type": "integer"}},
             "required": ["tasks_completed", "pending_tasks"]}},
        {"id": "get_tasks", "handler_method": "get_tasks", "api_surface": None, "permissions": [],
         "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
         "output_schema": {"type": "object", "properties": {"item": record}, "required": ["item"]}},
        {"id": "list_tasks", "handler_method": "list_tasks", "api_surface": None, "permissions": [],
         "input_schema": {"type": "object", "properties": {"page": {"type": "integer"}}},
         "output_schema": {"type": "object", "properties": {
             "items": {"type": "array", "items": record}, "total": {"type": "integer"}},
             "required": ["items", "total"]}},
        *(extra_actions or []),
    ]
    return yaml.safe_dump({
        "schema_version": "mozaiks.module.v1",
        "module": {"id": "task_management", "display_name": "Task Management", "version": "1.0.0", "owner": "app",
                   "visibility": "private", "handler": "backend.handler:TaskManagementModule"},
        "actions": actions,
    }, sort_keys=False)


def _data_contract() -> dict:
    field = lambda name, kind, required=True, nullable=False: {  # noqa: E731
        "name": name, "type": kind, "required": required, "default": None, "enum": None, "nullable": nullable,
    }
    return {"version": "1", "app_id": "page-binding-app", "surfaces": [{
        "surface_id": "task_management", "surface_kind": "module", "collections": [{
            "name": "tasks", "scope": "app",
            "ownership": {"surface_id": "task_management", "surface_kind": "module"},
            "fields": [field("task_id", "string"), field("user_id", "string"), field("title", "string"),
                       field("description", "string", required=False, nullable=True),
                       field("is_completed", "boolean", required=False)],
            "indexes": [{"keys": [{"field": "task_id", "order": 1}], "unique": True, "sparse": None, "name": None}],
            "search_by": "task_id", "lifecycle": {"write_mode": "module_action", "migration_policy": "additive_only"},
            "tenancy": "per_user", "owner_field": "user_id", "entity": "Task",
        }],
    }], "shared_collections": [], "policies": []}


def _surface_map(owned_mutations: list[str] | None = None) -> dict:
    return {"surfaces": [{
        "surface_id": "task_management", "label": "Task Management", "surface_kind": "module", "owner": "app",
        "source_capability_packs": [], "primary_entities": ["Task"],
        "owned_pages": ["Dashboard", "Tasks"],
        "owned_mutations": owned_mutations or ["create_task", "update_task", "delete_task"],
        "custom_reads": ["summarize_tasks"], "events_emitted": [], "workflow_triggers": [], "integrations": [],
        "notes": "",
    }]}


def _context(*, module_yaml: str | None = None, owned_mutations: list[str] | None = None, **extra):
    return _bridge({
        "generated_files": {"modules/task_management/module.yaml": module_yaml or _module_yaml()},
        "data_contract": _data_contract(),
        "design_surface_map": _surface_map(owned_mutations),
        "app_build_plan": {"pages": [{"name": "Dashboard", "route": "/dashboard"}, {"name": "Tasks", "route": "/tasks"}],
                           "capability_packs": []},
        **extra,
    })


def _table(actions: list[dict] | None = None, *, selection: str | None = "single") -> dict:
    config = {
        "columns": [{"key": "task_id", "label": "ID"}, {"key": "title", "label": "Title"}],
        "data_source": {"module_id": "task_management", "action_id": "list_tasks"},
        "data_key": "items", "pagination": False, "search": True,
    }
    if selection is not None:
        config["selection"] = selection
    if actions is not None:
        config["actions"] = actions
    return {"id": "task-table", "primitive": "ResourceTable", "title": "Tasks", "config": config}


def _dashboard(items: list[dict], table_actions: list[dict] | None = None) -> dict:
    return {
        "schema_version": "mozaiks.app_page.v1", "name": "dashboard", "route": "/dashboard", "title": "Dashboard",
        "page_type": "analytics_dashboard", "layout": "grid", "shell_mode": "standard",
        "sections": [
            {"id": "kpi", "primitive": "SummaryStrip", "title": "KPIs", "config": {
                "data_source": {"module_id": "task_management", "action_id": "summarize_tasks"}, "items": items,
            }},
            _table(table_actions),
        ],
    }


def _workflow_actions() -> list[dict]:
    return [
        {"id": "create_task", "label": "Create Task", "variant": "primary", "requires_selection": False,
         "closes_modal": True, "action_type": "workflow", "workflow_id": "create_task_workflow", "context_variables": None},
        {"id": "edit_task", "label": "Edit Task", "variant": "secondary", "requires_selection": True,
         "closes_modal": True, "action_type": "workflow", "workflow_id": "edit_task_workflow", "context_variables": None},
    ]


def _compile(files: dict[str, str], context, caplog=None) -> dict[str, dict]:
    if caplog is not None:
        caplog.set_level(logging.INFO, logger="mozaiksai.core.workflow.generator_support.page_binding_construction")
    compiled = compile_authored_page_files(files, payload={"code_files": []}, context=context)
    return {path: yaml.safe_load(content) for path, content in compiled.items()}


def _section(page: dict, section_id: str) -> dict:
    return next(section for section in page["sections"] if section["id"] == section_id)


def test_metric_id_that_names_a_returned_field_becomes_the_value_key_and_undeclared_keys_clear(caplog):
    page = _dashboard([
        {"id": "tasks_completed", "label": "Tasks Completed", "value_key": "completed_tasks",
         "detail_key": "completed_detail", "trend_key": "completed_trend"},
        {"id": "pending_tasks", "label": "Pending", "value_key": "pending_tasks", "trend_key": "pending_trend"},
    ])
    compiled = _compile({"ui/pages/dashboard.yaml": yaml.safe_dump(page)}, _context(), caplog)
    items = _section(compiled["ui/pages/dashboard.yaml"], "kpi")["config"]["items"]
    assert [(item["value_key"], item.get("detail_key"), item.get("trend_key")) for item in items] == [
        ("tasks_completed", None, None), ("pending_tasks", None, None),
    ]
    constructed = [record.getMessage() for record in caplog.records if "constructed" in record.getMessage()]
    assert any("'completed_tasks' is not returned" in line and "'tasks_completed'" in line for line in constructed)
    assert any("trend_key: 'pending_trend'" in line and "cleared" in line for line in constructed)
    validate_page_schema(compiled["ui/pages/dashboard.yaml"])


def test_metric_whose_id_names_nothing_is_a_judgment_gap_with_the_valid_fields():
    page = _dashboard([{"id": "done_count", "label": "Done", "value_key": "completed_tasks"}])
    with pytest.raises(ValueError) as error:
        _compile({"ui/pages/dashboard.yaml": yaml.safe_dump(page)}, _context())
    message = str(error.value)
    assert message.startswith("ui/pages/dashboard.yaml:")
    assert "'completed_tasks' is not declared" in message
    assert "Valid fields: pending_tasks, tasks_completed" in message


def test_invented_create_and_edit_workflow_buttons_become_modal_forms_for_the_collection(caplog):
    page = _dashboard([{"id": "tasks_completed", "label": "Done", "value_key": "tasks_completed"}], _workflow_actions())
    compiled = _compile({"ui/pages/dashboard.yaml": yaml.safe_dump(page)}, _context(), caplog)
    dashboard = compiled["ui/pages/dashboard.yaml"]
    validate_page_schema(dashboard)
    actions = _section(dashboard, "task-table")["config"]["actions"]
    # Opener ids are constructed so they cannot collide with an authored action
    # (the recorded run reused `create_task` for the empty-state action too).
    # The shared canonical delete has no authored button at all, so it gets its own.
    assert [(a["id"], a["label"], a["action_type"], a["event_type"], a["payload"]["modal_id"], a["requires_selection"]) for a in actions] == [
        ("open-create_task", "Create Task", "event", "ui.modal.open", "create_task-modal", False),
        ("open-update_task", "Edit Task", "event", "ui.modal.open", "update_task-modal", True),
        ("open-delete_task", "Delete", "event", "ui.modal.open", "delete_task-modal", True),
    ]
    assert all("workflow_id" not in action for action in actions)
    edit_form = _section(dashboard, "update_task-modal")["config"]["children"][0]["config"]
    assert edit_form["initial_values_key"] == "selected_row"
    assert [(field["name"], field["type"], field["required"]) for field in edit_form["fields"]] == [
        ("title", "text", False), ("description", "text", False),
    ]
    assert edit_form["submit_action"]["href"] == "/api/modules/task_management/update_task"
    assert edit_form["submit_action"]["payload"] == [
        {"key": "task_id", "value": "{selected_row.task_id}"},
        {"key": "title", "value": "{form.title}"}, {"key": "description", "value": "{form.description}"},
    ]
    create_form = _section(dashboard, "create_task-modal")["config"]["children"][0]["config"]
    assert "initial_values_key" not in create_form
    assert [(field["name"], field["required"]) for field in create_form["fields"]] == [("title", True), ("description", False)]
    assert create_form["submit_action"]["href"] == "/api/modules/task_management/create_task"
    assert {"task_management/create_task", "task_management/update_task"} <= reachable_page_action_keys([dashboard])
    lines = [record.getMessage() for record in caplog.records if "constructed" in record.getMessage()]
    assert any("'create_task_workflow' names no generated workflow" in line for line in lines)
    assert any("'edit_task_workflow' names no generated workflow" in line for line in lines)


def test_invented_workflow_stays_rejected_when_two_update_shaped_actions_exist():
    # complete_task writes a declared record field by identifier, so it is a
    # second update-shaped candidate and the choice between them is the author's.
    complete = {"id": "complete_task", "handler_method": "complete_task", "api_surface": None, "permissions": [],
                "input_schema": {"type": "object", "properties": {
                    "task_id": {"type": "string"}, "is_completed": {"type": "boolean"}}, "required": ["task_id"]},
                "output_schema": {"type": "object", "properties": {"success": {"type": "boolean"}}}}
    context = _context(module_yaml=_module_yaml([complete]),
                       owned_mutations=["create_task", "update_task", "delete_task", "complete_task"])
    page = _dashboard([{"id": "tasks_completed", "label": "Done", "value_key": "tasks_completed"}], _workflow_actions())
    with pytest.raises(ValueError) as error:
        _compile({"ui/pages/dashboard.yaml": yaml.safe_dump(page)}, context)
    message = str(error.value)
    assert "workflow_id 'edit_task_workflow' is not present" in message
    assert "create_task_workflow" not in message  # the single create action was constructed


def test_workflow_button_is_kept_when_the_bundle_declares_that_workflow():
    page = _dashboard([{"id": "tasks_completed", "label": "Done", "value_key": "tasks_completed"}], [
        {"id": "triage", "label": "Triage", "action_type": "workflow", "workflow_id": "Triage"},
    ])
    files = {"ui/pages/dashboard.yaml": yaml.safe_dump(page), "workflows/Triage/orchestrator.yaml": "workflow_name: Triage\n"}
    compiled = _compile(files, _context())
    actions = _section(compiled["ui/pages/dashboard.yaml"], "task-table")["config"]["actions"]
    assert actions[0]["action_type"] == "workflow" and actions[0]["workflow_id"] == "Triage"
    # The real workflow button is not a create/edit replacement candidate; only
    # the canonical writes' missing entry points are constructed.
    assert [action["id"] for action in actions] == ["triage", "open-update_task", "open-create_task", "open-delete_task"]
    assert [s["id"] for s in compiled["ui/pages/dashboard.yaml"]["sections"] if s["primitive"] == "Modal"] == [
        "update_task-modal", "create_task-modal", "delete_task-modal",
    ]


def _tasks_page(actions: list[dict] | None = None, *, selection: str | None = "single") -> dict:
    return {
        "schema_version": "mozaiks.app_page.v1", "name": "tasks", "route": "/tasks", "title": "Tasks",
        "page_type": "record_list", "layout": "full-width", "shell_mode": "workspace",
        "sections": [_table(actions, selection=selection)],
    }


@pytest.mark.parametrize("selection", [None, "none", "single"])
def test_gated_update_action_on_a_listed_collection_gets_an_edit_entry_point(selection, caplog):
    files = {"ui/pages/tasks.yaml": yaml.safe_dump(_tasks_page(selection=selection))}
    compiled = _compile(files, _context(), caplog)
    tasks = compiled["ui/pages/tasks.yaml"]
    validate_page_schema(tasks)
    table = _section(tasks, "task-table")["config"]
    assert table["selection"] == "single"
    assert table["actions"][0] == {
        "id": "open-update_task", "label": "Edit", "variant": "secondary", "action_type": "event",
        "event_type": "ui.modal.open", "payload": {"modal_id": "update_task-modal"},
        "requires_selection": True, "closes_modal": False,
    }
    assert [action["id"] for action in table["actions"]] == ["open-update_task", "open-create_task", "open-delete_task"]
    assert "task_management/update_task" in reachable_page_action_keys([tasks])
    lines = [record.getMessage() for record in caplog.records if "constructed" in record.getMessage()]
    assert any("gated action task_management/update_task (task.edit) has no page entry point" in line for line in lines)
    # Assembly re-normalizes the compiled page from the same approved inputs;
    # construction is idempotent, so the emitted page is byte-identical.
    context = _context()
    again = normalize_planned_page_content(
        yaml.safe_dump(tasks, sort_keys=False, allow_unicode=True), path="ui/pages/tasks.yaml",
        modules=module_action_index_from_context(context),
        data_contract=detach(context.get("data_contract")), design_surface_map=detach(context.get("design_surface_map")),
    )
    assert yaml.safe_load(again) == tasks


def test_no_edit_entry_point_is_added_when_the_page_already_reaches_the_gated_action():
    page = _tasks_page([{"id": "edit", "label": "Edit", "action_type": "event", "event_type": "ui.modal.open",
                         "requires_selection": True, "payload": {"modal_id": "edit-task"}}])
    page["sections"].append({"id": "edit-task", "primitive": "Modal", "config": {"children": [
        {"id": "edit-form", "primitive": "Form", "config": {
            "initial_values_key": "selected_row", "fields": [{"name": "title", "label": "Title", "type": "text"}],
            "submit_action": {"label": "Save", "action_type": "submit",
                              "data_source": {"module_id": "task_management", "action_id": "update_task"}},
        }},
    ]}})
    compiled = _compile({"ui/pages/tasks.yaml": yaml.safe_dump(page)}, _context())
    tasks = compiled["ui/pages/tasks.yaml"]
    # No second edit modal or opener; only the shared create/delete entry points are added.
    assert [s["id"] for s in tasks["sections"]] == ["task-table", "edit-task", "create_task-modal", "delete_task-modal"]
    assert [a["id"] for a in _section(tasks, "task-table")["config"]["actions"]] == [
        "edit", "open-create_task", "open-delete_task",
    ]


def test_every_page_in_the_task_is_reported_in_one_rejection():
    dashboard = _dashboard([{"id": "done_count", "label": "Done", "value_key": "completed_tasks"}])
    tasks = _tasks_page()
    _section(tasks, "task-table")["config"]["columns"].append({"key": "priority", "label": "Priority"})
    with pytest.raises(ValueError) as error:
        _compile({"ui/pages/dashboard.yaml": yaml.safe_dump(dashboard), "ui/pages/tasks.yaml": yaml.safe_dump(tasks)},
                 _context())
    lines = str(error.value).split("\n")
    assert len(lines) == 2
    assert lines[0].startswith("ui/pages/dashboard.yaml:") and "Valid fields: pending_tasks, tasks_completed" in lines[0]
    assert lines[1].startswith("ui/pages/tasks.yaml:") and "'priority' is not a declared row field" in lines[1]


def test_task_worker_lane_reports_every_owned_page_in_one_rejection():
    dashboard = _dashboard([{"id": "tasks_completed", "label": "Done", "value_key": "tasks_completed"}])
    dashboard["route"] = "/home"
    tasks = _tasks_page()
    tasks["route"] = "/work"
    context = _context()
    # The runner hands a worker a detached snapshot of the bridge, never the proxy.
    snapshot = {key: detach(context.get(key)) for key in ("generated_files", "data_contract", "design_surface_map", "app_build_plan")}
    with pytest.raises(ValueError) as error:
        _normalize_owned_page_files_from_plan(
            [{"filename": "ui/pages/dashboard.yaml", "content": yaml.safe_dump(dashboard)},
             {"filename": "ui/pages/tasks.yaml", "content": yaml.safe_dump(tasks)}],
            task={"owned_paths": ["ui/pages/dashboard.yaml", "ui/pages/tasks.yaml"]}, base_context=snapshot,
        )
    lines = str(error.value).split("\n")
    assert [line.split(":")[0] for line in lines] == ["ui/pages/dashboard.yaml", "ui/pages/tasks.yaml"]
    assert all("route must preserve approved" in line for line in lines)


def _placeholder_billing_files() -> dict[str, str]:
    module = yaml.safe_dump({"schema_version": "mozaiks.module.v1", "module": {"id": "billing_portal"}, "actions": [
        {"id": "get_subscription_status", "api_surface": "public_readonly", "permissions": [],
         "output_schema": {"type": "object", "properties": {"subscription_status": {"type": "string"}}}},
    ]})
    page = {
        "schema_version": "mozaiks.app_page.v1", "name": "billing", "route": "/billing", "title": "Billing",
        "page_type": "record_list", "layout": "full-width", "shell_mode": "standard",
        "sections": [{"id": "billing-info", "primitive": "ResourceTable", "title": "Billing", "config": {
            "columns": [{"key": "billing_id", "label": "ID"}],
            "data_source": {"module_id": "billing_portal", "action_id": "get_subscription_status"},
            "data_key": "billing_info", "selection": "single",
        }}],
    }
    return {"modules/billing_portal/module.yaml": module, "ui/pages/billing.yaml": yaml.safe_dump(page)}


def test_a_worker_copy_of_a_pack_template_page_is_discarded_not_validated():
    """Live chat fdfa818e: the page worker's pricing.yaml failed extra_forbidden on a page the pack ships."""
    packs = [{"id": "mozaikspay", "capability_source": "managed_capability", "status": "active",
              "pack_source_path": str(MOZAIKSPAY_PACK)}]
    files = _placeholder_billing_files()
    context = _bridge({
        "capability_packs": packs,
        "app_build_plan": {"pages": [{"name": "Billing", "route": "/billing"}], "capability_packs": []},
    })
    pack_paths = pack_owned_output_paths(context)
    assert {"ui/pages/billing.yaml", "ui/pages/usage.yaml", "ui/pages/pricing.yaml",
            "modules/billing_portal/module.yaml"} <= pack_paths
    placeholder = yaml.safe_load(files["ui/pages/billing.yaml"])
    payload = {"manifest": None, "pages": [placeholder, {"name": "Tasks", "route": "/tasks"}], "code_files": [
        {"filename": "modules/billing_portal/module.yaml", "content": files["modules/billing_portal/module.yaml"]},
    ], "deleted_files": ["ui/pages/usage.yaml"]}
    kept, discarded = discard_pack_owned_outputs(payload, pack_paths)
    assert discarded == ["modules/billing_portal/module.yaml", "ui/pages/billing.yaml", "ui/pages/usage.yaml"]
    assert kept["pages"] == [{"name": "Tasks", "route": "/tasks"}] and kept["code_files"] == []
    assert kept["deleted_files"] == []
    assert payload["pages"][0] is placeholder  # the worker's payload itself is not mutated
    # Without the pack the same page is authored work, and its binding error is real.
    with pytest.raises(ValueError, match="'billing_info' must select a declared array"):
        _compile({"ui/pages/billing.yaml": files["ui/pages/billing.yaml"]}, _bridge({
            "generated_files": {"modules/billing_portal/module.yaml": files["modules/billing_portal/module.yaml"]},
            "app_build_plan": {"pages": [{"name": "Billing", "route": "/billing"}], "capability_packs": []},
        }))


def test_unreachable_gated_action_is_attributed_to_the_authored_page_that_reads_its_module():
    packs = [{"id": "mozaikspay", "capability_source": "managed_capability", "status": "active",
              "pack_source_path": str(MOZAIKSPAY_PACK)}]
    files = {
        "ui/route_manifest.json": "{}",
        "ui/pages/billing.yaml": yaml.safe_dump({"name": "billing", "sections": [{"id": "s", "primitive": "SummaryStrip", "config": {
            "api_endpoint": "/api/modules/task_management/summarize_tasks", "items": []}}]}),
        "ui/pages/dashboard.yaml": yaml.safe_dump({"name": "dashboard", "sections": []}),
        "ui/pages/tasks.yaml": yaml.safe_dump({"name": "tasks", "sections": [{"id": "t", "primitive": "ResourceTable", "config": {
            "api_endpoint": "/api/modules/task_management/list_tasks", "columns": ["title"]}}]}),
        "modules/task_management/module.yaml": _module_yaml(),
    }
    wiring = {"passed": False, "failed_tests": [{
        "test": "wiring_unreachable_gated_action", "action": "task_management/update_task",
        "error": "Gated user-facing action 'task_management/update_task' (task.edit) has no reachable page binding.",
        "fix_suggestion": "Bind the action to a page read or typed submit/delete action.",
    }]}
    errors = _wiring_repair_errors(wiring, files, {"capability_packs": packs})
    assert len(errors) == 1
    # billing.yaml reads the module but is template-owned; tasks.yaml is the authored reader.
    assert errors[0].startswith("ui/pages/tasks.yaml: Gated user-facing action")
    assert "route_manifest" not in errors[0]
    without_reader = {path: content for path, content in files.items() if path != "ui/pages/tasks.yaml"}
    assert _wiring_repair_errors(wiring, without_reader, {"capability_packs": packs})[0].startswith("ui/pages/dashboard.yaml:")


def test_constructed_modal_form_survives_the_runtime_page_contract():
    page = _tasks_page()
    compiled = _compile({"ui/pages/tasks.yaml": yaml.safe_dump(page)}, _context())
    validated = validate_page_schema(deepcopy(compiled["ui/pages/tasks.yaml"]))
    modal = next(section for section in validated.sections if section.primitive == "Modal")
    form = modal.config["children"][0]["config"]
    assert form["submit_action"]["href"] == "/api/modules/task_management/update_task"


def test_assembly_re_derives_the_constructions_from_the_raw_typed_page():
    """Typed page output is re-materialized at assembly, so construction runs there too."""
    page = _dashboard([{"id": "tasks_completed", "label": "Done", "value_key": "completed_tasks",
                        "trend_key": "completed_trend"}], _workflow_actions())
    context = _context()
    files = [
        {"filename": "ui/pages/dashboard.yaml", "content": yaml.safe_dump(page)},
        {"filename": "modules/task_management/module.yaml", "content": _module_yaml()},
    ]
    plan = {"pages": [{"name": "Dashboard", "route": "/dashboard"}], "build_tasks": [
        {"task_id": "page_bundle", "task_type": "page_bundle", "owned_paths": ["ui/pages/dashboard.yaml"]},
    ]}
    assembled = {f["filename"]: f["content"] for f in _apply_planned_page_contracts(files, plan, context_variables=context)}
    dashboard = yaml.safe_load(assembled["ui/pages/dashboard.yaml"])
    validate_page_schema(dashboard)
    assert _section(dashboard, "kpi")["config"]["items"][0] == {
        "id": "tasks_completed", "label": "Done", "value_key": "tasks_completed", "trend_key": None,
    }
    assert [a["id"] for a in _section(dashboard, "task-table")["config"]["actions"]] == [
        "open-create_task", "open-update_task", "open-delete_task",
    ]
    assert {"task_management/create_task", "task_management/update_task"} <= reachable_page_action_keys([dashboard])


def test_an_authored_modal_with_the_constructed_id_is_not_reused_and_no_opener_is_duplicated(caplog):
    page = _tasks_page()
    page["sections"].append({"id": "update_task-modal", "primitive": "Modal", "config": {"children": [
        {"id": "delete-form", "primitive": "Form", "config": {
            "initial_values_key": "selected_row", "fields": [{"name": "title", "label": "Title", "type": "text"}],
            "submit_action": {"label": "Delete", "action_type": "submit",
                              "data_source": {"module_id": "task_management", "action_id": "delete_task"}},
        }},
    ]}})
    caplog.set_level(logging.INFO, logger="mozaiksai.core.workflow.generator_support.page_binding_construction")
    compiled = _compile({"ui/pages/tasks.yaml": yaml.safe_dump(page)}, _context())
    tasks = compiled["ui/pages/tasks.yaml"]
    # The edit entry point is refused; the shared create/delete still get theirs.
    assert [a["id"] for a in _section(tasks, "task-table")["config"]["actions"]] == ["open-create_task", "open-delete_task"]
    assert [s["id"] for s in tasks["sections"]] == ["task-table", "update_task-modal", "create_task-modal", "delete_task-modal"]
    assert any("not constructed" in r.getMessage() and "is not a Modal form submitting" in r.getMessage() for r in caplog.records)
    # A second pass over the same page adds nothing either.
    context = _context()
    again = normalize_planned_page_content(
        yaml.safe_dump(tasks, sort_keys=False), path="ui/pages/tasks.yaml",
        modules=module_action_index_from_context(context),
        data_contract=detach(context.get("data_contract")), design_surface_map=detach(context.get("design_surface_map")),
    )
    assert yaml.safe_load(again) == tasks


def test_an_existing_opener_is_not_duplicated_when_only_the_selection_was_missing():
    page = _tasks_page([{"id": "edit", "label": "Edit", "action_type": "event", "event_type": "ui.modal.open",
                         "requires_selection": True, "payload": {"modal_id": "update_task-modal"}}], selection="none")
    compiled = _compile({"ui/pages/tasks.yaml": yaml.safe_dump(page)}, _context())
    table = _section(compiled["ui/pages/tasks.yaml"], "task-table")["config"]
    assert [a["id"] for a in table["actions"]] == ["edit", "open-create_task", "open-delete_task"]
    assert table["selection"] == "single"
    assert [s["id"] for s in compiled["ui/pages/tasks.yaml"]["sections"]] == [
        "task-table", "update_task-modal", "create_task-modal", "delete_task-modal",
    ]


def test_an_invented_edit_workflow_in_the_empty_state_is_left_for_the_author():
    page = _dashboard([{"id": "tasks_completed", "label": "Done", "value_key": "tasks_completed"}])
    _section(page, "task-table")["config"]["empty"] = {"title": "Nothing", "action": {
        "id": "edit_task", "label": "Edit", "requires_selection": True, "action_type": "workflow",
        "workflow_id": "edit_task_workflow",
    }}
    with pytest.raises(ValueError, match="workflow_id 'edit_task_workflow' is not present"):
        _compile({"ui/pages/dashboard.yaml": yaml.safe_dump(page)}, _context())


def test_a_mutation_taking_undeclared_fields_is_not_a_record_write():
    reminder = {"id": "send_reminder", "handler_method": "send_reminder", "api_surface": None, "permissions": [],
                "input_schema": {"type": "object", "properties": {"email": {"type": "string"}}, "required": ["email"]},
                "output_schema": {"type": "object", "properties": {"success": {"type": "boolean"}}}}
    context = _context(module_yaml=_module_yaml([reminder]),
                       owned_mutations=["send_reminder", "update_task", "delete_task"])
    page = _dashboard([{"id": "tasks_completed", "label": "Done", "value_key": "tasks_completed"}], _workflow_actions()[:1])
    # The collection is module-written, so its canonical create_task is an approved
    # candidate even though the surface map omits it; send_reminder never is.
    compiled = _compile({"ui/pages/dashboard.yaml": yaml.safe_dump(page)}, context)
    dashboard = compiled["ui/pages/dashboard.yaml"]
    actions = _section(dashboard, "task-table")["config"]["actions"]
    assert ("open-create_task", "create_task-modal") in [(a["id"], a["payload"]["modal_id"]) for a in actions]
    modals = {s["id"] for s in dashboard["sections"] if s["primitive"] == "Modal"}
    assert "create_task-modal" in modals and not any("send_reminder" in modal for modal in modals)
    assert "task_management/send_reminder" not in reachable_page_action_keys([dashboard])


def test_every_unresolved_reference_on_a_page_is_reported_together():
    page = _dashboard([{"id": "tasks_completed", "label": "Done", "value_key": "completed_tasks"}])
    page["sections"][0]["config"]["data_source"] = {"module_id": "task_management", "action_id": "count_everything"}
    page["sections"][1]["config"]["data_source"] = {"module_id": "task_management", "action_id": "list_everything"}
    with pytest.raises(ValueError) as error:
        _compile({"ui/pages/dashboard.yaml": yaml.safe_dump(page)}, _context())
    message = str(error.value)
    assert "unknown module/action 'task_management/count_everything'" in message
    assert "unknown module/action 'task_management/list_everything'" in message


def test_an_authored_page_binds_a_facade_action_against_the_pack_template_contract():
    """No task authors the facade module, so pages compile against the template assembly applies."""
    packs = [{"id": "mozaikspay", "capability_source": "managed_capability", "status": "active",
              "pack_source_path": str(MOZAIKSPAY_PACK)}]
    page = {
        "schema_version": "mozaiks.app_page.v1", "name": "dashboard", "route": "/dashboard", "title": "Dashboard",
        "page_type": "analytics_dashboard", "layout": "full-width", "shell_mode": "standard",
        "sections": [{"id": "usage", "primitive": "SummaryStrip", "title": "Usage", "config": {
            "data_source": {"module_id": "billing_portal", "action_id": "get_token_status"},
            "items": [{"label": "Wallets", "value_key": "token_wallets"}],
        }}],
    }
    context = _bridge({
        "capability_packs": packs,
        "app_build_plan": {"pages": [{"name": "Dashboard", "route": "/dashboard"}], "capability_packs": []},
    })
    compiled = _compile({"ui/pages/dashboard.yaml": yaml.safe_dump(page)}, context)
    assert compiled["ui/pages/dashboard.yaml"]["sections"][0]["config"]["api_endpoint"] == "/api/modules/billing_portal/get_token_status"
    # A field the template contract does not return is still the author's binding error.
    page["sections"][0]["config"]["items"][0]["value_key"] = "wallet_count"
    with pytest.raises(ValueError, match="'wallet_count' is not declared in 'billing_portal/get_token_status'"):
        _compile({"ui/pages/dashboard.yaml": yaml.safe_dump(page)}, context)


def test_packs_selected_by_id_resolve_through_the_projected_operator_contracts():
    contract = yaml.safe_load((MOZAIKSPAY_PACK / "contract.yaml").read_text(encoding="utf-8"))
    context = _bridge({
        "app_build_plan": {"pages": [], "capability_packs": [{"capability_pack_id": "mozaikspay"}]},
        "operator_contracts": [{**contract, "contract_id": "mozaikspay"}],
    })
    assert {"ui/pages/billing.yaml", "ui/pages/usage.yaml", "ui/pages/pricing.yaml"} <= pack_owned_output_paths(context)
    # Facade page routes alone do not make a page pack-owned.
    assert pack_owned_output_paths(_bridge({"operator_contracts": [{
        "contract_id": "cloud", "required_outputs": [{"path": "services/integrations/cloud_client.py"}],
        "facades": [{"module_id": "cloud", "pages": [{"name": "Deployments", "route": "/deployments"}]}],
    }], "capability_packs": [{"id": "cloud"}]})) == frozenset({"services/integrations/cloud_client.py"})


def test_the_validation_feedback_carries_a_whole_multi_page_rejection():
    error = "\n".join(f"ui/pages/page{index}.yaml: Page bindings do not match declared contracts: " + "x" * 400
                      for index in range(20))
    assert len(error) > 4000
    feedback = _validation_feedback(error, None)
    assert error in feedback
    assert "[REJECTED TASK OUTPUT]" not in feedback
    assert "[REJECTED TASK OUTPUT]" in _validation_feedback(error, "{}")


def _contract_without(*keys: str) -> dict:
    contract = _data_contract()
    collection = contract["surfaces"][0]["collections"][0]
    for key in keys:
        collection[key] = None if key == "search_by" else []
    return contract


@pytest.mark.parametrize("stripped", [("search_by",), ("search_by", "indexes")])
def test_constructions_survive_a_collection_without_a_search_key(stripped, caplog):
    """The identity comes from the unique index, else from the canonical update write's required input."""
    context = _bridge({
        "generated_files": {"modules/task_management/module.yaml": _module_yaml()},
        "data_contract": _contract_without(*stripped), "design_surface_map": _surface_map(),
        "app_build_plan": {"pages": [{"name": "Dashboard", "route": "/dashboard"}], "capability_packs": []},
    })
    page = _dashboard([{"id": "tasks_completed", "label": "Done", "value_key": "tasks_completed"}], _workflow_actions())
    compiled = _compile({"ui/pages/dashboard.yaml": yaml.safe_dump(page)}, context, caplog)
    dashboard = compiled["ui/pages/dashboard.yaml"]
    assert [a["id"] for a in _section(dashboard, "task-table")["config"]["actions"]] == [
        "open-create_task", "open-update_task", "open-delete_task",
    ]
    edit_form = _section(dashboard, "update_task-modal")["config"]["children"][0]["config"]
    assert edit_form["submit_action"]["payload"][0] == {"key": "task_id", "value": "{selected_row.task_id}"}
    assert {"task_management/create_task", "task_management/update_task"} <= reachable_page_action_keys([dashboard])
    assert not [r for r in caplog.records if "not constructed" in r.getMessage()]


def test_a_collection_with_no_identity_at_all_still_gets_the_create_and_logs_the_refusals(caplog):
    """No search_by, no unique index and a non-canonical update: only the canonical create is determined."""
    renamed = yaml.safe_load(_module_yaml())
    for action in renamed["actions"]:
        if action["id"] == "update_task":
            action["id"] = action["handler_method"] = "revise_task"
    context = _bridge({
        "generated_files": {"modules/task_management/module.yaml": yaml.safe_dump(renamed, sort_keys=False)},
        "data_contract": _contract_without("search_by", "indexes"),
        "design_surface_map": _surface_map(["create_task", "revise_task", "delete_task"]),
        "app_build_plan": {"pages": [{"name": "Dashboard", "route": "/dashboard"}], "capability_packs": []},
    })
    caplog.set_level(logging.INFO, logger="mozaiksai.core.workflow.generator_support.page_binding_construction")
    page = _dashboard([{"id": "tasks_completed", "label": "Done", "value_key": "tasks_completed"}], _workflow_actions()[:1])
    compiled = _compile({"ui/pages/dashboard.yaml": yaml.safe_dump(page)}, context)
    dashboard = compiled["ui/pages/dashboard.yaml"]
    assert [a["id"] for a in _section(dashboard, "task-table")["config"]["actions"]] == ["open-create_task"]
    assert "task_management/create_task" in reachable_page_action_keys([dashboard])
    refused = [r.getMessage() for r in caplog.records if "not constructed" in r.getMessage()]
    assert any("gated task_management/revise_task" in line and "declares no record identity" in line for line in refused)
    # The edit button, which needs an identifier, is a judgment gap named in the rejection.
    page = _dashboard([{"id": "tasks_completed", "label": "Done", "value_key": "tasks_completed"}], _workflow_actions())
    with pytest.raises(ValueError, match="workflow_id 'edit_task_workflow' is not present"):
        _compile({"ui/pages/dashboard.yaml": yaml.safe_dump(page)}, context)
    assert any("'edit_task_workflow' not replaced" in line and "declares no record identity" in line
               for line in (r.getMessage() for r in caplog.records))


def test_pack_owned_outputs_are_template_outputs_and_genesis_workspace_templates():
    context = _bridge({"capability_packs": [{"id": "shop"}], "operator_contracts": [{
        "contract_id": "shop", "required_outputs": [
            {"path": "ui/pages/products.yaml", "owner": "templates"},
            {"path": "ui/pages/checkout.yaml"},
            {"path": "modules/shop/backend/handler.py", "owner": "workspace"},
            {"path": "modules/shop/backend/service.py", "owner": "generator"},
        ],
    }]})
    # Selected by id alone the pack's templates are unknown, so a workspace output stays authorable.
    assert pack_owned_output_paths(context) == frozenset({"ui/pages/products.yaml", "ui/pages/checkout.yaml"})
    packs = [{"id": "mozaikspay", "capability_source": "managed_capability", "status": "active",
              "pack_source_path": str(MOZAIKSPAY_PACK)}]
    handler = "modules/billing_portal/backend/handler.py"  # owner: workspace, shipped as a template
    assert handler in pack_owned_output_paths(_bridge({"capability_packs": packs}))
    binding = RunBuildBinding(target_app_id="generated-app", build_id="build-test",
                              build_registry_id="registry-test", phase="refinement").model_dump()
    refinement = _bridge({"capability_packs": packs, "run_build_binding": binding})
    assert handler not in pack_owned_output_paths(refinement)
    assert "modules/billing_portal/module.yaml" in pack_owned_output_paths(refinement)


def test_unreachable_gated_action_falls_back_to_the_plans_page_task_when_no_page_was_authored():
    plan = {
        "pages": [
            {"name": "Dashboard", "route": "/dashboard", "sections_hint": [
                {"primitive": "SummaryStrip", "data_source": {"module_id": "task_management", "action_id": "summarize_tasks"}},
            ]},
            {"name": "Tasks", "route": "/tasks"},
        ],
        "build_tasks": [{"task_id": "page_bundle", "task_type": "page_bundle", "initial_agent": "AppSchemaAgent",
                         "owned_paths": ["app.json", "ui/pages/tasks.yaml", "ui/pages/dashboard.yaml"]}],
    }
    wiring = {"passed": False, "failed_tests": [{
        "test": "wiring_unreachable_gated_action", "action": "task_management/update_task",
        "error": "Gated user-facing action 'task_management/update_task' (task.edit) has no reachable page binding.",
        "fix_suggestion": "Bind the action.",
    }]}
    files = {"ui/route_manifest.json": "{}", "modules/task_management/module.yaml": _module_yaml()}
    errors = _wiring_repair_errors(wiring, files, {"app_build_plan": plan, "capability_packs": []})
    assert errors[0].startswith("ui/pages/dashboard.yaml: Gated user-facing action")  # the page that lists the module
    plan["pages"][0].pop("sections_hint")
    errors = _wiring_repair_errors(wiring, files, {"app_build_plan": plan, "capability_packs": []})
    assert errors[0].startswith("ui/pages/tasks.yaml: ")  # else the page task's first owned page


def test_pages_the_compiler_rejected_keep_their_compiled_form_and_are_reported_once():
    dashboard = _dashboard([{"id": "done_count", "label": "Done", "value_key": "completed_tasks"}])
    tasks = _tasks_page()
    tasks["route"] = "/work"
    context = _context()
    snapshot = {key: detach(context.get(key)) for key in ("generated_files", "data_contract", "design_surface_map", "app_build_plan")}
    failures: list[str] = []
    compiled = compile_authored_page_files(
        {"ui/pages/dashboard.yaml": yaml.safe_dump(dashboard), "ui/pages/tasks.yaml": yaml.safe_dump(tasks)},
        payload={"code_files": []}, context=context, failures=failures,
    )
    assert [line.split(":")[0] for line in failures] == ["ui/pages/dashboard.yaml"]
    # The rejected page keeps what compilation made of it, so its structure can still be checked.
    assert "data_source" not in compiled["ui/pages/dashboard.yaml"]
    with pytest.raises(ValueError) as error:
        _normalize_owned_page_files_from_plan(
            [{"filename": path, "content": content} for path, content in compiled.items()],
            task={"owned_paths": ["ui/pages/dashboard.yaml", "ui/pages/tasks.yaml"]}, base_context=snapshot,
            reject_api_endpoints=False, skip_paths={"ui/pages/dashboard.yaml"},
        )
    assert str(error.value).startswith("ui/pages/tasks.yaml: ") and "route must preserve approved" in str(error.value)


def _gated_module_yaml(*gated: str) -> str:
    manifest = yaml.safe_load(_module_yaml())
    for action in manifest["actions"]:
        if action["id"] in gated:
            action["entitlement_gate"] = f"task.{action['id'].split('_')[0]}"
    return yaml.safe_dump(manifest, sort_keys=False)


def test_gated_canonical_create_and_delete_get_a_toolbar_create_and_a_confirmed_row_delete(caplog):
    """Live chat fdfa818e: gated create_task/delete_task had no page entry point at all."""
    context = _context(module_yaml=_gated_module_yaml("create_task", "delete_task"))
    compiled = _compile({"ui/pages/tasks.yaml": yaml.safe_dump(_tasks_page(selection=None))}, context, caplog)
    tasks = compiled["ui/pages/tasks.yaml"]
    validate_page_schema(deepcopy(tasks))
    table = _section(tasks, "task-table")["config"]
    assert table["selection"] == "single"
    assert [(a["id"], a["label"], a["variant"], a["requires_selection"]) for a in table["actions"]] == [
        ("open-update_task", "Edit", "secondary", True),
        ("open-create_task", "New Task", "primary", False),
        ("open-delete_task", "Delete", "danger", True),
    ]
    create_form = _section(tasks, "create_task-modal")["config"]["children"][0]["config"]
    assert create_form["submit_action"]["href"] == "/api/modules/task_management/create_task"
    assert [field["name"] for field in create_form["fields"]] == ["title", "description"]
    assert "initial_values_key" not in create_form
    confirm = _section(tasks, "delete_task-modal")["config"]
    assert confirm["actions"] == [
        {"id": "confirm-delete_task", "label": "Delete", "variant": "danger", "action_type": "delete",
         "href": "/api/modules/task_management/delete_task",
         "payload": {"task_id": "{selected_row.task_id}"}, "requires_selection": False, "closes_modal": True},
        {"id": "cancel-delete_task", "label": "Cancel", "variant": "secondary", "action_type": "event",
         "event_type": "ui.modal.close", "payload": {"modal_id": "delete_task-modal"},
         "requires_selection": False, "closes_modal": True},
    ]
    assert {f"task_management/{name}" for name in ("create_task", "update_task", "delete_task")} <= (
        reachable_page_action_keys([tasks])
    )
    lines = [record.getMessage() for record in caplog.records if "constructed" in record.getMessage()]
    assert any("create_task (task.create) has no page entry point; added a 'New Task' toolbar action" in line
               for line in lines)
    assert any("delete_task (task.delete) has no page entry point; added a Delete row action and a confirmation"
               in line for line in lines)
    # Construction is idempotent: assembly re-normalizes to the same page.
    again = normalize_planned_page_content(
        yaml.safe_dump(tasks, sort_keys=False, allow_unicode=True), path="ui/pages/tasks.yaml",
        modules=module_action_index_from_context(context),
        data_contract=detach(context.get("data_contract")), design_surface_map=detach(context.get("design_surface_map")),
    )
    assert yaml.safe_load(again) == tasks


def test_an_authored_section_holding_the_confirmation_id_is_never_overwritten(caplog):
    page = _tasks_page()
    page["sections"].append({"id": "delete_task-modal", "primitive": "Alert", "config": {"message": "Careful"}})
    context = _context(module_yaml=_gated_module_yaml("delete_task"))
    compiled = _compile({"ui/pages/tasks.yaml": yaml.safe_dump(page)}, context, caplog)
    tasks = compiled["ui/pages/tasks.yaml"]
    assert _section(tasks, "delete_task-modal")["primitive"] == "Alert"
    assert "task_management/delete_task" not in reachable_page_action_keys([tasks])
    refused = [record.getMessage() for record in caplog.records if "not constructed" in record.getMessage()]
    assert any("no delete entry point for gated task_management/delete_task: section 'delete_task-modal' exists"
               in line for line in refused)


def _shared_module_yaml() -> str:
    """The module with no gate at all: every write is in every plan (or the app sells no plans)."""
    manifest = yaml.safe_load(_module_yaml())
    for action in manifest["actions"]:
        action.pop("entitlement_gate", None)
    return yaml.safe_dump(manifest, sort_keys=False)


def _constructed(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if ": constructed " in record.getMessage()]


def test_shared_canonical_create_and_delete_get_the_same_entry_points_as_gated_ones(caplog):
    """Replay of fdfa818e at 92190318: #768 left only update_task gated, and create/delete lost their buttons."""
    context = _context()  # only update_task is gated, as in the replayed pricing contract
    compiled = _compile({"ui/pages/tasks.yaml": yaml.safe_dump(_tasks_page(selection=None))}, context, caplog)
    tasks = compiled["ui/pages/tasks.yaml"]
    validate_page_schema(deepcopy(tasks))
    table = _section(tasks, "task-table")["config"]
    assert table["selection"] == "single"
    assert [(a["id"], a["label"], a["variant"], a["requires_selection"], a["payload"]["modal_id"]) for a in table["actions"]] == [
        ("open-update_task", "Edit", "secondary", True, "update_task-modal"),
        ("open-create_task", "New Task", "primary", False, "create_task-modal"),
        ("open-delete_task", "Delete", "danger", True, "delete_task-modal"),
    ]
    create_form = _section(tasks, "create_task-modal")["config"]["children"][0]["config"]
    assert create_form["submit_action"]["href"] == "/api/modules/task_management/create_task"
    assert [(field["name"], field["required"]) for field in create_form["fields"]] == [("title", True), ("description", False)]
    confirm = _section(tasks, "delete_task-modal")["config"]["actions"][0]
    assert (confirm["action_type"], confirm["href"], confirm["payload"]) == (
        "delete", "/api/modules/task_management/delete_task", {"task_id": "{selected_row.task_id}"},
    )
    assert {f"task_management/{name}" for name in ("list_tasks", "create_task", "update_task", "delete_task")} <= (
        reachable_page_action_keys([tasks])
    )
    lines = _constructed(caplog)
    assert any(line.endswith(
        "tasks/task-table: gated action task_management/update_task (task.edit) has no page entry point; "
        "added an Edit row action and a modal form that submits it") for line in lines)
    assert any(line.endswith(
        "tasks/task-table: shared action task_management/create_task has no page entry point; "
        "added a 'New Task' toolbar action and a modal form that submits it") for line in lines)
    assert any(line.endswith(
        "tasks/task-table: shared action task_management/delete_task has no page entry point; "
        "added a Delete row action and a confirmation dialog that deletes the selected Task") for line in lines)
    again = normalize_planned_page_content(
        yaml.safe_dump(tasks, sort_keys=False, allow_unicode=True), path="ui/pages/tasks.yaml",
        modules=module_action_index_from_context(context),
        data_contract=detach(context.get("data_contract")), design_surface_map=detach(context.get("design_surface_map")),
    )
    assert yaml.safe_load(again) == tasks


def test_a_shared_canonical_update_gets_an_edit_entry_point(caplog):
    context = _context(module_yaml=_shared_module_yaml())
    compiled = _compile({"ui/pages/tasks.yaml": yaml.safe_dump(_tasks_page())}, context, caplog)
    tasks = compiled["ui/pages/tasks.yaml"]
    assert [a["id"] for a in _section(tasks, "task-table")["config"]["actions"]] == [
        "open-update_task", "open-create_task", "open-delete_task",
    ]
    edit_form = _section(tasks, "update_task-modal")["config"]["children"][0]["config"]
    assert edit_form["initial_values_key"] == "selected_row"
    assert edit_form["submit_action"]["payload"][0] == {"key": "task_id", "value": "{selected_row.task_id}"}
    assert any(line.endswith(
        "shared action task_management/update_task has no page entry point; "
        "added an Edit row action and a modal form that submits it") for line in _constructed(caplog))
    assert not any("gated action" in line for line in _constructed(caplog))


def _complete_action(action_id: str = "complete_task", gate: str | None = "task.complete") -> dict:
    """A custom update-shaped write: it addresses the record by task_id and sets a declared field."""
    action = {"id": action_id, "handler_method": action_id, "api_surface": None, "permissions": [],
              "input_schema": {"type": "object", "properties": {
                  "task_id": {"type": "string"}, "is_completed": {"type": "boolean"}},
                  "additionalProperties": False, "required": ["task_id", "is_completed"]},
              "output_schema": {"type": "object", "properties": {"success": {"type": "boolean"}}, "required": ["success"]}}
    if gate:
        action["entitlement_gate"] = gate
    return action


def _shared_update_with(*extra: dict) -> str:
    """update_task in every plan; the dashboard-only summarize_tasks gate is dropped, as no tasks bundle shows it."""
    manifest = yaml.safe_load(_module_yaml(list(extra)))
    for action in manifest["actions"]:
        if action["id"] in {"update_task", "summarize_tasks"}:
            action.pop("entitlement_gate")
    return yaml.safe_dump(manifest, sort_keys=False)


def test_the_canonical_update_and_a_gated_custom_update_each_get_their_own_row_action(caplog):
    """Verifier probe P1: a shared update_task beside a gated complete_task used to build an Edit for neither."""
    owned = ["create_task", "update_task", "delete_task", "complete_task"]
    context = _context(module_yaml=_shared_update_with(_complete_action()), owned_mutations=owned)
    compiled = _compile({"ui/pages/tasks.yaml": yaml.safe_dump(_tasks_page())}, context, caplog)
    tasks = compiled["ui/pages/tasks.yaml"]
    validate_page_schema(deepcopy(tasks))
    table = _section(tasks, "task-table")["config"]
    assert [(a["id"], a["label"], a["payload"]["modal_id"], a["requires_selection"]) for a in table["actions"]] == [
        ("open-update_task", "Edit", "update_task-modal", True),
        ("open-complete_task", "Complete Task", "complete_task-modal", True),
        ("open-create_task", "New Task", "create_task-modal", False),
        ("open-delete_task", "Delete", "delete_task-modal", True),
    ]
    complete = _section(tasks, "complete_task-modal")
    assert complete["title"] == "Complete Task"
    form = complete["config"]["children"][0]["config"]
    assert form["submit_action"]["href"] == "/api/modules/task_management/complete_task"
    assert [(field["name"], field["type"]) for field in form["fields"]] == [("is_completed", "checkbox")]
    assert _section(tasks, "update_task-modal")["title"] == "Edit Task"
    lines = _constructed(caplog)
    assert any(line.endswith(
        "shared action task_management/update_task has no page entry point; "
        "added an Edit row action and a modal form that submits it") for line in lines)
    assert any(line.endswith(
        "gated action task_management/complete_task (task.complete) has no page entry point; "
        "added a 'Complete Task' row action and a modal form that submits it") for line in lines)
    files = _wiring_files(tasks, _shared_update_with(_complete_action()))
    report = asyncio.run(validate_wiring({"generated_files": files}))
    assert report["passed"] is True, report["failed_tests"]
    again = normalize_planned_page_content(
        yaml.safe_dump(tasks, sort_keys=False, allow_unicode=True), path="ui/pages/tasks.yaml",
        modules=module_action_index_from_context(context),
        data_contract=detach(context.get("data_contract")), design_surface_map=detach(context.get("design_surface_map")),
    )
    assert yaml.safe_load(again) == tasks


def test_two_gated_custom_updates_are_refused_but_the_canonical_edit_is_still_built(caplog):
    owned = ["create_task", "update_task", "delete_task", "complete_task", "reopen_task"]
    module_yaml = _shared_update_with(_complete_action(), _complete_action("reopen_task", "task.reopen"))
    compiled = _compile({"ui/pages/tasks.yaml": yaml.safe_dump(_tasks_page())},
                        _context(module_yaml=module_yaml, owned_mutations=owned), caplog)
    actions = _section(compiled["ui/pages/tasks.yaml"], "task-table")["config"]["actions"]
    assert [(a["id"], a["label"]) for a in actions] == [
        ("open-update_task", "Edit"), ("open-create_task", "New Task"), ("open-delete_task", "Delete"),
    ]
    refused = [record.getMessage() for record in caplog.records if "not constructed" in record.getMessage()]
    for action_id in ("complete_task", "reopen_task"):
        assert any(f"no edit entry point for gated task_management/{action_id}: 2 gated update-shaped writes" in line
                   for line in refused)


def test_an_internal_canonical_write_gets_no_entry_point():
    manifest = yaml.safe_load(_shared_module_yaml())
    for action in manifest["actions"]:
        if action["id"] == "delete_task":
            action["api_surface"] = "internal"
    context = _context(module_yaml=yaml.safe_dump(manifest, sort_keys=False))
    tasks = _compile({"ui/pages/tasks.yaml": yaml.safe_dump(_tasks_page())}, context)["ui/pages/tasks.yaml"]
    assert [a["id"] for a in _section(tasks, "task-table")["config"]["actions"]] == ["open-update_task", "open-create_task"]
    assert "delete_task-modal" not in {section["id"] for section in tasks["sections"]}


def _authored_modals() -> list[dict]:
    """Modal forms the page author wrote for each canonical write, under the author's own ids."""
    def form(form_id: str, action_id: str, *, edit: bool) -> dict:
        config = {
            "fields": [{"name": "title", "label": "Title", "type": "text", "required": not edit}],
            "submit_action": {"id": f"save-{form_id}", "label": "Save", "action_type": "submit",
                              "data_source": {"module_id": "task_management", "action_id": action_id}},
        }
        if edit:
            config["initial_values_key"] = "selected_row"
            config["submit_action"]["payload"] = [
                {"key": "task_id", "value": "{selected_row.task_id}"}, {"key": "title", "value": "{form.title}"},
            ]
        return {"id": form_id, "primitive": "Form", "config": config}

    return [
        {"id": "new-task", "primitive": "Modal", "title": "New task", "config": {
            "title": "New task", "children": [form("new-task-form", "create_task", edit=False)]}},
        {"id": "edit-task", "primitive": "Modal", "title": "Edit task", "config": {
            "title": "Edit task", "children": [form("edit-task-form", "update_task", edit=True)]}},
        {"id": "remove-task", "primitive": "Modal", "title": "Remove task", "config": {
            "title": "Remove task", "children": [], "actions": [
                {"id": "confirm-remove", "label": "Remove", "variant": "danger", "action_type": "delete",
                 "data_source": {"module_id": "task_management", "action_id": "delete_task"},
                 "payload": {"task_id": "{selected_row.task_id}"}},
            ]}},
    ]


def test_an_authored_modal_for_the_write_gets_the_opener_instead_of_a_second_modal(caplog):
    page = _tasks_page(selection=None)
    page["sections"].extend(_authored_modals())
    context = _context(module_yaml=_shared_module_yaml())
    compiled = _compile({"ui/pages/tasks.yaml": yaml.safe_dump(page)}, context, caplog)
    tasks = compiled["ui/pages/tasks.yaml"]
    validate_page_schema(deepcopy(tasks))
    assert [section["id"] for section in tasks["sections"]] == ["task-table", "new-task", "edit-task", "remove-task"]
    table = _section(tasks, "task-table")["config"]
    assert [(a["id"], a["label"], a["payload"]["modal_id"], a["requires_selection"]) for a in table["actions"]] == [
        ("open-update_task", "Edit", "edit-task", True),
        ("open-create_task", "New Task", "new-task", False),
        ("open-delete_task", "Delete", "remove-task", True),
    ]
    assert table["selection"] == "single"
    assert {f"task_management/{name}" for name in ("create_task", "update_task", "delete_task")} <= (
        reachable_page_action_keys([tasks])
    )
    lines = _constructed(caplog)
    assert any(line.endswith(
        "shared action task_management/create_task has no page entry point; added a 'New Task' toolbar action "
        "opening the page's existing modal form 'new-task' that submits it") for line in lines)
    assert any(line.endswith(
        "shared action task_management/update_task has no page entry point; added an Edit row action "
        "opening the page's existing modal form 'edit-task' that submits it") for line in lines)
    assert any(line.endswith(
        "shared action task_management/delete_task has no page entry point; added a Delete row action "
        "opening the page's existing confirmation dialog 'remove-task' that deletes the selected Task") for line in lines)


def test_a_formless_authored_modal_is_not_opened_and_the_write_gets_a_working_form(caplog):
    """The replayed tasks.yaml: create-task-modal posts {form.title} from its footer with no Form to fill it."""
    page = _tasks_page()
    formless = {"id": "create-task-modal", "primitive": "Modal", "title": "New Task", "config": {
        "title": "Create a New Task", "size": "medium", "children": [], "actions": [
            {"id": "submit-create-task", "label": "Create", "variant": "primary", "action_type": "submit",
             "data_source": {"module_id": "task_management", "action_id": "create_task"},
             "payload": [{"key": "title", "value": "{form.title}"}, {"key": "description", "value": "{form.description}"}]},
        ]}}
    page["sections"].append(formless)
    compiled = _compile({"ui/pages/tasks.yaml": yaml.safe_dump(page)}, _context(), caplog)
    tasks = compiled["ui/pages/tasks.yaml"]
    table = _section(tasks, "task-table")["config"]
    opener = next(action for action in table["actions"] if action["id"] == "open-create_task")
    assert opener["payload"] == {"modal_id": "create_task-modal"}
    assert _section(tasks, "create_task-modal")["config"]["children"][0]["primitive"] == "Form"
    assert _section(tasks, "create-task-modal")["config"]["children"] == []  # the author's section is untouched
    assert any(line.endswith(
        "added a 'New Task' toolbar action and a modal form that submits it; modal 'create-task-modal' submits it "
        "from footer actions without a Form, so it collects no input and is left unopened") for line in _constructed(caplog))


def _wiring_files(tasks_page: dict, module_yaml: str | None = None) -> dict[str, str]:
    if module_yaml is None:  # only update_task stays gated; the dashboard's summarize_tasks is not on this bundle
        manifest = yaml.safe_load(_module_yaml())
        next(action for action in manifest["actions"] if action["id"] == "summarize_tasks").pop("entitlement_gate")
        module_yaml = yaml.safe_dump(manifest, sort_keys=False)
    return {
        "modules/task_management/module.yaml": module_yaml,
        "data/contract.json": json.dumps(_data_contract()),
        "ui/pages/tasks.yaml": yaml.safe_dump(tasks_page, sort_keys=False),
    }


def _bundle_tasks_page() -> dict:
    """A final bundle page that lists tasks and binds none of its writes."""
    page = _tasks_page(selection="single")
    config = _section(page, "task-table")["config"]
    config.pop("data_source")
    config["api_endpoint"] = "/api/modules/task_management/list_tasks"
    return page


def test_the_wiring_gate_fails_an_unreachable_shared_canonical_write_naming_the_action_and_page():
    files = _wiring_files(_bundle_tasks_page())
    report = asyncio.run(validate_wiring({"generated_files": files}))
    assert report["passed"] is False
    failures = {
        (failure["test"], failure["action"]): failure for failure in report["failed_tests"]
        if failure["test"].startswith("wiring_unreachable_")
    }
    # update_task is gated: the gated check reports it once, and the canonical check does not repeat it.
    assert sorted(failures) == [
        ("wiring_unreachable_canonical_write", "task_management/create_task"),
        ("wiring_unreachable_canonical_write", "task_management/delete_task"),
        ("wiring_unreachable_gated_action", "task_management/update_task"),
    ]
    create = failures[("wiring_unreachable_canonical_write", "task_management/create_task")]
    assert create["page"] == "tasks" and create["pages"] == ["tasks"]
    assert create["error"] == (
        "Canonical create action 'task_management/create_task' has no reachable page entry point, "
        "but page 'tasks' lists its collection 'tasks'."
    )
    assert create["fix_suggestion"].startswith("Give the 'tasks' table on page 'tasks' a toolbar action")
    assert "/api/modules/task_management/create_task" in create["fix_suggestion"]
    delete = failures[("wiring_unreachable_canonical_write", "task_management/delete_task")]
    assert "Delete row action" in delete["fix_suggestion"]
    assert report["checks"][0]["details"]["unreachable_canonical_writes"] == [
        "task_management/create_task", "task_management/delete_task",
    ]
    # The repair loop receives one message per failure, prefixed with the page that lists the collection.
    errors = _wiring_repair_errors(report, files, {"capability_packs": []})
    assert [error for error in errors if "Canonical" in error] == [
        f"ui/pages/tasks.yaml: {failure['error']} {failure['fix_suggestion']}"
        for failure in (create, delete)
    ]


def test_an_unreachable_canonical_write_listed_only_by_a_template_page_goes_to_an_authored_page():
    packs = [{"id": "mozaikspay", "capability_source": "managed_capability", "status": "active",
              "pack_source_path": str(MOZAIKSPAY_PACK)}]
    files = {
        "ui/route_manifest.json": "{}",
        "ui/pages/billing.yaml": yaml.safe_dump({"name": "billing", "sections": [{"id": "t", "primitive": "ResourceTable", "config": {
            "api_endpoint": "/api/modules/task_management/list_tasks", "columns": ["title"]}}]}),
        "ui/pages/dashboard.yaml": yaml.safe_dump({"name": "dashboard", "sections": [{"id": "s", "primitive": "SummaryStrip", "config": {
            "api_endpoint": "/api/modules/task_management/summarize_tasks", "items": []}}]}),
        "modules/task_management/module.yaml": _module_yaml(),
    }
    failure = {
        "test": "wiring_unreachable_canonical_write", "action": "task_management/create_task",
        "page": "billing", "pages": ["billing"],
        "error": "Canonical create action 'task_management/create_task' has no reachable page entry point, "
                 "but page 'billing' lists its collection 'tasks'.",
        "fix_suggestion": "Give the 'tasks' table on page 'billing' a toolbar action.",
    }
    errors = _wiring_repair_errors({"passed": False, "failed_tests": [failure]}, files, {"capability_packs": packs})
    # billing.yaml is a pack template page and cannot be repaired; the authored reader of the module can.
    assert len(errors) == 1 and errors[0].startswith("ui/pages/dashboard.yaml: Canonical create action")
    # An authored listing page is the target itself.
    failure = {**failure, "page": "tasks", "pages": ["billing", "tasks"]}
    files["ui/pages/tasks.yaml"] = files["ui/pages/billing.yaml"].replace("name: billing", "name: tasks")
    errors = _wiring_repair_errors({"passed": False, "failed_tests": [failure]}, files, {"capability_packs": packs})
    assert errors[0].startswith("ui/pages/tasks.yaml: Canonical create action")


def test_the_wiring_gate_passes_once_construction_supplies_every_canonical_write():
    compiled = _compile({"ui/pages/tasks.yaml": yaml.safe_dump(_tasks_page())}, _context())
    report = asyncio.run(validate_wiring({"generated_files": _wiring_files(compiled["ui/pages/tasks.yaml"])}))
    assert report["passed"] is True, report["failed_tests"]
    details = report["checks"][0]["details"]
    assert details["unreachable_canonical_writes"] == [] and details["unreachable_gated_actions"] == []


def test_the_wiring_gate_ignores_internal_and_unlisted_canonical_writes():
    manifest = yaml.safe_load(_shared_module_yaml())
    for action in manifest["actions"]:
        if action["id"] in {"create_task", "update_task", "delete_task"}:
            action["api_surface"] = "internal"
    internal = asyncio.run(validate_wiring({"generated_files": _wiring_files(
        _bundle_tasks_page(), yaml.safe_dump(manifest, sort_keys=False))}))
    assert internal["checks"][0]["details"]["unreachable_canonical_writes"] == []
    # A page that only counts tasks in a KPI does not list the collection, so it owes no entry point.
    kpi_only = {"schema_version": "mozaiks.app_page.v1", "name": "tasks", "route": "/tasks", "title": "Tasks",
                "page_type": "record_list", "layout": "full-width", "shell_mode": "workspace", "sections": [
                    {"id": "count", "primitive": "SummaryStrip", "title": "Tasks", "config": {
                        "api_endpoint": "/api/modules/task_management/list_tasks",
                        "items": [{"id": "total", "label": "Tasks", "value_key": "total"}]}}]}
    unlisted = asyncio.run(validate_wiring({"generated_files": _wiring_files(kpi_only, _shared_module_yaml())}))
    assert unlisted["checks"][0]["details"]["unreachable_canonical_writes"] == []

"""Pack-owned outputs are never model work (live AppGenerator chat fdfa818e at OSS 57c78c5f).

The selected MozaiksPay pack declares its facade module, client and pages in
``required_outputs``; its templates write them at assembly. The live run still
scheduled model work for them: plan repair synthesized a billing_portal task
trio, the page worker was made to author billing/pricing/usage, and assembly
checked those paths. These tests hold the contract at each stage with the real
tools and a real ContextVariablesBridge.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools import assemble_app_tasks as assembly
from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    review_app_build_plan,
    validate_plan_coverage,
)
from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    resolve_templates_for_pack,
)
from mozaiksai.core.runtime.app.page_schema import PageSchemaValidationError, validate_page_schema
from mozaiksai.core.workflow.agents.factory import _workflow_tool_invocation
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    pack_owned_output_paths,
)
from mozaiksai.core.workflow.generator_support.page_binding_construction import (
    construct_page_bindings,
)
from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    compile_authored_page_files,
    module_action_index,
)
from mozaiksai.core.workflow.task_batches import _normalize_owned_page_files_from_plan
from scripts.appgenerator_fixture_replay import execute_file_replay
from tests.test_app_plan_managed_facade_repair import _plan_and_context
from tests.test_appgenerator_assembly_failures import _context as _assembly_context
from tests.test_appgenerator_page_binding_construction import _bridge
from tests.test_continuous_deterministic_materialization import _load_models

MOZAIKSPAY = Path(__file__).resolve().parents[1] / "factory_app" / "build_context" / "mozaikspay"
PACK = {"id": "mozaikspay", "capability_source": "managed_capability", "status": "active",
        "pack_source_path": str(MOZAIKSPAY)}
PACK_PAGES = {"ui/pages/billing.yaml", "ui/pages/pricing.yaml", "ui/pages/usage.yaml"}


def _templates() -> dict[str, str]:
    return {file["filename"]: file["content"] for file in resolve_templates_for_pack(MOZAIKSPAY, "mozaikspay")}


def _page(name: str, route: str, sections: list[dict]) -> dict:
    return {"schema_version": "mozaiks.app_page.v1", "name": name, "route": route, "title": name,
            "page_type": "record_list", "layout": "full-width", "sections": sections}


def _placeholder_pricing() -> dict:
    """The live worker's pricing page: PricingCatalog with an `items` key the primitive does not accept."""
    return _page("Pricing", "/pricing", [{"id": "pricing-catalog", "primitive": "PricingCatalog", "config": {
        "data_source": {"module_id": "billing_portal", "action_id": "list_plans"}, "items": [],
    }}])


# ------------------------------------------------------------------ (a) plan review


def test_plan_review_schedules_no_model_work_for_the_selected_packs_outputs(caplog):
    """The live plan named no facade task; repair synthesized three and page_bundle owned the pack pages."""
    _load_models()
    plan, context = _plan_and_context(collapsed=False)
    plan["build_tasks"] = [task for task in plan["build_tasks"] if task["task_id"] not in {"2", "3", "4", "5"}]
    plan["generation_order"] = [task["task_id"] for task in plan["build_tasks"]]
    pages = next(task for task in plan["build_tasks"] if task["task_type"] == "page_bundle")
    pages["owned_paths"] = sorted({*pages["owned_paths"], *PACK_PAGES})
    pack_paths = pack_owned_output_paths(context)
    assert PACK_PAGES | {"modules/billing_portal/module.yaml", "modules/billing_portal/backend/handler.py"} <= pack_paths

    caplog.set_level(logging.INFO, logger="factory_app.workflows.AppGenerator.tools.app_plan_review")
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "ready", result
    cached = detach(context.get("app_build_plan"))
    tasks = cached["build_tasks"]
    assert not [task["task_id"] for task in tasks if task.get("capability_pack_id") == "billing_portal"]
    assert not {path for task in tasks for path in task["owned_paths"]} & pack_paths
    page_task = next(task for task in tasks if task["task_type"] == "page_bundle")
    page_artifacts = {path for path in page_task["owned_paths"] if path == "app.json" or path.startswith("ui/pages/")}
    assert page_artifacts == {"app.json", "ui/pages/dashboard.yaml", "ui/pages/tasks.yaml"}
    assert "Selected pack templates provide ui/pages/pricing.yaml, ui/pages/billing.yaml, ui/pages/usage.yaml" in (
        page_task["initial_message"]
    )
    # The facade stays an approved capability with its approved pages and actions.
    facade = next(pack for pack in cached["capability_packs"] if pack["capability_pack_id"] == "billing_portal")
    assert facade["capability_source"] == "generated_module"
    assert {(page["name"], page["route"]) for page in cached["pages"]} >= {
        ("Pricing", "/pricing"), ("Billing", "/billing"), ("Usage", "/usage"),
    }
    repairs = [record.getMessage() for record in caplog.records]
    assert any("released pack-owned" in line and "ui/pages/billing.yaml" in line for line in repairs)
    assert not any("synthesized 'task_billing_portal" in line for line in repairs)


def test_a_task_owning_a_pack_output_fails_plan_coverage():
    _load_models()
    plan, context = _plan_and_context(collapsed=False)
    with pytest.raises(ValueError, match=r"no task may own them: .*3 owns \['modules/billing_portal/module.yaml'\]"):
        validate_plan_coverage(plan, context)


# ------------------------------------------------------------------ (a) task time


@pytest.mark.asyncio
async def test_the_page_worker_copy_of_a_pack_page_is_discarded_with_a_logged_normalization(caplog):
    context = _bridge({
        "capability_packs": [PACK],
        "app_build_plan": {"pages": [
            {"name": "Dashboard", "route": "/dashboard"}, {"name": "Pricing", "route": "/pricing"},
        ], "capability_packs": [], "build_tasks": []},
    })
    task = {
        "task_id": "page_bundle", "task_type": "page_bundle", "initial_agent": "AppSchemaAgent",
        "capability_pack_id": None, "surface_id": "page_bundle", "surface_kind": "ui_only",
        "execution_target": "AppGenerator", "description": "Pages.", "initial_message": "Generate pages.",
        "owned_paths": ["app.json", "ui/pages/dashboard.yaml"], "depends_on": [],
    }
    values = {key: detach(context.get(key)) for key in context.snapshot()}
    values["app_task_batch_items"] = [{**task, "current_build_task": dict(task), "current_build_task_id": "page_bundle",
                                       "current_build_task_type": "page_bundle", "task_run_mode": True}]
    dashboard = _page("Dashboard", "/dashboard", [{"id": "hero", "primitive": "Panel", "config": {"title": "Overview"}}])
    output = {"manifest": {"app_name": "TaskTracker", "default_route": "/dashboard", "pages": ["Dashboard", "Pricing"]},
              "pages": [dashboard, _placeholder_pricing()]}

    caplog.set_level(logging.INFO, logger="mozaiksai.core.workflow.task_batches")
    results = await execute_file_replay(values, {}, task_outputs={"page_bundle": output})

    stored = results["page_bundle"]
    assert [page["name"] for page in stored["pages"]] == ["Dashboard"]
    assert "ui/pages/pricing.yaml" not in {entry["filename"] for entry in stored["code_files"]}
    assert any(
        "PACK_OWNED_OUTPUT_DISCARDED: task=page_bundle" in record.getMessage()
        and "ui/pages/pricing.yaml" in record.getMessage()
        for record in caplog.records
    )


# ------------------------------------------------------------------ (b) assembly


def _assembly_plan() -> dict:
    return {
        "pages": [{"name": "Dashboard", "route": "/dashboard"}, {"name": "Billing", "route": "/billing"},
                  {"name": "Pricing", "route": "/pricing"}, {"name": "Usage", "route": "/usage"}],
        "capability_packs": [],
        "build_tasks": [{"task_id": "page_bundle", "task_type": "page_bundle", "initial_agent": "AppSchemaAgent",
                         "owned_paths": ["app.json", "ui/pages/dashboard.yaml"], "depends_on": []}],
    }


@pytest.mark.asyncio
async def test_assembly_takes_pack_owned_files_only_from_the_templates(caplog):
    templates = _templates()
    placeholder_module = yaml.safe_dump({"schema_version": "mozaiks.module.v1", "module": {"id": "billing_portal"},
                                         "actions": [{"id": "get_subscription_status"}]})
    dashboard = _page("dashboard", "/dashboard", [{"id": "hero", "primitive": "Panel", "config": {"title": "Overview"}}])
    context = _assembly_context(
        generated_files={"ui/pages/billing.yaml": "stale: previous assembly copy\n"},
        deleted_files=["ui/pages/usage.yaml"],  # a repair cannot delete a pack-owned file either
        capability_packs=[PACK], app_build_plan=_assembly_plan(),
        app_task_batch_results={
            "page_bundle": {"_task_id": "page_bundle", "code_files": [
                {"filename": "app.json", "content": '{"appName": "TaskTracker"}'},
                {"filename": "ui/pages/dashboard.yaml", "content": yaml.safe_dump(dashboard)},
                {"filename": "ui/pages/pricing.yaml", "content": yaml.safe_dump(_placeholder_pricing())},
            ]},
            "facade_contract": {"_task_id": "facade_contract", "code_files": [
                {"filename": "modules/billing_portal/module.yaml", "content": placeholder_module},
            ]},
        },
    )

    caplog.set_level(logging.INFO, logger="factory_app.workflows.AppGenerator.tools.assemble_app_tasks")
    with _workflow_tool_invocation(context):
        result = await assembly.assemble_app_tasks(context_variables=context)

    assert result["success"] is True, result
    files = detach(context.get("generated_files"))
    for path in (*PACK_PAGES, "modules/billing_portal/module.yaml", "modules/billing_portal/backend/service.py"):
        assert files[path] == templates[path], path
    discarded = " ".join(record.getMessage() for record in caplog.records if "PACK_OWNED_OUTPUT_DISCARDED" in record.getMessage())
    for path in ("ui/pages/pricing.yaml", "modules/billing_portal/module.yaml", "ui/pages/billing.yaml"):
        assert path in discarded


def test_a_defective_template_page_fails_assembly_naming_its_pack_and_source():
    templates = _templates()
    stale = yaml.safe_load(templates["ui/pages/billing.yaml"])
    stale["sections"][0].update(primitive="DataTable", config={
        "api_endpoint": "/api/modules/billing_portal/get_subscription_status", "columns": ["plan_name", "status"],
    })
    dashboard = _page("dashboard", "/dashboard", [{"id": "hero", "primitive": "Panel", "config": {"title": "Overview"}}])
    files = {**templates, "ui/pages/billing.yaml": yaml.safe_dump(stale, sort_keys=False),
             "ui/pages/dashboard.yaml": yaml.safe_dump(dashboard), "app.json": "{}"}
    context = _bridge({"capability_packs": [PACK]})
    with pytest.raises(ValueError) as error:
        assembly._apply_planned_page_contracts(
            [{"filename": path, "content": content} for path, content in files.items()], _assembly_plan(),
            context_variables=context, pack_outputs=assembly.pack_owned_outputs(context),
        )
    message = str(error.value)
    assert message.startswith(f"ui/pages/billing.yaml: selected pack 'mozaikspay' template (from {MOZAIKSPAY})")
    assert "no task authors it, so the pack itself must be fixed" in message
    assert "Billing/billing-status.data_key: 'None' must select a declared array" in message


def test_template_pages_ship_byte_for_byte_and_are_never_renormalized():
    templates = _templates()
    context = _bridge({"capability_packs": [PACK]})
    plan = _assembly_plan()
    plan["build_tasks"][0]["owned_paths"] += sorted(PACK_PAGES)  # a stale plan: not re-derived either way
    dashboard = _page("dashboard", "/dashboard", [{"id": "hero", "primitive": "Panel", "config": {"title": "Overview"}}])
    files = {**templates, "ui/pages/dashboard.yaml": yaml.safe_dump(dashboard), "app.json": "{}"}
    assembled = {entry["filename"]: entry["content"] for entry in assembly._apply_planned_page_contracts(
        [{"filename": path, "content": content} for path, content in files.items()], plan,
        context_variables=context, pack_outputs=assembly.pack_owned_outputs(context),
    )}
    for path in PACK_PAGES:
        assert assembled[path] == templates[path]
    assert yaml.safe_load(assembled["ui/pages/billing.yaml"])["name"] == "Billing"  # not renamed to the file stem


# ------------------------------------------------------------------ (d) page-author errors


def _diagnostics(page: dict) -> list[str]:
    with pytest.raises(PageSchemaValidationError) as error:
        validate_page_schema(page)
    return [f"{item.location}: {item.code}: {item.message}" for item in error.value.diagnostics]


def test_extra_forbidden_names_the_section_primitive_field_and_allowed_keys():
    page = _placeholder_pricing()
    config = page["sections"][0]["config"]
    config["api_endpoint"] = "/api/modules/billing_portal/list_plans"
    del config["data_source"]
    [message] = _diagnostics(page)
    assert message.startswith(
        "$.sections[0].config.items: page_schema.extra_forbidden: PricingCatalog section 'pricing-catalog': "
        "field 'items' is not part of this contract; allowed fields: "
    )
    assert "plans_key" in message and "add_ons_key" in message and "api_endpoint" in message


def test_a_config_rule_names_the_section_primitive_field_and_the_allowed_shapes():
    """Live chat fdfa818e got only "$.sections[0]: ... does not match the registered page-schema contract"."""
    page = _page("Task Management", "/tasks", [{"id": "task-list", "primitive": "DataTable", "config": {
        "api_endpoint": "/api/modules/task_management/list_tasks", "columns": ["title"],
        "data_key": "items", "pagination": True, "pagination_mode": "client", "page_size": 20, "total_key": "total",
    }}])
    [message] = _diagnostics(page)
    assert message == (
        "$.sections[0].config: page_schema.value_error: DataTable section 'task-list': total_key is only read "
        "with pagination_mode 'server'; either set pagination_mode: server (with pagination: true and "
        "page_size 1-100) or set total_key to null for client pagination"
    )


def test_literal_missing_and_nested_errors_name_the_allowed_values_and_the_real_location():
    page = _page("Records", "/records", [
        {"id": "list", "primitive": "DataTable", "config": {"columns": ["title"], "pagination_mode": "pages"}},
        {"id": "edit", "primitive": "Modal", "config": {"title": "Edit", "children": [
            {"id": "edit-form", "primitive": "Form", "config": {"submit_action": {"label": "Save", "action_type": "submit"}}},
        ]}},
    ])
    messages = _diagnostics(page)
    assert (
        "$.sections[0].config.pagination_mode: page_schema.literal_error: DataTable section 'list': "
        "'pagination_mode' is outside the registered page-schema contract; allowed values: 'client' or 'server'."
    ) in messages
    assert (
        "$.sections[1].config.children[0].config.fields: page_schema.missing: Form section 'edit-form': "
        "required field 'fields' is missing."
    ) in messages


def test_unknown_primitives_list_the_registered_ones_without_echoing_the_value():
    page = _page("Records", "/records", [{"id": "x", "primitive": "SyntheticPrivateWidget", "config": {}}])
    [message] = _diagnostics(page)
    assert message.startswith("$.sections[0]: page_schema.value_error: section 'x': unknown page primitive; allowed primitives: ")
    assert "DataTable" in message and "SyntheticPrivateWidget" not in message


def test_a_dotted_metric_key_whose_final_segment_is_a_returned_field_is_constructed(caplog):
    """Live chat fdfa818e: Dashboard KPI value_key 'items.total' on list_tasks (fields: items, total)."""
    contracts = {"task_management/list_tasks": {"output_schema": {"type": "object", "properties": {
        "items": {"type": "array", "items": {"type": "object", "properties": {"title": {"type": "string"}}}},
        "total": {"type": "integer"},
    }}}}
    page = _page("Dashboard", "/dashboard", [{"id": "task-summary", "primitive": "SummaryStrip", "config": {
        "api_endpoint": "/api/modules/task_management/list_tasks", "items": [
            {"id": "pending-tasks", "label": "Pending", "value_key": "items.total"},
            {"id": "done", "label": "Done", "value_key": "items.done"},
        ],
    }}])
    caplog.set_level(logging.INFO, logger="mozaiksai.core.workflow.generator_support.page_binding_construction")
    notes = construct_page_bindings(page, contracts, page="ui/pages/dashboard.yaml")
    items = page["sections"][0]["config"]["items"]
    assert items[0]["value_key"] == "total"
    assert items[1]["value_key"] == "items.done"  # no returned top-level field is named: still the author's error
    assert notes == [
        "Dashboard/task-summary.items[0].value_key: 'items.total' is not returned by task_management/list_tasks; "
        "its final segment names the returned top-level field 'total', used it",
    ]
    assert any("constructed Dashboard/task-summary.items[0].value_key" in record.getMessage() for record in caplog.records)


def test_one_rejection_carries_binding_and_schema_errors_of_the_same_page():
    """A page whose bindings were rejected is still schema-checked, so the worker's one correction sees it all."""
    module = yaml.safe_dump({"schema_version": "mozaiks.module.v1", "module": {"id": "task_management"}, "actions": [
        {"id": "list_tasks", "output_schema": {"type": "object", "properties": {
            "items": {"type": "array", "items": {"type": "object", "properties": {"title": {"type": "string"}}}},
            "total": {"type": "integer"},
        }}},
    ]})
    context = _bridge({
        "generated_files": {"modules/task_management/module.yaml": module},
        "app_build_plan": {"pages": [{"name": "Dashboard", "route": "/dashboard"}], "capability_packs": []},
    })
    dashboard = _page("Dashboard", "/dashboard", [
        {"id": "summary", "primitive": "SummaryStrip", "config": {
            "data_source": {"module_id": "task_management", "action_id": "list_tasks"},
            "items": [{"label": "Open", "value_key": "open_tasks"}],
        }},
        {"id": "recent", "primitive": "DataTable", "config": {
            "data_source": {"module_id": "task_management", "action_id": "list_tasks"}, "columns": ["title"],
            "data_key": "items", "pagination": True, "pagination_mode": "client", "page_size": 20, "total_key": "total",
        }},
    ])
    failures: list[str] = []
    compiled = compile_authored_page_files(
        {"ui/pages/dashboard.yaml": yaml.safe_dump(dashboard)}, payload={"code_files": []}, context=context,
        failures=failures,
    )
    snapshot = {key: detach(context.get(key)) for key in ("generated_files", "app_build_plan")}
    with pytest.raises(ValueError) as error:
        _normalize_owned_page_files_from_plan(
            [{"filename": path, "content": content} for path, content in compiled.items()],
            task={"owned_paths": ["ui/pages/dashboard.yaml"]}, base_context=snapshot,
            reject_api_endpoints=False, skip_paths={"ui/pages/dashboard.yaml"},
        )
    rejection = "\n".join([*failures, str(error.value)])
    assert "Dashboard/summary.items[0].value_key: 'open_tasks' is not declared" in rejection
    assert "DataTable section 'recent': total_key is only read with pagination_mode 'server'" in rejection
    assert rejection.count("open_tasks") == 1  # the binding error is reported once


def test_the_recorded_live_rejection_is_one_complete_actionable_message():
    """The live worker's dashboard and tasks pages (fdfa818e, final attempt) against their module contract."""
    list_tasks = {"id": "list_tasks", "output_schema": {"type": "object", "properties": {
        "items": {"type": "array", "items": {"type": "object", "properties": {
            field: {"type": "string"} for field in ("task_id", "title", "status", "updated_at", "created_at")
        }}},
        "total": {"type": "integer"},
    }}}
    module = yaml.safe_dump({"schema_version": "mozaiks.module.v1", "module": {"id": "task_management"},
                             "actions": [list_tasks]})
    plan_pages = [{"name": "Dashboard", "route": "/dashboard"}, {"name": "Task Management", "route": "/tasks"}]
    context = _bridge({"generated_files": {"modules/task_management/module.yaml": module},
                       "app_build_plan": {"pages": plan_pages, "capability_packs": []}})
    table = {"data_source": {"module_id": "task_management", "action_id": "list_tasks"}, "data_key": "items",
             "pagination": True, "pagination_mode": "client", "page_size": 20, "total_key": "total", "selection": "none"}
    dashboard = _page("Dashboard", "/dashboard", [
        {"id": "task-summary", "primitive": "SummaryStrip", "config": {
            "data_source": {"module_id": "task_management", "action_id": "list_tasks"},
            "items": [{"id": "pending-tasks", "label": "Pending Tasks", "value_key": "items.total"},
                      {"id": "completed-tasks", "label": "Completed Tasks", "value_key": "items.total"}]}},
        {"id": "recent-activities", "primitive": "DataTable", "config": {**deepcopy(table), "columns": ["task_id", "title"]}},
    ])
    tasks = _page("Task Management", "/tasks", [
        {"id": "task-list", "primitive": "DataTable", "config": {**deepcopy(table), "columns": ["title", "status"]}},
    ])
    failures: list[str] = []
    compiled = compile_authored_page_files(
        {"ui/pages/dashboard.yaml": yaml.safe_dump(dashboard), "ui/pages/tasks.yaml": yaml.safe_dump(tasks)},
        payload={"code_files": []}, context=context, failures=failures,
    )
    assert failures == []  # items.total -> total was constructed
    snapshot = {key: detach(context.get(key)) for key in ("generated_files", "app_build_plan")}
    with pytest.raises(ValueError) as error:
        _normalize_owned_page_files_from_plan(
            [{"filename": path, "content": content} for path, content in compiled.items()],
            task={"owned_paths": ["ui/pages/dashboard.yaml", "ui/pages/tasks.yaml"]}, base_context=snapshot,
            reject_api_endpoints=False,
        )
    lines = str(error.value).split("\n")
    assert [line.split(": ", 1)[0] for line in lines] == ["ui/pages/dashboard.yaml", "ui/pages/tasks.yaml"]
    assert "$.sections[1].config: page_schema.value_error: DataTable section 'recent-activities': total_key" in lines[0]
    assert "$.sections[0].config: page_schema.value_error: DataTable section 'task-list': total_key" in lines[1]
    assert "Field value does not match the registered page-schema contract" not in str(error.value)
    assert module_action_index(snapshot["generated_files"])["task_management"]

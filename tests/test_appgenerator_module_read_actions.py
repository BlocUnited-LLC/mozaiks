from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_build_plan import app_build_plan
from factory_app.workflows.AppGenerator.tools.code_file_utils import save_generated_code
from mozaiksai.core.adapters.ag2_task_batch_runner import AG2TaskBatchRunnerResult
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.runtime.composition.schema_validation import validate_json_schema
from mozaiksai.core.workflow import task_batches
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
from mozaiksai.core.workflow.generator_support.module_read_actions import (
    close_module_read_actions,
    materialize_module_read_actions,
)


def _plan():
    source = {"module_id": "task_management", "action_id": "list_tasks"}
    return {
        "capability_packs": [{
            "capability_pack_id": "task_management", "capability_source": "generated_module",
            "primary_entities": ["Task"],
        }],
        "pages": [
            {"name": "dashboard", "primary_entities": ["Task"], "page_type_hint": "analytics_dashboard",
             "sections_hint": [
                 {"section_id_hint": "kpi-metrics", "primitive": "SummaryStrip", "data_source": source},
                 {"section_id_hint": "task-list-overview", "primitive": "ResourceTable", "data_source": source},
             ]},
            {"name": "tasks", "primary_entities": ["Task"], "page_type_hint": "record_list",
             "sections_hint": [{"section_id_hint": "task-data-table", "primitive": "DataTable", "data_source": source}]},
        ],
        "data_contract": {"version": "1", "surfaces": [{
            "surface_id": "task_management", "surface_kind": "module", "collections": [{
                "name": "tasks", "scope": "user", "scope_field": "owner_id",
                "ownership": {"surface_id": "task_management", "surface_kind": "module"},
                "fields": [{"name": "owner_id", "type": "string", "required": True}],
            }],
        }]},
    }


def _output():
    return {"module_contract": {
        "module_id": "task_management",
        "module_yaml": {
            "schema_version": "mozaiks.module.v1", "module": {"id": "task_management"},
            "actions": [{"id": name, "handler_method": name} for name in (
                "create_task", "update_task", "delete_task",
            )],
        },
    }}


def _closed(context):
    return close_module_read_actions(
        context.get("structured_output"), app_build_plan=context.get("app_build_plan"),
        data_contract=context.get("data_contract"),
    )


def test_live_task_sections_close_one_read_action_through_real_context_bridge():
    context = ContextVariablesBridge({"structured_output": _output(), "app_build_plan": _plan()})
    closed = _closed(context)
    actions = closed["module_contract"]["module_yaml"]["actions"]
    assert [action["id"] for action in actions] == ["create_task", "update_task", "delete_task", "list_tasks"]
    assert len(context.get("structured_output")["module_contract"]["module_yaml"]["actions"]) == 3
    rendered = extract_code_file_map_from_payload(closed)
    read = yaml.safe_load(rendered["modules/task_management/module.yaml"])["actions"][-1]
    assert read["handler_method"] == "list_tasks"
    assert read["input_schema"]["additionalProperties"] is False
    assert validate_json_schema({"page": 1, "page_size": 20, "search": "pending"}, read["input_schema"]) is None
    assert validate_json_schema({"offset": 0}, read["input_schema"]) is not None
    assert read["output_schema"]["properties"]["items"] == {"type": "array", "items": {"type": "object"}}
    assert read["output_schema"]["required"] == ["items", "total"]
    assert read["api_surface"] is None
    assert read["emits"] == []
    assert read["ask_context_safe"] is False
    assert close_module_read_actions(closed, app_build_plan=_plan()) == closed


def test_provider_output_envelope_keeps_closed_typed_actions_in_dependency_output():
    closed = close_module_read_actions({"ConfigMiddlewareOutput": _output()}, app_build_plan=_plan())
    assert closed["module_contract"]["module_yaml"]["actions"][-1]["id"] == "list_tasks"


@pytest.mark.parametrize("agent", ["ModelAgent", "ServiceAgent", "ControllerAgent"])
def test_standalone_backend_workers_receive_constructed_read_before_assembly(agent):
    from mozaiksai.core.workflow.context.context_utils import apply_context_exposures
    from mozaiksai.core.workflow.context.schema import load_context_variables_config

    context = ContextVariablesBridge({
        "app_build_plan": _plan(),
        "current_build_task": {"owned_paths": ["modules/task_management/module.yaml"]},
    })
    saved = save_generated_code(StructuredOutputOverlay(context, _output()))
    assert saved["saved_files"] == ["modules/task_management/module.yaml"]
    assert not context.get("generated_files")
    workflow = Path(__file__).resolve().parents[1] / "factory_app/workflows/AppGenerator"
    config = load_context_variables_config(yaml.safe_load(
        (workflow / "context_variables.yaml").read_text(encoding="utf-8"),
    ))
    rendered = apply_context_exposures("Implement the accepted contract", [], context.snapshot(), config.agents[agent].variables)
    assert "CODE_FILES:" in rendered
    assert "handler_method: list_tasks" in rendered
    assert "page_size" in rendered
    assert "total" in rendered


@pytest.mark.parametrize("encoded", [False, True])
@pytest.mark.parametrize("action_id", ["list_tasks", "get_tasks"])
def test_nested_dashboard_sections_close_their_exact_read_source(encoded, action_id):
    primitive = "ResourceTable" if action_id == "list_tasks" else "SummaryStrip"
    config = {"children": [{"id": "task-list-overview", "primitive": primitive, "config": {
        "data_source": {"module_id": "task_management", "action_id": action_id},
        **({"columns": ["id", "title"]} if action_id == "list_tasks" else {"items": []}),
    }}]}
    plan = _plan()
    plan["pages"] = [{
        "name": "dashboard", "primary_entities": ["Task"], "page_type_hint": "analytics_dashboard",
        "sections_hint": [{"primitive": "Grid", "data_source": None,
                           "config_hint": json.dumps(config) if encoded else config}],
    }]
    context = ContextVariablesBridge({"structured_output": _output(), "app_build_plan": plan})
    actions = _closed(context)["module_contract"]["module_yaml"]["actions"][3:]
    assert [action["id"] for action in actions] == [action_id]


def test_nested_unbound_list_uses_declared_entity():
    plan = _plan()
    plan["pages"] = [{
        "name": "dashboard", "primary_entities": ["Task"], "page_type_hint": "analytics_dashboard",
        "sections_hint": [{"primitive": "Grid", "config_hint": json.dumps({"children": [{
            "id": "task-data-table", "primitive": "DataTable", "config": {"columns": ["id"]},
        }]})}],
    }]
    context = ContextVariablesBridge({"structured_output": _output(), "app_build_plan": plan})
    actions = _closed(context)["module_contract"]["module_yaml"]["actions"][3:]
    assert [action["id"] for action in actions] == ["list_tasks"]


@pytest.mark.parametrize(("page_type", "expected"), [
    ("record_list", ["list_tasks"]), ("record_detail", ["get_tasks"]),
    ("split_view", ["get_tasks", "list_tasks"]),
])
def test_read_actions_follow_declared_page_kind_without_url_or_plural_guessing(page_type, expected):
    plan = _plan()
    plan["pages"] = [{"name": "tasks", "primary_entities": ["Task"], "page_type_hint": page_type}]
    context = ContextVariablesBridge({"structured_output": _output(), "app_build_plan": plan})
    actions = _closed(context)["module_contract"]["module_yaml"]["actions"][3:]
    assert [action["id"] for action in actions] == expected
    detail = next((action for action in actions if action["id"] == "get_tasks"), None)
    if detail:
        assert detail["input_schema"]["properties"][0]["name"] == "id"
        assert detail["input_schema"]["properties"][0]["required"] is True


def test_preserves_existing_read_contract_security_and_custom_behavior():
    output = _output()
    existing = {"id": "list_tasks", "handler_method": "custom_list", "permissions": ["task_management.read"],
                "entitlement_gate": "task_management.reporting", "api_surface": "internal"}
    output["module_contract"]["module_yaml"]["actions"].append(existing)
    assert close_module_read_actions(output, app_build_plan=_plan()) == output


@pytest.mark.parametrize("restriction", [
    {"permissions": ["task_management.admin"]}, {"entitlement_gate": "task_management.reporting"},
    {"api_surface": "internal"}, {"api_surface": "admin_internal"},
])
def test_read_construction_cannot_widen_an_existing_access_boundary(restriction):
    output = _output()
    output["module_contract"]["module_yaml"]["actions"][0].update(restriction)
    with pytest.raises(ValueError, match="explicitly declare 'list_tasks' and its access policy"):
        close_module_read_actions(output, app_build_plan=_plan())


def test_declared_noncanonical_source_does_not_invent_read_actions():
    plan = _plan()
    for page in plan["pages"]:
        for hint in page["sections_hint"]:
            hint["data_source"] = {"module_id": "task_management", "action_id": "summarize_tasks"}
    output = _output()
    assert close_module_read_actions(output, app_build_plan=plan) == output


def test_read_closure_requires_exact_data_ownership():
    plan = _plan()
    plan["data_contract"]["surfaces"][0]["collections"][0]["ownership"]["surface_id"] = "other"
    with pytest.raises(ValueError, match="canonical collection name and module ownership"):
        close_module_read_actions(_output(), app_build_plan=plan)
    plan["data_contract"] = None
    plan["pages"] = [{"primary_entities": ["Task"], "page_type_hint": "record_list"}]
    with pytest.raises(ValueError, match="owned collections before read actions"):
        close_module_read_actions(_output(), app_build_plan=plan)


def test_multiple_collection_entity_mapping_requires_an_exact_reference():
    plan = _plan()
    collections = plan["data_contract"]["surfaces"][0]["collections"]
    other = deepcopy(collections[0])
    other["name"] = "task_comments"
    collections.append(other)
    plan["pages"] = [{"name": "tasks", "primary_entities": ["Task"], "page_type_hint": "record_list"}]
    with pytest.raises(ValueError, match="must name an owned collection"):
        close_module_read_actions(_output(), app_build_plan=plan)
    plan["pages"][0]["sections_hint"] = [{"primitive": "DataTable", "data_source": {
        "module_id": "task_management", "action_id": "list_tasks",
    }}]
    actions = close_module_read_actions(_output(), app_build_plan=plan)["module_contract"]["module_yaml"]["actions"]
    assert [action["id"] for action in actions][3:] == ["list_tasks"]


def test_write_only_entity_surface_does_not_require_or_invent_read_contract():
    plan = _plan()
    plan["data_contract"] = None
    plan["pages"] = [{"primary_entities": ["Task"], "page_type_hint": "wizard",
                      "sections_hint": [{"primitive": "Form"}]}]
    assert close_module_read_actions(_output(), app_build_plan=plan) == _output()


def test_nonpersistent_summary_action_does_not_invent_persistence_ownership():
    plan = _plan()
    plan["data_contract"] = None
    plan["pages"] = [{"primary_entities": ["Task"], "page_type_hint": "analytics_dashboard",
                      "sections_hint": [{"primitive": "SummaryStrip", "data_source": {
                          "module_id": "task_management", "action_id": "get_wallet_summary",
                      }}]}]
    output = _output()
    output["module_contract"]["module_yaml"]["actions"].append({
        "id": "get_wallet_summary", "handler_method": "get_wallet_summary",
    })
    assert close_module_read_actions(output, app_build_plan=plan) == output


def test_assembly_materializes_same_read_contract_and_is_idempotent():
    files = extract_code_file_map_from_payload(_output())
    changes = materialize_module_read_actions(files, app_build_plan=_plan())
    files.update(changes)
    assert list(changes) == ["modules/task_management/module.yaml"]
    assert files == extract_code_file_map_from_payload(close_module_read_actions(_output(), app_build_plan=_plan()))
    assert materialize_module_read_actions(files, app_build_plan=_plan()) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("typed_output", [True, False])
async def test_admitted_read_action_reaches_service_dependency_and_saved_bridge(monkeypatch, typed_output):
    contract_task = {
        "task_id": "task_contract", "task_type": "module_contract", "capability_pack_id": "task_management",
        "initial_agent": "ConfigMiddlewareAgent", "initial_message": "Declare task actions.",
        "owned_paths": ["modules/task_management/module.yaml"], "depends_on": [],
    }
    service_task = {
        "task_id": "task_service", "task_type": "business_services", "capability_pack_id": "task_management",
        "initial_agent": "ServiceAgent", "initial_message": "Implement the declared task actions.",
        "owned_paths": ["modules/task_management/backend/handler.py"], "depends_on": ["task_contract"],
    }
    bridge = ContextVariablesBridge({"app_build_plan": _plan(), "app_task_batch_items": [contract_task, service_task]})
    saved_contracts = []

    async def run(_runner, request):
        worker = ContextVariablesBridge(request.context_variables)
        if request.task_id == "task_contract":
            output = _output()
            if not typed_output:
                output = {"code_files": [{"filename": path, "content": content}
                                         for path, content in extract_code_file_map_from_payload(output).items()]}
            save_generated_code(StructuredOutputOverlay(worker, output))
            saved_contracts.append(yaml.safe_load(worker.get("code_files")[0]["content"]))
        else:
            dependency = worker.get("dependency_task_outputs")["task_contract"]
            if typed_output:
                assert dependency["module_contract"]["module_yaml"]["actions"][-1]["id"] == "list_tasks"
            manifest = yaml.safe_load(dependency["code_files"][0]["content"])
            assert manifest["actions"][-1]["handler_method"] == "list_tasks"
            output = {"code_files": [{"filename": service_task["owned_paths"][0], "content": (
                "class TaskManagementHandler:\n"
                "    async def list_tasks(self, ctx, **params):\n"
                "        return await self.service.list_tasks(ctx, **params)\n"
            )}]}
        return AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output=output)

    monkeypatch.setattr(task_batches.AG2TaskBatchRunner, "run", run)
    config = task_batches.load_task_batches_config(
        "AppGenerator", workflows_root=Path(__file__).resolve().parents[1] / "factory_app" / "workflows",
    )

    async def checkpoint(updates):
        for key, value in updates.items():
            bridge.set(key, value)

    snapshot = bridge.snapshot()
    await task_batches.execute_task_batches_for_trigger(
        workflow_name="AppGenerator", trigger_agent="AppPlanAgent", batches_config=config,
        agents={"ConfigMiddlewareAgent": object(), "ServiceAgent": object()}, context_variables=snapshot,
        chat_id="read-actions", app_id="read-actions", user_id="user-1", fresh_agents_per_task=False,
        parent_channel_id="read-actions-parent", checkpoint=checkpoint,
    )
    assert snapshot["app_task_batch_status"] == "completed", snapshot["app_task_batch_results"]
    assert len(saved_contracts) == 1
    assert saved_contracts[0]["actions"][-1]["id"] == "list_tasks"


def test_plan_closes_each_owned_pages_exact_source_dependency_with_real_bridge():
    from tests.test_appgenerator_task_batch_contracts import _base_plan

    plan = _base_plan()
    plan["pages"][0]["sections_hint"] = [
        {"primitive": "DataTable", "data_source": {"module_id": "task_management", "action_id": "list_tasks"}},
        {"primitive": "SummaryStrip", "data_source": {"module_id": "projects", "action_id": "project_summary"}},
    ]
    plan["pages"].append({
        "name": "Archive", "route": "/archive", "purpose": "Read archives", "sections_hint": [{
            "primitive": "DataTable", "data_source": {"module_id": "archive", "action_id": "list_archives"},
        }],
    })
    for module_id in ("task_management", "projects", "archive"):
        plan["build_tasks"].append({
            "task_id": f"contract_{module_id}", "task_type": "module_contract", "capability_pack_id": module_id,
            "surface_id": module_id, "surface_kind": "module", "execution_target": "AppGenerator",
            "initial_agent": "ConfigMiddlewareAgent", "initial_message": "Declare module actions.",
            "description": "Declare module actions.", "owned_paths": [f"modules/{module_id}/module.yaml"],
            "depends_on": [], "acceptance_criteria": [],
        })
    bridge = ContextVariablesBridge({})
    app_build_plan(AppBuildPlan=plan, context_variables=bridge)
    page_task = next(task for task in bridge.get("app_task_batch_items") if task["task_type"] == "page_bundle")
    assert list(page_task["depends_on"]) == ["task_service_foundation", "contract_task_management", "contract_projects"]
    assert "contract_archive" not in page_task["depends_on"]

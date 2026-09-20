"""Regressions discovered by running real Factory workers against the runtime."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_build_plan import app_build_plan
from factory_app.workflows.AppGenerator.tools.code_file_utils import (
    admitted_app_file_map,
    save_generated_code,
)
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
    _scan_page_api_endpoint_alignment,
    _scan_planned_data_fields,
)
from factory_app.workflows.AppGenerator.tools.hook_file_contract_context import (
    inject_cookie_cutter_contracts_context,
)
from factory_app.workflows.AppGenerator.tools.module_runtime_quality import (
    review_module_runtime_quality,
)
from factory_app.workflows.AppGenerator.tools.repair_policy import prepare_bundle_repair
from mozaiksai.core.events import auto_tool_handler
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
from scripts.appgenerator_fixture_replay import execute_file_replay
from scripts.smoke_appgenerator_live_acceptance import SmokeContext


def _repair_context(task_type, agent, files):
    context = SmokeContext({"app_id": "contract-regression", "generated_files": files})
    task = {
        "task_id": "fixture_task", "task_type": task_type, "initial_agent": agent,
        "capability_pack_id": None if task_type == "persistence_contract" else "contacts",
        "surface_id": "contacts", "surface_kind": "module", "execution_target": "AppGenerator",
        "description": "Emit the assigned regression fixture.", "initial_message": "Emit the assigned fixture files.",
        "owned_paths": list(files), "depends_on": [], "acceptance_criteria": ["Preserve task ownership."],
    }
    app_build_plan(AppBuildPlan={
        "app_kind": "internal_app", "auth_strategy": "none", "roles": [], "entities": [],
        "build_tasks": [task], "pages": [{"name": "Contacts", "route": "/contacts", "purpose": "Review contacts."}],
        "capability_packs": [],
    }, context_variables=context)
    asyncio.run(execute_file_replay(context.data, files))
    return context.data


def test_action_surface_null_is_not_a_string_literal():
    from pydantic import ValidationError

    from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
        _scan_action_api_surface,
    )
    from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs

    models, _ = load_workflow_structured_outputs("AppGenerator")
    action = {"id": "create", "description": "Create", "handler_method": "create",
              "input_schema": {"type": "object"}, "output_schema": {"type": "object"}}
    assert models["ModuleAction"].model_validate({**action, "api_surface": None}).api_surface is None
    with pytest.raises(ValidationError):
        models["ModuleAction"].model_validate({**action, "api_surface": "null"})
    assert _scan_action_api_surface({
        "modules/records/module.yaml": yaml.safe_dump({"actions": [{**action, "api_surface": "null"}]}),
    })


@pytest.mark.parametrize("surface", ["internal", "admin_internal", None])
def test_page_http_binding_rejects_internal_actions_and_routes_to_contract_owner(surface):
    files = {
        "modules/contacts/module.yaml": yaml.safe_dump({"actions": [{"id": "create", "api_surface": surface}]}),
        "ui/pages/contacts.yaml": yaml.safe_dump({"sections": [{"config": {"children": [{
            "primitive": "Form", "config": {"submit_action": {
                "href": "/api/modules/contacts/create",
            }},
        }]}}]}),
    }
    errors = _scan_page_api_endpoint_alignment(files)
    if surface is None:
        assert errors == []
    else:
        assert len(errors) == 1
        assert "internal-only" in errors[0]
        path = "modules/contacts/module.yaml"
        context = _repair_context("module_contract", "ConfigMiddlewareAgent", {path: files[path]})
        repair = prepare_bundle_repair({"passed": False, "errors": errors}, context)
        assert repair["target_agent"] == "ConfigMiddlewareAgent"
        assert repair["active"]["allowed_paths"] == [path]


def test_service_worker_receives_runtime_and_account_protocol_with_protected_context():
    agent = SimpleNamespace(
        name="ServiceAgent", system_message="Worker instructions",
        context_variables=ContextVariablesBridge({"current_build_task": {
            "owned_paths": ["modules/contacts/backend/account_data_handler.py"],
        }}),
    )
    agent.update_system_message = lambda message: setattr(agent, "system_message", message)
    inject_cookie_cutter_contracts_context(agent, [])
    assert "async find_many(" in agent.system_message
    assert "async count(" in agent.system_message
    assert "Required account-data runtime contract:" in agent.system_message
    assert "delete_user_data" in agent.system_message
    assert "__init__(self, db: Any)" in agent.system_message
    assert "Worker instructions" in agent.system_message


@pytest.mark.parametrize("method", ["find", "count_documents", "find_one_and_update", "to_list"])
def test_protected_runtime_audit_rejects_motor_api_and_routes_to_service(method):
    path = "modules/contacts/backend/repo.py"
    files = {path: f"async def fetch(ctx):\n    collection = ctx.persistence.collection('contacts', 'contacts')\n    return await collection.{method}({{}})\n"}
    evidence = _repair_context("business_services", "ServiceAgent", files)
    context = ContextVariablesBridge({**evidence, "code_files": [{"filename": path, "content": files[path]}]})
    result = review_module_runtime_quality(context_variables=context)
    assert result["status"] == "needs_revision"
    assert result["module_backend_file_count"] == 1
    repair = prepare_bundle_repair({"passed": False, "errors": result["warnings"]}, context)
    assert repair["target_agent"] == "ServiceAgent"


def test_runtime_account_load_failure_routes_to_service():
    path = "modules/contacts/backend/account_data_handler.py"
    context = _repair_context("business_services", "ServiceAgent", {path: "class AccountDataHandler: pass\n"})
    repair = prepare_bundle_repair({"passed": False, "errors": [
        f"{path}: invalid account-data implementation"
    ]}, context)
    assert repair["target_agent"] == "ServiceAgent"


def test_generated_repair_persists_typed_files_without_any_chat_history():
    path = "modules/contacts/backend/repo.py"
    evidence = _repair_context("business_services", "ServiceAgent", {path: "old"})
    base = ContextVariablesBridge({**evidence, "code_files": [
        {"filename": path, "content": "old"},
        {"filename": "modules/contacts/backend/unused.py", "content": "old"},
    ]})
    prepare_bundle_repair({"passed": False, "errors": [f"{path}: invalid persistence API"]}, base)
    payload = {"python_files": [{"path": path, "content": "corrected"}],
               "code_files": [{"filename": path, "content": "stale mirror"}]}
    result = save_generated_code(StructuredOutputOverlay(base, payload))
    assert result["saved_files"] == [path]
    assert base.snapshot()["code_files"] == [
        {"filename": path, "content": "corrected"},
        {"filename": "modules/contacts/backend/unused.py", "content": "old"},
    ]


def test_generated_repair_cannot_delete_an_unowned_file():
    path = "modules/contacts/backend/repo.py"
    foreign = "modules/contacts/backend/unused.py"
    context = _repair_context("business_services", "ServiceAgent", {path: "old"})
    context["generated_files"][foreign] = "preserved"
    before = dict(context["generated_files"])
    base = ContextVariablesBridge(context)
    prepare_bundle_repair({"passed": False, "errors": [f"{path}: invalid persistence API"]}, base)
    result = save_generated_code(StructuredOutputOverlay(base, {
        "python_files": [{"path": path, "content": "corrected"}], "deleted_files": [foreign],
    }))
    assert result["status"] == "rejected"
    assert "outside owned_paths" in result["error"]
    assert base.snapshot()["generated_files"] == before
    assert not base.snapshot().get("deleted_files")


def test_generated_repair_can_delete_an_owned_optional_companion():
    module_path = "modules/contacts/module.yaml"
    optional_path = "modules/contacts/contracts/notifications.yaml"
    foreign = "modules/contacts/backend/repo.py"
    owned_files = {
        module_path: "module_id: contacts\nactions: []\n",
        optional_path: "notifications: []\n",
    }
    context = _repair_context("module_contract", "ConfigMiddlewareAgent", owned_files)
    context["generated_files"][foreign] = "preserved"
    context["code_files"] = [
        {"filename": path, "content": content}
        for path, content in context["generated_files"].items()
    ]
    base = ContextVariablesBridge(context)
    accepted_results = base.snapshot()["app_task_batch_results"]
    repair = prepare_bundle_repair({"passed": False, "errors": [
        f"{optional_path}: remove the unused optional notifications contract",
    ]}, base)
    assert repair["target_agent"] == "ConfigMiddlewareAgent"
    assert optional_path in repair["active"]["allowed_paths"]

    result = save_generated_code(StructuredOutputOverlay(base, {"deleted_files": [optional_path]}))

    assert result == {"saved_files": [], "deleted_files": [optional_path]}
    assert base.snapshot()["deleted_files"] == [optional_path]
    assert admitted_app_file_map(base) == {module_path: owned_files[module_path], foreign: "preserved"}
    assert base.snapshot()["app_task_batch_results"] == accepted_results


def test_data_contract_failure_has_explicit_database_repair_owner():
    context = _repair_context("persistence_contract", "DatabaseAgent", {"data/contract.json": "{}"})
    repair = prepare_bundle_repair({"passed": False, "errors": [
        "data/contract.json: approved optional field became required"
    ]}, context)
    assert repair["target_agent"] == "DatabaseAgent"


def test_data_contract_cannot_drop_fields_or_make_optional_email_required():
    planned = {"surfaces": [{"surface_id": "contacts", "collections": [{"name": "contacts", "fields": [
        {"name": "email", "type": "string", "required": False},
        {"name": "notes", "type": "string", "required": False},
    ]}]}]}
    actual = json.loads(json.dumps(planned))
    actual["surfaces"][0]["collections"][0]["fields"] = [{"name": "email", "type": "string", "required": True}]
    errors = _scan_planned_data_fields({"data/contract.json": json.dumps(actual)}, planned)
    assert len(errors) == 2
    assert any("email" in error for error in errors)
    assert any("notes" in error for error in errors)
    assert _scan_planned_data_fields({"data/contract.json": json.dumps(planned)}, planned) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("self_emits_ui", [True, False])
async def test_auto_tool_does_not_mount_tool_owned_ui_before_export(monkeypatch, self_emits_ui):
    transport = SimpleNamespace(send_event_to_ui=AsyncMock())
    monkeypatch.setattr(auto_tool_handler, "_get_simple_transport", AsyncMock(return_value=transport))
    binding = auto_tool_handler.AutoToolBinding(
        model_name="Export", agent_name="ExportAgent", tool_name="export_bundle",
        function=lambda: None, param_names=(), accepts_context=False,
        ui_config={"component": "Workbench"}, model_cls=dict,
        self_emits_ui=self_emits_ui,
    )
    await auto_tool_handler.AutoToolEventHandler()._emit_tool_call(
        binding, "ExportAgent", "chat", {"agent_message": "Ready to download"}, "turn",
    )
    kinds = [call.args[0]["kind"] for call in transport.send_event_to_ui.await_args_list]
    assert kinds == (["select_speaker"] if self_emits_ui else ["select_speaker", "tool_call"])

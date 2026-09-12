"""Regressions discovered by running real Factory workers against the runtime."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_validation import _prepare_bundle_repair
from factory_app.workflows.AppGenerator.tools.code_file_utils import save_generated_code
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
from mozaiksai.core.events import auto_tool_handler
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay


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
    errors = _scan_page_api_endpoint_alignment({
        "modules/contacts/module.yaml": yaml.safe_dump({"actions": [{"id": "create", "api_surface": surface}]}),
        "ui/pages/contacts.yaml": yaml.safe_dump({"sections": [{"config": {"children": [{
            "primitive": "Form", "config": {"submit_action": {
                "href": "/api/modules/contacts/create",
            }},
        }]}}]}),
    })
    if surface is None:
        assert errors == []
    else:
        assert len(errors) == 1
        assert "internal-only" in errors[0]
        assert _prepare_bundle_repair({"passed": False, "errors": errors}, {})["target_agent"] == "ConfigMiddlewareAgent"


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
    context = ContextVariablesBridge({"code_files": [{
        "filename": "modules/contacts/backend/repo.py",
        "content": f"async def fetch(ctx):\n    collection = ctx.persistence.collection('contacts', 'contacts')\n    return await collection.{method}({{}})\n",
    }]})
    result = review_module_runtime_quality(context_variables=context)
    assert result["status"] == "needs_revision"
    assert result["module_backend_file_count"] == 1
    repair = _prepare_bundle_repair({"passed": False, "errors": result["warnings"]}, {})
    assert repair["target_agent"] == "ServiceAgent"


def test_runtime_account_load_failure_routes_to_service():
    repair = _prepare_bundle_repair({"passed": False, "errors": [
        "modules/contacts/backend/account_data_handler.py: invalid account-data implementation"
    ]}, {})
    assert repair["target_agent"] == "ServiceAgent"


def test_generated_repair_persists_typed_files_without_any_chat_history():
    path = "modules/contacts/backend/repo.py"
    base = ContextVariablesBridge({"code_files": [
        {"filename": path, "content": "old"},
        {"filename": "modules/contacts/backend/unused.py", "content": "old"},
    ]})
    payload = {"python_files": [{"path": path, "content": "corrected"}],
               "code_files": [{"filename": path, "content": "stale mirror"}],
               "deleted_files": ["modules/contacts/backend/unused.py"]}
    result = save_generated_code(StructuredOutputOverlay(base, payload))
    assert result["saved_files"] == [path]
    assert base.snapshot()["code_files"] == [{"filename": path, "content": "corrected"}]


def test_data_contract_failure_has_explicit_database_repair_owner():
    repair = _prepare_bundle_repair({"passed": False, "errors": [
        "data/contract.json: approved optional field became required"
    ]}, {})
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

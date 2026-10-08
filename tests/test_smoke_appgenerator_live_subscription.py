from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

import scripts.smoke_appgenerator_live_subscription as smoke_script
from factory_app.workflows._shared.subscription_contract_context import (
    subscription_assignment_store,
)
from factory_app.workflows.AppGenerator.tools import app_runtime_smoke, app_validation
from scripts.smoke_appgenerator_live_subscription import (
    REPORT_GATE_ID,
    WORKFLOWS_ROOT,
    assembled_subscription_yaml,
    deterministic_module_contract_output,
    run_deterministic_appgenerator_subscription_smoke,
    sample_subscription_contract,
    validate_assembled_subscription_yaml,
    validate_module_contract_output,
    validate_subscription_acceptance_handoff,
)
from tests.import_utils import import_module_directly

_workflow_manager_mod = import_module_directly("mozaiksai.core.workflow.workflow_manager")
_structured_mod = import_module_directly("mozaiksai.core.workflow.outputs.structured")


def _load_appgenerator_structured_registry():
    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    _workflow_manager_mod.initialize_workflows(base_path=str(WORKFLOWS_ROOT))
    _structured_mod._workflow_models.clear()
    _structured_mod._workflow_registries.clear()
    _structured_mod._workflow_structured_agents.clear()
    _structured_mod._provider_response_model_cache.clear()
    _models, registry = _structured_mod.load_workflow_structured_outputs("AppGenerator")
    return registry


def test_assembled_subscription_yaml_is_the_contract_with_the_constructed_store() -> None:
    contract = sample_subscription_contract()
    content = assembled_subscription_yaml(contract)

    assert validate_assembled_subscription_yaml(content, contract) == []
    assert yaml.safe_load(content)["assignment_store"] == subscription_assignment_store()

    drifted = content.replace("default_plan_id: free", "default_plan_id: pro")
    assert validate_assembled_subscription_yaml(drifted, contract)


def test_module_contract_validator_compiles_the_exact_approved_entitlement_gate() -> None:
    output = deterministic_module_contract_output()
    output["module_contract"]["module_yaml"]["actions"][1]["entitlement_gate"] = "reports.pro"

    content, errors = validate_module_contract_output(output)

    assert not errors
    assert yaml.safe_load(content)["actions"][1]["entitlement_gate"] == REPORT_GATE_ID
    assert output["module_contract"]["module_yaml"]["actions"][1]["entitlement_gate"] == "reports.pro"


def test_module_contract_validator_rejects_structured_output_schema_drift() -> None:
    output = deterministic_module_contract_output()
    output.pop("agent_message")
    output["_schema_validation_error"] = "agent_message field required"

    _content, errors = validate_module_contract_output(output)

    assert errors
    assert any("structured output failed ConfigMiddlewareOutput validation" in error for error in errors)
    assert any("agent_message" in error for error in errors)


@pytest.mark.parametrize("missing_field", ["items", "total"])
def test_module_contract_validator_rejects_missing_list_response_fields(missing_field: str) -> None:
    output = deterministic_module_contract_output()
    list_action = next(action for action in output["module_contract"]["module_yaml"]["actions"] if action["id"] == "list_reports")
    list_action["output_schema"]["properties"].pop(missing_field)

    _content, errors = validate_module_contract_output(output)

    assert any(f"list_reports output_schema must declare {missing_field}" in error for error in errors)


def test_module_contract_validator_rejects_missing_form_input() -> None:
    output = deterministic_module_contract_output()
    generate = next(action for action in output["module_contract"]["module_yaml"]["actions"] if action["id"] == "generate_report")
    generate["input_schema"]["properties"] = {}

    _content, errors = validate_module_contract_output(output)

    assert "generate_report input_schema must declare a required topic string for the Reports form." in errors


def test_model_schema_compiles_to_the_report_metric_binding() -> None:
    output = deterministic_module_contract_output()
    output["code_files"] = []
    action = output["module_contract"]["module_yaml"]["actions"][0]
    action["output_schema"] = {
        "type": "object", "description": None, "items_type": None,
        "properties": [
            {"name": "items", "type": "array", "required": True, "items_type": "object", "description": None, "enum_values": None},
            {"name": "total", "type": "integer", "required": True, "items_type": None, "description": None, "enum_values": None},
        ],
    }
    generate = output["module_contract"]["module_yaml"]["actions"][1]
    generate["input_schema"] = {
        "type": "object", "description": None, "items_type": None,
        "properties": [
            {"name": "topic", "type": "string", "required": True, "items_type": None, "description": None, "enum_values": None},
        ],
    }

    content, errors = validate_module_contract_output(output)

    assert not errors
    assert yaml.safe_load(content)["actions"][0]["output_schema"]["properties"] == {
        "items": {"type": "array", "items": {"type": "object"}}, "total": {"type": "integer"},
    }
    assert yaml.safe_load(content)["actions"][1]["input_schema"]["required"] == ["topic"]


def test_config_middleware_rejects_uppercase_action_description() -> None:
    registry = _load_appgenerator_structured_registry()
    output = deterministic_module_contract_output()
    for action in output["module_contract"]["module_yaml"]["actions"]:
        for key in ("input_schema", "output_schema"):
            schema = action.get(key)
            if not isinstance(schema, dict) or not isinstance(schema.get("properties"), dict):
                continue
            required = set(schema.pop("required", []))
            schema["properties"] = [
                {
                    "name": name, "type": prop["type"], "required": name in required,
                    "items_type": (prop.get("items") or {}).get("type"),
                    "description": None, "enum_values": None,
                }
                for name, prop in schema["properties"].items()
            ]
            schema["items_type"] = None
            schema["description"] = None
    output = registry["ConfigMiddlewareAgent"].model_validate(output).model_dump(mode="json")
    action = output["module_contract"]["module_yaml"]["actions"][0]
    action["Description"] = action.pop("description")

    coerced = smoke_script._coerce_structured_output(
        output, registry["ConfigMiddlewareAgent"]
    )

    assert "_schema_validation_error" in coerced
    assert "description" in coerced["_schema_validation_error"]


@pytest.mark.asyncio
async def test_live_task_uses_factory_response_schema_and_marks_invalid_reply_failed(monkeypatch) -> None:
    import ag2

    from mozaiksai.core.usage import middleware as usage_middleware
    from mozaiksai.core.workflow import agents as agents_module
    from mozaiksai.core.workflow import workflow_manager
    from mozaiksai.core.workflow.agents import factory as agents_factory
    from mozaiksai.core.workflow.outputs import structured as outputs_structured

    provider_schema = object()
    captured: dict[str, object] = {}

    class FakeReply:
        async def content(self):
            return {"agent_message": "invalid contract"}

    class FakeAgent:
        def __init__(self, name, **kwargs):
            captured.update(kwargs)

        async def ask(self, *args, **kwargs):
            return FakeReply()

    class InvalidSchema:
        @staticmethod
        def model_validate(_value):
            raise ValueError("description field required")

    monkeypatch.setattr(ag2, "Agent", FakeAgent)
    monkeypatch.setattr(workflow_manager, "initialize_workflows", lambda **kwargs: None)
    monkeypatch.setattr(
        agents_module, "create_agents",
        AsyncMock(return_value={"ConfigMiddlewareAgent": SimpleNamespace(response_schema=provider_schema)}),
    )
    monkeypatch.setattr(
        outputs_structured, "load_workflow_structured_outputs",
        lambda _workflow: ({}, {"ConfigMiddlewareAgent": InvalidSchema}),
    )
    monkeypatch.setattr(
        outputs_structured, "get_llm_for_workflow", AsyncMock(return_value=("test-model", {})),
    )
    monkeypatch.setattr(agents_factory, "llm_config_to_ag2_config", lambda _config: {})
    monkeypatch.setattr(
        smoke_script, "_render_agent_system_prompt", AsyncMock(return_value="system prompt"),
    )
    monkeypatch.setattr(usage_middleware, "build_ag2_usage_middleware", lambda **kwargs: object())

    result = await smoke_script._run_config_task(
        task=smoke_script._module_contract_task(),
        contract=sample_subscription_contract(),
        prompt="Generate a module contract.",
        timeout_seconds=1.0,
    )

    assert captured["response_schema"] is provider_schema
    assert result["success"] is False
    assert result["error"] == "description field required"


@pytest.mark.skipif(os.name != "nt", reason="Windows event loop policy only")
def test_live_smoke_event_loop_supports_local_build_subprocess() -> None:
    code = """
import asyncio
import sys
from scripts.smoke_appgenerator_live_subscription import _configure_event_loop_policy

_configure_event_loop_policy()

async def check():
    process = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
    assert await process.wait() == 0

asyncio.run(check())
"""
    child = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, check=False)
    assert child.returncode == 0, child.stderr


def test_config_middleware_schema_defaults_omitted_deleted_files() -> None:
    registry = _load_appgenerator_structured_registry()
    output = {
        "mode": "service_foundation",
        "module_contract": None,
        "service_foundation_bundle": {"files": []},
        "code_files": [],
        "agent_message": "No service foundation files are needed.",
    }

    assert "deleted_files" not in output

    validated = registry["ConfigMiddlewareAgent"].model_validate(output).model_dump(mode="json")

    assert validated["deleted_files"] == []


def test_config_middleware_schema_defaults_omitted_module_optional_fields() -> None:
    registry = _load_appgenerator_structured_registry()
    output = deterministic_module_contract_output()
    output["module_contract"]["notifications_yaml"] = {
        "schema_version": "mozaiks.notifications.v1",
        "notifications": [],
    }
    output["module_contract"]["admin_yaml"] = {
        "schema_version": "mozaiks.admin.v2",
        "panels": [],
        "hooks": [],
    }
    output["module_contract"]["relationships_yaml"] = None
    output["module_contract"]["policy_hooks_yaml"] = None
    module = output["module_contract"]["module_yaml"]["module"]
    actions = output["module_contract"]["module_yaml"]["actions"]
    actions[0]["input_schema"] = {
        "type": "object",
        "description": "Request to list reports.",
        "properties": [],
        "items_type": None,
    }
    actions[0]["output_schema"] = {
        "type": "object",
        "description": "Response containing report items.",
        "properties": [
            {
                "name": "items",
                "type": "array",
                "description": "Report ids.",
                "required": True,
                "enum_values": [],
                "items_type": "string",
            }
        ],
        "items_type": None,
    }
    actions[1]["input_schema"] = {
        "type": "object",
        "description": "Report generation request.",
        "properties": [
            {
                "name": "topic",
                "type": "string",
                "description": "Report topic.",
                "required": True,
                "enum_values": [],
                "items_type": None,
            }
        ],
        "items_type": None,
    }
    actions[1]["output_schema"] = {
        "type": "object",
        "description": "Generated report response.",
        "properties": [
            {
                "name": "report_id",
                "type": "string",
                "description": "Generated report id.",
                "required": True,
                "enum_values": [],
                "items_type": None,
            },
            {
                "name": "topic",
                "type": "string",
                "description": "Report topic.",
                "required": True,
                "enum_values": [],
                "items_type": None,
            },
        ],
        "items_type": None,
    }

    assert "user_data_scope" not in module
    for action in actions:
        action.pop("api_surface", None)
    assert all("api_surface" not in action for action in actions)

    validated = registry["ConfigMiddlewareAgent"].model_validate(output).model_dump(mode="json")
    validated_module = validated["module_contract"]["module_yaml"]["module"]
    validated_actions = validated["module_contract"]["module_yaml"]["actions"]

    assert validated_module["user_data_scope"] is False
    assert all(action["api_surface"] is None for action in validated_actions)


def test_module_contract_validator_uses_typed_contract_over_raw_file_drift() -> None:
    output = deterministic_module_contract_output()
    output["code_files"][0]["content"] = output["code_files"][0]["content"].replace(
        "  handler_method: generate_report",
        "handler_method: generate_report",
    )

    content, errors = validate_module_contract_output(output)

    assert not errors
    assert yaml.safe_load(content)["actions"][1]["handler_method"] == "generate_report"


@pytest.mark.asyncio
async def test_deterministic_subscription_smoke_keeps_unavailable_runtime_checks_pending(monkeypatch) -> None:
    monkeypatch.setattr(app_runtime_smoke, "resolve_smoke_mongo_uri", lambda: None)
    build = AsyncMock(side_effect=AssertionError("Incomplete runtime acceptance must not reach the build."))
    monkeypatch.setattr(app_validation, "_run_local_validation", build)
    payload = await run_deterministic_appgenerator_subscription_smoke()

    assert payload["success"] is False
    acceptance = payload["appgenerator_acceptance"]
    assert acceptance["task_batch_status"] == "completed"
    assert acceptance["failed_tasks"] == {}
    assert acceptance["accepted_task_ids"] == [
        "entitlement_contract", "entitlement_services", "reports_models", "reports_services",
        "subscription_pages", "subscription_persistence", "task_reports_module_contract",
    ]
    assert {
        f"modules/reports/contracts/{name}.yaml"
        for name in ("admin", "events", "notifications", "reactions", "settings")
    }.issubset(acceptance["generated_paths"])
    assert acceptance["acceptance"]["status"] == "pending"
    assert acceptance["acceptance"]["passed"] is False
    assert acceptance["acceptance"]["validation_evidence"]["failed"] == []
    assert acceptance["acceptance"]["validation_evidence"]["skipped"] == ["app_runtime_smoke"]
    assert "snapshot_digest" not in acceptance["acceptance"]
    assert acceptance["app_validation_result"]["validation_status"] == "pending"
    assert acceptance["context"]["integration_tests_passed"] is False
    assert acceptance["export_gate"]["allow_export"] is False
    build.assert_not_awaited()
    assert acceptance["runtime_loader"]["subscriptions_loaded"] is True
    assert acceptance["runtime_loader"]["action_entitlements"]["generate_report"] == REPORT_GATE_ID

    generated = acceptance["context"]["generated_files"]
    assert acceptance["context"]["app_assembly_status"] == "passed"
    report_page = yaml.safe_load(generated["ui/pages/reports.yaml"])
    assert report_page["sections"][0]["config"]["value_key"] == "total"
    assert report_page["sections"][0]["primitive"] == "Metric"
    assert report_page["sections"][1]["config"]["fields"][0]["name"] == "topic"
    assert 'return {"items": items, "total": len(items)}' in generated["modules/reports/backend/service.py"]
    subscriptions = yaml.safe_load(generated["config/subscriptions.yaml"])
    assert subscriptions["assignment_store"] == subscription_assignment_store()
    plans = subscriptions["plans"]
    assert [plan["capabilities"] for plan in plans] == [[], [REPORT_GATE_ID]]
    assert "usage_limits" not in plans[0]
    assert plans[1]["usage_limits"][0]["monthly_limit"] == 1000
    assert json.loads(generated["app.json"])["authRequired"] is True
    assert {"config/auth.yaml", "ui/auth/authAdapter.js"} <= generated.keys()
    report_collection = json.loads(generated["data/contract.json"])["surfaces"][0]["collections"][0]
    assert (report_collection["scope"], report_collection["tenancy"], report_collection["owner_field"]) == ("app", "app_wide", None)
    policy = generated["modules/reports/backend/policy.py"]
    assert "Ownership preflight compiled from data/contract.json" in policy
    assert "ReportsPolicy" not in policy

    details = acceptance["wiring"]["checks"][0]["details"]
    assert details["platform_endpoint_count"] == 3
    assert details["wired_count"] == 2


@pytest.mark.asyncio
async def test_subscription_export_requires_completed_runtime_and_build_checks(monkeypatch):
    # Explicit execution fixtures for this unit test; the canonical validator
    # still writes acceptance, build and integration evidence into context.
    smoke = AsyncMock(return_value={
        "status": "passed", "passed": True, "failed_tests": [], "checks": [],
    })
    build = AsyncMock(return_value=app_validation._base_result(strategy="local", status="passed"))
    monkeypatch.setattr(app_runtime_smoke, "run_app_runtime_smoke", smoke)
    monkeypatch.setattr(app_validation, "_run_local_validation", build)

    payload = await run_deterministic_appgenerator_subscription_smoke()

    assert payload["success"] is True, payload["validation_errors"]
    result = payload["appgenerator_acceptance"]
    assert result["acceptance"]["status"] == "passed"
    assert result["app_validation_result"]["validation_status"] == "passed"
    assert result["context"]["app_validation_strategy_used"] == "local"
    assert result["context"]["integration_tests_passed"] is True
    assert result["export_gate"]["allow_export"] is True
    smoke.assert_awaited_once()
    build.assert_awaited_once()


@pytest.mark.asyncio
async def test_subscription_handoff_retains_invalid_companion_for_acceptance() -> None:
    module = deterministic_module_contract_output()
    module_yaml, errors = validate_module_contract_output(module)
    assert not errors and module_yaml is not None
    module["module_contract"]["notifications_yaml"] = {
        "schema_version": "mozaiks.notifications.v1", "rules": [],
    }

    result = await validate_subscription_acceptance_handoff(
        module_yaml=module_yaml,
        task_outputs={"task_reports_module_contract": module},
    )

    assert result["task_batch_status"] == "completed"
    assert "modules/reports/contracts/notifications.yaml" in result["generated_paths"]
    assert result["acceptance"]["passed"] is False
    assert result["export_gate"]["allow_export"] is False
    assert any(
        "Invalid notifications.yaml" in failure["error"] and "rules" in failure["error"]
        for failure in result["acceptance"]["failed_tests"]
    )

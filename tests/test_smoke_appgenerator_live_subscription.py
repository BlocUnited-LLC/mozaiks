from __future__ import annotations

import pytest

from scripts.smoke_appgenerator_live_subscription import (
    WORKFLOWS_ROOT,
    deterministic_module_contract_output,
    deterministic_subscription_output,
    run_deterministic_appgenerator_subscription_smoke,
    sample_subscription_contract,
    validate_module_contract_output,
    validate_subscription_output,
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


def test_subscription_output_validator_rejects_provider_specific_drift() -> None:
    contract = sample_subscription_contract()
    output = deterministic_subscription_output()
    output["code_files"][0]["content"] += "\npayment_provider_price_id: price_123\n"
    output["subscription_config_bundle"]["files"][0]["content"] = output["code_files"][0]["content"]

    _content, errors = validate_subscription_output(output, contract)

    assert errors
    assert any("payment_provider" in error for error in errors)


def test_module_contract_validator_requires_exact_entitlement_gate() -> None:
    output = deterministic_module_contract_output()
    output["code_files"][0]["content"] = output["code_files"][0]["content"].replace(
        "entitlement_gate: reports.generate",
        "entitlement_gate: reports.pro",
    )

    _content, errors = validate_module_contract_output(output)

    assert errors
    assert any("reports.generate" in error for error in errors)


def test_module_contract_validator_rejects_structured_output_schema_drift() -> None:
    output = deterministic_module_contract_output()
    output.pop("agent_message")
    output["_schema_validation_error"] = "agent_message field required"

    _content, errors = validate_module_contract_output(output)

    assert errors
    assert any("structured output failed ConfigMiddlewareOutput validation" in error for error in errors)
    assert any("agent_message" in error for error in errors)


def test_config_middleware_schema_defaults_omitted_deleted_files() -> None:
    registry = _load_appgenerator_structured_registry()
    output = deterministic_subscription_output()

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
        "required": [],
        "properties": [],
        "items_type": None,
    }
    actions[0]["output_schema"] = {
        "type": "object",
        "description": "Response containing reports.",
        "required": ["reports"],
        "properties": [
            {
                "name": "reports",
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
        "required": ["topic"],
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
        "required": ["report_id", "topic"],
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
    assert all("api_surface" not in action for action in actions)

    validated = registry["ConfigMiddlewareAgent"].model_validate(output).model_dump(mode="json")
    validated_module = validated["module_contract"]["module_yaml"]["module"]
    validated_actions = validated["module_contract"]["module_yaml"]["actions"]

    assert validated_module["user_data_scope"] is False
    assert all(action["api_surface"] is None for action in validated_actions)


def test_module_contract_validator_rejects_action_field_indentation_drift() -> None:
    output = deterministic_module_contract_output()
    output["code_files"][0]["content"] = output["code_files"][0]["content"].replace(
        "    entitlement_gate: reports.generate",
        "entitlement_gate: reports.generate",
    )

    _content, errors = validate_module_contract_output(output)

    assert errors
    assert any("not valid YAML" in error for error in errors)


@pytest.mark.asyncio
async def test_deterministic_subscription_smoke_validates_acceptance_loader_and_wiring() -> None:
    payload = await run_deterministic_appgenerator_subscription_smoke()

    assert payload["success"] is True
    acceptance = payload["appgenerator_acceptance"]
    assert acceptance["acceptance"]["passed"] is True
    assert acceptance["export_gate"]["allow_export"] is True
    assert acceptance["runtime_loader"]["subscriptions_loaded"] is True
    assert acceptance["runtime_loader"]["action_entitlements"]["generate_report"] == "reports.generate"

    details = acceptance["wiring"]["checks"][0]["details"]
    assert details["platform_endpoint_count"] == 3
    assert details["wired_count"] == 2

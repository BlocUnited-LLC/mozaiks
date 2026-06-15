from __future__ import annotations

import pytest

from scripts.smoke_appgenerator_live_subscription import (
    deterministic_module_contract_output,
    deterministic_subscription_output,
    run_deterministic_appgenerator_subscription_smoke,
    sample_subscription_contract,
    validate_module_contract_output,
    validate_subscription_output,
)


def test_subscription_output_validator_rejects_provider_specific_drift() -> None:
    contract = sample_subscription_contract()
    output = deterministic_subscription_output()
    output["code_files"][0]["content"] += "\nstripe_price_id: price_123\n"
    output["subscription_config_bundle"]["files"][0]["content"] = output["code_files"][0]["content"]

    _content, errors = validate_subscription_output(output, contract)

    assert errors
    assert any("stripe" in error for error in errors)


def test_module_contract_validator_requires_exact_entitlement_gate() -> None:
    output = deterministic_module_contract_output()
    output["code_files"][0]["content"] = output["code_files"][0]["content"].replace(
        "entitlement_gate: reports.generate",
        "entitlement_gate: reports.pro",
    )

    _content, errors = validate_module_contract_output(output)

    assert errors
    assert any("reports.generate" in error for error in errors)


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

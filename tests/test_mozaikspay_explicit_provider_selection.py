from __future__ import annotations

import pytest

from factory_app.workflows.AppGenerator.tools.app_build_plan import app_build_plan


class _Context:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def get(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)

    def set(self, key: str, value: object) -> None:
        self.values[key] = value


def _subscription_task() -> dict[str, object]:
    return {
        "task_id": "task_subscription_config",
        "task_type": "subscription_config",
        "capability_pack_id": None,
        "surface_id": "subscription_contract",
        "surface_kind": "app_policy",
        "initial_agent": "ConfigMiddlewareAgent",
        "owned_paths": ["config/subscriptions.yaml"],
    }


def _base_plan(**overrides: object) -> dict[str, object]:
    plan: dict[str, object] = {
        "agent_message": "Plan ready.",
        "app_kind": "saas",
        "pages": [{"name": "Home", "route": "/", "purpose": "Home"}],
        "capability_packs": [],
        "build_tasks": [_subscription_task()],
    }
    plan.update(overrides)
    return plan


def _mozaikspay_pack() -> dict[str, object]:
    return {
        "capability_pack_id": "mozaikspay",
        "capability_source": "managed_capability",
        "pack_type": "managed_capability",
        "provides_capabilities": ["subscription_write_path"],
    }


def _entitlement_dispatch_pack() -> dict[str, object]:
    return {
        "capability_pack_id": "entitlement_dispatch",
        "capability_source": "generated_module",
        "pack_type": "generated_module",
    }


def _run_plan(plan: dict[str, object]) -> _Context:
    context = _Context()
    app_build_plan(AppBuildPlan=plan, context_variables=context)
    return context


def test_subscription_config_requires_explicit_monetization_provider() -> None:
    with pytest.raises(ValueError, match="monetization_provider is required"):
        _run_plan(_base_plan(capability_packs=[_mozaikspay_pack()]))


def test_unknown_monetization_provider_fails_before_materialization() -> None:
    with pytest.raises(ValueError, match="must be one of"):
        _run_plan(_base_plan(monetization_provider="custom"))


def test_mozaiks_pay_requires_explicit_mozaikspay_pack_selection() -> None:
    with pytest.raises(ValueError, match="requires the mozaikspay managed capability pack"):
        _run_plan(_base_plan(monetization_provider="mozaiks_pay"))


def test_mozaiks_pay_and_entitlement_dispatch_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="must not both be selected"):
        _run_plan(
            _base_plan(
                monetization_provider="mozaiks_pay",
                capability_packs=[_mozaikspay_pack(), _entitlement_dispatch_pack()],
            )
        )


def test_explicit_mozaiks_pay_selection_is_cached_without_auto_account_activation() -> None:
    context = _run_plan(
        _base_plan(
            monetization_provider="mozaiks_pay",
            capability_packs=[_mozaikspay_pack()],
        )
    )

    normalized = context.values["app_build_plan"]
    assert isinstance(normalized, dict)
    assert normalized["monetization_provider"] == "mozaiks_pay"
    assert {pack["capability_pack_id"] for pack in normalized["capability_packs"]} == {"mozaikspay"}


def test_explicit_self_managed_selection_uses_entitlement_dispatch_only() -> None:
    context = _run_plan(
        _base_plan(
            monetization_provider="entitlement_dispatch",
            capability_packs=[_entitlement_dispatch_pack()],
        )
    )

    normalized = context.values["app_build_plan"]
    assert isinstance(normalized, dict)
    assert normalized["monetization_provider"] == "entitlement_dispatch"
    assert {pack["capability_pack_id"] for pack in normalized["capability_packs"]} == {"entitlement_dispatch"}

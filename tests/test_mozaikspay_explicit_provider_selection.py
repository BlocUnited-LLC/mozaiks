from __future__ import annotations

from pathlib import Path

import pytest

from factory_app.workflows.AppGenerator.tools.app_build_plan import app_build_plan
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from tests.reviewed_pack_fixture import reviewed_pack_fixture
from tests.test_factory_pack_selection import _enable_managed


def _base_plan(**overrides: object) -> dict[str, object]:
    plan: dict[str, object] = {
        "agent_message": "Plan ready.",
        "app_kind": "saas",
        "pages": [{"name": "Home", "route": "/", "purpose": "Home"}],
        "capability_packs": [],
        "build_tasks": [],
    }
    plan.update(overrides)
    return plan


def _mozaikspay_pack() -> dict[str, object]:
    return {
        "capability_pack_id": "mozaikspay",
        "capability_source": "managed_capability",
        "pack_type": "mozaikspay",
        "provides_capabilities": ["subscription_write_path"],
    }


def _entitlement_dispatch_pack() -> dict[str, object]:
    return {
        "capability_pack_id": "entitlement_dispatch",
        "capability_source": "generated_module",
        "pack_type": "custom_domain",
    }


@pytest.fixture()
def registered_mozaikspay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_managed(tmp_path, monkeypatch)


def _run_plan(
    plan: dict[str, object], *, reviewed_packs: tuple[str, ...] = (), contract_required: bool = True
) -> ContextVariablesBridge:
    context = ContextVariablesBridge(reviewed_pack_fixture(
        {"subscription_contract": {"contract_required": contract_required}},
        list(reviewed_packs),
    ))
    app_build_plan(AppBuildPlan=plan, context_variables=context)
    return context


def test_a_required_contract_requires_an_explicit_or_selected_provider() -> None:
    with pytest.raises(ValueError, match="monetization_provider is required"):
        _run_plan(_base_plan())


def test_a_provider_without_a_required_contract_is_rejected() -> None:
    with pytest.raises(ValueError, match="only valid when the approved subscription contract"):
        _run_plan(_base_plan(monetization_provider="mozaiks_pay"), contract_required=False)


def test_unknown_monetization_provider_fails_before_materialization() -> None:
    with pytest.raises(ValueError, match="must be one of"):
        _run_plan(_base_plan(monetization_provider="custom"))


@pytest.mark.usefixtures("registered_mozaikspay")
def test_explicit_mozaiks_pay_provider_includes_public_pack_contract() -> None:
    context = _run_plan(_base_plan(monetization_provider="mozaiks_pay"), reviewed_packs=("mozaikspay",))
    normalized = detach(context.get("app_build_plan"))
    assert isinstance(normalized, dict)
    assert normalized["monetization_provider"] == "mozaiks_pay"
    assert {pack["capability_pack_id"] for pack in normalized["capability_packs"]} == {"mozaikspay", "billing_portal"}
    assert {page["route"] for page in normalized["pages"]} == {"/", "/billing", "/usage"}


@pytest.mark.usefixtures("registered_mozaikspay")
def test_registered_mozaiks_pay_provider_without_build_selection_is_rejected() -> None:
    with pytest.raises(ValueError, match="Managed billing must be selected and owner-reviewed"):
        _run_plan(_base_plan(monetization_provider="mozaiks_pay"))


@pytest.mark.usefixtures("registered_mozaikspay")
def test_inline_mozaiks_pay_pack_claim_without_review_is_rejected() -> None:
    context = ContextVariablesBridge({"subscription_contract": {"contract_required": True}})
    with pytest.raises(ValueError, match="selected pack identities differ from the owner-reviewed design"):
        app_build_plan(
            AppBuildPlan=_base_plan(
                monetization_provider="mozaiks_pay",
                capability_packs=[_mozaikspay_pack()],
            ),
            context_variables=context,
        )
    assert context.get("app_plan_ready") is False
    assert context.get("app_build_plan") is None


@pytest.mark.usefixtures("registered_mozaikspay")
def test_reviewed_mozaiks_pay_pack_still_requires_approved_subscription_contract() -> None:
    with pytest.raises(ValueError, match="Subscription assignment packs require an approved contract"):
        _run_plan(
            _base_plan(monetization_provider="mozaiks_pay"),
            reviewed_packs=("mozaikspay",),
            contract_required=False,
        )


@pytest.mark.usefixtures("registered_mozaikspay")
def test_mozaiks_pay_and_entitlement_dispatch_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="Multiple subscription assignment writers .*select exactly one"):
        _run_plan(
            _base_plan(
                monetization_provider="mozaiks_pay",
                capability_packs=[_mozaikspay_pack(), _entitlement_dispatch_pack()],
            ),
            reviewed_packs=("mozaikspay", "entitlement_dispatch"),
        )


@pytest.mark.usefixtures("registered_mozaikspay")
def test_explicit_mozaiks_pay_selection_is_cached_without_auto_account_activation() -> None:
    context = _run_plan(
        _base_plan(
            monetization_provider="mozaiks_pay",
            capability_packs=[_mozaikspay_pack()],
        ),
        reviewed_packs=("mozaikspay",),
    )

    normalized = detach(context.get("app_build_plan"))
    assert isinstance(normalized, dict)
    assert normalized["monetization_provider"] == "mozaiks_pay"
    assert {pack["capability_pack_id"] for pack in normalized["capability_packs"]} == {"mozaikspay", "billing_portal"}
    assert {page["route"] for page in normalized["pages"]} == {"/", "/billing", "/usage"}


def test_explicit_self_managed_selection_uses_entitlement_dispatch_only() -> None:
    context = _run_plan(
        _base_plan(
            monetization_provider="entitlement_dispatch",
            capability_packs=[_entitlement_dispatch_pack()],
        ),
        reviewed_packs=("entitlement_dispatch",),
    )

    normalized = detach(context.get("app_build_plan"))
    assert isinstance(normalized, dict)
    assert normalized["monetization_provider"] == "entitlement_dispatch"
    assert {pack["capability_pack_id"] for pack in normalized["capability_packs"]} == {"entitlement_dispatch"}


def test_selected_self_managed_pack_resolves_the_provider() -> None:
    context = _run_plan(
        _base_plan(capability_packs=[_entitlement_dispatch_pack()]),
        reviewed_packs=("entitlement_dispatch",),
    )

    normalized = detach(context.get("app_build_plan"))
    assert isinstance(normalized, dict)
    assert normalized["monetization_provider"] == "entitlement_dispatch"
    assert {pack["capability_pack_id"] for pack in normalized["capability_packs"]} == {"entitlement_dispatch"}


@pytest.mark.usefixtures("registered_mozaikspay")
def test_monetization_provider_in_nested_monetization_plan_is_accepted_as_fallback() -> None:
    """Resolve the provider from monetization_plan when the top-level value is omitted."""
    plan = _base_plan(
        capability_packs=[_mozaikspay_pack()],
        monetization_plan={"monetization_provider": "mozaiks_pay"},
    )
    context = _run_plan(plan, reviewed_packs=("mozaikspay",))
    normalized = detach(context.get("app_build_plan"))
    assert isinstance(normalized, dict)
    assert normalized["monetization_provider"] == "mozaiks_pay"

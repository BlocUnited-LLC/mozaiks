"""A subscriber the payment provider reports as trialing is entitled.

The designer used to author `assignment_store`, and live chat 5d930588 wrote
`active_statuses: ["active", "trial"]`. Billing fulfillment writes the provider's
status verbatim ("trialing"), so that app denied every trial subscriber its paid
features. The store is now constructed by code; this drives the real writer and
the real reader against the config/subscriptions.yaml assembly generates from
the recorded design.
"""

from __future__ import annotations

import ast
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from factory_app.workflows._shared.subscription_contract_context import (
    ASSIGNMENT_DATA_ALIAS,
    subscription_assignment_store,
)
from factory_app.workflows.AppGenerator.tools.materialize_app_config_contracts import (
    materialize_app_config_contracts,
)
from factory_app.workflows.SubscriptionContractDesigner.tools.save_subscription_contract import (
    normalize_subscription_contract,
)
from mozaiksai.core.billing.fulfillment import BillingFulfillmentCommand, BillingFulfillmentService
from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter
from mozaiksai.core.runtime.app.subscriptions_loader import (
    SubscriptionsConfig,
    load_subscriptions_config,
)
from mozaiksai.core.tokens.wallet import TokenWalletLedger
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from tests.test_billing_fulfillment import _Collection, _Database

FIXTURES = Path(__file__).parent / "fixtures"
APP_ID = "app_5d930588"


def _recorded() -> tuple[dict, dict]:
    output = json.loads((FIXTURES / "subscription_contract_designer_5d930588_body.json").read_text(encoding="utf-8"))
    context = json.loads((FIXTURES / "subscription_contract_designer_5d930588_context.json").read_text(encoding="utf-8"))
    return output, context


def _generated_config(tmp_path: Path) -> SubscriptionsConfig:
    """Save the recorded design, let assembly write the file, load it as the runtime does."""
    output, context = _recorded()
    contract = normalize_subscription_contract(deepcopy(output), ContextVariablesBridge(context))
    files = materialize_app_config_contracts(
        app_id=APP_ID, app_build_plan={}, context_variables={"subscription_contract": contract},
    )
    content = next(file["content"] for file in files if file["filename"] == "config/subscriptions.yaml")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "subscriptions.yaml").write_text(content, encoding="utf-8")
    config = load_subscriptions_config(tmp_path)
    assert config is not None
    return config


def _paid_capability(config: SubscriptionsConfig) -> tuple[str, str]:
    default = set(config.capabilities_for_plan(config.default_plan_id))
    for plan in config.plans:
        extra = sorted(set(plan.capabilities) - default)
        if extra:
            return plan.plan_id, extra[0]
    raise AssertionError("the recorded design sells no capability beyond its default plan")


def _command(status: str, *, user_id: str, plan_id: str) -> BillingFulfillmentCommand:
    return BillingFulfillmentCommand(
        command_id=f"cmd_{user_id}_{status}", event_type="subscription_activated", source="mozaikspay",
        app_id=APP_ID, user_id=user_id, plan_id=plan_id, status=status,
        occurred_at=datetime(2026, 9, 30, tzinfo=UTC),
    )


async def _fulfil(config: SubscriptionsConfig, assignments: _Collection, command: BillingFulfillmentCommand) -> None:
    service = BillingFulfillmentService(
        config=config, ledger=TokenWalletLedger(database=_Database()),
        collection_resolver=lambda alias: assignments,
    )
    result = await service.apply(command)
    assert result.success, result


def test_the_recorded_design_authored_a_store_that_denies_trials() -> None:
    output, _ = _recorded()
    assert output["subscription_config_file"]["assignment_store"]["active_statuses"] == ["active", "trial"]


@pytest.mark.asyncio
async def test_a_trialing_subscriber_is_entitled_under_the_generated_config(tmp_path: Path) -> None:
    config = _generated_config(tmp_path)
    assert config.assignment_store is not None
    assert config.assignment_store.model_dump(mode="json", exclude_none=True) == subscription_assignment_store()
    plan_id, capability = _paid_capability(config)
    assignments = _Collection()

    await _fulfil(config, assignments, _command("trialing", user_id="user_trial", plan_id=plan_id))

    adapter = ConfiguredEntitlementAdapter(config=config, collection_resolver=lambda alias: assignments)
    granted = await adapter.check(capability, app_id=APP_ID, user_id="user_trial")
    assert granted.granted is True, granted
    assert granted.reason == "active_subscription"
    unpaid = await adapter.check(capability, app_id=APP_ID, user_id="user_free")
    assert unpaid.granted is False


@pytest.mark.asyncio
async def test_the_recorded_store_would_have_denied_the_same_assignment(tmp_path: Path) -> None:
    """The contrast: identical write, the designer's store as the reader."""
    config = _generated_config(tmp_path)
    plan_id, capability = _paid_capability(config)
    recorded_store = _recorded()[0]["subscription_config_file"]["assignment_store"]
    recorded = SubscriptionsConfig.model_validate(
        {**config.model_dump(mode="json", exclude_none=True), "assignment_store": recorded_store}
    )
    assignments = _Collection()

    await _fulfil(recorded, assignments, _command("trialing", user_id="user_trial", plan_id=plan_id))

    adapter = ConfiguredEntitlementAdapter(config=recorded, collection_resolver=lambda alias: assignments)
    denied = await adapter.check(capability, app_id=APP_ID, user_id="user_trial")
    assert denied.granted is False
    assert denied.reason == "inactive_subscription"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["active", "pending", "trialing"])
async def test_every_status_the_runtime_counts_as_active_is_entitled(tmp_path: Path, status: str) -> None:
    config = _generated_config(tmp_path)
    plan_id, capability = _paid_capability(config)
    assignments = _Collection()

    await _fulfil(config, assignments, _command(status, user_id="user_1", plan_id=plan_id))

    adapter = ConfiguredEntitlementAdapter(config=config, collection_resolver=lambda alias: assignments)
    assert (await adapter.check(capability, app_id=APP_ID, user_id="user_1")).granted is True


@pytest.mark.asyncio
async def test_each_subscriber_keeps_their_own_assignment(tmp_path: Path) -> None:
    """user_id_field keys the assignment; a second subscriber must not replace the first."""
    config = _generated_config(tmp_path)
    plan_id, capability = _paid_capability(config)
    assignments = _Collection()

    await _fulfil(config, assignments, _command("trialing", user_id="user_a", plan_id=plan_id))
    await _fulfil(config, assignments, _command("cancelled", user_id="user_b", plan_id=plan_id))

    adapter = ConfiguredEntitlementAdapter(config=config, collection_resolver=lambda alias: assignments)
    assert (await adapter.check(capability, app_id=APP_ID, user_id="user_a")).granted is True
    assert (await adapter.check(capability, app_id=APP_ID, user_id="user_b")).granted is False
    assert len(assignments.docs) == 2


def test_the_generated_file_differs_from_the_designers_only_in_the_store(tmp_path: Path) -> None:
    output, _ = _recorded()
    config = _generated_config(tmp_path)
    generated = yaml.safe_load((tmp_path / "config" / "subscriptions.yaml").read_text(encoding="utf-8"))
    assert generated["assignment_store"] == subscription_assignment_store()
    assert config.default_plan_id == output["subscription_config_file"]["default_plan_id"]
    assert [plan.plan_id for plan in config.plans] == [
        plan["plan_id"] for plan in output["subscription_config_file"]["plans"]
    ]


def _template(relative: str) -> ast.Module:
    root = Path(__file__).resolve().parents[1] / "factory_app" / "build_context"
    return ast.parse((root / relative).read_text(encoding="utf-8"))


def test_the_constructed_alias_and_subject_are_the_writers_own() -> None:
    """data_alias and user_id_field are the two non-default fields; both come from the writers."""
    store = subscription_assignment_store()
    repo = _template("entitlement_dispatch/templates/modules/entitlement_dispatch/backend/repo.py")
    alias = next(
        node.value.value for node in repo.body
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "_SUBSCRIPTIONS_ALIAS"
    )
    assert store["data_alias"] == ASSIGNMENT_DATA_ALIAS == alias

    activate = next(
        node for node in ast.walk(repo) if isinstance(node, ast.AsyncFunctionDef) and node.name == "activate"
    )
    upsert = next(
        node for node in ast.walk(activate)
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "update_one"
    )
    assert [key.value for key in upsert.args[0].keys] == ["app_id", "user_id"]

    facade = _template("mozaikspay/templates/modules/billing_portal/backend/service.py")
    scope = next(node for node in ast.walk(facade) if isinstance(node, ast.FunctionDef) and node.name == "_ctx_scope")
    returned = next(node.value for node in ast.walk(scope) if isinstance(node, ast.Return))
    assert "user_id" in [key.value for key in returned.keys]
    assert store["user_id_field"] == "user_id"
    assert "workspace_id_field" not in store

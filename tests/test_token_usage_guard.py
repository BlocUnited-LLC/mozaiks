from __future__ import annotations

import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from mozaiksai.core.tokens.guard import TokenUsageGuard


def _write_subscriptions(tmp_path: Path, *, assignment_store: bool = False) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    assignment_block = (
        """
            assignment_store:
              data_alias: subscriptions.assignments
              user_id_field: user_id
              active_statuses: [active]
        """
        if assignment_store
        else ""
    )
    config_dir.joinpath("subscriptions.yaml").write_text(
        textwrap.dedent(
            f"""
            schema_version: mozaiks.subscriptions.v1
            label: Token SaaS
            default_plan_id: pro
            {assignment_block}
            token_wallets:
              - wallet_id: ai_tokens
                label: AI tokens
                unit: tokens
                usage_meter_id: ai_tokens
                scope: user
                auto_debit_usage: true
                allow_negative_balance: false
                depleted_balance:
                  recovery_action: top_up
                  billing_route: /billing
                  top_up_route: /billing
                  upgrade_route: /pricing
            top_up_products:
              - product_id: ai_tokens_10k
                label: 10K AI tokens
                wallet_id: ai_tokens
                token_amount: 10000
                price:
                  amount_cents: 500
                  currency: usd
                  display: "$5"
            plans:
              - plan_id: pro
                label: Pro
                capabilities: [ai.chat]
            """
        ),
        encoding="utf-8",
    )


class _Ledger:
    def __init__(self, *, balance: int) -> None:
        self.balance = balance
        self.ensure_calls: list[dict[str, Any]] = []
        self.query_calls: list[dict[str, Any]] = []

    async def ensure_plan_allowances(self, **kwargs):
        self.ensure_calls.append(kwargs)
        return [SimpleNamespace(status="applied")]

    async def ensure_resolved_wallet_allowances(self, **kwargs):
        self.ensure_calls.append(kwargs)
        return [SimpleNamespace(status="applied")]

    async def query_balance(self, **kwargs):
        self.query_calls.append(kwargs)
        return {"balance": self.balance}


class _AssignmentCollection:
    async def find_one(self, query: dict[str, Any], projection: dict[str, int] | None = None):
        if query.get("app_id") == "app_1" and query.get("user_id") == "user_1":
            return {
                "app_id": "app_1",
                "user_id": "user_1",
                "plan_id": "operator_plus",
                "status": "active",
            }
        return None


class _WorkspaceAssignmentCollection:
    async def find_one(self, query: dict[str, Any], projection: dict[str, int] | None = None):
        if (
            query.get("app_id") == "app_1"
            and query.get("tenant_id") == "tenant_1"
            and query.get("workspace_id") == "workspace_1"
            and query.get("user_id") is None
        ):
            return {
                "app_id": "app_1",
                "tenant_id": "tenant_1",
                "workspace_id": "workspace_1",
                "plan_id": "operator_workspace",
                "status": "active",
            }
        return None


class _AIPlanCollection:
    def __init__(self, plan_id: str) -> None:
        self.plan_id = plan_id

    async def find_one(self, query: dict[str, Any], projection: dict[str, int] | None = None):
        if query != {
            "app_id": "app_1", "tenant_id": None, "workspace_id": None,
            "user_id": "user_1",
        }:
            return None
        return {**query, "plan_id": self.plan_id, "status": "active"}


def _multi_product_token_config(
    *, active_top_up: bool = False, nested_top_up: bool = False,
    shared_wallet: bool = False,
    enterprise_cadence: str = "monthly",
) -> SubscriptionsConfig:
    products = [
        {
            "product_id": "platform", "label": "Platform", "default_plan_id": "builder",
            "plans": [{"plan_id": "builder", "label": "Builder"}],
        },
        {
            "product_id": "ai", "label": "AI", "default_plan_id": "ai_starter",
            "assignment_store": {
                "data_alias": "billing.ai", "user_id_field": "user_id",
                "workspace_id_field": "workspace_id",
                "active_statuses": ["active"],
            },
            "plans": [
                {"plan_id": "ai_starter", "label": "Starter"},
                {
                    "plan_id": "ai_pro", "label": "Pro",
                    "token_allowances": [{
                        "wallet_id": "ai_tokens", "amount": 500,
                        "cadence": "monthly",
                    }],
                },
                {
                    "plan_id": "ai_enterprise", "label": "Enterprise",
                    "token_allowances": [{
                        "wallet_id": "ai_tokens", "amount": 1000,
                        "cadence": enterprise_cadence,
                    }],
                },
            ],
            "top_up_products": ([{
                "product_id": "ai_tokens_10k", "label": "10K AI tokens",
                "wallet_id": "ai_tokens", "token_amount": 10000,
                "price": {"amount_cents": 500, "currency": "usd"},
            }] if nested_top_up else []),
        },
    ]
    if shared_wallet:
        products.append({
            "product_id": "other_ai", "label": "Other AI",
            "default_plan_id": "other_starter",
            "plans": [
                {"plan_id": "other_starter", "label": "Starter"},
                {
                    "plan_id": "other_pro", "label": "Pro",
                    "token_allowances": [{
                        "wallet_id": "ai_tokens", "amount": 2000,
                        "cadence": "monthly",
                    }],
                },
            ],
        })
    return SubscriptionsConfig.model_validate({
        "schema_version": "mozaiks.subscriptions.v2",
        "label": "Token SaaS",
        "default_product_id": "platform",
        "token_wallets": [{
            "wallet_id": "ai_tokens", "scope": "user", "auto_debit_usage": True,
            "depleted_balance": {
                "recovery_action": "upgrade", "billing_route": "/pricing",
                "upgrade_route": "/pricing",
            },
        }],
        "top_up_products": ([{
            "product_id": "ai_tokens_10k", "label": "10K AI tokens",
            "wallet_id": "ai_tokens",
            "token_amount": 10000,
            "price": {"amount_cents": 500, "currency": "usd"},
        }] if active_top_up else []),
        "products": products,
    })


@pytest.mark.asyncio
async def test_token_usage_guard_allows_when_wallet_has_required_balance(tmp_path: Path) -> None:
    _write_subscriptions(tmp_path)
    ledger = _Ledger(balance=50)
    guard = TokenUsageGuard(app_root=tmp_path, ledger=ledger)

    decision = await guard.check(
        app_id="app_1",
        user_id="user_1",
        required_tokens=25,
    )

    assert decision.allowed is True
    assert decision.reason == "sufficient_balance"
    assert ledger.ensure_calls[0]["plan_id"] == "pro"
    assert ledger.query_calls[0]["wallet_id"] == "ai_tokens"


@pytest.mark.asyncio
async def test_token_usage_guard_denies_before_llm_when_wallet_is_depleted(tmp_path: Path) -> None:
    _write_subscriptions(tmp_path)
    guard = TokenUsageGuard(app_root=tmp_path, ledger=_Ledger(balance=0))

    decision = await guard.check(
        app_id="app_1",
        user_id="user_1",
        required_tokens=1,
    )

    assert decision.allowed is False
    assert decision.error_code == "INSUFFICIENT_TOKENS"
    assert decision.app_id == "app_1"
    assert decision.wallet_id == "ai_tokens"
    assert decision.balance == 0
    assert decision.required_tokens == 1
    assert decision.scope == "user"
    assert decision.user_id == "user_1"
    assert decision.recovery_action == "top_up"
    assert decision.billing_route == "/billing"
    assert decision.top_up_route == "/billing"
    assert decision.upgrade_route == "/pricing"
    assert decision.top_up_product_ids == ("ai_tokens_10k",)
    assert decision.to_error_metadata()["top_up_product_ids"] == ["ai_tokens_10k"]


@pytest.mark.asyncio
async def test_token_usage_guard_denies_missing_user_scope(tmp_path: Path) -> None:
    _write_subscriptions(tmp_path)
    guard = TokenUsageGuard(app_root=tmp_path, ledger=_Ledger(balance=100))

    decision = await guard.check(app_id="app_1", required_tokens=1)

    assert decision.allowed is False
    assert decision.error_code == "TOKEN_USAGE_SCOPE_MISSING"
    assert decision.reason == "missing_user_id"


@pytest.mark.asyncio
async def test_token_usage_guard_preserves_dynamic_assignment_without_default_allowance(tmp_path: Path) -> None:
    _write_subscriptions(tmp_path, assignment_store=True)
    ledger = _Ledger(balance=50)
    guard = TokenUsageGuard(
        app_root=tmp_path,
        ledger=ledger,
        collection_resolver=lambda alias: _AssignmentCollection(),
    )

    decision = await guard.check(
        app_id="app_1",
        user_id="user_1",
        required_tokens=25,
    )

    assert decision.allowed is True
    assert ledger.ensure_calls == []


@pytest.mark.asyncio
async def test_token_usage_guard_uses_workspace_assignment_for_plan_resolution(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_dir.joinpath("subscriptions.yaml").write_text(
        textwrap.dedent(
            """
            schema_version: mozaiks.subscriptions.v1
            label: Token SaaS
            default_plan_id: pro
            assignment_store:
              data_alias: subscriptions.assignments
              user_id_field: user_id
              workspace_id_field: workspace_id
              active_statuses: [active]
            token_wallets:
              - wallet_id: ai_tokens
                label: AI tokens
                unit: tokens
                usage_meter_id: ai_tokens
                scope: user
                auto_debit_usage: true
                allow_negative_balance: false
            plans:
              - plan_id: pro
                label: Pro
                capabilities: [ai.chat]
            """
        ),
        encoding="utf-8",
    )
    ledger = _Ledger(balance=50)
    guard = TokenUsageGuard(
        app_root=tmp_path,
        ledger=ledger,
        collection_resolver=lambda alias: _WorkspaceAssignmentCollection(),
    )

    decision = await guard.check(
        app_id="app_1",
        user_id="user_1",
        tenant_id="tenant_1",
        workspace_id="workspace_1",
        required_tokens=25,
    )

    assert decision.allowed is True
    assert ledger.ensure_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ai_plan_id", "expected_action"),
    [
        ("ai_starter", "upgrade"),
        ("ai_pro", "upgrade"),
        ("ai_enterprise", "contact_admin"),
    ],
)
async def test_v2_depleted_wallet_uses_owning_product_plan(
    ai_plan_id: str, expected_action: str,
) -> None:
    ledger = _Ledger(balance=0)
    guard = TokenUsageGuard(
        config=_multi_product_token_config(),
        ledger=ledger,
        collection_resolver=lambda alias: _AIPlanCollection(ai_plan_id),
    )

    decision = await guard.check(app_id="app_1", user_id="user_1")

    assert decision.error_code == "INSUFFICIENT_TOKENS"
    assert decision.recovery_action == expected_action
    assert decision.upgrade_route == (
        "/pricing" if expected_action == "upgrade" else None
    )
    assert decision.billing_route == (
        "/pricing" if expected_action == "upgrade" else None
    )
    assert decision.top_up_product_ids == ()
    assert ledger.ensure_calls == []  # Paid v2 grants belong to billing fulfillment.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config_kwargs,plan_id",
    [
        ({"active_top_up": True}, "ai_enterprise"),
        ({"nested_top_up": True}, "ai_enterprise"),
        ({"shared_wallet": True}, "ai_enterprise"),
        ({"enterprise_cadence": "manual"}, "ai_enterprise"),
        ({}, "operator_ai_plan"),
    ],
)
async def test_v2_recovery_keeps_declared_upgrade_when_plan_authority_is_unclear(
    config_kwargs: dict[str, Any], plan_id: str,
) -> None:
    guard = TokenUsageGuard(
        config=_multi_product_token_config(**config_kwargs),
        ledger=_Ledger(balance=0),
        collection_resolver=lambda alias: _AIPlanCollection(plan_id),
    )

    decision = await guard.check(app_id="app_1", user_id="user_1")

    assert decision.error_code == "INSUFFICIENT_TOKENS"
    assert decision.recovery_action == "upgrade"
    assert decision.upgrade_route == "/pricing"
    if config_kwargs.get("active_top_up"):
        assert decision.top_up_product_ids == ("ai_tokens_10k",)

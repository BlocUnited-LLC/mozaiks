from __future__ import annotations

"""Tests for mozaiksai.core.runtime.app.subscriptions_loader."""

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from mozaiksai.core.runtime.app.subscriptions_loader import (
    AddOnProductDef,
    PlanDef,
    PricingCatalogDef,
    SubscriptionAssignmentStoreDef,
    SubscriptionsConfig,
    SubscriptionsLoadError,
    UsageChargePolicyDef,
    UsageLimitDef,
    load_subscriptions_config,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, content: str) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "subscriptions.yaml"
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# load_subscriptions_config — file-absent case
# ---------------------------------------------------------------------------


def test_load_returns_none_when_file_absent(tmp_path: Path) -> None:
    result = load_subscriptions_config(tmp_path)
    assert result is None


# ---------------------------------------------------------------------------
# load_subscriptions_config — valid YAML
# ---------------------------------------------------------------------------


def test_load_valid_minimal_config(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
        schema_version: mozaiks.subscriptions.v1
        label: Test App
        default_plan_id: free
        plans:
          - plan_id: free
            label: Free
            capabilities: []
        """,
    )
    config = load_subscriptions_config(tmp_path)
    assert config is not None
    assert config.schema_version == "mozaiks.subscriptions.v1"
    assert config.label == "Test App"
    assert config.default_plan_id == "free"
    assert len(config.plans) == 1
    assert config.plans[0].plan_id == "free"
    assert config.plans[0].capabilities == []


def test_load_multi_plan_with_capabilities(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
        schema_version: mozaiks.subscriptions.v1
        label: My SaaS
        default_plan_id: free
        plans:
          - plan_id: free
            label: Free
            capabilities: []
          - plan_id: pro
            label: Pro
            capabilities:
              - wallet.view
              - analytics.dashboard
          - plan_id: enterprise
            label: Enterprise
            capabilities:
              - wallet.view
              - wallet.transfer
              - analytics.dashboard
              - analytics.export
        """,
    )
    config = load_subscriptions_config(tmp_path)
    assert config is not None
    assert len(config.plans) == 3
    pro = next(p for p in config.plans if p.plan_id == "pro")
    assert "wallet.view" in pro.capabilities
    assert "analytics.dashboard" in pro.capabilities


def test_load_plan_usage_limits(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
        schema_version: mozaiks.subscriptions.v1
        label: My SaaS
        default_plan_id: free
        plans:
          - plan_id: free
            label: Free
            capabilities: []
            usage_limits:
              - meter_id: ai_tokens
                label: AI tokens
                unit: tokens
                monthly_limit: 100000
                capability_id: ai.chat
        """,
    )

    config = load_subscriptions_config(tmp_path)

    assert config is not None
    limit = config.plans[0].usage_limits[0]
    assert limit.meter_id == "ai_tokens"
    assert limit.unit == "tokens"
    assert limit.monthly_limit == 100000
    assert limit.capability_id == "ai.chat"


def test_load_token_wallets_and_plan_allowances(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
        schema_version: mozaiks.subscriptions.v1
        label: Token SaaS
        default_plan_id: pro
        token_wallets:
          - wallet_id: ai_tokens
            label: AI token balance
            unit: tokens
            usage_meter_id: ai_tokens
            scope: user
            auto_debit_usage: true
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
              currency: USD
              display: "$5"
        plans:
          - plan_id: pro
            label: Pro
            capabilities: [ai.chat]
            token_allowances:
              - wallet_id: ai_tokens
                amount: 100000
                cadence: monthly
        """,
    )

    config = load_subscriptions_config(tmp_path)

    assert config is not None
    assert config.token_wallets[0].wallet_id == "ai_tokens"
    assert config.token_wallets[0].auto_debit_usage is True
    assert config.token_wallets[0].depleted_balance is not None
    assert config.token_wallets[0].depleted_balance.recovery_action == "top_up"
    assert config.top_up_products[0].product_id == "ai_tokens_10k"
    assert config.top_up_products[0].price.amount_cents == 500
    assert config.top_up_products[0].price.currency == "usd"
    assert config.top_up_products_for_wallet("ai_tokens")[0].token_amount == 10000
    assert config.plans[0].token_allowances[0].amount == 100000
    assert config.plan_by_id("missing").plan_id == "pro"
    assert config.token_wallet_by_id("ai_tokens") is config.token_wallets[0]


def test_token_top_up_products_must_reference_declared_wallets() -> None:
    with pytest.raises(ValidationError, match="top_up_product wallet_id"):
        SubscriptionsConfig.model_validate(
            {
                "schema_version": "mozaiks.subscriptions.v1",
                "label": "Token SaaS",
                "default_plan_id": "pro",
                "token_wallets": [{"wallet_id": "ai_tokens"}],
                "top_up_products": [
                    {
                        "product_id": "other_tokens_10k",
                        "label": "10K other tokens",
                        "wallet_id": "other_tokens",
                        "token_amount": 10000,
                        "price": {"amount_cents": 500, "currency": "usd"},
                    }
                ],
                "plans": [{"plan_id": "pro", "label": "Pro"}],
            }
        )


def test_token_top_up_product_price_currency_must_be_three_letter_code() -> None:
    with pytest.raises(ValidationError, match="three-letter ISO 4217"):
        SubscriptionsConfig.model_validate(
            {
                "schema_version": "mozaiks.subscriptions.v1",
                "label": "Token SaaS",
                "default_plan_id": "pro",
                "token_wallets": [{"wallet_id": "ai_tokens"}],
                "top_up_products": [
                    {
                        "product_id": "ai_tokens_10k",
                        "label": "10K AI tokens",
                        "wallet_id": "ai_tokens",
                        "token_amount": 10000,
                        "price": {"amount_cents": 500, "currency": "us-dollar"},
                    }
                ],
                "plans": [{"plan_id": "pro", "label": "Pro"}],
            }
        )


def test_depleted_balance_routes_must_be_app_local() -> None:
    with pytest.raises(ValidationError, match="app-local path"):
        SubscriptionsConfig.model_validate(
            {
                "schema_version": "mozaiks.subscriptions.v1",
                "label": "Token SaaS",
                "default_plan_id": "pro",
                "token_wallets": [
                    {
                        "wallet_id": "ai_tokens",
                        "depleted_balance": {
                            "recovery_action": "top_up",
                            "billing_route": "https://provider.example/billing",
                        },
                    }
                ],
                "plans": [{"plan_id": "pro", "label": "Pro"}],
            }
        )


def test_load_usage_charge_policies(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
        schema_version: mozaiks.subscriptions.v1
        label: Token SaaS
        default_plan_id: pro
        usage_charge_policies:
          - meter_id: ai_tokens
            label: AI usage
            source: runtime_llm_usage
            basis: provider_cost_usd
            markup_percent: 35
            minimum_charge_usd: 0.01
            rounding: cent
        plans:
          - plan_id: pro
            label: Pro
            capabilities: [ai.chat]
        """,
    )

    config = load_subscriptions_config(tmp_path)

    assert config is not None
    policy = config.usage_charge_policy_by_meter_id("ai_tokens")
    assert policy is not None
    assert policy.basis == "provider_cost_usd"
    assert policy.markup_percent == 35
    assert policy.minimum_charge_usd == 0.01


def test_load_pricing_catalog_groups(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
        schema_version: mozaiks.subscriptions.v1
        label: Multi Service SaaS
        default_plan_id: free
        pricing_catalog:
          default_group_id: platform
          groups:
            - group_id: platform
              label: Platform
              description: Core subscription plans.
              kind: subscription
              plan_ids: [free, pro]
              capability_groups: [platform, billing]
            - group_id: marketing
              label: Marketing
              kind: add_on
              add_on_ids: [hero_weekly]
        plans:
          - plan_id: free
            label: Free
            capabilities: []
          - plan_id: pro
            label: Pro
            capabilities: [billing.checkout]
        """,
    )

    config = load_subscriptions_config(tmp_path)

    assert config is not None
    assert config.pricing_catalog is not None
    assert config.pricing_catalog.default_group_id == "platform"
    assert config.pricing_catalog.groups[0].plan_ids == ["free", "pro"]
    assert config.pricing_catalog.groups[1].add_on_ids == ["hero_weekly"]


def test_load_add_on_products_for_pricing_catalog_groups(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
        schema_version: mozaiks.subscriptions.v1
        label: Multi Service SaaS
        default_plan_id: free
        add_on_products:
          - add_on_id: hero_weekly
            label: Hero Placement - Weekly
            description: Promoted placement for seven days.
            kind: marketplace_placement
            billing_mode: one_time
            required_capability: marketplace.placements.order
            capability_groups: [marketplace.placements]
            duration_days: 7
            price:
              amount_cents: 9900
              currency: USD
              display: "$99/week"
              interval: one_time
        pricing_catalog:
          default_group_id: marketing
          groups:
            - group_id: marketing
              label: Marketing
              kind: add_on
              add_on_ids: [hero_weekly]
        plans:
          - plan_id: free
            label: Free
            capabilities: []
        """,
    )

    config = load_subscriptions_config(tmp_path)

    assert config is not None
    assert config.add_on_products[0].add_on_id == "hero_weekly"
    assert config.add_on_products[0].price is not None
    assert config.add_on_products[0].price.currency == "usd"
    assert config.add_on_product_by_id("hero_weekly") is config.add_on_products[0]
    assert config.add_on_products_for_group("marketing") == [config.add_on_products[0]]


def test_add_on_products_reject_unknown_pricing_catalog_add_on_id() -> None:
    with pytest.raises(ValidationError, match="unknown add_on_ids"):
        SubscriptionsConfig.model_validate(
            {
                "schema_version": "mozaiks.subscriptions.v1",
                "label": "Multi Service SaaS",
                "default_plan_id": "free",
                "add_on_products": [
                    {
                        "add_on_id": "hero_weekly",
                        "label": "Hero Placement - Weekly",
                    }
                ],
                "pricing_catalog": {
                    "default_group_id": "marketing",
                    "groups": [
                        {
                            "group_id": "marketing",
                            "label": "Marketing",
                            "kind": "add_on",
                            "add_on_ids": ["missing"],
                        }
                    ],
                },
                "plans": [
                    {"plan_id": "free", "label": "Free", "capabilities": []},
                ],
            }
        )


def test_add_on_product_rejects_provider_specific_extra_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AddOnProductDef.model_validate(
            {
                "add_on_id": "hero_weekly",
                "label": "Hero Placement - Weekly",
                "stripe_price_id": "price_123",
            }
        )


def test_pricing_catalog_rejects_unknown_plan_id() -> None:
    with pytest.raises(ValidationError, match="unknown plan_ids"):
        SubscriptionsConfig.model_validate(
            {
                "schema_version": "mozaiks.subscriptions.v1",
                "label": "Multi Service SaaS",
                "default_plan_id": "free",
                "pricing_catalog": {
                    "default_group_id": "platform",
                    "groups": [
                        {
                            "group_id": "platform",
                            "label": "Platform",
                            "plan_ids": ["free", "missing"],
                        }
                    ],
                },
                "plans": [
                    {"plan_id": "free", "label": "Free", "capabilities": []},
                ],
            }
        )


def test_pricing_catalog_rejects_empty_groups() -> None:
    with pytest.raises(ValidationError, match="groups must be non-empty"):
        PricingCatalogDef.model_validate({"groups": []})


def test_token_allowance_requires_declared_wallet_when_wallets_declared() -> None:
    with pytest.raises(ValidationError, match="must reference token_wallets"):
        SubscriptionsConfig.model_validate(
            {
                "schema_version": "mozaiks.subscriptions.v1",
                "label": "Token SaaS",
                "default_plan_id": "pro",
                "token_wallets": [{"wallet_id": "ai_tokens"}],
                "plans": [
                    {
                        "plan_id": "pro",
                        "label": "Pro",
                        "token_allowances": [
                            {
                                "wallet_id": "other_tokens",
                                "amount": 100,
                            }
                        ],
                    }
                ],
            }
        )


def test_usage_charge_policy_tokens_basis_requires_unit_price() -> None:
    with pytest.raises(ValidationError, match="unit_price_usd_per_1k"):
        UsageChargePolicyDef.model_validate(
            {
                "meter_id": "ai_tokens",
                "basis": "tokens",
                "markup_percent": 10,
            }
        )


def test_usage_charge_policy_meter_ids_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="meter_ids must be unique"):
        SubscriptionsConfig.model_validate(
            {
                "schema_version": "mozaiks.subscriptions.v1",
                "label": "Token SaaS",
                "default_plan_id": "pro",
                "usage_charge_policies": [
                    {"meter_id": "ai_tokens", "basis": "provider_cost_usd"},
                    {"meter_id": "ai_tokens", "basis": "provider_cost_usd"},
                ],
                "plans": [{"plan_id": "pro", "label": "Pro"}],
            }
        )


def test_load_assignment_store(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
        schema_version: mozaiks.subscriptions.v1
        label: My SaaS
        default_plan_id: free
        assignment_store:
          data_alias: billing.subscriptions
          workspace_id_field: workspace_id
          active_statuses: [active, pending]
        plans:
          - plan_id: free
            label: Free
            capabilities: []
          - plan_id: pro
            label: Pro
            capabilities:
              - analytics.dashboard
        """,
    )

    config = load_subscriptions_config(tmp_path)

    assert config is not None
    assert config.assignment_store is not None
    assert config.assignment_store.data_alias == "billing.subscriptions"
    assert config.assignment_store.workspace_id_field == "workspace_id"
    assert config.assignment_store.active_statuses == ["active", "pending"]


def test_invalid_usage_limit_rejected() -> None:
    with pytest.raises(ValidationError):
        UsageLimitDef.model_validate(
            {
                "meter_id": "Bad Meter",
                "unit": "tokens",
                "monthly_limit": 100,
            }
        )


def test_invalid_assignment_store_alias_rejected() -> None:
    with pytest.raises(ValidationError):
        SubscriptionAssignmentStoreDef.model_validate(
            {"data_alias": "Billing Subscriptions"}
        )


def test_empty_assignment_store_statuses_rejected() -> None:
    with pytest.raises(ValidationError):
        SubscriptionAssignmentStoreDef.model_validate(
            {"data_alias": "billing.subscriptions", "active_statuses": []}
        )


# ---------------------------------------------------------------------------
# SubscriptionsConfig — validation
# ---------------------------------------------------------------------------


def test_duplicate_plan_ids_rejected() -> None:
    with pytest.raises(ValidationError):
        SubscriptionsConfig.model_validate(
            {
                "schema_version": "mozaiks.subscriptions.v1",
                "label": "Test",
                "default_plan_id": "free",
                "plans": [
                    {"plan_id": "free", "label": "Free", "capabilities": []},
                    {"plan_id": "free", "label": "Free Again", "capabilities": []},
                ],
            }
        )


def test_default_plan_id_must_reference_declared_plan() -> None:
    with pytest.raises(ValidationError):
        SubscriptionsConfig.model_validate(
            {
                "schema_version": "mozaiks.subscriptions.v1",
                "label": "Test",
                "default_plan_id": "nonexistent",
                "plans": [
                    {"plan_id": "free", "label": "Free", "capabilities": []},
                ],
            }
        )


def test_empty_plans_rejected() -> None:
    with pytest.raises(ValidationError):
        SubscriptionsConfig.model_validate(
            {
                "schema_version": "mozaiks.subscriptions.v1",
                "label": "Test",
                "default_plan_id": "free",
                "plans": [],
            }
        )


def test_invalid_capability_id_format_rejected() -> None:
    with pytest.raises(ValidationError):
        PlanDef.model_validate(
            {
                "plan_id": "pro",
                "label": "Pro",
                "capabilities": ["UPPERCASE.bad", "has spaces"],
            }
        )


def test_invalid_plan_id_format_rejected() -> None:
    with pytest.raises(ValidationError):
        PlanDef.model_validate(
            {"plan_id": "UPPERCASE", "label": "Bad", "capabilities": []}
        )


def test_wrong_schema_version_rejected(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
        schema_version: mozaiks.subscriptions.v2
        label: Test
        default_plan_id: free
        plans:
          - plan_id: free
            label: Free
            capabilities: []
        """,
    )
    with pytest.raises(SubscriptionsLoadError):
        load_subscriptions_config(tmp_path)


def test_not_a_yaml_object_raises(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "subscriptions.yaml").write_text("- just a list\n", encoding="utf-8")
    with pytest.raises(SubscriptionsLoadError):
        load_subscriptions_config(tmp_path)


# ---------------------------------------------------------------------------
# SubscriptionsConfig.capabilities_for_plan
# ---------------------------------------------------------------------------


def test_capabilities_for_plan_known_plan() -> None:
    config = SubscriptionsConfig.model_validate(
        {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "Test",
            "default_plan_id": "free",
            "plans": [
                {"plan_id": "free", "label": "Free", "capabilities": []},
                {
                    "plan_id": "pro",
                    "label": "Pro",
                    "capabilities": ["wallet.view", "analytics.dashboard"],
                },
            ],
        }
    )
    caps = config.capabilities_for_plan("pro")
    assert "wallet.view" in caps
    assert "analytics.dashboard" in caps
    assert len(caps) == 2


def test_capabilities_for_plan_falls_back_to_default() -> None:
    config = SubscriptionsConfig.model_validate(
        {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "Test",
            "default_plan_id": "free",
            "plans": [
                {"plan_id": "free", "label": "Free", "capabilities": []},
                {
                    "plan_id": "pro",
                    "label": "Pro",
                    "capabilities": ["wallet.view"],
                },
            ],
        }
    )
    # Unknown plan — falls back to default (free), which has no capabilities
    caps = config.capabilities_for_plan("unknown_plan")
    assert caps == frozenset()


def test_capabilities_for_plan_free_has_no_capabilities() -> None:
    config = SubscriptionsConfig.model_validate(
        {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "Test",
            "default_plan_id": "free",
            "plans": [
                {"plan_id": "free", "label": "Free", "capabilities": []},
            ],
        }
    )
    assert config.capabilities_for_plan("free") == frozenset()


# ---------------------------------------------------------------------------
# AppLoadResult carries subscriptions_config
# ---------------------------------------------------------------------------


def test_app_load_result_has_subscriptions_config_field() -> None:
    """AppLoadResult must expose subscriptions_config for platform.py wiring."""
    from mozaiksai.core.runtime.app.loader import AppLoadResult

    result = AppLoadResult.__dataclass_fields__
    assert "subscriptions_config" in result


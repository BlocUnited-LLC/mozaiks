from __future__ import annotations

import pytest

from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from mozaiksai.hosts.platform import (
    _current_user_token_wallet_summary,
    _serialize_subscription_usage_limits,
)


def test_profile_usage_serializes_subscription_usage_limits():
    config = SubscriptionsConfig.model_validate(
        {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "Generated SaaS",
            "default_plan_id": "pro",
            "plans": [
                {
                    "plan_id": "pro",
                    "label": "Pro",
                    "capabilities": ["ai.chat"],
                    "usage_limits": [
                        {
                            "meter_id": "ai_tokens",
                            "label": "AI tokens",
                            "unit": "tokens",
                            "monthly_limit": 100000,
                            "capability_id": "ai.chat",
                        }
                    ],
                    "token_allowances": [
                        {
                            "wallet_id": "ai_tokens",
                            "amount": 100000,
                            "cadence": "monthly",
                        }
                    ],
                }
            ],
            "token_wallets": [
                {
                    "wallet_id": "ai_tokens",
                    "label": "AI tokens",
                    "unit": "tokens",
                    "usage_meter_id": "ai_tokens",
                    "scope": "user",
                    "auto_debit_usage": True,
                }
            ],
            "usage_charge_policies": [
                {
                    "meter_id": "ai_tokens",
                    "label": "AI usage",
                    "source": "runtime_llm_usage",
                    "basis": "provider_cost_usd",
                    "markup_percent": 35,
                    "rounding": "cent",
                }
            ],
        }
    )

    payload = _serialize_subscription_usage_limits(config)

    assert payload == {
        "schema_version": "mozaiks.subscriptions.v1",
        "default_plan_id": "pro",
        "plans": [
            {
                "plan_id": "pro",
                "label": "Pro",
                "usage_limits": [
                    {
                        "meter_id": "ai_tokens",
                        "label": "AI tokens",
                        "unit": "tokens",
                        "monthly_limit": 100000,
                        "capability_id": "ai.chat",
                    }
                ],
                "token_allowances": [
                    {
                        "wallet_id": "ai_tokens",
                        "amount": 100000,
                        "cadence": "monthly",
                        "label": None,
                    }
                ],
            }
        ],
        "token_wallets": [
            {
                "wallet_id": "ai_tokens",
                "label": "AI tokens",
                "unit": "tokens",
                "usage_meter_id": "ai_tokens",
                "scope": "user",
                "auto_debit_usage": True,
                "allow_negative_balance": False,
                "depleted_balance": None,
            }
        ],
        "usage_charge_policies": [
            {
                "meter_id": "ai_tokens",
                "label": "AI usage",
                "source": "runtime_llm_usage",
                "basis": "provider_cost_usd",
                "markup_percent": 35.0,
                "unit_price_usd_per_1k": None,
                "minimum_charge_usd": 0.0,
                "rounding": "cent",
            }
        ],
        "source": "app_config_subscriptions",
    }


@pytest.mark.asyncio
async def test_current_user_token_wallet_summary_uses_ledger(monkeypatch):
    config = SubscriptionsConfig.model_validate(
        {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "Generated SaaS",
            "default_plan_id": "pro",
            "token_wallets": [
                {
                    "wallet_id": "ai_tokens",
                    "label": "AI tokens",
                    "unit": "tokens",
                    "usage_meter_id": "ai_tokens",
                    "scope": "user",
                }
            ],
            "plans": [
                {
                    "plan_id": "pro",
                    "label": "Pro",
                    "token_allowances": [
                        {
                            "wallet_id": "ai_tokens",
                            "amount": 1000,
                            "cadence": "monthly",
                        }
                    ],
                }
            ],
        }
    )

    class _Ledger:
        async def wallet_summaries_for_config(self, **kwargs):
            assert kwargs["app_id"] == "app_1"
            assert kwargs["user_id"] == "user_1"
            assert kwargs["plan_id"] == "pro"
            assert kwargs["ensure_allowances"] is False
            return {
                "wallets": [
                    {
                        "wallet_id": "ai_tokens",
                        "balance": {"balance": 900},
                    }
                ],
                "source": "token_wallet_ledger",
            }

    monkeypatch.setattr(
        "mozaiksai.core.tokens.wallet.get_token_wallet_ledger",
        lambda: _Ledger(),
    )

    payload = await _current_user_token_wallet_summary(
        config,
        app_id="app_1",
        user_id="user_1",
        ensure_allowances=False,
    )

    assert payload["wallets"][0]["balance"]["balance"] == 900
    assert payload["source"] == "token_wallet_ledger"

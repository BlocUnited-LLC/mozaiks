from __future__ import annotations

from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from mozaiksai.hosts.platform import _serialize_subscription_usage_limits


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
            }
        ],
        "source": "app_config_subscriptions",
    }

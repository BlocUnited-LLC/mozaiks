"""An empty pricing catalog is an absent one, not an invalid one.

The 2026-09-23 acceptance run at OSS 008aa0cc reached
SubscriptionContractDesigner with a monetized free/Pro concept and died there:

    Tool 'save_subscription_contract' returned failure: invalid_subscription_contract:
    pricing_catalog
      Value error, pricing_catalog.groups must be non-empty
      [input_value={'default_group_id': None, 'groups': []}]
    TOOL_OUTCOME_REJECTED attempt=1/3 ... 2/3 ... 3/3 -> attempts_exhausted

The agent was right. The prompt tells it (agents.yaml, "Pricing catalog
groups"): "If the app has one simple subscription ladder, set pricing_catalog
to null so the generated pricing page uses the default plan cards." Free/Pro is
one simple ladder, and the agent said so -- as an empty catalog rather than a
null one, because a nullable object under strict-mode decoding is easier to
fill than to omit.

`PricingCatalogDef` refuses empty groups. That is correct for a hand-written
config and fatal for a generated one: pricing_catalog is optional display
metadata, so the build died on something the app does not need, and the retry
re-asked a question the model had already answered the same way three times.

`_normalize_subscription_config` already treats an empty collection as absent
for token_wallets, top_up_products, add_on_products, usage_charge_policies and
per-plan usage_limits/token_allowances. Those run after validation; this one has
to run before it.
"""

from __future__ import annotations

import pytest

from factory_app.workflows.SubscriptionContractDesigner.tools.save_subscription_contract import (
    _normalize_subscription_config,
)

PLANS = [
    {"plan_id": "free", "label": "Free", "capabilities": ["tasks.basic"]},
    {"plan_id": "pro", "label": "Pro", "capabilities": ["tasks.unlimited", "analytics.advanced"]},
]


def _config(**overrides) -> dict:
    return {"label": "TaskTracker Pro", "default_plan_id": "free", "plans": PLANS, **overrides}


def test_the_live_payload_is_accepted() -> None:
    """The exact object the agent emitted three times on the 008aa0cc run."""
    result = _normalize_subscription_config(
        _config(pricing_catalog={"default_group_id": None, "groups": []})
    )
    assert result.get("pricing_catalog") is None
    assert [plan["plan_id"] for plan in result["plans"]] == ["free", "pro"], (
        "normalizing the catalog must not disturb the contract that matters"
    )


def test_a_catalog_with_no_groups_key_is_also_absent() -> None:
    """The model omits `groups` as readily as it empties it; both mean none."""
    result = _normalize_subscription_config(_config(pricing_catalog={"default_group_id": None}))
    assert result.get("pricing_catalog") is None


def test_an_explicit_null_still_works() -> None:
    """The shape the prompt actually asks for must keep working."""
    result = _normalize_subscription_config(_config(pricing_catalog=None))
    assert result.get("pricing_catalog") is None


def test_a_real_catalog_is_preserved() -> None:
    """The fix must not become a way to silently discard display metadata."""
    result = _normalize_subscription_config(
        _config(
            pricing_catalog={
                "default_group_id": "core",
                "groups": [{"group_id": "core", "label": "Core", "plan_ids": ["free", "pro"]}],
            }
        )
    )
    catalog = result["pricing_catalog"]
    assert [group["group_id"] for group in catalog["groups"]] == ["core"]
    assert catalog["default_group_id"] == "core"


def test_a_malformed_catalog_is_still_refused() -> None:
    """Only emptiness is reinterpreted. A catalog that says something wrong still fails."""
    with pytest.raises(ValueError):
        _normalize_subscription_config(
            _config(
                pricing_catalog={
                    "default_group_id": "missing_group",
                    "groups": [{"group_id": "core", "label": "Core", "plan_ids": ["free"]}],
                }
            )
        )


def test_a_catalog_referencing_an_unknown_plan_is_still_refused() -> None:
    """The group/plan integrity rules are untouched by this normalization."""
    with pytest.raises(ValueError):
        _normalize_subscription_config(
            _config(
                pricing_catalog={
                    "default_group_id": "core",
                    "groups": [{"group_id": "core", "label": "Core", "plan_ids": ["enterprise"]}],
                }
            )
        )


def test_the_runtime_contract_is_unchanged() -> None:
    """The loader stays strict; only the factory boundary normalizes.

    A hand-written config that declares an empty catalog is still a mistake
    worth reporting, so this fix must not be implemented by relaxing the model.
    """
    from mozaiksai.core.runtime.app.subscriptions_loader import PricingCatalogDef

    with pytest.raises(ValueError, match="groups must be non-empty"):
        PricingCatalogDef.model_validate({"groups": []})

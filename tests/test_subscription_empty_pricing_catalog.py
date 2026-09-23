"""An optional pricing catalog degrades to what is valid; it never fails the contract.

`pricing_catalog` is display metadata for pricing tabs. The runtime never reads
it to decide entitlement -- `ConfiguredEntitlementAdapter` answers from
`plans[].capabilities`. But `PricingCatalogDef` and the config-level validators
reject ten distinct malformations in it, and each one fails the whole
contract, so a monetized build dies over which tab opens first.

Two live acceptance runs, two different malformations, the same optional field:

    82f87eb3        {"default_group_id": null, "groups": []}
    (#711)          -> "pricing_catalog.groups must be non-empty"
                       3 identical retries -> attempts_exhausted

    82f87eb3+#349   {"default_group_id": "default",
                     "groups": [{"group_id": "basic", ...}]}
                    -> "default_group_id 'default' must reference a declared
                        pricing_catalog group_id; known group_ids: ['basic']"
                       3 retries -> attempts_exhausted

#711 fixed the first and deliberately kept the second fatal --
`test_a_malformed_catalog_is_still_refused` asserted exactly the failure that
then blocked the next run. That line was drawn in the wrong place: it sorted by
"empty vs malformed" when the question is whether the field carries app
meaning. Dropping an unresolvable tab preference loses nothing a user can
observe. Failing the build loses the whole contract, including the plans and
capabilities that were correct.

So the catalog now degrades to a valid subset. Anything that carries contract
meaning -- plans, capabilities, wallets, assignment_store -- stays strict, and
the runtime loader is untouched: a hand-written config with a dangling default
is still a mistake worth reporting there.
"""

from __future__ import annotations

import pytest

from factory_app.workflows.SubscriptionContractDesigner.tools.save_subscription_contract import (
    _normalize_subscription_config,
)

PLANS = [
    {"plan_id": "free", "label": "Free", "capabilities": ["tasks.basic"]},
    {"plan_id": "pro", "label": "Pro", "capabilities": ["tasks.unlimited"]},
]


def _config(**overrides) -> dict:
    return {"label": "TaskTracker Pro", "default_plan_id": "free", "plans": PLANS, **overrides}


def _catalog(result: dict) -> dict | None:
    return result.get("pricing_catalog")


def test_the_first_live_payload_is_accepted() -> None:
    """Run at 82f87eb3: an empty catalog is an absent catalog (#711)."""
    result = _normalize_subscription_config(
        _config(pricing_catalog={"default_group_id": None, "groups": []})
    )
    assert _catalog(result) is None


def test_the_second_live_payload_is_accepted() -> None:
    """Run at 82f87eb3+#349: a default naming no declared group is simply no default."""
    result = _normalize_subscription_config(
        _config(
            pricing_catalog={
                "default_group_id": "default",
                "groups": [{"group_id": "basic", "label": "Basic", "plan_ids": ["free", "pro"]}],
            }
        )
    )
    catalog = _catalog(result)
    assert catalog is not None, "the groups were valid; only the tab preference was not"
    assert catalog.get("default_group_id") is None
    assert [group["group_id"] for group in catalog["groups"]] == ["basic"]
    assert catalog["groups"][0]["plan_ids"] == ["free", "pro"], "the usable part is kept whole"


def test_a_group_keeps_the_plans_that_exist() -> None:
    result = _normalize_subscription_config(
        _config(
            pricing_catalog={
                "default_group_id": "core",
                "groups": [{"group_id": "core", "label": "Core", "plan_ids": ["free", "ghost"]}],
            }
        )
    )
    assert _catalog(result)["groups"][0]["plan_ids"] == ["free"]


def test_a_group_that_lists_nothing_declared_is_dropped() -> None:
    """A tab with no resolvable contents has nothing to render."""
    result = _normalize_subscription_config(
        _config(
            pricing_catalog={
                "default_group_id": "core",
                "groups": [{"group_id": "core", "label": "Core", "plan_ids": ["ghost"]}],
            }
        )
    )
    assert _catalog(result) is None


def test_duplicate_group_ids_keep_the_first() -> None:
    result = _normalize_subscription_config(
        _config(
            pricing_catalog={
                "default_group_id": "core",
                "groups": [
                    {"group_id": "core", "label": "A", "plan_ids": ["free"]},
                    {"group_id": "core", "label": "B", "plan_ids": ["pro"]},
                ],
            }
        )
    )
    catalog = _catalog(result)
    assert [group["group_id"] for group in catalog["groups"]] == ["core"]
    assert catalog["groups"][0]["plan_ids"] == ["free"]


def test_a_valid_catalog_is_preserved_whole() -> None:
    """Degrading must not become a way to quietly discard correct display metadata."""
    result = _normalize_subscription_config(
        _config(
            pricing_catalog={
                "default_group_id": "core",
                "groups": [{"group_id": "core", "label": "Core", "plan_ids": ["free", "pro"]}],
            }
        )
    )
    catalog = _catalog(result)
    assert catalog["default_group_id"] == "core"
    assert catalog["groups"][0]["plan_ids"] == ["free", "pro"]


def test_an_explicit_null_still_works() -> None:
    assert _catalog(_normalize_subscription_config(_config(pricing_catalog=None))) is None


@pytest.mark.parametrize(
    "catalog",
    [
        {"default_group_id": None, "groups": []},
        {"default_group_id": "default", "groups": [{"group_id": "basic", "label": "Basic", "plan_ids": ["free"]}]},
        {"default_group_id": "core", "groups": [{"group_id": "core", "label": "Core", "plan_ids": ["ghost"]}]},
        {"groups": [{"group_id": "core", "label": "Core", "plan_ids": ["free"], "add_on_ids": ["nope"]}]},
        {"groups": [{"group_id": "core", "plan_ids": ["free"]}]},  # no label
    ],
)
def test_the_contract_survives_every_catalog_malformation(catalog: dict) -> None:
    """The point of the whole change: plans and capabilities reach the build regardless."""
    result = _normalize_subscription_config(_config(pricing_catalog=catalog))
    assert [plan["plan_id"] for plan in result["plans"]] == ["free", "pro"]
    assert result["plans"][1]["capabilities"] == ["tasks.unlimited"]


def test_the_runtime_contract_is_unchanged() -> None:
    """Only the factory boundary degrades. A hand-written config is still held strict.

    This is what stops the fix from being implemented by loosening the loader,
    which would hide real mistakes in a config a person wrote by hand.
    """
    from mozaiksai.core.runtime.app.subscriptions_loader import PricingCatalogDef

    with pytest.raises(ValueError, match="groups must be non-empty"):
        PricingCatalogDef.model_validate({"groups": []})
    with pytest.raises(ValueError, match="must reference a declared"):
        PricingCatalogDef.model_validate(
            {"default_group_id": "nope", "groups": [{"group_id": "core", "label": "Core"}]}
        )

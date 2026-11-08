"""Drift guards for the generated-app entitlement gate path.

Protects against regressions that would silently break the end-to-end
subscription → entitlement record → feature gate flow when a generated bundle
includes mozaikspay + commerce packs.

Three drift surfaces are guarded:

1. Compiled commerce module.yaml — approved entitlement gates on paid actions.
   Raw templates retain action permissions; approved subscription selections
   determine the final pricing gates.

2. Pack mutual-exclusion contract — mozaikspay declares subscription_write_path
   and the entitlement_dispatch contract forbids co-selection with it.
   If either contract drifts, the planner may include both packs and create a
   duplicate write path that races on subscription state.

3. Subscriptions fixture + ConfiguredEntitlementAdapter — commerce capability IDs
   declared in a fixture subscriptions.yaml are correctly granted/denied by the
   runtime adapter. If the capability ID strings in the compiled module and the
   subscriptions config drift apart, users silently get ENTITLEMENT_REQUIRED even
   with an active paid subscription.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.module_entitlement_gates import (
    apply_entitlement_gates,
)
from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter
from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from tests.appgenerator_subscription_fixture import subscription_contract

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_WORKSPACE = Path(__file__).resolve().parents[1]
_BUILD_CONTEXT = _WORKSPACE / "factory_app" / "build_context"
_COMMERCE_MODULE_YAML = (
    _BUILD_CONTEXT / "commerce" / "templates" / "modules" / "commerce" / "module.yaml"
)
_MOZAIKSPAY_CONTRACT = _BUILD_CONTEXT / "mozaikspay" / "contract.yaml"
_ENTITLEMENT_DISPATCH_CONTRACT = _BUILD_CONTEXT / "entitlement_dispatch" / "contract.yaml"


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeCollection:
    """In-memory collection stub — no real MongoDB required."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records

    async def find_one(
        self,
        query: dict[str, Any],
        projection: dict[str, int] | None = None,
    ) -> dict[str, Any] | None:
        for rec in self._records:
            if all(rec.get(k) == v for k, v in query.items()):
                return dict(rec)
        return None


def _commerce_subscriptions_config() -> SubscriptionsConfig:
    """The approved feature-selection fixture consumed by the gate compiler."""
    return SubscriptionsConfig.model_validate(
        {
            **subscription_contract("commerce", "start_checkout", "create_product")["subscription_config_file"],
            "label": "Commerce SaaS",
            "assignment_store": {
                "data_alias": "billing.subscriptions",
                "user_id_field": "user_id",
                "active_statuses": ["active"],
            },
        }
    )


def _compiled_commerce(*, gated: bool = True) -> dict[str, Any]:
    context = {
        "subscription_contract": subscription_contract("commerce", "start_checkout", "create_product") if gated else None,
        "design_surface_map": {"surfaces": [{
            "surface_id": "commerce", "surface_kind": "module", "owner": "app",
            "owned_mutations": [
                "create_product", "update_product", "archive_product", "adjust_inventory",
                "add_cart_item", "update_cart_item", "remove_cart_item", "clear_cart",
                "start_checkout", "record_checkout_result", "update_order_status",
            ],
            "custom_reads": ["list_products", "get_product", "get_cart", "list_orders", "get_order"],
        }]},
    }
    compiled = apply_entitlement_gates([{
        "filename": "modules/commerce/module.yaml",
        "content": _COMMERCE_MODULE_YAML.read_text(encoding="utf-8"),
    }], context_variables=context)
    return yaml.safe_load(compiled[0]["content"])


def _active_commerce_grant(*, plan_id: str = "pro") -> dict[str, Any]:
    return {
        "app_id": "commerce-app",
        "user_id": "merchant-1",
        "tenant_id": None,
        "workspace_id": None,
        "plan_id": plan_id,
        "status": "active",
        "granted_capabilities": [
            {"capability_id": "feature.module.commerce.start_checkout"},
            {"capability_id": "feature.module.commerce.create_product"},
        ],
    }


# ---------------------------------------------------------------------------
# 1. Compiled commerce module.yaml — entitlement gate and permission drift guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("gated", [False, True])
def test_commerce_compiled_pricing_gates_preserve_permissions(gated: bool) -> None:
    raw = _read_yaml(_COMMERCE_MODULE_YAML)
    assert not any(action.get("entitlement_gate") for action in raw["actions"])
    compiled = _compiled_commerce(gated=gated)
    actions = {action["id"]: action for action in compiled["actions"]}
    assert {key: row["entitlement_gate"] for key, row in actions.items() if row.get("entitlement_gate")} == (
        {key: "feature.module.commerce." + key for key in ("start_checkout", "create_product")} if gated else {}
    )
    assert compiled["permissions"] == raw["permissions"]
    assert {key: row.get("permissions") for key, row in actions.items()} == {
        action["id"]: action.get("permissions") for action in raw["actions"]
    }
    assert actions["start_checkout"]["permissions"] == ["commerce.checkout.start"]
    assert actions["create_product"]["permissions"] == ["commerce.catalog.manage"]


def test_commerce_entitlement_gate_capability_ids_match_approved_plan_capabilities() -> None:
    """Compiled pricing gates resolve to approved plans, separately from permissions."""
    module_yaml = _compiled_commerce()
    declared_cap_ids = {
        capability for plan in _commerce_subscriptions_config().plans for capability in plan.capabilities
    }
    gated_actions = [
        a for a in (module_yaml.get("actions") or []) if a.get("entitlement_gate")
    ]

    assert len(gated_actions) == 2
    for action in gated_actions:
        gate_cap = action["entitlement_gate"]
        assert gate_cap in declared_cap_ids, (
            f"action '{action['id']}' declares entitlement_gate: {gate_cap!r} "
            f"but that capability_id is not in the approved subscription plans. "
            f"Declared: {sorted(declared_cap_ids)}"
        )


# ---------------------------------------------------------------------------
# 2. Pack mutual-exclusion contract drift guard
# ---------------------------------------------------------------------------


def test_mozaikspay_contract_declares_subscription_write_path() -> None:
    """mozaikspay contract.yaml must declare provides_capabilities: [subscription_write_path].

    This is the capability flag the OSS planner reads to decide whether
    entitlement_dispatch must be excluded.  If this field is removed or renamed
    the planner will include both packs, creating a duplicate subscription
    assignment write path.
    """
    contract = _read_yaml(_MOZAIKSPAY_CONTRACT)

    assert "subscription_write_path" in (contract.get("provides_capabilities") or []), (
        "mozaikspay contract.yaml must declare provides_capabilities: [subscription_write_path]. "
        "Removing this causes the OSS planner to include entitlement_dispatch alongside mozaikspay, "
        "creating a duplicate write path for subscription assignments."
    )


def test_entitlement_dispatch_contract_uses_capability_based_skip_not_pack_name() -> None:
    """entitlement_dispatch contract must reference the capability flag, not a hardcoded pack name.

    Any managed pack can replace mozaikspay by declaring the same capability.
    If the contract names 'mozaikspay' directly it creates OSS/product coupling.
    """
    contract = _read_yaml(_ENTITLEMENT_DISPATCH_CONTRACT)
    writer_contract = contract.get("managed_assignment_writer_contract", {})
    selection_text = writer_contract.get("selection_mechanism", "")

    assert "provides_capabilities" in selection_text, (
        "entitlement_dispatch managed_assignment_writer_contract.selection_mechanism "
        "must reference provides_capabilities (the capability flag), not a hardcoded pack name."
    )
    assert "subscription_write_path" in selection_text, (
        "entitlement_dispatch managed_assignment_writer_contract.selection_mechanism "
        "must reference subscription_write_path."
    )
    # Confirm the contract does not hardcode the current default pack name.
    assert "mozaikspay" not in writer_contract, (
        "managed_assignment_writer_contract must not name 'mozaikspay' directly — "
        "use provides_capabilities: [subscription_write_path] so any compliant pack can replace it."
    )


def test_entitlement_dispatch_cross_pack_integration_forbids_co_selection() -> None:
    """cross_pack_integrations must state that entitlement_dispatch and any subscription_write_path
    provider must NOT be co-selected.

    If this constraint is removed the planner no longer has a machine-readable signal
    to exclude entitlement_dispatch when a managed pack is selected.
    """
    contract = _read_yaml(_ENTITLEMENT_DISPATCH_CONTRACT)
    cross_pack = contract.get("cross_pack_integrations", [])

    subscription_write_path_entry = next(
        (item for item in cross_pack if item.get("provides_capability") == "subscription_write_path"),
        None,
    )
    assert subscription_write_path_entry is not None, (
        "entitlement_dispatch contract.yaml cross_pack_integrations must have an entry with "
        "provides_capability: subscription_write_path describing the mutual exclusion rule."
    )

    description_text = subscription_write_path_entry.get("description", "")
    assert "NOT" in description_text or "must not" in description_text.lower() or "must NOT" in description_text, (
        "The subscription_write_path cross_pack_integrations entry must explicitly state "
        "that entitlement_dispatch must NOT be included alongside the provider."
    )


# ---------------------------------------------------------------------------
# 3. Subscriptions fixture + ConfiguredEntitlementAdapter — capability ID alignment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_merchant_subscription_grants_commerce_checkout_start() -> None:
    """A subscription record granting commerce capabilities allows start_checkout.

    This uses the gate from the compiled manifest so a mismatch with the approved
    subscription plan fails at the runtime adapter.
    """
    config = _commerce_subscriptions_config()
    collection = _FakeCollection([_active_commerce_grant()])
    adapter = ConfiguredEntitlementAdapter(
        config=config,
        collection_resolver=lambda alias: collection,
    )

    action = next(action for action in _compiled_commerce()["actions"] if action["id"] == "start_checkout")
    result = await adapter.check(
        action["entitlement_gate"],
        app_id="commerce-app",
        user_id="merchant-1",
    )

    assert result.granted is True
    assert result.reason == "active_subscription"


@pytest.mark.asyncio
async def test_active_merchant_subscription_grants_commerce_products_manage() -> None:
    """An active subscription grants the compiled create_product gate."""
    config = _commerce_subscriptions_config()
    collection = _FakeCollection([_active_commerce_grant()])
    adapter = ConfiguredEntitlementAdapter(
        config=config,
        collection_resolver=lambda alias: collection,
    )

    action = next(action for action in _compiled_commerce()["actions"] if action["id"] == "create_product")
    result = await adapter.check(
        action["entitlement_gate"],
        app_id="commerce-app",
        user_id="merchant-1",
    )

    assert result.granted is True
    assert result.reason == "active_subscription"


@pytest.mark.asyncio
async def test_free_plan_user_denied_commerce_checkout_start() -> None:
    """A user with no subscription record is denied the paid checkout feature.

    The free plan declares no capabilities, so the default plan check must deny.
    This is the denial half of the gate: without an active paid subscription the
    start_checkout action must be unreachable.
    """
    config = _commerce_subscriptions_config()
    collection = _FakeCollection([])  # No assignment record
    adapter = ConfiguredEntitlementAdapter(
        config=config,
        collection_resolver=lambda alias: collection,
    )

    action = next(action for action in _compiled_commerce()["actions"] if action["id"] == "start_checkout")
    result = await adapter.check(
        action["entitlement_gate"],
        app_id="commerce-app",
        user_id="free-user",
    )

    assert result.granted is False


@pytest.mark.asyncio
async def test_cancelled_merchant_subscription_denied_commerce_checkout_start() -> None:
    """A cancelled subscription no longer grants the paid checkout feature.

    This tests the expiry/cancellation half: once a subscription is cancelled
    the entitlement gate must deny the user even if the record still exists.
    """
    config = _commerce_subscriptions_config()
    cancelled_record = {**_active_commerce_grant(), "status": "cancelled"}
    collection = _FakeCollection([cancelled_record])
    adapter = ConfiguredEntitlementAdapter(
        config=config,
        collection_resolver=lambda alias: collection,
    )

    action = next(action for action in _compiled_commerce()["actions"] if action["id"] == "start_checkout")
    result = await adapter.check(
        action["entitlement_gate"],
        app_id="commerce-app",
        user_id="merchant-1",
    )

    assert result.granted is False


@pytest.mark.asyncio
async def test_commerce_capability_ids_are_not_in_free_plan_default() -> None:
    """Commerce paid capabilities must not appear in the free default plan.

    If paid checkout or product-management features were accidentally added to
    the free plan, the entitlement gate would become a no-op for all users.
    This test validates the fixture contract, not the runtime adapter.
    """
    config = _commerce_subscriptions_config()
    free_plan = next(p for p in config.plans if p.plan_id == "free")
    free_caps = set(free_plan.capabilities)

    assert "feature.module.commerce.start_checkout" not in free_caps, (
        "The paid checkout feature must not be in the free plan — "
        "it must require an active paid subscription."
    )
    assert "feature.module.commerce.create_product" not in free_caps, (
        "The paid product-management feature must not be in the free plan — "
        "it must require an active paid subscription."
    )

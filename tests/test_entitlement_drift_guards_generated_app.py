"""Drift guards for the generated-app entitlement gate path.

Protects against regressions that would silently break the end-to-end
subscription → entitlement record → feature gate flow when a generated bundle
includes mozaikspay + commerce packs.

Three drift surfaces are guarded:

1. Commerce module.yaml template — entitlement_gate wiring on paid actions.
   If a future edit removes or renames entitlement_gate on start_checkout or
   create_product, those actions become unguarded and any user can call them.

2. Pack mutual-exclusion contract — mozaikspay declares subscription_write_path
   and the entitlement_dispatch contract forbids co-selection with it.
   If either contract drifts, the planner may include both packs and create a
   duplicate write path that races on subscription state.

3. Subscriptions fixture + ConfiguredEntitlementAdapter — commerce capability IDs
   declared in a fixture subscriptions.yaml are correctly granted/denied by the
   runtime adapter.  If the capability ID strings in the module template and the
   subscriptions config drift apart, users silently get ENTITLEMENT_REQUIRED even
   with an active paid subscription.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter
from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig

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
    """Fixture subscriptions.yaml with commerce capability IDs in a paid plan."""
    return SubscriptionsConfig.model_validate(
        {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "Commerce SaaS",
            "default_plan_id": "free",
            "assignment_store": {
                "data_alias": "billing.subscriptions",
                "user_id_field": "user_id",
                "active_statuses": ["active"],
            },
            "plans": [
                {
                    "plan_id": "free",
                    "label": "Free",
                    "capabilities": [],
                },
                {
                    "plan_id": "merchant",
                    "label": "Merchant",
                    "capabilities": [
                        "commerce.checkout.start",
                        "commerce.products.manage",
                        "commerce.orders.manage",
                    ],
                },
            ],
        }
    )


def _active_commerce_grant(*, plan_id: str = "merchant") -> dict[str, Any]:
    return {
        "app_id": "commerce-app",
        "user_id": "merchant-1",
        "tenant_id": None,
        "workspace_id": None,
        "plan_id": plan_id,
        "status": "active",
        "granted_capabilities": [
            {"capability_id": "commerce.checkout.start"},
            {"capability_id": "commerce.products.manage"},
            {"capability_id": "commerce.orders.manage"},
        ],
    }


# ---------------------------------------------------------------------------
# 1. Commerce module.yaml template — entitlement_gate drift guard
# ---------------------------------------------------------------------------


def test_commerce_start_checkout_declares_entitlement_gate() -> None:
    """start_checkout must declare entitlement_gate: commerce.checkout.start.

    Without this gate any unauthenticated user can start a checkout session
    on any generated commerce app regardless of their subscription tier.
    """
    module_yaml = _read_yaml(_COMMERCE_MODULE_YAML)
    actions = {a["id"]: a for a in module_yaml.get("actions") or []}

    assert "start_checkout" in actions, "commerce module.yaml must declare start_checkout action"
    gate = actions["start_checkout"].get("entitlement_gate")
    assert gate == "commerce.checkout.start", (
        f"start_checkout must declare entitlement_gate: commerce.checkout.start, got {gate!r}"
    )


def test_commerce_create_product_declares_entitlement_gate() -> None:
    """create_product must declare entitlement_gate: commerce.products.manage.

    Without this gate any user (including free-tier) can create products,
    bypassing the subscription requirement for merchant capabilities.
    """
    module_yaml = _read_yaml(_COMMERCE_MODULE_YAML)
    actions = {a["id"]: a for a in module_yaml.get("actions") or []}

    assert "create_product" in actions, "commerce module.yaml must declare create_product action"
    gate = actions["create_product"].get("entitlement_gate")
    assert gate == "commerce.products.manage", (
        f"create_product must declare entitlement_gate: commerce.products.manage, got {gate!r}"
    )


def test_commerce_entitlement_gate_capability_ids_match_declared_capabilities() -> None:
    """The entitlement_gate values on actions must be declared in the module's capabilities list.

    If the gate capability ID and the declared capability ID drift apart the gate
    will always deny because ConfiguredEntitlementAdapter checks the granted list,
    not the action permission string.
    """
    module_yaml = _read_yaml(_COMMERCE_MODULE_YAML)
    declared_cap_ids = {
        cap["capability_id"] for cap in (module_yaml.get("capabilities") or [])
    }
    gated_actions = [
        a for a in (module_yaml.get("actions") or []) if a.get("entitlement_gate")
    ]

    for action in gated_actions:
        gate_cap = action["entitlement_gate"]
        assert gate_cap in declared_cap_ids, (
            f"action '{action['id']}' declares entitlement_gate: {gate_cap!r} "
            f"but that capability_id is not in the module capabilities list. "
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

    This test uses the actual capability ID strings from the commerce module.yaml
    template and verifies the runtime adapter reads them correctly.  If the capability
    IDs in the module template drift from the subscriptions.yaml fixture, this test
    will catch it.
    """
    config = _commerce_subscriptions_config()
    collection = _FakeCollection([_active_commerce_grant()])
    adapter = ConfiguredEntitlementAdapter(
        config=config,
        collection_resolver=lambda alias: collection,
    )

    result = await adapter.check(
        "commerce.checkout.start",
        app_id="commerce-app",
        user_id="merchant-1",
    )

    assert result.granted is True
    assert result.reason == "active_subscription"


@pytest.mark.asyncio
async def test_active_merchant_subscription_grants_commerce_products_manage() -> None:
    """Merchant subscription grants commerce.products.manage (required by create_product gate)."""
    config = _commerce_subscriptions_config()
    collection = _FakeCollection([_active_commerce_grant()])
    adapter = ConfiguredEntitlementAdapter(
        config=config,
        collection_resolver=lambda alias: collection,
    )

    result = await adapter.check(
        "commerce.products.manage",
        app_id="commerce-app",
        user_id="merchant-1",
    )

    assert result.granted is True
    assert result.reason == "active_subscription"


@pytest.mark.asyncio
async def test_free_plan_user_denied_commerce_checkout_start() -> None:
    """A user with no subscription record is denied commerce.checkout.start.

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

    result = await adapter.check(
        "commerce.checkout.start",
        app_id="commerce-app",
        user_id="free-user",
    )

    assert result.granted is False


@pytest.mark.asyncio
async def test_cancelled_merchant_subscription_denied_commerce_checkout_start() -> None:
    """A cancelled subscription no longer grants commerce.checkout.start.

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

    result = await adapter.check(
        "commerce.checkout.start",
        app_id="commerce-app",
        user_id="merchant-1",
    )

    assert result.granted is False


@pytest.mark.asyncio
async def test_commerce_capability_ids_are_not_in_free_plan_default() -> None:
    """Commerce paid capabilities must not appear in the free default plan.

    If commerce.checkout.start or commerce.products.manage were accidentally
    added to the free plan, the entitlement gate would become a no-op for all users.
    This test validates the fixture contract, not the runtime adapter.
    """
    config = _commerce_subscriptions_config()
    free_plan = next(p for p in config.plans if p.plan_id == "free")
    free_caps = set(free_plan.capabilities)

    assert "commerce.checkout.start" not in free_caps, (
        "commerce.checkout.start must not be in the free plan — "
        "it must require an active paid subscription."
    )
    assert "commerce.products.manage" not in free_caps, (
        "commerce.products.manage must not be in the free plan — "
        "it must require an active paid subscription."
    )

from __future__ import annotations

import inspect
from typing import Any

import pytest

from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter
from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig


class FakeCollection:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.queries: list[dict[str, Any]] = []

    async def find_one(
        self,
        query: dict[str, Any],
        projection: dict[str, int] | None = None,
    ) -> dict[str, Any] | None:
        self.queries.append(query)
        for record in self.records:
            if all(record.get(key) == value for key, value in query.items()):
                return dict(record)
        return None


def _config() -> SubscriptionsConfig:
    return SubscriptionsConfig.model_validate(
        {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "Test SaaS",
            "default_plan_id": "free",
            "assignment_store": {
                "data_alias": "billing.subscriptions",
                "workspace_id_field": "workspace_id",
                "active_statuses": ["active", "pending"],
            },
            "plans": [
                {
                    "plan_id": "free",
                    "label": "Free",
                    "capabilities": ["dashboard.view"],
                },
                {
                    "plan_id": "pro",
                    "label": "Pro",
                    "capabilities": ["dashboard.view", "reports.export"],
                },
            ],
        }
    )


def test_platform_loader_uses_configured_entitlement_adapter_only() -> None:
    from mozaiksai.hosts import platform

    source = inspect.getsource(platform._load_entitlement_adapter)
    assert "app.services.adapters.entitlements" not in source
    assert "grant_adapter" not in source

    adapter = platform._load_entitlement_adapter(config=_config())

    assert isinstance(adapter, ConfiguredEntitlementAdapter)


@pytest.mark.asyncio
async def test_default_plan_capability_grants_when_no_assignment() -> None:
    collection = FakeCollection([])
    adapter = ConfiguredEntitlementAdapter(
        config=_config(),
        collection_resolver=lambda alias: collection,
    )

    result = await adapter.check("dashboard.view", app_id="app-1")

    assert result.granted is True
    assert result.reason == "default_plan"


@pytest.mark.asyncio
async def test_assignment_grants_capability_from_granted_capabilities() -> None:
    collection = FakeCollection(
        [
            {
                "app_id": "app-1",
                "tenant_id": None,
                "workspace_id": None,
                "plan_id": "pro",
                "status": "active",
                "granted_capabilities": [
                    {"pack_id": "reports", "capability_id": "reports.export"},
                ],
            }
        ]
    )
    adapter = ConfiguredEntitlementAdapter(
        config=_config(),
        collection_resolver=lambda alias: collection,
    )

    result = await adapter.check("reports.export", app_id="app-1")

    assert result.granted is True
    assert result.reason == "active_subscription"


@pytest.mark.asyncio
async def test_plan_snapshot_capabilities_take_precedence() -> None:
    collection = FakeCollection(
        [
            {
                "app_id": "app-1",
                "tenant_id": None,
                "workspace_id": None,
                "plan_id": "pro",
                "status": "active",
                "granted_capabilities": [
                    {"pack_id": "reports", "capability_id": "reports.export"},
                ],
                "plan_snapshot": {
                    "granted_capabilities": [
                        {"pack_id": "billing", "capability_id": "billing.manage"},
                    ],
                },
            }
        ]
    )
    adapter = ConfiguredEntitlementAdapter(
        config=_config(),
        collection_resolver=lambda alias: collection,
    )

    granted = await adapter.check("billing.manage", app_id="app-1")
    stale = await adapter.check("reports.export", app_id="app-1")

    assert granted.granted is True
    assert stale.granted is False


@pytest.mark.asyncio
async def test_inactive_assignment_denies() -> None:
    collection = FakeCollection(
        [
            {
                "app_id": "app-1",
                "tenant_id": None,
                "workspace_id": None,
                "plan_id": "pro",
                "status": "cancelled",
                "granted_capabilities": [
                    {"pack_id": "reports", "capability_id": "reports.export"},
                ],
            }
        ]
    )
    adapter = ConfiguredEntitlementAdapter(
        config=_config(),
        collection_resolver=lambda alias: collection,
    )

    result = await adapter.check("reports.export", app_id="app-1")

    assert result.granted is False
    assert result.reason == "inactive_subscription"


@pytest.mark.asyncio
async def test_expired_assignment_denies() -> None:
    collection = FakeCollection(
        [
            {
                "app_id": "app-1",
                "tenant_id": None,
                "workspace_id": None,
                "plan_id": "pro",
                "status": "active",
                "expires_at": "2000-01-01T00:00:00+00:00",
                "granted_capabilities": [
                    {"pack_id": "reports", "capability_id": "reports.export"},
                ],
            }
        ]
    )
    adapter = ConfiguredEntitlementAdapter(
        config=_config(),
        collection_resolver=lambda alias: collection,
    )

    result = await adapter.check("reports.export", app_id="app-1")

    assert result.granted is False
    assert result.reason == "expired"


@pytest.mark.asyncio
async def test_tenant_specific_assignment_is_preferred() -> None:
    collection = FakeCollection(
        [
            {
                "app_id": "app-1",
                "tenant_id": None,
                "workspace_id": None,
                "plan_id": "free",
                "status": "active",
                "granted_capabilities": [],
            },
            {
                "app_id": "app-1",
                "tenant_id": "tenant-1",
                "workspace_id": None,
                "plan_id": "pro",
                "status": "active",
                "granted_capabilities": [
                    {"pack_id": "reports", "capability_id": "reports.export"},
                ],
            },
        ]
    )
    adapter = ConfiguredEntitlementAdapter(
        config=_config(),
        collection_resolver=lambda alias: collection,
    )

    result = await adapter.check(
        "reports.export",
        app_id="app-1",
        tenant_id="tenant-1",
    )

    assert result.granted is True
    assert collection.queries[0]["tenant_id"] == "tenant-1"

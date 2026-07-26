from __future__ import annotations

"""Configured subscription entitlement checks for SaaS app workspaces."""

import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.ports.entitlement import EntitlementResult
from mozaiksai.core.runtime.app.subscriptions_loader import (
    ProductDef,
    SubscriptionAssignmentStoreDef,
    SubscriptionsConfig,
)
from mozaiksai.core.runtime.persistence.app_data import collection_name_for_alias
from mozaiksai.core.runtime.persistence.mongo import DEFAULT_APP_DATABASE_NAME

CollectionResolver = Callable[[str], Any]


def _default_database_name() -> str:
    return (
        os.getenv("MOZAIKS_APP_DATA_DATABASE_NAME")
        or os.getenv("MOZAIKS_APP_DATABASE_NAME")
        or os.getenv("MOZAIKS_APPS_DATABASE")
        or DEFAULT_APP_DATABASE_NAME
    ).strip() or DEFAULT_APP_DATABASE_NAME


def _field_value(record: Mapping[str, Any], field_name: str | None, default: Any = None) -> Any:
    if not field_name:
        return default
    current: Any = record
    for part in field_name.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return default
        current = current[part]
    return current


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _capability_ids(raw_capabilities: Any) -> frozenset[str]:
    if not isinstance(raw_capabilities, list):
        return frozenset()
    capability_ids: set[str] = set()
    for item in raw_capabilities:
        if isinstance(item, str):
            capability_id = item.strip()
        elif isinstance(item, Mapping):
            capability_id = str(item.get("capability_id") or "").strip()
        else:
            capability_id = ""
        if capability_id:
            capability_ids.add(capability_id)
    return frozenset(capability_ids)


class ConfiguredEntitlementAdapter:
    """Entitlement adapter backed by ``config/subscriptions.yaml``.

    The adapter is provider-neutral. It does not create subscriptions, call
    billing providers, or mutate grant state. It reads a configured app data
    alias for the current subscription assignment and checks the assignment's
    snapshotted granted capabilities before falling back to the plan catalog.
    """

    def __init__(
        self,
        *,
        config: SubscriptionsConfig | None,
        collection_resolver: CollectionResolver | None = None,
    ) -> None:
        self._config = config
        self._collection_resolver = collection_resolver

    async def check(
        self,
        capability_id: str,
        *,
        app_id: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> EntitlementResult:
        capability_id = str(capability_id or "").strip()
        app_id = str(app_id or "").strip()
        if not self._config or not capability_id or not app_id:
            return EntitlementResult(granted=False, reason="not_configured")

        # v2: multi-product — union capabilities across all products
        if self._config.products:
            best_reason = "no_grant"
            best_expires_at = None
            for product in self._config.products:
                result = await self._check_product(
                    product, capability_id,
                    app_id=app_id, user_id=user_id,
                    tenant_id=tenant_id, workspace_id=workspace_id,
                )
                if result.granted:
                    return result
                # Prefer "expired" over generic "no_grant" so callers can surface expiry UI.
                if result.reason == "expired" and best_reason != "expired":
                    best_reason = "expired"
                    best_expires_at = result.expires_at
            return EntitlementResult(granted=False, reason=best_reason, expires_at=best_expires_at)

        # v1: single assignment store (existing logic preserved exactly)
        store = self._config.assignment_store
        if store is None:
            return self._check_default_plan(capability_id)

        try:
            record = await self._find_assignment(
                store,
                app_id=app_id,
                user_id=user_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
            if not record:
                return self._check_default_plan(capability_id)

            status = str(_field_value(record, store.status_field, "") or "").strip().lower()
            if status not in {s.lower() for s in store.active_statuses}:
                return EntitlementResult(
                    granted=False,
                    reason="inactive_subscription" if status else "subscription_status_missing",
                    expires_at=self._expires_at(record, store),
                )

            expires_at = self._expires_at(record, store)
            parsed_expiry = _parse_datetime(expires_at)
            if parsed_expiry is not None and parsed_expiry <= datetime.now(UTC):
                return EntitlementResult(granted=False, reason="expired", expires_at=expires_at)

            capabilities = self._capabilities_for_record(record, store)
            if capability_id in capabilities:
                return EntitlementResult(
                    granted=True,
                    reason="active_subscription",
                    expires_at=expires_at,
                )

            return EntitlementResult(granted=False, reason="no_grant", expires_at=expires_at)
        except Exception:
            return EntitlementResult(granted=False, reason="error")

    async def current_plan_id(
        self,
        *,
        app_id: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> str | None:
        """Return the effective active plan id for this scope.

        The default plan is returned when no assignment store exists or no
        active assignment is found. Active assignment plan IDs are returned as
        stored so operator-authored catalogs can snapshot plans that are not in
        the static app config fallback.
        """

        app_id = str(app_id or "").strip()
        if not self._config or not app_id:
            return None

        # v2: return the primary (default) product's active plan
        if self._config.products:
            primary_product = None
            if self._config.default_product_id:
                for p in self._config.products:
                    if p.product_id == self._config.default_product_id:
                        primary_product = p
                        break
            if primary_product is None and self._config.products:
                primary_product = self._config.products[0]
            if primary_product is None:
                return None
            store = primary_product.assignment_store
            if store is None:
                return primary_product.default_plan_id
            try:
                record = await self._find_assignment(
                    store, app_id=app_id, user_id=user_id,
                    tenant_id=tenant_id, workspace_id=workspace_id,
                )
                if not record:
                    return primary_product.default_plan_id
                status = str(_field_value(record, store.status_field, "") or "").strip().lower()
                if status not in {s.lower() for s in store.active_statuses}:
                    return primary_product.default_plan_id
                parsed_expiry = _parse_datetime(self._expires_at(record, store))
                if parsed_expiry is not None and parsed_expiry <= datetime.now(UTC):
                    return primary_product.default_plan_id
                plan_id = str(_field_value(record, store.plan_id_field, primary_product.default_plan_id) or "").strip()
                return plan_id or primary_product.default_plan_id
            except Exception:
                return primary_product.default_plan_id

        # v1: existing behavior
        store = self._config.assignment_store
        if store is None:
            return self._config.default_plan_id

        try:
            record = await self._find_assignment(
                store,
                app_id=app_id,
                user_id=user_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
            if not record:
                return self._config.default_plan_id

            status = str(_field_value(record, store.status_field, "") or "").strip().lower()
            if status not in {s.lower() for s in store.active_statuses}:
                return self._config.default_plan_id

            parsed_expiry = _parse_datetime(self._expires_at(record, store))
            if parsed_expiry is not None and parsed_expiry <= datetime.now(UTC):
                return self._config.default_plan_id

            plan_id = str(_field_value(record, store.plan_id_field, self._config.default_plan_id) or "").strip()
            return plan_id or self._config.default_plan_id
        except Exception:
            return self._config.default_plan_id

    def _check_default_plan(self, capability_id: str) -> EntitlementResult:
        if self._config is None:
            raise RuntimeError("EntitlementAdapter config not initialized")
        capabilities = self._config.capabilities_for_plan(self._config.default_plan_id)
        if capability_id in capabilities:
            return EntitlementResult(granted=True, reason="default_plan")
        return EntitlementResult(granted=False, reason="no_grant")

    async def _check_product(
        self,
        product: "ProductDef",
        capability_id: str,
        *,
        app_id: str,
        user_id: str | None,
        tenant_id: str | None,
        workspace_id: str | None,
    ) -> EntitlementResult:
        """Check a single product's assignment store for a capability grant."""
        store = product.assignment_store
        if store is None:
            # No store: grant capabilities from the product's default plan
            caps = product.capabilities_for_plan(product.default_plan_id)
            if capability_id in caps:
                return EntitlementResult(granted=True, reason="default_plan")
            return EntitlementResult(granted=False, reason="no_grant")

        try:
            record = await self._find_assignment(
                store,
                app_id=app_id,
                user_id=user_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
            if not record:
                caps = product.capabilities_for_plan(product.default_plan_id)
                if capability_id in caps:
                    return EntitlementResult(granted=True, reason="default_plan")
                return EntitlementResult(granted=False, reason="no_grant")

            status = str(_field_value(record, store.status_field, "") or "").strip().lower()
            if status not in {s.lower() for s in store.active_statuses}:
                return EntitlementResult(
                    granted=False,
                    reason="inactive_subscription" if status else "subscription_status_missing",
                    expires_at=self._expires_at(record, store),
                )

            expires_at = self._expires_at(record, store)
            parsed_expiry = _parse_datetime(expires_at)
            if parsed_expiry is not None and parsed_expiry <= datetime.now(UTC):
                return EntitlementResult(granted=False, reason="expired", expires_at=expires_at)

            # Check capabilities: prefer plan_snapshot, then record caps, then catalog
            snapshot = _field_value(record, store.plan_snapshot_field)
            if isinstance(snapshot, Mapping):
                snap_caps = _capability_ids(snapshot.get("granted_capabilities"))
                if snap_caps:
                    if capability_id in snap_caps:
                        return EntitlementResult(granted=True, reason="active_subscription", expires_at=expires_at)
                    return EntitlementResult(granted=False, reason="no_grant", expires_at=expires_at)

            record_caps = _capability_ids(_field_value(record, store.capabilities_field))
            if record_caps:
                if capability_id in record_caps:
                    return EntitlementResult(granted=True, reason="active_subscription", expires_at=expires_at)
                return EntitlementResult(granted=False, reason="no_grant", expires_at=expires_at)

            plan_id = str(_field_value(record, store.plan_id_field, product.default_plan_id) or "").strip()
            caps = product.capabilities_for_plan(plan_id or product.default_plan_id)
            if capability_id in caps:
                return EntitlementResult(granted=True, reason="active_subscription", expires_at=expires_at)
            return EntitlementResult(granted=False, reason="no_grant", expires_at=expires_at)

        except Exception:
            return EntitlementResult(granted=False, reason="error")

    async def _collection(self, store: SubscriptionAssignmentStoreDef) -> Any:
        if self._collection_resolver is not None:
            return self._collection_resolver(store.data_alias)
        collection_name = collection_name_for_alias(store.data_alias)
        client = get_mongo_client()
        return client[_default_database_name()][collection_name]

    async def _find_assignment(
        self,
        store: SubscriptionAssignmentStoreDef,
        *,
        app_id: str,
        user_id: str | None,
        tenant_id: str | None,
        workspace_id: str | None,
    ) -> Mapping[str, Any] | None:
        collection = await self._collection(store)
        projection = {"_id": 0}
        for query in self._candidate_queries(
            store,
            app_id=app_id,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        ):
            record = await collection.find_one(query, projection)
            if record:
                return record
        return None

    def _candidate_queries(
        self,
        store: SubscriptionAssignmentStoreDef,
        *,
        app_id: str,
        user_id: str | None,
        tenant_id: str | None,
        workspace_id: str | None,
    ) -> list[dict[str, Any]]:
        base = {store.app_id_field: app_id}
        candidates: list[dict[str, Any]] = []

        def add_query(
            *,
            tenant_value: str | None = None,
            workspace_value: str | None = None,
            user_value: str | None = None,
        ) -> None:
            query = dict(base)
            if store.tenant_id_field:
                query[store.tenant_id_field] = tenant_value
            if store.workspace_id_field:
                query[store.workspace_id_field] = workspace_value
            if store.user_id_field:
                query[store.user_id_field] = user_value
            if query not in candidates:
                candidates.append(query)

        tenant = str(tenant_id or "").strip() or None
        workspace = str(workspace_id or "").strip() or None
        user = str(user_id or "").strip() or None

        if tenant and workspace and user:
            add_query(tenant_value=tenant, workspace_value=workspace, user_value=user)
        if tenant and workspace:
            add_query(tenant_value=tenant, workspace_value=workspace, user_value=None)
        if tenant and user:
            add_query(tenant_value=tenant, workspace_value=None, user_value=user)
        if tenant:
            add_query(tenant_value=tenant, workspace_value=None, user_value=None)
        if workspace and user:
            add_query(tenant_value=None, workspace_value=workspace, user_value=user)
        if workspace:
            add_query(tenant_value=None, workspace_value=workspace, user_value=None)
        if user:
            add_query(tenant_value=None, workspace_value=None, user_value=user)
        add_query(tenant_value=None, workspace_value=None, user_value=None)
        return candidates

    def _capabilities_for_record(
        self,
        record: Mapping[str, Any],
        store: SubscriptionAssignmentStoreDef,
    ) -> frozenset[str]:
        snapshot = _field_value(record, store.plan_snapshot_field)
        if isinstance(snapshot, Mapping):
            snapshot_caps = _capability_ids(snapshot.get("granted_capabilities"))
            if snapshot_caps:
                return snapshot_caps

        record_caps = _capability_ids(_field_value(record, store.capabilities_field))
        if record_caps:
            return record_caps

        plan_id = str(_field_value(record, store.plan_id_field, self._config.default_plan_id) or "").strip()
        return self._config.capabilities_for_plan(plan_id or self._config.default_plan_id)

    def _expires_at(
        self,
        record: Mapping[str, Any],
        store: SubscriptionAssignmentStoreDef,
    ) -> str | None:
        value = _field_value(record, store.expires_at_field)
        if isinstance(value, datetime):
            return value.isoformat()
        if value is None:
            return None
        value_str = str(value).strip()
        return value_str or None


__all__ = ["ConfiguredEntitlementAdapter"]

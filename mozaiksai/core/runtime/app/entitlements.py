from __future__ import annotations

"""Configured subscription entitlement checks for SaaS app workspaces."""

import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.ports.entitlement import EntitlementResult
from mozaiksai.core.runtime.app.subscriptions_loader import (
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
    ) -> EntitlementResult:
        capability_id = str(capability_id or "").strip()
        app_id = str(app_id or "").strip()
        if not self._config or not capability_id or not app_id:
            return EntitlementResult(granted=False, reason="not_configured")

        store = self._config.assignment_store
        if store is None:
            return self._check_default_plan(capability_id)

        try:
            record = await self._find_assignment(
                store,
                app_id=app_id,
                user_id=user_id,
                tenant_id=tenant_id,
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
        store = self._config.assignment_store
        if store is None:
            return self._config.default_plan_id

        try:
            record = await self._find_assignment(
                store,
                app_id=app_id,
                user_id=user_id,
                tenant_id=tenant_id,
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
    ) -> Mapping[str, Any] | None:
        collection = await self._collection(store)
        projection = {"_id": 0}
        for query in self._candidate_queries(
            store,
            app_id=app_id,
            user_id=user_id,
            tenant_id=tenant_id,
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
    ) -> list[dict[str, Any]]:
        base = {store.app_id_field: app_id}
        candidates: list[dict[str, Any]] = []

        def add_query(
            *,
            include_tenant: bool,
            tenant_value: str | None = None,
            include_user: bool,
            user_value: str | None = None,
        ) -> None:
            query = dict(base)
            if store.tenant_id_field and include_tenant:
                query[store.tenant_id_field] = tenant_value
            if store.user_id_field and include_user:
                query[store.user_id_field] = user_value
            if store.workspace_id_field:
                query[store.workspace_id_field] = None
            if query not in candidates:
                candidates.append(query)

        if tenant_id and user_id:
            add_query(include_tenant=True, tenant_value=tenant_id, include_user=True, user_value=user_id)
        if tenant_id:
            add_query(include_tenant=True, tenant_value=tenant_id, include_user=False)
        if user_id:
            add_query(include_tenant=False, include_user=True, user_value=user_id)
        add_query(include_tenant=True, tenant_value=None, include_user=True, user_value=None)
        add_query(include_tenant=False, include_user=False)
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

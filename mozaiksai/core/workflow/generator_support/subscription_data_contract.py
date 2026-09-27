"""Compile subscription assignment storage into the generated app data contract."""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionAssignmentStoreDef
from mozaiksai.core.workflow.generator_support.module_entitlement_gates import (
    subscription_contract_payload,
)

_SURFACE_ID = "subscription_assignments"
_FIELD_TYPES = {
    "app_id_field": "string",
    "tenant_id_field": "string",
    "workspace_id_field": "string",
    "user_id_field": "string",
    "plan_id_field": "string",
    "status_field": "string",
    "starts_at_field": "datetime",
    "expires_at_field": "datetime",
    "capabilities_field": "array",
    "plan_snapshot_field": "object",
    "revision_field": "number",
}


def _assignment_collection(store: SubscriptionAssignmentStoreDef, name: str) -> dict[str, Any]:
    fields: dict[str, dict[str, Any]] = {}
    for attribute, field_type in _FIELD_TYPES.items():
        field = getattr(store, attribute)
        if field is not None:
            fields[field] = {
                "name": field,
                "type": field_type,
                "required": attribute in {"app_id_field", "plan_id_field", "status_field"},
                "default": None,
                "enum": None,
                "nullable": attribute not in {"app_id_field", "plan_id_field", "status_field"},
            }
    lookup_fields = list(dict.fromkeys(field for field in (
        store.app_id_field, store.user_id_field, store.tenant_id_field, store.workspace_id_field,
    ) if field is not None))
    return {
        "name": name,
        "mongo_collection": name,
        "data_alias": store.data_alias,
        "scope": "app",
        "entity": store.data_alias,
        # Assignment subjects may be users, workspaces, tenants, or the app.
        # The entitlement adapter applies the configured subject query; these
        # operational records do not generate ordinary module CRUD surfaces.
        "tenancy": "app_wide",
        "owner_field": None,
        "ownership": {"surface_id": _SURFACE_ID, "surface_kind": "app_policy"},
        "fields": list(fields.values()),
        "indexes": [{
            "name": "assignment_subject",
            "keys": [{"field": field, "order": 1} for field in lookup_fields],
            "unique": True,
        }],
        "search_by": None,
        "lifecycle": {"write_mode": "platform_sync", "migration_policy": "additive_only"},
    }


def ensure_subscription_assignment_stores(
    data_contract: dict[str, Any], subscription_contract: dict[str, Any] | None,
) -> dict[str, Any]:
    """Declare every configured alias without assigning managed state to a facade.

    The subscription contract owns the store identity and field mappings. This
    projection supplies the persistence artifact required by both managed
    fulfillment and the self-hosted entitlement_dispatch writer.
    """
    result = deepcopy(data_contract)
    contract = subscription_contract_payload(subscription_contract)
    if contract is None or not contract.get("contract_required"):
        return result
    config = contract.get("subscription_config_file") or {}
    stores: dict[str, SubscriptionAssignmentStoreDef] = {}
    for owner in [config, *(config.get("products") or [])]:
        if owner.get("assignment_store") is None:
            continue
        store = SubscriptionAssignmentStoreDef.model_validate(owner["assignment_store"])
        previous = stores.get(store.data_alias)
        if previous is not None and any(getattr(previous, field) != getattr(store, field) for field in _FIELD_TYPES):
            raise ValueError(f"Assignment data alias {store.data_alias!r} has conflicting field mappings")
        stores[store.data_alias] = store
    if not stores:
        return result

    aliases = result.setdefault("aliases", [])
    surfaces = result.setdefault("surfaces", [])
    collections = [collection for surface in surfaces for collection in surface.get("collections", [])]
    collections.extend(result.get("shared_collections") or [])
    declared: dict[str, str] = {}

    def declare(alias: str, name: Any) -> None:
        if not isinstance(name, str) or not name.strip() or "\x00" in name or "$" in name or name.startswith("system."):
            raise ValueError(f"Assignment data alias {alias!r} requires a valid literal collection name")
        if alias in declared and declared[alias] != name:
            raise ValueError(f"Assignment data alias {alias!r} has conflicting collection declarations")
        declared[alias] = name

    configured = set(stores)
    for entry in aliases:
        alias = entry.get("alias") or entry.get("data_alias")
        if alias in configured:
            declare(alias, entry.get("collection") or entry.get("mongo_collection"))
    for collection in collections:
        name = collection.get("mongo_collection") or collection.get("collection") or collection.get("name")
        collection_aliases = [collection.get("data_alias"), collection.get("primary_alias")]
        collection_aliases.extend(entry.get("data_alias") or entry.get("alias")
                                  for key in ("shared_with", "read_by") for entry in collection.get(key) or [])
        for alias in collection_aliases:
            if alias in configured:
                declare(alias, name)

    for store in stores.values():
        alias = store.data_alias
        explicitly_mapped = alias in declared
        name = declared.get(alias) or re.sub(r"[.-]", "_", alias)
        declare(alias, name)
        for entry in aliases:
            if (entry.get("collection") or entry.get("mongo_collection")) == name and (entry.get("alias") or entry.get("data_alias")) != alias:
                raise ValueError(f"Assignment data alias {alias!r} conflicts with an existing alias for {name!r}")
        if not any((entry.get("alias") or entry.get("data_alias")) == alias for entry in aliases):
            aliases.append({"alias": alias, "collection": name})
        existing = [collection for collection in collections if (
            collection.get("mongo_collection") or collection.get("collection") or collection.get("name")
        ) == name]
        if existing:
            if not explicitly_mapped or len(existing) != 1 or existing[0].get("data_alias", alias) != alias:
                raise ValueError(f"Assignment data alias {alias!r} conflicts with collection {name!r}")
            existing[0]["mongo_collection"] = name
            continue
        surface = next((item for item in surfaces if item["surface_id"] == _SURFACE_ID), None)
        if surface is None:
            surface = {"surface_id": _SURFACE_ID, "surface_kind": "app_policy", "collections": []}
            surfaces.append(surface)
        if surface["surface_kind"] != "app_policy":
            raise ValueError(f"Assignment store surface {_SURFACE_ID!r} must have kind app_policy")
        collection = _assignment_collection(store, name)
        surface["collections"].append(collection)
        collections.append(collection)
    return result

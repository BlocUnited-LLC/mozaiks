"""Compile generation-time persistence decisions into strict runtime artifacts."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from mozaiksai.core.runtime.persistence.indexes import _normalize_index_spec
from mozaiksai.core.runtime.persistence.migrations import (
    SUPPORTED_MIGRATION_OPERATIONS,
    _validate_migration,
)

from .module_action_inventory import managed_pack_contracts


def _derived_index_name(index: dict[str, Any], path: str) -> str:
    normalized = _normalize_index_spec({**index, "name": "generated"}, path)
    identity = json.dumps(
        {"keys": normalized.keys, "options": normalized.options},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return "idx_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def scope_owned_unique_index(index: dict[str, Any], collection: dict[str, Any], path: str) -> dict[str, Any]:
    """Compound unique keys with the owner field so principals cannot collide or probe.

    A unique key declared on a per_user or per_workspace collection is unique
    within one owner's rows, never across the app. A derived name follows the
    scoped keys; an explicit name is preserved.
    """
    owner_field = collection.get("owner_field")
    if collection.get("tenancy") not in {"per_user", "per_workspace"} or not owner_field or not index.get("unique"):
        return index
    keys = list(index.get("keys") or [])
    fields = [
        key.get("field") if isinstance(key, dict) else key[0] if isinstance(key, (list, tuple)) and key else None
        for key in keys
    ]
    if owner_field in fields:
        return index
    result = deepcopy(index)
    result["keys"] = [{"field": owner_field, "order": 1}, *keys]
    if result.get("name") is None or result["name"] == _derived_index_name(index, path):
        result["name"] = None
    return result


def materialize_index_name(
    index: dict[str, Any], path: str, *, collection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Scope owned unique keys, preserve explicit names, and derive omitted names."""
    result = deepcopy(index)
    if collection is not None:
        result = scope_owned_unique_index(result, collection, path)
    if result.get("name") is None:
        result["name"] = _derived_index_name(result, path)
    _normalize_index_spec(result, path)
    return result


def normalize_data_contract_indexes(data_contract: dict[str, Any]) -> dict[str, Any]:
    """Scope and name indexes before design persistence and when compiling recorded designs."""
    result = deepcopy(data_contract)
    collections = [
        collection for surface in result.get("surfaces") or []
        for collection in surface.get("collections") or []
    ] + list(result.get("shared_collections") or [])
    for collection in collections:
        if "indexes" not in collection:
            continue
        collection["indexes"] = [
            materialize_index_name(
                index, f"data_contract.{collection.get('name')}.indexes[{offset}]", collection=collection,
            )
            for offset, index in enumerate(collection.get("indexes") or [])
        ]
    return result


def managed_data_owners(context_variables: Any) -> set[str]:
    """Read managed state owners from selected pack contracts, never name guesses."""
    owners: set[str] = set()
    for contract in managed_pack_contracts(context_variables):
        owners.update(facade["module_id"] for facade in contract.get("facades") or [])
        for rule in contract.get("surface_ownership") or []:
            owners.update(rule.get("surface_ids") or [])
    return owners


def materialize_data_migrations(
    files: dict[str, str], *, managed_owners: set[str],
) -> dict[str, str]:
    """Stamp migration headers and exclude state belonging to managed providers."""
    result = dict(files)
    for path, content in files.items():
        if not path.startswith("data/migrations/") or not path.endswith(".json"):
            continue
        migration = json.loads(content)
        if not isinstance(migration, dict):
            raise ValueError(f"{path}: migration must be an object")
        if "aliases" in migration or "shared_collections" in migration:
            raise ValueError(f"{path}: collections and aliases belong in data/contract.json; emit additive operations")
        surfaces = migration.pop("surfaces", [])
        if not isinstance(surfaces, list) or any(
            not isinstance(surface, dict) or surface.get("surface_id") not in managed_owners
            for surface in surfaces
        ):
            raise ValueError(f"{path}: collections and aliases belong in data/contract.json; emit additive operations")
        operations = migration.get("operations", [] if surfaces else None)
        if not isinstance(operations, list):
            raise ValueError(f"{path}.operations must be a list")
        compiled_operations = []
        for offset, operation in enumerate(operations):
            location = f"{path}.operations[{offset}]"
            if not isinstance(operation, dict):
                raise ValueError(f"{location} must be an object")
            if operation.get("module_id") in managed_owners:
                continue
            if operation.get("type") not in SUPPORTED_MIGRATION_OPERATIONS:
                raise ValueError(f"{location}.type must be ensure_collection or ensure_index")
            if not operation.get("module_id") or not operation.get("entity_name"):
                raise ValueError(f"{location} requires module_id and entity_name")
            if operation["type"] == "ensure_index":
                index = operation.get("index")
                if not isinstance(index, dict):
                    raise ValueError(f"{location}.index must be an object")
                operation["index"] = materialize_index_name(index, f"{location}.index")
            compiled_operations.append(operation)
        migration["operations"] = compiled_operations
        if migration.get("version") is None:
            migration["version"] = "1"
        if migration.get("schema_version") is None:
            migration["schema_version"] = "mozaiks.data_migration.v1"
        _validate_migration(migration, path)
        result[path] = json.dumps(migration, indent=2, ensure_ascii=False)
    return result

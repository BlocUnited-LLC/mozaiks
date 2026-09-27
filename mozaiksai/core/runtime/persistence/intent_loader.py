from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

DataContract = dict[str, Any]
DataEntityIndex = dict[tuple[str, str], dict[str, Any]]


class DataContractLoadError(ValueError):
    """Raised when data/contract.json is present but invalid."""


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DataContractLoadError(f"{path} must be an object")
    return value


def _require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise DataContractLoadError(f"{path} must be a list")
    return value


def validate_collection_ownership(collection: dict[str, Any], path: str) -> None:
    """Validate the row ownership contract shared by design save and runtime load."""
    if collection.get("scope") not in {"app", "platform", "hosted"}:
        raise DataContractLoadError(f"{path}.scope must be one of ['app', 'platform', 'hosted']")
    if not _is_non_empty_string(collection.get("entity")):
        raise DataContractLoadError(f"{path}.entity must name an explicit surface primary_entities entry")
    tenancy = collection.get("tenancy")
    if tenancy not in {"per_user", "per_workspace", "app_wide"}:
        raise DataContractLoadError(
            f"{path}.tenancy must be one of ['per_user', 'per_workspace', 'app_wide']; "
            "declare owner_field from the collection fields for scoped records, or null for app_wide"
        )
    fields = _require_list(collection.get("fields"), f"{path}.fields")
    names = sorted({
        field["name"] for field in fields
        if isinstance(field, dict) and _is_non_empty_string(field.get("name"))
    })
    owner_field = collection.get("owner_field")
    if tenancy == "app_wide":
        if owner_field is not None:
            raise DataContractLoadError(f"{path}.owner_field must be null for app_wide tenancy")
    elif not _is_non_empty_string(owner_field) or owner_field not in names:
        raise DataContractLoadError(
            f"{path}.owner_field must be one of its declared fields {names} for {tenancy} tenancy"
        )
    elif not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(owner_field)):
        raise DataContractLoadError(f"{path}.owner_field must be a document field identifier")
    elif owner_field in {"app_id", "user_id", "tenant_id", "workspace_id"}:
        expected = {"per_user": "user_id", "per_workspace": "workspace_id"}[tenancy]
        if owner_field != expected:
            raise DataContractLoadError(f"{path}.owner_field {owner_field!r} conflicts with {tenancy}; use {expected!r} or a custom field")
    if "scope_field" in collection or "entity_name" in collection:
        raise DataContractLoadError(f"{path} uses obsolete scope_field/entity_name; declare tenancy, owner_field, and entity")


def load_data_contract(app_root: Path) -> DataContract | None:
    """Load the app data contract as runtime metadata only.

    Loads from the canonical path ``data/contract.json``. Does not apply
    migrations, create indexes, or connect to Mongo.
    """

    root = Path(app_root)
    contract_path = root / "data" / "contract.json"
    if not contract_path.exists():
        return None

    try:
        raw = json.loads(contract_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DataContractLoadError(f"Failed to read data/contract.json: {exc}") from exc

    contract = _require_object(raw, "data_contract")
    _validate_data_contract(contract)
    return contract


def iter_data_contract_collections(
    contract: DataContract | None,
) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """Yield every collection with its declared owner, independent of storage grouping."""
    if contract is None:
        return
    if contract.get("entities") is not None:
        raise DataContractLoadError("data_contract.entities is obsolete; declare collections with entity under surfaces")
    groups: list[tuple[str, str, str, list[Any]]] = []
    kinds: dict[str, str] = {}
    for index, surface in enumerate(_require_list(contract.get("surfaces", []), "data_contract.surfaces")):
        path = f"data_contract.surfaces[{index}]"
        surface = _require_object(surface, path)
        for field in ("surface_id", "surface_kind"):
            if not _is_non_empty_string(surface.get(field)):
                raise DataContractLoadError(f"{path}.{field} is required")
        surface_id, surface_kind = surface["surface_id"], surface["surface_kind"]
        if surface_id in kinds and kinds[surface_id] != surface_kind:
            raise DataContractLoadError(f"{path}.surface_kind conflicts with owner {surface_id!r}")
        kinds[surface_id] = surface_kind
        groups.append((surface_id, surface_kind, f"{path}.collections", _require_list(surface.get("collections"), f"{path}.collections")))
    groups.append(("", "", "data_contract.shared_collections", _require_list(
        contract.get("shared_collections", []), "data_contract.shared_collections",
    )))
    entities: set[tuple[str, str]] = set()
    names: set[tuple[str, str]] = set()
    for group_id, group_kind, group_path, collections in groups:
        for index, value in enumerate(collections):
            path = f"{group_path}[{index}]"
            collection = _require_object(value, path)
            ownership = _require_object(collection.get("ownership", {}), f"{path}.ownership")
            owner_id = ownership.get("surface_id", group_id)
            owner_kind = ownership.get("surface_kind", group_kind)
            if not _is_non_empty_string(owner_id) or not _is_non_empty_string(owner_kind):
                raise DataContractLoadError(f"{path}.ownership requires surface_id and surface_kind")
            if group_id and (owner_id, owner_kind) != (group_id, group_kind):
                raise DataContractLoadError(f"{path}.ownership must match enclosing surface {group_id!r}/{group_kind!r}")
            if owner_id in kinds and kinds[owner_id] != owner_kind:
                raise DataContractLoadError(f"{path}.ownership.surface_kind conflicts with owner {owner_id!r}")
            kinds[owner_id] = owner_kind
            if collection.get("module_id", owner_id) != owner_id:
                raise DataContractLoadError(f"{path}.module_id conflicts with declared owner {owner_id!r}")
            for field, seen in (("entity", entities), ("name", names)):
                if not _is_non_empty_string(collection.get(field)):
                    raise DataContractLoadError(f"{path}.{field} is required")
                key = (str(owner_id), str(collection[field]))
                if key in seen:
                    raise DataContractLoadError(f"{path}.{field} duplicates {owner_id}.{collection[field]}")
                seen.add(key)
            yield str(owner_id), str(owner_kind), collection


def index_data_contract_by_entity(contract: DataContract | None) -> DataEntityIndex:
    """Index surface and shared collections by declared ``(owner_id, entity)``."""
    return {
        (owner_id, collection["entity"]): collection
        for owner_id, _owner_kind, collection in iter_data_contract_collections(contract)
    }


def _validate_data_contract(contract: DataContract) -> None:
    if not _is_non_empty_string(contract.get("version")):
        raise DataContractLoadError("data_contract.version is required")
    _require_list(contract.get("surfaces"), "data_contract.surfaces")
    for owner_id, _owner_kind, collection in iter_data_contract_collections(contract):
        validate_collection_ownership(collection, f"data_contract collection {owner_id}.{collection['name']}")


__all__ = [
    "DataContract",
    "DataContractLoadError",
    "DataEntityIndex",
    "index_data_contract_by_entity",
    "iter_data_contract_collections",
    "validate_collection_ownership",
    "load_data_contract",
]

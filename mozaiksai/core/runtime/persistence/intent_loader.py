from __future__ import annotations

import json
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


def index_data_contract_by_entity(contract: DataContract | None) -> DataEntityIndex:
    """Index loaded data contract by ``(module_id, entity_name)``."""

    if contract is None:
        return {}

    index: DataEntityIndex = {}
    for entity_index, entity in enumerate(contract.get("entities") or []):
        entity_path = f"data_contract.entities[{entity_index}]"
        entity_obj = _require_object(entity, entity_path)
        module_id = str(entity_obj.get("module_id") or "").strip()
        entity_name = str(entity_obj.get("entity_name") or entity_obj.get("name") or "").strip()
        if not module_id:
            raise DataContractLoadError(f"{entity_path}.module_id is required")
        if not entity_name:
            raise DataContractLoadError(f"{entity_path}.entity_name is required")
        index[(module_id, entity_name)] = entity_obj

    for surface_index, surface in enumerate(contract.get("surfaces") or []):
        surface_path = f"data_contract.surfaces[{surface_index}]"
        surface_obj = _require_object(surface, surface_path)
        surface_id = str(surface_obj.get("surface_id") or "").strip()
        surface_kind = str(surface_obj.get("surface_kind") or "").strip()
        for collection_index, collection in enumerate(surface_obj.get("collections") or []):
            collection_path = f"{surface_path}.collections[{collection_index}]"
            collection_obj = _require_object(collection, collection_path)
            ownership = collection_obj.get("ownership") if isinstance(collection_obj.get("ownership"), dict) else {}
            module_id = str(collection_obj.get("module_id") or ownership.get("surface_id") or surface_id).strip()
            entity_name = str(collection_obj.get("entity_name") or collection_obj.get("name") or "").strip()
            if surface_kind == "module" and not module_id:
                raise DataContractLoadError(f"{collection_path}.module_id is required")
            if surface_kind == "module" and not entity_name:
                raise DataContractLoadError(f"{collection_path}.entity_name is required")
            if module_id and entity_name:
                index[(module_id, entity_name)] = collection_obj

    return index


def _validate_data_contract(contract: DataContract) -> None:
    if not _is_non_empty_string(contract.get("version")):
        raise DataContractLoadError("data_contract.version is required")

    surfaces = _require_list(contract.get("surfaces"), "data_contract.surfaces")
    for surface_index, surface in enumerate(surfaces):
        surface_path = f"data_contract.surfaces[{surface_index}]"
        surface_obj = _require_object(surface, surface_path)
        if not _is_non_empty_string(surface_obj.get("surface_id")):
            raise DataContractLoadError(f"{surface_path}.surface_id is required")
        if not _is_non_empty_string(surface_obj.get("surface_kind")):
            raise DataContractLoadError(f"{surface_path}.surface_kind is required")
        collections = _require_list(surface_obj.get("collections"), f"{surface_path}.collections")
        for collection_index, collection in enumerate(collections):
            collection_path = f"{surface_path}.collections[{collection_index}]"
            collection_obj = _require_object(collection, collection_path)
            if surface_obj["surface_kind"] == "module" and not _is_non_empty_string(collection_obj.get("name")):
                raise DataContractLoadError(f"{collection_path}.entity_name is required")

    entities = contract.get("entities")
    if entities is not None:
        _require_list(entities, "data_contract.entities")
    index_data_contract_by_entity(contract)


__all__ = [
    "DataContract",
    "DataContractLoadError",
    "DataEntityIndex",
    "index_data_contract_by_entity",
    "load_data_contract",
]

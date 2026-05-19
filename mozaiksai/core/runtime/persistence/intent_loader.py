from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DatabaseIntent = dict[str, Any]
DatabaseEntityIndex = dict[tuple[str, str], dict[str, Any]]


class DatabaseIntentLoadError(ValueError):
    """Raised when config/database_intent.json is present but invalid."""


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DatabaseIntentLoadError(f"{path} must be an object")
    return value


def _require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise DatabaseIntentLoadError(f"{path} must be a list")
    return value


def load_database_intent(app_root: Path) -> DatabaseIntent | None:
    """Load app/config/database_intent.json as runtime metadata only.

    This does not apply migrations, create indexes, or connect to Mongo.
    """

    intent_path = Path(app_root) / "config" / "database_intent.json"
    if not intent_path.exists():
        return None

    try:
        raw = json.loads(intent_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DatabaseIntentLoadError(f"Failed to read config/database_intent.json: {exc}") from exc

    intent = _require_object(raw, "database_intent")
    _validate_database_intent(intent)
    return intent


def index_database_intent_by_entity(intent: DatabaseIntent | None) -> DatabaseEntityIndex:
    """Index loaded database intent by ``(module_id, entity_name)``."""

    if intent is None:
        return {}

    index: DatabaseEntityIndex = {}
    for entity_index, entity in enumerate(intent.get("entities") or []):
        entity_path = f"database_intent.entities[{entity_index}]"
        entity_obj = _require_object(entity, entity_path)
        module_id = str(entity_obj.get("module_id") or "").strip()
        entity_name = str(entity_obj.get("entity_name") or entity_obj.get("name") or "").strip()
        if not module_id:
            raise DatabaseIntentLoadError(f"{entity_path}.module_id is required")
        if not entity_name:
            raise DatabaseIntentLoadError(f"{entity_path}.entity_name is required")
        index[(module_id, entity_name)] = entity_obj

    for surface_index, surface in enumerate(intent.get("surfaces") or []):
        surface_path = f"database_intent.surfaces[{surface_index}]"
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
                raise DatabaseIntentLoadError(f"{collection_path}.module_id is required")
            if surface_kind == "module" and not entity_name:
                raise DatabaseIntentLoadError(f"{collection_path}.entity_name is required")
            if module_id and entity_name:
                index[(module_id, entity_name)] = collection_obj

    return index


def _validate_database_intent(intent: DatabaseIntent) -> None:
    if not _is_non_empty_string(intent.get("version")):
        raise DatabaseIntentLoadError("database_intent.version is required")

    surfaces = _require_list(intent.get("surfaces"), "database_intent.surfaces")
    for surface_index, surface in enumerate(surfaces):
        surface_path = f"database_intent.surfaces[{surface_index}]"
        surface_obj = _require_object(surface, surface_path)
        if not _is_non_empty_string(surface_obj.get("surface_id")):
            raise DatabaseIntentLoadError(f"{surface_path}.surface_id is required")
        if not _is_non_empty_string(surface_obj.get("surface_kind")):
            raise DatabaseIntentLoadError(f"{surface_path}.surface_kind is required")
        collections = _require_list(surface_obj.get("collections"), f"{surface_path}.collections")
        for collection_index, collection in enumerate(collections):
            collection_path = f"{surface_path}.collections[{collection_index}]"
            collection_obj = _require_object(collection, collection_path)
            if surface_obj["surface_kind"] == "module" and not _is_non_empty_string(collection_obj.get("name")):
                raise DatabaseIntentLoadError(f"{collection_path}.entity_name is required")

    entities = intent.get("entities")
    if entities is not None:
        _require_list(entities, "database_intent.entities")
    index_database_intent_by_entity(intent)


__all__ = [
    "DatabaseEntityIndex",
    "DatabaseIntent",
    "DatabaseIntentLoadError",
    "index_database_intent_by_entity",
    "load_database_intent",
]

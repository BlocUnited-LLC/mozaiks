from __future__ import annotations

from typing import Any

from .intent_loader import DatabaseIntent
from .mongo import MongoPersistenceContext


class DatabaseIndexApplyError(ValueError):
    """Raised when database intent index metadata cannot be applied."""


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _normalize_index_keys(raw_keys: Any, path: str) -> list[tuple[str, int]]:
    if not isinstance(raw_keys, list) or not raw_keys:
        raise DatabaseIndexApplyError(f"{path}.keys must be a non-empty list")

    normalized: list[tuple[str, int]] = []
    for index, item in enumerate(raw_keys):
        item_path = f"{path}.keys[{index}]"
        if isinstance(item, dict):
            field = item.get("field")
            order = item.get("order", 1)
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            field, order = item
        else:
            raise DatabaseIndexApplyError(f"{item_path} must be an object or [field, order] pair")

        if not _is_non_empty_string(field):
            raise DatabaseIndexApplyError(f"{item_path}.field is required")
        try:
            order_int = int(order)
        except Exception as exc:
            raise DatabaseIndexApplyError(f"{item_path}.order must be an integer") from exc
        if order_int not in {-1, 1}:
            raise DatabaseIndexApplyError(f"{item_path}.order must be 1 or -1")
        normalized.append((field.strip(), order_int))

    return normalized


def _normalize_index_spec(raw_spec: Any, path: str) -> dict[str, Any]:
    if not isinstance(raw_spec, dict):
        raise DatabaseIndexApplyError(f"{path} must be an object")
    spec = dict(raw_spec)
    spec["keys"] = _normalize_index_keys(spec.get("keys"), path)
    if "name" in spec and spec["name"] is not None:
        name = str(spec["name"]).strip()
        if not name:
            raise DatabaseIndexApplyError(f"{path}.name must be non-empty when provided")
        spec["name"] = name
    return spec


def _iter_indexed_collections(intent: DatabaseIntent):
    surfaces = intent.get("surfaces") or []
    for surface_index, surface in enumerate(surfaces):
        surface_path = f"database_intent.surfaces[{surface_index}]"
        if not isinstance(surface, dict):
            raise DatabaseIndexApplyError(f"{surface_path} must be an object")
        surface_id = str(surface.get("surface_id") or "").strip()
        surface_kind = str(surface.get("surface_kind") or "").strip()
        collections = surface.get("collections") or []
        for collection_index, collection in enumerate(collections):
            collection_path = f"{surface_path}.collections[{collection_index}]"
            if not isinstance(collection, dict):
                raise DatabaseIndexApplyError(f"{collection_path} must be an object")
            indexes = collection.get("indexes") or []
            if not indexes:
                continue

            ownership = collection.get("ownership") if isinstance(collection.get("ownership"), dict) else {}
            module_id = str(collection.get("module_id") or ownership.get("surface_id") or surface_id).strip()
            entity_name = str(collection.get("entity_name") or collection.get("name") or "").strip()
            if surface_kind == "module" and not module_id:
                raise DatabaseIndexApplyError(f"{collection_path}.module_id is required")
            if surface_kind == "module" and not entity_name:
                raise DatabaseIndexApplyError(f"{collection_path}.entity_name is required")
            if not isinstance(indexes, list):
                raise DatabaseIndexApplyError(f"{collection_path}.indexes must be a list")
            yield module_id, entity_name, indexes, collection_path


async def apply_database_indexes(
    intent: DatabaseIntent | None,
    *,
    app_id: str | None = None,
    persistence: MongoPersistenceContext | None = None,
) -> int:
    """Ensure indexes declared in database intent exist.

    This is index-only. It does not mutate documents, apply migrations, or
    write migration history.
    """

    if intent is None:
        return 0

    resolved_app_id = str(app_id or intent.get("app_id") or "").strip()
    if not resolved_app_id:
        raise DatabaseIndexApplyError("app_id is required to apply database indexes")

    context = persistence or MongoPersistenceContext(app_id=resolved_app_id)
    applied_specs = 0
    for module_id, entity_name, indexes, collection_path in _iter_indexed_collections(intent):
        normalized_indexes = [
            _normalize_index_spec(index_spec, f"{collection_path}.indexes[{index}]")
            for index, index_spec in enumerate(indexes)
        ]
        if not normalized_indexes:
            continue
        collection = context.collection(module_id, entity_name)
        await collection.ensure_indexes(normalized_indexes)
        applied_specs += len(normalized_indexes)
    return applied_specs


__all__ = [
    "DatabaseIndexApplyError",
    "apply_database_indexes",
]

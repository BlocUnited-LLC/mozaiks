"""Managers for runtime-managed data entities created by workflows."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from logs.logging_config import get_workflow_logger

try:  # Local import with fallback for unit tests
    from mozaiksai.core.core_config import get_mongo_client
except Exception:  # pragma: no cover
    get_mongo_client = None  # type: ignore

logger = get_workflow_logger("context.data_entity")

_TYPE_CHECKS = {
    "string": str,
    "str": str,
    "integer": int,
    "int": int,
    "number": (int, float),
    "float": (int, float),
    "boolean": bool,
    "bool": bool,
    "object": dict,
    "dict": dict,
    "array": list,
    "list": list,
}


@dataclass
class _PendingWrite:
    operation: str
    payload: Dict[str, Any]
    search_value: Optional[Any] = None


class DataEntityManager:
    """Runtime helper for creating and updating workflow-owned collections."""

    def __init__(
        self,
        *,
        database_name: str,
        collection: str,
        schema: Optional[Dict[str, Any]] = None,
        indexes: Optional[List[Dict[str, Any]]] = None,
        write_strategy: str = "immediate",
        search_by: Optional[str] = None,
    ) -> None:
        if not database_name or not collection:
            raise ValueError("DataEntityManager requires database_name and collection")

        if get_mongo_client is None:
            raise RuntimeError("Mongo client unavailable; cannot manage data entities")

        self._database_name = database_name
        self._collection_name = collection
        self._schema = schema or {}
        self._indexes = indexes or []
        self._write_strategy = write_strategy
        self._search_by = search_by
        self._pending: List[_PendingWrite] = []
        self._client = get_mongo_client()
        self._init_lock = asyncio.Lock()
        self._initialized = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a new document honoring schema validation and write strategy."""

        await self._ensure_ready()
        doc = self._validate_payload(data)
        if self._write_strategy == "immediate":
            await self._collection.insert_one(doc)
        else:
            self._pending.append(_PendingWrite("insert", doc))
        return doc

    async def update(self, search_value: Any, updates: Dict[str, Any]) -> None:
        """Update an existing document identified by the configured search key."""

        if not self._search_by:
            raise ValueError("update() requires search_by to be defined in data_entity source")

        await self._ensure_ready()
        payload = self._validate_updates(updates)
        if self._write_strategy == "immediate":
            await self._collection.update_one({self._search_by: search_value}, {"$set": payload}, upsert=False)
        else:
            self._pending.append(_PendingWrite("update", payload, search_value))

    async def flush(self) -> None:
        """Persist pending writes for deferred strategies."""

        if not self._pending:
            return

        await self._ensure_ready()
        collection = self._collection
        pending, self._pending = self._pending, []
        for item in pending:
            if item.operation == "insert":
                await collection.insert_one(item.payload)
            elif item.operation == "update":
                await collection.update_one({self._search_by: item.search_value}, {"$set": item.payload}, upsert=False)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _ensure_ready(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await self._ensure_indexes()
            self._initialized = True

    async def _ensure_indexes(self) -> None:
        for index in self._normalized_indexes():
            try:
                options = {k: v for k, v in index.items() if k not in {"keys"} and v is not None}
                await self._collection.create_index(index["keys"], **options)
            except Exception as err:
                logger.warning(
                    "Failed creating index on %s.%s: %s",
                    self._database_name,
                    self._collection_name,
                    err,
                )

    def _schema_fields(self) -> Dict[str, Dict[str, Any]]:
        schema = self._schema or {}
        if not isinstance(schema, dict):
            return {}

        raw_fields = schema.get("fields")
        if isinstance(raw_fields, list):
            fields: Dict[str, Dict[str, Any]] = {}
            for field in raw_fields:
                if not isinstance(field, dict):
                    continue
                name = str(field.get("name") or "").strip()
                if not name:
                    continue
                fields[name] = dict(field)
            return fields

        fields = {}
        for field_name, spec in schema.items():
            if isinstance(spec, dict):
                fields[str(field_name)] = dict(spec)
        return fields

    def _normalized_indexes(self) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for index in self._indexes:
            if not isinstance(index, dict):
                continue

            raw_keys = index.get("keys")
            keys: List[tuple[str, int]] = []
            if isinstance(raw_keys, list):
                for entry in raw_keys:
                    if isinstance(entry, (list, tuple)) and len(entry) == 2:
                        field_name = str(entry[0] or "").strip()
                        if not field_name:
                            continue
                        direction = -1 if str(entry[1]).strip() == "-1" else 1
                        keys.append((field_name, direction))

            if not keys:
                field_name = str(index.get("field") or "").strip()
                if not field_name:
                    continue
                direction = -1 if str(index.get("direction", 1)).strip() == "-1" else 1
                keys = [(field_name, direction)]

            normalized.append(
                {
                    "keys": keys,
                    "unique": bool(index.get("unique")) if "unique" in index else None,
                    "sparse": bool(index.get("sparse")) if "sparse" in index else None,
                    "name": str(index.get("name")).strip() if index.get("name") else None,
                }
            )
        return normalized

    def _validate_payload(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("DataEntityManager.create expects a dict payload")

        validated = dict(data)
        for field_name, spec in self._schema_fields().items():
            if field_name in validated:
                validated[field_name] = self._validate_field_value(field_name, validated[field_name], spec)
                continue

            if "default" in spec:
                validated[field_name] = deepcopy(spec.get("default"))
                continue

            if bool(spec.get("required")):
                raise ValueError(f"Missing required field '{field_name}' for data_entity insert")

        return validated

    def _validate_updates(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(updates, dict):
            raise ValueError("DataEntityManager.update expects a dict payload")

        validated = dict(updates)
        schema_fields = self._schema_fields()
        for field_name, value in list(validated.items()):
            spec = schema_fields.get(field_name)
            if spec is None:
                continue
            validated[field_name] = self._validate_field_value(field_name, value, spec)
        return validated

    def _validate_field_value(self, field_name: str, value: Any, spec: Dict[str, Any]) -> Any:
        nullable = bool(spec.get("nullable"))
        if value is None:
            if bool(spec.get("required")) and not nullable:
                raise ValueError(f"Field '{field_name}' cannot be null")
            return value

        expected_type = str(spec.get("type") or "").strip().lower()
        checker = _TYPE_CHECKS.get(expected_type)
        if checker and not isinstance(value, checker):
            raise ValueError(
                f"Field '{field_name}' must be of type '{expected_type}', got {type(value).__name__}"
            )

        allowed_values = spec.get("enum")
        if isinstance(allowed_values, list) and value not in allowed_values:
            raise ValueError(f"Field '{field_name}' must be one of {allowed_values}")

        return value

    @property
    def _collection(self):  # pragma: no cover - exercised via public methods
        return self._client[self._database_name][self._collection_name]


__all__ = ["DataEntityManager"]

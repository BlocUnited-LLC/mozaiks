"""Managers for runtime-managed data entities created by workflows."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from logs.logging_config import get_workflow_logger

try:  # Local import with fallback for unit tests
    from mozaiksai.core.core_config import get_mongo_client
except Exception:  # pragma: no cover
    get_mongo_client = None  # type: ignore

logger = get_workflow_logger("context.data_entity")


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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a new document honoring schema validation and write strategy."""

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

        payload = self._validate_updates(updates)
        if self._write_strategy == "immediate":
            await self._collection.update_one({self._search_by: search_value}, {"$set": payload}, upsert=False)
        else:
            self._pending.append(_PendingWrite("update", payload, search_value))

    async def flush(self) -> None:
        """Persist pending writes for deferred strategies."""

        if not self._pending:
            return

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

    def _validate_payload(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("DataEntityManager.create expects a dict payload")

        validated: Dict[str, Any] = {}
        for field_name, spec in (self._schema or {}).items():
            required = bool(spec.get("required"))
            if required and field_name not in data:
                raise ValueError(f"Missing required field '{field_name}' for data_entity insert")

        validated.update(data)
        return validated

    def _validate_updates(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(updates, dict):
            raise ValueError("DataEntityManager.update expects a dict payload")
        return dict(updates)

    @property
    def _collection(self):  # pragma: no cover - exercised via public methods
        return self._client[self._database_name][self._collection_name]


__all__ = ["DataEntityManager"]

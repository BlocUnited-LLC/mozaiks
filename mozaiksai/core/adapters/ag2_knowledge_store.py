"""Tenant-scoped Mongo implementation of AG2's KnowledgeStore protocol.

AG2 owns Network persistence semantics and Hub hydration. This adapter only
maps AG2's virtual file paths onto Mozaiks' canonical runtime database because
AG2 does not currently ship a Mongo KnowledgeStore.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Any

from ag2.knowledge import NoopChangeSubscription
from pymongo import ReturnDocument

from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, RuntimeCollections
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id, dual_write_app_scope


def _normalize_path(path: str) -> str:
    value = "/" + str(path or "").strip().lstrip("/")
    return value.rstrip("/") or "/"


class MongoAG2KnowledgeStore:
    """Persist one AG2 Network namespace for one app workflow chat."""

    def __init__(
        self,
        *,
        app_id: str,
        chat_id: str,
        collection: Any | None = None,
    ) -> None:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        resolved_chat_id = str(chat_id or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        if not resolved_chat_id:
            raise ValueError("chat_id is required")
        self._app_id = resolved_app_id
        self._chat_id = resolved_chat_id
        self._collection = collection
        self._client: Any | None = None
        self._lock = asyncio.Lock()
        self._indexes_ready = False

    async def read(self, path: str) -> str | None:
        doc = await (await self._coll()).find_one(self._filter(_normalize_path(path)))
        return None if doc is None else str(doc.get("content") or "")

    async def write(self, path: str, content: str) -> None:
        normalized = _normalize_path(path)
        now = datetime.now(UTC)
        await (await self._coll()).update_one(
            self._filter(normalized),
            {
                "$set": dual_write_app_scope(
                    {
                        "app_id": self._app_id,
                        "chat_id": self._chat_id,
                        "path": normalized,
                        "content": str(content),
                        "updated_at": now,
                    },
                    self._app_id,
                ),
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    async def list(self, path: str = "/") -> list[str]:
        normalized = _normalize_path(path)
        prefix = normalized.rstrip("/") + "/"
        query = self._scope_filter()
        query["path"] = {"$regex": f"^{re.escape(prefix)}"}
        cursor = (await self._coll()).find(query, {"path": 1})
        docs = await cursor.to_list(length=100_000)
        children: set[str] = set()
        for doc in docs:
            remainder = str(doc.get("path") or "")[len(prefix) :]
            if not remainder:
                continue
            children.add(remainder.split("/", 1)[0] + ("/" if "/" in remainder else ""))
        return sorted(children)

    async def delete(self, path: str) -> None:
        normalized = _normalize_path(path)
        query = self._scope_filter()
        query["$or"] = [
            {"path": normalized},
            {"path": {"$regex": f"^{re.escape(normalized.rstrip('/') + '/')}"}},
        ]
        await (await self._coll()).delete_many(query)

    async def exists(self, path: str) -> bool:
        normalized = _normalize_path(path)
        query = self._scope_filter()
        query["$or"] = [
            {"path": normalized},
            {"path": {"$regex": f"^{re.escape(normalized.rstrip('/') + '/')}"}},
        ]
        return await (await self._coll()).find_one(query, {"_id": 1}) is not None

    async def append(self, path: str, content: str) -> int:
        """Atomically append UTF-8 content and return its prior byte offset."""
        normalized = _normalize_path(path)
        now = datetime.now(UTC)
        prior = await (await self._coll()).find_one_and_update(
            self._filter(normalized),
            [
                {
                    "$set": dual_write_app_scope(
                        {
                            "app_id": self._app_id,
                            "chat_id": self._chat_id,
                            "path": normalized,
                            "content": {
                                "$concat": [
                                    {"$ifNull": ["$content", ""]},
                                    str(content),
                                ]
                            },
                            "created_at": {"$ifNull": ["$created_at", now]},
                            "updated_at": now,
                        },
                        self._app_id,
                    )
                }
            ],
            upsert=True,
            return_document=ReturnDocument.BEFORE,
        )
        return len(str((prior or {}).get("content") or "").encode("utf-8"))

    async def read_range(self, path: str, start: int, end: int | None = None) -> str:
        content = await self.read(path)
        if content is None:
            return ""
        data = content.encode("utf-8")
        stop = len(data) if end is None else min(end, len(data))
        if start >= stop:
            return ""
        return data[start:stop].decode("utf-8", errors="strict")

    async def on_change(self, path: str, callback: Any) -> NoopChangeSubscription:
        _ = path, callback
        return NoopChangeSubscription()

    def _scope_filter(self) -> dict[str, Any]:
        return {
            "chat_id": self._chat_id,
            **build_app_scope_filter(self._app_id),
        }

    def _filter(self, path: str) -> dict[str, Any]:
        return {"path": path, **self._scope_filter()}

    async def _coll(self) -> Any:
        if self._collection is None:
            async with self._lock:
                if self._client is None:
                    self._client = get_mongo_client()
                self._collection = self._client[SYSTEM_DATABASE][
                    RuntimeCollections.AG2_NETWORK_KNOWLEDGE
                ]
        if not self._indexes_ready:
            create_index = getattr(self._collection, "create_index", None)
            if callable(create_index):
                await create_index(
                    [("app_id", 1), ("chat_id", 1), ("path", 1)],
                    name="ag2_network_app_chat_path",
                    unique=True,
                )
            self._indexes_ready = True
        return self._collection


__all__ = ["MongoAG2KnowledgeStore"]

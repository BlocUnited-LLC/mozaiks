from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mozaiksai.core.runtime.persistence import app_data_from_context

_CONTACTS_ALIAS = "contacts.contacts"

Document = dict[str, Any]


def _persistence(ctx):
    return app_data_from_context(ctx)


def _normalize_doc(value: Any) -> Document | None:
    return dict(value) if isinstance(value, dict) else None


def _normalize_docs(value: Any) -> list[Document]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


class ContactRepo:
    async def list(self, ctx, *, query: dict[str, Any], limit: int, before: str | None = None) -> list[Document]:
        q = dict(query)
        if before:
            q["created_at"] = {"$lt": before}
        col = _persistence(ctx).collection(_CONTACTS_ALIAS)
        cursor = col.find(q, {"_id": 0}).sort("created_at", -1).limit(limit)
        return _normalize_docs(await cursor.to_list(length=limit))

    async def get(self, ctx, *, user_id: str, contact_user_id: str) -> Document | None:
        col = _persistence(ctx).collection(_CONTACTS_ALIAS)
        return _normalize_doc(await col.find_one({"user_id": user_id, "contact_user_id": contact_user_id}, {"_id": 0}))

    async def insert(self, ctx, *, doc: Mapping[str, Any]) -> None:
        col = _persistence(ctx).collection(_CONTACTS_ALIAS)
        await col.insert_one(dict(doc))

    async def update(self, ctx, *, user_id: str, contact_user_id: str, updates: dict[str, Any]) -> int:
        col = _persistence(ctx).collection(_CONTACTS_ALIAS)
        result = await col.update_one(
            {"user_id": user_id, "contact_user_id": contact_user_id},
            {"$set": dict(updates)},
        )
        return int(result.matched_count)

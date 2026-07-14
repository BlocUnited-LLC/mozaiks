"""Persistence operations for the friends module."""
from __future__ import annotations

from typing import Any

from mozaiksai.core.runtime.persistence import app_data_from_context

_FRIENDSHIPS = "friends.friendships"
_REQUESTS = "friends.requests"

Document = dict[str, Any]


def _p(ctx):
    return app_data_from_context(ctx)


def _norm(v: Any) -> Document | None:
    return dict(v) if isinstance(v, dict) else None


def _norms(v: Any) -> list[Document]:
    return [dict(i) for i in v if isinstance(i, dict)] if isinstance(v, list) else []


class FriendRequestRepo:
    async def create(self, ctx, doc: Document) -> Document:
        await _p(ctx).collection(_REQUESTS).insert_one({**doc})
        return doc

    async def get(self, ctx, *, request_id: str) -> Document | None:
        return _norm(await _p(ctx).collection(_REQUESTS).find_one(
            {"request_id": request_id}, {"_id": 0}
        ))

    async def find_pending(self, ctx, *, requester_id: str, recipient_id: str, app_id: str | None) -> Document | None:
        q: dict[str, Any] = {
            "requester_id": requester_id,
            "recipient_id": recipient_id,
            "status": "pending",
        }
        if app_id:
            q["app_id"] = app_id
        return _norm(await _p(ctx).collection(_REQUESTS).find_one(q, {"_id": 0}))

    async def update_status(self, ctx, *, request_id: str, status: str, updated_at: str) -> bool:
        result = await _p(ctx).collection(_REQUESTS).update_one(
            {"request_id": request_id},
            {"$set": {"status": status, "updated_at": updated_at}},
        )
        return result.modified_count > 0

    async def list(
        self,
        ctx,
        *,
        query: dict[str, Any],
        limit: int,
        before: str | None = None,
    ) -> list[Document]:
        if before:
            query = {**query, "created_at": {"$lt": before}}
        cursor = (
            _p(ctx).collection(_REQUESTS)
            .find(query, {"_id": 0})
            .sort("created_at", -1)
            .limit(limit)
        )
        return _norms(await cursor.to_list(length=limit))

    async def count(self, ctx, *, query: dict[str, Any]) -> int:
        return await _p(ctx).collection(_REQUESTS).count_documents(query)


class FriendshipRepo:
    async def create_pair(self, ctx, doc_a: Document, doc_b: Document) -> None:
        """Insert two symmetric records atomically-ish (best effort — both or log)."""
        col = _p(ctx).collection(_FRIENDSHIPS)
        await col.insert_one({**doc_a})
        await col.insert_one({**doc_b})

    async def find(self, ctx, *, user_id: str, friend_user_id: str, app_id: str | None) -> Document | None:
        q: dict[str, Any] = {"user_id": user_id, "friend_user_id": friend_user_id}
        if app_id:
            q["app_id"] = app_id
        return _norm(await _p(ctx).collection(_FRIENDSHIPS).find_one(q, {"_id": 0}))

    async def delete_pair(self, ctx, *, user_id: str, friend_user_id: str, app_id: str | None) -> int:
        q: dict[str, Any] = {
            "$or": [
                {"user_id": user_id, "friend_user_id": friend_user_id},
                {"user_id": friend_user_id, "friend_user_id": user_id},
            ]
        }
        if app_id:
            q["app_id"] = app_id
        result = await _p(ctx).collection(_FRIENDSHIPS).delete_many(q)
        return result.deleted_count

    async def list(
        self,
        ctx,
        *,
        user_id: str,
        app_id: str | None,
        limit: int,
        before: str | None = None,
    ) -> list[Document]:
        q: dict[str, Any] = {"user_id": user_id}
        if app_id:
            q["app_id"] = app_id
        if before:
            q["created_at"] = {"$lt": before}
        cursor = (
            _p(ctx).collection(_FRIENDSHIPS)
            .find(q, {"_id": 0})
            .sort("created_at", -1)
            .limit(limit)
        )
        return _norms(await cursor.to_list(length=limit))

    async def count(self, ctx, *, user_id: str, app_id: str | None) -> int:
        q: dict[str, Any] = {"user_id": user_id}
        if app_id:
            q["app_id"] = app_id
        return await _p(ctx).collection(_FRIENDSHIPS).count_documents(q)

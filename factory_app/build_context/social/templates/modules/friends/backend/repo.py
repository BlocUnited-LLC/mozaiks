from __future__ import annotations

from collections.abc import Mapping
from typing import Any

Document = dict[str, Any]


def _collection(ctx, entity_name: str):
    persistence = getattr(ctx, "persistence", None)
    if persistence is None:
        raise RuntimeError("Persistence is not available for this app context.")
    return persistence.collection("friends", entity_name)


def _norm(v: Any) -> Document | None:
    return dict(v) if isinstance(v, Mapping) else None


def _norms(v: Any) -> list[Document]:
    return [dict(i) for i in v or [] if isinstance(i, Mapping)]


class FriendRequestRepo:
    async def create(self, ctx, doc: Document) -> Document:
        await _collection(ctx, "requests").insert_one({**doc})
        return doc

    async def get(self, ctx, *, request_id: str) -> Document | None:
        return _norm(await _collection(ctx, "requests").find_one({"request_id": request_id}))

    async def find_pending(self, ctx, *, requester_id: str, recipient_id: str, app_id: str | None) -> Document | None:
        q: dict[str, Any] = {
            "requester_id": requester_id,
            "recipient_id": recipient_id,
            "status": "pending",
        }
        if app_id:
            q["app_id"] = app_id
        return _norm(await _collection(ctx, "requests").find_one(q))

    async def update_status(self, ctx, *, request_id: str, status: str, updated_at: str) -> bool:
        result = await _collection(ctx, "requests").update_one(
            {"request_id": request_id},
            {"$set": {"status": status, "updated_at": updated_at}},
        )
        return bool(getattr(result, "modified_count", 0))

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
        return _norms(
            await _collection(ctx, "requests").find_many(
                query,
                limit=limit,
                sort=[("created_at", -1)],
            )
        )

    async def count(self, ctx, *, query: dict[str, Any]) -> int:
        return int(await _collection(ctx, "requests").count(query))


class FriendshipRepo:
    async def create_pair(self, ctx, doc_a: Document, doc_b: Document) -> None:
        col = _collection(ctx, "friendships")
        await col.insert_one({**doc_a})
        await col.insert_one({**doc_b})

    async def find(self, ctx, *, user_id: str, friend_user_id: str, app_id: str | None) -> Document | None:
        q: dict[str, Any] = {"user_id": user_id, "friend_user_id": friend_user_id}
        if app_id:
            q["app_id"] = app_id
        return _norm(await _collection(ctx, "friendships").find_one(q))

    async def delete_pair(self, ctx, *, user_id: str, friend_user_id: str, app_id: str | None) -> int:
        q: dict[str, Any] = {
            "$or": [
                {"user_id": user_id, "friend_user_id": friend_user_id},
                {"user_id": friend_user_id, "friend_user_id": user_id},
            ]
        }
        if app_id:
            q["app_id"] = app_id
        result = await _collection(ctx, "friendships").delete_many(q)
        return int(getattr(result, "deleted_count", 0))

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
        return _norms(
            await _collection(ctx, "friendships").find_many(
                q,
                limit=limit,
                sort=[("created_at", -1)],
            )
        )

    async def count(self, ctx, *, user_id: str, app_id: str | None) -> int:
        q: dict[str, Any] = {"user_id": user_id}
        if app_id:
            q["app_id"] = app_id
        return int(await _collection(ctx, "friendships").count(q))

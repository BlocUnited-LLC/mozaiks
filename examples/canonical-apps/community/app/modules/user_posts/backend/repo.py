from __future__ import annotations

from collections.abc import Mapping
from typing import Any

Document = dict[str, Any]


def _collection(ctx, entity_name: str):
    persistence = getattr(ctx, "persistence", None)
    if persistence is None:
        raise RuntimeError("Persistence is not available for this app context.")
    return persistence.collection("user_posts", entity_name)


def _norm(v: Any) -> Document | None:
    return dict(v) if isinstance(v, Mapping) else None


def _norms(v: Any) -> list[Document]:
    return [dict(i) for i in v or [] if isinstance(i, Mapping)]


class PostRepo:
    async def create(self, ctx, doc: Document) -> Document:
        await _collection(ctx, "posts").insert_one({**doc})
        return doc

    async def get(self, ctx, *, post_id: str) -> Document | None:
        return _norm(await _collection(ctx, "posts").find_one({"post_id": post_id}))

    async def soft_delete(self, ctx, *, post_id: str, deleted_at: str) -> bool:
        result = await _collection(ctx, "posts").update_one(
            {"post_id": post_id},
            {"$set": {"status": "deleted", "updated_at": deleted_at}},
        )
        return bool(getattr(result, "modified_count", 0))

    async def list(self, ctx, *, query: dict[str, Any], limit: int, before: str | None = None) -> list[Document]:
        if before:
            query = {**query, "created_at": {"$lt": before}}
        return _norms(
            await _collection(ctx, "posts").find_many(
                query,
                limit=limit,
                sort=[("created_at", -1)],
            )
        )

    async def increment_comment_count(self, ctx, *, post_id: str, delta: int = 1) -> None:
        await _collection(ctx, "posts").update_one(
            {"post_id": post_id}, {"$inc": {"comment_count": delta}}
        )

    async def set_reaction_count(self, ctx, *, post_id: str, count: int) -> None:
        await _collection(ctx, "posts").update_one(
            {"post_id": post_id}, {"$set": {"reaction_count": count}}
        )


class ReactionRepo:
    async def get(self, ctx, *, post_id: str, user_id: str) -> Document | None:
        return _norm(await _collection(ctx, "reactions").find_one({"post_id": post_id, "user_id": user_id}))

    async def upsert(self, ctx, doc: Document) -> None:
        await _collection(ctx, "reactions").update_one(
            {"post_id": doc["post_id"], "user_id": doc["user_id"]},
            {"$set": {**doc}},
            upsert=True,
        )

    async def delete(self, ctx, *, post_id: str, user_id: str) -> bool:
        result = await _collection(ctx, "reactions").delete_one({"post_id": post_id, "user_id": user_id})
        return bool(getattr(result, "deleted_count", 0))

    async def count_by_post(self, ctx, *, post_id: str) -> int:
        return int(await _collection(ctx, "reactions").count({"post_id": post_id}))

    async def count_by_type(self, ctx, *, post_id: str) -> dict[str, int]:
        pipeline = [
            {"$match": {"post_id": post_id}},
            {"$group": {"_id": "$reaction_type", "count": {"$sum": 1}}},
        ]
        results = await _collection(ctx, "reactions").aggregate(pipeline)
        return {r["_id"]: r["count"] for r in results if r.get("_id")}


class CommentRepo:
    async def create(self, ctx, doc: Document) -> Document:
        await _collection(ctx, "comments").insert_one({**doc})
        return doc

    async def get(self, ctx, *, comment_id: str) -> Document | None:
        return _norm(await _collection(ctx, "comments").find_one({"comment_id": comment_id}))

    async def delete(self, ctx, *, comment_id: str) -> bool:
        result = await _collection(ctx, "comments").delete_one({"comment_id": comment_id})
        return bool(getattr(result, "deleted_count", 0))

    async def list(self, ctx, *, post_id: str, limit: int, after: str | None = None) -> list[Document]:
        q: dict[str, Any] = {"post_id": post_id}
        if after:
            q["created_at"] = {"$gt": after}
        return _norms(
            await _collection(ctx, "comments").find_many(
                q,
                limit=limit,
                sort=[("created_at", 1)],
            )
        )

    async def count(self, ctx, *, post_id: str) -> int:
        return int(await _collection(ctx, "comments").count({"post_id": post_id}))

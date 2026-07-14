"""Persistence operations for the user_posts module."""
from __future__ import annotations

from typing import Any

from mozaiksai.core.runtime.persistence import app_data_from_context

_POSTS = "user_posts.posts"
_REACTIONS = "user_posts.reactions"
_COMMENTS = "user_posts.comments"

Document = dict[str, Any]


def _p(ctx):
    return app_data_from_context(ctx)


def _norm(v: Any) -> Document | None:
    return dict(v) if isinstance(v, dict) else None


def _norms(v: Any) -> list[Document]:
    return [dict(i) for i in v if isinstance(i, dict)] if isinstance(v, list) else []


class PostRepo:
    async def create(self, ctx, doc: Document) -> Document:
        await _p(ctx).collection(_POSTS).insert_one({**doc})
        return doc

    async def get(self, ctx, *, post_id: str) -> Document | None:
        return _norm(await _p(ctx).collection(_POSTS).find_one({"post_id": post_id}, {"_id": 0}))

    async def soft_delete(self, ctx, *, post_id: str, deleted_at: str) -> bool:
        result = await _p(ctx).collection(_POSTS).update_one(
            {"post_id": post_id},
            {"$set": {"status": "deleted", "updated_at": deleted_at}},
        )
        return result.modified_count > 0

    async def list(self, ctx, *, query: dict[str, Any], limit: int, before: str | None = None) -> list[Document]:
        if before:
            query = {**query, "created_at": {"$lt": before}}
        cursor = _p(ctx).collection(_POSTS).find(query, {"_id": 0}).sort("created_at", -1).limit(limit)
        return _norms(await cursor.to_list(length=limit))

    async def increment_comment_count(self, ctx, *, post_id: str, delta: int = 1) -> None:
        await _p(ctx).collection(_POSTS).update_one(
            {"post_id": post_id}, {"$inc": {"comment_count": delta}}
        )

    async def set_reaction_count(self, ctx, *, post_id: str, count: int) -> None:
        await _p(ctx).collection(_POSTS).update_one(
            {"post_id": post_id}, {"$set": {"reaction_count": count}}
        )


class ReactionRepo:
    async def get(self, ctx, *, post_id: str, user_id: str) -> Document | None:
        return _norm(await _p(ctx).collection(_REACTIONS).find_one(
            {"post_id": post_id, "user_id": user_id}, {"_id": 0}
        ))

    async def upsert(self, ctx, doc: Document) -> None:
        await _p(ctx).collection(_REACTIONS).replace_one(
            {"post_id": doc["post_id"], "user_id": doc["user_id"]},
            {**doc},
            upsert=True,
        )

    async def delete(self, ctx, *, post_id: str, user_id: str) -> bool:
        result = await _p(ctx).collection(_REACTIONS).delete_one({"post_id": post_id, "user_id": user_id})
        return result.deleted_count > 0

    async def count_by_post(self, ctx, *, post_id: str) -> int:
        return await _p(ctx).collection(_REACTIONS).count_documents({"post_id": post_id})

    async def count_by_type(self, ctx, *, post_id: str) -> dict[str, int]:
        pipeline = [
            {"$match": {"post_id": post_id}},
            {"$group": {"_id": "$reaction_type", "count": {"$sum": 1}}},
        ]
        results = await _p(ctx).collection(_REACTIONS).aggregate(pipeline).to_list(length=None)
        return {r["_id"]: r["count"] for r in results if r.get("_id")}


class CommentRepo:
    async def create(self, ctx, doc: Document) -> Document:
        await _p(ctx).collection(_COMMENTS).insert_one({**doc})
        return doc

    async def get(self, ctx, *, comment_id: str) -> Document | None:
        return _norm(await _p(ctx).collection(_COMMENTS).find_one({"comment_id": comment_id}, {"_id": 0}))

    async def delete(self, ctx, *, comment_id: str) -> bool:
        result = await _p(ctx).collection(_COMMENTS).delete_one({"comment_id": comment_id})
        return result.deleted_count > 0

    async def list(self, ctx, *, post_id: str, limit: int, after: str | None = None) -> list[Document]:
        q: dict[str, Any] = {"post_id": post_id}
        if after:
            q["created_at"] = {"$gt": after}
        cursor = _p(ctx).collection(_COMMENTS).find(q, {"_id": 0}).sort("created_at", 1).limit(limit)
        return _norms(await cursor.to_list(length=limit))

    async def count(self, ctx, *, post_id: str) -> int:
        return await _p(ctx).collection(_COMMENTS).count_documents({"post_id": post_id})

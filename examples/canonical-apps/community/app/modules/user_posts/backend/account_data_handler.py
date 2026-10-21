"""AccountDataHandler for the user_posts module.

GDPR deletion and export for posts, reactions, and comments.

Deletion policy:
  - Posts authored by the deleted user: hard-deleted (overrides the normal
    soft-delete path — GDPR takes precedence over thread integrity).
  - Post reactions left by the deleted user: hard-deleted.
  - Comments authored by the deleted user: hard-deleted.
    Reaction and comment counts on surviving posts will drift; this is
    acceptable — the platform must not retain user data to preserve counts.
"""
from __future__ import annotations

from typing import Any

from mozaiksai.core.runtime.composition.bson_safe import json_safe_bson

_PAGE_SIZE = 100


class AccountDataHandler:
    """GDPR delete + export handler for the user_posts module."""

    def __init__(self, persistence: Any) -> None:
        self._persistence = persistence

    def _collection(self, app_id: str, user_id: str, name: str) -> Any:
        if self._persistence.app_id != app_id:
            raise PermissionError("account persistence is bound to another app")
        principal = self._persistence.principal
        if principal is None or not user_id or principal.user_id != user_id:
            raise PermissionError("account persistence is bound to another user")
        return self._persistence.collection("user_posts", name)

    async def delete_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        posts = await self._collection(app_id, user_id, "posts").delete_many({"author_id": user_id})
        reactions = await self._collection(app_id, user_id, "reactions").delete_many({"user_id": user_id})
        comments = await self._collection(app_id, user_id, "comments").delete_many({"author_id": user_id})

        deleted_count = posts.deleted_count + reactions.deleted_count + comments.deleted_count
        return {
            "posts_deleted": posts.deleted_count,
            "reactions_deleted": reactions.deleted_count,
            "comments_deleted": comments.deleted_count,
            "deleted_count": deleted_count,
        }

    async def export_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        return {
            "posts": await self._export_collection(app_id, user_id, "posts", "author_id"),
            "post_reactions": await self._export_collection(app_id, user_id, "reactions", "user_id"),
            "post_comments": await self._export_collection(app_id, user_id, "comments", "author_id"),
        }

    async def _export_collection(self, app_id: str, user_id: str, name: str, owner_field: str) -> list:
        collection = self._collection(app_id, user_id, name)
        query: dict[str, Any] = {owner_field: user_id}
        records = []
        while True:
            page = await collection.find_many(query, limit=_PAGE_SIZE, sort=[("_id", 1)])
            records.extend(json_safe_bson({key: value for key, value in row.items() if key != "_id"}) for row in page)
            if len(page) < _PAGE_SIZE:
                return records
            query = {owner_field: user_id, "_id": {"$gt": page[-1]["_id"]}}

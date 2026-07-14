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

_POSTS = "user_posts.posts"
_REACTIONS = "user_posts.reactions"
_COMMENTS = "user_posts.comments"


class AccountDataHandler:
    """GDPR delete + export handler for the user_posts module."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def delete_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        scope = {"app_id": app_id}

        posts = await self._db[_POSTS].delete_many({**scope, "author_id": user_id})
        reactions = await self._db[_REACTIONS].delete_many({**scope, "user_id": user_id})
        comments = await self._db[_COMMENTS].delete_many({**scope, "author_id": user_id})

        deleted_count = posts.deleted_count + reactions.deleted_count + comments.deleted_count
        return {
            "posts_deleted": posts.deleted_count,
            "reactions_deleted": reactions.deleted_count,
            "comments_deleted": comments.deleted_count,
            "deleted_count": deleted_count,
        }

    async def export_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        scope = {"app_id": app_id}

        posts = await self._db[_POSTS].find(
            {**scope, "author_id": user_id}, {"_id": 0}
        ).to_list(length=None)

        reactions = await self._db[_REACTIONS].find(
            {**scope, "user_id": user_id}, {"_id": 0}
        ).to_list(length=None)

        comments = await self._db[_COMMENTS].find(
            {**scope, "author_id": user_id}, {"_id": 0}
        ).to_list(length=None)

        return {
            "posts": posts,
            "post_reactions": reactions,
            "post_comments": comments,
        }

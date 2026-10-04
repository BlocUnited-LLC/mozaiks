"""Scoping and authorization helpers for the user_posts module."""
from __future__ import annotations

from typing import Any

from .schemas import POST_STATUS_PUBLISHED, VISIBILITY_PUBLIC


def actor_id(ctx) -> str:
    return getattr(ctx, "user_id", None) or "anonymous"


def published_posts_query(ctx, *, author_id: str | None = None, visibility: str | None = None) -> dict[str, Any]:
    # ctx.persistence owns app scoping and rejects caller-supplied app_id filters.
    # Mongo scalar equality also matches array elements; array-valued fields grant no access.
    q: dict[str, Any] = {
        "status": {"$eq": POST_STATUS_PUBLISHED, "$not": {"$type": "array"}},
        "$or": [
            {"visibility": {"$eq": VISIBILITY_PUBLIC, "$not": {"$type": "array"}}},
            {"author_id": {"$eq": actor_id(ctx), "$not": {"$type": "array"}}},
        ],
    }
    if author_id:
        q["author_id"] = author_id
    if visibility and visibility != "all":
        q["visibility"] = visibility
    return q


def can_read_post(post: dict[str, Any] | None, user_id: str) -> bool:
    """Nonpublic posts remain author-only; friendship access is not implemented."""
    return bool(
        post
        and post.get("status") == POST_STATUS_PUBLISHED
        and (post.get("visibility") == VISIBILITY_PUBLIC or post.get("author_id") == user_id)
    )


def can_delete_post(post: dict[str, Any], user_id: str, roles: list[str]) -> bool:
    return post.get("author_id") == user_id or "admin" in roles or "moderator" in roles


def can_delete_comment(comment: dict[str, Any], user_id: str, roles: list[str]) -> bool:
    return comment.get("author_id") == user_id or "admin" in roles or "moderator" in roles

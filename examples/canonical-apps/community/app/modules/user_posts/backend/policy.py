"""Scoping and authorization helpers for the user_posts module."""
from __future__ import annotations

from typing import Any


def actor_id(ctx) -> str:
    return getattr(ctx, "user_id", None) or "anonymous"


def published_posts_query(ctx, *, author_id: str | None = None, visibility: str | None = None) -> dict[str, Any]:
    # ctx.persistence owns app scoping and rejects caller-supplied app_id filters.
    q: dict[str, Any] = {"status": "published"}
    if author_id:
        q["author_id"] = author_id
    if visibility and visibility != "all":
        q["visibility"] = visibility
    return q


def can_delete_post(post: dict[str, Any], user_id: str, roles: list[str]) -> bool:
    return post.get("author_id") == user_id or "admin" in roles or "moderator" in roles


def can_delete_comment(comment: dict[str, Any], user_id: str, roles: list[str]) -> bool:
    return comment.get("author_id") == user_id or "admin" in roles or "moderator" in roles

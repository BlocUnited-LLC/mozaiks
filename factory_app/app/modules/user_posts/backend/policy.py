"""Scoping and authorization helpers for the user_posts module."""
from __future__ import annotations

from typing import Any


def actor_id(ctx) -> str:
    return getattr(ctx, "user_id", None) or "anonymous"


def app_scope(ctx) -> dict[str, Any]:
    app_id = getattr(ctx, "app_id", None)
    return {"app_id": app_id} if app_id else {}


def published_posts_query(ctx, *, author_id: str | None = None, visibility: str | None = None) -> dict[str, Any]:
    q: dict[str, Any] = {**app_scope(ctx), "status": "published"}
    if author_id:
        q["author_id"] = author_id
    if visibility and visibility != "all":
        q["visibility"] = visibility
    return q


def can_delete_post(post: dict[str, Any], user_id: str, roles: list[str]) -> bool:
    return post.get("author_id") == user_id or "admin" in roles or "moderator" in roles


def can_delete_comment(comment: dict[str, Any], user_id: str, roles: list[str]) -> bool:
    return comment.get("author_id") == user_id or "admin" in roles or "moderator" in roles

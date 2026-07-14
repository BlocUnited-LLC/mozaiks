"""Type shapes and helpers for the user_posts module."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, TypedDict

POST_STATUS_PUBLISHED = "published"
POST_STATUS_DELETED = "deleted"

VISIBILITY_PUBLIC = "public"
VISIBILITY_FRIENDS = "friends"
VISIBILITY_PRIVATE = "private"

REACTION_TYPES = {"like", "love", "celebrate", "support"}
DEFAULT_REACTION = "like"


class Post(TypedDict, total=False):
    post_id: str
    author_id: str
    body: str
    body_preview: str       # first 200 chars for feed/event payloads
    media_urls: list[str]
    visibility: str         # public | friends | private
    status: str             # published | deleted
    reaction_count: int
    comment_count: int
    created_at: str
    updated_at: str


class Comment(TypedDict, total=False):
    comment_id: str
    post_id: str
    author_id: str
    body: str
    created_at: str


class PostReaction(TypedDict, total=False):
    post_id: str
    user_id: str
    reaction_type: str
    created_at: str


def timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def coerce_limit(value: Any, default: int = 20, maximum: int = 100) -> int:
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def body_preview(body: str, length: int = 200) -> str:
    s = (body or "").strip()
    return s[:length] + ("…" if len(s) > length else "")

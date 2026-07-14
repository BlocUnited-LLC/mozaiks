"""Type shapes and constants for the activity_feed module."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, TypedDict

# Activity type constants
ACTIVITY_FRIENDSHIP_CREATED = "friendship.created"
ACTIVITY_POST_PUBLISHED = "post.published"
ACTIVITY_POST_COMMENTED = "post.commented"


class ActivityEvent(TypedDict, total=False):
    event_id: str
    activity_type: str        # friendship.created | post.published | post.commented
    actor_id: str             # user who performed the action
    about_user_id: str | None # user the action is about (e.g. the new friend)
    subject_id: str | None    # id of the primary subject (post_id, friendship_id, etc.)
    subject_type: str | None  # post | friendship | comment
    subject_label: str | None # human-readable label for the subject
    feed_user_ids: list[str]  # which users' feeds this event appears in
    metadata: dict[str, Any]
    created_at: str


def timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def coerce_limit(value: Any, default: int = 20, maximum: int = 100) -> int:
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return default

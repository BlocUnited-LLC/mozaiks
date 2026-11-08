"""Scoping helpers for the activity_feed module."""
from __future__ import annotations

from typing import Any

# ctx.persistence owns app scoping and rejects caller-supplied app_id filters.


def actor_id(ctx) -> str:
    return getattr(ctx, "user_id", None) or "anonymous"


def feed_query(ctx, *, user_id: str) -> dict[str, Any]:
    """Query for events that belong in user_id's feed."""
    return {"feed_user_ids": user_id}


def profile_activity_query(ctx, *, user_id: str) -> dict[str, Any]:
    """Query for the public activity timeline of a specific user."""
    return {"actor_id": user_id}

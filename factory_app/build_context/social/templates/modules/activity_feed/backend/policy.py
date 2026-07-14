"""Scoping helpers for the activity_feed module."""
from __future__ import annotations

from typing import Any


def actor_id(ctx) -> str:
    return getattr(ctx, "user_id", None) or "anonymous"


def app_scope(ctx) -> dict[str, Any]:
    app_id = getattr(ctx, "app_id", None)
    return {"app_id": app_id} if app_id else {}


def feed_query(ctx, *, user_id: str) -> dict[str, Any]:
    """Query for events that belong in user_id's feed."""
    return {**app_scope(ctx), "feed_user_ids": user_id}


def profile_activity_query(ctx, *, user_id: str) -> dict[str, Any]:
    """Query for the public activity timeline of a specific user."""
    return {**app_scope(ctx), "actor_id": user_id}

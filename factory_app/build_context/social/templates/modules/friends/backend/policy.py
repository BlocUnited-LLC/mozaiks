"""Scoping helpers for the friends module."""
from __future__ import annotations

from typing import Any


def actor_id(ctx) -> str:
    return getattr(ctx, "user_id", None) or "anonymous"


def app_scope(ctx) -> dict[str, Any]:
    app_id = getattr(ctx, "app_id", None)
    return {"app_id": app_id} if app_id else {}


def friendship_query(ctx, *, user_id: str) -> dict[str, Any]:
    """Query scoped to app + a specific user's friendship records."""
    return {**app_scope(ctx), "user_id": user_id}


def request_query_as_requester(ctx, *, requester_id: str, status: str | None = None) -> dict[str, Any]:
    q: dict[str, Any] = {**app_scope(ctx), "requester_id": requester_id}
    if status and status != "all":
        q["status"] = status
    return q


def request_query_as_recipient(ctx, *, recipient_id: str, status: str | None = None) -> dict[str, Any]:
    q: dict[str, Any] = {**app_scope(ctx), "recipient_id": recipient_id}
    if status and status != "all":
        q["status"] = status
    return q

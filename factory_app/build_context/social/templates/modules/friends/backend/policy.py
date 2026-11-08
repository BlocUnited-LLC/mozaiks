"""Scoping helpers for the friends module."""
from __future__ import annotations

from typing import Any

# ctx.persistence owns app scoping and rejects caller-supplied app_id filters.


def actor_id(ctx) -> str:
    return getattr(ctx, "user_id", None) or "anonymous"


def friendship_query(ctx, *, user_id: str) -> dict[str, Any]:
    """Query scoped to a specific user's friendship records."""
    return {"user_id": user_id}


def request_query_as_requester(ctx, *, requester_id: str, status: str | None = None) -> dict[str, Any]:
    q: dict[str, Any] = {"requester_id": requester_id}
    if status and status != "all":
        q["status"] = status
    return q


def request_query_as_recipient(ctx, *, recipient_id: str, status: str | None = None) -> dict[str, Any]:
    q: dict[str, Any] = {"recipient_id": recipient_id}
    if status and status != "all":
        q["status"] = status
    return q

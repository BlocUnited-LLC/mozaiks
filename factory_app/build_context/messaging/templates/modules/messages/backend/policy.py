from __future__ import annotations

from typing import Any


def actor_id(ctx) -> str:
    return getattr(ctx, "user_id", None) or "anonymous"


def require_participant(thread: dict[str, Any], user_id: str) -> bool:
    return user_id in (thread.get("participant_ids") or [])


def scoped_thread_query(ctx, **extra) -> dict[str, Any]:
    """Base thread query scoped to the current user as a participant."""
    query: dict[str, Any] = {"participant_ids": actor_id(ctx)}
    query.update(extra)
    return query

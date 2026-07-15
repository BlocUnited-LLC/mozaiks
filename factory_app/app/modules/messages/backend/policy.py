from __future__ import annotations

from typing import Any


def actor_id(ctx) -> str:
    return str(getattr(ctx, "user_id", None) or "anonymous").strip() or "anonymous"


def is_participant(thread: dict[str, Any], user_id: str) -> bool:
    return user_id in (thread.get("participant_ids") or [])


def participant_thread_query(ctx, **extra: Any) -> dict[str, Any]:
    query: dict[str, Any] = {"participant_ids": actor_id(ctx)}
    query.update({key: value for key, value in extra.items() if value is not None})
    return query

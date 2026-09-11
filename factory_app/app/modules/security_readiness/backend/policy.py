from __future__ import annotations

from typing import Any


def actor_id(ctx) -> str:
    return str(getattr(ctx, "user_id", None) or "system").strip() or "system"


def app_findings_query(ctx, *, app_id: str, **extra: Any) -> dict[str, Any]:
    query: dict[str, Any] = {
        "app_id": app_id,
        "owner_user_id": actor_id(ctx),
    }
    query.update({key: value for key, value in extra.items() if value is not None})
    return query


def finding_query(ctx, *, finding_id: str) -> dict[str, Any]:
    return {
        "finding_id": finding_id,
        "owner_user_id": actor_id(ctx),
    }

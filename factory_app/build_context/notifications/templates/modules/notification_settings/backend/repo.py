from __future__ import annotations

from collections.abc import Mapping
from typing import Any

Document = dict[str, Any]


def _collection(ctx, entity_name: str):
    persistence = getattr(ctx, "persistence", None)
    if persistence is None:
        raise RuntimeError("Persistence is not available for this app context.")
    return persistence.collection("notification_settings", entity_name)


def _document(value: Any) -> Document | None:
    return dict(value) if isinstance(value, Mapping) else None


async def get_preferences(ctx, *, user_id: str) -> Document | None:
    app_id = getattr(ctx, "app_id", None)
    return _document(
        await _collection(ctx, "preferences").find_one(
            {"app_id": app_id, "user_id": user_id}
        )
    )


async def upsert_preferences(ctx, *, user_id: str, update: Document) -> None:
    from .schemas import timestamp_now

    app_id = getattr(ctx, "app_id", None)
    await _collection(ctx, "preferences").update_one(
        {"app_id": app_id, "user_id": user_id},
        {"$set": {**update, "app_id": app_id, "user_id": user_id, "updated_at": timestamp_now()}},
        upsert=True,
    )

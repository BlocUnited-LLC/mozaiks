from __future__ import annotations

from typing import Any

from . import repo
from .schemas import notification_preferences_doc, preferences_response


async def get_notification_preferences(ctx, *, user_id: str) -> dict[str, Any]:
    doc = await repo.get_preferences(ctx, user_id=user_id)
    if doc is None:
        # Return defaults if the user has never saved preferences
        doc = notification_preferences_doc(
            user_id=user_id,
            app_id=getattr(ctx, "app_id", None),
        )
    return {"preferences": preferences_response(doc)}


async def update_notification_preferences(
    ctx,
    *,
    user_id: str,
    email_enabled: bool | None = None,
    push_enabled: bool | None = None,
    sms_enabled: bool | None = None,
) -> dict[str, Any]:
    existing = await repo.get_preferences(ctx, user_id=user_id)
    current = preferences_response(existing) if existing else {
        "email_enabled": True,
        "push_enabled": True,
        "sms_enabled": False,
    }

    update: dict[str, Any] = {}
    if email_enabled is not None:
        update["email_enabled"] = email_enabled
    if push_enabled is not None:
        update["push_enabled"] = push_enabled
    if sms_enabled is not None:
        update["sms_enabled"] = sms_enabled

    merged = {**current, **update}
    await repo.upsert_preferences(ctx, user_id=user_id, update=merged)
    return {"success": True, "preferences": merged}

"""AccountDataHandler for the activity_feed module.

Implements GDPR account deletion and data portability export for all
user-scoped activity event records.

Deletion policy:
  - Activity events where the deleted user is the actor (actor_id): hard-deleted.
  - Activity events where the deleted user is the about_user (about_user_id):
    anonymized by clearing the about_user_id field so the event record
    survives for other participants' feeds.
  - Feed membership (feed_user_ids array) updated to remove the deleted user_id
    so events no longer appear in queries scoped to that user.

We avoid deleting events where the deleted user appears only as an observer
(feed_user_ids member) because this would remove legitimate activity from
other participants' feeds. Instead we pull their ID out of the array.
"""
from __future__ import annotations

from typing import Any


_EVENTS_COL = "activity_feed.events"


class AccountDataHandler:
    async def delete_user_data(self, ctx: Any, *, user_id: str) -> None:
        from mozaiksai.core.runtime.app.data import app_data_from_context

        p = app_data_from_context(ctx)
        col = await p.collection(_EVENTS_COL)

        # Hard-delete all events where this user was the actor.
        await col.delete_many({"actor_id": user_id})

        # Anonymize events where this user is the about_user.
        await col.update_many(
            {"about_user_id": user_id},
            {"$unset": {"about_user_id": ""}},
        )

        # Remove user from feed_user_ids membership so events no longer
        # surface in queries for this user without disturbing other participants.
        await col.update_many(
            {"feed_user_ids": user_id},
            {"$pull": {"feed_user_ids": user_id}},
        )

    async def export_user_data(self, ctx: Any, *, user_id: str) -> dict[str, Any]:
        from mozaiksai.core.runtime.app.data import app_data_from_context

        p = app_data_from_context(ctx)
        col = await p.collection(_EVENTS_COL)

        cursor = col.find(
            {"actor_id": user_id},
            {"_id": 0},
        ).sort("created_at", -1).limit(500)
        events = await cursor.to_list(length=500)

        return {"activity_events": events}

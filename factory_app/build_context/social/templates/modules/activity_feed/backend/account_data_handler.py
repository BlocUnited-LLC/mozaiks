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

from mozaiksai.core.runtime.composition.bson_safe import json_safe_bson

_PAGE_SIZE = 100


class AccountDataHandler:
    def __init__(self, persistence: Any) -> None:
        self._persistence = persistence

    def _collection(self, app_id: str, user_id: str) -> Any:
        if self._persistence.app_id != app_id:
            raise PermissionError("account persistence is bound to another app")
        principal = self._persistence.principal
        if principal is None or not user_id or principal.user_id != user_id:
            raise PermissionError("account persistence is bound to another user")
        return self._persistence.collection("activity_feed", "events")

    async def _update_matching(self, collection: Any, query: dict[str, Any], update: dict[str, Any]) -> int:
        cursor = dict(query)
        changed = 0
        while True:
            page = await collection.find_many(cursor, limit=_PAGE_SIZE, sort=[("_id", 1)], projection={"_id": 1})
            for row in page:
                result = await collection.update_one({**query, "_id": row["_id"]}, update)
                changed += int(result.modified_count)
            if len(page) < _PAGE_SIZE:
                return changed
            cursor = {**query, "_id": {"$gt": page[-1]["_id"]}}

    async def delete_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        col = self._collection(app_id, user_id)

        # Hard-delete all events where this user was the actor.
        actor_result = await col.delete_many({"actor_id": user_id})

        # Anonymize events where this user is the about_user.
        about_count = await self._update_matching(
            col, {"about_user_id": user_id},
            {"$unset": {"about_user_id": ""}},
        )

        # Remove user from feed_user_ids membership so events no longer
        # surface in queries for this user without disturbing other participants.
        feed_count = await self._update_matching(
            col, {"feed_user_ids": user_id},
            {"$pull": {"feed_user_ids": user_id}},
        )
        return {
            "events_deleted": getattr(actor_result, "deleted_count", 0),
            "events_anonymized": about_count,
            "feed_memberships_removed": feed_count,
        }

    async def export_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        col = self._collection(app_id, user_id)
        events = await col.aggregate([
            {"$match": {"actor_id": user_id}},
            {"$sort": {"created_at": -1}},
            {"$limit": 500},
            {"$project": {"_id": 0}},
        ])
        return {"activity_events": json_safe_bson(events)}

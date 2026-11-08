"""AccountDataHandler for the friends module.

GDPR deletion and export for friend requests and friendship records.

Deletion policy:
  - Friendship records where user_id == deleted user: hard-deleted.
  - Friendship records where friend_user_id == deleted user: hard-deleted.
    (Both sides of the symmetric pair are removed so the peer also loses
    the connection, which is correct — the user's consent to the
    relationship is being revoked.)
  - Friend requests where requester_id OR recipient_id == deleted user:
    hard-deleted (both sent and received requests).
"""
from __future__ import annotations

from typing import Any

from mozaiksai.core.runtime.composition.bson_safe import json_safe_bson

_PAGE_SIZE = 100


class AccountDataHandler:
    """GDPR delete + export handler for the friends module."""

    def __init__(self, persistence: Any) -> None:
        self._persistence = persistence

    def _collection(self, app_id: str, user_id: str, name: str) -> Any:
        if self._persistence.app_id != app_id:
            raise PermissionError("account persistence is bound to another app")
        principal = self._persistence.principal
        if principal is None or not user_id or principal.user_id != user_id:
            raise PermissionError("account persistence is bound to another user")
        return self._persistence.collection("friends", name)

    async def _records(self, app_id: str, user_id: str, name: str, owner_field: str) -> list[dict[str, Any]]:
        collection = self._collection(app_id, user_id, name)
        query: dict[str, Any] = {owner_field: user_id}
        records = []
        while True:
            page = await collection.find_many(query, limit=_PAGE_SIZE, sort=[("_id", 1)])
            records.extend({key: value for key, value in row.items() if key != "_id"} for row in page)
            if len(page) < _PAGE_SIZE:
                return records
            query = {owner_field: user_id, "_id": {"$gt": page[-1]["_id"]}}

    async def delete_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        friendships = self._collection(app_id, user_id, "friendships")
        requests = self._collection(app_id, user_id, "requests")

        # Remove all friendship records involving this user (both directions).
        fs_as_user = await friendships.delete_many({"user_id": user_id})
        fs_as_friend = await friendships.delete_many({"friend_user_id": user_id})

        # Remove all friend requests involving this user (sent or received).
        req_sent = await requests.delete_many({"requester_id": user_id})
        req_received = await requests.delete_many({"recipient_id": user_id})

        deleted_count = (
            fs_as_user.deleted_count
            + fs_as_friend.deleted_count
            + req_sent.deleted_count
            + req_received.deleted_count
        )
        return {
            "friendships_deleted": fs_as_user.deleted_count + fs_as_friend.deleted_count,
            "requests_deleted": req_sent.deleted_count + req_received.deleted_count,
            "deleted_count": deleted_count,
        }

    async def export_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        return json_safe_bson({
            "friendships": await self._records(app_id, user_id, "friendships", "user_id"),
            "friend_requests_sent": await self._records(app_id, user_id, "requests", "requester_id"),
            "friend_requests_received": await self._records(app_id, user_id, "requests", "recipient_id"),
        })

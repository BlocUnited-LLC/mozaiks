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

_FRIENDSHIPS = "friends.friendships"
_REQUESTS = "friends.requests"


class AccountDataHandler:
    """GDPR delete + export handler for the friends module."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def delete_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        scope = {"app_id": app_id}

        # Remove all friendship records involving this user (both directions).
        fs_as_user = await self._db[_FRIENDSHIPS].delete_many({**scope, "user_id": user_id})
        fs_as_friend = await self._db[_FRIENDSHIPS].delete_many({**scope, "friend_user_id": user_id})

        # Remove all friend requests involving this user (sent or received).
        req_sent = await self._db[_REQUESTS].delete_many({**scope, "requester_id": user_id})
        req_received = await self._db[_REQUESTS].delete_many({**scope, "recipient_id": user_id})

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
        scope = {"app_id": app_id}

        friendships = await self._db[_FRIENDSHIPS].find(
            {**scope, "user_id": user_id}, {"_id": 0}
        ).to_list(length=None)

        requests_sent = await self._db[_REQUESTS].find(
            {**scope, "requester_id": user_id}, {"_id": 0}
        ).to_list(length=None)

        requests_received = await self._db[_REQUESTS].find(
            {**scope, "recipient_id": user_id}, {"_id": 0}
        ).to_list(length=None)

        return {
            "friendships": friendships,
            "friend_requests_sent": requests_sent,
            "friend_requests_received": requests_received,
        }

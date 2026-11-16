"""Account export and deletion over the authoritative connection requests."""
from __future__ import annotations

from typing import Any

from mozaiksai.core.runtime.composition.bson_safe import json_safe_bson

from .repo import friendship_view

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
        return self._persistence.collection("friends", "requests")

    async def _records(self, app_id: str, user_id: str, owner_field: str) -> list[dict[str, Any]]:
        collection = self._collection(app_id, user_id)
        query: dict[str, Any] = {owner_field: user_id}
        records = []
        while True:
            page = await collection.find_many(query, limit=_PAGE_SIZE, sort=[("_id", 1)])
            records.extend({key: value for key, value in row.items() if key != "_id"} for row in page)
            if len(page) < _PAGE_SIZE:
                return records
            query = {owner_field: user_id, "_id": {"$gt": page[-1]["_id"]}}

    async def delete_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        collection = self._collection(app_id, user_id)
        parties = {"$or": [{"requester_id": user_id}, {"recipient_id": user_id}]}
        connections = await collection.count({**parties, "status": "accepted"})
        result = await collection.delete_many(parties)
        return {"friendships_deleted": connections, "requests_deleted": result.deleted_count,
                "deleted_count": result.deleted_count}

    async def export_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        sent = await self._records(app_id, user_id, "requester_id")
        received = await self._records(app_id, user_id, "recipient_id")
        return json_safe_bson({
            "friendships": [friendship_view(row, user_id) for row in [*sent, *received] if row.get("status") == "accepted"],
            "friend_requests_sent": sent,
            "friend_requests_received": received,
        })

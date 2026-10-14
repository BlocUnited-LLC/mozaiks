"""Export and delete account contributions while preserving peer conversations."""
from __future__ import annotations

from typing import Any

_PAGE_SIZE = 100


class AccountDataHandler:
    """Use the host's app-bound persistence and the same collections as the repo."""

    def __init__(self, persistence: Any) -> None:
        self.persistence = persistence

    def _collection(self, app_id: str, name: str) -> Any:
        if self.persistence.app_id != app_id:
            raise PermissionError("account persistence is bound to another app")
        return self.persistence.collection("messages", name)

    async def _records(self, app_id: str, name: str, query: dict[str, Any]) -> list[dict[str, Any]]:
        collection = self._collection(app_id, name)
        records = []
        cursor = dict(query)
        while True:
            page = await collection.find_many(cursor, limit=_PAGE_SIZE, sort=[("_id", 1)])
            records.extend({key: value for key, value in row.items() if key != "_id"} for row in page)
            if len(page) < _PAGE_SIZE:
                return records
            cursor = {**query, "_id": {"$gt": page[-1]["_id"]}}

    async def delete_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        threads = self._collection(app_id, "threads")
        messages = self._collection(app_id, "messages")
        threads_deleted = 0
        for thread in await self._records(app_id, "threads", {"created_by": user_id}):
            peer_messages = await messages.count({
                "thread_id": thread["thread_id"], "sender_id": {"$ne": user_id},
            })
            if peer_messages == 0:
                result = await threads.delete_one({"thread_id": thread["thread_id"], "created_by": user_id})
                threads_deleted += int(result.deleted_count)
        messages_result = await messages.delete_many({"sender_id": user_id})
        reads_result = await self._collection(app_id, "thread_reads").delete_many({"user_id": user_id})
        return {
            "threads_deleted": threads_deleted,
            "messages_deleted": int(messages_result.deleted_count),
            "read_state_deleted": int(reads_result.deleted_count),
        }

    async def export_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        return {
            "threads_created": await self._records(app_id, "threads", {"created_by": user_id}),
            "messages_sent": await self._records(app_id, "messages", {"sender_id": user_id}),
        }

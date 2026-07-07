from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from mozaiksai.core.runtime.persistence import app_data_from_context

# Collection aliases — resolved through the app's DATA_COLLECTIONS to stable
# MongoDB collection names declared in the data migration fragment.
_THREADS_ALIAS = "messages.threads"
_MESSAGES_ALIAS = "messages.messages"
_READ_STATES_ALIAS = "messages.thread_reads"
_NOTIFICATIONS_ALIAS = "messages.notifications"

Document = dict[str, Any]


def _persistence(ctx):
    return app_data_from_context(ctx)


def _normalize_doc(value: Any) -> Document | None:
    return dict(value) if isinstance(value, dict) else None


def _normalize_docs(value: Any) -> list[Document]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


class ThreadRepo:
    async def list(
        self,
        ctx,
        *,
        query: dict[str, Any],
        limit: int,
        before: str | None = None,
    ) -> list[Document]:
        col = _persistence(ctx).collection(_THREADS_ALIAS)
        if before:
            query = {**query, "updated_at": {"$lt": before}}
        cursor = col.find(query, {"_id": 0}).sort("updated_at", -1).limit(limit)
        return _normalize_docs(await cursor.to_list(length=limit))

    async def get(self, ctx, *, thread_id: str) -> Document | None:
        col = _persistence(ctx).collection(_THREADS_ALIAS)
        return _normalize_doc(await col.find_one({"thread_id": thread_id}, {"_id": 0}))

    async def find_by_participants(
        self, ctx, *, participant_ids: Sequence[str], thread_type: str = "dm"
    ) -> Document | None:
        """Find an open thread with exactly this set of participants."""
        col = _persistence(ctx).collection(_THREADS_ALIAS)
        return _normalize_doc(await col.find_one(
            {
                "participant_ids": {"$all": participant_ids, "$size": len(participant_ids)},
                "thread_type": thread_type,
                "status": "open",
            },
            {"_id": 0},
        ))

    async def insert(self, ctx, *, doc: Mapping[str, Any]) -> None:
        col = _persistence(ctx).collection(_THREADS_ALIAS)
        await col.insert_one(dict(doc))

    async def update_last_message(
        self, ctx, *, thread_id: str, now: str, preview: Mapping[str, Any]
    ) -> None:
        col = _persistence(ctx).collection(_THREADS_ALIAS)
        await col.update_one(
            {"thread_id": thread_id},
            {"$set": {"updated_at": now, "last_message_at": now, "last_message": preview}},
        )

    async def update_status(
        self, ctx, *, thread_id: str, status: str, now: str
    ) -> int:
        col = _persistence(ctx).collection(_THREADS_ALIAS)
        result = await col.update_one(
            {"thread_id": thread_id},
            {"$set": {"status": status, "updated_at": now}},
        )
        return int(result.matched_count)

    async def remove_participant(
        self, ctx, *, thread_id: str, user_id: str, now: str
    ) -> int:
        col = _persistence(ctx).collection(_THREADS_ALIAS)
        result = await col.update_one(
            {"thread_id": thread_id},
            {
                "$pull": {"participant_ids": user_id},
                "$set": {"updated_at": now},
            },
        )
        return int(result.matched_count)

    async def count_participants(self, ctx, *, thread_id: str) -> int:
        col = _persistence(ctx).collection(_THREADS_ALIAS)
        thread = _normalize_doc(await col.find_one({"thread_id": thread_id}, {"participant_ids": 1}))
        if not thread:
            return 0
        return len(thread.get("participant_ids") or [])


class MessageRepo:
    async def list(
        self,
        ctx,
        *,
        thread_id: str,
        limit: int,
        before: str | None = None,
    ) -> list[Document]:
        col = _persistence(ctx).collection(_MESSAGES_ALIAS)
        query: dict[str, Any] = {
            "thread_id": thread_id,
            "is_deleted": {"$ne": True},
        }
        if before:
            query["created_at"] = {"$lt": before}
        # Descend for cursor, then reverse so caller sees oldest-first
        cursor = col.find(query, {"_id": 0}).sort("created_at", -1).limit(limit)
        msgs = _normalize_docs(await cursor.to_list(length=limit))
        msgs.reverse()
        return msgs

    async def insert(self, ctx, *, doc: Mapping[str, Any]) -> None:
        col = _persistence(ctx).collection(_MESSAGES_ALIAS)
        await col.insert_one(dict(doc))

    async def count_unread(
        self, ctx, *, thread_id: str, user_id: str, since: str
    ) -> int:
        col = _persistence(ctx).collection(_MESSAGES_ALIAS)
        return int(await col.count_documents(
            {
                "thread_id": thread_id,
                "sender_id": {"$ne": user_id},
                "created_at": {"$gt": since},
                "is_deleted": {"$ne": True},
            }
        ))

    async def get_by_id(self, ctx, *, thread_id: str, message_id: str) -> Document | None:
        col = _persistence(ctx).collection(_MESSAGES_ALIAS)
        return _normalize_doc(await col.find_one(
            {"thread_id": thread_id, "message_id": message_id}, {"_id": 0}
        ))

    async def update(
        self, ctx, *, thread_id: str, message_id: str, updates: dict[str, Any]
    ) -> int:
        col = _persistence(ctx).collection(_MESSAGES_ALIAS)
        result = await col.update_one(
            {"thread_id": thread_id, "message_id": message_id},
            {"$set": dict(updates)},
        )
        return int(result.matched_count)

    async def get_latest(self, ctx, *, thread_id: str) -> Document | None:
        col = _persistence(ctx).collection(_MESSAGES_ALIAS)
        cursor = (
            col.find(
                {"thread_id": thread_id, "is_deleted": {"$ne": True}}, {"_id": 0}
            )
            .sort("created_at", -1)
            .limit(1)
        )
        docs = _normalize_docs(await cursor.to_list(length=1))
        return docs[0] if docs else None


class ReadStateRepo:
    async def get(self, ctx, *, thread_id: str, user_id: str) -> Document | None:
        col = _persistence(ctx).collection(_READ_STATES_ALIAS)
        return _normalize_doc(await col.find_one(
            {"thread_id": thread_id, "user_id": user_id}, {"_id": 0}
        ))

    async def list_for_user(
        self, ctx, *, user_id: str, thread_ids: list[str]
    ) -> list[Document]:
        if not thread_ids:
            return []
        col = _persistence(ctx).collection(_READ_STATES_ALIAS)
        cursor = col.find(
            {"user_id": user_id, "thread_id": {"$in": thread_ids}}, {"_id": 0}
        )
        return _normalize_docs(await cursor.to_list(length=len(thread_ids)))

    async def upsert(
        self,
        ctx,
        *,
        thread_id: str,
        user_id: str,
        message_id: str | None,
        now: str,
    ) -> None:
        col = _persistence(ctx).collection(_READ_STATES_ALIAS)
        await col.update_one(
            {"thread_id": thread_id, "user_id": user_id},
            {"$set": {"last_read_message_id": message_id, "read_at": now}},
            upsert=True,
        )

    async def delete_for_user(self, ctx, *, thread_id: str, user_id: str) -> None:
        col = _persistence(ctx).collection(_READ_STATES_ALIAS)
        await col.delete_one({"thread_id": thread_id, "user_id": user_id})


class NotificationRepo:
    async def list(self, ctx, *, user_id: str, limit: int) -> list[Document]:
        col = _persistence(ctx).collection(_NOTIFICATIONS_ALIAS)
        cursor = (
            col.find({"user_id": user_id}, {"_id": 0})
            .sort("created_at", -1)
            .limit(limit)
        )
        return _normalize_docs(await cursor.to_list(length=limit))

    async def count_unread(self, ctx, *, user_id: str) -> int:
        col = _persistence(ctx).collection(_NOTIFICATIONS_ALIAS)
        return int(await col.count_documents({"user_id": user_id, "read": {"$ne": True}}))

    async def insert(self, ctx, *, doc: Mapping[str, Any]) -> None:
        col = _persistence(ctx).collection(_NOTIFICATIONS_ALIAS)
        await col.insert_one(dict(doc))

    async def mark_read(self, ctx, *, notification_id: str, user_id: str) -> int:
        col = _persistence(ctx).collection(_NOTIFICATIONS_ALIAS)
        result = await col.update_one(
            {"notification_id": notification_id, "user_id": user_id},
            {"$set": {"read": True}},
        )
        return int(result.matched_count)

    async def mark_all_read(self, ctx, *, user_id: str) -> None:
        col = _persistence(ctx).collection(_NOTIFICATIONS_ALIAS)
        await col.update_many(
            {"user_id": user_id, "read": {"$ne": True}},
            {"$set": {"read": True}},
        )

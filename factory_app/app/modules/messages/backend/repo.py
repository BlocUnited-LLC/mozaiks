from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

Document = dict[str, Any]


def _collection(ctx, entity_name: str):
    persistence = getattr(ctx, "persistence", None)
    if persistence is None:
        raise RuntimeError("Persistence is not available for this app context.")
    return persistence.collection("messages", entity_name)


def _document(value: Any) -> Document | None:
    return dict(value) if isinstance(value, Mapping) else None


def _documents(values: list[Any] | None) -> list[Document]:
    return [dict(item) for item in values or [] if isinstance(item, Mapping)]


class ThreadRepo:
    async def insert(self, ctx, *, record: Mapping[str, Any]) -> None:
        await _collection(ctx, "threads").insert_one(dict(record))

    async def get(self, ctx, *, thread_id: str) -> Document | None:
        return _document(await _collection(ctx, "threads").find_one({"thread_id": thread_id}))

    async def list(self, ctx, *, query: dict[str, Any], limit: int) -> list[Document]:
        return _documents(
            await _collection(ctx, "threads").find_many(
                query,
                limit=limit,
                sort=[("updated_at", -1)],
            )
        )

    async def update(self, ctx, *, thread_id: str, updates: Mapping[str, Any]) -> int:
        result = await _collection(ctx, "threads").update_one(
            {"thread_id": thread_id},
            {"$set": dict(updates)},
        )
        return int(getattr(result, "matched_count", 0) or 0)

    async def update_last_message(
        self,
        ctx,
        *,
        thread_id: str,
        updated_at: str,
        preview: Mapping[str, Any],
        participant_ids: Sequence[str] | None = None,
    ) -> int:
        updates: dict[str, Any] = {
            "updated_at": updated_at,
            "last_message_at": updated_at,
            "last_message": dict(preview),
        }
        if participant_ids is not None:
            updates["participant_ids"] = list(participant_ids)
        return await self.update(ctx, thread_id=thread_id, updates=updates)


class MessageRepo:
    async def insert(self, ctx, *, record: Mapping[str, Any]) -> None:
        await _collection(ctx, "messages").insert_one(dict(record))

    async def list(self, ctx, *, thread_id: str, limit: int) -> list[Document]:
        return _documents(
            await _collection(ctx, "messages").find_many(
                {"thread_id": thread_id, "is_deleted": {"$ne": True}},
                limit=limit,
                sort=[("created_at", 1)],
            )
        )


class ReadStateRepo:
    async def upsert(self, ctx, *, thread_id: str, user_id: str, read_at: str) -> None:
        await _collection(ctx, "thread_reads").update_one(
            {"thread_id": thread_id, "user_id": user_id},
            {"$set": {"thread_id": thread_id, "user_id": user_id, "read_at": read_at}},
            upsert=True,
        )

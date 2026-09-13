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

    async def get(self, ctx, *, query: Mapping[str, Any]) -> Document | None:
        return _document(await _collection(ctx, "threads").find_one(dict(query)))

    async def list(self, ctx, *, query: dict[str, Any], limit: int) -> list[Document]:
        return _documents(
            await _collection(ctx, "threads").find_many(
                query,
                limit=limit,
                sort=[("updated_at", -1)],
            )
        )

    async def update(self, ctx, *, query: Mapping[str, Any], updates: Mapping[str, Any]) -> int:
        result = await _collection(ctx, "threads").update_one(
            dict(query),
            {"$set": dict(updates)},
        )
        return int(getattr(result, "matched_count", 0) or 0)

    async def update_last_message(
        self,
        ctx,
        *,
        thread_id: str,
        query: Mapping[str, Any] | None = None,
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
        return await self.update(ctx, query=query or {"thread_id": thread_id}, updates=updates)


class MessageRepo:
    async def insert(self, ctx, *, record: Mapping[str, Any]) -> None:
        await _collection(ctx, "messages").insert_one(dict(record))

    async def list(self, ctx, *, query: Mapping[str, Any], limit: int) -> list[Document]:
        return _documents(
            await _collection(ctx, "messages").find_many(
                dict(query),
                limit=limit,
                sort=[("created_at", 1)],
            )
        )


class ReadStateRepo:
    async def upsert(
        self,
        ctx,
        *,
        thread_id: str,
        user_id: str,
        read_at: str,
        scope_type: str | None = None,
        scope_id: str | None = None,
    ) -> None:
        query = {"thread_id": thread_id, "user_id": user_id}
        updates = {"thread_id": thread_id, "user_id": user_id, "read_at": read_at}
        if scope_type:
            query["scope_type"] = scope_type
            updates["scope_type"] = scope_type
        if scope_id:
            query["scope_id"] = scope_id
            updates["scope_id"] = scope_id
        await _collection(ctx, "thread_reads").update_one(
            query,
            {"$set": updates},
            upsert=True,
        )

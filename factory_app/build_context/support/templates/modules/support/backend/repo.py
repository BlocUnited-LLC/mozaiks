from __future__ import annotations

from collections.abc import Mapping
from typing import Any

Document = dict[str, Any]


def _collection(ctx, entity_name: str):
    persistence = getattr(ctx, "persistence", None)
    if persistence is None:
        raise RuntimeError("Persistence is not available for this app context.")
    return persistence.collection("support", entity_name)


def _document(value: Any) -> Document | None:
    return dict(value) if isinstance(value, Mapping) else None


def _documents(values: list[Any] | None) -> list[Document]:
    return [dict(item) for item in values or [] if isinstance(item, Mapping)]


class SupportRequestRepo:
    async def insert(self, ctx, *, record: Mapping[str, Any]) -> None:
        await _collection(ctx, "requests").insert_one(dict(record))

    async def get(self, ctx, *, request_id: str) -> Document | None:
        return _document(await _collection(ctx, "requests").find_one({"request_id": request_id}))

    async def list(self, ctx, *, query: dict[str, Any], limit: int) -> list[Document]:
        return _documents(
            await _collection(ctx, "requests").find_many(
                query,
                limit=limit,
                sort=[("updated_at", -1)],
            )
        )

    async def update(self, ctx, *, request_id: str, updates: Mapping[str, Any]) -> int:
        result = await _collection(ctx, "requests").update_one(
            {"request_id": request_id},
            {"$set": dict(updates)},
        )
        return int(getattr(result, "matched_count", 0) or 0)

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

Document = dict[str, Any]


def _collection(ctx, entity_name: str):
    persistence = getattr(ctx, "persistence", None)
    if persistence is None:
        raise RuntimeError("Persistence is not available for this app context.")
    return persistence.collection("activity_feed", entity_name)


def _norms(v: Any) -> list[Document]:
    return [dict(i) for i in v or [] if isinstance(i, Mapping)]


class ActivityEventRepo:
    async def insert(self, ctx, doc: Document) -> None:
        await _collection(ctx, "events").insert_one({**doc})

    async def list(
        self,
        ctx,
        *,
        query: dict[str, Any],
        limit: int,
        before: str | None = None,
    ) -> list[Document]:
        if before:
            query = {**query, "created_at": {"$lt": before}}
        return _norms(
            await _collection(ctx, "events").find_many(
                query,
                limit=limit,
                sort=[("created_at", -1)],
            )
        )

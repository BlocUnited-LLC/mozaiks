"""Persistence operations for the activity_feed module."""
from __future__ import annotations

from typing import Any

from mozaiksai.core.runtime.persistence import app_data_from_context

_EVENTS = "activity_feed.events"

Document = dict[str, Any]


def _p(ctx):
    return app_data_from_context(ctx)


def _norms(v: Any) -> list[Document]:
    return [dict(i) for i in v if isinstance(i, dict)] if isinstance(v, list) else []


class ActivityEventRepo:
    async def insert(self, ctx, doc: Document) -> None:
        await _p(ctx).collection(_EVENTS).insert_one({**doc})

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
        cursor = (
            _p(ctx).collection(_EVENTS)
            .find(query, {"_id": 0})
            .sort("created_at", -1)
            .limit(limit)
        )
        return _norms(await cursor.to_list(length=limit))

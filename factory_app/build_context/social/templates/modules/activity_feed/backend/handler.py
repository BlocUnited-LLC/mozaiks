"""Thin dispatch handler for the activity_feed module."""
from __future__ import annotations

from .service import ActivityFeedService


class ActivityFeedHandler:
    def __init__(self, service: ActivityFeedService | None = None) -> None:
        self._service = service or ActivityFeedService()

    async def get_feed(self, ctx, limit=None, before=None, **_params):
        return await self._service.get_feed(ctx, limit=limit, before=before)

    async def get_profile_activity(self, ctx, user_id: str | None = None, limit=None, before=None, **_params):
        return await self._service.get_profile_activity(
            ctx,
            user_id=user_id,
            limit=limit,
            before=before,
        )

    async def record_activity(self, ctx, **payload):
        return await self._service.record_activity(ctx, payload=payload)

    # Reaction entry points — called by the module executor via reactions.yaml
    async def on_friendship_created(self, ctx, **event):
        await self._service.on_friendship_created(ctx, event)

    async def on_post_published(self, ctx, **event):
        await self._service.on_post_published(ctx, event)

    async def on_post_commented(self, ctx, **event):
        await self._service.on_post_commented(ctx, event)

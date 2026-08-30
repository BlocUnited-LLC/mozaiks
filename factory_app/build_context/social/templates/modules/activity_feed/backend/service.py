"""Business logic for the activity_feed module."""
from __future__ import annotations

from uuid import uuid4

from .policy import actor_id, app_scope, feed_query, profile_activity_query
from .repo import ActivityEventRepo
from .schemas import (
    ACTIVITY_FRIENDSHIP_CREATED,
    ACTIVITY_POST_COMMENTED,
    ACTIVITY_POST_PUBLISHED,
    coerce_limit,
    timestamp_now,
)


class ActivityFeedService:
    def __init__(self) -> None:
        self._repo = ActivityEventRepo()

    # ── Public feed actions ───────────────────────────────────────────────────

    async def get_feed(self, ctx, *, limit: int | None = None, before: str | None = None) -> dict:
        user_id = actor_id(ctx)
        bounded = coerce_limit(limit)
        rows = await self._repo.list(
            ctx,
            query=feed_query(ctx, user_id=user_id),
            limit=bounded + 1,
            before=before,
        )
        has_more = len(rows) > bounded
        page = rows[:bounded]
        return {
            "events": page,
            "count": len(page),
            "next_cursor": page[-1]["created_at"] if has_more and page else None,
        }

    async def get_profile_activity(
        self, ctx, *, user_id: str | None = None, limit: int | None = None, before: str | None = None
    ) -> dict:
        target_user = user_id or actor_id(ctx)
        bounded = coerce_limit(limit)
        rows = await self._repo.list(
            ctx,
            query=profile_activity_query(ctx, user_id=target_user),
            limit=bounded + 1,
            before=before,
        )
        has_more = len(rows) > bounded
        page = rows[:bounded]
        return {
            "events": page,
            "count": len(page),
            "next_cursor": page[-1]["created_at"] if has_more and page else None,
        }

    # ── Internal record action ────────────────────────────────────────────────

    async def record_activity(self, ctx, *, payload: dict) -> dict:
        now = timestamp_now()
        doc = {
            "event_id": f"ae_{uuid4().hex}",
            "activity_type": payload.get("activity_type", "unknown"),
            "actor_id": payload.get("actor_id", ""),
            "about_user_id": payload.get("about_user_id"),
            "subject_id": payload.get("subject_id"),
            "subject_type": payload.get("subject_type"),
            "subject_label": payload.get("subject_label"),
            "feed_user_ids": payload.get("feed_user_ids") or [],
            "metadata": payload.get("metadata") or {},
            "created_at": now,
            **app_scope(ctx),
        }
        await self._repo.insert(ctx, doc)
        await ctx.emit("domain.social.activity.recorded", {
            "event_id": doc["event_id"],
            "activity_type": doc["activity_type"],
            "actor_id": doc["actor_id"],
            "created_at": doc["created_at"],
        })
        return {"success": True, "event_id": doc["event_id"]}

    # ── Reaction handlers (called by reactions.yaml routing) ──────────────────

    async def on_friendship_created(self, ctx, event: dict) -> None:
        """Record a 'became friends' activity for both participants' feeds."""
        user_a = event.get("user_a_id", "")
        user_b = event.get("user_b_id", "")
        if not user_a or not user_b:
            return
        await self.record_activity(ctx, payload={
            "activity_type": ACTIVITY_FRIENDSHIP_CREATED,
            "actor_id": user_a,
            "about_user_id": user_b,
            "subject_id": event.get("friendship_id"),
            "subject_type": "friendship",
            "subject_label": "made a new connection",
            "feed_user_ids": [user_a, user_b],
            "metadata": {"friendship_id": event.get("friendship_id")},
        })

    async def on_post_published(self, ctx, event: dict) -> None:
        """Record a 'shared a post' activity for the author's feed and their friends."""
        author_id = event.get("author_id", "")
        if not author_id:
            return
        # feed_user_ids will be expanded by the caller if friend-of-friend fanout is needed.
        # Base implementation: author's own timeline only.
        await self.record_activity(ctx, payload={
            "activity_type": ACTIVITY_POST_PUBLISHED,
            "actor_id": author_id,
            "subject_id": event.get("post_id"),
            "subject_type": "post",
            "subject_label": event.get("body_preview") or "shared a post",
            "feed_user_ids": [author_id],
            "metadata": {"post_id": event.get("post_id")},
        })

    async def on_post_commented(self, ctx, event: dict) -> None:
        """Record a 'commented on a post' activity."""
        author_id = event.get("author_id", "")
        post_author_id = event.get("post_author_id", "")
        if not author_id:
            return
        feed_ids = list({author_id, post_author_id} - {""})
        await self.record_activity(ctx, payload={
            "activity_type": ACTIVITY_POST_COMMENTED,
            "actor_id": author_id,
            "about_user_id": post_author_id or None,
            "subject_id": event.get("post_id"),
            "subject_type": "post",
            "subject_label": "commented on a post",
            "feed_user_ids": feed_ids,
            "metadata": {
                "post_id": event.get("post_id"),
                "comment_id": event.get("comment_id"),
            },
        })

"""Business logic for the user_posts module."""
from __future__ import annotations

from uuid import uuid4

from .policy import actor_id, app_scope, can_delete_comment, can_delete_post, published_posts_query
from .repo import CommentRepo, PostRepo, ReactionRepo
from .schemas import (
    DEFAULT_REACTION,
    POST_STATUS_DELETED,
    POST_STATUS_PUBLISHED,
    REACTION_TYPES,
    VISIBILITY_PUBLIC,
    body_preview,
    coerce_limit,
    timestamp_now,
)


class UserPostsService:
    def __init__(self) -> None:
        self._posts = PostRepo()
        self._reactions = ReactionRepo()
        self._comments = CommentRepo()

    # ── Posts ─────────────────────────────────────────────────────────────────

    async def create_post(self, ctx, *, body: str, media_urls: list | None = None, visibility: str | None = None) -> dict:
        body = (body or "").strip()
        if not body:
            return {"success": False, "error": "Post body cannot be empty."}

        user_id = actor_id(ctx)
        now = timestamp_now()
        preview = body_preview(body)
        doc = {
            "post_id": f"pst_{uuid4().hex}",
            "author_id": user_id,
            "body": body,
            "body_preview": preview,
            "media_urls": list(media_urls or []),
            "visibility": visibility or VISIBILITY_PUBLIC,
            "status": POST_STATUS_PUBLISHED,
            "reaction_count": 0,
            "comment_count": 0,
            "created_at": now,
            "updated_at": now,
            **app_scope(ctx),
        }
        await self._posts.create(ctx, doc)

        await ctx.emit("domain.social.post.published", {
            "post_id": doc["post_id"],
            "author_id": user_id,
            "body_preview": preview,
            "visibility": doc["visibility"],
            "created_at": now,
        })

        return {"success": True, "post": {k: v for k, v in doc.items() if k != "app_id"}}

    async def get_post(self, ctx, *, post_id: str) -> dict:
        post = await self._posts.get(ctx, post_id=post_id)
        if not post or post.get("status") == POST_STATUS_DELETED:
            return {"post": None}
        return {"post": post}

    async def delete_post(self, ctx, *, post_id: str) -> dict:
        user_id = actor_id(ctx)
        roles = list(getattr(ctx, "roles", None) or [])
        post = await self._posts.get(ctx, post_id=post_id)

        if not post:
            return {"success": False, "error": "Post not found."}
        if not can_delete_post(post, user_id, roles):
            return {"success": False, "error": "You do not have permission to delete this post."}

        now = timestamp_now()
        await self._posts.soft_delete(ctx, post_id=post_id, deleted_at=now)

        await ctx.emit("domain.social.post.deleted", {
            "post_id": post_id,
            "author_id": post.get("author_id", ""),
            "deleted_by": user_id,
            "deleted_at": now,
        })

        return {"success": True}

    async def list_posts(
        self, ctx, *, author_id: str | None = None, visibility: str | None = None,
        limit: int | None = None, before: str | None = None
    ) -> dict:
        bounded = coerce_limit(limit)
        rows = await self._posts.list(
            ctx,
            query=published_posts_query(ctx, author_id=author_id, visibility=visibility),
            limit=bounded + 1,
            before=before,
        )
        has_more = len(rows) > bounded
        page = rows[:bounded]
        return {
            "posts": page,
            "count": len(page),
            "next_cursor": page[-1]["created_at"] if has_more and page else None,
        }

    async def list_user_posts(self, ctx, *, user_id: str | None = None, limit: int | None = None, before: str | None = None) -> dict:
        target = user_id or actor_id(ctx)
        return await self.list_posts(ctx, author_id=target, limit=limit, before=before)

    # ── Reactions ─────────────────────────────────────────────────────────────

    async def react_to_post(self, ctx, *, post_id: str, reaction_type: str | None = None) -> dict:
        user_id = actor_id(ctx)
        rtype = reaction_type if reaction_type in REACTION_TYPES else DEFAULT_REACTION

        post = await self._posts.get(ctx, post_id=post_id)
        if not post or post.get("status") == POST_STATUS_DELETED:
            return {"success": False, "error": "Post not found.", "action": "none", "reaction_count": 0}

        existing = await self._reactions.get(ctx, post_id=post_id, user_id=user_id)
        now = timestamp_now()

        if existing and existing.get("reaction_type") == rtype:
            # Toggle off
            await self._reactions.delete(ctx, post_id=post_id, user_id=user_id)
            action = "removed"
        else:
            await self._reactions.upsert(ctx, {
                "post_id": post_id,
                "user_id": user_id,
                "reaction_type": rtype,
                "created_at": now,
                **app_scope(ctx),
            })
            action = "added"

        new_count = await self._reactions.count_by_post(ctx, post_id=post_id)
        await self._posts.set_reaction_count(ctx, post_id=post_id, count=new_count)

        await ctx.emit("domain.social.post.reacted", {
            "post_id": post_id,
            "post_author_id": post.get("author_id", ""),
            "user_id": user_id,
            "reaction_type": rtype,
            "action": action,
            "reacted_at": now,
        })

        return {"success": True, "action": action, "reaction_count": new_count}

    async def get_reaction_summary(self, ctx, *, post_id: str) -> dict:
        user_id = actor_id(ctx)
        by_type = await self._reactions.count_by_type(ctx, post_id=post_id)
        total = sum(by_type.values())
        viewer = await self._reactions.get(ctx, post_id=post_id, user_id=user_id)
        return {
            "total": total,
            "by_type": by_type,
            "viewer_reaction": viewer.get("reaction_type") if viewer else None,
        }

    # ── Comments ──────────────────────────────────────────────────────────────

    async def add_comment(self, ctx, *, post_id: str, body: str) -> dict:
        body = (body or "").strip()
        if not body:
            return {"success": False, "error": "Comment body cannot be empty.", "comment": None}

        post = await self._posts.get(ctx, post_id=post_id)
        if not post or post.get("status") == POST_STATUS_DELETED:
            return {"success": False, "error": "Post not found.", "comment": None}

        user_id = actor_id(ctx)
        now = timestamp_now()
        doc = {
            "comment_id": f"cmt_{uuid4().hex}",
            "post_id": post_id,
            "author_id": user_id,
            "body": body,
            "created_at": now,
            **app_scope(ctx),
        }
        await self._comments.create(ctx, doc)
        await self._posts.increment_comment_count(ctx, post_id=post_id)

        await ctx.emit("domain.social.post.commented", {
            "post_id": post_id,
            "post_author_id": post.get("author_id", ""),
            "comment_id": doc["comment_id"],
            "author_id": user_id,
            "body_preview": body_preview(body, 100),
            "created_at": now,
        })

        return {"success": True, "comment": {k: v for k, v in doc.items() if k != "app_id"}}

    async def delete_comment(self, ctx, *, comment_id: str) -> dict:
        user_id = actor_id(ctx)
        roles = list(getattr(ctx, "roles", None) or [])
        comment = await self._comments.get(ctx, comment_id=comment_id)

        if not comment:
            return {"success": False, "error": "Comment not found."}
        if not can_delete_comment(comment, user_id, roles):
            return {"success": False, "error": "You do not have permission to delete this comment."}

        now = timestamp_now()
        await self._comments.delete(ctx, comment_id=comment_id)
        await self._posts.increment_comment_count(ctx, post_id=comment["post_id"], delta=-1)

        await ctx.emit("domain.social.post.comment_deleted", {
            "post_id": comment["post_id"],
            "comment_id": comment_id,
            "deleted_by": user_id,
            "deleted_at": now,
        })

        return {"success": True}

    async def list_comments(self, ctx, *, post_id: str, limit: int | None = None, after: str | None = None) -> dict:
        bounded = coerce_limit(limit, default=50)
        rows = await self._comments.list(ctx, post_id=post_id, limit=bounded + 1, after=after)
        has_more = len(rows) > bounded
        page = rows[:bounded]
        return {
            "comments": page,
            "count": len(page),
            "next_cursor": page[-1]["created_at"] if has_more and page else None,
        }

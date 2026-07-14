"""Thin dispatch handler for the user_posts module."""
from __future__ import annotations

from .service import UserPostsService

_service = UserPostsService()


class UserPostsHandler:
    async def create_post(self, ctx, payload):
        return await _service.create_post(
            ctx,
            body=payload.get("body", ""),
            media_urls=payload.get("media_urls"),
            visibility=payload.get("visibility"),
        )

    async def get_post(self, ctx, payload):
        return await _service.get_post(ctx, post_id=payload.get("post_id", ""))

    async def delete_post(self, ctx, payload):
        return await _service.delete_post(ctx, post_id=payload.get("post_id", ""))

    async def list_posts(self, ctx, payload):
        return await _service.list_posts(
            ctx,
            author_id=payload.get("author_id"),
            visibility=payload.get("visibility"),
            limit=payload.get("limit"),
            before=payload.get("before"),
        )

    async def list_user_posts(self, ctx, payload):
        return await _service.list_user_posts(
            ctx, user_id=payload.get("user_id"), limit=payload.get("limit"), before=payload.get("before")
        )

    async def react_to_post(self, ctx, payload):
        return await _service.react_to_post(
            ctx, post_id=payload.get("post_id", ""), reaction_type=payload.get("reaction_type")
        )

    async def get_reaction_summary(self, ctx, payload):
        return await _service.get_reaction_summary(ctx, post_id=payload.get("post_id", ""))

    async def add_comment(self, ctx, payload):
        return await _service.add_comment(ctx, post_id=payload.get("post_id", ""), body=payload.get("body", ""))

    async def delete_comment(self, ctx, payload):
        return await _service.delete_comment(ctx, comment_id=payload.get("comment_id", ""))

    async def list_comments(self, ctx, payload):
        return await _service.list_comments(
            ctx, post_id=payload.get("post_id", ""), limit=payload.get("limit"), after=payload.get("after")
        )

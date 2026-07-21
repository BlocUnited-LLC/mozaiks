"""Thin dispatch handler for the user_posts module."""
from __future__ import annotations

from .service import UserPostsService

_service = UserPostsService()


class UserPostsHandler:
    async def create_post(self, ctx, body: str = "", media_urls=None, visibility=None, **_params):
        return await _service.create_post(
            ctx,
            body=body,
            media_urls=media_urls,
            visibility=visibility,
        )

    async def get_post(self, ctx, post_id: str = "", **_params):
        return await _service.get_post(ctx, post_id=post_id)

    async def delete_post(self, ctx, post_id: str = "", **_params):
        return await _service.delete_post(ctx, post_id=post_id)

    async def list_posts(self, ctx, author_id=None, visibility=None, limit=None, before=None, **_params):
        return await _service.list_posts(
            ctx,
            author_id=author_id,
            visibility=visibility,
            limit=limit,
            before=before,
        )

    async def list_user_posts(self, ctx, user_id: str | None = None, limit=None, before=None, **_params):
        return await _service.list_user_posts(ctx, user_id=user_id, limit=limit, before=before)

    async def react_to_post(self, ctx, post_id: str = "", reaction_type=None, **_params):
        return await _service.react_to_post(ctx, post_id=post_id, reaction_type=reaction_type)

    async def get_reaction_summary(self, ctx, post_id: str = "", **_params):
        return await _service.get_reaction_summary(ctx, post_id=post_id)

    async def add_comment(self, ctx, post_id: str = "", body: str = "", **_params):
        return await _service.add_comment(ctx, post_id=post_id, body=body)

    async def delete_comment(self, ctx, comment_id: str = "", **_params):
        return await _service.delete_comment(ctx, comment_id=comment_id)

    async def list_comments(self, ctx, post_id: str = "", limit=None, after=None, **_params):
        return await _service.list_comments(ctx, post_id=post_id, limit=limit, after=after)

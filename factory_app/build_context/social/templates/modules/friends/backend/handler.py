"""Thin dispatch handler for the friends module."""
from __future__ import annotations

from .service import FriendsService


class FriendsHandler:
    def __init__(self, service: FriendsService | None = None) -> None:
        self._service = service or FriendsService()

    async def send_friend_request(self, ctx, recipient_id: str = "", **_params):
        return await self._service.send_friend_request(ctx, recipient_id=recipient_id)

    async def accept_friend_request(self, ctx, request_id: str = "", **_params):
        return await self._service.accept_friend_request(ctx, request_id=request_id)

    async def decline_friend_request(self, ctx, request_id: str = "", **_params):
        return await self._service.decline_friend_request(ctx, request_id=request_id)

    async def withdraw_friend_request(self, ctx, request_id: str = "", **_params):
        return await self._service.withdraw_friend_request(ctx, request_id=request_id)

    async def remove_friend(self, ctx, friend_user_id: str = "", **_params):
        return await self._service.remove_friend(ctx, friend_user_id=friend_user_id)

    async def list_friends(self, ctx, limit=None, before=None, **_params):
        return await self._service.list_friends(ctx, limit=limit, before=before)

    async def list_friends_of(self, ctx, user_id: str = "", limit=None, before=None, **_params):
        return await self._service.list_friends_of(ctx, user_id=user_id, limit=limit, before=before)

    async def list_friend_requests(
        self,
        ctx,
        direction: str = "all",
        status: str = "pending",
        limit=None,
        before=None,
        **_params,
    ):
        return await self._service.list_friend_requests(
            ctx,
            direction=direction,
            status=status,
            limit=limit,
            before=before,
        )

    async def get_friendship_status(self, ctx, other_user_id: str = "", **_params):
        return await self._service.get_friendship_status(ctx, other_user_id=other_user_id)

    async def get_friends_count(self, ctx, **_params):
        return await self._service.get_friends_count(ctx)

"""Thin dispatch handler for the friends module."""
from __future__ import annotations

from .service import FriendsService

_service = FriendsService()


class FriendsHandler:
    async def send_friend_request(self, ctx, payload):
        return await _service.send_friend_request(ctx, recipient_id=payload.get("recipient_id", ""))

    async def accept_friend_request(self, ctx, payload):
        return await _service.accept_friend_request(ctx, request_id=payload.get("request_id", ""))

    async def decline_friend_request(self, ctx, payload):
        return await _service.decline_friend_request(ctx, request_id=payload.get("request_id", ""))

    async def withdraw_friend_request(self, ctx, payload):
        return await _service.withdraw_friend_request(ctx, request_id=payload.get("request_id", ""))

    async def remove_friend(self, ctx, payload):
        return await _service.remove_friend(ctx, friend_user_id=payload.get("friend_user_id", ""))

    async def list_friends(self, ctx, payload):
        return await _service.list_friends(ctx, limit=payload.get("limit"), before=payload.get("before"))

    async def list_friends_of(self, ctx, payload):
        return await _service.list_friends_of(
            ctx, user_id=payload.get("user_id", ""), limit=payload.get("limit"), before=payload.get("before")
        )

    async def list_friend_requests(self, ctx, payload):
        return await _service.list_friend_requests(
            ctx,
            direction=payload.get("direction", "all"),
            status=payload.get("status", "pending"),
            limit=payload.get("limit"),
            before=payload.get("before"),
        )

    async def get_friendship_status(self, ctx, payload):
        return await _service.get_friendship_status(ctx, other_user_id=payload.get("other_user_id", ""))

    async def get_friends_count(self, ctx, payload):
        return await _service.get_friends_count(ctx)

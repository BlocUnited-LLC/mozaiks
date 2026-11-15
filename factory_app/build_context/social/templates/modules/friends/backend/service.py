"""Business logic for the friends module."""
from __future__ import annotations

from uuid import uuid4

from .policy import actor_id
from .repo import FriendRequestRepo, FriendshipRepo, friendship_view
from .schemas import (
    REQUEST_STATUS_ACCEPTED,
    REQUEST_STATUS_DECLINED,
    REQUEST_STATUS_PENDING,
    REQUEST_STATUS_WITHDRAWN,
    coerce_limit,
    timestamp_now,
)


class FriendsService:
    def __init__(self) -> None:
        self._requests = FriendRequestRepo()
        self._friendships = FriendshipRepo()

    # ── Friend requests ───────────────────────────────────────────────────────

    async def send_friend_request(self, ctx, *, recipient_id: str) -> dict:
        user_id = actor_id(ctx)

        if user_id == recipient_id:
            return {"success": False, "error": "You cannot send a friend request to yourself."}

        # Already friends?
        existing = await self._friendships.find(ctx, user_id=user_id, friend_user_id=recipient_id)
        if existing:
            return {"success": False, "error": "You are already friends with this user."}

        # Pending request already exists?
        pending = await self._requests.find_pending(ctx, requester_id=user_id, recipient_id=recipient_id)
        if pending:
            return {"success": False, "error": "A pending friend request already exists.", "request": pending}

        now = timestamp_now()
        doc = {
            "request_id": f"req_{uuid4().hex}",
            "requester_id": user_id,
            "requester_name": getattr(ctx, "display_name", None) or user_id,
            "recipient_id": recipient_id,
            "status": REQUEST_STATUS_PENDING,
            "created_at": now,
            "updated_at": now,
        }
        if await self._requests.create(ctx, doc) is None:
            return {"success": False, "error": "A request or friendship already exists for these users."}

        await ctx.emit("domain.social.friend_request.sent", {
            "request_id": doc["request_id"],
            "requester_id": user_id,
            "requester_name": doc["requester_name"],
            "recipient_id": recipient_id,
            "created_at": now,
        })

        return {"success": True, "request": doc}

    async def accept_friend_request(self, ctx, *, request_id: str) -> dict:
        user_id = actor_id(ctx)
        req = await self._requests.get(ctx, request_id=request_id)

        if not req:
            return {"success": False, "error": "Friend request not found."}
        if req.get("recipient_id") != user_id:
            return {"success": False, "error": "You can only accept requests addressed to you."}
        if req.get("status") == REQUEST_STATUS_ACCEPTED:
            return await self._accepted_result(ctx, req)
        if req.get("status") != REQUEST_STATUS_PENDING:
            return {"success": False, "error": f"Request is already {req.get('status')}."}
        now = timestamp_now()
        event_id = str(uuid4())
        transitioned = await self._requests.update_status(ctx, request_id=request_id, status=REQUEST_STATUS_ACCEPTED,
                                                         updated_at=now, event_id=event_id)
        if not transitioned:
            current = await self._requests.get(ctx, request_id=request_id)
            if current and current.get("status") == REQUEST_STATUS_ACCEPTED:
                return await self._accepted_result(ctx, current)
            return {"success": False, "error": "The request changed before it could be accepted."}
        return await self._accepted_result(ctx, {**req, "status": REQUEST_STATUS_ACCEPTED,
                                                "acceptance_event_id": event_id, "accepted_at": now, "updated_at": now, "acceptance_delivery_pending": True})

    async def _accepted_result(self, ctx, request: dict) -> dict:
        from mozaiksai.core.events.unified_event_dispatcher import EventDispatchOutcome
        from mozaiksai.core.runtime.composition.module_event_provenance import ModuleEventRejection

        outcome = await ctx.emit("domain.social.friendship.created", {
            "friendship_id": f"fs_{request['request_id']}",
            "user_a_id": request["requester_id"],
            "user_b_id": request["recipient_id"],
            "requester_id": request["requester_id"],
            "request_id": request["request_id"],
            "created_at": request["accepted_at"],
        }, event_id=request["acceptance_event_id"])
        if isinstance(outcome, ModuleEventRejection) or (isinstance(outcome, EventDispatchOutcome) and not outcome.success):
            raise RuntimeError("Friendship accepted; its notification delivery needs a retry.")
        if isinstance(outcome, EventDispatchOutcome) and outcome.success:
            await self._requests.mark_acceptance_delivered(ctx, request_id=request["request_id"], event_id=request["acceptance_event_id"])
            request = {**request, "acceptance_delivery_pending": False}
        return {"success": True, "friendship": friendship_view(request, actor_id(ctx))}

    async def decline_friend_request(self, ctx, *, request_id: str) -> dict:
        user_id = actor_id(ctx)
        req = await self._requests.get(ctx, request_id=request_id)

        if not req:
            return {"success": False, "error": "Friend request not found."}
        if req.get("recipient_id") != user_id:
            return {"success": False, "error": "You can only decline requests addressed to you."}
        if req.get("status") != REQUEST_STATUS_PENDING:
            return {"success": False, "error": f"Request is already {req.get('status')}."}

        now = timestamp_now()
        if not await self._requests.update_status(ctx, request_id=request_id, status=REQUEST_STATUS_DECLINED, updated_at=now):
            return {"success": False, "error": "The request changed before this action completed."}

        await ctx.emit("domain.social.friend_request.declined", {
            "request_id": request_id,
            "requester_id": req["requester_id"],
            "recipient_id": user_id,
            "declined_at": now,
        })

        return {"success": True}

    async def withdraw_friend_request(self, ctx, *, request_id: str) -> dict:
        user_id = actor_id(ctx)
        req = await self._requests.get(ctx, request_id=request_id)

        if not req:
            return {"success": False, "error": "Friend request not found."}
        if req.get("requester_id") != user_id:
            return {"success": False, "error": "You can only withdraw requests you sent."}
        if req.get("status") != REQUEST_STATUS_PENDING:
            return {"success": False, "error": f"Request is already {req.get('status')}."}

        now = timestamp_now()
        if not await self._requests.update_status(ctx, request_id=request_id, status=REQUEST_STATUS_WITHDRAWN, updated_at=now):
            return {"success": False, "error": "The request changed before this action completed."}

        await ctx.emit("domain.social.friend_request.withdrawn", {
            "request_id": request_id,
            "requester_id": user_id,
            "recipient_id": req["recipient_id"],
            "withdrawn_at": now,
        })

        return {"success": True}

    # ── Friendships ───────────────────────────────────────────────────────────

    async def remove_friend(self, ctx, *, friend_user_id: str) -> dict:
        user_id = actor_id(ctx)

        deleted = await self._friendships.remove(ctx, user_id=user_id, friend_user_id=friend_user_id)
        if not deleted:
            return {"success": False, "error": "Friendship not found."}

        now = timestamp_now()
        await ctx.emit("domain.social.friendship.removed", {
            "member_user_id": user_id,
            "friend_user_id": friend_user_id,
            "removed_at": now,
        })

        return {"success": True}

    async def list_friends(self, ctx, *, limit: int | None = None, before: str | None = None) -> dict:
        user_id = actor_id(ctx)
        bounded = coerce_limit(limit)
        rows = await self._friendships.list(ctx, user_id=user_id, limit=bounded + 1, before=before)
        has_more = len(rows) > bounded
        page = rows[:bounded]
        return {
            "friends": page,
            "count": len(page),
            "next_cursor": page[-1]["created_at"] if has_more and page else None,
        }

    async def list_friends_of(self, ctx, *, user_id: str, limit: int | None = None, before: str | None = None) -> dict:
        bounded = coerce_limit(limit)
        rows = await self._friendships.list(ctx, user_id=user_id, limit=bounded + 1, before=before)
        has_more = len(rows) > bounded
        page = rows[:bounded]
        return {
            "friends": page,
            "count": len(page),
            "next_cursor": page[-1]["created_at"] if has_more and page else None,
        }

    async def list_friend_requests(
        self,
        ctx,
        *,
        direction: str = "all",
        status: str = "pending",
        limit: int | None = None,
        before: str | None = None,
    ) -> dict:
        from .policy import request_query_as_recipient, request_query_as_requester

        user_id = actor_id(ctx)
        bounded = coerce_limit(limit)
        norm_status = None if status == "all" else status

        rows: list = []
        if direction in ("incoming", "all"):
            rows += await self._requests.list(
                ctx,
                query=request_query_as_recipient(ctx, recipient_id=user_id, status=norm_status),
                limit=bounded,
                before=before,
            )
        if direction in ("outgoing", "all"):
            rows += await self._requests.list(
                ctx,
                query=request_query_as_requester(ctx, requester_id=user_id, status=norm_status),
                limit=bounded,
                before=before,
            )

        rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        page = rows[:bounded]
        return {"requests": page, "count": len(page)}

    async def get_friendship_status(self, ctx, *, other_user_id: str) -> dict:
        user_id = actor_id(ctx)

        friendship = await self._friendships.find(ctx, user_id=user_id, friend_user_id=other_user_id)
        if friendship:
            return {"status": "friends", "friendship": friendship}

        sent = await self._requests.find_pending(ctx, requester_id=user_id, recipient_id=other_user_id)
        if sent:
            return {"status": "request_sent", "request": sent}

        received = await self._requests.find_pending(ctx, requester_id=other_user_id, recipient_id=user_id)
        if received:
            return {"status": "request_received", "request": received}

        return {"status": "none"}

    async def get_friends_count(self, ctx) -> dict:
        user_id = actor_id(ctx)
        count = await self._friendships.count(ctx, user_id=user_id)
        return {"count": count}

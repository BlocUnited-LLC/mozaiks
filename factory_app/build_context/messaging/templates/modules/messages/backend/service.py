from __future__ import annotations

from typing import Any
from uuid import uuid4

from .policy import actor_id, require_participant, scoped_thread_query
from .repo import MessageRepo, NotificationRepo, ReadStateRepo, ThreadRepo
from .schemas import (
    MAX_MESSAGE_LENGTH,
    MESSAGE_PREVIEW_LENGTH,
    THREAD_STATUS_CLOSED,
    THREAD_STATUSES,
    THREAD_TYPE_DM,
    THREAD_TYPE_GROUP,
    Message,
    Thread,
    coerce_limit,
    timestamp_now,
)

_EPOCH = "1970-01-01T00:00:00+00:00"


class MessageService:
    def __init__(
        self,
        *,
        threads: ThreadRepo | None = None,
        messages: MessageRepo | None = None,
        reads: ReadStateRepo | None = None,
        notifications: NotificationRepo | None = None,
    ) -> None:
        self._threads = threads or ThreadRepo()
        self._messages = messages or MessageRepo()
        self._reads = reads or ReadStateRepo()
        self._notifications = notifications or NotificationRepo()

    # ------------------------------------------------------------------
    # Thread listing / fetching
    # ------------------------------------------------------------------

    async def list_threads(
        self,
        ctx,
        *,
        status: str | None = None,
        thread_type: str | None = None,
        limit: int = 20,
        before: str | None = None,
    ) -> dict[str, Any]:
        query = scoped_thread_query(ctx)
        if status:
            query["status"] = status
        if thread_type and thread_type in (THREAD_TYPE_DM, THREAD_TYPE_GROUP):
            query["thread_type"] = thread_type
        threads = await self._threads.list(
            ctx,
            query=query,
            limit=coerce_limit(limit),
            before=before or None,
        )
        # Return a next_cursor so the client can page forward
        next_cursor = threads[-1]["updated_at"] if len(threads) == coerce_limit(limit) else None
        return {"threads": threads, "next_cursor": next_cursor}

    async def get_thread(
        self,
        ctx,
        *,
        thread_id: str,
        message_limit: int = 50,
        before: str | None = None,
    ) -> dict[str, Any]:
        thread = await self._threads.get(ctx, thread_id=thread_id)
        if not thread:
            return {"thread": None, "messages": [], "error": "thread not found"}

        user_id = actor_id(ctx)
        if not require_participant(thread, user_id):
            return {"thread": None, "messages": [], "error": "access denied"}

        limit = coerce_limit(message_limit, default=50, maximum=200)
        messages = await self._messages.list(
            ctx, thread_id=thread_id, limit=limit, before=before or None
        )

        # next_cursor = oldest message's created_at if there may be more
        next_cursor = messages[0]["created_at"] if len(messages) == limit else None

        read_state = await self._reads.get(ctx, thread_id=thread_id, user_id=user_id)
        since = read_state["read_at"] if read_state else _EPOCH
        unread = await self._messages.count_unread(
            ctx, thread_id=thread_id, user_id=user_id, since=since
        )

        return {
            "thread": thread,
            "messages": messages,
            "unread_count": unread,
            "next_cursor": next_cursor,
        }

    async def get_unread_summary(self, ctx) -> dict[str, Any]:
        """Return total number of threads that have unread messages.

        Fetches up to 500 threads the user participates in and compares each
        thread's last_message_at against the user's read state. Efficient for
        inboxes up to ~500 threads; beyond that, denormalize a per-user counter.
        """
        user_id = actor_id(ctx)
        threads = await self._threads.list(
            ctx,
            query={
                "participant_ids": user_id,
                "last_message_at": {"$ne": None},
                "status": "open",
            },
            limit=500,
        )
        if not threads:
            return {"unread_thread_count": 0}

        thread_ids = [t["thread_id"] for t in threads]
        read_states = await self._reads.list_for_user(
            ctx, user_id=user_id, thread_ids=thread_ids
        )
        read_map = {rs["thread_id"]: rs["read_at"] for rs in read_states}

        unread = 0
        for thread in threads:
            last_msg_at = thread.get("last_message_at")
            if not last_msg_at:
                continue
            # Don't count threads where I sent the last message
            last_msg = thread.get("last_message") or {}
            if last_msg.get("sender_id") == user_id:
                continue
            read_at = read_map.get(thread["thread_id"])
            if not read_at or last_msg_at > read_at:
                unread += 1

        return {"unread_thread_count": unread}

    # ------------------------------------------------------------------
    # Thread creation
    # ------------------------------------------------------------------

    async def find_or_create_dm(
        self, ctx, *, participant_id: str
    ) -> dict[str, Any]:
        """Find an existing open DM thread between caller and participant_id,
        or create one. Guarantees exactly one DM thread per pair."""
        creator = actor_id(ctx)
        target = str(participant_id or "").strip()
        if not target:
            return {"success": False, "error": "participant_id is required"}
        if target == creator:
            return {"success": False, "error": "cannot start a DM with yourself"}

        # Canonical sorted participant list ensures uniqueness regardless of who initiates
        sorted_participants = sorted([creator, target])
        existing = await self._threads.find_by_participants(
            ctx, participant_ids=sorted_participants, thread_type=THREAD_TYPE_DM
        )
        if existing:
            return {"success": True, "thread": existing, "created": False}

        now = timestamp_now()
        thread: Thread = {
            "thread_id": str(uuid4()),
            "title": "",
            "thread_type": THREAD_TYPE_DM,
            "participant_ids": sorted_participants,
            "context_id": None,
            "status": "open",
            "created_by": creator,
            "created_at": now,
            "updated_at": now,
            "last_message_at": None,
            "last_message": None,
        }
        await self._threads.insert(ctx, doc=thread)
        await ctx.emit(
            "app.messages.thread.created",
            {
                "thread_id": thread["thread_id"],
                "thread_type": THREAD_TYPE_DM,
                "created_by": creator,
            },
        )
        return {"success": True, "thread": thread, "created": True}

    async def create_thread(
        self,
        ctx,
        *,
        title: str,
        participant_ids: list[str] | None = None,
        context_id: str | None = None,
    ) -> dict[str, Any]:
        title = title.strip()[:200]
        if not title:
            return {"success": False, "error": "title is required"}

        creator = actor_id(ctx)
        participants = list(dict.fromkeys([creator, *(participant_ids or [])]))
        now = timestamp_now()
        thread: Thread = {
            "thread_id": str(uuid4()),
            "title": title,
            "thread_type": THREAD_TYPE_GROUP,
            "participant_ids": participants,
            "context_id": context_id,
            "status": "open",
            "created_by": creator,
            "created_at": now,
            "updated_at": now,
            "last_message_at": None,
            "last_message": None,
        }
        await self._threads.insert(ctx, doc=thread)
        await ctx.emit(
            "app.messages.thread.created",
            {
                "thread_id": thread["thread_id"],
                "thread_type": THREAD_TYPE_GROUP,
                "created_by": creator,
            },
        )
        return {"success": True, "thread": thread}

    # ------------------------------------------------------------------
    # Thread management
    # ------------------------------------------------------------------

    async def update_thread_status(
        self, ctx, *, thread_id: str, status: str
    ) -> dict[str, Any]:
        if status not in THREAD_STATUSES:
            return {"success": False, "error": f"invalid status: {status!r}"}

        thread = await self._threads.get(ctx, thread_id=thread_id)
        if not thread:
            return {"success": False, "error": "thread not found"}

        user_id = actor_id(ctx)
        if not require_participant(thread, user_id):
            return {"success": False, "error": "access denied"}

        # Only the thread creator can close/archive; any participant can re-open
        if status != "open" and thread.get("created_by") != user_id:
            return {"success": False, "error": "only the thread creator can close or archive"}

        now = timestamp_now()
        matched = await self._threads.update_status(
            ctx, thread_id=thread_id, status=status, now=now
        )
        if not matched:
            return {"success": False, "error": "thread not found"}

        await ctx.emit(
            "app.messages.thread.status_updated",
            {"thread_id": thread_id, "status": status, "updated_by": user_id},
        )
        return {"success": True}

    async def leave_thread(self, ctx, *, thread_id: str) -> dict[str, Any]:
        """Remove the caller from a thread's participant list.

        If the thread becomes empty after the user leaves it is automatically
        closed so it no longer appears in any inbox.
        """
        thread = await self._threads.get(ctx, thread_id=thread_id)
        if not thread:
            return {"success": False, "error": "thread not found"}

        user_id = actor_id(ctx)
        if not require_participant(thread, user_id):
            return {"success": False, "error": "not a participant"}

        now = timestamp_now()
        await self._threads.remove_participant(ctx, thread_id=thread_id, user_id=user_id, now=now)
        await self._reads.delete_for_user(ctx, thread_id=thread_id, user_id=user_id)

        remaining = await self._threads.count_participants(ctx, thread_id=thread_id)
        if remaining == 0:
            await self._threads.update_status(
                ctx, thread_id=thread_id, status=THREAD_STATUS_CLOSED, now=now
            )

        await ctx.emit(
            "app.messages.thread.left",
            {"thread_id": thread_id, "user_id": user_id},
        )
        return {"success": True}

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    async def send_message(
        self,
        ctx,
        *,
        thread_id: str,
        body: str,
        message_type: str = "text",
    ) -> dict[str, Any]:
        body = body.strip()
        if not body:
            return {"success": False, "error": "body is required"}
        if len(body) > MAX_MESSAGE_LENGTH:
            return {
                "success": False,
                "error": f"message exceeds {MAX_MESSAGE_LENGTH} character limit",
            }

        thread = await self._threads.get(ctx, thread_id=thread_id)
        if not thread:
            return {"success": False, "error": "thread not found"}
        if thread.get("status") != "open":
            return {"success": False, "error": "thread is closed"}

        sender_id = actor_id(ctx)
        if not require_participant(thread, sender_id):
            return {"success": False, "error": "access denied"}

        now = timestamp_now()
        message_id = str(uuid4())
        message: Message = {
            "message_id": message_id,
            "thread_id": thread_id,
            "sender_id": sender_id,
            "body": body,
            "message_type": message_type,
            "created_at": now,
            "edited_at": None,
            "is_deleted": False,
        }
        await self._messages.insert(ctx, doc=message)

        preview = {
            "message_id": message_id,
            "sender_id": sender_id,
            "body_preview": body[:MESSAGE_PREVIEW_LENGTH],
            "sent_at": now,
        }
        await self._threads.update_last_message(
            ctx, thread_id=thread_id, now=now, preview=preview
        )

        participant_ids = list(thread["participant_ids"])
        recipient_ids = [uid for uid in participant_ids if uid != sender_id]
        await ctx.emit(
            "app.messages.message.sent",
            {
                "thread_id": thread_id,
                "message_id": message_id,
                "sender_id": sender_id,
                "body_preview": body[:MESSAGE_PREVIEW_LENGTH],
                "sent_at": now,
                "recipient_ids": recipient_ids,
                "participant_ids": participant_ids,
            },
        )
        return {"success": True, "message": message}

    async def edit_message(
        self, ctx, *, thread_id: str, message_id: str, body: str
    ) -> dict[str, Any]:
        body = body.strip()
        if not body:
            return {"success": False, "error": "body is required"}
        if len(body) > MAX_MESSAGE_LENGTH:
            return {
                "success": False,
                "error": f"message exceeds {MAX_MESSAGE_LENGTH} character limit",
            }

        thread = await self._threads.get(ctx, thread_id=thread_id)
        if not thread:
            return {"success": False, "error": "thread not found"}

        user_id = actor_id(ctx)
        if not require_participant(thread, user_id):
            return {"success": False, "error": "access denied"}

        message = await self._messages.get_by_id(
            ctx, thread_id=thread_id, message_id=message_id
        )
        if not message:
            return {"success": False, "error": "message not found"}
        if message.get("is_deleted"):
            return {"success": False, "error": "cannot edit a deleted message"}
        if message.get("sender_id") != user_id:
            return {"success": False, "error": "only the sender may edit a message"}
        if message.get("message_type") == "system":
            return {"success": False, "error": "system messages cannot be edited"}

        now = timestamp_now()
        await self._messages.update(
            ctx,
            thread_id=thread_id,
            message_id=message_id,
            updates={"body": body, "edited_at": now},
        )
        updated = dict(message)
        updated["body"] = body
        updated["edited_at"] = now
        await ctx.emit(
            "app.messages.message.edited",
            {
                "thread_id": thread_id,
                "message_id": message_id,
                "sender_id": user_id,
                "edited_at": now,
            },
        )
        return {"success": True, "message": updated}

    async def delete_message(
        self, ctx, *, thread_id: str, message_id: str
    ) -> dict[str, Any]:
        thread = await self._threads.get(ctx, thread_id=thread_id)
        if not thread:
            return {"success": False, "error": "thread not found"}

        user_id = actor_id(ctx)
        if not require_participant(thread, user_id):
            return {"success": False, "error": "access denied"}

        message = await self._messages.get_by_id(
            ctx, thread_id=thread_id, message_id=message_id
        )
        if not message:
            return {"success": False, "error": "message not found"}
        if message.get("is_deleted"):
            return {"success": False, "error": "message already deleted"}
        # Only sender may delete; thread creators may also delete any message in their thread
        is_sender = message.get("sender_id") == user_id
        is_thread_creator = thread.get("created_by") == user_id
        if not (is_sender or is_thread_creator):
            return {"success": False, "error": "only the sender or thread creator may delete a message"}

        now = timestamp_now()
        await self._messages.update(
            ctx,
            thread_id=thread_id,
            message_id=message_id,
            updates={"is_deleted": True, "body": "", "edited_at": now},
        )
        await ctx.emit(
            "app.messages.message.deleted",
            {
                "thread_id": thread_id,
                "message_id": message_id,
                "deleted_by": user_id,
                "deleted_at": now,
            },
        )
        return {"success": True}

    async def mark_thread_read(self, ctx, *, thread_id: str) -> dict[str, Any]:
        user_id = actor_id(ctx)
        thread = await self._threads.get(ctx, thread_id=thread_id)
        if not thread or not require_participant(thread, user_id):
            return {"success": False, "error": "thread not found"}

        latest = await self._messages.get_latest(ctx, thread_id=thread_id)
        last_id = latest["message_id"] if latest else None

        now = timestamp_now()
        await self._reads.upsert(
            ctx, thread_id=thread_id, user_id=user_id, message_id=last_id, now=now
        )
        await ctx.emit(
            "app.messages.thread.read",
            {"thread_id": thread_id, "user_id": user_id},
        )
        return {"success": True}

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    async def list_notifications(
        self, ctx, *, limit: int = 20
    ) -> dict[str, Any]:
        user_id = actor_id(ctx)
        notifications = await self._notifications.list(
            ctx, user_id=user_id, limit=coerce_limit(limit)
        )
        unread_count = await self._notifications.count_unread(ctx, user_id=user_id)
        return {"notifications": notifications, "unread_count": unread_count}

    async def mark_notification_read(
        self, ctx, *, notification_id: str
    ) -> dict[str, Any]:
        user_id = actor_id(ctx)
        matched = await self._notifications.mark_read(
            ctx, notification_id=notification_id, user_id=user_id
        )
        if not matched:
            return {"success": False, "error": "notification not found"}
        return {"success": True}

    async def mark_all_notifications_read(self, ctx) -> dict[str, Any]:
        user_id = actor_id(ctx)
        await self._notifications.mark_all_read(ctx, user_id=user_id)
        return {"success": True}

from __future__ import annotations

from typing import Any

from .service import MessageService


class MessageHandler:
    def __init__(self) -> None:
        self.service: MessageService = MessageService()

    async def list_threads(
        self,
        ctx,
        *,
        status: str | None = None,
        thread_type: str | None = None,
        limit: int = 20,
        before: str | None = None,
    ) -> dict[str, Any]:
        return await self.service.list_threads(
            ctx, status=status, thread_type=thread_type, limit=limit, before=before
        )

    async def get_thread(
        self,
        ctx,
        *,
        thread_id: str,
        message_limit: int = 50,
        before: str | None = None,
    ) -> dict[str, Any]:
        return await self.service.get_thread(
            ctx, thread_id=thread_id, message_limit=message_limit, before=before
        )

    async def get_unread_summary(self, ctx) -> dict[str, Any]:
        return await self.service.get_unread_summary(ctx)

    async def find_or_create_dm(
        self, ctx, *, participant_id: str
    ) -> dict[str, Any]:
        return await self.service.find_or_create_dm(ctx, participant_id=participant_id)

    async def create_thread(
        self,
        ctx,
        *,
        title: str,
        participant_ids: list[str] | None = None,
        context_id: str | None = None,
    ) -> dict[str, Any]:
        return await self.service.create_thread(
            ctx,
            title=title,
            participant_ids=participant_ids,
            context_id=context_id,
        )

    async def send_message(
        self, ctx, *, thread_id: str, body: str, message_type: str = "text"
    ) -> dict[str, Any]:
        return await self.service.send_message(
            ctx, thread_id=thread_id, body=body, message_type=message_type
        )

    async def edit_message(
        self, ctx, *, thread_id: str, message_id: str, body: str
    ) -> dict[str, Any]:
        return await self.service.edit_message(
            ctx, thread_id=thread_id, message_id=message_id, body=body
        )

    async def delete_message(
        self, ctx, *, thread_id: str, message_id: str
    ) -> dict[str, Any]:
        return await self.service.delete_message(
            ctx, thread_id=thread_id, message_id=message_id
        )

    async def mark_thread_read(self, ctx, *, thread_id: str) -> dict[str, Any]:
        return await self.service.mark_thread_read(ctx, thread_id=thread_id)

    async def update_thread_status(
        self, ctx, *, thread_id: str, status: str
    ) -> dict[str, Any]:
        return await self.service.update_thread_status(
            ctx, thread_id=thread_id, status=status
        )

    async def leave_thread(self, ctx, *, thread_id: str) -> dict[str, Any]:
        return await self.service.leave_thread(ctx, thread_id=thread_id)

    async def list_notifications(self, ctx, *, limit: int = 20) -> dict[str, Any]:
        return await self.service.list_notifications(ctx, limit=limit)

    async def mark_notification_read(
        self, ctx, *, notification_id: str
    ) -> dict[str, Any]:
        return await self.service.mark_notification_read(
            ctx, notification_id=notification_id
        )

    async def mark_all_notifications_read(self, ctx) -> dict[str, Any]:
        return await self.service.mark_all_notifications_read(ctx)

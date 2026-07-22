from __future__ import annotations

from typing import Any

from mozaiksai.core.runtime.composition.module_context import ModuleContext

from .service import MessageService


class MessagesHandler:
    def __init__(self, service: MessageService | None = None) -> None:
        self._service = service or MessageService()

    async def create_thread(
        self,
        ctx: ModuleContext,
        *,
        title: str | None = None,
        participant_ids: list[str] | None = None,
        thread_type: str = "group",
        scope_type: str = "app",
        scope_id: str | None = None,
        subject_app_id: str | None = None,
        related_type: str | None = None,
        related_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        **_: object,
    ) -> dict[str, Any]:
        return await self._service.create_thread(
            ctx,
            title=title,
            participant_ids=participant_ids,
            thread_type=thread_type,
            scope_type=scope_type,
            scope_id=scope_id,
            subject_app_id=subject_app_id,
            related_type=related_type,
            related_id=related_id,
            metadata=metadata,
        )

    async def list_threads(
        self,
        ctx: ModuleContext,
        *,
        status: str | None = None,
        thread_type: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        subject_app_id: str | None = None,
        related_type: str | None = None,
        related_id: str | None = None,
        limit: int = 50,
        **_: object,
    ) -> dict[str, Any]:
        return await self._service.list_threads(
            ctx,
            status=status,
            thread_type=thread_type,
            scope_type=scope_type,
            scope_id=scope_id,
            subject_app_id=subject_app_id,
            related_type=related_type,
            related_id=related_id,
            limit=limit,
        )

    async def get_thread(
        self,
        ctx: ModuleContext,
        *,
        thread_id: str,
        message_limit: int = 50,
        **_: object,
    ) -> dict[str, Any]:
        return await self._service.get_thread(ctx, thread_id=thread_id, message_limit=message_limit)

    async def send_message(
        self,
        ctx: ModuleContext,
        *,
        thread_id: str,
        body: str,
        message_type: str = "text",
        **_: object,
    ) -> dict[str, Any]:
        return await self._service.send_message(ctx, thread_id=thread_id, body=body, message_type=message_type)

    async def mark_thread_read(
        self,
        ctx: ModuleContext,
        *,
        thread_id: str,
        **_: object,
    ) -> dict[str, Any]:
        return await self._service.mark_thread_read(ctx, thread_id=thread_id)

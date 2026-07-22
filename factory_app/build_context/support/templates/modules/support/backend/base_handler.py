"""
OSS-generated base handler for the support module.
DO NOT EDIT — regenerated on framework upgrades.
Put app-specific customizations in handler.py (the workspace subclass).
"""
from __future__ import annotations

from mozaiksai.core.runtime.composition.module_context import ModuleContext

from .service import SupportService


class SupportBaseHandler:
    def __init__(self, service: SupportService | None = None) -> None:
        self._service = service or SupportService()

    async def create_support_request(
        self,
        ctx: ModuleContext,
        *,
        message: str,
        subject: str | None = None,
        page_url: str | None = None,
        page_title: str | None = None,
        severity: str = "low",
        app_id: str | None = None,
        message_thread_id: str | None = None,
        **_: object,
    ) -> dict:
        return await self._service.create_support_request(
            ctx,
            message=message,
            subject=subject,
            page_url=page_url,
            page_title=page_title,
            severity=severity,
            app_id=app_id,
            message_thread_id=message_thread_id,
        )

    async def list_support_requests(
        self,
        ctx: ModuleContext,
        *,
        status: str = "open",
        scope: str = "user",
        app_id: str | None = None,
        limit: int = 50,
        **_: object,
    ) -> dict:
        return await self._service.list_support_requests(
            ctx,
            status=status,
            scope=scope,
            app_id=app_id,
            limit=limit,
        )

    async def link_message_thread(
        self,
        ctx: ModuleContext,
        *,
        request_id: str,
        message_thread_id: str,
        **_: object,
    ) -> dict:
        return await self._service.link_message_thread(
            ctx,
            request_id=request_id,
            message_thread_id=message_thread_id,
        )

    async def update_support_status(
        self,
        ctx: ModuleContext,
        *,
        request_id: str,
        status: str,
        assignee_id: str | None = None,
        **_: object,
    ) -> dict:
        return await self._service.update_support_status(
            ctx,
            request_id=request_id,
            status=status,
            assignee_id=assignee_id,
        )

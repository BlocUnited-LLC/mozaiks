from __future__ import annotations

from mozaiksai.core.runtime.composition.module_context import ModuleContext

from .service import WorkspaceSupportService


class WorkspaceSupportModule:
    def __init__(self, service: WorkspaceSupportService | None = None) -> None:
        self.service = service or WorkspaceSupportService()

    async def create_support_request(
        self,
        ctx: ModuleContext,
        *,
        message: str,
        page_url: str | None = None,
        page_title: str | None = None,
        severity: str = "low",
        app_id: str | None = None,
        conversation_transcript: list[dict] | None = None,
        **_: object,
    ) -> dict:
        return await self.service.create_support_request(
            ctx,
            message=message,
            page_url=page_url,
            page_title=page_title,
            severity=severity,
            app_id=app_id,
            conversation_transcript=conversation_transcript,
        )

    async def list_support_requests(
        self,
        ctx: ModuleContext,
        *,
        status: str = "open",
        limit: int = 50,
        scope: str = "user",
        app_id: str | None = None,
        **_: object,
    ) -> dict:
        return await self.service.list_support_requests(
            ctx,
            status=status,
            limit=limit,
            scope=scope,
            app_id=app_id,
        )

    async def submit_session_feedback(
        self,
        ctx: ModuleContext,
        *,
        session_id: str | None = None,
        workflow_name: str | None = None,
        rating: int = 1,
        app_id: str | None = None,
        **_: object,
    ) -> dict:
        return await self.service.submit_session_feedback(
            ctx,
            session_id=session_id,
            workflow_name=workflow_name,
            rating=rating,
            app_id=app_id,
        )

    async def add_support_message(
        self,
        ctx: ModuleContext,
        *,
        request_id: str,
        message: str,
        sender_role: str = "user",
        **_: object,
    ) -> dict:
        return await self.service.add_support_message(
            ctx,
            request_id=request_id,
            message=message,
            sender_role=sender_role,
        )

    async def delete_support_request(
        self,
        ctx: ModuleContext,
        *,
        request_id: str,
        **_: object,
    ) -> dict:
        return await self.service.delete_support_request(ctx, request_id=request_id)

    async def update_support_request_status(
        self,
        ctx: ModuleContext,
        *,
        request_id: str,
        status: str,
        **_: object,
    ) -> dict:
        return await self.service.update_support_request_status(
            ctx,
            request_id=request_id,
            status=status,
        )

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
        **_: object,
    ) -> dict:
        return await self.service.create_support_request(
            ctx,
            message=message,
            page_url=page_url,
            page_title=page_title,
            severity=severity,
        )

    async def list_support_requests(
        self,
        ctx: ModuleContext,
        *,
        status: str = "open",
        limit: int = 50,
        **_: object,
    ) -> dict:
        return await self.service.list_support_requests(ctx, status=status, limit=limit)

    async def submit_session_feedback(
        self,
        ctx: ModuleContext,
        *,
        session_id: str | None = None,
        workflow_name: str | None = None,
        rating: int = 1,
        **_: object,
    ) -> dict:
        return await self.service.submit_session_feedback(
            ctx,
            session_id=session_id,
            workflow_name=workflow_name,
            rating=rating,
        )

from __future__ import annotations

from mozaiksai.core.runtime.composition.module_context import ModuleContext

from .service import WorkspaceIntegrationsService


class WorkspaceIntegrationsModule:
    def __init__(self, service: WorkspaceIntegrationsService | None = None) -> None:
        self.service = service or WorkspaceIntegrationsService()

    async def list_integrations(
        self,
        ctx: ModuleContext,
        *,
        category: str | None = None,
        **_: object,
    ) -> dict:
        return await self.service.list_integrations(ctx, category=category)

    async def get_integration(
        self,
        ctx: ModuleContext,
        *,
        integration_id: str,
        **_: object,
    ) -> dict:
        return await self.service.get_integration(ctx, integration_id=integration_id)

    async def set_integration_note(
        self,
        ctx: ModuleContext,
        *,
        integration_id: str,
        note: str,
        **_: object,
    ) -> dict:
        result = await self.service.set_integration_note(
            ctx,
            integration_id=integration_id,
            note=note,
            user_id=ctx.user_id or "system",
        )
        await ctx.emit(
            "domain.workspace_integrations.note_updated",
            {
                "integration_id": integration_id,
                "note": note,
                "updated_by": ctx.user_id or "system",
            },
        )
        return result

    async def declare_app_integration_needs(
        self,
        ctx: ModuleContext,
        *,
        app_id: str,
        needs: list,
        declared_at: str | None = None,
        **_: object,
    ) -> dict:
        from datetime import UTC, datetime
        result = await self.service.declare_app_integration_needs(
            app_id=app_id,
            needs=needs,
            declared_at=declared_at or datetime.now(UTC).isoformat(),
        )
        if result.get("saved", 0) > 0:
            await ctx.emit(
                "domain.workspace_integrations.declarations_saved",
                {"app_id": app_id, "count": result["saved"]},
            )
        return result

    async def list_app_integration_needs(
        self,
        ctx: ModuleContext,
        **_: object,
    ) -> dict:
        return await self.service.list_app_integration_needs(app_id=ctx.app_id)

    async def save_workspace_connector(
        self,
        ctx: ModuleContext,
        *,
        workspace_id: str,
        service: str,
        secret_value: str,
        display_name: str | None = None,
        public_config: dict | None = None,
        required_fields: list | None = None,
        ttl_days: int = 30,
        **_: object,
    ) -> dict:
        return await self.service.save_workspace_connector(
            workspace_id=workspace_id,
            service=service,
            secret_value=secret_value,
            display_name=display_name,
            user_id=ctx.user_id,
            public_config=public_config,
            required_fields=required_fields,
            ttl_days=ttl_days,
        )

    async def list_workspace_connectors(
        self,
        ctx: ModuleContext,
        *,
        workspace_id: str,
        **_: object,
    ) -> dict:
        return await self.service.list_workspace_connectors(workspace_id=workspace_id)

    async def delete_workspace_connector(
        self,
        ctx: ModuleContext,
        *,
        workspace_id: str,
        service: str,
        **_: object,
    ) -> dict:
        return await self.service.delete_workspace_connector(workspace_id=workspace_id, service=service)

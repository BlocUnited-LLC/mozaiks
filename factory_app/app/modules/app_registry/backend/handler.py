from __future__ import annotations

from mozaiksai.core.runtime.composition.module_context import ModuleContext

from .service import AppRegistryService


class AppRegistryModule:
    def __init__(self, service: AppRegistryService | None = None) -> None:
        self.service = service or AppRegistryService()

    async def create_app_record(
        self,
        ctx: ModuleContext,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> dict:
        return await self.service.create_app_record(
            owner_user_id=ctx.user_id or "", name=name, description=description,
            chat_app_id=ctx.app_id,
        )

    async def list_apps(self, ctx: ModuleContext) -> dict:
        return await self.service.list_apps(owner_user_id=ctx.user_id or "")

    async def get_app_record(
        self,
        ctx: ModuleContext,
        *,
        app_id: str | None = None,
        build_registry_id: str | None = None,
    ) -> dict:
        return await self.service.get_app_record(
            owner_user_id=ctx.user_id or "",
            app_id=app_id or ctx.app_id,
            build_registry_id=build_registry_id,
        )

    async def delete_app(
        self,
        ctx: ModuleContext,
        *,
        build_registry_id: str,
    ) -> dict:
        result = await self.service.delete_app(build_registry_id=build_registry_id, owner_user_id=ctx.user_id or "")
        if result.get("success"):
            await ctx.emit(
                "domain.app_registry.app_deleted",
                {"build_registry_id": build_registry_id},
            )
        return result

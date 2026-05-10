from __future__ import annotations

from typing import Optional

from mozaiksai.core.runtime.composition.module_context import ModuleContext

from .service import AppRegistryService


class AppRegistryModule:
    def __init__(self, service: Optional[AppRegistryService] = None) -> None:
        self.service = service or AppRegistryService()

    async def create_app_record(
        self,
        ctx: ModuleContext,
        *,
        name: str,
        description: Optional[str] = None,
        status: str = "draft",
        app_id: Optional[str] = None,
    ) -> dict:
        result = await self.service.create_app_record(
            owner_user_id=ctx.user_id or "anonymous",
            name=name,
            description=description,
            status=status,
            app_id=app_id or ctx.app_id,
        )
        app = result.get("app") or {}
        await ctx.emit(
            "domain.app_registry.app_created",
            {
                "build_registry_id": app.get("build_registry_id"),
                "app_id": app.get("app_id"),
                "lifecycle_state": app.get("lifecycle_state"),
            },
        )
        return result

    async def update_build_status(
        self,
        ctx: ModuleContext,
        *,
        build_registry_id: str,
        status: str,
        bundle_path: Optional[str] = None,
    ) -> dict:
        result = await self.service.update_build_status(
            build_registry_id=build_registry_id,
            status=status,
            bundle_path=bundle_path,
        )
        app = result.get("app") or {}
        if app:
            await ctx.emit(
                "domain.app_registry.status_changed",
                {
                    "build_registry_id": app.get("build_registry_id"),
                    "app_id": app.get("app_id"),
                    "lifecycle_state": app.get("lifecycle_state"),
                },
            )
        return result

    async def list_apps(self, ctx: ModuleContext) -> dict:
        return await self.service.list_apps(owner_user_id=ctx.user_id or "anonymous")

    async def get_app_record(
        self,
        ctx: ModuleContext,
        *,
        app_id: Optional[str] = None,
        build_registry_id: Optional[str] = None,
    ) -> dict:
        return await self.service.get_app_record(
            app_id=app_id or ctx.app_id,
            build_registry_id=build_registry_id,
        )

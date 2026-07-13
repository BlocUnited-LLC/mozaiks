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
        status: str = "draft",
        app_id: str | None = None,
        chat_app_id: str | None = None,
        active_chat_id: str | None = None,
        active_workflow_id: str | None = None,
        name_source: str | None = None,
        build_context_profile: dict[str, object] | None = None,
        current_build_run: dict[str, object] | None = None,
    ) -> dict:
        result = await self.service.create_app_record(
            owner_user_id=ctx.user_id or "anonymous",
            name=name,
            description=description,
            status=status,
            app_id=app_id or ctx.app_id,
            chat_app_id=chat_app_id,
            active_chat_id=active_chat_id,
            active_workflow_id=active_workflow_id,
            name_source=name_source,
            build_context_profile=build_context_profile,
            current_build_run=current_build_run,
        )
        app = result.get("app") or {}
        await ctx.emit(
            "domain.app_registry.app_created",
            {
                "build_registry_id": app.get("build_registry_id"),
                "app_id": app.get("app_id"),
                "chat_app_id": app.get("chat_app_id"),
                "lifecycle_state": app.get("lifecycle_state"),
                "current_build_run": app.get("current_build_run"),
            },
        )
        return result

    async def update_build_status(
        self,
        ctx: ModuleContext,
        *,
        build_registry_id: str,
        status: str,
        bundle_path: str | None = None,
        artifact_version_id: str | None = None,
        workflow_sequence: str | None = None,
        active_chat_id: str | None = None,
        active_workflow_id: str | None = None,
        current_build_run: dict[str, object] | None = None,
    ) -> dict:
        result = await self.service.update_build_status(
            build_registry_id=build_registry_id,
            status=status,
            bundle_path=bundle_path,
            artifact_version_id=artifact_version_id,
            workflow_sequence=workflow_sequence,
            active_chat_id=active_chat_id,
            active_workflow_id=active_workflow_id,
            current_build_run=current_build_run,
        )
        app = result.get("app") or {}
        if app:
            await ctx.emit(
                "domain.app_registry.status_changed",
                {
                    "build_registry_id": app.get("build_registry_id"),
                    "app_id": app.get("app_id"),
                    "lifecycle_state": app.get("lifecycle_state"),
                    "current_build_run": app.get("current_build_run"),
                },
            )
        return result

    async def list_apps(self, ctx: ModuleContext) -> dict:
        return await self.service.list_apps(owner_user_id=ctx.user_id or "anonymous")

    async def get_app_record(
        self,
        ctx: ModuleContext,
        *,
        app_id: str | None = None,
        build_registry_id: str | None = None,
    ) -> dict:
        return await self.service.get_app_record(
            app_id=app_id or ctx.app_id,
            build_registry_id=build_registry_id,
        )

    async def delete_app(
        self,
        ctx: ModuleContext,
        *,
        build_registry_id: str,
    ) -> dict:
        result = await self.service.delete_app(build_registry_id=build_registry_id)
        if result.get("success"):
            await ctx.emit(
                "domain.app_registry.app_deleted",
                {"build_registry_id": build_registry_id},
            )
        return result

    async def promote_build(
        self,
        ctx: ModuleContext,
        *,
        build_registry_id: str,
    ) -> dict:
        result = await self.service.promote_build(
            build_registry_id=build_registry_id,
            promoted_by=ctx.user_id or "anonymous",
        )
        app = result.get("app") or {}
        if app:
            await ctx.emit(
                "domain.app_registry.app_promoted",
                {
                    "build_registry_id": app.get("build_registry_id"),
                    "app_id": app.get("app_id"),
                    "lifecycle_state": app.get("lifecycle_state"),
                    "bundle_path": app.get("bundle_path"),
                },
            )
        return result

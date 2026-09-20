from __future__ import annotations

from typing import Any

from mozaiksai.core.runtime.composition.module_context import ModuleContext

from .service import SecurityReadinessService


class SecurityReadinessModule:
    def __init__(self, service: SecurityReadinessService | None = None) -> None:
        self.service = service or SecurityReadinessService()

    async def record_assessment(
        self,
        ctx: ModuleContext,
        *,
        app_id: str,
        findings: list[dict[str, Any]],
        build_id: str | None = None,
        build_registry_id: str | None = None,
        artifact_version_id: str | None = None,
        source: str = "manual",
        assessed_at: str | None = None,
        **_: object,
    ) -> dict[str, Any]:
        return await self.service.record_assessment(
            ctx,
            app_id=app_id,
            findings=findings,
            build_id=build_id,
            build_registry_id=build_registry_id,
            artifact_version_id=artifact_version_id,
            source=source,
            assessed_at=assessed_at,
        )

    async def list_findings(
        self,
        ctx: ModuleContext,
        *,
        app_id: str,
        build_registry_id: str | None = None,
        status: str | None = None,
        severity: str | None = None,
        control_area: str | None = None,
        limit: int = 100,
        **_: object,
    ) -> dict[str, Any]:
        return await self.service.list_findings(
            ctx,
            app_id=app_id,
            build_registry_id=build_registry_id,
            status=status,
            severity=severity,
            control_area=control_area,
            limit=limit,
        )

    async def get_summary(
        self,
        ctx: ModuleContext,
        *,
        app_id: str,
        build_registry_id: str | None = None,
        **_: object,
    ) -> dict[str, Any]:
        return await self.service.get_summary(
            ctx, app_id=app_id, build_registry_id=build_registry_id,
        )

    async def update_finding_status(
        self,
        ctx: ModuleContext,
        *,
        finding_id: str,
        status: str,
        note: str | None = None,
        **_: object,
    ) -> dict[str, Any]:
        return await self.service.update_finding_status(
            ctx,
            finding_id=finding_id,
            status=status,
            note=note,
        )

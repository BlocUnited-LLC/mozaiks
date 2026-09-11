from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .policy import actor_id, app_findings_query, finding_query
from .repo import SecurityReadinessRepo
from .schemas import (
    CONTROL_AREAS,
    SEVERITIES,
    STATUSES,
    build_finding_document,
    coerce_limit,
    normalize_enum,
    normalize_optional_text,
    public_finding,
    summarize_findings,
    timestamp_now,
)

if TYPE_CHECKING:
    from mozaiksai.core.runtime.composition.module_context import ModuleContext


class SecurityReadinessService:
    def __init__(self, repo: SecurityReadinessRepo | None = None) -> None:
        self.repo = repo or SecurityReadinessRepo()

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
    ) -> dict[str, Any]:
        normalized_app_id = normalize_optional_text(app_id)
        if not normalized_app_id:
            raise ValueError("app_id is required")
        if not isinstance(findings, list):
            raise ValueError("findings must be a list")
        now = timestamp_now()
        assessed = normalize_optional_text(assessed_at) or now
        normalized_source = normalize_enum(source, ("manual", "validation", "eval", "import"), default="manual", field_name="source")
        documents: list[dict[str, Any]] = [
            dict(
                build_finding_document(
                    app_id=normalized_app_id,
                    owner_user_id=actor_id(ctx),
                    finding=finding,
                    source=normalized_source,
                    assessed_at=assessed,
                    now=now,
                    build_id=build_id,
                    build_registry_id=build_registry_id,
                    artifact_version_id=artifact_version_id,
                )
            )
            for finding in findings
            if isinstance(finding, dict)
        ]
        saved = await self.repo.insert_findings(ctx, documents)
        all_findings = await self.repo.list_findings(
            ctx,
            query=app_findings_query(
                ctx, app_id=normalized_app_id,
                build_registry_id=normalize_optional_text(build_registry_id),
            ),
            limit=250,
        )
        summary = summarize_findings(all_findings)
        await ctx.emit(
            "domain.security_readiness.assessment_recorded",
            {
                "app_id": normalized_app_id,
                **({"build_id": build_id} if build_id else {}),
                **({"artifact_version_id": artifact_version_id} if artifact_version_id else {}),
                "saved": len(saved),
            },
        )
        return {"success": True, "saved": len(saved), "app_id": normalized_app_id, "summary": summary}

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
    ) -> dict[str, Any]:
        normalized_app_id = normalize_optional_text(app_id)
        if not normalized_app_id:
            raise ValueError("app_id is required")
        query = app_findings_query(
            ctx,
            app_id=normalized_app_id,
            build_registry_id=normalize_optional_text(build_registry_id),
            status=normalize_enum(status, STATUSES, default="open", field_name="status") if status else None,
            severity=normalize_enum(severity, SEVERITIES, default="medium", field_name="severity") if severity else None,
            control_area=normalize_enum(
                control_area,
                CONTROL_AREAS,
                default="compliance_readiness",
                field_name="control_area",
            ) if control_area else None,
        )
        findings = await self.repo.list_findings(ctx, query=query, limit=coerce_limit(limit))
        public = [public_finding(item) for item in findings]
        return {"app_id": normalized_app_id, "findings": public, "count": len(public)}

    async def get_summary(
        self, ctx: ModuleContext, *, app_id: str, build_registry_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_app_id = normalize_optional_text(app_id)
        if not normalized_app_id:
            raise ValueError("app_id is required")
        findings = await self.repo.list_findings(
            ctx,
            query=app_findings_query(
                ctx, app_id=normalized_app_id,
                build_registry_id=normalize_optional_text(build_registry_id),
            ),
            limit=250,
        )
        return {"app_id": normalized_app_id, "summary": summarize_findings(findings)}

    async def update_finding_status(
        self,
        ctx: ModuleContext,
        *,
        finding_id: str,
        status: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        normalized_finding_id = normalize_optional_text(finding_id)
        if not normalized_finding_id:
            raise ValueError("finding_id is required")
        normalized_status = normalize_enum(status, STATUSES, default="open", field_name="status")
        now = timestamp_now()
        finding = await self.repo.update_status(
            ctx,
            query=finding_query(ctx, finding_id=normalized_finding_id),
            status=normalized_status,
            note=normalize_optional_text(note),
            updated_by=actor_id(ctx),
            updated_at=now,
        )
        if finding is None:
            return {"success": False, "finding": None}
        public = public_finding(finding)
        await ctx.emit(
            "domain.security_readiness.finding_status_updated",
            {
                "finding_id": normalized_finding_id,
                "app_id": public.get("app_id"),
                "status": normalized_status,
            },
        )
        return {"success": True, "finding": public}

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from .policy import normalize_optional_text, validate_lifecycle_state
from .schemas import ensure_create_payload, ensure_status_payload

if TYPE_CHECKING:
    from .repo import AppRegistryRepo


def _slugify(value: str) -> str:
    lowered = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return lowered or "app"


def _create_app_id(name: str) -> str:
    return f"{_slugify(name)}-{uuid4().hex[:8]}"


class AppRegistryService:
    def __init__(self, repo: AppRegistryRepo | None = None) -> None:
        if repo is None:
            from .repo import AppRegistryRepo

            repo = AppRegistryRepo()
        self.repo = repo

    async def create_app_record(
        self,
        *,
        owner_user_id: str,
        name: str | None = None,
        description: str | None = None,
        status: str = "draft",
        app_id: str | None = None,
        chat_app_id: str | None = None,
        active_chat_id: str | None = None,
        active_workflow_id: str | None = None,
        name_source: str | None = None,
        build_context_profile: dict[str, Any] | None = None,
        current_build_run: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = ensure_create_payload(
            name=name,
            description=description,
            status=status,
            app_id=app_id,
            chat_app_id=chat_app_id,
            active_chat_id=active_chat_id,
            active_workflow_id=active_workflow_id,
            name_source=name_source,
            build_context_profile=build_context_profile,
            current_build_run=current_build_run,
        )
        resolved_app_id = payload["app_id"] or _create_app_id(payload["name"] or "draft-app")
        app = await self.repo.upsert_app_record(
            owner_user_id=owner_user_id,
            name=payload["name"],
            description=payload["description"],
            lifecycle_state=payload["status"],
            app_id=resolved_app_id,
            chat_app_id=payload["chat_app_id"],
            active_chat_id=payload["active_chat_id"],
            active_workflow_id=payload["active_workflow_id"],
            name_source=payload["name_source"],
            name_status=payload["name_status"],
            build_context_profile=payload["build_context_profile"],
            current_build_run=payload["current_build_run"],
        )
        return {"success": True, "app": app}

    async def update_build_status(
        self,
        *,
        build_registry_id: str,
        status: str,
        bundle_path: str | None = None,
        artifact_version_id: str | None = None,
        workflow_sequence: str | None = None,
        active_chat_id: str | None = None,
        active_workflow_id: str | None = None,
        current_build_run: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = ensure_status_payload(
            build_registry_id=build_registry_id,
            status=status,
            bundle_path=bundle_path,
            artifact_version_id=artifact_version_id,
            workflow_sequence=workflow_sequence,
            active_chat_id=active_chat_id,
            active_workflow_id=active_workflow_id,
            current_build_run=current_build_run,
        )
        app = await self.repo.update_lifecycle_state(
            build_registry_id=payload["build_registry_id"],
            lifecycle_state=payload["status"],
            bundle_path=payload["bundle_path"],
            artifact_version_id=payload["artifact_version_id"],
            workflow_sequence=payload["workflow_sequence"],
            active_chat_id=payload["active_chat_id"],
            active_workflow_id=payload["active_workflow_id"],
            current_build_run=payload["current_build_run"],
        )
        return {"success": app is not None, "app": app}

    async def list_apps(self, *, owner_user_id: str) -> dict[str, Any]:
        apps = await self.repo.list_apps_for_user(owner_user_id=owner_user_id)
        return {"apps": apps}

    async def get_app_record(
        self,
        *,
        app_id: str | None = None,
        build_registry_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_app_id = normalize_optional_text(app_id)
        normalized_record_id = normalize_optional_text(build_registry_id)
        app = None
        if normalized_record_id:
            app = await self.repo.get_by_build_registry_id(build_registry_id=normalized_record_id)
        elif normalized_app_id:
            app = await self.repo.get_by_app_id(app_id=normalized_app_id)
        return {"app": app}

    async def promote_build(
        self,
        *,
        build_registry_id: str,
        promoted_by: str,
    ) -> dict[str, Any]:
        normalized_record_id = normalize_optional_text(build_registry_id)
        if not normalized_record_id:
            raise ValueError("build_registry_id is required")
        record = await self.repo.get_by_build_registry_id(build_registry_id=normalized_record_id)
        if not record:
            raise ValueError(f"App record not found: {normalized_record_id}")
        current_state = record.get("lifecycle_state", "")
        if current_state != "review":
            raise ValueError(
                f"Cannot promote app from '{current_state}' state. App must be in 'review' to promote."
            )
        app = await self.repo.update_lifecycle_state(
            build_registry_id=normalized_record_id,
            lifecycle_state="active",
            bundle_path=record.get("bundle_path"),
        )
        return {"success": app is not None, "app": app}

    async def delete_app(self, *, build_registry_id: str) -> dict[str, Any]:
        normalized_record_id = normalize_optional_text(build_registry_id)
        if not normalized_record_id:
            raise ValueError("build_registry_id is required")
        # Resolve app_id before deleting so we can cascade-purge usage events
        existing = await self.repo.get_by_build_registry_id(build_registry_id=normalized_record_id)
        deleted = await self.repo.delete_app(build_registry_id=normalized_record_id)
        if deleted and existing:
            app_id = existing.get("app_id")
            if app_id:
                try:
                    from mozaiksai.core.usage import get_runtime_usage_ledger
                    await get_runtime_usage_ledger().purge_usage_for_app(app_id=app_id)
                except Exception:
                    pass  # usage purge is best-effort; don't fail the delete
        return {"success": deleted}

    async def ensure_status_for_app(
        self,
        *,
        app_id: str,
        owner_user_id: str,
        status: str,
        default_name: str | None = None,
    ) -> dict[str, Any]:
        existing = await self.repo.get_by_app_id(app_id=app_id)
        validated_status = validate_lifecycle_state(status)
        if existing:
            app = await self.repo.update_lifecycle_state(
                build_registry_id=existing["build_registry_id"],
                lifecycle_state=validated_status,
            )
            return {"success": app is not None, "app": app}
        return await self.create_app_record(
            owner_user_id=owner_user_id,
            name=default_name or app_id,
            status=validated_status,
            app_id=app_id,
        )

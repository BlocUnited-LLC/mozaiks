from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Dict, Optional
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
    def __init__(self, repo: Optional["AppRegistryRepo"] = None) -> None:
        if repo is None:
            from .repo import AppRegistryRepo

            repo = AppRegistryRepo()
        self.repo = repo

    async def create_app_record(
        self,
        *,
        owner_user_id: str,
        name: str,
        description: Optional[str] = None,
        status: str = "draft",
        app_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = ensure_create_payload(
            name=name,
            description=description,
            status=status,
            app_id=app_id,
        )
        resolved_app_id = payload["app_id"] or _create_app_id(payload["name"])
        app = await self.repo.upsert_app_record(
            owner_user_id=owner_user_id,
            name=payload["name"],
            description=payload["description"],
            lifecycle_state=payload["status"],
            app_id=resolved_app_id,
        )
        return {"success": True, "app": app}

    async def update_build_status(
        self,
        *,
        build_registry_id: str,
        status: str,
        bundle_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = ensure_status_payload(
            build_registry_id=build_registry_id,
            status=status,
            bundle_path=bundle_path,
        )
        app = await self.repo.update_lifecycle_state(
            build_registry_id=payload["build_registry_id"],
            lifecycle_state=payload["status"],
            bundle_path=payload["bundle_path"],
        )
        return {"success": app is not None, "app": app}

    async def list_apps(self, *, owner_user_id: str) -> Dict[str, Any]:
        apps = await self.repo.list_apps_for_user(owner_user_id=owner_user_id)
        return {"apps": apps}

    async def get_app_record(
        self,
        *,
        app_id: Optional[str] = None,
        build_registry_id: Optional[str] = None,
    ) -> Dict[str, Any]:
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
    ) -> Dict[str, Any]:
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

    async def ensure_status_for_app(
        self,
        *,
        app_id: str,
        owner_user_id: str,
        status: str,
        default_name: Optional[str] = None,
    ) -> Dict[str, Any]:
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

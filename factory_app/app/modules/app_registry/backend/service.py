from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from mozaiksai.core.session.build_binding import BuildTargetReference, RunBuildBinding

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

    async def resolve_build_binding(
        self,
        *,
        owner_user_id: str,
        app_id: str,
        chat_id: str,
        workflow_name: str,
        build_registry_id: str | None = None,
        source_chat_id: str | None = None,
        persisted_binding: dict[str, Any] | None = None,
        resume: bool = False,
        refinement: bool = False,
        allow_create: bool = False,
    ) -> RunBuildBinding:
        """Bind a build through authenticated registry/session ownership.

        IDs supplied by a client are selectors only. Execution app identity
        never becomes the generated app ID, including when reopening a build.
        """
        if not owner_user_id or not app_id or not chat_id:
            raise ValueError("Build binding requires execution app, owner, and chat identity")
        reference = BuildTargetReference(
            build_registry_id=build_registry_id, source_chat_id=source_chat_id,
        )
        build_registry_id = reference.build_registry_id
        source_chat_id = reference.source_chat_id
        binding = RunBuildBinding.model_validate(persisted_binding) if persisted_binding is not None else None
        if resume and binding is None:
            raise ValueError("Build session has no registered target")
        if source_chat_id:
            source = await self.repo.get_owned_chat_binding(
                app_id=app_id, owner_user_id=owner_user_id, chat_id=source_chat_id
            )
            if source is None:
                raise ValueError("Source build session is not available")
            if source:
                source_binding = RunBuildBinding.model_validate(source)
                if binding is not None and binding != source_binding:
                    raise ValueError("Source session does not match the build binding")
                binding = source_binding
            elif not allow_create or refinement or resume or binding is not None:
                raise ValueError("Source session has no build binding")
        if binding is not None:
            if build_registry_id and build_registry_id != binding.build_registry_id:
                raise ValueError("Requested app does not match the source build session")
            build_registry_id = binding.build_registry_id

        if build_registry_id:
            record = await self.repo.get_by_build_registry_id(
                build_registry_id=build_registry_id, owner_user_id=owner_user_id
            )
            if not record or record.get("chat_app_id") != app_id:
                raise ValueError("Registered build target is not available in this host")
            if binding is not None and binding.target_app_id != record["app_id"]:
                raise ValueError("Persisted build target does not match the registry")
            if binding is None and not refinement:
                active_chat = record.get("active_chat_id")
                if not active_chat:
                    if not allow_create or record.get("lifecycle_state") != "draft":
                        raise ValueError("Registered app has no resumable build session")
                    binding = RunBuildBinding(
                        build_registry_id=build_registry_id, target_app_id=record["app_id"],
                        build_id=f"build_{uuid4().hex}", phase="genesis",
                    )
                    result = await self.update_build_status(
                        owner_user_id=owner_user_id, build_registry_id=build_registry_id,
                        status="building", active_chat_id=chat_id, active_workflow_id=workflow_name,
                        current_build_run={"build_id": binding.build_id, "phase": binding.phase},
                        expected_lifecycle_state="draft",
                    )
                    if not result["success"]:
                        raise ValueError("Registered draft changed before its build could start")
                else:
                    source = await self.repo.get_owned_chat_binding(
                        app_id=app_id, owner_user_id=owner_user_id, chat_id=active_chat
                    )
                    if not source:
                        raise ValueError("Registered app build session is not available")
                    binding = RunBuildBinding.model_validate(source)
                if binding.build_registry_id != build_registry_id or binding.target_app_id != record["app_id"]:
                    raise ValueError("Registered app has an inconsistent build session")
        else:
            if not allow_create or refinement or resume:
                raise ValueError("Select a registered build target before starting this workflow")
            build_id = f"build_{uuid4().hex}"
            result = await self.create_app_record(
                owner_user_id=owner_user_id,
                status="building",
                chat_app_id=app_id,
                active_chat_id=chat_id,
                active_workflow_id=workflow_name,
                current_build_run={"build_id": build_id, "phase": "genesis"},
            )
            record = result["app"]
            binding = RunBuildBinding(
                build_registry_id=record["build_registry_id"],
                target_app_id=record["app_id"],
                build_id=build_id,
                phase="genesis",
            )

        if refinement and not resume:
            binding = await self.begin_refinement_run(
                owner_user_id=owner_user_id, app_id=app_id,
                build_registry_id=record["build_registry_id"],
                workflow_name=workflow_name, chat_id=chat_id,
            )
        if binding is None:
            raise ValueError("Build target could not be established")
        current_build_id = (record.get("current_build_run") or {}).get("build_id")
        if (not refinement or resume) and current_build_id and current_build_id != binding.build_id:
            raise ValueError("The selected build session has been superseded")
        return binding

    async def begin_refinement_run(
        self, *, owner_user_id: str, app_id: str, build_registry_id: str,
        workflow_name: str, chat_id: str | None = None,
    ) -> RunBuildBinding:
        """Allocate a run for a workflow or an inline coding execution."""
        record = await self.repo.get_by_build_registry_id(
            build_registry_id=build_registry_id, owner_user_id=owner_user_id,
        )
        if not record or record.get("chat_app_id") != app_id:
            raise ValueError("Registered build target is not available in this host")
        binding = RunBuildBinding(
            build_registry_id=record["build_registry_id"], target_app_id=record["app_id"],
            build_id=f"build_{uuid4().hex}", phase="refinement",
        )
        result = await self.update_build_status(
            owner_user_id=owner_user_id, build_registry_id=binding.build_registry_id,
            status="building", active_chat_id=chat_id, active_workflow_id=workflow_name,
            expected_build_id=(record.get("current_build_run") or {}).get("build_id"),
            current_build_run={"build_id": binding.build_id, "phase": binding.phase},
        )
        if not result["success"]:
            raise ValueError("Registered build changed before refinement could start")
        return binding

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
        owner_user_id: str,
        status: str,
        bundle_path: str | None = None,
        artifact_version_id: str | None = None,
        workflow_sequence: str | None = None,
        active_chat_id: str | None = None,
        active_workflow_id: str | None = None,
        current_build_run: dict[str, Any] | None = None,
        expected_build_id: str | None = None,
        expected_lifecycle_state: str | None = None,
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
            owner_user_id=owner_user_id,
            build_registry_id=payload["build_registry_id"],
            lifecycle_state=payload["status"],
            bundle_path=payload["bundle_path"],
            artifact_version_id=payload["artifact_version_id"],
            workflow_sequence=payload["workflow_sequence"],
            active_chat_id=payload["active_chat_id"],
            active_workflow_id=payload["active_workflow_id"],
            current_build_run=payload["current_build_run"],
            expected_build_id=expected_build_id,
            expected_lifecycle_state=expected_lifecycle_state,
        )
        return {"success": app is not None, "app": app}

    async def list_apps(self, *, owner_user_id: str) -> dict[str, Any]:
        apps = await self.repo.list_apps_for_user(owner_user_id=owner_user_id)
        return {"apps": apps}

    async def get_app_record(
        self,
        *,
        owner_user_id: str,
        app_id: str | None = None,
        build_registry_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_app_id = normalize_optional_text(app_id)
        normalized_record_id = normalize_optional_text(build_registry_id)
        app = None
        if normalized_record_id:
            app = await self.repo.get_by_build_registry_id(build_registry_id=normalized_record_id, owner_user_id=owner_user_id)
        elif normalized_app_id:
            app = await self.repo.get_by_app_id(app_id=normalized_app_id, owner_user_id=owner_user_id)
        return {"app": app}

    async def promote_build(
        self,
        *,
        build_registry_id: str,
        promoted_by: str,
        expected_build_id: str,
        expected_artifact_version_id: str,
        bundle_path: str | None = None,
    ) -> dict[str, Any]:
        normalized_record_id = normalize_optional_text(build_registry_id)
        if not normalized_record_id:
            raise ValueError("build_registry_id is required")
        record = await self.repo.get_by_build_registry_id(build_registry_id=normalized_record_id, owner_user_id=promoted_by)
        if not record:
            raise ValueError(f"App record not found: {normalized_record_id}")
        current_state = record.get("lifecycle_state", "")
        if current_state != "review":
            raise ValueError(
                f"Cannot promote app from '{current_state}' state. App must be in 'review' to promote."
            )
        app = await self.repo.update_lifecycle_state(
            owner_user_id=promoted_by,
            build_registry_id=normalized_record_id,
            lifecycle_state="active",
            bundle_path=bundle_path or record.get("bundle_path"),
            expected_build_id=expected_build_id,
            expected_artifact_version_id=expected_artifact_version_id,
            expected_lifecycle_state="review",
        )
        return {"success": app is not None, "app": app}

    async def delete_app(self, *, build_registry_id: str, owner_user_id: str) -> dict[str, Any]:
        normalized_record_id = normalize_optional_text(build_registry_id)
        if not normalized_record_id:
            raise ValueError("build_registry_id is required")
        # A directory record does not authorize deleting app-wide usage facts.
        deleted = await self.repo.delete_app(build_registry_id=normalized_record_id, owner_user_id=owner_user_id)
        return {"success": deleted}

    async def ensure_status_for_app(
        self,
        *,
        app_id: str,
        owner_user_id: str,
        status: str,
        default_name: str | None = None,
    ) -> dict[str, Any]:
        existing = await self.repo.get_by_app_id(app_id=app_id, owner_user_id=owner_user_id)
        validated_status = validate_lifecycle_state(status)
        if existing:
            app = await self.repo.update_lifecycle_state(
                owner_user_id=owner_user_id,
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

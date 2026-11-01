"""The bound build's current app bundle version, verified against the registry."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from factory_app.app.modules.app_registry.backend.service import AppRegistryService
from factory_app.workflows._shared.platform.build_target import require_build_binding
from mozaiksai.core.artifacts.models import (
    BuildRecord,
    BuildRecordStatus,
    resolve_canonical_bundle_entry,
)
from mozaiksai.core.artifacts.store import get_artifact_store
from mozaiksai.core.session.build_binding import RunBuildBinding
from mozaiksai.core.workflow.context.frozen import detach


async def resolve_current_build_artifact(
    context: Any, *, app_id: str, user_id: str, error_prefix: str,
) -> tuple[RunBuildBinding, dict[str, Any], BuildRecord]:
    """Return the binding, registry record, and verified current bundle of the bound build.

    ``app_id`` is the execution host; failures raise ``ValueError`` codes
    prefixed with ``error_prefix`` so each caller keeps its own diagnostics.
    """
    try:
        binding = require_build_binding(context)
    except ValidationError as exc:
        raise ValueError(f"{error_prefix}_binding_invalid") from exc
    if binding.target_app_id == app_id:
        raise ValueError(f"{error_prefix}_target_is_execution_host")
    response = await AppRegistryService().get_app_record(
        owner_user_id=user_id, build_registry_id=binding.build_registry_id,
    )
    record = response.get("app")
    if not record or record.get("chat_app_id") != app_id or record.get("app_id") != binding.target_app_id:
        raise ValueError(f"{error_prefix}_target_unavailable")
    run = record.get("current_build_run") or {}
    if run.get("build_id") != binding.build_id or run.get("phase") != binding.phase:
        raise ValueError(f"{error_prefix}_build_superseded")
    artifact_id = run.get("artifact_version_id")
    if not artifact_id:
        raise ValueError(f"{error_prefix}_artifact_missing")
    selected = detach(context.get("artifact_version_id")) if context is not None else None
    if selected is not None and selected != artifact_id:
        raise ValueError(f"{error_prefix}_artifact_not_current")
    artifact = await get_artifact_store().get_build_record(
        app_id=binding.target_app_id, build_record_id=artifact_id,
    )
    if artifact is None:
        raise ValueError(f"{error_prefix}_artifact_unavailable")
    if artifact.id != artifact_id or artifact.app_id != binding.target_app_id:
        raise ValueError(f"{error_prefix}_artifact_target_mismatch")
    if artifact.lifecycle_status not in {BuildRecordStatus.DRAFT, BuildRecordStatus.CURRENT}:
        raise ValueError(f"{error_prefix}_artifact_stale")
    metadata = artifact.commit_metadata.metadata
    if artifact.commit_metadata.author_user_id != user_id or any(
        metadata.get(key) != value for key, value in binding.model_dump().items()
    ):
        raise ValueError(f"{error_prefix}_artifact_binding_mismatch")
    try:
        resolve_canonical_bundle_entry(artifact)
    except ValueError as exc:
        raise ValueError(f"{error_prefix}_artifact_manifest_invalid") from exc
    return binding, record, artifact

"""Security assessment source ownership over the existing Factory build binding."""

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
from mozaiksai.core.workflow.agents.factory import active_workflow_tool_run
from mozaiksai.core.workflow.context.frozen import detach


def context_get(context: Any, key: str, default: Any = None) -> Any:
    return detach(context.get(key, default)) if context is not None else default


def context_set(context: Any, key: str, value: Any) -> None:
    if isinstance(context, dict):
        context[key] = value
    elif context is not None:
        context.set(key, value)


async def resolve_security_artifact(context: Any) -> tuple[RunBuildBinding, BuildRecord]:
    workflow, app_id, _, user_id = active_workflow_tool_run()
    if workflow != "SecurityReadiness":
        raise ValueError("security_source_workflow_mismatch")
    try:
        binding = require_build_binding(context)
    except ValidationError as exc:
        raise ValueError("security_source_binding_invalid") from exc
    if binding.target_app_id == app_id:
        raise ValueError("security_source_target_is_execution_host")
    response = await AppRegistryService().get_app_record(
        owner_user_id=user_id, build_registry_id=binding.build_registry_id,
    )
    record = response.get("app")
    if not record or record.get("chat_app_id") != app_id or record.get("app_id") != binding.target_app_id:
        raise ValueError("security_source_target_unavailable")
    run = record.get("current_build_run") or {}
    if run.get("build_id") != binding.build_id or run.get("phase") != binding.phase:
        raise ValueError("security_source_build_superseded")
    artifact_id = run.get("artifact_version_id")
    if not artifact_id:
        raise ValueError("security_source_artifact_missing")
    selected = context_get(context, "artifact_version_id")
    if selected is not None and selected != artifact_id:
        raise ValueError("security_source_artifact_not_current")
    artifact = await get_artifact_store().get_build_record(
        app_id=binding.target_app_id, build_record_id=artifact_id,
    )
    if artifact is None:
        raise ValueError("security_source_artifact_unavailable")
    if artifact.id != artifact_id or artifact.app_id != binding.target_app_id:
        raise ValueError("security_source_artifact_target_mismatch")
    if artifact.lifecycle_status not in {BuildRecordStatus.DRAFT, BuildRecordStatus.CURRENT}:
        raise ValueError("security_source_artifact_stale")
    metadata = artifact.commit_metadata.metadata
    if artifact.commit_metadata.author_user_id != user_id or any(
        metadata.get(key) != value for key, value in binding.model_dump().items()
    ):
        raise ValueError("security_source_artifact_binding_mismatch")
    try:
        resolve_canonical_bundle_entry(artifact)
    except ValueError as exc:
        raise ValueError("security_source_artifact_manifest_invalid") from exc
    return binding, artifact


def source_failure(context: Any, error: str, diagnostics: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    result = {
        "success": False, "status": "not_assessed", "persisted": False,
        "mode": context_get(context, "security_readiness_mode", "advisory"),
        "checked_file_count": 0, "finding_count": 0, "findings": [],
        "source_error": error, "source_diagnostics": diagnostics or [],
    }
    context_set(context, "security_readiness_findings", [])
    context_set(context, "security_readiness_summary", result)
    context_set(context, "security_readiness_recorded", False)
    return result


def source_exception(context: Any, exc: Exception) -> dict[str, Any]:
    code = str(exc)
    if code.startswith(("security_source_", "artifact_bundle_", "workflow_tool_invocation_")):
        return source_failure(context, code)
    return source_failure(context, "security_source_unavailable", [
        {"code": type(exc).__name__, "blocking": True},
    ])

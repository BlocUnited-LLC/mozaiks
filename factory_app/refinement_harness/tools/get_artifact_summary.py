from __future__ import annotations

from typing import Any

from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.core.artifacts.store import ArtifactStore, get_artifact_store

from ._shared import normalize_context


async def get_artifact_summary(
    *,
    context: ControlPlaneToolContext | dict[str, Any] | None = None,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    tool_context = normalize_context(context)
    app_id = str(tool_context.app_id or "").strip()
    if not app_id:
        return {"present": False, "reason": "missing_app_id"}

    store = artifact_store or get_artifact_store()
    artifact = None
    if tool_context.artifact_version_id:
        artifact = await store.get_artifact_version(
            app_id=app_id,
            artifact_version_id=str(tool_context.artifact_version_id),
        )
    elif tool_context.artifact_kind:
        versions = await store.list_artifact_versions(
            app_id=app_id,
            artifact_kind=str(tool_context.artifact_kind),
            artifact_key=str(tool_context.artifact_key or "").strip() or None,
            limit=1,
        )
        artifact = versions[0] if versions else None

    if artifact is None:
        return {
            "present": False,
            "app_id": app_id,
            "artifact_kind": tool_context.artifact_kind,
            "artifact_key": tool_context.artifact_key,
            "artifact_version_id": tool_context.artifact_version_id,
        }

    recent_changes = await store.list_change_requests(
        app_id=app_id,
        artifact_version_id=artifact.id,
        limit=5,
    )
    return {
        "present": True,
        "app_id": app_id,
        "artifact_version_id": artifact.id,
        "artifact_kind": artifact.artifact_kind,
        "artifact_key": artifact.artifact_key,
        "version_number": artifact.version_number,
        "lineage_root_id": artifact.lineage_root_id,
        "parent_version_id": artifact.parent_version_id,
        "lifecycle_status": artifact.lifecycle_status.value,
        "validation_status": artifact.validation_status.value,
        "source_workflow": artifact.source_workflow,
        "files_count": len(artifact.files_manifest or []),
        "canonical_inputs_version": dict(artifact.canonical_inputs_version or {}),
        "recent_change_classes": [change.classification.value for change in recent_changes],
        "updated_at": artifact.updated_at,
    }

from __future__ import annotations

from typing import Any

from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.core.artifacts.store import BuildRecordStore, get_build_record_store

from ._shared import normalize_context


async def get_build_record_summary(
    *,
    context: ControlPlaneToolContext | dict[str, Any] | None = None,
    record_store: BuildRecordStore | None = None,
) -> dict[str, Any]:
    tool_context = normalize_context(context)
    app_id = str(tool_context.app_id or "").strip()
    if not app_id:
        return {"present": False, "reason": "missing_app_id"}

    store = record_store or get_build_record_store()
    artifact = None
    if tool_context.build_record_id:
        artifact = await store.get_build_record(
            app_id=app_id,
            build_record_id=str(tool_context.build_record_id),
        )
    elif tool_context.build_family:
        versions = await store.list_build_records(
            app_id=app_id,
            build_family=str(tool_context.build_family),
            build_key=str(tool_context.build_key or "").strip() or None,
            limit=1,
        )
        artifact = versions[0] if versions else None

    if artifact is None:
        return {
            "present": False,
            "app_id": app_id,
            "build_family": tool_context.build_family,
            "build_key": tool_context.build_key,
            "build_record_id": tool_context.build_record_id,
        }

    recent_changes = await store.list_change_requests(
        app_id=app_id,
        build_record_id=artifact.id,
        limit=5,
    )
    return {
        "present": True,
        "app_id": app_id,
        "build_record_id": artifact.id,
        "build_family": artifact.build_family,
        "build_key": artifact.build_key,
        "version_number": artifact.version_number,
        "lineage_root_id": artifact.lineage_root_id,
        "parent_build_record_id": artifact.parent_build_record_id,
        "lifecycle_status": artifact.lifecycle_status.value,
        "validation_status": artifact.validation_status.value,
        "source_workflow": artifact.source_workflow,
        "files_count": len(artifact.files_manifest or []),
        "canonical_inputs_version": dict(artifact.canonical_inputs_version or {}),
        "recent_change_classes": [change.classification.value for change in recent_changes],
        "updated_at": artifact.updated_at,
    }

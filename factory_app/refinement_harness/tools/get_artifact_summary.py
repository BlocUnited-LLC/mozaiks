from __future__ import annotations

from typing import Any

from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.core.artifacts.store import BuildRecordStore, get_build_record_store

from ._shared import normalize_context


async def get_artifact_summary(
    *,
    context: ControlPlaneToolContext | dict[str, Any] | None = None,
    artifact_store: BuildRecordStore | None = None,
) -> dict[str, Any]:
    tool_context = normalize_context(context)
    app_id = str(tool_context.app_id or "").strip()
    if not app_id:
        return {"present": False, "reason": "missing_app_id"}

    store = artifact_store or get_build_record_store()
    build_record = None
    if tool_context.build_record_id:
        build_record = await store.get_build_record(
            app_id=app_id,
            build_record_id=str(tool_context.build_record_id),
        )
    elif tool_context.build_family:
        records = await store.list_build_records(
            app_id=app_id,
            build_family=str(tool_context.build_family),
            build_key=str(tool_context.build_key or "").strip() or None,
            limit=1,
        )
        build_record = records[0] if records else None

    if build_record is None:
        return {
            "present": False,
            "app_id": app_id,
            "build_family": tool_context.build_family,
            "build_key": tool_context.build_key,
            "build_record_id": tool_context.build_record_id,
        }

    recent_changes = await store.list_change_requests(
        app_id=app_id,
        build_record_id=build_record.id,
        limit=5,
    )
    return {
        "present": True,
        "app_id": app_id,
        "build_record_id": build_record.id,
        "build_family": build_record.build_family,
        "build_key": build_record.build_key,
        "version_number": build_record.version_number,
        "lineage_root_id": build_record.lineage_root_id,
        "parent_version_id": build_record.parent_version_id,
        "lifecycle_status": build_record.lifecycle_status.value,
        "validation_status": build_record.validation_status.value,
        "source_workflow": build_record.source_workflow,
        "files_count": len(build_record.files_manifest or []),
        "canonical_inputs_version": dict(build_record.canonical_inputs_version or {}),
        "recent_change_classes": [change.classification.value for change in recent_changes],
        "updated_at": build_record.updated_at,
    }

"""Persistence helpers for generator workflow export metadata."""

from __future__ import annotations

from typing import Any, Dict, Optional

from mozaiksai.core.data.persistence.artifact_store import BuilderArtifactStore


async def record_workflow_export(
    *,
    app_id: str,
    user_id: Optional[str],
    workflow_type: str,
    repo_url: Optional[str],
    job_id: Optional[str],
    meta: Optional[Dict[str, Any]] = None,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    store = BuilderArtifactStore()
    return await store.record_workflow_export(
        app_id=app_id,
        user_id=user_id,
        workflow_type=workflow_type,
        repo_url=repo_url,
        job_id=job_id,
        meta=meta,
        extra_fields=extra_fields,
    )


async def get_latest_workflow_export(*, app_id: str, workflow_type: str) -> Optional[Dict[str, Any]]:
    store = BuilderArtifactStore()
    return await store.get_latest_workflow_export(app_id=app_id, workflow_type=workflow_type)


__all__ = ["get_latest_workflow_export", "record_workflow_export"]

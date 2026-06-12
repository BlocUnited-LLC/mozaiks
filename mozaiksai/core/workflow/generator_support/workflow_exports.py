"""Persistence helpers for generator workflow export metadata."""

from __future__ import annotations

from typing import Any

from mozaiksai.core.data.persistence.artifact_store import BuilderArtifactStore


async def record_workflow_export(
    *,
    app_id: str,
    user_id: str | None,
    workflow_type: str,
    repo_url: str | None,
    job_id: str | None,
    meta: dict[str, Any] | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
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


async def get_latest_workflow_export(*, app_id: str, workflow_type: str) -> dict[str, Any] | None:
    store = BuilderArtifactStore()
    return await store.get_latest_workflow_export(app_id=app_id, workflow_type=workflow_type)


__all__ = ["get_latest_workflow_export", "record_workflow_export"]

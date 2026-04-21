"""Persistence helpers for generator workflow export metadata."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Dict, Optional

from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager


async def _collection(name: str):
    pm = AG2PersistenceManager()
    await pm.persistence._ensure_client()
    assert pm.persistence.client is not None, "Mongo client not initialized"
    return pm.persistence.client["mozaiksai"][name]


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
    now = datetime.now(UTC).isoformat()
    doc: Dict[str, Any] = {
        "app_id": app_id,
        "appId": app_id,
        "user_id": user_id,
        "userId": user_id,
        "workflow_type": workflow_type,
        "workflowType": workflow_type,
        "repo_url": repo_url,
        "repoUrl": repo_url,
        "job_id": job_id,
        "jobId": job_id,
        "meta": meta or {},
        "created_at_utc": now,
        "createdAt": now,
        "updated_at_utc": now,
        "updatedAt": now,
    }
    if extra_fields:
        doc.update(extra_fields)
    coll = await _collection("WorkflowExports")
    await coll.insert_one(doc)
    return doc


async def get_latest_workflow_export(*, app_id: str, workflow_type: str) -> Optional[Dict[str, Any]]:
    coll = await _collection("WorkflowExports")
    cursor = coll.find({"app_id": app_id, "workflow_type": workflow_type}).sort("_id", -1).limit(1)
    docs = await cursor.to_list(length=1)
    return docs[0] if docs else None


__all__ = ["get_latest_workflow_export", "record_workflow_export"]

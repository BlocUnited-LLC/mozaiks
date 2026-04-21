"""Persistence helpers for workflow-generated artifacts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Dict, Optional

from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager


async def record_workflow_artifacts(
    *,
    app_id: str,
    user_id: Optional[str],
    workflow_type: str,
    workflow_name: str,
    artifacts: Dict[str, Any],
) -> Dict[str, Any]:
    pm = AG2PersistenceManager()
    await pm.persistence._ensure_client()
    assert pm.persistence.client is not None, "Mongo client not initialized"
    now = datetime.now(UTC).isoformat()
    doc = {
        "app_id": app_id,
        "appId": app_id,
        "user_id": user_id,
        "userId": user_id,
        "workflow_type": workflow_type,
        "workflowType": workflow_type,
        "workflow_name": workflow_name,
        "workflowName": workflow_name,
        "artifacts": artifacts,
        "created_at_utc": now,
        "createdAt": now,
    }
    await pm.persistence.client["mozaiksai"]["WorkflowArtifacts"].insert_one(doc)
    return doc


__all__ = ["record_workflow_artifacts"]

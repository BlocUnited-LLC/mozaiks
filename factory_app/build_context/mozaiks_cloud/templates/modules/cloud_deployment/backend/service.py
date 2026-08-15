"""Cloud deployment module service.

This module is a managed-capability facade. The actual deployment operations
are performed by the Mozaiks Cloud provider. This service layer:

- Records deployment operations initiated via the facade actions
- Emits normalized domain events so downstream modules can react
- Provides read access to cached deployment status from the provider

Event types emitted by this module (declared in contracts/events.yaml):
  cloud.deployment.submitted
  cloud.deployment.status_updated
  cloud.deployment.rollback_requested
"""

from __future__ import annotations

import uuid
from typing import Any


_MODULE_ID = "cloud_deployment"


async def record_deployment_submitted(
    ctx: Any,
    *,
    app_id: str,
    environment: str,
    release_ref: str,
    operation_id: str,
) -> dict[str, Any]:
    """Persist a submitted deployment record and emit cloud.deployment.submitted."""
    record = {
        "operation_id": operation_id,
        "app_id": app_id,
        "environment": environment,
        "release_ref": release_ref,
        "status": "pending",
    }
    col = ctx.persistence.collection(_MODULE_ID, "deployments")
    await col.insert_one({**record, "_id": operation_id})
    await _emit(ctx, "cloud.deployment.submitted", record)
    return record


async def record_deployment_status_updated(
    ctx: Any,
    *,
    operation_id: str,
    status: str,
    message: str | None = None,
) -> dict[str, Any]:
    """Persist a provider status update and emit cloud.deployment.status_updated."""
    update = {"status": status, "message": message}
    col = ctx.persistence.collection(_MODULE_ID, "deployments")
    await col.update_one({"_id": operation_id}, {"$set": update})
    payload = {"operation_id": operation_id, **update}
    await _emit(ctx, "cloud.deployment.status_updated", payload)
    return payload


async def record_rollback_requested(
    ctx: Any,
    *,
    operation_id: str,
    target_release_ref: str,
) -> dict[str, Any]:
    """Persist a rollback request and emit cloud.deployment.rollback_requested."""
    payload = {
        "operation_id": operation_id,
        "target_release_ref": target_release_ref,
        "status": "rolling_back",
    }
    col = ctx.persistence.collection(_MODULE_ID, "deployments")
    await col.update_one({"_id": operation_id}, {"$set": {"status": "rolling_back"}})
    await _emit(ctx, "cloud.deployment.rollback_requested", payload)
    return payload


async def get_deployment(ctx: Any, *, operation_id: str) -> dict[str, Any] | None:
    """Retrieve a cached deployment record."""
    col = ctx.persistence.collection(_MODULE_ID, "deployments")
    return await col.find_one({"_id": operation_id})


async def _emit(ctx: Any, event_type: str, payload: dict[str, Any]) -> None:
    bus = getattr(ctx, "event_bus", None)
    if bus is None:
        return
    await bus.emit(
        event_type=event_type,
        payload=payload,
        source_module=_MODULE_ID,
        correlation_id=str(uuid.uuid4()),
    )

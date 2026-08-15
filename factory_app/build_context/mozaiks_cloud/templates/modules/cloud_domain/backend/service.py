"""Cloud domain module service.

This module is a managed-capability facade. The actual domain operations
are performed by the Mozaiks Cloud provider. This service layer:

- Records domain operations initiated via the facade actions
- Emits normalized domain events so downstream modules can react
- Provides read access to cached domain status from the provider

Event types emitted by this module (declared in contracts/events.yaml):
  cloud.domain.connected
  cloud.domain.status_updated
  cloud.domain.disconnected
"""

from __future__ import annotations

import uuid
from typing import Any


_MODULE_ID = "cloud_domain"


async def record_domain_connected(
    ctx: Any,
    *,
    operation_id: str,
    domain: str,
    app_id: str,
) -> dict[str, Any]:
    """Persist a domain connection record and emit cloud.domain.connected."""
    record = {
        "operation_id": operation_id,
        "domain": domain,
        "app_id": app_id,
        "status": "pending_verification",
    }
    col = ctx.persistence.collection(_MODULE_ID, "domains")
    await col.insert_one({**record, "_id": domain})
    await _emit(ctx, "cloud.domain.connected", record)
    return record


async def record_domain_status_updated(
    ctx: Any,
    *,
    domain: str,
    status: str,
    dns_verified: bool = False,
    tls_status: str = "pending",
) -> dict[str, Any]:
    """Persist a provider domain status update and emit cloud.domain.status_updated."""
    update = {"status": status, "dns_verified": dns_verified, "tls_status": tls_status}
    col = ctx.persistence.collection(_MODULE_ID, "domains")
    await col.update_one({"_id": domain}, {"$set": update})
    payload = {"domain": domain, **update}
    await _emit(ctx, "cloud.domain.status_updated", payload)
    return payload


async def record_domain_disconnected(
    ctx: Any,
    *,
    domain: str,
) -> dict[str, Any]:
    """Persist a domain disconnection and emit cloud.domain.disconnected."""
    col = ctx.persistence.collection(_MODULE_ID, "domains")
    await col.update_one({"_id": domain}, {"$set": {"status": "disconnected"}})
    payload = {"domain": domain, "status": "disconnected"}
    await _emit(ctx, "cloud.domain.disconnected", payload)
    return payload


async def get_domain(ctx: Any, *, domain: str) -> dict[str, Any] | None:
    """Retrieve a cached domain record."""
    col = ctx.persistence.collection(_MODULE_ID, "domains")
    return await col.find_one({"_id": domain})


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

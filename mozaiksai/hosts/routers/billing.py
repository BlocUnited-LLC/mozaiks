"""Billing fulfillment ingress for loaded app workspaces."""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from mozaiksai.core.audit import AuditRecord, get_audit_logger
from mozaiksai.core.audit.audit_logger import AuditEventKind, _hash_inputs
from mozaiksai.core.auth import UserPrincipal, optional_user
from mozaiksai.core.auth.adapters.registry import is_auth_enabled
from mozaiksai.core.auth.dependencies import validate_path_app_id
from mozaiksai.core.billing import (
    BillingFulfillmentCommand,
    BillingFulfillmentCommandStore,
    BillingFulfillmentConflictError,
    BillingFulfillmentPendingError,
    BillingFulfillmentService,
)
from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher

router = APIRouter(tags=["billing"])
_global_command_store: BillingFulfillmentCommandStore | None = None


def _command_store(request: Request) -> BillingFulfillmentCommandStore:
    configured = getattr(request.app.state, "billing_fulfillment_command_store", None)
    if isinstance(configured, BillingFulfillmentCommandStore):
        return configured
    global _global_command_store
    if _global_command_store is None:
        _global_command_store = BillingFulfillmentCommandStore()
    return _global_command_store


def _valid_internal_api_key(request: Request) -> bool:
    expected = os.getenv("INTERNAL_API_KEY", "").strip()
    if not expected:
        return False
    provided = request.headers.get("x-internal-api-key", "").strip()
    if not provided:
        return False
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid internal API key")
    return True


def _authorize_fulfillment(
    request: Request,
    principal: UserPrincipal | None,
) -> str:
    if _valid_internal_api_key(request):
        return "internal_api_key"

    if principal is not None:
        if (
            principal.has_role("admin")
            or principal.has_scope("billing.admin")
            or principal.has_scope("billing.fulfillment.apply")
        ):
            return principal.user_id

    if not is_auth_enabled() and not os.getenv("INTERNAL_API_KEY", "").strip():
        return principal.user_id if principal is not None else "local_dev"

    raise HTTPException(
        status_code=403,
        detail="Billing fulfillment requires an internal API key or billing admin scope.",
    )


async def _emit_billing_event(payload: dict[str, Any]) -> None:
    event_type = str(payload.get("event_type") or "").strip()
    if not event_type:
        return
    await get_event_dispatcher().emit(
        event_type,
        {key: value for key, value in payload.items() if key != "event_type"},
    )


def _fulfillment_service(request: Request) -> BillingFulfillmentService:
    factory = getattr(request.app.state, "billing_fulfillment_service_factory", None)
    if callable(factory):
        return factory(request)
    return BillingFulfillmentService(
        config=getattr(request.app.state, "subscriptions_config", None),
        command_store=_command_store(request),
        event_sink=_emit_billing_event,
    )


async def _audit_fulfillment(
    *,
    actor_id: str,
    command: BillingFulfillmentCommand,
    result_status: str,
    success: bool,
    replayed: bool,
) -> None:
    await get_audit_logger().log(
        AuditRecord(
            kind=AuditEventKind.ADMIN_WRITE,
            actor_id=actor_id,
            app_id=command.app_id,
            tenant_id=command.tenant_id,
            workspace_id=command.workspace_id,
            resource="billing.fulfillment",
            action=result_status,
            inputs_hash=_hash_inputs(command.model_dump(mode="json")),
            outcome="ok" if success else "fail",
            extra={
                "command_id": command.command_id,
                "event_type": command.event_type,
                "source": command.source,
                "replayed": replayed,
            },
        )
    )


@router.post("/api/billing/fulfillment/apply", status_code=200)
async def apply_billing_fulfillment(
    command: BillingFulfillmentCommand,
    request: Request,
    principal: UserPrincipal | None = Depends(optional_user),
) -> dict[str, Any]:
    actor_id = _authorize_fulfillment(request, principal)
    if principal is not None and actor_id != "internal_api_key":
        validate_path_app_id(principal, command.app_id)

    try:
        result = await _fulfillment_service(request).apply_durable(command)
    except BillingFulfillmentConflictError as exc:
        await _emit_billing_event(
            {
                "event_type": "billing.fulfillment.rejected",
                "command_id": command.command_id,
                "source": command.source,
                "fulfillment_event_type": command.event_type,
                "status": "rejected",
                "reason": "idempotency_conflict",
            }
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BillingFulfillmentPendingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await _audit_fulfillment(
        actor_id=actor_id,
        command=command,
        result_status=result.status,
        success=result.success,
        replayed=result.replayed,
    )
    return result.model_dump(mode="json")


@router.get("/api/admin/billing/fulfillment", status_code=200)
async def list_billing_fulfillment_commands(
    request: Request,
    app_id: str | None = None,
    command_id: str | None = None,
    limit: int = 100,
    principal: UserPrincipal | None = Depends(optional_user),
) -> dict[str, Any]:
    actor_id = _authorize_fulfillment(request, principal)
    scoped_app_id = app_id
    if actor_id != "internal_api_key" and principal is not None:
        if scoped_app_id:
            validate_path_app_id(principal, scoped_app_id)
        elif principal.app_id:
            scoped_app_id = principal.app_id
    records = await _command_store(request).list_records(
        app_id=scoped_app_id,
        command_id=command_id,
        limit=limit,
    )
    return {"records": records}

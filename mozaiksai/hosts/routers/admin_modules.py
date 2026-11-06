"""Admin module dispatch boundary (ADR-0001).

Route:
    POST /api/admin/modules/{module_name}/{action_name}
    POST /api/admin/modules/{module_name}/{action_name}/read

Dispatches ONLY actions declared ``api_surface: admin_internal``, and only
for token-validated principals carrying an operator role from a
deny-by-default allowlist (``MOZAIKS_ADMIN_DISPATCH_ROLES``, default
``platform_operator``). Dispatch always runs with an enforce-mode
``authenticated_user`` authority — never ``trusted_bypass``, including local
development — so each action's declared module.yaml permissions are enforced
by ModuleExecutor, and every dispatch carries an audited provenance surface
plus a params digest (never raw params). Gate rejections (401/403/404) are
audited too, so probing the operator surface is visible.

Deliberately POST-only: admin params in GET query strings would land raw in
proxy/access logs, defeating the digest-only audit design. Bearer tokens must
arrive in the Authorization header — the ``access_token`` query fallback
(a WebSocket-upgrade affordance) is rejected here for the same reason.

``internal`` actions stay HTTP-unreachable everywhere. The public module
route's ban on ``admin_internal`` is unchanged; this router is the only
sanctioned HTTP path to operator surfaces.

Automatic data loads use the parameterless /read operation. It additionally
requires registered ``read_only: true`` metadata; ordinary admin actions may
write and are never inferred to be safe from their names or input schemas.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from mozaiksai.core.audit.audit_logger import get_audit_logger
from mozaiksai.core.auth import UserPrincipal, optional_user

from .modules import (
    _execute_module_action,
    _module_action_api_surface,
    _split_post_body,
)

router = APIRouter(tags=["admin-modules"])

_OPERATOR_ROLES_ENV = "MOZAIKS_ADMIN_DISPATCH_ROLES"
_DEFAULT_OPERATOR_ROLES = ("platform_operator",)


def _operator_allowlist() -> frozenset[str]:
    """Role names allowed to use the admin boundary. Deny-by-default.

    Matched against principal ROLES only — OAuth scopes are client-requestable
    at token-mint time and never grant the operator gate.
    """
    raw = os.getenv(_OPERATOR_ROLES_ENV, "")
    names = {part.strip() for part in raw.split(",") if part.strip()}
    return frozenset(names or _DEFAULT_OPERATOR_ROLES)


async def _audit_gate_denial(
    request: Request,
    principal: UserPrincipal | None,
    module_name: str,
    action_name: str,
    *,
    outcome: str,
) -> None:
    """Record a rejected admin-boundary attempt — probing must be visible."""
    try:
        await get_audit_logger().log_admin_access(
            actor_id=(principal.user_id if principal else "anonymous"),
            app_id=(principal.app_id if principal else None),
            route=request.url.path,
            method=request.method,
            outcome=outcome,
        )
    except Exception:  # pragma: no cover — auditing must never mask the denial
        pass


async def _require_operator(
    request: Request,
    principal: UserPrincipal | None,
    module_name: str,
    action_name: str,
) -> UserPrincipal:
    # Header-carried bearer only: the access_token query fallback exists for
    # WebSocket upgrades and would put operator tokens into URL/proxy logs.
    if request.query_params.get("access_token"):
        await _audit_gate_denial(
            request, principal, module_name, action_name, outcome="denied_query_token"
        )
        raise HTTPException(
            status_code=401,
            detail="Operator authentication must use the Authorization header",
        )
    # Only a principal produced by validating a real bearer token counts.
    # Role strings alone are insufficient: the anonymous and dev-override
    # personas can carry any of them freely, so with auth disabled this
    # boundary refuses everything — admin actions were never
    # HTTP-dispatchable in local no-auth mode and remain that way.
    if principal is None or not principal.is_authenticated:
        await _audit_gate_denial(
            request, principal, module_name, action_name, outcome="denied_unauthenticated"
        )
        raise HTTPException(status_code=401, detail="Operator authentication required")
    if not (_operator_allowlist() & set(principal.roles)):
        await _audit_gate_denial(
            request, principal, module_name, action_name, outcome="denied_not_operator"
        )
        raise HTTPException(status_code=403, detail="Operator role required")
    return principal


async def _require_admin_surface(
    request: Request,
    principal: UserPrincipal,
    module_name: str,
    action_name: str,
) -> None:
    """404 unless the action is declared admin_internal in a populated surface map."""
    surfaces = getattr(request.app.state, "module_action_surfaces", None)
    if not isinstance(surfaces, dict) or (
        _module_action_api_surface(request, module_name, action_name) != "admin_internal"
    ):
        await _audit_gate_denial(
            request, principal, module_name, action_name, outcome="denied_not_admin_surface"
        )
        raise HTTPException(status_code=404, detail="Action not found")


@router.post("/api/admin/modules/{module_name}/{action_name}")
async def execute_admin_module_action(
    module_name: str,
    action_name: str,
    request: Request,
    principal: UserPrincipal | None = Depends(optional_user),
):
    operator = await _require_operator(request, principal, module_name, action_name)
    await _require_admin_surface(request, operator, module_name, action_name)
    body: dict[str, Any] = {}
    if request.headers.get("content-type", "").lower().startswith("application/json"):
        try:
            parsed = await request.json()
            if isinstance(parsed, dict):
                body = parsed
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc

    params, context_overrides = _split_post_body(body)

    return await _execute_module_action(
        module_name=module_name,
        action_name=action_name,
        request=request,
        principal=operator,
        params=params,
        context_overrides=context_overrides,
        lane="admin",
    )


@router.post("/api/admin/modules/{module_name}/{action_name}/read")
async def read_admin_module_action(
    module_name: str,
    action_name: str,
    request: Request,
    principal: UserPrincipal | None = Depends(optional_user),
):
    """Admit only explicitly declared parameterless reads through the admin lane."""
    operator = await _require_operator(request, principal, module_name, action_name)
    await _require_admin_surface(request, operator, module_name, action_name)
    registry = getattr(request.app.state, "executor_registry", None)
    executor = registry.module_executor if registry is not None else None
    if executor is None or not executor.is_read_only_action(module_name, action_name):
        await _audit_gate_denial(
            request, operator, module_name, action_name, outcome="denied_not_read_only"
        )
        raise HTTPException(status_code=404, detail="Action not found")

    if request.query_params:
        raise HTTPException(status_code=400, detail="Automatic admin reads do not accept query inputs")
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(status_code=400, detail="Automatic admin reads require an empty JSON object")
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc
    if not isinstance(body, dict) or body:
        raise HTTPException(status_code=400, detail="Automatic admin reads require an empty JSON object")

    return await _execute_module_action(
        module_name=module_name,
        action_name=action_name,
        request=request,
        principal=operator,
        params={},
        context_overrides={},
        lane="admin",
    )

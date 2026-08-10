"""Module action dispatch router.

Routes:
    GET  /api/modules/{module_name}/{action_name}
    POST /api/modules/{module_name}/{action_name}
"""
from __future__ import annotations

import logging
import re
from typing import Any, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request

from mozaiksai.core.auth import UserPrincipal, optional_user
from mozaiksai.core.auth.adapters.registry import is_auth_enabled
from mozaiksai.core.auth.dependencies import validate_path_app_id
from mozaiksai.core.runtime.composition.module_authority import (
    ModuleDispatchAuthority,
    ModuleDispatchAuthorityKind,
    ModuleDispatchProvenance,
)
from mozaiksai.core.runtime.composition.module_executor import ModuleRequest
from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks

router = APIRouter(tags=["modules"])
logger = logging.getLogger(__name__)

_MODULE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PUBLIC_MODULE_API_SURFACES = {"public", "public_readonly"}
_OBSERVED_MODULES = {"messages", "workspace_support"}
_RESERVED_CONTEXT_KEYS = ("app_id", "user_id", "tenant_id", "workspace_id", "correlation_id", "auth_token")
# Reserved keys that are safe to auto-promote from an action's own "params" into
# the trusted execution "context" when the caller omits them from context. This
# is intentional for resource-scoping identifiers such as app_id, which many
# actions also declare as a business input for the same resource being scoped.
#
# "user_id" is deliberately excluded: action input schemas very commonly declare
# their own "user_id" parameter that names a *target* subject (e.g. add_member's
# user to add, update_member_role's user whose role changes) rather than the
# calling actor's identity. Promoting it here would silently overwrite the
# trusted execution context's user_id with that target's user_id, letting an
# action's own business input hijack actor identity resolution downstream
# (privilege confusion: authorization checks would then run as the target user
# instead of the real caller). Context-level user_id overrides must be supplied
# explicitly under the "context" envelope key, never inferred from "params".
_PROMOTABLE_PARAM_CONTEXT_KEYS = tuple(
    key for key in _RESERVED_CONTEXT_KEYS if key != "user_id"
)
# Actions with these surfaces are only reachable through the internal event bus
# or direct ModuleExecutor calls.  HTTP dispatch is always rejected regardless
# of authentication status so that event-pipeline internal handlers cannot be
# triggered directly by external callers.
_INTERNAL_MODULE_API_SURFACES = {"internal", "admin_internal"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_bearer_token(request: Request) -> str | None:
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth_header:
        return None
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip() or None
    return auth_header.strip() or None


def _module_action_api_surface(request: Request, module_name: str, action_name: str) -> str | None:
    surfaces = getattr(request.app.state, "module_action_surfaces", None)
    if not isinstance(surfaces, dict):
        return None
    module_surfaces = surfaces.get(module_name)
    if not isinstance(module_surfaces, dict):
        return None
    value = module_surfaces.get(action_name)
    return str(value or "").strip() or None


def _is_public_module_action(request: Request, module_name: str, action_name: str) -> bool:
    return _module_action_api_surface(request, module_name, action_name) in _PUBLIC_MODULE_API_SURFACES


def _is_internal_module_action(request: Request, module_name: str, action_name: str) -> bool:
    """Return True when the action surface is internal-only and must not be dispatched via HTTP."""
    return _module_action_api_surface(request, module_name, action_name) in _INTERNAL_MODULE_API_SURFACES


def _split_post_body(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a module POST body into action params and execution context.

    The canonical module API payload is {"params": {...}, "context": {...}}.
    Values inside "params" are action inputs, so reserved resource-scoping words
    such as app_id must remain available for resource-scoped actions and are
    auto-promoted into context when the caller omits them there. "user_id" is
    never auto-promoted this way (see _PROMOTABLE_PARAM_CONTEXT_KEYS) because
    action inputs frequently name a target subject, not the caller. Raw-body
    payloads still treat all reserved words (including user_id) as execution
    context and remove them from action params to preserve the old dispatch
    contract, since that legacy shape has no "params" input namespace to
    collide with.
    """
    params_body = body.get("params")
    context_body = body.get("context")
    if isinstance(params_body, dict):
        has_params_envelope = True
        params = dict(params_body)
    else:
        has_params_envelope = False
        params = dict(body)
    context_overrides: dict[str, Any] = dict(context_body) if isinstance(context_body, dict) else {}

    # The legacy raw-body shape has no "params" input namespace, so every
    # top-level reserved key (including user_id) has always unambiguously meant
    # execution context there. The canonical enveloped shape does have a
    # "params" input namespace, where "user_id" can legitimately be an action's
    # own target-subject input, so it is excluded from promotion in that case.
    promotable_keys = _RESERVED_CONTEXT_KEYS if not has_params_envelope else _PROMOTABLE_PARAM_CONTEXT_KEYS
    for key in promotable_keys:
        if key in params and key not in context_overrides:
            context_overrides[key] = params[key]

    if not has_params_envelope:
        params.pop("context", None)
        params.pop("params", None)
        for key in _RESERVED_CONTEXT_KEYS:
            params.pop(key, None)

    return params, context_overrides


async def _resolve_module_dispatch_scope(
    *,
    request: Request | None,
    principal: UserPrincipal | None,
    module_name: str,
    action_name: str,
    app_id: str,
    tenant_id: str | None,
    workspace_id: str | None,
    user_id: str | None,
    params: dict[str, Any],
) -> dict[str, Any]:
    default_permissions = list(principal.scopes) if principal else []
    return await get_platform_hooks().call_module_scope(
        principal=principal,
        module_name=module_name,
        action_name=action_name,
        requested_scope={
            "app_id": str(app_id),
            "tenant_id": str(tenant_id) if tenant_id else None,
            "workspace_id": str(workspace_id) if workspace_id else None,
            "user_id": str(user_id) if user_id else None,
        },
        params=params,
        request=request,
        default_permissions=default_permissions,
    )


async def _execute_module_action(
    *,
    module_name: str,
    action_name: str,
    request: Request,
    principal: UserPrincipal | None,
    params: dict[str, Any],
    context_overrides: dict[str, Any] | None = None,
) -> Any:
    if not _MODULE_NAME_RE.fullmatch(module_name):
        raise HTTPException(status_code=400, detail="Invalid module name")
    if not _MODULE_NAME_RE.fullmatch(action_name):
        raise HTTPException(status_code=400, detail="Invalid action name")

    # Internal-surface actions are event-bus reactions or trusted runtime calls.
    # They must never be reachable via the external HTTP module dispatch path,
    # regardless of authentication status.  Callers that need to invoke these
    # actions directly must use ModuleExecutor with granted_permissions=None.
    if _is_internal_module_action(request, module_name, action_name):
        raise HTTPException(status_code=404, detail="Action not found")

    if is_auth_enabled() and principal is None and not _is_public_module_action(request, module_name, action_name):
        raise HTTPException(status_code=401, detail="Missing authorization token")

    # IDOR gate: when an explicit app_id was supplied (not derived from the token),
    # verify it matches the authenticated principal's token claim. This prevents a
    # caller from executing actions scoped to a foreign app by passing ?app_id=other.
    # Must run before executor availability checks so auth errors take priority.
    context_overrides = context_overrides or {}
    explicit_app_id = context_overrides.get("app_id") or request.query_params.get("app_id")
    if explicit_app_id and principal is not None:
        validate_path_app_id(principal, str(explicit_app_id))

    app_id = (
        explicit_app_id
        or (principal.app_id if principal else None)
        or "default"
    )

    # HTTP query-string user_id is an authenticated override only. In local
    # no-auth mode, optional_user already resolves the stable anonymous/dev
    # principal, and trusted callers that need an explicit execution user pass
    # context_overrides directly instead of relying on external query params.
    requested_user_id = context_overrides.get("user_id")
    if requested_user_id is None and is_auth_enabled():
        requested_user_id = request.query_params.get("user_id")
    if (
        is_auth_enabled()
        and principal is not None
        and requested_user_id
        and str(requested_user_id).strip() != str(principal.user_id)
    ):
        raise HTTPException(status_code=403, detail="Token user_id does not match request user_id")

    tenant_id = (
        context_overrides.get("tenant_id")
        or request.query_params.get("tenant_id")
        or (principal.tenant_id if principal else None)
    )
    if tenant_id and principal is not None and not principal.validate_tenant_id(str(tenant_id)):
        raise HTTPException(status_code=403, detail="Token tenant_id does not match request tenant_id")

    workspace_id = (
        context_overrides.get("workspace_id")
        or request.query_params.get("workspace_id")
        or (principal.workspace_id if principal else None)
    )
    if workspace_id and principal is not None and not principal.validate_workspace_id(str(workspace_id)):
        raise HTTPException(status_code=403, detail="Token workspace_id does not match request workspace_id")

    failed_at_startup: list[str] = getattr(request.app.state, "failed_module_names", [])
    if module_name in failed_at_startup:
        raise HTTPException(
            status_code=503,
            detail=f"Module '{module_name}' failed to load at startup. Check platform logs for details.",
        )

    executor_registry = getattr(request.app.state, "executor_registry", None)
    module_executor = executor_registry.module_executor if executor_registry is not None else None
    if module_executor is None:
        raise HTTPException(
            status_code=503,
            detail="Module runtime is not available. Verify modules/*/module.yaml handlers are loaded.",
        )

    correlation_id = context_overrides.get("correlation_id") or request.query_params.get("correlation_id") or str(uuid4())
    auth_token = context_overrides.get("auth_token") or _extract_bearer_token(request)
    principal_user_id = principal.user_id if principal else None
    user_id = str(requested_user_id).strip() if requested_user_id else principal_user_id

    dispatch_scope = await _resolve_module_dispatch_scope(
        request=request,
        principal=principal,
        module_name=module_name,
        action_name=action_name,
        app_id=str(app_id),
        tenant_id=str(tenant_id) if tenant_id else None,
        workspace_id=str(workspace_id) if workspace_id else None,
        user_id=str(user_id) if user_id else None,
        params=params,
    )
    # When auth is disabled (dev/local mode), optional_user still returns an
    # anonymous principal so downstream code has a stable user shape. Treat all
    # such module HTTP calls as trusted local dispatch so module permission
    # declarations don't block the Studio admin UI. In production
    # (AUTH_ENABLED=true), non-public HTTP callers must carry a token with
    # explicit scopes, so granted_permissions remains a concrete list.
    if not is_auth_enabled():
        granted_permissions = None
        authority = ModuleDispatchAuthority(
            kind="local_development",
            permission_mode="trusted_bypass",
            reason="auth-disabled local HTTP module dispatch",
            actor_id=str(user_id) if user_id else None,
            legacy_granted_permissions_none=True,
        )
    else:
        granted_permissions = list(dispatch_scope.get("permissions") or [])
        authority_kind = cast(
            ModuleDispatchAuthorityKind,
            "authenticated_user" if principal is not None else "public_http",
        )
        authority = ModuleDispatchAuthority(
            kind=authority_kind,
            permission_mode="enforce",
            reason="HTTP module dispatch",
            actor_id=str(user_id) if user_id else None,
            permissions=tuple(granted_permissions),
        )

    module_request = ModuleRequest(
        module=module_name,
        action=action_name,
        params=params,
        app_id=str(dispatch_scope.get("app_id") or app_id),
        user_id=str(dispatch_scope.get("user_id") or user_id) if (dispatch_scope.get("user_id") or user_id) else None,
        tenant_id=str(dispatch_scope.get("tenant_id") or tenant_id) if (dispatch_scope.get("tenant_id") or tenant_id) else None,
        workspace_id=str(dispatch_scope.get("workspace_id") or workspace_id) if (dispatch_scope.get("workspace_id") or workspace_id) else None,
        auth_token=str(auth_token) if auth_token else None,
        correlation_id=str(correlation_id) if correlation_id else None,
        # HTTP module dispatch supplies a concrete permission list when auth is
        # enabled. When auth is disabled (dev mode, no principal) granted_permissions
        # is None so the executor bypasses enforcement as a trusted internal call.
        # Internal trusted calls can also bypass by invoking ModuleExecutor directly
        # with granted_permissions=None.
        granted_permissions=granted_permissions,
        authority=authority,
        provenance=ModuleDispatchProvenance(
            surface="http_module_dispatch",
            correlation_id=str(correlation_id) if correlation_id else None,
        ),
    )

    if module_name in _OBSERVED_MODULES:
        logger.info(
            "module_dispatch.start module=%s action=%s app_id=%s user_id=%s tenant_id=%s workspace_id=%s correlation_id=%s param_keys=%s auth_enabled=%s",
            module_name,
            action_name,
            module_request.app_id,
            module_request.user_id,
            module_request.tenant_id,
            module_request.workspace_id,
            module_request.correlation_id,
            sorted(params.keys()),
            is_auth_enabled(),
        )

    result = await module_executor.execute(module_request, context=None)
    if result.success:
        if module_name in _OBSERVED_MODULES:
            data: dict[str, Any] = result.data if isinstance(result.data, dict) else {}
            thread_data = data.get("thread")
            thread: dict[str, Any] = thread_data if isinstance(thread_data, dict) else {}
            thread_id = data.get("thread_id") or thread.get("thread_id")
            logger.info(
                "module_dispatch.success module=%s action=%s correlation_id=%s data_keys=%s request_id=%s thread_id=%s message_thread_id=%s",
                module_name,
                action_name,
                module_request.correlation_id,
                sorted(data.keys()),
                data.get("request_id"),
                thread_id,
                data.get("message_thread_id"),
            )
        return result.data if result.data is not None else {}

    if result.error_code in {"MODULE_NOT_FOUND", "ACTION_NOT_FOUND"}:
        status_code = 404
    elif result.error_code == "PERMISSION_DENIED":
        status_code = 403
    elif result.error_code == "ENTITLEMENT_REQUIRED":
        status_code = 402
    elif result.error_code == "INVALID_PARAMS":
        status_code = 400
    else:
        status_code = 500

    if module_name in _OBSERVED_MODULES:
        logger.warning(
            "module_dispatch.failed module=%s action=%s correlation_id=%s status_code=%s error_code=%s error=%s",
            module_name,
            action_name,
            module_request.correlation_id,
            status_code,
            result.error_code,
            result.error,
        )

    raise HTTPException(
        status_code=status_code,
        detail={
            "error": result.error or "Module action failed",
            "error_code": result.error_code or "EXECUTION_ERROR",
            "module": module_name,
            "action": action_name,
        },
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/api/modules/{module_name}/{action_name}")
async def execute_module_action_get(
    module_name: str,
    action_name: str,
    request: Request,
    principal: UserPrincipal | None = Depends(optional_user),
):
    reserved_keys = set(_RESERVED_CONTEXT_KEYS)
    params = {key: value for key, value in request.query_params.items() if key not in reserved_keys}
    return await _execute_module_action(
        module_name=module_name,
        action_name=action_name,
        request=request,
        principal=principal,
        params=params,
    )


@router.post("/api/modules/{module_name}/{action_name}")
async def execute_module_action_post(
    module_name: str,
    action_name: str,
    request: Request,
    principal: UserPrincipal | None = Depends(optional_user),
):
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
        principal=principal,
        params=params,
        context_overrides=context_overrides,
    )

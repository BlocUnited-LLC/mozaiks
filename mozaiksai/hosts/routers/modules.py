"""Module action dispatch router.

Routes:
    GET  /api/modules/{module_name}/{action_name}
    POST /api/modules/{module_name}/{action_name}
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request

from mozaiksai.core.auth import UserPrincipal, optional_user
from mozaiksai.core.auth.adapters.registry import is_auth_enabled
from mozaiksai.core.auth.dependencies import validate_path_app_id
from mozaiksai.core.metrics.usage_instrumentation import record_action_invocation
from mozaiksai.core.runtime.composition.module_authority import (
    ModuleDispatchAuthority,
    ModuleDispatchAuthorityKind,
    ModuleDispatchProvenance,
)
from mozaiksai.core.runtime.composition.module_executor import ModuleRequest
from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks
from mozaiksai.core.runtime.composition.workflow_trigger_guard import (
    WORKFLOW_TRIGGER_TRACE_HEADER,
    WORKFLOW_TRIGGER_TRACE_KEY,
)

router = APIRouter(tags=["modules"])
logger = logging.getLogger(__name__)

_MODULE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PUBLIC_MODULE_API_SURFACES = {"public", "public_readonly"}
_OBSERVED_MODULES = {"messages", "workspace_support"}
_RESERVED_CONTEXT_KEYS = ("app_id", "user_id", "tenant_id", "workspace_id", "correlation_id", "auth_token")
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


def _workflow_trigger_trace(request: Request) -> dict[str, Any] | None:
    raw = str(request.headers.get(WORKFLOW_TRIGGER_TRACE_HEADER) or "").strip()
    if not raw:
        return None
    if len(raw) > 4096:
        raise HTTPException(status_code=400, detail="Workflow trigger trace header is too large.")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Workflow trigger trace header is invalid.") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="Workflow trigger trace header is invalid.")
    return parsed


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
    Callers may also POST a flat body with no "params"/"context"
    envelope, in which case the whole body (minus any stray "context"/"params"
    keys) is treated as action params. This is purely a structural split:
    reserved execution-context words that also appear in params (app_id,
    user_id, tenant_id, workspace_id, correlation_id, auth_token) are
    reconciled against the target action's declared input schema in
    _reconcile_reserved_params, not here, since the schema is only known once
    the module/action are resolved.
    """
    params_body = body.get("params")
    context_body = body.get("context")
    if isinstance(params_body, dict):
        params = dict(params_body)
    else:
        params = dict(body)
        params.pop("context", None)
        params.pop("params", None)
    context_overrides: dict[str, Any] = dict(context_body) if isinstance(context_body, dict) else {}
    return params, context_overrides


def _module_action_input_properties(module_executor: Any, module_name: str, action_name: str) -> dict[str, Any]:
    """Return the declared input schema properties for a module action, or {} if unknown."""
    if module_executor is None:
        return {}
    action_schemas = getattr(module_executor, "_action_schemas", None)
    if not isinstance(action_schemas, dict):
        return {}
    module_schemas = action_schemas.get(module_name)
    if not isinstance(module_schemas, dict):
        return {}
    action_schema = module_schemas.get(action_name)
    if not isinstance(action_schema, dict):
        return {}
    input_schema = action_schema.get("input")
    if not isinstance(input_schema, dict):
        return {}
    properties = input_schema.get("properties")
    return properties if isinstance(properties, dict) else {}


def _reconcile_reserved_params(
    params: dict[str, Any],
    context_overrides: dict[str, Any],
    *,
    action_input_properties: dict[str, Any],
) -> None:
    """Reconcile reserved execution-context words that also appear in action params.

    Mutates params/context_overrides in place, regardless of whether the caller
    used the enveloped {params, context} shape or a flat body — both
    shapes can collide with an action's own declared input properties the same
    way, so both need the same schema-aware treatment. For each reserved word
    present in params:

      - If the target action's own input schema declares that word as a
        business property, the value stays in params so schema validation and
        the handler call see it (many actions scope by app_id, or take a
        target-subject user_id such as add_member's invitee or
        update_member_role's subject). "user_id" specifically is never
        promoted into context in this case: doing so would let a target
        subject's id silently override the authenticated actor's identity,
        letting an action's own business input hijack actor identity
        resolution downstream (authorization checks would then run as the
        target user instead of the real caller).
      - Otherwise, the value is promoted into context_overrides (when not
        already set there) and removed from params, since the handler method
        is invoked as handler.method(ctx, **params) and does not accept this
        key as a keyword argument — leaving it in params would raise a
        TypeError instead of reaching the handler. This also preserves the
        original flat-body convention (a raw POST body's own reserved words,
        such as a bare top-level "user_id", set the execution context) for
        actions that do not declare that word as one of their own inputs.

    This function only ever runs against POST body params (see
    execute_module_action_get, which never promotes reserved GET query
    params into context at all).
    """
    for key in _RESERVED_CONTEXT_KEYS:
        if key not in params:
            continue
        declared_as_business_param = key in action_input_properties
        if not (key == "user_id" and declared_as_business_param):
            context_overrides.setdefault(key, params[key])
        if not declared_as_business_param:
            params.pop(key, None)


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
    # actions directly must use ModuleExecutor with a server-owned trusted
    # dispatch authority.
    if _is_internal_module_action(request, module_name, action_name):
        raise HTTPException(status_code=404, detail="Action not found")

    # Reconcile reserved execution-context words (app_id, user_id, tenant_id,
    # workspace_id, correlation_id, auth_token) that also appear in params
    # against the target action's declared input schema. This must run before
    # any of the context-derived values below are read, and before the
    # executor-availability check further down, since it only needs read-only
    # schema metadata already loaded at startup rather than a live executor.
    context_overrides = context_overrides or {}
    schema_executor_registry = getattr(request.app.state, "executor_registry", None)
    schema_module_executor = schema_executor_registry.module_executor if schema_executor_registry is not None else None
    action_input_properties = _module_action_input_properties(schema_module_executor, module_name, action_name)
    _reconcile_reserved_params(params, context_overrides, action_input_properties=action_input_properties)

    if is_auth_enabled() and principal is None and not _is_public_module_action(request, module_name, action_name):
        raise HTTPException(status_code=401, detail="Missing authorization token")

    # IDOR gate: when an explicit app_id was supplied (not derived from the token),
    # verify it matches the authenticated principal's token claim. This prevents a
    # caller from executing actions scoped to a foreign app by passing ?app_id=other.
    # Must run before executor availability checks so auth errors take priority.
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
    # explicit scopes that become the enforce-mode authority's permissions.
    if not is_auth_enabled():
        authority = ModuleDispatchAuthority(
            kind="local_development",
            permission_mode="trusted_bypass",
            reason="auth-disabled local HTTP module dispatch",
            actor_id=str(user_id) if user_id else None,
        )
    else:
        authority_kind = cast(
            ModuleDispatchAuthorityKind,
            "authenticated_user" if principal is not None else "public_http",
        )
        authority = ModuleDispatchAuthority(
            kind=authority_kind,
            permission_mode="enforce",
            reason="HTTP module dispatch",
            actor_id=str(user_id) if user_id else None,
            permissions=tuple(dispatch_scope.get("permissions") or []),
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
        authority=authority,
        provenance=ModuleDispatchProvenance(
            surface="http_module_dispatch",
            correlation_id=str(correlation_id) if correlation_id else None,
            metadata=(
                {WORKFLOW_TRIGGER_TRACE_KEY: trigger_trace}
                if (trigger_trace := _workflow_trigger_trace(request)) is not None
                else {}
            ),
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
        record_action_invocation(
            app_id=str(module_request.app_id or "default"),
            module_id=module_name,
            action_id=action_name,
            user_id=module_request.user_id,
        )
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
    # Reserved execution-context words are query-string-only here and are
    # never promoted into the trusted execution context from GET query
    # params (that would let an unauthenticated caller set app_id/user_id/etc
    # via a URL). app_id has its own explicit, IDOR-guarded query fallback in
    # _execute_module_action; the rest are simply not meaningful as GET
    # action inputs today, so they are stripped to avoid handler TypeErrors.
    params = {
        key: value
        for key, value in request.query_params.items()
        if key not in _RESERVED_CONTEXT_KEYS
    }
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

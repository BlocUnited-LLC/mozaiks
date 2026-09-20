"""Permissioned module dispatch for tools attached to a live workflow session."""
from __future__ import annotations

import asyncio
import math
import time
from typing import Any

from starlette.websockets import WebSocketState

from mozaiksai.core.auth.adapters import get_auth_adapter
from mozaiksai.core.auth.adapters.registry import is_auth_enabled
from mozaiksai.core.auth.websocket_auth import WebSocketUser
from mozaiksai.core.runtime.composition.module_authority import (
    ModuleDispatchProvenance,
    workflow_user_authority,
)
from mozaiksai.core.runtime.composition.module_dispatch import (
    ModuleActionDispatchRequest,
    ModuleDispatchScope,
    dispatch_module_action,
)
from mozaiksai.core.runtime.composition.module_executor import ModuleResult
from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks
from mozaiksai.core.workflow.agents.factory import active_workflow_tool_run

_RECONNECT_TIMEOUT_SECONDS = 2.0
_RECONNECT_POLL_SECONDS = 0.05


class _ConnectionUnavailable(PermissionError):
    """A connection changed before dispatch; no action has executed yet."""


def _live_session(transport: Any, *, app_id: str, chat_id: str, user_id: str) -> tuple[Any, Any, WebSocketUser]:
    connection = (getattr(transport, "connections", {}) or {}).get(chat_id) or {}
    websocket = connection.get("websocket")
    principal = getattr(getattr(websocket, "state", None), "user", None)
    if websocket is None:
        raise _ConnectionUnavailable("workflow_session_connection_unavailable")
    if not isinstance(principal, WebSocketUser):
        raise PermissionError("workflow_session_principal_unavailable")
    if (
        connection.get("app_id") != app_id
        or principal.user_id != user_id
        or connection.get("user_id") != principal.user_id
        or not principal.validate_app_id(app_id)
    ):
        raise PermissionError("workflow_session_scope_mismatch")
    # A chat-bound token cannot authorize an aliased successor chat.
    if not principal.validate_chat_id(chat_id):
        raise PermissionError("workflow_session_scope_mismatch")
    if is_auth_enabled():
        if principal.provider == "none":
            raise PermissionError("workflow_session_principal_unavailable")
        expires = principal.raw_claims.get("exp")
        if expires is not None:
            try:
                expired = not math.isfinite(float(expires)) or float(expires) <= time.time()
            except (TypeError, ValueError):
                expired = True
            if expired:
                raise PermissionError("workflow_session_principal_expired")
    if (
        not connection.get("active")
        or getattr(websocket, "client_state", None) != WebSocketState.CONNECTED
        or getattr(websocket, "application_state", None) != WebSocketState.CONNECTED
    ):
        raise _ConnectionUnavailable("workflow_session_connection_unavailable")
    return connection, websocket, principal


def _principal_scope(principal: WebSocketUser) -> tuple[Any, ...]:
    return (
        principal.user_id, principal.app_id, principal.chat_id,
        principal.tenant_id, principal.workspace_id, tuple(principal.scopes),
        tuple(principal.roles), principal.provider,
    )


async def dispatch_workflow_module_action(
    module: str, action: str, params: dict[str, Any],
) -> ModuleResult:
    """Use the run's authenticated socket principal, never model-supplied identity.

    Background runs without an authenticated live connection fail closed. No
    bearer token, role, or dispatch authority is copied into workflow state.
    """
    from mozaiksai.core.transport.simple_transport import SimpleTransport

    run_identity = active_workflow_tool_run()
    transport = await SimpleTransport.get_instance()
    deadline = time.monotonic() + _RECONNECT_TIMEOUT_SECONDS
    while True:
        if active_workflow_tool_run() != run_identity:
            raise PermissionError("workflow_session_scope_mismatch")
        try:
            request, app = await _prepare_live_dispatch(
                module, action, params, transport=transport, run_identity=run_identity,
            )
            break
        except _ConnectionUnavailable:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            await asyncio.sleep(min(_RECONNECT_POLL_SECONDS, remaining))
    # Execution is outside the retry boundary: a started action is never replayed.
    return await dispatch_module_action(request, app=app)


async def _prepare_live_dispatch(
    module: str, action: str, params: dict[str, Any], *,
    transport: Any, run_identity: tuple[str, str, str, str],
) -> tuple[ModuleActionDispatchRequest, Any]:
    workflow_name, app_id, chat_id, user_id = run_identity
    connection, websocket, principal = _live_session(
        transport, app_id=app_id, chat_id=chat_id, user_id=user_id,
    )
    principal_scope = _principal_scope(principal)
    surfaces = getattr(websocket.app.state, "module_action_surfaces", {})
    module_surfaces = surfaces.get(module, {})
    # A present None value is the canonical authenticated action default. A
    # missing entry has no loaded contract and must never imply public access.
    if action not in module_surfaces or module_surfaces[action] not in {None, "public", "public_readonly"}:
        raise PermissionError("workflow_module_action_unavailable")
    if is_auth_enabled():
        permissions = list(principal.scopes)
    else:
        # The no-auth path-user binding supplies identity only. Resolve local
        # permissions from the configured adapter, as HTTP local mode does.
        permissions = list((await get_auth_adapter().validate_token("")).scopes)

    scope = await get_platform_hooks().call_module_scope(
        principal=principal, module_name=module, action_name=action,
        requested_scope={
            "app_id": app_id, "user_id": principal.user_id,
            "tenant_id": principal.tenant_id, "workspace_id": principal.workspace_id,
        },
        params=params, request=websocket, default_permissions=permissions,
    )
    # Hooks may narrow tenant/workspace membership; they cannot turn a workflow
    # dispatch into a different actor or app.
    if scope.get("app_id") != app_id or scope.get("user_id") != principal.user_id:
        raise PermissionError("workflow_session_scope_mismatch")
    # Recheck revocation after awaited provider hooks before dispatching.
    if active_workflow_tool_run() != run_identity:
        raise PermissionError("workflow_session_scope_mismatch")
    current_connection, current_websocket, current_principal = _live_session(
        transport, app_id=app_id, chat_id=chat_id, user_id=user_id,
    )
    if _principal_scope(principal) != principal_scope:
        raise PermissionError("workflow_session_principal_changed")
    if current_connection is not connection or current_websocket is not websocket:
        raise _ConnectionUnavailable("workflow_session_connection_changed")
    if current_principal is not principal:
        raise PermissionError("workflow_session_principal_changed")
    return (
        ModuleActionDispatchRequest(
            module=module, action=action, params=params,
            scope=ModuleDispatchScope(
                app_id=app_id, user_id=principal.user_id,
                tenant_id=scope.get("tenant_id"), workspace_id=scope.get("workspace_id"),
            ),
            authority=workflow_user_authority(
                actor_id=principal.user_id, permissions=tuple(scope.get("permissions") or []),
                workflow_name=workflow_name,
            ),
            provenance=ModuleDispatchProvenance(
                surface="workflow_tool", workflow_name=workflow_name, workflow_run_id=chat_id,
            ),
        ),
        websocket.app,
    )

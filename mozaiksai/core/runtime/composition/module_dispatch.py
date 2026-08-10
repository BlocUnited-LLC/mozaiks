from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from mozaiksai.core.runtime.composition.module_authority import (
    ModuleDispatchAuthority,
    ModuleDispatchProvenance,
)
from mozaiksai.core.runtime.composition.module_executor import ModuleRequest, ModuleResult

_MODULE_ACTION_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class ModuleDispatchScope:
    """App-local scope for a module action dispatch."""

    app_id: str
    user_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None


@dataclass(frozen=True)
class ModuleDispatchMetadata:
    """Request metadata preserved across app-local module dispatch."""

    auth_token: str | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class ModuleActionDispatchRequest:
    """Public request for dispatching an app-local module action."""

    module: str
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    scope: ModuleDispatchScope = field(default_factory=lambda: ModuleDispatchScope(app_id="default"))
    metadata: ModuleDispatchMetadata = field(default_factory=ModuleDispatchMetadata)
    granted_permissions: list[str] = field(default_factory=list)
    authority: ModuleDispatchAuthority | None = None
    provenance: ModuleDispatchProvenance | None = None


def _resolve_module_executor(app: Any | None) -> Any:
    if app is None:
        from mozaiksai.hosts.platform import app as platform_app

        app = platform_app

    registry = getattr(getattr(app, "state", None), "executor_registry", None)
    executor = getattr(registry, "module_executor", None)
    if executor is None:
        raise RuntimeError("Module runtime is not available.")
    return executor


def _validate_request(request: ModuleActionDispatchRequest) -> None:
    if not _MODULE_ACTION_RE.fullmatch(request.module):
        raise ValueError("Invalid module name")
    if not _MODULE_ACTION_RE.fullmatch(request.action):
        raise ValueError("Invalid action name")
    if not request.scope.app_id:
        raise ValueError("scope.app_id is required")
    if request.granted_permissions is None:  # type: ignore[unreachable]
        raise ValueError(
            "granted_permissions must be a concrete list. Trusted/internal authority "
            "dispatch is intentionally not exposed by this public facade."
        )
    if request.authority is not None:
        _validate_public_authority(request.authority)


def _validate_public_authority(authority: ModuleDispatchAuthority) -> None:
    if authority.permission_mode != "enforce":
        raise ValueError(
            "Public module dispatch authority must use permission_mode='enforce'. "
            "Trusted bypass is intentionally not exposed by this facade."
        )
    if authority.legacy_granted_permissions_none:
        raise ValueError("legacy_trusted dispatch cannot be requested through the public facade.")
    if authority.kind in {
        "legacy_permissions",
        "legacy_trusted",
        "framework_internal",
        "operator_internal",
        "local_development",
    }:
        raise ValueError(f"Authority kind {authority.kind!r} is not valid for public module dispatch.")


async def dispatch_module_action(
    request: ModuleActionDispatchRequest,
    *,
    app: Any | None = None,
) -> ModuleResult:
    """Dispatch an app-local module action without exposing executor registries.

    This facade preserves current permission and entitlement behavior for
    concrete permission lists. It intentionally does not provide a public path
    to the current ``granted_permissions=None`` trusted bypass.
    """

    _validate_request(request)
    executor = _resolve_module_executor(app)
    granted_permissions = list(request.granted_permissions)
    authority = request.authority or ModuleDispatchAuthority(
        kind="app_internal",
        permission_mode="enforce",
        reason="public app-local module dispatch facade",
        actor_id=request.scope.user_id,
        permissions=tuple(granted_permissions),
    )
    authority = ModuleDispatchAuthority(
        kind=authority.kind,
        permission_mode=authority.permission_mode,
        reason=authority.reason,
        actor_id=authority.actor_id or request.scope.user_id,
        permissions=tuple(granted_permissions),
        legacy_granted_permissions_none=False,
    )
    provenance = request.provenance or ModuleDispatchProvenance(
        surface="app_local_dispatch",
        correlation_id=request.metadata.correlation_id,
    )
    if provenance.correlation_id is None and request.metadata.correlation_id:
        provenance = ModuleDispatchProvenance(
            surface=provenance.surface,
            workflow_name=provenance.workflow_name,
            workflow_run_id=provenance.workflow_run_id,
            event_id=provenance.event_id,
            event_type=provenance.event_type,
            event_producer=provenance.event_producer,
            correlation_id=request.metadata.correlation_id,
            causation_id=provenance.causation_id,
            metadata=provenance.metadata,
        )
    module_request = ModuleRequest(
        module=request.module,
        action=request.action,
        params=dict(request.params or {}),
        app_id=request.scope.app_id,
        user_id=request.scope.user_id,
        tenant_id=request.scope.tenant_id,
        workspace_id=request.scope.workspace_id,
        auth_token=request.metadata.auth_token,
        correlation_id=request.metadata.correlation_id,
        granted_permissions=granted_permissions,
        authority=authority,
        provenance=provenance,
    )
    return await executor.execute(module_request, context=None)

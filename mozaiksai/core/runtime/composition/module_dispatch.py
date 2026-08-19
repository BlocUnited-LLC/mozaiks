from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, cast

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
    # Concrete caller-held permission ids. Always a list; this facade has no
    # trusted path, so an empty list simply means "no permissions held".
    permissions: list[str] = field(default_factory=list)
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
    if request.permissions is None:  # type: ignore[unreachable]
        raise ValueError(
            "permissions must be a concrete list. Trusted/internal authority "
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
    if authority.kind in {
        "framework_internal",
        "operator_internal",
        "local_development",
        "event_reaction",
    }:
        raise ValueError(f"Authority kind {authority.kind!r} is not valid for public module dispatch.")


async def dispatch_module_action(
    request: ModuleActionDispatchRequest,
    *,
    app: Any | None = None,
) -> ModuleResult:
    """Dispatch an app-local module action without exposing executor registries.

    This facade always dispatches with an enforce-mode authority built from the
    caller's concrete permission list. It intentionally provides no trusted
    bypass path of any kind.
    """

    _validate_request(request)
    executor = _resolve_module_executor(app)
    permissions = tuple(request.permissions)
    base = request.authority
    authority = ModuleDispatchAuthority(
        kind=base.kind if base is not None else "app_internal",
        permission_mode="enforce",
        reason=base.reason if base is not None else "public app-local module dispatch facade",
        actor_id=(base.actor_id if base is not None else None) or request.scope.user_id,
        permissions=permissions,
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
        authority=authority,
        provenance=provenance,
    )
    return cast(ModuleResult, await executor.execute(module_request, context=None))

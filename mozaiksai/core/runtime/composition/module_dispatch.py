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
    # Explicit dispatch authority. Required: the caller states who is
    # dispatching and which concrete permissions it holds. This facade accepts
    # only enforce-mode, public-safe authorities and passes them through to
    # ModuleExecutor unchanged.
    authority: ModuleDispatchAuthority = field(kw_only=True)
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

    The caller's explicit enforce-mode authority is validated and passed to
    ModuleExecutor exactly as supplied. This facade provides no trusted bypass
    path of any kind and never rewrites the caller's permission set.
    """

    _validate_request(request)
    executor = _resolve_module_executor(app)
    authority = request.authority
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

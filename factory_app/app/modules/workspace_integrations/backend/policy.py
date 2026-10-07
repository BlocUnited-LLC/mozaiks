from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mozaiksai.core.runtime.composition.module_context import ModuleContext

_DEVELOPMENT_WORKSPACE_ID = "demo-workspace"

# Registry mode controls whether secret presence is checked from the environment.
# In hosted multi-tenant deployments where secrets are not accessible per-tenant
# from os.environ, set MOZAIKS_INTEGRATIONS_REGISTRY_MODE=catalog_only to prevent
# false configured/missing readings.
_REGISTRY_MODE_ENV = "MOZAIKS_INTEGRATIONS_REGISTRY_MODE"
_MODE_CATALOG_ONLY = "catalog_only"


def get_registry_mode() -> str:
    return os.environ.get(_REGISTRY_MODE_ENV, "live").lower()


def is_catalog_only_mode() -> bool:
    return get_registry_mode() == _MODE_CATALOG_ONLY


def check_secret_presence(secret_names: list[str]) -> dict[str, bool]:
    """Return a mapping of secret name → whether it is set (non-empty) in the environment.

    Never returns secret values — only boolean presence.
    """
    if is_catalog_only_mode():
        return {name: False for name in secret_names}
    return {name: bool(os.environ.get(name, "").strip()) for name in secret_names}


def derive_status(required_secrets: list[str]) -> tuple[str, list[str]]:
    """Derive status and list of missing required secrets.

    Returns:
        (status, missing_secret_names)
        status is one of: "configured", "partial", "missing", "unknown"
    """
    if not required_secrets:
        # No secrets needed — always configured regardless of registry mode.
        return "configured", []

    if is_catalog_only_mode():
        return "unknown", []

    presence = check_secret_presence(required_secrets)
    missing = [name for name, present in presence.items() if not present]

    if not missing:
        return "configured", []
    if len(missing) < len(required_secrets):
        return "partial", missing
    return "missing", missing


def _is_local_development(ctx: ModuleContext) -> bool:
    authority = ctx.dispatch_authority
    return authority is not None and authority.kind == "local_development"


def _verified_workspace_id(ctx: ModuleContext) -> str | None:
    """The workspace the caller's validated credential or host-verified membership binds.

    ``ctx.workspace_id`` and ``ctx.tenant_id`` are requested dispatch scope,
    which an unbound credential may choose freely; they never qualify.
    """
    principal = ctx.persistence.principal if ctx.persistence is not None else None
    if principal is None or principal.source != "authenticated":
        return None
    return principal.workspace_id or None


def _assert_dispatch_scope(ctx: ModuleContext, verified_workspace: str) -> None:
    principal = ctx.persistence.principal if ctx.persistence is not None else None
    if ctx.workspace_id and str(ctx.workspace_id) != verified_workspace:
        raise PermissionError("The dispatch workspace is not the caller's verified workspace.")
    if ctx.tenant_id and (principal is None or str(ctx.tenant_id) != str(principal.tenant_id or "")):
        raise PermissionError("The dispatch tenant is not the caller's verified tenant.")


def connector_workspace_id(ctx: ModuleContext, requested: str | None = None) -> str:
    """Return the workspace a workspace connector action acts on.

    Local development (authentication off, development access) keeps its
    explicit selection: the requested workspace, then the dispatch workspace
    or tenant, then the demo workspace. Every other caller acts only on its
    verified workspace; a requested workspace must name that workspace.

    Raises PermissionError when the caller has no verified workspace or
    requests another one.
    """
    if _is_local_development(ctx):
        return str(requested or ctx.workspace_id or ctx.tenant_id or _DEVELOPMENT_WORKSPACE_ID)
    verified = _verified_workspace_id(ctx)
    if verified is None:
        raise PermissionError("Workspace connectors require a verified workspace.")
    _assert_dispatch_scope(ctx, verified)
    if requested not in (None, "") and str(requested) != verified:
        raise PermissionError("The requested workspace is not the caller's verified workspace.")
    return verified


def connector_overlay_workspace_id(ctx: ModuleContext) -> str | None:
    """Return the workspace whose connectors may overlay app integration needs.

    Like ``connector_workspace_id`` without a requested workspace, except a
    caller with no verified workspace gets no overlay instead of a refusal.
    """
    if _is_local_development(ctx):
        return str(ctx.workspace_id or ctx.tenant_id or _DEVELOPMENT_WORKSPACE_ID)
    verified = _verified_workspace_id(ctx)
    if verified:
        _assert_dispatch_scope(ctx, verified)
    return verified

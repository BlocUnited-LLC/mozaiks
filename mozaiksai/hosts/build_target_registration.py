"""In-process integration for registering the loaded app as a Factory target.

App Python and the platform host share a process. Dispatch metadata catches
incorrect wiring; it cannot authenticate arbitrary Python in that process.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, TypeAdapter

from mozaiksai.core.runtime.composition.module_context import ModuleContext
from mozaiksai.core.session.build_binding import BuildIdentity
from mozaiksai.core.workflow.paths import resolve_active_app_root

REGISTER_SELF_PERMISSION = "factory.build_target.register_self"
_build_identity = TypeAdapter(BuildIdentity)


class ExistingSelfBuildTarget(BaseModel):
    """Factory identity to link to an app-owned, scoped managed-app record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    build_registry_id: BuildIdentity
    target_app_id: BuildIdentity
    execution_app_id: BuildIdentity
    owner_user_id: str


def _app_registry_service() -> Any:
    from factory_app.app.modules.app_registry.backend.service import AppRegistryService

    return AppRegistryService()


def _loaded_host_identity() -> tuple[str, str | None]:
    platform_host = sys.modules.get("mozaiksai.hosts.platform")
    state = getattr(getattr(platform_host, "app", None), "state", None)
    if state is None or getattr(state, "startup_degraded", True):
        raise RuntimeError("A healthy loaded platform host is required for target registration")
    loaded_root = getattr(state, "loaded_app_root", None)
    if not isinstance(loaded_root, Path) or loaded_root != resolve_active_app_root().resolve():
        raise RuntimeError("The active app root no longer matches the loaded host")
    loaded_app_id = _build_identity.validate_python(getattr(state, "loaded_app_id", None))
    raw_name = getattr(state, "loaded_app_name", None)
    name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else None
    return loaded_app_id, name


def _consistent_dispatch_actor(ctx: ModuleContext) -> str:
    """Check the supplied dispatch facts for consistency, not provenance."""
    authority = ctx.dispatch_authority
    audit = ctx.dispatch_audit
    actor = str(ctx.user_id or "").strip()
    permission_check = audit.permission_check if audit is not None else None
    if (
        actor in {"", "anonymous", "system"}
        or authority is None
        or authority.kind != "app_internal"
        or authority.permission_mode != "enforce"
        or authority.actor_id != actor
        or REGISTER_SELF_PERMISSION not in authority.permissions
        or audit is None
        or audit.outcome != "allowed"
        or audit.authority_kind != "app_internal"
        or audit.permission_mode != "enforce"
        or audit.actor_id != actor
        or audit.app_id != ctx.app_id
        or audit.module != ctx.module_id
        or audit.action != ctx.action_id
        or permission_check is None
        or not permission_check.checked
        or not permission_check.allowed
        or REGISTER_SELF_PERMISSION not in permission_check.required_permissions
        or REGISTER_SELF_PERMISSION not in permission_check.granted
    ):
        raise PermissionError("Existing build-target registration requires an enforced operator action")
    return actor


async def register_existing_self_build_target(ctx: ModuleContext) -> ExistingSelfBuildTarget:
    """Insert or reopen the exact loaded-host Factory target after app authorization.

    Trusted app code must first verify its own durable owner, tenant, and
    workspace authority, then call through an app-internal ModuleExecutor
    action declaring ``factory.build_target.register_self``. The dispatch
    facts are constructible in-process and do not replace that owner check.
    There is no request-supplied target ID, source path, lifecycle state, or
    build pointer.
    """
    owner_user_id = _consistent_dispatch_actor(ctx)
    target_app_id, name = _loaded_host_identity()
    if ctx.app_id != target_app_id:
        raise ValueError("Execution app does not match the loaded host target")
    record = await _app_registry_service().register_existing_app_record(
        owner_user_id=owner_user_id,
        app_id=target_app_id,
        chat_app_id=target_app_id,
        name=name,
    )
    if (
        record.get("app_id") != target_app_id
        or record.get("chat_app_id") != target_app_id
        or record.get("owner_user_id") != owner_user_id
    ):
        raise RuntimeError("Registered Factory target does not match the loaded host binding")
    return ExistingSelfBuildTarget(
        build_registry_id=record["build_registry_id"],
        target_app_id=record["app_id"],
        execution_app_id=record["chat_app_id"],
        owner_user_id=record["owner_user_id"],
    )

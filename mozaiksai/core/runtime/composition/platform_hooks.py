from __future__ import annotations

"""Platform Hook Registry
======================
Allows proprietary platform layers (Mozaiks, or any deployer) to inject
behaviour into the core runtime at well-defined callsites WITHOUT modifying
the runtime host.

Open-source deployments:
    Leave RUNTIME_PLATFORM_EXTENSIONS unset.  All hook callers return safe
    no-op defaults and the runtime works as a clean AG2 runtime.

Platform deployments (e.g. Mozaiks):
    Set the env var to one or more comma-separated entrypoints:

        RUNTIME_PLATFORM_EXTENSIONS=mozaiksai.platform.extensions:get_bundle

    Each entrypoint is a ``module.path:attr`` string.  The attribute must be
    a callable returning a bundle dict (or object with the same keys), or
    directly be such a bundle.

Bundle keys (all optional):
    on_startup            async (app: FastAPI) -> None
        Mount extra routers, initialise managers, etc.  Called during FastAPI
        startup after persistence_manager and simple_transport are available on
        app.state.

    chat_prereqs          async (*, app_id, user_id, workflow_name, persistence)
                              -> Tuple[bool, Optional[str]]
        Gate check before creating a chat session.  First hook that returns
        (False, reason) wins.

    chat_session_fields   async (*, app_id, user_id, workflow_name, chat_id)
                              -> Dict[str, Any]
        Extra fields to merge into the chat session document at creation time
        (e.g. journey_id, journey_key).

    module_permission_resolver
                          async (*, principal, module_name, action_name,
                                  app_id, tenant_id, user_id, params,
                                  request, default_permissions)
                              -> Optional[List[str]]
        Optional host authorization bridge for module action dispatch.  Return
        a permission list to use for the module request, or None to keep the
        current/default permission list.  Hosts can map authenticated principals
        to app-local memberships without teaching OSS runtime about a specific
        tenant product.

    module_scope_resolver
                          async (*, principal, module_name, action_name,
                                  requested_scope, params, request,
                                  default_permissions)
                              -> Optional[Dict[str, Any]]
        Optional host scope bridge for module action dispatch. Return any of
        app_id, user_id, tenant_id, workspace_id, permissions after validating
        the requested scope against app-local memberships.

    workflow_ordering     (workflow_names: List[str]) -> List[str]
        Reorder the workflow list returned to the frontend (e.g. by journey
        step sequence).

    workflow_name_resolver (requested_workflow_name: str, workflow_names: List[str])
        -> Optional[str]
        Resolve stale or case-insensitive workflow names to a loaded workflow.

    on_account_delete_complete
                          async (*, app_id: str, user_id: str,
                                  deletion_results: Dict[str, Any]) -> None
        Called after all ``AccountDataHandler.delete_user_data`` handlers have
        run.  Use this to revoke auth provider sessions, remove OIDC accounts,
        emit a hosted audit event, cancel platform subscriptions, etc.
        Raised exceptions are logged as warnings and do not surface to the
        caller.

    on_account_export_ready
                          async (*, app_id: str, user_id: str,
                                  export_payload: Dict[str, Any]) -> Dict[str, Any]
        Called after all ``AccountDataHandler.export_user_data`` handlers have
        run.  Use this to inject platform-level records (auth identity, billing
        history, subscription snapshot) into the export payload before it is
        returned to the caller.  Must return the (possibly augmented) payload.
"""

import importlib
import inspect
import os
from collections.abc import Callable
from typing import Any

from logs.logging_config import get_workflow_logger

logger = get_workflow_logger("platform_hooks")


def _clean_optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


_BUNDLE_KEYS = (
    "on_startup",
    "chat_prereqs",
    "chat_session_fields",
    "module_permission_resolver",
    "module_scope_resolver",
    "workflow_ordering",
    "workflow_name_resolver",
)


def _resolve_entrypoint(entrypoint: str) -> Any:
    """Load ``module.path:attr`` and return the attribute."""
    if ":" not in entrypoint:
        raise ValueError(f"Invalid entrypoint (expected 'module:attr'): {entrypoint!r}")
    module_path, attr = entrypoint.split(":", 1)
    module = importlib.import_module(module_path.strip())
    obj = getattr(module, attr.strip(), None)
    if obj is None:
        raise ImportError(f"Attribute not found: {entrypoint!r}")
    return obj


class PlatformHookRegistry:
    """Singleton registry for optional platform-layer hooks.

    Loaded lazily on first ``get_instance()`` call from the
    ``RUNTIME_PLATFORM_EXTENSIONS`` environment variable.
    """

    _instance: PlatformHookRegistry | None = None

    def __init__(self) -> None:
        self._startup_hooks: list[Callable] = []
        self._chat_prereqs_hooks: list[Callable] = []
        self._chat_session_fields_hooks: list[Callable] = []
        self._module_permission_resolver_hooks: list[Callable] = []
        self._module_scope_resolver_hooks: list[Callable] = []
        self._workflow_ordering_hooks: list[Callable] = []
        self._workflow_name_resolver_hooks: list[Callable] = []
        self._loaded = False

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> PlatformHookRegistry:
        if cls._instance is None:
            cls._instance = cls()
            cls._instance._load()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (for testing)."""
        cls._instance = None

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True

        raw = os.getenv("RUNTIME_PLATFORM_EXTENSIONS", "").strip()
        if not raw:
            logger.debug(
                "PLATFORM_HOOKS: RUNTIME_PLATFORM_EXTENSIONS not set — "
                "running as open-source runtime (all hooks are no-ops)"
            )
            return

        for entry in raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            try:
                obj = _resolve_entrypoint(entry)
                bundle = obj() if callable(obj) else obj
                self._register_bundle(bundle, source=entry)
                logger.info("PLATFORM_HOOKS_LOADED: %s", entry)
            except Exception as exc:
                logger.warning("PLATFORM_HOOKS_LOAD_FAILED: %s — %s", entry, exc)

    def _register_bundle(self, bundle: Any, source: str = "") -> None:
        def _get(key: str) -> Any:
            if isinstance(bundle, dict):
                return bundle.get(key)
            return getattr(bundle, key, None)

        slot_map = {
            "on_startup": self._startup_hooks,
            "chat_prereqs": self._chat_prereqs_hooks,
            "chat_session_fields": self._chat_session_fields_hooks,
            "module_permission_resolver": self._module_permission_resolver_hooks,
            "module_scope_resolver": self._module_scope_resolver_hooks,
            "workflow_ordering": self._workflow_ordering_hooks,
            "workflow_name_resolver": self._workflow_name_resolver_hooks,
        }
        for key, target in slot_map.items():
            val = _get(key)
            if callable(val):
                target.append(val)
                logger.debug("PLATFORM_HOOKS_REGISTERED: %s from %s", key, source)

    # ------------------------------------------------------------------
    # Hook callers
    # ------------------------------------------------------------------

    async def run_startup(self, app: Any) -> None:
        """Run all platform startup handlers."""
        for hook in self._startup_hooks:
            try:
                res = hook(app)
                if inspect.isawaitable(res):
                    await res
            except Exception as exc:
                logger.warning("PLATFORM_HOOKS_STARTUP_ERROR: %s", exc)

        if self._startup_hooks:
            logger.info("PLATFORM_HOOKS: ran %s startup hook(s)", len(self._startup_hooks))

    async def call_chat_prereqs(
        self,
        app_id: str,
        user_id: str,
        workflow_name: str,
        persistence: Any,
    ) -> tuple[bool, str | None]:
        """Gate check before creating/resuming a chat session."""
        for hook in self._chat_prereqs_hooks:
            try:
                res = hook(
                    app_id=app_id,
                    user_id=user_id,
                    workflow_name=workflow_name,
                    persistence=persistence,
                )
                if inspect.isawaitable(res):
                    res = await res
                if isinstance(res, tuple) and len(res) == 2:
                    ok, reason = res
                    if not ok:
                        return False, str(reason) if reason else "Prerequisite not met"
            except Exception as exc:
                logger.warning("PLATFORM_HOOKS_PREREQS_ERROR: %s", exc)
        return True, None

    async def call_chat_session_fields(
        self,
        app_id: str,
        user_id: str,
        workflow_name: str,
        chat_id: str,
    ) -> dict[str, Any]:
        """Collect extra fields to inject into the chat session document."""
        extra: dict[str, Any] = {}
        for hook in self._chat_session_fields_hooks:
            try:
                res = hook(
                    app_id=app_id,
                    user_id=user_id,
                    workflow_name=workflow_name,
                    chat_id=chat_id,
                )
                if inspect.isawaitable(res):
                    res = await res
                if isinstance(res, dict):
                    extra.update(res)
            except Exception as exc:
                logger.warning("PLATFORM_HOOKS_SESSION_FIELDS_ERROR: %s", exc)
        return extra

    async def call_module_permissions(
        self,
        *,
        principal: Any,
        module_name: str,
        action_name: str,
        app_id: str,
        tenant_id: str | None,
        user_id: str | None,
        params: dict[str, Any],
        request: Any = None,
        default_permissions: list[str] | None = None,
    ) -> list[str] | None:
        """Resolve module permissions through optional host hooks.

        ``None`` retains the trusted/internal bypass semantics used by
        ModuleExecutor.  A hook may return an empty list to require enforcement
        with no permissions granted.
        """

        current = list(default_permissions) if default_permissions is not None else None
        for hook in self._module_permission_resolver_hooks:
            try:
                res = hook(
                    principal=principal,
                    module_name=module_name,
                    action_name=action_name,
                    app_id=app_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    params=dict(params or {}),
                    request=request,
                    default_permissions=list(current) if current is not None else None,
                )
                if inspect.isawaitable(res):
                    res = await res
                if res is None:
                    continue
                if isinstance(res, (list, tuple, set)):
                    current = [str(item) for item in res if str(item).strip()]
            except Exception as exc:
                logger.warning("PLATFORM_HOOKS_MODULE_PERMISSIONS_ERROR: %s", exc)
        return current

    async def call_module_scope(
        self,
        *,
        principal: Any,
        module_name: str,
        action_name: str,
        requested_scope: dict[str, Any],
        params: dict[str, Any],
        request: Any = None,
        default_permissions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Resolve canonical module dispatch scope through optional host hooks."""

        app_id = str(requested_scope.get("app_id") or "")
        user_id = _clean_optional(requested_scope.get("user_id"))
        tenant_id = _clean_optional(requested_scope.get("tenant_id"))
        workspace_id = _clean_optional(requested_scope.get("workspace_id"))
        permissions: list[str] = list(default_permissions) if default_permissions is not None else []

        for hook in self._module_scope_resolver_hooks:
            try:
                res = hook(
                    principal=principal,
                    module_name=module_name,
                    action_name=action_name,
                    requested_scope={
                        "app_id": app_id,
                        "user_id": user_id,
                        "tenant_id": tenant_id,
                        "workspace_id": workspace_id,
                        "permissions": list(permissions),
                    },
                    params=dict(params or {}),
                    request=request,
                    default_permissions=list(permissions),
                )
                if inspect.isawaitable(res):
                    res = await res
                if isinstance(res, dict):
                    if "app_id" in res:
                        app_id = str(res.get("app_id") or "")
                    if "user_id" in res:
                        user_id = _clean_optional(res.get("user_id"))
                    if "tenant_id" in res:
                        tenant_id = _clean_optional(res.get("tenant_id"))
                    if "workspace_id" in res:
                        workspace_id = _clean_optional(res.get("workspace_id"))
                    raw_permissions = res.get("permissions")
                    if isinstance(raw_permissions, (list, tuple, set)):
                        permissions = [str(item) for item in raw_permissions if str(item).strip()]
            except Exception as exc:
                logger.warning("PLATFORM_HOOKS_MODULE_SCOPE_ERROR: %s", exc)

        permissions = await self.call_module_permissions(
            principal=principal,
            module_name=module_name,
            action_name=action_name,
            app_id=app_id,
            tenant_id=tenant_id,
            user_id=user_id,
            params=params,
            request=request,
            default_permissions=list(permissions),
        ) or []
        return {
            "app_id": app_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "permissions": permissions,
        }

    def call_workflow_ordering(self, workflow_names: list[str]) -> list[str]:
        """Reorder the workflow list for frontend display."""
        result = list(workflow_names)
        for hook in self._workflow_ordering_hooks:
            try:
                ordered = hook(result)
                if isinstance(ordered, list):
                    result = ordered
            except Exception as exc:
                logger.warning("PLATFORM_HOOKS_ORDERING_ERROR: %s", exc)
        return result

    def call_workflow_name_resolver(
        self,
        requested_workflow_name: str,
        workflow_names: list[str],
    ) -> str | None:
        """Resolve a requested workflow name against loaded workflow names."""
        names = list(workflow_names or [])
        for hook in self._workflow_name_resolver_hooks:
            try:
                resolved = hook(requested_workflow_name, names)
                if isinstance(resolved, str) and resolved.strip():
                    return resolved.strip()
            except Exception as exc:
                logger.warning("PLATFORM_HOOKS_WORKFLOW_NAME_RESOLVER_ERROR: %s", exc)

        requested = str(requested_workflow_name or "").strip()
        if not requested:
            return None
        for name in names:
            if str(name).lower() == requested.lower():
                return str(name)
        return None

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def has_prereqs(self) -> bool:
        return bool(self._chat_prereqs_hooks)

    @property
    def has_session_fields(self) -> bool:
        return bool(self._chat_session_fields_hooks)

    @property
    def has_module_permission_resolver(self) -> bool:
        return bool(self._module_permission_resolver_hooks)

    @property
    def has_module_scope_resolver(self) -> bool:
        return bool(self._module_scope_resolver_hooks)

    @property
    def has_startup(self) -> bool:
        return bool(self._startup_hooks)

    def summary(self) -> dict[str, Any]:
        return {
            "startup_hooks": len(self._startup_hooks),
            "chat_prereqs_hooks": len(self._chat_prereqs_hooks),
            "chat_session_fields_hooks": len(self._chat_session_fields_hooks),
            "module_permission_resolver_hooks": len(self._module_permission_resolver_hooks),
            "module_scope_resolver_hooks": len(self._module_scope_resolver_hooks),
            "workflow_ordering_hooks": len(self._workflow_ordering_hooks),
            "workflow_name_resolver_hooks": len(self._workflow_name_resolver_hooks),
        }


def get_platform_hooks() -> PlatformHookRegistry:
    """Return the singleton PlatformHookRegistry, loading env extensions on first call."""
    return PlatformHookRegistry.get_instance()

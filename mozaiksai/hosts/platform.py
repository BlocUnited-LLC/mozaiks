from __future__ import annotations

"""Platform composition host layered on top of mozaiksai.hosts.runtime."""

import asyncio
import inspect
import json
import os
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from logs.logging_config import get_workflow_logger
from mozaiksai.core.admin.registry import build_admin_shell_routes, load_admin_registry
from mozaiksai.core.auth import (
    WS_CLOSE_POLICY_VIOLATION,
    UserPrincipal,
    authenticate_websocket_with_path_binding,
    require_any_auth,
    require_user_scope,
)
from mozaiksai.core.auth.dependencies import (
    resolve_scope_from_principal,
    validate_path_app_id,
    validate_path_id,
)
from mozaiksai.core.auth.dependencies import (
    validate_user_id_against_principal as _validate_user_id_against_principal,
)
from mozaiksai.core.multitenant import build_app_scope_filter
from mozaiksai.core.ports.entitlement import EntitlementPort
from mozaiksai.core.profile.discovery import load_profile_panels, load_profile_tabs
from mozaiksai.core.relationships.discovery import load_relationship_providers
from mozaiksai.core.runtime.app.ai_config import resolve_runtime_ai_config
from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter
from mozaiksai.core.runtime.app.loader import AppLoader, AppLoadError
from mozaiksai.core.runtime.app.module_loader import ModuleLoadError
from mozaiksai.core.runtime.composition.executor_registry import ExecutorRegistry
from mozaiksai.core.runtime.composition.extensions import (
    mount_module_routers,
    start_module_services,
    stop_services,
)
from mozaiksai.core.runtime.composition.module_event_router import ModuleEventRouter
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks
from mozaiksai.core.runtime.persistence import (
    DatabaseStartupPolicyError,
    apply_data_migrations,
    apply_database_indexes,
    get_database_startup_policy,
    load_data_migrations,
)
from mozaiksai.core.session.launcher import (
    create_routed_chat_session,
    validate_context_for_workflow,
)
from mozaiksai.core.workflow.paths import candidate_app_workflows_roots, resolve_active_app_root
from mozaiksai.hosts import runtime as runtime_app
from mozaiksai.resources import resolve_factory_app_root
from mozaiksai.version import __version__ as _API_VERSION

app = runtime_app.app
persistence_manager = runtime_app.persistence_manager
logger = get_workflow_logger("platform_app")

from mozaiksai.core.ports.collaboration import NoOpCollaborationAdapter  # noqa: E402

executor_registry = ExecutorRegistry()
app.state.executor_registry = executor_registry
app.state.subscriptions_config = None
app.state.startup_degraded = False
app.state.startup_degraded_reason: str | None = None
app.state.failed_module_names: list[str] = []
app.state.collaboration = NoOpCollaborationAdapter()
_runtime_services: list[Any] = []


class DatabaseStartupError(RuntimeError):
    """Raised when required generated-app database startup work fails."""


@app.middleware("http")
async def add_api_version_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-API-Version"] = _API_VERSION
    return response


_DEFAULT_PROFILE_USER_ID = os.getenv("MOZAIKS_DEFAULT_USER_ID", "demo-user").strip() or "demo-user"
_ACCOUNT_PROFILE_COLLECTION = "UserProfiles"
_ACCOUNT_PREFERENCES_COLLECTION = "UserPreferences"


# Module runtime_extensions.yaml routers are mounted in _platform_startup()
# after ModuleLoader registers module packages in sys.modules.
# mount_declared_routers is retained for workspace-level (non-module) extensions.

try:
    from mozaiksai.core.admin.router import router as admin_router

    app.include_router(admin_router)
except Exception as exc:  # pragma: no cover
    logger.debug("ADMIN_ROUTER_MOUNT_FAILED: %s", exc)

# Router modules extracted from platform.py for code organization.
from mozaiksai.hosts.routers.account import router as _account_router  # noqa: E402
from mozaiksai.hosts.routers.chat import router as _chat_router  # noqa: E402
from mozaiksai.hosts.routers.modules import router as _modules_router  # noqa: E402
from mozaiksai.hosts.routers.notifications import router as _notifications_router  # noqa: E402
from mozaiksai.hosts.routers.sessions import router as _sessions_router  # noqa: E402
from mozaiksai.hosts.routers.shell import router as _shell_router  # noqa: E402
from mozaiksai.hosts.routers.transitions import router as _transitions_router  # noqa: E402
from mozaiksai.hosts.routers.workflows import router as _workflows_router  # noqa: E402

app.include_router(_account_router)
app.include_router(_modules_router)
app.include_router(_notifications_router)
app.include_router(_shell_router)
app.include_router(_chat_router)
app.include_router(_sessions_router)
app.include_router(_transitions_router)
app.include_router(_workflows_router)


def resolve_app_root() -> Path:
    return resolve_active_app_root()


def _load_entitlement_adapter(config: Any) -> EntitlementPort:
    """Return the configured subscription entitlement adapter."""
    adapter = ConfiguredEntitlementAdapter(config=config)
    logger.info("ENTITLEMENT_ADAPTER_READY: configured subscriptions adapter wired")
    return adapter  # type: ignore[return-value]


_NON_RUNNABLE_WORKFLOW_IDS = {"extended_orchestration"}


def _get_ordered_workflow_names() -> list[str]:
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    return get_platform_hooks().call_workflow_ordering(sorted(workflow_manager.get_all_workflow_names()))


def _get_configured_entry_point() -> str | None:
    app_root = resolve_app_root()
    ai_path = app_root / "config" / "ai.json"

    try:
        ai = json.loads(ai_path.read_text(encoding="utf-8")) if ai_path.exists() else {}
        ai = resolve_runtime_ai_config(ai, app_root=app_root)
        candidate = ((ai.get("workflows") or {}).get("entry_point") or "").strip()
        return candidate or None
    except Exception:
        return None


def _is_runnable_workflow_name(workflow_name: str | None, ordered_names: list[str] | None = None) -> bool:
    name = str(workflow_name or "").strip()
    if not name:
        return False
    if name in _NON_RUNNABLE_WORKFLOW_IDS:
        return False
    names = ordered_names if ordered_names is not None else _get_ordered_workflow_names()
    return any(name.lower() == loaded.lower() for loaded in names)


def _resolve_requested_workflow_name(requested_workflow_name: str | None) -> str:
    ordered_names = _get_ordered_workflow_names()
    if not ordered_names:
        raise HTTPException(status_code=503, detail="No runnable workflows are currently loaded.")

    requested = str(requested_workflow_name or "").strip()
    if requested and requested not in _NON_RUNNABLE_WORKFLOW_IDS:
        for loaded in ordered_names:
            if loaded.lower() == requested.lower():
                return loaded

    entry_point = _get_configured_entry_point()
    if entry_point:
        for loaded in ordered_names:
            if loaded.lower() == entry_point.lower():
                return loaded

    return ordered_names[0]


async def _platform_startup() -> None:
    """Initialize platform/app-shell composition after runtime startup."""
    global _runtime_services

    app_root = resolve_app_root()
    database_startup_policy = get_database_startup_policy()
    logger.info("DATABASE_STARTUP_POLICY: policy=%s app_root=%s", database_startup_policy, app_root)
    try:
        load_result = await AppLoader.load(str(app_root))
        app.state.subscriptions_config = load_result.subscriptions_config
        if load_result.data_contract:
            index_app_id = (
                load_result.data_contract.get("app_id")
                or load_result.definition.config.get("appId")
                or load_result.definition.config.get("app_id")
                or _resolve_default_app_id()
            )
            try:
                index_count = await apply_database_indexes(load_result.data_contract, app_id=str(index_app_id))
                if index_count:
                    logger.info("DATABASE_INDEXES_READY: app_id=%s count=%s", index_app_id, index_count)
            except Exception as exc:
                logger.warning(
                    "DATABASE_INDEXES_NOT_APPLIED: policy=%s app_id=%s app_root=%s error=%s",
                    database_startup_policy,
                    index_app_id,
                    app_root,
                    exc,
                )
                if database_startup_policy == "required":
                    raise DatabaseStartupError(
                        f"Database indexes were not applied for app_id={index_app_id!r} "
                        f"at app_root={str(app_root)!r}: {exc}"
                    ) from exc
        try:
            migrations = load_data_migrations(app_root)
            if migrations:
                migration_app_id = (
                    (load_result.data_contract or {}).get("app_id")
                    or load_result.definition.config.get("appId")
                    or load_result.definition.config.get("app_id")
                    or _resolve_default_app_id()
                )
                migration_count = await apply_data_migrations(
                    app_id=str(migration_app_id),
                    migrations=migrations,
                )
                if migration_count:
                    logger.info(
                        "data_migrations_APPLIED: app_id=%s count=%s migrations=%s",
                        migration_app_id,
                        migration_count,
                        [str(migration.get("migration_id") or "") for migration in migrations],
                    )
        except Exception as exc:
            failed_migration_ids = [
                str(migration.get("migration_id") or "")
                for migration in locals().get("migrations", [])
                if isinstance(migration, dict)
            ]
            logger.warning(
                "data_migrations_NOT_APPLIED: policy=%s app_id=%s app_root=%s migrations=%s error=%s",
                database_startup_policy,
                locals().get("migration_app_id", _resolve_default_app_id()),
                app_root,
                failed_migration_ids,
                exc,
            )
            if database_startup_policy == "required":
                raise DatabaseStartupError(
                    f"Data migrations were not applied for app_root={str(app_root)!r} "
                    f"migrations={failed_migration_ids!r}: {exc}"
                ) from exc
        if load_result.modules:
            from mozaiksai.core.events import get_event_dispatcher

            dispatcher = get_event_dispatcher()
            workflow_capability_routes = _load_workflow_capability_routes(app_root)
            app.state.workflow_capability_routes = workflow_capability_routes

            async def invoke_capability(
                capability_id: str,
                source_event: dict[str, Any],
                subscription: dict[str, Any],
            ) -> dict[str, Any]:
                return await _invoke_workflow_capability(
                    capability_id=capability_id,
                    source_event=source_event,
                    subscription=subscription,
                    routes=workflow_capability_routes,
                    event_emitter=dispatcher.emit,
                )

            module_event_router = ModuleEventRouter(
                load_result.modules,
                event_emitter=dispatcher.emit,
                capability_invoker=invoke_capability,
            )
            module_event_router.register(dispatcher)
            app.state.module_event_router = module_event_router

            entitlement_checker: EntitlementPort | None = None
            if load_result.subscriptions_config is not None:
                entitlement_checker = _load_entitlement_adapter(
                    config=load_result.subscriptions_config,
                )

            module_executor = ModuleExecutor(
                event_emitter=dispatcher.emit,
                entitlement_checker=entitlement_checker,
            )
            module_action_surfaces: dict[str, dict[str, str | None]] = {}
            for loaded_module in load_result.modules:
                module_action_surfaces[loaded_module.name] = loaded_module.action_api_surface_map
                module_executor.register(
                    loaded_module.name,
                    loaded_module.handler,
                    action_method_map=loaded_module.action_method_map,
                    settings=(
                        loaded_module.manifests.settings.settings
                        if loaded_module.manifests.settings is not None
                        else None
                    ),
                    action_permissions=loaded_module.action_permissions_map,
                    action_schemas=loaded_module.action_schemas_map,
                    action_entitlements=loaded_module.action_entitlement_map,
                )
            executor_registry.register(module_executor)
            app.state.module_action_surfaces = module_action_surfaces
            logger.info("MODULE_EXECUTOR_READY: %s module(s)", len(load_result.modules))

            if load_result.failed_module_names:
                failed_names = sorted(load_result.failed_module_names)
                reason = f"MODULE_LOAD_PARTIAL: {len(failed_names)} module(s) failed to load"
                logger.error("PLATFORM_DEGRADED: %s — %s", reason, ", ".join(failed_names))
                app.state.startup_degraded = True
                app.state.startup_degraded_reason = reason
                app.state.failed_module_names = failed_names

            # Mount api_router extensions and start startup_service extensions
            # now that module packages are registered in sys.modules.
            try:
                n = mount_module_routers(app, load_result.modules)
                if n:
                    logger.info("MODULE_EXTENSIONS_ROUTERS_MOUNTED: %s router(s)", n)
            except Exception as exc:
                logger.error("MODULE_EXTENSIONS_ROUTER_MOUNT_FAILED: %s", exc)
                if not app.state.startup_degraded:
                    app.state.startup_degraded = True
                    app.state.startup_degraded_reason = "MODULE_EXTENSIONS_ROUTER_MOUNT_FAILED"

            try:
                module_services = await start_module_services(load_result.modules)
                _runtime_services.extend(module_services)
            except Exception as exc:
                logger.error("MODULE_EXTENSIONS_SERVICES_NOT_STARTED: %s", exc)
                if not app.state.startup_degraded:
                    app.state.startup_degraded = True
                    app.state.startup_degraded_reason = "MODULE_EXTENSIONS_SERVICES_NOT_STARTED"

    except DatabaseStartupError:
        raise
    except DatabaseStartupPolicyError:
        raise
    except AppLoadError:
        logger.debug("APP_LOAD_SKIPPED: app.json not found for platform host")
    except ModuleLoadError as exc:
        # A module contract is invalid — platform starts in degraded state so
        # health checks can surface this rather than hiding it as a warning.
        logger.error("APP_LOAD_FAILED_DEGRADED (ModuleLoadError): %s", exc)
        app.state.startup_degraded = True
        app.state.startup_degraded_reason = "MODULE_LOAD_ERROR"
    except Exception as exc:
        # Unexpected error during app/module setup. Mark degraded so health
        # checks report the problem; do not swallow silently.
        logger.error("APP_LOAD_FAILED_DEGRADED (%s): %s", type(exc).__name__, exc)
        app.state.startup_degraded = True
        app.state.startup_degraded_reason = "STARTUP_ERROR"

    try:
        await get_platform_hooks().run_startup(app)
    except Exception as exc:
        logger.warning("PLATFORM_HOOKS_STARTUP_FAILED: %s", exc)


async def _platform_shutdown() -> None:
    global _runtime_services
    if not _runtime_services:
        return
    try:
        await stop_services(_runtime_services)
    except Exception as _shutdown_exc:
        logger.warning("PLATFORM_RUNTIME_SERVICES_STOP_FAILED: %s", _shutdown_exc)
    _runtime_services = []


@asynccontextmanager
async def platform_lifespan(_: FastAPI) -> AsyncIterator[None]:
    await _platform_startup()
    try:
        yield
    finally:
        await _platform_shutdown()


runtime_app.register_app_lifespan(app, platform_lifespan)


PROFILE_SHELL_ROUTE = {
    "path": "/me",
    "component": "ProfilePage",
    "label": "Profile",
    "order": 998,
    "title": "Profile",
    "shellMode": "social",
}


def _append_page_once(pages: list[dict], page: dict) -> None:
    path = page.get("path")
    if not isinstance(path, str) or any(existing.get("path") == path for existing in pages):
        return
    pages.append(page)


def _normalize_shell_surface(surface: str | None) -> str:
    candidate = str(surface or "platform").strip().lower()
    return candidate if candidate in {"platform", "studio"} else "platform"


def _page_targets_surface(page: dict, *, surface: str) -> bool:
    meta = page.get("meta") if isinstance(page.get("meta"), dict) else {}
    declared_surfaces = meta.get("surfaces", meta.get("surface"))

    normalized_surfaces: list[str] = []
    if isinstance(declared_surfaces, str):
        value = declared_surfaces.strip().lower()
        if value:
            normalized_surfaces.append(value)
    elif isinstance(declared_surfaces, list):
        for item in declared_surfaces:
            if isinstance(item, str) and item.strip():
                normalized_surfaces.append(item.strip().lower())

    if not normalized_surfaces:
        return True
    return surface in normalized_surfaces


def _title_from_id(value: str) -> str:
    return " ".join(part.capitalize() for part in re.split(r"[-_]+", value) if part)


_DEFAULT_NAVIGATION_POLICY: dict[str, Any] = {
    "desktop": {"global": "header", "local": "sidebar", "footer": "visible"},
    "mobile": {"global": "bottomBar", "local": "sheet", "footer": "hidden"},
    "maxMobileItems": 5,
    "autoFromPages": False,
}

_SHELL_MODE_VALUES = {"standard", "workspace", "conversation", "focused", "immersive", "public"}

_DEFAULT_CHROME_POLICY: dict[str, Any] = {
    "defaultMode": "standard",
    "modes": {
        "standard": {
            "desktop": {"header": True, "footer": True, "bottomBar": False, "localNav": True},
            "mobile": {"header": True, "footer": False, "bottomBar": True, "localNav": "sheet"},
        },
        "workspace": {
            "desktop": {"header": True, "footer": False, "bottomBar": False, "localNav": True},
            "mobile": {"header": True, "footer": False, "bottomBar": True, "localNav": "sheet"},
        },
        "conversation": {
            "desktop": {"header": True, "footer": False, "bottomBar": False, "localNav": False},
            "mobile": {"header": True, "footer": False, "bottomBar": False, "localNav": False},
        },
        "focused": {
            "desktop": {"header": True, "footer": False, "bottomBar": False, "localNav": False},
            "mobile": {"header": True, "footer": False, "bottomBar": False, "localNav": False},
        },
        "immersive": {
            "desktop": {"header": False, "footer": False, "bottomBar": False, "localNav": False},
            "mobile": {"header": False, "footer": False, "bottomBar": False, "localNav": False},
        },
        "public": {
            "desktop": {"header": True, "footer": True, "bottomBar": False, "localNav": False},
            "mobile": {"header": True, "footer": False, "bottomBar": False, "localNav": False},
        },
    },
}

_NAVIGATION_ITEM_FIELDS = {
    "id",
    "label",
    "action",
    "path",
    "href",
    "icon",
    "iconLabel",
    "requiresRole",
    "visible",
    "order",
    "scope",
    "group",
    "placement",
}


def _clean_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _normalize_shell_mode(value: Any) -> str | None:
    mode = _clean_string(value)
    if not mode:
        return None
    normalized = mode.replace("_", "-").lower()
    return normalized if normalized in _SHELL_MODE_VALUES else None


def _normalize_chrome_viewport_policy(raw: Any, defaults: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return dict(defaults)

    result = dict(defaults)
    for field in ("header", "footer", "bottomBar", "localNav"):
        value = raw.get(field)
        if value is None:
            continue
        if field == "localNav":
            if isinstance(value, bool):
                result[field] = value
            elif isinstance(value, str) and value.strip():
                result[field] = value.strip()
            continue
        if isinstance(value, bool):
            result[field] = value
    return result


def _normalize_chrome_mode_policy(raw: Any, defaults: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return deepcopy(defaults)

    desktop_defaults = defaults.get("desktop", {})
    mobile_defaults = defaults.get("mobile", {})
    shared = {
        key: raw[key]
        for key in ("header", "footer", "bottomBar", "localNav")
        if key in raw
    }
    desktop_raw = {**shared, **(raw.get("desktop") if isinstance(raw.get("desktop"), dict) else {})}
    mobile_raw = {**shared, **(raw.get("mobile") if isinstance(raw.get("mobile"), dict) else {})}
    return {
        "desktop": _normalize_chrome_viewport_policy(desktop_raw, desktop_defaults),
        "mobile": _normalize_chrome_viewport_policy(mobile_raw, mobile_defaults),
    }


def _normalize_chrome_policy(chrome: Any) -> dict[str, Any]:
    policy = deepcopy(_DEFAULT_CHROME_POLICY)
    if not isinstance(chrome, dict):
        return policy

    default_mode = _normalize_shell_mode(chrome.get("defaultMode"))
    if default_mode:
        policy["defaultMode"] = default_mode

    raw_modes = chrome.get("modes") if isinstance(chrome.get("modes"), dict) else {}
    for mode, default_mode_policy in list(policy["modes"].items()):
        raw_mode = raw_modes.get(mode)
        if raw_mode is None:
            raw_mode = chrome.get(mode)
        policy["modes"][mode] = _normalize_chrome_mode_policy(raw_mode, default_mode_policy)
    return policy


def _shell_mode_from_entry(entry: dict[str, Any]) -> str | None:
    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    return (
        _normalize_shell_mode(entry.get("shellMode"))
        or _normalize_shell_mode(entry.get("shell_mode"))
        or _normalize_shell_mode(meta.get("shellMode"))
        or _normalize_shell_mode(meta.get("shell_mode"))
    )


def _route_item_from_page(page: dict) -> dict[str, Any] | None:
    path = page.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        return None
    meta = page.get("meta") if isinstance(page.get("meta"), dict) else {}
    nav = meta.get("navigation") if isinstance(meta.get("navigation"), dict) else {}
    item_id = (
        _clean_string(nav.get("id"))
        or _clean_string(page.get("id"))
        or _clean_string(page.get("schema"))
        or path.strip("/").replace("/", "-")
    )
    label = _clean_string(nav.get("label")) or _clean_string(page.get("label")) or _title_from_id(item_id)
    item: dict[str, Any] = {
        "id": item_id,
        "label": label,
        "action": "navigate",
        "path": path,
    }
    if isinstance(page.get("order"), int):
        item["order"] = page["order"]
    requires_role = nav.get("requiresRole") or meta.get("requiresRole")
    if isinstance(requires_role, str) and requires_role.strip():
        item["requiresRole"] = requires_role.strip()
    for field in ("icon", "iconLabel", "scope", "group", "visible", "placement"):
        if field in nav:
            item[field] = nav[field]
    return item


def _shell_shortcut_catalog(pages: list[dict], shortcuts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {
        "home": {"id": "home", "label": "Home", "action": "navigate", "path": "/"},
        "apps": {"id": "apps", "label": "Apps", "action": "navigate", "path": "/apps"},
        "workspace": {"id": "workspace", "label": "Workspace", "action": "navigate", "path": "/apps"},
        "profile": {"id": "profile", "label": "Profile", "action": "navigate", "path": "/me"},
        "account": {"id": "profile", "label": "Profile", "action": "navigate", "path": "/me"},
        "settings": {"id": "settings", "label": "Settings", "action": "navigate", "path": "/settings"},
        "notifications": {"id": "notifications", "label": "Alerts", "action": "navigate", "path": "/notifications"},
        "marketplace": {"id": "marketplace", "label": "Marketplace", "action": "navigate", "path": "/marketplace"},
        "wallet": {"id": "wallet", "label": "Wallet", "action": "navigate", "path": "/wallet"},
        "create": {"id": "create", "label": "Create", "action": "navigate", "path": "/create?new=1"},
        "admin": {"id": "admin", "label": "Admin", "action": "navigate", "path": "/admin", "requiresRole": "admin"},
        "support": {"id": "support", "label": "Support", "action": "navigate", "path": "/me?tab=support-tickets"},
        "signin": {"id": "signin", "label": "Sign In", "action": "signin"},
        "signout": {"id": "signout", "label": "Sign Out", "action": "signout"},
        "legal": {"id": "legal", "label": "Legal Notice", "href": "/legal"},
        "terms": {"id": "terms", "label": "Terms of Service", "href": "/terms"},
        "cookies": {"id": "cookies", "label": "Cookie Policy", "href": "/cookies"},
        "privacy": {"id": "privacy", "label": "Privacy Policy", "href": "/privacy"},
    }

    for page in pages:
        if not isinstance(page, dict):
            continue
        item = _route_item_from_page(page)
        if not item:
            continue
        catalog[item["id"]] = item
        path_key = str(item["path"]).strip("/").replace("/", "-")
        if path_key:
            catalog.setdefault(path_key, item)

    return catalog


def _shortcut_ids(shortcuts: dict[str, Any], key: str) -> list[str]:
    value = shortcuts.get(key)
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []


def _expand_shortcut_items(ids: list[str], catalog: dict[str, dict[str, Any]], *, footer: bool = False) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item_id in ids:
        item = catalog.get(item_id)
        if not item:
            continue
        output = dict(item)
        key = str(output.get("href") or output.get("path") or output.get("id") or item_id)
        if key in seen:
            continue
        seen.add(key)
        if footer:
            href = output.get("href") or output.get("path")
            if not isinstance(href, str) or not href:
                continue
            expanded.append({"label": output.get("label") or _title_from_id(item_id), "href": href})
            continue
        if output.get("href") and not output.get("action"):
            output["action"] = "navigate"
        expanded.append(output)
    return expanded


def _normalize_navigation_policy(navigation: Any) -> dict[str, Any]:
    if not isinstance(navigation, dict):
        return dict(_DEFAULT_NAVIGATION_POLICY)

    raw_policy = navigation.get("policy") if isinstance(navigation.get("policy"), dict) else navigation

    def viewport_policy(name: str) -> dict[str, str]:
        defaults = _DEFAULT_NAVIGATION_POLICY[name]
        raw = raw_policy.get(name) if isinstance(raw_policy.get(name), dict) else {}
        return {
            "global": _clean_string(raw.get("global")) or defaults["global"],
            "local": _clean_string(raw.get("local")) or defaults["local"],
            "footer": _clean_string(raw.get("footer")) or defaults["footer"],
        }

    try:
        max_mobile = int(raw_policy.get("maxMobileItems") or _DEFAULT_NAVIGATION_POLICY["maxMobileItems"])
    except Exception:
        max_mobile = int(_DEFAULT_NAVIGATION_POLICY["maxMobileItems"])

    return {
        "desktop": viewport_policy("desktop"),
        "mobile": viewport_policy("mobile"),
        "maxMobileItems": max(1, min(max_mobile, 5)),
        "autoFromPages": bool(raw_policy.get("autoFromPages") or False),
    }


def _navigation_config_from_page(page: dict) -> dict[str, Any] | None:
    if not isinstance(page, dict):
        return None
    direct = page.get("navigation")
    if isinstance(direct, dict):
        return direct
    meta = page.get("meta") if isinstance(page.get("meta"), dict) else {}
    nav = meta.get("navigation")
    return nav if isinstance(nav, dict) else None


def _navigation_item_from_page(page: dict, *, auto_from_pages: bool) -> dict[str, Any] | None:
    nav = _navigation_config_from_page(page)
    if nav is None and not auto_from_pages:
        return None
    if isinstance(nav, dict) and nav.get("visible") is False:
        return None
    if isinstance(nav, dict) and nav.get("include") is False:
        return None

    meta = page.get("meta") if isinstance(page.get("meta"), dict) else {}
    if nav is None and (
        page.get("path") == PROFILE_SHELL_ROUTE["path"]
        or page.get("component") == "AdminPortal"
        or meta.get("adminSection")
    ):
        return None

    base = _route_item_from_page(page)
    if not base:
        return None
    if isinstance(nav, dict):
        base.update({key: value for key, value in nav.items() if key in _NAVIGATION_ITEM_FIELDS or key in {"include", "priority"}})
    base.setdefault("scope", "global")
    return _sanitize_navigation_item(base)


def _navigation_items_from_config(
    navigation: Any,
    catalog: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(navigation, dict):
        return []
    raw_items = navigation.get("items")
    if isinstance(raw_items, dict):
        iterable: list[Any] = [{**value, "id": key} for key, value in raw_items.items() if isinstance(value, dict)]
    elif isinstance(raw_items, list):
        iterable = raw_items
    else:
        iterable = []

    items: list[dict[str, Any]] = []
    for raw in iterable:
        if isinstance(raw, str):
            base = catalog.get(raw)
            if base:
                items.append(_sanitize_navigation_item(base))
            continue
        if not isinstance(raw, dict):
            continue
        reference = _clean_string(raw.get("shortcut")) or _clean_string(raw.get("id"))
        base = catalog.get(reference) if reference else None
        item = {**(base or {}), **raw}
        if reference and not item.get("id"):
            item["id"] = reference
        normalized = _sanitize_navigation_item(item)
        if normalized:
            items.append(normalized)
    return items


def _shortcut_navigation_items(shortcuts: Any, catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(shortcuts, dict):
        return []

    items: list[dict[str, Any]] = []
    for index, item in enumerate(_expand_shortcut_items(_shortcut_ids(shortcuts, "header"), catalog)):
        items.append({**item, "order": index, "scope": item.get("scope", "global"), "placement": {"desktop": "header", "mobile": "hidden"}})
    for index, item in enumerate(_expand_shortcut_items(_shortcut_ids(shortcuts, "mobile"), catalog)):
        items.append({**item, "order": index, "scope": item.get("scope", "global"), "placement": {"desktop": "hidden", "mobile": "bottomBar"}})
    for index, item in enumerate(_expand_shortcut_items(_shortcut_ids(shortcuts, "profile"), catalog)):
        items.append({**item, "order": index, "scope": "profile"})
    for index, item in enumerate(_expand_shortcut_items(_shortcut_ids(shortcuts, "footer"), catalog, footer=True)):
        item_id = str(item.get("href") or item.get("label") or "").strip("/").replace("/", "-")
        items.append({"id": item_id or "footer-link", **item, "order": index, "scope": "footer"})
    return [_sanitize_navigation_item(item) for item in items if _sanitize_navigation_item(item)]


def _sanitize_navigation_item(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(item, dict) or item.get("visible") is False:
        return None
    item_id = _clean_string(item.get("id"))
    path = _clean_string(item.get("path"))
    href = _clean_string(item.get("href"))
    action = _clean_string(item.get("action"))
    if not item_id:
        item_id = (path or href or "").strip("/").replace("/", "-")
    if not item_id:
        return None

    label = _clean_string(item.get("label")) or _title_from_id(item_id)
    scope = _clean_string(item.get("scope")) or "global"
    if scope not in {"global", "local", "profile", "footer"}:
        scope = "global"

    output: dict[str, Any] = {
        "id": item_id,
        "label": label,
        "scope": scope,
    }
    if action:
        output["action"] = action
    elif path:
        output["action"] = "navigate"
    if path:
        output["path"] = path
    if href:
        output["href"] = href
    for key in ("icon", "iconLabel", "requiresRole", "group"):
        value = _clean_string(item.get(key))
        if value:
            output[key] = value
    if isinstance(item.get("visible"), bool):
        output["visible"] = item["visible"]
    if isinstance(item.get("order"), int):
        output["order"] = item["order"]
    elif isinstance(item.get("priority"), int):
        output["order"] = item["priority"]
    placement = item.get("placement")
    if isinstance(placement, dict):
        clean_placement = {
            key: value.strip()
            for key, value in placement.items()
            if key in {"desktop", "mobile"} and isinstance(value, str) and value.strip()
        }
        if clean_placement:
            output["placement"] = clean_placement
    elif isinstance(placement, str) and placement.strip():
        output["placement"] = {"desktop": placement.strip(), "mobile": placement.strip()}
    return output


def _dedupe_navigation_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        item_id = _clean_string(item.get("id"))
        if not item_id:
            continue
        placement = item.get("placement")
        placement_key = json.dumps(placement, sort_keys=True) if isinstance(placement, dict) else str(placement or "")
        key = f"{item_id}:{item.get('scope', 'global')}:{placement_key}"
        current = by_id.get(key)
        if current is None:
            by_id[key] = item
        else:
            merged_placement = {}
            if isinstance(current.get("placement"), dict):
                merged_placement.update(current["placement"])
            if isinstance(item.get("placement"), dict):
                merged_placement.update(item["placement"])
            merged = {**current, **item}
            if merged_placement:
                merged["placement"] = merged_placement
            by_id[key] = merged
    return sorted(
        by_id.values(),
        key=lambda item: (item.get("order", 500), str(item.get("label") or item.get("id") or "")),
    )


def _placement_for_item(item: dict[str, Any], *, viewport: str, policy: dict[str, Any]) -> str:
    placement = item.get("placement") if isinstance(item.get("placement"), dict) else {}
    explicit = _clean_string(placement.get(viewport))
    if explicit:
        return explicit
    scope = item.get("scope")
    if scope == "local":
        return policy[viewport]["local"]
    if scope == "footer":
        return policy[viewport]["footer"]
    if scope == "profile":
        return "profile"
    return policy[viewport]["global"]


def _public_nav_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key in _NAVIGATION_ITEM_FIELDS and key != "scope"}


def _footer_link_from_item(item: dict[str, Any]) -> dict[str, Any] | None:
    href = _clean_string(item.get("href")) or _clean_string(item.get("path"))
    if not href:
        return None
    return {"label": item.get("label") or _title_from_id(str(item.get("id") or "link")), "href": href}


def _header_action_targets(header: Any) -> set[str]:
    if not isinstance(header, dict):
        return set()

    actions = header.get("actions")
    if not isinstance(actions, list):
        return set()

    targets: set[str] = set()
    for action in actions:
        if not isinstance(action, dict):
            continue
        for key in ("path", "href"):
            target = _clean_string(action.get(key))
            if target:
                targets.add(target)
    return targets


_ADMIN_PORTAL_MENU_ITEM = {
    "id": "admin-portal",
    "label": "Admin Portal",
    "action": "navigate",
    "path": "/apps",
    "requiresRole": "admin",
}


def _inject_admin_portal(result: dict) -> None:
    """Guarantee Admin Portal appears in the profile menu for admin users.

    Called after the full shell config pipeline so nothing can suppress it.
    Inserts before signout, or appends if signout is absent.
    """
    profile = result.get("profile")
    if not isinstance(profile, dict):
        profile = {"show": True, "menu": []}
        result["profile"] = profile

    menu = profile.get("menu")
    if not isinstance(menu, list):
        menu = []
        profile["menu"] = menu

    if any(isinstance(item, dict) and item.get("id") == "admin-portal" for item in menu):
        return

    signout_idx = next(
        (
            i
            for i, item in enumerate(menu)
            if isinstance(item, dict)
            and (item.get("id") == "signout" or item.get("action") == "signout")
        ),
        None,
    )
    if signout_idx is not None:
        menu.insert(signout_idx, dict(_ADMIN_PORTAL_MENU_ITEM))
    else:
        menu.append(dict(_ADMIN_PORTAL_MENU_ITEM))


def _apply_dynamic_shell_navigation(
    result: dict,
    *,
    pages: list[dict],
    navigation: Any,
    shortcuts: Any,
) -> None:
    policy = _normalize_navigation_policy(navigation)
    catalog = _shell_shortcut_catalog(pages, shortcuts if isinstance(shortcuts, dict) else {})
    all_items = _dedupe_navigation_items([
        *[
            item
            for page in pages
            for item in [_navigation_item_from_page(page, auto_from_pages=policy["autoFromPages"])]
            if item
        ],
        *_navigation_items_from_config(navigation, catalog),
        *_shortcut_navigation_items(shortcuts, catalog),
    ])

    resolved: dict[str, Any] = {
        "desktop": {"header": [], "sidebar": [], "rail": []},
        "mobile": {"bottomBar": [], "sheet": [], "more": []},
        "local": {"desktop": [], "mobile": []},
        "profile": [],
        "footer": [],
    }

    for item in all_items:
        scope = item.get("scope", "global")
        desktop_placement = _placement_for_item(item, viewport="desktop", policy=policy)
        mobile_placement = _placement_for_item(item, viewport="mobile", policy=policy)
        public_item = _public_nav_item(item)

        if scope == "profile" or desktop_placement == "profile" or mobile_placement == "profile":
            resolved["profile"].append(public_item)
        if scope == "footer" or desktop_placement == "visible" or mobile_placement == "visible":
            footer_link = _footer_link_from_item(item)
            if footer_link:
                resolved["footer"].append(footer_link)

        if scope == "local":
            resolved["local"]["desktop"].append(public_item)
            resolved["local"]["mobile"].append(public_item)
        elif desktop_placement in resolved["desktop"]:
            resolved["desktop"][desktop_placement].append(public_item)

        if mobile_placement == "bottomBar":
            resolved["mobile"]["bottomBar"].append(public_item)
        elif mobile_placement in {"sheet", "more"}:
            resolved["mobile"][mobile_placement].append(public_item)

    max_mobile = policy["maxMobileItems"]
    resolved["mobile"]["bottomBar"] = resolved["mobile"]["bottomBar"][:max_mobile]

    if resolved["desktop"]["header"]:
        header = result.get("header") if isinstance(result.get("header"), dict) else {}
        if not isinstance(header.get("pages"), list) or not header["pages"]:
            header_action_targets = _header_action_targets(header)
            header["pages"] = [
                {key: value for key, value in item.items() if key in {"id", "label", "path", "icon", "requiresRole", "visible"}}
                for item in resolved["desktop"]["header"]
                if item.get("path") and item.get("path") not in header_action_targets
            ]
            result["header"] = header

    if resolved["mobile"]["bottomBar"]:
        mobile = result.get("mobile") if isinstance(result.get("mobile"), dict) else {}
        bottom_bar = mobile.get("bottomBar") if isinstance(mobile.get("bottomBar"), dict) else {}
        if not isinstance(bottom_bar.get("items"), list) or not bottom_bar["items"]:
            bottom_bar["visible"] = bottom_bar.get("visible", "auto")
            bottom_bar["items"] = resolved["mobile"]["bottomBar"]
            mobile["bottomBar"] = bottom_bar
            result["mobile"] = mobile

    if resolved["profile"]:
        profile = result.get("profile") if isinstance(result.get("profile"), dict) else {}
        if not isinstance(profile.get("menu"), list) or not profile["menu"]:
            profile["show"] = profile.get("show", True)
            profile["menu"] = resolved["profile"]
            result["profile"] = profile


    if resolved["footer"]:
        footer = result.get("footer") if isinstance(result.get("footer"), dict) else {}
        if not isinstance(footer.get("links"), list) or not footer["links"]:
            footer["visible"] = footer.get("visible", True)
            footer["links"] = resolved["footer"]
        if "hideOnMobile" not in footer:
            footer["hideOnMobile"] = policy["mobile"]["footer"] == "hidden"
        result["footer"] = footer

    if isinstance(shortcuts, dict) and isinstance(shortcuts.get("footerHideOnMobile"), bool):
        footer = result.get("footer") if isinstance(result.get("footer"), dict) else {}
        footer["hideOnMobile"] = shortcuts["footerHideOnMobile"]
        result["footer"] = footer

    result["navigation"] = {
        "policy": policy,
        "items": all_items,
        "resolved": resolved,
    }


async def build_shell_config(*, surface: str = "platform") -> dict:
    """Compose app-shell config from platform-owned manifests."""
    app_root = resolve_app_root()
    ai_path = app_root / "config" / "ai.json"

    shell_surface = _normalize_shell_surface(surface)
    is_studio = shell_surface == "studio"
    result: dict = {"chat_startup_mode": "ask", "landing_spot": "/apps" if is_studio else "/"}
    app_manifest = _load_app_manifest()
    shell_shortcuts: dict[str, Any] | None = None
    shell_navigation: dict[str, Any] | None = None
    shell_chrome: dict[str, Any] | None = None

    try:
        if app_manifest:
            for key in ("appName", "app_name"):
                value = app_manifest.get(key)
                if isinstance(value, str) and value.strip():
                    result["appName"] = value.strip()
                    break
            for key in ("appId", "app_id"):
                value = app_manifest.get(key)
                if isinstance(value, str) and value.strip():
                    result["appId"] = value.strip()
                    break
            if not is_studio:
                startup = app_manifest.get("startup") if isinstance(app_manifest.get("startup"), dict) else {}
                landing_spot = startup.get("landing_spot")
                if isinstance(landing_spot, str) and landing_spot.startswith("/"):
                    result["landing_spot"] = landing_spot
    except Exception as exc:
        logger.warning("[shell-config] Could not read app startup config: %s", exc)

    try:
        ai = json.loads(ai_path.read_text(encoding="utf-8")) if ai_path.exists() else {}
        ai = resolve_runtime_ai_config(ai, app_root=app_root)
        chat = ai.get("chat") or {}
        workflows = ai.get("workflows") or {}
        result["chat_startup_mode"] = chat.get("chat_startup_mode") or chat.get("startup_mode") or "ask"
        result["entry_point"] = workflows.get("entry_point")
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to read shell config") from exc

    try:
        shell_config_path = _resolve_shell_config_path()
        if shell_config_path.exists():
            shell_config = json.loads(shell_config_path.read_text(encoding="utf-8"))
            for key in ("header", "profile", "notifications", "footer", "mobile"):
                value = shell_config.get(key)
                if value is not None:
                    result[key] = value
            if isinstance(shell_config.get("shortcuts"), dict):
                shell_shortcuts = shell_config["shortcuts"]
            if isinstance(shell_config.get("navigation"), dict):
                shell_navigation = shell_config["navigation"]
            if isinstance(shell_config.get("chrome"), dict):
                shell_chrome = shell_config["chrome"]
    except Exception as exc:
        logger.warning("[shell-config] Could not read shell config: %s", exc)

    pages: list[dict] = []
    for loader, label in (
        (_load_ui_route_manifest_pages, "UI route manifest pages"),
        (_load_page_schema_routes, "page schema routes"),
        (_load_workflow_entrypoint_pages, "workflow entrypoint routes"),
    ):
        try:
            pages.extend(loader(app_root))
        except Exception as exc:
            logger.warning("[shell-config] Could not read %s: %s", label, exc)

    # In Studio mode, also load routes from the factory_app bundle when the
    # active workspace is a different app root. Studio routes (surfaces: [studio])
    # are declared in factory_app/app/ui/route_manifest.json and are not present
    # in a freshly scaffolded workspace's route manifest.
    if is_studio:
        factory_root = resolve_factory_app_root()
        if factory_root is not None:
            factory_app_bundle = factory_root / "app"
            if factory_app_bundle.resolve() != app_root.resolve():
                try:
                    pages.extend(_load_ui_route_manifest_pages(factory_app_bundle))
                except Exception as exc:
                    logger.warning("[shell-config] Could not read factory route manifest: %s", exc)

    if pages:
        result["pages"] = _dedupe_and_sort_pages([
            page for page in pages if _page_targets_surface(page, surface=shell_surface)
        ])

    pages = result.get("pages", [])
    _append_page_once(
        pages,
        {
            "path": PROFILE_SHELL_ROUTE["path"],
            "component": PROFILE_SHELL_ROUTE["component"],
            "label": PROFILE_SHELL_ROUTE["label"],
            "order": PROFILE_SHELL_ROUTE["order"],
            "meta": {
                "requiresAuth": True,
                "title": PROFILE_SHELL_ROUTE["title"],
                "appShell": True,
                "shellMode": "social",
                "ai_context": "The user is on their Profile page — their account identity, preferences, and module-contributed profile panels.",
            },
        },
    )
    _append_page_once(
        pages,
        {
            "path": "/u/:username",
            "component": "ProfilePage",
            "label": "User Profile",
            "order": 999,
            "meta": {
                "requiresAuth": True,
                "title": "Profile",
                "appShell": True,
                "shellMode": "social",
                "ai_context": "The user is viewing another user's public profile.",
            },
        },
    )
    result["pages"] = _dedupe_and_sort_pages(pages)

    pages = result.get("pages", [])
    _admin_registry = load_admin_registry(resolve_active_app_root())
    for route in build_admin_shell_routes(_admin_registry):
        route_surfaces = route.get("surfaces")
        if isinstance(route_surfaces, list) and shell_surface not in route_surfaces:
            continue
        _append_page_once(pages, {
            "path": route["path"],
            "component": "AdminPortal",
            "label": route["label"],
            "order": route["order"],
            "meta": {
                "requiresAuth": True,
                "requiresRole": "admin",
                "title": route["title"],
                "appShell": True,
                "adminPage": route["admin_page"],
                "shellMode": "workspace",
                **({"surfaces": route_surfaces} if isinstance(route_surfaces, list) else {}),
            },
        })
    result["pages"] = _dedupe_and_sort_pages(pages)

    _apply_dynamic_shell_navigation(
        result,
        pages=result["pages"],
        navigation=shell_navigation,
        shortcuts=shell_shortcuts,
    )
    result["chrome"] = _normalize_chrome_policy(shell_chrome)

    # Admin Portal is a framework guarantee — inject after the full pipeline so
    # no app config or route processing can accidentally suppress it.
    _inject_admin_portal(result)

    return result


@app.get("/health")
async def health_check(request: Request):
    """Liveness and readiness probe.

    Returns 200 when the platform is healthy and accepting requests.
    Returns 503 when the platform degraded at startup (e.g. module load failure).
    This endpoint is intentionally unauthenticated and lightweight.
    """
    from mozaiksai.version import __version__

    if getattr(request.app.state, "startup_degraded", False):
        reason = getattr(request.app.state, "startup_degraded_reason", "unknown")
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "version": __version__, "reason": reason},
        )
    return JSONResponse(
        status_code=200,
        content={"status": "ok", "version": __version__},
    )


@app.get("/api/shell-config")
async def get_shell_config():
    return await build_shell_config(surface="platform")


@app.get("/api/me")
async def get_current_user_profile(
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_any_auth),
):
    resolved_app_id, user_id = _resolve_profile_scope(principal, app_id=app_id)
    return await _ensure_account_profile(principal, app_id=resolved_app_id, user_id=user_id)


@app.put("/api/me")
async def update_current_user_profile(
    body: ProfileUpdateRequest,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_any_auth),
):
    resolved_app_id, user_id = _resolve_profile_scope(principal, app_id=app_id)
    profile = await _ensure_account_profile(principal, app_id=resolved_app_id, user_id=user_id)

    updates: dict[str, Any] = {}
    payload = body.model_dump(exclude_unset=True)
    if "display_name" in payload:
        value = payload.get("display_name")
        updates["display_name"] = value.strip() if isinstance(value, str) and value.strip() else None
    if "bio" in payload:
        value = payload.get("bio")
        updates["bio"] = value.strip() if isinstance(value, str) and value.strip() else None
    if "avatar_url" in payload:
        value = payload.get("avatar_url")
        updates["avatar_url"] = value.strip() if isinstance(value, str) and value.strip() else None

    if updates:
        collection = await _account_profile_collection()
        await collection.update_one(
            {"_id": _profile_doc_id(resolved_app_id, user_id)},
            {"$set": {**updates, "updated_at": datetime.now(UTC)}},
        )
        profile = await _ensure_account_profile(principal, app_id=resolved_app_id, user_id=user_id)

    return profile


@app.get("/api/me/preferences")
async def get_current_user_preferences(
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_any_auth),
):
    resolved_app_id, user_id = _resolve_profile_scope(principal, app_id=app_id)
    return await _load_account_preferences(app_id=resolved_app_id, user_id=user_id)


@app.get("/api/me/usage")
async def get_current_user_usage(
    app_id: str | None = None,
    limit: int = 500,
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return user-scoped token usage and app-declared subscription limits."""
    resolved_app_id, user_id = _resolve_profile_scope(principal, app_id=app_id)
    from mozaiksai.core.usage import get_runtime_token_budget_alert_ledger, get_runtime_usage_ledger

    bounded_limit = max(1, min(int(limit or 1), 1000))
    ledger = get_runtime_usage_ledger()
    alert_ledger = get_runtime_token_budget_alert_ledger()
    usage = await ledger.query_usage(app_id=resolved_app_id, user_id=user_id, limit=bounded_limit)
    subscriptions = getattr(app.state, "subscriptions_config", None)
    charge_policy = _runtime_llm_usage_charge_policy(subscriptions)
    if charge_policy is not None:
        from mozaiksai.core.usage.charges import enrich_usage_with_charge_policy

        usage = enrich_usage_with_charge_policy(usage, charge_policy)
    return {
        **usage,
        "token_budget_alerts": await alert_ledger.query_alerts(
            app_id=resolved_app_id,
            user_id=user_id,
            limit=min(bounded_limit, 100),
        ),
        "subscription_usage": _serialize_subscription_usage_limits(subscriptions),
        "token_wallets": await _current_user_token_wallet_summary(
            subscriptions,
            app_id=resolved_app_id,
            user_id=user_id,
            tenant_id=str(principal.tenant_id) if principal.tenant_id else None,
            workspace_id=str(principal.workspace_id) if principal.workspace_id else None,
            ensure_allowances=False,
        ),
    }


@app.get("/api/me/tokens")
async def get_current_user_tokens(
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return current user's provider-neutral token wallet balances."""

    resolved_app_id, user_id = _resolve_profile_scope(principal, app_id=app_id)
    subscriptions = getattr(app.state, "subscriptions_config", None)
    return await _current_user_token_wallet_summary(
        subscriptions,
        app_id=resolved_app_id,
        user_id=user_id,
        tenant_id=str(principal.tenant_id) if principal.tenant_id else None,
        workspace_id=str(principal.workspace_id) if principal.workspace_id else None,
        ensure_allowances=False,
    )


@app.post("/api/me/tokens/sync")
async def sync_current_user_token_allowances(
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Idempotently materialize current subscription token allowances."""

    resolved_app_id, user_id = _resolve_profile_scope(principal, app_id=app_id)
    subscriptions = getattr(app.state, "subscriptions_config", None)
    return await _current_user_token_wallet_summary(
        subscriptions,
        app_id=resolved_app_id,
        user_id=user_id,
        tenant_id=str(principal.tenant_id) if principal.tenant_id else None,
        workspace_id=str(principal.workspace_id) if principal.workspace_id else None,
        ensure_allowances=True,
    )


@app.get("/api/me/tokens/ledger")
async def get_current_user_token_ledger(
    app_id: str | None = None,
    wallet_id: str = "ai_tokens",
    limit: int = 100,
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return current user's token wallet ledger entries for one wallet."""

    resolved_app_id, user_id = _resolve_profile_scope(principal, app_id=app_id)
    from mozaiksai.core.tokens.wallet import get_token_wallet_ledger

    subscriptions = getattr(app.state, "subscriptions_config", None)
    wallet_scope = None
    if subscriptions is not None:
        wallet = subscriptions.token_wallet_by_id(wallet_id)
        wallet_scope = wallet.scope if wallet is not None else None
    ledger = get_token_wallet_ledger()
    entries = await ledger.list_entries(
        app_id=resolved_app_id,
        wallet_id=wallet_id,
        user_id=user_id,
        tenant_id=str(principal.tenant_id) if principal.tenant_id else None,
        preferred_scope=wallet_scope,
        limit=limit,
    )
    return {
        "app_id": resolved_app_id,
        "user_id": user_id,
        "tenant_id": str(principal.tenant_id) if principal.tenant_id else None,
        "wallet_id": wallet_id,
        "entries": entries,
        "count": len(entries),
        "source": "token_wallet_ledger",
    }


@app.put("/api/me/preferences")
async def update_current_user_preferences(
    body: ProfilePreferencesUpdateRequest,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_any_auth),
):
    resolved_app_id, user_id = _resolve_profile_scope(principal, app_id=app_id)
    collection = await _account_preferences_collection()
    now = datetime.now(UTC)
    await collection.update_one(
        {"_id": _profile_doc_id(resolved_app_id, user_id)},
        {
            "$setOnInsert": {
                "_id": _profile_doc_id(resolved_app_id, user_id),
                "app_id": resolved_app_id,
                "user_id": user_id,
                "created_at": now,
            },
            "$set": {
                "settings": body.settings,
                "updated_at": now,
            },
        },
        upsert=True,
    )
    return await _load_account_preferences(app_id=resolved_app_id, user_id=user_id)


def _relationship_result_rows(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("relationships", "rows", "items"):
        rows = data.get(key)
        if isinstance(rows, list):
            return rows
    return []


def _normalize_relationship_routes(raw_routes: Any) -> list[dict[str, str]]:
    if not isinstance(raw_routes, list):
        return []
    routes: list[dict[str, str]] = []
    for route in raw_routes:
        if not isinstance(route, dict):
            continue
        label = str(route.get("label") or "").strip()
        path = str(route.get("path") or route.get("href") or "").strip()
        if not label or not path.startswith("/"):
            continue
        routes.append({"label": label[:80], "path": path})
    return routes


def _normalize_relationship_row(
    row: Any,
    *,
    module_id: str,
    provider: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None

    provider_id = str(provider.get("id") or "").strip()
    resource_type = str(row.get("resource_type") or "").strip()
    resource_id = str(row.get("resource_id") or row.get("id") or "").strip()
    relationship_type = str(row.get("relationship_type") or row.get("type") or "").strip()
    if not resource_type or not resource_id or not relationship_type:
        return None

    provider_resource_types = provider.get("resource_types")
    if isinstance(provider_resource_types, list) and provider_resource_types and resource_type not in provider_resource_types:
        return None
    provider_relationship_types = provider.get("relationship_types")
    if (
        isinstance(provider_relationship_types, list)
        and provider_relationship_types
        and relationship_type not in provider_relationship_types
    ):
        return None

    primary_route = str(row.get("primary_route") or row.get("path") or "").strip()
    relationship_id = str(row.get("relationship_id") or "").strip()
    if not relationship_id:
        relationship_id = f"{module_id}:{provider_id}:{resource_type}:{resource_id}:{relationship_type}"

    capabilities = row.get("capabilities")
    if not isinstance(capabilities, list):
        capabilities = []
    capabilities = [str(item).strip() for item in capabilities if str(item or "").strip()]

    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    normalized: dict[str, Any] = {
        "relationship_id": relationship_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "resource_label": str(row.get("resource_label") or row.get("label") or resource_id).strip(),
        "relationship_type": relationship_type,
        "status": str(row.get("status") or "active").strip() or "active",
        "capabilities": capabilities,
        "primary_route": primary_route if primary_route.startswith("/") else None,
        "secondary_routes": _normalize_relationship_routes(row.get("secondary_routes")),
        "source_module": module_id,
        "source_provider": provider_id,
        "updated_at": row.get("updated_at"),
        "metadata": metadata,
    }
    for optional_key in ("resource_subtitle", "created_at", "expires_at"):
        value = row.get(optional_key)
        if value not in (None, ""):
            normalized[optional_key] = value
    return normalized


@app.get("/api/me/profile-panels")
async def get_profile_panels(
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return module-declared profile panels, each hydrated with live action data.

    Walks modules/*/contracts/profile.yaml under the active app root and, for
    each panel that declares an ``action``, calls the module executor to fetch
    panel data. Panels whose action fails are still returned with ``data: null``
    and an ``error`` string so the UI can render graceful empty states.
    """
    # Keep profile hydration bound to the active app runtime. The optional
    # query app_id is contextual data for panel actions, not a persistence scope
    # override. Support links use it as the subject app id for tickets.
    resolved_app_id, user_id = _resolve_profile_scope(principal, app_id=None)
    app_root = resolve_app_root()
    raw_panels = load_profile_panels(app_root)

    module_executor = executor_registry.module_executor
    hydrated: list[dict[str, Any]] = []
    action_params = {"app_id": app_id} if app_id else {}

    logger.info(
        "[profile-panels] load start runtime_app_id=%s requested_app_id=%s user_id=%s panel_count=%s",
        resolved_app_id,
        app_id,
        user_id,
        len(raw_panels),
    )

    for panel in raw_panels:
        action = panel.get("action")
        panel_out: dict[str, Any] = {**panel, "data": None, "error": None}

        if action and module_executor is not None:
            module_name = panel.get("module_id", "")
            try:
                req = ModuleRequest(
                    module=module_name,
                    action=action,
                    params=action_params,
                    app_id=resolved_app_id,
                    user_id=user_id,
                    tenant_id=str(principal.tenant_id) if principal.tenant_id else None,
                    auth_token=None,
                    correlation_id=None,
                    granted_permissions=list(principal.scopes) if principal else None,
                )
                result = await module_executor.execute(req, context=None)
                if result.success:
                    panel_out["data"] = result.data
                    logger.info(
                        "[profile-panels] action success module=%s action=%s panel_id=%s runtime_app_id=%s requested_app_id=%s data_keys=%s item_count=%s",
                        module_name,
                        action,
                        panel.get("id"),
                        resolved_app_id,
                        app_id,
                        sorted((result.data or {}).keys()) if isinstance(result.data, dict) else [],
                        len((result.data or {}).get("requests", [])) if isinstance(result.data, dict) else None,
                    )
                else:
                    panel_out["error"] = result.error or f"Action {action!r} failed"
                    logger.warning(
                        "[profile-panels] action failed module=%s action=%s panel_id=%s runtime_app_id=%s requested_app_id=%s error=%s",
                        module_name,
                        action,
                        panel.get("id"),
                        resolved_app_id,
                        app_id,
                        panel_out["error"],
                    )
            except Exception as exc:
                logger.warning("[profile-panels] %s.%s failed: %s", module_name, action, exc, exc_info=True)
                panel_out["error"] = f"Action {action!r} failed"

        hydrated.append(panel_out)

    logger.info(
        "[profile-panels] load complete runtime_app_id=%s requested_app_id=%s hydrated_count=%s panel_ids=%s",
        resolved_app_id,
        app_id,
        len(hydrated),
        [panel.get("id") for panel in hydrated],
    )
    return {"panels": hydrated}


@app.get("/api/me/profile-tabs")
async def get_profile_tabs(
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return module-declared profile tabs, each hydrated with live action data.

    Walks modules/*/contracts/profile.yaml under the active app root and, for
    each tab that declares an ``action``, calls the module executor to fetch
    tab data. Tabs whose action fails are still returned with ``data: null``
    and an ``error`` string so the UI can render graceful empty states.
    """
    resolved_app_id, user_id = _resolve_profile_scope(principal, app_id=None)
    app_root = resolve_app_root()
    raw_tabs = load_profile_tabs(app_root)

    module_executor = executor_registry.module_executor
    hydrated: list[dict[str, Any]] = []
    action_params = {"app_id": app_id} if app_id else {}

    logger.info(
        "[profile-tabs] load start runtime_app_id=%s requested_app_id=%s user_id=%s tab_count=%s",
        resolved_app_id,
        app_id,
        user_id,
        len(raw_tabs),
    )

    for tab in raw_tabs:
        action = tab.get("action")
        tab_out: dict[str, Any] = {**tab, "data": None, "error": None}

        if action and module_executor is not None:
            module_name = tab.get("module_id", "")
            try:
                req = ModuleRequest(
                    module=module_name,
                    action=action,
                    params=action_params,
                    app_id=resolved_app_id,
                    user_id=user_id,
                    tenant_id=str(principal.tenant_id) if principal.tenant_id else None,
                    auth_token=None,
                    correlation_id=None,
                    granted_permissions=list(principal.scopes) if principal else None,
                )
                result = await module_executor.execute(req, context=None)
                if result.success:
                    tab_out["data"] = result.data
                    logger.info(
                        "[profile-tabs] action success module=%s action=%s tab_id=%s runtime_app_id=%s requested_app_id=%s data_keys=%s",
                        module_name,
                        action,
                        tab.get("id"),
                        resolved_app_id,
                        app_id,
                        sorted((result.data or {}).keys()) if isinstance(result.data, dict) else [],
                    )
                else:
                    tab_out["error"] = result.error or f"Action {action!r} failed"
                    logger.warning(
                        "[profile-tabs] action failed module=%s action=%s tab_id=%s runtime_app_id=%s requested_app_id=%s error=%s",
                        module_name,
                        action,
                        tab.get("id"),
                        resolved_app_id,
                        app_id,
                        tab_out["error"],
                    )
            except Exception as exc:
                logger.warning("[profile-tabs] %s.%s failed: %s", module_name, action, exc, exc_info=True)
                tab_out["error"] = f"Action {action!r} failed"

        hydrated.append(tab_out)

    logger.info(
        "[profile-tabs] load complete runtime_app_id=%s requested_app_id=%s hydrated_count=%s tab_ids=%s",
        resolved_app_id,
        app_id,
        len(hydrated),
        [tab.get("id") for tab in hydrated],
    )
    return {"tabs": hydrated}


@app.get("/api/me/relationships")
async def get_current_user_relationships(
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return module-declared current-user resource relationships.

    Modules opt into this surface with ``contracts/relationships.yaml``. Each
    provider delegates hydration to a module action and returns normalized rows
    for account, portfolio, and "my resources" surfaces. Provider failures are
    isolated so one broken module cannot blank the whole response.
    """
    resolved_app_id, user_id = _resolve_profile_scope(principal, app_id=app_id)
    app_root = resolve_app_root()
    raw_providers = load_relationship_providers(app_root)

    module_executor = executor_registry.module_executor
    relationships: list[dict[str, Any]] = []
    providers: list[dict[str, Any]] = []

    for provider in raw_providers:
        action = str(provider.get("action") or "").strip()
        module_id = str(provider.get("module_id") or "").strip()
        provider_out: dict[str, Any] = {
            **provider,
            "count": 0,
            "error": None,
        }

        if not action or module_executor is None:
            providers.append(provider_out)
            continue

        try:
            req = ModuleRequest(
                module=module_id,
                action=action,
                params={},
                app_id=resolved_app_id,
                user_id=user_id,
                tenant_id=str(principal.tenant_id) if principal.tenant_id else None,
                auth_token=None,
                correlation_id=None,
                granted_permissions=list(principal.scopes) if principal else None,
            )
            result = await module_executor.execute(req, context=None)
            if result.success:
                for row in _relationship_result_rows(result.data):
                    normalized = _normalize_relationship_row(row, module_id=module_id, provider=provider)
                    if normalized is not None:
                        relationships.append(normalized)
                        provider_out["count"] += 1
            else:
                provider_out["error"] = result.error or f"Action {action!r} failed"
        except Exception as exc:
            logger.warning("[relationships] %s.%s failed: %s", module_id, action, exc)
            provider_out["error"] = f"Action {action!r} failed"

        providers.append(provider_out)

    relationships.sort(
        key=lambda row: (
            str(row.get("resource_type") or ""),
            str(row.get("relationship_type") or ""),
            str(row.get("resource_label") or ""),
            str(row.get("relationship_id") or ""),
        )
    )
    return {"relationships": relationships, "providers": providers}


def _normalize_shell_page_entry(entry: dict, *, order_fallback: int) -> dict | None:
    if not isinstance(entry, dict):
        return None
    path = entry.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        return None
    component = entry.get("component")
    transition = entry.get("transition")
    workflow = entry.get("workflow")
    if not any(isinstance(value, str) and value.strip() for value in (component, transition, workflow)):
        return None

    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    page: dict = {
        "path": path,
        "label": entry.get("label", ""),
        "order": entry.get("order", order_fallback),
        "meta": {
            **_normalize_route_requires_role_meta(meta),
            "requiresAuth": entry.get("requiresAuth", True),
        },
    }
    shell_mode = _shell_mode_from_entry(entry)
    if shell_mode:
        page["meta"]["shellMode"] = shell_mode
    if isinstance(component, str) and component.strip():
        page["component"] = component.strip()
    if isinstance(transition, str) and transition.strip():
        page["transition"] = transition.strip()
    if isinstance(workflow, str) and workflow.strip():
        page["workflow"] = workflow.strip()
    if isinstance(entry.get("sequence"), str) and entry["sequence"].strip():
        page["sequence"] = entry["sequence"].strip()
    if isinstance(entry.get("schema"), str) and entry["schema"].strip():
        page["schema"] = entry["schema"].strip()
    if isinstance(entry.get("navigation"), dict):
        nav = entry["navigation"]
        page["meta"]["navigation"] = nav
        # If the page declares a navigation group (workspace-studio, app-studio,
        # etc.), it must participate in shell navigation — mark appShell=True so
        # WorkspaceLayout and other layout-aware components can find it.
        if isinstance(nav.get("group"), str) and nav["group"].strip():
            page["meta"].setdefault("appShell", True)
    if isinstance(meta.get("ai_context"), str) and meta["ai_context"].strip():
        page["meta"]["ai_context"] = meta["ai_context"].strip()
    return page


def _coerce_requires_role(value: Any) -> str | None:
    """Normalize route-role metadata without treating it as security enforcement.

    Current shell route metadata is single-role only. If route or page schema
    content provides a role list, keep the first non-empty role as
    declaration and visibility intent only. Module policy remains the
    authoritative security boundary for resource-scoped authorization until
    frontend role checks and scoped route auth land.
    """

    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, str):
                continue
            normalized = item.strip()
            if normalized:
                return normalized
    return None


def _normalize_route_requires_role_meta(meta: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(meta)
    requires_role = _coerce_requires_role(normalized.get("requiresRole"))
    if not requires_role:
        requires_role = _coerce_requires_role(normalized.get("roles"))
    normalized.pop("roles", None)
    if requires_role:
        normalized["requiresRole"] = requires_role
    return normalized


def _load_ui_route_manifest_pages(app_root: Path) -> list[dict]:
    manifest_path = (app_root / "ui" / "route_manifest.json").resolve()
    if manifest_path is None:
        return []
    if not manifest_path.exists():
        return []
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = raw.get("pages") if isinstance(raw, dict) else []
    if not isinstance(entries, list):
        return []
    pages: list[dict] = []
    for index, entry in enumerate(entries):
        page = _normalize_shell_page_entry(entry, order_fallback=index)
        if page:
            pages.append(page)
    return pages


def _load_page_schema_routes(app_root: Path) -> list[dict]:
    pages_dir = app_root / "ui" / "pages"
    if not pages_dir.exists():
        return []

    candidates: list[tuple[Path, str]] = []
    for child in sorted(pages_dir.iterdir(), key=lambda item: item.name.lower()):
        if child.is_file() and child.suffix.lower() in {".yaml", ".yml"}:
            candidates.append((child, child.stem))
        elif child.is_dir():
            page_yaml = child / "page.yaml"
            page_yml = child / "page.yml"
            if page_yaml.exists():
                candidates.append((page_yaml, child.name))
            elif page_yml.exists():
                candidates.append((page_yml, child.name))

    pages: list[dict] = []
    for index, (page_path, default_name) in enumerate(candidates):
        raw = yaml.safe_load(page_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            continue
        route = raw.get("route")
        if not isinstance(route, str) or not route.startswith("/"):
            continue
        name = str(raw.get("name") or default_name).strip() or default_name
        title = str(raw.get("title") or name).strip()
        raw_meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
        raw_requires_role = raw_meta.get("requiresRole")
        if raw_requires_role is None:
            raw_requires_role = raw_meta.get("roles")
        if raw_requires_role is None:
            raw_requires_role = raw.get("roles")
        meta: dict = _normalize_route_requires_role_meta(
            {
                "title": title,
                "appShell": True,
                "requiresAuth": True,
                **({"requiresRole": raw_requires_role} if raw_requires_role is not None else {}),
            }
        )
        if isinstance(raw.get("navigation"), dict):
            meta["navigation"] = raw["navigation"]
        shell_mode = _normalize_shell_mode(raw.get("shell_mode")) or _normalize_shell_mode(raw.get("shellMode"))
        if shell_mode:
            meta["shellMode"] = shell_mode
        pages.append({
            "path": route,
            "label": title,
            "component": "SchemaPage",
            "schema": name,
            "order": 100 + index,
            "meta": meta,
        })
    return pages


def _load_workflow_entrypoint_pages(app_root: Path) -> list[dict]:
    from mozaiksai.core.workflow.pack.config import list_entrypoints, load_global_pack_graph

    _ = app_root
    pack = load_global_pack_graph()
    if pack is None:
        return []

    transition_shell_modes = {
        transition.id: transition.ui.shell_mode
        for transition in getattr(pack, "transitions", [])
        if getattr(transition, "ui", None) is not None and transition.ui.shell_mode
    }
    pages: list[dict] = []
    for index, entry in enumerate(list_entrypoints(pack)):
        raw_entry = entry.model_dump(exclude_none=True)
        transition_id = raw_entry.get("transition")
        if (
            isinstance(transition_id, str)
            and transition_id in transition_shell_modes
            and not _shell_mode_from_entry(raw_entry)
        ):
            meta = raw_entry.get("meta") if isinstance(raw_entry.get("meta"), dict) else {}
            raw_entry["meta"] = {**meta, "shellMode": transition_shell_modes[transition_id]}
        page = _normalize_shell_page_entry(raw_entry, order_fallback=200 + index)
        if page:
            pages.append(page)
    return pages


def _dedupe_and_sort_pages(pages: list[dict]) -> list[dict]:
    by_path: dict[str, dict] = {}
    for page in pages:
        path = page.get("path")
        if isinstance(path, str) and path not in by_path:
            by_path[path] = page
    return sorted(
        by_path.values(),
        key=lambda page: (page.get("order", 0), str(page.get("label") or page.get("path") or "")),
    )


def _resolve_shell_config_path() -> Path:
    app_root = resolve_app_root()
    return (app_root / "config" / "shell.json").resolve()


def _resolve_app_manifest_path() -> Path:
    app_root = resolve_app_root()
    return (app_root / "app.json").resolve()


def _load_app_manifest() -> dict[str, Any]:
    app_manifest_path = _resolve_app_manifest_path()
    if not app_manifest_path.exists():
        return {}
    try:
        raw = json.loads(app_manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("APP_MANIFEST_LOAD_FAILED %s: %s", app_manifest_path, exc)
        return {}
    return raw if isinstance(raw, dict) else {}


def _resolve_default_app_id() -> str:
    manifest = _load_app_manifest()
    if not manifest:
        return "default"

    for key in ("appId", "app_id"):
        value = manifest.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "default"


def _profile_doc_id(app_id: str, user_id: str) -> str:
    return f"{app_id}:{user_id}"


def _default_username(principal: UserPrincipal, user_id: str) -> str:
    email = str(principal.email or "").strip()
    if email and "@" in email:
        return email.split("@", 1)[0]
    name = str(principal.name or "").strip()
    if name:
        return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or user_id
    return user_id


async def _account_profile_collection():
    await persistence_manager.persistence._ensure_client()
    client = persistence_manager.persistence.client
    if client is None:
        raise RuntimeError("Mongo client not initialized")
    return client["mozaiksai"][_ACCOUNT_PROFILE_COLLECTION]


async def _account_preferences_collection():
    await persistence_manager.persistence._ensure_client()
    client = persistence_manager.persistence.client
    if client is None:
        raise RuntimeError("Mongo client not initialized")
    return client["mozaiksai"][_ACCOUNT_PREFERENCES_COLLECTION]


def _resolve_profile_scope(
    principal: UserPrincipal,
    *,
    app_id: str | None = None,
) -> tuple[str, str]:
    return resolve_scope_from_principal(
        principal,
        app_id=app_id,
        default_user_id=_DEFAULT_PROFILE_USER_ID,
        default_app_id=_resolve_default_app_id(),
    )


def _serialize_subscription_usage_limits(config: Any) -> dict[str, Any]:
    if config is None:
        return {
            "schema_version": None,
            "default_plan_id": None,
            "plans": [],
            "token_wallets": [],
            "usage_charge_policies": [],
            "source": "none",
        }
    plans: list[dict[str, Any]] = []
    for plan in getattr(config, "plans", []) or []:
        limits = []
        for limit in getattr(plan, "usage_limits", []) or []:
            limits.append(
                {
                    "meter_id": limit.meter_id,
                    "label": limit.label or limit.meter_id,
                    "unit": limit.unit,
                    "monthly_limit": limit.monthly_limit,
                    "capability_id": limit.capability_id,
                }
            )
        token_allowances = [
            allowance.model_dump()
            for allowance in getattr(plan, "token_allowances", []) or []
        ]
        plans.append(
            {
                "plan_id": plan.plan_id,
                "label": plan.label,
                "usage_limits": limits,
                "token_allowances": token_allowances,
            }
        )
    token_wallets = [
        wallet.model_dump()
        for wallet in getattr(config, "token_wallets", []) or []
    ]
    usage_charge_policies = [
        policy.model_dump()
        for policy in getattr(config, "usage_charge_policies", []) or []
    ]
    return {
        "schema_version": getattr(config, "schema_version", None),
        "default_plan_id": getattr(config, "default_plan_id", None),
        "plans": plans,
        "token_wallets": token_wallets,
        "usage_charge_policies": usage_charge_policies,
        "source": "app_config_subscriptions",
    }


def _runtime_llm_usage_charge_policy(config: Any) -> Any | None:
    if config is None:
        return None
    for policy in getattr(config, "usage_charge_policies", []) or []:
        if getattr(policy, "source", None) == "runtime_llm_usage":
            return policy
    return None


async def _current_user_token_wallet_summary(
    config: Any,
    *,
    app_id: str,
    user_id: str,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    ensure_allowances: bool = False,
) -> dict[str, Any]:
    if config is None or not getattr(config, "token_wallets", None):
        return {
            "wallets": [],
            "source": "none",
        }

    from mozaiksai.core.tokens.wallet import get_token_wallet_ledger

    plan_id = getattr(config, "default_plan_id", None)
    try:
        adapter = ConfiguredEntitlementAdapter(config=config)
        resolved_plan_id = await adapter.current_plan_id(
            app_id=app_id,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if resolved_plan_id:
            plan_id = resolved_plan_id
    except Exception as exc:
        logger.debug("TOKEN_WALLET_PLAN_RESOLUTION_SKIPPED: %s", exc)

    ledger = get_token_wallet_ledger()
    return await ledger.wallet_summaries_for_config(
        config=config,
        app_id=app_id,
        user_id=user_id,
        tenant_id=tenant_id,
        plan_id=plan_id,
        ensure_allowances=ensure_allowances,
    )


async def _ensure_account_profile(
    principal: UserPrincipal,
    *,
    app_id: str,
    user_id: str,
) -> dict[str, Any]:
    collection = await _account_profile_collection()
    now = datetime.now(UTC)
    username = _default_username(principal, user_id)
    default_display_name = str(principal.name or "").strip() or username
    doc_id = _profile_doc_id(app_id, user_id)

    await collection.update_one(
        {"_id": doc_id},
        {
            "$setOnInsert": {
                "_id": doc_id,
                "app_id": app_id,
                "user_id": user_id,
                "display_name": default_display_name,
                "avatar_url": None,
                "subscription_tier": None,
                "created_at": now,
            },
            "$set": {
                "email": principal.email,
                "name": principal.name,
                "username": username,
                "roles": list(principal.roles or []),
                "provider": principal.provider,
                "last_login_at": now,
                "updated_at": now,
            },
        },
        upsert=True,
    )
    doc = await collection.find_one({"_id": doc_id}) or {}
    return {
        "app_id": app_id,
        "user_id": user_id,
        "email": doc.get("email"),
        "username": doc.get("username") or username,
        "display_name": doc.get("display_name") or default_display_name,
        "avatar_url": doc.get("avatar_url"),
        "subscription_tier": doc.get("subscription_tier"),
        "roles": doc.get("roles") or list(principal.roles or []),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "last_login_at": doc.get("last_login_at"),
    }


async def _load_account_preferences(*, app_id: str, user_id: str) -> dict[str, Any]:
    collection = await _account_preferences_collection()
    doc = await collection.find_one({"_id": _profile_doc_id(app_id, user_id)}) or {}
    return {
        "app_id": app_id,
        "user_id": user_id,
        "settings": doc.get("settings") or {},
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


class ProfileUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=120, description="Preferred user-facing display name")
    bio: str | None = Field(default=None, max_length=500, description="Short user bio")
    avatar_url: str | None = Field(default=None, max_length=2048, description="Optional avatar image URL — must be a URL, not a data URI")


class ProfilePreferencesUpdateRequest(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict, description="App-scoped account preference map")




def _load_workflow_capability_routes(app_root: Path) -> dict[str, list[dict[str, Any]]]:
    """Index workflow trigger declarations by public capability id."""
    workflows_dir = next(
        (root for root in candidate_app_workflows_roots(app_root) if root.exists()),
        candidate_app_workflows_roots(app_root)[0],
    )
    if not workflows_dir.exists():
        return {}

    routes: dict[str, list[dict[str, Any]]] = {}
    for workflow_dir in sorted(workflows_dir.iterdir(), key=lambda item: item.name.lower()):
        if not workflow_dir.is_dir():
            continue
        orchestrator_path = workflow_dir / "orchestrator.yaml"
        if not orchestrator_path.exists():
            continue
        try:
            raw = yaml.safe_load(orchestrator_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            logger.warning("WORKFLOW_CAPABILITY_ROUTE_LOAD_FAILED: %s: %s", orchestrator_path, exc)
            continue
        if not isinstance(raw, dict):
            continue

        workflow_id = str(raw.get("workflow_name") or workflow_dir.name).strip()
        if not workflow_id:
            continue
        triggers = raw.get("triggers") if isinstance(raw.get("triggers"), list) else []
        for trigger in triggers:
            if not isinstance(trigger, dict):
                continue
            capability_ids = _trigger_capability_ids(trigger)
            if not capability_ids:
                continue
            event_type = str(trigger.get("event") or "").strip() or None
            route = {
                "workflow_id": workflow_id,
                "event_type": event_type,
                "trigger": dict(trigger),
                "orchestrator_path": str(orchestrator_path),
            }
            for capability_id in capability_ids:
                routes.setdefault(capability_id, []).append(route)
    return routes

def _trigger_capability_ids(trigger: dict[str, Any]) -> list[str]:
    capability_ids = trigger.get("capability_ids")
    if isinstance(capability_ids, list):
        results = [str(item).strip() for item in capability_ids if str(item).strip()]
        if results:
            return results
    capability_id = str(trigger.get("capability_id") or "").strip()
    return [capability_id] if capability_id else []


async def _invoke_workflow_capability(
    *,
    capability_id: str,
    source_event: dict[str, Any],
    subscription: dict[str, Any],
    routes: dict[str, list[dict[str, Any]]],
    event_emitter: Callable[[str, dict[str, Any]], Any] | None = None,
    create_session: Callable[..., Any] | None = None,
    auto_start: bool = True,
)-> dict[str, Any]:
    route = _select_workflow_capability_route(
        capability_id=capability_id,
        source_event_type=str(source_event.get("type") or "").strip(),
        routes=routes,
    )
    if route is None:
        logger.warning(
            "WORKFLOW_CAPABILITY_UNRESOLVED: capability_id=%s event_type=%s",
            capability_id,
            source_event.get("type"),
        )
        return {
            "status": "unresolved",
            "capability_id": capability_id,
            "event_type": source_event.get("type"),
        }

    workflow_id = str(route["workflow_id"])
    tenant = source_event.get("tenant") if isinstance(source_event.get("tenant"), dict) else {}
    actor = source_event.get("actor") if isinstance(source_event.get("actor"), dict) else {}
    app_id = str(tenant.get("app_id") or source_event.get("app_id") or "default")
    user_id = str(actor.get("id") or source_event.get("user_id") or "system")
    context_seed = _build_workflow_trigger_context(
        capability_id=capability_id,
        source_event=source_event,
        trigger=route.get("trigger") if isinstance(route.get("trigger"), dict) else {},
    )
    context_variables = validate_context_for_workflow(workflow_id, context_seed)
    trigger_meta = {
        "trigger_source": "module_event",
        "event_type": source_event.get("type"),
        "source_event_id": source_event.get("id"),
        "capability_id": capability_id,
        "workflow_id": workflow_id,
        "subscription_id": subscription.get("id"),
        "module_id": subscription.get("module_id"),
    }
    session_creator = create_session or create_routed_chat_session
    chat_id = await _maybe_await(
        session_creator(
            workflow_id=workflow_id,
            app_id=app_id,
            user_id=user_id,
            context_variables=context_variables,
            trigger_meta=trigger_meta,
            session_router=None,
        )
    )

    started = await _start_workflow_background_if_available(
        chat_id=str(chat_id),
        workflow_id=workflow_id,
        app_id=app_id,
        user_id=user_id,
        trigger=route.get("trigger") if isinstance(route.get("trigger"), dict) else {},
        auto_start=auto_start,
    )
    result = {
        "status": "created",
        "capability_id": capability_id,
        "workflow_id": workflow_id,
        "chat_id": str(chat_id),
        "app_id": app_id,
        "user_id": user_id,
        "started": started,
        "websocket_url": f"/ws/{workflow_id}/{app_id}/{chat_id}/{user_id}",
    }

    if event_emitter is not None:
        event = {
            "id": f"evt_{uuid4().hex}",
            "type": "platform.workflow_capability_started",
            "version": 1,
            "occurred_at": datetime.now(UTC).isoformat(),
            "source": {"layer": "platform", "capability_id": capability_id, "workflow_id": workflow_id},
            "tenant": tenant,
            "correlation": source_event.get("correlation") if isinstance(source_event.get("correlation"), dict) else {},
            "payload": {**result, "source_event_id": source_event.get("id")},
            "visibility": "internal",
        }
        await _maybe_await(event_emitter("platform.workflow_capability_started", event))
    return result


def _select_workflow_capability_route(
    *,
    capability_id: str,
    source_event_type: str,
    routes: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    candidates = routes.get(capability_id) or []
    for route in candidates:
        event_type = route.get("event_type")
        if event_type and event_type == source_event_type:
            return route
    for route in candidates:
        if not route.get("event_type"):
            return route
    return candidates[0] if candidates else None


def _build_workflow_trigger_context(
    *,
    capability_id: str,
    source_event: dict[str, Any],
    trigger: dict[str, Any],
) -> dict[str, Any]:
    payload = source_event.get("payload") if isinstance(source_event.get("payload"), dict) else {}
    context: dict[str, Any] = {
        "source_event": source_event,
        "event_payload": payload,
        "triggered_event_type": source_event.get("type"),
        "triggered_capability_id": capability_id,
    }
    trigger_context = trigger.get("context") or trigger.get("context_variables")
    if isinstance(trigger_context, dict):
        for key, value in trigger_context.items():
            key_text = str(key or "").strip()
            if key_text:
                context[key_text] = _resolve_event_context_value(value, source_event)
    return context


def _resolve_event_context_value(value: Any, source_event: dict[str, Any]) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.startswith("payload."):
        payload = source_event.get("payload") if isinstance(source_event.get("payload"), dict) else {}
        return _deep_get(payload, text.removeprefix("payload."))
    if text.startswith("event."):
        return _deep_get(source_event, text.removeprefix("event."))
    return value


def _deep_get(source: Any, dotted_path: str) -> Any:
    current = source
    for part in dotted_path.split("."):
        if not part:
            continue
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


async def _start_workflow_background_if_available(
    *,
    chat_id: str,
    workflow_id: str,
    app_id: str,
    user_id: str,
    trigger: dict[str, Any],
    auto_start: bool,
) -> bool:
    if not auto_start:
        return False
    transport = getattr(runtime_app, "simple_transport", None)
    if transport is None or not hasattr(transport, "_run_workflow_background"):
        return False
    initial_message = trigger.get("initial_message")
    initial_agent = trigger.get("initial_agent")
    task = asyncio.create_task(
        transport._run_workflow_background(
            chat_id=chat_id,
            workflow_name=workflow_id,
            app_id=app_id,
            user_id=user_id,
            ws_id=None,
            initial_message=initial_message if isinstance(initial_message, str) and initial_message.strip() else None,
            initial_agent_name_override=initial_agent if isinstance(initial_agent, str) and initial_agent.strip() else None,
        ),
        name=f"workflow:{workflow_id}:{chat_id}",
    )
    task.add_done_callback(
        lambda t: logger.error(
            "WORKFLOW_BACKGROUND_TASK_FAILED workflow=%s chat=%s: %s",
            workflow_id,
            chat_id,
            t.exception(),
        )
        if not t.cancelled() and t.exception() is not None
        else None
    )
    background_tasks = getattr(transport, "_background_tasks", None)
    if isinstance(background_tasks, dict):
        background_tasks[chat_id] = task
    return True


async def _maybe_await(result: Any) -> Any:
    if inspect.isawaitable(result):
        return await result
    return result


@app.post("/api/chats/{app_id}/{workflow_name}/start")
async def start_chat(
    app_id: str,
    workflow_name: str,
    request: Request,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_app_id(principal, app_id)
    requested_workflow_name = workflow_name
    workflow_name = _resolve_requested_workflow_name(workflow_name)
    if requested_workflow_name and requested_workflow_name != workflow_name:
        logger.debug(
            "CHAT_START_WORKFLOW_NORMALIZED: requested=%s resolved=%s app_id=%s user_id=%s",
            requested_workflow_name,
            workflow_name,
            app_id,
            principal.user_id,
        )

    try:
        data = await request.json()
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    user_id = _validate_user_id_against_principal(principal, body_user_id=data.get("user_id"))
    ok, prereq_error = await get_platform_hooks().call_chat_prereqs(
        app_id=app_id,
        user_id=user_id,
        workflow_name=workflow_name,
        persistence=persistence_manager,
    )
    if not ok:
        raise HTTPException(status_code=409, detail=prereq_error)

    client_request_id = data.get("client_request_id")
    force_new = str(data.get("force_new", "false")).lower() in {"1", "true", "yes", "on"}
    context_variables = validate_context_for_workflow(
        workflow_name,
        data.get("context_variables") if isinstance(data.get("context_variables"), dict) else {},
    )
    trigger_meta = data.get("trigger_meta") if isinstance(data.get("trigger_meta"), dict) else {}

    idempotency_window_sec = int(os.getenv("CHAT_START_IDEMPOTENCY_SEC", "15"))
    reuse_cutoff = datetime.now(UTC) - timedelta(seconds=idempotency_window_sec)
    coll = await runtime_app._chat_coll()

    if not force_new:
        base_query = {
            "user_id": user_id,
            "workflow_name": workflow_name,
            "status": 0,
            "created_at": {"$gte": reuse_cutoff},
            **build_app_scope_filter(app_id),
        }
        reused_doc = None
        if client_request_id:
            reused_doc = await coll.find_one({**base_query, "client_request_id": client_request_id}, {"chat_id": 1})
        if not reused_doc:
            reused_doc = await coll.find_one(base_query, {"chat_id": 1})
        if reused_doc:
            chat_id = reused_doc.get("chat_id") or reused_doc.get("_id")
            try:
                cache_seed = await persistence_manager.get_or_assign_cache_seed(chat_id, app_id)
            except Exception:
                cache_seed = None
            return {
                "success": True,
                "chat_id": chat_id,
                "workflow_name": workflow_name,
                "app_id": app_id,
                "user_id": user_id,
                "websocket_url": f"/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}",
                "message": "Existing recent chat reused.",
                "reused": True,
                "cache_seed": cache_seed,
            }

    chat_id = str(uuid4())
    extra_fields: dict[str, Any] = {}
    if client_request_id:
        extra_fields["client_request_id"] = client_request_id
    if trigger_meta:
        allowed_trigger_keys = {
            "trigger_source",
            "action_id",
            "change_class",
            "artifact_kind",
            "artifact_version_id",
        }
        extra_fields["trigger_meta"] = {key: value for key, value in trigger_meta.items() if key in allowed_trigger_keys}
    extra_fields.update(context_variables)

    try:
        platform_fields = await get_platform_hooks().call_chat_session_fields(
            app_id=app_id,
            user_id=user_id,
            workflow_name=workflow_name,
            chat_id=chat_id,
        )
        if platform_fields:
            extra_fields.update(platform_fields)
    except Exception as exc:
        logger.debug("platform session fields skipped: %s", exc)

    await persistence_manager.create_chat_session(
        chat_id=chat_id,
        app_id=app_id,
        workflow_name=workflow_name,
        user_id=user_id,
        extra_fields=extra_fields or None,
    )

    try:
        from mozaiksai.core.session import get_session_router

        await get_session_router().bind_workflow_session(
            app_id=app_id,
            user_id=user_id,
            workflow_id=workflow_name,
            chat_id=chat_id,
        )
    except Exception as exc:
        logger.debug("session router bind skipped for %s: %s", chat_id, exc)

    try:
        cache_seed = await persistence_manager.get_or_assign_cache_seed(chat_id, app_id)
    except Exception:
        cache_seed = None

    return {
        "success": True,
        "chat_id": chat_id,
        "workflow_name": workflow_name,
        "app_id": app_id,
        "user_id": user_id,
        "websocket_url": f"/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}",
        "message": "Chat session initialized; connect to websocket to start.",
        "reused": False,
        "cache_seed": cache_seed,
    }

async def chat_meta(
    *,
    app_id: str,
    workflow_name: str,
    chat_id: str,
    principal: Any,
) -> dict[str, Any]:
    """Return metadata dict for a chat session without emitting a WebSocket event."""
    user_id = principal.user_id
    exists = False
    last_artifact = None
    run_history_count = None
    status = None

    try:
        from mozaiksai.core.data.persistence.persistence_manager import extract_last_artifact

        coll = await runtime_app._chat_coll()
        doc = await coll.find_one(
            {"_id": chat_id, "user_id": user_id, **build_app_scope_filter(app_id)},
            {"workflow_ui_state.last_artifact": 1, "created_at": 1, "status": 1},
        )
        if doc:
            exists = True
            last_artifact = extract_last_artifact(doc)
            status = doc.get("status")
            run_history = await runtime_app.persistence_manager.load_run_history(
                chat_id=chat_id,
                app_id=app_id,
            )
            run_history_count = len(run_history)
    except Exception as meta_err:
        logger.debug("chat_meta lookup failed for %s: %s", chat_id, meta_err)

    return {
        "exists": exists,
        "chat_id": chat_id,
        "workflow_name": workflow_name,
        "app_id": app_id,
        "user_id": user_id,
        "last_artifact": last_artifact,
        "run_history_count": run_history_count,
        "status": status,
    }


@app.websocket("/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    workflow_name: str,
    app_id: str,
    chat_id: str,
    user_id: str,
):
    if not runtime_app.simple_transport:
        await websocket.close(code=1000, reason="Transport service not available")
        return

    try:
        validate_path_id(workflow_name, "workflow_name")
        validate_path_id(app_id, "app_id")
        validate_path_id(chat_id, "chat_id")
        validate_path_id(user_id, "user_id")
    except HTTPException:
        await websocket.close(code=1008, reason="Invalid path parameter")
        return

    requested_workflow_name = workflow_name
    workflow_name = _resolve_requested_workflow_name(workflow_name)

    ws_user = await authenticate_websocket_with_path_binding(
        websocket,
        path_user_id=user_id,
        path_app_id=app_id,
        path_chat_id=chat_id,
    )
    if ws_user is None:
        return
    user_id = ws_user.user_id

    try:
        coll = await runtime_app._chat_coll()
        existing = await coll.find_one(
            {"_id": chat_id, **build_app_scope_filter(app_id)},
            {"_id": 1, "user_id": 1, "workflow_name": 1},
        )
        if existing:
            owner = existing.get("user_id")
            existing_workflow = existing.get("workflow_name")
            if not owner or str(owner).strip() != str(user_id).strip():
                await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason="Chat not found")
                return
            existing_workflow_name = str(existing_workflow or "").strip()
            if existing_workflow_name:
                if not _is_runnable_workflow_name(existing_workflow_name):
                    logger.warning(
                        "WS_CHAT_WORKFLOW_REPAIRED: chat_id=%s old=%s new=%s",
                        chat_id,
                        existing_workflow_name,
                        workflow_name,
                    )
                    try:
                        await coll.update_one(
                            {"_id": chat_id, **build_app_scope_filter(app_id)},
                            {"$set": {"workflow_name": workflow_name, "last_updated_at": datetime.now(UTC)}},
                        )
                    except Exception as repair_err:
                        logger.debug("WS_CHAT_WORKFLOW_REPAIR_FAILED for %s: %s", chat_id, repair_err)
                elif existing_workflow_name != workflow_name:
                    # Allow stale client URLs by honoring persisted workflow ownership.
                    workflow_name = existing_workflow_name
    except Exception as ownership_err:
        logger.debug("WS_CHAT_OWNERSHIP_CHECK_SKIPPED: %s", ownership_err)

    if requested_workflow_name and requested_workflow_name != workflow_name:
        logger.debug(
            "WS_WORKFLOW_NORMALIZED: requested=%s resolved=%s app_id=%s chat_id=%s user_id=%s",
            requested_workflow_name,
            workflow_name,
            app_id,
            chat_id,
            user_id,
        )

    from mozaiksai.core.transport.session_registry import session_registry

    ws_id = id(websocket)

    try:
        is_valid, error_msg = await get_platform_hooks().call_chat_prereqs(
            app_id=app_id,
            user_id=user_id,
            workflow_name=workflow_name,
            persistence=persistence_manager,
        )
        if not is_valid:
            try:
                await websocket.accept()
                await websocket.send_json({
                    "type": "chat.error",
                    "data": {
                        "message": error_msg,
                        "error_code": "WORKFLOW_PREREQS_NOT_MET",
                        "workflow_name": workflow_name,
                        "chat_id": chat_id,
                    },
                    "timestamp": datetime.now(UTC).isoformat(),
                })
            except Exception as _prereq_send_exc:
                logger.debug("WS_PREREQ_ERROR_SEND_FAILED chat=%s: %s", chat_id, _prereq_send_exc)
            await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason="Prerequisites not met")
            return
    except Exception as dep_err:
        logger.error("WS_PREREQ_VALIDATION_FAILED: %s", dep_err, exc_info=True)
        try:
            await websocket.accept()
            await websocket.send_json({
                "type": "chat.error",
                "data": {
                    "message": "Failed to validate workflow prerequisites. Please try again.",
                    "error_code": "PREREQ_VALIDATION_ERROR",
                    "workflow_name": workflow_name,
                    "chat_id": chat_id,
                },
                "timestamp": datetime.now(UTC).isoformat(),
            })
        except Exception as _err_send_exc:
            logger.debug("WS_PREREQ_VALIDATION_ERROR_SEND_FAILED chat=%s: %s", chat_id, _err_send_exc)
        await websocket.close(code=1011, reason="Prerequisite validation failed")
        return

    active_chat_id = chat_id
    session_state_payload: dict[str, Any] | None = None
    try:
        from mozaiksai.core.session import get_session_router

        session_router = get_session_router()
        resume_resolution = await session_router.resolve_resume(
            app_id=app_id,
            user_id=user_id,
            requested_workflow_id=workflow_name,
            requested_chat_id=chat_id,
        )
        resolved_chat_id = str(resume_resolution.get("chat_id") or "").strip()
        if resolved_chat_id:
            active_chat_id = resolved_chat_id
        session_state_payload = resume_resolution.get("session_state") or None

        coll = await runtime_app._chat_coll()
        existing_doc = await coll.find_one(
            {"_id": active_chat_id, "user_id": user_id, **build_app_scope_filter(app_id)},
            {"_id": 1},
        )
        if not existing_doc:
            await persistence_manager.create_chat_session(active_chat_id, app_id, workflow_name, user_id)
            await session_router.bind_workflow_session(
                app_id=app_id,
                user_id=user_id,
                workflow_id=workflow_name,
                chat_id=active_chat_id,
            )
            session_state_payload = await session_router.get_session_snapshot(app_id=app_id, user_id=user_id)
    except Exception as pre_err:
        logger.error("WS_SESSION_DETERMINATION_FAILED: %s", pre_err)

    async def _auto_start_if_needed() -> None:
        try:
            from mozaiksai.core.workflow.workflow_manager import workflow_manager

            try:
                if os.getenv("ENVIRONMENT", "development").lower() != "production":
                    workflow_manager.reload_workflow(workflow_name)
            except Exception as reload_err:
                logger.debug("Workflow hot-reload skipped for %s: %s", workflow_name, reload_err)

            cfg = workflow_manager.get_config(workflow_name) or {}
            startup_mode = str(cfg.get("workflow_startup_mode") or "").strip() or "AgentDriven"
            if startup_mode != "AgentDriven":
                return

            coll = await runtime_app._chat_coll()
            chat_doc = await coll.find_one(
                {"_id": active_chat_id, "user_id": user_id, **build_app_scope_filter(app_id)},
                {"status": 1},
            )
            if not chat_doc:
                return
            if int(chat_doc.get("status", -1)) != 0:
                return
            run_history = await runtime_app.persistence_manager.load_run_history(
                chat_id=active_chat_id,
                app_id=app_id,
            )
            if run_history:
                return

            local_transport = runtime_app.simple_transport
            if not local_transport:
                return

            for _ in range(20):
                conn = local_transport.connections.get(active_chat_id)
                if conn and conn.get("websocket") is not None:
                    if conn.get("autostarted"):
                        return
                    conn["autostarted"] = True
                    break
                await asyncio.sleep(0.1)

            await local_transport.handle_user_input_from_api(
                chat_id=active_chat_id,
                user_id=user_id,
                workflow_name=workflow_name,
                message=None,
                app_id=app_id,
            )
        except Exception as exc:
            logger.error("Auto-start failed for %s/%s: %s", workflow_name, active_chat_id, exc)

    _task = asyncio.create_task(_auto_start_if_needed())
    _task.add_done_callback(
        lambda t: logger.error(
            "Auto-start task raised unexpected error for %s/%s: %s",
            workflow_name,
            active_chat_id,
            t.exception(),
        )
        if not t.cancelled() and t.exception() is not None
        else None
    )

    try:
        has_children = False

        chat_exists_flag = False
        coll = None
        try:
            coll = await runtime_app._chat_coll()
            existing_doc = await coll.find_one(
                {"_id": active_chat_id, "user_id": user_id, **build_app_scope_filter(app_id)},
                {"_id": 1},
            )
            chat_exists_flag = existing_doc is not None
        except Exception as chat_err:
            logger.debug("chat existence check failed for %s: %s", active_chat_id, chat_err)

        if not chat_exists_flag:
            try:
                await persistence_manager.create_chat_session(active_chat_id, app_id, workflow_name, user_id)
                try:
                    from mozaiksai.core.session import get_session_router

                    await get_session_router().bind_workflow_session(
                        app_id=app_id,
                        user_id=user_id,
                        workflow_id=workflow_name,
                        chat_id=active_chat_id,
                    )
                except Exception as bind_err:
                    logger.debug("WS backfill bind skipped for %s: %s", active_chat_id, bind_err)
                chat_exists_flag = True
            except Exception as create_err:
                logger.debug("Failed to backfill chat session for %s: %s", active_chat_id, create_err)

        try:
            from mozaiksai.core.data.persistence.persistence_manager import extract_last_artifact

            cache_seed = await persistence_manager.get_or_assign_cache_seed(active_chat_id, app_id)
        except Exception as seed_err:
            cache_seed = None
            logger.debug("cache_seed retrieval failed for WS %s: %s", active_chat_id, seed_err)

        if session_state_payload is None:
            try:
                from mozaiksai.core.session import get_session_router

                session_state_payload = await get_session_router().get_session_snapshot(app_id=app_id, user_id=user_id)
            except Exception as session_err:
                logger.debug("session snapshot unavailable for %s: %s", active_chat_id, session_err)

        if runtime_app.simple_transport:
            last_artifact = None
            created_at_iso = None
            doc = None
            run_history_count = None
            try:
                if coll is not None:
                    doc = await coll.find_one(
                        {"_id": active_chat_id, "user_id": user_id, **build_app_scope_filter(app_id)},
                        {"workflow_ui_state.last_artifact": 1, "created_at": 1, "status": 1},
                    )
                    if doc:
                        last_artifact = extract_last_artifact(doc)
                        created_at = doc.get("created_at")
                        if created_at:
                            try:
                                created_at_iso = created_at.isoformat()
                            except Exception:
                                created_at_iso = str(created_at)
                        run_history = await runtime_app.persistence_manager.load_run_history(
                            chat_id=active_chat_id,
                            app_id=app_id,
                        )
                        run_history_count = len(run_history)
            except Exception as artifact_err:
                logger.debug("last_artifact fetch failed for chat_meta %s: %s", active_chat_id, artifact_err)

            await runtime_app.simple_transport.send_event_to_ui(
                {
                    "kind": "chat_meta",
                    "chat_id": active_chat_id,
                    "workflow_name": workflow_name,
                    "app_id": app_id,
                    "user_id": user_id,
                    "has_children": has_children,
                    "cache_seed": cache_seed,
                    "chat_exists": chat_exists_flag,
                    "last_artifact": last_artifact,
                    "status": doc.get("status") if doc else None,
                    "run_history_count": run_history_count,
                    "created_at": created_at_iso,
                    "session_state": session_state_payload,
                },
                active_chat_id,
            )
    except Exception as meta_err:
        logger.debug("Failed to emit chat_meta for %s: %s", active_chat_id, meta_err)

    session_registry.add_workflow(
        ws_id=ws_id,
        chat_id=active_chat_id,
        workflow_name=workflow_name,
        app_id=app_id,
        user_id=user_id,
        auto_activate=True,
    )

    try:
        await runtime_app.simple_transport.handle_websocket(
            websocket=websocket,
            chat_id=active_chat_id,
            user_id=user_id,
            workflow_name=workflow_name,
            app_id=app_id,
            ws_id=ws_id,
        )
    finally:
        session_registry.remove_session(ws_id)
        logger.debug("SESSION_REGISTRY_CLEANUP ws_id=%s", ws_id)


_PLATFORM_OVERRIDE_PATHS = frozenset({
    "/api/chats/{app_id}/{workflow_name}/start",
    "/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}",
})

app.router.routes[:] = sorted(
    app.router.routes,
    key=lambda route: (
        0
        if getattr(route, "path", None) in _PLATFORM_OVERRIDE_PATHS
        and getattr(getattr(route, "endpoint", None), "__module__", "") == __name__
        else 1
    ),
)

from __future__ import annotations

"""Platform composition host layered on top of mozaiksai.hosts.runtime."""

from contextlib import asynccontextmanager
import json
import os
import re
import asyncio
import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

import yaml
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from mozaiksai.hosts import runtime as runtime_app
from logs.logging_config import get_workflow_logger
from mozaiksai.core.auth import (
    UserPrincipal,
    WS_CLOSE_POLICY_VIOLATION,
    authenticate_websocket_with_path_binding,
    optional_user,
    require_any_auth,
    require_user_scope,
)
from mozaiksai.core.auth.dependencies import (
    validate_path_app_id,
    validate_user_id_against_principal as _validate_user_id_against_principal,
)
from mozaiksai.core.multitenant import build_app_scope_filter
from mozaiksai.core.chat_attachments.attachments import handle_chat_upload
from mozaiksai.core.observability.performance_manager import get_performance_manager
from mozaiksai.core.runtime.app.loader import AppLoadError, AppLoader
from mozaiksai.core.runtime.composition.executor_registry import ExecutorRegistry
from mozaiksai.core.runtime.composition.extensions import (
    mount_declared_routers,
    start_declared_services,
    stop_services,
)
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from mozaiksai.core.runtime.composition.module_event_router import ModuleEventRouter
from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks
from mozaiksai.core.session.launcher import create_routed_chat_session, launch_transition, validate_context_for_workflow
from mozaiksai.core.workflow.paths import resolve_active_app_root
from mozaiksai.core.admin.contract import build_admin_shell_routes


app = runtime_app.app
persistence_manager = runtime_app.persistence_manager
logger = get_workflow_logger("platform_app")

executor_registry = ExecutorRegistry()
app.state.executor_registry = executor_registry
_runtime_services: list[Any] = []
_DEFAULT_PROFILE_USER_ID = os.getenv("MOZAIKS_DEFAULT_USER_ID", "demo-user").strip() or "demo-user"
_ACCOUNT_PROFILE_COLLECTION = "UserProfiles"
_ACCOUNT_PREFERENCES_COLLECTION = "UserPreferences"


try:
    mount_declared_routers(app)
except Exception as exc:  # pragma: no cover
    logger.debug("RUNTIME_EXTENSIONS_MOUNT_FAILED: %s", exc)

try:
    from mozaiksai.core.admin import router as admin_router

    app.include_router(admin_router)
except Exception as exc:  # pragma: no cover
    logger.debug("ADMIN_ROUTER_MOUNT_FAILED: %s", exc)


def resolve_platform_path() -> Path:
    return resolve_active_app_root()


_NON_RUNNABLE_WORKFLOW_IDS = {"extended_orchestration"}


def _get_ordered_workflow_names() -> List[str]:
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    return get_platform_hooks().call_workflow_ordering(sorted(workflow_manager.get_all_workflow_names()))


def _get_configured_entry_point() -> Optional[str]:
    platform_root = resolve_platform_path()
    ai_path = platform_root / "config" / "ai.json"
    if not ai_path.exists():
        ai_path = Path(__file__).parent / "platform" / "config" / "ai.json"
    if not ai_path.exists():
        return None

    try:
        ai = json.loads(ai_path.read_text(encoding="utf-8"))
        candidate = ((ai.get("workflows") or {}).get("entry_point") or "").strip()
        return candidate or None
    except Exception:
        return None


def _is_runnable_workflow_name(workflow_name: Optional[str], ordered_names: Optional[List[str]] = None) -> bool:
    name = str(workflow_name or "").strip()
    if not name:
        return False
    if name in _NON_RUNNABLE_WORKFLOW_IDS:
        return False
    names = ordered_names if ordered_names is not None else _get_ordered_workflow_names()
    return any(name.lower() == loaded.lower() for loaded in names)


def _resolve_requested_workflow_name(requested_workflow_name: Optional[str]) -> str:
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

    try:
        _runtime_services = await start_declared_services()
    except Exception as exc:
        logger.debug("RUNTIME_EXTENSIONS_SERVICES_NOT_STARTED: %s", exc)

    platform_root = resolve_platform_path()
    try:
        load_result = await AppLoader.load(str(platform_root))
        if load_result.modules:
            from mozaiksai.core.events import get_event_dispatcher

            dispatcher = get_event_dispatcher()
            workflow_capability_routes = _load_workflow_capability_routes(platform_root)
            app.state.workflow_capability_routes = workflow_capability_routes

            async def invoke_capability(
                capability_id: str,
                source_event: Dict[str, Any],
                subscription: Dict[str, Any],
            ) -> Dict[str, Any]:
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

            module_executor = ModuleExecutor(event_emitter=dispatcher.emit)
            for loaded_module in load_result.modules:
                module_executor.register(
                    loaded_module.name,
                    loaded_module.handler,
                    action_method_map=loaded_module.action_method_map,
                )
            executor_registry.register(module_executor)
            logger.info("MODULE_EXECUTOR_READY: %s module(s)", len(load_result.modules))
    except AppLoadError:
        logger.debug("APP_LOAD_SKIPPED: app.json not found for platform host")
    except Exception as exc:
        logger.warning("APP_LOAD_FAILED: %s", exc)

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
    except Exception:
        pass
    _runtime_services = []


@asynccontextmanager
async def platform_lifespan(_: FastAPI) -> AsyncIterator[None]:
    await _platform_startup()
    try:
        yield
    finally:
        await _platform_shutdown()


runtime_app.register_app_lifespan(app, platform_lifespan)


STUDIO_SHELL_ROUTES: tuple[dict[str, Any], ...] = (
    {
        "path": "/studio",
        "component": "StudioHomePage",
        "label": "Studio",
        "order": 5,
        "title": "Studio Home",
    },
    {
        "path": "/studio/create",
        "component": "StudioCreatePage",
        "label": "Create",
        "order": 6,
        "title": "Studio Create",
    },
    {
        "path": "/studio/adapters",
        "component": "StudioAdaptersPage",
        "label": "Adapters",
        "order": 7,
        "title": "Studio Adapters",
    },
)


PROFILE_SHELL_ROUTE = {
    "path": "/profile",
    "component": "ProfilePage",
    "label": "Profile",
    "order": 998,
    "title": "Profile",
}


def _append_page_once(pages: List[dict], page: dict) -> None:
    path = page.get("path")
    if not isinstance(path, str) or any(existing.get("path") == path for existing in pages):
        return
    pages.append(page)


async def build_shell_config(*, include_studio: bool = False) -> dict:
    """Compose app-shell config from platform-owned manifests."""
    platform_root = resolve_platform_path()
    ai_path = platform_root / "config" / "ai.json"
    if not ai_path.exists():
        ai_path = Path(__file__).parent / "platform" / "config" / "ai.json"

    result: dict = {"chat_startup_mode": "ask", "landing_spot": "/"}

    try:
        app_manifest_path = _resolve_app_manifest_path()
        if app_manifest_path.exists():
            app_manifest = json.loads(app_manifest_path.read_text(encoding="utf-8"))
            startup = app_manifest.get("startup") if isinstance(app_manifest.get("startup"), dict) else {}
            landing_spot = startup.get("landing_spot")
            if isinstance(landing_spot, str) and landing_spot.startswith("/"):
                result["landing_spot"] = landing_spot
    except Exception as exc:
        logger.warning("[shell-config] Could not read app startup config: %s", exc)

    if ai_path.exists():
        try:
            ai = json.loads(ai_path.read_text(encoding="utf-8"))
            chat = ai.get("chat") or {}
            workflows = ai.get("workflows") or {}
            result["chat_startup_mode"] = chat.get("chat_startup_mode") or chat.get("startup_mode") or "ask"
            result["entry_point"] = workflows.get("entry_point")
            result["resume_policy"] = workflows.get("resume_policy")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to read shell config: {exc}") from exc

    try:
        shell_config_path = _resolve_shell_config_path()
        if shell_config_path.exists():
            shell_config = json.loads(shell_config_path.read_text(encoding="utf-8"))
            for key in ("header", "profile", "notifications", "footer"):
                value = shell_config.get(key)
                if value is not None:
                    result[key] = value
    except Exception as exc:
        logger.warning("[shell-config] Could not read shell config: %s", exc)

    pages: List[dict] = []
    for loader, label in (
        (_load_ui_route_manifest_pages, "UI route manifest pages"),
        (_load_page_schema_routes, "page schema routes"),
        (_load_workflow_entrypoint_pages, "workflow entrypoint routes"),
    ):
        try:
            pages.extend(loader(platform_root))
        except Exception as exc:
            logger.warning("[shell-config] Could not read %s: %s", label, exc)

    if pages:
        result["pages"] = _dedupe_and_sort_pages(pages)

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
            },
        },
    )
    result["pages"] = _dedupe_and_sort_pages(pages)

    admin_config_path = platform_root / "config" / "admin.json"
    if admin_config_path.exists():
        try:
            admin_cfg = json.loads(admin_config_path.read_text(encoding="utf-8"))
            if admin_cfg.get("enabled", True):
                pages = result.get("pages", [])
                for route in build_admin_shell_routes(admin_cfg.get("sections")):
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
                            "adminSection": route["admin_section"],
                        },
                    })
                result["pages"] = _dedupe_and_sort_pages(pages)
        except Exception as exc:
            logger.warning("[shell-config] Could not read admin.json: %s", exc)

    if include_studio:
        pages = result.get("pages", [])
        for route in STUDIO_SHELL_ROUTES:
            _append_page_once(pages, {
                "path": route["path"],
                "component": route["component"],
                "label": route["label"],
                "order": route["order"],
                "meta": {
                    "requiresAuth": True,
                    "requiresRole": "admin",
                    "title": route["title"],
                    "appShell": True,
                },
            })
        result["pages"] = _dedupe_and_sort_pages(pages)

    return result


@app.get("/api/shell-config")
async def get_shell_config():
    return await build_shell_config(include_studio=False)


@app.get("/api/me")
async def get_current_user_profile(
    app_id: Optional[str] = None,
    principal: UserPrincipal = Depends(require_any_auth),
):
    resolved_app_id, user_id = _resolve_profile_scope(principal, app_id=app_id)
    return await _ensure_account_profile(principal, app_id=resolved_app_id, user_id=user_id)


@app.put("/api/me")
async def update_current_user_profile(
    body: ProfileUpdateRequest,
    app_id: Optional[str] = None,
    principal: UserPrincipal = Depends(require_any_auth),
):
    resolved_app_id, user_id = _resolve_profile_scope(principal, app_id=app_id)
    profile = await _ensure_account_profile(principal, app_id=resolved_app_id, user_id=user_id)

    updates: dict[str, Any] = {}
    payload = body.model_dump(exclude_unset=True)
    if "display_name" in payload:
        value = payload.get("display_name")
        updates["display_name"] = value.strip() if isinstance(value, str) and value.strip() else None
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
    app_id: Optional[str] = None,
    principal: UserPrincipal = Depends(require_any_auth),
):
    resolved_app_id, user_id = _resolve_profile_scope(principal, app_id=app_id)
    return await _load_account_preferences(app_id=resolved_app_id, user_id=user_id)


@app.put("/api/me/preferences")
async def update_current_user_preferences(
    body: ProfilePreferencesUpdateRequest,
    app_id: Optional[str] = None,
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


def _normalize_shell_page_entry(entry: dict, *, order_fallback: int) -> Optional[dict]:
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
        "meta": {**meta, "requiresAuth": entry.get("requiresAuth", True)},
    }
    if isinstance(component, str) and component.strip():
        page["component"] = component.strip()
    if isinstance(transition, str) and transition.strip():
        page["transition"] = transition.strip()
    if isinstance(workflow, str) and workflow.strip():
        page["workflow"] = workflow.strip()
    if isinstance(entry.get("schema"), str) and entry["schema"].strip():
        page["schema"] = entry["schema"].strip()
    return page


def _load_ui_route_manifest_pages(platform_root: Path) -> List[dict]:
    manifest_path = (platform_root / "ui" / "route_manifest.json").resolve()
    if manifest_path is None:
        return []
    if not manifest_path.exists():
        return []
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = raw.get("pages") if isinstance(raw, dict) else []
    if not isinstance(entries, list):
        return []
    pages: List[dict] = []
    for index, entry in enumerate(entries):
        page = _normalize_shell_page_entry(entry, order_fallback=index)
        if page:
            pages.append(page)
    return pages


def _load_page_schema_routes(platform_root: Path) -> List[dict]:
    pages_dir = platform_root / "ui" / "pages"
    if not pages_dir.exists():
        return []

    candidates: List[tuple[Path, str]] = []
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

    pages: List[dict] = []
    for index, (page_path, default_name) in enumerate(candidates):
        raw = yaml.safe_load(page_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            continue
        route = raw.get("route")
        if not isinstance(route, str) or not route.startswith("/"):
            continue
        name = str(raw.get("name") or default_name).strip() or default_name
        title = str(raw.get("title") or name).strip()
        roles = raw.get("roles")
        meta: dict = {"title": title, "appShell": True, "requiresAuth": True}
        if isinstance(roles, list) and roles:
            meta["roles"] = roles
        pages.append({
            "path": route,
            "label": title,
            "component": "SchemaPage",
            "schema": name,
            "order": 100 + index,
            "meta": meta,
        })
    return pages


def _load_workflow_entrypoint_pages(platform_root: Path) -> List[dict]:
    from mozaiksai.core.workflow.pack.config import list_entrypoints, load_global_pack_graph

    _ = platform_root
    pack = load_global_pack_graph()
    if pack is None:
        return []

    pages: List[dict] = []
    for index, entry in enumerate(list_entrypoints(pack)):
        page = _normalize_shell_page_entry(entry.model_dump(exclude_none=True), order_fallback=200 + index)
        if page:
            pages.append(page)
    return pages


def _dedupe_and_sort_pages(pages: List[dict]) -> List[dict]:
    by_path: Dict[str, dict] = {}
    for page in pages:
        path = page.get("path")
        if isinstance(path, str) and path not in by_path:
            by_path[path] = page
    return sorted(
        by_path.values(),
        key=lambda page: (page.get("order", 0), str(page.get("label") or page.get("path") or "")),
    )


def _resolve_theme_config_path() -> Path:
    platform_root = resolve_platform_path()
    return (platform_root / "brand" / "theme_config.json").resolve()


def _resolve_shell_config_path() -> Path:
    platform_root = resolve_platform_path()
    return (platform_root / "config" / "shell.json").resolve()


def _resolve_app_manifest_path() -> Path:
    platform_root = resolve_platform_path()
    return (platform_root / "app.json").resolve()


def _resolve_default_app_id() -> str:
    app_manifest_path = _resolve_app_manifest_path()
    if not app_manifest_path.exists():
        return "default"
    try:
        manifest = json.loads(app_manifest_path.read_text(encoding="utf-8"))
    except Exception:
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
    assert client is not None, "Mongo client not initialized"
    return client["mozaiksai"][_ACCOUNT_PROFILE_COLLECTION]


async def _account_preferences_collection():
    await persistence_manager.persistence._ensure_client()
    client = persistence_manager.persistence.client
    assert client is not None, "Mongo client not initialized"
    return client["mozaiksai"][_ACCOUNT_PREFERENCES_COLLECTION]


def _resolve_profile_scope(
    principal: UserPrincipal,
    *,
    app_id: Optional[str] = None,
) -> tuple[str, str]:
    return resolve_scope_from_principal(
        principal,
        app_id=app_id,
        default_user_id=_DEFAULT_PROFILE_USER_ID,
        default_app_id=_resolve_default_app_id(),
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
    display_name: Optional[str] = Field(default=None, description="Preferred user-facing display name")
    avatar_url: Optional[str] = Field(default=None, description="Optional avatar image URL")


class ProfilePreferencesUpdateRequest(BaseModel):
    settings: Dict[str, Any] = Field(default_factory=dict, description="App-scoped account preference map")


def _resolve_pages_dir() -> Path:
    return (resolve_platform_path() / "ui" / "pages").resolve()


def _resolve_page_schema_path(name: str) -> Path:
    pages_dir = _resolve_pages_dir()
    candidates = (
        pages_dir / f"{name}.yaml",
        pages_dir / f"{name}.yml",
        pages_dir / name / "page.yaml",
        pages_dir / name / "page.yml",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


@app.get("/api/theme-config")
async def get_theme_config():
    config_path = _resolve_theme_config_path()
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="Theme config not found")
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read theme config: {exc}") from exc


@app.get("/api/themes/{app_id}")
async def get_app_theme(app_id: str):
    _ = app_id
    return await get_theme_config()


@app.get("/api/pages/{name}")
async def get_page_schema(name: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise HTTPException(status_code=400, detail="Invalid page name")

    page_path = _resolve_page_schema_path(name)
    if not page_path.exists():
        raise HTTPException(status_code=404, detail=f"Page '{name}' not found")

    try:
        schema = yaml.safe_load(page_path.read_text(encoding="utf-8"))
        if not isinstance(schema, dict):
            raise ValueError("Page schema must be a YAML mapping")
        return JSONResponse(content=schema)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read page schema: {exc}") from exc


def _load_pack_graph_or_404():
    from mozaiksai.core.workflow.pack.config import load_global_pack_graph

    pack = load_global_pack_graph()
    if pack is None:
        raise HTTPException(status_code=404, detail="No extension registry found")
    return pack


def _load_workflow_capability_routes(platform_root: Path) -> Dict[str, List[Dict[str, Any]]]:
    """Index workflow trigger declarations by public capability id."""
    workflows_dir = platform_root / "workflows"
    if not workflows_dir.exists():
        return {}

    routes: Dict[str, List[Dict[str, Any]]] = {}
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

def _trigger_capability_ids(trigger: Dict[str, Any]) -> List[str]:
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
    source_event: Dict[str, Any],
    subscription: Dict[str, Any],
    routes: Dict[str, List[Dict[str, Any]]],
    event_emitter: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
    create_session: Optional[Callable[..., Any]] = None,
    auto_start: bool = True,
)-> Dict[str, Any]:
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
    routes: Dict[str, List[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
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
    source_event: Dict[str, Any],
    trigger: Dict[str, Any],
) -> Dict[str, Any]:
    payload = source_event.get("payload") if isinstance(source_event.get("payload"), dict) else {}
    context: Dict[str, Any] = {
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


def _resolve_event_context_value(value: Any, source_event: Dict[str, Any]) -> Any:
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
    trigger: Dict[str, Any],
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
    background_tasks = getattr(transport, "_background_tasks", None)
    if isinstance(background_tasks, dict):
        background_tasks[chat_id] = task
    return True


async def _maybe_await(result: Any) -> Any:
    if inspect.isawaitable(result):
        return await result
    return result


def _extract_bearer_token(request: Request) -> Optional[str]:
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth_header:
        return None
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip() or None
    return auth_header.strip() or None


async def _execute_module_action(
    *,
    module_name: str,
    action_name: str,
    request: Request,
    principal: Optional[UserPrincipal],
    params: Dict[str, Any],
    context_overrides: Optional[Dict[str, Any]] = None,
) -> Any:
    module_executor = executor_registry.module_executor
    if module_executor is None:
        raise HTTPException(
            status_code=503,
            detail="Module runtime is not available. Verify modules/*/module.yaml handlers are loaded.",
        )

    context_overrides = context_overrides or {}
    app_id = (
        context_overrides.get("app_id")
        or request.query_params.get("app_id")
        or (principal.app_id if principal else None)
        or "default"
    )
    tenant_id = (
        context_overrides.get("tenant_id")
        or request.query_params.get("tenant_id")
        or (principal.tenant_id if principal else None)
    )
    correlation_id = context_overrides.get("correlation_id") or request.query_params.get("correlation_id") or str(uuid4())
    auth_token = context_overrides.get("auth_token") or _extract_bearer_token(request)
    user_id = context_overrides.get("user_id") or (principal.user_id if principal else None)

    module_request = ModuleRequest(
        module=module_name,
        action=action_name,
        params=params,
        app_id=str(app_id),
        user_id=str(user_id) if user_id else None,
        tenant_id=str(tenant_id) if tenant_id else None,
        auth_token=str(auth_token) if auth_token else None,
        correlation_id=str(correlation_id) if correlation_id else None,
    )

    result = await module_executor.execute(module_request, context=None)
    if result.success:
        return result.data if result.data is not None else {}

    if result.error_code in {"MODULE_NOT_FOUND", "ACTION_NOT_FOUND"}:
        status_code = 404
    elif result.error_code == "INVALID_PARAMS":
        status_code = 400
    else:
        status_code = 500

    raise HTTPException(
        status_code=status_code,
        detail={
            "error": result.error or "Module action failed",
            "error_code": result.error_code or "EXECUTION_ERROR",
            "module": module_name,
            "action": action_name,
        },
    )


@app.get("/api/modules/{module_name}/{action_name}")
async def execute_module_action_get(
    module_name: str,
    action_name: str,
    request: Request,
    principal: Optional[UserPrincipal] = Depends(optional_user),
):
    reserved_keys = {"app_id", "user_id", "tenant_id", "correlation_id", "auth_token"}
    params = {key: value for key, value in request.query_params.items() if key not in reserved_keys}
    return await _execute_module_action(
        module_name=module_name,
        action_name=action_name,
        request=request,
        principal=principal,
        params=params,
    )


@app.post("/api/modules/{module_name}/{action_name}")
async def execute_module_action_post(
    module_name: str,
    action_name: str,
    request: Request,
    principal: Optional[UserPrincipal] = Depends(optional_user),
):
    body: Dict[str, Any] = {}
    if request.headers.get("content-type", "").lower().startswith("application/json"):
        try:
            parsed = await request.json()
            if isinstance(parsed, dict):
                body = parsed
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc

    params = dict(body.get("params") or {}) if isinstance(body.get("params"), dict) else dict(body)
    context_overrides = body.get("context") if isinstance(body.get("context"), dict) else {}

    # Route reserved execution-context fields away from action params.
    # This keeps handler signatures clean while still honoring explicit scope overrides.
    reserved_context_keys = ("app_id", "user_id", "tenant_id", "correlation_id", "auth_token")
    for key in reserved_context_keys:
        if key in params and key not in context_overrides:
            context_overrides[key] = params[key]

    params.pop("context", None)
    params.pop("params", None)
    for key in reserved_context_keys:
        params.pop(key, None)

    return await _execute_module_action(
        module_name=module_name,
        action_name=action_name,
        request=request,
        principal=principal,
        params=params,
        context_overrides=context_overrides,
    )


@app.get("/api/transitions/{transition_id}")
async def get_transition_by_id(transition_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", transition_id):
        raise HTTPException(status_code=400, detail="Invalid transition id")

    pack = _load_pack_graph_or_404()
    from mozaiksai.core.workflow.pack.config import get_transition

    transition = get_transition(pack, transition_id)
    if transition is None:
        raise HTTPException(status_code=404, detail=f"Transition '{transition_id}' not found")
    return transition.model_dump(exclude_none=True)


class TransitionResolveRequest(BaseModel):
    transition_id: str
    option_id: Optional[str] = None
    context_variables: Dict[str, Any] = Field(default_factory=dict)
    app_id: Optional[str] = None
    user_id: Optional[str] = None


def resolve_scope_from_principal(
    principal: UserPrincipal,
    *,
    app_id: Optional[str] = None,
    user_id: Optional[str] = None,
    default_user_id: Optional[str] = None,
    default_app_id: str = "default",
) -> Tuple[str, str]:
    effective_user_id = user_id
    if principal.user_id == "anonymous" and not effective_user_id:
        effective_user_id = str(default_user_id or "").strip() or None

    resolved_user_id = _validate_user_id_against_principal(principal, body_user_id=effective_user_id)

    provided_app_id = str(app_id or "").strip() or None
    principal_app_id = str(principal.app_id or "").strip() or None
    if principal_app_id and provided_app_id and provided_app_id != principal_app_id:
        raise HTTPException(status_code=403, detail="app_id in request body does not match authenticated app scope")

    resolved_app_id = principal_app_id or provided_app_id or default_app_id
    if not resolved_app_id:
        raise HTTPException(status_code=400, detail="app_id is required")
    return resolved_app_id, resolved_user_id


@app.post("/api/transitions/resolve")
async def resolve_transition_route(
    body: TransitionResolveRequest,
    principal: UserPrincipal = Depends(require_user_scope),
):
    try:
        app_id, user_id = resolve_scope_from_principal(principal, app_id=body.app_id, user_id=body.user_id)
        launch_result = await launch_transition(
            app_id=app_id,
            user_id=user_id,
            transition_id=body.transition_id,
            option_id=body.option_id,
            context_variables=body.context_variables or {},
        )
    except ValueError as route_err:
        raise HTTPException(status_code=400, detail=str(route_err)) from route_err
    except Exception as route_err:
        logger.error("Transition resolution failed: %s", route_err, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to resolve transition: {route_err}") from route_err

    if launch_result.resolution_type == "transition":
        if launch_result.transition is None or not launch_result.next_transition_id:
            raise HTTPException(status_code=500, detail="Transition launch did not return the next transition")
        return {
            "resolution_type": "transition",
            "transition_id": body.transition_id,
            "option_id": launch_result.option_id,
            "next_transition_id": launch_result.next_transition_id,
            "transition": launch_result.transition.model_dump(exclude_none=True),
            "context_variables": launch_result.context_variables,
        }

    workflow_launch = launch_result.workflow_launch
    if workflow_launch is None:
        raise HTTPException(status_code=500, detail="Workflow transition launch did not start a workflow")

    return {
        "resolution_type": "workflow",
        "chat_id": workflow_launch.chat_id,
        "workflow_id": workflow_launch.workflow_id,
        "option_id": launch_result.option_id,
        "requested_workflow_id": workflow_launch.requested_workflow_id,
        "websocket_url": workflow_launch.websocket_url,
        "routing_explanation": workflow_launch.routing_explanation,
        "rerouted_by_dependency": workflow_launch.rerouted_by_dependency,
    }


@app.get("/api/session/state")
async def get_session_state(
    principal: UserPrincipal = Depends(require_user_scope),
):
    from mozaiksai.core.session import get_session_router

    snapshot = await get_session_router().get_session_snapshot(app_id=principal.app_id, user_id=principal.user_id)
    return {"session_state": snapshot}


class SessionApprovalAwaitRequest(BaseModel):
    approval_id: str
    workflow_id: Optional[str] = None
    chat_id: Optional[str] = None


@app.post("/api/session/approvals/await")
async def mark_session_awaiting_approval(
    body: SessionApprovalAwaitRequest,
    principal: UserPrincipal = Depends(require_user_scope),
):
    from mozaiksai.core.session import get_session_router

    snapshot = await get_session_router().mark_awaiting_approval(
        app_id=principal.app_id,
        user_id=principal.user_id,
        approval_id=body.approval_id,
        workflow_id=body.workflow_id,
        chat_id=body.chat_id,
    )
    return {"session_state": snapshot}


class SessionApprovalResolveRequest(BaseModel):
    approval_id: str
    approved: bool = True


@app.post("/api/session/approvals/resolve")
async def resolve_session_approval(
    body: SessionApprovalResolveRequest,
    principal: UserPrincipal = Depends(require_user_scope),
):
    from mozaiksai.core.session import get_session_router

    snapshot = await get_session_router().resolve_approval(
        app_id=principal.app_id,
        user_id=principal.user_id,
        approval_id=body.approval_id,
        approved=body.approved,
    )
    return {"session_state": snapshot}


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
        logger.info(
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
    extra_fields: Dict[str, Any] = {}
    if client_request_id:
        extra_fields["client_request_id"] = client_request_id
    if trigger_meta:
        allowed_trigger_keys = {"trigger_source", "action_id", "change_class", "artifact_version_id"}
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
        perf_mgr = await get_performance_manager()
        await perf_mgr.record_workflow_start(chat_id, app_id, workflow_name, user_id)
    except Exception as exc:
        logger.debug("perf_start skipped %s: %s", chat_id, exc)

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

@app.get("/api/workflows")
async def get_workflows(
    principal: UserPrincipal = Depends(require_any_auth),
):
    _ = principal
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    ordered_names = get_platform_hooks().call_workflow_ordering(sorted(workflow_manager.get_all_workflow_names()))
    workflows = []
    for workflow_name in ordered_names:
        config = workflow_manager.get_config(workflow_name)
        workflows.append({
            "name": workflow_name,
            "display_name": config.get("display_name") or config.get("name") or workflow_name,
            "initial_agent": config.get("initial_agent"),
            "visual_agents": config.get("visual_agents") or [],
            "status": "ready",
        })
    return {"workflows": workflows}


@app.get("/api/workflows/config")
async def get_workflow_configs(
    principal: UserPrincipal = Depends(require_any_auth),
):
    _ = principal
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    ordered_names = get_platform_hooks().call_workflow_ordering(sorted(workflow_manager.get_all_workflow_names()))
    return {workflow_name: workflow_manager.get_config(workflow_name) for workflow_name in ordered_names}

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
        logger.info(
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
            except Exception:
                pass
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
        except Exception:
            pass
        await websocket.close(code=1011, reason="Prerequisite validation failed")
        return

    active_chat_id = chat_id
    session_state_payload: Optional[Dict[str, Any]] = None
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
                {"status": 1, "last_sequence": 1, "messages": {"$slice": 1}},
            )
            if not chat_doc:
                return
            if int(chat_doc.get("status", -1)) != 0:
                return
            last_sequence = int(chat_doc.get("last_sequence", 0) or 0)
            if last_sequence > 0 or bool(chat_doc.get("messages")):
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

    asyncio.create_task(_auto_start_if_needed())

    try:
        has_children = False
        try:
            from mozaiksai.core.workflow.pack.graph import workflow_has_mid_flight_journeys

            has_children = workflow_has_mid_flight_journeys(workflow_name)
        except Exception:
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
            try:
                if coll is not None:
                    doc = await coll.find_one(
                        {"_id": active_chat_id, "user_id": user_id, **build_app_scope_filter(app_id)},
                        {"last_artifact": 1, "created_at": 1, "status": 1, "last_sequence": 1},
                    )
                    if doc:
                        last_artifact = doc.get("last_artifact")
                        created_at = doc.get("created_at")
                        if created_at:
                            try:
                                created_at_iso = created_at.isoformat()
                            except Exception:
                                created_at_iso = str(created_at)
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
                    "last_sequence": doc.get("last_sequence") if doc else None,
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
        logger.info("Cleaned up session registry for ws_id=%s", ws_id)


@app.post("/api/chat/upload")
async def upload_chat_file(
    request: Request,
    file: UploadFile = File(...),
    appId: Optional[str] = Form(None),
    userId: str = Form(...),
    chatId: str = Form(...),
    intent: str = Form("context"),
    bundle_path: Optional[str] = Form(None),
    principal: UserPrincipal = Depends(require_user_scope),
):
    _ = request
    resolved_app_id = (appId or "").strip()
    if not resolved_app_id:
        raise HTTPException(status_code=400, detail="appId is required")
    user_id = _validate_user_id_against_principal(principal, body_user_id=userId)
    return await _handle_chat_upload(
        file=file,
        app_id=resolved_app_id,
        user_id=user_id,
        chat_id=chatId,
        intent=intent,
        bundle_path=bundle_path,
    )


@app.post("/api/chat/upload/{app_id}/{user_id}")
async def upload_chat_file_scoped(
    app_id: str,
    user_id: str,
    file: UploadFile = File(...),
    chatId: str = Form(...),
    intent: str = Form("context"),
    bundle_path: Optional[str] = Form(None),
    principal: UserPrincipal = Depends(require_user_scope),
):
    user_id = _validate_user_id_against_principal(principal, path_user_id=user_id)
    return await _handle_chat_upload(
        file=file,
        app_id=app_id,
        user_id=user_id,
        chat_id=chatId,
        intent=intent,
        bundle_path=bundle_path,
    )


async def _handle_chat_upload(
    *,
    file: UploadFile,
    app_id: str,
    user_id: str,
    chat_id: str,
    intent: str,
    bundle_path: Optional[str],
) -> Dict[str, Any]:
    if not app_id or not user_id or not chat_id:
        raise HTTPException(status_code=400, detail="app_id, user_id, and chat_id are required")

    allowed_raw = os.getenv("CHAT_ATTACHMENTS_ALLOWED_WORKFLOWS", "").strip()
    try:
        coll = await runtime_app._chat_coll()
        res = await handle_chat_upload(
            chat_coll=coll,
            file_obj=file,
            app_id=app_id,
            user_id=user_id,
            chat_id=chat_id,
            intent=intent,
            bundle_path=bundle_path,
            allowed_workflows_env=allowed_raw,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Chat session not found")
    except ValueError as exc:
        message = str(exc)
        if message.startswith("File too large"):
            raise HTTPException(status_code=413, detail=message) from exc
        raise HTTPException(status_code=400, detail=message) from exc
    except Exception as exc:
        logger.exception("UPLOAD_FAILED")
        raise HTTPException(status_code=500, detail="Upload failed") from exc

    try:
        if runtime_app.simple_transport:
            workflow_name = None
            try:
                doc = await coll.find_one(
                    {"_id": chat_id, "user_id": user_id, **build_app_scope_filter(app_id)},
                    {"workflow_name": 1},
                )
                if doc:
                    workflow_name = doc.get("workflow_name")
            except Exception:
                workflow_name = None

            await runtime_app.simple_transport.send_event_to_ui(
                {
                    "kind": "attachment_uploaded",
                    "chat_id": chat_id,
                    "app_id": app_id,
                    "user_id": user_id,
                    "workflow_name": workflow_name,
                    "attachment": res.attachment,
                },
                chat_id,
            )
    except Exception as exc:
        logger.debug("attachment_uploaded WS emit failed for chat %s: %s", chat_id, exc)

    return {
        "success": True,
        "chat_id": chat_id,
        "app_id": app_id,
        "user_id": user_id,
        "attachment": res.attachment,
    }


@app.get("/api/chats/{app_id}/{workflow_name}")
async def list_chats(
    app_id: str,
    workflow_name: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    try:
        coll = await runtime_app._chat_coll()
        query: Dict[str, Any] = {"workflow_name": workflow_name, **build_app_scope_filter(app_id)}
        if principal.user_id != "anonymous":
            query["user_id"] = principal.user_id
        docs = await coll.find(query).sort("created_at", -1).to_list(length=20)
        return {"chat_ids": [doc.get("_id") for doc in docs]}
    except Exception as exc:
        logger.error("Failed to list chats for app %s workflow %s: %s", app_id, workflow_name, exc)
        raise HTTPException(status_code=500, detail="Failed to list chats") from exc


@app.get("/api/chats/exists/{app_id}/{workflow_name}/{chat_id}")
async def chat_exists(
    app_id: str,
    workflow_name: str,
    chat_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    try:
        coll = await runtime_app._chat_coll()
        query: Dict[str, Any] = {"_id": chat_id, "workflow_name": workflow_name, **build_app_scope_filter(app_id)}
        if principal.user_id != "anonymous":
            query["user_id"] = principal.user_id
        doc = await coll.find_one(query, {"_id": 1})
        return {"exists": doc is not None}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to check chat existence: {exc}") from exc


@app.get("/api/sessions/list/{app_id}/{user_id}")
async def list_user_sessions(
    app_id: str,
    user_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    user_id = _validate_user_id_against_principal(principal, path_user_id=user_id)
    try:
        from mozaiksai.core.data.models import WorkflowStatus

        coll = await runtime_app._chat_coll()
        sessions = await coll.find({
            "user_id": user_id,
            "status": int(WorkflowStatus.IN_PROGRESS),
            **build_app_scope_filter(app_id),
        }).sort("last_updated_at", -1).to_list(length=100)
        runnable_names = _get_ordered_workflow_names()
        sessions = [
            session
            for session in sessions
            if _is_runnable_workflow_name(session.get("workflow_name"), runnable_names)
        ]

        result = []
        for session in sessions:
            result.append({
                "chat_id": session["_id"],
                "workflow_name": session.get("workflow_name"),
                "created_at": session.get("created_at").isoformat() if session.get("created_at") else None,
                "last_updated_at": session.get("last_updated_at").isoformat() if session.get("last_updated_at") else None,
                "last_artifact": session.get("last_artifact"),
            })
        return {"sessions": result, "count": len(result)}
    except Exception as exc:
        logger.error("[LIST_SESSIONS] Failed to list sessions: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to list sessions: {exc}") from exc


@app.get("/api/sessions/recent/{app_id}/{user_id}")
async def get_most_recent_workflow_session(
    app_id: str,
    user_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    user_id = _validate_user_id_against_principal(principal, path_user_id=user_id)
    try:
        from mozaiksai.core.data.models import WorkflowStatus

        coll = await runtime_app._chat_coll()
        sessions = await coll.find({
            "user_id": user_id,
            "status": int(WorkflowStatus.IN_PROGRESS),
            **build_app_scope_filter(app_id),
        }).sort("last_updated_at", -1).to_list(length=100)
        runnable_names = _get_ordered_workflow_names()
        sessions = [
            session
            for session in sessions
            if _is_runnable_workflow_name(session.get("workflow_name"), runnable_names)
        ]

        if not sessions:
            return {"found": False, "chat_id": None, "workflow_name": None}
        recent = sessions[0]
        return {
            "found": True,
            "chat_id": recent["_id"],
            "workflow_name": recent.get("workflow_name"),
            "created_at": recent.get("created_at").isoformat() if recent.get("created_at") else None,
            "last_updated_at": recent.get("last_updated_at").isoformat() if recent.get("last_updated_at") else None,
            "last_artifact": recent.get("last_artifact"),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch most recent session: {exc}") from exc


@app.get("/api/sessions/oldest/{app_id}/{user_id}")
async def get_oldest_workflow_session(
    app_id: str,
    user_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    user_id = _validate_user_id_against_principal(principal, path_user_id=user_id)
    try:
        from mozaiksai.core.data.models import WorkflowStatus

        coll = await runtime_app._chat_coll()
        sessions = await coll.find({
            "user_id": user_id,
            "status": int(WorkflowStatus.IN_PROGRESS),
            **build_app_scope_filter(app_id),
        }).sort("created_at", 1).to_list(length=100)
        runnable_names = _get_ordered_workflow_names()
        sessions = [
            session
            for session in sessions
            if _is_runnable_workflow_name(session.get("workflow_name"), runnable_names)
        ]

        if not sessions:
            return {"found": False, "chat_id": None, "workflow_name": None}
        oldest = sessions[0]
        return {
            "found": True,
            "chat_id": oldest["_id"],
            "workflow_name": oldest.get("workflow_name"),
            "created_at": oldest.get("created_at").isoformat() if oldest.get("created_at") else None,
            "last_updated_at": oldest.get("last_updated_at").isoformat() if oldest.get("last_updated_at") else None,
            "last_artifact": oldest.get("last_artifact"),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch oldest session: {exc}") from exc


@app.delete("/api/sessions/{app_id}/{user_id}")
async def delete_user_sessions(
    app_id: str,
    user_id: str,
    status: str = "in_progress",
    workflow_name: Optional[str] = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    user_id = _validate_user_id_against_principal(principal, path_user_id=user_id)
    try:
        from mozaiksai.core.data.models import WorkflowStatus

        normalized_status = str(status or "in_progress").strip().lower()
        query: Dict[str, Any] = {"user_id": user_id, **build_app_scope_filter(app_id)}
        if workflow_name:
            query["workflow_name"] = str(workflow_name).strip()
        if normalized_status in {"in_progress", "active", "open"}:
            query["status"] = int(WorkflowStatus.IN_PROGRESS)
        elif normalized_status in {"completed", "done", "closed"}:
            query["status"] = int(WorkflowStatus.COMPLETED)
        elif normalized_status in {"all", "any", "*"}:
            pass
        else:
            raise HTTPException(status_code=400, detail="status must be one of: in_progress, completed, all")

        result = await (await runtime_app._chat_coll()).delete_many(query)
        return {
            "success": True,
            "app_id": app_id,
            "user_id": user_id,
            "status": normalized_status,
            "workflow_name": workflow_name,
            "deleted_count": int(result.deleted_count or 0),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete sessions: {exc}") from exc


@app.delete("/api/general_chats/{app_id}/{user_id}")
async def delete_general_chats(
    app_id: str,
    user_id: str,
    status: str = "all",
    principal: UserPrincipal = Depends(require_user_scope),
):
    user_id = _validate_user_id_against_principal(principal, path_user_id=user_id)
    try:
        from mozaiksai.core.data.models import WorkflowStatus

        normalized_status = str(status or "all").strip().lower()
        query: Dict[str, Any] = {"user_id": user_id, **build_app_scope_filter(app_id)}
        if normalized_status in {"in_progress", "active", "open"}:
            query["status"] = int(WorkflowStatus.IN_PROGRESS)
        elif normalized_status in {"completed", "done", "closed"}:
            query["status"] = int(WorkflowStatus.COMPLETED)
        elif normalized_status in {"all", "any", "*"}:
            pass
        else:
            raise HTTPException(status_code=400, detail="status must be one of: in_progress, completed, all")

        general_coll = await persistence_manager._general_coll()
        result = await general_coll.delete_many(query)
        deleted_count = int(result.deleted_count or 0)

        if normalized_status in {"all", "any", "*"}:
            try:
                counter_coll = await persistence_manager._general_counter_coll()
                await counter_coll.delete_many({"user_id": user_id, **build_app_scope_filter(app_id)})
            except Exception as counter_err:
                logger.debug("[DELETE_GENERAL_CHATS] Counter reset skipped: %s", counter_err)

        return {
            "success": True,
            "app_id": app_id,
            "user_id": user_id,
            "status": normalized_status,
            "deleted_count": deleted_count,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete general chats: {exc}") from exc


@app.get("/api/notifications/count")
async def notifications_count_fallback(
    principal: UserPrincipal = Depends(require_user_scope),
):
    try:
        from mozaiksai.core.core_config import get_mongo_client

        collection = get_mongo_client()["mozaiks"]["platform_notifications"]
        unread_count = await collection.count_documents(_notification_query_for_principal(principal))
        return {"count": int(unread_count), "unread_count": int(unread_count)}
    except Exception as exc:
        logger.debug("NOTIFICATION_COUNT_SKIPPED: %s", exc)
        return {"count": 0, "unread_count": 0}


def _notification_query_for_principal(principal: UserPrincipal) -> Dict[str, Any]:
    query: Dict[str, Any] = {"status": "unread"}
    if principal.app_id:
        query["app_id"] = principal.app_id

    visibility: List[Dict[str, Any]] = [{"actor.id": principal.user_id}]
    roles = [role for role in principal.roles if role]
    if roles:
        visibility.append({"audience.roles": {"$in": roles}})
    visibility.append({"audience.roles": {"$exists": False}})
    query["$or"] = visibility
    return query


@app.get("/api/general_chats/list/{app_id}/{user_id}")
async def list_general_chats_fallback(
    app_id: str,
    user_id: str,
    limit: int = 50,
    principal: UserPrincipal = Depends(require_user_scope),
):
    user_id = _validate_user_id_against_principal(principal, path_user_id=user_id)
    return {
        "app_id": app_id,
        "user_id": user_id,
        "limit": max(1, int(limit or 50)),
        "sessions": [],
        "count": 0,
        "source": "fallback",
    }


@app.get("/api/general_chats/transcript/{app_id}/{general_chat_id}")
async def general_chat_transcript_fallback(
    app_id: str,
    general_chat_id: str,
    after_sequence: int = -1,
    limit: int = 200,
    principal: UserPrincipal = Depends(require_user_scope),
):
    _ = principal
    return {
        "app_id": app_id,
        "chat_id": general_chat_id,
        "label": general_chat_id,
        "messages": [],
        "last_sequence": max(-1, int(after_sequence or -1)),
        "limit": max(1, int(limit or 200)),
        "found": False,
        "source": "fallback",
    }


@app.get("/api/chats/meta/{app_id}/{workflow_name}/{chat_id}")
async def chat_meta(
    app_id: str,
    workflow_name: str,
    chat_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    try:
        has_children = False
        try:
            from mozaiksai.core.workflow.pack.graph import workflow_has_mid_flight_journeys

            has_children = workflow_has_mid_flight_journeys(workflow_name)
        except Exception:
            has_children = False

        coll = await runtime_app._chat_coll()
        projection = {"cache_seed": 1, "last_artifact": 1, "status": 1, "last_sequence": 1, "_id": 1, "workflow_name": 1}
        query: Dict[str, Any] = {"_id": chat_id, "workflow_name": workflow_name, **build_app_scope_filter(app_id)}
        if principal.user_id != "anonymous":
            query["user_id"] = principal.user_id
        doc = await coll.find_one(query, projection)
        if not doc:
            return {"exists": False}

        artifact_instance_id = None
        artifact_state = None
        try:
            from mozaiksai.core.workflow import session_manager

            workflow_session = await session_manager.get_workflow_session(chat_id, app_id)
            if workflow_session and workflow_session.get("artifact_instance_id"):
                artifact_instance_id = workflow_session["artifact_instance_id"]
                artifact_doc = await session_manager.get_artifact_instance(artifact_instance_id, app_id)
                if artifact_doc:
                    artifact_state = artifact_doc.get("state")
        except Exception as artifact_err:
            logger.warning("[CHAT_META] Failed to retrieve artifact instance for chat %s: %s", chat_id, artifact_err)

        return {
            "exists": True,
            "chat_id": chat_id,
            "workflow_name": workflow_name,
            "has_children": has_children,
            "cache_seed": doc.get("cache_seed"),
            "status": doc.get("status"),
            "last_sequence": doc.get("last_sequence"),
            "last_artifact": doc.get("last_artifact"),
            "artifact_instance_id": artifact_instance_id,
            "artifact_state": artifact_state,
            "app_id": app_id,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load chat meta: {exc}") from exc


_PLATFORM_OVERRIDE_PATHS = frozenset({
    "/api/chats/{app_id}/{workflow_name}/start",
    "/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}",
    "/api/workflows",
    "/api/workflows/config",
    "/api/chats/meta/{app_id}/{workflow_name}/{chat_id}",
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

from __future__ import annotations

"""Platform composition host layered on top of runtime_app.py."""

import json
import os
import re
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import yaml
from fastapi import Depends, File, Form, HTTPException, Request, UploadFile, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import runtime_app
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
from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks


app = runtime_app.app
persistence_manager = runtime_app.persistence_manager
logger = get_workflow_logger("platform_app")

executor_registry = ExecutorRegistry()
app.state.executor_registry = executor_registry
_runtime_services: list[Any] = []


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
    platform_path = os.environ.get("PLATFORM_PATH", "")
    if platform_path:
        candidate = Path(platform_path)
        if candidate.is_absolute():
            return candidate
        return (Path(__file__).parent / candidate).resolve()

    monorepo = Path(__file__).parent / "mozaiks-platform" / "app"
    if monorepo.is_dir():
        return monorepo
    return Path(__file__).parent / "platform"


@app.on_event("startup")
async def platform_startup() -> None:
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
            module_executor = ModuleExecutor()
            for loaded_module in load_result.modules:
                module_executor.register(loaded_module.name, loaded_module.handler)
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


@app.on_event("shutdown")
async def platform_shutdown() -> None:
    global _runtime_services
    if not _runtime_services:
        return
    try:
        await stop_services(_runtime_services)
    except Exception:
        pass
    _runtime_services = []


# The admin portal is one framework-owned shell, but the app shell still needs
# explicit section routes so navigation, route matching, and shell manifests all
# expose the same stable paths.
ADMIN_SHELL_ROUTES: tuple[dict[str, Any], ...] = (
    {
        "path": "/admin",
        "label": "Admin",
        "order": 999,
        "title": "Admin",
        "admin_section": "overview",
    },
    {
        "path": "/admin/users",
        "label": "Users",
        "order": 1000,
        "title": "Users",
        "admin_section": "users",
    },
    {
        "path": "/admin/billing",
        "label": "Billing",
        "order": 1001,
        "title": "Billing",
        "admin_section": "billing",
    },
    {
        "path": "/admin/usage",
        "label": "Usage",
        "order": 1002,
        "title": "Usage",
        "admin_section": "usage",
    },
    {
        "path": "/admin/activity",
        "label": "Activity",
        "order": 1003,
        "title": "Activity",
        "admin_section": "activity",
    },
    {
        "path": "/admin/settings",
        "label": "Settings",
        "order": 1004,
        "title": "Settings",
        "admin_section": "settings",
    },
    {
        "path": "/admin/integrations",
        "label": "Integrations",
        "order": 1005,
        "title": "Integrations",
        "admin_section": "integrations",
    },
    {
        "path": "/admin/support",
        "label": "Support",
        "order": 1006,
        "title": "Support",
        "admin_section": "support",
    },
)


STUDIO_SHELL_ROUTES: tuple[dict[str, Any], ...] = (
    {
        "path": "/studio",
        "component": "StudioHomePage",
        "label": "Studio",
        "order": 5,
        "title": "Studio Home",
    },
    {
        "path": "/studio/build",
        "component": "StudioBuildPage",
        "label": "Build",
        "order": 6,
        "title": "Studio Build",
    },
    {
        "path": "/studio/adapters",
        "component": "StudioAdaptersPage",
        "label": "Adapters",
        "order": 7,
        "title": "Studio Adapters",
    },
)


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
        (_load_ui_extension_pages, "UI extension routes"),
        (_load_page_schema_routes, "page schema routes"),
        (_load_workflow_entrypoint_pages, "workflow entrypoint routes"),
    ):
        try:
            pages.extend(loader(platform_root))
        except Exception as exc:
            logger.warning("[shell-config] Could not read %s: %s", label, exc)

    if pages:
        result["pages"] = _dedupe_and_sort_pages(pages)

    admin_config_path = platform_root / "config" / "admin.json"
    if admin_config_path.exists():
        try:
            admin_cfg = json.loads(admin_config_path.read_text(encoding="utf-8"))
            if admin_cfg.get("enabled", True):
                pages = result.get("pages", [])
                for route in ADMIN_SHELL_ROUTES:
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


def _load_ui_extension_pages(platform_root: Path) -> List[dict]:
    candidates = [
        (platform_root / ".." / "ui" / "extension.json").resolve(),
        (platform_root / "ui" / "extension.json").resolve(),
        (Path(__file__).parent / "platform" / "ui" / "extension.json").resolve(),
    ]
    manifest_path = next((candidate for candidate in candidates if candidate.exists()), None)
    if manifest_path is None:
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
    pages_dir = platform_root / "pages"
    if not pages_dir.exists():
        return []

    candidates: List[Path] = []
    for child in sorted(pages_dir.iterdir(), key=lambda item: item.name.lower()):
        if child.is_file() and child.suffix.lower() in {".yaml", ".yml"}:
            candidates.append(child)
        elif child.is_dir() and (child / "page.yaml").exists():
            candidates.append(child / "page.yaml")

    pages: List[dict] = []
    for index, page_path in enumerate(candidates):
        raw = yaml.safe_load(page_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            continue
        route = raw.get("route")
        if not isinstance(route, str) or not route.startswith("/"):
            continue
        name = str(raw.get("name") or page_path.stem).strip() or page_path.stem
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
    from mozaiksai.core.workflow.pack.config import list_entrypoints
    from mozaiksai.core.workflow.pack.schema import parse_global_pack_graph

    registry_path = platform_root / "workflows" / "extended_orchestration" / "extension_registry.json"
    if not registry_path.exists():
        return []
    raw = json.loads(registry_path.read_text(encoding="utf-8"))
    pack = parse_global_pack_graph(raw)

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
    candidates = [
        platform_root / ".." / "brand" / "theme_config.json",
        platform_root / "brand" / "theme_config.json",
        Path(__file__).parent / "platform" / "config" / "theme_config.json",
    ]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved
    return candidates[-1].resolve()


def _resolve_shell_config_path() -> Path:
    platform_root = resolve_platform_path()
    candidates = [
        platform_root / "config" / "shell.json",
        Path(__file__).parent / "platform" / "config" / "shell.json",
    ]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved
    return candidates[-1].resolve()


def _resolve_app_manifest_path() -> Path:
    platform_root = resolve_platform_path()
    candidates = [
        platform_root / "app.json",
        Path(__file__).parent / "platform" / "app.json",
    ]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved
    return candidates[-1].resolve()


def _resolve_pages_dir() -> Path:
    platform_path = os.getenv("PLATFORM_PATH")
    if platform_path:
        candidate = Path(platform_path) / "pages"
        if candidate.is_dir():
            return candidate
    monorepo = Path(__file__).parent / "mozaiks-platform" / "app" / "pages"
    if monorepo.is_dir():
        return monorepo
    return Path(__file__).parent / "platform" / "pages"


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

    page_path = _resolve_pages_dir() / f"{name}.yaml"
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


def validate_context_for_workflow(workflow_id: str, merged_context: Dict[str, Any]) -> Dict[str, Any]:
    validated_context: Dict[str, Any] = {}
    if not merged_context:
        return validated_context

    try:
        from mozaiksai.core.workflow.workflow_manager import workflow_manager

        wf_cfg = workflow_manager.get_config(workflow_id) or {}
        declared_keys = set((wf_cfg.get("context_variables") or {}).get("definitions", {}).keys())
    except Exception:
        declared_keys = set()

    for key, value in merged_context.items():
        if declared_keys and key not in declared_keys:
            logger.warning("TRIGGER_CONTEXT_KEY_REJECTED: key=%s workflow=%s", key, workflow_id)
            continue
        validated_context[key] = value
    return validated_context


async def create_routed_chat_session(
    *,
    workflow_id: str,
    app_id: str,
    user_id: str,
    context_variables: Dict[str, Any],
    trigger_meta: Dict[str, Any],
    session_router: Optional[Any] = None,
    journey_id: Optional[str] = None,
) -> str:
    chat_id = str(uuid4())
    extra_fields: Dict[str, Any] = {"trigger_meta": trigger_meta}
    extra_fields.update(context_variables)

    await persistence_manager.create_chat_session(
        chat_id=chat_id,
        app_id=app_id,
        workflow_name=workflow_id,
        user_id=user_id,
        extra_fields=extra_fields or None,
    )

    if session_router is not None:
        try:
            await session_router.bind_workflow_session(
                app_id=app_id,
                user_id=user_id,
                workflow_id=workflow_id,
                chat_id=chat_id,
                journey_id=journey_id,
            )
        except Exception as exc:
            logger.warning("Failed to bind SessionRouter chat session: %s", exc)
    return chat_id


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
    default_app_id: str = "default",
) -> Tuple[str, str]:
    resolved_user_id = _validate_user_id_against_principal(principal, body_user_id=user_id)

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
        from mozaiksai.core.session import get_session_router

        session_router = get_session_router()
        app_id, user_id = resolve_scope_from_principal(principal, app_id=body.app_id, user_id=body.user_id)
        resolution = await session_router.resolve_transition(
            app_id=app_id,
            user_id=user_id,
            transition_id=body.transition_id,
            option_id=body.option_id,
            context_seed=body.context_variables or {},
        )
    except ValueError as route_err:
        raise HTTPException(status_code=400, detail=str(route_err)) from route_err
    except Exception as route_err:
        logger.error("Transition resolution failed: %s", route_err, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to resolve transition: {route_err}") from route_err

    pack = _load_pack_graph_or_404()
    if resolution.resolution_type == "transition":
        from mozaiksai.core.workflow.pack.config import get_transition

        next_transition = get_transition(pack, resolution.target_id)
        if next_transition is None:
            raise HTTPException(
                status_code=500,
                detail=f"Transition '{resolution.target_id}' could not be loaded after resolution",
            )
        return {
            "resolution_type": "transition",
            "transition_id": body.transition_id,
            "option_id": resolution.option_id,
            "next_transition_id": resolution.target_id,
            "transition": next_transition.model_dump(exclude_none=True),
            "context_variables": resolution.context_seed,
        }

    route_decision = resolution.routing_decision
    if route_decision is None:
        raise HTTPException(status_code=500, detail="Workflow transition resolution is missing routing decision")

    resolved_workflow_id = route_decision.workflow_id
    validated_context = validate_context_for_workflow(resolved_workflow_id, resolution.context_seed)
    trigger_meta = {
        "trigger_source": "transition",
        "transition_id": body.transition_id,
        "option_id": body.option_id,
        "requested_workflow_id": route_decision.requested_workflow_id,
        "resolved_workflow_id": resolved_workflow_id,
        "rerouted_by_dependency": bool(route_decision.rerouted_by_dependency),
    }
    chat_id = await create_routed_chat_session(
        workflow_id=resolved_workflow_id,
        app_id=app_id,
        user_id=user_id,
        context_variables=validated_context,
        trigger_meta=trigger_meta,
        session_router=session_router,
    )

    return {
        "resolution_type": "workflow",
        "chat_id": chat_id,
        "workflow_id": resolved_workflow_id,
        "option_id": resolution.option_id,
        "requested_workflow_id": route_decision.requested_workflow_id,
        "websocket_url": f"/ws/{resolved_workflow_id}/{app_id}/{chat_id}/{user_id}",
        "routing_explanation": route_decision.explanation,
        "rerouted_by_dependency": bool(route_decision.rerouted_by_dependency),
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
            if existing_workflow and str(existing_workflow).strip() != str(workflow_name).strip():
                await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason="Chat not found")
                return
    except Exception as ownership_err:
        logger.debug("WS_CHAT_OWNERSHIP_CHECK_SKIPPED: %s", ownership_err)

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
    _ = principal
    return {"count": 0, "unread_count": 0}


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

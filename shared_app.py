# ==============================================================================
# FILE: shared_app.py
# DESCRIPTION: FastAPI runtime entrypoint for chat, workflow, transport, and auth-facing APIs.
# ==============================================================================
import logging
import os
import sys
import hmac
from typing import Optional, Any, List, Dict, Tuple
from datetime import datetime, timedelta, UTC
from pathlib import Path
# Ensure project root is on Python path for workflow imports
sys.path.insert(0, str(Path(__file__).parent))
import json
import yaml
import asyncio
import importlib
from fastapi import FastAPI, HTTPException, Request, WebSocket, UploadFile, File, Form, Depends
from fastapi.responses import JSONResponse, FileResponse, Response
from starlette.middleware.cors import CORSMiddleware
from bson.objectid import ObjectId
from uuid import uuid4
import autogen
from pydantic import BaseModel, Field, ValidationError
from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.transport.simple_transport import SimpleTransport
from mozaiksai.core.workflow.workflow_manager import workflow_status_summary, get_workflow_transport, get_workflow_tools
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.multitenant import build_app_scope_filter
from mozaiksai.core.chat_attachments.attachments import handle_chat_upload
from mozaiksai.core.runtime.composition.extensions import mount_declared_routers, start_declared_services, stop_services
from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks
from mozaiksai.core.runtime.composition.executor_registry import ExecutorRegistry, ExecutorType
from mozaiksai.core.runtime.composition.module_executor import OperationExecutor, OperationRequest
from mozaiksai.core.runtime.app.loader import AppLoader, AppLoadError

# JWT Authentication dependencies
from mozaiksai.core.auth import (
    UserPrincipal,
    require_user_scope,
    require_any_auth,
    optional_user,
    authenticate_websocket_with_path_user,
    authenticate_websocket_with_path_binding,
    verify_user_owns_resource,
    get_auth_config,
    WS_CLOSE_POLICY_VIOLATION,
)
from mozaiksai.core.auth.dependencies import (
    validate_path_app_id,
    validate_path_chat_id,
    validate_user_id_against_principal as _validate_user_id_against_principal,
)

# Initialize persistence manager (handles lean chat session storage internally)
persistence_manager = AG2PersistenceManager()
_runtime_services = []

# Executor registry — populated during startup from discovered operations/*/operation.yaml
executor_registry = ExecutorRegistry()



async def _chat_coll():
    """Return the new lean chat_sessions collection (lowercase)."""
    # Delegate to the persistence manager's internal helper (ensures client)
    return await persistence_manager._coll()


# Import our custom logging setup
from logs.logging_config import (
    setup_development_logging, 
    setup_production_logging, 
    get_workflow_logger,
)

# Setup logging based on environment ASAP (before any KV/DB work)
env = os.getenv("ENVIRONMENT", "development").lower()

if env == "production":
    setup_production_logging()
    get_workflow_logger("shared_app_setup").info(
        "LOGGING_CONFIGURED: Production logging configuration applied"
    )
else:
    setup_development_logging()
    get_workflow_logger("shared_app_setup").info(
        "LOGGING_CONFIGURED: Development logging configuration applied"
    )

# (Startup log moved below after business_logger is defined)

# Set autogen library logging to DEBUG for detailed output
logging.getLogger('autogen').setLevel(logging.DEBUG)

# Get specialized loggers
wf_logger = get_workflow_logger("shared_app")
performance_logger = get_workflow_logger("performance.shared_app")
logger = logging.getLogger(__name__)

# Log AG2 version for debugging
wf_logger.info(f"🔍 autogen version: {getattr(autogen, '__version__', 'unknown')}")

# Emit an explicit startup log line so file logging can be verified quickly
wf_logger.info(f"SERVER_STARTUP_INIT: Starting MozaiksAI in {env} mode")

# ---------------------------------------------------------------------------
# Patch Autogen file logger to tolerate non-serializable objects
# ---------------------------------------------------------------------------
def _patch_autogen_file_logger() -> None:
    try:
        from autogen.logger import file_logger as _file_logger
        from autogen.logger.logger_utils import get_current_ts as _logger_ts
    except Exception as patch_err:  # pragma: no cover - defensive safeguard
        wf_logger.debug(f"Skipped Autogen file_logger patch: {patch_err}")
        return

    # Use an Any-typed alias to avoid static type errors on dynamic attributes
    FL: Any = _file_logger.FileLogger

    if getattr(FL, "_mozaiks_safe_json", False):
        return

    import json as _json
    import threading as _threading

    safe_serialize = _file_logger.safe_serialize

    def _serialize_wrapper_payload(wrapper, session_id, thread_id, init_args):
        return _json.dumps({
            "wrapper_id": id(wrapper),
            "session_id": session_id,
            "json_state": safe_serialize(init_args or {}),
            "timestamp": _logger_ts(),
            "thread_id": thread_id,
        })

    def _serialize_client_payload(client, wrapper, session_id, thread_id, init_args):
        return _json.dumps({
            "client_id": id(client),
            "wrapper_id": id(wrapper),
            "session_id": session_id,
            "class": type(client).__name__,
            "json_state": safe_serialize(init_args or {}),
            "timestamp": _logger_ts(),
            "thread_id": thread_id,
        })

    def _patched_log_new_wrapper(self, wrapper, init_args=None):
        thread_id = _threading.get_ident()
        try:
            payload = _serialize_wrapper_payload(wrapper, self.session_id, thread_id, init_args)
            self.logger.info(payload)
        except Exception as exc:  # pragma: no cover - logging fallback
            self.logger.error(f"[file_logger] Failed to log event {exc}")

    def _patched_log_new_client(self, client, wrapper, init_args):
        thread_id = _threading.get_ident()
        try:
            payload = _serialize_client_payload(client, wrapper, self.session_id, thread_id, init_args)
            self.logger.info(payload)
        except Exception as exc:  # pragma: no cover - logging fallback
            self.logger.error(f"[file_logger] Failed to log event {exc}")

    # Monkey patch methods and set a marker attribute
    FL.log_new_wrapper = _patched_log_new_wrapper  # type: ignore[attr-defined]
    FL.log_new_client = _patched_log_new_client  # type: ignore[attr-defined]
    setattr(FL, "_mozaiks_safe_json", True)
    wf_logger.info("Patched Autogen FileLogger for safe JSON serialization")


_patch_autogen_file_logger()

# Initialize unified event dispatcher
from mozaiksai.core.events import get_event_dispatcher
event_dispatcher = get_event_dispatcher()
wf_logger.info("🎯 Unified Event Dispatcher initialized")

from mozaiksai.core.observability.performance_manager import get_performance_manager

# FastAPI app
app = FastAPI(
    title="MozaiksAI Runtime",
    description="Production-ready AG2 runtime with workflow-specific tools",
    version="5.0.0",
)


@app.get("/", include_in_schema=False)
async def read_root():
    """Simple root endpoint so localhost:8000/ is never a 404."""
    return {
        "service": "mozaiks-runtime",
        "status": "ok",
        "docs": "https://docs.mozaiks.ai",
        "health": "/api/health",
    }


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve a favicon for backend-origin requests (e.g., Swagger/docs probes)."""
    brand_dir = Path(__file__).parent / "platform" / "brand"
    ico_path = brand_dir / "favicon.ico"
    svg_path = brand_dir / "assets" / "mozaik_logo.svg"
    png_path = brand_dir / "assets" / "mozaik.png"

    if ico_path.exists():
        return FileResponse(str(ico_path), media_type="image/x-icon")
    if svg_path.exists():
        return FileResponse(str(svg_path), media_type="image/svg+xml")
    if png_path.exists():
        return FileResponse(str(png_path), media_type="image/png")
    return Response(status_code=204)


@app.get("/.well-known/appspecific/com.chrome.devtools.json", include_in_schema=False)
async def chrome_devtools_probe():
    """Silence Chrome devtools probe noise on localhost."""
    return Response(status_code=204)

# Expose shared state on app for platform extension routers.
app.state.persistence_manager = persistence_manager

def _parse_csv_origins(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [origin.strip() for origin in raw.split(",") if origin and origin.strip()]


def _build_cors_origins() -> List[str]:
    origins: List[str] = []

    origins.extend(_parse_csv_origins(os.getenv("FRONTEND_URL")))
    origins.extend(_parse_csv_origins(os.getenv("REACT_DEV_ORIGIN")))
    origins.extend(_parse_csv_origins(os.getenv("ADDITIONAL_CORS_ORIGINS")))

    # In local/dev flows we commonly run Vite on 3000 or 5173 and occasionally 3001.
    if env != "production":
        origins.extend(
            [
                "http://localhost:3000",
                "http://127.0.0.1:3000",
                "http://localhost:3001",
                "http://127.0.0.1:3001",
                "http://localhost:5173",
                "http://127.0.0.1:5173",
            ]
        )

    # De-dupe while preserving order.
    deduped: List[str] = []
    seen = set()
    for origin in origins:
        if origin not in seen:
            seen.add(origin)
            deduped.append(origin)
    return deduped


_cors_origins = _build_cors_origins()
if _cors_origins:
    wf_logger.info("CORS allow_origins configured: %s", _cors_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    wf_logger.warning("No explicit CORS origins configured; falling back to allow_origin_regex=.*")
    app.add_middleware(
        CORSMiddleware,
        # Allow all origins, including file:// (null); using regex for full coverage
        allow_origin_regex=r".*",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# ---------------------------------------------------------------------------
# Principal Header Enforcement Middleware
# ---------------------------------------------------------------------------
# When an upstream gateway is present, it attaches x-app-id and x-user-id headers
# derived from the authenticated context. This middleware validates that those
# headers (if present) match path parameters for defense-in-depth.

_ENFORCE_PRINCIPAL_HEADERS = os.getenv("ENFORCE_PRINCIPAL_HEADERS", "false").lower() in ("true", "1", "yes")

@app.middleware("http")
async def principal_header_middleware(request: Request, call_next):
    """Validate x-app-id / x-user-id headers match path params when present."""
    if not _ENFORCE_PRINCIPAL_HEADERS:
        return await call_next(request)
    
    # Extract headers (upstream gateway sets these)
    hdr_app_id = request.headers.get("x-app-id") or request.headers.get("x-mozaiks-app-id")
    hdr_user_id = request.headers.get("x-user-id") or request.headers.get("x-mozaiks-user-id")
    
    # Skip validation if headers not present (local dev / direct access)
    if not hdr_app_id and not hdr_user_id:
        return await call_next(request)
    
    # Extract path params (FastAPI resolves these after routing; we need to parse manually)
    path = request.url.path
    path_params = request.path_params  # Empty at middleware stage
    
    # Parse app_id from common path patterns: /api/chats/{app_id}/... or /ws/{workflow}/{app_id}/...
    import re
    app_id_match = re.search(r'/api/chats/([^/]+)/', path) or re.search(r'/ws/[^/]+/([^/]+)/', path)
    user_id_match = re.search(r'/ws/[^/]+/[^/]+/[^/]+/([^/]+)', path)
    
    path_app_id = app_id_match.group(1) if app_id_match else None
    path_user_id = user_id_match.group(1) if user_id_match else None
    
    # Enforce app_id match
    if hdr_app_id and path_app_id:
        if str(hdr_app_id).strip() != str(path_app_id).strip():
            from fastapi.responses import JSONResponse
            wf_logger.warning(
                "PRINCIPAL_HEADER_MISMATCH",
                extra={"header_app_id": hdr_app_id, "path_app_id": path_app_id}
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "x-app-id header does not match path app_id"}
            )
    
    # Enforce user_id match
    if hdr_user_id and path_user_id:
        if str(hdr_user_id).strip() != str(path_user_id).strip():
            from fastapi.responses import JSONResponse
            wf_logger.warning(
                "PRINCIPAL_HEADER_MISMATCH",
                extra={"header_user_id": hdr_user_id, "path_user_id": path_user_id}
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "x-user-id header does not match path user_id"}
            )
    
    return await call_next(request)

# Mount workflow-declared routers (plugins)
try:
    mount_declared_routers(app)
except Exception as _ext_err:  # pragma: no cover
    wf_logger.debug(f"RUNTIME_EXTENSIONS_MOUNT_FAILED: {_ext_err}")

# Mount first-class admin router
from mozaiksai.core.admin import router as admin_router
app.include_router(admin_router)


mongo_client = None  # delay until startup so logging is definitely initialized
simple_transport: Optional[SimpleTransport] = None


def _validate_internal_api_key(request: Request) -> None:
    expected = os.getenv("INTERNAL_API_KEY", "").strip()
    if not expected:
        return
    provided = request.headers.get("X-Internal-API-Key", "")
    if not hmac.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Invalid internal API key")


@app.get("/health/active-runs")
async def health_active_runs(
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return the current in-memory background workflow task snapshot."""
    try:
        transport = await SimpleTransport.get_instance()
        return transport.get_background_run_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/metrics/perf/aggregate")
async def metrics_perf_aggregate(
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return aggregate in-memory performance counters (no DB hits)."""
    try:
        perf_mgr = await get_performance_manager()
        return await perf_mgr.aggregate()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to collect aggregate metrics: {e}")

@app.get("/metrics/perf/chats")
async def metrics_perf_chats(
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return per-chat in-memory performance snapshots."""
    try:
        perf_mgr = await get_performance_manager()
        return await perf_mgr.snapshot_all()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to collect chat metrics: {e}")

@app.get("/metrics/perf/chats/{chat_id}")
async def metrics_perf_chat(
    chat_id: str,
    principal: UserPrincipal = Depends(require_any_auth),
):
    try:
        perf_mgr = await get_performance_manager()
        snap = await perf_mgr.snapshot_chat(chat_id)
        if not snap:
            raise HTTPException(status_code=404, detail="Chat not tracked")
        return snap
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to collect chat metric: {e}")


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
    """Upload a file associated with a specific chat session.

    The uploaded file is stored on disk and a metadata record is appended to the
    ChatSessions document under the `attachments` array.

    Clients may set `intent` to `context` (default) or `bundle`/`deliverable` to
    include the file in AgentGenerator's generated download bundle.
    """
    resolved_app_id = (appId or "").strip()
    if not resolved_app_id:
        raise HTTPException(status_code=400, detail="appId is required")
    
    # Validate body user_id matches JWT
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
    """Back-compat upload endpoint used by older ChatUI adapters."""
    # Validate path user_id matches JWT
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
        coll = await _chat_coll()
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
    except ValueError as ve:
        msg = str(ve)
        if msg.startswith("File too large"):
            raise HTTPException(status_code=413, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    except Exception as exc:
        logger.exception("UPLOAD_FAILED")
        raise HTTPException(status_code=500, detail="Upload failed") from exc

    # Emit a first-class websocket event so the UI can render an attachment indicator
    # without injecting synthetic chat text.
    try:
        if simple_transport:
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

            await simple_transport.send_event_to_ui(
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
    except Exception as e:
        logger.debug(f"attachment_uploaded WS emit failed for chat {chat_id}: {e}")

    # NOTE: We intentionally do NOT inject a synthetic chat message or an internal
    # input_request "nudge" into the workflow. Uploads are represented via persisted
    # ChatSessions.attachments and surfaced to agents via ContextVariables (workflow config).

    return {
        "success": True,
        "chat_id": chat_id,
        "app_id": app_id,
        "user_id": user_id,
        "attachment": res.attachment,
    }


@app.on_event("startup")
async def startup():
    """Initialize application on startup."""
    global simple_transport
    startup_start = datetime.now(UTC)
    
    wf_logger.info("🚀 APP_STARTUP: FastAPI startup event triggered")
    wf_logger.info(f"🔧 APP_STARTUP: Environment = {env}")
    
    # -----------------------------
    # Cache behavior controls (expert defaults)
    # - Tools: clear on start in development by default so tool edits take effect
    # - LLM: do NOT clear by default; allow opt-in via env
    #   Use LLM_CONFIG_CACHE_TTL env to tighten dev TTL (e.g., 0) if desired
    # -----------------------------
    def _env_bool(name: str, default: bool = False) -> bool:
        val = os.getenv(name)
        if val is None:
            return default
        return str(val).lower() in ("1", "true", "yes", "y", "on")

    # Clear workflow tool module cache on startup (default ON in dev)
    try:
        clear_tools = _env_bool("CLEAR_TOOL_CACHE_ON_START", default=(env != "production"))
        if clear_tools:
            from mozaiksai.core.workflow.agents.tools import clear_tool_cache
            cleared = clear_tool_cache()  # clear all workflow tool modules
            wf_logger.info(f"🧹 TOOL_CACHE: Cleared {cleared} cached tool modules on startup")
        else:
            wf_logger.info("🧹 TOOL_CACHE: Preserve cached tool modules (CLEAR_TOOL_CACHE_ON_START=0)")
    except Exception as e:
        wf_logger.error("TOOL_CACHE_CLEAR_FAILED: Failed to clear tool cache on startup", error=str(e))

    # Optional: clear LLM caches on startup (default OFF)
    try:
        if _env_bool("CLEAR_LLM_CACHES_ON_START", default=False):
            from mozaiksai.core.workflow.validation.llm_config import clear_llm_caches
            clear_llm_caches(raw=True, built=True)
            wf_logger.info("🧹 LLM_CACHE: Cleared raw and built llm_config caches on startup")
        # Log effective TTL to aid ops visibility
        ttl = os.getenv("LLM_CONFIG_CACHE_TTL", "300")
        wf_logger.info(f"⏱️ LLM_CACHE: Effective TTL (secs) = {ttl}")
    except Exception as e:
        wf_logger.error("LLM_CACHE_CLEAR_FAILED: Failed LLM cache management on startup", error=str(e))
    
    try:
        # Initialize performance / observability
        wf_logger.info("🔧 APP_STARTUP: Initializing performance manager...")
        perf_mgr = await get_performance_manager()
        await perf_mgr.initialize()
        wf_logger.info("✅ APP_STARTUP: Performance manager initialized")

        # Initialize simple transport
        streaming_start = datetime.now(UTC)
        simple_transport = await SimpleTransport.get_instance()
        app.state.simple_transport = simple_transport  # expose for platform extension routers
        streaming_time = (datetime.now(UTC) - streaming_start).total_seconds() * 1000
        performance_logger.info(
            "streaming_config_init_duration",
            metric_name="streaming_config_init_duration",
            value=float(streaming_time),
            config_keys=[],
            streaming_enabled=True,
        )

        # Build Mongo client now (after logging configured)
        global mongo_client
        if mongo_client is None:
            mongo_client = get_mongo_client()

        # Test MongoDB connection
        mongo_start = datetime.now(UTC)
        try:
            await mongo_client.admin.command("ping")
            mongo_time = (datetime.now(UTC) - mongo_start).total_seconds() * 1000
            performance_logger.info(
                "mongodb_ping_duration",
                metric_name="mongodb_ping_duration",
                value=float(mongo_time),
                unit="ms",
            )
        except Exception as e:
            get_workflow_logger("shared_app").error(
                "MONGODB_CONNECTION_FAILED: Failed to connect to MongoDB",
                error=str(e)
            )
            raise

        # Import workflow modules
        import_start = datetime.now(UTC)
        await _import_workflow_modules()
        import_time = (datetime.now(UTC) - import_start).total_seconds() * 1000
        performance_logger.info(
            "workflow_import_duration",
            metric_name="workflow_import_duration",
            value=float(import_time),
            unit="ms",
        )

        # Component system is event-driven, no upfront initialization needed.
        registry_start = datetime.now(UTC)
        registry_time = (datetime.now(UTC) - registry_start).total_seconds() * 1000
        performance_logger.info(
            "unified_registry_init_duration",
            metric_name="unified_registry_init_duration",
            value=float(registry_time),
            unit="ms",
        )

        # Log workflow and tool summary
        status = workflow_status_summary()

        # Start declared startup services (workflow plugins)
        global _runtime_services
        try:
            _runtime_services = await start_declared_services()
        except Exception as _svc_err:
            wf_logger.debug(f"RUNTIME_EXTENSIONS_SERVICES_NOT_STARTED: {_svc_err}")

        # Load discovered operations (if present)
        _platform_path = os.environ.get("PLATFORM_PATH", "platform")
        try:
            _app_result = await AppLoader.load(_platform_path)
            if _app_result.operations:
                _mod_executor = OperationExecutor()
                for _loaded_op in _app_result.operations:
                    _mod_executor.register(_loaded_op.name, _loaded_op.handler)
                executor_registry.register(_mod_executor)
                wf_logger.info(
                    f"OPERATION_EXECUTOR_READY: {len(_app_result.operations)} operation(s) registered "
                    f"({[op.name for op in _app_result.operations]})"
                )
        except AppLoadError:
            pass  # No app manifest — ai_only mode, operations not applicable
        except Exception as _mod_err:
            wf_logger.warning(f"MODULE_LOAD_FAILED: {_mod_err}")

        # Run platform extension startup hooks (mounts platform routes, inits managers)
        try:
            await get_platform_hooks().run_startup(app)
        except Exception as _ph_err:
            wf_logger.warning(f"PLATFORM_HOOKS_STARTUP_FAILED: {_ph_err}")

        # Total startup time
        total_startup_time = (datetime.now(UTC) - startup_start).total_seconds() * 1000
        performance_logger.info(
            "total_startup_duration",
            metric_name="total_startup_duration",
            value=float(total_startup_time),
            unit="ms",
            workflows_count=status.get("total_workflows", 0),
            tools_count=status.get("total_tools", 0),
        )

        # Business event
        await event_dispatcher.emit_business_event(
            log_event_type="SERVER_STARTUP_COMPLETED",
            description="Server startup completed successfully with unified event dispatcher",
            context={
                "environment": env,
                "startup_time_ms": total_startup_time,
                "workflows_registered": status.get("total_workflows", 0),
                "tools_available": status.get("total_tools", 0),
                "summary": status.get("summary", "Unknown")
            }
        )
        wf_logger.info(f"✅ Server ready - {status['summary']} (Startup: {total_startup_time:.1f}ms)")
    except Exception as e:
        startup_time = (datetime.now(UTC) - startup_start).total_seconds() * 1000
        get_workflow_logger("shared_app").error(
            "SERVER_STARTUP_FAILED: Server startup failed",
            environment=env,
            error=str(e),
            startup_time_ms=startup_time
        )
        raise

@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown"""
    global simple_transport
    shutdown_start = datetime.now(UTC)
    
    wf_logger.info("🛑 Shutting down server...")
    
    try:
        global _runtime_services
        if _runtime_services:
            try:
                await stop_services(_runtime_services)
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        _runtime_services = []

        if simple_transport:
            # No explicit disconnect needed for websockets with this transport design
            pass
        
        if mongo_client:
            mongo_client.close()
        
        # Calculate shutdown time and log metrics
        shutdown_time = (datetime.now(UTC) - shutdown_start).total_seconds() * 1000
        
        performance_logger.info(
            "shutdown_duration",
            extra={
                "metric_name": "shutdown_duration",
                "value": float(shutdown_time),
                "unit": "ms",
            },
        )
        
        get_workflow_logger("shared_app").info(
            "SERVER_SHUTDOWN_COMPLETED: Server shutdown completed successfully",
            shutdown_time_ms=shutdown_time,
        )
        
        wf_logger.info(f"✅ Shutdown complete ({shutdown_time:.1f}ms)")
        
    except Exception as e:
        shutdown_time = (datetime.now(UTC) - shutdown_start).total_seconds() * 1000
        get_workflow_logger("shared_app").error(
            "SERVER_SHUTDOWN_FAILED: Error during server shutdown",
            error=str(e),
            shutdown_time_ms=shutdown_time
        )

async def _import_workflow_modules():
    """
    Workflow system startup - using runtime auto-discovery.
    No more scanning for initializer.py files - workflows are discovered on-demand.
    """
    scan_start = datetime.now(UTC)
    
    # Runtime auto-discovery means no upfront imports needed
    # Workflows will be discovered when requested via WebSocket
    
    scan_time = (datetime.now(UTC) - scan_start).total_seconds() * 1000
    
    performance_logger.info(
        "workflow_discovery_duration",
        extra={
            "metric_name": "workflow_discovery_duration",
            "value": float(scan_time),
            "unit": "ms",
            "discovery_mode": "runtime_auto_discovery",
            "upfront_imports": 0,
        },
    )

    get_workflow_logger("shared_app").info(
        "WORKFLOW_SYSTEM_READY: Workflow system initialized with runtime auto-discovery",
        scan_duration_ms=scan_time,
        discovery_mode="runtime_on_demand"
    )

# ============================================================================
# API ENDPOINTS (WebSocket and workflow handling)
# ============================================================================
# ============================================================================
# Realtime events (platform-specific routes mounted by mozaiksai.platform via hooks)
# ============================================================================

@app.get("/api/events/metrics")
async def get_event_metrics(
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Get unified event dispatcher metrics"""
    try:
        metrics = event_dispatcher.get_metrics()
        
        return {
            "status": "success",
            "data": metrics,
            "timestamp": datetime.now(UTC).isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Failed to get event metrics: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve event metrics")

@app.get("/api/health")
async def health_check(
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Health check endpoint."""
    health_start = datetime.now(UTC)
    try:
        mongo_ping_start = datetime.now(UTC)
        if mongo_client is None:
            raise HTTPException(status_code=503, detail="MongoDB client not initialized")
        await mongo_client.admin.command("ping")
        mongo_ping_time = (datetime.now(UTC) - mongo_ping_start).total_seconds() * 1000
        status = workflow_status_summary()
        registered_workflows = status.get("registered_workflows") or []
        if not isinstance(registered_workflows, list):
            registered_workflows = []

        total_tools = 0
        for wf_name in registered_workflows:
            try:
                wf_tools = get_workflow_tools(wf_name)
                if isinstance(wf_tools, list):
                    total_tools += len(wf_tools)
            except Exception:
                # Best-effort: tool introspection should never break health.
                continue
        connection_info = {
            "websocket_connections": len(simple_transport.connections) if simple_transport else 0,
            "total_connections": len(simple_transport.connections) if simple_transport else 0
        }
        health_time = (datetime.now(UTC) - health_start).total_seconds() * 1000
        performance_logger.info(
            "health_check_duration",
            extra={
                "metric_name": "health_check_duration",
                "value": float(health_time),
                "unit": "ms",
                "mongodb_ping_ms": float(mongo_ping_time),
                "active_connections": connection_info["total_connections"],
                "workflows_count": len(registered_workflows),
            },
        )
        health_data = {
            # SDK-expected fields (primary)
            "status": "ok",  # SDK expects "ok" not "healthy"
            "version": os.getenv("MOZAIKS_VERSION", "5.0.0"),
            "workflows_loaded": len(registered_workflows),
            # Extended fields (for detailed health)
            "mongodb": "connected",
            "mongodb_ping_ms": round(mongo_ping_time, 2),
            "simple_transport": "initialized" if simple_transport else "not_initialized",
            "active_connections": connection_info,
            "workflows": registered_workflows,
            "transport_groups": status.get("transport_groups", {}),
            "tools_available": total_tools > 0,
            "total_tools": total_tools,
            "health_check_time_ms": round(health_time, 2)
        }
        wf_logger.debug(f"✅ Health check passed - Response time: {health_time:.1f}ms")
        return health_data
    except Exception as e:
        logger.error(f"❌ Health check failed: {e}")
        raise HTTPException(status_code=500, detail=f"Health check failed: {e}")

# ============================================================================
# Chat Management Endpoints
# ============================================================================


@app.post("/api/chats/{app_id}/{workflow_name}/start")
async def start_chat(
    app_id: str,
    workflow_name: str,
    request: Request,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Start a new chat session for a workflow.

    Idempotency / duplicate suppression strategy:
      - If an in-progress chat for (app_id, user_id, workflow_name) was created within the last N seconds
        (default 15) AND client did not set force_new=true, we *reuse* that chat_id instead of creating a new one.
      - Optional client-supplied "client_request_id" can further collapse rapid replays (e.g. browser double-submit).
    This prevents multiple empty ChatSessions docs when the frontend issues parallel start attempts during
    React StrictMode double-mount or network retries.
    """
    # Enforce app_id binding: token app_id must match path app_id
    validate_path_app_id(principal, app_id)
    
    IDEMPOTENCY_WINDOW_SEC = int(os.getenv("CHAT_START_IDEMPOTENCY_SEC", "15"))
    now = datetime.now(UTC)
    reuse_cutoff = now - timedelta(seconds=IDEMPOTENCY_WINDOW_SEC)
    try:
            data = await request.json()
            body_user_id = data.get("user_id")
            client_request_id = data.get("client_request_id")
            force_new = str(data.get("force_new", "false")).lower() in ("1", "true", "yes")
            # Transition-seeded initial context: dict of key/value pairs merged into workflow context_variables at start.
            transition_context: Dict[str, Any] = data.get("context_variables") or {}
            # Trigger metadata: source, action_id, change_class, artifact_version_id
            # Stored on session doc for observability/audit — not merged into context_variables.
            trigger_meta: Dict[str, Any] = data.get("trigger_meta") or {}
            
            # Validate and get canonical user_id from JWT
            user_id = _validate_user_id_against_principal(principal, body_user_id=body_user_id)

            # Platform hook: prerequisite dependency check (no-op when no platform registered).
            ok, prereq_error = await get_platform_hooks().call_chat_prereqs(
                app_id=app_id,
                user_id=user_id,
                workflow_name=workflow_name,
                persistence=persistence_manager,
            )
            if not ok:
                raise HTTPException(status_code=409, detail=prereq_error)

            # Obtain underlying lean chat sessions collection
            coll = await _chat_coll()

            # Reuse recent in-progress session if present (idempotent start)
            reused_doc = None
            if not force_new:
                base_query = {
                    "user_id": user_id,
                    "workflow_name": workflow_name,
                    "status": 0,
                    "created_at": {"$gte": reuse_cutoff},
                    **build_app_scope_filter(app_id),
                }
                # Prefer matching client_request_id (if the client intentionally reuses it),
                # but do not require it — frontend may generate a new UUID per attempt.
                if client_request_id:
                    reused_doc = await coll.find_one(
                        {**base_query, "client_request_id": client_request_id},
                        projection={"chat_id": 1, "created_at": 1},
                    )
                if not reused_doc:
                    reused_doc = await coll.find_one(base_query, projection={"chat_id": 1, "created_at": 1})

            if reused_doc:
                chat_id = reused_doc["chat_id"]
                try:
                    from mozaiksai.core.session import get_session_router

                    await get_session_router().bind_workflow_session(
                        app_id=app_id,
                        user_id=user_id,
                        workflow_id=workflow_name,
                        chat_id=chat_id,
                    )
                except Exception as bind_err:
                    logger.debug(f"session router bind skipped for reused chat {chat_id}: {bind_err}")
                get_workflow_logger("shared_app").info(
                    "CHAT_SESSION_REUSED: Existing recent chat reused",
                    app_id=app_id,
                    workflow_name=workflow_name,
                    user_id=user_id,
                    chat_id=chat_id,
                    reuse_window_sec=IDEMPOTENCY_WINDOW_SEC,
                )
                # Ensure a cache_seed exists for this chat (persist if newly assigned)
                try:
                    cache_seed = await persistence_manager.get_or_assign_cache_seed(chat_id, app_id)
                except Exception as se:
                    cache_seed = None
                    logger.debug(f"cache_seed assignment failed (reused chat {chat_id}): {se}")
                # Return existing without touching performance manager again
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

            # Generate a new chat ID
            chat_id = str(uuid4())

            # Create session doc immediately (idempotent); attach client_request_id for future reuse
            try:
                extra_fields: Dict[str, Any] = {}
                if client_request_id:
                    extra_fields["client_request_id"] = client_request_id
                # Store trigger metadata for observability — not merged into context_variables
                if trigger_meta and isinstance(trigger_meta, dict):
                    allowed_trigger_keys = {"trigger_source", "action_id", "change_class", "artifact_version_id"}
                    extra_fields["trigger_meta"] = {k: v for k, v in trigger_meta.items() if k in allowed_trigger_keys}
                # Store transition-seeded context keys directly so fetch_chat_session_extra_context
                # returns them and orchestration_patterns merges them into context_variables.
                # SECURITY: only allow keys that are declared in the workflow's
                # context_variables.yaml — prevents arbitrary key injection via URL ?context=.
                if transition_context and isinstance(transition_context, dict):
                    try:
                        from mozaiksai.core.workflow.workflow_manager import workflow_manager
                        wf_cfg = workflow_manager.get_config(workflow_name) or {}
                        declared_keys: set = set(
                            (wf_cfg.get("context_variables") or {}).get("definitions", {}).keys()
                        )
                    except Exception:
                        declared_keys = set()
                    for ctx_key, ctx_val in transition_context.items():
                        if not isinstance(ctx_key, str) or not ctx_key.strip():
                            continue
                        # Allow key if workflow declares it, OR if no declarations found
                        # (graceful degradation when context_variables.yaml is absent).
                        if declared_keys and ctx_key not in declared_keys:
                            wf_logger.warning(
                                "TRANSITION_CONTEXT_KEY_REJECTED: undeclared key stripped from transition context",
                                extra={"key": ctx_key, "workflow": workflow_name, "chat_id": chat_id},
                            )
                            continue
                        extra_fields[ctx_key] = ctx_val

                # Platform hook: inject extra session fields (journey metadata, etc.).
                try:
                    # chat_id is the newly generated UUID assigned a few lines above.
                    platform_fields = await get_platform_hooks().call_chat_session_fields(
                        app_id=app_id,
                        user_id=user_id,
                        workflow_name=workflow_name,
                        chat_id=chat_id,
                    )
                    if platform_fields:
                        extra_fields.update(platform_fields)
                except Exception as _pf_err:
                    logger.debug(f"platform session fields skipped: {_pf_err}")

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
                except Exception as bind_err:
                    logger.debug(f"session router bind skipped for {chat_id}: {bind_err}")
            except Exception as ce:
                logger.debug(f"chat_session pre-create skipped {chat_id}: {ce}")

            # Initialize performance tracking early
            try:
                perf_mgr = await get_performance_manager()
                await perf_mgr.record_workflow_start(chat_id, app_id, workflow_name, user_id)
            except Exception as perf_e:
                logger.debug(f"perf_start skipped {chat_id}: {perf_e}")

            get_workflow_logger("shared_app").info(
                "CHAT_SESSION_STARTED: New chat session initiated",
                app_id=app_id,
                workflow_name=workflow_name,
                user_id=user_id,
                chat_id=chat_id,
                idempotency_window_sec=IDEMPOTENCY_WINDOW_SEC,
            )

            # Assign per-chat cache seed (deterministic) and include in response
            try:
                cache_seed = await persistence_manager.get_or_assign_cache_seed(chat_id, app_id)
            except Exception as se:
                cache_seed = None
                logger.debug(f"cache_seed assignment failed (new chat {chat_id}): {se}")

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
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Failed to start chat session: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start chat: {e}")

@app.get("/api/chats/{app_id}/{workflow_name}")
async def list_chats(
    app_id: str,
    workflow_name: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """List recent chat IDs for a given app and workflow."""
    try:
        coll = await _chat_coll()
        query: Dict[str, Any] = {"workflow_name": workflow_name, **build_app_scope_filter(app_id)}
        if principal.user_id != "anonymous":
            query["user_id"] = principal.user_id
        cursor = coll.find(query).sort("created_at", -1)
        docs = await cursor.to_list(length=20)
        chat_ids = [doc.get("_id") for doc in docs]
        return {"chat_ids": chat_ids}
    except Exception as e:
        logger.error(f"❌ Failed to list chats for app {app_id}, workflow {workflow_name}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list chats")

@app.get("/api/chats/exists/{app_id}/{workflow_name}/{chat_id}")
async def chat_exists(
    app_id: str,
    workflow_name: str,
    chat_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Lightweight existence check for a chat session.

    Frontend uses this to decide whether to clear any cached artifact UI state
    before attempting restoration. We do NOT load the full transcript; only a
    projection on _id to keep this fast.
    """
    try:
        coll = await _chat_coll()
        query: Dict[str, Any] = {
            "_id": chat_id,
            "workflow_name": workflow_name,
            **build_app_scope_filter(app_id),
        }
        if principal.user_id != "anonymous":
            query["user_id"] = principal.user_id
        doc = await coll.find_one(
            query,
            {"_id": 1},
        )
        return {"exists": doc is not None}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to check chat existence: {e}")

@app.get("/api/sessions/list/{app_id}/{user_id}")
async def list_user_sessions(
    app_id: str,
    user_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """
    List all active/paused workflow sessions for a user.
    
    Used by frontend to render session tabs (like browser tabs).
    Returns sessions across all workflows so UI can show which ones are IN_PROGRESS.
    """
    # Validate path user_id matches JWT
    user_id = _validate_user_id_against_principal(principal, path_user_id=user_id)
    
    try:
        from mozaiksai.core.data.models import WorkflowStatus
        coll = await _chat_coll()
        
        # Find all IN_PROGRESS sessions for this user
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
                "last_artifact": session.get("last_artifact"),  # Quick metadata
            })
        
        wf_logger.debug(f"[LIST_SESSIONS] Found {len(result)} IN_PROGRESS sessions for user {user_id}")
        
        return {
            "sessions": result,
            "count": len(result)
        }
    except Exception as e:
        wf_logger.error(f"[LIST_SESSIONS] Failed to list sessions: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list sessions: {e}")


@app.get("/api/sessions/recent/{app_id}/{user_id}")
async def get_most_recent_workflow_session(
    app_id: str,
    user_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """
    Return the most recently updated IN_PROGRESS workflow session for a user.

    Used when toggling from general mode back to workflow mode to resume where the
    user most recently left off (least-surprising default).
    """
    # Validate path user_id matches JWT
    user_id = _validate_user_id_against_principal(principal, path_user_id=user_id)
    
    try:
        from mozaiksai.core.data.models import WorkflowStatus
        coll = await _chat_coll()

        # Find all IN_PROGRESS sessions, sorted by last_updated_at descending (most recent first)
        sessions = (
            await coll.find(
                {
                    "user_id": user_id,
                    "status": int(WorkflowStatus.IN_PROGRESS),
                    **build_app_scope_filter(app_id),
                }
            )
            .sort("last_updated_at", -1)
            .to_list(length=100)
        )

        if not sessions:
            wf_logger.debug(f"[RECENT_SESSION] No IN_PROGRESS workflows for user {user_id}")
            return {
                "found": False,
                "chat_id": None,
                "workflow_name": None,
            }

        recent = sessions[0]

        wf_logger.debug(
            f"[RECENT_SESSION] Returning most recent workflow {recent.get('workflow_name')} "
            f"chat_id={recent['_id']} for user {user_id}"
        )

        return {
            "found": True,
            "chat_id": recent["_id"],
            "workflow_name": recent.get("workflow_name"),
            "created_at": recent.get("created_at").isoformat() if recent.get("created_at") else None,
            "last_updated_at": recent.get("last_updated_at").isoformat() if recent.get("last_updated_at") else None,
            "last_artifact": recent.get("last_artifact"),
        }
    except Exception as e:
        wf_logger.error(f"[RECENT_SESSION] Failed to fetch most recent session: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch most recent session: {e}")


@app.get("/api/sessions/oldest/{app_id}/{user_id}")
async def get_oldest_workflow_session(
    app_id: str,
    user_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """
    Return the oldest IN_PROGRESS workflow session for a user.

    Useful when UX policy wants "finish oldest run first" semantics.
    """
    user_id = _validate_user_id_against_principal(principal, path_user_id=user_id)

    try:
        from mozaiksai.core.data.models import WorkflowStatus

        coll = await _chat_coll()
        sessions = (
            await coll.find(
                {
                    "user_id": user_id,
                    "status": int(WorkflowStatus.IN_PROGRESS),
                    **build_app_scope_filter(app_id),
                }
            )
            .sort("created_at", 1)
            .to_list(length=100)
        )

        if not sessions:
            wf_logger.debug(f"[OLDEST_SESSION] No IN_PROGRESS workflows for user {user_id}")
            return {
                "found": False,
                "chat_id": None,
                "workflow_name": None,
            }

        oldest = sessions[0]
        wf_logger.debug(
            f"[OLDEST_SESSION] Returning oldest workflow {oldest.get('workflow_name')} "
            f"chat_id={oldest['_id']} for user {user_id}"
        )

        return {
            "found": True,
            "chat_id": oldest["_id"],
            "workflow_name": oldest.get("workflow_name"),
            "created_at": oldest.get("created_at").isoformat() if oldest.get("created_at") else None,
            "last_updated_at": oldest.get("last_updated_at").isoformat() if oldest.get("last_updated_at") else None,
            "last_artifact": oldest.get("last_artifact"),
        }
    except Exception as e:
        wf_logger.error(f"[OLDEST_SESSION] Failed to fetch oldest session: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch oldest session: {e}")


@app.delete("/api/sessions/{app_id}/{user_id}")
async def delete_user_sessions(
    app_id: str,
    user_id: str,
    status: str = "in_progress",
    workflow_name: Optional[str] = None,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """
    Delete workflow sessions for a user/app scope.

    Query params:
      - status: in_progress | completed | all (default: in_progress)
      - workflow_name: optional workflow filter
    """
    user_id = _validate_user_id_against_principal(principal, path_user_id=user_id)

    try:
        from mozaiksai.core.data.models import WorkflowStatus

        normalized_status = str(status or "in_progress").strip().lower()
        query: Dict[str, Any] = {
            "user_id": user_id,
            **build_app_scope_filter(app_id),
        }

        if workflow_name:
            query["workflow_name"] = str(workflow_name).strip()

        if normalized_status in {"in_progress", "active", "open"}:
            query["status"] = int(WorkflowStatus.IN_PROGRESS)
        elif normalized_status in {"completed", "done", "closed"}:
            query["status"] = int(WorkflowStatus.COMPLETED)
        elif normalized_status in {"all", "any", "*"}:
            pass
        else:
            raise HTTPException(
                status_code=400,
                detail="status must be one of: in_progress, completed, all",
            )

        coll = await _chat_coll()
        result = await coll.delete_many(query)
        deleted_count = int(result.deleted_count or 0)

        wf_logger.info(
            "[DELETE_SESSIONS] Deleted workflow sessions",
            extra={
                "app_id": app_id,
                "user_id": user_id,
                "status": normalized_status,
                "workflow_name": workflow_name,
                "deleted_count": deleted_count,
            },
        )

        return {
            "success": True,
            "app_id": app_id,
            "user_id": user_id,
            "status": normalized_status,
            "workflow_name": workflow_name,
            "deleted_count": deleted_count,
        }
    except HTTPException:
        raise
    except Exception as e:
        wf_logger.error(f"[DELETE_SESSIONS] Failed to delete sessions: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete sessions: {e}")


@app.delete("/api/general_chats/{app_id}/{user_id}")
async def delete_general_chats(
    app_id: str,
    user_id: str,
    status: str = "all",
    principal: UserPrincipal = Depends(require_user_scope),
):
    """
    Delete Ask-mode general chats for a user/app scope.

    Query params:
      - status: in_progress | completed | all (default: all)
    """
    user_id = _validate_user_id_against_principal(principal, path_user_id=user_id)

    try:
        from mozaiksai.core.data.models import WorkflowStatus

        normalized_status = str(status or "all").strip().lower()
        query: Dict[str, Any] = {
            "user_id": user_id,
            **build_app_scope_filter(app_id),
        }

        if normalized_status in {"in_progress", "active", "open"}:
            query["status"] = int(WorkflowStatus.IN_PROGRESS)
        elif normalized_status in {"completed", "done", "closed"}:
            query["status"] = int(WorkflowStatus.COMPLETED)
        elif normalized_status in {"all", "any", "*"}:
            pass
        else:
            raise HTTPException(
                status_code=400,
                detail="status must be one of: in_progress, completed, all",
            )

        general_coll = await persistence_manager._general_coll()
        result = await general_coll.delete_many(query)
        deleted_count = int(result.deleted_count or 0)

        # If we cleared all general chats for this scope, reset the sequence counter too.
        if normalized_status in {"all", "any", "*"}:
            try:
                counter_coll = await persistence_manager._general_counter_coll()
                await counter_coll.delete_many({
                    "user_id": user_id,
                    **build_app_scope_filter(app_id),
                })
            except Exception as counter_err:  # pragma: no cover - non-fatal
                wf_logger.debug(f"[DELETE_GENERAL_CHATS] Counter reset skipped: {counter_err}")

        wf_logger.info(
            "[DELETE_GENERAL_CHATS] Deleted general chats",
            extra={
                "app_id": app_id,
                "user_id": user_id,
                "status": normalized_status,
                "deleted_count": deleted_count,
            },
        )

        return {
            "success": True,
            "app_id": app_id,
            "user_id": user_id,
            "status": normalized_status,
            "deleted_count": deleted_count,
        }
    except HTTPException:
        raise
    except Exception as e:
        wf_logger.error(f"[DELETE_GENERAL_CHATS] Failed to delete general chats: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete general chats: {e}")


# NOTE:
# - These REST routes are optional platform enrichments for list/transcript UX.
#   When platform routers are mounted via RUNTIME_PLATFORM_EXTENSIONS they may
#   override these fallbacks.
# - Fallback behavior below keeps frontend polling noise-free when optional
#   platform routes are not present.


@app.get("/api/notifications/count")
async def notifications_count_fallback(
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Fallback unread-count endpoint when notification service is absent."""
    return {"count": 0, "unread_count": 0}


@app.get("/api/general_chats/list/{app_id}/{user_id}")
async def list_general_chats_fallback(
    app_id: str,
    user_id: str,
    limit: int = 50,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Fallback Ask-mode chat list endpoint when general chat service is absent."""
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
    """Fallback Ask-mode transcript endpoint when general chat service is absent."""
    _ = principal  # principal check enforced by dependency
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
    """Return lightweight chat metadata including cache_seed, last_artifact, and artifact_instance.

    This allows a second user/browser to restore artifact UI state even if local
    storage is empty. Includes both UI tool artifacts (last_artifact) and
    WorkflowSession artifacts (artifact_instance_id + state) for multi-workflow navigation.
    
    Does not return full transcript.
    """
    try:
        has_children = False
        try:
            from mozaiksai.core.workflow.pack.graph import workflow_has_mid_flight_journeys

            has_children = workflow_has_mid_flight_journeys(workflow_name)
        except Exception:
            has_children = False

        coll = await _chat_coll()
        projection = {"cache_seed": 1, "last_artifact": 1, "status": 1, "last_sequence": 1, "_id": 1, "workflow_name": 1}
        query: Dict[str, Any] = {"_id": chat_id, "workflow_name": workflow_name, **build_app_scope_filter(app_id)}
        if principal.user_id != "anonymous":
            query["user_id"] = principal.user_id
        doc = await coll.find_one(
            query,
            projection,
        )
        if not doc:
            return {"exists": False}
        
        # Also fetch artifact instance from WorkflowSessions (for multi-workflow navigation)
        artifact_instance_id = None
        artifact_state = None
        try:
            from mozaiksai.core.workflow import session_manager
            workflow_session = await session_manager.get_workflow_session(chat_id, app_id)
            if workflow_session and workflow_session.get("artifact_instance_id"):
                artifact_instance_id = workflow_session["artifact_instance_id"]
                # Fetch full artifact state for restoration
                artifact_doc = await session_manager.get_artifact_instance(artifact_instance_id, app_id)
                if artifact_doc:
                    artifact_state = artifact_doc.get("state")
                    wf_logger.debug(
                        f"[CHAT_META] Retrieved artifact instance {artifact_instance_id} for chat {chat_id}"
                    )
        except Exception as artifact_err:
            wf_logger.warning(f"[CHAT_META] Failed to retrieve artifact instance for chat {chat_id}: {artifact_err}")
        
        return {
            "exists": True,
            "chat_id": chat_id,
            "workflow_name": workflow_name,
            "has_children": has_children,
            "cache_seed": doc.get("cache_seed"),
            "status": doc.get("status"),
            "last_sequence": doc.get("last_sequence"),
            "last_artifact": doc.get("last_artifact"),  # UI tool artifacts (legacy/quick restore)
            "artifact_instance_id": artifact_instance_id,  # WorkflowSession artifact ID
            "artifact_state": artifact_state,  # Full artifact state for multi-workflow navigation
            "app_id": app_id,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load chat meta: {e}")
    

@app.websocket("/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    workflow_name: str,
    app_id: str,
    chat_id: str,
    user_id: str,
):
    """WebSocket endpoint for real-time agent communication with multi-workflow session support."""
    if not simple_transport:
        await websocket.close(code=1000, reason="Transport service not available")
        return

    # Authenticate WebSocket connection and validate path bindings (user_id, app_id, chat_id)
    ws_user = await authenticate_websocket_with_path_binding(
        websocket, 
        path_user_id=user_id,
        path_app_id=app_id,
        path_chat_id=chat_id,
    )
    if ws_user is None:
        return  # Connection already closed with 1008
    
    # Use canonical user_id from JWT (or path if auth disabled)
    user_id = ws_user.user_id

    # If chat_id already exists, ensure it belongs to this principal to prevent cross-user access.
    try:
        coll = await _chat_coll()
        existing = await coll.find_one(
            {"_id": chat_id, **build_app_scope_filter(app_id)},
            {"_id": 1, "user_id": 1, "workflow_name": 1},
        )
        if existing:
            owner = existing.get("user_id")
            wf = existing.get("workflow_name")
            if not owner or str(owner).strip() != str(user_id).strip():
                await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason="Chat not found")
                return
            if wf and str(wf).strip() != str(workflow_name).strip():
                await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason="Chat not found")
                return
    except Exception as ownership_err:
        wf_logger.debug(f"WS_CHAT_OWNERSHIP_CHECK_SKIPPED: {ownership_err}")

    # Register this WebSocket connection in session registry
    from mozaiksai.core.transport.session_registry import session_registry
    ws_id = id(websocket)

    # Validate workflow prerequisites early (fail-closed) so we don't create/buffer state
    # for workflows the user is not allowed to start/resume.
    try:
        is_valid, error_msg = await get_platform_hooks().call_chat_prereqs(
            app_id=app_id,
            user_id=user_id,
            workflow_name=workflow_name,
            persistence=persistence_manager,
        )

        if not is_valid:
            wf_logger.warning(
                "WS_PREREQS_NOT_MET",
                extra={
                    "workflow_name": workflow_name,
                    "app_id": app_id,
                    "user_id": user_id,
                    "error": error_msg,
                    "chat_id": chat_id,
                },
            )
            try:
                await websocket.accept()
                await websocket.send_json(
                    {
                        "type": "chat.error",
                        "data": {
                            "message": error_msg,
                            "error_code": "WORKFLOW_PREREQS_NOT_MET",
                            "workflow_name": workflow_name,
                            "chat_id": chat_id,
                        },
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )
            except Exception:
                pass
            await websocket.close(code=1008, reason="Prerequisites not met")
            return
    except Exception as dep_err:
        wf_logger.error(f"WS_PREREQ_VALIDATION_FAILED: {dep_err}", exc_info=True)
        try:
            await websocket.accept()
            await websocket.send_json(
                {
                    "type": "chat.error",
                    "data": {
                        "message": "Failed to validate workflow prerequisites. Please try again.",
                        "error_code": "PREREQ_VALIDATION_ERROR",
                        "workflow_name": workflow_name,
                        "chat_id": chat_id,
                    },
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
        except Exception:
            pass
        await websocket.close(code=1011, reason="Prerequisite validation failed")
        return

    wf_logger.info(f"🔌 New WebSocket connection for workflow '{workflow_name}' (incoming chat_id={chat_id}, ws_id={ws_id})")

    # SessionRouter-owned resume target resolution
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

        coll = await _chat_coll()
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
            wf_logger.info(
                "WS_SESSION_CREATED",
                extra={"chat_id": active_chat_id, "workflow_name": workflow_name, "resolved_from": resume_resolution.get("resolved_from")},
            )
        else:
            wf_logger.info(
                "WS_AUTO_RESUME",
                extra={
                    "chat_id": active_chat_id,
                    "incoming_chat_id": chat_id,
                    "workflow_name": workflow_name,
                    "resolved_from": resume_resolution.get("resolved_from"),
                },
            )
    except Exception as pre_err:
        wf_logger.error(f"WS_SESSION_DETERMINATION_FAILED: {pre_err}")

    # Auto-start AgentDriven workflows once the socket is accepted and registered
    async def _auto_start_if_needed():
        try:
            from mozaiksai.core.workflow.workflow_manager import workflow_manager
            # In development, pick up YAML edits (workflow_startup_mode, initial_message, etc.)
            # without requiring a Python process restart.
            try:
                if os.getenv("ENVIRONMENT", "development").lower() != "production":
                    workflow_manager.reload_workflow(workflow_name)
            except Exception as reload_err:
                wf_logger.debug(f"Workflow hot-reload skipped for {workflow_name}: {reload_err}")

            cfg = workflow_manager.get_config(workflow_name) or {}
            startup_mode = str(cfg.get("workflow_startup_mode") or "").strip() or "AgentDriven"
            if startup_mode != "AgentDriven":
                wf_logger.debug(
                    "WS_AUTO_START_SKIPPED",
                    extra={
                        "workflow_name": workflow_name,
                        "chat_id": active_chat_id,
                        "reason": f"workflow_startup_mode={startup_mode}",
                    },
                )
                return

            # Never auto-start from completed or non-fresh sessions.
            # AgentDriven auto-start is only for brand-new chats.
            coll = await _chat_coll()
            chat_doc = await coll.find_one(
                {"_id": active_chat_id, "user_id": user_id, **build_app_scope_filter(app_id)},
                {"status": 1, "last_sequence": 1, "messages": {"$slice": 1}},
            )
            if not chat_doc:
                return

            status = int(chat_doc.get("status", -1))
            if status != 0:
                wf_logger.debug(
                    "WS_AUTO_START_SKIPPED",
                    extra={
                        "workflow_name": workflow_name,
                        "chat_id": active_chat_id,
                        "reason": f"status={status}",
                    },
                )
                return

            last_sequence = int(chat_doc.get("last_sequence", 0) or 0)
            has_messages = bool(chat_doc.get("messages"))
            if last_sequence > 0 or has_messages:
                wf_logger.debug(
                    "WS_AUTO_START_SKIPPED",
                    extra={
                        "workflow_name": workflow_name,
                        "chat_id": active_chat_id,
                        "reason": f"not_fresh last_sequence={last_sequence} has_messages={has_messages}",
                    },
                )
                return

            local_transport = simple_transport
            if not local_transport:
                return

            # wait until the connection is registered
            for _ in range(20):  # poll for registration using active_chat_id
                conn = local_transport.connections.get(active_chat_id)
                if conn and conn.get("websocket") is not None:
                    # idempotency guard so auto-start runs at most once per socket
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
        except Exception as e:
            logger.error(f"Auto-start failed for {workflow_name}/{active_chat_id}: {e}")

    asyncio.create_task(_auto_start_if_needed())

    # Emit an initial metadata event (chat_meta) with cache_seed for frontend cache alignment
    try:
        has_children = False
        try:
            from mozaiksai.core.workflow.pack.graph import workflow_has_mid_flight_journeys

            has_children = workflow_has_mid_flight_journeys(workflow_name)
        except Exception:
            has_children = False

        chat_exists = False
        coll = None
        try:
            coll = await _chat_coll()
            existing_doc = await coll.find_one(
                {"_id": active_chat_id, "user_id": user_id, **build_app_scope_filter(app_id)},
                {"_id": 1},
            )
            chat_exists = existing_doc is not None
        except Exception as ce:
            wf_logger.debug(f"chat existence check failed for {active_chat_id}: {ce}")

        # If chat does not exist, create a minimal session doc BEFORE assigning seed
        if not chat_exists:
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
                    wf_logger.debug(f"WS backfill bind skipped for {active_chat_id}: {bind_err}")
                chat_exists = True
                wf_logger.info("WS_BACKFILL_SESSION_CREATED", extra={"chat_id": active_chat_id})
            except Exception as ce:
                wf_logger.debug(f"Failed to backfill chat session for {active_chat_id}: {ce}")

        try:
            cache_seed = await persistence_manager.get_or_assign_cache_seed(active_chat_id, app_id)
        except Exception as ce:
            cache_seed = None
            wf_logger.debug(f"cache_seed retrieval failed for WS {active_chat_id}: {ce}")

        if session_state_payload is None:
            try:
                from mozaiksai.core.session import get_session_router

                session_state_payload = await get_session_router().get_session_snapshot(
                    app_id=app_id,
                    user_id=user_id,
                )
            except Exception as session_err:
                wf_logger.debug(f"session snapshot unavailable for {active_chat_id}: {session_err}")

        if simple_transport:
            # Attempt to include last_artifact for immediate restore (avoid separate HTTP roundtrip)
            last_artifact = None
            created_at_iso = None
            doc = None
            try:
                if coll is not None:
                    doc = await coll.find_one(
                        {"_id": active_chat_id, "user_id": user_id, **build_app_scope_filter(app_id)},
                        {"last_artifact": 1, "created_at": 1, "status": 1, "last_sequence": 1}
                    )
                    if doc:
                        last_artifact = doc.get("last_artifact")
                        ca = doc.get("created_at")
                        if ca:
                            try:
                                created_at_iso = ca.isoformat()
                            except Exception:
                                created_at_iso = str(ca)
            except Exception as la_err:
                wf_logger.debug(f"last_artifact fetch failed for chat_meta {active_chat_id}: {la_err}")

            await simple_transport.send_event_to_ui({
                'kind': 'chat_meta',
                'chat_id': active_chat_id,
                'workflow_name': workflow_name,
                'app_id': app_id,
                'app_id': app_id,
                'user_id': user_id,
                'has_children': has_children,
                'cache_seed': cache_seed,
                'chat_exists': chat_exists,
                'last_artifact': last_artifact,
                'status': doc.get("status") if doc else None,
                'last_sequence': doc.get("last_sequence") if doc else None,
                'created_at': created_at_iso,
                'session_state': session_state_payload,
            }, active_chat_id)
            wf_logger.info(
                "CHAT_META_EMITTED",
                extra={
                    "chat_id": active_chat_id,
                    "workflow_name": workflow_name,
                    "app_id": app_id,
                    "cache_seed": cache_seed,
                    "chat_exists": chat_exists,
                    "has_last_artifact": bool(last_artifact),
                    "created_at": created_at_iso,
                },
            )
    except Exception as meta_e:
        wf_logger.debug(f"Failed to emit chat_meta for {active_chat_id}: {meta_e}")
    
    # Register initial workflow in session registry
    session_registry.add_workflow(
        ws_id=ws_id,
        chat_id=active_chat_id,
        workflow_name=workflow_name,
        app_id=app_id,
        user_id=user_id,
        auto_activate=True
    )

    try:
        await simple_transport.handle_websocket(
            websocket=websocket,
            chat_id=active_chat_id,
            user_id=user_id,
            workflow_name=workflow_name,
            app_id=app_id,
            ws_id=ws_id  # Pass ws_id for session switching
        )
    finally:
        # Clean up session registry on disconnect
        session_registry.remove_session(ws_id)
        wf_logger.info(f"🔌 Cleaned up session registry for ws_id={ws_id}")

@app.post("/chat/{app_id}/{chat_id}/{user_id}/input")
async def handle_user_input(
    request: Request,
    app_id: str,
    chat_id: str,
    user_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Endpoint to receive user input and trigger the workflow."""
    # Validate path user_id matches JWT
    user_id = _validate_user_id_against_principal(principal, path_user_id=user_id)
    
    if not simple_transport:
        raise HTTPException(status_code=503, detail="Transport service is not available.")

    try:
        # Ensure the chat exists and is owned by the authenticated principal.
        try:
            coll = await _chat_coll()
            owned = await coll.find_one(
                {"_id": chat_id, "user_id": user_id, **build_app_scope_filter(app_id)},
                {"_id": 1},
            )
            if not owned:
                raise HTTPException(status_code=404, detail="Chat not found")
        except HTTPException:
            raise
        except Exception as owner_err:
            raise HTTPException(status_code=500, detail=f"Failed to validate chat ownership: {owner_err}")

        data = await request.json()
        message = data.get("message")
        workflow_name = data.get("workflow_name")  # No default, must be provided
        
        get_workflow_logger("shared_app").info(
            "USER_INPUT_ENDPOINT_CALLED: User input endpoint called",
            app_id=app_id,
            chat_id=chat_id,
            user_id=user_id,
            workflow_name=workflow_name,
            message_length=(len(message) if message else 0)
        )
        
        if not message:
            raise HTTPException(status_code=400, detail="Message cannot be empty.")

        result = await simple_transport.handle_user_input_from_api(
            chat_id=chat_id,
            user_id=user_id,
            workflow_name=workflow_name,
            message=message,
            app_id=app_id
        )

        
        get_workflow_logger("shared_app").info(
            "USER_INPUT_PROCESSED: User input processed successfully",
            chat_id=chat_id,
            transport=result.get("transport")
        )
        
        return {"status": "Message received and is being processed.", "transport": result.get("transport")}

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")
    except Exception as e:
        logger.error(f"? Error handling user input for chat {chat_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to process input: {e}")

@app.post("/api/user-input/submit")
async def submit_user_input_response(
    request: Request,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """
    API endpoint for submitting user input responses.
    
    This endpoint is called by the frontend when a user responds to a user input request
    sent via WebSocket from AG2 agents.
    """
    if not simple_transport:
        raise HTTPException(status_code=503, detail="Transport service is not available.")

    try:
        data = await request.json()
        input_request_id = data.get("input_request_id")
        user_input = data.get("user_input")
        
        if not input_request_id:
            raise HTTPException(status_code=400, detail="'input_request_id' field is required.")
        if not user_input:
            raise HTTPException(status_code=400, detail="'user_input' field is required.")
        
        # Submit the user input to the transport layer
        success = await simple_transport.submit_user_input(input_request_id, user_input)
        
        if success:
            get_workflow_logger("shared_app").info(
                "USER_INPUT_RESPONSE_SUBMITTED: User input response submitted",
                input_request_id=input_request_id,
                input_length=len(user_input)
            )
            return {"status": "success", "message": "User input submitted successfully"}
        else:
            raise HTTPException(status_code=404, detail="Input request not found or already completed")

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")
    except Exception as e:
        logger.error(f"? Error submitting user input response: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to submit user input: {e}")

@app.get("/api/workflows/{workflow_name}/transport")
async def get_workflow_transport_info(
    workflow_name: str,
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Get transport information for a specific workflow."""
    transport = get_workflow_transport(workflow_name)
    
    return {
        "workflow_name": workflow_name,
        "transport": transport,
        "endpoints": {
            "websocket": f"/ws/{workflow_name}/{{app_id}}/{{chat_id}}/{{user_id}}",
            "input": "/chat/{{app_id}}/{{chat_id}}/{{user_id}}/input"
        }
    }

@app.get("/api/workflows/{workflow_name}/tools")
async def get_workflow_tools_info(
    workflow_name: str,
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Get UI tools manifest for a specific workflow."""
    tools = get_workflow_tools(workflow_name)
    
    return {
        "workflow_name": workflow_name,
        "tools": tools
    }

@app.get("/api/workflows/{workflow_name}/ui-tools")
async def get_workflow_ui_tools_manifest(
    workflow_name: str,
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Get UI tools manifest with schemas for frontend development."""
    try:
        from mozaiksai.core.workflow.workflow_manager import workflow_manager
        ui_tools = workflow_manager.get_workflow_tools(workflow_name)
        manifest = []
        for rec in ui_tools:
            manifest.append({
                "ui_tool_id": rec.get("tool_id"),
                "component": rec.get("component"),
                "mode": rec.get("mode"),
                "agent": rec.get("agent"),
                "workflow": workflow_name
            })
        return {"workflow_name": workflow_name, "ui_tools_count": len(manifest), "ui_tools": manifest}
        
    except Exception as e:
        logger.error(f"Error getting UI tools manifest for {workflow_name}: {e}")
        return {
            "workflow_name": workflow_name,
            "ui_tools_count": 0,
            "ui_tools": [],
            "error": str(e)
        }

# ==============================================================================
# TOKEN API ENDPOINTS
# ==============================================================================


# ==============================================================================
# BACKEND-TO-BACKEND WORKFLOW TRIGGER
# ==============================================================================

class TriggerWorkflowRequest(BaseModel):
    """Request body for programmatic workflow trigger."""
    user_id: str = Field(..., description="User ID to run workflow as")
    app_id: Optional[str] = Field(None, description="Application ID (optional)")
    context: Optional[Dict[str, Any]] = Field(None, description="Initial context variables")
    webhook_url: Optional[str] = Field(None, description="URL to POST completion notification")


@app.post("/api/workflows/{workflow_name}/trigger")
async def trigger_workflow(
    workflow_name: str,
    request: TriggerWorkflowRequest,
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Trigger a workflow programmatically (backend-to-backend).

    This endpoint creates a new chat session and optionally registers a webhook
    for completion notification. Use this for automated workflows, scheduled tasks,
    or child workflow spawning.

    Returns:
        - chat_id: The created chat session ID
        - run_id: Alias for chat_id (for tracking)
        - success: Whether the trigger was successful
    """
    try:
        from mozaiksai.core.workflow.workflow_manager import workflow_manager

        # Validate workflow exists
        if workflow_name not in workflow_manager.get_all_workflow_names():
            raise HTTPException(status_code=404, detail=f"Workflow '{workflow_name}' not found")

        # Use provided app_id or generate one for headless runs
        app_id = request.app_id or f"trigger-{workflow_name}"
        user_id = request.user_id

        # Generate chat ID (this is also the run_id)
        chat_id = str(uuid4())
        run_id = chat_id  # run_id is an alias for tracking

        # Prepare extra fields for the session
        extra_fields: Dict[str, Any] = {
            "trigger_mode": "api",  # Mark as API-triggered
        }

        # Store webhook URL if provided (for completion notification)
        if request.webhook_url:
            extra_fields["webhook_url"] = request.webhook_url

        # Store initial context if provided
        if request.context:
            extra_fields["initial_context"] = request.context

        # Create the chat session
        await persistence_manager.create_chat_session(
            chat_id=chat_id,
            app_id=app_id,
            workflow_name=workflow_name,
            user_id=user_id,
            extra_fields=extra_fields,
        )

        # Initialize performance tracking
        try:
            perf_mgr = await get_performance_manager()
            await perf_mgr.record_workflow_start(chat_id, app_id, workflow_name, user_id)
        except Exception as perf_e:
            logger.debug(f"perf_start skipped for trigger {chat_id}: {perf_e}")

        get_workflow_logger("shared_app").info(
            "WORKFLOW_TRIGGERED: Workflow triggered via API",
            app_id=app_id,
            workflow_name=workflow_name,
            user_id=user_id,
            chat_id=chat_id,
            run_id=run_id,
            has_webhook=bool(request.webhook_url),
            has_context=bool(request.context),
        )

        return {
            "success": True,
            "chat_id": chat_id,
            "run_id": run_id,
            "workflow_name": workflow_name,
            "app_id": app_id,
            "user_id": user_id,
            "websocket_url": f"/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}",
            "message": "Workflow triggered. Connect via WebSocket or poll for completion.",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Failed to trigger workflow {workflow_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to trigger workflow: {e}")


# ==============================================================================
# PLATFORM CONFIG ENDPOINTS
# ==============================================================================

def _resolve_platform_path() -> Path:
    """Resolve the active platform root directory."""
    platform_path = os.environ.get("PLATFORM_PATH", "")
    if platform_path:
        candidate = Path(platform_path)
        if candidate.is_absolute():
            return candidate
        return (Path(__file__).parent / candidate).resolve()
    # Monorepo local dev default
    monorepo = Path(__file__).parent / "mozaiks-platform" / "app"
    if monorepo.is_dir():
        return monorepo
    return Path(__file__).parent / "platform"


@app.get("/api/shell-config")
async def get_shell_config():
    """Return app-shell config composed from app, AI, shell, page, UI, and workflow owners."""
    platform_root = _resolve_platform_path()
    ai_path = platform_root / "config" / "ai.json"
    if not ai_path.exists():
        # Legacy fallback for repos still using ./platform/config as the active shell config.
        ai_path = Path(__file__).parent / "platform" / "config" / "ai.json"

    result: dict = {"chat_startup_mode": "ask", "landing_spot": "/"}

    # 0a. App startup metadata belongs to app.json, not shell chrome.
    try:
        app_manifest_path = _resolve_app_manifest_path()
        if app_manifest_path.exists():
            app_manifest = json.loads(app_manifest_path.read_text(encoding="utf-8"))
            startup = app_manifest.get("startup") if isinstance(app_manifest.get("startup"), dict) else {}
            landing_spot = startup.get("landing_spot")
            if isinstance(landing_spot, str) and landing_spot.startswith("/"):
                result["landing_spot"] = landing_spot
    except Exception as e:
        logger.warning(f"[shell-config] Could not read app startup config: {e}")

    if ai_path.exists():
        try:
            ai = json.loads(ai_path.read_text(encoding="utf-8"))
            chat = ai.get("chat") or {}
            workflows = ai.get("workflows") or {}
            result["chat_startup_mode"] = chat.get("chat_startup_mode") or chat.get("startup_mode") or "ask"
            result["entry_point"] = workflows.get("entry_point")
            result["resume_policy"] = workflows.get("resume_policy")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read shell config: {e}")

    # 0b. Seed app-shell UI chrome from shell.json when available.
    try:
        shell_config_path = _resolve_shell_config_path()
        if shell_config_path.exists():
            shell_config = json.loads(shell_config_path.read_text(encoding="utf-8"))
            for key in ("header", "profile", "notifications", "footer"):
                value = shell_config.get(key)
                if value is not None:
                    result[key] = value
    except Exception as e:
        logger.warning(f"[shell-config] Could not read shell config: {e}")

    # 1. Compose routes from owners. No central route manifest.
    pages: List[dict] = []
    try:
        pages.extend(_load_ui_extension_pages(platform_root))
    except Exception as e:
        logger.warning(f"[shell-config] Could not read UI extension routes: {e}")
    try:
        pages.extend(_load_page_schema_routes(platform_root))
    except Exception as e:
        logger.warning(f"[shell-config] Could not read page schema routes: {e}")
    try:
        pages.extend(_load_workflow_entrypoint_pages(platform_root))
    except Exception as e:
        logger.warning(f"[shell-config] Could not read workflow entrypoint routes: {e}")

    if pages:
        result["pages"] = _dedupe_and_sort_pages(pages)

    # 2. Auto-inject admin page when admin.json is present and enabled.
    #    The generator writes admin.json; the runtime wires the route automatically.
    admin_config_path = platform_root / "config" / "admin.json"
    if admin_config_path.exists():
        try:
            admin_cfg = json.loads(admin_config_path.read_text(encoding="utf-8"))
            if admin_cfg.get("enabled", True):
                pages = result.get("pages", [])
                if not any(p.get("path") == "/admin" for p in pages):
                    pages.append({
                        "path": "/admin",
                        "component": "AdminPortal",
                        "label": "Admin Portal",
                        "order": 999,
                        "meta": {
                            "requiresAuth": True,
                            "requiresRole": "admin",
                            "title": "Admin Portal",
                            "appShell": True,
                        },
                    })
                    result["pages"] = pages
        except Exception as e:
            logger.warning(f"[shell-config] Could not read admin.json: {e}")

    return result


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
        "meta": {
            **meta,
            "requiresAuth": entry.get("requiresAuth", True),
        },
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
    """Load persistent React page routes from UI extension owner manifests."""
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
    """Derive SchemaPage routes directly from pages/*.yaml owner files."""
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
        meta: dict = {"title": title, "appShell": True}
        if isinstance(roles, list) and roles:
            meta["roles"] = roles
        page = {
            "path": route,
            "label": title,
            "component": "SchemaPage",
            "schema": name,
            "order": 100 + index,
            "meta": meta,
        }
        page["meta"]["requiresAuth"] = True
        pages.append(page)
    return pages


def _load_workflow_entrypoint_pages(platform_root: Path) -> List[dict]:
    """Derive transition/workflow routes from extension_registry entrypoints."""
    from mozaiksai.core.workflow.pack.config import list_entrypoints
    from mozaiksai.core.workflow.pack.schema import parse_global_pack_graph

    registry_path = platform_root / "workflows" / "extended_orchestration" / "extension_registry.json"
    if not registry_path.exists():
        return []
    raw = json.loads(registry_path.read_text(encoding="utf-8"))
    pack = parse_global_pack_graph(raw)

    pages: List[dict] = []
    for index, entry in enumerate(list_entrypoints(pack)):
        raw = entry.model_dump(exclude_none=True)
        page = _normalize_shell_page_entry(raw, order_fallback=200 + index)
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
    """
    Resolve the active theme config path from the current platform layout.

    Supported layouts:
      - product app bundle: <platform>/app + sibling <platform>/brand/theme_config.json
      - legacy app bundle: <platform>/brand/theme_config.json
      - OSS fallback: platform/config/theme_config.json
    """
    platform_root = _resolve_platform_path()
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
    """Resolve the active shell config path from the current platform layout."""
    platform_root = _resolve_platform_path()
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
    """Resolve the active app manifest path from the current platform layout."""
    platform_root = _resolve_platform_path()
    candidates = [
        platform_root / "app.json",
        Path(__file__).parent / "platform" / "app.json",
    ]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved
    return candidates[-1].resolve()


@app.get("/api/theme-config")
async def get_theme_config():
    """Return theme config for frontend (no auth required for config)."""
    config_path = _resolve_theme_config_path()
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="Theme config not found")
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read theme config: {e}")

@app.get("/api/themes/{app_id}")
async def get_app_theme(app_id: str):
    """Return theme config for a specific app (falls back to platform theme)."""
    config_path = _resolve_theme_config_path()
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="Theme config not found")
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read theme config: {e}")


def _resolve_pages_dir() -> Path:
    """Resolve the generated pages directory.

    Preference order:
      1. $PLATFORM_PATH/pages/          (production / container deploy)
      2. mozaiks-platform/app/pages/    (local dev monorepo)
      3. platform/pages/                (fallback)
    """
    platform_path = os.getenv("PLATFORM_PATH")
    if platform_path:
        candidate = Path(platform_path) / "pages"
        if candidate.is_dir():
            return candidate
    # Monorepo local dev path
    monorepo = Path(__file__).parent / "mozaiks-platform" / "app" / "pages"
    if monorepo.is_dir():
        return monorepo
    return Path(__file__).parent / "platform" / "pages"


@app.get("/api/pages/{name}")
async def get_page_schema(name: str):
    """Return a parsed AppPageSchema for the given page name.

    Reads pages/{name}.yaml from the platform pages directory and returns it
    as JSON. SchemaPage (frontend) calls this on mount to hydrate the page renderer.

    No auth required — page schemas are static declarative config, not user data.
    """
    import re
    # Sanitize: allow only alphanumeric, dash, underscore
    if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
        raise HTTPException(status_code=400, detail="Invalid page name")

    pages_dir = _resolve_pages_dir()
    page_path = pages_dir / f"{name}.yaml"

    if not page_path.exists():
        raise HTTPException(status_code=404, detail=f"Page '{name}' not found")

    try:
        content = page_path.read_text(encoding="utf-8")
        schema = yaml.safe_load(content)
        if not isinstance(schema, dict):
            raise ValueError("Page schema must be a YAML mapping")
        return JSONResponse(content=schema)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read page schema: {e}")


# ==============================================================================
# ROUTING TRANSITION ENDPOINTS
# ==============================================================================

def _load_pack_graph_or_404():
    """Load the global pack graph or raise 404 if not found."""
    from mozaiksai.core.workflow.pack.config import load_global_pack_graph
    pack = load_global_pack_graph()
    if pack is None:
        raise HTTPException(status_code=404, detail="No extension registry found")
    return pack


def _validate_context_for_workflow(workflow_id: str, merged_context: Dict[str, Any]) -> Dict[str, Any]:
    """Filter trigger context to keys declared in context_variables.yaml."""
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
            wf_logger.warning(
                "TRIGGER_CONTEXT_KEY_REJECTED",
                extra={"key": key, "workflow": workflow_id},
            )
            continue
        validated_context[key] = value
    return validated_context


async def _create_routed_chat_session(
    *,
    workflow_id: str,
    app_id: str,
    user_id: str,
    context_variables: Dict[str, Any],
    trigger_meta: Dict[str, Any],
    session_router: Optional[Any] = None,
    journey_id: Optional[str] = None,
) -> str:
    """Create a workflow chat session and bind it to SessionRouter state."""
    chat_id = str(uuid4())
    extra_fields: Dict[str, Any] = {"trigger_meta": trigger_meta}
    for key, value in context_variables.items():
        extra_fields[key] = value

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
        except Exception as bind_err:
            wf_logger.warning("Failed to bind SessionRouter chat session: %s", bind_err)

    return chat_id


def _extract_bearer_token(request: Request) -> Optional[str]:
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth_header:
        return None
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip() or None
    return auth_header.strip() or None


async def _execute_operation_action(
    *,
    operation_name: str,
    action_name: str,
    request: Request,
    principal: Optional[UserPrincipal],
    params: Dict[str, Any],
    context_overrides: Optional[Dict[str, Any]] = None,
) -> Any:
    """Dispatch an operation action through the registered OperationExecutor."""
    module_executor = executor_registry.operation_executor
    if module_executor is None:
        raise HTTPException(
            status_code=503,
            detail="Operation runtime is not available. Verify operations/*/operation.yaml handlers are loaded.",
        )

    context_overrides = context_overrides or {}
    request_app_id = request.query_params.get("app_id")
    request_tenant_id = request.query_params.get("tenant_id")
    request_correlation_id = request.query_params.get("correlation_id")
    app_id = (
        context_overrides.get("app_id")
        or request_app_id
        or (principal.app_id if principal else None)
        or "default"
    )
    tenant_id = (
        context_overrides.get("tenant_id")
        or request_tenant_id
        or (principal.tenant_id if principal else None)
    )
    correlation_id = (
        context_overrides.get("correlation_id")
        or request_correlation_id
        or str(uuid4())
    )
    auth_token = context_overrides.get("auth_token") or _extract_bearer_token(request)
    context_user_id = context_overrides.get("user_id")
    user_id = context_user_id or (principal.user_id if principal else None)

    operation_request = OperationRequest(
        operation=operation_name,
        action=action_name,
        params=params,
        app_id=str(app_id),
        user_id=str(user_id) if user_id else None,
        tenant_id=str(tenant_id) if tenant_id else None,
        auth_token=str(auth_token) if auth_token else None,
        correlation_id=str(correlation_id) if correlation_id else None,
    )

    result = await module_executor.execute(operation_request, context=None)
    if result.success:
        return result.data if result.data is not None else {}

    if result.error_code in {"OPERATION_NOT_FOUND", "ACTION_NOT_FOUND"}:
        status_code = 404
    elif result.error_code == "INVALID_PARAMS":
        status_code = 400
    else:
        status_code = 500

    raise HTTPException(
        status_code=status_code,
        detail={
            "error": result.error or "Operation action failed",
            "error_code": result.error_code or "EXECUTION_ERROR",
            "operation": operation_name,
            "action": action_name,
        },
    )


@app.get("/api/operations/{operation_name}/{action_name}")
async def execute_operation_action_get(
    operation_name: str,
    action_name: str,
    request: Request,
    principal: Optional[UserPrincipal] = Depends(optional_user),
):
    """Execute an operation action using query params as action params."""
    reserved_keys = {"app_id", "tenant_id", "correlation_id", "auth_token"}
    params = {
        key: value
        for key, value in request.query_params.items()
        if key not in reserved_keys
    }
    return await _execute_operation_action(
        operation_name=operation_name,
        action_name=action_name,
        request=request,
        principal=principal,
        params=params,
    )


@app.post("/api/operations/{operation_name}/{action_name}")
async def execute_operation_action_post(
    operation_name: str,
    action_name: str,
    request: Request,
    principal: Optional[UserPrincipal] = Depends(optional_user),
):
    """Execute an operation action with JSON payload.

    Supported payload shapes:
    - `{...action_params}`
    - `{"params": {...action_params}, "context": {"app_id": "...", "tenant_id": "..."}}`
    """
    body: Dict[str, Any] = {}
    if request.headers.get("content-type", "").lower().startswith("application/json"):
        try:
            parsed = await request.json()
            if isinstance(parsed, dict):
                body = parsed
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON payload.")

    if isinstance(body.get("params"), dict):
        params = dict(body.get("params") or {})
    else:
        params = dict(body)
    context_overrides = body.get("context") if isinstance(body.get("context"), dict) else {}
    params.pop("context", None)
    params.pop("params", None)

    return await _execute_operation_action(
        operation_name=operation_name,
        action_name=action_name,
        request=request,
        principal=principal,
        params=params,
        context_overrides=context_overrides,
    )


@app.get("/api/transitions/{transition_id}")
async def get_transition_by_id(transition_id: str):
    """Return a WorkflowTransition by id from the global extension registry.

    The shell fetches this when it needs to mount a transition component.
    No auth required — transition configs are static declarative config.
    """
    import re
    if not re.fullmatch(r'[A-Za-z0-9_-]+', transition_id):
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


def _resolve_scope_from_principal(
    principal: UserPrincipal,
    *,
    app_id: Optional[str] = None,
    user_id: Optional[str] = None,
    default_app_id: str = "default",
) -> Tuple[str, str]:
    """Resolve the effective app/user scope for HTTP workflow/session APIs.

    Production stays principal-first. Local auth-disabled flows may provide
    explicit caller scope in the request body so the shell can still create
    deterministic transition and workflow sessions.
    """
    resolved_user_id = _validate_user_id_against_principal(principal, body_user_id=user_id)

    provided_app_id = str(app_id or "").strip() or None
    principal_app_id = str(principal.app_id or "").strip() or None

    if principal_app_id and provided_app_id and provided_app_id != principal_app_id:
        raise HTTPException(
            status_code=403,
            detail="app_id in request body does not match authenticated app scope",
        )

    resolved_app_id = principal_app_id or provided_app_id or default_app_id
    if not resolved_app_id:
        raise HTTPException(status_code=400, detail="app_id is required")

    return resolved_app_id, resolved_user_id


@app.post("/api/transitions/resolve")
async def resolve_transition_route(
    body: TransitionResolveRequest,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Resolve a transition server-side and return the next transition or workflow session."""
    try:
        from mozaiksai.core.session import get_session_router

        session_router = get_session_router()
        app_id, user_id = _resolve_scope_from_principal(
            principal,
            app_id=body.app_id,
            user_id=body.user_id,
        )
        resolution = await session_router.resolve_transition(
            app_id=app_id,
            user_id=user_id,
            transition_id=body.transition_id,
            option_id=body.option_id,
            context_seed=body.context_variables or {},
        )
    except ValueError as route_err:
        raise HTTPException(status_code=400, detail=str(route_err))
    except Exception as route_err:
        wf_logger.error("Transition resolution failed: %s", route_err, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to resolve transition: {route_err}")

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
    validated_context = _validate_context_for_workflow(
        resolved_workflow_id,
        resolution.context_seed,
    )
    trigger_meta = {
        "trigger_source": "transition",
        "transition_id": body.transition_id,
        "option_id": body.option_id,
        "requested_workflow_id": route_decision.requested_workflow_id,
        "resolved_workflow_id": resolved_workflow_id,
        "rerouted_by_dependency": bool(route_decision.rerouted_by_dependency),
    }
    try:
        chat_id = await _create_routed_chat_session(
            workflow_id=resolved_workflow_id,
            app_id=app_id,
            user_id=user_id,
            context_variables=validated_context,
            trigger_meta=trigger_meta,
            session_router=session_router,
        )
    except Exception as session_err:
        raise HTTPException(status_code=500, detail=f"Failed to create session: {session_err}")

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
    """Return the canonical SessionRouter state for the authenticated user/app scope."""
    from mozaiksai.core.session import get_session_router

    snapshot = await get_session_router().get_session_snapshot(
        app_id=principal.app_id,
        user_id=principal.user_id,
    )
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
    """Mark the current session as awaiting explicit human approval."""
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
    """Resolve an explicit human approval checkpoint on the current session."""
    from mozaiksai.core.session import get_session_router

    snapshot = await get_session_router().resolve_approval(
        app_id=principal.app_id,
        user_id=principal.user_id,
        approval_id=body.approval_id,
        approved=body.approved,
    )
    return {"session_state": snapshot}


# ==============================================================================
# UNIFIED WORKFLOW TRIGGER ENDPOINT
# ==============================================================================

class WorkflowTriggerRequest(BaseModel):
    """Unified trigger intake for all non-chat trigger sources.

    trigger_source: one of transition | action | event | schedule | refinement | chat
    workflow_id:    workflow to start — for refinement triggers, omit to let the
                    router resolve the correct re-entry point automatically.
    context_variables: keys merged into workflow context at start (validated against declared keys)
    action_id:      for action triggers — which page action fired it
    change_class:   for refinement — patch | design | feature | core
    artifact_version_id: for refinement — which artifact is being refined
    artifact_kind:  for refinement routing — app_bundle | workflow_bundle | design_docs | concept
    raw_user_request: for refinement — natural-language change description (stored on ChangeRequest)
    """
    workflow_id: Optional[str] = None
    trigger_source: str = "chat"
    context_variables: Dict[str, Any] = Field(default_factory=dict)
    action_id: Optional[str] = None
    change_class: Optional[str] = None
    artifact_version_id: Optional[str] = None
    artifact_kind: Optional[str] = None
    raw_user_request: Optional[str] = None
    app_id: Optional[str] = None
    user_id: Optional[str] = None


@app.post("/api/workflows/trigger")
async def trigger_workflow(
    body: WorkflowTriggerRequest,
    request: Request,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Unified trigger endpoint for workflow starts from non-chat sources.

    SessionRouter owns trigger routing:
      - refinement re-entry workflow resolution
      - hard dependency enforcement and prerequisite reroute
      - session-level lifecycle state persistence
    """
    app_id, user_id = _resolve_scope_from_principal(
        principal,
        app_id=body.app_id,
        user_id=body.user_id,
    )

    valid_change_classes = {"patch", "design", "feature", "core"}
    if body.change_class and body.change_class not in valid_change_classes:
        raise HTTPException(status_code=400, detail=f"Invalid change_class. Must be one of: {valid_change_classes}")

    try:
        from mozaiksai.core.session import TriggerInput, get_session_router

        session_router = get_session_router()
        routing_decision = await session_router.route_trigger(
            TriggerInput(
                app_id=app_id,
                user_id=user_id,
                trigger_source=body.trigger_source,
                workflow_id=body.workflow_id,
                change_class=body.change_class,
                artifact_kind=body.artifact_kind,
                artifact_version_id=body.artifact_version_id,
                raw_user_request=body.raw_user_request,
                context_variables=body.context_variables or {},
            )
        )
    except ValueError as route_err:
        raise HTTPException(status_code=400, detail=str(route_err))
    except HTTPException:
        raise
    except Exception as route_err:
        wf_logger.error("SessionRouter routing failed: %s", route_err, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to route workflow trigger: {route_err}")

    resolved_workflow_id = routing_decision.workflow_id
    context_from_router: Dict[str, Any] = dict(routing_decision.context_seed)

    # ── Context validation ────────────────────────────────────────────────────
    # Merge router seed + caller context; validate against declared keys.
    merged_context = {**context_from_router, **body.context_variables}
    validated_context = _validate_context_for_workflow(resolved_workflow_id, merged_context)

    # ── Persist ChangeRequest for refinement observability ───────────────────
    if body.trigger_source == "refinement" and body.change_class:
        try:
            _coll = await persistence_manager._coll()
            change_request_doc = {
                "kind": "change_request",
                "app_id": app_id,
                "user_id": user_id,
                "artifact_kind": body.artifact_kind or "app_bundle",
                "artifact_version_id": body.artifact_version_id,
                "raw_user_request": body.raw_user_request,
                "classification": body.change_class,
                "router_decision": {
                    "workflow_id": resolved_workflow_id,
                    "explanation": routing_decision.explanation,
                    "is_full_restart": routing_decision.is_full_restart,
                    "rerouted_by_dependency": routing_decision.rerouted_by_dependency,
                },
                "created_at": datetime.now(UTC).isoformat(),
            }
            await _coll.insert_one(change_request_doc)
        except Exception as persist_err:
            wf_logger.warning("Failed to persist ChangeRequest: %s", persist_err)

    # ── Create chat session ───────────────────────────────────────────────────
    try:
        trigger_meta: Dict[str, Any] = {
            "trigger_source": body.trigger_source,
            **({"action_id": body.action_id} if body.action_id else {}),
            **({"change_class": body.change_class} if body.change_class else {}),
            **({"artifact_version_id": body.artifact_version_id} if body.artifact_version_id else {}),
            **({"artifact_kind": body.artifact_kind} if body.artifact_kind else {}),
            "requested_workflow_id": routing_decision.requested_workflow_id,
            "resolved_workflow_id": resolved_workflow_id,
            "rerouted_by_dependency": bool(routing_decision.rerouted_by_dependency),
        }
        chat_id = await _create_routed_chat_session(
            workflow_id=resolved_workflow_id,
            app_id=app_id,
            user_id=user_id,
            context_variables=validated_context,
            trigger_meta=trigger_meta,
            session_router=session_router,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create session: {e}")

    wf_logger.info(
        "WORKFLOW_TRIGGERED",
        extra={
            "workflow_id": resolved_workflow_id,
            "requested_workflow_id": routing_decision.requested_workflow_id,
            "trigger_source": body.trigger_source,
            "change_class": body.change_class,
            "routing_explanation": routing_decision.explanation,
            "rerouted_by_dependency": bool(routing_decision.rerouted_by_dependency),
            "chat_id": chat_id,
            "app_id": app_id,
            "user_id": user_id,
        },
    )

    return {
        "chat_id": chat_id,
        "workflow_id": resolved_workflow_id,
        "requested_workflow_id": routing_decision.requested_workflow_id,
        "websocket_url": f"/ws/{resolved_workflow_id}/{app_id}/{chat_id}/{user_id}",
        "trigger_source": body.trigger_source,
        "routing_explanation": routing_decision.explanation,
        "rerouted_by_dependency": bool(routing_decision.rerouted_by_dependency),
    }


@app.get("/api/workflows")
async def get_workflows(
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Get all workflows in SDK-friendly format.

    Returns a list of workflow info objects suitable for SDK consumption.
    For the raw config dict format, use /api/workflows/config instead.
    """
    try:
        from mozaiksai.core.workflow.workflow_manager import workflow_manager

        workflow_names = sorted(workflow_manager.get_all_workflow_names())
        # Platform hook: reorder by workflow sequence when pack config present.
        ordered_names = get_platform_hooks().call_workflow_ordering(workflow_names)

        workflows_list = []
        for workflow_name in ordered_names:
            config = workflow_manager.get_config(workflow_name)
            # Build SDK-friendly workflow info
            workflows_list.append({
                "name": workflow_name,
                "display_name": config.get("display_name") or config.get("name") or workflow_name,
                "initial_agent": config.get("initial_agent"),
                "visual_agents": config.get("visual_agents") or [],
                "status": "ready",
            })

        get_workflow_logger("shared_app").info(
            "WORKFLOWS_REQUESTED: Workflows requested (SDK format)",
            workflow_count=len(workflows_list),
        )
        return {"workflows": workflows_list}

    except Exception as e:
        logger.error(f"❌ Failed to get workflows: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve workflows")

@app.get("/api/workflows/config")
async def get_workflow_configs(
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Get all workflow configurations for frontend"""
    try:
        from mozaiksai.core.workflow.workflow_manager import workflow_manager

        workflow_names = sorted(workflow_manager.get_all_workflow_names())
        ordered_names = get_platform_hooks().call_workflow_ordering(workflow_names)

        configs: dict = {}
        for workflow_name in ordered_names:
            configs[workflow_name] = workflow_manager.get_config(workflow_name)

        get_workflow_logger("shared_app").info(
            "WORKFLOW_CONFIGS_REQUESTED: Workflow configurations requested by frontend",
            workflow_count=len(configs),
        )
        return configs

    except Exception as e:
        logger.error(f"? Failed to get workflow configs: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve workflow configurations")

# NOTE: /api/workflows/{app_id}/available is mounted by mozaiksai.platform
# (pack dependency-aware workflow availability) when RUNTIME_PLATFORM_EXTENSIONS is set.

@app.post("/chat/{app_id}/{chat_id}/component_action")
async def handle_component_action(
    request: Request,
    app_id: str,
    chat_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Endpoint to receive component actions for AG2 ContextVariables (WebSocket support)."""
    if not simple_transport:
        raise HTTPException(status_code=503, detail="Transport service is not available.")

    try:
        # Ensure the chat exists and is owned by the authenticated principal.
        if principal.user_id != "anonymous":
            try:
                coll = await _chat_coll()
                owned = await coll.find_one(
                    {"_id": chat_id, "user_id": principal.user_id, **build_app_scope_filter(app_id)},
                    {"_id": 1},
                )
                if not owned:
                    raise HTTPException(status_code=404, detail="Chat not found")
            except HTTPException:
                raise
            except Exception as owner_err:
                raise HTTPException(status_code=500, detail=f"Failed to validate chat ownership: {owner_err}")

        data = await request.json()
        component_id = data.get("component_id")
        action_type = data.get("action_type")
        action_data = data.get("action_data", {})
        
        get_workflow_logger("shared_app").info(
            "COMPONENT_ACTION_ENDPOINT_CALLED: Component action endpoint called",
            app_id=app_id,
            chat_id=chat_id,
            component_id=component_id,
            action_type=action_type
        )
        
        if not component_id or not action_type:
            raise HTTPException(status_code=400, detail="'component_id' and 'action_type' fields are required.")

        logger.info(f"🧩 Component action via HTTP: {component_id} -> {action_type}")

        try:
            result = await simple_transport.process_component_action(
                chat_id=chat_id,
                app_id=app_id,
                component_id=component_id,
                action_type=action_type,
                action_data=action_data or {}
            )
            get_workflow_logger("shared_app").info(
                "COMPONENT_ACTION_PROCESSED: Component action processed successfully",
                chat_id=chat_id,
                component_id=component_id,
                action_type=action_type,
                applied_keys=list((result.get('applied') or {}).keys())
            )
            return {
                "status": "success",
                "message": "Component action applied",
                "applied": result.get('applied'),
                "timestamp": datetime.now(UTC).isoformat()
            }
        except Exception as action_error:
            logger.error(f"❌ Component action failed: {action_error}")
            raise HTTPException(status_code=500, detail=f"Component action failed: {action_error}")

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")
    except Exception as e:
        logger.error(f"? Error handling component action for chat {chat_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to process component action: {e}")

@app.post("/api/ui-tool/submit")
async def submit_ui_tool_response(
    request: Request,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """
    API endpoint for submitting UI tool responses.
    
    This endpoint is called by the frontend when a user interacts with UI tool components
    (like AgentAPIKeyInput or FileDownloadCenter) and submits responses.
    """
    if not simple_transport:
        raise HTTPException(status_code=503, detail="Transport service is not available.")

    try:
        data = await request.json()
        event_id = data.get("event_id")
        response_data = data.get("response_data")
        
        if not event_id:
            raise HTTPException(status_code=400, detail="'event_id' field is required.")
        if not response_data:
            raise HTTPException(status_code=400, detail="'response_data' field is required.")
        
        # Submit the UI tool response to the transport layer
        success = await simple_transport.submit_ui_tool_response(event_id, response_data)
        
        if success:
            get_workflow_logger("shared_app").info(
                "UI_TOOL_RESPONSE_SUBMITTED: UI tool response submitted",
                event_id=event_id,
                response_status=response_data.get("status", "unknown"),
                ui_tool_id=response_data.get("data", {}).get("ui_tool_id", "unknown")
            )
            return {"status": "success", "message": "UI tool response submitted successfully"}
        else:
            raise HTTPException(status_code=404, detail="UI tool event not found or already completed")

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")
    except Exception as e:
        logger.error(f"? Error submitting UI tool response: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to submit UI tool response: {e}")

@app.get("/api/download/workflow-file")
async def download_workflow_file(
    file_path: str,
    principal: Optional[UserPrincipal] = Depends(optional_user),
):
    """
    Download a single workflow file.
    
    Args:
        file_path: Absolute path to the file to download
    
    Returns:
        File content with proper download headers
    """
    from fastapi.responses import FileResponse
    import mimetypes
    
    try:
        if not file_path:
            raise HTTPException(status_code=400, detail="file_path query parameter is required")

        file = Path(file_path)
        workflows_base = Path(__file__).parent / "workflows"

        if not file.is_absolute():
            file = workflows_base / file
        
        try:
            file_resolved = file.resolve()
            workflows_base_resolved = workflows_base.resolve()
            if not str(file_resolved).startswith(str(workflows_base_resolved)):
                raise HTTPException(status_code=403, detail="Access denied: File outside workflow directories")
        except Exception:
            raise HTTPException(status_code=403, detail="Invalid file path")
        
        if not file.exists():
            raise HTTPException(status_code=404, detail="File not found")
        
        if not file.is_file():
            raise HTTPException(status_code=400, detail="Path is not a file")
        
        # Determine proper MIME type based on file extension
        mime_type, _ = mimetypes.guess_type(file.name)
        if not mime_type:
            # Default MIME types for common workflow files
            if file.suffix == '.json':
                mime_type = 'application/json'
            elif file.suffix == '.env':
                mime_type = 'text/plain'
            elif file.suffix == '.py':
                mime_type = 'text/x-python'
            elif file.suffix == '.js':
                mime_type = 'text/javascript'
            elif file.suffix == '.jsx':
                mime_type = 'text/javascript'
            else:
                mime_type = 'application/octet-stream'
        
        # Return file with download headers
        return FileResponse(
            path=str(file_resolved),
            filename=file.name,
            media_type=mime_type,
            headers={
                "Content-Disposition": f'attachment; filename="{file.name}"',
                "X-Content-Type-Options": "nosniff"
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ File download failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to download file: {e}")


# NOTE: /api/apps/{app_id}/builds/{build_id}/export is mounted by mozaiksai.platform
# (AgentGenerator build bundle download) when RUNTIME_PLATFORM_EXTENSIONS is set.

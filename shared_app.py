# ==============================================================================
# FILE: shared_app.py
# DESCRIPTION: FastAPI app - workflow agnostic, tools handled by workflows
# ==============================================================================
import logging
import os
import sys
from typing import Optional, Any, List, Dict, Tuple
from datetime import datetime, timedelta, UTC
from pathlib import Path
# Ensure project root is on Python path for workflow imports
sys.path.insert(0, str(Path(__file__).parent))
import json
import asyncio
import importlib
from fastapi import FastAPI, HTTPException, Request, WebSocket, UploadFile, File, Form, Depends
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware
from bson.objectid import ObjectId
from uuid import uuid4
import autogen
from pydantic import BaseModel, Field
from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.transport.simple_transport import SimpleTransport
from mozaiksai.core.workflow.workflow_manager import workflow_status_summary, get_workflow_transport, get_workflow_tools
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id
from mozaiksai.core.artifacts.attachments import handle_chat_upload
from mozaiksai.core.runtime.extensions import mount_declared_routers, start_declared_services, stop_services
from mozaiksai.core.runtime.platform_hooks import get_platform_hooks

# JWT Authentication dependencies
from mozaiksai.core.auth import (
    UserPrincipal,
    ServicePrincipal,
    require_user_scope,
    require_any_auth,
    require_internal,
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
    require_execution_token,
    validate_user_id_against_principal as _validate_user_id_against_principal,
)

# Initialize persistence manager (handles lean chat session storage internally)
persistence_manager = AG2PersistenceManager()
_runtime_services = []



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
from mozaiksai.core.workflow.orchestration_patterns import get_run_registry_summary

# FastAPI app
app = FastAPI(
    title="MozaiksAI Runtime",
    description="Production-ready AG2 runtime with workflow-specific tools",
    version="5.0.0",
)

# Expose shared state on app for platform extension routers.
app.state.persistence_manager = persistence_manager

# Allow CORS for all origins (e.g., test_client.html local file)
_react_dev_origin = os.getenv("REACT_DEV_ORIGIN")
if _react_dev_origin and _react_dev_origin.strip():
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[_react_dev_origin.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
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
# When MozaiksCore is the gateway, it attaches x-app-id and x-user-id headers
# derived from the authenticated context. This middleware validates that those
# headers (if present) match path parameters for defense-in-depth.

_ENFORCE_PRINCIPAL_HEADERS = os.getenv("ENFORCE_PRINCIPAL_HEADERS", "false").lower() in ("true", "1", "yes")

@app.middleware("http")
async def principal_header_middleware(request: Request, call_next):
    """Validate x-app-id / x-user-id headers match path params when present."""
    if not _ENFORCE_PRINCIPAL_HEADERS:
        return await call_next(request)
    
    # Extract headers (MozaiksCore gateway sets these)
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


mongo_client = None  # delay until startup so logging is definitely initialized
simple_transport: Optional[SimpleTransport] = None


@app.get("/health/active-runs")
async def health_active_runs(
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return summary of active runs (in-memory registry)."""
    try:
        return get_run_registry_summary()
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
        "app_id": app_id,
        "user_id": user_id,
        "attachment": res.attachment,
    }


# # ------------------------------------------------------------------------------
# # SERVICE REGISTRY (backend-agnostic routing for artifact modules)
# # Map logical service names -> base URLs. Change via env vars per environment.
# # ------------------------------------------------------------------------------
# import httpx  # HTTP client for proxying requests
# SERVICE_REGISTRY = {
#     # Most of your app is .NET — point this to your .NET gateway/base URL.
#     # e.g., "http://localhost:5000" or "https://api.mycorp.internal"
#     "dotnet": os.getenv("DOTNET_BASE", "http://localhost:5000"),

#     # Your FastAPI (this app) can also be targeted by name if you want:
#     "fastapi": os.getenv("FASTAPI_BASE", "http://localhost:8000"),
#     # Add more services as needed:
#     # "java": os.getenv("JAVA_BASE", "http://java-service:8080"),
# }


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
            "status": "healthy",
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

def _maybe_enforce_principal_headers(*, app_id: str, user_id: Optional[str], headers: Any) -> None:
    """Defense-in-depth: if a gateway supplies principal headers, enforce they match path/body values.

    This keeps the runtime compatible with local/dev usage (headers absent) while allowing
    MozaiksCore to pass trusted IDs derived from auth context and have the runtime verify them.
    """
    try:
        expected_app_id = coalesce_app_id(app_id=app_id)
        if not expected_app_id:
            return

        hdr_app = None
        hdr_user = None
        try:
            hdr_app = headers.get("x-app-id") or headers.get("x-mozaiks-app-id")
            hdr_user = headers.get("x-user-id") or headers.get("x-mozaiks-user-id")
        except Exception:
            hdr_app = None
            hdr_user = None

        if hdr_app:
            resolved_hdr_app = coalesce_app_id(app_id=hdr_app)
            if resolved_hdr_app and resolved_hdr_app != expected_app_id:
                raise HTTPException(status_code=403, detail="app_id mismatch")

        if hdr_user and user_id:
            resolved_hdr_user = str(hdr_user).strip()
            if resolved_hdr_user and resolved_hdr_user != str(user_id).strip():
                raise HTTPException(status_code=403, detail="user_id mismatch")
    except HTTPException:
        raise
    except Exception:
        # Never hard-fail on header parsing; this is an optional check.
        return


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
            
            # Validate and get canonical user_id from JWT
            user_id = _validate_user_id_against_principal(principal, body_user_id=body_user_id)

            # Platform hook: prerequisite gate check (no-op when no platform registered).
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


# NOTE: /api/general_chats/* routes are mounted by mozaiksai.platform
# (Ask Mode / dual-mode UX) when RUNTIME_PLATFORM_EXTENSIONS is set.
# The stubs below allow the open-source frontend to degrade gracefully.


@app.get("/api/general_chats/list/{app_id}/{user_id}")
async def list_general_chats_stub(
    app_id: str,
    user_id: str,
    limit: int = 50,
):
    """Stub: Ask-mode general chat sessions are a platform feature. Returns empty list."""
    return {"sessions": []}


@app.get("/api/general_chats/transcript/{app_id}/{general_chat_id}")
async def general_chat_transcript_stub(
    app_id: str,
    general_chat_id: str,
):
    """Stub: Ask-mode transcripts are a platform feature. Returns not-found so the frontend falls back."""
    raise HTTPException(status_code=404, detail="General chat not available in open-source runtime")


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
            from mozaiksai.core.workflow.pack.config import workflow_has_journeys

            has_children = workflow_has_journeys(workflow_name)
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

    # Auto resume vs new session selection
    active_chat_id = chat_id
    try:
        coll = await _chat_coll()
        latest = await coll.find({
            "workflow_name": workflow_name,
            "user_id": user_id,
            **build_app_scope_filter(app_id),
        }).sort("created_at", -1).limit(1).to_list(length=1)
        if latest:
            latest_doc = latest[0]
            latest_status = int(latest_doc.get("status", -1))
            latest_id = latest_doc.get("_id")
            if latest_status == 0:
                active_chat_id = latest_id
                wf_logger.info("WS_AUTO_RESUME", extra={"chat_id": active_chat_id, "incoming_chat_id": chat_id})
            else:
                # Ensure provided chat id exists (create minimal doc if missing)
                if not await coll.find_one({"_id": chat_id, "user_id": user_id, **build_app_scope_filter(app_id)}):
                    await persistence_manager.create_chat_session(chat_id, app_id, workflow_name, user_id)
                    wf_logger.info("WS_NEW_SESSION_CREATED", extra={"chat_id": chat_id})
        else:
            if not await coll.find_one({"_id": chat_id, "user_id": user_id, **build_app_scope_filter(app_id)}):
                await persistence_manager.create_chat_session(chat_id, app_id, workflow_name, user_id)
                wf_logger.info("WS_FIRST_SESSION_CREATED", extra={"chat_id": chat_id})
    except Exception as pre_err:
        wf_logger.error(f"WS_SESSION_DETERMINATION_FAILED: {pre_err}")

    # Auto-start AgentDriven workflows once the socket is accepted and registered
    async def _auto_start_if_needed():
        try:
            from mozaiksai.core.workflow.workflow_manager import workflow_manager
            cfg = workflow_manager.get_config(workflow_name)
            if cfg.get("startup_mode", "AgentDriven") == "AgentDriven":
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
            from mozaiksai.core.workflow.pack.config import workflow_has_journeys

            has_children = workflow_has_journeys(workflow_name)
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
                chat_exists = True
                wf_logger.info("WS_BACKFILL_SESSION_CREATED", extra={"chat_id": active_chat_id})
            except Exception as ce:
                wf_logger.debug(f"Failed to backfill chat session for {active_chat_id}: {ce}")

        try:
            cache_seed = await persistence_manager.get_or_assign_cache_seed(active_chat_id, app_id)
        except Exception as ce:
            cache_seed = None
            wf_logger.debug(f"cache_seed retrieval failed for WS {active_chat_id}: {ce}")

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
            }, active_chat_id)
            wf_logger.info(
                "CHAT_META_EMITTED",
                extra={
                    "chat_id": active_chat_id,
                    "workflow_name": workflow_name,
                    "app_id": app_id,
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


@app.get("/api/workflows")
async def get_workflows(
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Get all workflows for frontend (alias for /api/workflows/config)"""
    try:
        from mozaiksai.core.workflow.workflow_manager import workflow_manager

        workflow_names = sorted(workflow_manager.get_all_workflow_names())
        # Platform hook: reorder by journey step sequence when pack config present.
        ordered_names = get_platform_hooks().call_workflow_ordering(workflow_names)

        configs: dict = {}
        for workflow_name in ordered_names:
            configs[workflow_name] = workflow_manager.get_config(workflow_name)

        get_workflow_logger("shared_app").info(
            "WORKFLOWS_REQUESTED: Workflows requested by frontend",
            workflow_count=len(configs),
        )
        return configs

    except Exception as e:
        logger.error(f"? Failed to get workflows: {e}")
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
# (pack-gated workflow availability) when RUNTIME_PLATFORM_EXTENSIONS is set.

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
    service: ServicePrincipal = Depends(require_internal),
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

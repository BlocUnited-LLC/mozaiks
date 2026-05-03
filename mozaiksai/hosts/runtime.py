# ==============================================================================
# MODULE: mozaiksai.hosts.runtime
# DESCRIPTION: Minimal FastAPI runtime host for workflows, agents, transport,
#              events, persistence, and auth.
# ==============================================================================
from __future__ import annotations

from contextlib import asynccontextmanager
import hmac
import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any, AsyncIterator, Callable, Dict, Optional
from uuid import uuid4

import autogen
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware

from mozaiksai.core.transport.rate_limit import RateLimitMiddleware

from logs.logging_config import (
    get_workflow_logger,
    setup_development_logging,
    setup_production_logging,
)
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
from mozaiksai.core.core_config import close_mongo_client, get_mongo_client
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher
from mozaiksai.core.multitenant import build_app_scope_filter
from mozaiksai.core.observability.performance_manager import get_performance_manager
from mozaiksai.core.transport.simple_transport import SimpleTransport
from mozaiksai.core.workflow.workflow_manager import (
    get_workflow_tools,
    get_workflow_transport,
    workflow_status_summary,
)
from mozaiksai.version import __version__


env = os.getenv("ENVIRONMENT", "development").lower()

# Max characters allowed in a single user message (~2 000 GPT-4 tokens at the default).
# Set CHAT_MESSAGE_MAX_CHARS=0 to disable.
_MESSAGE_MAX_CHARS = int(os.getenv("CHAT_MESSAGE_MAX_CHARS", "8000"))
if env == "production":
    setup_production_logging()
else:
    setup_development_logging()

logger = logging.getLogger(__name__)
wf_logger = get_workflow_logger("runtime_app")
performance_logger = get_workflow_logger("performance.runtime_app")

persistence_manager = AG2PersistenceManager()
event_dispatcher = get_event_dispatcher()

mongo_client = None
simple_transport: Optional[SimpleTransport] = None


def _patch_autogen_file_logger() -> None:
    """Patch AG2 file logging so non-serializable values cannot break runtime logs."""
    try:
        from autogen.logger import file_logger as _file_logger
        from autogen.logger.logger_utils import get_current_ts as _logger_ts
    except Exception as patch_err:  # pragma: no cover - defensive safeguard
        wf_logger.debug("Skipped AG2 file logger patch: %s", patch_err)
        return

    file_logger_cls: Any = _file_logger.FileLogger
    if getattr(file_logger_cls, "_mozaiks_safe_json", False):
        return

    import json as _json
    import threading as _threading

    safe_serialize = _file_logger.safe_serialize

    def _patched_log_new_wrapper(self, wrapper, init_args=None):
        try:
            self.logger.info(
                _json.dumps(
                    {
                        "wrapper_id": id(wrapper),
                        "session_id": self.session_id,
                        "json_state": safe_serialize(init_args or {}),
                        "timestamp": _logger_ts(),
                        "thread_id": _threading.get_ident(),
                    }
                )
            )
        except Exception as exc:  # pragma: no cover - logging fallback
            self.logger.error("[file_logger] Failed to log wrapper event %s", exc)

    def _patched_log_new_client(self, client, wrapper, init_args):
        thread_id = _threading.get_ident()
        try:
            self.logger.info(
                _json.dumps(
                    {
                        "client_id": id(client),
                        "wrapper_id": id(wrapper),
                        "session_id": self.session_id,
                        "class": type(client).__name__,
                        "json_state": safe_serialize(init_args or {}),
                        "timestamp": _logger_ts(),
                        "thread_id": thread_id,
                    }
                )
            )
        except Exception as exc:  # pragma: no cover - logging fallback
            self.logger.error("[file_logger] Failed to log client event %s", exc)

    file_logger_cls.log_new_wrapper = _patched_log_new_wrapper  # type: ignore[attr-defined]
    file_logger_cls.log_new_client = _patched_log_new_client  # type: ignore[attr-defined]
    setattr(file_logger_cls, "_mozaiks_safe_json", True)


_patch_autogen_file_logger()
logging.getLogger("autogen").setLevel(logging.DEBUG)
wf_logger.info("autogen version: %s", getattr(autogen, "__version__", "unknown"))

app = FastAPI(
    title="Mozaiks Runtime Host",
    description="Generic workflow, agent, transport, event, persistence, and auth runtime.",
    version=__version__,
)
app.state.persistence_manager = persistence_manager


def register_app_lifespan(
    target_app: FastAPI,
    lifespan_factory: Callable[[FastAPI], AsyncIterator[None]],
) -> None:
    existing_lifespan = target_app.router.lifespan_context

    @asynccontextmanager
    async def _composed_lifespan(app_instance: FastAPI):
        async with existing_lifespan(app_instance):
            async with lifespan_factory(app_instance):
                yield

    target_app.router.lifespan_context = _composed_lifespan


def _parse_csv_origins(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    return [origin.strip() for origin in raw.split(",") if origin and origin.strip()]


def _build_cors_origins() -> list[str]:
    origins: list[str] = []
    origins.extend(_parse_csv_origins(os.getenv("FRONTEND_URL")))
    origins.extend(_parse_csv_origins(os.getenv("REACT_DEV_ORIGIN")))
    origins.extend(_parse_csv_origins(os.getenv("CORS_ORIGINS")))
    origins.extend(_parse_csv_origins(os.getenv("ADDITIONAL_CORS_ORIGINS")))
    return list(dict.fromkeys(origins))


_cors_origins = _build_cors_origins()
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Rate limiting — runs before CORS (outermost layer).
# Controlled entirely by RATE_LIMIT_* env vars; disabled when RATE_LIMIT_ENABLED=false.
app.add_middleware(RateLimitMiddleware)


async def _chat_coll():
    """Return the lean chat session collection."""
    return await persistence_manager._coll()


def _validate_internal_api_key(request: Request) -> None:
    expected = os.getenv("INTERNAL_API_KEY", "").strip()
    if not expected:
        return

    provided = request.headers.get("x-internal-api-key", "").strip()
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid internal API key")


_ENFORCE_PRINCIPAL_HEADERS = os.getenv("ENFORCE_PRINCIPAL_HEADERS", "false").lower() in {
    "true",
    "1",
    "yes",
}


@app.middleware("http")
async def principal_header_middleware(request: Request, call_next):
    """Validate gateway-provided principal headers against runtime route scope."""
    if not _ENFORCE_PRINCIPAL_HEADERS:
        return await call_next(request)

    header_app_id = request.headers.get("x-app-id") or request.headers.get("x-mozaiks-app-id")
    header_user_id = request.headers.get("x-user-id") or request.headers.get("x-mozaiks-user-id")
    if not header_app_id and not header_user_id:
        return await call_next(request)

    path = request.url.path
    app_match = re.search(r"/api/chats/([^/]+)/", path) or re.search(r"/ws/[^/]+/([^/]+)/", path)
    user_match = re.search(r"/ws/[^/]+/[^/]+/[^/]+/([^/]+)", path)

    path_app_id = app_match.group(1) if app_match else None
    path_user_id = user_match.group(1) if user_match else None

    if header_app_id and path_app_id and str(header_app_id).strip() != str(path_app_id).strip():
        return JSONResponse(
            status_code=403,
            content={"detail": "x-app-id header does not match path app_id"},
        )

    if header_user_id and path_user_id and str(header_user_id).strip() != str(path_user_id).strip():
        return JSONResponse(
            status_code=403,
            content={"detail": "x-user-id header does not match path user_id"},
        )

    return await call_next(request)


async def _runtime_startup() -> None:
    """Initialize runtime-only services."""
    global mongo_client, simple_transport

    startup_start = datetime.now(UTC)
    wf_logger.info("RUNTIME_STARTUP: starting runtime host in %s mode", env)

    perf_mgr = await get_performance_manager()
    await perf_mgr.initialize()

    simple_transport = await SimpleTransport.get_instance()
    app.state.simple_transport = simple_transport

    if mongo_client is None:
        mongo_client = get_mongo_client()
    await mongo_client.admin.command("ping")

    status = workflow_status_summary()
    startup_time_ms = (datetime.now(UTC) - startup_start).total_seconds() * 1000
    performance_logger.info(
        "runtime_startup_duration",
        extra={
            "metric_name": "runtime_startup_duration",
            "value": float(startup_time_ms),
            "unit": "ms",
            "workflows_count": status.get("total_workflows", 0),
            "tools_count": status.get("total_tools", 0),
        },
    )

    await event_dispatcher.emit_business_event(
        log_event_type="RUNTIME_STARTUP_COMPLETED",
        description="Runtime host startup completed",
        context={
            "environment": env,
            "startup_time_ms": startup_time_ms,
            "workflows_registered": status.get("total_workflows", 0),
            "tools_available": status.get("total_tools", 0),
        },
    )


async def _runtime_shutdown() -> None:
    """Clean up runtime-only services."""
    global mongo_client, simple_transport

    simple_transport = None
    if mongo_client:
        close_mongo_client()
        mongo_client = None


@asynccontextmanager
async def runtime_lifespan(_: FastAPI):
    await _runtime_startup()
    try:
        yield
    finally:
        await _runtime_shutdown()


register_app_lifespan(app, runtime_lifespan)


@app.get("/api/health/live")
async def health_liveness():
    """Liveness probe — confirms the process is running. Never checks dependencies."""
    return {"status": "alive", "timestamp": datetime.now(UTC).isoformat()}


@app.get("/api/health/ready")
async def health_readiness():
    """Readiness probe — checks all critical dependencies before accepting traffic."""
    checks: dict[str, str] = {}
    degraded = False

    # MongoDB
    if mongo_client is None:
        checks["mongodb"] = "not_initialized"
        degraded = True
    else:
        try:
            await mongo_client.admin.command("ping", serverSelectionTimeoutMS=2000)
            checks["mongodb"] = "ok"
        except Exception as exc:
            checks["mongodb"] = f"error: {exc}"
            degraded = True

    # Transport
    if simple_transport is None:
        checks["transport"] = "not_initialized"
        degraded = True
    else:
        checks["transport"] = "ok"

    status_code = 503 if degraded else 200
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "degraded" if degraded else "ready",
            "timestamp": datetime.now(UTC).isoformat(),
            "checks": checks,
        },
    )


@app.get("/api/health")
async def health_check():
    """Full health check — includes dependency status and workflow summary."""
    if mongo_client is None:
        raise HTTPException(status_code=503, detail="MongoDB client is not initialized")

    try:
        await mongo_client.admin.command("ping", serverSelectionTimeoutMS=2000)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"MongoDB unreachable: {exc}") from exc

    status = workflow_status_summary()

    return {
        "status": "healthy",
        "timestamp": datetime.now(UTC).isoformat(),
        "workflows": status,
        "transport": "initialized" if simple_transport else "not_initialized",
    }


@app.get("/api/events/metrics")
async def get_event_metrics(
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return runtime event dispatcher metrics."""
    _ = principal
    return event_dispatcher.get_metrics()


@app.get("/health/active-runs")
async def health_active_runs(
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return the current in-memory background workflow task snapshot."""
    _ = principal
    try:
        transport = await SimpleTransport.get_instance()
        return transport.get_background_run_summary()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/metrics/perf/aggregate")
async def metrics_perf_aggregate(
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return aggregate in-memory performance counters."""
    _ = principal
    try:
        perf_mgr = await get_performance_manager()
        return await perf_mgr.aggregate()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to collect aggregate metrics: {exc}") from exc


@app.get("/metrics/perf/chats")
async def metrics_perf_chats(
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return per-chat in-memory performance snapshots."""
    _ = principal
    try:
        perf_mgr = await get_performance_manager()
        return await perf_mgr.snapshot_all()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to collect chat metrics: {exc}") from exc


@app.get("/metrics/perf/chats/{chat_id}")
async def metrics_perf_chat(
    chat_id: str,
    principal: UserPrincipal = Depends(require_any_auth),
):
    _ = principal
    try:
        perf_mgr = await get_performance_manager()
        snap = await perf_mgr.snapshot_chat(chat_id)
        if not snap:
            raise HTTPException(status_code=404, detail="Chat not tracked")
        return snap
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to collect chat metric: {exc}") from exc


def _validate_context_for_workflow(workflow_name: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Filter caller-provided context to declared workflow context variables."""
    if not context:
        return {}

    try:
        from mozaiksai.core.workflow.workflow_manager import workflow_manager

        wf_cfg = workflow_manager.get_config(workflow_name) or {}
        declared_keys = set((wf_cfg.get("context_variables") or {}).get("definitions", {}).keys())
    except Exception:
        declared_keys = set()

    validated: Dict[str, Any] = {}
    for key, value in context.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if declared_keys and key not in declared_keys:
            wf_logger.warning(
                "RUNTIME_CONTEXT_KEY_REJECTED",
                extra={"workflow_name": workflow_name, "key": key},
            )
            continue
        validated[key] = value
    return validated


@app.post("/api/chats/{app_id}/{workflow_name}/start")
async def start_chat(
    app_id: str,
    workflow_name: str,
    request: Request,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Create an idempotent runtime chat session for a workflow."""
    validate_path_app_id(principal, app_id)

    data: Dict[str, Any] = {}
    try:
        parsed = await request.json()
        if isinstance(parsed, dict):
            data = parsed
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    except Exception:
        data = {}

    user_id = _validate_user_id_against_principal(
        principal,
        body_user_id=data.get("user_id"),
    )
    client_request_id = data.get("client_request_id")
    force_new = str(data.get("force_new", "false")).lower() in {"1", "true", "yes", "on"}
    context_variables = _validate_context_for_workflow(
        workflow_name,
        data.get("context_variables") if isinstance(data.get("context_variables"), dict) else {},
    )

    idempotency_window_sec = int(os.getenv("CHAT_START_IDEMPOTENCY_SEC", "15"))
    reuse_cutoff = datetime.now(UTC) - timedelta(seconds=idempotency_window_sec)
    coll = await _chat_coll()

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
            reused_doc = await coll.find_one(
                {**base_query, "client_request_id": client_request_id},
                projection={"chat_id": 1, "created_at": 1},
            )
        if not reused_doc:
            reused_doc = await coll.find_one(base_query, projection={"chat_id": 1, "created_at": 1})
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
                "reused": True,
                "cache_seed": cache_seed,
            }

    chat_id = str(uuid4())
    extra_fields: Dict[str, Any] = {}
    if client_request_id:
        extra_fields["client_request_id"] = client_request_id
    extra_fields.update(context_variables)

    await persistence_manager.create_chat_session(
        chat_id=chat_id,
        app_id=app_id,
        workflow_name=workflow_name,
        user_id=user_id,
        extra_fields=extra_fields or None,
    )

    try:
        perf_mgr = await get_performance_manager()
        await perf_mgr.record_workflow_start(chat_id, app_id, workflow_name, user_id)
    except Exception as perf_err:
        wf_logger.debug("perf_start skipped for chat %s: %s", chat_id, perf_err)

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
        "reused": False,
        "cache_seed": cache_seed,
    }


class TriggerWorkflowRequest(BaseModel):
    """Request body for programmatic runtime workflow triggering."""

    user_id: str = Field(..., description="User ID to run workflow as")
    app_id: Optional[str] = Field(None, description="Application ID")
    context: Optional[Dict[str, Any]] = Field(None, description="Initial context variables")
    webhook_url: Optional[str] = Field(None, description="Optional completion notification URL")


@app.post("/api/workflows/{workflow_name}/trigger")
async def trigger_workflow(
    workflow_name: str,
    body: TriggerWorkflowRequest,
    request: Request,
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Create a runtime workflow session for programmatic callers."""
    _ = principal
    _validate_internal_api_key(request)

    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    if workflow_name not in workflow_manager.get_all_workflow_names():
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_name}' not found")

    app_id = body.app_id or f"trigger-{workflow_name}"
    user_id = body.user_id
    chat_id = str(uuid4())
    context = _validate_context_for_workflow(workflow_name, body.context or {})

    extra_fields: Dict[str, Any] = {"trigger_mode": "api"}
    if body.webhook_url:
        extra_fields["webhook_url"] = body.webhook_url
    extra_fields.update(context)

    await persistence_manager.create_chat_session(
        chat_id=chat_id,
        app_id=app_id,
        workflow_name=workflow_name,
        user_id=user_id,
        extra_fields=extra_fields,
    )

    try:
        perf_mgr = await get_performance_manager()
        await perf_mgr.record_workflow_start(chat_id, app_id, workflow_name, user_id)
    except Exception as perf_err:
        wf_logger.debug("perf_start skipped for trigger %s: %s", chat_id, perf_err)

    return {
        "success": True,
        "chat_id": chat_id,
        "run_id": chat_id,
        "workflow_name": workflow_name,
        "app_id": app_id,
        "user_id": user_id,
        "websocket_url": f"/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}",
        "message": "Workflow session created. Connect via WebSocket or submit input to execute.",
    }


@app.websocket("/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    workflow_name: str,
    app_id: str,
    chat_id: str,
    user_id: str,
):
    """Pure runtime WebSocket transport endpoint."""
    if simple_transport is None:
        await websocket.close(code=1011, reason="Transport service unavailable")
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
        coll = await _chat_coll()
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
        else:
            await persistence_manager.create_chat_session(
                chat_id=chat_id,
                app_id=app_id,
                workflow_name=workflow_name,
                user_id=user_id,
            )
    except Exception as ownership_err:
        wf_logger.warning("WS_CHAT_PREP_FAILED: %s", ownership_err)
        await websocket.close(code=1011, reason="Failed to prepare chat session")
        return

    await simple_transport.handle_websocket(
        websocket=websocket,
        chat_id=chat_id,
        user_id=user_id,
        workflow_name=workflow_name,
        app_id=app_id,
        ws_id=id(websocket),
    )


@app.post("/chat/{app_id}/{chat_id}/{user_id}/input")
async def handle_user_input(
    request: Request,
    app_id: str,
    chat_id: str,
    user_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Submit user input and execute or resume a runtime workflow."""
    user_id = _validate_user_id_against_principal(principal, path_user_id=user_id)

    if simple_transport is None:
        raise HTTPException(status_code=503, detail="Transport service is not available")

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    message = data.get("message")
    workflow_name = data.get("workflow_name")
    if not workflow_name:
        raise HTTPException(status_code=400, detail="workflow_name is required")
    if not message:
        raise HTTPException(status_code=400, detail="message cannot be empty")
    if _MESSAGE_MAX_CHARS and len(str(message)) > _MESSAGE_MAX_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"message exceeds maximum length of {_MESSAGE_MAX_CHARS} characters",
        )

    coll = await _chat_coll()
    owned = await coll.find_one(
        {"_id": chat_id, "user_id": user_id, **build_app_scope_filter(app_id)},
        {"_id": 1},
    )
    if not owned:
        raise HTTPException(status_code=404, detail="Chat not found")

    result = await simple_transport.handle_user_input_from_api(
        chat_id=chat_id,
        user_id=user_id,
        workflow_name=workflow_name,
        message=message,
        app_id=app_id,
    )
    return {"status": "Message received and is being processed.", "result": result}


@app.post("/chat/{app_id}/{chat_id}/component_action")
async def handle_component_action(
    request: Request,
    app_id: str,
    chat_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Apply a runtime UI component action to workflow context."""
    if simple_transport is None:
        raise HTTPException(status_code=503, detail="Transport service is not available")

    if principal.user_id != "anonymous":
        coll = await _chat_coll()
        owned = await coll.find_one(
            {"_id": chat_id, "user_id": principal.user_id, **build_app_scope_filter(app_id)},
            {"_id": 1},
        )
        if not owned:
            raise HTTPException(status_code=404, detail="Chat not found")

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    component_id = data.get("component_id")
    action_type = data.get("action_type")
    action_data = data.get("action_data", {})
    if not component_id or not action_type:
        raise HTTPException(status_code=400, detail="'component_id' and 'action_type' are required")

    result = await simple_transport.process_component_action(
        chat_id=chat_id,
        app_id=app_id,
        component_id=component_id,
        action_type=action_type,
        action_data=action_data or {},
    )
    return {"status": "success", "result": result}


@app.post("/api/tool-call/respond")
async def submit_tool_call_response(
    request: Request,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Submit a response from a runtime UI tool component."""
    _ = principal
    if simple_transport is None:
        raise HTTPException(status_code=503, detail="Transport service is not available")

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_id = data.get("event_id")
    response_data = data.get("response_data")
    if not event_id:
        raise HTTPException(status_code=400, detail="event_id is required")
    if not response_data:
        raise HTTPException(status_code=400, detail="response_data is required")

    success = await simple_transport.submit_tool_call_response(event_id, response_data)
    if not success:
        raise HTTPException(status_code=404, detail="UI tool event not found or already completed")
    return {"status": "success"}


@app.get("/api/workflows/{workflow_name}/transport")
async def get_workflow_transport_info(
    workflow_name: str,
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return workflow transport metadata."""
    _ = principal
    return {"workflow_name": workflow_name, "transport": get_workflow_transport(workflow_name)}


@app.get("/api/workflows/{workflow_name}/tools")
async def get_workflow_tools_info(
    workflow_name: str,
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return workflow runtime tool metadata."""
    _ = principal
    return {"workflow_name": workflow_name, "tools": get_workflow_tools(workflow_name)}


@app.get("/api/workflows/{workflow_name}/ui-tools")
async def get_workflow_ui_tools_manifest(
    workflow_name: str,
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return workflow UI tool manifest metadata."""
    _ = principal
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    ui_tools = workflow_manager.get_workflow_tools(workflow_name)
    return {"workflow_name": workflow_name, "ui_tools": ui_tools}


@app.get("/api/workflows")
async def get_workflows(
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return loaded workflows in SDK-friendly format."""
    _ = principal
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    workflows = []
    for workflow_name in sorted(workflow_manager.get_all_workflow_names()):
        config = workflow_manager.get_config(workflow_name)
        workflows.append(
            {
                "name": workflow_name,
                "display_name": config.get("display_name") or config.get("name") or workflow_name,
                "initial_agent": config.get("initial_agent"),
                "visual_agents": config.get("visual_agents") or [],
                "status": "ready",
            }
        )
    return {"workflows": workflows}


@app.get("/api/workflows/config")
async def get_workflow_configs(
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return loaded workflow configurations."""
    _ = principal
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    return {
        workflow_name: workflow_manager.get_config(workflow_name)
        for workflow_name in sorted(workflow_manager.get_all_workflow_names())
    }

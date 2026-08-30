# ==============================================================================
# MODULE: mozaiksai.hosts.runtime
# DESCRIPTION: Minimal FastAPI runtime host for workflows, agents, transport,
#              events, persistence, and auth.
# ==============================================================================
from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import logging
import os
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import ag2
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.cors import CORSMiddleware

from logs.logging_config import (
    get_workflow_logger,
    setup_development_logging,
    setup_production_logging,
)
from mozaiksai.core.auth import (
    WS_CLOSE_POLICY_VIOLATION,
    UserPrincipal,
    authenticate_websocket_with_path_binding,
    require_any_auth,
    require_user_scope,
)
from mozaiksai.core.auth.dependencies import (
    validate_path_app_id,
    validate_path_id,
)
from mozaiksai.core.auth.dependencies import (
    validate_user_id_against_principal as _validate_user_id_against_principal,
)
from mozaiksai.core.cache.redis_cache import get_redis_cache
from mozaiksai.core.core_config import close_mongo_client, get_mongo_client
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher
from mozaiksai.core.multitenant import build_app_scope_filter
from mozaiksai.core.ports.event_bus import get_event_bus
from mozaiksai.core.runtime.persistence.artifact_version import ensure_artifact_version_indexes
from mozaiksai.core.runtime.persistence.distributed_lock import (
    configure_chat_lock,
    ensure_lock_indexes,
)
from mozaiksai.core.runtime.persistence.startup_policy import get_database_startup_policy
from mozaiksai.core.startup.validation import run_startup_checks
from mozaiksai.core.transport.handlers.workflow_handlers import _background_task_failure_callback
from mozaiksai.core.transport.rate_limit import RateLimitMiddleware
from mozaiksai.core.transport.request_middleware import (
    RequestBodySizeLimitMiddleware,
    RequestIDMiddleware,
)
from mozaiksai.core.transport.security_headers import SecurityHeadersMiddleware
from mozaiksai.core.transport.simple_transport import SimpleTransport
from mozaiksai.core.workflow.idempotency import ensure_idempotency_indexes
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
simple_transport: SimpleTransport | None = None


logging.getLogger("ag2").setLevel(logging.DEBUG)
wf_logger.info("ag2 version: %s", ag2.__version__)

app = FastAPI(
    title="Mozaiks Runtime Host",
    description="Generic workflow, agent, transport, event, persistence, and auth runtime.",
    version=__version__,
)
app.state.persistence_manager = persistence_manager

# AG2 per-agent Prometheus metrics — enabled by AG2_METRICS_ENABLED=true.
# Requires: pip install "ag2[metrics]"
# Mounted at AG2_METRICS_PATH (default: /metrics/ag2).
try:
    from mozaiksai.core.observability import make_metrics_asgi_app

    _ag2_metrics_app = make_metrics_asgi_app()
    if _ag2_metrics_app is not None:
        _ag2_metrics_path = os.getenv("AG2_METRICS_PATH", "/metrics/ag2")
        app.mount(_ag2_metrics_path, _ag2_metrics_app)
except Exception as _ag2_metrics_err:
    logger.debug("AG2 metrics ASGI mount skipped: %s", _ag2_metrics_err)


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Sanitize 5xx responses so internal exception details are never sent to clients.

    4xx errors are intentionally user-facing (validation, not-found, conflict) and
    pass through unchanged. 5xx errors log the detail server-side and return a
    generic message to prevent leaking database addresses, file paths, or stack info.
    """
    if exc.status_code == 500:
        # Strip internal exception details from 500 responses to prevent leaking
        # database addresses, file paths, or exception stack info to clients.
        # 503 and other 5xx codes carry intentional operational messages (e.g.
        # "Service Unavailable", "Refinement classification unavailable") that
        # should pass through to the caller.
        logger.warning(
            "HTTP 500 on %s %s: %s",
            request.method,
            request.url.path,
            exc.detail,
        )
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})
    headers = getattr(exc, "headers", None)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=headers,
    )


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


def _parse_csv_origins(raw: str | None) -> list[str]:
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
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-App-ID", "X-User-ID"],
        expose_headers=["X-API-Version", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset", "X-Request-ID"],
    )

# Rate limiting — runs before CORS (outermost layer).
# Controlled entirely by RATE_LIMIT_* env vars; disabled when RATE_LIMIT_ENABLED=false.
app.add_middleware(RateLimitMiddleware)

# Security headers — outermost response decorator.
# Adds X-Content-Type-Options, X-Frame-Options, CSP, HSTS, Referrer-Policy,
# and Permissions-Policy. Controlled by SECURITY_HEADERS_ENABLED env var.
app.add_middleware(SecurityHeadersMiddleware)

# Request ID — generates or validates X-Request-ID on every request/response.
# Bound to request.state.request_id for handlers, logs, and audit trails.
app.add_middleware(RequestIDMiddleware)

# Request body size limit — rejects oversized JSON bodies before the route handler
# reads them. Configurable via MAX_REQUEST_BODY_BYTES (default 1 MB).
app.add_middleware(RequestBodySizeLimitMiddleware)


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


def _client_console_bridge_enabled() -> bool:
    raw = os.getenv("CLIENT_CONSOLE_BRIDGE_ENABLED")
    if raw is None or not raw.strip():
        return env != "production"
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class ClientConsoleLogEntry(BaseModel):
    level: str = Field(default="info")
    method: str | None = None
    message: str | None = None
    args: list[Any] = Field(default_factory=list)
    timestamp: str | None = None
    app_id: str | None = None
    chat_id: str | None = None
    workflow_name: str | None = None
    user_id: str | None = None
    pathname: str | None = None
    href: str | None = None
    origin: str | None = None
    title: str | None = None
    source: str | None = None
    lineno: int | None = None
    colno: int | None = None
    stack: str | None = None
    reason: Any | None = None
    error: Any | None = None
    category: str | None = None

    @field_validator("level")
    @classmethod
    def _normalize_level(cls, value: str) -> str:
        normalized = str(value or "info").strip().lower()
        return normalized or "info"

    @field_validator("message")
    @classmethod
    def _normalize_message(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


class ClientConsoleBatch(BaseModel):
    source: str = Field(default="browser_console")
    reason: str | None = None
    sent_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    entries: list[ClientConsoleLogEntry] = Field(default_factory=list)

    @field_validator("entries")
    @classmethod
    def _limit_entries(cls, value: list[ClientConsoleLogEntry]) -> list[ClientConsoleLogEntry]:
        return value[:100]


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

    simple_transport = await SimpleTransport.get_instance()
    app.state.simple_transport = simple_transport

    if mongo_client is None:
        mongo_client = get_mongo_client()
    _db_policy = get_database_startup_policy()
    try:
        await mongo_client.admin.command("ping")
    except Exception as _ping_exc:
        wf_logger.warning(
            "RUNTIME_STARTUP_MONGO_PING_FAILED: policy=%s error=%s",
            _db_policy,
            _ping_exc,
        )
        if _db_policy == "required":
            raise

    await run_startup_checks()

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

    # Ensure enterprise infrastructure indexes exist.
    await asyncio.gather(
        ensure_lock_indexes(),
        ensure_idempotency_indexes(),
        ensure_artifact_version_indexes(),
    )

    # Pin the chat execution lock mode for this host process: `required`
    # (fail-closed distributed exclusion) whenever database persistence is
    # enabled, `local` (explicit single-process boundary) otherwise.
    configure_chat_lock()

    # Connect distributed cache (Redis) — falls back to in-memory silently.
    await get_redis_cache().connect()

    # Register built-in connector health providers for all catalog services.
    try:
        from mozaiksai.core.workflow.generator_support.connector_health_providers import (
            register_builtin_providers,
        )
        register_builtin_providers()
    except Exception as _chp_err:
        wf_logger.warning("Connector health providers registration failed: %s", _chp_err)

    # Start inter-instance event bus.
    event_bus = get_event_bus()
    await event_bus.start()

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


async def _ping_mongo(client) -> None:
    """Issue a valid MongoDB ping command.

    Server selection timeouts belong on the Mongo client/URI, not inside the
    ping command body. Passing serverSelectionTimeoutMS to command("ping", ...)
    makes MongoDB interpret it as an unknown command field.
    """
    await client.admin.command("ping")


@app.get("/api/health/readiness")
@app.get("/api/health/ready")
async def health_readiness(request: Request):
    """Readiness probe — checks all critical dependencies before accepting traffic."""
    checks: dict[str, str] = {}
    degraded = False

    # MongoDB
    if mongo_client is None:
        checks["mongodb"] = "not_initialized"
        degraded = True
    else:
        try:
            await _ping_mongo(mongo_client)
            checks["mongodb"] = "ok"
        except Exception:
            checks["mongodb"] = "error"
            degraded = True

    # Transport
    if simple_transport is None:
        checks["transport"] = "not_initialized"
        degraded = True
    else:
        checks["transport"] = "ok"

    # Platform startup degradation — set when module loading fails unexpectedly.
    startup_degraded = getattr(request.app.state, "startup_degraded", False)
    failed_module_names: list[str] = getattr(request.app.state, "failed_module_names", [])
    if startup_degraded:
        reason = getattr(request.app.state, "startup_degraded_reason", "unknown")
        checks["app_startup"] = f"degraded: {reason}"
        degraded = True
    else:
        checks["app_startup"] = "ok"

    status_code = 503 if degraded else 200
    body: dict[str, Any] = {
        "status": "degraded" if degraded else "ready",
        "timestamp": datetime.now(UTC).isoformat(),
        "checks": checks,
    }
    # Expose only the count of failed modules, not the names, to avoid leaking
    # internal app structure through an unauthenticated endpoint.
    if failed_module_names:
        body["failed_module_count"] = len(failed_module_names)
    return JSONResponse(status_code=status_code, content=body)


@app.get("/api/health")
async def health_check():
    """Full health check — includes dependency status and workflow summary."""
    if mongo_client is None:
        raise HTTPException(status_code=503, detail="MongoDB client is not initialized")

    try:
        await _ping_mongo(mongo_client)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="MongoDB unreachable") from exc

    status = workflow_status_summary()

    return {
        "status": "healthy",
        "timestamp": datetime.now(UTC).isoformat(),
        "workflows": status,
        "transport": "initialized" if simple_transport else "not_initialized",
    }


@app.post("/api/debug/client-console")
async def ingest_client_console_logs(request: Request):
    """Ingest browser console logs so frontend issues appear in backend logs."""
    if not _client_console_bridge_enabled():
        raise HTTPException(status_code=404, detail="Client console bridge disabled")

    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Unable to read JSON payload") from exc

    try:
        batch = ClientConsoleBatch.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid client console payload") from exc

    logger = logging.getLogger("mozaiks.workflow.frontend.browser_console")
    accepted = 0
    for entry in batch.entries:
        entry_logger = get_workflow_logger(
            base_logger=logger,
            app_id=entry.app_id or batch.metadata.get("app_id"),
            chat_id=entry.chat_id or batch.metadata.get("chat_id"),
            workflow_name=entry.workflow_name or batch.metadata.get("workflow_name"),
            user_id=entry.user_id or batch.metadata.get("user_id"),
        )
        message = entry.message or ""
        details = {
            "console_category": entry.category or batch.source,
            "console_method": entry.method or entry.level,
            "console_args": entry.args,
            "console_timestamp": entry.timestamp,
            "console_pathname": entry.pathname or batch.metadata.get("pathname"),
            "console_href": entry.href or batch.metadata.get("href"),
            "console_origin": entry.origin or batch.metadata.get("origin"),
            "console_title": entry.title or batch.metadata.get("title"),
            "console_source": entry.source,
            "console_lineno": entry.lineno,
            "console_colno": entry.colno,
            "console_stack": entry.stack,
            "console_reason": entry.reason,
            "console_error": entry.error,
            "console_batch_reason": batch.reason,
            "console_batch_sent_at": batch.sent_at,
        }
        level = str(entry.level or "info").lower()
        log_method = getattr(entry_logger, level, entry_logger.info)
        log_method(message or "BROWSER_CONSOLE_EVENT", **details)
        accepted += 1

    return {"status": "accepted", "entries": accepted}


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
        raise HTTPException(status_code=500, detail="Failed to retrieve background run summary") from exc


def _validate_context_for_workflow(workflow_name: str, context: dict[str, Any]) -> dict[str, Any]:
    """Filter caller-provided context to declared workflow context variables."""
    if not context:
        return {}

    try:
        from mozaiksai.core.workflow.context.authority import (
            CALLER_INPUT_WRITER,
            ContextAuthorityError,
            build_context_authority_policy,
        )
        from mozaiksai.core.workflow.workflow_manager import workflow_manager

        wf_cfg = workflow_manager.get_config(workflow_name) or {}
        definitions = (wf_cfg.get("context_variables") or {}).get("definitions", {})
        declared_keys = set(definitions.keys())
        policy = build_context_authority_policy(
            workflow_name=workflow_name,
            definitions=definitions,
            transition_rules=(wf_cfg.get("transition_graph") or {}).get("transition_rules") or [],
        )
    except Exception:
        declared_keys = set()
        policy = None

    validated: dict[str, Any] = {}
    for key, value in context.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if declared_keys and key not in declared_keys:
            wf_logger.warning(
                "RUNTIME_CONTEXT_KEY_REJECTED",
                extra={"workflow_name": workflow_name, "key": key},
            )
            continue
        if policy is not None:
            try:
                policy.require_can_write(key, writer_id=CALLER_INPUT_WRITER)
            except ContextAuthorityError as exc:
                wf_logger.warning(
                    "RUNTIME_CONTEXT_AUTHORITY_REJECTED",
                    extra={"workflow_name": workflow_name, "key": key},
                )
                raise HTTPException(status_code=400, detail={"code": "CONTEXT_AUTHORITY_REJECTED"}) from exc
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
    validate_path_id(app_id, "app_id")
    validate_path_id(workflow_name, "workflow_name")
    validate_path_app_id(principal, app_id)

    data: dict[str, Any] = {}
    try:
        parsed = await request.json()
        if isinstance(parsed, dict):
            data = parsed
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc
    except Exception:
        data = {}

    user_id = _validate_user_id_against_principal(
        principal,
        body_user_id=data.get("user_id"),
    )
    client_request_id = data.get("client_request_id")
    force_new = str(data.get("force_new", "false")).lower() in {"1", "true", "yes", "on"}
    transport_purpose = str(data.get("transport_purpose") or "").strip().lower()
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
    extra_fields: dict[str, Any] = {}
    if client_request_id:
        extra_fields["client_request_id"] = client_request_id
    if transport_purpose == "ask_carrier":
        extra_fields["transport_purpose"] = "ask_carrier"
    extra_fields.update(context_variables)

    await persistence_manager.create_chat_session(
        chat_id=chat_id,
        app_id=app_id,
        workflow_name=workflow_name,
        user_id=user_id,
        extra_fields=extra_fields or None,
    )

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
    app_id: str | None = Field(None, description="Application ID")
    journey_id: str | None = Field(None, description="Optional workflow sequence to bind the run to")
    context: dict[str, Any] | None = Field(None, description="Initial context variables")
    webhook_url: str | None = Field(None, description="Optional completion notification URL (https:// only)")

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        try:
            parsed = urlparse(v)
        except Exception as exc:
            raise ValueError("webhook_url must be a valid URL") from exc
        if parsed.scheme != "https":
            raise ValueError("webhook_url must use the https:// scheme")
        if not parsed.netloc:
            raise ValueError("webhook_url must include a hostname")
        hostname = parsed.hostname or ""
        # Reject hostnames that are private or reserved IP addresses.
        # Covers loopback (127.x / ::1), RFC-1918 private ranges (10.x, 172.16-31.x,
        # 192.168.x), link-local (169.254.x — cloud metadata endpoints), and
        # IPv6 ULA / link-local ranges.
        _BLOCKED_HOSTNAMES = {"localhost", "::1", "0.0.0.0"}
        if hostname in _BLOCKED_HOSTNAMES:
            raise ValueError("webhook_url must not target a private or reserved address")
        try:
            addr = ipaddress.ip_address(hostname)
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved or addr.is_multicast:
                raise ValueError("webhook_url must not target a private or reserved address")
        except ValueError as ip_exc:
            if "webhook_url" in str(ip_exc):
                # Re-raise our own validation error (not an ipaddress parse error)
                raise
            # hostname is a domain name — pass; runtime should use an egress proxy
            # for comprehensive SSRF protection in production deployments.
        return v


@app.post("/api/workflows/{workflow_name}/trigger")
async def trigger_workflow(
    workflow_name: str,
    body: TriggerWorkflowRequest,
    request: Request,
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Create a runtime workflow session for programmatic callers."""
    validate_path_id(workflow_name, "workflow_name")
    _validate_internal_api_key(request)

    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    if workflow_name not in workflow_manager.get_all_workflow_names():
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_name}' not found")

    app_id = body.app_id or f"trigger-{workflow_name}"

    # When an authenticated principal has an app_id claim, validate it matches the
    # requested app_id to prevent cross-tenant workflow creation via this endpoint.
    validate_path_app_id(principal, app_id)
    user_id = body.user_id
    chat_id = str(uuid4())
    context = _validate_context_for_workflow(workflow_name, body.context or {})

    extra_fields: dict[str, Any] = {"trigger_mode": "api"}
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

    if body.journey_id:
        try:
            from mozaiksai.core.session.router import get_session_router

            await get_session_router().bind_workflow_session(
                app_id=app_id,
                user_id=user_id,
                workflow_id=workflow_name,
                chat_id=chat_id,
                journey_id=body.journey_id,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid journey binding") from exc

    return {
        "success": True,
        "chat_id": chat_id,
        "run_id": chat_id,
        "workflow_name": workflow_name,
        "app_id": app_id,
        "user_id": user_id,
        "journey_id": body.journey_id,
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
    try:
        validate_path_id(app_id, "app_id")
        validate_path_id(chat_id, "chat_id")
        validate_path_id(user_id, "user_id")
        validate_path_id(workflow_name, "workflow_name")
    except Exception:
        await websocket.close(code=1008, reason="Invalid path parameter")
        return

    if simple_transport is None:
        await websocket.close(code=1011, reason="Transport service unavailable")
        return

    suppress_history_replay = str(websocket.query_params.get("suppress_history_replay", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    ws_user = await authenticate_websocket_with_path_binding(
        websocket,
        path_user_id=user_id,
        path_app_id=app_id,
        path_chat_id=chat_id,
    )
    if ws_user is None:
        return

    user_id = ws_user.user_id
    # Store the JWT exp claim so the inbound message loop can enforce token expiry.
    # Falls back to 0 (no expiry check) when auth is disabled or the token lacks `exp`.
    _raw_claims = getattr(ws_user, "raw_claims", None) or {}
    _token_exp: int = int(_raw_claims.get("exp", 0) or 0)

    ws_id: int | None = None
    try:
        from mozaiksai.core.data.persistence.persistence_manager import extract_last_artifact
        from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks
        from mozaiksai.core.session.router import get_session_router
        from mozaiksai.core.transport.event_contract import send_event_envelope
        from mozaiksai.core.transport.session_registry import session_registry
        from mozaiksai.core.workflow.workflow_manager import workflow_manager

        coll = await _chat_coll()
        hooks = get_platform_hooks()
        workflow_names = hooks.call_workflow_ordering(list(workflow_manager.get_all_workflow_names()))

        existing = await coll.find_one(
            {"_id": chat_id, **build_app_scope_filter(app_id)},
            {
                "_id": 1,
                "user_id": 1,
                "workflow_name": 1,
                "workflow_ui_state.last_artifact": 1,
                "status": 1,
                "created_at": 1,
            },
        )
        if existing:
            owner = existing.get("user_id")
            if not owner or str(owner).strip() != str(user_id).strip():
                await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason="Chat not found")
                return

        persisted_workflow = str((existing or {}).get("workflow_name") or "").strip()
        resolved_persisted = (
            hooks.call_workflow_name_resolver(persisted_workflow, workflow_names)
            if persisted_workflow
            else None
        )
        resolved_requested = hooks.call_workflow_name_resolver(workflow_name, workflow_names)

        if resolved_persisted:
            resolved_workflow_name = resolved_persisted
        else:
            resolved_workflow_name = resolved_requested or workflow_name
            if existing and persisted_workflow and persisted_workflow != resolved_workflow_name:
                await coll.update_one(
                    {"_id": chat_id, **build_app_scope_filter(app_id)},
                    {"$set": {"workflow_name": resolved_workflow_name, "last_updated_at": datetime.now(UTC)}},
                )
                existing["workflow_name"] = resolved_workflow_name
                existing["last_updated_at"] = datetime.now(UTC)

        try:
            prereqs_ok, prereq_reason = await hooks.call_chat_prereqs(
                app_id=app_id,
                user_id=user_id,
                workflow_name=resolved_workflow_name,
                persistence=persistence_manager,
            )
        except Exception as prereq_exc:
            logger.error("WS_PREREQ_VALIDATION_FAILED workflow=%s chat=%s: %s", resolved_workflow_name, chat_id, prereq_exc, exc_info=True)
            await websocket.accept()
            await send_event_envelope(websocket,
                {
                    "schema_version": "mozaiks.ui.event.v1",
                    "type": "chat.error",
                    "data": {
                        "message": "Failed to validate workflow prerequisites. Please try again.",
                        "error_code": "PREREQ_VALIDATION_ERROR",
                        "workflow_name": resolved_workflow_name,
                        "chat_id": chat_id,
                    },
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
            await websocket.close(code=1011, reason="Prerequisite validation failed")
            return

        if not prereqs_ok:
            await websocket.accept()
            await send_event_envelope(websocket,
                {
                    "schema_version": "mozaiks.ui.event.v1",
                    "type": "chat.error",
                    "data": {
                        "message": prereq_reason or "Workflow prerequisites are not met.",
                        "error_code": "WORKFLOW_PREREQS_NOT_MET",
                        "workflow_name": resolved_workflow_name,
                        "chat_id": chat_id,
                    },
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
            await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason="Prerequisites not met")
            return

        if not existing:
            await persistence_manager.create_chat_session(
                chat_id=chat_id,
                app_id=app_id,
                workflow_name=resolved_workflow_name,
                user_id=user_id,
            )
            existing = await coll.find_one(
                {"_id": chat_id, **build_app_scope_filter(app_id)},
                {
                    "_id": 1,
                    "user_id": 1,
                    "workflow_name": 1,
                    "workflow_ui_state.last_artifact": 1,
                    "status": 1,
                    "created_at": 1,
                },
            ) or {
                "_id": chat_id,
                "user_id": user_id,
                "workflow_name": resolved_workflow_name,
                "status": 0,
            }

        session_router = get_session_router()
        resume_resolution = await session_router.resolve_resume(
            app_id=app_id,
            user_id=user_id,
            requested_workflow_id=resolved_workflow_name,
            requested_chat_id=chat_id,
        )
        resolved_chat_id = str(resume_resolution.get("chat_id") or chat_id)
        resume_workflow_name = str(resume_resolution.get("workflow_id") or "").strip()
        if resume_workflow_name:
            resolved_resume_workflow = hooks.call_workflow_name_resolver(resume_workflow_name, workflow_names)
            if resolved_resume_workflow:
                resolved_workflow_name = resolved_resume_workflow
        resolved_doc = await coll.find_one(
            {"_id": resolved_chat_id, **build_app_scope_filter(app_id)},
            {
                "_id": 1,
                "user_id": 1,
                "workflow_name": 1,
                "workflow_ui_state.last_artifact": 1,
                "status": 1,
                "created_at": 1,
            },
        )

        created_resolved_chat = False
        if not resolved_doc:
            await persistence_manager.create_chat_session(
                chat_id=resolved_chat_id,
                app_id=app_id,
                workflow_name=resolved_workflow_name,
                user_id=user_id,
            )
            created_resolved_chat = True
            await session_router.bind_workflow_session(
                app_id=app_id,
                user_id=user_id,
                workflow_id=resolved_workflow_name,
                chat_id=resolved_chat_id,
            )
            resolved_doc = await coll.find_one(
                {"_id": resolved_chat_id, **build_app_scope_filter(app_id)},
                {
                    "_id": 1,
                    "user_id": 1,
                    "workflow_name": 1,
                    "workflow_ui_state.last_artifact": 1,
                    "status": 1,
                    "created_at": 1,
                },
            ) or {
                "_id": resolved_chat_id,
                "user_id": user_id,
                "workflow_name": resolved_workflow_name,
                "status": 0,
            }

        owner = resolved_doc.get("user_id")
        if not owner or str(owner).strip() != str(user_id).strip():
            await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason="Chat not found")
            return

        resolved_doc_workflow = str(resolved_doc.get("workflow_name") or "").strip()
        if resolved_doc_workflow and resolved_doc_workflow != resolved_workflow_name:
            maybe_resolved = hooks.call_workflow_name_resolver(resolved_doc_workflow, workflow_names)
            if maybe_resolved:
                resolved_workflow_name = maybe_resolved
            else:
                await coll.update_one(
                    {"_id": resolved_chat_id, **build_app_scope_filter(app_id)},
                    {"$set": {"workflow_name": resolved_workflow_name, "last_updated_at": datetime.now(UTC)}},
                )
                resolved_doc["workflow_name"] = resolved_workflow_name

        session_state = resume_resolution.get("session_state")
        if session_state is None:
            if created_resolved_chat:
                session_state = await session_router.get_session_snapshot(app_id=app_id, user_id=user_id)
            else:
                session_state = {}

        # Capture session version at the point of resume.  This baseline is used
        # later to detect silent concurrent writes (a soft staleness guard that
        # complements the distributed lock's hard mutual-exclusion boundary).
        _resume_session_version = await persistence_manager.get_session_version(resolved_chat_id, app_id)

        cache_seed = await persistence_manager.get_or_assign_cache_seed(resolved_chat_id, app_id)
        ws_id = id(websocket)
        session_registry.add_workflow(
            ws_id=ws_id,
            chat_id=resolved_chat_id,
            workflow_name=resolved_workflow_name,
            app_id=app_id,
            user_id=user_id,
            auto_activate=True,
        )

        created_at = resolved_doc.get("created_at")
        run_history = await persistence_manager.load_run_history(chat_id=resolved_chat_id, app_id=app_id)
        run_history_count = len(run_history)
        chat_meta = {
            "kind": "chat_meta",
            "chat_id": resolved_chat_id,
            "workflow_name": resolved_workflow_name,
            "app_id": app_id,
            "user_id": user_id,
            "has_children": False,
            "cache_seed": cache_seed,
            "chat_exists": True,
            "last_artifact": extract_last_artifact(resolved_doc),
            "status": int(resolved_doc.get("status", 0) or 0),
            "run_history_count": run_history_count,
            "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
            "session_state": session_state or {},
            "session_version": _resume_session_version,
        }
        await simple_transport.send_event_to_ui(chat_meta, resolved_chat_id)

        from mozaiksai.core.workflow.startup_messages import (
            resolve_workflow_launch_taxonomy,
            should_autostart_empty_workflow,
        )

        config = workflow_manager.get_config(resolved_workflow_name) or {}
        _config_startup_mode = str(config.get("workflow_startup_mode") or "").strip()
        startup_mode = _config_startup_mode or "AgentDriven"
        launch_taxonomy = resolve_workflow_launch_taxonomy(
            resolved_workflow_name,
            workflow_startup_mode=startup_mode,
        )
        # When the operator has explicitly set workflow_startup_mode in the config,
        # that intent takes precedence over the registry's launch_behavior default.
        # Pass launch_behavior=None so startup_mode alone drives the auto-start decision.
        _autostart_launch_behavior = None if _config_startup_mode else launch_taxonomy.get("launch_behavior")
        if (
            should_autostart_empty_workflow(
                startup_mode,
                launch_behavior=_autostart_launch_behavior,
            )
            and run_history_count == 0
        ):
            existing_task = simple_transport._background_tasks.get(resolved_chat_id)
            if not (existing_task and not existing_task.done()):
                conn = simple_transport.connections.setdefault(resolved_chat_id, {})
                conn["autostarted"] = True
                _agent_start_task = asyncio.create_task(
                    simple_transport.handle_user_input_from_api(
                        chat_id=resolved_chat_id,
                        user_id=user_id,
                        workflow_name=resolved_workflow_name,
                        message=None,
                        app_id=app_id,
                    )
                )
                simple_transport._background_tasks[resolved_chat_id] = _agent_start_task
                _agent_start_task.add_done_callback(
                    _background_task_failure_callback(resolved_workflow_name, resolved_chat_id)
                )

        # Staleness guard: verify the session hasn't been concurrently modified
        # between when we read the document and now.  Any version advance means
        # another instance (or code path) wrote to this session in the interim.
        # The distributed lock in core/runtime/persistence/distributed_lock.py
        # provides the hard mutual-exclusion boundary during execution; this check
        # catches modifications that arrive before lock acquisition.
        if not created_resolved_chat and _resume_session_version is not None:
            is_fresh = await persistence_manager.check_session_not_stale(
                resolved_chat_id, app_id, known_version=_resume_session_version
            )
            if not is_fresh:
                await websocket.close(
                    code=WS_CLOSE_POLICY_VIOLATION,
                    reason="Session was concurrently modified; please retry",
                )
                return

        await simple_transport.handle_websocket(
            websocket=websocket,
            chat_id=resolved_chat_id,
            user_id=user_id,
            workflow_name=resolved_workflow_name,
            app_id=app_id,
            ws_id=ws_id,
            token_exp=_token_exp,
            suppress_history_replay=suppress_history_replay,
        )
    except Exception as ownership_err:
        wf_logger.warning("WS_CHAT_PREP_FAILED: %s", ownership_err)
        await websocket.close(code=1011, reason="Failed to prepare chat session")
        return
    finally:
        if ws_id is not None:
            session_registry.remove_session(ws_id)


@app.post("/chat/{app_id}/{chat_id}/{user_id}/input")
async def handle_user_input(
    request: Request,
    app_id: str,
    chat_id: str,
    user_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    """Submit user input and execute or resume a runtime workflow."""
    validate_path_id(app_id, "app_id")
    validate_path_id(chat_id, "chat_id")
    validate_path_id(user_id, "user_id")
    user_id = _validate_user_id_against_principal(principal, path_user_id=user_id)

    if simple_transport is None:
        raise HTTPException(status_code=503, detail="Transport service is not available")

    try:
        data = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

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
    validate_path_id(app_id, "app_id")
    validate_path_id(chat_id, "chat_id")
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
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

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
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

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
    validate_path_id(workflow_name, "workflow_name")
    _ = principal
    return {"workflow_name": workflow_name, "transport": get_workflow_transport(workflow_name)}


@app.get("/api/workflows/{workflow_name}/tools")
async def get_workflow_tools_info(
    workflow_name: str,
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return workflow runtime tool metadata."""
    validate_path_id(workflow_name, "workflow_name")
    _ = principal
    return {"workflow_name": workflow_name, "tools": get_workflow_tools(workflow_name)}


@app.get("/api/workflows/{workflow_name}/ui-tools")
async def get_workflow_ui_tools_manifest(
    workflow_name: str,
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return workflow UI tool manifest metadata."""
    validate_path_id(workflow_name, "workflow_name")
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
    from mozaiksai.core.workflow.startup_messages import resolve_workflow_launch_taxonomy
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    workflows = []
    for workflow_name in sorted(workflow_manager.get_all_workflow_names()):
        config = workflow_manager.get_config(workflow_name)
        startup_mode = str(config.get("workflow_startup_mode") or "").strip() or "AgentDriven"
        launch_taxonomy = resolve_workflow_launch_taxonomy(
            workflow_name,
            workflow_startup_mode=startup_mode,
        )
        workflows.append(
            {
                "name": workflow_name,
                "display_name": config.get("display_name") or config.get("name") or workflow_name,
                "initial_agent": config.get("initial_agent"),
                "visual_agents": config.get("visual_agents") or [],
                "startup_mode": startup_mode,
                "workflow_startup_mode": startup_mode,
                "interaction_mode": launch_taxonomy.get("interaction_mode"),
                "launch_behavior": launch_taxonomy.get("launch_behavior"),
                "handoff_style": launch_taxonomy.get("handoff_style"),
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

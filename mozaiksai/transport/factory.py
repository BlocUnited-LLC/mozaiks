"""mozaiksai.transport.factory — Application factory for MozaiksAI Runtime.

Public entry-point:

    from mozaiksai.transport.factory import build_app
    app = build_app()

The factory wires up:
  - FastAPI instance with metadata
  - CORS middleware (env-driven)
  - Principal-header enforcement middleware (env-gated)
  - Persistence manager   (``app.state.persistence_manager``)
  - Event dispatcher       (``app.state.event_dispatcher``)
  - SimpleTransport        (``app.state.simple_transport``, on startup)
  - MongoDB client         (``app.state.mongo_client``, on startup)
  - Runtime extensions     (workflow-declared APIRouters)
  - Route modules          (health, chat, session, upload, workflow, ws, input)
  - Startup / shutdown lifecycle with performance metrics
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, UTC

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

from logs.logging_config import (
    setup_development_logging,
    setup_production_logging,
    get_workflow_logger,
)

# ---------------------------------------------------------------------------
# Module-level logging bootstrap
# ---------------------------------------------------------------------------

_ENV = os.getenv("ENVIRONMENT", "development").lower()


def _configure_logging() -> None:
    """Set up structured logging based on ``ENVIRONMENT`` env var."""
    if _ENV == "production":
        setup_production_logging()
        get_workflow_logger("factory_setup").info(
            "LOGGING_CONFIGURED: Production logging configuration applied"
        )
    else:
        setup_development_logging()
        get_workflow_logger("factory_setup").info(
            "LOGGING_CONFIGURED: Development logging configuration applied"
        )
    # AG2 library logging
    logging.getLogger("autogen").setLevel(logging.DEBUG)


def _patch_autogen_file_logger() -> None:
    try:
        from mozaiksai.engine.observability.runtime_patches import patch_ag2_file_logger

        patch_ag2_file_logger()
    except Exception as patch_err:
        get_workflow_logger("factory").debug(f"Skipped AG2 file_logger patch: {patch_err}")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_app() -> FastAPI:
    """Create and return the fully-configured MozaiksAI FastAPI application.

    This is the single authoritative factory.  ``shared_app.py`` calls it and
    exposes the result as ``app`` so that ``run_server.py`` can import it.
    """

    # -- logging & AG2 safety patches (before anything else) ----------------
    _configure_logging()
    _patch_autogen_file_logger()

    wf_logger = get_workflow_logger("factory")
    performance_logger = get_workflow_logger("performance.factory")

    from mozaiksai.engine.capabilities import get_ag2_capability_report

    ag2_capabilities = get_ag2_capability_report()
    wf_logger.info(f"ag2 version: {ag2_capabilities.get('version', 'unknown')}")
    wf_logger.info(f"SERVER_STARTUP_INIT: Starting MozaiksAI in {_ENV} mode")

    # -- core singletons (before app creation so they're available) ---------
    from mozaiksai.runtime.data.persistence.persistence_manager import AG2PersistenceManager
    from mozaiksai.kernel.dispatcher import get_event_dispatcher
    from mozaiksai.runtime.observability.performance_manager import get_performance_manager
    from mozaiksai.runtime.observability.run_registry import get_run_registry_summary  # noqa: F401, pre-import

    persistence_manager = AG2PersistenceManager()
    event_dispatcher = get_event_dispatcher()
    wf_logger.info("Unified Event Dispatcher initialized")

    # -- FastAPI app ---------------------------------------------------------
    app = FastAPI(
        title="MozaiksAI Runtime",
        description="Production-ready AG2 runtime with workflow-specific tools",
        version="5.0.0",
    )

    app.state.persistence_manager = persistence_manager
    app.state.event_dispatcher = event_dispatcher
    # Populated during startup; set to None so route modules can check safely
    app.state.simple_transport = None
    app.state.mongo_client = None

    # -- CORS ---------------------------------------------------------------
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
            allow_origin_regex=r".*",
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # -- principal-header enforcement middleware (env-gated) -----------------
    _enforce = os.getenv("ENFORCE_PRINCIPAL_HEADERS", "false").lower() in (
        "true",
        "1",
        "yes",
    )

    @app.middleware("http")
    async def principal_header_middleware(request: Request, call_next):
        """Validate x-app-id / x-user-id headers match path params when present."""
        if not _enforce:
            return await call_next(request)

        hdr_app_id = request.headers.get("x-app-id") or request.headers.get(
            "x-mozaiks-app-id"
        )
        hdr_user_id = request.headers.get("x-user-id") or request.headers.get(
            "x-mozaiks-user-id"
        )

        if not hdr_app_id and not hdr_user_id:
            return await call_next(request)

        path = request.url.path
        app_id_match = re.search(r"/api/chats/([^/]+)/", path) or re.search(
            r"/ws/[^/]+/([^/]+)/", path
        )
        user_id_match = re.search(r"/ws/[^/]+/[^/]+/[^/]+/([^/]+)", path)

        path_app_id = app_id_match.group(1) if app_id_match else None
        path_user_id = user_id_match.group(1) if user_id_match else None

        if hdr_app_id and path_app_id:
            if str(hdr_app_id).strip() != str(path_app_id).strip():
                wf_logger.warning(
                    "PRINCIPAL_HEADER_MISMATCH",
                    extra={
                        "header_app_id": hdr_app_id,
                        "path_app_id": path_app_id,
                    },
                )
                return JSONResponse(
                    status_code=403,
                    content={"detail": "x-app-id header does not match path app_id"},
                )

        if hdr_user_id and path_user_id:
            if str(hdr_user_id).strip() != str(path_user_id).strip():
                wf_logger.warning(
                    "PRINCIPAL_HEADER_MISMATCH",
                    extra={
                        "header_user_id": hdr_user_id,
                        "path_user_id": path_user_id,
                    },
                )
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": "x-user-id header does not match path user_id"
                    },
                )

        return await call_next(request)

    # -- runtime extensions (workflow-declared routers / plugins) ------------
    from mozaiksai.runtime.extensions.extensions import (
        mount_declared_routers,
        start_declared_services,
        stop_services,
    )

    try:
        mount_declared_routers(app)
    except Exception as _ext_err:
        wf_logger.debug(f"RUNTIME_EXTENSIONS_MOUNT_FAILED: {_ext_err}")

    # -- route modules ------------------------------------------------------
    from mozaiksai.transport.routes import ALL_ROUTERS

    for rtr in ALL_ROUTERS:
        app.include_router(rtr)

    # -- lifecycle: startup -------------------------------------------------
    @app.on_event("startup")
    async def startup():
        startup_start = datetime.now(UTC)
        wf_logger.info("APP_STARTUP: FastAPI startup event triggered")
        wf_logger.info(f"APP_STARTUP: Environment = {_ENV}")

        # ---- cache controls -----------------------------------------------
        def _env_bool(name: str, default: bool = False) -> bool:
            val = os.getenv(name)
            if val is None:
                return default
            return str(val).lower() in ("1", "true", "yes", "y", "on")

        try:
            clear_tools = _env_bool(
                "CLEAR_TOOL_CACHE_ON_START", default=(_ENV != "production")
            )
            if clear_tools:
                from mozaiksai.engine.agents.tools import clear_tool_cache

                cleared = clear_tool_cache()
                wf_logger.info(
                    f"TOOL_CACHE: Cleared {cleared} cached tool modules on startup"
                )
            else:
                wf_logger.info(
                    "TOOL_CACHE: Preserve cached tool modules (CLEAR_TOOL_CACHE_ON_START=0)"
                )
        except Exception as e:
            wf_logger.error(
                "TOOL_CACHE_CLEAR_FAILED: Failed to clear tool cache on startup",
                error=str(e),
            )

        try:
            if _env_bool("CLEAR_LLM_CACHES_ON_START", default=False):
                from mozaiksai.engine.validation.llm_config import clear_llm_caches

                clear_llm_caches(raw=True, built=True)
                wf_logger.info(
                    "LLM_CACHE: Cleared raw and built llm_config caches on startup"
                )
            ttl = os.getenv("LLM_CONFIG_CACHE_TTL", "300")
            wf_logger.info(f"LLM_CACHE: Effective TTL (secs) = {ttl}")
        except Exception as e:
            wf_logger.error(
                "LLM_CACHE_CLEAR_FAILED: Failed LLM cache management on startup",
                error=str(e),
            )

        try:
            # Performance / observability
            wf_logger.info("APP_STARTUP: Initializing performance manager...")
            perf_mgr = await get_performance_manager()
            await perf_mgr.initialize()
            wf_logger.info("APP_STARTUP: Performance manager initialized")

            # SimpleTransport
            from mozaiksai.transport.websocket.handler import SimpleTransport

            streaming_start = datetime.now(UTC)
            simple_transport = await SimpleTransport.get_instance()
            app.state.simple_transport = simple_transport

            # Wire transport port into the pack coordinator so it no longer
            # imports SimpleTransport directly.
            event_dispatcher._pack_coordinator.set_transport(simple_transport)
            streaming_time = (
                (datetime.now(UTC) - streaming_start).total_seconds() * 1000
            )
            performance_logger.info(
                "streaming_config_init_duration",
                metric_name="streaming_config_init_duration",
                value=float(streaming_time),
                config_keys=[],
                streaming_enabled=True,
            )

            # WorkerRegistry bootstrap — register AgentWorker so RunSupervisor
            # can dispatch runs without importing AG2 directly.
            try:
                from mozaiksai.workers.agent_worker import AgentWorker
                from mozaiksai.runtime.worker_registry import get_worker_registry
                from mozaiksai.runtime.execution.capability_registry import (
                    get_capability_registry,
                    CapabilityMetadata,
                )

                _agent_worker = AgentWorker()
                get_worker_registry().register(_agent_worker)
                get_capability_registry().register(
                    "agent",
                    metadata=CapabilityMetadata(
                        name="agent",
                        worker_type="agent",
                        description="Multi-agent orchestration via AG2",
                        features=["handoffs", "tools", "group_chat"],
                    ),
                )
                wf_logger.info(
                    "RUNTIME: AgentWorker registered (capability='agent', worker_type='agent')"
                )
            except Exception as _wr_err:
                wf_logger.warning(
                    f"RUNTIME: WorkerRegistry bootstrap failed (non-fatal): {_wr_err}"
                )

            # Platform worker registration hook — lets platform layers register
            # additional capability workers (e.g. 'provision', 'build') without
            # modifying this factory.
            try:
                from mozaiksai.runtime.extensions.platform_hooks import get_platform_hooks
                from mozaiksai.runtime.worker_registry import get_worker_registry
                from mozaiksai.runtime.execution.capability_registry import get_capability_registry

                get_platform_hooks().call_register_workers(
                    worker_registry=get_worker_registry(),
                    capability_registry=get_capability_registry(),
                )
            except Exception as _prw_err:
                wf_logger.warning(
                    f"RUNTIME: platform register_workers hook failed (non-fatal): {_prw_err}"
                )

            # MongoDB client
            from mozaiksai.runtime.config import get_mongo_client

            mongo_client = get_mongo_client()
            app.state.mongo_client = mongo_client

            mongo_start = datetime.now(UTC)
            try:
                await mongo_client.admin.command("ping")
                mongo_time = (
                    (datetime.now(UTC) - mongo_start).total_seconds() * 1000
                )
                performance_logger.info(
                    "mongodb_ping_duration",
                    metric_name="mongodb_ping_duration",
                    value=float(mongo_time),
                    unit="ms",
                )
            except Exception as e:
                get_workflow_logger("factory").error(
                    "MONGODB_CONNECTION_FAILED: Failed to connect to MongoDB",
                    error=str(e),
                )
                raise

            # Workflow discovery (runtime auto-discovery, no upfront imports)
            import_start = datetime.now(UTC)
            import_time = (
                (datetime.now(UTC) - import_start).total_seconds() * 1000
            )
            performance_logger.info(
                "workflow_import_duration",
                metric_name="workflow_import_duration",
                value=float(import_time),
                unit="ms",
            )

            # Registry timing (placeholder; component system is event-driven)
            registry_start = datetime.now(UTC)
            registry_time = (
                (datetime.now(UTC) - registry_start).total_seconds() * 1000
            )
            performance_logger.info(
                "unified_registry_init_duration",
                metric_name="unified_registry_init_duration",
                value=float(registry_time),
                unit="ms",
            )

            from mozaiksai.kernel.workflow_manager import workflow_status_summary

            status = workflow_status_summary()

            # Declared startup services
            try:
                runtime_services = await start_declared_services()
                app.state._runtime_services = runtime_services
            except Exception as _svc_err:
                wf_logger.debug(
                    f"RUNTIME_EXTENSIONS_SERVICES_NOT_STARTED: {_svc_err}"
                )
                app.state._runtime_services = []

            # Platform hooks (mount platform routes, init managers)
            from mozaiksai.runtime.extensions.platform_hooks import get_platform_hooks

            try:
                await get_platform_hooks().run_startup(app)
            except Exception as _ph_err:
                wf_logger.warning(f"PLATFORM_HOOKS_STARTUP_FAILED: {_ph_err}")

            # Total startup time
            total_startup_time = (
                (datetime.now(UTC) - startup_start).total_seconds() * 1000
            )
            performance_logger.info(
                "total_startup_duration",
                metric_name="total_startup_duration",
                value=float(total_startup_time),
                unit="ms",
                workflows_count=status.get("total_workflows", 0),
                tools_count=status.get("total_tools", 0),
            )

            await event_dispatcher.emit_business_event(
                log_event_type="SERVER_STARTUP_COMPLETED",
                description="Server startup completed successfully with unified event dispatcher",
                context={
                    "environment": _ENV,
                    "startup_time_ms": total_startup_time,
                    "workflows_registered": status.get("total_workflows", 0),
                    "tools_available": status.get("total_tools", 0),
                    "summary": status.get("summary", "Unknown"),
                },
            )
            wf_logger.info(
                f"Server ready - {status['summary']} (Startup: {total_startup_time:.1f}ms)"
            )
        except Exception as e:
            startup_time = (
                (datetime.now(UTC) - startup_start).total_seconds() * 1000
            )
            get_workflow_logger("factory").error(
                "SERVER_STARTUP_FAILED: Server startup failed",
                environment=_ENV,
                error=str(e),
                startup_time_ms=startup_time,
            )
            raise

    # -- lifecycle: shutdown ------------------------------------------------
    @app.on_event("shutdown")
    async def shutdown():
        shutdown_start = datetime.now(UTC)
        wf_logger.info("Shutting down server...")

        try:
            runtime_services = getattr(app.state, "_runtime_services", [])
            if runtime_services:
                try:
                    await stop_services(runtime_services)
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            app.state._runtime_services = []

            mongo_client = getattr(app.state, "mongo_client", None)
            if mongo_client:
                mongo_client.close()

            shutdown_time = (
                (datetime.now(UTC) - shutdown_start).total_seconds() * 1000
            )
            performance_logger.info(
                "shutdown_duration",
                extra={
                    "metric_name": "shutdown_duration",
                    "value": float(shutdown_time),
                    "unit": "ms",
                },
            )
            get_workflow_logger("factory").info(
                "SERVER_SHUTDOWN_COMPLETED: Server shutdown completed successfully",
                shutdown_time_ms=shutdown_time,
            )
            wf_logger.info(f"Shutdown complete ({shutdown_time:.1f}ms)")
        except Exception as e:
            shutdown_time = (
                (datetime.now(UTC) - shutdown_start).total_seconds() * 1000
            )
            get_workflow_logger("factory").error(
                "SERVER_SHUTDOWN_FAILED: Error during server shutdown",
                error=str(e),
                shutdown_time_ms=shutdown_time,
            )

    return app

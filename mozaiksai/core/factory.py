"""mozaiksai.core.factory — Application factory for mozaiks.

Public entry-point for both mozaiks-platform and self-hosted deployments:

    from mozaiks.core import create_app
    from mozaiks.orchestration import create_ai_workflow_runner

    app = create_app(ai_engine=create_ai_workflow_runner())

The factory wires up:
  • FastAPI instance with metadata
  • CORS middleware (env-driven)
  • Principal-header enforcement middleware
  • Persistence manager  (``app.state.persistence_manager``)
  • SimpleTransport       (``app.state.simple_transport``, on startup)
  • Runtime extensions     (workflow-declared APIRouters)
  • Event dispatcher
  • Performance manager
  • Startup / shutdown lifecycle

Platform or self-hosted callers may register additional workflows, routers,
or middleware on the returned ``app`` before handing it to uvicorn.

NOTE:
  ``shared_app.py`` (root of the mozaiks repo) still contains ~1 300 lines of
  inline route definitions for health, chat, websocket, sessions, etc.
  Those will be extracted into ``mozaiksai.core.routes`` as part of the
  "route extraction" milestone.  *Until then*, running the core standalone
  should use ``shared_app:app`` directly.  This factory is primarily
  intended for **platform / self-hosted** apps that compose on top.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware


# ---------------------------------------------------------------------------
# Principal-header enforcement
# ---------------------------------------------------------------------------

class _PrincipalHeaderMiddleware(BaseHTTPMiddleware):
    """Reject requests that are missing x-app-id / x-user-id headers.

    Requests to /docs, /openapi.json, /health, etc. are always allowed.
    """

    SKIP_PREFIXES = ("/docs", "/openapi.json", "/redoc", "/health", "/metrics", "/favicon")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if any(path.startswith(p) for p in self.SKIP_PREFIXES):
            return await call_next(request)

        # For API paths that embed app_id / user_id, headers are optional
        # (path params take precedence).  For everything else, require them.
        app_id = request.headers.get("x-app-id") or request.path_params.get("app_id")
        user_id = request.headers.get("x-user-id") or request.path_params.get("user_id")

        if not app_id:
            return Response("Missing x-app-id header", status_code=400)

        # Store resolved principal on request state for downstream handlers
        request.state.app_id = app_id
        request.state.user_id = user_id or "anonymous"
        return await call_next(request)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_app(
    *,
    ai_engine: Optional[Callable[..., Any]] = None,
    title: str = "MozaiksAI Runtime",
    description: str = "Production-ready AG2 runtime with workflow-specific tools",
    version: str = "1.0.0",
    enable_principal_middleware: bool = True,
    **fastapi_kwargs: Any,
) -> FastAPI:
    """Create and return a fully-configured FastAPI application.

    Parameters
    ----------
    ai_engine : callable, optional
        The workflow orchestration runner returned by
        ``create_ai_workflow_runner()``.  Stored on ``app.state.ai_engine``
        so route-handlers can access it.
    title, description, version : str
        Passed through to ``FastAPI(…)``.
    enable_principal_middleware : bool
        When *True* (default) the ``x-app-id`` / ``x-user-id`` header
        enforcement middleware is added.
    **fastapi_kwargs
        Additional keyword arguments forwarded to ``FastAPI(…)``.

    Returns
    -------
    FastAPI
    """

    # -- app instance -------------------------------------------------------
    app = FastAPI(
        title=title,
        description=description,
        version=version,
        **fastapi_kwargs,
    )

    # -- CORS ---------------------------------------------------------------
    react_origin = os.environ.get("REACT_DEV_ORIGIN")
    if react_origin:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[react_origin],
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

    # -- principal enforcement middleware -----------------------------------
    if enable_principal_middleware:
        app.add_middleware(_PrincipalHeaderMiddleware)

    # -- core singletons (lazy-imported to avoid circular deps) -------------
    from mozaiksai.core.data import AG2PersistenceManager
    from mozaiksai.core.events import get_event_dispatcher

    persistence_manager = AG2PersistenceManager()
    event_dispatcher = get_event_dispatcher()

    app.state.persistence_manager = persistence_manager
    app.state.event_dispatcher = event_dispatcher
    app.state.ai_engine = ai_engine

    # -- runtime extensions (routers declared in workflow YAML) -------------
    from mozaiksai.core.runtime import mount_declared_routers

    mount_declared_routers(app)

    # -- lifecycle: startup -------------------------------------------------
    @app.on_event("startup")
    async def _startup() -> None:
        from mozaiksai.core.observability import get_performance_manager
        from mozaiksai.core.transport import SimpleTransport
        from mozaiksai.core.runtime import start_declared_services

        # Performance manager
        perf = get_performance_manager()
        app.state.performance_manager = perf

        # WebSocket transport
        transport = SimpleTransport()
        app.state.simple_transport = transport

        # MongoDB connectivity check
        try:
            await persistence_manager.ping()
        except Exception:
            pass  # logged internally by persistence_manager

        # Declared services from workflow YAML
        services = await start_declared_services()
        app.state._runtime_services = services

    # -- lifecycle: shutdown ------------------------------------------------
    @app.on_event("shutdown")
    async def _shutdown() -> None:
        from mozaiksai.core.runtime import stop_services

        await stop_services(getattr(app.state, "_runtime_services", []))
        try:
            await persistence_manager.close()
        except Exception:
            pass

    # -- minimal health endpoint --------------------------------------------
    @app.get("/health", tags=["system"])
    async def health() -> dict:
        return {"status": "ok"}

    return app

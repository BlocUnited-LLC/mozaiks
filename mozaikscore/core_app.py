# ==============================================================================
# FILE: mozaikscore/core_app.py
# DESCRIPTION: FastAPI application for the mozaikscore substrate.
#              CORS, middleware, lifecycle events, router mounting.
#              Runs on port 8001 (mozaiksai runs on 8000).
# ==============================================================================
import os
import time
import logging
import traceback

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette import status

logger = logging.getLogger("mozaikscore")

# ===========================================================================
# App identity
# ===========================================================================
APP_ID = os.getenv("MOZAIKS_APP_ID", "").strip()
ENV = os.getenv("ENV", "development")

if not APP_ID and ENV == "production":
    raise RuntimeError("MOZAIKS_APP_ID must be set in production")
elif not APP_ID:
    APP_ID = "dev_app"
    logger.warning("MOZAIKS_APP_ID not set, using default: %s", APP_ID)

# ===========================================================================
# FastAPI application
# ===========================================================================
app = FastAPI(
    title=f"MozaiksCore ({APP_ID})",
    description="Application services substrate — CRUD, settings, subscriptions, modules.",
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
def _parse_csv_origins(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [o.strip() for o in raw.split(",") if o and o.strip()]


cors_origins: list[str] = []
cors_origins.extend(_parse_csv_origins(os.getenv("FRONTEND_URL")))
cors_origins.extend(_parse_csv_origins(os.getenv("REACT_DEV_ORIGIN")))
cors_origins.extend(_parse_csv_origins(os.getenv("ADDITIONAL_CORS_ORIGINS")))

if ENV != "production":
    cors_origins.extend(
        [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3001",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )

# De-dupe while preserving order
_seen = set()
cors_origins = [o for o in cors_origins if not (o in _seen or _seen.add(o))]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Mount director router (core CRUD routes)
# ---------------------------------------------------------------------------
from mozaikscore.core.director import router as director_router  # noqa: E402

app.include_router(director_router)

# ---------------------------------------------------------------------------
# Mount admin and API route modules
# ---------------------------------------------------------------------------
from mozaikscore.core.routes import (  # noqa: E402
    admin_users_router,
    notifications_router,
    notifications_admin_router,
    analytics_router,
    status_router,
    app_metadata_router,
    push_subscriptions_router,
    events_router,
    subscription_sync_router,
    theme_router,
    settings_router,
    profile_router,
    modules_router,
    subscriptions_router,
)

app.include_router(admin_users_router)
app.include_router(notifications_router)
app.include_router(notifications_admin_router)
app.include_router(analytics_router)
app.include_router(status_router)
app.include_router(app_metadata_router)
app.include_router(push_subscriptions_router)
app.include_router(events_router)
app.include_router(subscription_sync_router)
app.include_router(theme_router)
app.include_router(settings_router)
app.include_router(profile_router)
app.include_router(modules_router)
app.include_router(subscriptions_router)

# Cross-substrate event relay (mozaiksai → mozaikscore inbound)
from mozaikscore.core.cross_substrate_bridge import router as relay_router  # noqa: E402

app.include_router(relay_router)


# ---------------------------------------------------------------------------
# Request logging middleware
# ---------------------------------------------------------------------------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    t0 = time.time()
    req_id = str(hash(f"{t0}:{request.client.host}"))[0:8]
    path = request.url.path
    method = request.method
    logger.info("[%s] %s %s", req_id, method, path)
    try:
        response = await call_next(request)
        elapsed = time.time() - t0
        response.headers["X-Process-Time"] = str(elapsed)
        logger.info("[%s] %s %s -> %d (%.3fs)", req_id, method, path, response.status_code, elapsed)
        return response
    except Exception as exc:
        elapsed = time.time() - t0
        logger.error("[%s] %s %s ERROR (%.3fs): %s", req_id, method, path, elapsed, exc)
        raise


# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception: %s", exc)
    logger.error(traceback.format_exc())
    from mozaikscore.core.event_bus import event_bus

    event_bus.publish("api_error", {"path": str(request.url), "method": request.method, "error": str(exc)})
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal server error occurred."},
    )


# ---------------------------------------------------------------------------
# WebSocket endpoint — real-time push to connected clients
# ---------------------------------------------------------------------------
@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    from mozaikscore.core.websocket_manager import websocket_manager

    # Authenticate the WebSocket connection using the shared mozaiksai auth
    try:
        from mozaiksai.core.auth.websocket_auth import authenticate_websocket
        ws_user = await authenticate_websocket(websocket)
        if ws_user is None:
            return  # Connection already closed with 1008 by authenticate_websocket

        # Verify the authenticated user matches the requested user_id
        if ws_user.user_id != user_id:
            await websocket.close(code=1008, reason="User ID mismatch")
            return
    except ImportError:
        # Dev fallback: no mozaiksai auth available — accept as-is
        if ENV != "production":
            logger.warning("WebSocket auth unavailable — accepting unauthenticated connection (dev mode)")
            await websocket.accept()
        else:
            await websocket.close(code=1008, reason="Auth service unavailable")
            return

    websocket_manager.active_connections.setdefault(user_id, []).append(websocket)
    logger.info("User %s connected via WebSocket (authenticated)", user_id)
    try:
        while True:
            # Keep connection alive; client can send pings or commands
            data = await websocket.receive_text()
            # Echo-ack so client knows connection is live
            await websocket.send_json({"type": "ack", "data": data})
    except WebSocketDisconnect:
        websocket_manager.disconnect(user_id, websocket)
        logger.info("WebSocket disconnected for user %s", user_id)
    except Exception as exc:
        logger.error("WebSocket error for user %s: %s", user_id, exc)
        websocket_manager.disconnect(user_id, websocket)


# ---------------------------------------------------------------------------
# Lifecycle events
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    logger.info("Starting MozaiksCore (%s)", APP_ID)

    from mozaikscore.core.automation_nats import (
        get_substrate_event_nats_publisher,
        use_nats_transport,
    )
    from mozaikscore.core.database import verify_connection, initialize_database
    from mozaikscore.core.module_manager import module_manager
    from mozaikscore.core.notifications_manager import create_notification_indexes
    from mozaikscore.core.event_bus import event_bus
    from mozaikscore.core.websocket_event_bridge import register_websocket_events
    from mozaikscore.core.cross_substrate_bridge import register_outbound_relay

    await verify_connection()
    await initialize_database()
    await module_manager.load_modules()
    await create_notification_indexes()
    await event_bus.start_background_processing()
    if use_nats_transport():
        await get_substrate_event_nats_publisher().start()
    register_websocket_events()
    register_outbound_relay()

    logger.info("MozaiksCore startup complete — %d modules loaded", len(module_manager.modules))


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down MozaiksCore (%s)", APP_ID)

    from mozaikscore.core.automation_nats import (
        get_substrate_event_nats_publisher,
        use_nats_transport,
    )
    from mozaikscore.core.state_manager import state_manager
    from mozaikscore.core.database import db_cache
    from mozaikscore.core.notifications_manager import notifications_manager
    from mozaikscore.core.event_bus import event_bus

    await event_bus.stop_background_processing()
    if use_nats_transport():
        await get_substrate_event_nats_publisher().stop()
    await notifications_manager.stop_background_processing()
    state_manager.clear()
    if hasattr(db_cache, "clear"):
        db_cache.clear()

    logger.info("MozaiksCore shutdown complete")

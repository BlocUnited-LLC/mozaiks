"""Transport route modules.

Each sub-module exposes an ``APIRouter`` named ``router`` which the
application factory includes at startup.
"""

from mozaiksai.transport.routes.health_routes import router as health_router
from mozaiksai.transport.routes.upload_routes import router as upload_router
from mozaiksai.transport.routes.chat_routes import router as chat_router
from mozaiksai.transport.routes.session_routes import router as session_router
from mozaiksai.transport.routes.workflow_routes import router as workflow_router
from mozaiksai.transport.routes.ws_routes import router as ws_router
from mozaiksai.transport.routes.input_routes import router as input_router

ALL_ROUTERS = [
    health_router,
    upload_router,
    chat_router,
    session_router,
    workflow_router,
    ws_router,
    input_router,
]

__all__ = [
    "health_router",
    "upload_router",
    "chat_router",
    "session_router",
    "workflow_router",
    "ws_router",
    "input_router",
    "ALL_ROUTERS",
]

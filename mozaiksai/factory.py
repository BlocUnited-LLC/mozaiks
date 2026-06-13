"""Convenience factory for an embeddable runtime-only mozaiksai app.

This factory is useful for isolated runtime embeddings, smoke scripts, and
tests. In the canonical architecture, the preferred host entrypoints are in
``mozaiksai.hosts``:

- ``mozaiksai.hosts.runtime``   — runtime substrate host
- ``mozaiksai.hosts.platform``  — headless app host
- ``mozaiksai.hosts.studio``    — local/private Studio management host

Use ``create_mozaiks_app()`` when you explicitly want only the runtime substrate
as a mountable FastAPI sub-application.
"""

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from mozaiksai.version import __version__

logger = logging.getLogger(__name__)


class MozaiksConfig(BaseModel):
    """Configuration for the runtime-substrate convenience factory."""
    workflow_dir: str = Field(default="./workflows", description="Path to workflows directory")
    mongo_uri: str | None = Field(default=None, description="MongoDB connection URI")
    cors_origins: list = Field(default=["*"], description="Allowed CORS origins")
    debug: bool = Field(default=False, description="Enable debug mode")


def create_mozaiks_app(
    workflow_dir: str = "./workflows",
    mongo_uri: str | None = None,
    cors_origins: list | None = None,
    debug: bool = False,
    **kwargs: Any,
) -> FastAPI:
    """
    Create a mountable runtime-substrate FastAPI application.

    Args:
        workflow_dir: Path to the workflows directory containing workflow definitions
        mongo_uri: MongoDB connection URI (defaults to MONGO_URI env var)
        cors_origins: List of allowed CORS origins (defaults to ["*"])
        debug: Enable debug logging

    Returns:
        FastAPI application that can be mounted on another app

    Example:
        ```python
        from fastapi import FastAPI
        from mozaiksai import create_mozaiks_app

        app = FastAPI()
        app.mount("/ai", create_mozaiks_app(workflow_dir="./workflows"))
        ```

    Note:
        This is a convenience factory for the runtime layer only. For the
        canonical host entrypoints, import from ``mozaiksai.hosts`` or
        use ``mozaiks serve`` from the CLI.
    """
    # Resolve paths
    workflow_path = Path(workflow_dir).resolve()
    if not workflow_path.exists():
        logger.warning(f"Workflow directory does not exist: {workflow_path}")

    # Set environment variables for the runtime
    os.environ.setdefault("WORKFLOW_DIR", str(workflow_path))
    if mongo_uri:
        os.environ["MONGO_URI"] = mongo_uri

    # Create the runtime-only FastAPI sub-application
    runtime_subapp = FastAPI(
        title="mozaiksai Runtime Substrate",
        description="Convenience runtime-only sub-application",
        version=__version__,
        docs_url="/docs" if debug else None,
        redoc_url="/redoc" if debug else None,
    )

    # CORS
    runtime_subapp.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Import routes after env vars are set
    # This deferred import ensures the runtime picks up the configuration
    from mozaiksai.core.core_config import close_mongo_client
    from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
    from mozaiksai.core.multitenant import coalesce_app_id
    from mozaiksai.core.transport.simple_transport import SimpleTransport
    from mozaiksai.core.workflow.workflow_manager import workflow_status_summary

    # Initialize persistence
    persistence_manager = AG2PersistenceManager()
    transport = SimpleTransport()
    # Ensure orchestration paths that call SimpleTransport.get_instance() use the
    # same transport object as the mounted app websocket endpoint.
    SimpleTransport._instance = transport

    # Store references on app state
    runtime_subapp.state.persistence = persistence_manager
    runtime_subapp.state.transport = transport
    runtime_subapp.state.workflow_dir = workflow_path

    @runtime_subapp.on_event("shutdown")
    async def _shutdown_runtime_subapp() -> None:
        """Release process-scoped runtime resources for smoke/tests."""
        SimpleTransport._instance = None
        close_mongo_client()

    # ----- Health endpoint -----
    @runtime_subapp.get("/health")
    async def health():
        return {"status": "ok", "service": "mozaiksai"}

    # ----- Workflow info endpoint -----
    @runtime_subapp.get("/workflows")
    async def list_workflows():
        """List available workflows."""
        try:
            summary = workflow_status_summary()
            return {"workflows": summary}
        except Exception as e:
            logger.error(f"Failed to list workflows: {e}")
            return {"workflows": [], "error": str(e)}

    # ----- Chat start endpoint -----
    class StartChatRequest(BaseModel):
        user_id: str
        context: dict[str, Any] | None = None

    @runtime_subapp.post("/chats/{app_id}/{workflow_name}/start")
    async def start_chat(
        app_id: str,
        workflow_name: str,
        body: StartChatRequest,
    ):
        """Start a new chat session for a workflow."""
        from datetime import UTC, datetime
        from uuid import uuid4

        chat_id = str(uuid4())
        resolved_app_id = coalesce_app_id(app_id)

        try:
            coll = await persistence_manager._coll()
            now = datetime.now(UTC)

            doc = {
                "chat_id": chat_id,
                "app_id": resolved_app_id,
                "user_id": body.user_id,
                "workflow_name": workflow_name,
                "status": 0,
                "created_at": now,
                "updated_at": now,
            }

            if body.context:
                doc["initial_context"] = body.context

            await coll.insert_one(doc)

            return {
                "success": True,
                "chat_id": chat_id,
                "workflow_name": workflow_name,
                "app_id": resolved_app_id,
                "websocket_url": f"/ws/{workflow_name}/{resolved_app_id}/{chat_id}/{body.user_id}",
            }
        except Exception as e:
            logger.error(f"Failed to start chat: {e}")
            raise HTTPException(status_code=500, detail=str(e)) from e

    # ----- WebSocket endpoint -----
    @runtime_subapp.websocket("/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}")
    async def websocket_endpoint(
        websocket: WebSocket,
        workflow_name: str,
        app_id: str,
        chat_id: str,
        user_id: str,
    ):
        """WebSocket endpoint for real-time chat communication."""
        await transport.handle_websocket(
            websocket=websocket,
            workflow_name=workflow_name,
            app_id=app_id,
            chat_id=chat_id,
            user_id=user_id,
        )

    logger.info("mozaiksai runtime substrate created with workflow_dir=%s", workflow_path)

    return runtime_subapp

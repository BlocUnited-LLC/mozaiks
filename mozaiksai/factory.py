"""Convenience factory for an embeddable runtime-only mozaiksai app.

This factory is useful for isolated runtime embeddings, smoke scripts, and
tests. In the canonical repo architecture, the preferred host entrypoints are:

- `runtime_app.py`   - runtime substrate host
- `platform_app.py`  - headless app host
- `studio_app.py`    - local/private builder host
- `mozaiks_app.py`   - hosted product host

Use `create_mozaiks_app()` when you explicitly want only the runtime substrate
as a mountable FastAPI sub-application.
"""

import os
import logging
from typing import Optional, Dict, Any
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class MozaiksConfig(BaseModel):
    """Configuration for the runtime-substrate convenience factory."""
    workflow_dir: str = Field(default="./workflows", description="Path to workflows directory")
    mongo_uri: Optional[str] = Field(default=None, description="MongoDB connection URI")
    cors_origins: list = Field(default=["*"], description="Allowed CORS origins")
    debug: bool = Field(default=False, description="Enable debug mode")


def create_mozaiks_app(
    workflow_dir: str = "./workflows",
    mongo_uri: Optional[str] = None,
    cors_origins: Optional[list] = None,
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
        canonical four-host repo entrypoints, use the root modules
        `runtime_app.py`, `platform_app.py`, `studio_app.py`, or
        `mozaiks_app.py`.
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
        version="1.0.0",
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
    from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
    from mozaiksai.core.transport.simple_transport import SimpleTransport
    from mozaiksai.core.workflow.workflow_manager import workflow_status_summary
    from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id
    from mozaiksai.trigger import trigger_workflow

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
        context: Optional[Dict[str, Any]] = None

    @runtime_subapp.post("/chats/{app_id}/{workflow_name}/start")
    async def start_chat(
        app_id: str,
        workflow_name: str,
        body: StartChatRequest,
    ):
        """Start a new chat session for a workflow."""
        from uuid import uuid4
        from datetime import datetime, UTC

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
            raise HTTPException(status_code=500, detail=str(e))

    # ----- Trigger endpoint -----
    class TriggerRequest(BaseModel):
        user_id: str
        context: Optional[Dict[str, Any]] = None
        app_id: Optional[str] = None

    @runtime_subapp.post("/workflows/{workflow_name}/trigger")
    async def trigger_workflow_endpoint(
        workflow_name: str,
        body: TriggerRequest,
    ):
        """Trigger a workflow programmatically (for webhooks, backend events, etc.)."""
        result = await trigger_workflow(
            workflow_name=workflow_name,
            user_id=body.user_id,
            context=body.context,
            app_id=body.app_id,
        )

        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))

        return result

    # ----- Internal trigger endpoint -----
    # Called by platform-hosted or external/generated app backends when a
    # domain event should trigger a workflow.
    # Protected by X-Internal-Api-Key header (set INTERNAL_API_KEY env var).
    class InternalTriggerRequest(BaseModel):
        workflow_name: str
        user_id: str
        app_id: Optional[str] = None
        context: Optional[Dict[str, Any]] = None

    def _validate_internal_key(key: Optional[str]) -> bool:
        expected = os.getenv("INTERNAL_API_KEY", "").strip()
        if not expected:
            # No key configured — allow in dev, but warn
            logger.warning(
                "INTERNAL_API_KEY not set; /internal/trigger is open. "
                "Set INTERNAL_API_KEY in production."
            )
            return True
        import hmac
        return bool(key) and hmac.compare_digest(expected.encode(), key.encode())

    @runtime_subapp.post("/internal/trigger")
    async def internal_trigger(
        body: InternalTriggerRequest,
        x_internal_api_key: Optional[str] = Header(None, alias="X-Internal-Api-Key"),
    ):
        """
        Internal endpoint for app-domain event ingress.

        Called when an app-domain event should start or resume a workflow.

        Authentication: X-Internal-Api-Key header must match INTERNAL_API_KEY env var.
        If INTERNAL_API_KEY is unset the endpoint is open (development mode).
        """
        if not _validate_internal_key(x_internal_api_key):
            raise HTTPException(status_code=401, detail="Invalid internal API key")

        result = await trigger_workflow(
            workflow_name=body.workflow_name,
            user_id=body.user_id,
            context=body.context,
            app_id=body.app_id,
        )

        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))

        logger.info(
            "Internal trigger: workflow=%s user=%s app=%s",
            body.workflow_name, body.user_id, body.app_id,
        )
        return result

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

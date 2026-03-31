"""
Programmatic workflow trigger for backend code.

Usage:
    from mozaiksai import trigger_workflow

    # In your backend code
    @router.post("/orders")
    async def create_order(order: Order):
        saved = await db.orders.insert_one(order.dict())

        # Trigger workflow for follow-up
        await trigger_workflow(
            workflow_name="CustomerSupport",
            user_id=order.user_id,
            context={"order_id": str(saved.inserted_id), "trigger": "new_order"}
        )

        return saved
"""

import os
import logging
from uuid import uuid4
from datetime import datetime, UTC
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


async def trigger_workflow(
    workflow_name: str,
    user_id: str,
    context: Optional[Dict[str, Any]] = None,
    app_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Programmatically trigger a workflow from backend code.

    This creates a new chat session for the specified workflow and user.
    The workflow will start according to its `workflow_startup_mode`:
    - UserDriven: Waits for user input
    - AgentDriven: Starts immediately with initial_message

    Args:
        workflow_name: Name of the workflow to trigger (e.g., "CustomerSupport")
        user_id: User ID for the session
        context: Optional context variables to pass to the workflow
        app_id: Optional app ID (defaults to DEFAULT_APP_ID env var or "default")

    Returns:
        dict with:
            - success: bool
            - chat_id: str (if successful)
            - workflow_name: str
            - error: str (if failed)

    Example:
        result = await trigger_workflow(
            workflow_name="CustomerSupport",
            user_id="user_123",
            context={"order_id": "order_456", "issue": "refund_request"}
        )

        if result["success"]:
            print(f"Started workflow with chat_id: {result['chat_id']}")
    """
    # Import here to avoid circular imports
    from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
    from mozaiksai.core.multitenant import coalesce_app_id

    resolved_app_id = app_id or os.getenv("DEFAULT_APP_ID", "default")
    resolved_app_id = coalesce_app_id(resolved_app_id)

    try:
        persistence = AG2PersistenceManager()
        chat_id = str(uuid4())

        # Get the underlying collection
        coll = await persistence._coll()

        # Create the chat session document
        now = datetime.now(UTC)
        doc = {
            "chat_id": chat_id,
            "app_id": resolved_app_id,
            "user_id": user_id,
            "workflow_name": workflow_name,
            "status": 0,  # in_progress
            "created_at": now,
            "updated_at": now,
            "trigger_source": "backend",  # Mark as programmatic trigger
        }

        # Store initial context if provided
        if context:
            doc["initial_context"] = context

        await coll.insert_one(doc)

        logger.info(
            "WORKFLOW_TRIGGERED: Programmatic workflow trigger",
            extra={
                "workflow_name": workflow_name,
                "user_id": user_id,
                "app_id": resolved_app_id,
                "chat_id": chat_id,
                "has_context": bool(context),
            }
        )

        return {
            "success": True,
            "chat_id": chat_id,
            "workflow_name": workflow_name,
            "app_id": resolved_app_id,
        }

    except Exception as e:
        logger.error(
            "WORKFLOW_TRIGGER_FAILED: Failed to trigger workflow",
            extra={
                "workflow_name": workflow_name,
                "user_id": user_id,
                "error": str(e),
            }
        )
        return {
            "success": False,
            "error": str(e),
            "workflow_name": workflow_name,
        }

# ==============================================================================
# FILE: mozaiksai/core/transport/webhook_dispatcher.py
# DESCRIPTION: Dispatches webhook callbacks when workflows complete.
#              Used for backend-to-backend integration where the caller
#              needs to be notified of completion without maintaining
#              a WebSocket connection.
# ==============================================================================
"""Webhook dispatcher for workflow completion notifications."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class WebhookPayload:
    """Payload sent to webhook URL on workflow completion."""

    event_type: str
    chat_id: str
    run_id: str
    workflow_name: str
    app_id: str
    user_id: str
    status: str  # "completed", "failed", "cancelled"
    completed_at: str
    executed_agents: list = field(default_factory=list)
    final_context: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "chat_id": self.chat_id,
            "run_id": self.run_id,
            "workflow_name": self.workflow_name,
            "app_id": self.app_id,
            "user_id": self.user_id,
            "status": self.status,
            "completed_at": self.completed_at,
            "executed_agents": self.executed_agents,
            "final_context": self.final_context,
            "error": self.error,
        }


class WebhookDispatcher:
    """
    Dispatches webhook callbacks for workflow events.

    Usage:
        dispatcher = WebhookDispatcher()
        await dispatcher.notify_completion(
            webhook_url="https://your-service.com/webhook",
            chat_id="...",
            workflow_name="AppGenerator",
            ...
        )
    """

    def __init__(
        self,
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ):
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    async def notify_completion(
        self,
        webhook_url: str,
        chat_id: str,
        workflow_name: str,
        app_id: str,
        user_id: str,
        status: str = "completed",
        executed_agents: list | None = None,
        final_context: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> bool:
        """
        Send completion notification to webhook URL.

        Args:
            webhook_url: URL to POST the completion payload
            chat_id: The chat/session ID
            workflow_name: Name of the workflow that completed
            app_id: Application ID
            user_id: User ID
            status: Completion status ("completed", "failed", "cancelled")
            executed_agents: List of agents that executed
            final_context: Final context variables (optional)
            error: Error message if status is "failed"

        Returns:
            True if webhook was delivered successfully
        """
        payload = WebhookPayload(
            event_type="workflow.completed",
            chat_id=chat_id,
            run_id=chat_id,  # run_id is alias for chat_id
            workflow_name=workflow_name,
            app_id=app_id,
            user_id=user_id,
            status=status,
            completed_at=datetime.now(UTC).isoformat(),
            executed_agents=executed_agents or [],
            final_context=final_context,
            error=error,
        )

        return await self._send_webhook(webhook_url, payload)

    async def _send_webhook(
        self,
        url: str,
        payload: WebhookPayload,
    ) -> bool:
        """Send webhook with retry logic."""

        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        url,
                        json=payload.to_dict(),
                        headers={
                            "Content-Type": "application/json",
                            "X-Mozaiks-Event": payload.event_type,
                            "X-Mozaiks-Chat-Id": payload.chat_id,
                        },
                    )

                    if response.status_code < 300:
                        logger.info(
                            "Webhook delivered successfully to %s (chat_id=%s, status=%s)",
                            url, payload.chat_id, response.status_code)
                        return True

                    logger.warning(
                        "Webhook delivery failed to %s (status=%s, attempt=%s/%s)",
                        url, response.status_code, attempt + 1, self.max_retries)

            except httpx.TimeoutException:
                logger.warning(
                    "Webhook timeout to %s (attempt=%s/%s)", url, attempt + 1, self.max_retries)
            except Exception as e:
                logger.error(
                    "Webhook error to %s: %s (attempt=%s/%s)", url, e, attempt + 1, self.max_retries)

            # Wait before retry (except on last attempt)
            if attempt < self.max_retries - 1:
                await asyncio.sleep(self.retry_delay * (attempt + 1))

        logger.error(
            "Webhook delivery failed after %s attempts to %s (chat_id=%s)",
            self.max_retries, url, payload.chat_id)
        return False


# Singleton instance
_dispatcher: WebhookDispatcher | None = None


def get_webhook_dispatcher() -> WebhookDispatcher:
    """Get or create the webhook dispatcher singleton."""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = WebhookDispatcher()
    return _dispatcher


async def dispatch_completion_webhook(
    chat_id: str,
    workflow_name: str,
    app_id: str,
    user_id: str,
    webhook_url: str | None = None,
    status: str = "completed",
    executed_agents: list | None = None,
    final_context: dict[str, Any] | None = None,
    error: str | None = None,
) -> bool:
    """
    Convenience function to dispatch a completion webhook.

    If webhook_url is None, attempts to retrieve it from the chat session.
    """
    if not webhook_url:
        # Try to get webhook_url from persistence
        try:
            from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
            pm = AG2PersistenceManager()
            session = await pm.get_chat_session(chat_id)  # type: ignore[attr-defined]
            if session:
                webhook_url = session.get("webhook_url")
        except Exception as e:
            logger.debug("Could not retrieve webhook_url from session: %s", e)

    if not webhook_url:
        logger.debug("No webhook_url configured for chat_id=%s, skipping notification", chat_id)
        return False

    dispatcher = get_webhook_dispatcher()
    return await dispatcher.notify_completion(
        webhook_url=webhook_url,
        chat_id=chat_id,
        workflow_name=workflow_name,
        app_id=app_id,
        user_id=user_id,
        status=status,
        executed_agents=executed_agents,
        final_context=final_context,
        error=error,
    )


__all__ = [
    "WebhookDispatcher",
    "WebhookPayload",
    "get_webhook_dispatcher",
    "dispatch_completion_webhook",
]

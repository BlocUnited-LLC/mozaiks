# ==============================================================================
# FILE: core/workflow/stream/handlers/error_handler.py
# DESCRIPTION: Handler for ErrorEvent
# ==============================================================================

"""
Error Event Handler

Handles AG2 ErrorEvent for workflow error reporting.
Logs errors and forwards them to the UI.
"""

from typing import TYPE_CHECKING, Any, Dict, Optional, Set, Type

from .base import BaseEventHandler

if TYPE_CHECKING:
    from ..context import StreamContext, StreamState

# Import AG2 ErrorEvent
try:
    from autogen.events.agent_events import ErrorEvent
    HAS_ERROR_EVENT = True
except ImportError:
    HAS_ERROR_EVENT = False
    ErrorEvent = type(None)  # type: ignore

from mozaiksai.core.events.event_serialization import (
    serialize_event_content,
    extract_agent_name,
)


class ErrorHandler(BaseEventHandler):
    """
    Handler for ErrorEvent.

    Logs error details and builds error payload for UI notification.
    """

    def event_types(self) -> Set[Type]:
        """Handle ErrorEvent if available."""
        if HAS_ERROR_EVENT:
            return {ErrorEvent}
        return set()

    async def handle(
        self,
        event: Any,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> Optional[Dict[str, Any]]:
        """
        Handle ErrorEvent.

        Logs the error and builds a payload for UI notification.

        Args:
            event: ErrorEvent instance
            ctx: Stream context
            state: Stream state

        Returns:
            error payload for UI
        """
        # Extract error details
        error_message = getattr(event, "message", None) or getattr(event, "content", None)
        error_code = getattr(event, "code", None) or "UNKNOWN_ERROR"
        agent_name = extract_agent_name(event) or state.turn_agent

        # Serialize error content
        try:
            serialized_error = serialize_event_content(error_message)
        except Exception:
            serialized_error = str(error_message)

        # Log error
        ctx.wf_logger.error(
            f" [{ctx.workflow_name_upper}] ErrorEvent: code={error_code} "
            f"agent={agent_name} message={serialized_error}"
        )

        # Build error payload
        return {
            "kind": "error",
            "code": error_code,
            "message": serialized_error,
            "agent": agent_name,
            "chat_id": ctx.chat_id,
            "sequence": state.sequence_counter,
        }

    def should_break(self, event: Any, state: "StreamState") -> bool:
        """ErrorEvent does not terminate the stream by default."""
        return False

    def priority(self) -> int:
        """High priority for error handling."""
        return 20

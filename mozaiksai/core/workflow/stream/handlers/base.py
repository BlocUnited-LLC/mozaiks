# ==============================================================================
# FILE: core/workflow/stream/handlers/base.py
# DESCRIPTION: Abstract base class for AG2 event handlers
# ==============================================================================

"""
Base Event Handler

Defines the interface contract that all AG2 event handlers must implement.
Handlers are responsible for processing specific event types and returning
UI payloads for transport.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, Optional, Set, Type

if TYPE_CHECKING:
    from ..context import StreamContext, StreamState


class BaseEventHandler(ABC):
    """
    Abstract base class for AG2 event handlers.

    Each handler processes a specific set of AG2 event types. The handler
    dispatch system uses event_types() to route events to the correct handler.

    Lifecycle:
        1. Registry calls event_types() to determine which events this handler processes
        2. For each matching event, handle() is called with context and state
        3. Handler returns Optional[Dict] payload to send to UI, or None to suppress
        4. Registry checks should_break() to determine if event loop should terminate

    Example:
        class TextEventHandler(BaseEventHandler):
            def event_types(self) -> Set[Type]:
                return {TextEvent, PrintEvent}

            async def handle(self, event, ctx, state) -> Optional[Dict[str, Any]]:
                # Process event, update state, return UI payload
                return {"kind": "text", "content": event.content, ...}

            def should_break(self, event, state) -> bool:
                return False  # Continue processing
    """

    @abstractmethod
    def event_types(self) -> Set[Type]:
        """
        Return the set of AG2 event types this handler processes.

        The registry uses this to route events to the correct handler.
        A handler can process multiple related event types.

        Returns:
            Set of AG2 event type classes (e.g., {TextEvent, PrintEvent})
        """
        pass

    @abstractmethod
    async def handle(
        self,
        event: Any,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> Optional[Dict[str, Any]]:
        """
        Process an AG2 event and optionally return a UI payload.

        This is the main entry point for event processing. Handlers should:
        1. Extract relevant data from the event
        2. Update state as needed (e.g., record tool calls, update turn)
        3. Perform side effects (persistence, lifecycle triggers)
        4. Build and return a UI payload dict, or None to suppress

        Args:
            event: The AG2 event object to process
            ctx: Immutable stream context with services and configuration
            state: Mutable stream state for tracking progress

        Returns:
            Dict with UI payload (must include "kind" field), or None if event
            should not be sent to UI. Common kinds:
                - "text": Agent message
                - "tool_call": Tool invocation
                - "tool_response": Tool result
                - "input_request": User input prompt
                - "select_speaker": Speaker transition
                - "run_complete": Workflow completion
                - "error": Error event
        """
        pass

    def should_break(self, event: Any, state: "StreamState") -> bool:
        """
        Determine if the event stream should terminate after this event.

        Override this method in handlers that can terminate the stream
        (e.g., RunCompletionEvent, handoff_to_user).

        Args:
            event: The AG2 event that was just processed
            state: Current stream state

        Returns:
            True to break out of event loop, False to continue
        """
        return False

    def priority(self) -> int:
        """
        Return handler priority for ordering when multiple handlers match.

        Lower values = higher priority. Default is 100.
        Override in subclasses that need specific ordering.

        Returns:
            Integer priority value
        """
        return 100


class DefaultEventHandler(BaseEventHandler):
    """
    Fallback handler for unrecognized event types.

    Logs the event and passes through without UI emission.
    Useful for debugging and ensuring no events are silently dropped.
    """

    def event_types(self) -> Set[Type]:
        # Empty set - this handler is used as fallback, not via type matching
        return set()

    async def handle(
        self,
        event: Any,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> Optional[Dict[str, Any]]:
        """Log unhandled event and return None (no UI emission)."""
        event_class = event.__class__.__name__
        ctx.wf_logger.debug(
            f" [{ctx.workflow_name_upper}] Unhandled event type: {event_class}"
        )
        return None

    def should_break(self, event: Any, state: "StreamState") -> bool:
        return False

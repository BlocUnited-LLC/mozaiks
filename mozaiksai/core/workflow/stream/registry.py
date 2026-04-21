# ==============================================================================
# FILE: mozaiksai/core/workflow/stream/registry.py
# DESCRIPTION: Handler registry for AG2 event dispatch
# ==============================================================================

"""
Event Handler Registry

Maps AG2 event types to their handlers and provides dispatch functionality.
Duplicate event registrations fail fast so the dispatch contract stays explicit.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional, Type

from .handlers.base import BaseEventHandler, DefaultEventHandler

if TYPE_CHECKING:
    from .context import StreamContext, StreamState

logger = logging.getLogger(__name__)


class EventHandlerRegistry:
    """
    Registry mapping AG2 event types to handlers.

    Provides:
        - Handler registration by event type
        - Fallback handler for unknown events
        - Event dispatch with payload collection

    Usage:
        registry = EventHandlerRegistry()
        registry.register(TextEventHandler())
        registry.register(ToolCallHandler())
        registry.set_default_handler(DefaultEventHandler())

        # Dispatch event to appropriate handler
        payload = await registry.dispatch(event, ctx, state)
    """

    def __init__(self):
        # Maps event type -> handler
        self._handlers: Dict[Type, BaseEventHandler] = {}
        # Fallback for unregistered event types
        self._default_handler: Optional[BaseEventHandler] = DefaultEventHandler()

    def register(self, handler: BaseEventHandler) -> "EventHandlerRegistry":
        """
        Register a handler for its declared event types.

        Args:
            handler: Handler instance to register

        Returns:
            Self for method chaining
        """
        event_types = handler.event_types()
        if not event_types:
            raise ValueError(
                f"Handler {handler.__class__.__name__} must declare at least one event type"
            )

        for event_type in event_types:
            existing = self._handlers.get(event_type)
            if existing:
                raise ValueError(
                    f"Duplicate handler registration for {event_type.__name__}: "
                    f"{existing.__class__.__name__} and {handler.__class__.__name__}"
                )

            self._handlers[event_type] = handler
            logger.debug(
                f"Registered {handler.__class__.__name__} for {event_type.__name__}"
            )

        return self

    def set_default_handler(self, handler: BaseEventHandler) -> "EventHandlerRegistry":
        """
        Set the fallback handler for unrecognized event types.

        Args:
            handler: Handler to use for unknown events

        Returns:
            Self for method chaining
        """
        self._default_handler = handler
        return self

    def get_handler(self, event: Any) -> Optional[BaseEventHandler]:
        """
        Get the handler for an event instance.

        Checks exact type match first, then parent classes.

        Args:
            event: AG2 event instance

        Returns:
            Matching handler or default handler
        """
        event_type = type(event)

        # Exact type match
        handler = self._handlers.get(event_type)
        if handler:
            return handler

        # Check parent classes (for inheritance hierarchies)
        for registered_type, h in self._handlers.items():
            if isinstance(event, registered_type):
                return h

        return self._default_handler

    async def dispatch(
        self,
        event: Any,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> Optional[Dict[str, Any]]:
        """
        Dispatch an event to its handler and return the UI payload.

        Args:
            event: AG2 event to process
            ctx: Stream context
            state: Stream state

        Returns:
            UI payload dict from handler, or None if suppressed
        """
        handler = self.get_handler(event)
        if handler:
            try:
                return await handler.handle(event, ctx, state)
            except Exception as e:
                logger.error(
                    f"Handler {handler.__class__.__name__} failed for "
                    f"{event.__class__.__name__}: {e}",
                    exc_info=True,
                )
                try:
                    ctx.wf_logger.warning(
                        f" [{ctx.workflow_name_upper}] Handler {handler.__class__.__name__} failed "
                        f"for {event.__class__.__name__}: {e}"
                    )
                except Exception:
                    pass
                # Don't crash the stream - return None and continue
                return None
        return None

    def should_break(self, event: Any, state: "StreamState") -> bool:
        """
        Check if the event stream should terminate after this event.

        Args:
            event: AG2 event that was just processed
            state: Current stream state

        Returns:
            True if stream should break, False to continue
        """
        handler = self.get_handler(event)
        if handler:
            return handler.should_break(event, state)
        return False

    def clear(self) -> None:
        """Clear all registered handlers."""
        self._handlers.clear()
        self._default_handler = DefaultEventHandler()

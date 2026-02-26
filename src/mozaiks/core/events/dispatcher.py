"""Business event dispatcher — singleton async event bus.

Routes ``subscription.*``, ``notification.*``, ``settings.*``, and
``entitlement.*`` events to registered handlers.

Layer: ``core.events`` (shared runtime).
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class BusinessEventDispatcher:
    """Async in-process event bus for business domain events.

    Usage::

        dispatcher = BusinessEventDispatcher.get_instance()
        dispatcher.register("subscription.limit_reached", my_handler)
        await dispatcher.emit("subscription.limit_reached", {"user_id": "u1", ...})
    """

    _instance: BusinessEventDispatcher | None = None

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    # -- singleton access ---------------------------------------------------

    @classmethod
    def get_instance(cls) -> BusinessEventDispatcher:
        """Return (or create) the process-wide singleton."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Discard the singleton (test helper)."""
        cls._instance = None

    # -- registration -------------------------------------------------------

    def register(self, event_type: str, handler: EventHandler) -> None:
        """Subscribe *handler* to events of *event_type*."""
        self._handlers[event_type].append(handler)
        logger.debug("Registered handler for %s: %s", event_type, handler)

    def unregister(self, event_type: str, handler: EventHandler) -> None:
        """Remove a previously registered handler."""
        try:
            self._handlers[event_type].remove(handler)
        except ValueError:
            pass

    # -- emission -----------------------------------------------------------

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Emit a business event, dispatching to all registered handlers.

        Handlers are invoked concurrently via ``asyncio.gather``.  If a
        handler raises, the exception is logged but does not prevent other
        handlers from executing.
        """
        event = {"event_type": event_type, **payload}

        handlers = self._handlers.get(event_type, [])
        if not handlers:
            logger.debug("No handlers for %s", event_type)
            return

        results = await asyncio.gather(
            *(h(event) for h in handlers),
            return_exceptions=True,
        )
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                logger.error(
                    "Handler %s for %s raised: %s",
                    handlers[i],
                    event_type,
                    result,
                    exc_info=result,
                )

"""EventBus — distributes DomainEvents to subscribers.

The EventBus is the central hub for event distribution. It receives
DomainEvents from the RunSupervisor and forwards them to registered
subscribers (transport, persistence, observability).

Design rules
------------
* Non-blocking event distribution — subscribers should not block the bus
* Fire-and-forget semantics with optional acknowledgment
* Supports filtering by event_type pattern
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from mozaiksai.contracts import DomainEvent

logger = logging.getLogger(__name__)

# Type alias for event handlers
EventHandler = Callable[[DomainEvent], Awaitable[None] | None]


@dataclass
class Subscription:
    """Represents a subscription to the event bus."""

    handler: EventHandler
    pattern: str  # e.g., "agent.*", "run.completed", "*"
    subscriber_id: str
    async_mode: bool = True

    def matches(self, event_type: str) -> bool:
        """Check if this subscription matches the event type.

        Parameters
        ----------
        event_type : str
            The event type to check (e.g., "agent.text").

        Returns
        -------
        bool
            True if this subscription should receive the event.
        """
        if self.pattern == "*":
            return True

        if self.pattern.endswith(".*"):
            prefix = self.pattern[:-2]
            return event_type.startswith(prefix + ".")

        return self.pattern == event_type


class EventBus:
    """Central event distribution hub.

    The EventBus:
    - Receives DomainEvents from producers (RunSupervisor, workers)
    - Distributes events to subscribed handlers
    - Supports pattern-based filtering
    - Handles async and sync handlers

    Usage::

        bus = EventBus()

        # Subscribe to all agent events
        bus.subscribe("agent.*", handler, subscriber_id="transport")

        # Publish an event
        await bus.publish(event)
    """

    def __init__(self):
        """Initialize the event bus."""
        self._subscriptions: list[Subscription] = []
        self._by_pattern: dict[str, list[Subscription]] = defaultdict(list)
        self._stats: dict[str, int] = defaultdict(int)

    def subscribe(
        self,
        pattern: str,
        handler: EventHandler,
        subscriber_id: str,
        async_mode: bool = True,
    ) -> Subscription:
        """Subscribe to events matching a pattern.

        Parameters
        ----------
        pattern : str
            Event type pattern (e.g., "agent.*", "run.completed", "*").
        handler : EventHandler
            Callable to invoke when a matching event is published.
        subscriber_id : str
            Unique identifier for the subscriber.
        async_mode : bool
            If True, handler is awaited. If False, handler is called synchronously.

        Returns
        -------
        Subscription
            The created subscription (can be used to unsubscribe).
        """
        sub = Subscription(
            handler=handler,
            pattern=pattern,
            subscriber_id=subscriber_id,
            async_mode=async_mode,
        )

        self._subscriptions.append(sub)
        self._by_pattern[pattern].append(sub)

        logger.info(
            f"[EVENT_BUS] Subscribed: subscriber={subscriber_id} pattern={pattern}"
        )

        return sub

    def unsubscribe(self, subscription: Subscription) -> None:
        """Remove a subscription.

        Parameters
        ----------
        subscription : Subscription
            The subscription to remove.
        """
        try:
            self._subscriptions.remove(subscription)
            self._by_pattern[subscription.pattern].remove(subscription)
            logger.info(
                f"[EVENT_BUS] Unsubscribed: subscriber={subscription.subscriber_id}"
            )
        except ValueError:
            pass

    def unsubscribe_all(self, subscriber_id: str) -> int:
        """Remove all subscriptions for a subscriber.

        Parameters
        ----------
        subscriber_id : str
            The subscriber to remove.

        Returns
        -------
        int
            Number of subscriptions removed.
        """
        to_remove = [s for s in self._subscriptions if s.subscriber_id == subscriber_id]
        for sub in to_remove:
            self.unsubscribe(sub)
        return len(to_remove)

    async def publish(self, event: DomainEvent) -> int:
        """Publish an event to all matching subscribers.

        Parameters
        ----------
        event : DomainEvent
            The event to publish.

        Returns
        -------
        int
            Number of handlers that received the event.
        """
        event_type = event.event_type
        self._stats["total_published"] += 1
        self._stats[f"published.{event_type}"] += 1

        matching = [s for s in self._subscriptions if s.matches(event_type)]

        if not matching:
            return 0

        # Dispatch to handlers
        tasks = []
        for sub in matching:
            try:
                result = sub.handler(event)
                if sub.async_mode and asyncio.iscoroutine(result):
                    tasks.append(asyncio.create_task(result))
            except Exception as e:
                logger.error(
                    f"[EVENT_BUS] Handler error: subscriber={sub.subscriber_id} "
                    f"event_type={event_type} error={e}"
                )
                self._stats["handler_errors"] += 1

        # Wait for async handlers (with timeout)
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"[EVENT_BUS] Handler timeout: event_type={event_type}"
                )
                self._stats["handler_timeouts"] += 1

        return len(matching)

    async def publish_batch(self, events: list[DomainEvent]) -> int:
        """Publish multiple events.

        Parameters
        ----------
        events : list[DomainEvent]
            Events to publish.

        Returns
        -------
        int
            Total number of handler invocations.
        """
        total = 0
        for event in events:
            total += await self.publish(event)
        return total

    def stats(self) -> dict[str, Any]:
        """Return event bus statistics.

        Returns
        -------
        dict
            Statistics about event distribution.
        """
        return {
            "subscription_count": len(self._subscriptions),
            "patterns": list(self._by_pattern.keys()),
            "stats": dict(self._stats),
        }


# Global event bus instance
_global_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Get the global event bus instance.

    Returns
    -------
    EventBus
        The global event bus.
    """
    global _global_event_bus
    if _global_event_bus is None:
        _global_event_bus = EventBus()
    return _global_event_bus


def reset_event_bus() -> None:
    """Reset the global event bus (for testing)."""
    global _global_event_bus
    _global_event_bus = None


__all__ = [
    "EventBus",
    "EventHandler",
    "Subscription",
    "get_event_bus",
    "reset_event_bus",
]

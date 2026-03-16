# ==============================================================================
# FILE: mozaikscore/core/event_bus.py
# DESCRIPTION: Thread-safe pub/sub event bus with async support, retry logic,
#              event history, and delivery statistics.
# ORIGIN: Migrated from mozaiks-core-public/backend/core/event_bus.py
# ==============================================================================
import logging
import threading
import traceback
import asyncio
import time

logger = logging.getLogger("mozaikscore.event_bus")


class EventBus:
    def __init__(self):
        self.subscribers: dict[str, list] = {}
        self.lock = threading.Lock()
        self.event_history: dict[str, list] = {}
        self.max_history_per_event = 100
        self.max_retry_count = 3

        # Delivery statistics
        self.stats = {
            "events_published": 0,
            "events_delivered": 0,
            "delivery_failures": 0,
            "events_by_type": {},
        }

    # ------------------------------------------------------------------
    # Subscribe / Unsubscribe
    # ------------------------------------------------------------------
    def subscribe(self, event: str, callback):
        with self.lock:
            if event not in self.subscribers:
                self.subscribers[event] = []
            self.subscribers[event].append(callback)
            logger.info("Subscribed '%s' to event '%s'", callback.__name__, event)

    def unsubscribe(self, event: str, callback):
        with self.lock:
            if event in self.subscribers and callback in self.subscribers[event]:
                self.subscribers[event].remove(callback)
                logger.info("Unsubscribed '%s' from event '%s'", callback.__name__, event)
                if not self.subscribers[event]:
                    del self.subscribers[event]

    # ------------------------------------------------------------------
    # Publish (fire-and-forget)
    # ------------------------------------------------------------------
    def publish(self, event: str, data):
        with self.lock:
            self.stats["events_published"] += 1
            self.stats["events_by_type"].setdefault(event, 0)
            self.stats["events_by_type"][event] += 1

            # Record in history
            self.event_history.setdefault(event, [])
            history = self.event_history[event]
            history.append({"timestamp": time.time(), "data": data})
            if len(history) > self.max_history_per_event:
                history.pop(0)

            event_subscribers = self.subscribers.get(event, []).copy()

        if not event_subscribers:
            return

        logger.info("Event '%s' triggered with data: %s", event, data)

        for callback in event_subscribers:
            try:
                if asyncio.iscoroutinefunction(callback):
                    asyncio.create_task(
                        self._process_async_callback(callback, data, event)
                    )
                else:
                    callback(data)
                    with self.lock:
                        self.stats["events_delivered"] += 1
            except Exception as exc:
                with self.lock:
                    self.stats["delivery_failures"] += 1
                logger.error("Error in event callback for '%s': %s", event, exc)
                logger.error(traceback.format_exc())

    # ------------------------------------------------------------------
    # Async callback processing with retry
    # ------------------------------------------------------------------
    async def _process_async_callback(self, callback, data, event_name, retry_count=0):
        try:
            await callback(data)
            with self.lock:
                self.stats["events_delivered"] += 1
        except Exception as exc:
            with self.lock:
                self.stats["delivery_failures"] += 1
            logger.error("Error in async event callback for '%s': %s", event_name, exc)
            logger.error(traceback.format_exc())

            if retry_count < self.max_retry_count:
                retry_delay = 0.5 * (2 ** retry_count)
                logger.info(
                    "Retrying event callback for '%s' in %.1fs (attempt %d)",
                    event_name, retry_delay, retry_count + 1,
                )
                await asyncio.sleep(retry_delay)
                await self._process_async_callback(callback, data, event_name, retry_count + 1)

    # ------------------------------------------------------------------
    # Lifecycle hooks (called from core_app startup/shutdown)
    # ------------------------------------------------------------------
    async def start_background_processing(self):
        """No-op — async callbacks are dispatched inline via create_task."""
        logger.info("EventBus ready (inline dispatch)")

    async def stop_background_processing(self):
        """No-op — nothing to tear down."""
        logger.info("EventBus stopped")

    # ------------------------------------------------------------------
    # Statistics & history
    # ------------------------------------------------------------------
    def get_stats(self) -> dict:
        with self.lock:
            return self.stats.copy()

    def reset_stats(self):
        with self.lock:
            self.stats = {
                "events_published": 0,
                "events_delivered": 0,
                "delivery_failures": 0,
                "events_by_type": {},
            }

    def get_event_history(self, event_type: str | None = None, limit: int = 10) -> dict:
        with self.lock:
            if event_type:
                history = self.event_history.get(event_type, [])
                return {event_type: history[-limit:]}
            return {evt: h[-limit:] for evt, h in self.event_history.items()}


# ---------------------------------------------------------------------------
# Singleton instance
# ---------------------------------------------------------------------------
event_bus = EventBus()


# ---------------------------------------------------------------------------
# Default event handlers
# ---------------------------------------------------------------------------
def _log_subscription_change(data):
    logger.info("User %s changed subscription to '%s'", data.get("user_id"), data.get("plan"))


def _log_theme_change(data):
    logger.info("Theme changed to '%s'", data.get("theme"))


def _log_module_executed(data):
    logger.info("Module '%s' executed by user %s", data.get("module"), data.get("user"))


event_bus.subscribe("subscription_updated", _log_subscription_change)
event_bus.subscribe("theme_changed", _log_theme_change)
event_bus.subscribe("module_executed", _log_module_executed)


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------
def on_event(event_name: str):
    """Decorator to auto-subscribe a function to an event.

    Example::

        @on_event("user_login")
        def handle_login(data):
            print(f"User {data['username']} logged in")
    """

    def decorator(func):
        event_bus.subscribe(event_name, func)
        return func

    return decorator

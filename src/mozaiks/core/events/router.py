"""Event router — wires business events to notification triggers.

Routes ``subscription.*``, ``entitlement.*`` events emitted by the
:class:`SubscriptionDispatcher` to the :class:`NotificationDispatcher`
for automatic notification delivery.

Layer: ``core.events`` (shared runtime).
"""

from __future__ import annotations

import logging
from typing import Any

from mozaiks.contracts.ports.business import ThrottleBackend
from mozaiks.core.events.dispatcher import BusinessEventDispatcher
from mozaiks.core.events.notification_dispatcher import NotificationDispatcher

logger = logging.getLogger(__name__)


class EventRouter:
    """Routes business events to notification triggers.

    Typical boot sequence::

        router = EventRouter(
            throttle_backend=my_throttle_backend,
            business_dispatcher=BusinessEventDispatcher.get_instance(),
        )
        router.register_notification_dispatcher("my_workflow", notif_dispatcher)
        router.start()
    """

    # Business event → notification trigger type
    TRIGGER_MAPPINGS: dict[str, str] = {
        # Subscription → entitlement triggers
        "subscription.limit_warning": "entitlement.limit_warning",
        "subscription.limit_reached": "entitlement.limit_reached",
        # Subscription pass-through
        "subscription.plan_changed": "subscription.plan_changed",
        "subscription.trial_started": "subscription.trial_started",
        "subscription.trial_ending": "subscription.trial_ending",
        "subscription.trial_ended": "subscription.trial_ended",
        "subscription.payment_failed": "subscription.payment_failed",
        "subscription.renewed": "subscription.renewed",
        # Entitlement events
        "entitlement.granted": "entitlement.feature_granted",
        "entitlement.revoked": "entitlement.feature_revoked",
    }

    DEFAULT_THROTTLE_TTL = 300  # 5 minutes

    def __init__(
        self,
        *,
        throttle_backend: ThrottleBackend | None = None,
        business_dispatcher: BusinessEventDispatcher | None = None,
    ) -> None:
        self._business = business_dispatcher or BusinessEventDispatcher.get_instance()
        self._throttle = throttle_backend
        self._notification_dispatchers: dict[str, NotificationDispatcher] = {}
        self._started = False

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_notification_dispatcher(
        self,
        workflow_name: str,
        dispatcher: NotificationDispatcher,
    ) -> None:
        """Register a :class:`NotificationDispatcher` for *workflow_name*."""
        self._notification_dispatchers[workflow_name] = dispatcher

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Subscribe to all mapped event types on the business dispatcher."""
        if self._started:
            return
        for event_type in self.TRIGGER_MAPPINGS:
            self._business.register(event_type, self._handle_event)
        self._started = True
        logger.info("EventRouter started — listening for %d event types", len(self.TRIGGER_MAPPINGS))

    def stop(self) -> None:
        """Unsubscribe from all event types."""
        for event_type in self.TRIGGER_MAPPINGS:
            self._business.unregister(event_type, self._handle_event)
        self._started = False

    # ------------------------------------------------------------------
    # Handler
    # ------------------------------------------------------------------

    async def _handle_event(self, event: dict[str, Any]) -> None:
        event_type: str = event.get("event_type", "")
        trigger = self.TRIGGER_MAPPINGS.get(event_type)
        if not trigger:
            return

        user_id = event.get("user_id")
        workflow_name = event.get("workflow_name", "default")

        if not user_id:
            logger.debug("Ignoring event %s without user_id", event_type)
            return

        # Throttle check
        if self._throttle is not None:
            throttle_key = f"{user_id}:{trigger}"
            if await self._throttle.is_throttled(throttle_key):
                logger.debug("Throttled %s for %s", trigger, user_id)
                return

        dispatcher = self._notification_dispatchers.get(workflow_name)
        if dispatcher is None:
            logger.debug("No NotificationDispatcher for workflow %s", workflow_name)
            return

        dispatched = await dispatcher.trigger(
            trigger_type=trigger,
            user_id=user_id,
            context=event,
        )

        if dispatched:
            logger.info(
                "Triggered notifications %s for %s via %s",
                dispatched,
                user_id,
                trigger,
            )

        # Set throttle
        if self._throttle is not None:
            throttle_key = f"{user_id}:{trigger}"
            await self._throttle.set_throttle(throttle_key, ttl_seconds=self.DEFAULT_THROTTLE_TTL)

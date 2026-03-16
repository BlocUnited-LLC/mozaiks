from __future__ import annotations

import logging
import os
from typing import Optional

from mozaiksai.core.automation.contracts import SubstrateEventEnvelope
from mozaiksai.core.automation.router import get_automation_router

logger = logging.getLogger("mozaiksai.automation.nats_consumer")


def get_automation_transport_mode() -> str:
    return str(os.getenv("MOZAIKS_AUTOMATION_TRANSPORT", "http") or "http").strip().lower()


def use_nats_transport() -> bool:
    return get_automation_transport_mode() in {"nats", "dual"}


def _subject_prefix() -> str:
    return str(
        os.getenv("MOZAIKS_AUTOMATION_NATS_SUBJECT_PREFIX", "mozaiks.substrate.events") or "mozaiks.substrate.events"
    ).strip()


def build_subscription_subject() -> str:
    return f"{_subject_prefix()}.*"


def _queue_name() -> str:
    return str(os.getenv("MOZAIKS_AUTOMATION_NATS_QUEUE", "mozaiksai-automation") or "mozaiksai-automation").strip()


def _nats_url() -> str:
    return str(os.getenv("MOZAIKS_NATS_URL", "nats://localhost:4222") or "nats://localhost:4222").strip()


class AutomationNatsConsumer:
    def __init__(self) -> None:
        self._broker = None
        self._started = False
        self._subscription_subject = build_subscription_subject()
        self._queue = _queue_name()
        self._url = _nats_url()

    async def start(self) -> None:
        if self._started:
            return

        try:
            from faststream.nats import NatsBroker
        except ImportError as exc:  # pragma: no cover - dependency-gated path
            raise RuntimeError(
                "FastStream NATS transport requested but faststream[nats] is not installed"
            ) from exc

        broker = NatsBroker(self._url)

        @broker.subscriber(self._subscription_subject, queue=self._queue)
        async def _consume(message: dict) -> None:
            await self._handle_message(message)

        await broker.start()
        self._broker = broker
        self._started = True
        logger.info(
            "Automation NATS consumer connected url=%s subject=%s queue=%s",
            self._url,
            self._subscription_subject,
            self._queue,
        )

    async def stop(self) -> None:
        if not self._started or self._broker is None:
            return
        await self._broker.stop()
        self._broker = None
        self._started = False
        logger.info("Automation NATS consumer stopped")

    async def _handle_message(self, message: dict) -> None:
        try:
            envelope = SubstrateEventEnvelope.model_validate(message)
        except Exception as exc:
            logger.warning("Invalid NATS automation event payload: %s", exc)
            return

        router = get_automation_router()
        decision, result = await router.dispatch(envelope)
        if decision.status.value == "matched":
            logger.info(
                "Automation NATS event matched route_id=%s route=%s dispatch_status=%s",
                decision.route_id,
                decision.route,
                result.status if result else None,
            )
        else:
            logger.debug(
                "Automation NATS event ignored status=%s detail=%s",
                decision.status.value,
                decision.detail,
            )


_consumer: Optional[AutomationNatsConsumer] = None


def get_automation_nats_consumer() -> AutomationNatsConsumer:
    global _consumer
    if _consumer is None:
        _consumer = AutomationNatsConsumer()
    return _consumer


__all__ = [
    "AutomationNatsConsumer",
    "build_subscription_subject",
    "get_automation_nats_consumer",
    "get_automation_transport_mode",
    "use_nats_transport",
]

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger("mozaikscore.automation_nats")


def get_automation_transport_mode() -> str:
    return str(os.getenv("MOZAIKS_AUTOMATION_TRANSPORT", "http") or "http").strip().lower()


def use_nats_transport() -> bool:
    return get_automation_transport_mode() in {"nats", "dual"}


def use_http_transport() -> bool:
    return get_automation_transport_mode() in {"http", "dual"}


def _subject_prefix() -> str:
    return str(
        os.getenv("MOZAIKS_AUTOMATION_NATS_SUBJECT_PREFIX", "mozaiks.substrate.events") or "mozaiks.substrate.events"
    ).strip()


def _queue_name() -> str:
    return str(os.getenv("MOZAIKS_AUTOMATION_NATS_QUEUE", "mozaiksai-automation") or "mozaiksai-automation").strip()


def _nats_url() -> str:
    return str(os.getenv("MOZAIKS_NATS_URL", "nats://localhost:4222") or "nats://localhost:4222").strip()


def sanitize_subject_token(value: str) -> str:
    text = str(value or "").strip().replace(".", "_")
    return text or "unknown"


def build_subject(app_id: Optional[str]) -> str:
    return f"{_subject_prefix()}.{sanitize_subject_token(str(app_id or 'unknown'))}"


def build_subscription_subject() -> str:
    return f"{_subject_prefix()}.*"


class SubstrateEventNatsPublisher:
    def __init__(self) -> None:
        self._broker = None
        self._started = False
        self._publisher = None
        self._subject_prefix = _subject_prefix()
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
        await broker.start()
        self._broker = broker
        self._started = True
        logger.info(
            "Automation NATS publisher connected url=%s subject_prefix=%s queue=%s",
            self._url,
            self._subject_prefix,
            self._queue,
        )

    async def stop(self) -> None:
        if not self._started or self._broker is None:
            return
        await self._broker.stop()
        self._broker = None
        self._started = False
        logger.info("Automation NATS publisher stopped")

    async def publish(self, envelope: Dict[str, Any]) -> None:
        if not self._started or self._broker is None:
            await self.start()

        if self._broker is None:  # pragma: no cover - defensive
            raise RuntimeError("Automation NATS publisher is unavailable")

        tenant = envelope.get("tenant") if isinstance(envelope, dict) else {}
        app_id = tenant.get("app_id") if isinstance(tenant, dict) else None
        subject = build_subject(str(app_id or "unknown"))
        correlation_id = str(envelope.get("correlation_id") or "").strip() or None

        await self._broker.publish(
            envelope,
            subject=subject,
            correlation_id=correlation_id,
            headers={
                "event_type": str(envelope.get("event_type") or ""),
                "app_id": str(app_id or ""),
            },
        )
        logger.debug(
            "Published substrate event to NATS subject=%s event_type=%s",
            subject,
            envelope.get("event_type"),
        )


_publisher: Optional[SubstrateEventNatsPublisher] = None


def get_substrate_event_nats_publisher() -> SubstrateEventNatsPublisher:
    global _publisher
    if _publisher is None:
        _publisher = SubstrateEventNatsPublisher()
    return _publisher


__all__ = [
    "SubstrateEventNatsPublisher",
    "build_subject",
    "build_subscription_subject",
    "get_automation_transport_mode",
    "get_substrate_event_nats_publisher",
    "sanitize_subject_token",
    "use_http_transport",
    "use_nats_transport",
]

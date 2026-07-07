# ==============================================================================
# FILE: mozaiksai/core/ports/event_bus.py
# DESCRIPTION: Port and adapters for inter-instance event propagation.
#              The in-process unified_event_dispatcher handles events within one
#              instance. This bus propagates events across multiple instances.
#
# Without this, module events (e.g. "contact.created") are invisible to
# other runtime instances behind a load balancer.
#
# Backends:
#   NoOpEventBus      — single-instance (default, no external deps)
#   MongoEventBus     — MongoDB change streams (no extra infra)
#   RedisEventBus     — Redis pub/sub (requires Redis)
#
# Configuration (env vars):
#   EVENT_BUS_BACKEND  — "noop" | "mongo" | "redis" (default: "noop")
#   EVENT_BUS_CHANNEL  — channel/topic name (default: "mozaiks_events")
#   REDIS_URL          — Redis connection URL (for redis backend)
# ==============================================================================
from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from logs.logging_config import get_core_logger

logger = get_core_logger("event_bus")

EventHandler = Callable[[str, dict[str, Any]], Awaitable[None]]

_CHANNEL = os.getenv("EVENT_BUS_CHANNEL", "mozaiks_events")
_BACKEND = os.getenv("EVENT_BUS_BACKEND", "noop").strip().lower()


@runtime_checkable
class EventBus(Protocol):
    """Port for inter-instance event propagation."""

    async def publish(self, event_type: str, data: dict[str, Any]) -> None:
        """Publish an event to all instances."""
        ...

    async def subscribe(self, handler: EventHandler) -> None:
        """Subscribe to incoming events from other instances. Runs until cancelled."""
        ...

    async def start(self) -> None:
        """Start the bus (connect, create channels, etc.)."""
        ...

    async def stop(self) -> None:
        """Stop the bus gracefully."""
        ...


# ---------------------------------------------------------------------------
# No-op (single instance / development)
# ---------------------------------------------------------------------------

class NoOpEventBus:
    """Pass-through bus that does not propagate events across instances.

    Use in single-instance deployments or development. Events are still
    handled by the in-process unified_event_dispatcher.
    """

    async def publish(self, event_type: str, data: dict[str, Any]) -> None:
        logger.debug("EVENT_BUS_NOOP publish event_type=%s", event_type)

    async def subscribe(self, handler: EventHandler) -> None:
        logger.debug("EVENT_BUS_NOOP subscribe — no cross-instance propagation")
        # Block indefinitely without doing anything (caller can cancel).
        await asyncio.Event().wait()

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


# ---------------------------------------------------------------------------
# MongoDB change stream bus
# ---------------------------------------------------------------------------

class MongoEventBus:
    """Cross-instance event bus using MongoDB capped collection + change streams.

    Publishes events by inserting into a capped collection.
    Subscribers watch the change stream and call their handler.

    No additional infrastructure required beyond MongoDB.

    Configuration (env vars):
        MONGO_EVENT_BUS_DB          — database name (default: mozaiks_events)
        MONGO_EVENT_BUS_COLLECTION  — collection name (default: event_bus)
        MONGO_EVENT_BUS_CAP_BYTES   — capped collection max bytes (default: 10MB)
    """

    def __init__(self) -> None:
        self._db_name = os.getenv("MONGO_EVENT_BUS_DB", "mozaiks_events")
        self._col_name = os.getenv("MONGO_EVENT_BUS_COLLECTION", "event_bus")
        self._cap_bytes = int(os.getenv("MONGO_EVENT_BUS_CAP_BYTES", str(10 * 1024 * 1024)))
        self._collection: Any = None
        self._handlers: list[EventHandler] = []
        self._watch_task: asyncio.Task | None = None

    def _get_collection(self) -> Any | None:
        try:
            from mozaiksai.core.core_config import get_mongo_client
            client = get_mongo_client()
            if client is None:
                return None
            return client[self._db_name][self._col_name]
        except Exception:
            return None

    async def start(self) -> None:
        collection = self._get_collection()
        if collection is None:
            logger.warning("MongoEventBus: MongoDB unavailable — falling back to no-op")
            return
        try:
            db = collection.database
            existing = await db.list_collection_names()
            if self._col_name not in existing:
                await db.create_collection(
                    self._col_name,
                    capped=True,
                    size=self._cap_bytes,
                )
                logger.info("MongoEventBus: created capped collection '%s'", self._col_name)
        except Exception as exc:
            logger.warning("MongoEventBus: startup error: %s", exc)

    async def publish(self, event_type: str, data: dict[str, Any]) -> None:
        collection = self._get_collection()
        if collection is None:
            return
        try:
            doc = {
                "_id": str(uuid4()),
                "event_type": event_type,
                "data": data,
                "published_at": datetime.now(UTC).isoformat(),
            }
            await collection.insert_one(doc)
        except Exception as exc:
            logger.warning("MongoEventBus publish failed event_type=%s: %s", event_type, exc)

    async def subscribe(self, handler: EventHandler) -> None:
        collection = self._get_collection()
        if collection is None:
            logger.warning("MongoEventBus: subscribe skipped — MongoDB unavailable")
            return
        try:
            async with collection.watch(
                [{"$match": {"operationType": "insert"}}],
                full_document="updateLookup",
            ) as stream:
                async for change in stream:
                    doc = change.get("fullDocument") or {}
                    event_type = doc.get("event_type", "")
                    event_data = doc.get("data", {})
                    if event_type:
                        try:
                            await handler(event_type, event_data)
                        except Exception as exc:
                            logger.error(
                                "MongoEventBus handler error event_type=%s: %s",
                                event_type, exc,
                            )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("MongoEventBus watch stream error: %s", exc)

    async def stop(self) -> None:
        if self._watch_task and not self._watch_task.done():
            self._watch_task.cancel()


# ---------------------------------------------------------------------------
# Redis pub/sub bus
# ---------------------------------------------------------------------------

class RedisEventBus:
    """Cross-instance event bus using Redis pub/sub.

    Requires: pip install redis[asyncio]
    Configuration (env vars):
        REDIS_URL          — Redis connection URL (default: redis://localhost:6379)
        EVENT_BUS_CHANNEL  — pub/sub channel name (default: mozaiks_events)
    """

    def __init__(self) -> None:
        self._url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self._channel = _CHANNEL
        self._client: Any = None
        self._pubsub: Any = None

    async def start(self) -> None:
        try:
            import redis.asyncio as aioredis
            self._client = aioredis.from_url(self._url, decode_responses=True)
            await self._client.ping()
            logger.info("RedisEventBus connected to %s channel=%s", self._url, self._channel)
        except ImportError as exc:
            raise RuntimeError(
                "redis[asyncio] is required for RedisEventBus. "
                "Install: pip install 'redis[asyncio]'"
            ) from exc

    async def publish(self, event_type: str, data: dict[str, Any]) -> None:
        if self._client is None:
            return
        try:
            payload = json.dumps({"event_type": event_type, "data": data})
            await self._client.publish(self._channel, payload)
        except Exception as exc:
            logger.warning("RedisEventBus publish failed: %s", exc)

    async def subscribe(self, handler: EventHandler) -> None:
        if self._client is None:
            return
        try:
            self._pubsub = self._client.pubsub()
            await self._pubsub.subscribe(self._channel)
            async for message in self._pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    payload = json.loads(message["data"])
                    await handler(payload["event_type"], payload.get("data", {}))
                except Exception as exc:
                    logger.error("RedisEventBus handler error: %s", exc)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        if self._pubsub:
            await self._pubsub.unsubscribe()
        if self._client:
            await self._client.aclose()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Return the configured inter-instance event bus (process-wide singleton)."""
    global _event_bus
    if _event_bus is None:
        if _BACKEND == "mongo":
            _event_bus = MongoEventBus()
        elif _BACKEND == "redis":
            _event_bus = RedisEventBus()
        else:
            _event_bus = NoOpEventBus()
        logger.info("EventBus backend: %s", _BACKEND)
    return _event_bus


__all__ = [
    "EventBus",
    "EventHandler",
    "MongoEventBus",
    "NoOpEventBus",
    "RedisEventBus",
    "get_event_bus",
]

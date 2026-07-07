# ==============================================================================
# FILE: mozaiksai/core/runtime/persistence/distributed_lock.py
# DESCRIPTION: MongoDB-backed distributed lock for chat session state mutations.
#              Prevents two runtime instances from resuming the same chat
#              simultaneously, which would corrupt session state.
#
# Pattern: findOneAndUpdate with upsert on (resource_key).
#   Acquire: insert a lock document with an expiry.
#   Release: delete the lock document by _id + holder.
#   Expire:  TTL index on expires_at automatically cleans orphaned locks.
#
# Configuration (env vars):
#   DISTRIBUTED_LOCK_TTL_SECONDS   — lock TTL in seconds (default 60)
#   DISTRIBUTED_LOCK_RETRY_DELAY   — retry interval on contention in seconds (default 0.2)
#   DISTRIBUTED_LOCK_MAX_RETRIES   — max acquisition retries (default 15 = 3s total)
# ==============================================================================
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from logs.logging_config import get_core_logger

logger = get_core_logger("distributed_lock")

_LOCK_COLLECTION = "distributed_locks"
_LOCK_DB = os.getenv("MOZAIKS_APP_DATABASE_NAME", "mozaiks_apps")


def _ttl() -> int:
    try:
        return int(os.getenv("DISTRIBUTED_LOCK_TTL_SECONDS", "60").strip())
    except (ValueError, AttributeError):
        return 60


def _retry_delay() -> float:
    try:
        return float(os.getenv("DISTRIBUTED_LOCK_RETRY_DELAY", "0.2").strip())
    except (ValueError, AttributeError):
        return 0.2


def _max_retries() -> int:
    try:
        return int(os.getenv("DISTRIBUTED_LOCK_MAX_RETRIES", "15").strip())
    except (ValueError, AttributeError):
        return 15


class LockAcquisitionError(Exception):
    """Raised when a distributed lock cannot be acquired within the retry budget."""

    def __init__(self, resource: str) -> None:
        super().__init__(f"Could not acquire lock for resource '{resource}' within retry budget")
        self.resource = resource


def _get_lock_collection() -> Any | None:
    try:
        from mozaiksai.core.core_config import get_mongo_client
        client = get_mongo_client()
        if client is None:
            return None
        return client[_LOCK_DB][_LOCK_COLLECTION]
    except Exception as exc:
        logger.debug("Lock collection unavailable: %s", exc)
        return None


async def ensure_lock_indexes() -> None:
    """Create TTL index on expires_at — call once at startup.

    The TTL index auto-cleans orphaned locks after their expiry,
    preventing lock starvation if a holder crashes.
    """
    collection = _get_lock_collection()
    if collection is None:
        return
    try:
        await collection.create_index(
            [("expires_at", 1)],
            expireAfterSeconds=0,
            name="lock_ttl_idx",
            background=True,
        )
        await collection.create_index(
            [("resource", 1)],
            unique=True,
            name="lock_resource_unique_idx",
            background=True,
        )
        logger.debug("Distributed lock indexes ensured")
    except Exception as exc:
        logger.warning("Could not ensure lock indexes: %s", exc)


async def _try_acquire(collection: Any, resource: str, holder_id: str, ttl_seconds: int) -> bool:
    """Attempt a single lock acquisition. Returns True on success."""
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
    now = datetime.now(UTC)
    try:
        # Upsert: insert new lock if none exists OR the existing one is expired.
        result = await collection.find_one_and_update(
            {
                "resource": resource,
                "$or": [
                    {"expires_at": {"$lt": now}},   # expired lock — can steal
                    {"holder_id": holder_id},         # same holder re-acquiring
                ],
            },
            {
                "$set": {
                    "resource": resource,
                    "holder_id": holder_id,
                    "expires_at": expires_at,
                    "acquired_at": now.isoformat(),
                }
            },
            upsert=False,
            return_document=True,
        )
        if result is not None:
            return True

        # No expired lock exists — try a clean insert.
        try:
            await collection.insert_one({
                "resource": resource,
                "holder_id": holder_id,
                "expires_at": expires_at,
                "acquired_at": now.isoformat(),
            })
            return True
        except Exception:
            # DuplicateKeyError or similar — lock is held by another.
            return False
    except Exception as exc:
        logger.debug("Lock acquisition attempt failed resource=%s: %s", resource, exc)
        return False


async def _release(collection: Any, resource: str, holder_id: str) -> None:
    """Release a lock held by this holder."""
    try:
        await collection.delete_one({"resource": resource, "holder_id": holder_id})
    except Exception as exc:
        logger.warning("Lock release failed resource=%s holder=%s: %s", resource, holder_id, exc)


@asynccontextmanager
async def distributed_lock(resource: str, *, holder_id: str | None = None):
    """Async context manager that acquires and releases a distributed lock.

    Usage:
        async with distributed_lock(f"chat:{chat_id}"):
            # Only one instance can resume this chat at a time
            ...

    Raises LockAcquisitionError if the lock cannot be acquired.
    Falls through silently if MongoDB is unavailable (degraded mode).
    """
    collection = _get_lock_collection()
    if collection is None:
        # MongoDB unavailable — run without distributed lock (degraded mode).
        logger.warning("DISTRIBUTED_LOCK_DEGRADED resource=%s — MongoDB unavailable, proceeding without lock", resource)
        yield
        return

    effective_holder = holder_id or str(uuid4())
    ttl = _ttl()
    delay = _retry_delay()
    max_retries = _max_retries()

    acquired = False
    for attempt in range(max_retries + 1):
        acquired = await _try_acquire(collection, resource, effective_holder, ttl)
        if acquired:
            break
        if attempt < max_retries:
            logger.debug(
                "LOCK_CONTENTION resource=%s attempt=%d/%d — retrying in %.2fs",
                resource, attempt + 1, max_retries, delay,
            )
            await asyncio.sleep(delay)

    if not acquired:
        raise LockAcquisitionError(resource)

    logger.debug("LOCK_ACQUIRED resource=%s holder=%s", resource, effective_holder)
    try:
        yield
    finally:
        await _release(collection, resource, effective_holder)
        logger.debug("LOCK_RELEASED resource=%s holder=%s", resource, effective_holder)


__all__ = [
    "LockAcquisitionError",
    "distributed_lock",
    "ensure_lock_indexes",
]

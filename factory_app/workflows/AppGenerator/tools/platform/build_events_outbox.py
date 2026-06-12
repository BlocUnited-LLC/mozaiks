
import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from logs.logging_config import get_core_logger
from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, PlatformCollections
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id

logger = get_core_logger("platform_build_events_outbox")

_INDEX_LOCK = asyncio.Lock()
_INDEX_READY = False


def _db_name() -> str:
    return (os.getenv("MOZAIKS_PLATFORM_OUTBOX_DB") or SYSTEM_DATABASE).strip() or SYSTEM_DATABASE


def _collection_name() -> str:
    return (
        os.getenv("MOZAIKS_PLATFORM_BUILD_EVENTS_OUTBOX_COLLECTION") or PlatformCollections.BUILD_EVENTS_OUTBOX
    ).strip() or PlatformCollections.BUILD_EVENTS_OUTBOX


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _safe_token(value: str) -> str:
    # Keep deterministic ids readable, but avoid path separators and whitespace.
    raw = str(value or "").strip()
    if not raw:
        return "unknown"
    return "".join(ch for ch in raw if ch.isalnum() or ch in {"-", "_", "."})[:160] or "unknown"


def build_outbox_id(*, app_id: str, build_id: str, event_type: str) -> str:
    return f"build_evt:{_safe_token(app_id)}:{_safe_token(build_id)}:{_safe_token(event_type)}"


async def _coll() -> Any:
    client = get_mongo_client()
    return client[_db_name()][_collection_name()]


async def ensure_indexes() -> None:
    global _INDEX_READY
    if _INDEX_READY:
        return
    async with _INDEX_LOCK:
        if _INDEX_READY:
            return
        try:
            coll = await _coll()
            await coll.create_index([("app_id", 1), ("build_id", 1), ("event_type", 1)], name="app_build_event")
            await coll.create_index([("platform_notified", 1), ("next_retry_at", 1)], name="notify_retry_due")
            _INDEX_READY = True
        except Exception as exc:  # pragma: no cover
            # Outbox should never hard-fail runtime startup.
            logger.warning("Failed to ensure build-events outbox indexes: %s", exc)
            _INDEX_READY = True


async def upsert_outbox_event(
    *,
    app_id: str,
    build_id: str,
    event_type: str,
    status: str,
    payload: dict[str, Any],
    user_id: str | None = None,
    workflow_name: str | None = None,
    idempotency_key: str | None = None,
) -> str:
    await ensure_indexes()

    resolved_app_id = coalesce_app_id(app_id=app_id)
    if not resolved_app_id:
        raise ValueError("app_id is required")
    bid = str(build_id or "").strip()
    if not bid:
        raise ValueError("build_id is required")
    evt = str(event_type or "").strip()
    if not evt:
        raise ValueError("event_type is required")

    outbox_id = build_outbox_id(app_id=str(resolved_app_id), build_id=bid, event_type=evt)
    coll = await _coll()

    now = _utc_now()
    existing = await coll.find_one({"_id": outbox_id}, {"platform_notified": 1})
    if isinstance(existing, dict) and existing.get("platform_notified") is True:
        return outbox_id

    set_fields: dict[str, Any] = {
        **build_app_scope_filter(str(resolved_app_id)),
        "build_id": bid,
        "event_type": evt,
        "status": str(status or "").strip() or "unknown",
        "payload": payload if isinstance(payload, dict) else {"value": payload},
        "user_id": str(user_id).strip() if user_id else None,
        "workflow_name": str(workflow_name).strip() if workflow_name else None,
        "idempotency_key": str(idempotency_key).strip() if idempotency_key else None,
        "updated_at": now,
        # Make the event eligible for immediate retry attempt.
        "next_retry_at": now,
    }

    await coll.update_one(
        {"_id": outbox_id},
        {
            "$set": set_fields,
            "$setOnInsert": {
                "_id": outbox_id,
                "platform_notified": False,
                "attempts": 0,
                "created_at": now,
            },
        },
        upsert=True,
    )
    return outbox_id


def compute_next_retry_at(*, attempts: int, now: datetime | None = None) -> datetime:
    # Backoff for persistent retries (separate from per-request retry).
    base = 5.0  # seconds
    cap = 300.0  # 5 minutes
    try:
        n = max(0, int(attempts))
    except Exception:
        n = 0
    delay = min(cap, base * (2**n))
    return (now or _utc_now()) + timedelta(seconds=delay)


async def mark_attempt(
    *,
    outbox_id: str,
    ok: bool,
    status_code: int | None = None,
    error: str | None = None,
) -> None:
    coll = await _coll()
    now = _utc_now()
    update: dict[str, Any] = {"updated_at": now, "last_attempt_at": now}
    if ok:
        update["platform_notified"] = True
        update["notified_at"] = now
        if status_code is not None:
            update["last_status_code"] = int(status_code)
        update["last_error"] = None
        update["next_retry_at"] = None
        await coll.update_one({"_id": outbox_id}, {"$set": update})
        return

    # Failure: increment attempts and schedule next retry.
    doc = await coll.find_one({"_id": outbox_id}, {"attempts": 1})
    current_attempts = int(doc.get("attempts", 0)) if isinstance(doc, dict) else 0
    next_attempts = current_attempts + 1
    update["attempts"] = next_attempts
    update["platform_notified"] = False
    if status_code is not None:
        update["last_status_code"] = int(status_code)
    if error:
        update["last_error"] = str(error)[:4000]
    update["next_retry_at"] = compute_next_retry_at(attempts=next_attempts, now=now)
    await coll.update_one({"_id": outbox_id}, {"$set": update})


async def get_outbox_event(*, outbox_id: str) -> dict[str, Any] | None:
    event_id = str(outbox_id or "").strip()
    if not event_id:
        return None
    coll = await _coll()
    doc = await coll.find_one({"_id": event_id})
    return doc if isinstance(doc, dict) else None


async def list_due_events(*, limit: int = 25) -> list[dict[str, Any]]:
    await ensure_indexes()
    coll = await _coll()
    now = _utc_now()
    cursor = (
        coll.find(
            {
                "platform_notified": {"$ne": True},
                "$or": [{"next_retry_at": {"$lte": now}}, {"next_retry_at": None}],
            }
        )
        .sort("updated_at", 1)
        .limit(int(limit))
    )
    docs = await cursor.to_list(length=int(limit))
    return [d for d in docs if isinstance(d, dict)]


__all__ = [
    "ensure_indexes",
    "upsert_outbox_event",
    "mark_attempt",
    "get_outbox_event",
    "list_due_events",
    "build_outbox_id",
]


# ==============================================================================
# FILE: mozaiksai/control_plane/refinement_tracking.py
# DESCRIPTION: Durable audit trail for control-plane refinement events.
#
#   Persists one document per event to the `cp_refinement_events` collection.
#   Events are written fire-and-forget — failures are logged at DEBUG level
#   and never raise to callers, so the critical path is unaffected.
#
#   Collection: mozaiks.cp_refinement_events
#   Index suggestion: (request_id, app_id, created_at)
#
# Event kinds emitted by the harness:
#   "request_received"  — refinement request accepted before classification
#   "classified"        — classifier produced a change_class and route
#   "routed"            — harness decision resolved to a workflow_sequence
#   "completed"         — refinement flow finished successfully
#   "failed"            — refinement flow raised an unrecoverable error
# ==============================================================================
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

logger = logging.getLogger("mozaiksai.control_plane.refinement_tracking")

_COLLECTION_NAME = "cp_refinement_events"
_DATABASE_NAME = "mozaiks"


def _get_collection() -> Any | None:
    """Return the Motor collection, or None when Mongo is not configured."""
    try:
        from mozaiksai.core.core_config import get_mongo_client

        client = get_mongo_client()
        if client is None:
            return None
        return client[_DATABASE_NAME][_COLLECTION_NAME]
    except Exception:
        return None


async def record_refinement_event(
    *,
    event_kind: str,
    request_id: str,
    app_id: str,
    intent: str | None = None,
    change_class: str | None = None,
    workflow_sequence: str | None = None,
    outcome: str | None = None,
    error: str | None = None,
    duration_ms: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Persist a single refinement event to the audit collection.

    Fire-and-forget: failures are logged at DEBUG level and never raised.
    The control-plane critical path is never blocked by tracking failures.

    Args:
        event_kind:        One of "request_received", "classified", "routed",
                           "completed", "failed".
        request_id:        Refinement request correlation ID.
        app_id:            App being refined.
        intent:            Original user intent text (request_received).
        change_class:      Classifier output e.g. "patch", "feature" (classified).
        workflow_sequence: Selected workflow sequence ID (routed/completed).
        outcome:           Final outcome: "ok", "error", "skipped" (completed/failed).
        error:             Error description when outcome is "error" (failed).
        duration_ms:       Wall-clock time for this event stage.
        metadata:          Additional structured context (agent name, token counts…).
    """
    try:
        collection = _get_collection()
        if collection is None:
            return
        doc: dict[str, Any] = {
            "_id": f"rfe-{uuid4()}",
            "event_kind": event_kind,
            "request_id": request_id,
            "app_id": app_id,
            "created_at": datetime.now(UTC),
        }
        if intent is not None:
            doc["intent"] = intent[:2000]  # cap to avoid oversized documents
        if change_class is not None:
            doc["change_class"] = change_class
        if workflow_sequence is not None:
            doc["workflow_sequence"] = workflow_sequence
        if outcome is not None:
            doc["outcome"] = outcome
        if error is not None:
            doc["error"] = error[:1000]
        if duration_ms is not None:
            doc["duration_ms"] = duration_ms
        if metadata:
            doc["metadata"] = metadata

        await collection.insert_one(doc)
    except Exception as exc:
        logger.debug(
            "refinement_tracking: write failed event_kind=%s request_id=%s: %s",
            event_kind,
            request_id,
            exc,
        )


async def get_refinement_history(
    *,
    app_id: str,
    request_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return recent refinement events for an app, newest first.

    Args:
        app_id:     App to query.
        request_id: Optional filter to a single request's event chain.
        limit:      Maximum documents to return (capped at 200).

    Returns:
        List of event documents with ``_id`` excluded. Empty list on error.
    """
    bounded_limit = max(1, min(int(limit), 200))
    try:
        collection = _get_collection()
        if collection is None:
            return []
        query: dict[str, Any] = {"app_id": app_id}
        if request_id:
            query["request_id"] = request_id
        cursor = (
            collection.find(query, {"_id": 0})
            .sort("created_at", -1)
            .limit(bounded_limit)
        )
        return list(await cursor.to_list(length=bounded_limit))  # type: ignore[arg-type]
    except Exception as exc:
        logger.debug("refinement_tracking: read failed app_id=%s: %s", app_id, exc)
        return []


__all__ = [
    "get_refinement_history",
    "record_refinement_event",
]

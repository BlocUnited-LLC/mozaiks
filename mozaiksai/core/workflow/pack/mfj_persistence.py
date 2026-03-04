# === MOZAIKS-CORE-HEADER ===
# ==============================================================================
# FILE: core/workflow/pack/mfj_persistence.py
# DESCRIPTION: MongoDB-backed persistence for MFJ completion records.
#
# Persists _MFJCompletionRecord data to the ``MozaiksAI.MFJCompletions``
# collection so that ``requires`` checks survive process restarts.
#
# Architecture:
#   - Write-through: every _record_mfj_completion() call stores to both
#     in-memory cache *and* MongoDB.
#   - Read-through: _check_mfj_requires() checks in-memory first; on cache
#     miss, loads from MongoDB and populates cache.
#   - Graceful degradation: if MongoDB is unavailable, ops fall back to
#     in-memory-only mode. A warning is logged but orchestration is never
#     blocked.
#   - TTL index: completed records auto-expire (default 7 days). Active
#     sessions refresh on access.
#
# For future coding agents:
#   - The coordinator owns the in-memory ``_completed_mfjs`` dict. This
#     module provides an *async* backing store.  Don't duplicate the
#     in-memory dict here.
#   - get_mongo_client() returns an AsyncIOMotorClient. All collection
#     operations are async.
#   - Index creation is idempotent (checks for existing indexes first).
#   - The ``MFJCompletionStore`` is instantiated once per coordinator
#     instance and stored as ``self._mfj_store``.
# ==============================================================================

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DB_NAME = "MozaiksAI"
_COLLECTION_NAME = "MFJCompletions"
_DEFAULT_TTL_DAYS = 7

# Index names — used to check for existing indexes before creation.
_IDX_PARENT_TRIGGER = "mfj_parent_trigger"
_IDX_TTL = "mfj_ttl_completed_at"


# ---------------------------------------------------------------------------
# MFJ Completion Store
# ---------------------------------------------------------------------------

class MFJCompletionStore:
    """Async MongoDB persistence layer for MFJ completion records.

    Usage in coordinator::

        store = MFJCompletionStore(ttl_days=7)
        await store.ensure_indexes()          # call once on startup
        await store.write_completion(record)  # called from _record_mfj_completion
        ids = await store.load_completed_trigger_ids(parent_chat_id)
        await store.load_active_completions(parent_chat_ids)  # recovery

    All methods are safe to call when MongoDB is unavailable — they log
    warnings and return empty/default values.
    """

    def __init__(self, *, ttl_days: int = _DEFAULT_TTL_DAYS) -> None:
        self._ttl_days = ttl_days
        self._client: Optional[Any] = None  # AsyncIOMotorClient, resolved lazily
        self._indexes_ensured = False

    # ------------------------------------------------------------------
    # Lazy client access
    # ------------------------------------------------------------------

    def _get_collection(self) -> Any:
        """Return the MFJCompletions collection (Motor async collection).

        Lazily creates the Motor client on first call. Returns ``None``
        if MONGO_URI is not configured (test/dev environments).
        """
        if self._client is None:
            try:
                from mozaiksai.core.core_config import get_mongo_client
                self._client = get_mongo_client()
            except Exception as exc:
                logger.warning(
                    "[MFJ-PERSIST] MongoDB unavailable — running in-memory only: %s",
                    exc,
                )
                return None
        return self._client[_DB_NAME][_COLLECTION_NAME]

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    async def ensure_indexes(self) -> None:
        """Create compound and TTL indexes if they don't already exist.

        Indexes:
          - ``(parent_chat_id, trigger_id)`` — fast ``requires`` lookups.
          - TTL on ``completed_at`` — auto-cleanup after ``ttl_days``.

        Idempotent: skips creation if indexes already present.
        """
        if self._indexes_ensured:
            return

        coll = self._get_collection()
        if coll is None:
            return

        try:
            existing = await coll.list_indexes().to_list(length=None)
            existing_names: Set[str] = {idx["name"] for idx in existing}

            # Compound index for requires lookups
            if _IDX_PARENT_TRIGGER not in existing_names:
                await coll.create_index(
                    [("parent_chat_id", 1), ("trigger_id", 1)],
                    name=_IDX_PARENT_TRIGGER,
                )
                logger.info(
                    "[MFJ-PERSIST] Created compound index %s",
                    _IDX_PARENT_TRIGGER,
                )

            # TTL index on completed_at
            if _IDX_TTL not in existing_names:
                await coll.create_index(
                    "completed_at",
                    name=_IDX_TTL,
                    expireAfterSeconds=self._ttl_days * 86400,
                )
                logger.info(
                    "[MFJ-PERSIST] Created TTL index %s (%d days)",
                    _IDX_TTL,
                    self._ttl_days,
                )

            self._indexes_ensured = True
        except Exception as exc:
            logger.warning("[MFJ-PERSIST] Index creation failed: %s", exc)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def write_completion(
        self,
        *,
        parent_chat_id: str,
        trigger_id: str,
        completed_at: datetime,
        child_count: int,
        all_succeeded: bool,
        child_chat_ids: Optional[List[str]] = None,
        merge_summary_preview: str = "",
    ) -> bool:
        """Persist a single MFJ completion record to MongoDB.

        Returns ``True`` on success, ``False`` on failure (logged, never
        raised — orchestration must not be blocked by persistence errors).
        """
        coll = self._get_collection()
        if coll is None:
            return False

        doc = {
            "parent_chat_id": parent_chat_id,
            "trigger_id": trigger_id,
            "completed_at": completed_at,
            "child_count": child_count,
            "all_succeeded": all_succeeded,
            "child_chat_ids": child_chat_ids or [],
            "merge_summary_preview": merge_summary_preview[:500],
        }

        try:
            await coll.insert_one(doc)
            logger.info(
                "[MFJ-PERSIST] Wrote completion: parent=%s trigger=%s",
                parent_chat_id,
                trigger_id,
            )
            return True
        except Exception as exc:
            logger.warning(
                "[MFJ-PERSIST] Write failed for parent=%s trigger=%s: %s",
                parent_chat_id,
                trigger_id,
                exc,
            )
            return False

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def load_completed_trigger_ids(
        self,
        parent_chat_id: str,
    ) -> Set[str]:
        """Load all completed trigger IDs for a parent chat from MongoDB.

        Used as a cache-miss fallback by the coordinator's
        ``_check_mfj_requires()`` method.

        Returns an empty set if MongoDB is unavailable.
        """
        coll = self._get_collection()
        if coll is None:
            return set()

        try:
            cursor = coll.find(
                {"parent_chat_id": parent_chat_id},
                {"trigger_id": 1, "_id": 0},
            )
            docs = await cursor.to_list(length=500)
            return {doc["trigger_id"] for doc in docs if "trigger_id" in doc}
        except Exception as exc:
            logger.warning(
                "[MFJ-PERSIST] Read failed for parent=%s: %s",
                parent_chat_id,
                exc,
            )
            return set()

    async def load_completions_for_parents(
        self,
        parent_chat_ids: List[str],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Bulk-load completion records for multiple parents (recovery).

        Returns a dict of ``parent_chat_id → List[record_dict]`` where each
        record_dict has the same fields as stored in MongoDB.

        Used on coordinator startup to rebuild ``_completed_mfjs`` cache.
        """
        if not parent_chat_ids:
            return {}

        coll = self._get_collection()
        if coll is None:
            return {}

        try:
            cursor = coll.find(
                {"parent_chat_id": {"$in": parent_chat_ids}},
                {"_id": 0},
            )
            docs = await cursor.to_list(length=5000)

            result: Dict[str, List[Dict[str, Any]]] = {}
            for doc in docs:
                pid = doc.get("parent_chat_id", "")
                result.setdefault(pid, []).append(doc)
            return result
        except Exception as exc:
            logger.warning(
                "[MFJ-PERSIST] Bulk load failed for %d parents: %s",
                len(parent_chat_ids),
                exc,
            )
            return {}

    # ------------------------------------------------------------------
    # Recovery helpers
    # ------------------------------------------------------------------

    async def load_paused_parent_ids(self) -> List[str]:
        """Return parent_chat_ids that have completion records but may
        still need to resume (stale run detection).

        Queries for unique parent_chat_ids in the completions collection
        that have records newer than TTL/2 (i.e., likely still active).
        """
        coll = self._get_collection()
        if coll is None:
            return []

        try:
            cutoff = datetime.now(timezone.utc) - timedelta(
                days=self._ttl_days // 2 or 1
            )
            pipeline = [
                {"$match": {"completed_at": {"$gte": cutoff}}},
                {"$group": {"_id": "$parent_chat_id"}},
            ]
            cursor = coll.aggregate(pipeline)
            docs = await cursor.to_list(length=1000)
            return [doc["_id"] for doc in docs if doc.get("_id")]
        except Exception as exc:
            logger.warning("[MFJ-PERSIST] Paused parent scan failed: %s", exc)
            return []

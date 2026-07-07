# ==============================================================================
# FILE: mozaiksai/core/workflow/idempotency.py
# DESCRIPTION: Task execution idempotency for workflow task batches.
#              Deduplicates tool executions by (chat_id, tool_name, args_hash).
#              Prevents double-execution when a task batch retries after a
#              successful tool call but failed result persistence.
#
# Storage: MongoDB collection idempotency_records (TTL 24h).
# ==============================================================================
from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from logs.logging_config import get_core_logger

logger = get_core_logger("task_idempotency")

_IDEMPOTENCY_COLLECTION = "idempotency_records"
_IDEMPOTENCY_DB = os.getenv("MOZAIKS_APP_DATABASE_NAME", "mozaiks_apps")
_RECORD_TTL_HOURS = int(os.getenv("IDEMPOTENCY_RECORD_TTL_HOURS", "24").strip() or "24")


def _compute_args_hash(args: Any) -> str:
    """Stable SHA-256 hash of tool arguments."""
    try:
        serialised = json.dumps(args, sort_keys=True, default=str)
    except Exception:
        serialised = repr(args)
    return hashlib.sha256(serialised.encode()).hexdigest()


def _make_idempotency_key(chat_id: str, tool_name: str, args_hash: str) -> str:
    return f"{chat_id}::{tool_name}::{args_hash}"


def _get_collection() -> Any | None:
    try:
        from mozaiksai.core.core_config import get_mongo_client
        client = get_mongo_client()
        if client is None:
            return None
        return client[_IDEMPOTENCY_DB][_IDEMPOTENCY_COLLECTION]
    except Exception as exc:
        logger.debug("Idempotency collection unavailable: %s", exc)
        return None


async def ensure_idempotency_indexes() -> None:
    """Create TTL and lookup indexes. Call once at startup."""
    collection = _get_collection()
    if collection is None:
        return
    try:
        await collection.create_index(
            [("expires_at", 1)],
            expireAfterSeconds=0,
            name="idempotency_ttl_idx",
            background=True,
        )
        await collection.create_index(
            [("idempotency_key", 1)],
            unique=True,
            name="idempotency_key_unique_idx",
            background=True,
        )
        logger.debug("Idempotency indexes ensured")
    except Exception as exc:
        logger.warning("Could not ensure idempotency indexes: %s", exc)


class IdempotencyGuard:
    """Guards tool execution against duplicate runs.

    Usage:
        guard = IdempotencyGuard()
        result, already_ran = await guard.check_and_reserve(
            chat_id=chat_id,
            tool_name="generate_app_files",
            args=task_args,
        )
        if already_ran:
            return result  # Return cached result — skip re-execution
        ...
        await guard.record_success(chat_id, "generate_app_files", args=task_args, result=my_result)
    """

    async def check_and_reserve(
        self,
        *,
        chat_id: str,
        tool_name: str,
        args: Any = None,
    ) -> tuple[Any | None, bool]:
        """Check if this (chat_id, tool_name, args) has already succeeded.

        Returns (cached_result, True) if found.
        Returns (None, False) and reserves the key if not found.
        """
        collection = _get_collection()
        if collection is None:
            return None, False

        args_hash = _compute_args_hash(args)
        key = _make_idempotency_key(chat_id, tool_name, args_hash)

        try:
            existing = await collection.find_one({"idempotency_key": key})
            if existing and existing.get("status") == "success":
                logger.debug(
                    "IDEMPOTENCY_HIT chat_id=%s tool=%s — skipping re-execution",
                    chat_id, tool_name,
                )
                return existing.get("result"), True
        except Exception as exc:
            logger.debug("Idempotency check failed: %s", exc)
            return None, False

        # Reserve the key in PENDING state.
        try:
            expires_at = datetime.now(UTC) + timedelta(hours=_RECORD_TTL_HOURS)
            await collection.update_one(
                {"idempotency_key": key},
                {
                    "$setOnInsert": {
                        "idempotency_key": key,
                        "chat_id": chat_id,
                        "tool_name": tool_name,
                        "args_hash": args_hash,
                        "status": "pending",
                        "created_at": datetime.now(UTC).isoformat(),
                        "expires_at": expires_at,
                    }
                },
                upsert=True,
            )
        except Exception as exc:
            # Race with another instance — treat as non-idempotent (safe).
            logger.debug("Idempotency reserve race chat_id=%s tool=%s: %s", chat_id, tool_name, exc)

        return None, False

    async def record_success(
        self,
        *,
        chat_id: str,
        tool_name: str,
        args: Any = None,
        result: Any = None,
    ) -> None:
        """Mark a previously reserved key as successfully completed."""
        collection = _get_collection()
        if collection is None:
            return

        args_hash = _compute_args_hash(args)
        key = _make_idempotency_key(chat_id, tool_name, args_hash)

        try:
            await collection.update_one(
                {"idempotency_key": key},
                {
                    "$set": {
                        "status": "success",
                        "result": result,
                        "completed_at": datetime.now(UTC).isoformat(),
                    }
                },
            )
        except Exception as exc:
            logger.warning("Idempotency record_success failed tool=%s: %s", tool_name, exc)

    async def record_failure(
        self,
        *,
        chat_id: str,
        tool_name: str,
        args: Any = None,
        error: str | None = None,
    ) -> None:
        """Clear a pending reservation so retries can proceed."""
        collection = _get_collection()
        if collection is None:
            return

        args_hash = _compute_args_hash(args)
        key = _make_idempotency_key(chat_id, tool_name, args_hash)

        try:
            await collection.delete_one({"idempotency_key": key})
        except Exception as exc:
            logger.debug("Idempotency record_failure cleanup failed: %s", exc)


_idempotency_guard: IdempotencyGuard | None = None


def get_idempotency_guard() -> IdempotencyGuard:
    """Return the process-wide IdempotencyGuard singleton."""
    global _idempotency_guard
    if _idempotency_guard is None:
        _idempotency_guard = IdempotencyGuard()
    return _idempotency_guard


__all__ = [
    "IdempotencyGuard",
    "ensure_idempotency_indexes",
    "get_idempotency_guard",
]

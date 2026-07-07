# ==============================================================================
# FILE: mozaiksai/core/flags/feature_flags.py
# DESCRIPTION: Feature flag system for canary rollouts and capability gating.
#
# Backends (in priority order):
#   1. Environment variables   — MOZAIKS_FLAG_{FLAG_NAME}=true/false
#   2. MongoDB collection      — {app_db}.feature_flags (if available)
#   3. Default (disabled)
#
# This allows operators to:
#   - Enable a new workflow for a subset of apps without redeploying
#   - Roll back a feature by flipping a flag
#   - Canary test by enabling for specific app_ids
#
# Configuration (env vars):
#   MOZAIKS_FLAG_{NAME}          — "true" enables the flag globally
#   FEATURE_FLAGS_CACHE_TTL      — seconds to cache MongoDB flags (default 30)
#
# Usage:
#   flags = get_flags()
#   if flags.is_enabled("new_workflow_queue"):
#       ...
#   if flags.is_enabled("new_workflow_queue", app_id="app_123"):
#       ...
# ==============================================================================
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from logs.logging_config import get_core_logger

logger = get_core_logger("feature_flags")

_CACHE_TTL = float(os.getenv("FEATURE_FLAGS_CACHE_TTL", "30").strip() or "30")
_FLAG_PREFIX = "MOZAIKS_FLAG_"
_FLAGS_COLLECTION = "feature_flags"
_FLAGS_DB = os.getenv("MOZAIKS_APP_DATABASE_NAME", "mozaiks_apps")


class FeatureFlags:
    """Evaluates feature flags per request.

    Check order:
      1. Env var MOZAIKS_FLAG_{NAME} (global)
      2. MongoDB flag document (app_id-scoped or global)
      3. Default: disabled

    The MongoDB backend is optional and cached to avoid per-request DB hits.
    """

    def __init__(self) -> None:
        self._mongo_cache: dict[str, Any] = {}
        self._cache_loaded_at: float = 0.0
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Synchronous evaluation (env-only, no DB)
    # ------------------------------------------------------------------

    def is_enabled(self, flag: str, *, app_id: str | None = None) -> bool:
        """Check if a flag is enabled. Safe to call from sync context.

        Only evaluates environment variables synchronously. For MongoDB-backed
        flags, use await is_enabled_async().
        """
        env_key = _FLAG_PREFIX + flag.upper().replace("-", "_").replace(".", "_")
        env_val = os.getenv(env_key, "").strip().lower()
        if env_val in ("true", "1", "yes", "on"):
            return True
        if env_val in ("false", "0", "no", "off"):
            return False

        # Fall back to cached MongoDB state if available (set by async path).
        return self._evaluate_mongo_cache(flag, app_id=app_id)

    def _evaluate_mongo_cache(self, flag: str, *, app_id: str | None = None) -> bool:
        """Evaluate flag from the in-memory MongoDB cache."""
        # App-scoped check first, then global.
        if app_id:
            scoped_key = f"{flag}::{app_id}"
            if scoped_key in self._mongo_cache:
                return bool(self._mongo_cache[scoped_key].get("enabled", False))
        if flag in self._mongo_cache:
            return bool(self._mongo_cache[flag].get("enabled", False))
        return False

    # ------------------------------------------------------------------
    # Async evaluation (env + MongoDB)
    # ------------------------------------------------------------------

    async def is_enabled_async(self, flag: str, *, app_id: str | None = None) -> bool:
        """Check if a flag is enabled, including MongoDB-backed flags."""
        # Env var takes absolute precedence.
        env_key = _FLAG_PREFIX + flag.upper().replace("-", "_").replace(".", "_")
        env_val = os.getenv(env_key, "").strip().lower()
        if env_val in ("true", "1", "yes", "on"):
            return True
        if env_val in ("false", "0", "no", "off"):
            return False

        await self._maybe_refresh_cache()
        return self._evaluate_mongo_cache(flag, app_id=app_id)

    async def _maybe_refresh_cache(self) -> None:
        """Refresh MongoDB flag cache if TTL has elapsed."""
        now = time.monotonic()
        if (now - self._cache_loaded_at) < _CACHE_TTL:
            return

        async with self._lock:
            if (now - self._cache_loaded_at) < _CACHE_TTL:
                return
            await self._load_from_mongo()
            self._cache_loaded_at = time.monotonic()

    async def _load_from_mongo(self) -> None:
        try:
            from mozaiksai.core.core_config import get_mongo_client
            client = get_mongo_client()
            if client is None:
                return
            collection = client[_FLAGS_DB][_FLAGS_COLLECTION]
            docs = await collection.find({}).to_list(length=500)
            new_cache: dict[str, Any] = {}
            for doc in docs:
                key = doc.get("flag") or doc.get("_id", "")
                if key:
                    new_cache[str(key)] = doc
            self._mongo_cache = new_cache
            logger.debug("Feature flags loaded from MongoDB: %d flags", len(new_cache))
        except Exception as exc:
            logger.debug("Feature flags MongoDB load failed: %s", exc)

    # ------------------------------------------------------------------
    # Management helpers
    # ------------------------------------------------------------------

    async def set_flag(self, flag: str, enabled: bool, *, app_id: str | None = None) -> None:
        """Write or update a flag in MongoDB.

        Does not affect env-var flags (those take precedence).
        """
        try:
            from mozaiksai.core.core_config import get_mongo_client
            client = get_mongo_client()
            if client is None:
                logger.warning("Cannot set flag '%s' — MongoDB unavailable", flag)
                return
            collection = client[_FLAGS_DB][_FLAGS_COLLECTION]
            key = f"{flag}::{app_id}" if app_id else flag
            await collection.update_one(
                {"_id": key},
                {"$set": {"flag": key, "enabled": enabled, "app_id": app_id}},
                upsert=True,
            )
            # Invalidate cache
            self._cache_loaded_at = 0.0
            logger.info("Feature flag set: %s=%s (app_id=%s)", flag, enabled, app_id)
        except Exception as exc:
            logger.error("Failed to set feature flag '%s': %s", flag, exc)

    def all_env_flags(self) -> dict[str, bool]:
        """Return all flags currently set via environment variables."""
        result: dict[str, bool] = {}
        for key, val in os.environ.items():
            if key.startswith(_FLAG_PREFIX):
                flag_name = key[len(_FLAG_PREFIX):].lower()
                result[flag_name] = val.strip().lower() in ("true", "1", "yes", "on")
        return result


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_flags: FeatureFlags | None = None


def get_flags() -> FeatureFlags:
    """Return the process-wide FeatureFlags singleton."""
    global _flags
    if _flags is None:
        _flags = FeatureFlags()
    return _flags


def is_enabled(flag: str, *, app_id: str | None = None) -> bool:
    """Convenience wrapper — sync, env-var only evaluation."""
    return get_flags().is_enabled(flag, app_id=app_id)


__all__ = ["FeatureFlags", "get_flags", "is_enabled"]

# ==============================================================================
# FILE: mozaiksai/core/cache/redis_cache.py
# DESCRIPTION: Distributed Redis cache layer for JWKS, app context, and
#              module registry — shared across all runtime instances.
#              Falls back to in-memory dict when Redis is unavailable.
#
# Configuration (env vars):
#   REDIS_URL            — Redis connection URL (required to enable; unset = in-memory only)
#   REDIS_CACHE_ENABLED  — set to "false" to disable even when REDIS_URL is set
#   REDIS_CACHE_TTL      — default TTL in seconds (default: 300)
#   REDIS_CACHE_PREFIX   — key prefix (default: "mozaiks:")
#
# Install: pip install "redis[asyncio]"
# ==============================================================================
from __future__ import annotations

import json
import os
import time
from typing import Any

from logs.logging_config import get_core_logger

logger = get_core_logger("redis_cache")

_REDIS_URL = os.getenv("REDIS_URL", "").strip()
_ENABLED = bool(_REDIS_URL) and os.getenv("REDIS_CACHE_ENABLED", "true").lower() not in ("false", "0", "no")
_DEFAULT_TTL = int(os.getenv("REDIS_CACHE_TTL", "300").strip() or "300")
_PREFIX = os.getenv("REDIS_CACHE_PREFIX", "mozaiks:").rstrip(":") + ":"


# ---------------------------------------------------------------------------
# In-memory fallback (single-instance, no Redis)
# ---------------------------------------------------------------------------

class _MemoryCache:
    """In-process fallback when Redis is unavailable."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}  # key → (value, expires_at)

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl: int = _DEFAULT_TTL) -> None:
        self._store[key] = (value, time.monotonic() + ttl)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def clear_prefix(self, prefix: str) -> None:
        to_delete = [k for k in self._store if k.startswith(prefix)]
        for k in to_delete:
            del self._store[k]


_memory_cache = _MemoryCache()


# ---------------------------------------------------------------------------
# Redis-backed cache
# ---------------------------------------------------------------------------

class RedisCache:
    """Distributed cache backed by Redis.

    Values are JSON-serialised before storage. Non-serialisable values
    fall back to repr() and cannot be deserialised — use only with JSON-safe data.

    All operations degrade silently to _MemoryCache on Redis failure.
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._available = False

    async def connect(self) -> bool:
        """Attempt to connect to Redis. Returns True on success."""
        if not _ENABLED:
            logger.info("Redis cache disabled (REDIS_CACHE_ENABLED=false)")
            return False
        try:
            import redis.asyncio as aioredis
            self._client = aioredis.from_url(
                _REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
            )
            await self._client.ping()
            self._available = True
            logger.info("Redis cache connected: %s prefix=%s", _REDIS_URL, _PREFIX)
            return True
        except ImportError:
            logger.info(
                "Redis cache disabled: redis[asyncio] not installed. "
                "Install: pip install 'redis[asyncio]'"
            )
        except Exception as exc:
            logger.warning("Redis cache unavailable: %s — using in-memory fallback", exc)
        self._available = False
        return False

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass

    def _key(self, key: str) -> str:
        return _PREFIX + key

    async def get(self, key: str) -> Any | None:
        """Get a cached value. Returns None if missing or expired."""
        if not self._available or self._client is None:
            return _memory_cache.get(key)
        try:
            raw = await self._client.get(self._key(key))
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            logger.debug("Redis GET failed key=%s: %s — using memory fallback", key, exc)
            return _memory_cache.get(key)

    async def set(self, key: str, value: Any, ttl: int = _DEFAULT_TTL) -> None:
        """Set a cached value with TTL in seconds."""
        # Always update in-memory fallback too.
        _memory_cache.set(key, value, ttl)
        if not self._available or self._client is None:
            return
        try:
            serialised = json.dumps(value, default=str)
            await self._client.setex(self._key(key), ttl, serialised)
        except Exception as exc:
            logger.debug("Redis SET failed key=%s: %s", key, exc)

    async def delete(self, key: str) -> None:
        """Delete a cached key."""
        _memory_cache.delete(key)
        if not self._available or self._client is None:
            return
        try:
            await self._client.delete(self._key(key))
        except Exception as exc:
            logger.debug("Redis DELETE failed key=%s: %s", key, exc)

    async def invalidate_prefix(self, prefix: str) -> None:
        """Invalidate all keys matching a prefix (use sparingly — SCAN-based)."""
        _memory_cache.clear_prefix(prefix)
        if not self._available or self._client is None:
            return
        try:
            full_prefix = self._key(prefix)
            cursor = 0
            while True:
                cursor, keys = await self._client.scan(cursor=cursor, match=full_prefix + "*", count=100)
                if keys:
                    await self._client.delete(*keys)
                if cursor == 0:
                    break
        except Exception as exc:
            logger.debug("Redis invalidate_prefix failed prefix=%s: %s", prefix, exc)

    @property
    def is_available(self) -> bool:
        return self._available

    # ------------------------------------------------------------------
    # Named cache helpers for common use cases
    # ------------------------------------------------------------------

    async def get_jwks(self, issuer: str) -> Any | None:
        return await self.get(f"jwks:{issuer}")

    async def set_jwks(self, issuer: str, jwks: Any, ttl: int = 3600) -> None:
        await self.set(f"jwks:{issuer}", jwks, ttl)

    async def get_app_context(self, app_id: str) -> Any | None:
        return await self.get(f"app_ctx:{app_id}")

    async def set_app_context(self, app_id: str, context: Any, ttl: int = 60) -> None:
        await self.set(f"app_ctx:{app_id}", context, ttl)

    async def invalidate_app_context(self, app_id: str) -> None:
        await self.delete(f"app_ctx:{app_id}")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_redis_cache: RedisCache | None = None


def get_redis_cache() -> RedisCache:
    """Return the process-wide RedisCache singleton."""
    global _redis_cache
    if _redis_cache is None:
        _redis_cache = RedisCache()
    return _redis_cache


__all__ = ["RedisCache", "get_redis_cache"]

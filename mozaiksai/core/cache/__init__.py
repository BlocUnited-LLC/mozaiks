"""Distributed cache layer for JWKS, app context, and module registry.

Uses Redis when available, falls back to in-memory store on a single instance.
"""

from mozaiksai.core.cache.redis_cache import RedisCache, get_redis_cache

__all__ = ["RedisCache", "get_redis_cache"]

# ==============================================================================
# FILE: mozaikscore/core/database.py
# DESCRIPTION: Database layer for the application substrate.
#              Uses the shared Motor client from mozaiksai.core.core_config
#              and exposes mozaikscore-specific collection accessors.
# ==============================================================================
import os
import logging
import asyncio
import functools
import time
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger("mozaikscore.database")

# ---------------------------------------------------------------------------
# Database selection
# ---------------------------------------------------------------------------
_client: Optional[Any] = None
_db: Optional[Any] = None
_db_enterprise: Optional[Any] = None


def _get_client():
    """Lazy singleton for the shared Motor client."""
    global _client
    if _client is None:
        from mozaiksai.core.core_config import get_mongo_client
        _client = get_mongo_client()
    return _client


def get_database():
    """Get the mozaikscore application database.

    Production uses 'client', dev uses 'MozaiksCore' — same convention as the
    legacy mozaiks-core-public database layer.
    """
    global _db
    if _db is None:
        client = _get_client()
        if os.getenv("ENV") == "production":
            _db = client["client"]
        else:
            _db = client["MozaiksCore"]
    return _db


def get_enterprise_database():
    """Get the shared enterprise database (MozaiksDB)."""
    global _db_enterprise
    if _db_enterprise is None:
        _db_enterprise = _get_client()["MozaiksDB"]
    return _db_enterprise


# ---------------------------------------------------------------------------
# Collection accessors
# ---------------------------------------------------------------------------
def get_enterprises_collection():
    return get_enterprise_database()["Enterprises"]


def get_users_collection():
    return get_database()["users"]


def get_settings_collection():
    return get_database()["settings"]


def get_subscriptions_collection():
    return get_database()["subscriptions"]


def get_subscription_history_collection():
    return get_database()["subscription_history"]


def get_billing_history_collection():
    return get_database()["billing_history"]


# ---------------------------------------------------------------------------
# Connection verification
# ---------------------------------------------------------------------------
_is_connected = False
_last_connection_check: float = 0
_connection_check_interval = 60  # seconds


async def verify_connection(force: bool = False) -> bool:
    """Verify connection to MongoDB.  Caches status for 60 s unless force=True."""
    global _is_connected, _last_connection_check

    now = time.time()
    if not force and _is_connected and (now - _last_connection_check) < _connection_check_interval:
        return True

    try:
        client = _get_client()
        await client.admin.command("ping")
        logger.info("Connected to MongoDB")
        _is_connected = True
        _last_connection_check = now
        return True
    except Exception as exc:
        _is_connected = False
        logger.error("MongoDB connection error: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------
def with_retry(max_retries: int = 3, delay: int = 1):
    """Async retry with exponential backoff.  Re-verifies connection on failure."""

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            retries = 0
            current_delay = delay
            while True:
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    retries += 1
                    if retries > max_retries:
                        logger.error(
                            "Max retries (%d) exceeded for %s: %s",
                            max_retries, func.__name__, exc,
                        )
                        raise
                    logger.warning(
                        "Retry %d/%d for %s after error: %s",
                        retries, max_retries, func.__name__, exc,
                    )
                    await asyncio.sleep(current_delay)
                    current_delay *= 2
                    await verify_connection(force=True)

        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# DBCache — in-memory TTL cache for document lookups
# ---------------------------------------------------------------------------
class DBCache:
    """Simple in-memory LRU-ish cache with TTL (seconds)."""

    def __init__(self, max_size: int = 1000, ttl: int = 300):
        self.cache: dict = {}
        self.max_size = max_size
        self.ttl = ttl

    def get(self, key: str):
        if key in self.cache:
            value, ts = self.cache[key]
            if time.time() - ts <= self.ttl:
                return value
            del self.cache[key]
        return None

    def set(self, key: str, value):
        if len(self.cache) >= self.max_size:
            oldest_keys = sorted(
                self.cache, key=lambda k: self.cache[k][1]
            )[: len(self.cache) // 10]
            for k in oldest_keys:
                del self.cache[k]
        self.cache[key] = (value, time.time())

    def invalidate(self, key: str):
        self.cache.pop(key, None)

    def clear(self):
        self.cache.clear()


# Singleton cache
db_cache = DBCache()


def make_cache_key(collection_name: str, document_id: str) -> str:
    return f"{collection_name}:{document_id}"


async def get_cached_document(collection, query: dict, cache_key: Optional[str] = None):
    """Fetch a document, using the cache when possible."""
    if cache_key:
        cached = db_cache.get(cache_key)
        if cached is not None:
            return cached

    document = await collection.find_one(query)

    if cache_key and document:
        db_cache.set(cache_key, document)
    return document


async def update_and_invalidate(collection, query: dict, update: dict, cache_key: Optional[str] = None):
    """Update a document and bust the cache entry."""
    result = await collection.update_one(query, update)
    if cache_key:
        db_cache.invalidate(cache_key)
    return result


# ---------------------------------------------------------------------------
# Index helpers (run once at startup)
# ---------------------------------------------------------------------------
@with_retry(max_retries=5, delay=2)
async def create_settings_indexes():
    coll = get_settings_collection()
    await coll.create_index([("user_id", 1), ("plugin_name", 1)])
    logger.info("Created settings compound index (user_id, plugin_name)")


@with_retry(max_retries=5, delay=1)
async def create_enterprise_index():
    coll = get_enterprises_collection()
    await coll.create_index("AdminId")
    logger.info("Created enterprise index on AdminId")


@with_retry(max_retries=5, delay=1)
async def ensure_enterprise_exists():
    admin_id = os.getenv("AdminId")
    if not admin_id:
        logger.warning("AdminId not set — skipping enterprise seed")
        return
    coll = get_enterprises_collection()
    existing = await coll.find_one({"AdminId": admin_id})
    if not existing:
        await coll.insert_one({
            "AdminId": admin_id,
            "name": os.getenv("EnterpriseName", "Default Enterprise"),
            "created_at": datetime.utcnow().isoformat(),
        })
        logger.info("Created enterprise with AdminId=%s", admin_id)
    else:
        logger.info("Enterprise with AdminId=%s already exists", admin_id)


async def initialize_database():
    """Top-level init — verify connection, create indexes, seed enterprise."""
    await verify_connection()
    await create_enterprise_index()
    await ensure_enterprise_exists()
    await create_settings_indexes()

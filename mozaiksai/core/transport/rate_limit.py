"""
Rate limiting middleware for the Mozaiks runtime.

Reads configuration from environment variables:
  RATE_LIMIT_ENABLED              — true/false (default: true)
  RATE_LIMIT_REQUESTS_PER_MINUTE  — global default RPM per client (default: 60)
  RATE_LIMIT_EXCLUDED_PATHS       — comma-separated paths to skip (default: health endpoints)
  RATE_LIMIT_PATH_LIMITS          — per-path overrides, format: "path:rpm,path:rpm"
  RATE_LIMIT_CLIENT_HEADER        — header to use for client ID behind a proxy (e.g. X-Forwarded-For)
  REDIS_URL                       — Redis connection URL; falls back to in-memory if unset

Built-in tighter limits on high-cost endpoints (can be overridden via RATE_LIMIT_PATH_LIMITS):
  /api/chats   → 10/minute   (workflow session starts, each spawns an LLM context)
  /chat        → 30/minute   (user message sends, primary LLM cost driver)
  /api/workflows → 20/minute (programmatic workflow triggers)
"""
from __future__ import annotations

import logging
import os
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() in {"true", "1", "yes"}
_RPM = int(os.getenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "60"))
_CLIENT_HEADER = os.getenv("RATE_LIMIT_CLIENT_HEADER", "").strip()
_EXCLUDED_PATHS = {
    p.strip()
    for p in os.getenv(
        "RATE_LIMIT_EXCLUDED_PATHS",
        "/api/health,/api/health/live,/api/health/ready",
    ).split(",")
    if p.strip()
}

# Parse per-path overrides from env: "path:rpm,path:rpm"
_env_path_limits: dict[str, int] = {}
_raw_path_limits = os.getenv("RATE_LIMIT_PATH_LIMITS", "").strip()
if _raw_path_limits:
    for _entry in _raw_path_limits.split(","):
        if ":" in _entry:
            _p, _r = _entry.rsplit(":", 1)
            try:
                _env_path_limits[_p.strip()] = int(_r.strip())
            except ValueError:
                logger.warning("Rate limiter: invalid RATE_LIMIT_PATH_LIMITS entry: %s", _entry)

# Default tighter limits on expensive endpoints; env values override these.
_DEFAULT_PATH_LIMITS: dict[str, int] = {
    "/api/chats": 10,
    "/chat": 30,
    "/api/workflows": 20,
}


def _path_limits() -> dict[str, int]:
    return {**_DEFAULT_PATH_LIMITS, **_env_path_limits}


def _get_client_key(request: Request) -> str:
    """Resolve the client identity used as the rate-limit bucket key."""
    if _CLIENT_HEADER:
        val = request.headers.get(_CLIENT_HEADER, "").strip()
        if val:
            # X-Forwarded-For may be comma-separated; use the leftmost (original client) IP.
            return val.split(",")[0].strip()
    client = request.client
    return client.host if client else "unknown"


def _build_storage():
    """Return Redis storage when REDIS_URL is configured, otherwise in-memory."""
    from limits.storage import MemoryStorage

    redis_url = os.getenv("REDIS_URL", "").strip()
    if redis_url:
        try:
            from limits.storage import RedisStorage

            store = RedisStorage(redis_url)
            # Mask credentials in the log.
            safe_url = redis_url.split("@")[-1] if "@" in redis_url else redis_url
            logger.info("Rate limiter: using Redis storage (%s)", safe_url)
            return store
        except Exception as exc:
            logger.warning(
                "Rate limiter: Redis unavailable (%s) — falling back to in-memory storage", exc
            )

    logger.info(
        "Rate limiter: using in-memory storage. "
        "Set REDIS_URL for multi-instance / multi-pod deployments."
    )
    return MemoryStorage()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Per-client rate limiting with path-prefix overrides.

    Request pipeline:
      1. Skip OPTIONS (CORS preflight) and excluded health paths.
      2. Resolve client key (IP or configurable header).
      3. Find the most specific path-prefix limit; fall back to global.
      4. Hit the limiter — reject with 429 if exceeded, attach headers otherwise.
    """

    def __init__(self, app, **kwargs):
        super().__init__(app, **kwargs)

        if not _ENABLED:
            logger.info("Rate limiting disabled (RATE_LIMIT_ENABLED=false)")
            self._enabled = False
            return

        from limits import parse
        from limits.strategies import MovingWindowRateLimiter

        self._enabled = True
        self._storage = _build_storage()
        self._limiter = MovingWindowRateLimiter(self._storage)

        limits_map = _path_limits()
        self._global_limit = parse(f"{_RPM}/minute")
        self._path_limits_parsed = {
            path: parse(f"{rpm}/minute") for path, rpm in limits_map.items()
        }

        logger.info(
            "Rate limiting enabled: global=%d/min, path overrides=%s",
            _RPM,
            {k: v for k, v in limits_map.items()},
        )

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self._enabled:
            return await call_next(request)

        # Never rate-limit CORS preflight.
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path

        if path in _EXCLUDED_PATHS:
            return await call_next(request)

        client_key = _get_client_key(request)

        # Pick the most specific matching path-prefix limit.
        active_limit = self._global_limit
        matched_prefix = "global"
        for prefix, limit_item in self._path_limits_parsed.items():
            if path.startswith(prefix):
                active_limit = limit_item
                matched_prefix = prefix
                break

        # Namespace (client_key, matched_prefix) keeps per-path buckets independent.
        allowed = self._limiter.hit(active_limit, client_key, matched_prefix)
        stats = self._limiter.get_window_stats(active_limit, client_key, matched_prefix)
        remaining = max(0, stats.remaining)
        reset_in = max(0, int(stats.reset_time - time.time()))

        if not allowed:
            logger.warning(
                "Rate limit exceeded: client=%s path=%s prefix=%s",
                client_key,
                path,
                matched_prefix,
            )
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please slow down."},
                headers={
                    "Retry-After": str(reset_in),
                    "X-RateLimit-Limit": str(active_limit.amount),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_in),
                },
            )

        response = await call_next(request)

        # Attach informational rate-limit headers to every allowed response.
        try:
            response.headers["X-RateLimit-Limit"] = str(active_limit.amount)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            response.headers["X-RateLimit-Reset"] = str(reset_in)
        except Exception:
            pass  # Headers are informational; never fail a request over them.

        return response

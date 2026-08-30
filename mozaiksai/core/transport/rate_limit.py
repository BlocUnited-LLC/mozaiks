"""
Rate limiting middleware for the Mozaiks runtime.

Reads configuration from environment variables:
  RATE_LIMIT_ENABLED              — true/false (default: true)
  RATE_LIMIT_REQUESTS_PER_MINUTE  — global default RPM per client (default: 60)
  RATE_LIMIT_EXCLUDED_PATHS       — comma-separated paths to skip (default: health + shell/theme/me infrastructure endpoints)
  RATE_LIMIT_PATH_LIMITS          — per-path overrides, format: "path:rpm,path:rpm"
  RATE_LIMIT_CLIENT_HEADER        — header to use for client ID behind a proxy (e.g. X-Forwarded-For)
  REDIS_URL                       — Redis connection URL; falls back to in-memory if unset

Built-in tighter limits on high-cost endpoints (can be overridden via RATE_LIMIT_PATH_LIMITS):
  /api/auth    → 20/minute   (token endpoints — brute-force protection)
  /api/chats/exists → 120/minute (cheap session resume checks)
  /api/chats/meta → 120/minute (cheap session metadata checks)
  /api/chats   → 10/minute   (workflow session starts, each spawns an LLM context)
  /chat        → 30/minute   (user message sends, primary LLM cost driver)
  /api/workflows → 120/minute (read-only workflow catalog listing; fetched on every page load and HMR reload)
  /ws/         → 10/minute   (WebSocket upgrade requests — each starts a new workflow context)
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

# Default tighter limits on expensive endpoints; env values override these.
_DEFAULT_PATH_LIMITS: dict[str, int] = {
    "/api/auth": 20,      # auth token endpoints — brute-force protection
    "/api/chats/exists": 120,
    "/api/chats/meta": 120,
    "/api/chats": 10,
    "/chat": 30,
    "/api/workflows": 120,  # read-only catalog listing; fetched on page load and HMR reloads
    "/ws/": 10,           # WebSocket upgrades — each opens a new workflow context
}


def _path_limits() -> dict[str, int]:
    env_path_limits: dict[str, int] = {}
    raw_path_limits = os.getenv("RATE_LIMIT_PATH_LIMITS", "").strip()
    if raw_path_limits:
        for entry in raw_path_limits.split(","):
            if ":" not in entry:
                continue
            path, rpm = entry.rsplit(":", 1)
            try:
                env_path_limits[path.strip()] = int(rpm.strip())
            except ValueError:
                logger.warning("Rate limiter: invalid RATE_LIMIT_PATH_LIMITS entry: %s", entry)
    return {**_DEFAULT_PATH_LIMITS, **env_path_limits}


def _rate_limit_enabled() -> bool:
    return os.getenv("RATE_LIMIT_ENABLED", "true").lower() in {"true", "1", "yes"}


def _requests_per_minute() -> int:
    return int(os.getenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "60"))


def _client_header() -> str:
    return os.getenv("RATE_LIMIT_CLIENT_HEADER", "").strip()


def _excluded_paths() -> set[str]:
    return {
        path.strip()
        for path in os.getenv(
            "RATE_LIMIT_EXCLUDED_PATHS",
            (
                "/api/health,/api/health/live,/api/health/ready,/api/health/readiness,"
                "/api/shell-config,/api/theme-config,/api/me,/api/me/preferences"
            ),
        ).split(",")
        if path.strip()
    }


def _get_client_key(request: Request, client_header: str) -> str:
    """Resolve the client identity used as the rate-limit bucket key."""
    if client_header:
        val = request.headers.get(client_header, "").strip()
        if val:
            # X-Forwarded-For may be comma-separated; use the leftmost (original client) IP.
            return val.split(",")[0].strip()
    client = request.client
    return client.host if client else "unknown"


def _select_path_limit(path: str, parsed_limits: dict[str, Any], global_limit: Any) -> tuple[str, Any]:
    """Return the most specific configured path-prefix limit for a request path."""
    for prefix in sorted(parsed_limits, key=len, reverse=True):
        if path.startswith(prefix):
            return prefix, parsed_limits[prefix]
    return "global", global_limit


def _build_storage():
    """Return Redis storage when REDIS_URL is configured, otherwise in-memory.

    RedisStorage uses a lazy connection internally; the constructor does not
    attempt to connect.  We call ``store.check()`` (a synchronous ping) to
    verify the server is reachable before handing the storage to the limiter.
    If the ping fails we fall back to per-worker in-memory storage so that a
    misconfigured or temporarily unavailable Redis does not make the whole rate
    limiter raise on every request.
    """
    from limits.storage import MemoryStorage

    redis_url = os.getenv("REDIS_URL", "").strip()
    if redis_url:
        try:
            from limits.storage import RedisStorage

            store = RedisStorage(redis_url)
            if not store.check():
                raise ConnectionError("Redis ping returned False — server unreachable")
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

        if not _rate_limit_enabled():
            logger.info("Rate limiting disabled (RATE_LIMIT_ENABLED=false)")
            self._enabled = False
            return

        from limits import parse
        from limits.strategies import MovingWindowRateLimiter

        self._enabled = True
        self._client_header = _client_header()
        self._excluded_paths = _excluded_paths()
        self._storage = _build_storage()
        self._limiter = MovingWindowRateLimiter(self._storage)

        limits_map = _path_limits()
        requests_per_minute = _requests_per_minute()
        self._global_limit = parse(f"{requests_per_minute}/minute")
        self._path_limits_parsed = {
            path: parse(f"{rpm}/minute") for path, rpm in limits_map.items()
        }

        logger.info(
            "Rate limiting enabled: global=%d/min, path overrides=%s",
            requests_per_minute,
            {k: v for k, v in limits_map.items()},
        )

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self._enabled:
            return await call_next(request)  # type: ignore[no-any-return]

        # Never rate-limit CORS preflight.
        if request.method == "OPTIONS":
            return await call_next(request)  # type: ignore[no-any-return]

        path = request.url.path

        if path in self._excluded_paths:
            return await call_next(request)  # type: ignore[no-any-return]

        client_key = _get_client_key(request, self._client_header)

        matched_prefix, active_limit = _select_path_limit(
            path,
            self._path_limits_parsed,
            self._global_limit,
        )

        # Namespace (client_key, matched_prefix) keeps per-path buckets independent.
        # Fail open: if the backing storage is transiently unavailable (e.g. Redis
        # connection refused) allow the request through rather than returning 500.
        try:
            allowed = self._limiter.hit(active_limit, client_key, matched_prefix)
            stats = self._limiter.get_window_stats(active_limit, client_key, matched_prefix)
            remaining = max(0, stats.remaining)
            reset_in = max(0, int(stats.reset_time - time.time()))
        except Exception as exc:
            logger.warning(
                "Rate limiter: storage error — allowing request through: %s", exc
            )
            return await call_next(request)  # type: ignore[no-any-return]

        if not allowed:
            logger.warning(
                "Rate limit exceeded: client=%s path=%s prefix=%s",
                client_key,
                path,
                matched_prefix,
            )
            # Include CORS header on the 429 so the browser reports it as a
            # rate-limit error rather than a confusing CORS policy violation.
            # The rate-limit middleware sits outside the CORS middleware, so it
            # must add the header itself when it short-circuits the response.
            response_headers: dict[str, str] = {
                "Retry-After": str(reset_in),
                "X-RateLimit-Limit": str(active_limit.amount),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(reset_in),
            }
            origin = request.headers.get("origin", "")
            if origin:
                response_headers["Access-Control-Allow-Origin"] = origin
                response_headers["Access-Control-Allow-Credentials"] = "true"
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please slow down."},
                headers=response_headers,
            )

        response = await call_next(request)

        # Attach informational rate-limit headers to every allowed response.
        try:
            response.headers["X-RateLimit-Limit"] = str(active_limit.amount)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            response.headers["X-RateLimit-Reset"] = str(reset_in)
        except Exception:
            pass  # Headers are informational; never fail a request over them.

        return response  # type: ignore[no-any-return]

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[override]
        """ASGI entry point — applies rate limiting to both HTTP and WebSocket scopes."""
        if scope["type"] != "websocket" or not self._enabled:
            await super().__call__(scope, receive, send)
            return

        # WebSocket rate limiting: check on the upgrade request before accepting.
        path = scope.get("path", "")

        if path in self._excluded_paths:
            await super().__call__(scope, receive, send)
            return

        # Resolve client key from scope headers.
        from starlette.datastructures import Headers

        headers = Headers(scope=scope)
        client_key: str
        if self._client_header:
            val = headers.get(self._client_header, "").strip()
            client_key = val.split(",")[0].strip() if val else "unknown"
        else:
            client_info = scope.get("client")
            client_key = client_info[0] if client_info else "unknown"

        matched_prefix, active_limit = _select_path_limit(
            path,
            self._path_limits_parsed,
            self._global_limit,
        )

        try:
            allowed = self._limiter.hit(active_limit, client_key, matched_prefix)
        except Exception as exc:
            logger.warning(
                "Rate limiter: storage error (websocket) — allowing through: %s", exc
            )
            await super().__call__(scope, receive, send)
            return

        if not allowed:
            logger.warning(
                "Rate limit exceeded (websocket): client=%s path=%s prefix=%s",
                client_key,
                path,
                matched_prefix,
            )
            # Complete the WebSocket handshake just enough to close it cleanly.
            # The ASGI WS protocol requires we receive the connect event before
            # sending close; never closing leaves the client hanging.
            event = await receive()
            if event.get("type") == "websocket.connect":
                await send({"type": "websocket.close", "code": 1008, "reason": "Rate limit exceeded"})
            return

        await super().__call__(scope, receive, send)

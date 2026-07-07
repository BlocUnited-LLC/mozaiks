"""
Request-level middleware: body size limiting and request ID propagation.

Body size limiting:
  Rejects requests whose Content-Length header exceeds a configurable max
  before reading the body. This prevents DoS via unbounded uploads on JSON
  endpoints that do not use chunked upload paths.

  Configuration:
    MAX_REQUEST_BODY_BYTES   — max body bytes (default: 1MB = 1_048_576).
                               Set to 0 to disable.
    MAX_REQUEST_BODY_EXCLUDED_PATHS — comma-separated path prefixes exempt from
                               the limit (default: /api/upload,/api/attachments).

Request ID propagation:
  Every request gets a unique X-Request-ID. If the client supplies one it is
  used as-is (max 128 chars, safe chars only); otherwise a UUID is generated.
  The ID is echoed back on the response header and bound to request.state so
  downstream handlers, logs, and tools can reference it for tracing.
"""
from __future__ import annotations

import logging
import os
import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mozaiksai.core.tracing.context import bind_trace_id

logger = logging.getLogger(__name__)

_DEFAULT_MAX_BYTES = 1_048_576  # 1 MB
_DEFAULT_EXCLUDED_PREFIXES = "/api/upload,/api/attachments"
_SAFE_REQUEST_ID_RE = re.compile(r'^[\w\-\.]{1,128}$')


def _max_bytes() -> int:
    raw = os.getenv("MAX_REQUEST_BODY_BYTES", str(_DEFAULT_MAX_BYTES)).strip()
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning("MAX_REQUEST_BODY_BYTES is not an integer (%r); using default", raw)
        return _DEFAULT_MAX_BYTES


def _excluded_prefixes() -> set[str]:
    raw = os.getenv("MAX_REQUEST_BODY_EXCLUDED_PATHS", _DEFAULT_EXCLUDED_PREFIXES)
    return {p.strip() for p in raw.split(",") if p.strip()}


class RequestBodySizeLimitMiddleware(BaseHTTPMiddleware):
    """
    Reject requests whose Content-Length header exceeds MAX_REQUEST_BODY_BYTES.

    Only inspects the Content-Length header; does not buffer the body. This is
    a fast first-pass check that stops obvious abuse before the route handler
    reads the payload. Chunked requests without Content-Length pass through
    (the upload endpoints handle their own size enforcement).

    Never limits OPTIONS (CORS preflight) or GET/HEAD requests.
    """

    def __init__(self, app, **kwargs):
        super().__init__(app, **kwargs)
        self._max_bytes = _max_bytes()
        self._excluded = _excluded_prefixes()
        if self._max_bytes:
            logger.info(
                "Request body size limit: %d bytes (excluded: %s)",
                self._max_bytes,
                self._excluded,
            )
        else:
            logger.info("Request body size limit disabled (MAX_REQUEST_BODY_BYTES=0)")

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self._max_bytes:
            return await call_next(request)  # type: ignore[no-any-return]

        if request.method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)  # type: ignore[no-any-return]

        path = request.url.path
        for prefix in self._excluded:
            if path.startswith(prefix):
                return await call_next(request)  # type: ignore[no-any-return]

        content_length = request.headers.get("content-length")
        if content_length:
            try:
                length = int(content_length)
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid Content-Length header"},
                )
            if length > self._max_bytes:
                logger.warning(
                    "Request body too large: %d bytes > %d limit, path=%s",
                    length,
                    self._max_bytes,
                    path,
                )
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": f"Request body too large. Maximum allowed: {self._max_bytes} bytes."
                    },
                )

        return await call_next(request)  # type: ignore[no-any-return]


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Attach a unique X-Request-ID to every request and response.

    If the client sends X-Request-ID, it is validated (safe chars, max 128 chars)
    and used as-is. Otherwise a UUID4 is generated. The ID is bound to
    request.state.request_id for use by handlers, tools, and log formatters.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get("x-request-id", "").strip()
        if incoming and _SAFE_REQUEST_ID_RE.match(incoming):
            request_id = incoming
        else:
            request_id = str(uuid.uuid4())

        request.state.request_id = request_id
        # Propagate into async context so child tasks (audit, fire-and-forget)
        # inherit the trace ID without requiring explicit parameter threading.
        bind_trace_id(request_id)

        response = await call_next(request)
        try:
            response.headers["X-Request-ID"] = request_id
        except Exception:
            pass  # Never fail a request over a response header
        return response  # type: ignore[no-any-return]

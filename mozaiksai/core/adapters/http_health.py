# ==============================================================================
# FILE: mozaiksai/core/adapters/http_health.py
# DESCRIPTION: Provider-neutral HTTP health check probe.
#
# Uses httpx (already a Mozaiks dependency).
# Returns status code, latency, redirect chain, and content metadata.
#
# No external credentials required. Safe to call from any workflow tool
# or module service that needs to verify a URL is reachable and healthy.
# ==============================================================================
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("mozaiksai.adapters.http_health")

_DEFAULT_TIMEOUT = 15.0
_DEFAULT_USER_AGENT = "mozaiks-health-probe/1.0"


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


class HttpHealthResult:
    """Result of an HTTP health check probe."""

    __slots__ = (
        "url",
        "success",
        "status_code",
        "response_ms",
        "redirected",
        "redirect_count",
        "final_url",
        "content_type",
        "content_length",
        "server",
        "error",
    )

    def __init__(
        self,
        url: str,
        success: bool,
        status_code: Optional[int],
        response_ms: float,
        redirected: bool,
        redirect_count: int,
        final_url: Optional[str],
        content_type: Optional[str],
        content_length: Optional[int],
        server: Optional[str],
        error: Optional[str],
    ) -> None:
        self.url = url
        self.success = success
        self.status_code = status_code
        self.response_ms = response_ms
        self.redirected = redirected
        self.redirect_count = redirect_count
        self.final_url = final_url
        self.content_type = content_type
        self.content_length = content_length
        self.server = server
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "success": self.success,
            "status_code": self.status_code,
            "response_ms": self.response_ms,
            "redirected": self.redirected,
            "redirect_count": self.redirect_count,
            "final_url": self.final_url,
            "content_type": self.content_type,
            "content_length": self.content_length,
            "server": self.server,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------


async def probe_http(
    url: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    follow_redirects: bool = True,
    headers: Optional[Dict[str, str]] = None,
) -> HttpHealthResult:
    """Probe *url* and return health metadata.

    Performs a HEAD request first (lightweight); falls back to GET if the
    server returns 405 Method Not Allowed.

    Args:
        url: Full URL including scheme (http:// or https://).
        timeout: Request timeout in seconds.
        follow_redirects: Whether to follow HTTP redirects (default True).
        headers: Optional extra headers to include.

    Returns:
        HttpHealthResult with status, latency, redirect info, and content metadata.
    """
    merged_headers = {"User-Agent": _DEFAULT_USER_AGENT}
    if headers:
        merged_headers.update(headers)

    client_kwargs: Dict[str, Any] = {
        "timeout": httpx.Timeout(timeout, connect=10.0),
        "follow_redirects": follow_redirects,
        "headers": merged_headers,
    }

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.head(url)

            # Fall back to GET if HEAD is not allowed
            if resp.status_code == 405:
                resp = await client.get(url)

        elapsed_ms = (time.monotonic() - start) * 1000

        redirect_count = len(resp.history)
        redirected = redirect_count > 0
        final_url = str(resp.url) if redirected else None

        ct_header = resp.headers.get("content-type", "")
        content_type = ct_header.split(";")[0].strip() or None

        cl_raw = resp.headers.get("content-length")
        content_length: Optional[int] = None
        if cl_raw and cl_raw.isdigit():
            content_length = int(cl_raw)

        server = resp.headers.get("server") or resp.headers.get("x-powered-by") or None

        success = 200 <= resp.status_code < 400

        return HttpHealthResult(
            url=url,
            success=success,
            status_code=resp.status_code,
            response_ms=round(elapsed_ms, 2),
            redirected=redirected,
            redirect_count=redirect_count,
            final_url=final_url,
            content_type=content_type,
            content_length=content_length,
            server=server,
            error=None,
        )

    except httpx.TimeoutException as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        return HttpHealthResult(
            url=url,
            success=False,
            status_code=None,
            response_ms=round(elapsed_ms, 2),
            redirected=False,
            redirect_count=0,
            final_url=None,
            content_type=None,
            content_length=None,
            server=None,
            error=f"Timeout after {timeout}s: {exc}",
        )
    except httpx.HTTPError as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        return HttpHealthResult(
            url=url,
            success=False,
            status_code=None,
            response_ms=round(elapsed_ms, 2),
            redirected=False,
            redirect_count=0,
            final_url=None,
            content_type=None,
            content_length=None,
            server=None,
            error=str(exc),
        )


__all__ = ["HttpHealthResult", "probe_http"]

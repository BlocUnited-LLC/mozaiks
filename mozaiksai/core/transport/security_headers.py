"""
HTTP security headers middleware for the Mozaiks runtime.

Adds defensive HTTP response headers to every request that prevent common
browser-level attacks: clickjacking, MIME sniffing, information leakage,
cross-origin data access, and referrer exposure.

Configuration via environment variables (all optional):

  SECURITY_HEADERS_ENABLED        — true/false (default: true)
  HSTS_MAX_AGE                    — seconds for Strict-Transport-Security max-age (default: 31536000 / 1 year)
  HSTS_INCLUDE_SUBDOMAINS         — true/false (default: true)
  CONTENT_SECURITY_POLICY         — override the full CSP header value; default is a permissive
                                    but defensive baseline suitable for API + WebSocket services
  REFERRER_POLICY                 — override Referrer-Policy; default: strict-origin-when-cross-origin
  PERMISSIONS_POLICY              — override Permissions-Policy; default disables sensitive APIs

The middleware never sets security headers on responses that already have them,
so hosted product layers can override per-route values without fighting the middleware.
"""
from __future__ import annotations

import logging
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

# Default CSP is intentionally permissive for API+WS services:
# - 'self' for all fetch/script/style directives
# - wss: for WebSocket upgrade handshakes
# - frame-ancestors 'none' blocks embedding in iframes (clickjacking protection)
_DEFAULT_CSP = (
    "default-src 'self'; "
    "connect-src 'self' wss:; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "base-uri 'self'"
)
_DEFAULT_REFERRER_POLICY = "strict-origin-when-cross-origin"
_DEFAULT_PERMISSIONS_POLICY = (
    "geolocation=(), "
    "microphone=(), "
    "camera=(), "
    "payment=(), "
    "usb=(), "
    "magnetometer=(), "
    "gyroscope=(), "
    "accelerometer=()"
)


def _enabled() -> bool:
    return os.getenv("SECURITY_HEADERS_ENABLED", "true").lower() in {"true", "1", "yes"}


def _hsts_max_age() -> int:
    return int(os.getenv("HSTS_MAX_AGE", "31536000"))


def _hsts_include_subdomains() -> bool:
    return os.getenv("HSTS_INCLUDE_SUBDOMAINS", "true").lower() in {"true", "1", "yes"}


def _csp() -> str:
    return os.getenv("CONTENT_SECURITY_POLICY", _DEFAULT_CSP).strip()


def _referrer_policy() -> str:
    return os.getenv("REFERRER_POLICY", _DEFAULT_REFERRER_POLICY).strip()


def _permissions_policy() -> str:
    return os.getenv("PERMISSIONS_POLICY", _DEFAULT_PERMISSIONS_POLICY).strip()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Add HTTP security headers to every response.

    Headers set (when not already present on the response):
      - X-Content-Type-Options: nosniff
      - X-Frame-Options: DENY
      - Strict-Transport-Security: max-age=<n>[; includeSubDomains]
      - Referrer-Policy: <policy>
      - Permissions-Policy: <policy>
      - Content-Security-Policy: <policy>
      - X-Permitted-Cross-Domain-Policies: none

    Does not override headers the route handler set explicitly.
    Does not add HSTS on non-HTTPS requests (avoids breaking local HTTP dev).
    """

    def __init__(self, app, **kwargs):
        super().__init__(app, **kwargs)
        self._enabled = _enabled()
        if not self._enabled:
            logger.info("Security headers middleware disabled (SECURITY_HEADERS_ENABLED=false)")
            return

        hsts_age = _hsts_max_age()
        hsts_subdomains = _hsts_include_subdomains()
        hsts_value = f"max-age={hsts_age}"
        if hsts_subdomains:
            hsts_value += "; includeSubDomains"

        self._headers: dict[str, str] = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "X-Permitted-Cross-Domain-Policies": "none",
            "Referrer-Policy": _referrer_policy(),
            "Permissions-Policy": _permissions_policy(),
            "Content-Security-Policy": _csp(),
            "_hsts_value": hsts_value,  # injected conditionally below
        }
        logger.info(
            "Security headers middleware enabled: HSTS=%s, CSP=%s",
            hsts_value,
            self._headers["Content-Security-Policy"][:60] + "...",
        )

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self._enabled:
            return await call_next(request)  # type: ignore[no-any-return]

        response = await call_next(request)

        for header, value in self._headers.items():
            if header.startswith("_"):
                continue
            if header not in response.headers:
                response.headers[header] = value

        # HSTS: only on HTTPS connections (avoids downgrade loops on local HTTP dev)
        if request.url.scheme == "https" and "Strict-Transport-Security" not in response.headers:
            response.headers["Strict-Transport-Security"] = self._headers["_hsts_value"]

        return response  # type: ignore[no-any-return]

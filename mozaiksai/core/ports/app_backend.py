# ==============================================================================
# FILE: mozaiksai/core/ports/app_backend.py
# DESCRIPTION: AppBackendPort — engine-agnostic, backend-agnostic contract
#              between the AI runtime and any external app backend.
#              It provides a generic HTTP call surface and an event emit surface so that
#              any CRUD backend — greenfield templates or otherwise — can be
#              reached without coupling the AI runtime to a specific API shape.
# ==============================================================================
"""AppBackendPort — generic contract for AI-runtime ↔ app-backend communication."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class BackendResponse:
    """Result of a generic backend call."""

    success: bool
    status_code: int = 0
    data: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class BackendHealth:
    """Health status from any app backend."""

    healthy: bool
    version: str = "unknown"
    details: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Port protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class AppBackendPort(Protocol):
    """Engine-agnostic, backend-agnostic contract for app-backend operations.

    Implementors:
        HttpAppBackendAdapter — generic HTTP adapter (default)

    Consumers:
        AG2 tool functions    — workflow agents call the backend
        transport layer       — admin API proxying

    The port is deliberately thin: one generic request method, one event
    emitter, one health check.  Backend-specific semantics (which path to
    call, what payload shape to send) live in workflow configuration or
    AG2 tool function arguments — not in the port.
    """

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        user_token: str | None = None,
    ) -> BackendResponse:
        """Execute an HTTP request against the app backend.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, PATCH).
            path: URL path relative to the backend base URL (e.g. "/api/users").
            json_body: Optional JSON payload.
            headers: Optional extra headers merged with defaults.
            user_token: Optional JWT forwarded as Authorization: Bearer.

        Returns:
            BackendResponse with status, data, and error info.
        """
        ...

    async def emit(self, event_type: str, data: dict[str, Any]) -> bool:
        """Emit a domain event to the runtime event system.

        Args:
            event_type: Dot-delimited event name (e.g. "listing.saved").
            data: Arbitrary event payload.

        Returns:
            True if the event was dispatched successfully.
        """
        ...

    async def health(self) -> BackendHealth:
        """Check whether the app backend is reachable."""
        ...


__all__ = [
    "AppBackendPort",
    "BackendResponse",
    "BackendHealth",
]

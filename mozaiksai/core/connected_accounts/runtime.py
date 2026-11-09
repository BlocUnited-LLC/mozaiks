"""How a plugin module obtains the calling user's connected account.

Use it from a module action handler::

    from mozaiksai.core.connected_accounts import account_request, google_api_base_url

    async def list_upcoming_events(self, ctx, **params):
        response = await account_request(
            ctx, "google_calendar", "GET", f"{google_api_base_url()}/calendar/v3/calendars/primary/events",
        )
        ...  # the provider answered this user's request

The token is for the user making the request, and covers only the scopes the
named connection declares in the module's ``contracts/account_connections.yaml``.
If the user has not connected (or must reconnect), the call raises
:class:`AccountConnectionRequired`, which the executor reports as
``ACCOUNT_CONNECTION_REQUIRED`` so the page or workflow can ask the user to
connect. :func:`account_access_token` returns the token itself, for a client
library; when the provider refuses that token (HTTP 401), ask again once with
``refresh=True``, so a revoked grant raises :class:`AccountConnectionRequired`
instead of failing on every call. :func:`account_request` does that for you.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from logs.logging_config import get_core_logger
from mozaiksai.core.runtime.composition.module_authority import ModuleDispatchAuthority

from .errors import AccountAccessDenied, AccountConnectionRequired, AccountProviderUnavailable
from .service import get_connected_accounts_service

logger = get_core_logger("connected_accounts")

# A dispatch made by the end user themselves: their HTTP request, their own live
# workflow session, or local development. Every other dispatch (anonymous visitors,
# event reactions, framework, app-internal or operator work) is server-owned and
# can never act with a user's third-party account.
_USER_DISPATCH_KINDS = frozenset({"authenticated_user", "workflow", "local_development"})
_API_TIMEOUT_SECONDS = 15.0


def api_http_client() -> httpx.AsyncClient:
    """The HTTP client for provider API calls; tests replace this factory."""
    return httpx.AsyncClient(timeout=_API_TIMEOUT_SECONDS)


async def account_access_token(ctx: Any, connection_id: str, *, refresh: bool = False) -> str:
    authority = getattr(ctx, "dispatch_authority", None)
    if not isinstance(authority, ModuleDispatchAuthority) or authority.kind not in _USER_DISPATCH_KINDS:
        raise AccountAccessDenied("Only the signed-in user's own requests can use their connected account.")
    user_id = str(getattr(ctx, "user_id", "") or "").strip()
    actor_id = str(authority.actor_id or "").strip()
    if not user_id or actor_id != user_id:
        raise AccountAccessDenied("A connected account is used only for the user who is making the request.")
    service = get_connected_accounts_service()
    if service is None or service.app_id != str(getattr(ctx, "app_id", "") or ""):
        # A host that runs modules without wiring connected accounts (or a dispatch for
        # another app) cannot offer the connection: that is the plugin's ordinary
        # "not available" answer, never a server error.
        logger.warning(
            "CONNECTED_ACCOUNTS_NOT_CONFIGURED: connection=%s app_id=%s",
            connection_id, getattr(ctx, "app_id", None),
        )
        raise AccountConnectionRequired(
            connection_id=connection_id, provider="", label=connection_id.replace("_", " ").title(),
            reason="unavailable",
        )
    return await service.access_token(user_id=user_id, connection_id=connection_id, refresh=refresh)


async def account_request(
    ctx: Any,
    connection_id: str,
    method: str,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    json: Any = None,
    headers: Mapping[str, str] | None = None,
) -> httpx.Response:
    """Call a provider API as the calling user with *connection_id*'s token.

    A 401 is retried once with a freshly refreshed token. Any other response is
    returned as it came; the caller decides what a provider error means. The
    token is never part of an exception or a log line.
    """
    token = await account_access_token(ctx, connection_id)
    attempt = 1
    while True:
        request_headers = {"Accept": "application/json", **dict(headers or {}), "Authorization": f"Bearer {token}"}
        try:
            async with api_http_client() as http:
                response = await http.request(method, url, params=params, json=json, headers=request_headers)
        except httpx.HTTPError as exc:
            raise AccountProviderUnavailable(f"The provider could not be reached ({type(exc).__name__}).") from exc
        if response.status_code != 401 or attempt == 2:
            return response
        attempt += 1
        token = await account_access_token(ctx, connection_id, refresh=True)


__all__ = ["account_access_token", "account_request", "api_http_client"]

"""End users connect their own third-party accounts; the app's admins choose what is offered.

Plugins (for example Google Calendar or Gmail) declare the account connection
they need in ``contracts/account_connections.yaml``; see
:mod:`mozaiksai.core.connected_accounts` for the model.

The signed-in user, for their own connections only:

* ``GET /api/me/connected-accounts``: the connections the app offers, each
  with this user's status, and the accounts the user has connected.
* ``POST /api/me/connected-accounts/{connection_id}/connect``: start a
  connection. Returns the provider's authorize URL and a flow id, and sets the
  browser-binding cookie. The page opens the URL in a popup: a popup
  navigation cannot send the bearer header, so the start is a fetch.
* ``GET /api/me/connected-accounts/flows/{flow_id}``: the outcome of the
  user's flow, polled by the page that opened the popup.
* ``DELETE /api/me/connected-accounts/providers/{provider}``: disconnect.
  The grant is revoked at the provider and forgotten here.

``GET /api/connected-accounts/callback`` is the provider's redirect target and
the only route here without a bearer token. It accepts a server-issued,
unexpired ``state`` once, and only from the browser holding that flow's binding
cookie, so a flow one person started cannot be completed with another person's
consent.

The app's own admins (the ``/api/admin`` gate: the ``admin`` role, or a
verified email on the ``app.json`` admins list):

* ``GET /api/admin/connected-accounts``: every declared connection, whether
  it is offered and whether the app's OAuth client is configured, the
  redirect URI to register with the provider, and aggregate counts. Never a
  user's identity or token.
* ``PUT /api/admin/connected-accounts/{connection_id}`` with
  ``{"offered": bool}``: offer or withhold a connection. Audited.

Browser binding cookie: outside local development it is
``__Host-mozaiks_account_connect_binding`` (``Secure``, ``Path=/``, no
``Domain``), which only this exact host can set, over HTTPS. Local
development (authentication off, development access, a request from this
machine) uses the unprefixed ``mozaiks_account_connect_binding`` without
``Secure``. The reasoning is the same as for the GitHub flow in
:mod:`mozaiksai.hosts.routers.oauth_github`.

The redirect URI is ``MOZAIKS_CONNECTED_ACCOUNTS_REDIRECT_URI`` when set,
otherwise this route's URL as the request reached the host. Set it in any
deployment behind a proxy, so it matches what is registered with the provider
exactly.
"""

from __future__ import annotations

import html
import logging
import os
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, StrictBool

from mozaiksai.core.admin.router import _require_admin
from mozaiksai.core.auth.anonymous_access import is_local_client
from mozaiksai.core.auth.dependencies import UserPrincipal, require_user
from mozaiksai.core.connected_accounts import (
    AccountConnectionRequired,
    ConnectedAccountsService,
    UnknownAccountConnection,
    get_connected_accounts_service,
)

router = APIRouter(tags=["connected-accounts"])
logger = logging.getLogger(__name__)

REDIRECT_URI_ENV = "MOZAIKS_CONNECTED_ACCOUNTS_REDIRECT_URI"
CALLBACK_ROUTE_NAME = "connected_accounts_callback"


@dataclass(frozen=True)
class _BindingCookie:
    name: str
    path: str
    secure: bool


#: The binding cookie everywhere except local development (see module docstring).
SECURE_BINDING_COOKIE = _BindingCookie("__Host-mozaiks_account_connect_binding", "/", True)
#: The binding cookie of a flow started in local development only.
LOCAL_BINDING_COOKIE = _BindingCookie("mozaiks_account_connect_binding", "/api/connected-accounts", False)
_BINDING_COOKIES = {cookie.name: cookie for cookie in (SECURE_BINDING_COOKIE, LOCAL_BINDING_COOKIE)}
# A started flow waits this long for the provider's callback; the cookie lives as long.
_BINDING_MAX_AGE_SECONDS = 600

_NO_STORE = {"Cache-Control": "no-store"}


def _service(request: Request) -> ConnectedAccountsService:
    service = get_connected_accounts_service()
    loaded_app_id = getattr(request.app.state, "loaded_app_id", None)
    if service is None or not loaded_app_id or service.app_id != loaded_app_id:
        raise HTTPException(status_code=404, detail="Connected accounts are not available in this app.")
    return service


async def _require_account_owner(principal: UserPrincipal = Depends(require_user)) -> str:
    """The user a connection belongs to: a token-validated user, or local development access.

    An anonymous visitor (``AUTH_ANON_ACCESS=public``) has no identity to bind a
    personal account to.
    """
    user_id = str(principal.user_id or "").strip()
    if not user_id or not (principal.is_authenticated or principal.has_local_development_access):
        raise HTTPException(status_code=403, detail="Connecting an account requires a signed-in user")
    return user_id


def _binding_cookie_for(request: Request, principal: UserPrincipal) -> _BindingCookie:
    if principal.has_local_development_access and is_local_client(request.scope):
        return LOCAL_BINDING_COOKIE
    return SECURE_BINDING_COOKIE


def _cookie_values(request: Request, name: str) -> list[str]:
    """Every value the request carries for cookie *name* (``request.cookies`` keeps only the last)."""
    values: list[str] = []
    for header in request.headers.getlist("cookie"):
        for pair in header.split(";"):
            key, separator, value = pair.partition("=")
            if separator and key.strip() == name:
                values.append(value.strip())
    return values


def _redirect_uri(request: Request) -> str:
    configured = os.getenv(REDIRECT_URI_ENV, "").strip()
    return configured or str(request.url_for(CALLBACK_ROUTE_NAME))


# ---------------------------------------------------------------------------
# Popup pages. The popup shows a fixed message; nothing from the request is
# reflected into it. Success closes itself (the opener learns the outcome by
# polling the flow); a failure stays open so the reason stays readable.
# ---------------------------------------------------------------------------
_POPUP_CSP = "default-src 'none'; style-src 'unsafe-inline'"
_POPUP_CLOSING_CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'"
_POPUP_CLOSE_SCRIPT = (
    "<script>setTimeout(function () { try { window.close(); } catch (e) {} }, 300);</script>"
)
_POPUP_PAGE = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>{title}</title></head>
<body>
<p style="font-family:sans-serif;padding:2rem">{message}</p>
{script}
</body>
</html>"""


def _popup(title: str, message: str, status_code: int, *, closes: bool) -> HTMLResponse:
    return HTMLResponse(
        _POPUP_PAGE.format(
            title=html.escape(title), message=html.escape(message), script=_POPUP_CLOSE_SCRIPT if closes else "",
        ),
        status_code=status_code,
        headers={"Content-Security-Policy": _POPUP_CLOSING_CSP if closes else _POPUP_CSP, **_NO_STORE},
    )


# ---------------------------------------------------------------------------
# The signed-in user's own connections
# ---------------------------------------------------------------------------

@router.get("/api/me/connected-accounts")
async def list_connected_accounts(request: Request, user_id: str = Depends(_require_account_owner)) -> JSONResponse:
    return JSONResponse(await _service(request).list_for_user(user_id), headers=_NO_STORE)


@router.post("/api/me/connected-accounts/{connection_id}/connect")
async def start_account_connection(
    connection_id: str,
    request: Request,
    user_id: str = Depends(_require_account_owner),
    principal: UserPrincipal = Depends(require_user),
) -> JSONResponse:
    service = _service(request)
    cookie = _binding_cookie_for(request, principal)
    try:
        started = await service.start(
            user_id=user_id,
            connection_id=connection_id,
            redirect_uri=_redirect_uri(request),
            binding_cookie=cookie.name,
        )
    except UnknownAccountConnection as exc:
        raise HTTPException(status_code=404, detail="This app does not offer that connection.") from exc
    except AccountConnectionRequired as exc:
        # The app's OAuth client for this provider is not configured.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    response = JSONResponse(
        {"authorize_url": started.authorize_url, "flow_id": started.flow_id}, headers=_NO_STORE,
    )
    response.set_cookie(
        cookie.name,
        started.binding,
        max_age=_BINDING_MAX_AGE_SECONDS,
        path=cookie.path,
        secure=cookie.secure,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/api/me/connected-accounts/flows/{flow_id}")
async def account_connection_flow(
    flow_id: str, request: Request, user_id: str = Depends(_require_account_owner),
) -> JSONResponse:
    """The outcome of the caller's flow. A flow that is unknown or someone else's reads ``unknown``."""
    return JSONResponse(await _service(request).flow_status(user_id=user_id, flow_id=flow_id), headers=_NO_STORE)


@router.delete("/api/me/connected-accounts/providers/{provider_id}")
async def disconnect_account(
    provider_id: str, request: Request, user_id: str = Depends(_require_account_owner),
) -> JSONResponse:
    return JSONResponse(await _service(request).disconnect(user_id=user_id, provider_id=provider_id), headers=_NO_STORE)


# ---------------------------------------------------------------------------
# The provider's redirect
# ---------------------------------------------------------------------------

@router.get("/api/connected-accounts/callback", name=CALLBACK_ROUTE_NAME)
async def connected_accounts_callback(
    request: Request, code: str = "", state: str = "", error: str = "",
) -> HTMLResponse:
    service = get_connected_accounts_service()
    if service is None:
        return _popup("Connection failed", "Connected accounts are not available in this app.", 404, closes=False)
    result = await service.complete(
        state=state, code=code, error=error, read_cookie=lambda name: _cookie_values(request, name),
    )
    if result.status == "connected":
        response = _popup("Connected", f"{result.message} Closing…", 200, closes=True)
    else:
        response = _popup("Connection failed", result.message, 400, closes=False)
    cookie = _BINDING_COOKIES.get(result.clear_cookie or "")
    if cookie is not None:
        # Only once this flow's own binding was verified: the cookie belongs to the newest
        # flow this browser started, so a callback for any other flow leaves it in place.
        response.delete_cookie(cookie.name, path=cookie.path, secure=cookie.secure, httponly=True, samesite="lax")
    return response


# ---------------------------------------------------------------------------
# The app's admins
# ---------------------------------------------------------------------------

class OfferingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offered: StrictBool


@router.get("/api/admin/connected-accounts")
async def admin_connected_accounts(
    request: Request, _admin: UserPrincipal = Depends(_require_admin),
) -> JSONResponse:
    summary = await _service(request).admin_summary(redirect_uri=_redirect_uri(request))
    return JSONResponse(summary, headers=_NO_STORE)


@router.put("/api/admin/connected-accounts/{connection_id}")
async def admin_set_connection_offering(
    connection_id: str,
    body: OfferingUpdate,
    request: Request,
    admin: UserPrincipal = Depends(_require_admin),
) -> JSONResponse:
    try:
        result = await _service(request).set_offered(
            connection_id=connection_id, offered=body.offered, actor_id=str(admin.user_id or ""),
        )
    except UnknownAccountConnection as exc:
        raise HTTPException(status_code=404, detail="No installed plugin declares that connection.") from exc
    return JSONResponse(result, headers=_NO_STORE)


__all__ = ["router"]

"""GitHub OAuth popup flow for brownfield repository access.

ExistingAppDiscovery reads a private repository with a short-lived GitHub
access token that never leaves this process and is never written to the
database. Every step of the flow is bound to the person who started it:

1. ``POST /api/oauth/github/start`` (authenticated) mints an unguessable OAuth
   ``state``, a separate connection id, and a browser-binding nonce. The state
   is bound to the caller's user id, the nonce is set as an HttpOnly cookie
   scoped to this route family, and the response carries the GitHub authorize
   URL and the connection id. The page navigates its popup to that URL: a
   popup navigation cannot send the bearer header, so the start is a fetch.
2. ``GET /api/oauth/github/callback`` is GitHub's redirect target and the only
   route here without a bearer token. It accepts a server-issued, unexpired
   state once, and only from the browser holding that flow's binding cookie,
   so a flow one person started cannot be completed with another person's
   GitHub consent.
3. ``GET /api/oauth/github/ready`` and ``GET /api/oauth/github/repos``
   (authenticated) answer only the user who owns the connection.
4. :func:`consume_github_token` hands the token to the discovery run once, and
   only when the run belongs to that user.

Scope: discovery only reads (repository metadata, the git tree, and manifest
file contents), but GitHub OAuth Apps have no read-only scope for private
repositories, so the flow requests ``repo``. The token lives at most
``_TOKEN_TTL`` seconds and is used only for those reads. A GitHub App with a
read-only contents permission is the planned replacement for this flow.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from typing import TypeVar

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from mozaiksai.core.auth.dependencies import UserPrincipal, require_user

router = APIRouter(tags=["oauth"])
logger = logging.getLogger(__name__)

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_REPOS_URL = "https://api.github.com/user/repos"
#: GitHub OAuth Apps offer no read-only private-repository scope (see module docstring).
GITHUB_SCOPE = "repo"

BINDING_COOKIE = "mozaiks_github_oauth_binding"
BINDING_COOKIE_PATH = "/api/oauth/github"

_STATE_TTL = 600   # seconds a started flow may wait for GitHub's callback
_TOKEN_TTL = 300   # seconds a connected token waits for its owner's discovery run
_MAX_ENTRIES_PER_USER = 5


@dataclass(frozen=True)
class _OwnedEntry:
    owner_user_id: str
    expires_at: float


@dataclass(frozen=True)
class _PendingFlow(_OwnedEntry):
    connection_id: str
    binding_digest: str


@dataclass(frozen=True)
class _Connection(_OwnedEntry):
    access_token: str


_Entry = TypeVar("_Entry", bound=_OwnedEntry)

# In-process stores. The OAuth state keys a pending flow; the connection id,
# which never appears in a URL GitHub sees, keys the connected token.
_PENDING_FLOWS: dict[str, _PendingFlow] = {}
_CONNECTIONS: dict[str, _Connection] = {}


def _clock() -> float:
    return time.monotonic()


def _github_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=15.0)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _purge(store: dict[str, _Entry], now: float) -> None:
    for key in [key for key, entry in store.items() if now >= entry.expires_at]:
        del store[key]


def _purge_expired() -> None:
    now = _clock()
    _purge(_PENDING_FLOWS, now)
    _purge(_CONNECTIONS, now)


def _make_room(store: dict[str, _Entry], owner_user_id: str) -> None:
    """Drop the owner's oldest entries so one account cannot grow a store without bound."""
    owned = [key for key, entry in store.items() if entry.owner_user_id == owner_user_id]
    for key in owned[: max(0, len(owned) - _MAX_ENTRIES_PER_USER + 1)]:
        del store[key]


def _owned_connection(connection_id: str, owner_user_id: str) -> _Connection | None:
    _purge_expired()
    entry = _CONNECTIONS.get(connection_id)
    if entry is None or not owner_user_id or entry.owner_user_id != owner_user_id:
        return None
    return entry


def consume_github_token(connection_id: str, *, user_id: str) -> str | None:
    """Pop and return the GitHub access token for *connection_id*.

    Called once by ExistingAppDiscovery's before_chat hook with the run's user
    id. Returns None when the connection is absent, expired, or owned by a
    different user; a mismatch leaves the owner's connection in place.
    """
    key = str(connection_id or "")
    entry = _owned_connection(key, str(user_id or "").strip())
    if entry is None:
        return None
    del _CONNECTIONS[key]
    return entry.access_token


async def _require_github_owner(principal: UserPrincipal = Depends(require_user)) -> str:
    """The user id a GitHub connection is bound to.

    A token-validated user, or local development access with authentication
    off. An anonymous visitor (``AUTH_ANON_ACCESS=public``) has no identity to
    bind a personal GitHub credential to.
    """
    user_id = str(principal.user_id or "").strip()
    if not user_id or not (principal.is_authenticated or principal.has_local_development_access):
        raise HTTPException(status_code=403, detail="Connecting GitHub requires a signed-in user")
    return user_id


def _oauth_credentials() -> tuple[str, str]:
    return (
        os.getenv("GITHUB_OAUTH_CLIENT_ID", "").strip(),
        os.getenv("GITHUB_OAUTH_CLIENT_SECRET", "").strip(),
    )


# ---------------------------------------------------------------------------
# Popup pages. The popup only shows a fixed message and closes itself; nothing
# from the request is reflected into the page, and completion is detected by
# the opener polling /ready.
# The default SecurityHeadersMiddleware sets default-src 'self', which blocks
# the inline close script; it keeps a Content-Security-Policy already present.
# ---------------------------------------------------------------------------
_POPUP_CSP = "default-src 'none'; script-src 'unsafe-inline'"
_POPUP_PAGE = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>{title}</title></head>
<body>
<p style="font-family:sans-serif;padding:2rem">{message}</p>
<script>setTimeout(function () {{ try {{ window.close(); }} catch (e) {{}} }}, 300);</script>
</body>
</html>"""


def _popup(title: str, message: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        _POPUP_PAGE.format(title=title, message=message),
        status_code=status_code,
        headers={"Content-Security-Policy": _POPUP_CSP, "Cache-Control": "no-store"},
    )


def _popup_error(message: str, status_code: int = 400) -> HTMLResponse:
    return _popup("GitHub connection failed", message, status_code)


def _flow_finished(response: HTMLResponse) -> HTMLResponse:
    """Clear the binding cookie once the flow it belongs to has been verified and ended.

    Only then: the cookie belongs to the newest flow this browser started, so a
    callback for any other flow leaves it in place.
    """
    response.delete_cookie(BINDING_COOKIE, path=BINDING_COOKIE_PATH)
    return response


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/api/oauth/github/status")
async def github_oauth_status(_owner: str = Depends(_require_github_owner)) -> JSONResponse:
    """Return whether GitHub OAuth credentials are configured."""
    client_id, client_secret = _oauth_credentials()
    return JSONResponse({"configured": bool(client_id and client_secret)})


@router.post("/api/oauth/github/start")
async def github_oauth_start(
    request: Request,
    owner_user_id: str = Depends(_require_github_owner),
) -> JSONResponse:
    """Start a GitHub connection for the caller and return the authorize URL."""
    client_id, client_secret = _oauth_credentials()
    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail="GitHub OAuth is not configured")

    _purge_expired()
    _make_room(_PENDING_FLOWS, owner_user_id)
    state = secrets.token_urlsafe(32)
    connection_id = secrets.token_urlsafe(32)
    binding = secrets.token_urlsafe(32)
    _PENDING_FLOWS[state] = _PendingFlow(
        owner_user_id=owner_user_id,
        expires_at=_clock() + _STATE_TTL,
        connection_id=connection_id,
        binding_digest=_digest(binding),
    )

    params = {"client_id": client_id, "state": state, "scope": GITHUB_SCOPE}
    redirect_uri = os.getenv("GITHUB_OAUTH_REDIRECT_URI", "").strip()
    if redirect_uri:
        params["redirect_uri"] = redirect_uri
    response = JSONResponse(
        {
            "authorize_url": f"{GITHUB_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}",
            "connection_id": connection_id,
        },
        headers={"Cache-Control": "no-store"},
    )
    response.set_cookie(
        BINDING_COOKIE,
        binding,
        max_age=_STATE_TTL,
        path=BINDING_COOKIE_PATH,
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/api/oauth/github/callback")
async def github_oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
) -> HTMLResponse:
    """Handle GitHub's redirect: verify the flow, exchange the code, close the popup."""
    if error:
        logger.warning("[github_oauth] GitHub returned an authorization error")
        return _popup_error("GitHub access was not granted. You can close this window.", 200)

    if not code or not state:
        return _popup_error("The GitHub response was incomplete. Start the connection again.")

    _purge_expired()
    flow = _PENDING_FLOWS.pop(state, None)
    if flow is None:
        return _popup_error("This GitHub connection request is invalid or has expired. Start it again.")
    binding = request.cookies.get(BINDING_COOKIE, "")
    if not binding or not hmac.compare_digest(_digest(binding), flow.binding_digest):
        logger.warning("[github_oauth] Callback refused: browser binding missing or mismatched")
        return _popup_error(
            "This GitHub connection was started in a different browser. Start it again here."
        )

    client_id, client_secret = _oauth_credentials()
    if not client_id or not client_secret:
        return _flow_finished(_popup_error("GitHub OAuth is not configured.", 503))

    try:
        async with _github_client() as http:
            resp = await http.post(
                GITHUB_TOKEN_URL,
                data={"client_id": client_id, "client_secret": client_secret, "code": code},
                headers={"Accept": "application/json"},
            )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error("[github_oauth] Token exchange failed: %s", type(exc).__name__)
        return _flow_finished(_popup_error("GitHub token exchange failed. Start the connection again.", 502))

    access_token = data.get("access_token") if isinstance(data, dict) else None
    if not isinstance(access_token, str) or not access_token:
        logger.warning("[github_oauth] Token exchange returned no access token")
        return _flow_finished(
            _popup_error("GitHub did not return an access token. Start the connection again.", 502)
        )

    _make_room(_CONNECTIONS, flow.owner_user_id)
    _CONNECTIONS[flow.connection_id] = _Connection(
        owner_user_id=flow.owner_user_id,
        expires_at=_clock() + _TOKEN_TTL,
        access_token=access_token,
    )
    logger.info("[github_oauth] GitHub connected")
    return _flow_finished(_popup("GitHub connected", "GitHub connected. Closing&hellip;"))


@router.get("/api/oauth/github/ready")
async def github_oauth_ready(
    connection_id: str = "",
    owner_user_id: str = Depends(_require_github_owner),
) -> JSONResponse:
    """Whether the caller's connection has a token yet.

    Polled by the page that started the flow. A connection that is pending,
    expired, or owned by someone else reads the same: not ready.
    """
    ready = _owned_connection(connection_id, owner_user_id) is not None
    return JSONResponse({"ready": ready}, headers={"Cache-Control": "no-store"})


@router.get("/api/oauth/github/repos")
async def github_list_repos(
    connection_id: str = "",
    owner_user_id: str = Depends(_require_github_owner),
) -> JSONResponse:
    """List repos the caller's connected GitHub account can access.

    Reads the token without consuming it, so the discovery run can still
    consume it when the workflow starts.
    """
    entry = _owned_connection(connection_id, owner_user_id)
    if entry is None:
        return JSONResponse({"error": "GitHub connection not found or expired"}, status_code=404)

    try:
        async with _github_client() as http:
            resp = await http.get(
                GITHUB_REPOS_URL,
                params={
                    "per_page": 100,
                    "sort": "updated",
                    "affiliation": "owner,collaborator,organization_member",
                },
                headers={
                    "Authorization": f"Bearer {entry.access_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
        resp.raise_for_status()
        raw = resp.json()
    except Exception as exc:
        logger.error("[github_oauth] Repo list failed: %s", type(exc).__name__)
        return JSONResponse({"error": "Failed to fetch repos from GitHub"}, status_code=502)

    repos = [
        {
            "full_name": r["full_name"],
            "private": r.get("private", False),
            "description": r.get("description") or "",
            "updated_at": r.get("updated_at") or "",
        }
        for r in (raw if isinstance(raw, list) else [])
        if isinstance(r, dict) and r.get("full_name")
    ]
    return JSONResponse({"repos": repos}, headers={"Cache-Control": "no-store"})


__all__ = ["router", "consume_github_token"]

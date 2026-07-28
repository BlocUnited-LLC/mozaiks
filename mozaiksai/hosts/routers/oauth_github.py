"""GitHub OAuth popup flow for brownfield repo access.

Provides a short-lived in-process token store so the ExistingAppDiscovery
before_chat hook can consume a GitHub access token obtained via OAuth without
ever writing the token to the database.

Routes:
    GET /api/oauth/github/status    -- whether OAuth is configured
    GET /api/oauth/github/connect   -- start popup OAuth flow
    GET /api/oauth/github/callback  -- receive code, exchange for token, close popup
"""
from __future__ import annotations

import json
import logging
import os
import time

import httpx
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

router = APIRouter(tags=["oauth"])
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-process token store
# Tokens are stored with a 5-minute TTL and consumed exactly once.
# The session_key (a client-generated UUID) is stored in context_variables;
# the access token itself never leaves the server process.
# ---------------------------------------------------------------------------
_TOKEN_STORE: dict[str, tuple[str, float]] = {}
_STATE_STORE: dict[str, float] = {}
_TOKEN_TTL = 300   # 5 minutes
_STATE_TTL = 600   # 10 minutes


def _purge_expired() -> None:
    now = time.monotonic()
    for store in (_TOKEN_STORE, _STATE_STORE):
        expired = [k for k, v in store.items() if now > (v[1] if isinstance(v, tuple) else v)]
        for k in expired:
            del store[k]


def consume_github_token(session_key: str) -> str | None:
    """Pop and return the GitHub access token for *session_key*.

    Called once by ExistingAppDiscovery's before_chat hook.
    Returns None when the key is absent or expired.
    """
    _purge_expired()
    entry = _TOKEN_STORE.pop(session_key, None)
    if entry is None:
        return None
    token, expiry = entry
    if time.monotonic() > expiry:
        return None
    return token


def _peek_github_token(session_key: str) -> str | None:
    """Return the GitHub access token without consuming it.

    Used by the repo-list endpoint so the token remains available for the
    before_chat hook to consume later.
    """
    _purge_expired()
    entry = _TOKEN_STORE.get(session_key)
    if entry is None:
        return None
    token, expiry = entry
    if time.monotonic() > expiry:
        _TOKEN_STORE.pop(session_key, None)
        return None
    return token


# ---------------------------------------------------------------------------
# CSP override for popup HTML responses
# The default SecurityHeadersMiddleware sets default-src 'self' which blocks
# inline <script> tags. Popup responses need script-src unsafe-inline so the
# window.opener.postMessage(...) call can run.
# The middleware skips headers already present on the response.
# ---------------------------------------------------------------------------
_POPUP_CSP = "default-src 'none'; script-src 'unsafe-inline'"


def _popup_html(body: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        body,
        status_code=status_code,
        headers={"Content-Security-Policy": _POPUP_CSP},
    )


def _popup_success(session_key: str) -> str:
    payload = json.dumps({"type": "github_oauth_complete", "session_key": session_key})
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>GitHub Connected</title></head>
<body>
<p style="font-family:sans-serif;padding:2rem">GitHub connected. Closing&hellip;</p>
<script>
(function(){{
  var payload = {payload};
  try {{ new BroadcastChannel('github_oauth').postMessage(payload); }} catch (e) {{}}
  setTimeout(function() {{ try {{ window.close(); }} catch (e) {{}} }}, 300);
}})();
</script>
</body>
</html>"""


def _popup_error(message: str) -> str:
    payload = json.dumps({"type": "github_oauth_error", "error": message})
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>GitHub OAuth Error</title></head>
<body>
<p style="font-family:sans-serif;padding:2rem">GitHub connection failed. You may close this tab.</p>
<script>
(function(){{
  var payload = {payload};
  try {{ new BroadcastChannel('github_oauth').postMessage(payload); }} catch (e) {{}}
  setTimeout(function() {{ try {{ window.close(); }} catch (e) {{}} }}, 300);
}})();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/api/oauth/github/status")
async def github_oauth_status() -> JSONResponse:
    """Return whether GitHub OAuth credentials are configured."""
    configured = bool(
        os.getenv("GITHUB_OAUTH_CLIENT_ID") and os.getenv("GITHUB_OAUTH_CLIENT_SECRET")
    )
    return JSONResponse({"configured": configured})


@router.get("/api/oauth/github/connect")
async def github_oauth_connect(session_key: str = "") -> Response:
    """Redirect to GitHub OAuth authorization page."""
    client_id = os.getenv("GITHUB_OAUTH_CLIENT_ID", "").strip()
    if not client_id:
        return _popup_html(_popup_error("GitHub OAuth is not configured (missing GITHUB_OAUTH_CLIENT_ID)"), 503)

    if not session_key:
        return _popup_html(_popup_error("Missing session_key"), 400)

    _purge_expired()
    _STATE_STORE[session_key] = time.monotonic() + _STATE_TTL

    redirect_uri = os.getenv("GITHUB_OAUTH_REDIRECT_URI", "").strip()
    params = f"client_id={client_id}&state={session_key}&scope=repo"
    if redirect_uri:
        import urllib.parse
        params += f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"

    return RedirectResponse(url=f"https://github.com/login/oauth/authorize?{params}")


@router.get("/api/oauth/github/callback")
async def github_oauth_callback(
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
) -> HTMLResponse:
    """Handle the GitHub OAuth callback, exchange code for token, close popup."""
    if error:
        reason = error_description or error
        logger.warning("[github_oauth] OAuth denied: %s", reason)
        return _popup_html(_popup_error(f"GitHub access denied: {reason}"))

    if not code or not state:
        return _popup_html(_popup_error("Missing code or state parameter"), 400)

    _purge_expired()
    if state not in _STATE_STORE:
        return _popup_html(_popup_error("Invalid or expired OAuth state"), 400)
    del _STATE_STORE[state]

    client_id = os.getenv("GITHUB_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.getenv("GITHUB_OAUTH_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return _popup_html(_popup_error("GitHub OAuth credentials not configured"))

    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.post(
                "https://github.com/login/oauth/access_token",
                data={"client_id": client_id, "client_secret": client_secret, "code": code},
                headers={"Accept": "application/json"},
            )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error("[github_oauth] Token exchange failed: %s", exc)
        return _popup_html(_popup_error("GitHub token exchange failed"))

    access_token = data.get("access_token", "")
    if not access_token:
        err_msg = data.get("error_description") or data.get("error") or "Unknown error"
        logger.warning("[github_oauth] Token exchange returned no token: %s", err_msg)
        return _popup_html(_popup_error(f"GitHub OAuth failed: {err_msg}"))

    _TOKEN_STORE[state] = (access_token, time.monotonic() + _TOKEN_TTL)
    logger.info("[github_oauth] Token stored for session=%s", state[:8])
    return _popup_html(_popup_success(state))


@router.get("/api/oauth/github/_dev/complete")
async def github_oauth_dev_complete(session_key: str = "") -> HTMLResponse:
    """Dev-only: simulate a completed OAuth flow without going to GitHub.

    Seeds a placeholder token for *session_key* and returns the success HTML
    so the polling loop on the frontend detects completion. Only active when
    APP_ENV is not 'production'.
    """
    if os.getenv("APP_ENV", "").strip().lower() == "production":
        return HTMLResponse("Not found", status_code=404)
    if not session_key:
        return HTMLResponse("Missing session_key", status_code=400)
    _TOKEN_STORE[session_key] = ("ghp_dev_placeholder", time.monotonic() + _TOKEN_TTL)
    logger.info("[github_oauth] Dev: token seeded for session=%s", session_key[:8])
    return _popup_html(_popup_success(session_key))


@router.get("/api/oauth/github/ready")
async def github_oauth_ready(session_key: str = "") -> JSONResponse:
    """Check whether the OAuth token for *session_key* has been stored.

    Polled by the frontend after opening the OAuth popup so it can detect
    completion without relying on cross-origin postMessage or BroadcastChannel.
    Returns {ready: true} once the token exists, {ready: false} otherwise.
    """
    if not session_key:
        return JSONResponse({"ready": False})
    _purge_expired()
    ready = session_key in _TOKEN_STORE
    return JSONResponse({"ready": ready})


@router.get("/api/oauth/github/repos")
async def github_list_repos(session_key: str = "") -> JSONResponse:
    """List repos accessible by the connected GitHub user.

    Peeks at the token store without consuming the token so the before_chat
    hook can still use it when the workflow starts.
    """
    if not session_key:
        return JSONResponse({"error": "Missing session_key"}, status_code=400)

    token = _peek_github_token(session_key)
    if not token:
        return JSONResponse({"error": "Session not found or expired"}, status_code=404)

    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.get(
                "https://api.github.com/user/repos",
                params={
                    "per_page": 100,
                    "sort": "updated",
                    "affiliation": "owner,collaborator,organization_member",
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
        resp.raise_for_status()
        raw = resp.json()
    except Exception as exc:
        logger.error("[github_oauth] Repo list failed: %s", exc)
        return JSONResponse({"error": "Failed to fetch repos from GitHub"}, status_code=502)

    repos = [
        {
            "full_name": r["full_name"],
            "private": r.get("private", False),
            "description": r.get("description") or "",
            "updated_at": r.get("updated_at") or "",
        }
        for r in raw
        if isinstance(r, dict) and r.get("full_name")
    ]
    return JSONResponse({"repos": repos})


__all__ = ["router", "consume_github_token"]

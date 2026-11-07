"""OAuth 2.0 authorization-code flow with PKCE for connected accounts.

Only the connected-accounts service calls this module. Tokens are returned to
the caller and never logged; provider error bodies are reduced to their error
code before they reach a log line or an exception message.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from .providers import AccountProvider

_HTTP_TIMEOUT_SECONDS = 15.0


class AccountTokenError(Exception):
    """A token request failed.

    ``reason`` is ``invalid_grant`` when the user must connect again, and
    ``invalid_scope`` when the grant no longer covers the requested scopes.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    expires_at: float
    refresh_token: str | None
    scopes: frozenset[str]
    id_token_claims: Mapping[str, Any] = field(default_factory=dict)


def http_client() -> httpx.AsyncClient:
    """The HTTP client for provider calls; tests replace this factory."""
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS)


def pkce_pair() -> tuple[str, str]:
    """A PKCE verifier and its S256 challenge (RFC 7636)."""
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii")
    return verifier, challenge.rstrip("=")


def authorize_url(
    provider: AccountProvider,
    *,
    client_id: str,
    redirect_uri: str,
    scopes: Sequence[str],
    state: str,
    code_challenge: str,
) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(dict.fromkeys(scopes)),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        **dict(provider.authorize_params),
    }
    return f"{provider.authorize_url}?{urllib.parse.urlencode(params)}"


def _claims_from_id_token(id_token: Any) -> dict[str, Any]:
    """Claims of an ID token received directly from the token endpoint.

    It arrives over TLS straight from the provider's token endpoint, so the TLS
    server identity establishes the issuer (OpenID Connect Core 3.1.3.7); only
    display claims (subject, email) are read from it.
    """
    if not isinstance(id_token, str) or id_token.count(".") != 2:
        return {}
    payload = id_token.split(".")[1]
    try:
        decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _token_set(data: Mapping[str, Any], *, requested_scopes: Sequence[str], clock: float) -> TokenSet:
    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise AccountTokenError("provider_error", "The provider returned no access token.")
    try:
        expires_in = max(int(data.get("expires_in") or 3600), 60)
    except (TypeError, ValueError):
        expires_in = 3600
    granted = data.get("scope")
    scopes = frozenset(str(granted).split()) if isinstance(granted, str) and granted.strip() else frozenset(requested_scopes)
    refresh_token = data.get("refresh_token")
    return TokenSet(
        access_token=access_token,
        expires_at=clock + expires_in,
        refresh_token=refresh_token if isinstance(refresh_token, str) and refresh_token else None,
        scopes=scopes,
        id_token_claims=_claims_from_id_token(data.get("id_token")),
    )


async def _token_request(provider: AccountProvider, form: dict[str, str], *, requested_scopes: Sequence[str]) -> TokenSet:
    try:
        async with http_client() as http:
            response = await http.post(provider.token_url, data=form, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise AccountTokenError("provider_unreachable", f"{provider.label} could not be reached ({type(exc).__name__}).") from exc
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400 or not isinstance(data, dict):
        code = str((data or {}).get("error") or f"http_{response.status_code}") if isinstance(data, dict) else f"http_{response.status_code}"
        reason = code if code in {"invalid_grant", "invalid_scope"} else "provider_error"
        raise AccountTokenError(reason, f"{provider.label} refused the token request ({code}).")
    return _token_set(data, requested_scopes=requested_scopes, clock=time.time())


async def exchange_code(
    provider: AccountProvider,
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
    requested_scopes: Sequence[str],
) -> TokenSet:
    return await _token_request(
        provider,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": code_verifier,
        },
        requested_scopes=requested_scopes,
    )


async def refresh_access_token(
    provider: AccountProvider,
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    requested_scopes: Sequence[str],
) -> TokenSet:
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if requested_scopes:
        # A narrower access token (RFC 6749 section 6): only what this connection declares,
        # even when the user's grant also covers other connections.
        form["scope"] = " ".join(dict.fromkeys(requested_scopes))
    return await _token_request(provider, form, requested_scopes=requested_scopes)


async def revoke(provider: AccountProvider, token: str) -> bool:
    """Revoke a grant at the provider. A failure is reported, never raised: local removal still proceeds."""
    if not provider.revoke_url or not token:
        return False
    try:
        async with http_client() as http:
            response = await http.post(
                provider.revoke_url, data={"token": token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError:
        return False
    return response.status_code < 400


__all__ = [
    "AccountTokenError",
    "TokenSet",
    "authorize_url",
    "exchange_code",
    "http_client",
    "pkce_pair",
    "refresh_access_token",
    "revoke",
]

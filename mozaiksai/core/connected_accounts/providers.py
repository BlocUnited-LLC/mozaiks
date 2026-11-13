"""Third-party account providers an end user can connect to an app.

A provider is the OAuth 2.0 authorization server that issues the user's grant
(Google today). Plugins never talk to these endpoints: the connected-accounts
service runs the authorization-code flow (with PKCE) and hands plugins a
short-lived access token for the calling user only.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from mozaiksai.core.environment import resolve_environment

GOOGLE = "google"

# A local Google emulator (OAuth and APIs) for tests and offline proofs. Refused in
# any deployed environment, where Google's real endpoints are always used.
GOOGLE_EMULATOR_ENV = "MOZAIKS_GOOGLE_EMULATOR_URL"

_GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
_GOOGLE_API_BASE_URL = "https://www.googleapis.com"


class AccountProviderError(ValueError):
    """Raised for an unknown provider or a provider configuration that is not allowed here."""


@dataclass(frozen=True)
class AccountProvider:
    """OAuth endpoints and app-level client settings for one provider."""

    id: str
    label: str
    authorize_url: str
    token_url: str
    revoke_url: str | None
    # Requested with every connection so the user's own account can be shown ("Connected as …").
    identity_scopes: tuple[str, ...]
    authorize_params: Mapping[str, str]
    # The app's OAuth client, resolved like any app secret: environment first, then the
    # app's declared Key Vault references (security/secrets.yaml).
    client_id_env: str
    client_secret_env: str


def _google_emulator_url() -> str:
    emulator = os.getenv(GOOGLE_EMULATOR_ENV, "").strip().rstrip("/")
    if emulator and resolve_environment().is_deployed:
        raise AccountProviderError(
            f"{GOOGLE_EMULATOR_ENV} is set in a deployed environment; it is honored only in local "
            "development. Remove it so Google's real endpoints are used."
        )
    return emulator


def google_api_base_url() -> str:
    """Where plugins call Google APIs (Calendar ``/calendar/v3``, Gmail ``/gmail/v1``)."""
    return _google_emulator_url() or _GOOGLE_API_BASE_URL


def _google() -> AccountProvider:
    authorize_url, token_url, revoke_url = _GOOGLE_AUTHORIZE_URL, _GOOGLE_TOKEN_URL, _GOOGLE_REVOKE_URL
    emulator = _google_emulator_url()
    if emulator:
        authorize_url = f"{emulator}/o/oauth2/v2/auth"
        token_url = f"{emulator}/token"
        revoke_url = f"{emulator}/revoke"
    return AccountProvider(
        id=GOOGLE,
        label="Google",
        authorize_url=authorize_url,
        token_url=token_url,
        revoke_url=revoke_url,
        identity_scopes=("openid", "email"),
        authorize_params=MappingProxyType({
            # A refresh token, so the user stays connected without signing in again.
            "access_type": "offline",
            # Google issues a refresh token only on a consent screen; always show it.
            "prompt": "consent",
            # One grant per user per app: adding a plugin later adds its scopes to the grant.
            "include_granted_scopes": "true",
        }),
        client_id_env="GOOGLE_ACCOUNTS_CLIENT_ID",
        client_secret_env="GOOGLE_ACCOUNTS_CLIENT_SECRET",
    )


_PROVIDERS: dict[str, Callable[[], AccountProvider]] = {GOOGLE: _google}


def known_providers() -> frozenset[str]:
    return frozenset(_PROVIDERS)


def get_provider(provider_id: str) -> AccountProvider:
    factory = _PROVIDERS.get(str(provider_id or "").strip())
    if factory is None:
        raise AccountProviderError(f"Unknown account provider: {provider_id!r}")
    return factory()


__all__ = [
    "GOOGLE",
    "GOOGLE_EMULATOR_ENV",
    "AccountProvider",
    "AccountProviderError",
    "get_provider",
    "google_api_base_url",
    "known_providers",
]

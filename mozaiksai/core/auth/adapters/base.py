"""
Base auth adapter protocol and types.

All auth adapters must implement the AuthAdapter protocol.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class UserClaims:
    """
    Standardized user claims returned by all auth adapters.

    This is the common interface that all adapters must produce,
    regardless of the underlying auth provider.
    """

    user_id: str
    email: str | None = None
    name: str | None = None
    roles: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    raw_claims: dict[str, Any] = field(default_factory=dict)

    # Provider metadata
    provider: str = "unknown"

    # Optional app/session binding (for multi-tenant scenarios)
    app_id: str | None = None
    chat_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None

    def has_role(self, role: str) -> bool:
        """Check if user has a specific role."""
        return role in self.roles

    def has_any_role(self, roles: list[str]) -> bool:
        """Check if user has any of the specified roles."""
        return any(r in self.roles for r in roles)

    def has_scope(self, scope: str) -> bool:
        """Check if user has a specific scope."""
        return scope in self.scopes

    def get_claim(self, claim_name: str, default: Any = None) -> Any:
        """Get a raw claim value by name."""
        return self.raw_claims.get(claim_name, default)


class AuthError(Exception):
    """
    Authentication error with HTTP status code.

    Use this for all auth failures to provide consistent error handling.
    """

    def __init__(self, message: str, status_code: int = 401, provider: str = "unknown"):
        self.message = message
        self.status_code = status_code
        self.provider = provider
        super().__init__(message)

    def __str__(self) -> str:
        return f"[{self.provider}] {self.message}"


@runtime_checkable
class AuthAdapter(Protocol):
    """
    Protocol for auth adapters.

    All auth adapters must implement this interface to be compatible
    with the mozaiksai auth system.

    Example implementation:

        class MyCustomAdapter:
            name = "my-custom"

            async def validate_token(self, token: str) -> UserClaims:
                # Validate token and return claims
                ...

            def is_enabled(self) -> bool:
                return True
    """

    # Adapter identifier (e.g., "supabase", "keycloak", "auth0")
    name: str

    async def validate_token(self, token: str) -> UserClaims:
        """
        Validate an access token and return user claims.

        Args:
            token: The raw token string (without "Bearer " prefix)

        Returns:
            UserClaims with standardized user information

        Raises:
            AuthError on validation failure
        """
        ...

    def is_enabled(self) -> bool:
        """
        Check if this adapter is enabled/configured.

        Returns:
            True if the adapter can validate tokens, False otherwise
        """
        ...


class BaseAuthAdapter:
    """
    Base class for auth adapters with common functionality.

    Adapters can extend this class for shared utilities.

    Configuration snapshot
    ----------------------
    Adapters are constructed from an immutable configuration snapshot supplied
    by the auth registry (``settings``). The registry derives the adapter cache
    identity from that same snapshot, so the adapter a request uses is always
    built from exactly the configuration that identity describes. Reading live
    ``os.environ`` inside an adapter would break that guarantee: use
    :meth:`_setting` instead of ``os.getenv``.

    ``settings=None`` falls back to the live environment, preserving direct
    construction in tests and custom embedding code.
    """

    name: str = "base"

    def __init__(self, settings: Mapping[str, str] | None = None):
        self._settings: Mapping[str, str] | None = settings

    def _setting(self, name: str, default: str = "") -> str:
        """Read one configuration value from the snapshot, else the environment.

        An absent value and an empty value are equivalent (both yield the
        default), matching how the rest of the auth configuration treats blank
        environment variables.
        """
        if self._settings is not None:
            value = self._settings.get(name)
        else:
            value = os.getenv(name)
        return default if not value else value

    def _optional_setting(self, name: str) -> str | None:
        """Read a value whose absence is meaningfully different from empty."""
        if self._settings is not None:
            return self._settings.get(name) or None
        return os.getenv(name) or None

    async def validate_token(self, token: str) -> UserClaims:
        """Override in subclass."""
        raise NotImplementedError("Subclass must implement validate_token")

    def is_enabled(self) -> bool:
        """Override in subclass."""
        return False

    def _extract_bearer_token(self, auth_header: str) -> str:
        """
        Extract token from Authorization header.

        Args:
            auth_header: Full header value (e.g., "Bearer eyJ...")

        Returns:
            The token without the "Bearer " prefix
        """
        if not auth_header:
            raise AuthError("Missing authorization header", 401, self.name)

        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise AuthError("Invalid authorization header format", 401, self.name)

        return parts[1]

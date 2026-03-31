"""
Base auth adapter protocol and types.

All auth adapters must implement the AuthAdapter protocol.
"""

from typing import Optional, List, Dict, Any, Protocol, runtime_checkable
from dataclasses import dataclass, field


@dataclass
class UserClaims:
    """
    Standardized user claims returned by all auth adapters.

    This is the common interface that all adapters must produce,
    regardless of the underlying auth provider.
    """

    user_id: str
    email: Optional[str] = None
    name: Optional[str] = None
    roles: List[str] = field(default_factory=list)
    scopes: List[str] = field(default_factory=list)
    raw_claims: Dict[str, Any] = field(default_factory=dict)

    # Provider metadata
    provider: str = "unknown"

    # Optional app/session binding (for multi-tenant scenarios)
    app_id: Optional[str] = None
    chat_id: Optional[str] = None
    tenant_id: Optional[str] = None

    def has_role(self, role: str) -> bool:
        """Check if user has a specific role."""
        return role in self.roles

    def has_any_role(self, roles: List[str]) -> bool:
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
    """

    name: str = "base"

    def __init__(self):
        pass

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

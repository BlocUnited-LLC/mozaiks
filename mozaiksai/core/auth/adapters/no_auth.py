"""
No-auth adapter for demo and development mode.

This adapter bypasses all authentication and returns anonymous user claims.
Use this when AUTH_ENABLED=false or AUTH_PROVIDER=none.
"""

import os
from typing import Optional

from mozaiksai.core.auth.adapters.base import BaseAuthAdapter, UserClaims, AuthError


class NoAuthAdapter(BaseAuthAdapter):
    """
    Auth adapter that bypasses authentication.

    Returns anonymous user claims without validating any token.
    Useful for:
    - Local development
    - Demo deployments
    - Testing

    Configuration:
        AUTH_ENABLED=false
        or
        AUTH_PROVIDER=none

    Optional env vars:
        AUTH_ANON_USER_ID: User ID for anonymous user (default: "anonymous")
        AUTH_ANON_EMAIL: Email for anonymous user (default: None)
    """

    name = "none"

    def __init__(
        self,
        default_user_id: Optional[str] = None,
        default_email: Optional[str] = None,
        default_roles: Optional[list] = None,
    ):
        super().__init__()
        self._default_user_id = default_user_id or os.getenv("AUTH_ANON_USER_ID", "anonymous")
        self._default_email = default_email or os.getenv("AUTH_ANON_EMAIL")
        self._default_roles = default_roles or []

    async def validate_token(self, token: str) -> UserClaims:
        """
        Return anonymous user claims without validation.

        The token parameter is ignored - any token (or no token) is accepted.

        Args:
            token: Ignored

        Returns:
            UserClaims with anonymous user data
        """
        return UserClaims(
            user_id=self._default_user_id,
            email=self._default_email,
            name="Anonymous User",
            roles=self._default_roles,
            scopes=["access_as_user"],  # Grant basic scope
            raw_claims={},
            provider=self.name,
        )

    def is_enabled(self) -> bool:
        """Always enabled when selected."""
        return True

    def validate_token_sync(self, token: str) -> UserClaims:
        """
        Synchronous version for non-async contexts.

        Args:
            token: Ignored

        Returns:
            UserClaims with anonymous user data
        """
        return UserClaims(
            user_id=self._default_user_id,
            email=self._default_email,
            name="Anonymous User",
            roles=self._default_roles,
            scopes=["access_as_user"],
            raw_claims={},
            provider=self.name,
        )

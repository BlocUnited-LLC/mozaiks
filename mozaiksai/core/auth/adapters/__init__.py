"""
Auth adapters for provider-agnostic authentication.

This module provides a pluggable authentication system that works with:
- No auth (demo/development mode)
- Generic JWT (any OIDC provider)
- Supabase
- Keycloak
- Auth0
- Firebase
- Custom providers

Usage:
    from mozaiksai.core.auth.adapters import get_auth_adapter, UserClaims

    adapter = get_auth_adapter()
    claims = await adapter.validate_token(token)
    print(claims.user_id)
"""

from mozaiksai.core.auth.adapters.base import (
    AuthAdapter,
    UserClaims,
    AuthError,
)
from mozaiksai.core.auth.adapters.registry import (
    get_auth_adapter,
    register_adapter,
    list_adapters,
)

__all__ = [
    "AuthAdapter",
    "UserClaims",
    "AuthError",
    "get_auth_adapter",
    "register_adapter",
    "list_adapters",
]

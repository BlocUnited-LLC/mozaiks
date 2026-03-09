"""
MozaiksAI Authentication Module (Transport-Level Only).

This module provides **authentication**, not authorization.

    MozaiksAI authenticates requests but does not authorize behavior.
    Authorization is delegated to the host control plane (MozaiksCore or customer app).

See: docs/source_of_truth/04_AUTH_BOUNDARY.md

What This Module Does (Authentication):
    - Validates JWT signatures via OIDC discovery / JWKS
    - Verifies issuer, audience, expiration
    - Extracts identity claims (sub, email, scopes)
    - Rejects anonymous/invalid traffic

What This Module Does NOT Do (Authorization):
    - User account management
    - Subscription/entitlement checks
    - "Is user allowed to run workflow X?" decisions
    - Billing or feature gating

Quick Start:
    # HTTP route protection
    from mozaiksai.core.auth import require_user, UserPrincipal

    @app.get("/api/me")
    async def get_me(user: UserPrincipal = Depends(require_user)):
        return {"user_id": user.user_id, "email": user.email}

    # WebSocket authentication
    from mozaiksai.core.auth import authenticate_websocket

    @app.websocket("/ws/chat")
    async def chat_ws(websocket: WebSocket):
        user = await authenticate_websocket(websocket)
        if not user:
            return  # Connection closed

        await websocket.accept()
        # websocket.state.user_id is now set

Configuration (environment variables):
    # OIDC Discovery (recommended - provider-agnostic)
    MOZAIKS_OIDC_AUTHORITY=https://mozaiks.ciamlogin.com
    MOZAIKS_OIDC_TENANT_ID=9d0073d5-42e8-46f0-a325-5b4be7b1a38d
    MOZAIKS_OIDC_DISCOVERY_URL=    # Optional explicit override

    # Override (skip discovery for these if set)
    AUTH_ISSUER=                   # Explicit issuer override
    AUTH_JWKS_URL=                 # Explicit JWKS URL override

    # Validation
    AUTH_ENABLED=true              # Set false for local dev bypass
    AUTH_AUDIENCE=api://mozaiks-auth
    AUTH_REQUIRED_SCOPE=access_as_user

    # Claim mappings
    AUTH_USER_ID_CLAIM=sub
    AUTH_EMAIL_CLAIM=email
    AUTH_ROLES_CLAIM=roles

    # Caching
    AUTH_JWKS_CACHE_TTL=3600       # JWKS cache TTL (seconds)
    AUTH_DISCOVERY_CACHE_TTL=86400 # Discovery cache TTL (seconds)
"""

# Configuration
from mozaiksai.core.auth.config import (
    AuthConfig,
    get_auth_config,
    clear_auth_config_cache,
)

# OIDC Discovery
from mozaiksai.core.auth.discovery import (
    OIDCDiscoveryClient,
    CachedDiscovery,
    get_discovery_client,
    reset_discovery_client,
)

# JWT validation
from mozaiksai.core.auth.jwt_validator import (
    JWTValidator,
    TokenClaims,
    AuthError,
    get_jwt_validator,
    reset_jwt_validator,
)

# JWKS client
from mozaiksai.core.auth.jwks import (
    JWKSClient,
    get_jwks_client,
    reset_jwks_client,
)

# HTTP dependencies
from mozaiksai.core.auth.dependencies import (
    UserPrincipal,
    ServicePrincipal,
    require_user,
    require_user_scope,
    require_any_auth,
    require_internal,
    require_role,
    require_any_role,
    optional_user,
    require_execution_token,
    validate_path_app_id,
    validate_path_chat_id,
    validate_user_id_against_principal,
)

# WebSocket authentication
from mozaiksai.core.auth.websocket_auth import (
    WebSocketUser,
    authenticate_websocket,
    authenticate_websocket_with_path_user,
    authenticate_websocket_with_path_binding,
    verify_user_owns_resource,
    require_resource_ownership,
    WS_CLOSE_POLICY_VIOLATION,
    WS_CLOSE_AUTH_REQUIRED,
    WS_CLOSE_AUTH_INVALID,
    WS_CLOSE_ACCESS_DENIED,
)

__all__ = [
    # Config
    "AuthConfig",
    "get_auth_config",
    "clear_auth_config_cache",
    # OIDC Discovery
    "OIDCDiscoveryClient",
    "CachedDiscovery",
    "get_discovery_client",
    "reset_discovery_client",
    # JWT
    "JWTValidator",
    "TokenClaims",
    "AuthError",
    "get_jwt_validator",
    "reset_jwt_validator",
    # JWKS
    "JWKSClient",
    "get_jwks_client",
    "reset_jwks_client",
    # HTTP Dependencies
    "UserPrincipal",
    "ServicePrincipal",
    "require_user",
    "require_user_scope",
    "require_any_auth",
    "require_internal",
    "require_role",
    "require_any_role",
    "optional_user",
    "require_execution_token",
    "validate_path_app_id",
    "validate_path_chat_id",
    "validate_user_id_against_principal",
    # WebSocket
    "WebSocketUser",
    "authenticate_websocket",
    "authenticate_websocket_with_path_user",
    "authenticate_websocket_with_path_binding",
    "verify_user_owns_resource",
    "require_resource_ownership",
    "WS_CLOSE_POLICY_VIOLATION",
    "WS_CLOSE_AUTH_REQUIRED",
    "WS_CLOSE_AUTH_INVALID",
    "WS_CLOSE_ACCESS_DENIED",
]

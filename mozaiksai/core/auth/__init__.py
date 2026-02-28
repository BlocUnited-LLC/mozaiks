"""
MozaiksAI Authentication Module (Transport-Level Only).

Mozaiks ships with **Keycloak** as the native identity provider.
Configuration is loaded from `brand/public/auth.json` with env var overrides.

This module provides **authentication**, not authorization.

    MozaiksAI authenticates requests but does not authorize behavior.
    Authorization is delegated to the host control plane (MozaiksCore or customer app).

What This Module Does (Authentication):
    - Validates JWT signatures via OIDC discovery / JWKS (Keycloak default)
    - Verifies issuer, audience, expiration
    - Extracts identity claims (sub, email, scopes, realm_access.roles)
    - Rejects anonymous/invalid traffic

What This Module Does NOT Do (Authorization):
    - User account management
    - Subscription/entitlement checks
    - "Is user allowed to run workflow X?" decisions
    - Billing or feature gating

Configuration:
    Primary: `brand/public/auth.json` (declarative, per-app config)
    Override: Environment variables (deployment-specific)
    Fallback: Built-in Keycloak defaults (localhost:8080/realms/mozaiks)

    See `auth.json` reference: docs/guides/customizing-frontend/06-auth-json.md

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

Environment Variables (override auth.json when set):
    AUTH_ENABLED=false              # Bypass auth for local dev
    MOZAIKS_OIDC_AUTHORITY=http://localhost:8080/realms/mozaiks
    AUTH_AUDIENCE=mozaiks-app       # Keycloak client ID
    AUTH_REQUIRED_SCOPE=openid
    AUTH_ROLES_CLAIM=realm_access   # Keycloak role claim
"""

# Configuration
from mozaiksai.core.auth.config import (
    AuthConfig,
    get_auth_config,
    clear_auth_config_cache,
)

# File-based config loader (auth.json)
from mozaiksai.core.auth.auth_config_loader import (
    load_auth_json,
    clear_auth_json_cache,
    derive_auth_env,
    get_keycloak_branding,
    get_keycloak_realm_config,
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
    # File-based config (auth.json)
    "load_auth_json",
    "clear_auth_json_cache",
    "derive_auth_env",
    "get_keycloak_branding",
    "get_keycloak_realm_config",
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

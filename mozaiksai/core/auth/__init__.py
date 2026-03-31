"""
mozaiksai Authentication Module - Provider-Agnostic Auth System.

This module provides **authentication**, not authorization.

    mozaiksai authenticates requests but does not authorize behavior.
    Authorization is delegated to the host control plane or customer app.

Supported Auth Providers:
    - none: No authentication (demo/development mode)
    - jwt: Generic OIDC/JWT (any compliant provider)
    - supabase: Supabase Auth
    - keycloak: Keycloak
    - Custom: Register your own adapter

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

    # Provider Selection (auto-detected if not set)
    AUTH_PROVIDER=none|jwt|supabase|keycloak
    AUTH_ENABLED=true|false   # false = same as AUTH_PROVIDER=none

    # Generic JWT Configuration
    AUTH_JWKS_URL=https://.../.well-known/jwks.json
    AUTH_ISSUER=https://...
    AUTH_AUDIENCE=my-api
    AUTH_USER_ID_CLAIM=sub
    AUTH_EMAIL_CLAIM=email
    AUTH_ROLES_CLAIM=roles
    AUTH_SCOPES_CLAIM=scp
    AUTH_SCOPES_FORMAT=space|array

    # Supabase Configuration
    SUPABASE_URL=https://xyzcompany.supabase.co
    SUPABASE_JWT_SECRET=...  # Optional for local dev

    # Keycloak Configuration
    KEYCLOAK_URL=https://keycloak.example.com
    KEYCLOAK_REALM=myrealm
    KEYCLOAK_CLIENT_ID=my-app

Custom Adapter Registration:
    from mozaiksai.core.auth.adapters import register_adapter

    class MyCustomAdapter:
        name = "my-custom"

        async def validate_token(self, token: str) -> UserClaims:
            # Your validation logic
            ...

        def is_enabled(self) -> bool:
            return True

    register_adapter("my-custom", MyCustomAdapter)
"""

# Adapters (new pluggable system)
from mozaiksai.core.auth.adapters import (
    AuthAdapter,
    UserClaims,
    AuthError,
    get_auth_adapter,
    register_adapter,
    list_adapters,
)
from mozaiksai.core.auth.adapters.registry import (
    is_auth_enabled,
    reset_auth_adapter,
)

# HTTP dependencies
from mozaiksai.core.auth.dependencies import (
    UserPrincipal,
    require_user,
    require_user_scope,
    require_any_auth,
    require_role,
    require_any_role,
    optional_user,
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

# Legacy compatibility - keep old config exports
from mozaiksai.core.auth.config import (
    AuthConfig,
    get_auth_config,
    clear_auth_config_cache,
)

__all__ = [
    # Adapters (new)
    "AuthAdapter",
    "UserClaims",
    "AuthError",
    "get_auth_adapter",
    "register_adapter",
    "list_adapters",
    "is_auth_enabled",
    "reset_auth_adapter",
    # HTTP Dependencies
    "UserPrincipal",
    "require_user",
    "require_user_scope",
    "require_any_auth",
    "require_role",
    "require_any_role",
    "optional_user",
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
    # Legacy config (for backwards compatibility)
    "AuthConfig",
    "get_auth_config",
    "clear_auth_config_cache",
]

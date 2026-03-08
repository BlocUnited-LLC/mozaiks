"""
Runtime auth sub-package.

Re-exports all public symbols so consumers can do:
    from mozaiksai.runtime.auth import UserPrincipal, get_auth_config, ...
"""

# -- config ------------------------------------------------------------------
from mozaiksai.runtime.auth.config import (
    AuthConfig,
    get_auth_config,
    clear_auth_config_cache,
)

# -- auth_config_loader (declarative app.json bridge) -----------------------
from mozaiksai.runtime.auth.auth_config_loader import (
    load_app_json,
    load_auth_json,       # backwards-compat alias → load_app_json
    clear_app_json_cache,
    clear_auth_json_cache,  # backwards-compat alias → clear_app_json_cache
    derive_auth_env,
    get_keycloak_branding,
    get_keycloak_realm_config,
)

# -- OIDC discovery ----------------------------------------------------------
from mozaiksai.runtime.auth.discovery import (
    OIDCDiscoveryClient,
    CachedDiscovery,
    get_discovery_client,
    reset_discovery_client,
)

# -- JWKS --------------------------------------------------------------------
from mozaiksai.runtime.auth.jwks import (
    JWKSClient,
    CachedJWKS,
    get_jwks_client,
    reset_jwks_client,
)

# -- JWT validation ----------------------------------------------------------
from mozaiksai.runtime.auth.jwt_validator import (
    JWTValidator,
    TokenClaims,
    AuthError,
    get_jwt_validator,
    reset_jwt_validator,
)

# -- FastAPI HTTP dependencies ------------------------------------------------
from mozaiksai.runtime.auth.dependencies import (
    UserPrincipal,
    ServicePrincipal,
    require_user,
    require_user_scope,
    require_any_auth,
    require_internal,
    require_role,
    require_any_role,
    require_execution_token,
    optional_user,
    validate_path_app_id,
    validate_path_chat_id,
    validate_user_id_against_principal,
)

# -- WebSocket auth -----------------------------------------------------------
from mozaiksai.runtime.auth.websocket_auth import (
    WebSocketUser,
    WS_CLOSE_POLICY_VIOLATION,
    authenticate_websocket,
    authenticate_websocket_with_path_user,
    authenticate_websocket_with_path_binding,
    verify_user_owns_resource,
    require_resource_ownership,
)

__all__ = [
    # config
    "AuthConfig",
    "get_auth_config",
    "clear_auth_config_cache",
    # auth_config_loader
    "load_app_json",
    "load_auth_json",  # backwards-compat alias
    "clear_app_json_cache",
    "clear_auth_json_cache",  # backwards-compat alias
    "derive_auth_env",
    "get_keycloak_branding",
    "get_keycloak_realm_config",
    # discovery
    "OIDCDiscoveryClient",
    "CachedDiscovery",
    "get_discovery_client",
    "reset_discovery_client",
    # jwks
    "JWKSClient",
    "CachedJWKS",
    "get_jwks_client",
    "reset_jwks_client",
    # jwt_validator
    "JWTValidator",
    "TokenClaims",
    "AuthError",
    "get_jwt_validator",
    "reset_jwt_validator",
    # dependencies (HTTP)
    "UserPrincipal",
    "ServicePrincipal",
    "require_user",
    "require_user_scope",
    "require_any_auth",
    "require_internal",
    "require_role",
    "require_any_role",
    "require_execution_token",
    "optional_user",
    "validate_path_app_id",
    "validate_path_chat_id",
    "validate_user_id_against_principal",
    # websocket_auth
    "WebSocketUser",
    "WS_CLOSE_POLICY_VIOLATION",
    "authenticate_websocket",
    "authenticate_websocket_with_path_user",
    "authenticate_websocket_with_path_binding",
    "verify_user_owns_resource",
    "require_resource_ownership",
]

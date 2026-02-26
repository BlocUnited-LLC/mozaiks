"""Auth primitives and middleware (shared runtime layer).

Lower-level JWT/OIDC:
    JWTValidator, JWTValidatorConfig, JWKSClient, OIDCDiscoveryClient, …

Higher-level middleware (used by consuming platforms):
    AuthConfig, get_auth_config, WebSocketUser, UserPrincipal, ServicePrincipal,
    authenticate_websocket*, verify_user_owns_resource, require_user, require_role, …
"""

from mozaiks.core.auth.config import JWTValidatorConfig
from mozaiks.core.auth.discovery import OIDCDiscoveryClient, OIDCDiscoveryDocument
from mozaiks.core.auth.jwks import JWKSClient
from mozaiks.core.auth.jwt_validator import (
    JWTValidationError,
    JWTValidator,
    OIDCResolutionError,
    SigningKeyError,
    TokenExpiredError,
)
from mozaiks.core.auth.middleware import (
    AuthConfig,
    ServicePrincipal,
    UserPrincipal,
    WebSocketUser,
    WS_CLOSE_POLICY_VIOLATION,
    authenticate_websocket,
    authenticate_websocket_with_path_binding,
    authenticate_websocket_with_path_user,
    get_auth_config,
    optional_user,
    require_any_auth,
    require_internal,
    require_resource_ownership,
    require_role,
    require_user,
    require_user_scope,
    verify_user_owns_resource,
)

__all__ = [
    # JWT / OIDC primitives
    "JWTValidationError",
    "JWTValidator",
    "JWTValidatorConfig",
    "JWKSClient",
    "OIDCDiscoveryClient",
    "OIDCDiscoveryDocument",
    "OIDCResolutionError",
    "SigningKeyError",
    "TokenExpiredError",
    # Middleware — principals
    "AuthConfig",
    "WebSocketUser",
    "UserPrincipal",
    "ServicePrincipal",
    # Middleware — config
    "get_auth_config",
    # Middleware — WebSocket auth
    "authenticate_websocket",
    "authenticate_websocket_with_path_user",
    "authenticate_websocket_with_path_binding",
    # Middleware — resource ownership
    "verify_user_owns_resource",
    "require_resource_ownership",
    "require_user_scope",
    # Middleware — FastAPI dependency guards
    "require_user",
    "require_any_auth",
    "require_internal",
    "require_role",
    "optional_user",
    # Constants
    "WS_CLOSE_POLICY_VIOLATION",
]

"""
WebSocket authentication helper.

Validates access token at connection time and binds user context to websocket.state.

Usage:
    from mozaiksai.core.auth.websocket_auth import authenticate_websocket, WebSocketUser

    @app.websocket("/ws/chat/{chat_id}")
    async def websocket_endpoint(websocket: WebSocket, chat_id: str):
        # Authenticate and get user context
        user = await authenticate_websocket(websocket)
        if user is None:
            return  # Connection already closed with 1008

        # User context is now bound to websocket.state
        assert websocket.state.user_id == user.user_id

        # Verify user owns this chat (example)
        if not await verify_chat_ownership(chat_id, user.user_id):
            await websocket.close(code=1008, reason="Access denied")
            return

        await websocket.accept()
        ...
"""

import os
from typing import Optional, List, Any
from dataclasses import dataclass

from fastapi import WebSocket, Query

from mozaiksai.core.auth.config import get_auth_config
from mozaiksai.core.auth.jwt_validator import get_jwt_validator, TokenClaims, AuthError
from logs.logging_config import get_core_logger

logger = get_core_logger("auth.websocket")


# WebSocket close code 1008 = Policy Violation (RFC 6455)
# Used for all auth failures to indicate the connection violates server policy
WS_CLOSE_POLICY_VIOLATION = 1008

# Legacy close codes (kept for backwards compatibility, but 1008 is now primary)
WS_CLOSE_AUTH_REQUIRED = 1008
WS_CLOSE_AUTH_INVALID = 1008
WS_CLOSE_ACCESS_DENIED = 1008


@dataclass
class WebSocketUser:
    """Authenticated WebSocket user context."""

    user_id: str
    email: Optional[str]
    roles: List[str]
    scopes: List[str]
    raw_claims: dict
    # MozaiksCore execution claims (bound from token if present)
    mozaiks_token_use: Optional[str] = None
    mozaiks_app_id: Optional[str] = None
    mozaiks_chat_id: Optional[str] = None
    mozaiks_capability_id: Optional[str] = None

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    @property
    def is_execution_token(self) -> bool:
        """Check if this is a MozaiksCore execution token."""
        return self.mozaiks_token_use == "execution"

    def validate_app_id(self, path_app_id: str) -> bool:
        """Validate that token app_id matches path/payload app_id."""
        if not self.mozaiks_app_id:
            return True  # No app_id claim - legacy token
        return str(self.mozaiks_app_id) == str(path_app_id)

    def validate_chat_id(self, path_chat_id: str) -> bool:
        """Validate that token chat_id matches path/payload chat_id."""
        if not self.mozaiks_chat_id:
            return True  # No chat_id claim - session not bound
        return str(self.mozaiks_chat_id) == str(path_chat_id)


async def authenticate_websocket(
    websocket: WebSocket,
    access_token: Optional[str] = None,
    require_scope: bool = True,
    reject_app_only: bool = True,
) -> Optional[WebSocketUser]:
    """
    Authenticate a WebSocket connection.

    Token is extracted from:
    1. `access_token` parameter passed directly
    2. `access_token` query parameter (if MOZAIKS_WS_ALLOW_QUERY_TOKEN=true)

    Args:
        websocket: The WebSocket connection to authenticate
        access_token: Token passed directly (e.g., from header extraction)
        require_scope: Whether to require the delegated user scope (default True)
        reject_app_only: Whether to reject app-only tokens (default True for user WS)

    On success:
    - Returns WebSocketUser
    - Binds user_id, email, roles to websocket.state

    On failure:
    - Closes connection with code 1008 (Policy Violation)
    - Returns None

    Usage:
        @app.websocket("/ws/chat")
        async def chat_ws(websocket: WebSocket):
            user = await authenticate_websocket(websocket)
            if user is None:
                return  # Already closed

            await websocket.accept()
            # Use websocket.state.user_id
    """
    config = get_auth_config()

    # Auth bypass for local development
    if not config.enabled:
        logger.warning("Auth disabled - using anonymous WebSocket user")
        user = WebSocketUser(
            user_id="anonymous",
            email=None,
            roles=[],
            scopes=["access_as_user"],
            raw_claims={},
        )
        websocket.state.user_id = user.user_id
        websocket.state.email = user.email
        websocket.state.roles = user.roles
        websocket.state.user = user
        return user

    # Extract token
    token = access_token
    
    # Allow query param extraction by default (browsers can't set WS headers)
    # Set MOZAIKS_WS_ALLOW_QUERY_TOKEN=false to disable for reverse-proxy setups
    # that inject tokens via headers
    if not token:
        allow_query_token = os.getenv("MOZAIKS_WS_ALLOW_QUERY_TOKEN", "true").lower() in ("true", "1", "yes")
        if allow_query_token:
            token = websocket.query_params.get("access_token")
        elif websocket.query_params.get("access_token"):
            logger.warning("WebSocket query param token rejected (MOZAIKS_WS_ALLOW_QUERY_TOKEN=false)")

    if not token:
        logger.warning("WebSocket connection rejected: missing access_token")
        await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason="Missing access_token")
        return None

    # Validate token
    validator = get_jwt_validator()
    try:
        claims: TokenClaims = await validator.validate_token(token, require_scope=require_scope)
    except AuthError as e:
        logger.warning(f"WebSocket auth failed: {e.message}")
        await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason=e.message)
        return None
    except Exception as e:
        logger.error(f"WebSocket auth error: {e}")
        await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason="Authentication failed")
        return None

    # Reject app-only tokens on user WebSocket endpoints
    if reject_app_only:
        if not claims.scopes or config.required_scope not in claims.scopes:
            logger.warning("WebSocket rejected: app-only token used on user endpoint")
            await websocket.close(
                code=WS_CLOSE_POLICY_VIOLATION,
                reason="User endpoint requires delegated user token"
            )
            return None

    # Build user context
    user = WebSocketUser(
        user_id=claims.user_id,
        email=claims.email,
        roles=claims.roles,
        scopes=claims.scopes,
        raw_claims=claims.raw_claims,
        mozaiks_token_use=claims.mozaiks_token_use,
        mozaiks_app_id=claims.mozaiks_app_id,
        mozaiks_chat_id=claims.mozaiks_chat_id,
        mozaiks_capability_id=claims.mozaiks_capability_id,
    )

    # Bind to websocket.state for downstream access
    websocket.state.user_id = user.user_id
    websocket.state.email = user.email
    websocket.state.roles = user.roles
    websocket.state.user = user

    logger.debug(f"WebSocket authenticated: user_id={user.user_id}")
    return user


def verify_user_owns_resource(
    token_user_id: str,
    resource_user_id: str,
) -> bool:
    """
    Verify that the authenticated user owns a resource.

    Use this to prevent users from accessing other users' chats/apps.

    Args:
        token_user_id: user_id from validated token (websocket.state.user_id)
        resource_user_id: user_id from route param or database lookup

    Returns:
        True if user owns resource, False otherwise
    """
    if not token_user_id or not resource_user_id:
        return False
    return str(token_user_id) == str(resource_user_id)


async def require_resource_ownership(
    websocket: WebSocket,
    resource_user_id: str,
) -> bool:
    """
    Verify resource ownership and close connection if denied.

    Usage:
        user = await authenticate_websocket(websocket)
        if not user:
            return

        chat = await get_chat(chat_id)
        if not await require_resource_ownership(websocket, chat.user_id):
            return  # Already closed with 1008

        await websocket.accept()
    """
    token_user_id = getattr(websocket.state, "user_id", None)

    if not verify_user_owns_resource(token_user_id, resource_user_id):
        logger.warning(
            f"WebSocket access denied: token user {token_user_id} "
            f"tried to access resource owned by {resource_user_id}"
        )
        await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason="Access denied")
        return False

    return True


async def authenticate_websocket_with_path_user(
    websocket: WebSocket,
    path_user_id: str,
    access_token: Optional[str] = None,
) -> Optional[WebSocketUser]:
    """
    Authenticate WebSocket AND validate that JWT user matches path user_id.
    
    This is for backward-compatible routes that have {user_id} in the path.
    The path user_id MUST match the JWT sub claim.
    
    Args:
        websocket: The WebSocket connection
        path_user_id: The user_id from the URL path
        access_token: Optional token (e.g., from header)
    
    Returns:
        WebSocketUser if authenticated and user_id matches, None otherwise
    """
    config = get_auth_config()
    
    # Auth bypass for local development - use path user_id as identity
    if not config.enabled:
        logger.warning("Auth disabled - using path user_id for WebSocket")
        user = WebSocketUser(
            user_id=path_user_id,
            email=None,
            roles=[],
            scopes=["access_as_user"],
            raw_claims={},
        )
        websocket.state.user_id = user.user_id
        websocket.state.email = user.email
        websocket.state.roles = user.roles
        websocket.state.user = user
        return user
    
    # Authenticate with JWT
    user = await authenticate_websocket(
        websocket,
        access_token=access_token,
        require_scope=True,
        reject_app_only=True,
    )
    
    if user is None:
        return None  # Already closed by authenticate_websocket
    
    # Validate path user_id matches JWT
    if not verify_user_owns_resource(user.user_id, path_user_id):
        logger.warning(
            f"WebSocket user_id mismatch: JWT user {user.user_id} "
            f"tried to connect as path user {path_user_id}"
        )
        await websocket.close(
            code=WS_CLOSE_POLICY_VIOLATION,
            reason="user_id mismatch"
        )
        return None
    
    return user


async def authenticate_websocket_with_path_binding(
    websocket: WebSocket,
    path_user_id: str,
    path_app_id: str,
    path_chat_id: Optional[str] = None,
    access_token: Optional[str] = None,
) -> Optional[WebSocketUser]:
    """
    Authenticate WebSocket AND validate that JWT claims match path parameters.
    
    This enforces:
    - JWT sub == path user_id
    - JWT app_id == path app_id (if app_id claim present)
    - JWT chat_id == path chat_id (if chat_id claim present)
    
    Args:
        websocket: The WebSocket connection
        path_user_id: The user_id from the URL path
        path_app_id: The app_id from the URL path
        path_chat_id: Optional chat_id from the URL path
        access_token: Optional token (e.g., from header)
    
    Returns:
        WebSocketUser if authenticated and all bindings match, None otherwise
    """
    # First authenticate with user_id validation
    user = await authenticate_websocket_with_path_user(
        websocket, path_user_id, access_token
    )
    
    if user is None:
        return None  # Already closed
    
    config = get_auth_config()
    if not config.enabled:
        return user  # Skip additional binding in dev mode
    
    # Validate app_id binding
    if not user.validate_app_id(path_app_id):
        logger.warning(
            f"WebSocket app_id mismatch: token={user.mozaiks_app_id}, path={path_app_id}"
        )
        await websocket.close(
            code=WS_CLOSE_POLICY_VIOLATION,
            reason="app_id mismatch"
        )
        return None
    
    # Validate chat_id binding (if path has chat_id and token has chat_id claim)
    if path_chat_id and not user.validate_chat_id(path_chat_id):
        logger.warning(
            f"WebSocket chat_id mismatch: token={user.mozaiks_chat_id}, path={path_chat_id}"
        )
        await websocket.close(
            code=WS_CLOSE_POLICY_VIOLATION,
            reason="chat_id mismatch"
        )
        return None
    
    return user

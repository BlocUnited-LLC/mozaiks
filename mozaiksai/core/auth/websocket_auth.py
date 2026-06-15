"""
WebSocket authentication helper.

Validates access token at connection time and binds user context to websocket.state.
Uses the pluggable auth adapter system for provider-agnostic authentication.

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
from dataclasses import dataclass

from fastapi import WebSocket

from logs.logging_config import get_core_logger
from mozaiksai.core.auth.adapters import AuthError, UserClaims, get_auth_adapter
from mozaiksai.core.auth.adapters.registry import is_auth_enabled

logger = get_core_logger("auth.websocket")


# WebSocket close code 1008 = Policy Violation (RFC 6455)
# Used for all auth failures to indicate the connection violates server policy
WS_CLOSE_POLICY_VIOLATION = 1008

# Named auth close codes. All auth failures use 1008.
WS_CLOSE_AUTH_REQUIRED = 1008
WS_CLOSE_AUTH_INVALID = 1008
WS_CLOSE_ACCESS_DENIED = 1008


@dataclass
class WebSocketUser:
    """Authenticated WebSocket user context."""

    user_id: str
    email: str | None
    name: str | None
    roles: list[str]
    scopes: list[str]
    raw_claims: dict
    provider: str = "unknown"
    # Optional binding claims
    app_id: str | None = None
    chat_id: str | None = None
    tenant_id: str | None = None

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def validate_app_id(self, path_app_id: str) -> bool:
        """Validate that token app_id matches path/payload app_id."""
        if not self.app_id:
            return True  # User token is not app-bound
        return str(self.app_id) == str(path_app_id)

    def validate_chat_id(self, path_chat_id: str) -> bool:
        """Validate that token chat_id matches path/payload chat_id."""
        if not self.chat_id:
            return True  # No chat_id claim - session not bound
        return str(self.chat_id) == str(path_chat_id)

    @classmethod
    def from_claims(cls, claims: UserClaims) -> "WebSocketUser":
        """Create WebSocketUser from adapter UserClaims."""
        return cls(
            user_id=claims.user_id,
            email=claims.email,
            name=claims.name,
            roles=claims.roles,
            scopes=claims.scopes,
            raw_claims=claims.raw_claims,
            provider=claims.provider,
            app_id=claims.app_id,
            chat_id=claims.chat_id,
            tenant_id=claims.tenant_id,
        )


async def authenticate_websocket(
    websocket: WebSocket,
    access_token: str | None = None,
) -> WebSocketUser | None:
    """
    Authenticate a WebSocket connection using the configured auth adapter.

    Token is extracted from:
    1. `access_token` parameter passed directly
    2. `access_token` query parameter (if MOZAIKS_WS_ALLOW_QUERY_TOKEN=true)

    Args:
        websocket: The WebSocket connection to authenticate
        access_token: Token passed directly (e.g., from header extraction)

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
    # Get the configured auth adapter
    adapter = get_auth_adapter()

    # Check if auth is enabled
    if not is_auth_enabled():
        # No auth mode - create anonymous user
        logger.debug(f"Auth disabled - using anonymous WebSocket user (provider: {adapter.name})")
        try:
            claims = await adapter.validate_token("")  # NoAuthAdapter ignores token
            user = WebSocketUser.from_claims(claims)
        except Exception:
            # Fallback if adapter doesn't support empty token
            user = WebSocketUser(
                user_id="anonymous",
                email=None,
                name="Anonymous User",
                roles=[],
                scopes=["access_as_user"],
                raw_claims={},
                provider="none",
            )

        _bind_user_to_websocket(websocket, user)
        return user

    # Extract token
    token = access_token

    # Query-param token extraction is disabled by default.
    # Tokens in query params appear in server logs, browser history, and proxy logs.
    # Enable only for local dev or architectures where WebSocket header auth is unavailable:
    #   MOZAIKS_WS_ALLOW_QUERY_TOKEN=true
    if not token:
        allow_query_token = os.getenv("MOZAIKS_WS_ALLOW_QUERY_TOKEN", "false").lower() in ("true", "1", "yes")
        if allow_query_token:
            token = websocket.query_params.get("access_token")
        elif websocket.query_params.get("access_token"):
            logger.warning("WebSocket query param token rejected (MOZAIKS_WS_ALLOW_QUERY_TOKEN=false)")

    if not token:
        logger.warning("WebSocket connection rejected: missing access_token")
        await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason="Missing access_token")
        return None

    # Validate token using adapter
    try:
        claims = await adapter.validate_token(token)
    except AuthError as e:
        logger.warning(f"WebSocket auth failed ({adapter.name}): {e.message}")
        await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason=e.message)
        return None
    except Exception as e:
        logger.error("WebSocket auth error (%s): %s", adapter.name, e, exc_info=True)
        await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason="Authentication failed")
        return None

    # Build user context
    user = WebSocketUser.from_claims(claims)

    # Bind to websocket.state for downstream access
    _bind_user_to_websocket(websocket, user)

    logger.debug(f"WebSocket authenticated ({adapter.name}): user_id={user.user_id}")
    return user


def _bind_user_to_websocket(websocket: WebSocket, user: WebSocketUser) -> None:
    """Bind user context to websocket.state."""
    websocket.state.user_id = user.user_id
    websocket.state.email = user.email
    websocket.state.name = user.name
    websocket.state.roles = user.roles
    websocket.state.user = user


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

    if not verify_user_owns_resource(token_user_id, resource_user_id):  # type: ignore[arg-type]
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
    access_token: str | None = None,
) -> WebSocketUser | None:
    """
    Authenticate WebSocket AND validate that JWT user matches path user_id.

    This is for routes that have {user_id} in the path.
    The path user_id MUST match the JWT sub claim.

    Args:
        websocket: The WebSocket connection
        path_user_id: The user_id from the URL path
        access_token: Optional token (e.g., from header)

    Returns:
        WebSocketUser if authenticated and user_id matches, None otherwise
    """
    # Auth bypass for local development - use path user_id as identity
    if not is_auth_enabled():
        logger.debug("Auth disabled - using path user_id for WebSocket")
        user = WebSocketUser(
            user_id=path_user_id,
            email=None,
            name=None,
            roles=[],
            scopes=["access_as_user"],
            raw_claims={},
            provider="none",
        )
        _bind_user_to_websocket(websocket, user)
        return user

    # Authenticate with adapter
    user = await authenticate_websocket(websocket, access_token=access_token)  # type: ignore[assignment]

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
    path_chat_id: str | None = None,
    access_token: str | None = None,
) -> WebSocketUser | None:
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

    if not is_auth_enabled():
        return user  # Skip additional binding in dev mode

    # Validate app_id binding
    if not user.validate_app_id(path_app_id):
        logger.warning(
            f"WebSocket app_id mismatch: token={user.app_id}, path={path_app_id}"
        )
        await websocket.close(
            code=WS_CLOSE_POLICY_VIOLATION,
            reason="app_id mismatch"
        )
        return None

    # Validate chat_id binding (if path has chat_id and token has chat_id claim)
    if path_chat_id and not user.validate_chat_id(path_chat_id):
        logger.warning(
            f"WebSocket chat_id mismatch: token={user.chat_id}, path={path_chat_id}"
        )
        await websocket.close(
            code=WS_CLOSE_POLICY_VIOLATION,
            reason="chat_id mismatch"
        )
        return None

    return user

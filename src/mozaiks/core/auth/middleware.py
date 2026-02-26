"""Auth middleware and dependency-injection helpers (shared runtime layer).

This module provides the high-level authentication / authorisation primitives
that consuming platforms import from ``mozaiks.core.auth``.  The underlying
JWT validation is handled by :mod:`mozaiks.core.auth.jwt_validator`.

Responsibilities (shared runtime infrastructure):
  - WebSocket authentication strategies
  - HTTP dependency-injection guards (``require_user``, ``require_role`` …)
  - Resource-ownership checks
  - Principal data classes (``WebSocketUser``, ``UserPrincipal``, ``ServicePrincipal``)
  - ``AuthConfig`` — environment-driven configuration

These are framework-level concerns that run once and are shared across
all workflows.  The execution runtime (``mozaiks.orchestration``) must never own these.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from starlette.websockets import WebSocket, WebSocketState

from mozaiks.core.auth.config import JWTValidatorConfig
from mozaiks.core.auth.jwt_validator import JWTValidationError, JWTValidator
from mozaiks.core.config import get_settings

logger = logging.getLogger(__name__)

# RFC 6455 close code — policy violation.
WS_CLOSE_POLICY_VIOLATION: int = 1008


# ---------------------------------------------------------------------------
# Principal data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UserPrincipal:
    """Authenticated human user."""

    user_id: str
    display_name: str = ""
    roles: list[str] = field(default_factory=list)
    claims: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ServicePrincipal:
    """Authenticated service / machine identity."""

    service_id: str
    roles: list[str] = field(default_factory=list)
    claims: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WebSocketUser:
    """Identity resolved during WebSocket authentication."""

    user_id: str
    display_name: str = ""
    roles: list[str] = field(default_factory=list)
    app_id: str | None = None
    claims: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# AuthConfig
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AuthConfig:
    """High-level auth configuration derived from environment settings."""

    authority: str = ""
    audience: str = ""
    issuer: str = ""
    discovery_url: str = ""
    jwks_url: str = ""


def get_auth_config() -> AuthConfig:
    """Build :class:`AuthConfig` from the kernel :class:`Settings`."""
    s = get_settings()
    return AuthConfig(
        authority=s.OIDC_DISCOVERY_URL or "",
        audience=s.JWT_AUDIENCE or "",
        issuer=s.JWT_ISSUER or "",
        discovery_url=s.OIDC_DISCOVERY_URL or "",
        jwks_url=s.JWKS_URL or "",
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_validator() -> JWTValidator:
    """Construct a :class:`JWTValidator` from current settings."""
    s = get_settings()
    config = JWTValidatorConfig.from_settings(s)
    return JWTValidator(config)


async def _extract_claims(token: str) -> dict[str, Any]:
    """Validate *token* and return the decoded claims dict."""
    validator = _build_validator()
    return await validator.validate(token)


def _user_from_claims(claims: dict[str, Any]) -> WebSocketUser:
    return WebSocketUser(
        user_id=claims.get("sub", ""),
        display_name=claims.get("name", claims.get("preferred_username", "")),
        roles=claims.get("roles", []),
        app_id=claims.get("app_id"),
        claims=claims,
    )


def _principal_from_claims(claims: dict[str, Any]) -> UserPrincipal:
    return UserPrincipal(
        user_id=claims.get("sub", ""),
        display_name=claims.get("name", claims.get("preferred_username", "")),
        roles=claims.get("roles", []),
        claims=claims,
    )


# ---------------------------------------------------------------------------
# WebSocket authentication strategies
# ---------------------------------------------------------------------------

async def authenticate_websocket(
    websocket: WebSocket,
    *,
    token: str | None = None,
) -> WebSocketUser:
    """Authenticate a WebSocket connection using a bearer token.

    The token may be provided explicitly or extracted from the
    ``Authorization`` header / ``token`` query parameter.
    """
    if token is None:
        token = _extract_ws_token(websocket)
    if not token:
        await _close_ws(websocket, WS_CLOSE_POLICY_VIOLATION, "Missing auth token")
        raise JWTValidationError("No token provided")

    try:
        claims = await _extract_claims(token)
    except JWTValidationError:
        await _close_ws(websocket, WS_CLOSE_POLICY_VIOLATION, "Invalid token")
        raise

    return _user_from_claims(claims)


async def authenticate_websocket_with_path_user(
    websocket: WebSocket,
    *,
    user_id: str | None = None,
    token: str | None = None,
) -> WebSocketUser:
    """Authenticate WebSocket and verify *user_id* matches the token subject."""
    user = await authenticate_websocket(websocket, token=token)
    if user_id is not None and user.user_id != user_id:
        await _close_ws(websocket, WS_CLOSE_POLICY_VIOLATION, "User mismatch")
        raise JWTValidationError(
            f"Token subject '{user.user_id}' does not match path user '{user_id}'"
        )
    return user


async def authenticate_websocket_with_path_binding(
    websocket: WebSocket,
    *,
    binding_id: str | None = None,
    token: str | None = None,
) -> WebSocketUser:
    """Authenticate WebSocket and attach *binding_id* as ``app_id``."""
    user = await authenticate_websocket(websocket, token=token)
    if binding_id is not None:
        # Return a copy with the binding attached.
        return WebSocketUser(
            user_id=user.user_id,
            display_name=user.display_name,
            roles=user.roles,
            app_id=binding_id,
            claims=user.claims,
        )
    return user


# ---------------------------------------------------------------------------
# Resource ownership
# ---------------------------------------------------------------------------

async def verify_user_owns_resource(
    *,
    user_id: str,
    resource_owner_id: str,
) -> bool:
    """Return *True* when the authenticated user owns the resource."""
    return user_id == resource_owner_id


async def require_resource_ownership(
    *,
    user_id: str,
    resource_owner_id: str,
) -> None:
    """Raise if the authenticated user does not own the resource."""
    if user_id != resource_owner_id:
        raise PermissionError(
            f"User '{user_id}' does not own resource owned by '{resource_owner_id}'"
        )


async def require_user_scope(
    *,
    user: UserPrincipal | WebSocketUser,
    required_scope: str,
) -> None:
    """Raise if the user's roles do not include *required_scope*."""
    if required_scope not in (user.roles or []):
        raise PermissionError(f"User lacks required scope '{required_scope}'")


# ---------------------------------------------------------------------------
# FastAPI dependency-injection guards
# ---------------------------------------------------------------------------

async def require_user(token: str) -> UserPrincipal:
    """FastAPI dependency — resolve a :class:`UserPrincipal` from bearer token."""
    claims = await _extract_claims(token)
    return _principal_from_claims(claims)


async def require_any_auth(token: str) -> UserPrincipal | ServicePrincipal:
    """FastAPI dependency — accept user or service token."""
    claims = await _extract_claims(token)
    if claims.get("client_id") and not claims.get("sub"):
        return ServicePrincipal(
            service_id=claims["client_id"],
            roles=claims.get("roles", []),
            claims=claims,
        )
    return _principal_from_claims(claims)


async def require_internal(token: str) -> ServicePrincipal:
    """FastAPI dependency — require a service-to-service token."""
    claims = await _extract_claims(token)
    client_id = claims.get("client_id", claims.get("azp", ""))
    if not client_id:
        raise PermissionError("Token is not a service principal")
    return ServicePrincipal(
        service_id=client_id,
        roles=claims.get("roles", []),
        claims=claims,
    )


def require_role(*roles: str) -> Any:
    """Return a FastAPI dependency that enforces role membership."""

    async def _guard(token: str) -> UserPrincipal:
        principal = await require_user(token)
        for role in roles:
            if role not in principal.roles:
                raise PermissionError(f"Missing required role: {role}")
        return principal

    return _guard


async def optional_user(token: str | None = None) -> UserPrincipal | None:
    """FastAPI dependency — resolve user if a token is present, else *None*."""
    if not token:
        return None
    try:
        return await require_user(token)
    except (JWTValidationError, Exception):
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_ws_token(websocket: WebSocket) -> str | None:
    """Pull a bearer token from the WebSocket request."""
    auth_header = websocket.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return websocket.query_params.get("token")


async def _close_ws(websocket: WebSocket, code: int, reason: str) -> None:
    """Safely close a WebSocket if it is still connected."""
    try:
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.close(code=code, reason=reason)
    except Exception:
        pass


__all__ = [
    "AuthConfig",
    "get_auth_config",
    "authenticate_websocket",
    "authenticate_websocket_with_path_user",
    "authenticate_websocket_with_path_binding",
    "verify_user_owns_resource",
    "require_resource_ownership",
    "require_user_scope",
    "WebSocketUser",
    "UserPrincipal",
    "ServicePrincipal",
    "require_user",
    "require_any_auth",
    "require_internal",
    "require_role",
    "optional_user",
    "WS_CLOSE_POLICY_VIOLATION",
]

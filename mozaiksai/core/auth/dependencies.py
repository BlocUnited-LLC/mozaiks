"""
FastAPI authentication dependencies for protected HTTP routes.

Uses the pluggable auth adapter system for provider-agnostic authentication.

Usage:
    from mozaiksai.core.auth.dependencies import require_user, require_any_auth

    @app.get("/api/user/profile")
    async def get_profile(user: UserPrincipal = Depends(require_user)):
        return {"user_id": user.user_id, "email": user.email}

    @app.get("/api/admin/stats")
    async def get_stats(user: UserPrincipal = Depends(require_role("admin"))):
        ...
"""

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from logs.logging_config import get_core_logger
from mozaiksai.core.auth.adapters import AuthError, UserClaims, get_auth_adapter
from mozaiksai.core.auth.adapters.registry import is_auth_enabled

logger = get_core_logger("auth.dependencies")

# FastAPI security scheme for OpenAPI docs
bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class UserPrincipal:
    """
    Authenticated user principal.

    Attached to request.state.user for downstream access.
    """

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
        """Check if user has a specific role."""
        return role in self.roles

    def has_any_role(self, roles: list[str]) -> bool:
        """Check if user has any of the specified roles."""
        return any(r in self.roles for r in roles)

    def has_scope(self, scope: str) -> bool:
        """Check if user has a specific scope."""
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
    def from_claims(cls, claims: UserClaims) -> "UserPrincipal":
        """Create UserPrincipal from adapter UserClaims."""
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


def _extract_token(
    authorization: HTTPAuthorizationCredentials | None,
    request: Request,
) -> str | None:
    """
    Extract bearer token from Authorization header or query param.

    Priority:
    1. Authorization: Bearer <token> header
    2. ?access_token=<token> query param (for WebSocket upgrade)
    """
    if authorization and authorization.credentials:
        return authorization.credentials

    # Fallback to query param (useful for WS upgrade requests)
    token = request.query_params.get("access_token")
    return token if token else None


async def _validate_and_attach(
    request: Request,
    token: str,
) -> UserPrincipal:
    """Validate token and attach user principal to request.state."""
    adapter = get_auth_adapter()

    try:
        claims = await adapter.validate_token(token)
    except AuthError as e:
        logger.warning(f"Auth failed ({adapter.name}): {e.message}")
        raise HTTPException(status_code=e.status_code, detail=e.message)

    principal = UserPrincipal.from_claims(claims)

    # Attach to request state for downstream access
    request.state.user = principal
    request.state.user_id = principal.user_id

    return principal


async def require_user(
    request: Request,
    authorization: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> UserPrincipal:
    """
    Dependency that requires a valid user token.

    Returns UserPrincipal on success, raises HTTPException on failure.
    """
    # Auth bypass for local development
    if not is_auth_enabled():
        adapter = get_auth_adapter()
        logger.debug(f"Auth disabled - using anonymous principal ({adapter.name})")
        try:
            claims = await adapter.validate_token("")
            principal = UserPrincipal.from_claims(claims)
        except Exception:
            principal = UserPrincipal(
                user_id="anonymous",
                email=None,
                name="Anonymous User",
                roles=[],
                scopes=["access_as_user"],
                raw_claims={},
                provider="none",
            )
        request.state.user = principal
        request.state.user_id = principal.user_id
        return principal

    token = _extract_token(authorization, request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing authorization token")

    return await _validate_and_attach(request, token)


async def require_any_auth(
    request: Request,
    authorization: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> UserPrincipal:
    """
    Dependency that requires any valid token (no scope enforcement).

    Useful for endpoints that should be accessible to any authenticated user
    regardless of delegated scopes.
    """
    # Same as require_user - adapter handles scope enforcement
    return await require_user(request, authorization)


def require_role(role: str) -> Callable:
    """
    Dependency factory that requires a specific role.

    Usage:
        @app.get("/admin")
        async def admin_only(user: UserPrincipal = Depends(require_role("admin"))):
            ...
    """

    async def role_checker(
        request: Request,
        authorization: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    ) -> UserPrincipal:
        principal = await require_user(request, authorization)
        if not principal.has_role(role):
            raise HTTPException(
                status_code=403,
                detail=f"Required role: {role}",
            )
        return principal

    return role_checker


def require_any_role(roles: list[str]) -> Callable:
    """
    Dependency factory that requires any of the specified roles.

    Usage:
        @app.get("/moderator")
        async def mod_area(user: UserPrincipal = Depends(require_any_role(["admin", "moderator"]))):
            ...
    """

    async def role_checker(
        request: Request,
        authorization: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    ) -> UserPrincipal:
        principal = await require_user(request, authorization)
        if not principal.has_any_role(roles):
            raise HTTPException(
                status_code=403,
                detail=f"Required roles (any): {roles}",
            )
        return principal

    return role_checker


async def optional_user(
    request: Request,
    authorization: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> UserPrincipal | None:
    """
    Dependency that optionally validates a token if present.

    Returns UserPrincipal if token is valid, None if no token.
    Raises HTTPException if token is present but invalid.
    """
    if not is_auth_enabled():
        return None

    token = _extract_token(authorization, request)
    if not token:
        return None

    return await _validate_and_attach(request, token)


# ---------------------------------------------------------------------------
# Semantic aliases for enforcement clarity
# ---------------------------------------------------------------------------

# Alias for user-facing endpoints requiring delegated user tokens
require_user_scope = require_user


# ---------------------------------------------------------------------------
# Path validation helpers
# ---------------------------------------------------------------------------

def validate_path_app_id(principal: UserPrincipal, path_app_id: str) -> None:
    """
    Validate that path app_id matches token app_id claim.

    Call this in endpoints after obtaining the principal to enforce binding.
    Raises HTTPException(403) if mismatch.

    Usage:
        @app.post("/api/chats/{app_id}/start")
        async def start_chat(
            app_id: str,
            user: UserPrincipal = Depends(require_user),
        ):
            validate_path_app_id(user, app_id)
            ...
    """
    if not is_auth_enabled():
        return  # Skip in dev mode

    if not principal.validate_app_id(path_app_id):
        logger.warning(
            f"app_id mismatch: token={principal.app_id}, path={path_app_id}"
        )
        raise HTTPException(
            status_code=403,
            detail="Token app_id does not match request app_id"
        )


def validate_path_chat_id(principal: UserPrincipal, path_chat_id: str) -> None:
    """
    Validate that path chat_id matches token chat_id claim (if bound).

    Call this in endpoints after obtaining the principal to enforce binding.
    Raises HTTPException(403) if mismatch.
    """
    if not is_auth_enabled():
        return  # Skip in dev mode

    if not principal.validate_chat_id(path_chat_id):
        logger.warning(
            f"chat_id mismatch: token={principal.chat_id}, path={path_chat_id}"
        )
        raise HTTPException(
            status_code=403,
            detail="Token chat_id does not match request chat_id"
        )


# ---------------------------------------------------------------------------
# Shared user-id validation helper
# ---------------------------------------------------------------------------

def validate_user_id_against_principal(
    principal: "UserPrincipal",
    path_user_id: str | None = None,
    body_user_id: str | None = None,
) -> str:
    """Validate that path/body user_id matches the authenticated principal.

    When auth is enabled:
        - If *path_user_id* is provided it MUST match ``principal.user_id``.
        - If *body_user_id* is provided it MUST match ``principal.user_id``.
        - Returns the canonical ``user_id`` from the principal.

    When auth is disabled (anonymous principal):
        - Falls back to *path_user_id* or *body_user_id*.
        - Raises HTTP 400 if neither is provided.
    """
    jwt_user_id = principal.user_id

    # Auth-disabled path — trust caller-supplied id.
    if jwt_user_id == "anonymous":
        user_id = path_user_id or body_user_id
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id is required")
        return user_id

    # Auth-enabled — enforce match.
    if path_user_id and str(path_user_id).strip() != str(jwt_user_id).strip():
        raise HTTPException(
            status_code=403,
            detail="user_id in path does not match authenticated user",
        )
    if body_user_id and str(body_user_id).strip() != str(jwt_user_id).strip():
        raise HTTPException(
            status_code=403,
            detail="user_id in request body does not match authenticated user",
        )
    return jwt_user_id

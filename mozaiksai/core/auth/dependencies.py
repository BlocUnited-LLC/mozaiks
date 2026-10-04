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

import re
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from logs.logging_config import get_core_logger
from mozaiksai.core.auth.adapters import AuthError, UserClaims, get_auth_adapter
from mozaiksai.core.auth.adapters.registry import (
    ResolvedAuthConfig,
    is_auth_enabled,
    resolve_auth_config,
)
from mozaiksai.core.auth.anonymous_access import (
    ANONYMOUS_PROVENANCE,
    DEV_OVERRIDE_PROVENANCE,
    DEVELOPMENT_ACCESS_PROVENANCES,
    LOCAL_DEVELOPMENT_PROVENANCE,
    AnonymousGrant,
    anonymous_claims,
    resolve_anonymous_grant,
)

logger = get_core_logger("auth.dependencies")

#: The user id of the shared anonymous principal when AUTH_ANON_USER_ID is unset.
ANONYMOUS_USER_ID = "anonymous"

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
    workspace_id: str | None = None
    # Server-side provenance fact: how this principal came into existence.
    # "token_validated" is set ONLY where a bearer token was actually
    # validated by the configured auth adapter. With authentication off,
    # "local_development" is the anonymous principal granted development
    # access for this request (see core/auth/anonymous_access.py),
    # "dev_override" a request-scoped persona (reachable only from
    # development access), and "anonymous" a visitor without development
    # access, which is also the default for any principal constructed
    # elsewhere. Privileged surfaces must check is_authenticated or
    # has_local_development_access rather than inferring authority from
    # role/scope strings or from the process-wide auth mode.
    auth_provenance: str = ANONYMOUS_PROVENANCE

    @property
    def is_authenticated(self) -> bool:
        """True only when this principal was produced by validating a real
        bearer token against the configured auth adapter."""
        return self.auth_provenance == "token_validated"

    @property
    def has_local_development_access(self) -> bool:
        """True only for an anonymous principal granted development access for
        this request, or a dev persona derived from one."""
        return self.auth_provenance in DEVELOPMENT_ACCESS_PROVENANCES

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

    def validate_tenant_id(self, path_tenant_id: str) -> bool:
        """Validate that token tenant_id matches path/payload tenant_id."""
        if not self.tenant_id:
            return True
        return str(self.tenant_id) == str(path_tenant_id)

    def validate_workspace_id(self, path_workspace_id: str) -> bool:
        """Check optional token binding for host workspace selection.

        An unbound token does not grant ownership of the selected workspace.
        Persistence uses its separately captured authenticated principal.
        """
        if not self.workspace_id:
            return True
        return str(self.workspace_id) == str(path_workspace_id)

    @classmethod
    def from_claims(
        cls,
        claims: UserClaims,
        *,
        auth_provenance: str = ANONYMOUS_PROVENANCE,
    ) -> "UserPrincipal":
        """Create UserPrincipal from adapter UserClaims.

        ``auth_provenance`` must be "token_validated" only at call sites that
        actually validated a bearer token via the configured auth adapter.
        """
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
            workspace_id=claims.workspace_id,
            auth_provenance=auth_provenance,
        )


def _csv_request_value(request: Request, *, header: str, cookie: str, query: str) -> list[str] | None:
    raw = request.headers.get(header) or request.cookies.get(cookie) or request.query_params.get(query)
    if raw is None:
        return None
    return [item.strip() for item in raw.split(",") if item.strip()]


def _no_auth_dev_override_principal(request: Request, principal: UserPrincipal) -> UserPrincipal:
    """Apply request-scoped local-dev persona overrides when auth is disabled.

    This intentionally runs only for an anonymous principal that was granted
    development access for this request (see ``_anonymous_principal``). It
    lets local browser profiles test user-to-user flows such as DM
    notifications without reconfiguring the process-wide no-auth adapter.
    """
    requested_user_id = (
        request.headers.get("X-Mozaiks-Dev-User-Id")
        or request.cookies.get("mozaiks_dev_user_id")
        or request.query_params.get("dev_user_id")
    )
    if not requested_user_id:
        return principal

    user_id = validate_path_id(str(requested_user_id).strip(), "dev_user_id")
    roles = _csv_request_value(
        request,
        header="X-Mozaiks-Dev-Roles",
        cookie="mozaiks_dev_roles",
        query="dev_roles",
    )
    scopes = _csv_request_value(
        request,
        header="X-Mozaiks-Dev-Scopes",
        cookie="mozaiks_dev_scopes",
        query="dev_scopes",
    )
    return UserPrincipal(
        user_id=user_id,
        email=request.headers.get("X-Mozaiks-Dev-Email") or principal.email,
        name=request.headers.get("X-Mozaiks-Dev-Name") or principal.name or user_id,
        roles=roles if roles is not None else list(principal.roles),
        scopes=scopes if scopes is not None else list(principal.scopes),
        raw_claims={
            **dict(principal.raw_claims or {}),
            "dev_persona": True,
            "base_user_id": principal.user_id,
        },
        provider=principal.provider,
        app_id=principal.app_id,
        chat_id=principal.chat_id,
        tenant_id=principal.tenant_id,
        workspace_id=principal.workspace_id,
        # Dev personas are never authenticated provenance, no matter which
        # roles/scopes the request-scoped override assigns them.
        auth_provenance=DEV_OVERRIDE_PROVENANCE,
    )


def _extract_token(authorization: HTTPAuthorizationCredentials | None) -> str | None:
    """Extract the bearer token from the Authorization header.

    HTTP routes never read a token from the URL: query strings land in access
    logs, browser history, and Referer headers. WebSocket handshakes use the
    bearer subprotocol instead (see ``websocket_auth``).
    """
    if authorization and authorization.credentials:
        return authorization.credentials
    return None


async def _validate_and_attach(
    request: Request,
    token: str,
) -> UserPrincipal:
    """Validate token and attach user principal to request.state."""
    adapter = get_auth_adapter()

    try:
        claims = await adapter.validate_token(token)
    except AuthError as e:
        logger.warning(
            "AUTH_FAILED: provider=%s reason=%s status=%s",
            adapter.name,
            e.message,
            e.status_code,
            extra={"event": "AUTH_FAILED", "provider": adapter.name, "status": e.status_code},
        )
        raise HTTPException(status_code=e.status_code, detail=e.message) from e

    # The only place "token_validated" provenance is minted: a bearer token
    # was actually validated by the configured auth adapter just above.
    principal = UserPrincipal.from_claims(claims, auth_provenance="token_validated")

    # Attach to request state for downstream access
    request.state.user = principal
    request.state.user_id = principal.user_id
    request.state.app_id = principal.app_id
    request.state.tenant_id = principal.tenant_id
    request.state.workspace_id = principal.workspace_id

    return principal


#: Set on ``request.state`` when ``optional_user`` returns ``None`` because the
#: anonymous access policy refused the request, so a route that needs a
#: principal can say why (see hosts/routers/modules.py).
ANONYMOUS_ACCESS_REFUSAL_STATE = "anonymous_access_refusal"


async def _anonymous_principal(
    request: Request, config: ResolvedAuthConfig, grant: AnonymousGrant
) -> UserPrincipal:
    """Mint the principal of a request the anonymous access policy did not refuse.

    Development access gives the anonymous roles and scopes, then any dev
    persona the request names; anyone else is an anonymous visitor without
    development access.
    """
    claims = await anonymous_claims(grant, config, get_auth_adapter())
    if grant.development_access:
        principal = UserPrincipal.from_claims(claims, auth_provenance=LOCAL_DEVELOPMENT_PROVENANCE)
        principal = _no_auth_dev_override_principal(request, principal)
    else:
        principal = UserPrincipal.from_claims(claims, auth_provenance=ANONYMOUS_PROVENANCE)
    logger.debug(
        "Auth disabled - anonymous principal (provenance=%s)", principal.auth_provenance
    )
    request.state.user = principal
    request.state.user_id = principal.user_id
    request.state.app_id = principal.app_id
    request.state.tenant_id = principal.tenant_id
    request.state.workspace_id = principal.workspace_id
    return principal


async def require_user(
    request: Request,
    authorization: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> UserPrincipal:
    """
    Dependency that requires a valid user token.

    Returns UserPrincipal on success, raises HTTPException on failure.
    With authentication off, the anonymous access policy mints or refuses the
    principal (``AUTH_ANON_ACCESS``; see core/auth/anonymous_access.py).
    """
    if not is_auth_enabled():
        config = resolve_auth_config()
        grant = resolve_anonymous_grant(request.scope, config)
        if grant.refused:
            raise HTTPException(status_code=grant.status_code or 403, detail=grant.detail)
        return await _anonymous_principal(request, config, grant)

    token = _extract_token(authorization)
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

    With authentication off, a request the anonymous access policy refuses
    (implicit demo mode, or another machine under ``AUTH_ANON_ACCESS=local``)
    is a request without credentials: ``None``, never more. The route decides
    what an anonymous caller may do, exactly as for a request without a token
    on an authenticated host; the refusal is kept on ``request.state`` so the
    route can say why.
    """
    if not is_auth_enabled():
        config = resolve_auth_config()
        grant = resolve_anonymous_grant(request.scope, config)
        if grant.refused:
            setattr(request.state, ANONYMOUS_ACCESS_REFUSAL_STATE, grant)
            return None
        return await _anonymous_principal(request, config, grant)

    token = _extract_token(authorization)
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
            "app_id mismatch: token=%s, path=%s", principal.app_id, path_app_id)
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
            "chat_id mismatch: token=%s, path=%s", principal.chat_id, path_chat_id)
        raise HTTPException(
            status_code=403,
            detail="Token chat_id does not match request chat_id"
        )


# ---------------------------------------------------------------------------
# Shared user-id validation helper
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Path parameter format validation
# ---------------------------------------------------------------------------

# Allows UUIDs, slugs (alphanumeric + hyphens + underscores + dots), max 128 chars.
# Rejects path traversal, shell metacharacters, and excessively long values.
_SAFE_PATH_ID_RE = re.compile(r'^[\w\-\.]{1,128}$')


def validate_path_id(value: str, field_name: str = "id") -> str:
    """Validate a URL path parameter is safe to use in database queries.

    Accepts: alphanumeric characters, hyphens, underscores, dots; max 128 chars.
    Rejects: path traversal (`..`), shell metacharacters, empty strings, or
    values exceeding 128 characters.

    Raises HTTPException(400) on invalid input.
    Returns the value unchanged if valid.
    """
    if not value or not _SAFE_PATH_ID_RE.match(value) or ".." in value:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {field_name} format",
        )
    return value


def resolve_scope_from_principal(
    principal: "UserPrincipal",
    *,
    app_id: str | None = None,
    user_id: str | None = None,
    default_user_id: str | None = None,
    default_app_id: str = "default",
) -> tuple[str, str]:
    """Resolve and validate the (app_id, user_id) scope from an authenticated principal.

    Validates that any caller-supplied *app_id* or *user_id* match the
    authenticated principal's claims, then returns the canonical resolved pair.
    Raises :class:`fastapi.HTTPException` on a mismatch or missing required value.
    *default_user_id* is the user the shared development identity acts for
    when the caller names none; every other principal acts as itself.
    """
    effective_user_id = user_id
    if acts_for_any_user(principal) and not effective_user_id:
        effective_user_id = str(default_user_id or "").strip() or None

    resolved_user_id = validate_user_id_against_principal(principal, body_user_id=effective_user_id)

    provided_app_id = str(app_id or "").strip() or None
    principal_app_id = str(principal.app_id or "").strip() or None
    if principal_app_id and provided_app_id and provided_app_id != principal_app_id:
        raise HTTPException(status_code=403, detail="app_id in request body does not match authenticated app scope")

    resolved_app_id = principal_app_id or provided_app_id or default_app_id
    if not resolved_app_id:
        raise HTTPException(status_code=400, detail="app_id is required")
    return resolved_app_id, resolved_user_id


def is_shared_development_identity(principal: object) -> bool:
    """True for the shared anonymous principal granted development access.

    It stands for nobody in particular: a caller with development access (this
    machine under ``AUTH_ANON_ACCESS=local``, any client under ``open``) names
    the user it acts for. Anonymous visitors and token-validated principals
    act only as themselves.
    """
    return (
        isinstance(principal, UserPrincipal)
        and principal.has_local_development_access
        and principal.user_id == ANONYMOUS_USER_ID
    )


def _token_subject_is_anonymous(principal: object) -> bool:
    # Unchanged auth-on behaviour, kept apart so that the one auth-on change
    # (a token whose subject is literally "anonymous" no longer acts for other
    # users) is reviewed on its own.
    return (
        isinstance(principal, UserPrincipal)
        and principal.is_authenticated
        and principal.user_id == ANONYMOUS_USER_ID
    )


def acts_for_any_user(principal: object) -> bool:
    """True when the principal names the user it acts for and sees every owner's records.

    Only the shared development identity does (see
    :func:`is_shared_development_identity`); routes that scope records to their
    owner skip that scope for it and nobody else.
    """
    return is_shared_development_identity(principal) or _token_subject_is_anonymous(principal)


def validate_user_id_against_principal(
    principal: "UserPrincipal",
    path_user_id: str | None = None,
    body_user_id: str | None = None,
) -> str:
    """Validate that path/body user_id matches the principal.

    Every principal except the shared development identity (see
    :func:`is_shared_development_identity`), including an anonymous visitor:
        - If *path_user_id* is provided it MUST match ``principal.user_id``.
        - If *body_user_id* is provided it MUST match ``principal.user_id``.
        - Returns the canonical ``user_id`` from the principal.

    The shared development identity:
        - Falls back to *path_user_id* or *body_user_id*.
        - Raises HTTP 400 if neither is provided.
    """
    jwt_user_id = principal.user_id

    # Shared development identity — the caller names the user.
    if acts_for_any_user(principal):
        user_id = path_user_id or body_user_id
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id is required")
        return user_id

    # Everyone else acts as itself — enforce match.
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

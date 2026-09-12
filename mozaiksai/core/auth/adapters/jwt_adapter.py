"""
Generic JWT adapter for OIDC-compliant providers.

Supports configurable claim mappings to work with any JWT-based auth provider.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import jwt
from jwt import PyJWKClient

from logs.logging_config import get_core_logger
from mozaiksai.core.auth.adapters.base import AuthError, BaseAuthAdapter, UserClaims
from mozaiksai.core.auth.cache_ttl import (
    DEFAULT_DISCOVERY_CACHE_TTL_SECONDS,
    DEFAULT_JWKS_CACHE_TTL_SECONDS,
    DISCOVERY_CACHE_TTL_ENV,
    JWKS_CACHE_TTL_ENV,
    resolve_cache_ttl_setting,
)
from mozaiksai.core.auth.discovery import OIDCDiscoveryClient
from mozaiksai.core.auth.jwks import JWKSClient

logger = get_core_logger("auth.jwt_adapter")


def _claim_value(raw_claims: dict[str, Any], claim_name: str, *fallbacks: str) -> str | None:
    """Return the first non-empty claim value as a string."""
    for key in (claim_name, *fallbacks):
        if not key:
            continue
        value = raw_claims.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _discovery_url(*, authority: str, tenant_id: str, explicit_url: str) -> str | None:
    """Resolve the OIDC discovery URL from provider-neutral config."""
    if explicit_url.strip():
        return explicit_url.strip()
    base = authority.strip().rstrip("/")
    tenant = tenant_id.strip()
    if base and tenant:
        return f"{base}/{tenant}/v2.0/.well-known/openid-configuration"
    if base:
        return f"{base}/.well-known/openid-configuration"
    return None


@dataclass
class JWTAdapterConfig:
    """
    Configuration for the generic JWT adapter.

    All fields can be set via environment variables with AUTH_ prefix.
    """

    # JWKS and issuer (required for validation)
    jwks_url: str = ""
    issuer: str = ""
    audience: str = ""

    # OIDC discovery (used when jwks_url or issuer are not explicitly set)
    oidc_authority: str = ""
    oidc_tenant_id: str = ""
    oidc_discovery_url: str = ""

    # Claim mappings - which JWT claims map to our UserClaims
    user_id_claim: str = "sub"
    email_claim: str = "email"
    name_claim: str = "name"
    roles_claim: str = "roles"
    scopes_claim: str = "scp"  # Azure uses "scp", others use "scope"
    app_id_claim: str = "app_id"
    chat_id_claim: str = "chat_id"
    tenant_id_claim: str = "tid"
    workspace_id_claim: str = "workspace_id"

    # Scope format: "space" (space-separated string) or "array" (JSON array)
    scopes_format: str = "space"

    # Algorithms to accept
    algorithms: list[str] = None  # type: ignore[assignment]

    # Clock skew tolerance in seconds
    clock_skew_seconds: int = 120

    # Optional: required scope for user endpoints
    required_scope: str = ""

    # Cache TTLs for this adapter's own discovery/JWKS clients. Held here so
    # the lazily created clients derive from the same immutable snapshot that
    # identifies the adapter, never from live environment.
    jwks_cache_ttl_seconds: int = DEFAULT_JWKS_CACHE_TTL_SECONDS
    discovery_cache_ttl_seconds: int = DEFAULT_DISCOVERY_CACHE_TTL_SECONDS

    def __post_init__(self):
        if self.algorithms is None:
            self.algorithms = ["RS256"]

    @classmethod
    def from_env(cls, settings: Mapping[str, str] | None = None) -> "JWTAdapterConfig":
        """Load configuration from an immutable snapshot, else the environment.

        The auth registry passes the same snapshot it derived the adapter cache
        identity from, so the constructed adapter always matches that identity.
        """

        def _raw(name: str) -> str | None:
            """Return the configured value untouched (no default substitution)."""
            return settings.get(name) if settings is not None else os.getenv(name)

        def _get(name: str, default: str = "") -> str:
            # Absent and empty are equivalent here: a blank AUTH_* variable in
            # a .env must behave exactly like an unset one.
            value = settings.get(name) if settings is not None else os.getenv(name)
            return default if not value else value

        algorithms_str = _get("AUTH_ALGORITHMS", "RS256")
        algorithms = [a.strip() for a in algorithms_str.split(",") if a.strip()]

        raw_clock_skew = _get("AUTH_CLOCK_SKEW", "120").strip() or "120"
        try:
            clock_skew_seconds = int(raw_clock_skew)
        except ValueError as exc:
            raise ValueError(
                f"AUTH_CLOCK_SKEW must be an integer number of seconds, got {raw_clock_skew!r}"
            ) from exc

        # Raw values go straight to the canonical resolver: absent, empty, and
        # whitespace-only all normalize to the same default here as they do in
        # canonical auth resolution.
        jwks_cache_ttl_seconds = resolve_cache_ttl_setting(
            JWKS_CACHE_TTL_ENV, _raw(JWKS_CACHE_TTL_ENV)
        )
        discovery_cache_ttl_seconds = resolve_cache_ttl_setting(
            DISCOVERY_CACHE_TTL_ENV, _raw(DISCOVERY_CACHE_TTL_ENV)
        )

        return cls(
            jwks_url=_get("AUTH_JWKS_URL"),
            issuer=_get("AUTH_ISSUER"),
            audience=_get("AUTH_AUDIENCE"),
            oidc_authority=_get("MOZAIKS_OIDC_AUTHORITY"),
            oidc_tenant_id=_get("MOZAIKS_OIDC_TENANT_ID"),
            oidc_discovery_url=_get("MOZAIKS_OIDC_DISCOVERY_URL"),
            user_id_claim=_get("AUTH_USER_ID_CLAIM", "sub"),
            email_claim=_get("AUTH_EMAIL_CLAIM", "email"),
            name_claim=_get("AUTH_NAME_CLAIM", "name"),
            roles_claim=_get("AUTH_ROLES_CLAIM", "roles"),
            scopes_claim=_get("AUTH_SCOPES_CLAIM", "scp"),
            app_id_claim=_get("AUTH_APP_ID_CLAIM", "app_id"),
            chat_id_claim=_get("AUTH_CHAT_ID_CLAIM", "chat_id"),
            tenant_id_claim=_get("AUTH_TENANT_ID_CLAIM", "tid"),
            workspace_id_claim=_get("AUTH_WORKSPACE_ID_CLAIM", "workspace_id"),
            scopes_format=_get("AUTH_SCOPES_FORMAT", "space"),
            algorithms=algorithms,
            clock_skew_seconds=clock_skew_seconds,
            required_scope=_get("AUTH_REQUIRED_SCOPE"),
            jwks_cache_ttl_seconds=jwks_cache_ttl_seconds,
            discovery_cache_ttl_seconds=discovery_cache_ttl_seconds,
        )


class GenericJWTAdapter(BaseAuthAdapter):
    """
    Generic JWT adapter that works with any OIDC-compliant provider.

    Features:
    - Configurable claim mappings
    - JWKS-based signature validation
    - OIDC discovery for issuer and JWKS URL when explicit overrides are absent
    - Flexible scope extraction (space-separated or array)
    - Clock skew tolerance

    Configuration via environment variables:
        AUTH_JWKS_URL: Optional explicit URL to fetch JWKS
        AUTH_ISSUER: Optional explicit expected issuer claim
        MOZAIKS_OIDC_AUTHORITY: OIDC authority used for discovery when overrides are absent
        MOZAIKS_OIDC_TENANT_ID: Optional tenant appended to the authority discovery URL
        MOZAIKS_OIDC_DISCOVERY_URL: Optional explicit discovery document URL
        AUTH_AUDIENCE: Expected audience claim
        AUTH_USER_ID_CLAIM: Claim for user ID (default: sub)
        AUTH_EMAIL_CLAIM: Claim for email (default: email)
        AUTH_NAME_CLAIM: Claim for name (default: name)
        AUTH_ROLES_CLAIM: Claim for roles (default: roles)
        AUTH_SCOPES_CLAIM: Claim for scopes (default: scp)
        AUTH_SCOPES_FORMAT: "space" or "array" (default: space)
        AUTH_ALGORITHMS: Comma-separated algorithms (default: RS256)
        AUTH_CLOCK_SKEW: Clock skew tolerance in seconds (default: 120)
        AUTH_REQUIRED_SCOPE: Required scope for user endpoints
    """

    name = "jwt"

    def __init__(
        self,
        config: JWTAdapterConfig | None = None,
        settings: Mapping[str, str] | None = None,
    ):
        super().__init__(settings)
        self._config = config or JWTAdapterConfig.from_env(settings)
        self._pyjwk_client: PyJWKClient | None = None
        self._discovery_client: OIDCDiscoveryClient | None = None
        self._jwks_client: JWKSClient | None = None

    def _configured_discovery_url(self) -> str | None:
        return _discovery_url(
            authority=self._config.oidc_authority,
            tenant_id=self._config.oidc_tenant_id,
            explicit_url=self._config.oidc_discovery_url,
        )

    def _get_discovery_client(self) -> OIDCDiscoveryClient:
        if self._discovery_client is None:
            # Snapshot-bound: the URL and TTL come from this adapter's own
            # immutable config, and consult_environment=False forbids the
            # client from reading live environment for anything.
            self._discovery_client = OIDCDiscoveryClient(
                discovery_url=self._configured_discovery_url(),
                cache_ttl=self._config.discovery_cache_ttl_seconds,
                consult_environment=False,
            )
        return self._discovery_client

    def _get_jwks_client(self) -> PyJWKClient:
        """Get or create the explicit-URL PyJWT client for compatibility."""
        if self._pyjwk_client is None:
            if not self._config.jwks_url:
                raise AuthError(
                    "JWKS URL not configured. Set AUTH_JWKS_URL or configure OIDC discovery.",
                    500,
                    self.name,
                )
            self._pyjwk_client = PyJWKClient(self._config.jwks_url)
        return self._pyjwk_client

    async def _get_jwks_client_async(self) -> JWKSClient:
        """Get the async cached JWKS client, resolving discovery when needed."""
        if self._jwks_client is None:
            if self._config.jwks_url:
                jwks_url = self._config.jwks_url
            else:
                try:
                    jwks_url = await self._get_discovery_client().get_jwks_uri()
                except RuntimeError as exc:
                    raise AuthError(
                        "JWKS URL not configured. Set AUTH_JWKS_URL or configure OIDC discovery.",
                        500,
                        self.name,
                    ) from exc
            self._jwks_client = JWKSClient(
                jwks_url=jwks_url,
                cache_ttl=self._config.jwks_cache_ttl_seconds,
                use_discovery=False,
                consult_environment=False,
                discovery_client=self._get_discovery_client(),
            )
        return self._jwks_client

    async def _get_expected_issuer(self) -> str:
        """Resolve the expected issuer from explicit config or discovery."""
        issuer = str(self._config.issuer or "").strip()
        if issuer:
            return issuer
        try:
            return await self._get_discovery_client().get_issuer()
        except RuntimeError as exc:
            raise AuthError(
                "Issuer not configured. Set AUTH_ISSUER or configure OIDC discovery.",
                500,
                self.name,
            ) from exc

    async def validate_token(self, token: str) -> UserClaims:
        """
        Validate a JWT token and return user claims.

        Args:
            token: The raw JWT string (without "Bearer " prefix)

        Returns:
            UserClaims with standardized user information

        Raises:
            AuthError on validation failure
        """
        if not token or not token.strip():
            raise AuthError("Missing access token", 401, self.name)

        token = token.strip()

        # Decode header to get key id, then resolve signing key from explicit
        # JWKS URL or OIDC discovery.
        try:
            unverified_header = jwt.get_unverified_header(token)
        except jwt.DecodeError as e:
            logger.warning("Invalid token header: %s", e)
            raise AuthError("Invalid token format", 401, self.name) from e

        kid = unverified_header.get("kid")
        if not kid:
            raise AuthError("Token missing key ID (kid)", 401, self.name)

        try:
            jwks_client = await self._get_jwks_client_async()
            jwk = await jwks_client.get_signing_key(str(kid))
            if not jwk:
                raise AuthError("Invalid signing key", 401, self.name)
            signing_key = jwt.PyJWK.from_dict(jwk).key
            expected_issuer = await self._get_expected_issuer()
        except AuthError:
            raise
        except Exception as e:
            logger.error("Unexpected JWKS error: %s", e, exc_info=True)
            raise AuthError("Token validation failed", 401, self.name) from e

        # Decode and verify
        try:
            decode_options = {
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iat": True,
                "require": ["exp", "iss"],
            }

            # Only verify audience if configured
            if self._config.audience:
                decode_options["verify_aud"] = True
            else:
                decode_options["verify_aud"] = False

            claims = jwt.decode(
                token,
                signing_key,
                algorithms=self._config.algorithms,
                audience=self._config.audience if self._config.audience else None,
                issuer=expected_issuer,
                leeway=self._config.clock_skew_seconds,
                options=decode_options,  # type: ignore[arg-type]
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("Token has expired", 401, self.name) from exc
        except jwt.ImmatureSignatureError as exc:
            raise AuthError("Token not yet valid", 401, self.name) from exc
        except jwt.InvalidAudienceError as exc:
            raise AuthError("Invalid token audience", 401, self.name) from exc
        except jwt.InvalidIssuerError as exc:
            raise AuthError("Invalid token issuer", 401, self.name) from exc
        except jwt.InvalidSignatureError as exc:
            raise AuthError("Invalid token signature", 401, self.name) from exc
        except jwt.DecodeError as e:
            logger.warning("Token decode error: %s", e)
            raise AuthError("Invalid token format", 401, self.name) from e
        except Exception as e:
            logger.error("Token validation error: %s", e, exc_info=True)
            raise AuthError("Token validation failed", 401, self.name) from e

        # Extract claims using configured mappings
        user_claims = self._extract_claims(claims)

        # Enforce required scope if configured
        if self._config.required_scope:
            if self._config.required_scope not in user_claims.scopes:
                raise AuthError(
                    f"Missing required scope: {self._config.required_scope}",
                    403,
                    self.name,
                )

        return user_claims

    def _extract_claims(self, raw_claims: dict[str, Any]) -> UserClaims:
        """Extract standardized claims from raw JWT claims."""
        # User ID (required)
        user_id = raw_claims.get(self._config.user_id_claim)
        if not user_id:
            raise AuthError(
                f"Token missing required claim: {self._config.user_id_claim}",
                401,
                self.name,
            )

        # Email (optional)
        email = raw_claims.get(self._config.email_claim)

        # Name (optional)
        name = raw_claims.get(self._config.name_claim)

        # Roles (optional, can be list or string)
        roles_raw = raw_claims.get(self._config.roles_claim, [])
        if isinstance(roles_raw, str):
            roles = [roles_raw]
        elif isinstance(roles_raw, list):
            roles = roles_raw
        else:
            roles = []

        # Scopes (format depends on provider)
        scopes = self._extract_scopes(raw_claims)

        return UserClaims(
            user_id=str(user_id),
            email=str(email) if email else None,
            name=str(name) if name else None,
            roles=roles,
            scopes=scopes,
            raw_claims=raw_claims,
            provider=self.name,
            app_id=_claim_value(raw_claims, self._config.app_id_claim, "mozaiks_app_id"),
            chat_id=_claim_value(raw_claims, self._config.chat_id_claim, "mozaiks_chat_id"),
            tenant_id=_claim_value(raw_claims, self._config.tenant_id_claim, "tenant_id", "mozaiks_tenant_id"),
            workspace_id=_claim_value(raw_claims, self._config.workspace_id_claim, "mozaiks_workspace_id"),
        )

    def _extract_scopes(self, claims: dict[str, Any]) -> list[str]:
        """
        Extract scopes from claims based on configured format.

        Handles both space-separated strings (Azure) and arrays (Auth0).
        """
        scopes_raw = claims.get(self._config.scopes_claim, "")

        if self._config.scopes_format == "array":
            # Scopes are a JSON array
            if isinstance(scopes_raw, list):
                return scopes_raw
            elif isinstance(scopes_raw, str):
                # Try to parse as space-separated fallback
                return [s.strip() for s in scopes_raw.split() if s.strip()]
            return []
        else:
            # Default: space-separated string (Azure style)
            if isinstance(scopes_raw, str):
                return [s.strip() for s in scopes_raw.split() if s.strip()]
            elif isinstance(scopes_raw, list):
                return scopes_raw
            return []

    def is_enabled(self) -> bool:
        """Check if the adapter has required configuration."""
        explicit_configured = bool(self._config.jwks_url and self._config.issuer)
        return explicit_configured or bool(self._configured_discovery_url())

"""
Generic JWT adapter for OIDC-compliant providers.

Supports configurable claim mappings to work with any JWT-based auth provider.
"""

import os
from dataclasses import dataclass
from typing import Any

import jwt
from jwt import PyJWKClient

from logs.logging_config import get_core_logger
from mozaiksai.core.auth.adapters.base import AuthError, BaseAuthAdapter, UserClaims

logger = get_core_logger("auth.jwt_adapter")


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

    # Claim mappings - which JWT claims map to our UserClaims
    user_id_claim: str = "sub"
    email_claim: str = "email"
    name_claim: str = "name"
    roles_claim: str = "roles"
    scopes_claim: str = "scp"  # Azure uses "scp", others use "scope"

    # Scope format: "space" (space-separated string) or "array" (JSON array)
    scopes_format: str = "space"

    # Algorithms to accept
    algorithms: list[str] = None  # type: ignore[assignment]

    # Clock skew tolerance in seconds
    clock_skew_seconds: int = 120

    # Optional: required scope for user endpoints
    required_scope: str = ""

    def __post_init__(self):
        if self.algorithms is None:
            self.algorithms = ["RS256"]

    @classmethod
    def from_env(cls) -> "JWTAdapterConfig":
        """Load configuration from environment variables."""
        algorithms_str = os.getenv("AUTH_ALGORITHMS", "RS256")
        algorithms = [a.strip() for a in algorithms_str.split(",") if a.strip()]

        return cls(
            jwks_url=os.getenv("AUTH_JWKS_URL", ""),
            issuer=os.getenv("AUTH_ISSUER", ""),
            audience=os.getenv("AUTH_AUDIENCE", ""),
            user_id_claim=os.getenv("AUTH_USER_ID_CLAIM", "sub"),
            email_claim=os.getenv("AUTH_EMAIL_CLAIM", "email"),
            name_claim=os.getenv("AUTH_NAME_CLAIM", "name"),
            roles_claim=os.getenv("AUTH_ROLES_CLAIM", "roles"),
            scopes_claim=os.getenv("AUTH_SCOPES_CLAIM", "scp"),
            scopes_format=os.getenv("AUTH_SCOPES_FORMAT", "space"),
            algorithms=algorithms,
            clock_skew_seconds=int(os.getenv("AUTH_CLOCK_SKEW", "120")),
            required_scope=os.getenv("AUTH_REQUIRED_SCOPE", ""),
        )


class GenericJWTAdapter(BaseAuthAdapter):
    """
    Generic JWT adapter that works with any OIDC-compliant provider.

    Features:
    - Configurable claim mappings
    - JWKS-based signature validation
    - Flexible scope extraction (space-separated or array)
    - Clock skew tolerance

    Configuration via environment variables:
        AUTH_JWKS_URL: URL to fetch JWKS (required)
        AUTH_ISSUER: Expected issuer claim (required)
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

    def __init__(self, config: JWTAdapterConfig | None = None):
        super().__init__()
        self._config = config or JWTAdapterConfig.from_env()
        self._jwks_client: PyJWKClient | None = None

    def _get_jwks_client(self) -> PyJWKClient:
        """Get or create JWKS client."""
        if self._jwks_client is None:
            if not self._config.jwks_url:
                raise AuthError(
                    "JWKS URL not configured. Set AUTH_JWKS_URL.",
                    500,
                    self.name,
                )
            self._jwks_client = PyJWKClient(self._config.jwks_url)
        return self._jwks_client

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

        # Get signing key
        try:
            jwks_client = self._get_jwks_client()
            signing_key = jwks_client.get_signing_key_from_jwt(token)
        except jwt.PyJWKClientError as e:
            logger.warning(f"JWKS error: {e}")
            raise AuthError("Failed to verify token signature", 401, self.name) from e
        except Exception as e:
            logger.error(f"Unexpected JWKS error: {e}")
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
                signing_key.key,
                algorithms=self._config.algorithms,
                audience=self._config.audience if self._config.audience else None,
                issuer=self._config.issuer if self._config.issuer else None,
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
            logger.warning(f"Token decode error: {e}")
            raise AuthError("Invalid token format", 401, self.name) from e
        except Exception as e:
            logger.error(f"Token validation error: {e}")
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
            tenant_id=raw_claims.get("tid"),
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
        return bool(self._config.jwks_url and self._config.issuer)

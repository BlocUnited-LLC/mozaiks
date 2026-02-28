"""
JWT validation logic.

Validates access tokens against JWKS, checking:
- Signature (RS256 via JWKS)
- Issuer (iss) - from OIDC discovery or explicit override
- Audience (aud)
- Expiration (exp) and Not Before (nbf)
- Required scope (scp claim)

Supports OIDC discovery-driven validation or explicit configuration.
"""

from typing import Optional, List, Any, Dict
from dataclasses import dataclass
import time

import jwt
from jwt import PyJWKClient, InvalidTokenError

from mozaiksai.core.auth.config import get_auth_config, AuthConfig
from mozaiksai.core.auth.jwks import get_jwks_client
from logs.logging_config import get_core_logger

logger = get_core_logger("auth.jwt_validator")


@dataclass
class TokenClaims:
    """Validated token claims."""

    user_id: str
    email: Optional[str]
    roles: List[str]
    scopes: List[str]
    raw_claims: Dict[str, Any]
    # MozaiksCore execution claims (optional, populated if present)
    mozaiks_token_use: Optional[str] = None
    mozaiks_app_id: Optional[str] = None
    mozaiks_chat_id: Optional[str] = None
    mozaiks_capability_id: Optional[str] = None

    @property
    def has_user_scope(self) -> bool:
        """Check if token has the required user scope."""
        config = get_auth_config()
        return config.required_scope in self.scopes

    @property
    def is_execution_token(self) -> bool:
        """Check if this is a MozaiksCore execution token."""
        return self.mozaiks_token_use == "execution"

    def validate_app_id(self, path_app_id: str) -> bool:
        """Validate that token app_id matches path/payload app_id."""
        if not self.mozaiks_app_id:
            # No app_id claim in token - allow (legacy token)
            return True
        return str(self.mozaiks_app_id) == str(path_app_id)

    def validate_chat_id(self, path_chat_id: str) -> bool:
        """Validate that token chat_id matches path/payload chat_id (if bound)."""
        if not self.mozaiks_chat_id:
            # No chat_id claim in token - allow (session not bound)
            return True
        return str(self.mozaiks_chat_id) == str(path_chat_id)


class AuthError(Exception):
    """Authentication error with HTTP status code."""

    def __init__(self, message: str, status_code: int = 401):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class JWTValidator:
    """
    JWT validator using JWKS for signature verification.

    Uses PyJWT library with manual claim validation for provider-agnostic support.
    Supports OIDC discovery-driven issuer validation or explicit override.
    """

    def __init__(self, config: Optional[AuthConfig] = None):
        self._config = config or get_auth_config()
        self._cached_issuer: Optional[str] = None

    async def _get_issuer(self) -> str:
        """
        Get the expected issuer, either from explicit config or OIDC discovery.

        Returns:
            The issuer string to validate against

        Raises:
            RuntimeError if unable to determine issuer
        """
        # Check for explicit override
        if self._config.issuer_override:
            return self._config.issuer_override

        # Use OIDC discovery
        if self._config.use_discovery:
            from mozaiksai.core.auth.discovery import get_discovery_client
            discovery_client = get_discovery_client()
            return await discovery_client.get_issuer()

        raise RuntimeError(
            "No issuer configured. Set AUTH_ISSUER or enable OIDC discovery."
        )

    async def validate_token(
        self,
        token: str,
        require_scope: bool = True,
    ) -> TokenClaims:
        """
        Validate a JWT access token.

        Args:
            token: The raw JWT string (without "Bearer " prefix)
            require_scope: Whether to enforce required scope

        Returns:
            TokenClaims with user info

        Raises:
            AuthError on validation failure
        """
        if not token or not token.strip():
            raise AuthError("Missing access token", 401)

        token = token.strip()

        # Decode header to get kid
        try:
            unverified_header = jwt.get_unverified_header(token)
        except jwt.DecodeError as e:
            logger.warning(f"Invalid token header: {e}")
            raise AuthError("Invalid token format", 401)

        kid = unverified_header.get("kid")
        if not kid:
            raise AuthError("Token missing key ID (kid)", 401)

        # Fetch signing key from JWKS (may trigger discovery + JWKS fetch)
        jwks_client = get_jwks_client()
        try:
            jwk = await jwks_client.get_signing_key(kid)
        except RuntimeError as e:
            logger.error(f"Failed to fetch signing key: {e}")
            raise AuthError("Unable to validate token signature", 401)

        if not jwk:
            logger.warning(f"Signing key not found: {kid}")
            raise AuthError("Invalid signing key", 401)

        # Build public key from JWK
        try:
            from jwt import algorithms
            public_key = algorithms.RSAAlgorithm.from_jwk(jwk)
        except Exception as e:
            logger.error(f"Failed to load public key from JWK: {e}")
            raise AuthError("Invalid signing key format", 401)

        # Get expected issuer (may trigger discovery fetch)
        try:
            expected_issuer = await self._get_issuer()
        except RuntimeError as e:
            logger.error(f"Failed to get issuer: {e}")
            raise AuthError("Unable to validate token issuer", 401)

        # Decode and verify token
        try:
            claims = jwt.decode(
                token,
                public_key,
                algorithms=self._config.algorithms,
                audience=self._config.audience,
                issuer=expected_issuer,
                leeway=self._config.clock_skew_seconds,
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                    "require": ["exp", "iss", "aud"],
                },
            )
        except jwt.ExpiredSignatureError:
            raise AuthError("Token has expired", 401)
        except jwt.ImmatureSignatureError:
            raise AuthError("Token not yet valid", 401)
        except jwt.InvalidAudienceError:
            raise AuthError("Invalid token audience", 401)
        except jwt.InvalidIssuerError:
            raise AuthError("Invalid token issuer", 401)
        except jwt.InvalidSignatureError:
            raise AuthError("Invalid token signature", 401)
        except jwt.DecodeError as e:
            logger.warning(f"Token decode error: {e}")
            raise AuthError("Invalid token", 401)
        except Exception as e:
            logger.error(f"Unexpected token validation error: {e}")
            raise AuthError("Token validation failed", 401)

        # Extract user claims
        user_id = claims.get(self._config.user_id_claim)
        if not user_id:
            raise AuthError(f"Token missing {self._config.user_id_claim} claim", 401)

        email = claims.get(self._config.email_claim)
        
        # Roles can be a list or missing
        roles_raw = claims.get(self._config.roles_claim, [])
        roles = roles_raw if isinstance(roles_raw, list) else []

        # Scopes: Azure uses "scp" as space-separated string
        scopes_raw = claims.get("scp", "")
        if isinstance(scopes_raw, str):
            scopes = [s.strip() for s in scopes_raw.split() if s.strip()]
        elif isinstance(scopes_raw, list):
            scopes = scopes_raw
        else:
            scopes = []

        # Extract MozaiksCore execution claims (optional)
        mozaiks_token_use = claims.get("mozaiks_token_use")
        mozaiks_app_id = claims.get("mozaiks_app_id") or claims.get("app_id")
        mozaiks_chat_id = claims.get("mozaiks_chat_id") or claims.get("chat_id")
        mozaiks_capability_id = claims.get("mozaiks_capability_id") or claims.get("capability_id")

        token_claims = TokenClaims(
            user_id=str(user_id),
            email=str(email) if email else None,
            roles=roles,
            scopes=scopes,
            raw_claims=claims,
            mozaiks_token_use=mozaiks_token_use,
            mozaiks_app_id=mozaiks_app_id,
            mozaiks_chat_id=mozaiks_chat_id,
            mozaiks_capability_id=mozaiks_capability_id,
        )

        # Enforce scope if required
        if require_scope and self._config.required_scope:
            if not token_claims.has_user_scope:
                logger.warning(
                    f"Token missing required scope: {self._config.required_scope}. "
                    f"Token scopes: {scopes}"
                )
                raise AuthError(
                    f"Missing required scope: {self._config.required_scope}",
                    403,
                )

        logger.debug(f"Token validated for user: {user_id}")
        return token_claims


# Module-level singleton
_jwt_validator: Optional[JWTValidator] = None


def get_jwt_validator() -> JWTValidator:
    """Get or create the singleton JWT validator."""
    global _jwt_validator
    if _jwt_validator is None:
        _jwt_validator = JWTValidator()
    return _jwt_validator


def reset_jwt_validator() -> None:
    """Reset the singleton (for testing)."""
    global _jwt_validator
    _jwt_validator = None

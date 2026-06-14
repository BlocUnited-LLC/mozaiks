"""
Supabase auth adapter.

Validates Supabase JWT tokens with Supabase-specific claim mappings.

Supabase JWTs have the following structure:
- sub: User UUID
- email: User email
- role: User role (authenticated, anon, service_role)
- aud: Audience (usually "authenticated")
- app_metadata: Custom app claims
- user_metadata: User profile data
"""

import os
from typing import Any

import jwt
from jwt import PyJWKClient

from logs.logging_config import get_core_logger
from mozaiksai.core.auth.adapters.base import AuthError, BaseAuthAdapter, UserClaims

logger = get_core_logger("auth.supabase")


class SupabaseAuthAdapter(BaseAuthAdapter):
    """
    Auth adapter for Supabase authentication.

    Handles Supabase-specific JWT structure and claim mappings.

    Configuration via environment variables:
        SUPABASE_URL: Your Supabase project URL (required)
        SUPABASE_JWT_SECRET: JWT secret for HS256 validation (optional, for local dev)
        SUPABASE_ANON_KEY: Anon key (not used for validation, just reference)

    The adapter automatically constructs:
        - JWKS URL: {SUPABASE_URL}/auth/v1/.well-known/jwks.json
        - Issuer: {SUPABASE_URL}/auth/v1

    Example:
        SUPABASE_URL=https://xyzcompany.supabase.co
    """

    name = "supabase"

    def __init__(
        self,
        supabase_url: str | None = None,
        jwt_secret: str | None = None,
    ):
        super().__init__()
        self._supabase_url = supabase_url or os.getenv("SUPABASE_URL", "")
        self._jwt_secret = jwt_secret or os.getenv("SUPABASE_JWT_SECRET", "")
        self._jwks_client: PyJWKClient | None = None

        # Clean URL (remove trailing slash)
        if self._supabase_url:
            self._supabase_url = self._supabase_url.rstrip("/")

    @property
    def _jwks_url(self) -> str:
        """Construct JWKS URL from Supabase project URL."""
        return f"{self._supabase_url}/auth/v1/.well-known/jwks.json"

    @property
    def _issuer(self) -> str:
        """Expected issuer claim."""
        return f"{self._supabase_url}/auth/v1"

    def _get_jwks_client(self) -> PyJWKClient:
        """Get or create JWKS client."""
        if self._jwks_client is None:
            if not self._supabase_url:
                raise AuthError(
                    "SUPABASE_URL not configured",
                    500,
                    self.name,
                )
            self._jwks_client = PyJWKClient(self._jwks_url)
        return self._jwks_client

    async def validate_token(self, token: str) -> UserClaims:
        """
        Validate a Supabase JWT token.

        Args:
            token: The raw JWT string (without "Bearer " prefix)

        Returns:
            UserClaims with Supabase user information

        Raises:
            AuthError on validation failure
        """
        if not token or not token.strip():
            raise AuthError("Missing access token", 401, self.name)

        token = token.strip()

        # Determine validation method
        if self._jwt_secret:
            # Use HS256 with secret (local development)
            claims = await self._validate_with_secret(token)
        else:
            # Use RS256 with JWKS (production)
            claims = await self._validate_with_jwks(token)

        return self._extract_claims(claims)

    async def _validate_with_secret(self, token: str) -> dict[str, Any]:
        """Validate using JWT secret (HS256)."""
        try:
            claims = jwt.decode(
                token,
                self._jwt_secret,  # type: ignore[arg-type]
                algorithms=["HS256"],
                audience="authenticated",
                options={
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_aud": True,
                },
            )
            return claims
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("Token has expired", 401, self.name) from exc
        except jwt.InvalidAudienceError as exc:
            raise AuthError("Invalid token audience", 401, self.name) from exc
        except jwt.InvalidSignatureError as exc:
            raise AuthError("Invalid token signature", 401, self.name) from exc
        except jwt.DecodeError as e:
            logger.warning(f"Token decode error: {e}")
            raise AuthError("Invalid token format", 401, self.name) from e
        except Exception as e:
            logger.error(f"Token validation error: {e}")
            raise AuthError("Token validation failed", 401, self.name) from e

    async def _validate_with_jwks(self, token: str) -> dict[str, Any]:
        """Validate using JWKS (RS256)."""
        try:
            jwks_client = self._get_jwks_client()
            signing_key = jwks_client.get_signing_key_from_jwt(token)

            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience="authenticated",
                issuer=self._issuer,
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
            return claims
        except jwt.PyJWKClientError as e:
            logger.warning(f"JWKS error: {e}")
            raise AuthError("Failed to verify token signature", 401, self.name) from e
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("Token has expired", 401, self.name) from exc
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

    def _extract_claims(self, raw_claims: dict[str, Any]) -> UserClaims:
        """Extract standardized claims from Supabase JWT."""
        # User ID from sub claim
        user_id = raw_claims.get("sub")
        if not user_id:
            raise AuthError("Token missing user ID (sub)", 401, self.name)

        # Email from email claim
        email = raw_claims.get("email")

        # Name from user_metadata
        user_metadata = raw_claims.get("user_metadata", {})
        name = user_metadata.get("full_name") or user_metadata.get("name")

        # Role from role claim (Supabase uses singular "role")
        role = raw_claims.get("role", "authenticated")
        roles = [role] if role else []

        # App metadata may contain custom roles
        app_metadata = raw_claims.get("app_metadata", {})
        custom_roles = app_metadata.get("roles", [])
        if isinstance(custom_roles, list):
            roles.extend(custom_roles)

        # Supabase doesn't use scopes the same way, but we map role to scope
        scopes = ["access_as_user"] if role == "authenticated" else []

        return UserClaims(
            user_id=str(user_id),
            email=str(email) if email else None,
            name=str(name) if name else None,
            roles=roles,
            scopes=scopes,
            raw_claims=raw_claims,
            provider=self.name,
            app_id=app_metadata.get("app_id"),
        )

    def is_enabled(self) -> bool:
        """Check if Supabase is configured."""
        return bool(self._supabase_url)

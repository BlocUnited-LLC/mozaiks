"""
Keycloak auth adapter.

Validates Keycloak JWT tokens with Keycloak-specific claim mappings.

Keycloak JWTs have the following structure:
- sub: User UUID
- email: User email
- preferred_username: Username
- realm_access.roles: Realm-level roles
- resource_access.{client}.roles: Client-level roles
- azp: Authorized party (client_id)
"""

import os
from typing import Any

import jwt
from jwt import PyJWKClient

from logs.logging_config import get_core_logger
from mozaiksai.core.auth.adapters.base import AuthError, BaseAuthAdapter, UserClaims

logger = get_core_logger("auth.keycloak")


class KeycloakAuthAdapter(BaseAuthAdapter):
    """
    Auth adapter for Keycloak authentication.

    Handles Keycloak-specific JWT structure and role extraction from
    realm_access and resource_access claims.

    Configuration via environment variables:
        KEYCLOAK_URL: Keycloak server URL (e.g., https://keycloak.example.com)
        KEYCLOAK_REALM: Realm name
        KEYCLOAK_CLIENT_ID: Client ID for audience validation (optional)

    The adapter automatically constructs:
        - JWKS URL: {KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs
        - Issuer: {KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}

    Example:
        KEYCLOAK_URL=https://keycloak.example.com
        KEYCLOAK_REALM=myrealm
        KEYCLOAK_CLIENT_ID=my-app
    """

    name = "keycloak"

    def __init__(
        self,
        keycloak_url: str | None = None,
        realm: str | None = None,
        client_id: str | None = None,
    ):
        super().__init__()
        self._keycloak_url = keycloak_url or os.getenv("KEYCLOAK_URL", "")
        self._realm = realm or os.getenv("KEYCLOAK_REALM", "")
        self._client_id = client_id or os.getenv("KEYCLOAK_CLIENT_ID", "")
        self._jwks_client: PyJWKClient | None = None

        # Clean URL (remove trailing slash)
        if self._keycloak_url:
            self._keycloak_url = self._keycloak_url.rstrip("/")

    @property
    def _jwks_url(self) -> str:
        """Construct JWKS URL from Keycloak config."""
        return f"{self._keycloak_url}/realms/{self._realm}/protocol/openid-connect/certs"

    @property
    def _issuer(self) -> str:
        """Expected issuer claim."""
        return f"{self._keycloak_url}/realms/{self._realm}"

    def _get_jwks_client(self) -> PyJWKClient:
        """Get or create JWKS client."""
        if self._jwks_client is None:
            if not self._keycloak_url or not self._realm:
                raise AuthError(
                    "KEYCLOAK_URL and KEYCLOAK_REALM must be configured",
                    500,
                    self.name,
                )
            self._jwks_client = PyJWKClient(self._jwks_url)
        return self._jwks_client

    async def validate_token(self, token: str) -> UserClaims:
        """
        Validate a Keycloak JWT token.

        Args:
            token: The raw JWT string (without "Bearer " prefix)

        Returns:
            UserClaims with Keycloak user information

        Raises:
            AuthError on validation failure
        """
        if not token or not token.strip():
            raise AuthError("Missing access token", 401, self.name)

        token = token.strip()

        try:
            jwks_client = self._get_jwks_client()
            signing_key = jwks_client.get_signing_key_from_jwt(token)

            decode_options = {
                "verify_signature": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_iss": True,
                "require": ["exp", "iss", "sub"],
            }

            # Only verify audience if client_id is configured
            if self._client_id:
                decode_options["verify_aud"] = True
                audience = self._client_id
            else:
                decode_options["verify_aud"] = False
                audience = None

            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=audience,
                issuer=self._issuer,
                options=decode_options,
            )
        except jwt.PyJWKClientError as e:
            logger.warning(f"JWKS error: {e}")
            raise AuthError("Failed to verify token signature", 401, self.name)
        except jwt.ExpiredSignatureError:
            raise AuthError("Token has expired", 401, self.name)
        except jwt.InvalidAudienceError:
            raise AuthError("Invalid token audience", 401, self.name)
        except jwt.InvalidIssuerError:
            raise AuthError("Invalid token issuer", 401, self.name)
        except jwt.InvalidSignatureError:
            raise AuthError("Invalid token signature", 401, self.name)
        except jwt.DecodeError as e:
            logger.warning(f"Token decode error: {e}")
            raise AuthError("Invalid token format", 401, self.name)
        except Exception as e:
            logger.error(f"Token validation error: {e}")
            raise AuthError("Token validation failed", 401, self.name)

        return self._extract_claims(claims)

    def _extract_claims(self, raw_claims: dict[str, Any]) -> UserClaims:
        """Extract standardized claims from Keycloak JWT."""
        # User ID from sub claim
        user_id = raw_claims.get("sub")
        if not user_id:
            raise AuthError("Token missing user ID (sub)", 401, self.name)

        # Email from email claim
        email = raw_claims.get("email")

        # Name from name or preferred_username
        name = raw_claims.get("name") or raw_claims.get("preferred_username")

        # Extract roles from realm_access and resource_access
        roles = self._extract_roles(raw_claims)

        # Keycloak uses scope claim (space-separated)
        scope_str = raw_claims.get("scope", "")
        scopes = [s.strip() for s in scope_str.split() if s.strip()]

        return UserClaims(
            user_id=str(user_id),
            email=str(email) if email else None,
            name=str(name) if name else None,
            roles=roles,
            scopes=scopes,
            raw_claims=raw_claims,
            provider=self.name,
            tenant_id=raw_claims.get("azp"),  # Authorized party
        )

    def _extract_roles(self, claims: dict[str, Any]) -> list[str]:
        """
        Extract roles from Keycloak's nested structure.

        Keycloak stores roles in:
        - realm_access.roles: Realm-level roles
        - resource_access.{client_id}.roles: Client-level roles
        """
        roles = []

        # Realm-level roles
        realm_access = claims.get("realm_access", {})
        realm_roles = realm_access.get("roles", [])
        if isinstance(realm_roles, list):
            roles.extend(realm_roles)

        # Client-level roles (if client_id is configured)
        if self._client_id:
            resource_access = claims.get("resource_access", {})
            client_access = resource_access.get(self._client_id, {})
            client_roles = client_access.get("roles", [])
            if isinstance(client_roles, list):
                roles.extend(client_roles)

        return roles

    def is_enabled(self) -> bool:
        """Check if Keycloak is configured."""
        return bool(self._keycloak_url and self._realm)

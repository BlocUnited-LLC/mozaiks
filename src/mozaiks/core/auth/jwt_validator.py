"""JWT validation primitives built on OIDC discovery and JWKS."""

from __future__ import annotations

import base64
from typing import Any, Protocol

from jose import JWTError, jwk, jwt
from jose.exceptions import ExpiredSignatureError, JWKError, JWTClaimsError

from mozaiks.core.auth.config import JWTValidatorConfig
from mozaiks.core.auth.discovery import OIDCDiscoveryClient
from mozaiks.core.auth.jwks import JWKSClient


class JWTValidationError(Exception):
    """Raised when token validation fails."""


class TokenExpiredError(JWTValidationError):
    """Raised when token expiration validation fails."""


class SigningKeyError(JWTValidationError):
    """Raised when no valid signing key can be resolved."""


class OIDCResolutionError(JWTValidationError):
    """Raised when issuer/JWKS values cannot be resolved."""


class _JWKSProvider(Protocol):
    async def get_key(self, kid: str, jwks_url: str | None = None) -> dict[str, Any] | None: ...

    async def get_jwks(
        self,
        jwks_url: str | None = None,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]: ...


class JWTValidator:
    """Validate JWTs against OIDC/JWKS configuration."""

    def __init__(
        self,
        config: JWTValidatorConfig,
        *,
        discovery_client: OIDCDiscoveryClient | None = None,
        jwks_client: _JWKSProvider | None = None,
    ) -> None:
        self._config = config
        self._discovery_client = discovery_client
        if self._discovery_client is None and config.discovery_url is not None:
            self._discovery_client = OIDCDiscoveryClient(
                config.discovery_url,
                cache_ttl_seconds=config.discovery_cache_ttl_seconds,
            )
        self._jwks_client = jwks_client or JWKSClient(
            config.jwks_url,
            cache_ttl_seconds=config.jwks_cache_ttl_seconds,
        )

    async def _resolve_issuer_and_jwks(self) -> tuple[str | None, str]:
        issuer = self._config.issuer
        jwks_url = self._config.jwks_url

        if issuer is not None and jwks_url is not None:
            return issuer, jwks_url

        if self._discovery_client is None:
            raise OIDCResolutionError(
                "Missing issuer/jwks_url and no OIDC discovery client is configured."
            )

        document = await self._discovery_client.get_document()
        return issuer or document.issuer, jwks_url or document.jwks_uri

    @staticmethod
    def _decode_octet_secret(key_dict: dict[str, Any]) -> bytes:
        encoded = key_dict.get("k")
        if not isinstance(encoded, str) or not encoded:
            raise SigningKeyError("Symmetric JWK is missing key material.")
        padding = "=" * ((4 - len(encoded) % 4) % 4)
        try:
            return base64.urlsafe_b64decode(encoded + padding)
        except ValueError as exc:
            raise SigningKeyError("Symmetric JWK key material is not valid base64url.") from exc

    @classmethod
    def _materialize_verification_key(cls, key_dict: dict[str, Any]) -> Any:
        if key_dict.get("kty") == "oct":
            return cls._decode_octet_secret(key_dict)

        try:
            constructed_key = jwk.construct(key_dict)
        except JWKError as exc:
            raise SigningKeyError(f"Unable to construct JWK: {exc}") from exc

        if hasattr(constructed_key, "to_pem"):
            pem = constructed_key.to_pem()
            if isinstance(pem, bytes):
                return pem.decode("utf-8")
            return pem
        return key_dict

    def _validate_required_claims(self, claims: dict[str, Any]) -> None:
        missing = [claim for claim in self._config.required_claims if claim not in claims]
        if missing:
            missing_fields = ", ".join(sorted(missing))
            raise JWTValidationError(f"Token missing required claims: {missing_fields}")

    async def validate(self, token: str) -> dict[str, Any]:
        """Validate a JWT and return verified claims."""

        token = token.strip()
        if not token:
            raise JWTValidationError("Token is empty.")

        try:
            header = jwt.get_unverified_header(token)
        except JWTError as exc:
            raise JWTValidationError("Token header is invalid.") from exc

        kid = header.get("kid")
        alg = header.get("alg")
        if not isinstance(alg, str):
            raise JWTValidationError("Token header is missing algorithm.")
        if alg not in self._config.algorithms:
            allowed = ", ".join(self._config.algorithms)
            raise JWTValidationError(f"Token algorithm '{alg}' is not allowed. Allowed: {allowed}.")

        issuer, jwks_url = await self._resolve_issuer_and_jwks()
        key_dict: dict[str, Any] | None = None

        if isinstance(kid, str) and kid:
            key_dict = await self._jwks_client.get_key(kid, jwks_url=jwks_url)

        if key_dict is None:
            jwks_payload = await self._jwks_client.get_jwks(jwks_url=jwks_url, force_refresh=True)
            for candidate in jwks_payload.get("keys", []):
                if not isinstance(candidate, dict):
                    continue
                if kid and candidate.get("kid") != kid:
                    continue
                if candidate.get("alg") and candidate.get("alg") != alg:
                    continue
                key_dict = candidate
                break

        if key_dict is None:
            raise SigningKeyError("No signing key matched the token header.")

        verification_key = self._materialize_verification_key(key_dict)
        options = {
            "verify_signature": True,
            "verify_exp": True,
            "verify_nbf": True,
            "verify_iat": True,
            "verify_aud": self._config.audience is not None,
            "verify_iss": issuer is not None,
            "leeway": self._config.clock_skew_seconds,
        }

        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                verification_key,
                algorithms=list(self._config.algorithms),
                audience=self._config.audience,
                issuer=issuer,
                options=options,
                access_token=None,
            )
        except ExpiredSignatureError as exc:
            raise TokenExpiredError("Token has expired.") from exc
        except JWTClaimsError as exc:
            raise JWTValidationError(f"Token claims are invalid: {exc}") from exc
        except JWKError as exc:
            raise SigningKeyError(f"Signing key is invalid: {exc}") from exc
        except JWTError as exc:
            raise JWTValidationError(f"Token validation failed: {exc}") from exc

        self._validate_required_claims(claims)
        return claims


__all__ = [
    "JWTValidationError",
    "JWTValidator",
    "OIDCResolutionError",
    "SigningKeyError",
    "TokenExpiredError",
]

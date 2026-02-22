"""JWT/OIDC configuration primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mozaiks.core.config import Settings


@dataclass(frozen=True)
class JWTValidatorConfig:
    """Configuration consumed by :class:`JWTValidator`."""

    jwks_url: str | None = None
    issuer: str | None = None
    audience: str | None = None
    discovery_url: str | None = None
    algorithms: tuple[str, ...] = field(default_factory=lambda: ("RS256",))
    required_claims: tuple[str, ...] = field(default_factory=lambda: ("exp", "iat", "nbf"))
    clock_skew_seconds: int = 60
    jwks_cache_ttl_seconds: int = 3600
    discovery_cache_ttl_seconds: int = 3600

    def __post_init__(self) -> None:
        if self.jwks_url is None and self.discovery_url is None:
            raise ValueError("Either jwks_url or discovery_url must be configured.")

        if self.jwks_url and not self.jwks_url.startswith(("http://", "https://")):
            raise ValueError("jwks_url must use http:// or https://")

        if self.discovery_url and not self.discovery_url.startswith(("http://", "https://")):
            raise ValueError("discovery_url must use http:// or https://")

        if not self.algorithms:
            raise ValueError("At least one JWT algorithm is required.")

        if self.clock_skew_seconds < 0:
            raise ValueError("clock_skew_seconds must be >= 0")

    @classmethod
    def from_settings(cls, settings: "Settings") -> "JWTValidatorConfig":
        """Build validator config from kernel settings."""

        return cls(
            jwks_url=settings.JWKS_URL,
            issuer=settings.JWT_ISSUER,
            audience=settings.JWT_AUDIENCE,
            discovery_url=settings.OIDC_DISCOVERY_URL,
            algorithms=tuple(settings.JWT_ALGORITHMS),
            required_claims=tuple(settings.JWT_REQUIRED_CLAIMS),
            clock_skew_seconds=settings.JWT_CLOCK_SKEW_SECONDS,
            jwks_cache_ttl_seconds=settings.JWT_JWKS_CACHE_TTL_SECONDS,
            discovery_cache_ttl_seconds=settings.JWT_DISCOVERY_CACHE_TTL_SECONDS,
        )


__all__ = ["JWTValidatorConfig"]

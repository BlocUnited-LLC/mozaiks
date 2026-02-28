"""
Authentication configuration - provider-agnostic via environment variables.

Default values are for Mozaiks CIAM, but self-hosted deployments can override.

OIDC Discovery:
    By default, jwks_uri and issuer are derived from OIDC discovery.
    Set AUTH_JWKS_URL and AUTH_ISSUER to override (skip discovery).
"""

import os
from dataclasses import dataclass, field
from typing import Optional, List
from functools import lru_cache


@dataclass(frozen=True)
class AuthConfig:
    """Immutable auth configuration loaded from environment."""

    # Core settings
    enabled: bool = True
    
    # OIDC discovery settings (provider-agnostic)
    oidc_authority: str = ""
    oidc_tenant_id: str = ""
    oidc_discovery_url: str = ""  # Optional explicit override
    
    # Override settings (if set, skip discovery for these)
    issuer_override: Optional[str] = None
    jwks_url_override: Optional[str] = None
    
    # Audience and scope
    audience: str = ""
    required_scope: str = ""

    # Claim mappings (provider-specific)
    user_id_claim: str = "sub"
    email_claim: str = "email"
    roles_claim: str = "roles"

    # Caching TTLs
    jwks_cache_ttl_seconds: int = 3600  # 1 hour
    discovery_cache_ttl_seconds: int = 86400  # 24 hours

    # Allowed algorithms
    algorithms: List[str] = field(default_factory=lambda: ["RS256"])

    # Clock skew tolerance (seconds)
    clock_skew_seconds: int = 120

    @property
    def use_discovery(self) -> bool:
        """Whether to use OIDC discovery for jwks_uri and issuer."""
        # Use discovery unless BOTH overrides are set
        return not (self.issuer_override and self.jwks_url_override)


# Mozaiks CIAM defaults
_DEFAULT_OIDC_AUTHORITY = "https://mozaiks.ciamlogin.com"
_DEFAULT_OIDC_TENANT_ID = "9d0073d5-42e8-46f0-a325-5b4be7b1a38d"
_DEFAULT_AUDIENCE = "api://mozaiks-auth"
_DEFAULT_SCOPE = "access_as_user"


def _parse_bool(value: str) -> bool:
    """Parse boolean from env var string."""
    return value.lower() in ("true", "1", "yes", "on")


def _none_if_empty(value: Optional[str]) -> Optional[str]:
    """Return None if string is empty or whitespace."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


@lru_cache(maxsize=1)
def get_auth_config() -> AuthConfig:
    """
    Load auth configuration from environment variables.

    OIDC Discovery Variables:
        MOZAIKS_OIDC_AUTHORITY: Base authority URL (default: https://mozaiks.ciamlogin.com)
        MOZAIKS_OIDC_TENANT_ID: Tenant ID for discovery URL computation
        MOZAIKS_OIDC_DISCOVERY_URL: Explicit discovery URL (overrides authority/tenant)

    Override Variables (skip discovery):
        AUTH_ISSUER: If set, use this issuer instead of discovery
        AUTH_JWKS_URL: If set, use this JWKS URL instead of discovery

    Validation Variables:
        AUTH_ENABLED: Enable/disable auth (default: true, set to false for local dev)
        AUTH_AUDIENCE: Expected audience claim
        AUTH_REQUIRED_SCOPE: Required scope for user endpoints (e.g., access_as_user)

    Claim Mapping Variables:
        AUTH_USER_ID_CLAIM: Claim name for user ID (default: sub)
        AUTH_EMAIL_CLAIM: Claim name for email (default: email)
        AUTH_ROLES_CLAIM: Claim name for roles (default: roles)

    Cache Variables:
        AUTH_JWKS_CACHE_TTL: JWKS cache TTL in seconds (default: 3600)
        AUTH_DISCOVERY_CACHE_TTL: Discovery cache TTL in seconds (default: 86400)

    Other Variables:
        AUTH_ALGORITHMS: Comma-separated list of allowed algorithms (default: RS256)
        AUTH_CLOCK_SKEW: Clock skew tolerance in seconds (default: 120)
    """
    # Check if auth is enabled (allow local dev bypass)
    enabled_str = os.getenv("AUTH_ENABLED", "true")
    enabled = _parse_bool(enabled_str)

    # Parse algorithms list
    algorithms_str = os.getenv("AUTH_ALGORITHMS", "RS256")
    algorithms = [a.strip() for a in algorithms_str.split(",") if a.strip()]

    return AuthConfig(
        enabled=enabled,
        # OIDC discovery settings
        oidc_authority=os.getenv("MOZAIKS_OIDC_AUTHORITY", _DEFAULT_OIDC_AUTHORITY),
        oidc_tenant_id=os.getenv("MOZAIKS_OIDC_TENANT_ID", _DEFAULT_OIDC_TENANT_ID),
        oidc_discovery_url=os.getenv("MOZAIKS_OIDC_DISCOVERY_URL", ""),
        # Override settings
        issuer_override=_none_if_empty(os.getenv("AUTH_ISSUER")),
        jwks_url_override=_none_if_empty(os.getenv("AUTH_JWKS_URL")),
        # Audience and scope
        audience=os.getenv("AUTH_AUDIENCE", _DEFAULT_AUDIENCE),
        required_scope=os.getenv("AUTH_REQUIRED_SCOPE", _DEFAULT_SCOPE),
        # Claim mappings
        user_id_claim=os.getenv("AUTH_USER_ID_CLAIM", "sub"),
        email_claim=os.getenv("AUTH_EMAIL_CLAIM", "email"),
        roles_claim=os.getenv("AUTH_ROLES_CLAIM", "roles"),
        # Cache TTLs
        jwks_cache_ttl_seconds=int(os.getenv("AUTH_JWKS_CACHE_TTL", "3600")),
        discovery_cache_ttl_seconds=int(os.getenv("AUTH_DISCOVERY_CACHE_TTL", "86400")),
        # Other
        algorithms=algorithms,
        clock_skew_seconds=int(os.getenv("AUTH_CLOCK_SKEW", "120")),
    )


def clear_auth_config_cache() -> None:
    """Clear cached config (useful for testing)."""
    get_auth_config.cache_clear()


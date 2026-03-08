"""
Authentication configuration.

Resolution order (highest priority wins):
    1. Environment variables (deployment overrides)
    2. app.json file values (app-level defaults from app/app.json)
    3. Built-in Keycloak defaults (self-hosted fallback)

OIDC Discovery:
    By default, jwks_uri and issuer are derived from OIDC discovery.
    Set AUTH_JWKS_URL and AUTH_ISSUER to override (skip discovery).
"""

import os
from dataclasses import dataclass, field
from typing import Optional, List
from functools import lru_cache

from logs.logging_config import get_core_logger

_logger = get_core_logger("auth.config")


@dataclass(frozen=True)
class AuthConfig:
    """Immutable auth configuration loaded from app.json + environment."""

    # Core settings
    enabled: bool = True

    # Auth provider (informational — "keycloak" | "azure_ad" | "custom_oidc")
    provider: str = "keycloak"

    # OIDC discovery settings (provider-agnostic)
    oidc_authority: str = ""
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


# ---------------------------------------------------------------------------
# Built-in Keycloak defaults (OSS self-hosted)
# These are used when NEITHER app.json NOR env vars supply a value.
# ---------------------------------------------------------------------------
_DEFAULT_KEYCLOAK_AUTHORITY = "http://localhost:8080/realms/mozaiks"
_DEFAULT_AUDIENCE = "mozaiks-app"
_DEFAULT_SCOPE = "openid"


def _parse_bool(value: str) -> bool:
    """Parse boolean from env var string."""
    return value.lower() in ("true", "1", "yes", "on")


def _none_if_empty(value: Optional[str]) -> Optional[str]:
    """Return None if string is empty or whitespace."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _load_app_json_defaults() -> dict:
    """
    Load defaults from app.json's auth section (if present).

    Returns a flat dict with keys matching AuthConfig fields.
    Returns empty dict if app.json is not found or fails to load.
    """
    try:
        from mozaiksai.runtime.auth.auth_config_loader import derive_auth_env
        derived = derive_auth_env()
        if derived:
            _logger.debug("Auth defaults loaded from app.json")
        return derived
    except Exception as exc:
        _logger.debug(f"Could not load app.json defaults: {exc}")
        return {}


@lru_cache(maxsize=1)
def get_auth_config() -> AuthConfig:
    """
    Load auth configuration with layered resolution.

    Priority: env vars > app.json > built-in Keycloak defaults.

    Environment Variables:
        AUTH_ENABLED: Enable/disable auth (default: true, false for local dev bypass)

        MOZAIKS_OIDC_AUTHORITY: OIDC authority URL
            Keycloak default: http://localhost:8080/realms/mozaiks
        MOZAIKS_OIDC_DISCOVERY_URL: Explicit discovery URL override

        AUTH_ISSUER: Explicit issuer (skip discovery if set with AUTH_JWKS_URL)
        AUTH_JWKS_URL: Explicit JWKS URL (skip discovery if set with AUTH_ISSUER)

        AUTH_AUDIENCE: JWT audience (default: mozaiks-app)
        AUTH_REQUIRED_SCOPE: Required scope (default: openid)

        AUTH_USER_ID_CLAIM: User ID claim (default: sub)
        AUTH_EMAIL_CLAIM: Email claim (default: email)
        AUTH_ROLES_CLAIM: Roles claim (default: realm_access)

        AUTH_JWKS_CACHE_TTL: JWKS cache seconds (default: 3600)
        AUTH_DISCOVERY_CACHE_TTL: Discovery cache seconds (default: 86400)
        AUTH_ALGORITHMS: Signing algorithms (default: RS256)
        AUTH_CLOCK_SKEW: Clock skew seconds (default: 120)
    """
    # Load app.json defaults (empty dict if not found)
    file_defaults = _load_app_json_defaults()

    # Helper: env var > app.json > fallback
    def _resolve(env_key: str, json_key: str, fallback: str) -> str:
        env_val = os.getenv(env_key)
        if env_val is not None and env_val.strip():
            return env_val.strip()
        json_val = file_defaults.get(json_key)
        if json_val is not None and str(json_val).strip():
            return str(json_val).strip()
        return fallback

    # Check if auth is enabled (allow local dev bypass)
    enabled_str = os.getenv("AUTH_ENABLED", "true")
    enabled = _parse_bool(enabled_str)

    # Parse algorithms list
    algorithms_str = os.getenv("AUTH_ALGORITHMS", "RS256")
    algorithms = [a.strip() for a in algorithms_str.split(",") if a.strip()]

    # Resolve provider name
    provider = _resolve("MOZAIKS_AUTH_PROVIDER", "provider", "keycloak")

    return AuthConfig(
        enabled=enabled,
        provider=provider,
        # OIDC discovery settings
        oidc_authority=_resolve("MOZAIKS_OIDC_AUTHORITY", "authority", _DEFAULT_KEYCLOAK_AUTHORITY),
        oidc_discovery_url=_resolve("MOZAIKS_OIDC_DISCOVERY_URL", "discovery_url", ""),
        # Override settings
        issuer_override=_none_if_empty(os.getenv("AUTH_ISSUER")),
        jwks_url_override=_none_if_empty(os.getenv("AUTH_JWKS_URL")),
        # Audience and scope
        audience=_resolve("AUTH_AUDIENCE", "audience", _DEFAULT_AUDIENCE),
        required_scope=_resolve("AUTH_REQUIRED_SCOPE", "required_scope", _DEFAULT_SCOPE),
        # Claim mappings
        user_id_claim=_resolve("AUTH_USER_ID_CLAIM", "user_id_claim", "sub"),
        email_claim=_resolve("AUTH_EMAIL_CLAIM", "email_claim", "email"),
        roles_claim=_resolve("AUTH_ROLES_CLAIM", "roles_claim", "realm_access"),
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


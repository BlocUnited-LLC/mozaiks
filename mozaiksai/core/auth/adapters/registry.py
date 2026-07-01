"""
Auth adapter registry.

Manages registration and selection of auth adapters based on configuration.
"""

import os

from logs.logging_config import get_core_logger
from mozaiksai.core.auth.adapters.base import AuthAdapter, AuthError, BaseAuthAdapter

logger = get_core_logger("auth.registry")

# Global adapter registry
_adapter_registry: dict[str, type[BaseAuthAdapter]] = {}
_adapter_instance: AuthAdapter | None = None


def _has_oidc_discovery_config() -> bool:
    return bool(
        os.getenv("MOZAIKS_OIDC_DISCOVERY_URL", "").strip()
        or os.getenv("MOZAIKS_OIDC_AUTHORITY", "").strip()
    )


def register_adapter(name: str, adapter_class: type[BaseAuthAdapter]) -> None:
    """
    Register an auth adapter.

    Args:
        name: Adapter identifier (e.g., "supabase", "keycloak")
        adapter_class: The adapter class to register

    Example:
        register_adapter("my-custom", MyCustomAdapter)
    """
    _adapter_registry[name.lower()] = adapter_class
    logger.debug("Registered auth adapter: %s", name)


def list_adapters() -> list[str]:
    """List all registered adapter names."""
    return list(_adapter_registry.keys())


def _register_builtin_adapters() -> None:
    """Register built-in adapters."""
    # Import here to avoid circular imports
    from mozaiksai.core.auth.adapters.jwt_adapter import GenericJWTAdapter
    from mozaiksai.core.auth.adapters.keycloak import KeycloakAuthAdapter
    from mozaiksai.core.auth.adapters.no_auth import NoAuthAdapter
    from mozaiksai.core.auth.adapters.supabase import SupabaseAuthAdapter

    register_adapter("none", NoAuthAdapter)
    register_adapter("jwt", GenericJWTAdapter)
    register_adapter("supabase", SupabaseAuthAdapter)
    register_adapter("keycloak", KeycloakAuthAdapter)


def _auto_detect_provider() -> str:
    """
    Auto-detect auth provider from environment variables.

    Detection priority:
    1. AUTH_PROVIDER explicitly set
    2. AUTH_ENABLED=false -> "none"
    3. SUPABASE_URL set -> "supabase"
    4. KEYCLOAK_URL set -> "keycloak"
    5. Generic JWT config set -> "jwt"
    6. Default to "none" (demo mode)
    """
    # Explicit provider takes precedence
    explicit_provider = os.getenv("AUTH_PROVIDER", "").lower()
    if explicit_provider:
        return explicit_provider

    # AUTH_ENABLED=false means no auth
    auth_enabled = os.getenv("AUTH_ENABLED", "true").lower()
    if auth_enabled in ("false", "0", "no", "off"):
        return "none"

    # Auto-detect from environment
    if os.getenv("SUPABASE_URL"):
        return "supabase"

    if os.getenv("KEYCLOAK_URL") and os.getenv("KEYCLOAK_REALM"):
        return "keycloak"

    if (os.getenv("AUTH_JWKS_URL") and os.getenv("AUTH_ISSUER")) or _has_oidc_discovery_config():
        return "jwt"

    # Default to no auth for easy getting started
    logger.warning(
        "No auth provider detected. Defaulting to 'none' (demo mode). "
        "Set AUTH_PROVIDER or configure a specific provider."
    )
    return "none"


def get_auth_adapter(force_provider: str | None = None) -> AuthAdapter:
    """
    Get the configured auth adapter.

    Uses singleton pattern - same instance returned on subsequent calls.

    Args:
        force_provider: Override auto-detection with specific provider

    Returns:
        Configured AuthAdapter instance

    Environment Variables:
        AUTH_PROVIDER: Explicit provider selection (none, jwt, supabase, keycloak)
        AUTH_ENABLED: Set to "false" for demo mode (same as AUTH_PROVIDER=none)

    Example:
        adapter = get_auth_adapter()
        claims = await adapter.validate_token(token)
    """
    global _adapter_instance

    # Return cached instance if available and no force override
    if _adapter_instance is not None and force_provider is None:
        return _adapter_instance

    # Ensure built-in adapters are registered
    if not _adapter_registry:
        _register_builtin_adapters()

    # Determine which provider to use
    provider = force_provider or _auto_detect_provider()
    provider = provider.lower()

    # Look up adapter class
    adapter_class = _adapter_registry.get(provider)
    if adapter_class is None:
        available = ", ".join(_adapter_registry.keys())
        raise AuthError(
            f"Unknown auth provider: {provider}. Available: {available}",
            500,
            "registry",
        )

    # Instantiate adapter
    try:
        adapter = adapter_class()
    except Exception as e:
        logger.error("Failed to instantiate %s adapter: %s", provider, e, exc_info=True)
        raise AuthError(
            f"Failed to configure {provider} auth: {e}",
            500,
            provider,
        ) from e

    # Log which adapter is being used
    if adapter.is_enabled():
        logger.info("Auth adapter configured: %s", provider)
    else:
        logger.warning("Auth adapter %s is not fully configured", provider)

    # Cache if not forcing
    if force_provider is None:
        _adapter_instance = adapter

    return adapter


def reset_auth_adapter() -> None:
    """
    Reset the cached auth adapter.

    Useful for testing or when configuration changes.
    """
    global _adapter_instance
    _adapter_instance = None
    logger.debug("Auth adapter cache cleared")


def is_auth_enabled() -> bool:
    """
    Check if authentication is enabled.

    Returns:
        True if auth is enabled, False if using no-auth mode
    """
    provider = _auto_detect_provider()
    return provider != "none"

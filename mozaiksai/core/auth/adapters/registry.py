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


_TRUTHY_VALUES = frozenset({"true", "1", "yes", "on"})
_FALSY_VALUES = frozenset({"false", "0", "no", "off"})


def _has_oidc_discovery_config() -> bool:
    return bool(
        os.getenv("MOZAIKS_OIDC_DISCOVERY_URL", "").strip()
        or os.getenv("MOZAIKS_OIDC_AUTHORITY", "").strip()
    )


def _auth_enabled_setting() -> bool | None:
    """Return the operator's explicit AUTH_ENABLED declaration.

    ``True``/``False`` when AUTH_ENABLED is explicitly set to a recognized
    value, ``None`` when it is unset or empty. An unrecognized value fails
    closed with :class:`AuthError` — a typo in AUTH_ENABLED must never
    silently disable authentication.
    """
    raw = os.getenv("AUTH_ENABLED")
    if raw is None:
        return None
    value = raw.strip().lower()
    if not value:
        return None
    if value in _TRUTHY_VALUES:
        return True
    if value in _FALSY_VALUES:
        return False
    raise AuthError(
        f"Unrecognized AUTH_ENABLED value: {raw!r}. "
        "Use true/false (or 1/0, yes/no, on/off).",
        500,
        "registry",
    )


def is_auth_explicitly_disabled() -> bool:
    """True only when the operator explicitly declared no-auth operation.

    Explicit declaration means AUTH_ENABLED is set to a falsy value, or
    AUTH_PROVIDER is set to "none" without AUTH_ENABLED being explicitly
    true. Absence of provider configuration (implicit demo mode) is NOT an
    explicit declaration — security-sensitive bypasses must key off this
    helper, never off ``not is_auth_enabled()``.
    """
    enabled_setting = _auth_enabled_setting()
    if enabled_setting is False:
        return True
    explicit_provider = os.getenv("AUTH_PROVIDER", "").strip().lower()
    return explicit_provider == "none" and enabled_setting is not True


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
    2. AUTH_ENABLED explicitly false -> "none"
    3. SUPABASE_URL set -> "supabase"
    4. KEYCLOAK_URL set -> "keycloak"
    5. Generic JWT config set -> "jwt"
    6. AUTH_ENABLED explicitly true -> fatal (fail closed, any environment)
    7. Nothing configured at all -> "none" (demo mode)

    When authentication is explicitly enabled, inability to establish the
    configured provider raises :class:`AuthError` instead of silently falling
    back to the trusted-bypass "none" adapter. This does not depend on
    ENV=production — staging and every other environment fail closed too.
    """
    enabled_setting = _auth_enabled_setting()

    # Explicit provider takes precedence
    explicit_provider = os.getenv("AUTH_PROVIDER", "").strip().lower()
    if explicit_provider:
        if explicit_provider == "none" and enabled_setting is True:
            raise AuthError(
                "AUTH_ENABLED=true conflicts with AUTH_PROVIDER=none. "
                "Either configure a real auth provider or set AUTH_ENABLED=false "
                "to explicitly run without authentication.",
                500,
                "registry",
            )
        return explicit_provider

    # AUTH_ENABLED explicitly false means no auth (explicit dev contract)
    if enabled_setting is False:
        return "none"

    # Auto-detect from environment
    if os.getenv("SUPABASE_URL"):
        return "supabase"

    if os.getenv("KEYCLOAK_URL") and os.getenv("KEYCLOAK_REALM"):
        return "keycloak"

    if (os.getenv("AUTH_JWKS_URL") and os.getenv("AUTH_ISSUER")) or _has_oidc_discovery_config():
        return "jwt"

    if enabled_setting is True:
        # Auth was explicitly enabled but no provider can be established.
        # Dev intent is never inferred from provider misconfiguration.
        raise AuthError(
            "AUTH_ENABLED=true but no authentication provider is configured. "
            "Set AUTH_PROVIDER or configure SUPABASE_URL, KEYCLOAK_URL + "
            "KEYCLOAK_REALM, AUTH_JWKS_URL + AUTH_ISSUER, or "
            "MOZAIKS_OIDC_AUTHORITY. Refusing to fall back to unauthenticated "
            "operation.",
            500,
            "registry",
        )

    # Nothing auth-related configured at all: demo mode for easy getting started.
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
    elif provider != "none" and _auth_enabled_setting() is True:
        # Explicitly enabled auth with a provider that cannot validate tokens
        # is fatal — an incomplete provider config must never degrade to
        # unauthenticated operation.
        raise AuthError(
            f"AUTH_ENABLED=true but the {provider!r} auth provider is not fully "
            "configured and cannot validate tokens. Complete the provider "
            "configuration or set AUTH_ENABLED=false explicitly.",
            500,
            provider,
        )
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


def validate_auth_provider_configuration() -> str:
    """Resolve and validate the configured auth provider, failing closed.

    Returns the resolved provider name on success. Raises :class:`AuthError`
    when authentication is explicitly enabled but the configured provider is
    missing, unknown, conflicting, or not fully configured. Hosts call this
    at startup so misconfigured authentication aborts boot in every
    environment instead of degrading to trusted-bypass operation.

    Validation instantiates the adapter with ``force_provider`` so it never
    caches the singleton instance.
    """
    provider = _auto_detect_provider()
    if provider != "none":
        get_auth_adapter(force_provider=provider)
    return provider


def is_auth_enabled() -> bool:
    """
    Check if authentication is enabled.

    Returns:
        True if auth is enabled, False if using no-auth mode
    """
    provider = _auto_detect_provider()
    return provider != "none"

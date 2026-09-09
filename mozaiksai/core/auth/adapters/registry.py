"""
Auth adapter registry.

One canonical interpretation of authentication configuration lives here:
:func:`resolve_auth_config` parses the environment exactly once per call into
an immutable :class:`ResolvedAuthConfig`. Every predicate
(:func:`is_auth_enabled`, :func:`is_auth_explicitly_disabled`), adapter
resolution (:func:`get_auth_adapter`), and startup validation
(:func:`validate_auth_provider_configuration`) consumes that same resolved
state — there is no second parser, so the predicates can never contradict
each other.

Fail-closed contract:

- Explicitly enabled authentication (``AUTH_ENABLED=true``) with a missing,
  unknown, conflicting, or not-fully-configured provider raises
  :class:`AuthError` — in every environment, at resolution time.
- Contradictory explicit declarations (``AUTH_ENABLED=true`` +
  ``AUTH_PROVIDER=none``; ``AUTH_ENABLED=false`` + a real explicit
  ``AUTH_PROVIDER``) raise instead of silently choosing one.
- Unrecognized ``AUTH_ENABLED`` values raise instead of silently disabling
  authentication.
- No-auth operation of any kind (explicit disable or implicit demo mode) is
  rejected in protected deployed environments (staging/production, per
  ``mozaiksai.core.environment``), independent of startup-check mode.
- The cached adapter is keyed by a fingerprint of the resolved configuration:
  request-time adapter resolution always reflects the configuration that
  startup validated, and a stale trusted-bypass adapter cannot survive a
  configuration change.
"""

import os
from dataclasses import dataclass
from typing import Literal

from logs.logging_config import get_core_logger
from mozaiksai.core.auth.adapters.base import AuthAdapter, AuthError, BaseAuthAdapter
from mozaiksai.core.environment import deployment_environment, is_protected_environment

logger = get_core_logger("auth.registry")

# Global adapter registry
_adapter_registry: dict[str, type[BaseAuthAdapter]] = {}
_adapter_instance: AuthAdapter | None = None
_adapter_fingerprint: tuple[tuple[str, str], ...] | None = None


_TRUTHY_VALUES = frozenset({"true", "1", "yes", "on"})
_FALSY_VALUES = frozenset({"false", "0", "no", "off"})

# Environment variables that participate in auth configuration resolution or
# adapter construction. The resolved-config fingerprint snapshots these so the
# cached adapter is invalidated whenever any of them changes (D5 coherence).
_AUTH_CONFIG_ENV_VARS: tuple[str, ...] = (
    "AUTH_ENABLED",
    "AUTH_PROVIDER",
    "SUPABASE_URL",
    "SUPABASE_JWT_SECRET",
    "KEYCLOAK_URL",
    "KEYCLOAK_REALM",
    "KEYCLOAK_CLIENT_ID",
    "AUTH_JWKS_URL",
    "AUTH_ISSUER",
    "MOZAIKS_OIDC_AUTHORITY",
    "MOZAIKS_OIDC_TENANT_ID",
    "MOZAIKS_OIDC_DISCOVERY_URL",
    "AUTH_AUDIENCE",
    "AUTH_REQUIRED_SCOPE",
    "AUTH_ALGORITHMS",
    "AUTH_USER_ID_CLAIM",
    "AUTH_EMAIL_CLAIM",
    "AUTH_ROLES_CLAIM",
    "ENV",
    "ENVIRONMENT",
)

ResolvedAuthSource = Literal[
    "explicit_provider",
    "explicit_disable",
    "auto_detected",
    "demo_default",
]


@dataclass(frozen=True)
class ResolvedAuthConfig:
    """Immutable, canonical interpretation of the auth environment.

    Invariants (enforced at construction):
      - ``enabled`` is exactly ``provider != "none"``
      - ``enabled`` and ``explicitly_disabled`` are never both true
      - ``explicitly_disabled`` implies ``provider == "none"``
    """

    provider: str
    enabled: bool
    explicitly_disabled: bool
    source: ResolvedAuthSource
    environment: str
    fingerprint: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if self.enabled != (self.provider != "none"):
            raise AuthError(
                "Internal auth resolution invariant violated: enabled flag does "
                f"not match provider {self.provider!r}.",
                500,
                "registry",
            )
        if self.enabled and self.explicitly_disabled:
            raise AuthError(
                "Internal auth resolution invariant violated: configuration "
                "resolved as both enabled and explicitly disabled.",
                500,
                "registry",
            )


def _config_fingerprint() -> tuple[tuple[str, str], ...]:
    return tuple((name, os.getenv(name) or "") for name in _AUTH_CONFIG_ENV_VARS)


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


def resolve_auth_config() -> ResolvedAuthConfig:
    """Resolve the environment into the canonical auth configuration.

    This is the ONLY parser of auth-mode environment variables. Resolution
    order:

    1. ``AUTH_PROVIDER`` explicitly set — after contradiction checks against
       ``AUTH_ENABLED`` — selects that provider (``none`` = explicit disable).
    2. ``AUTH_ENABLED`` explicitly false — explicit disable.
    3. Auto-detection from provider signals (SUPABASE_URL,
       KEYCLOAK_URL + KEYCLOAK_REALM, AUTH_JWKS_URL + AUTH_ISSUER, OIDC
       discovery settings).
    4. ``AUTH_ENABLED`` explicitly true with nothing detectable — fatal.
    5. Nothing auth-related configured at all — implicit demo mode
       (``none``, not an explicit disable).

    Contradictory explicit declarations raise instead of silently resolving:
    ``AUTH_ENABLED=true`` + ``AUTH_PROVIDER=none`` and ``AUTH_ENABLED=false``
    + a real explicit ``AUTH_PROVIDER`` are both rejected. Passive provider
    signals (for example a SUPABASE_URL left in a developer .env) do not
    contradict an explicit disable — the explicit switch wins over passive
    presence, which is the existing OSS development contract.

    Environment policy (mandatory, mode-independent): any configuration that
    resolves to no-auth operation — explicit disable or implicit demo — is
    rejected in protected deployed environments (staging/production).
    """
    fingerprint = _config_fingerprint()
    environment = deployment_environment()
    enabled_setting = _auth_enabled_setting()
    explicit_provider = os.getenv("AUTH_PROVIDER", "").strip().lower()

    provider: str
    explicitly_disabled: bool
    source: ResolvedAuthSource

    if explicit_provider:
        if explicit_provider == "none" and enabled_setting is True:
            raise AuthError(
                "AUTH_ENABLED=true conflicts with AUTH_PROVIDER=none. "
                "Either configure a real auth provider or set AUTH_ENABLED=false "
                "to explicitly run without authentication.",
                500,
                "registry",
            )
        if explicit_provider != "none" and enabled_setting is False:
            raise AuthError(
                f"AUTH_ENABLED=false conflicts with AUTH_PROVIDER={explicit_provider!r}. "
                "Remove AUTH_PROVIDER to disable authentication, or set "
                "AUTH_ENABLED=true to use the configured provider. Refusing to "
                "silently pick one of two contradictory declarations.",
                500,
                "registry",
            )
        if explicit_provider == "none":
            provider, explicitly_disabled, source = "none", True, "explicit_disable"
        else:
            provider, explicitly_disabled, source = explicit_provider, False, "explicit_provider"
    elif enabled_setting is False:
        provider, explicitly_disabled, source = "none", True, "explicit_disable"
    elif os.getenv("SUPABASE_URL"):
        provider, explicitly_disabled, source = "supabase", False, "auto_detected"
    elif os.getenv("KEYCLOAK_URL") and os.getenv("KEYCLOAK_REALM"):
        provider, explicitly_disabled, source = "keycloak", False, "auto_detected"
    elif (os.getenv("AUTH_JWKS_URL") and os.getenv("AUTH_ISSUER")) or _has_oidc_discovery_config():
        provider, explicitly_disabled, source = "jwt", False, "auto_detected"
    elif enabled_setting is True:
        # Auth was explicitly enabled but no provider can be established.
        # Dev intent is never inferred from provider misconfiguration; this
        # does not depend on ENV — staging and every other environment fail
        # closed too.
        raise AuthError(
            "AUTH_ENABLED=true but no authentication provider is configured. "
            "Set AUTH_PROVIDER or configure SUPABASE_URL, KEYCLOAK_URL + "
            "KEYCLOAK_REALM, AUTH_JWKS_URL + AUTH_ISSUER, or "
            "MOZAIKS_OIDC_AUTHORITY. Refusing to fall back to unauthenticated "
            "operation.",
            500,
            "registry",
        )
    else:
        # Nothing auth-related configured at all: implicit demo mode for easy
        # getting started. NOT an explicit disable — security-sensitive
        # bypasses must not treat demo mode as operator intent.
        logger.warning(
            "No auth provider detected. Defaulting to 'none' (demo mode). "
            "Set AUTH_PROVIDER or configure a specific provider."
        )
        provider, explicitly_disabled, source = "none", False, "demo_default"

    if provider == "none" and is_protected_environment(environment):
        raise AuthError(
            f"Authentication-disabled operation is not permitted in the "
            f"{environment!r} environment. Configure a real auth provider "
            "(AUTH_PROVIDER, SUPABASE_URL, KEYCLOAK_URL + KEYCLOAK_REALM, "
            "AUTH_JWKS_URL + AUTH_ISSUER, or MOZAIKS_OIDC_AUTHORITY). "
            "Explicit AUTH_ENABLED=false / AUTH_PROVIDER=none are development "
            "contracts only.",
            500,
            "registry",
        )

    return ResolvedAuthConfig(
        provider=provider,
        enabled=provider != "none",
        explicitly_disabled=explicitly_disabled,
        source=source,
        environment=environment,
        fingerprint=fingerprint,
    )


def is_auth_enabled() -> bool:
    """True when the canonical resolved configuration has a real provider.

    Raises :class:`AuthError` (fail closed) when the configuration is invalid
    — it never reports "disabled" for a misconfigured deployment.
    """
    return resolve_auth_config().enabled


def is_auth_explicitly_disabled() -> bool:
    """True only when the operator explicitly declared no-auth operation.

    Explicit declaration means ``AUTH_ENABLED=false`` or
    ``AUTH_PROVIDER=none``. Implicit demo mode (no auth configuration at all)
    is NOT an explicit declaration — security-sensitive bypasses must key off
    this helper, never off ``not is_auth_enabled()``. Both predicates read the
    same :func:`resolve_auth_config` interpretation, so they can never both be
    true.
    """
    return resolve_auth_config().explicitly_disabled


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


def _build_adapter(provider: str, *, enabled: bool) -> AuthAdapter:
    """Instantiate and validate the adapter for a resolved provider."""
    if not _adapter_registry:
        _register_builtin_adapters()

    adapter_class = _adapter_registry.get(provider)
    if adapter_class is None:
        available = ", ".join(_adapter_registry.keys())
        raise AuthError(
            f"Unknown auth provider: {provider}. Available: {available}",
            500,
            "registry",
        )

    try:
        adapter = adapter_class()
    except Exception as e:
        logger.error("Failed to instantiate %s adapter: %s", provider, e, exc_info=True)
        raise AuthError(
            f"Failed to configure {provider} auth: {e}",
            500,
            provider,
        ) from e

    if adapter.is_enabled():
        logger.info("Auth adapter configured: %s", provider)
    elif enabled:
        # A resolved-enabled provider that cannot validate tokens is fatal —
        # an incomplete provider config must never degrade to unauthenticated
        # operation.
        raise AuthError(
            f"Auth provider {provider!r} is selected but not fully configured "
            "and cannot validate tokens. Complete the provider configuration "
            "or explicitly disable authentication (AUTH_ENABLED=false) in a "
            "development environment.",
            500,
            provider,
        )
    else:
        logger.warning("Auth adapter %s is not fully configured", provider)

    return adapter


def get_auth_adapter(force_provider: str | None = None) -> AuthAdapter:
    """
    Get the auth adapter for the canonical resolved configuration.

    The cached instance is keyed by the resolved-configuration fingerprint:
    when any auth-relevant environment variable changes, the stale adapter is
    discarded and a fresh one is built from the current configuration. The
    AuthConfig value cache is cleared on rebuild so adapter internals read the
    same environment snapshot. Repeated initialization is deterministic.

    Args:
        force_provider: Build an adapter for this provider name directly,
            bypassing resolution and the cache entirely (never cached).

    Returns:
        Configured AuthAdapter instance

    Example:
        adapter = get_auth_adapter()
        claims = await adapter.validate_token(token)
    """
    global _adapter_instance, _adapter_fingerprint

    if force_provider is not None:
        return _build_adapter(force_provider.lower(), enabled=force_provider.lower() != "none")

    config = resolve_auth_config()

    if _adapter_instance is not None and _adapter_fingerprint == config.fingerprint:
        return _adapter_instance

    # Configuration changed (or first resolution): rebuild coherently. The
    # AuthConfig cache must be refreshed first so the adapter constructor and
    # its validators read the same environment the fingerprint captured.
    from mozaiksai.core.auth.config import clear_auth_config_cache

    if _adapter_instance is not None:
        logger.info(
            "Auth configuration changed; rebuilding auth adapter (provider=%s)",
            config.provider,
        )
    clear_auth_config_cache()
    adapter = _build_adapter(config.provider, enabled=config.enabled)

    _adapter_instance = adapter
    _adapter_fingerprint = config.fingerprint
    return adapter


def reset_auth_adapter() -> None:
    """
    Reset the cached auth adapter.

    Useful for testing or when configuration changes.
    """
    global _adapter_instance, _adapter_fingerprint
    _adapter_instance = None
    _adapter_fingerprint = None
    logger.debug("Auth adapter cache cleared")


def validate_auth_provider_configuration() -> str:
    """Resolve and validate the auth configuration, failing closed.

    Returns the resolved provider name on success. Raises :class:`AuthError`
    when the configuration is invalid: enabled auth whose provider is missing,
    unknown, conflicting, or not fully configured; contradictory explicit
    declarations; or no-auth operation in a protected environment. Hosts call
    this at startup so misconfiguration aborts boot in every environment and
    every startup-check mode.

    On success for a real provider, the validated adapter is bound into the
    fingerprint-keyed cache — request-time resolution uses exactly the
    configuration startup validated.
    """
    config = resolve_auth_config()
    if config.provider != "none":
        get_auth_adapter()
    return config.provider

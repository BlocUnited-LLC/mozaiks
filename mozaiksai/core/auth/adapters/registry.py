"""
Auth adapter registry.

One canonical interpretation of authentication configuration lives here:
:func:`resolve_auth_config` parses the environment exactly once per call into
an immutable :class:`ResolvedAuthConfig` carrying a complete configuration
snapshot. Every predicate (:func:`is_auth_enabled`,
:func:`is_auth_explicitly_disabled`), adapter construction
(:func:`get_auth_adapter`), and startup validation
(:func:`validate_auth_provider_configuration`) consumes that same resolved
state — there is no second parser, so the predicates can never contradict
each other and the adapter can never be built from a configuration other than
the one its cache identity describes.

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
  permitted **only** in the finite set of recognized local/development/test
  environments (see :mod:`mozaiksai.core.environment`). Every other explicit
  environment value — known deployments and unknown/regional/custom names
  alike — rejects it, independent of startup-check mode.
- The cached adapter is keyed by a fingerprint derived from the complete
  configuration snapshot that actually constructs the adapter for the resolved
  provider, plus adapter-registration identity. Changing any meaning-bearing
  input rebuilds the adapter; a stale adapter can never serve requests under a
  newer configuration.
"""

import inspect
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from logs.logging_config import get_core_logger
from mozaiksai.core.auth.adapters.base import AuthAdapter, AuthError, BaseAuthAdapter
from mozaiksai.core.auth.cache_ttl import (
    CACHE_TTL_DEFAULTS,
    CacheTtlConfigError,
    resolve_cache_ttl_setting,
)
from mozaiksai.core.environment import (
    ENVIRONMENT_ENV_VARS,
    EnvironmentConfigError,
    ResolvedEnvironment,
    resolve_environment,
)

logger = get_core_logger("auth.registry")

_TRUTHY_VALUES = frozenset({"true", "1", "yes", "on"})
_FALSY_VALUES = frozenset({"false", "0", "no", "off"})

# ---------------------------------------------------------------------------
# Configuration census
#
# Every environment variable the auth subsystem reads, grouped by what it
# controls. The mode variables select the provider; the per-provider variables
# are the complete set of inputs that construct and control that provider's
# adapter (including shared infrastructure it drives, such as JWKS/discovery
# cache TTLs consumed through AuthConfig). Adapter cache identity is derived
# from exactly these, so any meaning-bearing change rebuilds the adapter.
# ---------------------------------------------------------------------------

_AUTH_MODE_ENV_VARS: tuple[str, ...] = ("AUTH_ENABLED", "AUTH_PROVIDER", *ENVIRONMENT_ENV_VARS)

_JWT_CONFIG_ENV_VARS: tuple[str, ...] = (
    "AUTH_JWKS_URL",
    "AUTH_ISSUER",
    "AUTH_AUDIENCE",
    "MOZAIKS_OIDC_AUTHORITY",
    "MOZAIKS_OIDC_TENANT_ID",
    "MOZAIKS_OIDC_DISCOVERY_URL",
    "AUTH_USER_ID_CLAIM",
    "AUTH_EMAIL_CLAIM",
    "AUTH_NAME_CLAIM",
    "AUTH_ROLES_CLAIM",
    "AUTH_SCOPES_CLAIM",
    "AUTH_APP_ID_CLAIM",
    "AUTH_CHAT_ID_CLAIM",
    "AUTH_TENANT_ID_CLAIM",
    "AUTH_WORKSPACE_ID_CLAIM",
    "AUTH_SCOPES_FORMAT",
    "AUTH_ALGORITHMS",
    "AUTH_CLOCK_SKEW",
    "AUTH_REQUIRED_SCOPE",
    # Shared validation infrastructure driven by the JWT adapter via AuthConfig.
    "AUTH_JWKS_CACHE_TTL",
    "AUTH_DISCOVERY_CACHE_TTL",
)

_PROVIDER_CONFIG_ENV_VARS: dict[str, tuple[str, ...]] = {
    "jwt": _JWT_CONFIG_ENV_VARS,
    "supabase": ("SUPABASE_URL", "SUPABASE_JWT_SECRET"),
    "keycloak": (
        "KEYCLOAK_URL",
        "KEYCLOAK_REALM",
        "KEYCLOAK_CLIENT_ID",
        "KEYCLOAK_APP_ID_CLAIM",
        "KEYCLOAK_TENANT_ID_CLAIM",
        "KEYCLOAK_WORKSPACE_ID_CLAIM",
    ),
    "none": (
        "AUTH_ANON_USER_ID",
        "AUTH_ANON_EMAIL",
        "AUTH_ANON_ROLES",
        "AUTH_ANON_SCOPES",
    ),
}

# Complete snapshot surface: mode variables plus every provider's inputs. The
# snapshot is what adapters construct from; the fingerprint uses the mode
# variables plus the resolved provider's own inputs.
_ALL_AUTH_ENV_VARS: tuple[str, ...] = tuple(
    dict.fromkeys(
        (
            *_AUTH_MODE_ENV_VARS,
            *(name for names in _PROVIDER_CONFIG_ENV_VARS.values() for name in names),
        )
    )
)

BUILTIN_PROVIDERS: frozenset[str] = frozenset(_PROVIDER_CONFIG_ENV_VARS)

SETTINGS_PARAMETER = "settings"

#: Placeholder used only to test-bind a constructor signature at registration.
#: It is never passed to a real constructor.
_SETTINGS_BINDING_PROBE = object()

#: How an adapter constructor must be invoked. Established positively at
#: registration — never inferred from a failed construction attempt.
ConstructorMode = Literal["settings_keyword", "no_settings"]
_CONSTRUCTOR_MODES: frozenset[str] = frozenset({"settings_keyword", "no_settings"})

ResolvedAuthSource = Literal[
    "explicit_provider",
    "explicit_disable",
    "auto_detected",
    "demo_default",
]


# ---------------------------------------------------------------------------
# Adapter registrations
# ---------------------------------------------------------------------------


def _validate_config_identity_value(value: object, *, source: str) -> str:
    """Validate one config-identity value against the documented contract.

    The contract is a non-blank string. Values of any other type — ``None``,
    lists, mappings, numbers — are rejected rather than coerced with ``str()``,
    which would otherwise mint a cache key that does not truthfully identify a
    configured revision. Empty and whitespace-only strings are rejected for the
    same reason: they cannot distinguish one revision from another.
    """
    if not isinstance(value, str):
        raise AuthError(
            f"Auth adapter config_identity {source} must be a string, got "
            f"{type(value).__name__}. Provide a string that changes whenever the "
            "adapter's configuration changes, or omit config_identity to opt out "
            "of caching entirely.",
            500,
            "registry",
        )
    if not value.strip():
        raise AuthError(
            f"Auth adapter config_identity {source} must be a non-blank string; "
            "an empty or whitespace-only identity does not identify a configured "
            "revision. Omit config_identity to opt out of caching entirely.",
            500,
            "registry",
        )
    return value


@dataclass(frozen=True)
class _AdapterRegistration:
    """A registered adapter class plus its construction and cache contracts.

    ``config_identity`` is how a provider states "my configuration changed".
    Built-in providers derive it from their declared environment census.
    Custom providers must supply one explicitly (a non-blank string, or a
    callable returning one) to be cacheable; without it the adapter is rebuilt
    on every resolution, because the runtime cannot know what configures a
    third-party adapter and must never reuse one validated under an older
    configuration.

    ``constructor_mode`` is established once, at registration, by positively
    classifying the constructor signature (or by an explicit declaration for
    uninspectable constructors). Construction never infers signature support
    from a caught ``TypeError`` — an exception raised inside a constructor body
    is a real failure and must propagate.
    """

    adapter_class: type[BaseAuthAdapter]
    generation: int
    builtin: bool
    constructor_mode: ConstructorMode
    config_identity: str | Callable[[], str] | None = None

    @property
    def accepts_settings(self) -> bool:
        return self.constructor_mode == "settings_keyword"

    def identity_token(self) -> str | None:
        """Return the provider-declared identity, or None when uncacheable.

        Raises :class:`AuthError` when a declared identity is malformed, so a
        misconfigured provider fails closed instead of silently caching under a
        meaningless key.
        """
        if self.builtin:
            return "builtin"
        if self.config_identity is None:
            return None
        if callable(self.config_identity):
            try:
                produced = self.config_identity()
            except AuthError:
                raise
            except Exception as exc:  # noqa: BLE001 - reported as AuthError
                raise AuthError(
                    f"Auth adapter config_identity callable raised {type(exc).__name__}: "
                    f"{exc}. The identity must be produced deterministically.",
                    500,
                    "registry",
                ) from exc
            return _validate_config_identity_value(produced, source="callable return value")
        return _validate_config_identity_value(self.config_identity, source="value")


def _bind_invocation(
    signature: inspect.Signature, mode: ConstructorMode
) -> tuple[bool, str | None]:
    """Try to bind the exact call the runtime performs for ``mode``.

    Returns ``(ok, reason)``. This is the single source of truth for whether a
    constructor mode is usable: the registry never reasons about parameter
    kinds to decide invocability, it asks Python's own binding machinery
    whether its intended call would succeed.
    """
    try:
        if mode == "settings_keyword":
            signature.bind(**{SETTINGS_PARAMETER: _SETTINGS_BINDING_PROBE})
        else:
            signature.bind()
    except TypeError as exc:
        return False, str(exc)
    return True, None


def _classify_constructor(
    adapter_class: type,
    *,
    declared_mode: ConstructorMode | None,
) -> ConstructorMode:
    """Positively establish how an adapter constructor must be invoked.

    A mode is accepted only when the exact invocation the runtime intends to
    perform binds successfully against the complete inspected signature —
    ``Constructor(settings=...)`` for ``settings_keyword`` and
    ``Constructor()`` for ``no_settings``. Recognizing a usable ``settings``
    parameter (or ``**kwargs``) is necessary but never sufficient: a
    constructor that also demands arguments the runtime cannot supply is
    rejected here rather than at first use.

    "Could not inspect" and "no ``settings`` keyword found" are likewise not
    evidence that a constructor takes no configuration — both raise instead of
    silently discarding the canonical snapshot.

    ``declared_mode`` is the escape hatch for constructors that genuinely
    cannot be inspected (C extensions, exotic callables). When the signature
    *is* inspectable the declaration is verified against it, so explicit
    metadata cannot claim an invocation that provably would not work.
    """
    name = getattr(adapter_class, "__name__", repr(adapter_class))

    if declared_mode is not None and declared_mode not in _CONSTRUCTOR_MODES:
        raise AuthError(
            f"Unknown constructor_mode {declared_mode!r} for auth adapter {name}. "
            f"Valid modes: {', '.join(sorted(_CONSTRUCTOR_MODES))}.",
            500,
            "registry",
        )

    try:
        signature = inspect.signature(adapter_class)
    except (TypeError, ValueError) as exc:
        if declared_mode is not None:
            # Genuinely uninspectable: the registrant's declaration is the only
            # available contract, which is exactly what it exists for.
            return declared_mode
        raise AuthError(
            f"Cannot establish the constructor contract for auth adapter {name}: "
            f"its signature could not be inspected ({type(exc).__name__}). Register it "
            "with an explicit constructor_mode "
            f"({', '.join(sorted(_CONSTRUCTOR_MODES))}) so the runtime knows whether it "
            "consumes the configuration snapshot. Refusing to assume it takes none.",
            500,
            "registry",
        ) from exc

    if declared_mode is not None:
        # Inspection is available, so verify rather than trust.
        ok, reason = _bind_invocation(signature, declared_mode)
        if not ok:
            raise AuthError(
                f"Auth adapter {name} was registered as constructor_mode "
                f"{declared_mode!r}, but the runtime's invocation cannot bind to its "
                f"signature {signature}: {reason}.",
                500,
                "registry",
            )
        return declared_mode

    settings_parameter = signature.parameters.get(SETTINGS_PARAMETER)
    if settings_parameter is not None and settings_parameter.kind is (
        inspect.Parameter.POSITIONAL_ONLY
    ):
        # Positional-only configuration is not part of the plugin contract;
        # accepting it silently would risk passing the snapshot in the wrong
        # slot, and ignoring it would discard the configuration entirely.
        raise AuthError(
            f"Auth adapter {name} declares {SETTINGS_PARAMETER!r} as a positional-only "
            "parameter, which the adapter contract does not support. Make it accept "
            f"{SETTINGS_PARAMETER}= as a keyword so the configuration snapshot can be "
            "supplied unambiguously.",
            500,
            "registry",
        )

    # A constructor is settings-aware when it names ``settings`` as a keyword or
    # absorbs keywords via ``**kwargs``; either way the full invocation must bind.
    accepts_settings_keyword = settings_parameter is not None or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    candidate: ConstructorMode = "settings_keyword" if accepts_settings_keyword else "no_settings"

    ok, reason = _bind_invocation(signature, candidate)
    if ok:
        return candidate

    if candidate == "settings_keyword":
        raise AuthError(
            f"Auth adapter {name} accepts {SETTINGS_PARAMETER}= but the runtime cannot "
            f"construct it: {signature} also requires argument(s) the runtime does not "
            f"supply ({reason}). Give those parameters defaults so construction needs "
            f"only {SETTINGS_PARAMETER}=.",
            500,
            "registry",
        )
    raise AuthError(
        f"Auth adapter {name} requires constructor argument(s) that the runtime cannot "
        f"supply: {signature} ({reason}). Accept the configuration snapshot as "
        f"{SETTINGS_PARAMETER}=, or give those parameters defaults.",
        500,
        "registry",
    )


@dataclass(frozen=True)
class ResolvedAuthConfig:
    """Immutable, canonical interpretation of the auth environment.

    Invariants (enforced at construction):
      - ``enabled`` is exactly ``provider != "none"``
      - ``enabled`` and ``explicitly_disabled`` are never both true
    """

    provider: str
    enabled: bool
    explicitly_disabled: bool
    source: ResolvedAuthSource
    environment: ResolvedEnvironment
    settings: Mapping[str, str]
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


@dataclass(frozen=True)
class _AdapterCacheEntry:
    """Atomically published cache record.

    Fingerprint, provider, and adapter are published together as one immutable
    value, so a request can never observe a fingerprint from one configuration
    beside an adapter built from another.
    """

    fingerprint: tuple[tuple[str, str], ...]
    provider: str
    adapter: AuthAdapter


# Global registry state
_adapter_registry: dict[str, _AdapterRegistration] = {}
_registry_generation: int = 0
_adapter_cache: _AdapterCacheEntry | None = None


def _environment_snapshot() -> Mapping[str, str]:
    """Capture the complete auth configuration surface once, immutably."""
    return MappingProxyType({name: os.getenv(name) or "" for name in _ALL_AUTH_ENV_VARS})


def _has_oidc_discovery_config(settings: Mapping[str, str]) -> bool:
    return bool(
        settings.get("MOZAIKS_OIDC_DISCOVERY_URL", "").strip()
        or settings.get("MOZAIKS_OIDC_AUTHORITY", "").strip()
    )


def _auth_enabled_setting(settings: Mapping[str, str]) -> bool | None:
    """Return the operator's explicit AUTH_ENABLED declaration.

    ``True``/``False`` when AUTH_ENABLED is explicitly set to a recognized
    value, ``None`` when it is unset or empty. An unrecognized value fails
    closed with :class:`AuthError` — a typo in AUTH_ENABLED must never
    silently disable authentication.
    """
    raw = settings.get("AUTH_ENABLED", "")
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


def _fingerprint(
    *,
    provider: str,
    settings: Mapping[str, str],
    registration: _AdapterRegistration | None,
) -> tuple[tuple[str, str], ...]:
    """Derive the cache identity from the snapshot that builds the adapter.

    Includes the mode variables, the resolved provider's complete declared
    configuration inputs, and the adapter registration's identity/generation.
    """
    parts: list[tuple[str, str]] = [
        (name, settings.get(name, "")) for name in _AUTH_MODE_ENV_VARS
    ]
    parts.extend(
        (name, settings.get(name, ""))
        for name in _PROVIDER_CONFIG_ENV_VARS.get(provider, ())
    )
    parts.append(("__provider__", provider))
    if registration is not None:
        parts.append(("__registration_generation__", str(registration.generation)))
        identity = registration.identity_token()
        parts.append(("__registration_identity__", identity if identity is not None else ""))
    return tuple(parts)


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
    permitted only in a recognized local/development/test environment.
    """
    settings = _environment_snapshot()

    try:
        environment = resolve_environment()
    except EnvironmentConfigError as exc:
        raise AuthError(str(exc), 500, "registry") from exc

    # Cache TTLs are parsed here, during canonical resolution, so a malformed
    # value fails startup rather than surfacing later inside lazy discovery or
    # JWKS client construction on a request path.
    for ttl_name in CACHE_TTL_DEFAULTS:
        try:
            resolve_cache_ttl_setting(ttl_name, settings.get(ttl_name))
        except CacheTtlConfigError as exc:
            raise AuthError(str(exc), 500, "registry") from exc

    enabled_setting = _auth_enabled_setting(settings)
    explicit_provider = settings.get("AUTH_PROVIDER", "").strip().lower()

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
    elif settings.get("SUPABASE_URL", "").strip():
        provider, explicitly_disabled, source = "supabase", False, "auto_detected"
    elif settings.get("KEYCLOAK_URL", "").strip() and settings.get("KEYCLOAK_REALM", "").strip():
        provider, explicitly_disabled, source = "keycloak", False, "auto_detected"
    elif (
        settings.get("AUTH_JWKS_URL", "").strip() and settings.get("AUTH_ISSUER", "").strip()
    ) or _has_oidc_discovery_config(settings):
        provider, explicitly_disabled, source = "jwt", False, "auto_detected"
    elif enabled_setting is True:
        # Auth was explicitly enabled but no provider can be established.
        # Dev intent is never inferred from provider misconfiguration; this
        # does not depend on the environment — every environment fails closed.
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

    if provider == "none" and not environment.permits_no_auth:
        raise AuthError(
            "Authentication-disabled operation is not permitted in the "
            f"{environment.display_name!r} environment. Unauthenticated operation "
            "is available only in recognized local development environments "
            "(development, local, test) or with no environment configured; "
            "explicit AUTH_ENABLED=false / AUTH_PROVIDER=none are development "
            "contracts only. Configure a real auth provider (AUTH_PROVIDER, "
            "SUPABASE_URL, KEYCLOAK_URL + KEYCLOAK_REALM, AUTH_JWKS_URL + "
            "AUTH_ISSUER, or MOZAIKS_OIDC_AUTHORITY).",
            500,
            "registry",
        )

    _ensure_builtin_adapters()
    registration = _adapter_registry.get(provider)

    return ResolvedAuthConfig(
        provider=provider,
        enabled=provider != "none",
        explicitly_disabled=explicitly_disabled,
        source=source,
        environment=environment,
        settings=settings,
        fingerprint=_fingerprint(provider=provider, settings=settings, registration=registration),
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
    true, and both reject in environments that forbid no-auth operation.
    """
    return resolve_auth_config().explicitly_disabled


def register_adapter(
    name: str,
    adapter_class: type[BaseAuthAdapter],
    *,
    config_identity: str | Callable[[], str] | None = None,
    constructor_mode: ConstructorMode | None = None,
) -> None:
    """
    Register an auth adapter.

    Args:
        name: Adapter identifier (e.g., "supabase", "keycloak")
        adapter_class: The adapter class to register
        config_identity: Cache-identity contract for custom adapters — a
            non-blank string, or a callable returning one, that changes
            whenever the adapter's configuration changes. Malformed identities
            (non-string values, or empty/whitespace-only strings) are rejected
            rather than coerced. Without a config_identity a custom adapter is
            never cached across resolutions (it is rebuilt every time), so a
            changed configuration can never silently reuse an adapter built
            under an older one.

        constructor_mode: Explicit constructor contract, required only when the
            constructor cannot be inspected (C extensions, exotic callables).
            ``"settings_keyword"`` means it accepts the configuration snapshot as
            ``settings=``; ``"no_settings"`` means it takes no canonical
            settings argument. When omitted, the contract is established by
            signature inspection, and registration fails rather than assuming a
            constructor takes no settings.

    Raises:
        AuthError: when a literal config_identity violates the contract, or the
            constructor contract cannot be positively established.

    Example:
        register_adapter("my-custom", MyCustomAdapter, config_identity=lambda: cfg.revision)
    """
    if config_identity is not None and not callable(config_identity):
        _validate_config_identity_value(config_identity, source="value")
    _register(
        name,
        adapter_class,
        builtin=False,
        config_identity=config_identity,
        constructor_mode=constructor_mode,
    )


def _register(
    name: str,
    adapter_class: type[BaseAuthAdapter],
    *,
    builtin: bool,
    config_identity: str | Callable[[], str] | None = None,
    constructor_mode: ConstructorMode | None = None,
) -> None:
    global _registry_generation
    mode = _classify_constructor(adapter_class, declared_mode=constructor_mode)
    _registry_generation += 1
    _adapter_registry[name.lower()] = _AdapterRegistration(
        adapter_class=adapter_class,
        generation=_registry_generation,
        builtin=builtin,
        constructor_mode=mode,
        config_identity=config_identity,
    )
    logger.debug(
        "Registered auth adapter: %s (builtin=%s, constructor_mode=%s)", name, builtin, mode
    )


def list_adapters() -> list[str]:
    """List all registered adapter names."""
    _ensure_builtin_adapters()
    return list(_adapter_registry.keys())


def _ensure_builtin_adapters() -> None:
    """Ensure every built-in adapter is registered.

    Registration is per-provider and idempotent rather than gated on the whole
    registry being empty: a custom adapter registered before the first auth
    resolution must not suppress the built-in providers. An intentional
    override of a built-in name is preserved.
    """
    if BUILTIN_PROVIDERS.issubset(_adapter_registry.keys()):
        return
    # Import here to avoid circular imports
    from mozaiksai.core.auth.adapters.jwt_adapter import GenericJWTAdapter
    from mozaiksai.core.auth.adapters.keycloak import KeycloakAuthAdapter
    from mozaiksai.core.auth.adapters.no_auth import NoAuthAdapter
    from mozaiksai.core.auth.adapters.supabase import SupabaseAuthAdapter

    for name, adapter_class in (
        ("none", NoAuthAdapter),
        ("jwt", GenericJWTAdapter),
        ("supabase", SupabaseAuthAdapter),
        ("keycloak", KeycloakAuthAdapter),
    ):
        if name not in _adapter_registry:
            _register(name, adapter_class, builtin=True)


def _build_adapter(
    provider: str,
    *,
    enabled: bool,
    settings: Mapping[str, str] | None,
) -> AuthAdapter:
    """Instantiate and validate the adapter for a resolved provider.

    The adapter is constructed from ``settings`` — the same immutable snapshot
    the cache fingerprint was derived from — so the adapter always matches its
    cache identity.
    """
    _ensure_builtin_adapters()

    registration = _adapter_registry.get(provider)
    if registration is None:
        available = ", ".join(_adapter_registry.keys())
        raise AuthError(
            f"Unknown auth provider: {provider}. Available: {available}",
            500,
            "registry",
        )

    # Constructor argument compatibility was decided at registration by
    # signature inspection. There is deliberately no "try with settings, catch
    # TypeError, retry without" path: a TypeError raised inside a constructor
    # body is a real construction failure and must fail closed, never silently
    # rebuild the adapter without its canonical configuration.
    try:
        if registration.constructor_mode == "settings_keyword":
            adapter = registration.adapter_class(settings=settings)  # type: ignore[call-arg]
        else:
            adapter = registration.adapter_class()
    except Exception as e:  # noqa: BLE001 - reported as AuthError below
        logger.error("Failed to instantiate %s adapter: %s", provider, e, exc_info=True)
        raise AuthError(f"Failed to configure {provider} auth: {e}", 500, provider) from e

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


def _is_cacheable(provider: str) -> bool:
    """Custom adapters without a declared config identity are never cached."""
    registration = _adapter_registry.get(provider)
    return registration is not None and registration.identity_token() is not None


def get_auth_adapter(force_provider: str | None = None) -> AuthAdapter:
    """
    Get the auth adapter for the canonical resolved configuration.

    The cached record (fingerprint + provider + adapter) is published
    atomically and keyed by the complete configuration identity for the
    resolved provider. When any meaning-bearing input changes — provider
    selection, any provider setting, environment, or adapter registration —
    the stale adapter is discarded and a fresh one is built from the current
    snapshot. Repeated initialization with unchanged configuration returns the
    same instance.

    Args:
        force_provider: Build an adapter for this provider name directly,
            bypassing resolution and the cache entirely (never cached).

    Returns:
        Configured AuthAdapter instance
    """
    global _adapter_cache

    if force_provider is not None:
        provider = force_provider.lower()
        return _build_adapter(
            provider,
            enabled=provider != "none",
            settings=_environment_snapshot(),
        )

    config = resolve_auth_config()

    cached = _adapter_cache  # single read of the atomically published record
    if cached is not None and cached.fingerprint == config.fingerprint:
        return cached.adapter

    # Configuration changed (or first resolution): rebuild coherently. The
    # AuthConfig value cache is refreshed first so any shared infrastructure
    # the adapter drives reads the same configuration generation.
    from mozaiksai.core.auth.config import clear_auth_config_cache

    if cached is not None:
        logger.info(
            "Auth configuration changed; rebuilding auth adapter (provider=%s)",
            config.provider,
        )
    clear_auth_config_cache()
    adapter = _build_adapter(config.provider, enabled=config.enabled, settings=config.settings)

    if _is_cacheable(config.provider):
        _adapter_cache = _AdapterCacheEntry(
            fingerprint=config.fingerprint,
            provider=config.provider,
            adapter=adapter,
        )
    else:
        # Custom provider with no declared configuration identity: never
        # cached, so a configuration change cannot reuse a stale adapter.
        _adapter_cache = None
    return adapter


def reset_auth_adapter() -> None:
    """
    Reset the cached auth adapter.

    Useful for testing or when configuration changes.
    """
    global _adapter_cache
    _adapter_cache = None
    logger.debug("Auth adapter cache cleared")


def validate_auth_provider_configuration() -> str:
    """Resolve and validate the auth configuration, failing closed.

    Returns the resolved provider name on success. Raises :class:`AuthError`
    when the configuration is invalid: enabled auth whose provider is missing,
    unknown, conflicting, or not fully configured; contradictory explicit
    declarations; conflicting environment declarations; or no-auth operation
    in an environment that does not permit it. Hosts call this at startup so
    misconfiguration aborts boot in every environment and every startup-check
    mode.

    On success the validated adapter is bound into the fingerprint-keyed cache
    — request-time resolution uses exactly the configuration startup validated.
    """
    config = resolve_auth_config()
    get_auth_adapter()
    return config.provider

"""Canonical parsing/normalization for auth cache TTL configuration.

``AUTH_JWKS_CACHE_TTL`` and ``AUTH_DISCOVERY_CACHE_TTL`` control how long a
JWKS or OIDC discovery document is reused before refetching. Every layer that
needs one — canonical auth resolution, the JWT adapter's immutable config, and
the ``AuthConfig`` compatibility surface — resolves it through
:func:`resolve_cache_ttl_setting`, so absent, empty, and whitespace-only values
all normalize identically and exactly once. Defaults live here only.

Contract:

- a base-10 integer number of seconds
- zero or greater; ``0`` means "always refetch" and is never treated as absent
- absent / empty / whitespace-only fall back to the canonical default for that
  setting
- anything else (non-integer text, negatives, floats) is rejected rather than
  coerced

Values are unbounded nonnegative integers. Cache expiry therefore compares
*elapsed* time against the TTL (``now - fetched_at > ttl``) instead of adding
the TTL to a float timestamp, so an arbitrarily large accepted TTL can never
raise ``OverflowError`` during request-time cache use. See
:func:`cache_entry_is_expired`.
"""

from __future__ import annotations

# Canonical defaults, matching the cache semantics documented in the clients:
# discovery documents rarely change (24h); signing keys rotate more often (1h).
DEFAULT_JWKS_CACHE_TTL_SECONDS = 3600
DEFAULT_DISCOVERY_CACHE_TTL_SECONDS = 86400

JWKS_CACHE_TTL_ENV = "AUTH_JWKS_CACHE_TTL"
DISCOVERY_CACHE_TTL_ENV = "AUTH_DISCOVERY_CACHE_TTL"

#: The one place a TTL setting's default is declared.
CACHE_TTL_DEFAULTS: dict[str, int] = {
    JWKS_CACHE_TTL_ENV: DEFAULT_JWKS_CACHE_TTL_SECONDS,
    DISCOVERY_CACHE_TTL_ENV: DEFAULT_DISCOVERY_CACHE_TTL_SECONDS,
}

__all__ = [
    "CACHE_TTL_DEFAULTS",
    "DEFAULT_DISCOVERY_CACHE_TTL_SECONDS",
    "DEFAULT_JWKS_CACHE_TTL_SECONDS",
    "DISCOVERY_CACHE_TTL_ENV",
    "JWKS_CACHE_TTL_ENV",
    "CacheTtlConfigError",
    "cache_entry_is_expired",
    "parse_cache_ttl_seconds",
    "resolve_cache_ttl_setting",
]


class CacheTtlConfigError(ValueError):
    """Raised when a cache TTL environment value is not a valid duration."""


def parse_cache_ttl_seconds(name: str, raw: str | None, *, default: int) -> int:
    """Parse one cache TTL value, failing closed on malformed input.

    Args:
        name: Environment variable name, used in the error message.
        raw: The configured value. Absent, empty, and whitespace-only all mean
            "not configured" and yield ``default``.
        default: Canonical default for this setting.

    Raises:
        CacheTtlConfigError: when the value is present but is not a base-10
            integer, or is negative.
    """
    text = (raw or "").strip()
    if not text:
        return default

    try:
        value = int(text, 10)
    except ValueError as exc:
        raise CacheTtlConfigError(
            f"{name} must be an integer number of seconds, got {text!r}."
        ) from exc

    if value < 0:
        raise CacheTtlConfigError(
            f"{name} must be zero or greater (0 means always refetch), got {value}."
        )
    return value


def resolve_cache_ttl_setting(name: str, raw: str | None) -> int:
    """Resolve a known TTL setting using its canonical default.

    ``name`` must be one of :data:`CACHE_TTL_DEFAULTS`. This is the entry point
    every layer uses, so a given raw value always produces the same number.
    """
    try:
        default = CACHE_TTL_DEFAULTS[name]
    except KeyError as exc:  # pragma: no cover - programming error
        raise CacheTtlConfigError(f"Unknown cache TTL setting: {name!r}") from exc
    return parse_cache_ttl_seconds(name, raw, default=default)


def cache_entry_is_expired(fetched_at: float, ttl_seconds: int, *, now: float) -> bool:
    """Return whether a cache entry fetched at ``fetched_at`` has expired.

    Mathematically equivalent to ``now > fetched_at + ttl_seconds`` but written
    as an elapsed-time comparison. Python compares a float against an
    arbitrary-precision int exactly, so this never converts a very large TTL to
    a float and never raises ``OverflowError`` — every TTL the parser accepts
    stays valid during real cache use.
    """
    return (now - fetched_at) > ttl_seconds

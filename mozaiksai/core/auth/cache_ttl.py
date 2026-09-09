"""Canonical parsing/validation for auth cache TTL configuration.

``AUTH_JWKS_CACHE_TTL`` and ``AUTH_DISCOVERY_CACHE_TTL`` control how long a
JWKS or OIDC discovery document is reused before refetching. They are parsed
once, during canonical auth configuration resolution, so a malformed value
fails startup instead of surfacing later during lazy client construction on a
request path.

Valid domain (matching the cache semantics in ``jwks.py`` / ``discovery.py``,
where a document expires once ``now > fetched_at + ttl_seconds``):

- a base-10 integer number of seconds
- zero or greater; ``0`` means "always refetch"

Negative values and non-integer strings are rejected rather than coerced.
"""

from __future__ import annotations

__all__ = ["CacheTtlConfigError", "parse_cache_ttl_seconds"]


class CacheTtlConfigError(ValueError):
    """Raised when a cache TTL environment value is not a valid duration."""


def parse_cache_ttl_seconds(name: str, raw: str | None, *, default: int | None = None) -> int:
    """Parse one cache TTL value, failing closed on malformed input.

    Args:
        name: Environment variable name, used in the error message.
        raw: The configured value. Empty/absent falls back to ``default``.
        default: Value to use when ``raw`` is empty or absent. Required when
            ``raw`` may be blank.

    Raises:
        CacheTtlConfigError: when the value is not a base-10 integer, or is
            negative.
    """
    text = (raw or "").strip()
    if not text:
        if default is None:
            raise CacheTtlConfigError(f"{name} must be set to an integer number of seconds.")
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

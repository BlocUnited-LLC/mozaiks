"""Keyed digest of an agent reply, for audit records.

A plain SHA-256 of a low-entropy reply (a yes/no, an enum member, a score level)
can be recovered by hashing every candidate value, so it would reveal the reply
it is meant to withhold. The digest here is an HMAC-SHA256, which cannot be
recovered without the key, but still lets two records for the same reply be
recognised as the same.

Configuration
-------------
``MOZAIKS_AUDIT_DIGEST_KEY``
    At least 32 characters, resolved through the app secret contract like any
    other secret. With it, digests are stable across restarts and processes.
    Without it (or if it is unusable) a random key is generated once per
    process: digests then compare only within that process, but are never
    recoverable by enumeration. There is no unkeyed mode.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from functools import cache
from typing import Any

from logs.logging_config import get_core_logger

logger = get_core_logger("structured_output_digest")

DIGEST_KEY_ENV = "MOZAIKS_AUDIT_DIGEST_KEY"
_MIN_KEY_CHARS = 32
# Domain separation: the configured secret is never used directly as the HMAC key.
_DOMAIN = b"mozaiks.audit.output-digest.v1"


def _configured_key() -> bytes | None:
    from mozaiksai.core.secrets import resolve_secret

    try:
        value = resolve_secret(DIGEST_KEY_ENV)
    except Exception as exc:  # unset, invalid contract or vault failure: never log the secret
        logger.debug("%s not available (%s); using a per-process digest key", DIGEST_KEY_ENV, type(exc).__name__)
        return None
    if len(value) < _MIN_KEY_CHARS:
        logger.warning(
            "%s is shorter than %d characters and is ignored; using a per-process digest key",
            DIGEST_KEY_ENV,
            _MIN_KEY_CHARS,
        )
        return None
    return value.encode("utf-8")


@cache
def _digest_key() -> bytes:
    master = _configured_key() or os.urandom(32)
    return hmac.new(master, _DOMAIN, hashlib.sha256).digest()


def reset_output_digest_key() -> None:
    """Forget the resolved key (tests, or after rotating the configured secret)."""

    _digest_key.cache_clear()


def output_digest(data: Any) -> str:
    """HMAC-SHA256 (hex) of the canonical JSON of ``data``; the content itself is not kept."""

    try:
        canonical = json.dumps(data, sort_keys=True, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        canonical = repr(data)
    return hmac.new(_digest_key(), canonical.encode("utf-8", "replace"), hashlib.sha256).hexdigest()


__all__ = ["DIGEST_KEY_ENV", "output_digest", "reset_output_digest_key"]

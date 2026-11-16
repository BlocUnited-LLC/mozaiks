"""Keyed digest of an agent reply, for audit records.

A plain SHA-256 of a low-entropy reply (a yes/no, an enum member, a score level)
can be recovered by hashing every candidate value, so it would reveal the reply
it is meant to withhold. The digest here is an HMAC-SHA256, which cannot be
recovered without the key, but still lets two records for the same reply be
recognised as the same.

Configuration
-------------
``MOZAIKS_AUDIT_DIGEST_KEY``
    Read directly from the process environment, not through the app secret
    resolver: that resolver can make a blocking Key Vault call, and the first
    rejection would pay for it before the audit timeout even starts. Use a
    randomly generated key of at least 32 characters, for example::

        python -c "import secrets; print(secrets.token_hex(32))"

    Digests are comparable across workers and restarts only if every process uses
    the same key. Without a key (or with one that is too short) a random key is
    generated once per process: digests then compare only within that process,
    but are never recoverable by enumeration. There is no unkeyed mode.
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
    value = os.getenv(DIGEST_KEY_ENV, "").strip()
    if not value:
        logger.debug("%s is not set; using a per-process digest key", DIGEST_KEY_ENV)
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
    """Forget the cached key (tests, or after changing the configured key)."""

    _digest_key.cache_clear()


def output_digest(data: Any) -> str:
    """HMAC-SHA256 (hex) of the canonical JSON of ``data``; the content itself is not kept."""

    try:
        canonical = json.dumps(data, sort_keys=True, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        canonical = repr(data)
    return hmac.new(_digest_key(), canonical.encode("utf-8", "replace"), hashlib.sha256).hexdigest()


__all__ = ["DIGEST_KEY_ENV", "output_digest", "reset_output_digest_key"]

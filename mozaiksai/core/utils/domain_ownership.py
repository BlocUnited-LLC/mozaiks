"""Canonical domain ownership challenge utilities.

Generates and names TXT DNS records used for proving control of a domain
before connecting it to a hosted app. The token format and challenge name
prefix are the canonical OSS standard — all Mozaiks hosted products that
support custom domain verification should use these helpers so that customer
DNS instructions are consistent across deployments.

Challenge record shape:
    Name:  ``_mozaiks-verify.{domain}``
    Value: ``mozaiks-site-verification={token}``
    Token: ``mzkv1_{url-safe base64}``
"""
from __future__ import annotations

import re
import secrets

_TOKEN_RE = re.compile(r"^mzkv1_[A-Za-z0-9_-]+$")
_CHALLENGE_PREFIX = "_mozaiks-verify"
_CHALLENGE_VALUE_PREFIX = "mozaiks-site-verification"


def generate_challenge_token() -> str:
    """Return a cryptographically random ownership challenge token.

    Format: ``mzkv1_{url-safe base64 string}``

    Raises:
        RuntimeError: if the underlying entropy source produces a token that
            does not match the expected format (defensive guard only).
    """
    token = f"mzkv1_{secrets.token_urlsafe(32)}"
    if not _TOKEN_RE.match(token):
        raise RuntimeError("Generated domain verification token was not base64url-safe.")
    return token


def challenge_name_for_domain(domain: str) -> str:
    """Return the TXT record *name* for the ownership challenge.

    Example::

        challenge_name_for_domain("example.com")
        # → "_mozaiks-verify.example.com"
    """
    normalized = (domain or "").strip().lower().rstrip(".")
    return f"{_CHALLENGE_PREFIX}.{normalized}"


def challenge_value_for_token(token: str) -> str:
    """Return the TXT record *value* for an ownership challenge token.

    Example::

        challenge_value_for_token("mzkv1_abc123")
        # → "mozaiks-site-verification=mzkv1_abc123"
    """
    return f"{_CHALLENGE_VALUE_PREFIX}={token}"


__all__ = [
    "generate_challenge_token",
    "challenge_name_for_domain",
    "challenge_value_for_token",
]

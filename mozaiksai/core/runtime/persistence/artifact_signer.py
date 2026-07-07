"""HMAC-SHA256 signing for promoted artifacts.

At promotion time, every artifact bundle is signed using a secret key from the
environment.  The signature is stored alongside the artifact version record and
can be verified before loading a bundle into the active runtime.

Configuration
-------------
``ARTIFACT_SIGNING_KEY``
    Hex-encoded 32-byte (64-char) signing key.  If absent or empty the signer
    operates in *unsigned* mode: ``sign_artifact`` returns an empty string and
    ``verify_artifact`` returns ``True`` (no-op so unsigned deployments remain
    functional).  Production deployments should always set this key.

Usage
-----
::

    from mozaiksai.core.runtime.persistence.artifact_signer import (
        sign_artifact,
        verify_artifact,
        assert_artifact_authentic,
        ArtifactSignatureError,
    )

    signature = sign_artifact(bundle_bytes)          # sign at promotion
    ok = verify_artifact(bundle_bytes, signature)    # verify before load
    assert_artifact_authentic(bundle_bytes, signature)  # raises on mismatch
"""

from __future__ import annotations

import hmac
import os

from logs.logging_config import get_core_logger

logger = get_core_logger("artifact_signer")

SIGNING_KEY_ENV = "ARTIFACT_SIGNING_KEY"
_ALGORITHM = "sha256"


class ArtifactSignatureError(Exception):
    """Raised when an artifact signature does not match the expected value."""


def _load_signing_key() -> bytes | None:
    """Return the raw signing key bytes, or None if not configured."""
    raw = os.getenv(SIGNING_KEY_ENV, "").strip()
    if not raw:
        return None
    try:
        return bytes.fromhex(raw)
    except ValueError:
        logger.error(
            "[ARTIFACT_SIGNER] %s is not valid hex — signing disabled",
            SIGNING_KEY_ENV,
        )
        return None


def sign_artifact(content: bytes) -> str:
    """Return an HMAC-SHA256 hex signature for *content*.

    Returns an empty string when ``ARTIFACT_SIGNING_KEY`` is not set.
    The empty string is the canonical sentinel for *unsigned*.
    """
    key = _load_signing_key()
    if key is None:
        logger.debug("[ARTIFACT_SIGNER] No signing key configured — artifact not signed")
        return ""
    mac = hmac.new(key, content, _ALGORITHM)
    signature = mac.hexdigest()
    logger.debug("[ARTIFACT_SIGNER] Signed %d bytes → %s…", len(content), signature[:16])
    return signature


def verify_artifact(content: bytes, signature: str) -> bool:
    """Return True if *signature* matches the HMAC-SHA256 of *content*.

    When ``ARTIFACT_SIGNING_KEY`` is not set **and** *signature* is empty the
    function returns ``True`` — both sides agree the artifact is unsigned.

    When a key is configured, an empty or mismatched *signature* returns
    ``False``.  The comparison is constant-time to prevent timing attacks.
    """
    key = _load_signing_key()
    if key is None:
        # Unsigned mode: any signature (including empty) passes.
        return True
    if not signature:
        logger.warning("[ARTIFACT_SIGNER] Artifact has no signature but a signing key is configured")
        return False
    try:
        expected = hmac.new(key, content, _ALGORITHM).hexdigest()
    except Exception as exc:
        logger.error("[ARTIFACT_SIGNER] Signature computation failed: %s", exc)
        return False
    result = hmac.compare_digest(expected, signature)
    if not result:
        logger.warning(
            "[ARTIFACT_SIGNER] Signature mismatch — expected %s… got %s…",
            expected[:16],
            signature[:16],
        )
    return result


def assert_artifact_authentic(content: bytes, signature: str) -> None:
    """Verify *signature* and raise :class:`ArtifactSignatureError` on failure.

    Callers that need a hard stop (e.g. runtime loading before execution) should
    use this rather than checking the bool themselves.
    """
    if not verify_artifact(content, signature):
        raise ArtifactSignatureError(
            "Artifact failed signature verification — content may have been tampered with."
        )


__all__ = [
    "SIGNING_KEY_ENV",
    "ArtifactSignatureError",
    "assert_artifact_authentic",
    "sign_artifact",
    "verify_artifact",
]

"""Tests for mozaiksai.core.runtime.persistence.artifact_signer."""

from __future__ import annotations

import pytest

from mozaiksai.core.runtime.persistence.artifact_signer import (
    SIGNING_KEY_ENV,
    ArtifactSignatureError,
    assert_artifact_authentic,
    sign_artifact,
    verify_artifact,
)

_VALID_KEY_HEX = "a" * 64  # 32 zero-like bytes, valid hex


# ---------------------------------------------------------------------------
# Unsigned mode (no key configured)
# ---------------------------------------------------------------------------


class TestUnsignedMode:
    def test_sign_returns_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)
        assert sign_artifact(b"hello world") == ""

    def test_verify_empty_signature_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)
        assert verify_artifact(b"hello world", "") is True

    def test_verify_nonempty_signature_also_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # In unsigned mode any signature is accepted (signed artifacts can still
        # be deployed to an environment that hasn't configured a key yet).
        monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)
        assert verify_artifact(b"hello world", "deadbeef") is True

    def test_assert_authentic_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)
        assert_artifact_authentic(b"hello world", "")  # must not raise


# ---------------------------------------------------------------------------
# Signed mode (key configured)
# ---------------------------------------------------------------------------


class TestSignedMode:
    def test_sign_verify_round_trip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(SIGNING_KEY_ENV, _VALID_KEY_HEX)
        content = b"app bundle bytes"
        sig = sign_artifact(content)
        assert sig != ""
        assert verify_artifact(content, sig) is True

    def test_sign_produces_hex_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(SIGNING_KEY_ENV, _VALID_KEY_HEX)
        sig = sign_artifact(b"data")
        assert all(c in "0123456789abcdef" for c in sig)
        assert len(sig) == 64  # HMAC-SHA256 produces a 32-byte / 64-char hex digest

    def test_tampered_content_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(SIGNING_KEY_ENV, _VALID_KEY_HEX)
        original = b"original content"
        sig = sign_artifact(original)
        tampered = b"tampered content"
        assert verify_artifact(tampered, sig) is False

    def test_wrong_signature_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(SIGNING_KEY_ENV, _VALID_KEY_HEX)
        content = b"some artifact"
        assert verify_artifact(content, "00" * 32) is False

    def test_empty_signature_fails_in_signed_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(SIGNING_KEY_ENV, _VALID_KEY_HEX)
        assert verify_artifact(b"data", "") is False

    def test_different_keys_produce_different_signatures(self, monkeypatch: pytest.MonkeyPatch) -> None:
        content = b"artifact"
        monkeypatch.setenv(SIGNING_KEY_ENV, "a" * 64)
        sig_a = sign_artifact(content)
        monkeypatch.setenv(SIGNING_KEY_ENV, "b" * 64)
        sig_b = sign_artifact(content)
        assert sig_a != sig_b

    def test_wrong_key_fails_verification(self, monkeypatch: pytest.MonkeyPatch) -> None:
        content = b"artifact"
        monkeypatch.setenv(SIGNING_KEY_ENV, "a" * 64)
        sig = sign_artifact(content)
        monkeypatch.setenv(SIGNING_KEY_ENV, "b" * 64)
        assert verify_artifact(content, sig) is False

    def test_assert_authentic_raises_on_mismatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(SIGNING_KEY_ENV, _VALID_KEY_HEX)
        content = b"real artifact"
        sig = sign_artifact(content)
        with pytest.raises(ArtifactSignatureError):
            assert_artifact_authentic(b"corrupted artifact", sig)

    def test_assert_authentic_passes_on_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(SIGNING_KEY_ENV, _VALID_KEY_HEX)
        content = b"valid artifact"
        sig = sign_artifact(content)
        assert_artifact_authentic(content, sig)  # must not raise

    def test_empty_content_round_trip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(SIGNING_KEY_ENV, _VALID_KEY_HEX)
        sig = sign_artifact(b"")
        assert verify_artifact(b"", sig) is True
        assert verify_artifact(b"notempty", sig) is False

    def test_large_content_round_trip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(SIGNING_KEY_ENV, _VALID_KEY_HEX)
        content = b"x" * (1024 * 1024)  # 1 MiB
        sig = sign_artifact(content)
        assert verify_artifact(content, sig) is True
        assert verify_artifact(content + b"\x00", sig) is False


# ---------------------------------------------------------------------------
# Invalid key configuration
# ---------------------------------------------------------------------------


class TestInvalidKeyConfig:
    def test_non_hex_key_degrades_to_unsigned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(SIGNING_KEY_ENV, "not-valid-hex!")
        # Falls back to unsigned mode: sign returns ""
        assert sign_artifact(b"data") == ""
        # and verify passes (unsigned passthrough)
        assert verify_artifact(b"data", "") is True

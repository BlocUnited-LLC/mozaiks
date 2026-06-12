"""
Pure helper unit tests for:
  mozaiksai/core/telemetry.py

Covers the single sync pure helper with no env dependency:

  _anonymize_build_id:
    - non-empty string → 16-char hex hash
    - same input → same output (deterministic)
    - different inputs → different outputs
    - empty string → deterministic hex hash
    - hash is exactly 16 hex chars
"""
from __future__ import annotations

import hashlib

from mozaiksai.core.telemetry import _anonymize_build_id


class TestAnonymizeBuildId:
    def test_returns_16_char_hex(self):
        result = _anonymize_build_id("build-abc-123")
        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic_for_same_input(self):
        assert _anonymize_build_id("build-abc") == _anonymize_build_id("build-abc")

    def test_different_inputs_produce_different_hashes(self):
        assert _anonymize_build_id("build-1") != _anonymize_build_id("build-2")

    def test_empty_string_returns_hex_hash(self):
        result = _anonymize_build_id("")
        assert len(result) == 16

    def test_matches_manual_sha256_prefix(self):
        raw = "my-build-id"
        expected = hashlib.sha256(raw.encode()).hexdigest()[:16]
        assert _anonymize_build_id(raw) == expected

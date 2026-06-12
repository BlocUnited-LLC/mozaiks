"""
Pure helper unit tests for:
  mozaiksai/control_plane/staged_coding_worker.py

Covers:

  _reason_text:
    - None → ""
    - empty string → ""
    - whitespace-only → ""
    - normal text ≤ 160 chars → returned as-is (normalized)
    - extra whitespace collapsed
    - text > 160 chars → truncated at 157 + "..."
    - "secret" in text → "" (redacted)
    - "password" in text → "" (redacted)
    - "api_key" in text → "" (redacted)
    - "apikey" in text → "" (redacted)
    - "token" in text → "" (redacted)
    - "credential" in text → "" (redacted)
    - secret term case-insensitive → redacted

  _coerce_worker_result:
    - StagedCodingWorkerResult instance → returned unchanged
    - dict → model_validate returns StagedCodingWorkerResult
"""
from __future__ import annotations

from mozaiksai.control_plane.staged_coding_worker import (
    StagedCodingWorkerResult,
    _coerce_worker_result,
    _reason_text,
)

# ---------------------------------------------------------------------------
# 1. _reason_text
# ---------------------------------------------------------------------------

class TestReasonText:
    def test_none_returns_empty(self):
        assert _reason_text(None) == ""

    def test_empty_string_returns_empty(self):
        assert _reason_text("") == ""

    def test_whitespace_only_returns_empty(self):
        assert _reason_text("   ") == ""

    def test_normal_short_text_returned(self):
        assert _reason_text("Added billing module") == "Added billing module"

    def test_extra_whitespace_collapsed(self):
        assert _reason_text("Added  billing   module") == "Added billing module"

    def test_leading_trailing_whitespace_stripped(self):
        assert _reason_text("  Added billing module  ") == "Added billing module"

    def test_text_exactly_160_chars_not_truncated(self):
        text = "A" * 160
        assert _reason_text(text) == text

    def test_text_161_chars_truncated_with_ellipsis(self):
        text = "A" * 161
        result = _reason_text(text)
        assert result.endswith("...")
        assert len(result) == 160

    def test_long_text_truncated_at_157_plus_ellipsis(self):
        text = "word " * 50  # 250 chars
        result = _reason_text(text)
        assert len(result) <= 160
        assert result.endswith("...")

    def test_secret_in_text_returns_empty(self):
        assert _reason_text("Set the secret key") == ""

    def test_password_in_text_returns_empty(self):
        assert _reason_text("Updated password logic") == ""

    def test_api_key_in_text_returns_empty(self):
        assert _reason_text("Rotated api_key value") == ""

    def test_apikey_in_text_returns_empty(self):
        assert _reason_text("Set apikey for service") == ""

    def test_token_in_text_returns_empty(self):
        assert _reason_text("Generated new token") == ""

    def test_credential_in_text_returns_empty(self):
        assert _reason_text("Updated credential store") == ""

    def test_secret_term_case_insensitive(self):
        assert _reason_text("Updated SECRET field") == ""
        assert _reason_text("Rotated TOKEN value") == ""

    def test_unrelated_text_not_redacted(self):
        result = _reason_text("Refactored billing module to support multi-currency")
        assert result != ""
        assert "billing" in result


# ---------------------------------------------------------------------------
# 2. _coerce_worker_result
# ---------------------------------------------------------------------------

class TestCoerceWorkerResult:
    def test_instance_returned_unchanged(self):
        result = StagedCodingWorkerResult(request_id="req-1")
        coerced = _coerce_worker_result(result)
        assert coerced is result

    def test_dict_returns_staged_coding_worker_result(self):
        d = {"request_id": "req-1"}
        result = _coerce_worker_result(d)
        assert isinstance(result, StagedCodingWorkerResult)
        assert result.request_id == "req-1"

    def test_dict_with_warnings_preserved(self):
        d = {"request_id": "req-1", "warnings": ["something"]}
        result = _coerce_worker_result(d)
        assert result.warnings == ["something"]

    def test_dict_defaults_applied(self):
        result = _coerce_worker_result({"request_id": "req-1"})
        assert result.source == "deterministic"
        assert result.changes == []

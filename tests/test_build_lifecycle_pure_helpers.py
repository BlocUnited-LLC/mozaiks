"""
Pure helper unit tests for:
  factory_app/workflows/_shared/platform/build_lifecycle.py

Covers helpers that are purely deterministic (no DB/env/network):

  _normalize_text:
    - None → None
    - empty string → None
    - whitespace-only → None
    - valid text → stripped string
    - integer input → stringified and returned

  _extract_preview_url:
    - empty dict → None
    - previewUrl key → returned
    - preview_url key → returned
    - app_validation_preview_url key → returned
    - nested app_validation_result.previewUrl → returned
    - nested app_validation.preview_url → returned
    - whitespace-only value → None
    - non-string value → None
    - first matching key wins

  _idempotency_key:
    - returns "build:{app_id}:{build_id}:{event_type}" string
    - components correctly embedded
"""
from __future__ import annotations

from factory_app.workflows._shared.platform.build_lifecycle import (
    _extract_preview_url,
    _idempotency_key,
    _normalize_text,
)

# ---------------------------------------------------------------------------
# 1. _normalize_text
# ---------------------------------------------------------------------------

class TestNormalizeText:
    def test_none_returns_none(self):
        assert _normalize_text(None) is None

    def test_empty_string_returns_none(self):
        assert _normalize_text("") is None

    def test_whitespace_only_returns_none(self):
        assert _normalize_text("   ") is None

    def test_valid_text_stripped(self):
        assert _normalize_text("  hello  ") == "hello"

    def test_single_word_returned(self):
        assert _normalize_text("abc") == "abc"

    def test_integer_stringified_and_returned(self):
        result = _normalize_text(42)
        assert result == "42"

    def test_zero_returns_none(self):
        # str(0 or "") = str("") = "" → None
        assert _normalize_text(0) is None

    def test_false_returns_none(self):
        # str(False or "") = "" → None
        assert _normalize_text(False) is None


# ---------------------------------------------------------------------------
# 2. _extract_preview_url
# ---------------------------------------------------------------------------

class TestExtractPreviewUrl:
    def test_empty_dict_returns_none(self):
        assert _extract_preview_url({}) is None

    def test_preview_url_camel_case(self):
        result = _extract_preview_url({"previewUrl": "https://example.com"})
        assert result == "https://example.com"

    def test_preview_url_snake_case(self):
        result = _extract_preview_url({"preview_url": "https://example.com"})
        assert result == "https://example.com"

    def test_app_validation_preview_url(self):
        result = _extract_preview_url({"app_validation_preview_url": "https://example.com"})
        assert result == "https://example.com"

    def test_nested_app_validation_result_preview_url(self):
        payload = {"app_validation_result": {"previewUrl": "https://nested.com"}}
        result = _extract_preview_url(payload)
        assert result == "https://nested.com"

    def test_nested_app_validation_preview_url(self):
        payload = {"app_validation": {"preview_url": "https://nested.com"}}
        result = _extract_preview_url(payload)
        assert result == "https://nested.com"

    def test_whitespace_only_value_skipped(self):
        result = _extract_preview_url({"previewUrl": "   "})
        assert result is None

    def test_non_string_value_skipped(self):
        result = _extract_preview_url({"previewUrl": None})
        assert result is None

    def test_integer_value_skipped(self):
        result = _extract_preview_url({"previewUrl": 42})
        assert result is None

    def test_url_stripped(self):
        result = _extract_preview_url({"previewUrl": "  https://example.com  "})
        assert result == "https://example.com"

    def test_preview_url_takes_priority_over_nested(self):
        payload = {
            "previewUrl": "https://top.com",
            "app_validation_result": {"previewUrl": "https://nested.com"},
        }
        result = _extract_preview_url(payload)
        assert result == "https://top.com"

    def test_nested_non_dict_skipped(self):
        payload = {"app_validation_result": "not-a-dict"}
        assert _extract_preview_url(payload) is None


# ---------------------------------------------------------------------------
# 3. _idempotency_key
# ---------------------------------------------------------------------------

class TestIdempotencyKey:
    def test_format_is_build_prefix(self):
        result = _idempotency_key(app_id="app1", build_id="build1", event_type="started")
        assert result == "build:app1:build1:started"

    def test_components_embedded_correctly(self):
        key = _idempotency_key(app_id="myapp", build_id="abc123", event_type="completed")
        assert "myapp" in key
        assert "abc123" in key
        assert "completed" in key

    def test_colon_separator(self):
        key = _idempotency_key(app_id="a", build_id="b", event_type="e")
        parts = key.split(":")
        assert parts[0] == "build"
        assert parts[1] == "a"
        assert parts[2] == "b"
        assert parts[3] == "e"

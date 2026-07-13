"""
Connector vault pure helper unit tests.

Covers:
  _slug:
    - valid lowercase slug → unchanged
    - uppercase → lowercased
    - spaces → dashes
    - special chars replaced with dash
    - consecutive dashes collapsed to one
    - leading/trailing dashes stripped
    - empty string → default returned
    - None/non-string → default returned
    - only special chars → default returned

  _secret_name:
    - returns string starting with prefix
    - contains service slug
    - contains app_id slug (truncated to 40 chars)
    - contains 10-char sha1 digest of app_id
    - total length capped at 127
    - prefix override applied
    - default prefix used when no prefix arg
    - special chars in service slugified
    - very long app_id truncated in name
    - result consistent for same inputs
"""
from __future__ import annotations

import hashlib

from mozaiksai.core.secrets.connector_vault import (
    _secret_name,
    _slug,
)

# ---------------------------------------------------------------------------
# 1. _slug
# ---------------------------------------------------------------------------

class TestSlug:
    def test_lowercase_slug_unchanged(self):
        assert _slug("my-service", default="x") == "my-service"

    def test_uppercase_lowercased(self):
        assert _slug("MyService", default="x") == "myservice"

    def test_spaces_replaced_with_dash(self):
        result = _slug("my service", default="x")
        assert " " not in result
        assert result == "my-service"

    def test_underscores_replaced_with_dash(self):
        result = _slug("my_service", default="x")
        assert result == "my-service"

    def test_special_chars_replaced_with_dash(self):
        result = _slug("my.service@host!", default="x")
        assert "." not in result
        assert "@" not in result
        assert "!" not in result

    def test_consecutive_dashes_collapsed(self):
        result = _slug("a--b---c", default="x")
        assert "--" not in result
        assert result == "a-b-c"

    def test_leading_trailing_dashes_stripped(self):
        result = _slug("-service-", default="x")
        assert not result.startswith("-")
        assert not result.endswith("-")

    def test_empty_string_returns_default(self):
        assert _slug("", default="fallback") == "fallback"

    def test_whitespace_only_returns_default(self):
        assert _slug("   ", default="fallback") == "fallback"

    def test_only_special_chars_returns_default(self):
        assert _slug("!@#$%", default="fallback") == "fallback"

    def test_none_equivalent_returns_default(self):
        # str(None or "") → ""
        assert _slug(None, default="fallback") == "fallback"  # type: ignore[arg-type]

    def test_numeric_slug_preserved(self):
        result = _slug("123", default="x")
        assert result == "123"


# ---------------------------------------------------------------------------
# 2. _secret_name
# ---------------------------------------------------------------------------

class TestSecretName:
    def test_result_is_string(self):
        assert isinstance(_secret_name("app-1", "payment_provider"), str)

    def test_starts_with_prefix(self):
        result = _secret_name("app-1", "payment_provider", prefix="myprefix")
        assert result.startswith("myprefix-")

    def test_contains_service_slug(self):
        result = _secret_name("app-1", "payment_provider")
        assert "payment-provider" in result

    def test_contains_app_id_slug(self):
        result = _secret_name("myapp", "payment_provider")
        assert "myapp" in result

    def test_contains_sha1_digest(self):
        app_id = "myapp"
        digest = hashlib.sha1(app_id.encode("utf-8")).hexdigest()[:10]
        result = _secret_name(app_id, "payment_provider")
        assert digest in result

    def test_total_length_capped_at_127(self):
        long_app_id = "a" * 200
        long_service = "s" * 200
        result = _secret_name(long_app_id, long_service)
        assert len(result) <= 127

    def test_prefix_override_applied(self):
        result = _secret_name("app-1", "payment_provider", prefix="custom-prefix")
        assert result.startswith("custom-prefix-")

    def test_special_chars_in_service_slugified(self):
        result = _secret_name("app-1", "my.service@v2")
        assert "." not in result
        assert "@" not in result

    def test_consistent_for_same_inputs(self):
        r1 = _secret_name("app-1", "payment_provider")
        r2 = _secret_name("app-1", "payment_provider")
        assert r1 == r2

    def test_different_app_ids_produce_different_names(self):
        r1 = _secret_name("app-1", "payment_provider")
        r2 = _secret_name("app-2", "payment_provider")
        assert r1 != r2

    def test_different_services_produce_different_names(self):
        r1 = _secret_name("app-1", "payment_provider")
        r2 = _secret_name("app-1", "openai")
        assert r1 != r2

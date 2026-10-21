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
    - includes an explicit app or workspace scope
    - contains service slug
    - contains scope_id slug (bounded)
    - ends with an untruncated digest of the complete canonical identity
    - total length capped at 127
    - prefix override applied
    - default prefix used when no prefix arg
    - special chars in service slugified
    - very long app_id truncated in name
    - result consistent for same inputs
"""
from __future__ import annotations

import hashlib
import json

import pytest

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
        assert isinstance(_secret_name("app", "app-1", "payment_provider"), str)

    def test_starts_with_prefix(self):
        result = _secret_name("app", "app-1", "payment_provider", prefix="myprefix")
        assert result.startswith("myprefix-")

    def test_contains_service_slug(self):
        result = _secret_name("app", "app-1", "payment_provider")
        assert "payment-provider" in result

    def test_contains_app_id_slug(self):
        result = _secret_name("app", "myapp", "payment_provider")
        assert "myapp" in result

    def test_contains_full_identity_digest(self, monkeypatch):
        monkeypatch.setenv("MOZAIKS_CONNECTOR_SECRET_PREFIX", "mozaiks-connector")
        app_id = "myapp"
        digest = hashlib.sha256(json.dumps(["mozaiks-connector", "app", app_id, "payment_provider"], separators=(",", ":")).encode()).hexdigest()[:24]
        result = _secret_name("app", app_id, "payment_provider")
        assert digest in result

    def test_total_length_capped_at_127(self):
        long_app_id = "a" * 200
        long_service = "s" * 200
        result = _secret_name("app", long_app_id, long_service)
        assert len(result) <= 127

    def test_prefix_override_applied(self):
        result = _secret_name("app", "app-1", "payment_provider", prefix="custom-prefix")
        assert result.startswith("custom-prefix-")

    def test_special_chars_in_service_slugified(self):
        result = _secret_name("app", "app-1", "my.service@v2")
        assert "." not in result
        assert "@" not in result

    def test_consistent_for_same_inputs(self):
        r1 = _secret_name("app", "app-1", "payment_provider")
        r2 = _secret_name("app", "app-1", "payment_provider")
        assert r1 == r2

    def test_different_app_ids_produce_different_names(self):
        r1 = _secret_name("app", "app-1", "payment_provider")
        r2 = _secret_name("app", "app-2", "payment_provider")
        assert r1 != r2

    def test_different_services_produce_different_names(self):
        r1 = _secret_name("app", "app-1", "payment_provider")
        r2 = _secret_name("app", "app-1", "openai")
        assert r1 != r2

    def test_scope_and_service_aliases_have_distinct_names(self):
        assert _secret_name("app", "same", "foo_bar") != _secret_name("workspace", "same", "foo_bar")
        assert _secret_name("app", "same", "foo_bar") != _secret_name("app", "same", "foo-bar")

    def test_long_name_keeps_full_digest(self):
        scope_id = "id" * 200
        service = "service" * 80
        identity = json.dumps(["prefix" * 100, "workspace", scope_id, service], separators=(",", ":"))
        digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
        result = _secret_name("workspace", scope_id, service, prefix="prefix" * 100)
        assert len(result) <= 127
        assert result.endswith(digest)
        assert result.startswith("prefix")

    def test_long_prefixes_with_same_visible_start_have_distinct_names(self):
        first = _secret_name("app", "same-id", "billing", prefix="mozaiks-connector-customer-a")
        second = _secret_name("app", "same-id", "billing", prefix="mozaiks-connector-customer-b")
        assert first != second
        assert first[:20] == second[:20]
        assert len(first) <= 127
        assert len(second) <= 127

    @pytest.mark.parametrize("scope,scope_id,service", [
        ("tenant", "id", "service"),
        ("", "id", "service"),
        ("app", "", "service"),
        ("app", "id", ""),
    ])
    def test_invalid_identity_is_rejected(self, scope, scope_id, service):
        with pytest.raises(ValueError):
            _secret_name(scope, scope_id, service)

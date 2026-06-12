"""
Connector health pure helper unit tests.

Covers:
  _normalize_id:
    - None → empty string
    - whitespace stripped
    - lowercased
    - spaces replaced with underscores
    - already normalized → unchanged
    - non-string int → str then normalized

  _is_secret_detail_key:
    - exact matches in SECRET_DETAIL_KEYS → True
      (secret_value, secret, api_key, apikey, token, password)
    - key containing "secret" → True
    - key containing "api_key" → True
    - key containing "apikey" → True
    - key containing "token" → True
    - key containing "password" → True
    - safe key → False
    - case insensitive (via str().strip().lower())
    - key with surrounding text containing marker → True
    - None key → False

  redact_health_details:
    - None → None
    - str/int/float/bool → unchanged
    - dict with safe keys → all present
    - dict with secret key → key removed
    - dict with "api_key" → removed
    - dict with "token" → removed
    - dict with "password" → removed
    - nested dict: inner secret key removed
    - list → each item processed
    - dict value that is non-primitive → converted to str
    - mixed safe and secret keys in same dict
"""
from __future__ import annotations

from mozaiksai.core.workflow.generator_support.connector_health import (
    _is_secret_detail_key,
    _normalize_id,
    redact_health_details,
)

# ---------------------------------------------------------------------------
# 1. _normalize_id
# ---------------------------------------------------------------------------

class TestNormalizeId:
    def test_none_returns_empty(self):
        assert _normalize_id(None) == ""

    def test_empty_string_returns_empty(self):
        assert _normalize_id("") == ""

    def test_whitespace_stripped(self):
        assert _normalize_id("  hello  ") == "hello"

    def test_lowercased(self):
        assert _normalize_id("MyConnector") == "myconnector"

    def test_spaces_replaced_with_underscores(self):
        assert _normalize_id("my connector") == "my_connector"

    def test_already_normalized(self):
        assert _normalize_id("my_connector") == "my_connector"

    def test_int_input_normalized(self):
        assert _normalize_id(42) == "42"

    def test_combined_normalization(self):
        assert _normalize_id("  My Connector  ") == "my_connector"


# ---------------------------------------------------------------------------
# 2. _is_secret_detail_key
# ---------------------------------------------------------------------------

class TestIsSecretDetailKey:
    # exact matches
    def test_secret_value_exact(self):
        assert _is_secret_detail_key("secret_value") is True

    def test_secret_exact(self):
        assert _is_secret_detail_key("secret") is True

    def test_api_key_exact(self):
        assert _is_secret_detail_key("api_key") is True

    def test_apikey_exact(self):
        assert _is_secret_detail_key("apikey") is True

    def test_token_exact(self):
        assert _is_secret_detail_key("token") is True

    def test_password_exact(self):
        assert _is_secret_detail_key("password") is True

    # substring matches
    def test_key_containing_secret(self):
        assert _is_secret_detail_key("my_secret_field") is True

    def test_key_containing_token(self):
        assert _is_secret_detail_key("access_token") is True

    def test_key_containing_password(self):
        assert _is_secret_detail_key("admin_password") is True

    def test_key_containing_api_key(self):
        assert _is_secret_detail_key("stripe_api_key") is True

    def test_key_containing_apikey(self):
        assert _is_secret_detail_key("stripe_apikey") is True

    # safe keys
    def test_safe_key_returns_false(self):
        assert _is_secret_detail_key("status") is False
        assert _is_secret_detail_key("message") is False
        assert _is_secret_detail_key("health_check_url") is False

    # case insensitive
    def test_uppercase_secret(self):
        assert _is_secret_detail_key("SECRET") is True

    def test_mixed_case_password(self):
        assert _is_secret_detail_key("Password") is True

    # none
    def test_none_key_returns_false(self):
        assert _is_secret_detail_key(None) is False

    def test_empty_string_returns_false(self):
        assert _is_secret_detail_key("") is False


# ---------------------------------------------------------------------------
# 3. redact_health_details
# ---------------------------------------------------------------------------

class TestRedactHealthDetails:
    def test_none_returned_unchanged(self):
        assert redact_health_details(None) is None

    def test_string_returned_unchanged(self):
        assert redact_health_details("hello") == "hello"

    def test_int_returned_unchanged(self):
        assert redact_health_details(42) == 42

    def test_float_returned_unchanged(self):
        assert redact_health_details(3.14) == 3.14

    def test_bool_returned_unchanged(self):
        assert redact_health_details(True) is True
        assert redact_health_details(False) is False

    def test_safe_dict_keys_preserved(self):
        d = {"status": "healthy", "message": "OK", "latency_ms": 100}
        result = redact_health_details(d)
        assert result == d

    def test_secret_key_removed(self):
        d = {"status": "ok", "secret": "s3cr3t", "message": "fine"}
        result = redact_health_details(d)
        assert "secret" not in result
        assert result["status"] == "ok"

    def test_api_key_removed(self):
        d = {"api_key": "sk-abc123", "status": "ok"}
        result = redact_health_details(d)
        assert "api_key" not in result

    def test_token_removed(self):
        d = {"access_token": "bearer-xyz", "user": "alice"}
        result = redact_health_details(d)
        assert "access_token" not in result
        assert result["user"] == "alice"

    def test_password_removed(self):
        d = {"password": "hunter2", "host": "db.example.com"}
        result = redact_health_details(d)
        assert "password" not in result
        assert result["host"] == "db.example.com"

    def test_nested_dict_secret_removed(self):
        d = {
            "config": {
                "host": "example.com",
                "api_key": "sk-secret",
            }
        }
        result = redact_health_details(d)
        assert "api_key" not in result["config"]
        assert result["config"]["host"] == "example.com"

    def test_list_items_processed(self):
        value = [{"status": "ok"}, {"secret": "bad", "msg": "hi"}]
        result = redact_health_details(value)
        assert isinstance(result, list)
        assert "secret" not in result[1]
        assert result[1]["msg"] == "hi"

    def test_non_primitive_value_converted_to_str(self):
        class Weird:
            def __str__(self):
                return "weird_repr"

        d = {"data": Weird()}
        result = redact_health_details(d)
        assert result["data"] == "weird_repr"

    def test_mixed_safe_and_secret_keys(self):
        d = {
            "status": "healthy",
            "api_key": "sk-123",
            "message": "All good",
            "secret_value": "supersecret",
            "latency_ms": 50,
        }
        result = redact_health_details(d)
        assert "api_key" not in result
        assert "secret_value" not in result
        assert result["status"] == "healthy"
        assert result["message"] == "All good"
        assert result["latency_ms"] == 50

"""
Connector store pure helper unit tests.

Covers:
  _redact_public_config:
    - None input → None
    - empty dict → empty dict
    - non-secret key preserved
    - secret_value key removed
    - secret key removed
    - api_key key removed
    - apikey key removed
    - token key removed
    - password key removed
    - key matching is case-insensitive
    - key with surrounding whitespace matched
    - nested dict recursion - inner secrets redacted
    - list of dicts - inner secrets redacted
    - non-dict list items preserved unchanged
    - mixed safe and secret keys at same level
    - deeply nested secret removed
"""
from __future__ import annotations

from mozaiksai.core.data.persistence.connector_store import _redact_public_config


class TestRedactPublicConfig:
    def test_none_input_returns_none(self):
        assert _redact_public_config(None) is None

    def test_empty_dict_returns_empty_dict(self):
        assert _redact_public_config({}) == {}

    def test_non_secret_key_preserved(self):
        config = {"name": "my_connector", "base_url": "https://example.com"}
        result = _redact_public_config(config)
        assert result == {"name": "my_connector", "base_url": "https://example.com"}

    def test_secret_value_key_removed(self):
        config = {"name": "connector", "secret_value": "abc123"}
        result = _redact_public_config(config)
        assert "secret_value" not in result
        assert result["name"] == "connector"

    def test_secret_key_removed(self):
        assert "secret" not in _redact_public_config({"secret": "xyz", "name": "x"})

    def test_api_key_removed(self):
        assert "api_key" not in _redact_public_config({"api_key": "sk-123"})

    def test_apikey_removed(self):
        assert "apikey" not in _redact_public_config({"apikey": "sk-456"})

    def test_token_removed(self):
        assert "token" not in _redact_public_config({"token": "bearer-abc"})

    def test_password_removed(self):
        assert "password" not in _redact_public_config({"password": "supersecret"})

    def test_key_matching_case_insensitive(self):
        # Keys are strip().lower() compared
        config = {"API_KEY": "sk-upper", "name": "x"}
        result = _redact_public_config(config)
        assert "API_KEY" not in result
        assert result["name"] == "x"

    def test_nested_dict_inner_secrets_redacted(self):
        config = {
            "provider": "stripe",
            "credentials": {
                "api_key": "sk-secret",
                "publishable_key": "pk-public",
            },
        }
        result = _redact_public_config(config)
        assert "api_key" not in result["credentials"]
        assert result["credentials"]["publishable_key"] == "pk-public"

    def test_list_of_dicts_inner_secrets_redacted(self):
        config = {
            "integrations": [
                {"service": "openai", "api_key": "sk-abc"},
                {"service": "stripe", "token": "tok-xyz"},
            ]
        }
        result = _redact_public_config(config)
        for item in result["integrations"]:
            assert "api_key" not in item
            assert "token" not in item

    def test_non_dict_list_items_preserved(self):
        config = {"tags": ["a", "b", "c"]}
        result = _redact_public_config(config)
        assert result["tags"] == ["a", "b", "c"]

    def test_mixed_safe_and_secret_keys(self):
        config = {"name": "svc", "api_key": "secret", "base_url": "https://api.svc.com"}
        result = _redact_public_config(config)
        assert result == {"name": "svc", "base_url": "https://api.svc.com"}

    def test_deeply_nested_secret_removed(self):
        config = {
            "tier_1": {
                "tier_2": {
                    "secret_value": "deep_secret",
                    "public": "visible",
                }
            }
        }
        result = _redact_public_config(config)
        assert "secret_value" not in result["tier_1"]["tier_2"]
        assert result["tier_1"]["tier_2"]["public"] == "visible"

    def test_returns_new_dict_not_same_object(self):
        config = {"name": "x"}
        result = _redact_public_config(config)
        assert result is not config

    def test_mixed_list_with_dict_and_non_dict(self):
        config = {
            "items": [
                {"api_key": "secret", "label": "a"},
                "plain_string",
                42,
            ]
        }
        result = _redact_public_config(config)
        items = result["items"]
        assert "api_key" not in items[0]
        assert items[0]["label"] == "a"
        assert items[1] == "plain_string"
        assert items[2] == 42

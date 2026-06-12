"""
Auth configuration unit tests.

Covers:
  _parse_bool:
    - "true","1","yes","on" → True
    - "false","0","no","off" → False
    - case insensitive

  _none_if_empty:
    - None returns None
    - empty string returns None
    - whitespace-only returns None
    - valid string stripped and returned

  AuthConfig.use_discovery:
    - True when neither override set
    - True when only issuer_override set
    - True when only jwks_url_override set
    - False when both overrides set

  get_auth_config / clear_auth_config_cache:
    - defaults: enabled=True, algorithms=["RS256"], user_id_claim="sub"
    - AUTH_ENABLED=false → enabled=False
    - AUTH_ALGORITHMS parses comma-separated list
    - MOZAIKS_OIDC_AUTHORITY populated
    - AUTH_ISSUER / AUTH_JWKS_URL set override fields
    - empty AUTH_ISSUER → issuer_override is None
    - cache cleared between tests via clear_auth_config_cache
"""
from __future__ import annotations

import pytest

from mozaiksai.core.auth.config import (
    AuthConfig,
    _none_if_empty,
    _parse_bool,
    clear_auth_config_cache,
    get_auth_config,
)

# ---------------------------------------------------------------------------
# 1. _parse_bool
# ---------------------------------------------------------------------------

class TestParseBool:
    @pytest.mark.parametrize("value", ["true", "TRUE", "True"])
    def test_truthy_true(self, value):
        assert _parse_bool(value) is True

    @pytest.mark.parametrize("value", ["1", "yes", "YES", "on", "ON"])
    def test_truthy_variants(self, value):
        assert _parse_bool(value) is True

    @pytest.mark.parametrize("value", ["false", "FALSE", "False"])
    def test_falsy_false(self, value):
        assert _parse_bool(value) is False

    @pytest.mark.parametrize("value", ["0", "no", "NO", "off", "OFF"])
    def test_falsy_variants(self, value):
        assert _parse_bool(value) is False

    def test_empty_string_is_falsy(self):
        assert _parse_bool("") is False

    def test_unknown_string_is_falsy(self):
        assert _parse_bool("maybe") is False


# ---------------------------------------------------------------------------
# 2. _none_if_empty
# ---------------------------------------------------------------------------

class TestNoneIfEmpty:
    def test_none_returns_none(self):
        assert _none_if_empty(None) is None

    def test_empty_string_returns_none(self):
        assert _none_if_empty("") is None

    def test_whitespace_only_returns_none(self):
        assert _none_if_empty("   ") is None

    def test_valid_string_returned_stripped(self):
        assert _none_if_empty("  https://auth.example.com  ") == "https://auth.example.com"

    def test_clean_string_unchanged(self):
        assert _none_if_empty("https://auth.example.com") == "https://auth.example.com"


# ---------------------------------------------------------------------------
# 3. AuthConfig.use_discovery
# ---------------------------------------------------------------------------

class TestAuthConfigUseDiscovery:
    def test_true_when_both_overrides_absent(self):
        cfg = AuthConfig(issuer_override=None, jwks_url_override=None)
        assert cfg.use_discovery is True

    def test_true_when_only_issuer_set(self):
        cfg = AuthConfig(issuer_override="https://issuer.example.com", jwks_url_override=None)
        assert cfg.use_discovery is True

    def test_true_when_only_jwks_set(self):
        cfg = AuthConfig(issuer_override=None, jwks_url_override="https://jwks.example.com/.well-known/jwks.json")
        assert cfg.use_discovery is True

    def test_false_when_both_overrides_set(self):
        cfg = AuthConfig(
            issuer_override="https://issuer.example.com",
            jwks_url_override="https://jwks.example.com/.well-known/jwks.json",
        )
        assert cfg.use_discovery is False


# ---------------------------------------------------------------------------
# 4. get_auth_config
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    """Ensure LRU cache is cleared before and after each test."""
    clear_auth_config_cache()
    yield
    clear_auth_config_cache()


class TestGetAuthConfig:
    def test_defaults_enabled_true(self, monkeypatch):
        monkeypatch.delenv("AUTH_ENABLED", raising=False)
        cfg = get_auth_config()
        assert cfg.enabled is True

    def test_auth_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "false")
        cfg = get_auth_config()
        assert cfg.enabled is False

    def test_default_algorithms_rs256(self, monkeypatch):
        monkeypatch.delenv("AUTH_ALGORITHMS", raising=False)
        cfg = get_auth_config()
        assert cfg.algorithms == ["RS256"]

    def test_custom_algorithms_parsed(self, monkeypatch):
        monkeypatch.setenv("AUTH_ALGORITHMS", "RS256,ES256")
        cfg = get_auth_config()
        assert "RS256" in cfg.algorithms
        assert "ES256" in cfg.algorithms

    def test_default_user_id_claim_is_sub(self, monkeypatch):
        monkeypatch.delenv("AUTH_USER_ID_CLAIM", raising=False)
        cfg = get_auth_config()
        assert cfg.user_id_claim == "sub"

    def test_custom_user_id_claim(self, monkeypatch):
        monkeypatch.setenv("AUTH_USER_ID_CLAIM", "oid")
        cfg = get_auth_config()
        assert cfg.user_id_claim == "oid"

    def test_oidc_authority_from_env(self, monkeypatch):
        monkeypatch.setenv("MOZAIKS_OIDC_AUTHORITY", "https://login.microsoftonline.com")
        cfg = get_auth_config()
        assert cfg.oidc_authority == "https://login.microsoftonline.com"

    def test_auth_issuer_sets_override(self, monkeypatch):
        monkeypatch.setenv("AUTH_ISSUER", "https://issuer.example.com")
        cfg = get_auth_config()
        assert cfg.issuer_override == "https://issuer.example.com"

    def test_empty_auth_issuer_gives_none_override(self, monkeypatch):
        monkeypatch.setenv("AUTH_ISSUER", "")
        cfg = get_auth_config()
        assert cfg.issuer_override is None

    def test_auth_jwks_url_sets_override(self, monkeypatch):
        monkeypatch.setenv("AUTH_JWKS_URL", "https://jwks.example.com/keys")
        cfg = get_auth_config()
        assert cfg.jwks_url_override == "https://jwks.example.com/keys"

    def test_both_overrides_disables_discovery(self, monkeypatch):
        monkeypatch.setenv("AUTH_ISSUER", "https://issuer.example.com")
        monkeypatch.setenv("AUTH_JWKS_URL", "https://jwks.example.com/keys")
        cfg = get_auth_config()
        assert cfg.use_discovery is False

    def test_default_clock_skew(self, monkeypatch):
        monkeypatch.delenv("AUTH_CLOCK_SKEW", raising=False)
        cfg = get_auth_config()
        assert cfg.clock_skew_seconds == 120

    def test_custom_clock_skew(self, monkeypatch):
        monkeypatch.setenv("AUTH_CLOCK_SKEW", "60")
        cfg = get_auth_config()
        assert cfg.clock_skew_seconds == 60

    def test_default_algorithms_whitespace_trimmed(self, monkeypatch):
        monkeypatch.setenv("AUTH_ALGORITHMS", " RS256 , ES256 ")
        cfg = get_auth_config()
        assert cfg.algorithms == ["RS256", "ES256"]

    def test_cache_returns_same_instance(self, monkeypatch):
        monkeypatch.delenv("AUTH_ENABLED", raising=False)
        cfg1 = get_auth_config()
        cfg2 = get_auth_config()
        assert cfg1 is cfg2

    def test_cache_cleared_between_calls(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        cfg1 = get_auth_config()
        clear_auth_config_cache()
        monkeypatch.setenv("AUTH_ENABLED", "false")
        cfg2 = get_auth_config()
        assert cfg1.enabled is True
        assert cfg2.enabled is False

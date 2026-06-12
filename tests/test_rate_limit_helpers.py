"""
Rate limit middleware pure helper unit tests.

Covers:
  _rate_limit_enabled:
    - default (no env) → True
    - "true" / "1" / "yes" → True
    - "false" / "0" / "no" → False
    - mixed case → handled

  _requests_per_minute:
    - default → 60
    - custom value → that integer

  _excluded_paths:
    - default includes /api/health and other infra paths
    - custom CSV → parsed into set
    - whitespace-padded entries stripped

  _path_limits:
    - no env → only defaults present
    - env entry "path:rpm" → merged, env takes precedence
    - malformed entry (no colon) → skipped
    - invalid rpm (non-int) → skipped

  _get_client_key:
    - no client_header → uses request.client.host
    - client_header set, header present → uses header value
    - X-Forwarded-For comma-separated → uses leftmost IP
    - header set but absent in request → falls back to client.host
    - request.client is None → returns "unknown"
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mozaiksai.core.transport.rate_limit import (
    _excluded_paths,
    _get_client_key,
    _path_limits,
    _rate_limit_enabled,
    _requests_per_minute,
)

# ---------------------------------------------------------------------------
# 1. _rate_limit_enabled
# ---------------------------------------------------------------------------

class TestRateLimitEnabled:
    def test_default_is_true(self, monkeypatch):
        monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)
        assert _rate_limit_enabled() is True

    @pytest.mark.parametrize("val", ["true", "True", "TRUE", "1", "yes"])
    def test_truthy_values(self, monkeypatch, val):
        monkeypatch.setenv("RATE_LIMIT_ENABLED", val)
        assert _rate_limit_enabled() is True

    @pytest.mark.parametrize("val", ["false", "False", "FALSE", "0", "no"])
    def test_falsy_values(self, monkeypatch, val):
        monkeypatch.setenv("RATE_LIMIT_ENABLED", val)
        assert _rate_limit_enabled() is False


# ---------------------------------------------------------------------------
# 2. _requests_per_minute
# ---------------------------------------------------------------------------

class TestRequestsPerMinute:
    def test_default_is_60(self, monkeypatch):
        monkeypatch.delenv("RATE_LIMIT_REQUESTS_PER_MINUTE", raising=False)
        assert _requests_per_minute() == 60

    def test_custom_value(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "120")
        assert _requests_per_minute() == 120

    def test_low_value(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "5")
        assert _requests_per_minute() == 5


# ---------------------------------------------------------------------------
# 3. _excluded_paths
# ---------------------------------------------------------------------------

class TestExcludedPaths:
    def test_default_includes_health(self, monkeypatch):
        monkeypatch.delenv("RATE_LIMIT_EXCLUDED_PATHS", raising=False)
        paths = _excluded_paths()
        assert "/api/health" in paths

    def test_default_includes_me(self, monkeypatch):
        monkeypatch.delenv("RATE_LIMIT_EXCLUDED_PATHS", raising=False)
        paths = _excluded_paths()
        assert "/api/me" in paths

    def test_default_includes_shell_config(self, monkeypatch):
        monkeypatch.delenv("RATE_LIMIT_EXCLUDED_PATHS", raising=False)
        paths = _excluded_paths()
        assert "/api/shell-config" in paths

    def test_custom_paths_override(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_EXCLUDED_PATHS", "/custom,/other")
        paths = _excluded_paths()
        assert "/custom" in paths
        assert "/other" in paths

    def test_whitespace_padded_stripped(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_EXCLUDED_PATHS", "  /api/health  ,  /api/me  ")
        paths = _excluded_paths()
        assert "/api/health" in paths
        assert "/api/me" in paths

    def test_returns_set(self, monkeypatch):
        monkeypatch.delenv("RATE_LIMIT_EXCLUDED_PATHS", raising=False)
        assert isinstance(_excluded_paths(), set)


# ---------------------------------------------------------------------------
# 4. _path_limits
# ---------------------------------------------------------------------------

class TestPathLimits:
    def test_defaults_present_when_no_env(self, monkeypatch):
        monkeypatch.delenv("RATE_LIMIT_PATH_LIMITS", raising=False)
        limits = _path_limits()
        assert "/api/chats" in limits
        assert "/chat" in limits
        assert "/api/workflows" in limits

    def test_default_values_correct(self, monkeypatch):
        monkeypatch.delenv("RATE_LIMIT_PATH_LIMITS", raising=False)
        limits = _path_limits()
        assert limits["/api/chats"] == 10
        assert limits["/chat"] == 30
        assert limits["/api/workflows"] == 20

    def test_env_entry_merged(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_PATH_LIMITS", "/api/custom:5")
        limits = _path_limits()
        assert limits["/api/custom"] == 5
        # defaults still present
        assert "/api/chats" in limits

    def test_env_overrides_default(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_PATH_LIMITS", "/api/chats:99")
        limits = _path_limits()
        assert limits["/api/chats"] == 99

    def test_malformed_entry_no_colon_skipped(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_PATH_LIMITS", "nocolon")
        limits = _path_limits()
        assert "nocolon" not in limits

    def test_invalid_rpm_skipped(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_PATH_LIMITS", "/api/test:notanumber")
        limits = _path_limits()
        assert "/api/test" not in limits

    def test_multiple_entries_parsed(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_PATH_LIMITS", "/a:10,/b:20")
        limits = _path_limits()
        assert limits["/a"] == 10
        assert limits["/b"] == 20

    def test_empty_env_uses_only_defaults(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_PATH_LIMITS", "")
        limits = _path_limits()
        assert "/api/chats" in limits


# ---------------------------------------------------------------------------
# 5. _get_client_key
# ---------------------------------------------------------------------------

def _mock_request(client_host: str | None = "1.2.3.4", headers: dict | None = None):
    request = MagicMock()
    if client_host is not None:
        request.client = MagicMock()
        request.client.host = client_host
    else:
        request.client = None
    request.headers = headers or {}
    return request


class TestGetClientKey:
    def test_no_client_header_uses_client_host(self):
        request = _mock_request(client_host="10.0.0.1")
        result = _get_client_key(request, client_header="")
        assert result == "10.0.0.1"

    def test_client_header_set_uses_header(self):
        request = _mock_request(headers={"X-Forwarded-For": "9.8.7.6"})
        result = _get_client_key(request, client_header="X-Forwarded-For")
        assert result == "9.8.7.6"

    def test_xff_comma_separated_uses_first(self):
        request = _mock_request(headers={"X-Forwarded-For": "1.1.1.1, 2.2.2.2, 3.3.3.3"})
        result = _get_client_key(request, client_header="X-Forwarded-For")
        assert result == "1.1.1.1"

    def test_client_header_absent_in_request_falls_back_to_host(self):
        request = _mock_request(client_host="5.5.5.5", headers={})
        result = _get_client_key(request, client_header="X-Forwarded-For")
        assert result == "5.5.5.5"

    def test_none_client_returns_unknown(self):
        request = _mock_request(client_host=None, headers={})
        result = _get_client_key(request, client_header="")
        assert result == "unknown"

    def test_whitespace_xff_header_stripped(self):
        request = _mock_request(headers={"X-Real-IP": "  4.4.4.4  "})
        result = _get_client_key(request, client_header="X-Real-IP")
        assert result == "4.4.4.4"

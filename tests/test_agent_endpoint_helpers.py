"""
Agent endpoint pure helper unit tests.

Covers:
  _render_template:
    - {app_id} token replaced
    - {{app_id}} token replaced
    - {appId} token replaced
    - {{appId}} token replaced
    - all four token variants in one template
    - no tokens → template unchanged
    - empty template → empty string
    - empty app_id → empty string replaces tokens
    - multiple occurrences of same token all replaced

  _resolve_from_env:
    - env key not set → None
    - env key set to empty string → None
    - app_id is empty string → None
    - app_id is whitespace-only string → None
    - both env and app_id set → rendered template returned
    - env value with surrounding whitespace stripped before render

  resolve_agent_websocket_url:
    - MOZAIKS_AGENT_WEBSOCKET_URL_TEMPLATE not set → None
    - template set with {app_id} → rendered URL
    - template set with {{appId}} → rendered URL

  resolve_agent_api_url:
    - MOZAIKS_AGENT_API_URL_TEMPLATE not set → None
    - template set with {app_id} → rendered URL
    - different app_ids produce different URLs
"""
from __future__ import annotations

import pytest

from mozaiksai.core.workflow.generator_support.agent_endpoints import (
    _render_template,
    _resolve_from_env,
    resolve_agent_api_url,
    resolve_agent_websocket_url,
)

# ---------------------------------------------------------------------------
# 1. _render_template
# ---------------------------------------------------------------------------

class TestRenderTemplate:
    def test_single_brace_app_id_replaced(self):
        result = _render_template("https://example.com/{app_id}/api", "myapp")
        assert result == "https://example.com/myapp/api"

    def test_double_brace_app_id_partial_replacement(self):
        # {{app_id}} — {app_id} is replaced first, leaving {<app_id>}
        # e.g. {{app_id}} → {myapp} (inner {app_id} consumed first)
        result = _render_template("https://example.com/{{app_id}}/api", "myapp")
        assert result == "https://example.com/{myapp}/api"

    def test_camel_case_single_brace_replaced(self):
        result = _render_template("https://example.com/{appId}/ws", "myapp")
        assert result == "https://example.com/myapp/ws"

    def test_camel_case_double_brace_partial_replacement(self):
        # {{appId}} — {appId} is replaced on step 3, leaving {<app_id>}
        result = _render_template("wss://host/{{appId}}/connect", "myapp")
        assert result == "wss://host/{myapp}/connect"

    def test_single_brace_variants_both_replaced(self):
        # Single-brace variants work correctly
        template = "{app_id}/{appId}"
        result = _render_template(template, "X")
        assert result == "X/X"

    def test_no_tokens_unchanged(self):
        template = "https://example.com/api/endpoint"
        assert _render_template(template, "any") == template

    def test_empty_template_returns_empty(self):
        assert _render_template("", "myapp") == ""

    def test_empty_app_id_replaces_tokens_with_empty(self):
        result = _render_template("https://{app_id}.example.com", "")
        assert result == "https://.example.com"

    def test_multiple_occurrences_all_replaced(self):
        result = _render_template("{app_id}/{app_id}", "abc")
        assert result == "abc/abc"

    def test_hyphen_in_app_id_preserved(self):
        result = _render_template("ws://{app_id}.api.local", "my-app-123")
        assert result == "ws://my-app-123.api.local"


# ---------------------------------------------------------------------------
# 2. _resolve_from_env
# ---------------------------------------------------------------------------

class TestResolveFromEnv:
    def test_env_key_not_set_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("TEST_RESOLVE_KEY", raising=False)
        assert _resolve_from_env("TEST_RESOLVE_KEY", "app-1") is None

    def test_env_key_empty_string_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TEST_RESOLVE_KEY", "")
        assert _resolve_from_env("TEST_RESOLVE_KEY", "app-1") is None

    def test_empty_app_id_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TEST_RESOLVE_KEY", "https://{app_id}.example.com")
        assert _resolve_from_env("TEST_RESOLVE_KEY", "") is None

    def test_whitespace_only_app_id_substituted_literally(self, monkeypatch: pytest.MonkeyPatch):
        # _resolve_from_env does NOT strip app_id; "   " is truthy so it substitutes
        monkeypatch.setenv("TEST_RESOLVE_KEY", "https://{app_id}.example.com")
        result = _resolve_from_env("TEST_RESOLVE_KEY", "   ")
        assert result == "https://   .example.com"

    def test_both_set_renders_template(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TEST_RESOLVE_KEY", "https://{app_id}.example.com")
        result = _resolve_from_env("TEST_RESOLVE_KEY", "my-app")
        assert result == "https://my-app.example.com"

    def test_whitespace_in_env_value_stripped(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TEST_RESOLVE_KEY", "  https://{app_id}.example.com  ")
        result = _resolve_from_env("TEST_RESOLVE_KEY", "app-1")
        # After strip → "https://{app_id}.example.com" → rendered
        assert result == "https://app-1.example.com"

    def test_single_brace_appid_variant_rendered(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TEST_RESOLVE_KEY", "wss://{appId}.ws.local/connect")
        result = _resolve_from_env("TEST_RESOLVE_KEY", "app-xyz")
        assert result == "wss://app-xyz.ws.local/connect"

    def test_returns_string_when_successful(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TEST_RESOLVE_KEY", "https://{app_id}/api")
        result = _resolve_from_env("TEST_RESOLVE_KEY", "app-1")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# 3. resolve_agent_websocket_url
# ---------------------------------------------------------------------------

class TestResolveAgentWebsocketUrl:
    def test_env_not_set_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("MOZAIKS_AGENT_WEBSOCKET_URL_TEMPLATE", raising=False)
        assert resolve_agent_websocket_url("app-1") is None

    def test_env_set_with_app_id_renders(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MOZAIKS_AGENT_WEBSOCKET_URL_TEMPLATE", "wss://{app_id}.ws.example.com")
        result = resolve_agent_websocket_url("myapp")
        assert result == "wss://myapp.ws.example.com"

    def test_env_set_with_single_brace_appid_variant(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MOZAIKS_AGENT_WEBSOCKET_URL_TEMPLATE", "wss://{appId}.ws.example.com")
        result = resolve_agent_websocket_url("app-2")
        assert result == "wss://app-2.ws.example.com"

    def test_none_app_id_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MOZAIKS_AGENT_WEBSOCKET_URL_TEMPLATE", "wss://{app_id}.example.com")
        assert resolve_agent_websocket_url(None) is None  # type: ignore[arg-type]

    def test_empty_string_app_id_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MOZAIKS_AGENT_WEBSOCKET_URL_TEMPLATE", "wss://{app_id}.example.com")
        assert resolve_agent_websocket_url("") is None


# ---------------------------------------------------------------------------
# 4. resolve_agent_api_url
# ---------------------------------------------------------------------------

class TestResolveAgentApiUrl:
    def test_env_not_set_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("MOZAIKS_AGENT_API_URL_TEMPLATE", raising=False)
        assert resolve_agent_api_url("app-1") is None

    def test_env_set_renders_url(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MOZAIKS_AGENT_API_URL_TEMPLATE", "https://{app_id}.api.example.com")
        result = resolve_agent_api_url("test-app")
        assert result == "https://test-app.api.example.com"

    def test_different_app_ids_produce_different_urls(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MOZAIKS_AGENT_API_URL_TEMPLATE", "https://{app_id}.api.example.com")
        r1 = resolve_agent_api_url("app-1")
        r2 = resolve_agent_api_url("app-2")
        assert r1 != r2

    def test_consistent_for_same_app_id(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MOZAIKS_AGENT_API_URL_TEMPLATE", "https://{app_id}.api.example.com")
        r1 = resolve_agent_api_url("app-1")
        r2 = resolve_agent_api_url("app-1")
        assert r1 == r2

    def test_empty_app_id_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MOZAIKS_AGENT_API_URL_TEMPLATE", "https://{app_id}.api.example.com")
        assert resolve_agent_api_url("") is None

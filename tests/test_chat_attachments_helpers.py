"""
Chat attachments pure helper unit tests.

Covers:
  _parse_allowed_workflows:
    - None → empty set
    - empty string → empty set
    - single workflow → {workflow}
    - comma-separated list → set of workflows
    - entries with whitespace → stripped
    - trailing/leading commas → empty entries excluded

  _normalize_intent:
    - "context" → "context"
    - "bundle" → "bundle"
    - "deliverable" → "deliverable"
    - uppercase → lowercased
    - whitespace stripped
    - unknown intent → ValueError
    - None → "context" (default)
    - empty string → "context" (default stripped to "context"? No: "" → "") → ValueError

  _max_bytes_from_env:
    - env var not set → default_bytes returned
    - env var set to valid int → returned
    - env var set to zero or negative → default returned
    - env var set to non-int string → default returned
    - env var set to whitespace → default returned
"""
from __future__ import annotations

import pytest

from mozaiksai.core.chat_attachments.attachments import (
    _max_bytes_from_env,
    _normalize_intent,
    _parse_allowed_workflows,
)

# ---------------------------------------------------------------------------
# 1. _parse_allowed_workflows
# ---------------------------------------------------------------------------

class TestParseAllowedWorkflows:
    def test_none_returns_empty_set(self):
        assert _parse_allowed_workflows(None) == set()  # type: ignore[arg-type]

    def test_empty_string_returns_empty_set(self):
        assert _parse_allowed_workflows("") == set()

    def test_single_workflow(self):
        result = _parse_allowed_workflows("AppGenerator")
        assert result == {"AppGenerator"}

    def test_comma_separated_list(self):
        result = _parse_allowed_workflows("AppGenerator,AgentGenerator,ExistingApp")
        assert result == {"AppGenerator", "AgentGenerator", "ExistingApp"}

    def test_whitespace_stripped_from_entries(self):
        result = _parse_allowed_workflows("  AppGenerator  ,  AgentGenerator  ")
        assert result == {"AppGenerator", "AgentGenerator"}

    def test_empty_entries_excluded(self):
        result = _parse_allowed_workflows(",AppGenerator,")
        assert result == {"AppGenerator"}

    def test_all_whitespace_entries_excluded(self):
        result = _parse_allowed_workflows("  ,  ,AppGenerator")
        assert result == {"AppGenerator"}


# ---------------------------------------------------------------------------
# 2. _normalize_intent
# ---------------------------------------------------------------------------

class TestNormalizeIntent:
    def test_context_returned(self):
        assert _normalize_intent("context") == "context"

    def test_bundle_returned(self):
        assert _normalize_intent("bundle") == "bundle"

    def test_deliverable_returned(self):
        assert _normalize_intent("deliverable") == "deliverable"

    def test_uppercase_lowercased(self):
        assert _normalize_intent("Context") == "context"
        assert _normalize_intent("BUNDLE") == "bundle"

    def test_whitespace_stripped(self):
        assert _normalize_intent("  context  ") == "context"

    def test_none_defaults_to_context(self):
        assert _normalize_intent(None) == "context"

    def test_unknown_intent_raises(self):
        with pytest.raises(ValueError, match="intent must be one of"):
            _normalize_intent("attachment")

    def test_empty_string_defaults_to_context(self):
        # "" → (None-like falsy) → "context" default applied
        assert _normalize_intent("") == "context"


# ---------------------------------------------------------------------------
# 3. _max_bytes_from_env
# ---------------------------------------------------------------------------

class TestMaxBytesFromEnv:
    def test_env_not_set_returns_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("TEST_MAX_BYTES", raising=False)
        result = _max_bytes_from_env("TEST_MAX_BYTES", 1024)
        assert result == 1024

    def test_env_set_to_valid_int(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TEST_MAX_BYTES", "2048")
        result = _max_bytes_from_env("TEST_MAX_BYTES", 1024)
        assert result == 2048

    def test_env_set_to_zero_returns_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TEST_MAX_BYTES", "0")
        result = _max_bytes_from_env("TEST_MAX_BYTES", 1024)
        assert result == 1024

    def test_env_set_to_negative_returns_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TEST_MAX_BYTES", "-100")
        result = _max_bytes_from_env("TEST_MAX_BYTES", 1024)
        assert result == 1024

    def test_env_set_to_non_int_returns_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TEST_MAX_BYTES", "ten_megabytes")
        result = _max_bytes_from_env("TEST_MAX_BYTES", 1024)
        assert result == 1024

    def test_env_set_to_whitespace_returns_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TEST_MAX_BYTES", "   ")
        result = _max_bytes_from_env("TEST_MAX_BYTES", 1024)
        assert result == 1024

    def test_env_set_to_float_string_returns_default(self, monkeypatch: pytest.MonkeyPatch):
        # int("3.5") raises ValueError → default
        monkeypatch.setenv("TEST_MAX_BYTES", "3.5")
        result = _max_bytes_from_env("TEST_MAX_BYTES", 1024)
        assert result == 1024

"""
Pure helper unit tests for mozaiksai/control_plane/app_context_impact.py.

Covers:
  _status_value:
    - string "active" → "active"
    - enum with .value attr → .value lowercased
    - None → None
    - empty string → None
    - whitespace → None
    - uppercase → lowercased

  _flatten_metadata_values:
    - flat dict → list of string values
    - nested dict → flattened strings
    - list of strings → list of strings
    - nested list → flattened
    - None → []
    - mixed dict/list → all string leaves

  _tokens:
    - empty → empty set
    - short tokens (<3 chars) excluded
    - tokens split on underscore
    - tokens split on hyphen
    - plural token also adds singular
    - alphanumeric tokens only (no punctuation)
    - deduplicates

  _as_path_candidates:
    - None → []
    - string → [string]
    - list of strings → flattened list
    - nested list → fully flattened
    - non-string, non-list, non-None → []

  _safe_relative_path:
    - empty string → None
    - whitespace → None
    - string with spaces → None
    - Windows absolute path "C:\..." → None
    - UNC path "\\\\server" → None
    - tilde path "~/" → None
    - leading "/" (absolute posix) → None
    - ".." traversal → None
    - clean relative path → returned
    - backslashes normalized
    - secret path terms → None (e.g. "secrets/key.pem")
"""
from __future__ import annotations

from mozaiksai.control_plane.app_context_impact import (
    _as_path_candidates,
    _flatten_metadata_values,
    _safe_relative_path,
    _status_value,
    _tokens,
)

# ---------------------------------------------------------------------------
# 1. _status_value
# ---------------------------------------------------------------------------

class TestStatusValue:
    def test_lowercase_string(self):
        assert _status_value("active") == "active"

    def test_uppercase_lowercased(self):
        assert _status_value("ACTIVE") == "active"

    def test_none_returns_none(self):
        assert _status_value(None) is None

    def test_empty_string_returns_none(self):
        assert _status_value("") is None

    def test_whitespace_only_returns_none(self):
        assert _status_value("   ") is None

    def test_value_attr_used(self):
        class FakeEnum:
            value = "pending"
        assert _status_value(FakeEnum()) == "pending"

    def test_strips_whitespace(self):
        assert _status_value("  stale  ") == "stale"


# ---------------------------------------------------------------------------
# 2. _flatten_metadata_values
# ---------------------------------------------------------------------------

class TestFlattenMetadataValues:
    def test_flat_dict_returns_string_values(self):
        result = _flatten_metadata_values({"key": "value", "other": "val2"})
        assert "value" in result
        assert "val2" in result

    def test_nested_dict_flattened(self):
        result = _flatten_metadata_values({"outer": {"inner": "leaf"}})
        assert "leaf" in result

    def test_list_of_strings(self):
        result = _flatten_metadata_values(["a", "b", "c"])
        assert result == ["a", "b", "c"]

    def test_nested_list_flattened(self):
        result = _flatten_metadata_values([["a", "b"], ["c"]])
        assert "a" in result
        assert "c" in result

    def test_none_returns_empty(self):
        assert _flatten_metadata_values(None) == []

    def test_mixed_dict_with_list_value(self):
        result = _flatten_metadata_values({"paths": ["app/module.py", "ui/page.yaml"]})
        assert "app/module.py" in result
        assert "ui/page.yaml" in result

    def test_int_value_stringified(self):
        result = _flatten_metadata_values(42)
        assert result == ["42"]

    def test_bool_stringified(self):
        result = _flatten_metadata_values(True)
        assert result == ["True"]

    def test_deeply_nested(self):
        result = _flatten_metadata_values({"a": {"b": {"c": "deep"}}})
        assert "deep" in result


# ---------------------------------------------------------------------------
# 3. _tokens
# ---------------------------------------------------------------------------

class TestTokens:
    def test_empty_string_returns_empty(self):
        assert _tokens("") == set()

    def test_none_returns_empty(self):
        assert _tokens(None) == set()  # type: ignore[arg-type]

    def test_short_tokens_excluded(self):
        result = _tokens("a b xy")
        assert "a" not in result
        assert "b" not in result
        assert "xy" not in result

    def test_long_tokens_included(self):
        result = _tokens("billing payment module")
        assert "billing" in result
        assert "payment" in result
        assert "module" in result

    def test_tokens_split_on_underscore(self):
        result = _tokens("billing_module")
        # "billing" and "module" both >= 3 chars
        assert "billing" in result
        assert "module" in result

    def test_tokens_split_on_hyphen(self):
        result = _tokens("auth-service")
        assert "auth" in result
        assert "service" in result

    def test_plural_adds_singular(self):
        result = _tokens("modules")
        assert "module" in result

    def test_lowercased(self):
        result = _tokens("BILLING")
        assert "billing" in result

    def test_punctuation_stripped(self):
        result = _tokens("billing! payment?")
        assert "billing" in result
        assert "payment" in result

    def test_deduplicates(self):
        result = _tokens("billing billing billing")
        # Each unique token appears only once in the set
        assert len([t for t in result if t == "billing"]) == 1


# ---------------------------------------------------------------------------
# 4. _as_path_candidates
# ---------------------------------------------------------------------------

class TestAsPathCandidates:
    def test_none_returns_empty(self):
        assert _as_path_candidates(None) == []

    def test_string_returns_single(self):
        result = _as_path_candidates("modules/billing/handler.py")
        assert result == ["modules/billing/handler.py"]

    def test_list_of_strings(self):
        result = _as_path_candidates(["a.py", "b.py"])
        assert result == ["a.py", "b.py"]

    def test_nested_list_flattened(self):
        result = _as_path_candidates([["a.py", "b.py"], ["c.py"]])
        assert "a.py" in result
        assert "c.py" in result

    def test_empty_list_returns_empty(self):
        assert _as_path_candidates([]) == []

    def test_int_returns_empty(self):
        assert _as_path_candidates(42) == []

    def test_dict_returns_empty(self):
        assert _as_path_candidates({"key": "val"}) == []

    def test_list_with_none_skips_none(self):
        result = _as_path_candidates([None, "valid.py"])
        assert "valid.py" in result
        assert None not in result


# ---------------------------------------------------------------------------
# 5. _safe_relative_path
# ---------------------------------------------------------------------------

class TestSafeRelativePath:
    def test_empty_string_returns_none(self):
        assert _safe_relative_path("") is None

    def test_whitespace_only_returns_none(self):
        assert _safe_relative_path("   ") is None

    def test_string_with_internal_space_returns_none(self):
        assert _safe_relative_path("modules/my module/file.py") is None

    def test_windows_absolute_path_returns_none(self):
        assert _safe_relative_path("C:\\modules\\billing\\handler.py") is None

    def test_unc_path_returns_none(self):
        assert _safe_relative_path("\\\\server\\share\\file.py") is None

    def test_tilde_path_returns_none(self):
        assert _safe_relative_path("~/configs/app.py") is None

    def test_absolute_posix_path_returns_none(self):
        assert _safe_relative_path("/etc/passwd") is None

    def test_traversal_double_dot_returns_none(self):
        assert _safe_relative_path("modules/../etc/passwd") is None

    def test_clean_relative_path_returned(self):
        result = _safe_relative_path("modules/billing/handler.py")
        assert result == "modules/billing/handler.py"

    def test_backslashes_normalized(self):
        result = _safe_relative_path("modules\\billing\\handler.py")
        assert result == "modules/billing/handler.py"

    def test_secret_path_returns_none(self):
        assert _safe_relative_path("config/secrets.yaml") is None

    def test_credentials_path_returns_none(self):
        assert _safe_relative_path("config/credentials.yaml") is None

    def test_single_filename_returned(self):
        result = _safe_relative_path("module.yaml")
        assert result == "module.yaml"

    def test_leading_slash_removed(self):
        assert _safe_relative_path("/modules/billing/handler.py") is None

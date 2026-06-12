"""
Pure helper unit tests for mozaiksai/core/workflow/context/derived.py.

Covers:
  _resolve_nested_key:
    - key=None → returns payload unchanged
    - empty/blank key → None
    - non-dict payload → None
    - simple key lookup
    - dotted path lookup
    - missing key → None
    - missing nested key → None
    - None payload → None

  _compile_optional_regex:
    - None pattern → None
    - empty string → None
    - valid pattern → re.Pattern
    - invalid regex → None
    - pattern is case-insensitive (IGNORECASE flag)

  _matches_text_conditions:
    - empty text → False
    - whitespace-only text → False
    - equals match (case-insensitive)
    - equals no match
    - contains match (case-insensitive)
    - contains no match
    - regex match
    - regex no match
    - all conditions None with non-empty text → False
    - multiple conditions: first matching one returns True
"""
from __future__ import annotations

import re

from mozaiksai.core.workflow.context.derived import (
    _compile_optional_regex,
    _matches_text_conditions,
    _resolve_nested_key,
)

# ---------------------------------------------------------------------------
# 1. _resolve_nested_key
# ---------------------------------------------------------------------------

class TestResolveNestedKey:
    def test_none_key_returns_payload(self):
        payload = {"a": 1}
        assert _resolve_nested_key(payload, None) == {"a": 1}

    def test_empty_key_returns_none(self):
        assert _resolve_nested_key({"a": 1}, "") is None

    def test_whitespace_key_returns_none(self):
        assert _resolve_nested_key({"a": 1}, "   ") is None

    def test_non_dict_payload_returns_none(self):
        assert _resolve_nested_key("string", "key") is None

    def test_none_payload_returns_none(self):
        assert _resolve_nested_key(None, "key") is None

    def test_simple_key_lookup(self):
        assert _resolve_nested_key({"key": "value"}, "key") == "value"

    def test_missing_key_returns_none(self):
        assert _resolve_nested_key({"a": 1}, "b") is None

    def test_dotted_path_lookup(self):
        payload = {"outer": {"inner": "deep_value"}}
        assert _resolve_nested_key(payload, "outer.inner") == "deep_value"

    def test_deeply_nested_dotted_path(self):
        payload = {"a": {"b": {"c": 42}}}
        assert _resolve_nested_key(payload, "a.b.c") == 42

    def test_missing_nested_key_returns_none(self):
        payload = {"outer": {"inner": "val"}}
        assert _resolve_nested_key(payload, "outer.missing") is None

    def test_non_dict_mid_path_returns_none(self):
        payload = {"outer": "not_a_dict"}
        assert _resolve_nested_key(payload, "outer.inner") is None

    def test_direct_key_takes_priority_over_dotted(self):
        # If "outer.inner" exists as a literal key, it's returned directly
        payload = {"outer.inner": "direct", "outer": {"inner": "nested"}}
        assert _resolve_nested_key(payload, "outer.inner") == "direct"

    def test_none_value_returned(self):
        # None value for a valid key is returned as None
        payload = {"key": None}
        result = _resolve_nested_key(payload, "key")
        assert result is None

    def test_list_value_returned(self):
        payload = {"items": [1, 2, 3]}
        assert _resolve_nested_key(payload, "items") == [1, 2, 3]


# ---------------------------------------------------------------------------
# 2. _compile_optional_regex
# ---------------------------------------------------------------------------

class TestCompileOptionalRegex:
    def test_none_returns_none(self):
        assert _compile_optional_regex(None) is None

    def test_empty_string_returns_none(self):
        assert _compile_optional_regex("") is None

    def test_valid_pattern_returns_compiled(self):
        result = _compile_optional_regex(r"\d+")
        assert isinstance(result, re.Pattern)

    def test_invalid_pattern_returns_none(self):
        assert _compile_optional_regex("[invalid") is None

    def test_case_insensitive_flag(self):
        pattern = _compile_optional_regex("hello")
        assert pattern is not None
        assert pattern.search("HELLO") is not None

    def test_complex_pattern(self):
        pattern = _compile_optional_regex(r"(billing|payment)")
        assert pattern is not None
        assert pattern.search("billing module") is not None


# ---------------------------------------------------------------------------
# 3. _matches_text_conditions
# ---------------------------------------------------------------------------

class TestMatchesTextConditions:
    def test_empty_text_returns_false(self):
        assert _matches_text_conditions(text="", equals="x", contains=None, compiled=None) is False

    def test_whitespace_text_returns_false(self):
        assert _matches_text_conditions(text="   ", equals="x", contains=None, compiled=None) is False

    def test_all_none_conditions_returns_false(self):
        assert _matches_text_conditions(text="hello", equals=None, contains=None, compiled=None) is False

    def test_equals_match_case_insensitive(self):
        result = _matches_text_conditions(
            text="Hello World", equals="hello world", contains=None, compiled=None
        )
        assert result is True

    def test_equals_no_match(self):
        result = _matches_text_conditions(
            text="Hello World", equals="goodbye", contains=None, compiled=None
        )
        assert result is False

    def test_contains_match(self):
        result = _matches_text_conditions(
            text="The billing module", equals=None, contains="billing", compiled=None
        )
        assert result is True

    def test_contains_case_insensitive(self):
        result = _matches_text_conditions(
            text="BILLING module", equals=None, contains="billing", compiled=None
        )
        assert result is True

    def test_contains_no_match(self):
        result = _matches_text_conditions(
            text="payment module", equals=None, contains="billing", compiled=None
        )
        assert result is False

    def test_regex_match(self):
        pattern = re.compile(r"\d{4}", re.IGNORECASE)
        result = _matches_text_conditions(
            text="invoice 2025", equals=None, contains=None, compiled=pattern
        )
        assert result is True

    def test_regex_no_match(self):
        pattern = re.compile(r"\d{4}", re.IGNORECASE)
        result = _matches_text_conditions(
            text="no numbers here", equals=None, contains=None, compiled=pattern
        )
        assert result is False

    def test_equals_checked_before_contains(self):
        # equals matches first → True even if contains also would match
        result = _matches_text_conditions(
            text="billing", equals="billing", contains="not_present", compiled=None
        )
        assert result is True

    def test_multiple_conditions_first_match_wins(self):
        # contains matches → True even if regex would not
        result = _matches_text_conditions(
            text="billing module",
            equals=None,
            contains="billing",
            compiled=re.compile(r"^xyz$"),
        )
        assert result is True

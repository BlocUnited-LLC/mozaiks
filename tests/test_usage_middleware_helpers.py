"""
Pure helper unit tests for:
  mozaiksai/core/usage/middleware.py

Covers sync pure helpers (no IO/async):

  _ctx_get:
    - None context_variables → default returned
    - dict context_variables → value returned by key
    - context_variables with .get method → delegated
    - context_variables with .data dict → data dict used
    - missing key → default returned
    - context_variables without .get or .data → default returned

  _int_usage:
    - None → 0
    - 0 → 0
    - positive int → returned as int
    - string number → coerced to int
    - negative value → clamped to 0
    - non-numeric string → 0

  _usage_value:
    - usage object with attr matching first name → returned
    - usage object with attr matching second name → returned
    - usage dict → value extracted
    - no matching name → 0
    - None usage → 0
    - value is None in usage → skipped to next name

  _text (middleware):
    - empty string → ""
    - "None" string → ""
    - "none" string (any case) → ""
    - normal text → returned stripped
    - None value → ""
    - whitespace-only → ""
    - leading/trailing whitespace stripped
"""
from __future__ import annotations

from types import SimpleNamespace

from mozaiksai.core.usage.middleware import (
    _ctx_get,
    _int_usage,
    _text,
    _usage_value,
)

# ---------------------------------------------------------------------------
# 1. _ctx_get
# ---------------------------------------------------------------------------

class TestCtxGet:
    def test_none_context_returns_default(self):
        assert _ctx_get(None, "key") is None
        assert _ctx_get(None, "key", "fallback") == "fallback"

    def test_dict_context_returns_value(self):
        ctx = {"key": "value", "other": 42}
        assert _ctx_get(ctx, "key") == "value"

    def test_dict_context_missing_key_returns_default(self):
        ctx = {"key": "value"}
        assert _ctx_get(ctx, "missing", "default") == "default"

    def test_object_with_get_method(self):
        class FakeCtx:
            def get(self, key, default=None):
                return {"key": "val"}.get(key, default)
        assert _ctx_get(FakeCtx(), "key") == "val"
        assert _ctx_get(FakeCtx(), "missing", "fb") == "fb"

    def test_object_with_data_dict_attribute(self):
        ctx = SimpleNamespace(data={"key": "from_data"})
        assert _ctx_get(ctx, "key") == "from_data"

    def test_object_without_get_or_data_returns_default(self):
        ctx = SimpleNamespace(other="stuff")
        assert _ctx_get(ctx, "key", "fallback") == "fallback"

    def test_default_is_none_when_not_specified(self):
        assert _ctx_get({}, "missing") is None


# ---------------------------------------------------------------------------
# 2. _int_usage
# ---------------------------------------------------------------------------

class TestIntUsage:
    def test_none_returns_zero(self):
        assert _int_usage(None) == 0

    def test_zero_returns_zero(self):
        assert _int_usage(0) == 0

    def test_positive_int_returned(self):
        assert _int_usage(100) == 100

    def test_string_number_coerced(self):
        assert _int_usage("42") == 42

    def test_negative_clamped_to_zero(self):
        assert _int_usage(-5) == 0

    def test_non_numeric_string_returns_zero(self):
        assert _int_usage("not-a-number") == 0

    def test_float_truncated(self):
        assert _int_usage(3.9) == 3


# ---------------------------------------------------------------------------
# 3. _usage_value
# ---------------------------------------------------------------------------

class TestUsageValue:
    def test_attribute_matching_first_name_returned(self):
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=20)
        assert _usage_value(usage, "prompt_tokens", "completion_tokens") == 10

    def test_attribute_matching_second_name_used_when_first_missing(self):
        usage = SimpleNamespace(completion_tokens=20)
        assert _usage_value(usage, "prompt_tokens", "completion_tokens") == 20

    def test_none_attribute_skips_to_next(self):
        usage = SimpleNamespace(prompt_tokens=None, completion_tokens=5)
        assert _usage_value(usage, "prompt_tokens", "completion_tokens") == 5

    def test_dict_usage_value_extracted(self):
        usage = {"input_tokens": 15, "output_tokens": 25}
        assert _usage_value(usage, "input_tokens", "output_tokens") == 15

    def test_no_matching_name_returns_zero(self):
        usage = SimpleNamespace(other_field=99)
        assert _usage_value(usage, "prompt_tokens") == 0

    def test_none_usage_returns_zero(self):
        assert _usage_value(None, "prompt_tokens") == 0

    def test_empty_dict_usage_returns_zero(self):
        assert _usage_value({}, "prompt_tokens") == 0


# ---------------------------------------------------------------------------
# 4. _text (middleware version)
# ---------------------------------------------------------------------------

class TestMiddlewareText:
    def test_empty_string_returns_empty(self):
        assert _text("") == ""

    def test_none_string_literal_returns_empty(self):
        assert _text("None") == ""

    def test_none_string_lowercase_returns_empty(self):
        assert _text("none") == ""

    def test_none_string_mixed_case_returns_empty(self):
        assert _text("NONE") == ""

    def test_normal_text_returned(self):
        assert _text("hello world") == "hello world"

    def test_none_value_returns_empty(self):
        assert _text(None) == ""

    def test_whitespace_only_returns_empty(self):
        assert _text("   ") == ""

    def test_leading_trailing_whitespace_stripped(self):
        assert _text("  hello  ") == "hello"

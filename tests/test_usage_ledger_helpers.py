"""
Usage ledger and context variable pure helper unit tests.

Covers:
  mozaiksai.core.usage.ledger:
    _int_value:
      - None → 0
      - zero → 0
      - positive int → value
      - negative clamped to 0
      - string digit → parsed
      - non-numeric string → 0
      - float → truncated int

    _float_value:
      - None → 0.0
      - zero → 0.0
      - positive float → value
      - negative clamped to 0.0
      - string float → parsed
      - non-numeric string → 0.0

    _text:
      - None → None
      - empty string → None
      - whitespace-only → None
      - non-empty string → stripped

  mozaiksai.core.workflow.context.variables:
    _coerce_value:
      - definition None → raw value returned as-is
      - raw_value None → None
      - type "boolean": "true" → True
      - type "boolean": "1" → True
      - type "boolean": "yes" → True
      - type "boolean": "on" → True
      - type "boolean": "false" → False
      - type "boolean": "0" → False
      - type "boolean": bool True → True
      - type "boolean": non-string truthy → True
      - type "bool" alias accepted
      - type "integer": string digit → int
      - type "integer": float → truncated int
      - type "integer": non-numeric string → raw value unchanged
      - type "int" alias accepted
      - type "string": value returned unchanged
      - unknown type: raw value returned
"""
from __future__ import annotations

import pytest

from mozaiksai.core.usage.ledger import _float_value, _int_value, _text
from mozaiksai.core.workflow.context.schema import (
    ContextVariableDefinition,
    ContextVariableSource,
)
from mozaiksai.core.workflow.context.variables import _coerce_value

# ---------------------------------------------------------------------------
# Helper: build a minimal ContextVariableDefinition with a type
# ---------------------------------------------------------------------------

def _defn(type_str: str | None) -> ContextVariableDefinition:
    return ContextVariableDefinition(
        type=type_str,
        source=ContextVariableSource(type="config"),
    )


# ---------------------------------------------------------------------------
# 1. _int_value
# ---------------------------------------------------------------------------

class TestIntValue:
    def test_none_returns_zero(self):
        assert _int_value(None) == 0

    def test_zero_returns_zero(self):
        assert _int_value(0) == 0

    def test_positive_int_returned(self):
        assert _int_value(42) == 42

    def test_negative_clamped_to_zero(self):
        assert _int_value(-5) == 0

    def test_string_digit_parsed(self):
        assert _int_value("100") == 100

    def test_non_numeric_string_returns_zero(self):
        assert _int_value("bad") == 0

    def test_float_truncated(self):
        assert _int_value(3.9) == 3

    def test_zero_string_returns_zero(self):
        assert _int_value("0") == 0


# ---------------------------------------------------------------------------
# 2. _float_value
# ---------------------------------------------------------------------------

class TestFloatValue:
    def test_none_returns_zero_float(self):
        assert _float_value(None) == 0.0

    def test_zero_returns_zero_float(self):
        assert _float_value(0.0) == 0.0

    def test_positive_float_returned(self):
        assert _float_value(3.14) == pytest.approx(3.14)

    def test_negative_clamped_to_zero(self):
        assert _float_value(-1.5) == 0.0

    def test_string_float_parsed(self):
        assert _float_value("2.5") == pytest.approx(2.5)

    def test_non_numeric_string_returns_zero(self):
        assert _float_value("nan_value") == 0.0

    def test_int_coerced_to_float(self):
        result = _float_value(5)
        assert result == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# 3. _text
# ---------------------------------------------------------------------------

class TestText:
    def test_none_returns_none(self):
        assert _text(None) is None

    def test_empty_string_returns_none(self):
        assert _text("") is None

    def test_whitespace_only_returns_none(self):
        assert _text("   ") is None

    def test_non_empty_returns_stripped(self):
        assert _text("  hello  ") == "hello"

    def test_already_stripped_string_unchanged(self):
        assert _text("world") == "world"

    def test_zero_int_returns_none(self):
        # str(0 or "") → "" → None
        assert _text(0) is None


# ---------------------------------------------------------------------------
# 4. _coerce_value
# ---------------------------------------------------------------------------

class TestCoerceValue:
    def test_none_definition_returns_raw(self):
        assert _coerce_value(None, "hello") == "hello"
        assert _coerce_value(None, 42) == 42

    def test_none_raw_returns_none(self):
        assert _coerce_value(_defn("string"), None) is None

    def test_bool_true_strings(self):
        defn = _defn("boolean")
        assert _coerce_value(defn, "true") is True
        assert _coerce_value(defn, "1") is True
        assert _coerce_value(defn, "yes") is True
        assert _coerce_value(defn, "on") is True

    def test_bool_false_strings(self):
        defn = _defn("boolean")
        assert _coerce_value(defn, "false") is False
        assert _coerce_value(defn, "0") is False
        assert _coerce_value(defn, "no") is False

    def test_bool_case_insensitive(self):
        defn = _defn("boolean")
        assert _coerce_value(defn, "TRUE") is True
        assert _coerce_value(defn, "YES") is True

    def test_bool_type_native_bool(self):
        defn = _defn("boolean")
        assert _coerce_value(defn, True) is True
        assert _coerce_value(defn, False) is False

    def test_bool_truthy_non_string(self):
        defn = _defn("boolean")
        assert _coerce_value(defn, 1) is True
        assert _coerce_value(defn, 0) is False

    def test_bool_alias(self):
        defn = _defn("bool")
        assert _coerce_value(defn, "yes") is True

    def test_integer_string_parsed(self):
        defn = _defn("integer")
        assert _coerce_value(defn, "42") == 42

    def test_integer_float_truncated(self):
        defn = _defn("integer")
        assert _coerce_value(defn, 3.9) == 3

    def test_integer_non_numeric_returns_raw(self):
        defn = _defn("integer")
        result = _coerce_value(defn, "bad")
        assert result == "bad"  # unchanged on parse failure

    def test_integer_alias_int(self):
        defn = _defn("int")
        assert _coerce_value(defn, "10") == 10

    def test_string_type_returns_unchanged(self):
        defn = _defn("string")
        assert _coerce_value(defn, "hello") == "hello"
        assert _coerce_value(defn, 42) == 42

    def test_unknown_type_returns_unchanged(self):
        defn = _defn("list")
        assert _coerce_value(defn, ["a", "b"]) == ["a", "b"]

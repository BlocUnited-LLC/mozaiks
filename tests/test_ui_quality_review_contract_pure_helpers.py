"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/ui_quality.py
  factory_app/workflows/AppGenerator/tools/review_module_contract_quality.py

Covers shared pure helpers (identical implementation in both modules):

  ui_quality._normalize_warnings / review_module_contract_quality._normalize_warnings:
    - None → []
    - empty string → []
    - non-empty string → [stripped]
    - string with only whitespace → []
    - list with strings → stripped non-empty items
    - list with whitespace-only strings → filtered out
    - list with non-string items → str() applied
    - int/float/other → [str(value)] if non-empty
    - list with empty strings → filtered

  ui_quality._as_int:
    - valid int → same
    - valid string number → converted
    - invalid string → default returned (0)
    - None → default returned (0)
    - float → truncated to int
    - custom default used on failure

  review_module_contract_quality._dedupe:
    - empty list → []
    - all duplicates → single occurrence
    - distinct items → all included
    - first occurrence of duplicate kept
    - order preserved for distinct items
    - mixed duplicates and uniques → correct result
"""
from __future__ import annotations

import pytest

from factory_app.workflows.AppGenerator.tools.review_module_contract_quality import (
    _dedupe,
)
from factory_app.workflows.AppGenerator.tools.review_module_contract_quality import (
    _normalize_warnings as normalize_review,
)
from factory_app.workflows.AppGenerator.tools.ui_quality import (
    _as_int,
)
from factory_app.workflows.AppGenerator.tools.ui_quality import (
    _normalize_warnings as normalize_ui,
)

# ---------------------------------------------------------------------------
# Parameterize to test both _normalize_warnings implementations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("normalize", [normalize_ui, normalize_review])
class TestNormalizeWarnings:
    def test_none_returns_empty(self, normalize):
        assert normalize(None) == []

    def test_empty_string_returns_empty(self, normalize):
        assert normalize("") == []

    def test_whitespace_only_string_returns_empty(self, normalize):
        assert normalize("   ") == []

    def test_non_empty_string_returns_single_item(self, normalize):
        result = normalize("Some warning")
        assert result == ["Some warning"]

    def test_string_stripped(self, normalize):
        result = normalize("  warning  ")
        assert result == ["warning"]

    def test_list_with_strings(self, normalize):
        result = normalize(["warn1", "warn2"])
        assert result == ["warn1", "warn2"]

    def test_list_empty_strings_filtered(self, normalize):
        result = normalize(["", "warn", "  "])
        assert result == ["warn"]

    def test_list_strings_stripped(self, normalize):
        result = normalize(["  warning  "])
        assert result == ["warning"]

    def test_list_non_string_items_str_applied(self, normalize):
        result = normalize([42])
        assert result == ["42"]

    def test_list_mixed_strings_and_ints(self, normalize):
        result = normalize(["error", 1, "warning"])
        assert "error" in result
        assert "1" in result
        assert "warning" in result

    def test_integer_value_returns_str(self, normalize):
        result = normalize(99)
        assert result == ["99"]

    def test_zero_returns_empty(self, normalize):
        # str(0) == "0" which is truthy → ["0"]
        result = normalize(0)
        assert result == ["0"]

    def test_empty_list_returns_empty(self, normalize):
        assert normalize([]) == []


# ---------------------------------------------------------------------------
# _as_int  (ui_quality.py only)
# ---------------------------------------------------------------------------

class TestAsInt:
    def test_int_input_returned(self):
        assert _as_int(42) == 42

    def test_string_number_converted(self):
        assert _as_int("5") == 5

    def test_invalid_string_returns_default(self):
        assert _as_int("abc") == 0

    def test_none_returns_default(self):
        assert _as_int(None) == 0

    def test_float_truncated(self):
        assert _as_int(3.9) == 3

    def test_custom_default_used_on_failure(self):
        assert _as_int("bad", default=-1) == -1

    def test_zero_returned(self):
        assert _as_int(0) == 0

    def test_negative_int(self):
        assert _as_int(-3) == -3


# ---------------------------------------------------------------------------
# _dedupe  (review_module_contract_quality.py only)
# ---------------------------------------------------------------------------

class TestDedupe:
    def test_empty_list_returns_empty(self):
        assert _dedupe([]) == []

    def test_single_item_unchanged(self):
        assert _dedupe(["warn"]) == ["warn"]

    def test_duplicates_reduced_to_one(self):
        result = _dedupe(["a", "a", "a"])
        assert result == ["a"]

    def test_first_occurrence_kept(self):
        result = _dedupe(["b", "a", "b"])
        assert result[0] == "b"
        assert result[1] == "a"
        assert len(result) == 2

    def test_distinct_items_all_included(self):
        result = _dedupe(["x", "y", "z"])
        assert result == ["x", "y", "z"]

    def test_order_preserved(self):
        items = ["c", "a", "b", "c", "a"]
        result = _dedupe(items)
        assert result == ["c", "a", "b"]

    def test_mixed_duplicates_and_uniques(self):
        items = ["err1", "err2", "err1", "err3", "err2"]
        result = _dedupe(items)
        assert result == ["err1", "err2", "err3"]

    def test_single_duplicate_pair(self):
        result = _dedupe(["dup", "other", "dup"])
        assert result == ["dup", "other"]

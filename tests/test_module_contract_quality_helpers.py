"""
review_module_contract_quality.py pure helper unit tests.

Covers:
  _context_get:
    - None context_variables → default returned
    - dict with key → value returned
    - dict without key → default returned
    - object with .get() method → used
    - object with .data dict attribute → used
    - object.get() returning None → default returned

  _context_set:
    - None context_variables → no-op (no error)
    - dict → key set
    - object with .set() method → called
    - object with .data dict attribute → data dict updated
    - object.set() raises → falls through to .data

  _normalize_warnings:
    - None → []
    - empty string → []
    - non-empty string → [stripped string]
    - list of strings → stripped, empty excluded
    - list with non-strings → coerced to str
    - arbitrary value → stringified if non-empty

  _dedupe:
    - empty list → []
    - no duplicates → same order preserved
    - with duplicates → first occurrence kept
    - all duplicates → single item
"""
from __future__ import annotations

from types import SimpleNamespace

from factory_app.workflows.AppGenerator.tools.review_module_contract_quality import (
    _context_get,
    _context_set,
    _dedupe,
    _normalize_warnings,
)

# ---------------------------------------------------------------------------
# 1. _context_get
# ---------------------------------------------------------------------------

class TestContextGet:
    def test_none_returns_default(self):
        assert _context_get(None, "key", "fallback") == "fallback"

    def test_none_default_is_none(self):
        assert _context_get(None, "key") is None

    def test_dict_key_found(self):
        assert _context_get({"key": "value"}, "key") == "value"

    def test_dict_key_missing_returns_default(self):
        assert _context_get({"other": "val"}, "key", "def") == "def"

    def test_object_with_get_method(self):
        ctx = SimpleNamespace()
        ctx.get = lambda k, d=None: "from_get" if k == "key" else d
        assert _context_get(ctx, "key") == "from_get"

    def test_object_with_data_dict(self):
        ctx = SimpleNamespace(data={"key": "data_value"})
        assert _context_get(ctx, "key") == "data_value"

    def test_object_get_returns_none_falls_to_default(self):
        ctx = SimpleNamespace()
        ctx.get = lambda k, d=None: None  # always returns None
        # When .get() returns None, _context_get returns default
        assert _context_get(ctx, "key", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# 2. _context_set
# ---------------------------------------------------------------------------

class TestContextSet:
    def test_none_no_error(self):
        _context_set(None, "key", "value")  # should not raise

    def test_dict_sets_key(self):
        ctx = {}
        _context_set(ctx, "mykey", "myvalue")
        assert ctx["mykey"] == "myvalue"

    def test_object_with_set_method(self):
        stored = {}
        ctx = SimpleNamespace()
        ctx.set = lambda k, v: stored.update({k: v})
        _context_set(ctx, "key", "val")
        assert stored["key"] == "val"

    def test_object_with_data_dict(self):
        ctx = SimpleNamespace(data={})
        _context_set(ctx, "mykey", "myval")
        assert ctx.data["mykey"] == "myval"

    def test_object_set_raises_falls_through_to_data(self):
        ctx = SimpleNamespace(data={})

        def bad_set(k, v):
            raise ValueError("no set")

        ctx.set = bad_set
        _context_set(ctx, "key", "val")
        assert ctx.data["key"] == "val"


# ---------------------------------------------------------------------------
# 3. _normalize_warnings
# ---------------------------------------------------------------------------

class TestNormalizeWarnings:
    def test_none_returns_empty(self):
        assert _normalize_warnings(None) == []

    def test_empty_string_returns_empty(self):
        assert _normalize_warnings("") == []

    def test_whitespace_string_returns_empty(self):
        assert _normalize_warnings("   ") == []

    def test_non_empty_string_returned_in_list(self):
        assert _normalize_warnings("a warning") == ["a warning"]

    def test_string_stripped(self):
        assert _normalize_warnings("  warning  ") == ["warning"]

    def test_list_of_strings(self):
        result = _normalize_warnings(["warn1", "  warn2  "])
        assert result == ["warn1", "warn2"]

    def test_list_empty_items_excluded(self):
        result = _normalize_warnings(["", "valid", "   "])
        assert result == ["valid"]

    def test_list_non_strings_coerced(self):
        result = _normalize_warnings([42, "str"])
        assert "42" in result
        assert "str" in result

    def test_integer_value_stringified(self):
        result = _normalize_warnings(42)
        assert result == ["42"]

    def test_zero_stringified_to_zero(self):
        # 0 → str → "0" which is non-empty, so ["0"] is returned
        result = _normalize_warnings(0)
        assert result == ["0"]


# ---------------------------------------------------------------------------
# 4. _dedupe
# ---------------------------------------------------------------------------

class TestDedupe:
    def test_empty_list_returns_empty(self):
        assert _dedupe([]) == []

    def test_no_duplicates_preserves_order(self):
        assert _dedupe(["a", "b", "c"]) == ["a", "b", "c"]

    def test_duplicates_first_occurrence_kept(self):
        result = _dedupe(["a", "b", "a", "c"])
        assert result == ["a", "b", "c"]

    def test_all_duplicates_single_item(self):
        assert _dedupe(["x", "x", "x"]) == ["x"]

    def test_order_preserved_for_first_occurrences(self):
        result = _dedupe(["z", "a", "z", "b", "a"])
        assert result == ["z", "a", "b"]

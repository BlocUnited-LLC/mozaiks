"""
ui_primitives.py pure helper unit tests.

Covers:
  _split_identifiers:
    - empty string → []
    - single identifier → [identifier]
    - comma-separated identifiers → all returned
    - whitespace around identifiers → stripped
    - aliased identifier ("Foo as F") → ["Foo"] (alias stripped)
    - invalid identifier (starts with digit) → excluded
    - invalid identifier (has dash) → excluded
    - empty token after split → skipped
    - mixed valid/invalid → only valid returned

  _names:
    - empty tuple → empty tuple
    - single export → (name,)
    - multiple exports same name → deduplicated
    - multiple exports different names → sorted alphabetically
    - exports with mixed names → sorted

  _validate_primitive_names:
    - None → []
    - empty iterable → []
    - valid name in allowed set → returned in list
    - whitespace-only name → skipped
    - name not in allowed → ValueError raised
    - non-string value → ValueError raised
    - duplicates → deduplicated (first occurrence kept)
    - error message includes both invalid and allowed names
"""
from __future__ import annotations

import pytest

from mozaiksai.core.workflow.ui_primitives import (
    UIPrimitiveExport,
    _names,
    _split_identifiers,
    _validate_primitive_names,
)

# ---------------------------------------------------------------------------
# 1. _split_identifiers
# ---------------------------------------------------------------------------

class TestSplitIdentifiers:
    def test_empty_string_returns_empty(self):
        assert _split_identifiers("") == []

    def test_single_identifier(self):
        assert _split_identifiers("Button") == ["Button"]

    def test_comma_separated_identifiers(self):
        result = _split_identifiers("Button, Card, Input")
        assert result == ["Button", "Card", "Input"]

    def test_whitespace_around_identifiers_stripped(self):
        result = _split_identifiers("  Button  ,  Card  ")
        assert result == ["Button", "Card"]

    def test_aliased_identifier_alias_stripped(self):
        result = _split_identifiers("Button as Btn")
        assert result == ["Button"]

    def test_aliased_with_multiple(self):
        result = _split_identifiers("Button as Btn, Card")
        assert result == ["Button", "Card"]

    def test_invalid_starts_with_digit_excluded(self):
        result = _split_identifiers("3Button")
        assert result == []

    def test_identifier_with_dash_excluded(self):
        # Dashes are not valid JS identifier characters per the regex
        result = _split_identifiers("my-button")
        assert result == []

    def test_empty_token_skipped(self):
        # Double comma creates empty token
        result = _split_identifiers("Button,,Card")
        assert result == ["Button", "Card"]

    def test_mixed_valid_invalid_only_valid_returned(self):
        result = _split_identifiers("Button, 3Invalid, Card")
        assert "Button" in result
        assert "Card" in result
        assert "3Invalid" not in result

    def test_valid_identifier_with_underscores(self):
        result = _split_identifiers("MyButton_v2")
        assert result == ["MyButton_v2"]


# ---------------------------------------------------------------------------
# 2. _names
# ---------------------------------------------------------------------------

class TestNames:
    def _export(self, name: str, source: str = "primitives.js") -> UIPrimitiveExport:
        return UIPrimitiveExport(name=name, source_file=source)

    def test_empty_tuple_returns_empty_tuple(self):
        assert _names(()) == ()

    def test_single_export(self):
        result = _names((self._export("Button"),))
        assert result == ("Button",)

    def test_multiple_exports_different_names_sorted(self):
        exports = (self._export("Card"), self._export("Button"), self._export("Input"))
        result = _names(exports)
        assert result == ("Button", "Card", "Input")

    def test_duplicate_names_deduplicated(self):
        exports = (self._export("Button", "a.js"), self._export("Button", "b.js"))
        result = _names(exports)
        assert result == ("Button",)

    def test_mixed_names_sorted_alphabetically(self):
        exports = tuple(self._export(n) for n in ["Zebra", "Alpha", "Middle"])
        result = _names(exports)
        assert result == ("Alpha", "Middle", "Zebra")

    def test_returns_tuple(self):
        result = _names((self._export("Button"),))
        assert isinstance(result, tuple)


# ---------------------------------------------------------------------------
# 3. _validate_primitive_names
# ---------------------------------------------------------------------------

class TestValidatePrimitiveNames:
    _ALLOWED = ("Button", "Card", "Input", "Text")

    def test_none_returns_empty(self):
        result = _validate_primitive_names(None, allowed_names=self._ALLOWED, context="page")
        assert result == []

    def test_empty_iterable_returns_empty(self):
        result = _validate_primitive_names([], allowed_names=self._ALLOWED, context="page")
        assert result == []

    def test_valid_name_returned(self):
        result = _validate_primitive_names(["Button"], allowed_names=self._ALLOWED, context="page")
        assert result == ["Button"]

    def test_multiple_valid_names_returned(self):
        result = _validate_primitive_names(["Button", "Card"], allowed_names=self._ALLOWED, context="page")
        assert "Button" in result
        assert "Card" in result

    def test_whitespace_only_name_skipped(self):
        result = _validate_primitive_names(["  ", "Button"], allowed_names=self._ALLOWED, context="page")
        assert result == ["Button"]

    def test_invalid_name_raises_value_error(self):
        with pytest.raises(ValueError, match="InvalidName"):
            _validate_primitive_names(["InvalidName"], allowed_names=self._ALLOWED, context="page")

    def test_error_includes_allowed_names(self):
        with pytest.raises(ValueError, match="Button"):
            _validate_primitive_names(["BadName"], allowed_names=self._ALLOWED, context="page")

    def test_error_includes_context(self):
        with pytest.raises(ValueError, match="my_page"):
            _validate_primitive_names(["Bad"], allowed_names=self._ALLOWED, context="my_page")

    def test_non_string_value_raises_value_error(self):
        with pytest.raises(ValueError):
            _validate_primitive_names([42], allowed_names=self._ALLOWED, context="page")

    def test_duplicates_deduplicated_first_kept(self):
        result = _validate_primitive_names(
            ["Button", "Button", "Card"],
            allowed_names=self._ALLOWED,
            context="page",
        )
        assert result.count("Button") == 1
        assert "Card" in result

    def test_whitespace_name_stripped_before_check(self):
        result = _validate_primitive_names(["  Button  "], allowed_names=self._ALLOWED, context="page")
        assert result == ["Button"]

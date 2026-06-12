"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/app_backend_admin_codegen.py
  factory_app/workflows/AppGenerator/tools/app_backend_admin_contract.py

Covers (app_backend_admin_codegen.py):
  _indent_block:
    - empty string → ""
    - single line → indented
    - multi-line → each line indented
    - blank lines NOT indented (preserved as empty)
    - zero spaces → unchanged

Covers (app_backend_admin_contract.py):
  _required_text:
    - non-empty string → stripped value
    - None → ValueError
    - empty → ValueError
    - whitespace only → ValueError

  _optional_text:
    - None → None
    - empty string → None
    - whitespace only → None
    - non-empty → stripped

  _string_list (admin_contract version):
    - None → []
    - valid list → deduplicated and stripped
    - non-list (not None) → ValueError  ← key difference from projection._string_list
    - duplicates removed
    - whitespace strings excluded
"""
from __future__ import annotations

import pytest

from factory_app.workflows.AppGenerator.tools.app_backend_admin_codegen import _indent_block
from factory_app.workflows.AppGenerator.tools.app_backend_admin_contract import (
    _optional_text,
    _required_text,
    _string_list,
)

# ---------------------------------------------------------------------------
# 1. _indent_block
# ---------------------------------------------------------------------------

class TestIndentBlock:
    def test_empty_string_returns_empty(self):
        assert _indent_block("", 4) == ""

    def test_single_line_indented(self):
        result = _indent_block("hello", 4)
        assert result == "    hello"

    def test_multi_line_each_indented(self):
        text = "line1\nline2\nline3"
        result = _indent_block(text, 2)
        lines = result.split("\n")
        assert lines[0] == "  line1"
        assert lines[1] == "  line2"
        assert lines[2] == "  line3"

    def test_blank_lines_not_indented(self):
        # Blank lines preserved without adding spaces
        text = "line1\n\nline3"
        result = _indent_block(text, 4)
        lines = result.split("\n")
        assert lines[0] == "    line1"
        assert lines[1] == ""  # blank line stays blank
        assert lines[2] == "    line3"

    def test_zero_spaces_unchanged(self):
        result = _indent_block("hello world", 0)
        assert result == "hello world"

    def test_preserves_leading_indentation_in_content(self):
        text = "  already_indented"
        result = _indent_block(text, 4)
        assert result == "      already_indented"


# ---------------------------------------------------------------------------
# 2. _required_text (admin_contract.py)
# ---------------------------------------------------------------------------

class TestRequiredTextAdminContract:
    def test_non_empty_string_returned(self):
        assert _required_text("billing", field_name="id") == "billing"

    def test_strips_whitespace(self):
        assert _required_text("  billing  ", field_name="id") == "billing"

    def test_none_raises(self):
        with pytest.raises(ValueError):
            _required_text(None, field_name="id")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            _required_text("", field_name="id")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError):
            _required_text("   ", field_name="id")


# ---------------------------------------------------------------------------
# 3. _optional_text (admin_contract.py)
# ---------------------------------------------------------------------------

class TestOptionalTextAdminContract:
    def test_none_returns_none(self):
        assert _optional_text(None) is None

    def test_empty_string_returns_none(self):
        assert _optional_text("") is None

    def test_whitespace_only_returns_none(self):
        assert _optional_text("   ") is None

    def test_non_empty_returned(self):
        assert _optional_text("my description") == "my description"

    def test_strips_whitespace(self):
        assert _optional_text("  hello  ") == "hello"


# ---------------------------------------------------------------------------
# 4. _string_list (admin_contract.py — raises on non-list)
# ---------------------------------------------------------------------------

class TestStringListAdminContract:
    def test_none_returns_empty(self):
        assert _string_list(None) == []

    def test_valid_list_returned(self):
        result = _string_list(["ops.read", "ops.write"])
        assert result == ["ops.read", "ops.write"]

    def test_non_list_raises_value_error(self):
        # Key difference from projection._string_list which returns []
        with pytest.raises(ValueError, match="must be a list"):
            _string_list("not_a_list")

    def test_duplicates_removed(self):
        result = _string_list(["ops.read", "ops.read", "ops.write"])
        assert result.count("ops.read") == 1

    def test_whitespace_stripped(self):
        result = _string_list(["  ops.read  "])
        assert result == ["ops.read"]

    def test_empty_strings_excluded(self):
        result = _string_list(["ops.read", "  ", "ops.write"])
        assert "  " not in result
        assert "ops.read" in result
        assert "ops.write" in result

    def test_order_preserved(self):
        result = _string_list(["c", "a", "b"])
        assert result == ["c", "a", "b"]

    def test_empty_list_returns_empty(self):
        assert _string_list([]) == []

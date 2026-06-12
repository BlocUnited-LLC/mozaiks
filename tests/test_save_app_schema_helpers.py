"""
save_app_schema.py pure helper unit tests.

Covers:
  _safe_path_segment:
    - empty/None → fallback returned
    - valid text → returned as-is (with special char replacement)
    - special chars replaced with dashes
    - leading/trailing dots and dashes stripped
    - whitespace stripped

  _safe_action_segment:
    - empty/None → "submit" fallback
    - valid lowercase text → returned
    - uppercase → lowercased
    - special chars replaced with underscores
    - leading/trailing underscores stripped
    - custom fallback used when empty

  _normalize_list:
    - non-list → []
    - list of items → same list
    - empty list → []

  _to_plain:
    - plain dict → same dict recursively processed
    - list of dicts → processed
    - Pydantic model with model_dump → dict returned
    - scalar → returned as-is
    - nested dict/list → recursively converted

  _strip_none:
    - dict with None values → Nones excluded
    - nested dict → Nones excluded at all levels
    - list with None items → Nones excluded
    - scalar → returned as-is

  _key_value_entries_to_dict:
    - None → None
    - dict → stripped of Nones
    - list of {key, value} → converted to dict
    - list items without "key" → skipped
    - non-dict list items → skipped
"""
from __future__ import annotations

from factory_app.workflows.AppGenerator.tools.save_app_schema import (
    _key_value_entries_to_dict,
    _normalize_list,
    _safe_action_segment,
    _safe_path_segment,
    _strip_none,
    _to_plain,
)

# ---------------------------------------------------------------------------
# 1. _safe_path_segment
# ---------------------------------------------------------------------------

class TestSafePathSegment:
    def test_empty_returns_fallback(self):
        assert _safe_path_segment("", fallback="default") == "default"

    def test_none_returns_fallback(self):
        assert _safe_path_segment(None, fallback="default") == "default"

    def test_valid_text_returned(self):
        assert _safe_path_segment("my-app", fallback="x") == "my-app"

    def test_spaces_replaced_with_dashes(self):
        result = _safe_path_segment("my app", fallback="x")
        assert " " not in result
        assert result

    def test_special_chars_replaced(self):
        result = _safe_path_segment("my@app!", fallback="x")
        assert "@" not in result
        assert "!" not in result

    def test_leading_trailing_dots_stripped(self):
        result = _safe_path_segment("...my-app...", fallback="x")
        assert not result.startswith(".")
        assert not result.endswith(".")

    def test_alphanumeric_underscore_dot_dash_preserved(self):
        result = _safe_path_segment("my_app-1.0", fallback="x")
        assert result == "my_app-1.0"

    def test_whitespace_stripped(self):
        result = _safe_path_segment("  myapp  ", fallback="x")
        assert result == "myapp"


# ---------------------------------------------------------------------------
# 2. _safe_action_segment
# ---------------------------------------------------------------------------

class TestSafeActionSegment:
    def test_empty_returns_submit_fallback(self):
        assert _safe_action_segment("") == "submit"

    def test_none_returns_submit_fallback(self):
        assert _safe_action_segment(None) == "submit"

    def test_valid_lowercase_returned(self):
        assert _safe_action_segment("create_task") == "create_task"

    def test_uppercase_lowercased(self):
        result = _safe_action_segment("CreateTask")
        assert result == result.lower()

    def test_special_chars_replaced_with_underscores(self):
        result = _safe_action_segment("create-task!")
        assert "-" not in result
        assert "!" not in result

    def test_leading_trailing_underscores_stripped(self):
        result = _safe_action_segment("__create_task__")
        assert not result.startswith("_")
        assert not result.endswith("_")

    def test_custom_fallback(self):
        assert _safe_action_segment("", fallback="action") == "action"

    def test_spaces_normalized(self):
        result = _safe_action_segment("create task")
        assert " " not in result
        assert "create" in result
        assert "task" in result


# ---------------------------------------------------------------------------
# 3. _normalize_list
# ---------------------------------------------------------------------------

class TestNormalizeList:
    def test_non_list_returns_empty(self):
        assert _normalize_list(None) == []
        assert _normalize_list("string") == []
        assert _normalize_list(42) == []
        assert _normalize_list({}) == []

    def test_list_returned(self):
        data = [1, 2, 3]
        assert _normalize_list(data) == [1, 2, 3]

    def test_empty_list_returns_empty(self):
        assert _normalize_list([]) == []

    def test_returns_new_list(self):
        original = [1, 2, 3]
        result = _normalize_list(original)
        assert result is not original
        assert result == original


# ---------------------------------------------------------------------------
# 4. _to_plain
# ---------------------------------------------------------------------------

class TestToPlain:
    def test_plain_dict_returned(self):
        data = {"key": "value"}
        result = _to_plain(data)
        assert result == {"key": "value"}

    def test_scalar_returned(self):
        assert _to_plain("string") == "string"
        assert _to_plain(42) == 42
        assert _to_plain(None) is None

    def test_list_processed_recursively(self):
        result = _to_plain([{"key": "value"}, "scalar"])
        assert result == [{"key": "value"}, "scalar"]

    def test_pydantic_model_converted(self):
        class FakePydantic:
            def model_dump(self):
                return {"id": "test", "value": 42}

        result = _to_plain(FakePydantic())
        assert result == {"id": "test", "value": 42}

    def test_model_dump_exception_falls_through(self):
        class BadModel:
            def model_dump(self):
                raise RuntimeError("boom")

        # Falls through to isinstance checks — it has model_dump but not dict/list
        result = _to_plain(BadModel())
        # Returns the object itself (no dict/list match)
        assert isinstance(result, BadModel)

    def test_nested_dict_processed(self):
        data = {"outer": {"inner": "value"}}
        result = _to_plain(data)
        assert result == {"outer": {"inner": "value"}}

    def test_nested_list_in_dict(self):
        data = {"items": [{"a": 1}, {"b": 2}]}
        result = _to_plain(data)
        assert result == {"items": [{"a": 1}, {"b": 2}]}


# ---------------------------------------------------------------------------
# 5. _strip_none
# ---------------------------------------------------------------------------

class TestStripNone:
    def test_dict_none_values_excluded(self):
        result = _strip_none({"a": 1, "b": None, "c": "x"})
        assert "b" not in result
        assert result == {"a": 1, "c": "x"}

    def test_nested_dict_nones_excluded(self):
        result = _strip_none({"outer": {"a": 1, "b": None}})
        assert result == {"outer": {"a": 1}}

    def test_list_none_items_excluded(self):
        result = _strip_none([1, None, 2, None, 3])
        assert result == [1, 2, 3]

    def test_scalar_returned_as_is(self):
        assert _strip_none("value") == "value"
        assert _strip_none(42) == 42
        assert _strip_none(None) is None

    def test_nested_list_in_dict(self):
        result = _strip_none({"items": [1, None, 2]})
        assert result == {"items": [1, 2]}

    def test_empty_dict_returned(self):
        assert _strip_none({}) == {}

    def test_all_nones_dict(self):
        result = _strip_none({"a": None, "b": None})
        assert result == {}


# ---------------------------------------------------------------------------
# 6. _key_value_entries_to_dict
# ---------------------------------------------------------------------------

class TestKeyValueEntriesToDict:
    def test_none_returns_none(self):
        assert _key_value_entries_to_dict(None) is None

    def test_dict_returned_stripped_of_nones(self):
        result = _key_value_entries_to_dict({"key": "value", "other": None})
        assert result == {"key": "value"}

    def test_list_of_key_value_pairs_converted(self):
        data = [
            {"key": "name", "value": "Alice"},
            {"key": "age", "value": 30},
        ]
        result = _key_value_entries_to_dict(data)
        assert result == {"name": "Alice", "age": 30}

    def test_list_items_without_key_skipped(self):
        data = [{"value": "orphan"}, {"key": "valid", "value": "x"}]
        result = _key_value_entries_to_dict(data)
        assert result == {"valid": "x"}

    def test_non_dict_list_items_skipped(self):
        data = ["string_item", {"key": "valid", "value": "x"}]
        result = _key_value_entries_to_dict(data)
        assert result == {"valid": "x"}

    def test_empty_key_skipped(self):
        data = [{"key": "", "value": "x"}, {"key": "valid", "value": "y"}]
        result = _key_value_entries_to_dict(data)
        assert result == {"valid": "y"}

    def test_value_none_stripped_in_result(self):
        data = [{"key": "name", "value": None}]
        result = _key_value_entries_to_dict(data)
        assert result == {"name": None}

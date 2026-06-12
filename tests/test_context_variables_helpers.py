"""
Context variables pure helper unit tests.

Covers:
  _is_within_root:
    - path equals root → True
    - path is a child of root → True
    - path is outside root → False
    - path is a sibling (same parent, different name) → False

  _context_to_dict:
    - plain dict → copy returned
    - object with .to_dict() method → called and returned
    - object with .data attribute that is dict → returned
    - object with neither → {}
    - dict with nested values → shallow copy

  _database_defaults:
    - None raw_section → None
    - raw_section not a dict → None
    - dict with "default_database_name" key (string) → returned
    - dict with "default_database" key (string) → returned
    - dict without either key → None
    - "default_database_name" takes priority over "default_database"
    - dict with None value → falls through / None returned
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from mozaiksai.core.workflow.context.variables import (
    _context_to_dict,
    _database_defaults,
    _is_within_root,
)

# ---------------------------------------------------------------------------
# 1. _is_within_root
# ---------------------------------------------------------------------------

class TestIsWithinRoot:
    def test_same_path_returns_true(self, tmp_path):
        assert _is_within_root(tmp_path, tmp_path) is True

    def test_child_path_returns_true(self, tmp_path):
        child = tmp_path / "subdir" / "file.py"
        assert _is_within_root(child, tmp_path) is True

    def test_outside_root_returns_false(self, tmp_path):
        # Two separate temp dirs
        with tempfile.TemporaryDirectory() as other:
            other_path = Path(other)
            assert _is_within_root(other_path, tmp_path) is False

    def test_sibling_dir_returns_false(self, tmp_path):
        sibling = tmp_path.parent / "sibling"
        assert _is_within_root(sibling, tmp_path) is False

    def test_deeply_nested_child_returns_true(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c" / "d" / "file.txt"
        assert _is_within_root(deep, tmp_path) is True


# ---------------------------------------------------------------------------
# 2. _context_to_dict
# ---------------------------------------------------------------------------

class _HasToDict:
    def to_dict(self):
        return {"from_to_dict": True}


class _HasData:
    data = {"from_data": "yes"}


class _HasDataNonDict:
    data = "not a dict"


class TestContextToDict:
    def test_plain_dict_returned_as_copy(self):
        d = {"a": 1, "b": 2}
        result = _context_to_dict(d)
        assert result == d
        assert result is not d

    def test_object_with_to_dict_called(self):
        result = _context_to_dict(_HasToDict())
        assert result == {"from_to_dict": True}

    def test_object_with_data_dict_returned(self):
        result = _context_to_dict(_HasData())
        assert result == {"from_data": "yes"}

    def test_object_with_non_dict_data_returns_empty(self):
        result = _context_to_dict(_HasDataNonDict())
        assert result == {}

    def test_none_input_returns_empty(self):
        assert _context_to_dict(None) == {}

    def test_string_input_returns_empty(self):
        assert _context_to_dict("not-a-dict") == {}

    def test_int_input_returns_empty(self):
        assert _context_to_dict(42) == {}

    def test_dict_with_nested_values_returned(self):
        d = {"key": {"nested": 1}, "list": [1, 2, 3]}
        result = _context_to_dict(d)
        assert result["key"] == {"nested": 1}
        assert result["list"] == [1, 2, 3]


# ---------------------------------------------------------------------------
# 3. _database_defaults
# ---------------------------------------------------------------------------

class TestDatabaseDefaults:
    def test_none_returns_none(self):
        assert _database_defaults(None) is None

    def test_non_dict_returns_none(self):
        assert _database_defaults("bad") is None

    def test_dict_with_default_database_name_key(self):
        assert _database_defaults({"default_database_name": "my_db"}) == "my_db"

    def test_dict_with_default_database_key(self):
        assert _database_defaults({"default_database": "fallback_db"}) == "fallback_db"

    def test_default_database_name_takes_priority(self):
        result = _database_defaults({"default_database_name": "primary", "default_database": "secondary"})
        assert result == "primary"

    def test_dict_without_either_key_returns_none(self):
        assert _database_defaults({"other_key": "value"}) is None

    def test_none_default_database_name_falls_through_to_default_database(self):
        assert _database_defaults({"default_database_name": None, "default_database": "fallback"}) == "fallback"

    def test_empty_dict_returns_none(self):
        assert _database_defaults({}) is None

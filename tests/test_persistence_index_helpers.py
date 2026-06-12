"""
mozaiksai/core/runtime/persistence/indexes.py pure helper unit tests.

Covers:
  _is_non_empty_string:
    - non-string → False
    - empty string → False
    - whitespace-only string → False
    - valid string → True

  _normalize_index_keys:
    - non-list → DatabaseIndexApplyError raised
    - empty list → DatabaseIndexApplyError raised
    - dict with field + order → (field, order) tuple
    - [field, order] pair → (field, order) tuple
    - order=1 → (field, 1)
    - order=-1 → (field, -1)
    - order=2 → DatabaseIndexApplyError raised
    - non-integer order → DatabaseIndexApplyError raised
    - missing field → DatabaseIndexApplyError raised
    - non-string field → DatabaseIndexApplyError raised
    - multiple keys → all normalized

  _normalize_index_spec:
    - non-dict → DatabaseIndexApplyError raised
    - missing name → DatabaseIndexApplyError raised
    - valid spec → _NormalizedIndexSpec returned
    - "background" option excluded from options
    - name and keys excluded from options
    - additional options included

  _index_spec_dict:
    - spec with no extra options → {"name": ..., "keys": [...]}
    - spec with options → options merged into result
"""
from __future__ import annotations

import pytest

from mozaiksai.core.runtime.persistence.indexes import (
    DatabaseIndexApplyError,
    _index_spec_dict,
    _is_non_empty_string,
    _normalize_index_keys,
    _normalize_index_spec,
    _NormalizedIndexSpec,
)

# ---------------------------------------------------------------------------
# 1. _is_non_empty_string
# ---------------------------------------------------------------------------

class TestIsNonEmptyString:
    def test_non_string_returns_false(self):
        assert _is_non_empty_string(None) is False
        assert _is_non_empty_string(42) is False
        assert _is_non_empty_string([]) is False

    def test_empty_string_returns_false(self):
        assert _is_non_empty_string("") is False

    def test_whitespace_only_returns_false(self):
        assert _is_non_empty_string("   ") is False

    def test_valid_string_returns_true(self):
        assert _is_non_empty_string("hello") is True

    def test_single_char_returns_true(self):
        assert _is_non_empty_string("x") is True


# ---------------------------------------------------------------------------
# 2. _normalize_index_keys
# ---------------------------------------------------------------------------

class TestNormalizeIndexKeys:
    def test_non_list_raises(self):
        with pytest.raises(DatabaseIndexApplyError, match="non-empty list"):
            _normalize_index_keys("not_a_list", "spec")

    def test_none_raises(self):
        with pytest.raises(DatabaseIndexApplyError):
            _normalize_index_keys(None, "spec")

    def test_empty_list_raises(self):
        with pytest.raises(DatabaseIndexApplyError, match="non-empty list"):
            _normalize_index_keys([], "spec")

    def test_dict_with_field_and_order(self):
        result = _normalize_index_keys([{"field": "name", "order": 1}], "spec")
        assert result == [("name", 1)]

    def test_list_pair_format(self):
        result = _normalize_index_keys([["email", -1]], "spec")
        assert result == [("email", -1)]

    def test_tuple_pair_format(self):
        result = _normalize_index_keys([("created_at", -1)], "spec")
        assert result == [("created_at", -1)]

    def test_order_1_valid(self):
        result = _normalize_index_keys([{"field": "name", "order": 1}], "spec")
        assert result[0][1] == 1

    def test_order_minus_1_valid(self):
        result = _normalize_index_keys([{"field": "name", "order": -1}], "spec")
        assert result[0][1] == -1

    def test_order_2_raises(self):
        with pytest.raises(DatabaseIndexApplyError, match="order must be 1 or -1"):
            _normalize_index_keys([{"field": "name", "order": 2}], "spec")

    def test_non_integer_order_raises(self):
        with pytest.raises(DatabaseIndexApplyError, match="order must be an integer"):
            _normalize_index_keys([{"field": "name", "order": "asc"}], "spec")

    def test_missing_field_raises(self):
        with pytest.raises(DatabaseIndexApplyError, match="field is required"):
            _normalize_index_keys([{"field": "", "order": 1}], "spec")

    def test_multiple_keys_normalized(self):
        keys = [{"field": "name", "order": 1}, {"field": "created_at", "order": -1}]
        result = _normalize_index_keys(keys, "spec")
        assert len(result) == 2
        assert ("name", 1) in result
        assert ("created_at", -1) in result

    def test_default_order_is_1(self):
        result = _normalize_index_keys([{"field": "name"}], "spec")
        assert result == [("name", 1)]

    def test_field_stripped(self):
        result = _normalize_index_keys([{"field": "  name  ", "order": 1}], "spec")
        assert result == [("name", 1)]


# ---------------------------------------------------------------------------
# 3. _normalize_index_spec
# ---------------------------------------------------------------------------

class TestNormalizeIndexSpec:
    def test_non_dict_raises(self):
        with pytest.raises(DatabaseIndexApplyError, match="must be an object"):
            _normalize_index_spec("not_a_dict", "spec")

    def test_missing_name_raises(self):
        with pytest.raises(DatabaseIndexApplyError, match="name is required"):
            _normalize_index_spec({"keys": [{"field": "name", "order": 1}]}, "spec")

    def test_empty_name_raises(self):
        with pytest.raises(DatabaseIndexApplyError, match="name is required"):
            _normalize_index_spec({"name": "", "keys": [{"field": "name", "order": 1}]}, "spec")

    def test_valid_spec_returned(self):
        raw = {"name": "idx_name", "keys": [{"field": "name", "order": 1}]}
        result = _normalize_index_spec(raw, "spec")
        assert isinstance(result, _NormalizedIndexSpec)
        assert result.name == "idx_name"
        assert result.keys == [("name", 1)]

    def test_background_option_excluded(self):
        raw = {
            "name": "idx_name",
            "keys": [{"field": "name", "order": 1}],
            "background": True,
            "unique": True,
        }
        result = _normalize_index_spec(raw, "spec")
        assert "background" not in result.options
        assert result.options.get("unique") is True

    def test_name_and_keys_excluded_from_options(self):
        raw = {"name": "idx_name", "keys": [{"field": "name", "order": 1}]}
        result = _normalize_index_spec(raw, "spec")
        assert "name" not in result.options
        assert "keys" not in result.options

    def test_additional_options_included(self):
        raw = {
            "name": "idx_email",
            "keys": [{"field": "email", "order": 1}],
            "unique": True,
            "sparse": True,
        }
        result = _normalize_index_spec(raw, "spec")
        assert result.options["unique"] is True
        assert result.options["sparse"] is True

    def test_none_option_values_excluded(self):
        raw = {
            "name": "idx_name",
            "keys": [{"field": "name", "order": 1}],
            "optional_opt": None,
        }
        result = _normalize_index_spec(raw, "spec")
        assert "optional_opt" not in result.options


# ---------------------------------------------------------------------------
# 4. _index_spec_dict
# ---------------------------------------------------------------------------

class TestIndexSpecDict:
    def test_basic_spec_dict(self):
        spec = _NormalizedIndexSpec(
            name="idx_name",
            keys=[("name", 1)],
            options={},
        )
        result = _index_spec_dict(spec)
        assert result == {"name": "idx_name", "keys": [("name", 1)]}

    def test_options_merged_into_result(self):
        spec = _NormalizedIndexSpec(
            name="idx_email",
            keys=[("email", 1)],
            options={"unique": True, "sparse": False},
        )
        result = _index_spec_dict(spec)
        assert result["unique"] is True
        assert result["sparse"] is False

    def test_keys_is_list(self):
        spec = _NormalizedIndexSpec(
            name="idx_compound",
            keys=[("name", 1), ("created_at", -1)],
            options={},
        )
        result = _index_spec_dict(spec)
        assert isinstance(result["keys"], list)
        assert result["keys"] == [("name", 1), ("created_at", -1)]

"""
ExistingAppDiscovery app_context_mapping.py pure helper unit tests.

Covers:
  _as_mapping:
    - Mapping (dict) → shallow dict copy
    - non-Mapping → {}
    - None → {}

  _as_list:
    - list → same list as new list
    - non-list → []
    - None → []

  _clean:
    - string stripped → stripped string
    - None/falsy → ""
    - integer → str of integer

  _slug:
    - valid text → lowercased with special chars replaced by underscores
    - leading/trailing underscores stripped
    - empty → fallback returned
    - fallback parameter respected

  _stable_id:
    - prefix only → slugged prefix
    - prefix + parts → joined with underscores
    - empty parts skipped
    - result capped at 120 chars

  _unique_strings:
    - empty list → []
    - duplicates removed (first kept)
    - empty strings excluded
    - non-string values cleaned via _clean

  _split_stack:
    - empty/None → []
    - comma-separated → list of parts
    - semicolon-separated → list of parts
    - pipe-separated → list of parts
    - duplicates removed
    - whitespace around parts stripped

  _map_adoption_path:
    - "embed" → AdoptionPath.AUGMENT
    - "bridge" → AdoptionPath.ADAPTER
    - "ecosystem" → AdoptionPath.OVERLAY
    - "gradual_modernization" → AdoptionPath.GRADUAL_MODERNIZATION
    - "native_migration" → AdoptionPath.GRADUAL_MODERNIZATION
    - unknown → AdoptionPath.OBSERVE

  _parse_decomposition_plan:
    - Mapping → dict
    - valid JSON string → dict
    - invalid JSON string → None
    - empty string → None
    - None → None
    - JSON string that isn't a dict → None
"""
from __future__ import annotations

from factory_app.workflows.ExistingAppDiscovery.tools.app_context_mapping import (
    _as_list,
    _as_mapping,
    _clean,
    _map_adoption_path,
    _parse_decomposition_plan,
    _slug,
    _split_stack,
    _stable_id,
    _unique_strings,
)
from mozaiksai.core.app_context.models import AdoptionPath

# ---------------------------------------------------------------------------
# 1. _as_mapping
# ---------------------------------------------------------------------------

class TestAsMapping:
    def test_dict_returned_as_dict(self):
        data = {"key": "value"}
        result = _as_mapping(data)
        assert result == {"key": "value"}

    def test_non_mapping_returns_empty(self):
        assert _as_mapping(None) == {}
        assert _as_mapping("string") == {}
        assert _as_mapping([1, 2]) == {}
        assert _as_mapping(42) == {}

    def test_mapping_subclass_accepted(self):
        from collections import OrderedDict
        data = OrderedDict({"a": 1, "b": 2})
        result = _as_mapping(data)
        assert result == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# 2. _as_list
# ---------------------------------------------------------------------------

class TestAsList:
    def test_list_returned(self):
        data = [1, 2, 3]
        result = _as_list(data)
        assert result == [1, 2, 3]

    def test_non_list_returns_empty(self):
        assert _as_list(None) == []
        assert _as_list("string") == []
        assert _as_list({"key": "val"}) == []

    def test_empty_list_returned(self):
        assert _as_list([]) == []

    def test_returns_new_list(self):
        original = [1, 2]
        result = _as_list(original)
        assert result is not original
        assert result == original


# ---------------------------------------------------------------------------
# 3. _clean
# ---------------------------------------------------------------------------

class TestClean:
    def test_string_stripped(self):
        assert _clean("  hello  ") == "hello"

    def test_none_returns_empty(self):
        assert _clean(None) == ""

    def test_falsy_returns_empty(self):
        assert _clean("") == ""
        assert _clean(0) == ""

    def test_integer_value_stringified(self):
        assert _clean(42) == "42"

    def test_plain_string(self):
        assert _clean("hello") == "hello"


# ---------------------------------------------------------------------------
# 4. _slug
# ---------------------------------------------------------------------------

class TestSlug:
    def test_simple_text_lowercased(self):
        assert _slug("Hello") == "hello"

    def test_special_chars_replaced_with_underscore(self):
        result = _slug("my app!")
        assert " " not in result
        assert "!" not in result

    def test_leading_trailing_underscores_stripped(self):
        result = _slug("  !my_app!  ")
        assert not result.startswith("_")
        assert not result.endswith("_")

    def test_empty_returns_fallback(self):
        assert _slug("") == "item"
        assert _slug(None) == "item"

    def test_custom_fallback(self):
        assert _slug("", fallback="default") == "default"

    def test_valid_identifier_preserved(self):
        assert _slug("my_app") == "my_app"

    def test_dashes_replaced_with_underscore(self):
        result = _slug("my-app-v2")
        assert "-" not in result


# ---------------------------------------------------------------------------
# 5. _stable_id
# ---------------------------------------------------------------------------

class TestStableId:
    def test_prefix_only(self):
        result = _stable_id("service")
        assert result == "service"

    def test_prefix_with_part(self):
        result = _stable_id("service", "api")
        assert "service" in result
        assert "api" in result

    def test_empty_parts_skipped(self):
        result = _stable_id("prefix", "", None, "valid")
        assert "valid" in result
        assert "__" not in result  # no empty segments

    def test_result_capped_at_120_chars(self):
        long_part = "a" * 200
        result = _stable_id("prefix", long_part)
        assert len(result) <= 120

    def test_parts_joined_with_underscore(self):
        result = _stable_id("service", "users", "api")
        parts = result.split("_")
        assert len(parts) >= 2


# ---------------------------------------------------------------------------
# 6. _unique_strings
# ---------------------------------------------------------------------------

class TestUniqueStrings:
    def test_empty_list_returns_empty(self):
        assert _unique_strings([]) == []

    def test_unique_items_preserved_in_order(self):
        result = _unique_strings(["a", "b", "c"])
        assert result == ["a", "b", "c"]

    def test_duplicates_first_occurrence_kept(self):
        result = _unique_strings(["a", "b", "a", "c"])
        assert result == ["a", "b", "c"]

    def test_empty_strings_excluded(self):
        result = _unique_strings(["a", "", "b", "  "])
        assert "" not in result
        assert "  " not in result
        assert result == ["a", "b"]

    def test_non_string_cleaned_via_clean(self):
        result = _unique_strings([42])
        assert "42" in result


# ---------------------------------------------------------------------------
# 7. _split_stack
# ---------------------------------------------------------------------------

class TestSplitStack:
    def test_none_returns_empty(self):
        assert _split_stack(None) == []

    def test_empty_string_returns_empty(self):
        assert _split_stack("") == []

    def test_comma_separated(self):
        result = _split_stack("Python, FastAPI, MongoDB")
        assert "Python" in result
        assert "FastAPI" in result
        assert "MongoDB" in result

    def test_semicolon_separated(self):
        result = _split_stack("React;Node.js;PostgreSQL")
        assert "React" in result
        assert "Node.js" in result

    def test_pipe_separated(self):
        result = _split_stack("Go|gRPC|Redis")
        assert "Go" in result
        assert "gRPC" in result

    def test_slash_separated(self):
        result = _split_stack("Python/FastAPI")
        assert "Python" in result
        assert "FastAPI" in result

    def test_whitespace_stripped(self):
        result = _split_stack("  Python  ,  FastAPI  ")
        assert "Python" in result
        assert "FastAPI" in result

    def test_duplicates_removed(self):
        result = _split_stack("Python, Python, FastAPI")
        assert result.count("Python") == 1


# ---------------------------------------------------------------------------
# 8. _map_adoption_path
# ---------------------------------------------------------------------------

class TestMapAdoptionPath:
    def test_embed_maps_to_augment(self):
        assert _map_adoption_path("embed") == AdoptionPath.AUGMENT

    def test_bridge_maps_to_adapter(self):
        assert _map_adoption_path("bridge") == AdoptionPath.ADAPTER

    def test_ecosystem_maps_to_overlay(self):
        assert _map_adoption_path("ecosystem") == AdoptionPath.OVERLAY

    def test_gradual_modernization(self):
        assert _map_adoption_path("gradual_modernization") == AdoptionPath.GRADUAL_MODERNIZATION

    def test_native_migration_maps_to_gradual_modernization(self):
        assert _map_adoption_path("native_migration") == AdoptionPath.GRADUAL_MODERNIZATION

    def test_unknown_maps_to_observe(self):
        assert _map_adoption_path("unknown") == AdoptionPath.OBSERVE

    def test_empty_string_maps_to_observe(self):
        assert _map_adoption_path("") == AdoptionPath.OBSERVE


# ---------------------------------------------------------------------------
# 9. _parse_decomposition_plan
# ---------------------------------------------------------------------------

class TestParseDecompositionPlan:
    def test_mapping_returns_dict(self):
        data = {"modules": ["tasks", "users"]}
        result = _parse_decomposition_plan(data)
        assert result == {"modules": ["tasks", "users"]}

    def test_valid_json_string_returns_dict(self):
        import json
        result = _parse_decomposition_plan(json.dumps({"modules": ["tasks"]}))
        assert result == {"modules": ["tasks"]}

    def test_invalid_json_returns_none(self):
        assert _parse_decomposition_plan("{broken json") is None

    def test_empty_string_returns_none(self):
        assert _parse_decomposition_plan("") is None

    def test_whitespace_string_returns_none(self):
        assert _parse_decomposition_plan("   ") is None

    def test_none_returns_none(self):
        assert _parse_decomposition_plan(None) is None

    def test_json_list_returns_none(self):
        # JSON that isn't a Mapping → None
        import json
        assert _parse_decomposition_plan(json.dumps(["not", "a", "dict"])) is None

    def test_json_scalar_returns_none(self):
        import json
        assert _parse_decomposition_plan(json.dumps("just_a_string")) is None

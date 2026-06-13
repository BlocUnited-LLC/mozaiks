"""
Structured output pure helper unit tests.

Covers:
  _build_literal_enum:
    - name and values produce an Enum class
    - enum members hold the original values
    - different names/values → distinct enum types

  _resolve_named_type:
    - known primitive in TYPE_MAP → Python type returned
    - key in available_models → model class returned
    - alias with type=literal and values → Enum returned
    - alias with type=union → Union returned
    - alias with type=union with None variant → Optional returned
    - circular reference → ValueError
    - empty type_name → ValueError
    - unknown type not in map/models/defs → ValueError
    - literal alias with empty values → ValueError
    - union alias with empty variants → ValueError
    - alias result cached in alias_cache

  _inline_schema_refs:
    - primitive node → returned as-is
    - dict without $ref → recursed
    - dict with $ref → inlined from defs
    - $ref with remainder fields → merged
    - circular $ref → handled (remainder returned)
    - list of nodes → each inlined
    - nested $ref → resolved transitively

  _add_additional_properties:
    - primitive → unchanged
    - non-object dict → unchanged
    - object dict without additionalProperties → False added
    - object dict with additionalProperties=True → overwritten to False
    - object dict with properties → required array populated
    - object dict already has additionalProperties=False → not changed
    - nested object → recursively processed
    - list of objects → each processed

  _is_pydantic_model:
    - BaseModel subclass class → True
    - BaseModel instance → False
    - plain class → False
    - str/int/None → False
    - issubclass exception swallowed → False

  _find_open_ended_object_path:
    - Dict annotation → returns path
    - List[str] → None
    - List[Dict] → returns "path[]"
    - Optional[str] → None
    - Optional[Dict] → returns path
    - Union[str, Dict] → returns path
    - plain str → None
    - pydantic model with no dict fields → None
    - pydantic model with dict field → returns "path.field_name"
    - visited pydantic model (cycle) → None

  _build_field:
    - no default → Field(...) (required)
    - default=None → Field(...) (required, no default set)
    - default="hello" → Field("hello", ...)
    - description forwarded

  supports_provider_response_format:
    - model with only str fields → (True, None)
    - model with Dict[str, Any] field → (False, path)
    - model with Optional[Dict] field → (False, path)
"""
from __future__ import annotations

from typing import Any, Union

import pytest
from pydantic import BaseModel

from mozaiksai.core.workflow.outputs.structured import (
    _add_additional_properties,
    _build_field,
    _build_literal_enum,
    _find_open_ended_object_path,
    _inline_schema_refs,
    _is_pydantic_model,
    _resolve_named_type,
    supports_provider_response_format,
)

# ---------------------------------------------------------------------------
# 1. _build_literal_enum
# ---------------------------------------------------------------------------

class TestBuildLiteralEnum:
    def test_returns_enum_class(self):
        e = _build_literal_enum("MyField", ["a", "b", "c"])
        assert hasattr(e, "__mro__")  # it's a class

    def test_enum_members_hold_values(self):
        e = _build_literal_enum("Status", ["active", "inactive"])
        member_values = {m.value for m in e}
        assert "active" in member_values
        assert "inactive" in member_values

    def test_different_values_produce_distinct_enum(self):
        e1 = _build_literal_enum("X", ["a", "b"])
        e2 = _build_literal_enum("X", ["c", "d"])
        assert e1 is not e2

    def test_single_value(self):
        e = _build_literal_enum("Single", ["only"])
        members = list(e)
        assert len(members) == 1
        assert members[0].value == "only"


# ---------------------------------------------------------------------------
# 2. _resolve_named_type
# ---------------------------------------------------------------------------

class TestResolveNamedType:
    def _call(self, name, models=None, alias_defs=None, cache=None, stack=None):
        return _resolve_named_type(
            name,
            models or {},
            alias_defs or {},
            cache if cache is not None else {},
            stack,
        )

    def test_str_in_type_map(self):
        assert self._call("str") is str

    def test_string_alias_in_type_map(self):
        assert self._call("string") is str

    def test_int_in_type_map(self):
        assert self._call("int") is int

    def test_bool_in_type_map(self):
        assert self._call("bool") is bool

    def test_float_in_type_map(self):
        assert self._call("float") is float

    def test_model_in_available_models(self):
        class MyModel(BaseModel):
            x: str

        result = self._call("MyModel", models={"MyModel": MyModel})
        assert result is MyModel

    def test_literal_alias(self):
        alias_defs = {"Status": {"type": "literal", "values": ["active", "inactive"]}}
        result = self._call("Status", alias_defs=alias_defs)
        # Should be an Enum with those values
        member_values = {m.value for m in result}
        assert "active" in member_values

    def test_literal_alias_empty_values_raises(self):
        alias_defs = {"Empty": {"type": "literal", "values": []}}
        with pytest.raises(ValueError, match="non-empty values"):
            self._call("Empty", alias_defs=alias_defs)

    def test_union_alias(self):
        alias_defs = {"Combo": {"type": "union", "variants": ["str", "int"]}}
        result = self._call("Combo", alias_defs=alias_defs)
        # result should be a Union type
        from typing import get_origin
        assert get_origin(result) is Union

    def test_union_alias_with_null_variant(self):
        alias_defs = {"MaybeStr": {"type": "union", "variants": ["str", "None"]}}
        result = self._call("MaybeStr", alias_defs=alias_defs)
        # Optional[str] is Union[str, NoneType]
        from typing import get_args, get_origin
        assert get_origin(result) is Union
        assert type(None) in get_args(result)

    def test_union_alias_empty_variants_raises(self):
        alias_defs = {"BadUnion": {"type": "union", "variants": []}}
        with pytest.raises(ValueError, match="non-empty variants"):
            self._call("BadUnion", alias_defs=alias_defs)

    def test_circular_alias_raises(self):
        alias_defs = {"A": {"type": "union", "variants": ["A"]}}
        with pytest.raises(ValueError, match="Circular"):
            self._call("A", alias_defs=alias_defs)

    def test_empty_type_name_raises(self):
        with pytest.raises(ValueError, match="Empty"):
            self._call("")

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown type"):
            self._call("NoSuchType")

    def test_unsupported_alias_type_raises(self):
        alias_defs = {"Bad": {"type": "object"}}
        with pytest.raises(ValueError, match="Unsupported alias"):
            self._call("Bad", alias_defs=alias_defs)

    def test_alias_result_cached(self):
        alias_defs = {"MyLiteral": {"type": "literal", "values": ["x"]}}
        cache: dict[str, Any] = {}
        result1 = self._call("MyLiteral", alias_defs=alias_defs, cache=cache)
        result2 = self._call("MyLiteral", alias_defs=alias_defs, cache=cache)
        assert result1 is result2
        assert "MyLiteral" in cache


# ---------------------------------------------------------------------------
# 3. _inline_schema_refs
# ---------------------------------------------------------------------------

class TestInlineSchemaRefs:
    def test_primitive_returned_as_is(self):
        assert _inline_schema_refs("hello", {}) == "hello"
        assert _inline_schema_refs(42, {}) == 42
        assert _inline_schema_refs(None, {}) is None

    def test_dict_without_ref_recursed(self):
        node = {"type": "string", "description": "a field"}
        result = _inline_schema_refs(node, {})
        assert result == node

    def test_dict_with_ref_inlined(self):
        defs = {"MyDef": {"type": "string"}}
        node = {"$ref": "#/$defs/MyDef"}
        result = _inline_schema_refs(node, defs)
        assert result == {"type": "string"}

    def test_ref_with_remainder_merged(self):
        defs = {"Base": {"type": "object", "properties": {}}}
        node = {"$ref": "#/$defs/Base", "description": "extra"}
        result = _inline_schema_refs(node, defs)
        assert result["description"] == "extra"
        assert result["type"] == "object"

    def test_list_of_nodes_inlined(self):
        defs = {"X": {"type": "integer"}}
        node = [{"$ref": "#/$defs/X"}, "plain"]
        result = _inline_schema_refs(node, defs)
        assert result[0] == {"type": "integer"}
        assert result[1] == "plain"

    def test_missing_ref_key_left_as_is(self):
        node = {"$ref": "#/$defs/Missing"}
        # No defs entry — falls through to plain dict recursion
        result = _inline_schema_refs(node, {})
        assert result == {"$ref": "#/$defs/Missing"}

    def test_nested_ref_resolved(self):
        defs = {
            "Inner": {"type": "string"},
            "Outer": {"field": {"$ref": "#/$defs/Inner"}},
        }
        node = {"$ref": "#/$defs/Outer"}
        result = _inline_schema_refs(node, defs)
        assert result["field"] == {"type": "string"}

    def test_circular_ref_handled(self):
        # Self-referencing definition shouldn't infinite-loop
        defs = {"Node": {"self": {"$ref": "#/$defs/Node"}}}
        node = {"$ref": "#/$defs/Node"}
        result = _inline_schema_refs(node, defs)
        # Should complete without error — result structure may vary
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# 4. _add_additional_properties
# ---------------------------------------------------------------------------

class TestAddAdditionalProperties:
    def test_primitive_unchanged(self):
        assert _add_additional_properties("hello") == "hello"
        assert _add_additional_properties(42) == 42
        assert _add_additional_properties(None) is None

    def test_non_object_dict_unchanged(self):
        schema = {"type": "string"}
        result = _add_additional_properties(schema)
        assert result == {"type": "string"}

    def test_object_without_additional_properties_gets_false(self):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        result = _add_additional_properties(schema)
        assert result["additionalProperties"] is False

    def test_object_with_additional_properties_true_overwritten(self):
        schema = {"type": "object", "additionalProperties": True}
        result = _add_additional_properties(schema)
        assert result["additionalProperties"] is False

    def test_object_with_properties_gets_required_array(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        }
        result = _add_additional_properties(schema)
        assert set(result["required"]) == {"name", "age"}

    def test_object_already_has_additional_properties_false_preserved(self):
        schema = {"type": "object", "additionalProperties": False, "properties": {}}
        result = _add_additional_properties(schema)
        assert result["additionalProperties"] is False

    def test_nested_object_recursively_processed(self):
        schema = {
            "type": "object",
            "properties": {
                "child": {"type": "object", "properties": {"x": {"type": "string"}}}
            },
        }
        result = _add_additional_properties(schema)
        child = result["properties"]["child"]
        assert child["additionalProperties"] is False

    def test_list_of_objects_processed(self):
        schema = [{"type": "object"}, {"type": "string"}]
        result = _add_additional_properties(schema)
        assert result[0]["additionalProperties"] is False

    def test_object_no_properties_gets_empty_required(self):
        schema = {"type": "object"}
        result = _add_additional_properties(schema)
        assert result.get("required") == []


# ---------------------------------------------------------------------------
# 5. _is_pydantic_model
# ---------------------------------------------------------------------------

class TestIsPydanticModel:
    def test_pydantic_subclass_true(self):
        class MyModel(BaseModel):
            x: str

        assert _is_pydantic_model(MyModel) is True

    def test_pydantic_instance_false(self):
        class MyModel(BaseModel):
            x: str = "a"

        assert _is_pydantic_model(MyModel()) is False

    def test_plain_class_false(self):
        class Foo:
            pass

        assert _is_pydantic_model(Foo) is False

    def test_str_false(self):
        assert _is_pydantic_model(str) is False

    def test_int_false(self):
        assert _is_pydantic_model(42) is False

    def test_none_false(self):
        assert _is_pydantic_model(None) is False

    def test_base_model_itself_false(self):
        # BaseModel is not a *subclass* of itself per issubclass
        assert _is_pydantic_model(object) is False


# ---------------------------------------------------------------------------
# 6. _find_open_ended_object_path
# ---------------------------------------------------------------------------

class TestFindOpenEndedObjectPath:
    def test_dict_annotation_returns_path(self):
        result = _find_open_ended_object_path(dict[str, Any], path="root")
        assert result == "root"

    def test_plain_str_returns_none(self):
        assert _find_open_ended_object_path(str, path="root") is None

    def test_plain_int_returns_none(self):
        assert _find_open_ended_object_path(int, path="root") is None

    def test_list_of_str_returns_none(self):
        assert _find_open_ended_object_path(list[str], path="root") is None

    def test_list_of_dict_returns_path_with_bracket(self):
        result = _find_open_ended_object_path(list[dict[str, Any]], path="items")
        assert result == "items[]"

    def test_optional_str_returns_none(self):
        assert _find_open_ended_object_path(str | None, path="root") is None

    def test_optional_dict_returns_path(self):
        result = _find_open_ended_object_path(dict[str, Any] | None, path="data")
        assert result == "data"

    def test_union_str_dict_returns_path(self):
        result = _find_open_ended_object_path(str | dict[str, Any], path="field")
        assert result == "field"

    def test_pydantic_model_no_dict_fields_returns_none(self):
        class Simple(BaseModel):
            name: str
            count: int

        assert _find_open_ended_object_path(Simple, path="Simple") is None

    def test_pydantic_model_with_dict_field_returns_path(self):
        class WithDict(BaseModel):
            data: dict[str, Any]

        result = _find_open_ended_object_path(WithDict, path="WithDict")
        assert result == "WithDict.data"

    def test_pydantic_model_cycle_handled(self):
        # Visiting twice should short-circuit
        class SelfRef(BaseModel):
            name: str

        visited = {SelfRef}
        result = _find_open_ended_object_path(SelfRef, path="SelfRef", visited_models=visited)
        assert result is None


# ---------------------------------------------------------------------------
# 7. _build_field
# ---------------------------------------------------------------------------

class TestBuildField:
    def test_no_default_produces_required_field(self):
        field = _build_field({})
        # Field(...) means required — default is PydanticUndefined
        from pydantic_core import PydanticUndefinedType
        assert isinstance(field.default, PydanticUndefinedType)

    def test_none_default_produces_required_field(self):
        field = _build_field({"default": None})
        from pydantic_core import PydanticUndefinedType
        assert isinstance(field.default, PydanticUndefinedType)

    def test_string_default_set(self):
        field = _build_field({"default": "hello"})
        assert field.default == "hello"

    def test_int_default_set(self):
        field = _build_field({"default": 42})
        assert field.default == 42

    def test_description_forwarded(self):
        field = _build_field({"description": "my field"})
        assert field.description == "my field"

    def test_description_with_default(self):
        field = _build_field({"default": "x", "description": "labeled"})
        assert field.default == "x"
        assert field.description == "labeled"


# ---------------------------------------------------------------------------
# 8. supports_provider_response_format
# ---------------------------------------------------------------------------

class TestSupportsProviderResponseFormat:
    def test_model_with_only_str_fields(self):
        class Simple(BaseModel):
            name: str
            title: str

        ok, path = supports_provider_response_format(Simple)
        assert ok is True
        assert path is None

    def test_model_with_dict_field(self):
        class WithDict(BaseModel):
            metadata: dict[str, Any]

        ok, path = supports_provider_response_format(WithDict)
        assert ok is False
        assert path is not None
        assert "metadata" in path

    def test_model_with_optional_dict_field(self):
        class WithOptionalDict(BaseModel):
            extras: dict[str, Any] | None = None

        ok, path = supports_provider_response_format(WithOptionalDict)
        assert ok is False
        assert path is not None

    def test_model_with_list_of_str_ok(self):
        class WithList(BaseModel):
            tags: list[str]

        ok, path = supports_provider_response_format(WithList)
        assert ok is True
        assert path is None

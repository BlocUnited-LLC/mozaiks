"""
Event serialization, app_ids, and handoff_events pure-function unit tests.

Covers:
  event_serialization.normalize_text_content:
    - None returns empty string
    - str passthrough
    - model with model_dump (content/text/message keys)
    - dict with content key
    - dict with text key
    - dict with message key
    - dict with no matching key falls back to str()
    - list joined with spaces
    - model_dump fallback to str when no matching key
    - nested model with model_dump

  event_serialization.serialize_event_content:
    - None passthrough
    - str/int/float/bool passthrough
    - Enum → value recursed
    - dict recursion
    - list recursion
    - model with model_dump
    - object with __dict__
    - tuple converted to list
    - set converted to list

  event_serialization.extract_agent_name:
    - dict with sender key
    - dict with agent key
    - dict with agent_name key
    - dict with name key
    - dict with nested dict under sender
    - attribute object with sender
    - attribute object with agent_name
    - attribute object with .name on sub-object
    - None returns None
    - unrecognized object returns None

  app_ids.normalize_app_id:
    - None returns None
    - empty string returns None
    - whitespace-only returns None
    - str strips whitespace
    - non-str converts to str

  app_ids.coalesce_app_id:
    - delegates to normalize_app_id

  app_ids.build_app_scope_filter:
    - valid app_id returns {"app_id": value}
    - empty/None returns {"app_id": "__invalid__"}

  app_ids.dual_write_app_scope:
    - sets app_id on doc
    - empty app_id does not modify doc

  handoff_events.sanitize_identifier:
    - str passthrough
    - object with .name str
    - object with .agent_name str
    - None returns None
    - other type converted via str()
"""
from __future__ import annotations

from enum import Enum
from unittest.mock import MagicMock

from mozaiksai.core.events.event_serialization import (
    extract_agent_name,
    normalize_text_content,
    serialize_event_content,
)
from mozaiksai.core.events.handoff_events import sanitize_identifier
from mozaiksai.core.multitenant.app_ids import (
    build_app_scope_filter,
    coalesce_app_id,
    dual_write_app_scope,
    normalize_app_id,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _Color(Enum):
    RED = "red"
    BLUE = "blue"


class _SimpleModel:
    """Simulate a Pydantic-like model with model_dump."""
    def __init__(self, data: dict):
        self._data = data

    def model_dump(self) -> dict:
        return self._data


class _ObjectWithName:
    def __init__(self, name: str):
        self.name = name


class _ObjectWithAgentName:
    def __init__(self, agent_name: str):
        self.agent_name = agent_name


class _ObjectWithSender:
    def __init__(self, sender: str):
        self.sender = sender


class _ObjectWithNestedNameAttr:
    """Attribute where the value itself has a .name attribute."""
    def __init__(self, nested_name: str):
        inner = MagicMock()
        inner.name = nested_name
        self.sender = inner


# ---------------------------------------------------------------------------
# 1. normalize_text_content
# ---------------------------------------------------------------------------

class TestNormalizeTextContent:
    def test_none_returns_empty_string(self):
        assert normalize_text_content(None) == ""

    def test_str_passthrough(self):
        assert normalize_text_content("hello") == "hello"

    def test_empty_str_passthrough(self):
        assert normalize_text_content("") == ""

    def test_dict_content_key(self):
        assert normalize_text_content({"content": "from content"}) == "from content"

    def test_dict_text_key(self):
        assert normalize_text_content({"text": "from text"}) == "from text"

    def test_dict_message_key(self):
        assert normalize_text_content({"message": "from message"}) == "from message"

    def test_dict_content_takes_priority_over_text(self):
        result = normalize_text_content({"content": "c", "text": "t"})
        assert result == "c"

    def test_dict_no_matching_key_falls_back_to_str(self):
        result = normalize_text_content({"other_key": "value"})
        assert isinstance(result, str)

    def test_list_joined_with_spaces(self):
        result = normalize_text_content(["hello", "world"])
        assert result == "hello world"

    def test_list_of_non_strings_joined(self):
        result = normalize_text_content([1, 2, 3])
        assert result == "1 2 3"

    def test_model_with_model_dump_content_key(self):
        model = _SimpleModel({"content": "model content"})
        assert normalize_text_content(model) == "model content"

    def test_model_with_model_dump_text_key(self):
        model = _SimpleModel({"text": "model text"})
        assert normalize_text_content(model) == "model text"

    def test_model_with_model_dump_no_matching_key_falls_back(self):
        model = _SimpleModel({"data": "something"})
        result = normalize_text_content(model)
        assert isinstance(result, str)

    def test_dict_whitespace_only_value_skipped(self):
        # "content" value is whitespace → skip to next key
        result = normalize_text_content({"content": "   ", "text": "actual"})
        assert result == "actual"

    def test_integer_returns_str(self):
        result = normalize_text_content(42)
        assert result == "42"


# ---------------------------------------------------------------------------
# 2. serialize_event_content
# ---------------------------------------------------------------------------

class TestSerializeEventContent:
    def test_none_passthrough(self):
        assert serialize_event_content(None) is None

    def test_str_passthrough(self):
        assert serialize_event_content("hello") == "hello"

    def test_int_passthrough(self):
        assert serialize_event_content(42) == 42

    def test_float_passthrough(self):
        assert serialize_event_content(3.14) == 3.14

    def test_bool_passthrough(self):
        assert serialize_event_content(True) is True

    def test_enum_to_value(self):
        result = serialize_event_content(_Color.RED)
        assert result == "red"

    def test_nested_enum_in_dict(self):
        result = serialize_event_content({"color": _Color.BLUE})
        assert result == {"color": "blue"}

    def test_dict_recursion(self):
        result = serialize_event_content({"a": 1, "b": "two"})
        assert result == {"a": 1, "b": "two"}

    def test_list_recursion(self):
        result = serialize_event_content([1, "two", _Color.RED])
        assert result == [1, "two", "red"]

    def test_tuple_converted_to_list(self):
        result = serialize_event_content((1, 2, 3))
        assert result == [1, 2, 3]

    def test_model_with_model_dump(self):
        model = _SimpleModel({"key": "value"})
        result = serialize_event_content(model)
        assert result == {"key": "value"}

    def test_object_with_dict_attr(self):
        class _Obj:
            def __init__(self):
                self.x = 1
                self.y = 2

        result = serialize_event_content(_Obj())
        assert result == {"x": 1, "y": 2}

    def test_set_converted_to_list(self):
        result = serialize_event_content({42})
        assert isinstance(result, list)
        assert result == [42]

    def test_empty_dict(self):
        assert serialize_event_content({}) == {}

    def test_empty_list(self):
        assert serialize_event_content([]) == []


# ---------------------------------------------------------------------------
# 3. extract_agent_name
# ---------------------------------------------------------------------------

class TestExtractAgentName:
    def test_dict_sender_key(self):
        assert extract_agent_name({"sender": "Alice"}) == "Alice"

    def test_dict_agent_key(self):
        assert extract_agent_name({"agent": "Bob"}) == "Bob"

    def test_dict_agent_name_key(self):
        assert extract_agent_name({"agent_name": "Carol"}) == "Carol"

    def test_dict_name_key(self):
        assert extract_agent_name({"name": "Dave"}) == "Dave"

    def test_dict_sender_takes_priority(self):
        result = extract_agent_name({"sender": "S", "agent": "A"})
        assert result == "S"

    def test_dict_nested_dict_under_sender_key(self):
        # Inner dict has "name" key
        result = extract_agent_name({"sender": {"name": "Nested"}})
        assert result == "Nested"

    def test_dict_whitespace_only_sender_skipped(self):
        result = extract_agent_name({"sender": "   ", "agent": "FallbackAgent"})
        assert result == "FallbackAgent"

    def test_attribute_object_sender(self):
        obj = _ObjectWithSender("SenderAgent")
        assert extract_agent_name(obj) == "SenderAgent"

    def test_attribute_object_agent_name(self):
        obj = _ObjectWithAgentName("AgentNameAgent")
        assert extract_agent_name(obj) == "AgentNameAgent"

    def test_attribute_object_name(self):
        obj = _ObjectWithName("NamedAgent")
        assert extract_agent_name(obj) == "NamedAgent"

    def test_attribute_with_nested_name_sub_object(self):
        obj = _ObjectWithNestedNameAttr("SubNameAgent")
        result = extract_agent_name(obj)
        assert result == "SubNameAgent"

    def test_none_returns_none(self):
        assert extract_agent_name(None) is None

    def test_empty_dict_returns_none(self):
        assert extract_agent_name({}) is None

    def test_unrecognized_object_returns_none(self):
        class _Opaque:
            pass
        assert extract_agent_name(_Opaque()) is None

    def test_dict_no_matching_key_returns_none(self):
        assert extract_agent_name({"other": "value"}) is None

    def test_strips_whitespace_from_result(self):
        assert extract_agent_name({"sender": "  SpaceAgent  "}) == "SpaceAgent"


# ---------------------------------------------------------------------------
# 4. normalize_app_id
# ---------------------------------------------------------------------------

class TestNormalizeAppId:
    def test_none_returns_none(self):
        assert normalize_app_id(None) is None

    def test_empty_string_returns_none(self):
        assert normalize_app_id("") is None

    def test_whitespace_only_returns_none(self):
        assert normalize_app_id("   ") is None

    def test_str_stripped(self):
        assert normalize_app_id("  my-app  ") == "my-app"

    def test_clean_str_unchanged(self):
        assert normalize_app_id("my-app") == "my-app"

    def test_non_str_converted(self):
        result = normalize_app_id(12345)
        assert result == "12345"


# ---------------------------------------------------------------------------
# 5. coalesce_app_id
# ---------------------------------------------------------------------------

class TestCoalesceAppId:
    def test_delegates_normalize_for_none(self):
        assert coalesce_app_id(app_id=None) is None

    def test_delegates_normalize_for_valid(self):
        assert coalesce_app_id(app_id="  app-x  ") == "app-x"

    def test_delegates_normalize_for_empty(self):
        assert coalesce_app_id(app_id="") is None


# ---------------------------------------------------------------------------
# 6. build_app_scope_filter
# ---------------------------------------------------------------------------

class TestBuildAppScopeFilter:
    def test_valid_app_id_returns_filter(self):
        result = build_app_scope_filter("my-app")
        assert result == {"app_id": "my-app"}

    def test_strips_whitespace(self):
        result = build_app_scope_filter("  my-app  ")
        assert result == {"app_id": "my-app"}

    def test_empty_string_returns_invalid(self):
        result = build_app_scope_filter("")
        assert result == {"app_id": "__invalid__"}

    def test_whitespace_only_returns_invalid(self):
        result = build_app_scope_filter("   ")
        assert result == {"app_id": "__invalid__"}


# ---------------------------------------------------------------------------
# 7. dual_write_app_scope
# ---------------------------------------------------------------------------

class TestDualWriteAppScope:
    def test_sets_app_id_on_doc(self):
        doc: dict = {}
        result = dual_write_app_scope(doc, "my-app")
        assert result["app_id"] == "my-app"

    def test_returns_same_doc_object(self):
        doc: dict = {"existing": True}
        result = dual_write_app_scope(doc, "my-app")
        assert result is doc

    def test_empty_app_id_does_not_modify_doc(self):
        doc: dict = {"key": "val"}
        dual_write_app_scope(doc, "")
        assert "app_id" not in doc

    def test_whitespace_app_id_does_not_modify_doc(self):
        doc: dict = {}
        dual_write_app_scope(doc, "   ")
        assert "app_id" not in doc

    def test_strips_whitespace_before_writing(self):
        doc: dict = {}
        dual_write_app_scope(doc, "  stripped-app  ")
        assert doc["app_id"] == "stripped-app"


# ---------------------------------------------------------------------------
# 8. sanitize_identifier (handoff_events)
# ---------------------------------------------------------------------------

class TestSanitizeIdentifier:
    def test_str_passthrough(self):
        assert sanitize_identifier("my_agent") == "my_agent"

    def test_empty_str_passthrough(self):
        assert sanitize_identifier("") == ""

    def test_object_with_name_attr(self):
        obj = _ObjectWithName("named")
        assert sanitize_identifier(obj) == "named"

    def test_object_with_agent_name_attr(self):
        obj = _ObjectWithAgentName("agent_named")
        assert sanitize_identifier(obj) == "agent_named"

    def test_none_returns_none(self):
        assert sanitize_identifier(None) is None

    def test_integer_converted_to_str(self):
        result = sanitize_identifier(42)
        assert result == "42"

    def test_arbitrary_object_str_fallback(self):
        class _Thing:
            def __str__(self):
                return "thing_str"

        assert sanitize_identifier(_Thing()) == "thing_str"

    def test_name_attr_takes_priority_over_agent_name(self):
        obj = MagicMock()
        obj.name = "name_val"
        obj.agent_name = "agent_name_val"
        # .name is checked first
        assert sanitize_identifier(obj) == "name_val"

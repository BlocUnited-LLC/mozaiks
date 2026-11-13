"""
Context graph extraction pure helper unit tests.

Covers:

  _extract_action_ids:
    - empty list → []
    - actions not a list → []
    - string items → returned as-is
    - dict items with id → id extracted
    - dict items with name → name extracted
    - dict items with neither → skipped
    - duplicates deduplicated
    - mixed string and dict items

  _extract_named_items:
    - None → []
    - non-list/dict → []
    - dict input → key names returned
    - list of dicts with name key → names extracted
    - list of dicts with id key (fallback) → ids extracted
    - list items without name/id → skipped
    - duplicates deduplicated

  _extract_tool_names:
    - not a list → []
    - list of dicts with id → ids extracted
    - list of dicts with name (fallback) → names
    - list of dicts with function (fallback) → function names
    - list of dicts with file (last resort) → file names
    - non-dict items skipped
    - duplicates deduplicated

  _extract_module_refs:
    - flat dict with module_id → returned
    - nested dict recursion → all module_ids collected
    - list of dicts → module_ids collected
    - module key (fallback) → collected
    - moduleId key (fallback) → collected
    - no module refs → []
    - duplicates deduplicated




"""
from __future__ import annotations

from mozaiksai.core.app_context.context_graph import (
    _extract_action_ids,
    _extract_module_refs,
    _extract_named_items,
    _extract_tool_names,
)

# ---------------------------------------------------------------------------
# 2. _extract_action_ids
# ---------------------------------------------------------------------------

class TestExtractActionIds:
    def test_empty_list_returns_empty(self):
        assert _extract_action_ids({"actions": []}) == []

    def test_actions_not_list_returns_empty(self):
        assert _extract_action_ids({"actions": "not_a_list"}) == []

    def test_no_actions_key_returns_empty(self):
        assert _extract_action_ids({}) == []

    def test_string_items_returned(self):
        result = _extract_action_ids({"actions": ["create", "update"]})
        assert result == ["create", "update"]

    def test_dict_items_with_id(self):
        result = _extract_action_ids({"actions": [{"id": "create"}, {"id": "delete"}]})
        assert result == ["create", "delete"]

    def test_dict_items_with_name_fallback(self):
        result = _extract_action_ids({"actions": [{"name": "submit"}]})
        assert result == ["submit"]

    def test_dict_items_with_action_id_fallback(self):
        result = _extract_action_ids({"actions": [{"action_id": "approve"}]})
        assert result == ["approve"]

    def test_dict_items_without_id_skipped(self):
        result = _extract_action_ids({"actions": [{"no_id": "value"}, {"id": "valid"}]})
        assert result == ["valid"]

    def test_duplicates_deduplicated(self):
        result = _extract_action_ids({"actions": ["create", "create", "update"]})
        assert result == ["create", "update"]

    def test_mixed_string_and_dict(self):
        result = _extract_action_ids({"actions": ["string_action", {"id": "dict_action"}]})
        assert result == ["string_action", "dict_action"]

    def test_non_string_non_dict_items_skipped(self):
        result = _extract_action_ids({"actions": [42, None, {"id": "valid"}]})
        assert result == ["valid"]


# ---------------------------------------------------------------------------
# 3. _extract_named_items
# ---------------------------------------------------------------------------

class TestExtractNamedItems:
    def test_none_returns_empty(self):
        assert _extract_named_items(None) == []

    def test_non_container_returns_empty(self):
        assert _extract_named_items("a string") == []
        assert _extract_named_items(42) == []

    def test_dict_input_returns_keys(self):
        result = _extract_named_items({"AgentA": {}, "AgentB": {}})
        assert set(result) == {"AgentA", "AgentB"}

    def test_list_of_dicts_with_name_key(self):
        result = _extract_named_items([{"name": "Alice"}, {"name": "Bob"}])
        assert result == ["Alice", "Bob"]

    def test_list_of_dicts_with_id_fallback(self):
        result = _extract_named_items([{"id": "agent_1"}, {"id": "agent_2"}])
        assert result == ["agent_1", "agent_2"]

    def test_list_items_without_name_or_id_skipped(self):
        result = _extract_named_items([{"other": "x"}, {"name": "valid"}])
        assert result == ["valid"]

    def test_duplicates_deduplicated(self):
        result = _extract_named_items([{"name": "Alice"}, {"name": "Alice"}, {"name": "Bob"}])
        assert result == ["Alice", "Bob"]

    def test_empty_list_returns_empty(self):
        assert _extract_named_items([]) == []

    def test_empty_dict_returns_empty(self):
        assert _extract_named_items({}) == []


# ---------------------------------------------------------------------------
# 4. _extract_tool_names
# ---------------------------------------------------------------------------

class TestExtractToolNames:
    def test_not_list_returns_empty(self):
        assert _extract_tool_names("not_a_list") == []
        assert _extract_tool_names(None) == []
        assert _extract_tool_names({}) == []

    def test_list_with_id_key(self):
        result = _extract_tool_names([{"id": "save_output"}])
        assert result == ["save_output"]

    def test_name_fallback_when_no_id(self):
        result = _extract_tool_names([{"name": "my_tool"}])
        assert result == ["my_tool"]

    def test_function_fallback(self):
        result = _extract_tool_names([{"function": "do_something"}])
        assert result == ["do_something"]

    def test_file_last_resort(self):
        result = _extract_tool_names([{"file": "tools/my_tool.py"}])
        assert result == ["tools/my_tool.py"]

    def test_non_dict_items_skipped(self):
        result = _extract_tool_names([42, None, {"id": "valid"}])
        assert result == ["valid"]

    def test_duplicates_deduplicated(self):
        result = _extract_tool_names([{"id": "tool_a"}, {"id": "tool_a"}])
        assert result == ["tool_a"]

    def test_empty_list_returns_empty(self):
        assert _extract_tool_names([]) == []


# ---------------------------------------------------------------------------
# 5. _extract_module_refs
# ---------------------------------------------------------------------------

class TestExtractModuleRefs:
    def test_flat_dict_with_module_id(self):
        result = _extract_module_refs({"module_id": "tasks"})
        assert result == ["tasks"]

    def test_module_key_fallback(self):
        result = _extract_module_refs({"module": "profile"})
        assert result == ["profile"]

    def test_module_id_camel_case(self):
        result = _extract_module_refs({"moduleId": "wallet"})
        assert result == ["wallet"]

    def test_nested_dict_recursion(self):
        data = {"sections": [{"module_id": "tasks"}, {"module_id": "profile"}]}
        result = _extract_module_refs(data)
        assert set(result) == {"tasks", "profile"}

    def test_list_of_dicts_collected(self):
        data = {"items": [{"module_id": "a"}, {"module_id": "b"}]}
        result = _extract_module_refs(data)
        assert set(result) == {"a", "b"}

    def test_duplicates_deduplicated(self):
        data = {"a": {"module_id": "tasks"}, "b": {"module_id": "tasks"}}
        result = _extract_module_refs(data)
        assert result == ["tasks"]

    def test_no_module_refs_returns_empty(self):
        assert _extract_module_refs({"title": "My Page", "layout": "grid"}) == []

    def test_empty_dict_returns_empty(self):
        assert _extract_module_refs({}) == []

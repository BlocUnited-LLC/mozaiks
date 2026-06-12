"""
Context graph extraction pure helper unit tests.

Covers:
  _qualified_symbol_name:
    - no parent → name returned
    - parent present → "parent.name"
    - empty parent (falsy) → name only

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

  _reference_name_candidates:
    - empty/None target → []
    - simple name → [name]
    - dotted name → [full, last, first]
    - single-dot name → [full, last] (last == first, deduplicated)
    - three-part dotted name → [full, last, first]

  _is_noise_reference:
    - noise target "print" → True
    - noise target "len" → True
    - non-noise target → False
    - empty target → True
    - dotted noise like "something.print" → True (last part is noise)
    - dotted non-noise → False

  _ascend:
    - count 0 → same path
    - count 1 → parent
    - count 2 → grandparent

  _join_relative:
    - simple path join
    - ".." ascends directory
    - "." skipped
    - empty segments skipped
"""
from __future__ import annotations

from pathlib import PurePosixPath

from mozaiksai.core.app_context.context_graph import (
    ExtractedReference,
    _ascend,
    _extract_action_ids,
    _extract_module_refs,
    _extract_named_items,
    _extract_tool_names,
    _is_noise_reference,
    _join_relative,
    _qualified_symbol_name,
    _reference_name_candidates,
)

# ---------------------------------------------------------------------------
# 1. _qualified_symbol_name
# ---------------------------------------------------------------------------

class TestQualifiedSymbolName:
    def test_no_parent_returns_name(self):
        assert _qualified_symbol_name(parent=None, name="my_func") == "my_func"

    def test_parent_present_returns_dotted(self):
        assert _qualified_symbol_name(parent="MyClass", name="my_method") == "MyClass.my_method"

    def test_empty_parent_falsy_returns_name(self):
        assert _qualified_symbol_name(parent="", name="my_func") == "my_func"


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


# ---------------------------------------------------------------------------
# 6. _reference_name_candidates
# ---------------------------------------------------------------------------

class TestReferenceNameCandidates:
    def test_empty_string_returns_empty(self):
        assert _reference_name_candidates("") == []

    def test_none_like_returns_empty(self):
        assert _reference_name_candidates(None) == []  # type: ignore[arg-type]

    def test_simple_name_returns_single(self):
        assert _reference_name_candidates("my_func") == ["my_func"]

    def test_dotted_two_parts(self):
        result = _reference_name_candidates("MyClass.my_method")
        assert "MyClass.my_method" in result
        assert "my_method" in result
        assert "MyClass" in result

    def test_dotted_three_parts(self):
        result = _reference_name_candidates("a.b.c")
        assert "a.b.c" in result
        assert "c" in result  # last part
        assert "a" in result  # first part

    def test_no_duplicates_in_result(self):
        result = _reference_name_candidates("foo")
        assert len(result) == len(set(result))


# ---------------------------------------------------------------------------
# 7. _is_noise_reference
# ---------------------------------------------------------------------------

class TestIsNoiseReference:
    def _ref(self, target: str) -> ExtractedReference:
        return ExtractedReference(target=target, kind="call")

    def test_print_is_noise(self):
        assert _is_noise_reference(self._ref("print")) is True

    def test_len_is_noise(self):
        assert _is_noise_reference(self._ref("len")) is True

    def test_bool_is_noise(self):
        assert _is_noise_reference(self._ref("bool")) is True

    def test_empty_target_is_noise(self):
        assert _is_noise_reference(self._ref("")) is True

    def test_dotted_noise_last_part(self):
        # "something.print" → last part "print" is noise
        assert _is_noise_reference(self._ref("something.print")) is True

    def test_dotted_non_noise(self):
        assert _is_noise_reference(self._ref("MyService.create")) is False

    def test_non_noise_target(self):
        assert _is_noise_reference(self._ref("save_workflow_output")) is False

    def test_console_log_is_noise(self):
        assert _is_noise_reference(self._ref("console.log")) is True


# ---------------------------------------------------------------------------
# 8. _ascend
# ---------------------------------------------------------------------------

class TestAscend:
    def test_count_zero_returns_same(self):
        path = PurePosixPath("a/b/c")
        assert _ascend(path, 0) == path

    def test_count_one_returns_parent(self):
        path = PurePosixPath("a/b/c")
        assert _ascend(path, 1) == PurePosixPath("a/b")

    def test_count_two_returns_grandparent(self):
        path = PurePosixPath("a/b/c")
        assert _ascend(path, 2) == PurePosixPath("a")

    def test_ascend_from_root_stays_at_root(self):
        path = PurePosixPath(".")
        result = _ascend(path, 3)
        # PurePosixPath(".").parent is still "." at root
        assert str(result) in {".", ""}


# ---------------------------------------------------------------------------
# 9. _join_relative
# ---------------------------------------------------------------------------

class TestJoinRelative:
    def test_simple_join(self):
        result = _join_relative(PurePosixPath("a/b"), "c.py")
        assert result == "a/b/c.py"

    def test_parent_dot_dot_ascends(self):
        result = _join_relative(PurePosixPath("a/b/c"), "../d.py")
        assert result == "a/b/d.py"

    def test_single_dot_skipped(self):
        result = _join_relative(PurePosixPath("a/b"), "./c.py")
        assert result == "a/b/c.py"

    def test_multiple_parent_traversals(self):
        result = _join_relative(PurePosixPath("a/b/c"), "../../d.py")
        assert result == "a/d.py"

    def test_root_dir_with_filename(self):
        result = _join_relative(PurePosixPath("src"), "utils.py")
        assert result == "src/utils.py"

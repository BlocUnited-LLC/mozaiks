"""
Pure helper unit tests for:
  mozaiksai/core/workflow/task_batches.py

Covers helpers NOT tested in test_task_batches_helpers.py:

  _to_plain_data:
    - BaseModel → model_dump(mode="json")
    - dict → returned unchanged
    - list → returned unchanged
    - empty string → empty string
    - valid JSON string → parsed to dict/list
    - invalid JSON string → returned as-is
    - other value (int) → returned as-is

  _normalize_agent_reply:
    - reply with .body attr → body extracted and converted
    - reply without .body attr → reply itself converted
    - plain dict → returned unchanged
    - JSON string body → parsed

  _task_dependencies:
    - dependency_field not in task → []
    - field value is None → []
    - field is a string → [stripped string]
    - field is empty string → []
    - field is a list → list of stripped non-empty strings
    - field is a list with empty entries → filtered

  _normalize_task_items:
    - empty list → []
    - None → []
    - non-mapping non-list value → []
    - list of dicts with task_id → normalized list
    - list entry with "id" field → task_id synthesized from id
    - list entry with "name" field → task_id synthesized from name
    - list entry with neither → "task_N" fallback
    - Mapping input → values() used
    - non-dict entries skipped

  _optional_task_output_paths:
    - task_type="page_bundle" → set includes "ui/route_manifest.json"
    - task_type="module_contract" with module_id → module-specific optional paths
    - task_type="module_contract" without module_id → empty set
    - task_type="other" → empty set
    - no task_type key → empty set
"""
from __future__ import annotations

from types import SimpleNamespace

from pydantic import BaseModel

from mozaiksai.core.workflow.task_batches import (
    _normalize_agent_reply,
    _normalize_task_items,
    _optional_task_output_paths,
    _task_dependencies,
    _to_plain_data,
)

# ---------------------------------------------------------------------------
# 1. _to_plain_data
# ---------------------------------------------------------------------------

class _FakeModel(BaseModel):
    name: str
    value: int = 0


class TestToPlainData:
    def test_base_model_returns_dict(self):
        model = _FakeModel(name="test", value=42)
        result = _to_plain_data(model)
        assert isinstance(result, dict)
        assert result["name"] == "test"
        assert result["value"] == 42

    def test_dict_returned_unchanged(self):
        d = {"key": "val"}
        result = _to_plain_data(d)
        assert result is d

    def test_list_returned_unchanged(self):
        lst = [1, 2, 3]
        result = _to_plain_data(lst)
        assert result is lst

    def test_empty_string_returned_as_is(self):
        assert _to_plain_data("") == ""

    def test_valid_json_string_parsed(self):
        result = _to_plain_data('{"key": "value"}')
        assert result == {"key": "value"}

    def test_valid_json_list_string_parsed(self):
        result = _to_plain_data('[1, 2, 3]')
        assert result == [1, 2, 3]

    def test_invalid_json_string_returned_as_is(self):
        result = _to_plain_data("not valid json {")
        assert result == "not valid json {"

    def test_integer_returned_as_is(self):
        assert _to_plain_data(42) == 42

    def test_none_returned_as_is(self):
        assert _to_plain_data(None) is None


# ---------------------------------------------------------------------------
# 2. _normalize_agent_reply
# ---------------------------------------------------------------------------

class TestNormalizeAgentReply:
    def test_dict_reply_returned_unchanged(self):
        d = {"key": "value"}
        result = _normalize_agent_reply(d)
        assert result is d

    def test_reply_with_body_attr_extracted(self):
        reply = SimpleNamespace(body={"key": "value"})
        result = _normalize_agent_reply(reply)
        assert result == {"key": "value"}

    def test_reply_without_body_attr_uses_reply_itself(self):
        # SimpleNamespace without .body → getattr returns the ns itself
        reply = SimpleNamespace(data="hello")
        result = _normalize_agent_reply(reply)
        # _to_plain_data of SimpleNamespace → returned as-is
        assert result is reply

    def test_json_string_body_parsed(self):
        reply = SimpleNamespace(body='{"x": 1}')
        result = _normalize_agent_reply(reply)
        assert result == {"x": 1}

    def test_none_body_attr_returns_none(self):
        reply = SimpleNamespace(body=None)
        result = _normalize_agent_reply(reply)
        assert result is None


# ---------------------------------------------------------------------------
# 3. _task_dependencies
# ---------------------------------------------------------------------------

class TestTaskDependencies:
    def test_field_not_in_task_returns_empty(self):
        assert _task_dependencies({}, "depends_on") == []

    def test_none_value_returns_empty(self):
        assert _task_dependencies({"depends_on": None}, "depends_on") == []

    def test_string_value_returns_single_item_list(self):
        result = _task_dependencies({"depends_on": "task_1"}, "depends_on")
        assert result == ["task_1"]

    def test_empty_string_returns_empty(self):
        assert _task_dependencies({"depends_on": ""}, "depends_on") == []

    def test_whitespace_string_returns_empty(self):
        assert _task_dependencies({"depends_on": "  "}, "depends_on") == []

    def test_list_value_returns_stripped_items(self):
        result = _task_dependencies({"depends_on": [" task_1 ", "task_2"]}, "depends_on")
        assert result == ["task_1", "task_2"]

    def test_list_filters_empty_strings(self):
        result = _task_dependencies({"depends_on": ["task_1", "", "task_2"]}, "depends_on")
        assert result == ["task_1", "task_2"]

    def test_list_filters_none_entries(self):
        result = _task_dependencies({"depends_on": ["task_1", None, "task_2"]}, "depends_on")  # type: ignore
        assert result == ["task_1", "task_2"]

    def test_custom_dependency_field(self):
        result = _task_dependencies({"prereqs": ["task_a"]}, "prereqs")
        assert result == ["task_a"]


# ---------------------------------------------------------------------------
# 4. _normalize_task_items
# ---------------------------------------------------------------------------

class TestNormalizeTaskItems:
    def test_empty_list_returns_empty(self):
        assert _normalize_task_items([]) == []

    def test_none_returns_empty(self):
        assert _normalize_task_items(None) == []

    def test_non_mapping_non_list_returns_empty(self):
        assert _normalize_task_items("not-a-list") == []

    def test_list_with_task_id_normalized(self):
        items = [{"task_id": "t1", "kind": "page"}]
        result = _normalize_task_items(items)
        assert len(result) == 1
        assert result[0]["task_id"] == "t1"

    def test_id_field_fallback_for_task_id(self):
        items = [{"id": "my-task", "kind": "module"}]
        result = _normalize_task_items(items)
        assert result[0]["task_id"] == "my-task"

    def test_name_field_fallback_for_task_id(self):
        items = [{"name": "build-auth", "kind": "module"}]
        result = _normalize_task_items(items)
        assert result[0]["task_id"] == "build-auth"

    def test_fallback_task_n_when_no_id(self):
        items = [{"kind": "page"}, {"kind": "module"}]
        result = _normalize_task_items(items)
        assert result[0]["task_id"] == "task_1"
        assert result[1]["task_id"] == "task_2"

    def test_mapping_input_uses_values(self):
        mapping = {"first": {"task_id": "t1"}, "second": {"task_id": "t2"}}
        result = _normalize_task_items(mapping)
        assert len(result) == 2

    def test_non_dict_entries_skipped(self):
        items = [{"task_id": "t1"}, "not-a-dict", 42]
        result = _normalize_task_items(items)
        assert len(result) == 1
        assert result[0]["task_id"] == "t1"

    def test_json_string_input_parsed_to_list(self):
        json_str = '[{"task_id": "t1"}]'
        result = _normalize_task_items(json_str)
        assert len(result) == 1
        assert result[0]["task_id"] == "t1"


# ---------------------------------------------------------------------------
# 5. _optional_task_output_paths
# ---------------------------------------------------------------------------

class TestOptionalTaskOutputPaths:
    def test_page_bundle_includes_route_manifest(self):
        result = _optional_task_output_paths({"task_type": "page_bundle"})
        assert "ui/route_manifest.json" in result

    def test_page_bundle_includes_multiple_shared_paths(self):
        result = _optional_task_output_paths({"task_type": "page_bundle"})
        assert "config/shell.json" in result
        assert "data/contract.json" in result

    def test_module_contract_with_module_id_returns_paths(self):
        task = {"task_type": "module_contract", "capability_pack_id": "billing"}
        result = _optional_task_output_paths(task)
        assert "modules/billing/contracts/notifications.yaml" in result
        assert "modules/billing/contracts/policy_hooks.yaml" in result
        assert "modules/billing/runtime_extensions.yaml" in result

    def test_module_contract_without_module_id_returns_empty(self):
        task = {"task_type": "module_contract"}
        assert _optional_task_output_paths(task) == set()

    def test_other_task_type_returns_empty(self):
        assert _optional_task_output_paths({"task_type": "experience_design"}) == set()

    def test_no_task_type_returns_empty(self):
        assert _optional_task_output_paths({}) == set()

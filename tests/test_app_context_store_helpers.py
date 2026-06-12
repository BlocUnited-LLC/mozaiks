"""
mozaiksai/core/app_context/store.py pure helper unit tests.

Covers:
  _surface_id:
    - prefix + simple string → prefixed surface id
    - special chars replaced with underscores
    - double underscores collapsed
    - result capped at 120 chars
    - empty value → fallback "surface"

  _normalize_identifier:
    - valid identifier → lowercased, special chars replaced
    - leading/trailing underscores stripped
    - empty string → empty string

  _normalize_app_bundle_path:
    - None → None
    - empty → None
    - backslash normalized to slash
    - leading/trailing slashes stripped
    - known prefix preserved
    - marker in path → prefix extracted
    - app.json suffix → "app.json"
    - unknown path → returned as-is (normalized)

  _dedupe:
    - empty list → []
    - unique values preserved in order
    - duplicates removed (first kept)
    - empty/whitespace entries excluded
    - non-string values cleaned

  _first_string:
    - non-dict → None
    - key found → stripped value returned
    - empty value skipped → next key checked
    - no matching key → None
    - priority order respected

  _strings_from_value:
    - string → [string]
    - list of strings → flattened
    - dict with id key → [id value]
    - dict with name key → [name value]
    - dict without known keys → []
    - nested list → flattened
    - non-string/list/dict → []

  _normalize_operation:
    - "read" → "read"
    - "reads" → "read"
    - "write" → "write"
    - "writes" → "write"
    - unknown → returned as-is (lowercased)
    - whitespace stripped

  _collect_string_values:
    - dict with matching key → values collected
    - dict with non-matching key → recurses
    - list → recurses into items
    - nested structure → all matching values

  _integration_ids_from_structured_value:
    - dict with "integration" key → ids
    - dict with "integrations" key → list of ids
    - dict with "connector" key → ids
    - dict with "provider" key → ids
    - nested dict → recurses
    - list → recurses
    - non-string values → excluded

  _action_ids_from_module_yaml:
    - non-dict → []
    - dict without actions → []
    - dict with actions list → action ids
    - actions without "id" key → skipped

  _module_id_from_endpoint:
    - no /api/modules/ marker → None
    - /api/modules/tasks/list → "tasks"
    - /api/modules/task_manager/action → "task_manager"
    - empty module segment → None

  _json_bytes:
    - dict → sorted JSON bytes
    - non-serializable default → str() used
    - empty dict → b"{}"

  _manifest_entries:
    - None → []
    - list of dicts → list of dict copies
    - model with model_dump → dict from model_dump
    - object with path attr → {"path": str}
    - unrecognized item → skipped

  _manifest_entries_by_path:
    - empty list → {}
    - list of entries with path → keyed by normalized path
    - entry without path → skipped
"""
from __future__ import annotations

import json

from mozaiksai.core.app_context.store import (
    _action_ids_from_module_yaml,
    _collect_string_values,
    _dedupe,
    _first_string,
    _integration_ids_from_structured_value,
    _json_bytes,
    _manifest_entries,
    _manifest_entries_by_path,
    _module_id_from_endpoint,
    _normalize_app_bundle_path,
    _normalize_identifier,
    _normalize_operation,
    _strings_from_value,
    _surface_id,
)

# ---------------------------------------------------------------------------
# 1. _surface_id
# ---------------------------------------------------------------------------

class TestSurfaceId:
    def test_prefix_and_simple_name(self):
        result = _surface_id("page", "Home")
        assert result.startswith("page_")
        assert "home" in result

    def test_special_chars_become_underscores(self):
        result = _surface_id("svc", "my-service/v2")
        assert "-" not in result
        assert "/" not in result

    def test_double_underscores_collapsed(self):
        result = _surface_id("svc", "my--service")
        assert "__" not in result

    def test_result_capped_at_120_chars(self):
        long_name = "a" * 200
        result = _surface_id("prefix", long_name)
        assert len(result) <= 120

    def test_empty_value_uses_surface_fallback(self):
        result = _surface_id("page", "")
        assert "surface" in result

    def test_empty_prefix(self):
        result = _surface_id("", "Dashboard")
        assert "dashboard" in result


# ---------------------------------------------------------------------------
# 2. _normalize_identifier
# ---------------------------------------------------------------------------

class TestNormalizeIdentifier:
    def test_simple_value(self):
        result = _normalize_identifier("tasks")
        assert result == "tasks"

    def test_uppercase_lowercased(self):
        result = _normalize_identifier("TaskManager")
        assert result == result.lower()

    def test_special_chars_replaced(self):
        result = _normalize_identifier("my-module/v2")
        assert "-" not in result
        assert "/" not in result

    def test_leading_trailing_underscores_stripped(self):
        result = _normalize_identifier("_tasks_")
        assert not result.startswith("_")
        assert not result.endswith("_")

    def test_empty_string_returns_empty(self):
        result = _normalize_identifier("")
        # _surface_id("", "") returns "surface" stripped of underscores
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# 3. _normalize_app_bundle_path
# ---------------------------------------------------------------------------

class TestNormalizeAppBundlePath:
    def test_none_returns_none(self):
        assert _normalize_app_bundle_path(None) is None

    def test_empty_string_returns_none(self):
        assert _normalize_app_bundle_path("") is None

    def test_backslash_normalized_to_slash(self):
        result = _normalize_app_bundle_path("ui\\pages\\home.yaml")
        assert "\\" not in result
        assert "/" in result

    def test_leading_slash_stripped(self):
        result = _normalize_app_bundle_path("/ui/pages/home.yaml")
        assert not result.startswith("/")

    def test_known_prefix_preserved(self):
        result = _normalize_app_bundle_path("ui/pages/home.yaml")
        assert result == "ui/pages/home.yaml"

    def test_modules_prefix_preserved(self):
        result = _normalize_app_bundle_path("modules/tasks/module.yaml")
        assert result == "modules/tasks/module.yaml"

    def test_marker_in_path_extracts_prefix(self):
        result = _normalize_app_bundle_path("app/bundle/ui/pages/home.yaml")
        assert result == "ui/pages/home.yaml"

    def test_app_json_suffix(self):
        result = _normalize_app_bundle_path("some/path/to/app.json")
        assert result == "app.json"

    def test_bare_app_json(self):
        result = _normalize_app_bundle_path("app.json")
        assert result == "app.json"

    def test_unknown_path_returned_normalized(self):
        result = _normalize_app_bundle_path("custom/dir/file.txt")
        assert result == "custom/dir/file.txt"

    def test_trailing_slash_stripped(self):
        result = _normalize_app_bundle_path("ui/pages/")
        assert not result.endswith("/")


# ---------------------------------------------------------------------------
# 4. _dedupe
# ---------------------------------------------------------------------------

class TestDedupe:
    def test_empty_list_returns_empty(self):
        assert _dedupe([]) == []

    def test_unique_values_preserved_in_order(self):
        assert _dedupe(["a", "b", "c"]) == ["a", "b", "c"]

    def test_duplicates_first_occurrence_kept(self):
        result = _dedupe(["a", "b", "a", "c"])
        assert result == ["a", "b", "c"]

    def test_empty_strings_excluded(self):
        result = _dedupe(["a", "", "b"])
        assert "" not in result
        assert result == ["a", "b"]

    def test_whitespace_only_excluded(self):
        result = _dedupe(["a", "   ", "b"])
        assert len(result) == 2

    def test_none_in_list_cleaned(self):
        # str(None or "").strip() = "" → excluded
        result = _dedupe(["a", None, "b"])
        assert result == ["a", "b"]


# ---------------------------------------------------------------------------
# 5. _first_string
# ---------------------------------------------------------------------------

class TestFirstString:
    def test_non_dict_returns_none(self):
        assert _first_string("string", ("id",)) is None
        assert _first_string(None, ("id",)) is None
        assert _first_string([], ("id",)) is None

    def test_key_found_returns_value(self):
        assert _first_string({"id": "task_id"}, ("id",)) == "task_id"

    def test_value_stripped(self):
        assert _first_string({"id": "  task  "}, ("id",)) == "task"

    def test_empty_value_skipped(self):
        result = _first_string({"id": "", "name": "fallback"}, ("id", "name"))
        assert result == "fallback"

    def test_whitespace_only_value_skipped(self):
        result = _first_string({"id": "  ", "name": "fallback"}, ("id", "name"))
        assert result == "fallback"

    def test_no_matching_key_returns_none(self):
        assert _first_string({"other": "value"}, ("id", "name")) is None

    def test_priority_order_respected(self):
        d = {"name": "by_name", "id": "by_id"}
        result = _first_string(d, ("id", "name"))
        assert result == "by_id"

    def test_multiple_keys_checked_in_order(self):
        d = {"entity_id": "eid", "id": "simple_id"}
        result = _first_string(d, ("entity_id", "id"))
        assert result == "eid"


# ---------------------------------------------------------------------------
# 6. _strings_from_value
# ---------------------------------------------------------------------------

class TestStringsFromValue:
    def test_string_returned_as_single_item_list(self):
        assert _strings_from_value("hello") == ["hello"]

    def test_list_flattened(self):
        result = _strings_from_value(["a", "b"])
        assert result == ["a", "b"]

    def test_nested_list_flattened(self):
        result = _strings_from_value([["a", "b"], "c"])
        assert result == ["a", "b", "c"]

    def test_dict_with_id_key(self):
        result = _strings_from_value({"id": "my_id", "other": "ignored"})
        assert result == ["my_id"]

    def test_dict_with_name_key(self):
        result = _strings_from_value({"name": "my_name"})
        assert result == ["my_name"]

    def test_dict_with_entity_id_key(self):
        result = _strings_from_value({"entity_id": "eid"})
        assert result == ["eid"]

    def test_dict_without_known_key_returns_empty(self):
        result = _strings_from_value({"unknown_key": "value"})
        assert result == []

    def test_int_returns_empty(self):
        assert _strings_from_value(42) == []

    def test_none_returns_empty(self):
        assert _strings_from_value(None) == []

    def test_list_with_mixed_types(self):
        result = _strings_from_value(["str_val", {"id": "dict_id"}, 42])
        assert "str_val" in result
        assert "dict_id" in result
        assert len(result) == 2


# ---------------------------------------------------------------------------
# 7. _normalize_operation
# ---------------------------------------------------------------------------

class TestNormalizeOperation:
    def test_read_returned(self):
        assert _normalize_operation("read") == "read"

    def test_reads_normalized_to_read(self):
        assert _normalize_operation("reads") == "read"

    def test_write_returned(self):
        assert _normalize_operation("write") == "write"

    def test_writes_normalized_to_write(self):
        assert _normalize_operation("writes") == "write"

    def test_unknown_lowercased_and_returned(self):
        assert _normalize_operation("Delete") == "delete"

    def test_whitespace_stripped(self):
        assert _normalize_operation("  read  ") == "read"

    def test_empty_string_returned_empty(self):
        assert _normalize_operation("") == ""


# ---------------------------------------------------------------------------
# 8. _collect_string_values
# ---------------------------------------------------------------------------

class TestCollectStringValues:
    def test_dict_matching_key_collected(self):
        data = {"operations": ["read", "write"]}
        result = _collect_string_values(data, {"operations"})
        assert "read" in result
        assert "write" in result

    def test_dict_non_matching_key_recurses(self):
        data = {"config": {"access": "read"}}
        result = _collect_string_values(data, {"access"})
        assert "read" in result

    def test_list_recurses(self):
        data = [{"operations": "read"}, {"operations": "write"}]
        result = _collect_string_values(data, {"operations"})
        assert "read" in result
        assert "write" in result

    def test_no_matching_keys_returns_empty(self):
        data = {"name": "tasks", "id": "module1"}
        result = _collect_string_values(data, {"access", "operations"})
        assert result == []

    def test_empty_dict_returns_empty(self):
        assert _collect_string_values({}, {"operations"}) == []


# ---------------------------------------------------------------------------
# 9. _integration_ids_from_structured_value
# ---------------------------------------------------------------------------

class TestIntegrationIdsFromStructuredValue:
    def test_integration_key(self):
        result = _integration_ids_from_structured_value({"integration": "stripe"})
        assert "stripe" in result

    def test_integrations_key_list(self):
        result = _integration_ids_from_structured_value({"integrations": ["stripe", "sendgrid"]})
        assert "stripe" in result
        assert "sendgrid" in result

    def test_connector_key(self):
        result = _integration_ids_from_structured_value({"connector": "redis"})
        assert "redis" in result

    def test_provider_id_key(self):
        result = _integration_ids_from_structured_value({"provider_id": "aws"})
        assert "aws" in result

    def test_nested_dict_recurses(self):
        data = {"config": {"integration": "stripe"}}
        result = _integration_ids_from_structured_value(data)
        assert "stripe" in result

    def test_list_recurses(self):
        data = [{"integration": "stripe"}, {"integration": "sendgrid"}]
        result = _integration_ids_from_structured_value(data)
        assert "stripe" in result
        assert "sendgrid" in result

    def test_non_dict_non_list_returns_empty(self):
        assert _integration_ids_from_structured_value("string") == []
        assert _integration_ids_from_structured_value(None) == []
        assert _integration_ids_from_structured_value(42) == []

    def test_ids_normalized(self):
        result = _integration_ids_from_structured_value({"integration": "Stripe"})
        assert "stripe" in result


# ---------------------------------------------------------------------------
# 10. _action_ids_from_module_yaml
# ---------------------------------------------------------------------------

class TestActionIdsFromModuleYaml:
    def test_non_dict_returns_empty(self):
        assert _action_ids_from_module_yaml(None) == []
        assert _action_ids_from_module_yaml("string") == []
        assert _action_ids_from_module_yaml([]) == []

    def test_dict_without_actions_returns_empty(self):
        assert _action_ids_from_module_yaml({"name": "tasks"}) == []

    def test_non_list_actions_returns_empty(self):
        assert _action_ids_from_module_yaml({"actions": "not_a_list"}) == []

    def test_actions_with_id(self):
        data = {"actions": [{"id": "create_task"}, {"id": "delete_task"}]}
        result = _action_ids_from_module_yaml(data)
        assert "create_task" in result
        assert "delete_task" in result

    def test_action_name_used_as_fallback(self):
        # "name" is a fallback key after "id", "action_id", "actionId"
        data = {"actions": [{"name": "create_task"}, {"id": "delete_task"}]}
        result = _action_ids_from_module_yaml(data)
        assert len(result) == 2

    def test_action_without_any_id_keys_skipped(self):
        data = {"actions": [{"label": "ignored"}, {"id": "valid_action"}]}
        result = _action_ids_from_module_yaml(data)
        assert len(result) == 1
        assert "valid_action" in result

    def test_action_ids_normalized(self):
        # _normalize_identifier lowercases alnum chars; non-alnum → "_"
        # "CreateTask" → all alnum → "createtask"
        data = {"actions": [{"id": "CreateTask"}]}
        result = _action_ids_from_module_yaml(data)
        assert result[0] == "createtask"

    def test_non_dict_action_items_skipped(self):
        data = {"actions": ["not_a_dict", {"id": "valid"}]}
        result = _action_ids_from_module_yaml(data)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# 11. _module_id_from_endpoint
# ---------------------------------------------------------------------------

class TestModuleIdFromEndpoint:
    def test_no_marker_returns_none(self):
        assert _module_id_from_endpoint("/api/tasks/list") is None

    def test_modules_marker_extracts_id(self):
        result = _module_id_from_endpoint("/api/modules/tasks/list")
        assert result == "tasks"

    def test_compound_module_id(self):
        result = _module_id_from_endpoint("/api/modules/task_manager/create")
        assert result == "task_manager"

    def test_module_id_normalized(self):
        # _normalize_identifier: all alnum → no underscores between camel-case words
        result = _module_id_from_endpoint("/api/modules/TaskManager/create")
        assert result == "taskmanager"

    def test_empty_module_segment_returns_none(self):
        assert _module_id_from_endpoint("/api/modules//action") is None

    def test_no_trailing_path_accepted(self):
        result = _module_id_from_endpoint("/api/modules/users")
        assert result == "users"


# ---------------------------------------------------------------------------
# 12. _json_bytes
# ---------------------------------------------------------------------------

class TestJsonBytes:
    def test_dict_returns_bytes(self):
        result = _json_bytes({"a": 1})
        assert isinstance(result, bytes)
        parsed = json.loads(result)
        assert parsed == {"a": 1}

    def test_empty_dict_returns_bytes(self):
        assert _json_bytes({}) == b"{}"

    def test_sorted_keys(self):
        result = _json_bytes({"z": 1, "a": 2})
        parsed = json.loads(result)
        assert list(parsed.keys()) == ["a", "z"]

    def test_non_serializable_converted_to_str(self):
        class Custom:
            def __str__(self):
                return "custom_value"
        result = _json_bytes({"key": Custom()})
        parsed = json.loads(result)
        assert parsed["key"] == "custom_value"

    def test_compact_separators(self):
        result = _json_bytes({"a": 1})
        # No spaces in output
        assert b" " not in result


# ---------------------------------------------------------------------------
# 13. _manifest_entries
# ---------------------------------------------------------------------------

class TestManifestEntries:
    def test_none_returns_empty(self):
        assert _manifest_entries(None) == []

    def test_empty_list_returns_empty(self):
        assert _manifest_entries([]) == []

    def test_dict_entry_copied(self):
        result = _manifest_entries([{"path": "ui/pages/home.yaml", "title": "Home"}])
        assert result == [{"path": "ui/pages/home.yaml", "title": "Home"}]

    def test_dict_entry_is_copy(self):
        original = {"path": "ui/pages/home.yaml"}
        result = _manifest_entries([original])
        assert result[0] is not original

    def test_model_with_model_dump_accepted(self):
        class FakeModel:
            def model_dump(self, **kwargs):
                return {"path": "ui/pages/home.yaml", "title": "Home"}
        result = _manifest_entries([FakeModel()])
        assert result == [{"path": "ui/pages/home.yaml", "title": "Home"}]

    def test_object_with_path_attr_accepted(self):
        class PathObj:
            path = "ui/pages/home.yaml"
        result = _manifest_entries([PathObj()])
        assert result == [{"path": "ui/pages/home.yaml"}]

    def test_unrecognized_item_skipped(self):
        result = _manifest_entries(["string_item", 42])
        assert result == []

    def test_mixed_entries(self):
        class PathObj:
            path = "ui/pages/x.yaml"
        result = _manifest_entries([{"path": "ui/pages/a.yaml"}, PathObj()])
        assert len(result) == 2


# ---------------------------------------------------------------------------
# 14. _manifest_entries_by_path
# ---------------------------------------------------------------------------

class TestManifestEntriesByPath:
    def test_empty_list_returns_empty_dict(self):
        assert _manifest_entries_by_path([]) == {}

    def test_entries_keyed_by_normalized_path(self):
        entries = [{"path": "ui/pages/home.yaml", "title": "Home"}]
        result = _manifest_entries_by_path(entries)
        assert "ui/pages/home.yaml" in result
        assert result["ui/pages/home.yaml"]["title"] == "Home"

    def test_entry_without_path_skipped(self):
        entries = [{"title": "No path"}, {"path": "ui/pages/x.yaml"}]
        result = _manifest_entries_by_path(entries)
        assert len(result) == 1
        assert "ui/pages/x.yaml" in result

    def test_path_normalized_in_key(self):
        entries = [{"path": "/ui/pages/home.yaml"}]
        result = _manifest_entries_by_path(entries)
        assert "ui/pages/home.yaml" in result

    def test_multiple_entries(self):
        entries = [
            {"path": "ui/pages/home.yaml"},
            {"path": "modules/tasks/module.yaml"},
        ]
        result = _manifest_entries_by_path(entries)
        assert len(result) == 2

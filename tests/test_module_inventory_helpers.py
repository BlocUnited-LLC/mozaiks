"""
Module inventory private helper unit tests.

Tests the pure extraction and classification helpers that are NOT covered
by the public-API-focused test_module_inventory.py.

Covers:
  _parse_yaml_safe:
    - valid YAML → parsed dict
    - valid YAML list → parsed list
    - malformed YAML → None
    - empty string → None
    - non-string-like content → None (coerced to str)

  _extract_action_ids:
    - None/non-dict → []
    - no actions key → []
    - actions not a list → []
    - dict actions with id → list of ids
    - non-dict actions items skipped
    - whitespace-only ids skipped
    - ids stripped

  _has_persistence_signal:
    - None/non-dict → False
    - no actions key → False
    - actions without emits → False
    - action with emits (truthy) → True
    - action with emits=None → False
    - mixed actions: one with emits → True

  _extract_event_types:
    - malformed YAML → []
    - no events key → []
    - events not a list → []
    - event with type field → type returned
    - event with event_type fallback → event_type returned
    - event with id fallback → id returned
    - type takes priority over event_type
    - non-dict events skipped
    - whitespace-only values skipped

  _crud_action_count:
    - empty list → 0
    - no CRUD verbs → 0
    - single "create" → 1
    - "list" → 1
    - "update" → 1
    - "delete" → 1
    - multiple CRUD actions → count
    - verb as substring (e.g. "create_listing") → counted
"""
from __future__ import annotations

from factory_app.refinement_harness.tools._module_inventory import (
    _crud_action_count,
    _extract_action_ids,
    _extract_event_types,
    _has_persistence_signal,
    _parse_yaml_safe,
)

# ---------------------------------------------------------------------------
# 1. _parse_yaml_safe
# ---------------------------------------------------------------------------

class TestParseYamlSafe:
    def test_valid_yaml_dict(self):
        result = _parse_yaml_safe("id: tasks\nname: Tasks Module", "test")
        assert isinstance(result, dict)
        assert result["id"] == "tasks"

    def test_valid_yaml_list(self):
        result = _parse_yaml_safe("- a\n- b\n- c", "test")
        assert result == ["a", "b", "c"]

    def test_malformed_yaml_returns_none(self):
        result = _parse_yaml_safe("{ not: valid: yaml: at all }}", "test")
        assert result is None

    def test_empty_string_returns_none(self):
        result = _parse_yaml_safe("", "test")
        assert result is None

    def test_plain_scalar_returned(self):
        result = _parse_yaml_safe("just_a_string", "test")
        # YAML scalar → string, not None
        assert result == "just_a_string"

    def test_null_yaml_returns_none(self):
        # YAML "null" → Python None
        result = _parse_yaml_safe("null", "test")
        assert result is None


# ---------------------------------------------------------------------------
# 2. _extract_action_ids
# ---------------------------------------------------------------------------

class TestExtractActionIds:
    def test_none_returns_empty(self):
        assert _extract_action_ids(None) == []

    def test_non_dict_returns_empty(self):
        assert _extract_action_ids(["not_a_dict"]) == []

    def test_no_actions_key_returns_empty(self):
        assert _extract_action_ids({"id": "tasks"}) == []

    def test_actions_not_list_returns_empty(self):
        assert _extract_action_ids({"actions": "not_a_list"}) == []

    def test_dict_actions_with_id(self):
        doc = {"actions": [{"id": "create_task"}, {"id": "list_tasks"}]}
        result = _extract_action_ids(doc)
        assert result == ["create_task", "list_tasks"]

    def test_non_dict_action_items_skipped(self):
        doc = {"actions": ["string_action", {"id": "valid_action"}]}
        result = _extract_action_ids(doc)
        assert result == ["valid_action"]

    def test_whitespace_only_id_skipped(self):
        doc = {"actions": [{"id": "  "}, {"id": "valid"}]}
        result = _extract_action_ids(doc)
        assert result == ["valid"]

    def test_ids_stripped(self):
        doc = {"actions": [{"id": "  create_task  "}]}
        result = _extract_action_ids(doc)
        assert result == ["create_task"]

    def test_missing_id_field_skipped(self):
        doc = {"actions": [{"name": "no_id_here"}, {"id": "valid"}]}
        result = _extract_action_ids(doc)
        assert result == ["valid"]

    def test_empty_actions_list(self):
        assert _extract_action_ids({"actions": []}) == []


# ---------------------------------------------------------------------------
# 3. _has_persistence_signal
# ---------------------------------------------------------------------------

class TestHasPersistenceSignal:
    def test_none_returns_false(self):
        assert _has_persistence_signal(None) is False

    def test_non_dict_returns_false(self):
        assert _has_persistence_signal([]) is False

    def test_no_actions_returns_false(self):
        assert _has_persistence_signal({"id": "tasks"}) is False

    def test_actions_without_emits_returns_false(self):
        doc = {"actions": [{"id": "list_tasks"}]}
        assert _has_persistence_signal(doc) is False

    def test_action_with_emits_returns_true(self):
        doc = {"actions": [{"id": "create_task", "emits": ["task.created"]}]}
        assert _has_persistence_signal(doc) is True

    def test_action_with_emits_none_returns_false(self):
        doc = {"actions": [{"id": "create_task", "emits": None}]}
        assert _has_persistence_signal(doc) is False

    def test_mixed_actions_one_with_emits(self):
        doc = {
            "actions": [
                {"id": "list_tasks"},
                {"id": "create_task", "emits": ["task.created"]},
            ]
        }
        assert _has_persistence_signal(doc) is True

    def test_actions_not_a_list_returns_false(self):
        doc = {"actions": "not_a_list"}
        assert _has_persistence_signal(doc) is False

    def test_non_dict_action_items_do_not_trigger(self):
        doc = {"actions": ["string_item"]}
        assert _has_persistence_signal(doc) is False


# ---------------------------------------------------------------------------
# 4. _extract_event_types
# ---------------------------------------------------------------------------

class TestExtractEventTypes:
    def test_malformed_yaml_returns_empty(self):
        assert _extract_event_types("{{{broken", "test") == []

    def test_no_events_key_returns_empty(self):
        assert _extract_event_types("id: events\n", "test") == []

    def test_events_not_list_returns_empty(self):
        assert _extract_event_types("events: not_a_list\n", "test") == []

    def test_event_with_type_field(self):
        yaml_content = "events:\n  - type: task.created\n"
        result = _extract_event_types(yaml_content, "test")
        assert result == ["task.created"]

    def test_event_type_fallback(self):
        yaml_content = "events:\n  - event_type: task.updated\n"
        result = _extract_event_types(yaml_content, "test")
        assert result == ["task.updated"]

    def test_id_fallback(self):
        yaml_content = "events:\n  - id: task.deleted\n"
        result = _extract_event_types(yaml_content, "test")
        assert result == ["task.deleted"]

    def test_type_takes_priority_over_event_type(self):
        yaml_content = "events:\n  - type: the.real.type\n    event_type: fallback\n"
        result = _extract_event_types(yaml_content, "test")
        assert result == ["the.real.type"]

    def test_non_dict_events_skipped(self):
        yaml_content = "events:\n  - string_event\n  - type: valid.event\n"
        result = _extract_event_types(yaml_content, "test")
        assert result == ["valid.event"]

    def test_whitespace_only_values_skipped(self):
        yaml_content = "events:\n  - type: '   '\n  - type: valid\n"
        result = _extract_event_types(yaml_content, "test")
        assert result == ["valid"]

    def test_multiple_events(self):
        yaml_content = "events:\n  - type: a.created\n  - type: b.updated\n"
        result = _extract_event_types(yaml_content, "test")
        assert result == ["a.created", "b.updated"]


# ---------------------------------------------------------------------------
# 5. _crud_action_count
# ---------------------------------------------------------------------------

class TestCrudActionCount:
    def test_empty_list_returns_zero(self):
        assert _crud_action_count([]) == 0

    def test_no_crud_verbs_returns_zero(self):
        assert _crud_action_count(["view_profile", "send_notification"]) == 0

    def test_create_counted(self):
        assert _crud_action_count(["create_task"]) == 1

    def test_list_counted(self):
        assert _crud_action_count(["list_tasks"]) == 1

    def test_update_counted(self):
        assert _crud_action_count(["update_task"]) == 1

    def test_delete_counted(self):
        assert _crud_action_count(["delete_task"]) == 1

    def test_multiple_crud_actions(self):
        actions = ["create_task", "list_tasks", "update_task", "delete_task"]
        assert _crud_action_count(actions) == 4

    def test_crud_verb_as_substring(self):
        # "create_listing" still contains "create"
        assert _crud_action_count(["create_listing"]) == 1

    def test_mixed_crud_and_non_crud(self):
        actions = ["create_task", "mark_complete", "list_tasks"]
        assert _crud_action_count(actions) == 2
